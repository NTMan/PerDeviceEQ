"""The level search: find the sink volume a measurement should use.

THREE CONSUMERS OF THE HARDWARE, one at a time -- the sensitivity
search, this, and the take. They are independent, and the only thing
that passes between them is a NUMBER: this walk's product is a sink
volume, which the take is then told to use. Nothing here knows what a
take is, and MeasureSession does not know this exists.

It stands on sweep_io, the same floor the session stands on -- a
sweep, run_take, analyze_take, and a moratorium to claim the hardware.
Not on the session, and no longer through a function-level import to
dodge a cycle: there is no cycle to dodge. knee_run.Walk has the same shape for the
capture gain; this is its twin for the playback level.

WHAT IT LOOKS FOR, settled by field ladders on two earphones: the
lowest level at which the distortion figure at 1 kHz is a MEASUREMENT
rather than a bound the rig cannot see under. That is also, by
construction, the level of least measured distortion -- below the
crossing the reading is the rig's own floor and above it the device is
climbing -- with the guard that keeps it from walking to silence.
"""

import math
import os
import tempfile

import numpy as np

from . import measure_build as mb
from . import measure_core as mc
from . import pw_backend
from .sweep_io import run_take, write_sweep_files

AUTO_RAMP = 2.0                 # coarse step while nothing is bracketed
AUTO_RAMP_NEAR = 1.12           # ~3 dB, once the margin says the
                                # crossing is close: doubling the CUBIC
                                # volume is EIGHTEEN decibels and
                                # overshoots exactly where precision is
                                # wanted
AUTO_NEAR_DB = 6.0              # "close" = within this of clearing the
                                # floor gap
AUTO_NEAR_STEPS = 3             # creep at most this often: a device
                                # quieter than the rig at every level
                                # would be crept after forever, and
                                # such a rig must reach the top to say so
AUTO_SETTLE_RATIO = 1.12        # stop closing on the lowest ok within
                                # this much volume -- about a decibel,
                                # finer than the crossing's own wobble
AUTO_SNR_MARGIN_DB = 1.0        # aim past clean, not onto its edge
AUTO_PEAK_FLOOR = -12.0         # quieter wastes capture robustness
AUTO_PEAK_CEIL = -2.0           # louder risks the converter
AUTO_EXPLORE_CEIL = 0.80        # the ramp stops here until a probe AT
                                # it is still quiet
AUTO_START_VOLUME = 0.15        # cubic; "start quiet"
AUTO_MAX_ADJUST = 12            # four to climb, three to creep, three
                                # to close is ten; a sweep is ~8.5 s so
                                # the ceiling only spends on a rig that
                                # needs it
AUTO_CLIP_BACKOFF = 0.7


def _clamp(v):
    return max(0.0, min(1.0, float(v)))


def start_volume(current=None):
    """Where a search begins: quiet, but never at zero.

    A multiplicative ramp cannot move from zero -- it doubles to zero,
    sees no change and stops after one sweep. A rig nobody has
    measured shows zero, so this is not a corner case. Zero is not
    quieter than the quiet start; it is nothing.
    """
    return _clamp(min(current or AUTO_START_VOLUME, AUTO_START_VOLUME))


class AutoLevel:
    """Bracket-and-close on the lowest usable level.

    Ramp up until a level is judged ok, then close between the highest
    quiet probe and the lowest ok one. The first ok is NOT the answer:
    the ramp doubles, so it can step straight over the crossing.
    """

    def __init__(self):
        self.lo = None            # (v, peak): highest too-quiet probe
        self.hi = None            # (v, peak): lowest too-loud / clipped
        self.ok = None            # (v, peak): LOWEST probe judged ok
        self.near = False         # the margin says the crossing is close
        self.crept = 0            # fine steps spent looking for it
        self.ceil = AUTO_EXPLORE_CEIL

    @staticmethod
    def verdict(peak, snr, clipped=False, thd_bound=None):
        """'loud' past the safe peak ceiling, 'ok' when hot enough AND
        clean enough AND the rig can see under the device, else 'quiet'.

        THE THIRD QUESTION is thd_bound, and a field ladder settled it.
        Peak and SNR say the capture is usable; they cannot say whether
        the distortion figure is a MEASUREMENT. On a CM106 into a
        coupler the midband stayed a bound until a recorded peak near
        -16 dBFS, well inside the old window, so the hunt stopped early
        and every THD number afterwards was a ceiling.

        ORDER MATTERS: the crossing decides where it can, and the peak
        floor is what remains when nothing can be said about the
        figure. The floor exists to keep a capture robust, and an SNR
        near fifty with a figure that IS a measurement is not a fragile
        capture.

        NOT A PRECISE OPTIMUM, and the code should not pretend
        otherwise: three ladders on one earphone put the crossing at
        75, 75 and 79 per cent, a wobble of 2.6 dB, because the
        question is a yes/no asked where the curve is flat.
        """
        if clipped or peak > AUTO_PEAK_CEIL:
            return "loud"
        if snr is None:
            return "quiet"
        last_dB = peak >= AUTO_PEAK_CEIL - 1.0
        if not (snr >= mc.SNR_WARN_DB + AUTO_SNR_MARGIN_DB
                or (snr >= mc.SNR_WARN_DB and last_dB)):
            return "quiet"
        if thd_bound is False:
            return "ok"
        if thd_bound is True:
            return "ok" if last_dB else "quiet"
        return "ok" if peak >= AUTO_PEAK_FLOOR else "quiet"

    def observe(self, v, peak, snr, clipped, thd_bound=None,
                margin_db=None):
        # THE RAMP STOPS DOUBLING NEAR THE ANSWER -- but only when
        # approaching from BELOW, only once the capture is worth
        # believing, and only a few times. A margin past the bar means
        # the crossing is behind us and the closing phase owns the
        # search; a margin under an untrustworthy capture means
        # nothing; and a margin that hovers at the bar without ever
        # clearing it would be crept after forever. All three cost
        # REACH, and the ramp is allowed twelve adjustments.
        self.near = (margin_db is not None and snr is not None
                     and not clipped and snr >= mc.SNR_WARN_DB
                     and self.crept < AUTO_NEAR_STEPS
                     and mb.THD_FLOOR_GAP_DB - AUTO_NEAR_DB < margin_db
                     < mb.THD_FLOOR_GAP_DB)
        if self.near:
            self.crept += 1
        said = self.verdict(peak, snr, clipped, thd_bound)
        if said == "loud":
            p = 0.0 if clipped else peak
            if self.hi is None or v < self.hi[0]:
                self.hi = (v, p)
        elif said == "quiet":
            if self.lo is None or v > self.lo[0]:
                self.lo = (v, peak)
            if v >= self.ceil - 1e-3:      # at the ceiling, still quiet
                self.ceil = 1.0            # -> the device needs more
        else:
            if self.ok is None or v < self.ok[0]:
                self.ok = (v, peak)
        return said

    def settled(self):
        """True once the LOWEST ok level is known well enough to stop.

        An ok level is a CEILING to close on, not a destination, and
        the stop must not require THIS probe to be the ok one: as the
        bracket narrows the probes land on the QUIET side, and
        demanding both left a field hunt circling 69, 64, 67, 68, 69
        with the answer already in hand.
        """
        if self.ok is None:
            return False
        if self.lo is None:
            return True                    # ok at the first probe
        return self.ok[0] / max(self.lo[0], 1e-6) <= AUTO_SETTLE_RATIO

    def phase(self):
        """What the search is doing, in one word, for the operator."""
        if self.ok is not None:
            return "closing"
        if self.hi is not None:
            return "bracketed"
        return "ramp"

    def next_volume(self, v):
        if self.ok and self.lo:                  # closing on the lowest ok
            nv = math.sqrt(self.lo[0] * self.ok[0])
        elif self.lo and self.hi:                # bracketed: bisect
            nv = math.sqrt(self.lo[0] * self.hi[0])
        elif self.hi:                            # too loud, no floor yet
            nv = self.hi[0] * AUTO_CLIP_BACKOFF
        else:                                    # climb for the loud end
            nv = min(v * (AUTO_RAMP_NEAR if self.near else AUTO_RAMP),
                     self.ceil)
        return _clamp(nv)


def summary(volume, probes):
    """What the search did, for the passport.

    THE SEARCH DESCRIBES ITSELF and the session merely carries it. A
    session records takes; asking it to also report how a level was
    chosen would put the search back inside it through the schema.
    """
    ok = [p for p in probes if not p.clipped and p.snr_db is not None]
    return {"enabled": True,
            "initial": round(probes[0].volume, 4) if probes else None,
            "adjustments": len(probes),
            "final": round(float(volume), 4),
            "in_window": bool(ok) and any(
                AUTO_PEAK_FLOOR <= p.peak_dbfs <= AUTO_PEAK_CEIL
                for p in ok)}


class Probe:
    """One rung: what a sweep at one level said."""

    __slots__ = ("volume", "peak_dbfs", "snr_db", "thd_pct", "thd_bound",
                 "margin_db", "clipped", "phase", "step")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def hunt(sink, source, channels, sink_name=None, analyze=0,
         sweep=None, freqs=None,
         pre_silence=1.0, post_silence=0.5, play_map=None,
         on_probe=None, should_stop=None, start=AUTO_START_VOLUME,
         max_adjust=AUTO_MAX_ADJUST):
    """Sweep at rising levels until the rig can see under the device.

    Returns (volume, probes). The volume is the walk's whole product;
    it is NOT left on the hardware, because the moratorium takes the
    measurement volume as a parameter and whoever measures next passes
    this number to their own claim. A lock is not a finding, and
    neither is a borrowed volume.

    `on_probe(probe)` is called after each sweep and `should_stop()` is
    asked before each, so a caller can narrate and interrupt.
    """
    sweep = sweep or mc.generate_sweep(262144, 48000, 20.0, 20000.0)
    freqs = mc.log_grid() if freqs is None else freqs
    outdir = tempfile.mkdtemp(prefix="pdeq-level-")
    wav = write_sweep_files(outdir, sweep, pre_silence, post_silence)
    duration = pre_silence + sweep.duration_s + post_silence
    name = sink_name or (sink.get("name") if isinstance(sink, dict)
                         else sink)
    if not name:
        raise ValueError("the moratorium needs the sink's node name")
    ctl = AutoLevel()
    v = start_volume(start)
    probes = []
    back = pw_backend.backend()

    try:
        for step in range(1, int(max_adjust) + 1):
            if should_stop is not None and should_stop():
                break
            # ONE CLAIM PER SWEEP, as the take does: the level moves
            # between rungs, and the moratorium is where a measurement
            # volume is set
            # THE NAME, NOT THE OBJECT: run_take wants the resolved
            # node and the moratorium wants the node's name, and the
            # two are not the same thing in this codebase
            back.moratorium_begin(name, v, mute_others=True)
            try:
                data, _info = run_take(sink, source, wav, duration,
                                       channels, sweep.fs, verify=False,
                                       channel_map=play_map)
            finally:
                back.moratorium_end()

            chan = np.asarray(data)[:, min(analyze, data.shape[1] - 1)]
            peak = float(np.max(np.abs(chan))) if chan.size else 0.0
            peak_db = (20.0 * math.log10(peak) if peak > 0 else -120.0)
            clipped = peak >= 0.999
            got = mc.analyze_take(chan, sweep, freqs)
            pct = bound = margin = None
            if not clipped:
                at = mb.thd_at(freqs, got.thd_db, got.thd_noise_db)
                if at:
                    pct, bound = at
                margin = mb.thd_margin_db(freqs, got.thd_db,
                                          got.thd_noise_db)
            snr = (float(got.snr_db)
                   if got.snr_db is not None
                   and math.isfinite(float(got.snr_db)) else None)

            ctl.observe(v, peak_db, snr, clipped, bound, margin)
            p = Probe(volume=v, peak_dbfs=peak_db, snr_db=snr,
                      thd_pct=pct, thd_bound=bound, margin_db=margin,
                      clipped=clipped, phase=ctl.phase(), step=step)
            probes.append(p)
            if on_probe is not None:
                on_probe(p)

            if ctl.settled():
                return _clamp(ctl.ok[0]), probes
            nv = ctl.next_volume(v)
            if abs(nv - v) < 1e-3:            # nowhere left to go
                break
            v = nv
    finally:
        for name in os.listdir(outdir):
            try:
                os.unlink(os.path.join(outdir, name))
            except OSError:
                pass
        try:
            os.rmdir(outdir)
        except OSError:
            pass

    # nothing satisfied every question: hand back the best seen rather
    # than nothing, and let the caller say so
    if ctl.ok is not None:
        return _clamp(ctl.ok[0]), probes
    return _clamp(v), probes
