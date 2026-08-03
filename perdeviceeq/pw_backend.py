"""PipeWireBackend: the heir of AudioBackend that speaks PipeWire.

Every verb delegates to the battle-tested functions in pipewire.py
where one exists; what did not exist there yet (stream mute, sink
volume) landed there as this heir's permanent verbs. Reading the
graph dump is this class's native tongue: the foreign-stream reader
lives here, and the session's private copies retire when the weaving
step routes the program through the backend.

One instance per process: use backend().
"""

import subprocess

from . import pipewire
from .audio_backend import AudioBackend


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
            pipewire.metadata_clear(device)
        else:
            pipewire.metadata_set(device, value)

    def _push_volume(self, device, cubic):
        sink_id = pipewire.resolve_sink_id(device)
        pipewire.set_sink_volume(sink_id, cubic)

    def _push_stream_mutes(self, sink, mute, prior=None):
        if not mute:
            for node_id in (prior or []):
                pipewire.set_stream_mute(node_id, False)
            return None
        dump = pipewire.pw_dump()
        sink_id = pipewire.resolve_sink_id(sink, dump)
        muted = []
        for st in self._foreign_output_streams(dump, sink_id):
            if st["prior_mute"]:
                continue
            if pipewire.set_stream_mute(st["id"], True):
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
