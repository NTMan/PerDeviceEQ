"""PipeWireBackend: the heir of AudioBackend that speaks PipeWire.

Every verb delegates to the battle-tested functions in pipewire.py
where one exists; what did not exist there yet (stream mute, sink
volume) landed there as this heir's permanent verbs. Reading the
graph dump is this class's native tongue: the foreign-stream reader
lives here, and the session's private copies retire when the weaving
step routes the program through the backend.

One instance per process: use backend().
"""

PLAY_NODE = "pde-measure-sweep"
CAPTURE_NODE = "pde-measure-capture"
_STREAM_PROPS = ("{ node.name = %s, node.target = %d, "
                 "node.dont-reconnect = true, application.name = "
                 "\"per-device-eq measure\" }")

import os
import re
import subprocess
import sys

from . import pipewire
from .audio_backend import AudioBackend


# The heir's own verbs: they lived in pipewire.py while the trio
# still had twins there; the doomed module only slims now, so the
# code that exists solely for this class lives with this class.
def metadata_get(key):
    r = pipewire._run(["pw-metadata", "-n", pipewire.METADATA_NAME, "0", key])
    m = re.search(r"key:'%s' value:'(.*?)' type:" % re.escape(key),
                  r.stdout, re.S)
    return m.group(1) if m else None


def wpstate_get(key):
    """Read a sink's graph from the WirePlumber hook's persisted state
    (a GKeyFile at $XDG_STATE_HOME/wireplumber/per-device-eq). The hook
    seeds its runtime table from here on a cold start and does NOT
    publish persisted graphs into the metadata, so a freshly-booted
    session where the GUI was never opened has the profile ONLY here."""
    base = os.environ.get("XDG_STATE_HOME") \
        or os.path.expanduser("~/.local/state")
    path = os.path.join(base, "wireplumber", "per-device-eq")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("[") or "=" not in line:
                    continue
                k, v = line.split("=", 1)     # node.name has no '=' itself
                if k == key:
                    return v or None
    except OSError:
        pass
    return None


def set_stream_mute(node_id, mute):
    """Props mute on a stream node; True on success. The backend's
    verb; the session's private twin retires when the weaving step
    routes it here."""
    r = pipewire._run(["pw-cli", "set-param", str(node_id), "Props",
              "{ mute = %s }" % ("true" if mute else "false")])
    return r.returncode == 0


def set_sink_volume(sink_id, cubic):
    """wpctl writes through to the device Route where one exists; raw
    Props writes on ALSA sinks do not stick. Raises on failure."""
    r = pipewire._run(["wpctl", "set-volume", str(sink_id), "%.4f" % cubic])
    if r.returncode != 0:
        raise RuntimeError("wpctl set-volume failed: %s"
                           % r.stderr.strip())


class StreamHandle:
    """A live capture or playback stream. read(n) for captures
    (raw interleaved bytes from stdout), wait() for playbacks,
    terminate() and alive() for both; poll(), returncode and
    stderr_read() serve the path courts."""

    def __init__(self, proc):
        self._proc = proc

    def read(self, nbytes):
        return self._proc.stdout.read(nbytes)

    def wait(self, timeout=None):
        return self._proc.wait(timeout=timeout)

    def poll(self):
        return self._proc.poll()

    @property
    def returncode(self):
        return self._proc.returncode

    def stderr_read(self):
        if self._proc.stderr is None:
            return ""
        return self._proc.stderr.read() or ""

    def kill(self):
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()

    def terminate(self, kill_after=3.0):
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=kill_after)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc.wait()

    def alive(self):
        return self._proc.poll() is None


class PipeWireBackend(AudioBackend):
    """The audio server is PipeWire; devices are node names."""

    # -- state verbs ---------------------------------------------------

    def _push_graph(self, device, value):
        if value is None:
            return pipewire.metadata_clear(device)
        return pipewire.metadata_set(device, value)

    def _push_volume(self, device, cubic):
        sink_id = pipewire.resolve_sink_id(device)
        set_sink_volume(sink_id, cubic)

    def _push_stream_mutes(self, sink, mute, prior=None):
        if not mute:
            for node_id in (prior or []):
                set_stream_mute(node_id, False)
            return None
        dump = pipewire.pw_dump()
        sink_id = pipewire.resolve_sink_id(sink, dump)
        muted = []
        for st in self._foreign_output_streams(dump, sink_id):
            if st["prior_mute"]:
                continue
            if set_stream_mute(st["id"], True):
                muted.append(st["id"])
        return muted

    @staticmethod
    def _foreign_output_streams(dump, sink_id):
        """Output streams linked into the sink, ours excluded."""
        linked = set()
        for o in dump:
            if not str(o.get("type", "")).endswith("Link"):
                continue
            info = o.get("info") or {}
            if info.get("input-node-id") == sink_id:
                linked.add(info.get("output-node-id"))
        out = []
        for o in dump:
            if not str(o.get("type", "")).endswith("Node"):
                continue
            if o.get("id") not in linked:
                continue
            props = (o.get("info") or {}).get("props") or {}
            if props.get("media.class") != "Stream/Output/Audio":
                continue
            name = props.get("node.name") or ""
            if name.startswith("pde-measure"):
                continue
            prior = False
            params = (o.get("info") or {}).get("params") or {}
            for entry in params.get("Props") or []:
                if "mute" in entry:
                    prior = bool(entry["mute"])
            out.append({"id": o["id"], "node_name": name,
                        "prior_mute": prior})
        return out

    def _read_graph(self, device):
        value = metadata_get(device)
        if value is not None:
            return value, "metadata"
        value = wpstate_get(device)
        return value, ("wpstate" if value is not None else None)

    def _read_volume(self, device):
        dump = pipewire.pw_dump()
        try:
            sink_id = pipewire.resolve_sink_id(device, dump)
        except Exception:
            return None
        for o in dump:
            if o.get("id") != sink_id:
                continue
            params = (o.get("info") or {}).get("params") or {}
            for entry in params.get("Props") or []:
                cv = entry.get("channelVolumes")
                if cv:
                    return (sum(cv) / len(cv)) ** (1.0 / 3.0)
        return None

    def _restore_failed(self, device, value):
        print("CRITICAL: failed to restore the EQ profile; put it "
              "back manually:\n  pw-metadata -n per-device-eq 0 "
              "'%s' '%s'" % (device, value), file=sys.stderr)

    # -- observed side ---------------------------------------------------

    def _pull(self):
        dump = pipewire.pw_dump()
        return {
            "sinks": pipewire.list_sinks(dump),
            "sources": pipewire.list_sources(dump),
            "default_sink": pipewire.default_sink_from_dump(dump),
        }

    # -- measurement streams ---------------------------------------------

    def capture(self, device, channels, rate):
        """Raw interleaved f32 from `device` (a node id), pinned via
        node.target (NOT --target, which the session manager relinks
        to the default source) and named for the path courts."""
        cmd = ["pw-record", "--raw",
               "-P", _STREAM_PROPS % (CAPTURE_NODE, int(device)),
               "--format", "f32", "--rate", str(int(rate)),
               "--channels", str(int(channels)), "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        return StreamHandle(proc)

    def monitor_capture(self, sink, channels, rate=48000):
        """Spawn pw-record on a sink's monitor (PRE-EQ tap in the in-node
        topology) streaming raw interleaved f32 to stdout. Returns the Popen;
        the caller owns its lifetime and reads .stdout.

        Privacy note (field-verified observations): this capture alone does
        NOT light GNOME's microphone indicator (monitor-source recordings are
        excluded), and gnome-control-center's Sound page alone does not
        either -- the icon appears only when BOTH run, and dies with the
        panel while this capture keeps going. The trigger is therefore an
        interaction (likely an extra meter stream the panel creates in
        reaction to a foreign recording); `pactl list source-outputs` while
        the icon is lit names the culprit. The stream stays named anyway, so
        mixer UIs show who is listening to what.

        node.dont-reconnect pins the tap to its pipe: without it,
        WirePlumber re-parents a capture whose target died onto the
        DEFAULT sink's monitor, and rerouted streams do not come home
        when the target returns. A sink that forks per card profile
        (IL-DSP: analog and iec958 alternate, one node at a time) can
        die and be reborn between two of our polls, so the wander was
        invisible to the app -- the meter kept dancing to another
        device's music (field catch). With the flag the tap dies with
        its pipe; the GUI notices the dead worker and re-arms."""
        cmd = ["pw-record", "--target", str(sink),
               "-P", "{ stream.capture.sink = true, node.name = per-device-eq-meter,"
                     " node.dont-reconnect = true,"
                     " application.name = \"Per-Device EQ\" }",
               "--format", "f32", "--rate", str(int(rate)),
               "--channels", str(int(channels)), "-"]
        return StreamHandle(subprocess.Popen(
            cmd, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL))

    def play(self, device, wav_path, stream_volume=1.0,
             channel_map=None):
        """Play a wav into `device` (a node id), pinned and named
        like capture(); stderr is kept for the play court."""
        cmd = ["pw-play", "--volume", "%.1f" % stream_volume,
               "-P", _STREAM_PROPS % (PLAY_NODE, int(device))]
        if channel_map:
            cmd += ["--channel-map", channel_map]
        cmd.append(wav_path)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        return StreamHandle(proc)


_instance = None


def backend():
    """The process-wide PipeWireBackend."""
    global _instance
    if _instance is None:
        _instance = PipeWireBackend()
    return _instance
