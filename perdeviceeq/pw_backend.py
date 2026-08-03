"""PipeWireBackend: the heir of AudioBackend that speaks PipeWire.

Every verb delegates to the battle-tested functions in pipewire.py
where one exists; what did not exist there yet (stream mute, sink
volume) landed there as this heir's permanent verbs. Reading the
graph dump is this class's native tongue: the foreign-stream reader
lives here, and the session's private copies retire when the weaving
step routes the program through the backend.

One instance per process: use backend().
"""

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
    terminate() and alive() for both."""

    def __init__(self, proc):
        self._proc = proc

    def read(self, nbytes):
        return self._proc.stdout.read(nbytes)

    def wait(self, timeout=None):
        return self._proc.wait(timeout=timeout)

    def terminate(self):
        if self._proc.poll() is None:
            self._proc.terminate()
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
        cmd = ["pw-record", "--raw",
               "-P", "{ node.name = pde-backend-capture, "
                     "node.dont-reconnect = true }",
               "--format", "f32", "--rate", str(int(rate)),
               "--channels", str(int(channels)),
               "--target", device, "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        return StreamHandle(proc)

    def monitor_capture(self, sink, channels, rate):
        return StreamHandle(
            pipewire.monitor_capture(sink, channels, rate))

    def play(self, device, wav_path, stream_volume=1.0):
        cmd = ["pw-play", "--volume", "%.4f" % stream_volume,
               "--target", device, wav_path]
        proc = subprocess.Popen(cmd,
                                stderr=subprocess.DEVNULL)
        return StreamHandle(proc)


_instance = None


def backend():
    """The process-wide PipeWireBackend."""
    global _instance
    if _instance is None:
        _instance = PipeWireBackend()
    return _instance
