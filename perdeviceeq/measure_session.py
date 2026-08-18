#!/usr/bin/env python3
"""The measurement session: one take at a time, and its record.

MeasureSession is the wizard-facing API. Preconditions in the
constructor (it refuses before any sound), a moratorium claimed and
released around every sweep -- our own profile bypassed on the target
sink, foreign streams muted -- and take(channel) for one physical
sweep, returning a structured TakeOutcome: the analyzed curve plus the
running per-frequency spread across the channel's accepted takes, the
GUI's live fan. discard() drops a bad take, set_level() names the level
its sweeps play at, finalize(channel) writes one result.json per
channel (fit_peq --left/--right takes it from there). No printing, no
prompts.

It stands on sweep_io, which moves one sweep and knows nothing about
takes, profiles or searches. THE TWO SEARCHES ARE SIBLINGS ON THAT
FLOOR, not parts of this one: knee_run walks the capture gain,
level_run the playback level. What arrives here from either is a
NUMBER the session is told to use -- nothing in here hunts. Extracted
from tools/measure_run.py, which remains the CLI on top.

Method notes (worth not re-deriving):

- EQ state (Task 4 lesson): the sweep runs with our own profile
  bypassed on the target sink -- the backend's moratorium deletes that
  sink's key from the 'per-device-eq' metadata, the same mechanism the
  app's Bypass switch uses, and the WirePlumber hook flattens the node.
  The graph string is read from the metadata, or, when the GUI has not
  published it this session (a cold PipeWire start seeds the hook from
  persisted state without touching the metadata), from that persisted
  state. The claim is taken per sweep and released in a finally, so an
  exception or a cancel restores it too. What was found and from where,
  that it was bypassed and that it was restored is recorded in
  `eq_profile_state`; a failed restore is loudly reported with the
  manual recovery command.
- Foreign streams: anything else playing into the sink during the
  sweep is measured too. By default their presence refuses the run
  with a list; mute_others instead has the moratorium mute them for
  the duration and restore the previous mute state after. The list,
  muted or not, goes into `foreign_streams` of the result; the reading
  of the graph for it is sweep_io's.
- Levels policy: the digital sweep level is FIXED at -6 dBFS (core),
  the sweep stream volume is forced to 1.0 (pw-play --volume, verified
  from the node's Props), and the sink volume is written only to the
  level this session was given -- given none, it is not written at
  all. Everything ends up in `levels`, including a search's own
  description of itself when one produced the number. The sink's
  applied volumes (channelVolumes and softVolumes) are read from its
  Props during every sweep and stored on the take: when the level was
  moved between takes of one channel and the move was applied in
  software, averaging and finalize align the takes onto the channel's
  quietest one by exactly the recorded gain ratio -- the known
  bookkeeping is removed, seating variation is kept. A hardware-volume
  device (softVolumes pinned at 1.0, e.g. BT absolute volume) records
  unity gains, the alignment is a no-op and mixed levels stay visible
  in the spread, which is the honest answer there.
- What a take is judged on here: a quick pre-roll noise-floor look
  right after capture (same threshold and wording as the core) so a
  noisy room is caught on take 1, not after five reseats; up to
  REPAIR_MAX_MS of isolated non-finite (NaN/Inf) samples on the
  analyzed channel are interpolated as a capture xrun (with a warning)
  while a larger flood aborts as a faulty input; the non-finite scan
  covers ALL channels, not just the analyzed one, so a glitch on the
  other side is not invisible. A full-scale sample count flags a
  genuinely clipped (unusable) take and a peak above HOT_DBFS is only
  a low-headroom advisory. The authoritative numbers are still
  computed by the core from the aligned impulse.
- Where the sweep actually went is sweep_io's verdict, kept here in
  `path_clean` and carried into the result: a sweep through an
  unidentified chain is not a measurement of the device.
"""
import math
import os
import re
from datetime import datetime
import shutil
import tempfile
import threading
from dataclasses import dataclass, field

import numpy as np

from scipy.stats import chi2

from . import measure_build as mb
from . import measure_core as mc
# THE FLOOR, imported by name so the call sites read the same as before
# and so this list IS the dependency: everything measure_session needs
# from the sweep-moving layer, and nothing else
from .sweep_io import (                              # noqa: F401
    BT_WARM_S, DEBUG_RAW_ENV, FULLSCALE, FaultyCaptureError,
    MeasureError, REPAIR_MAX_MS, RefusalError, _aim_layout, _props,
    _utc_now, await_sink_volume, check_sink_identity, foreign_streams,
    node_ident, peak_dbfs, pw_dump, repair_nonfinite, require_tools,
    resolve_node, run_take, save_take_wav, sink_applied_volumes,
    sink_volume_state, watch_volume_ends, write_sweep_files)
from . import pw_backend

HOT_DBFS = -1.0                          # peak above this = low headroom
AUTO_TRUST_FLOOR_PK = -20.0              # trust the room's floor read only
#                                          on a probe at least this hot
SPREAD_MAX_DB = 3.0                      # take-to-take spread above this
#                                          is untrustworthy (red on the
#                                          strip; the auto EQ ceiling)
DRIVER_MIN_OCT = 0.25                    # a spread-driver flag must win
#                                          back at least this much band
TRUST_CONFIDENCE = 0.68                  # the ceiling judges an upper
#                                          confidence bound on the spread,
#                                          not the point estimate: two or
#                                          three takes cannot certify calm


def gain_comp_factors(gains):
    """Per-take linear factors (each <= 1.0) that align takes captured
    at different software volumes onto the quietest one: a take
    recorded with gain g is scaled by min/g, removing exactly the known
    level move and nothing else. Downward only, so scaling the samples
    can never clip. Any unknown or unusable gain (None, <= 0,
    non-finite) returns None and disables compensation for the whole
    set -- aligning the known takes around an unknown one would shift
    real data by a guess."""
    vals = []
    for g in gains:
        try:
            g = float(g)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(g) or g <= 0.0:
            return None
        vals.append(g)
    if not vals:
        return None
    ref = min(vals)
    return [ref / g for g in vals]


def mirror_key(key):
    """The L<->R mirror partner of a speaker key ("FL"->"FR",
    "SL"->"SR", "RL"->"RR"), or None when the channel has no
    symmetric partner (FC, LFE, MONO): the ghost overlay compares a
    PAIR's symmetry, and a center or a sub has no symmetric
    reference -- comparing a speaker against "the others" in a room
    compares POSITIONS and their modes, not devices."""
    if key.endswith("L"):
        return key[:-1] + "R"
    if key.endswith("R"):
        return key[:-1] + "L"
    return None


@dataclass
class SessionConfig:
    """Everything a measurement session needs up front. The analyzed
    channel is deliberately NOT here: it is an argument of every take,
    so one session accumulates L and R side by side."""
    sink: str
    source: str
    channels: int = 1
    samples: int = mc.DEFAULT_N
    fs: int = mc.DEFAULT_FS
    f_start: float = mc.DEFAULT_F_START
    f_end: float = mc.DEFAULT_F_END
    pre_silence: float = 1.0
    post_silence: float = 0.5
    cal: str = None
    smoothing: int = 6
    device: str = None
    rig: str = None
    mic: str = None
    save_dir: str = None    # None -> throwaway tempdir, wiped on exit
    mute_others: bool = False
    raw_capture_dump: bool = False  # also forced on by DEBUG_RAW_ENV
    start_volume: float = None      # the level sweeps play at
    # One SINK CHANNEL INDEX per profile channel, or None for a channel
    # that is paired with no output. The caller owns the pairing -- a
    # profile channel names a side of a transducer and a sink channel
    # names a route, and which route carries which side is the
    # binding's business, not the session's. None for the whole thing
    # keeps the old behaviour: the Nth channel sweeps the Nth output.
    play_map: tuple = None


@dataclass
class TakeRecord:
    """One accepted take: the analyzed curve and its vital signs."""
    id: int                   # monotonic; also the take%02d.wav number
    channel: int              # the capture channel this take analyzed
    freq_hz: np.ndarray       # analysis grid (log, shared per session)
    mag_db: np.ndarray        # raw magnitude, no cal, no smoothing
    delay_ms: float           # linear-IR peak position in the recording
    snr_db: object            # core estimate from the aligned impulse
    peak_dbfs: float
    clipped: int              # full-scale sample count (0 = clean)
    repaired: int             # interpolated non-finite samples
    wav_path: str
    chan_vol: object = None   # sink channelVolumes entry (linear) for
                              # the played channel at sweep time
    soft_vol: object = None   # softVolumes ditto -- the gain PipeWire
                              # actually multiplied into the samples;
                              # 1.0 when the device does the volume
    noise_dbfs: object = None  # core pre-sweep noise-floor estimate
    capture_channel: object = None  # analyze column (which mic saw it)
    capture_gain: object = None  # (cubic, "hardware"/"software"/None)
                                 # on the source at sweep time: two
                                 # takes measured at different input
                                 # gains are not comparable, and the
                                 # passport must be able to say so
    created_utc: object = None      # ISO 8601 UTC acceptance time
    h2_db: object = None            # harmonic confession, core axes
    h3_db: object = None
    thd_db: object = None
    thd_noise_db: object = None     # the floor of the same reading


def spread_trust_bound(spread, n_takes):
    """Upper TRUST_CONFIDENCE bound on a sample std over `n_takes`
    takes: s * sqrt(df / chi2_a(df)). A std of three takes must not
    flip trust back up while an outlier seating sits in the sample;
    trust is earned by accumulating agreeing takes -- x2.42 at two,
    x1.61 at three, approaching 1 -- or restored by deleting the
    outlier, never by dilution."""
    df = n_takes - 1
    k = math.sqrt(df / chi2.ppf(1.0 - TRUST_CONFIDENCE, df))
    return np.asarray(spread, float) * k


def trusted_band_hz(freqs, ok, min_run_oct=1.0 / 6.0):
    """(floor, ceiling) of the trusted band from a per-frequency ok
    mask: each edge scans inward to the first at-least-`min_run_oct`
    contiguous trusted run, so a short island never moves an edge
    while a cliff moves it to its exact brink. A mask with no
    qualifying run degenerates to floor >= ceiling, which reads as
    'no trusted band'. The pure half of trusted_ceiling_hz /
    trusted_floor_hz, shared with the profile-side trust report."""
    f = np.asarray(freqs, float)
    ok = np.asarray(ok, bool)
    min_ratio = 2.0 ** min_run_oct
    n = len(f)
    ceiling = float(f[0])
    i = n - 1
    while i >= 0:
        if not ok[i]:
            i -= 1
            continue
        j = i
        while j >= 0 and ok[j]:
            j -= 1
        if j < 0 or f[i] / f[j + 1] >= min_ratio:
            ceiling = float(f[i])
            break
        i = j
    floor = float(f[-1])
    i = 0
    while i < n:
        if not ok[i]:
            i += 1
            continue
        j = i
        while j < n and ok[j]:
            j += 1
        if j >= n or f[j - 1] / f[i] >= min_ratio:
            floor = float(f[i])
            break
        i = j
    return floor, ceiling


TAKE_CLEAN = "clean"        # counts toward a channel's three good takes
TAKE_FLAGGED = "flagged"    # usable but not ideal; does NOT count
TAKE_CLIPPED = "clipped"    # unusable
TAKE_SILENT = "silent"      # not a measurement at all


def testified(rec):
    """Did this take hear anything at all?

    A capture with no onset -- nothing plugged in, the wrong port,
    a sweep that never left the sink -- still becomes a TakeRecord:
    the deconvolution runs on noise, the delay detector locks onto
    whatever peak the noise offered, and out comes a magnitude two
    hundred decibels down with a confession made of nothing. Such a
    take is not a bad measurement, it is an absence of one, and it
    must not be averaged into a channel's mean or asked about
    harmonics. An infinite or missing SNR is the mark: the onset
    search found no signal to measure noise against."""
    snr = getattr(rec, "snr_db", None)
    return snr is not None and math.isfinite(snr)


def take_quality(rec):
    """Classify an accepted take. Single source of truth for the wizard's
    ring/row status and the 'three clean takes' rule -- CLI, GUI and tests
    all judge quality here, using the same thresholds the live take() path
    warns on.

    FOUR verdicts, and the first is the one this judge lacked. A take
    that heard NOTHING -- no onset, so no SNR to compute -- used to
    fall through every test and come out clean, because each test asks
    whether something is WRONG and nothing is wrong with silence. A
    green dot went on a flat line at -240 dBFS and counted toward
    "three clean takes". The file already knew the case: testified()
    says such a take is "not a bad measurement, it is an absence of
    one", and this function simply never asked it. It asks now, first,
    before anything else can call the silence fine.

    Then: clipping is unusable (red); a hot peak (>= HOT_DBFS) or low
    SNR (< SNR_WARN_DB) is usable-but-flagged (amber) and does not
    count; everything else is clean (green). A repaired single-sample
    glitch stays clean -- the take is unaffected by an interpolated
    sample."""
    if not testified(rec):
        return TAKE_SILENT
    if rec.clipped:
        return TAKE_CLIPPED
    if rec.peak_dbfs >= HOT_DBFS:
        return TAKE_FLAGGED
    if rec.snr_db is not None and rec.snr_db < mc.SNR_WARN_DB:
        return TAKE_FLAGGED
    return TAKE_CLEAN


@dataclass
class TakeOutcome:
    """What one MeasureSession.take() call produced.

    kind == "take": `take` is the accepted TakeRecord and `spread_db`
    the per-frequency std (ddof=1) across the channel's accepted takes
    (None until there are two) -- the live fan and its width.
    A take() has ONE outcome now. Searching for a level is a different
    job with a different owner -- level_run.hunt -- and this class used
    to carry two more kinds because the two jobs shared a method.
    `notes` are printable warnings in the CLI's exact wording.
    """
    kind: str
    take: TakeRecord = None
    spread_db: object = None
    level: dict = None
    notes: list = field(default_factory=list)


class MeasureSession:
    """Single-take measurement lifecycle for the CLI and the wizard.

    Preconditions run in the constructor and raise RefusalError before
    anything is played or changed. __enter__ writes the sweep files
    -- into a throwaway tempdir when cfg.save_dir is unset, wiped again
    on __exit__: the canvas in the profile is the artifact, not the
    wavs -- and engages foreign-stream muting and the profile bypass
    (restored on ANY exit). take(channel) runs one physical sweep and
    returns a
    TakeOutcome; discard() drops a bad take from the accumulation (the
    wav stays on disk for the session's lifetime, ids are never
    reused); finalize(channel) assembles the channel's result via
    measure_core and writes it as JSON only when out_path is given.
    No printing, no prompts: decisions surface as outcomes, warning
    texts as `notes`.
    """

    def __init__(self, cfg, resolve=True, dump=None):
        if cfg.channels < 1:
            raise RefusalError("channels must be >= 1")
        tools = ["pw-dump", "pw-metadata", "pw-play", "pw-record"]
        if cfg.mute_others:
            tools.append("pw-cli")
        require_tools(tools)
        self.cfg = cfg
        self.precondition_notes = []

        # The unresolved skeleton: enough for adopting canvas
        # takes, statistics and parking -- a session for an
        # absent sink is livable (the gone Measure window browses
        # and refits offline). The graph is consulted at resolve
        # time: at construction by default, or at __enter__ for a
        # deferred birth -- which also means the live
        # preconditions are FRESH at arming instead of stale from
        # open time.
        self.sink = None
        self.source = None
        self.sink_ident = {"name": cfg.sink,
                           "description": cfg.device or cfg.sink}
        self.source_ident = {"name": cfg.source}
        self.sink_layout = []
        self.volume_start = None
        self._gain_now = None
        self._raw0 = None
        self.foreign = []
        self._resolved = False
        if resolve:
            # the caller's graph picture when it has one: a session is
            # built at every window open and again at every change of
            # rig or pairing, and each pw-dump here was a subprocess on
            # the main loop between the click and the window
            self._resolve(dump if dump is not None else pw_dump())

        self.sweep = mc.generate_sweep(cfg.samples, cfg.fs, cfg.f_start,
                                       cfg.f_end)
        self.wav_duration = (cfg.pre_silence + self.sweep.duration_s
                             + cfg.post_silence)
        self.freqs = mc.log_grid()          # process_takes' exact grid
        slug = re.sub(r"[^\w.+-]+", "_",
                      cfg.device or self.sink_ident["name"]
                      or "device").strip("_")
        dbg = os.environ.get(DEBUG_RAW_ENV)
        if dbg and not cfg.save_dir:
            # the debug hand: an investigation sometimes needs the
            # RAW material -- the sweep files, every take%02d.wav
            # and the multichannel raw%02d.wav dumps -- and the
            # profile is the wrong home for megabytes of base64
            # (the package stays bare; the canvas is the record).
            # Both halves already existed unwired: save_dir keeps
            # the session dir, raw_capture_dump writes the raw
            # captures. The environment hand joins them; an
            # explicit save_dir outranks it.
            cfg.save_dir = os.path.expanduser(dbg)
            cfg.raw_capture_dump = True
        if cfg.save_dir:
            self.outdir = os.path.join(
                cfg.save_dir,
                "%s_%s" % (slug,
                           datetime.now().strftime("%Y%m%d-%H%M%S")))
        else:                     # nothing here is worth keeping: the
            self.outdir = None    # canvas in the profile is the record
        self._ephemeral = not cfg.save_dir
        self._slug = slug

        self.wav = None                     # written on __enter__
        self.started_utc = None             # stamped on __enter__
        self.path_clean = None
        self.eq_state = None
        self._cancel = threading.Event()    # set by cancel() to abort a sweep
        # NONE MEANS NOBODY HAS ESTABLISHED A LEVEL YET, and the
        # sink's own listening volume is not one: wearing it made
        # an unarmed session report a level nobody chose -- the
        # very fallback the window's refresh docstring warns
        # about, alive one floor down. cfg seeds this once, at
        # birth; after that set_level() is the only writer.
        self._v_cur = cfg.start_volume
        self._level_found = None            # a search's own words, if any
        self._take_seq = 0                  # take%02d numbers, never reused
        self._takes = {}                    # channel -> [(record, samples)]

    def _resolve(self, dump):
        """Consult the live graph: identities, layout, volume,
        foreign streams. All RefusalErrors of the old
        construction-time preconditions live here; a deferred
        birth meets them at __enter__ instead."""
        self.sink = resolve_node(dump, self.cfg.sink, "Audio/Sink")
        check_sink_identity(self.sink)
        self.source = resolve_node(dump, self.cfg.source,
                                   "Audio/Source")
        src_p = _props(self.source)
        if src_p.get("media.class") != "Audio/Source":
            raise RefusalError(
                "capture target %r is %r, expected Audio/Source"
                % (self.cfg.source, src_p.get("media.class")))
        if not (src_p.get("device.api") or "").startswith("alsa"):
            self.precondition_notes.append(
                "WARNING: mic source device.api is %r; measurement "
                "mics are expected on USB/ALSA"
                % src_p.get("device.api"))
        self.sink_ident = node_ident(self.sink)
        self.source_ident = node_ident(self.source)
        # which JACK the card is listening on: one node, several
        # ports, and a passport must not be ambiguous about it
        self.source_ident["route"] = pw_backend.active_input_route(
            self.source_ident["name"], dump)
        # the ports, not audio.position: a sweep is aimed by NAME
        # and the name has to be one the sink's ports answer to
        self.sink_layout = _aim_layout(self.sink_ident["name"], dump)

        v0, raw0, muted = sink_volume_state(dump, self.sink["id"])
        if muted:
            raise RefusalError("sink is muted; unmute it and set "
                               "the working listening level first")
        if v0 is None:
            self.precondition_notes.append(
                "WARNING: could not read the sink volume from "
                "pw-dump")
        self.volume_start = v0
        self._raw0 = raw0

        self.foreign = foreign_streams(dump, self.sink["id"])
        if self.foreign and not self.cfg.mute_others:
            raise RefusalError(
                "other streams are playing into this sink (a sweep "
                "on top of them is not a measurement):\n  %s\nstop "
                "them or re-run with --mute-others" % "\n  ".join(
                    "id %(id)s  %(node_name)s  app=%(app)s" % s
                    for s in self.foreign))
        self._resolved = True

    # -- lifecycle ----------------------------------------------------------

    def __enter__(self):
        if not self._resolved:
            self._resolve(pw_dump())
        if self.outdir is None:             # ephemeral working dir
            self.outdir = tempfile.mkdtemp(
                prefix="per-device-eq-%s-" % self._slug)
        else:
            os.makedirs(self.outdir, exist_ok=True)
        self.started_utc = _utc_now()
        self.wav = write_sweep_files(self.outdir, self.sweep,
                                     self.cfg.pre_silence,
                                     self.cfg.post_silence)
        return self

    def __exit__(self, *exc):
        # NOTHING TO UNWIND: the hardware is claimed and given back
        # per sweep, inside take(), and the level search that used to
        # register a restore here lives in level_run now. What is left
        # is the throwaway directory.
        if self._ephemeral and self.outdir:
            shutil.rmtree(self.outdir, ignore_errors=True)
            self.outdir = None
            self.wav = None       # take() after exit fails loudly again
        return False

    # -- one physical sweep --------------------------------------------------

    def cancel(self):
        """Abort the sweep in flight (from the Stop button, on another
        thread): the running take() raises MeasureCancelled, its child
        processes are killed and the partial capture is dropped. A no-op
        when nothing is playing -- take() clears the flag as it starts."""
        self._cancel.set()

    def set_level(self, cubic, found=None):
        """The level this session's sweeps play at.

        Nothing to freeze: a session records takes and does not search.
        Whoever wants a level searched runs level_run.hunt and passes
        the number it returns.

        `found` is that search's own description of itself, carried
        verbatim into the passport. The session does not read it --
        interpreting it would put the search back inside here through
        the schema.
        """
        self._v_cur = max(0.0, min(1.0, float(cubic)))
        if found is not None:
            self._level_found = dict(found)

    def _meas_volume_arg(self):
        """The measurement volume for this sweep, or None when the
        sink already stands there: a no-op toggle must write nothing
        and must not trigger the settle or the Bluetooth warm-up."""
        if self._v_cur is None or self.volume_start is None:
            return None
        if abs(self._v_cur - self.volume_start) < 1e-4:
            return None                     # nothing to change
        return self._v_cur

    def _warm_sink(self):
        """Play BT_WARM_S of silence to the sink. A Bluetooth headset
        applies absolute volume asynchronously and may be resuming
        from A2DP suspend; the observed field failure was a sweep
        whose head played at the OLD volume with the 6 dB step landing
        three seconds in -- a mixed-level take no gate can repair.
        The silence wakes the link and gives the volume time to land
        before the real sweep starts. Best-effort: a failed warm-up
        must never fail the take."""
        try:
            import soundfile as sf
            wav = os.path.join(self.outdir, "warmup-silence.wav")
            if not os.path.exists(wav):
                sf.write(wav, np.zeros(int(BT_WARM_S * self.sweep.fs),
                                       dtype=np.float32),
                         self.sweep.fs)
            pw_backend.backend().play(
                self.sink["id"], wav).wait(timeout=BT_WARM_S + 5.0)
        except Exception:
            pass

    def _applied_gains(self, channel):
        """The sink's applied volumes for the played channel, read from
        the node while the measurement level is engaged: (channelVolumes
        entry, softVolumes entry), linear, None when unreadable. Read
        AFTER the sweep so the server had the whole take to settle any
        just-written volume. Metadata must never break a sweep, hence
        the broad catch."""
        try:
            cv, sv = sink_applied_volumes(pw_dump(), self.sink["id"])
        except Exception:
            return None, None

        def pick(arr):
            if not arr:
                return None
            if 0 <= channel < len(arr):
                return arr[channel]
            return sum(arr) / len(arr)
        return pick(cv), pick(sv)

    def _sink_index(self, channel):
        """The sink channel a profile channel sweeps through: the
        pairing when there is one, the same index otherwise."""
        pm = self.cfg.play_map
        if pm is None:
            return channel
        if not 0 <= channel < len(pm):
            return None
        return pm[channel]

    def _channel_map(self, channel):
        """The position for pw-play --channel-map, e.g. 'FL'. A mono
        sweep tagged with one position plays only that speaker, because
        PipeWire routes it by NAME.

        The name is read from the SINK'S OWN layout rather than from
        the profile: a card that calls its outputs AUX0..AUX9 matches
        nothing when handed 'FL', and a sweep that matches nothing is
        spread over every channel instead of one. None for a mono sink
        or an index outside the layout -- then the plain mono sweep is
        the honest thing to play."""
        n = len(self.sink_layout)
        idx = self._sink_index(channel)
        if idx is None or n <= 1 or not 0 <= idx < n:
            return None
        return self.sink_layout[idx].split(".")[0]

    def take(self, channel, analyze=None):
        """One sweep played and captured, analyzed on capture column
        `analyze` (defaults to `channel`) but stored under `channel`, the
        profile channel, so one capture column can feed several profile
        channels (e.g. measure the left cup on the right mic)."""
        cfg = self.cfg
        a = channel if analyze is None else analyze
        if not 0 <= a < cfg.channels:
            raise RefusalError("capture column %d out of range for a "
                               "%d-channel capture" % (a, cfg.channels))
        if cfg.play_map is not None and self._sink_index(channel) is None:
            # a sweep with no output to aim at would be played as plain
            # mono into EVERY channel, and the curve that came back
            # would look like a measurement while being a mixture
            raise RefusalError("channel %d is paired with no output; "
                               "pair it before measuring" % channel)
        if self.wav is None:
            raise MeasureError("session not entered (use `with session:`)")
        # Names are identity, ids are addresses, and PipeWire
        # recycles addresses on graph churn: the ids resolved at
        # enter time can point at a RECYCLED number by the next
        # take (the field caught a webcam wearing the mic's old
        # id -- the pin held, the address lied). Every take
        # re-resolves both ends by name, fresh; an absent end
        # fails to open, honestly, before anything is spawned.
        dump = pw_dump()
        try:
            self.sink = resolve_node(dump, cfg.sink, "Audio/Sink")
        except RefusalError:
            raise MeasureError(
                "output device %r failed to open: it is not in "
                "the graph" % (cfg.device or cfg.sink))
        try:
            self.source = resolve_node(dump, cfg.source,
                                       "Audio/Source")
        except RefusalError:
            raise MeasureError(
                "capture device %r failed to open: it is not in "
                "the graph" % cfg.source)
        self.sink_ident = node_ident(self.sink)
        self.source_ident = node_ident(self.source)
        # which JACK the card is listening on: one node, several
        # ports, and a passport must not be ambiguous about it
        self.source_ident["route"] = pw_backend.active_input_route(
            self.source_ident["name"], dump)
        # what the card's input gain IS at this sweep -- the window
        # sets it before calling in, and the session only witnesses
        self._gain_now = pw_backend.gain_of_node(self.source)
        # the ports, not audio.position: a sweep is aimed by NAME
        # and the name has to be one the sink's ports answer to
        self.sink_layout = _aim_layout(self.sink_ident["name"], dump)
        self._pending = None                # a new sweep supersedes it
        self._cancel.clear()                # fresh; cancel() sets it to abort
        raw_path = (os.path.join(self.outdir,
                                 "raw%02d.wav" % (self._take_seq + 1))
                    if cfg.raw_capture_dump else None)
        cmap = self._channel_map(channel)   # route the sweep to THIS speaker
        vol_arg = self._meas_volume_arg()
        auth = pw_backend.backend()
        self.eq_state = auth.moratorium_begin(
            self.sink_ident["name"], vol_arg,
            mute_others=bool(cfg.mute_others))
        muted = set(auth.moratorium_muted_ids())
        for st in self.foreign:
            if st["id"] in muted:
                st["muted_for_measure"] = True
        try:
            changed = vol_arg is not None
            if changed:
                # readback settle on EVERY transport; the
                # warm-up stays as the Bluetooth link wake
                await_sink_volume(self.sink["id"],
                                  self._v_cur)
                if (self.sink_ident.get("device_api")
                        or "").startswith("bluez"):
                    self._warm_sink()
            vstop = threading.Event()
            if os.environ.get("PDEQ_TRACE_VOL"):
                threading.Thread(
                    target=watch_volume_ends,
                    args=(self.sink["id"], vstop),
                    kwargs={"source_id":
                            self.source["id"]},
                    daemon=True).start()
            try:
                data, info = run_take(self.sink, self.source, self.wav,
                                      self.wav_duration, cfg.channels,
                                      self.sweep.fs,
                                      verify=self.path_clean is None,
                                      raw_dump_path=raw_path,
                                      cancel=self._cancel,
                                      channel_map=cmap)
                gains = self._applied_gains(channel)
            finally:
                vstop.set()
        finally:
            auth.moratorium_end()   # volume, EQ, unmute -- right after
        if info is not None:
            self.path_clean = info

        notes = []
        # diagnostic: scan ALL channels, not just the one we analyze,
        # so a glitch on the other channel isn't invisible
        for c in range(data.shape[1]):
            w = np.nonzero(~np.isfinite(data[:, c]))[0]
            if w.size:
                notes.append("note: %d non-finite sample(s) on channel %d "
                             "at %s of %d"
                             % (w.size, c, list(w[:6]), data.shape[0]))
        chan = data[:, a]
        where = np.nonzero(~np.isfinite(chan))[0]
        bad = int(where.size)
        if bad:
            limit = max(1, int(REPAIR_MAX_MS / 1000.0 * self.sweep.fs))
            if bad > limit or bad >= len(chan):
                raise FaultyCaptureError(a, cfg.channels, bad)
            chan = repair_nonfinite(chan)
            data = data.copy()
            data[:, a] = chan               # keep the saved take finite
            notes.append("WARNING: interpolated %d non-finite capture "
                         "sample(s) on column %d at %s of %d -- a benign "
                         "single-sample glitch during the sweep; the take "
                         "is unaffected."
                         % (bad, a, list(where[:6]), len(chan)))
        pk = peak_dbfs(chan)
        clipped = int(np.count_nonzero(np.abs(chan) >= FULLSCALE))
        if clipped:
            notes.append("WARNING: %d sample(s) at full scale -- the sweep "
                         "is clipped and this take is unusable; lower the "
                         "sink volume (or use --auto-level) and remeasure."
                         % clipped)
        elif pk >= HOT_DBFS:
            notes.append("WARNING: capture peak %.1f dBFS leaves little "
                         "headroom (risk of inter-sample clipping); "
                         "consider a lower level, or let the level "
                         "search choose one." % pk)

        return self._accept(channel, data, chan, pk, clipped, bad, notes,
                            gains, capture=a)

    def _accept(self, channel, data, chan, pk, clipped, repaired, notes,
                gains=(None, None), capture=None):
        self._take_seq += 1
        path = save_take_wav(self.outdir, self._take_seq, data,
                             self.sweep.fs)
        t = mc.analyze_take(chan, self.sweep, self.freqs)
        # WARNED FROM THE ANALYSIS, not the quick estimate. The take
        # already carries the analysed SNR and prints it; warning from
        # a second number put two figures about one recording side by
        # side -- "SNR 46.1 dB" over "WARNING: low SNR (2.9 dB)" -- and
        # the quick one is the wrong of the two here: it finds the
        # sweep's onset by a threshold ten times the pre-roll, which a
        # card with fixed spikes in its pre-roll defeats.
        snr = t.snr_db if t.snr_db is not None else self._quick_snr(chan)[0]
        if snr is not None and math.isfinite(float(snr)) \
                and float(snr) < mc.SNR_WARN_DB:
            notes.append("WARNING: low SNR (%.1f dB): raise the level or "
                         "kill the noise source" % float(snr))
        rec = TakeRecord(self._take_seq, channel, self.freqs, t.mag_db,
                         t.delay_ms, t.snr_db, pk, clipped, repaired, path,
                         chan_vol=gains[0], soft_vol=gains[1],
                         noise_dbfs=t.noise_dbfs,
                         capture_channel=capture,
                         capture_gain=getattr(self, "_gain_now", None),
                         created_utc=_utc_now(),
                         h2_db=t.h2_db, h3_db=t.h3_db,
                         thd_db=t.thd_db,
                         thd_noise_db=t.thd_noise_db)
        # whether the headline figure is a measurement or a bound is
        # a fact about the take, so it rides with the take rather than
        # a status line the next sweep wipes
        bound = mb.thd_bound_note(rec)
        if bound:
            notes.append(bound)
        self._takes.setdefault(channel, []).append((rec, chan))
        return TakeOutcome("take", take=rec,
                           spread_db=self.spread_db(channel), notes=notes)

    def _quick_snr(self, chan):
        """Fast per-take (snr, noise_dbfs) so a noisy room is caught
        before the next reseat and the auto-level can target SNR.
        Onset = first sustained crossing of 10x the pre-roll RMS;
        threshold and wording match the core. (None, None) when no
        onset is found."""
        fs = self.sweep.fs
        head = chan[:int(0.4 * fs)]
        noise = math.sqrt(float(np.mean(head ** 2))) if len(head) else 0.0
        thr = max(10.0 * noise, 1e-6)
        over = np.flatnonzero(np.abs(chan) > thr)
        if not len(over):
            return None, None
        snr, _, noise_db = mc.estimate_snr(chan, int(over[0]),
                                           self.sweep)
        return snr, noise_db

    # -- the accumulated fan --------------------------------------------------

    def takes_of(self, channel):
        """The channel's accepted TakeRecords, oldest first."""
        return [rec for rec, _ in self._takes.get(channel, [])]

    def adopt_take(self, channel, rec):
        """Seed the accumulation with a take from a previous session
        (the profile's canvas): the take list, the counts, the
        spread statistics and the quality gates treat it as one of
        their own. Adopted takes carry no samples and no wav -- only
        magnitudes, exactly what the mag-domain machinery consumes;
        finalize() therefore refuses a channel holding them (the
        canvas re-fit path never needs raw samples)."""
        rec.freq_hz = np.asarray(rec.freq_hz, float)
        rec.mag_db = np.asarray(rec.mag_db, float)
        self._takes.setdefault(channel, []).append((rec, None))

    def _comp_factors(self, entries):
        """gain_comp_factors over the entries' recorded soft gains;
        None disables compensation (an unknown gain, or no takes)."""
        return gain_comp_factors([rec.soft_vol for rec, _ in entries])

    def comp_shift_db(self, channel):
        """Per-take dB shifts (all <= 0) aligning the channel's
        accepted takes onto its quietest recorded software gain, in
        takes_of() order; None when compensation is off (an unknown
        gain somewhere, or no takes)."""
        f = self._comp_factors(self._takes.get(channel, []))
        if f is None:
            return None
        return [20.0 * math.log10(k) for k in f]

    def average_and_spread(self, channel, exclude_id=None):
        """Mean curve + take-to-take spread of the channel's accepted
        takes, with recorded level moves compensated: each curve is
        shifted down by its take's known software-gain excess over the
        channel's quietest take, so a manual level change or a re-level
        between takes neither smears the mean nor widens the corridor.
        Unknown gains fall back to the raw curves. The exact alignment
        finalize applies to the samples, so the live fan shows what the
        result will average. (None, None) without takes; spread is None
        until there are two.

        A take that heard NOTHING is not in this arithmetic at all.
        It still exists, it is still shown and can still be deleted
        by hand, but a capture with no onset is the absence of a
        measurement, and averaging it drags the mean two hundred
        decibels away from every real take while the spread -- the
        same call, one line down -- opens to the width of the
        canvas and paints the whole picture in untrustworthy red.
        The field saw exactly that and asked what the pink was."""
        entries = [e for e in self._takes.get(channel, [])
                   if (exclude_id is None or e[0].id != exclude_id)
                   and testified(e[0])]
        if not entries:
            return None, None
        mags = [rec.mag_db for rec, _ in entries]
        factors = self._comp_factors(entries)
        if factors is not None:
            mags = [m + 20.0 * math.log10(k)
                    for m, k in zip(mags, factors)]
        return mc.average_takes(mags)

    def spread_db(self, channel, exclude_id=None):
        """Per-frequency std (ddof=1) across the channel's accepted
        takes, level moves compensated; None until there are two. The
        live fan's width."""
        return self.average_and_spread(channel, exclude_id)[1]

    def _trust_mask(self, thresh, exclude=None):
        """(freqs, ok) under the confidence bound, or None without
        statistics. The judged quantity is not the point estimate but
        the upper TRUST_CONFIDENCE bound on each channel's spread,
        s*sqrt(df/chi2_a(df)): a sample std of three takes must not
        flip trust back up while an outlier seating sits in the
        sample. Trust is earned by accumulating agreeing takes --
        x2.42 at two, x1.61 at three, approaching 1 -- or restored by
        deleting the outlier, never by dilution. Shared by the
        ceiling (the auto EQ handle) and the spread driver."""
        combined = None
        for c, entries in self._takes.items():
            if exclude is not None:
                entries = [e for e in entries if e[0].id != exclude]
            if len(entries) < 2:
                continue
            sp = self.spread_db(c, exclude_id=exclude)
            if sp is None:
                continue
            sp = spread_trust_bound(sp, len(entries))
            combined = (sp if combined is None
                        else np.maximum(combined, sp))
        if combined is None:
            return None
        return np.asarray(self.freqs, float), combined <= thresh

    def trusted_ceiling_hz(self, thresh=SPREAD_MAX_DB):
        """Highest frequency the take-to-take statistics still trust:
        scanning DOWN from the top of the grid, the top of the first
        at-least-1/6-octave run where the bound (see _trust_mask)
        stays under `thresh`. A red island lower in the band does not
        pull the ceiling (it is visible on the strip and is the left
        handle's business); the HF cliff does, to its edge exactly.
        None while no channel has two takes. The bars on the strip
        keep showing the point estimate: they say what happened, the
        ceiling says what cannot be ruled out, so it may sit below
        the red. The statistics only mean what the takes vary over:
        reseat between takes, or the spread flatters the seating."""
        m = self._trust_mask(thresh)
        if m is None:
            return None
        return trusted_band_hz(*m)[1]

    def trusted_floor_hz(self, thresh=SPREAD_MAX_DB):
        """Mirror of trusted_ceiling_hz for the bottom of the band:
        scanning UP from the bottom of the grid, the bottom of the
        first at-least-1/6-octave trusted run under the bound. A red
        island mid-band does not push the floor up; a bass cliff (a
        seal that seats differently every take) does, to its edge
        exactly. None while no channel has two takes."""
        m = self._trust_mask(thresh)
        if m is None:
            return None
        return trusted_band_hz(*m)[0]

    def _trusted_octaves(self, thresh, exclude=None):
        """Total trustworthy bandwidth in octaves under the bound,
        with an optional take excluded."""
        m = self._trust_mask(thresh, exclude)
        if m is None:
            return None
        f, ok = m
        step = math.log2(f[-1] / f[0]) / max(1, len(f) - 1)
        return float(ok.sum()) * step

    def drive_shift_db(self, src, dst):
        """dB to add to `src`'s compensated mean to place it at
        `dst`'s drive -- the display-side twin of balance_trims'
        accounting, so an on-screen offset between two channels is
        the TRUE acoustic difference, not a trace of different sweep
        levels. 0.0 when both were driven identically; None when a
        difference exists but cannot be known, mirroring the trim's
        validity gate: every take's gains must be usable, and either
        the volume was applied in software (softVolumes track
        channelVolumes) or every take of both channels sat at one
        identical volume, which cancels an unknown hardware law."""
        pairs = []
        for c in (src, dst):
            entries = self._takes.get(c, [])
            if not entries:
                return None
            for rec, _ in entries:
                sv, cv = rec.soft_vol, rec.chan_vol
                try:
                    sv, cv = float(sv), float(cv)
                except (TypeError, ValueError):
                    return None
                if not (math.isfinite(sv) and math.isfinite(cv)) \
                        or sv <= 0 or cv <= 0:
                    return None
                pairs.append((sv, cv))
        software = all(math.isclose(sv, cv, rel_tol=1e-3,
                                    abs_tol=1e-6)
                       for sv, cv in pairs)
        one_vol = all(math.isclose(cv, pairs[0][1], rel_tol=1e-3,
                                   abs_tol=1e-6) for _, cv in pairs)
        if not (software or one_vol):
            return None
        gm = {}
        for c in (src, dst):
            softs = [rec.soft_vol for rec, _ in self._takes[c]]
            gm[c] = 20.0 * math.log10(min(softs))
        return gm[dst] - gm[src]

    def spread_driver(self, thresh=SPREAD_MAX_DB):
        """The one accepted take whose removal wins back the most
        trustworthy BANDWIDTH, as (take_id, octaves_regained), or
        None. Judged over the whole band, not the ceiling: a
        seal-leak take poisons the bass while the ceiling stays
        pinned by an HF region red in every take, and a ceiling-only
        verdict stays silent about it (observed in the field within
        a day of shipping it). Leave-one-out over channels with at
        least three takes (removing one of two leaves no statistics
        at all), the reduced sample honestly paying the higher
        confidence factor. A real improvement is required
        (DRIVER_MIN_OCT): when the scatter is spread evenly over the
        takes, deleting any one of them fixes nothing and nothing is
        flagged -- a highlight that cannot deliver on its promise
        would be a lie."""
        base = self._trusted_octaves(thresh)
        if base is None:
            return None
        best = None
        for c, entries in self._takes.items():
            if len(entries) < 3:
                continue
            for rec, _ in entries:
                oc = self._trusted_octaves(thresh, exclude=rec.id)
                if oc is None:
                    continue
                gain = oc - base
                if gain < DRIVER_MIN_OCT:
                    continue
                if best is None or gain > best[1]:
                    best = (rec.id, gain)
        return best

    def discard(self, channel, take_id):
        """Drop a bad take from the accumulation. The wav stays on disk
        as evidence; ids and file numbers are never reused."""
        entries = self._takes.get(channel, [])
        for i, (rec, _) in enumerate(entries):
            if rec.id == take_id:
                del entries[i]
                return rec
        raise MeasureError("no take %s on channel %d" % (take_id, channel))

    # -- result ---------------------------------------------------------------

    def finalize(self, channel, out_path=None, cal=None):
        """Average the channel's accepted takes into a result dict via
        measure_core.process_takes; out_path, when given, also writes
        the dict as JSON (the CLI's result.json -- the wizard keeps it
        in memory and stores the canvas in the profile instead).

        cal defaults to the session's cfg.cal; pass cal= to override per
        channel. The wizard measures both ears in one session but each
        coupler has its own mic-cal file (L_RAW vs R_RAW), so it finalizes
        each channel with that channel's cal. mag_db_uncal is stored
        regardless, so a different cal can still be applied later.

        Takes captured at different software volumes are aligned onto
        the channel's quietest one before averaging (recordings scaled
        by the recorded gain ratio, downward only); the per-take gains
        and the applied shifts land in `levels` so a stored result can
        be re-fit later with full knowledge of how it was driven."""
        entries = self._takes.get(channel, [])
        if not entries:
            raise MeasureError("no accepted takes on channel %d" % channel)
        if any(samples is None for _, samples in entries):
            raise MeasureError(
                "channel %r holds adopted canvas takes; finalize "
                "needs raw samples -- re-fit from the canvas "
                "instead" % channel)
        factors = self._comp_factors(entries)
        recordings = [samples for _, samples in entries]
        comp_db = None
        if factors is not None:
            comp_db = [round(20.0 * math.log10(k), 3) for k in factors]
            if any(abs(k - 1.0) > 1e-9 for k in factors):
                # align the takes onto the channel's quietest recorded
                # gain: exact, downward only, so it can never clip
                recordings = [s * k
                              for s, k in zip(recordings, factors)]
        dump = pw_dump()
        v_final, raw_final, _ = sink_volume_state(dump, self.sink["id"])
        # per-channel truth: the (compensated) result sits at the level
        # of THIS channel's quietest take; the session-wide scalar (the
        # last sweep's level, possibly another channel's) is only the
        # fallback when the applied gains could not be read
        v_report = None
        if factors is not None:
            k = min(range(len(entries)),
                    key=lambda i: entries[i][0].soft_vol)
            cv = entries[k][0].chan_vol
            if cv is not None and cv > 0:
                v_report = cv ** (1.0 / 3.0)
        if v_report is None:
            v_report = self._v_cur if self._v_cur is not None else v_final
        auto = dict(getattr(self, "_level_found", None)
                    or {"enabled": False, "adjustments": 0,
                        "initial": None, "final": None,
                        "in_window": None})
        if auto.get("enabled") and auto.get("final") is None:
            auto["final"] = round(v_report, 4)

        def _r6(v):
            return None if v is None else round(v, 6)
        levels = {
            "sink_volume": (round(v_report, 4)
                            if v_report is not None else None),
            "sink_volume_start": (round(self.volume_start, 4)
                                  if self.volume_start is not None
                                  else None),
            "sink_channel_volumes": raw_final or self._raw0,
            "stream_volume": (self.path_clean or {}).get(
                "playback_stream", {}).get("volume"),
            "capture_peak_dbfs": [round(r.peak_dbfs, 2)
                                  for r, _ in entries],
            "take_channel_volumes": [_r6(r.chan_vol)
                                     for r, _ in entries],
            "take_soft_volumes": [_r6(r.soft_vol) for r, _ in entries],
            "take_noise_dbfs": [round(r.noise_dbfs, 1)
                                if r.noise_dbfs is not None else None
                                for r, _ in entries],
            "gain_comp_db": comp_db,
            "auto_level": auto,
        }
        result = mc.process_takes(
            recordings, self.sweep,
            cal=(cal if cal is not None else self.cfg.cal),
            smoothing_fraction=self.cfg.smoothing,
            device=(self.cfg.device or self.sink_ident["description"]
                    or self.sink_ident["name"]),
            rig=self.cfg.rig, mic=self.cfg.mic,
            sink_api=self.sink_ident.get("device_api"),
            eq_profile_state=self.eq_state, levels=levels,
            path_clean=self.path_clean, foreign_streams=self.foreign)
        if out_path:
            mc.save_result(result, out_path)
        return result
