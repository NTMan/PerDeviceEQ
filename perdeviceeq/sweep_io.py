"""Moving one sweep through PipeWire: the floor everything else stands on.

THREE CONSUMERS OF THE HARDWARE, one at a time -- the sensitivity
search, the level search, and the take. They are independent jobs, and
what they share is not each other but this: resolving the nodes,
reading and claiming the graph, playing a wav while recording, proving
the sound went where it was aimed, and writing the wavs out.

All of it used to live in measure_session, which meant the level
search had to import from the module it was being taken OUT of -- a
function-level import to dodge a cycle, and the last thread tying two
jobs together. Nothing here knows what a take is, what a profile is,
or that a search exists.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import numpy as np

from . import debug
from . import measure_core as mc
from . import pw_backend
from .pw_backend import playback_channels, sink_channels


METADATA_NAME = "per-device-eq"          # same object the app + WP hook use
PLAY_NODE = pw_backend.PLAY_NODE
CAPTURE_NODE = pw_backend.CAPTURE_NODE
SINK_API_PREFIXES = ("alsa", "bluez")    # "real device" whitelist
FULLSCALE = 0.999                        # |sample| >= this = clipped
BT_WARM_S = 2.0                          # silence played to a bluez sink
#                                          after a volume change: absolute
#                                          volume applies asynchronously
#                                          (observed ~3 s into a sweep)
REPAIR_MAX_MS = 2.0                      # interp this many ms of dropouts;
#                                          more non-finite than that = fault
VERIFY_AFTER_S = 0.4                     # pw-play start -> pw-dump link check
VERIFY_TIMEOUT_S = 3.0
CAPTURE_LEAD_S = 0.5                     # record head start (extra pre-roll)
EXTRA_TAIL_S = 1.0                       # decay + link latency margin
class MeasureError(RuntimeError):
    pass


class RefusalError(RuntimeError):
    """Precondition not met; nothing was played, nothing was changed."""


class FaultyCaptureError(MeasureError):
    """A flood of non-finite samples: a broken input, not a dropout.
    Neutral wording; the CLI appends its --channel flag hint."""

    def __init__(self, channel, channels, bad):
        super().__init__(
            "channel %d capture has %d non-finite sample(s) (NaN/Inf) -- "
            "too many to be a dropout; the input is faulty, not merely "
            "quiet." % (channel, bad))
        self.channel = channel
        self.channels = channels
        self.bad = bad


class MeasureCancelled(Exception):
    """A sweep was cancelled by the user (Stop). A control-flow signal,
    not an error: the child processes are killed and the partial capture
    is discarded, so nothing is stored."""


# --- subprocess plumbing -----------------------------------------------------

def _run(cmd, timeout=5.0):
    """Bounded helper run: a hung pw-* child must never hang the runner."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")
    except Exception as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def require_tools(tools):
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        raise RefusalError("required PipeWire tools not in PATH: %s"
                           % " ".join(missing))


# --- pw-dump graph inspection ------------------------------------------------

def pw_dump():
    r = _run(["pw-dump"], timeout=10.0)
    if r.returncode != 0:
        raise MeasureError("pw-dump failed: %s" % (r.stderr.strip() or
                                                   r.returncode))
    try:
        return json.loads(r.stdout)
    except ValueError as e:
        raise MeasureError("pw-dump returned unparsable JSON: %s" % e)


def _props(obj):
    return (obj.get("info") or {}).get("props") or {}


def _params(obj):
    return (obj.get("info") or {}).get("params") or {}


def _nodes(dump):
    return [o for o in dump if o.get("type") == "PipeWire:Interface:Node"]


def _links(dump):
    out = []
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Link":
            continue
        i = o.get("info") or {}
        out.append((i.get("output-node-id"), i.get("input-node-id")))
    return out


def resolve_node(dump, ident, want_class):
    """id, exact node.name, or unique case-insensitive substring of
    node.name/node.description among nodes of `want_class`."""
    ns = _nodes(dump)
    if re.fullmatch(r"\d+", str(ident)):
        for o in ns:
            if o["id"] == int(ident):
                return o
        raise RefusalError("no node with id %s" % ident)
    for o in ns:
        if _props(o).get("node.name") == ident:
            return o
    needle = str(ident).lower()
    hits = [o for o in ns
            if _props(o).get("media.class") == want_class
            and (needle in (_props(o).get("node.name") or "").lower()
                 or needle in (_props(o).get("node.description")
                               or "").lower())]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise RefusalError("no %s matches %r (pw-dump for names)"
                           % (want_class, ident))
    names = ", ".join(_props(o).get("node.name") or "?" for o in hits)
    raise RefusalError("%r is ambiguous: %s" % (ident, names))


def node_ident(obj):
    p = _props(obj)
    return {"id": obj["id"], "name": p.get("node.name"),
            "description": p.get("node.description"),
            "media_class": p.get("media.class"),
            "device_api": p.get("device.api")}


def check_sink_identity(sink):
    """Refuse anything that is not a real output device: measuring into a
    loopback/effect sink is measuring the wrong thing."""
    p = _props(sink)
    problems = []
    if p.get("media.class") != "Audio/Sink":
        problems.append("media.class is %r, expected Audio/Sink"
                        % p.get("media.class"))
    api = p.get("device.api") or ""
    if not api.startswith(SINK_API_PREFIXES):
        problems.append("device.api is %r, expected alsa*/bluez* "
                        "(a virtual/effect sink is not the device)" % api)
    if problems:
        raise RefusalError("target %r is not a measurable device:\n  %s"
                           % (p.get("node.name"), "\n  ".join(problems)))


def props_param(obj):
    """The Props param block that carries volume/mute/channelVolumes."""
    for d in _params(obj).get("Props", []):
        if isinstance(d, dict) and "channelVolumes" in d:
            return d
    return {}


def foreign_streams(dump, sink_id):
    """Output streams currently linked into the sink, ours excluded."""
    linked = {a for a, b in _links(dump) if b == sink_id}
    out = []
    for o in _nodes(dump):
        if o["id"] not in linked:
            continue
        p = _props(o)
        if p.get("media.class") != "Stream/Output/Audio":
            continue
        name = p.get("node.name") or ""
        if name.startswith("pde-measure"):
            continue
        out.append({"id": o["id"], "node_name": name,
                    "app": p.get("application.name") or p.get("app.name"),
                    "prior_mute": bool(props_param(o).get("mute", False)),
                    "muted_for_measure": False})
    return out


# --- per-device-eq metadata (profile bypass) ---------------------------------


def metadata_set(key, value):
    r = _run(["pw-metadata", "-n", METADATA_NAME, "0", key, value])
    return r.returncode == 0 and "Found" in (r.stdout + r.stderr)


def metadata_clear(key):
    return _run(["pw-metadata", "-n", METADATA_NAME, "-d", "0", key]) \
        .returncode == 0



# --- volume ------------------------------------------------------------------

def sink_volume_state(dump, sink_id):
    """(cubic, raw channelVolumes, mute) from the sink's Props param.
    PipeWire stores channelVolumes linear; the user-facing value (wpctl,
    GNOME) is its cube root."""
    for o in _nodes(dump):
        if o["id"] == sink_id:
            d = props_param(o)
            cv = [float(v) for v in d.get("channelVolumes") or []]
            cubic = (sum(cv) / len(cv)) ** (1.0 / 3.0) if cv else None
            return cubic, cv, bool(d.get("mute", False))
    return None, [], False



def watch_volume_ends(sink_id, stop_evt, source_id=None):
    """Sample every volume that can affect a take, ten times a
    second, and print any change with a timestamp
    (PDEQ_TRACE_VOL=1). Watched: the sink device, the source
    device, and every Stream/* node -- the playback and
    capture streams carry their own channelVolumes and
    softVolumes, which no device slider shows and which
    session policy may touch asynchronously after connect."""
    t0 = time.monotonic()
    last = {}
    while not stop_evt.wait(0.1):
        try:
            dump = pw_dump()
        except Exception:
            continue
        for o in _nodes(dump):
            props = _props(o)
            cls = props.get("media.class") or ""
            oid = o["id"]
            if not (oid in (sink_id, source_id)
                    or cls.startswith("Stream/")):
                continue
            d = props_param(o)
            cv = tuple(float(v) for v in
                       d.get("channelVolumes") or ())
            sv = tuple(float(v) for v in
                       d.get("softVolumes") or ())
            if oid not in last:
                # census: announce every end on first sight,
                # including streams born mid-take -- a live
                # watcher is distinguishable from a dead one,
                # and a census missing our own streams
                # convicts the node filter
                debug.vol_trace(
                    "t=+%.3fs seen %s(%d) %s cv=%s sv=%s"
                    % (time.monotonic() - t0,
                       props.get("node.name") or "?",
                       oid, cls or "device",
                       ["%.4f" % v for v in cv],
                       ["%.4f" % v for v in sv]))
                last[oid] = (cv, sv)
                continue
            if last[oid] != (cv, sv):
                name = props.get("node.name") or "?"
                debug.vol_trace(
                    "t=+%.3fs end=%s(%d) cv=%s sv=%s"
                    % (time.monotonic() - t0, name, oid,
                       ["%.4f" % v for v in cv],
                       ["%.4f" % v for v in sv]))
                last[oid] = (cv, sv)


def await_sink_volume(sink_id, cubic, timeout_s=2.5):
    """Poll the sink until a just-written volume actually LANDS
    (channelVolumes ~= cubic**3), or the deadline passes. The
    field corpse: a take whose head played at the user's
    listening volume with our measurement level ramping in 2.6
    seconds later -- a 16 dB step mid-sweep, once per session,
    under first-take congestion. The old cure ("settle a
    Bluetooth sink") trusted the transport to name the risk;
    the write's round trip can be late on ANY sink, so the
    settle is now a READBACK, not a faith. Returns True when
    the volume is seen in place; on timeout returns False and
    says so -- a late volume must never fail the take, only
    stop being silent."""
    target = float(cubic) ** 3
    tol = max(0.02 * target, 1e-4)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            cv, _ = sink_applied_volumes(pw_dump(), sink_id)
        except Exception:
            cv = None
        if cv and max(abs(v - target) for v in cv) <= tol:
            return True
        if time.monotonic() >= deadline:
            print("volume settle timeout on sink %s; "
                  "sweeping anyway" % sink_id)
            return False
        time.sleep(0.05)


def sink_applied_volumes(dump, sink_id):
    """(channelVolumes, softVolumes) linear arrays from the sink's
    Props. channelVolumes is the user-facing volume cubed; softVolumes
    is the gain PipeWire actually multiplies into the samples -- equal
    on a software-volume sink, pinned at 1.0 when the device does the
    volume in hardware (a BT sink's absolute volume), where the applied
    gain is genuinely unknowable from the node."""
    for o in _nodes(dump):
        if o["id"] == sink_id:
            d = props_param(o)
            cv = [float(v) for v in d.get("channelVolumes") or []]
            sv = [float(v) for v in d.get("softVolumes") or []]
            return cv, sv
    return [], []


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp_vol(v):
    return max(0.02, min(1.0, v))


def peak_dbfs(x):
    if not len(x):
        return float("-inf")
    p = float(np.max(np.abs(x)))
    if not math.isfinite(p):
        return float("nan")               # NaN/Inf in the capture
    return 20.0 * math.log10(p) if p > 0 else float("-inf")


def repair_nonfinite(x):
    """Replace isolated non-finite samples (a capture xrun/dropout) with
    a linear interpolation of the surrounding good samples."""
    bad = ~np.isfinite(x)
    idx = np.arange(len(x))
    out = x.copy()
    out[bad] = np.interp(idx[bad], idx[~bad], x[~bad])
    return out


# --- capture -----------------------------------------------------------------

class CaptureStream:
    """pw-record streaming raw interleaved f32 to stdout (filename '-'),
    accumulated on a reader thread. Raw-to-stdout instead of letting
    pw-record write the wav: no header-finalization worries on kill, and
    the stop condition is an exact frame count, not a timer."""

    def __init__(self, target, channels, rate):
        self.channels = channels
        self.rate = rate
        self.target = int(target)
        # The spawn (--raw, node.target pinning, the measure dress)
        # is the backend's verb now; the exact-frame framing below
        # stays measurement law.
        self.proc = pw_backend.backend().capture(
            self.target, channels, rate)
        self._chunks = []
        self._bytes = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self):
        while True:
            chunk = self.proc.read(65536)
            if not chunk:
                return
            with self._lock:
                self._chunks.append(chunk)
                self._bytes += len(chunk)

    def wait_frames(self, n_frames, timeout, cancel=None):
        need = n_frames * self.channels * 4
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise MeasureCancelled()
            with self._lock:
                if self._bytes >= need:
                    return
            if self.proc.poll() is not None:
                raise MeasureError("pw-record exited early (rc=%s)"
                                   % self.proc.returncode)
            time.sleep(0.05)
        raise MeasureError("capture timed out: got %d of %d frames "
                           "(is the mic source alive?)"
                           % (self._bytes // (4 * self.channels), n_frames))

    def stop(self):
        self.proc.terminate(kill_after=3)
        self._thread.join(timeout=3)

    def data(self):
        buf = b"".join(self._chunks)
        n = len(buf) // (4 * self.channels) * self.channels
        return np.frombuffer(buf[:n * 4], dtype="<f4") \
            .reshape(-1, self.channels).astype(np.float64)


# --- playback + path verification --------------------------------------------

def _play_error(play):
    return MeasureError("pw-play failed (rc=%d): %s"
                        % (play.returncode,
                           play.stderr_read().strip()))


def verify_path(sink, play):
    """The sweep stream must exist and link into the target node and
    NOTHING else. Returns the path_clean dict; raises on a dirty path
    (and on pw-play dying before the stream ever links)."""
    deadline = time.monotonic() + VERIFY_TIMEOUT_S
    stream, targets = None, set()
    while time.monotonic() < deadline:
        if play.poll() is not None and play.returncode != 0:
            raise _play_error(play)
        dump = pw_dump()
        for o in _nodes(dump):
            if _props(o).get("node.name") == PLAY_NODE:
                stream = o
        if stream is not None:
            targets = {b for a, b in _links(dump) if a == stream["id"]}
            if targets:
                break
        time.sleep(0.2)
    if stream is None or not targets:
        raise MeasureError("sweep stream never appeared/linked; "
                           "cannot verify the playback path")
    unknown = []
    for t in sorted(targets - {sink["id"]}):
        name = "?"
        for o in _nodes(dump):
            if o["id"] == t:
                name = _props(o).get("node.name") or "?"
        unknown.append({"id": t, "node_name": name})
    d = props_param(stream)
    cv = d.get("channelVolumes") or [None]
    vol = d.get("volume")
    stream_volume = vol if vol is not None else cv[0]
    info = {"verified": not unknown and sink["id"] in targets,
            "target": node_ident(sink),
            "playback_stream": {"id": stream["id"], "name": PLAY_NODE,
                                "volume": stream_volume},
            "unknown_nodes": unknown}
    if unknown:
        raise MeasureError(
            "playback path is not clean, refusing to measure through an "
            "unidentified chain: %s"
            % ", ".join("%(node_name)s (id %(id)s)" % u for u in unknown))
    if stream_volume is not None and abs(stream_volume - 1.0) > 1e-3:
        print("WARNING: sweep stream volume is %.3f, not 1.0 (session "
              "manager restore rule?)" % stream_volume, file=sys.stderr)
    return info


def verify_capture(source, cap):
    """The capture stream must link FROM the requested source and no
    other. Raises if it is linked to a different source -- a wrong
    default source hijacks the stream and silently records the wrong mic
    (quiet, garbage SNR) instead of erroring. Mirrors verify_path."""
    deadline = time.monotonic() + VERIFY_TIMEOUT_S
    node, sources = None, set()
    while time.monotonic() < deadline:
        if cap.proc.poll() is not None:
            raise MeasureError("pw-record exited early (rc=%s)"
                               % cap.proc.returncode)
        dump = pw_dump()
        for o in _nodes(dump):
            if _props(o).get("node.name") == CAPTURE_NODE:
                node = o
        if node is not None:
            sources = {a for a, b in _links(dump) if b == node["id"]}
            if sources:
                break
        time.sleep(0.2)
    if node is None or not sources:
        raise MeasureError("capture stream never appeared/linked; "
                           "cannot verify the mic path (is the source "
                           "alive?)")
    wrong = []
    for s in sorted(sources - {source["id"]}):
        name = "?"
        for o in _nodes(dump):
            if o["id"] == s:
                name = _props(o).get("node.name") or "?"
        wrong.append({"id": s, "node_name": name})
    if source["id"] not in sources or wrong:
        raise MeasureError(
            "the capture opened on the wrong device: got %s, "
            "wanted %s -- the target was not honored, the take "
            "is refused"
            % (", ".join("%(node_name)s (id %(id)s)" % w for w in wrong)
               or "nothing", node_ident(source)["name"]))
    return {"verified": True, "source": node_ident(source)}


def _aim_layout(name, dump=None):
    """The names a sweep can be aimed with, in channel order.

    The ports first, because that is what pw-play matches against; the
    position list only as a fallback for a node with no readable ports.
    Getting this backwards spread every sweep over the whole card: the
    M62 answers AUX0..AUX9 for audio.position while its ports are
    called FL..SL, so a sweep tagged AUX0 matched nothing at all."""
    return (playback_channels(name, dump)
            or sink_channels(name, dump))


def run_take(sink, source, wav_path, wav_duration_s, channels, rate,
             verify, raw_dump_path=None, cancel=None, channel_map=None):
    """One sweep: start capture, play the wav, collect exactly enough
    frames. Returns (frames x channels array, path_clean or None). With
    raw_dump_path, the untouched capture is written there first,
    for glitch diagnostics."""
    cap = CaptureStream(source["id"], channels, rate)
    play = None
    path_info = None
    try:
        time.sleep(CAPTURE_LEAD_S)
        if verify:
            cap_info = verify_capture(source, cap)
        play = pw_backend.backend().play(
            sink["id"], wav_path, stream_volume=1.0,
            channel_map=channel_map)
        if verify:
            time.sleep(VERIFY_AFTER_S)
            path_info = verify_path(sink, play)
            path_info["capture"] = cap_info
        deadline = time.monotonic() + wav_duration_s + 30
        while True:
            if cancel is not None and cancel.is_set():
                raise MeasureCancelled()
            rc = play.poll()
            if rc is not None:
                break
            if time.monotonic() > deadline:
                raise MeasureError("pw-play did not finish in time")
            time.sleep(0.05)
        if rc != 0:
            raise _play_error(play)
        need = int((CAPTURE_LEAD_S + wav_duration_s + EXTRA_TAIL_S) * rate)
        cap.wait_frames(need, timeout=wav_duration_s + 60, cancel=cancel)
    finally:
        if play is not None and play.poll() is None:
            play.kill()
        cap.stop()
    if raw_dump_path is not None:
        import soundfile as sf
        sf.write(raw_dump_path, cap.data(), rate, subtype="FLOAT")
    return cap.data(), path_info


# --- sweep files ---------------------------------------------------------

def write_sweep_files(outdir, sweep, pre_s, post_s):
    import soundfile as sf
    pad0 = np.zeros(int(pre_s * sweep.fs))
    pad1 = np.zeros(int(post_s * sweep.fs))
    wav = os.path.join(outdir, "sweep.wav")
    sf.write(wav, np.concatenate([pad0, sweep.signal, pad1])
             .astype("float32"), sweep.fs, subtype="FLOAT")
    sf.write(os.path.join(outdir, "sweep-inverse.wav"),
             mc.inverse_sweep(sweep).astype("float32"), sweep.fs,
             subtype="FLOAT")
    with open(wav + ".json", "w") as f:
        json.dump({"n_samples": sweep.n_samples, "fs": sweep.fs,
                   "f_start": sweep.f_start, "f_end": sweep.f_end,
                   "level_dbfs": sweep.level_dbfs, "pre_silence_s": pre_s,
                   "post_silence_s": post_s}, f, indent=1)
    return wav


def save_take_wav(outdir, index, data, rate):
    import soundfile as sf
    path = os.path.join(outdir, "take%02d.wav" % index)
    sf.write(path, data.astype("float32"), rate, subtype="FLOAT")
    return path


def default_save_base():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local = os.path.join(root, "tests", "fixtures-local")
    return local if os.path.isdir(local) else os.getcwd()


# --- session: the wizard-facing single-take API ---------------------------

DEBUG_RAW_ENV = "PDEQ_DEBUG_RAW"
