"""PipeWireBackend: the heir of AudioBackend that speaks PipeWire.

Every verb delegates to the battle-tested functions in py
where one exists; what did not exist there yet (stream mute, sink
volume) landed there as this heir's permanent verbs. Reading the
graph dump is this class's native tongue: the foreign-stream reader
lives here, and the session's private copies retire when the weaving
step routes the program through the backend.

One instance per process: use backend().
"""

# ---- engine room (formerly py) ----
# Runtime bridge to PipeWire and, through the 'per-device-eq' metadata object,
# to the WirePlumber Lua hook.
#
# The app never talks to Lua directly: it writes a device's inline graph string
# into the metadata (`metadata_set`) and the hook -- subscribed to that object --
# applies it to the live node and re-applies on every reconnect. Reading state
# (sinks, channels, params, default) is done by shelling out to pw-dump /
# pw-metadata. No GTK here; only stdlib + the PipeWire CLI tools.

def _run(cmd, timeout=2.0):
    """Run a helper. A hung pw-* child is the classic way to freeze the GUI, so
    every call is bounded by a timeout; on timeout/failure we kill the child and
    return a sentinel CompletedProcess instead of blocking forever."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")
    except Exception as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))







def pw_dump():
    try:
        return json.loads(_run(["pw-dump"], timeout=5.0).stdout)
    except Exception:
        return []

def default_sink_name():
    try:
        out = _run(["pw-metadata", "-n", "default", "0", "default.audio.sink"]).stdout
        m = re.search(r"value:'?(\{.*?\})'?", out)
        if m:
            return json.loads(m.group(1)).get("name")
    except Exception:
        pass
    return None


def default_sink_from_dump(dump):
    """Default sink name from the 'default' Metadata object in a pw dump,
    or None -- lets one dump yield the default without a pw-metadata call."""
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Metadata":
            continue
        props = o.get("props") or (o.get("info") or {}).get("props") or {}
        if props.get("metadata.name") != "default":
            continue
        for e in (o.get("metadata") or []):
            if e.get("key") == "default.audio.sink":
                v = e.get("value")
                if isinstance(v, dict):
                    return v.get("name")
                if isinstance(v, str):
                    try:
                        return json.loads(v).get("name")
                    except Exception:
                        pass
    return None


def list_sinks(dump=None, default=None):
    dump = dump if dump is not None else pw_dump()
    if default is None:
        default = default_sink_name()
    sinks = []
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("media.class") == "Audio/Sink":
            name = p.get("node.name")
            if not name:
                continue
            sinks.append({"id": o["id"], "name": name,
                          "desc": p.get("node.description") or name,
                          "prio": p.get("priority.session") or 0,
                          "default": name == default})
    sinks.sort(key=lambda s: -(s["prio"] or 0))
    return sinks

def list_sources(dump=None):
    """Audio/Source nodes (measurement mics live here): id, name, desc,
    priority.session, sorted by priority. No 'default' flag on purpose --
    the system default source is the comms/webcam mic, never the
    measurement rig, so the measure window pre-selects the last-used
    source (per-sink recall) instead of the default."""
    dump = dump if dump is not None else pw_dump()
    sources = []
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("media.class") == "Audio/Source":
            name = p.get("node.name")
            if not name:
                continue
            sources.append({"id": o["id"], "name": name,
                            "desc": p.get("node.description") or name,
                            "prio": p.get("priority.session") or 0})
    sources.sort(key=lambda s: -(s["prio"] or 0))
    return sources

def node_params(name, dump=None):
    dump = dump if dump is not None else pw_dump()
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("node.name") == name:
            return (o.get("info") or {}).get("params") or {}, o["id"]
    return None, None

def resolve_sink_id(name, dump=None):
    _, nid = node_params(name, dump)
    return nid

def graph_loaded(name, dump=None):
    """Best-effort: a loaded in-node graph shows up as an extra Props block
    whose only key is 'params'."""
    params, _ = node_params(name, dump)
    if not params:
        return False
    for d in params.get("Props", []):
        if isinstance(d, dict) and list(d.keys()) == ["params"]:
            return True
    return False

def metadata_set(node_name, graph):
    """Write a device's graph into the 'per-device-eq' metadata. The WP hook is
    subscribed and applies it to the live node (and on every later reconnect).
    Stored as a plain string (no type tag), which the hook reads verbatim."""
    r = _run(["pw-metadata", "-n", METADATA_NAME, "0", node_name, graph])
    return r.returncode == 0 and "Found" in (r.stdout + r.stderr)


def metadata_clear(node_name):
    """Delete a device's key (Clean / unbound). The hook flattens the live node."""
    r = _run(["pw-metadata", "-n", METADATA_NAME, "-d", "0", node_name])
    return r.returncode == 0

_POS_FALLBACK = ["FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR"]



def _node_channels(name, dump=None):
    """Channel keys for any node (sink or source) from its negotiated
    Format position, falling back to channelVolumes length, then stereo."""
    params, _ = node_params(name, dump)
    pos, nch = None, None
    if params:
        for blk in (params.get("Format") or []):
            if isinstance(blk, dict):
                if blk.get("position"):
                    pos = blk["position"]
                if blk.get("channels"):
                    nch = blk["channels"]
        if nch is None:
            for d in (params.get("Props") or []):
                if isinstance(d, dict) and isinstance(d.get("channelVolumes"), list):
                    nch = len(d["channelVolumes"])
    if isinstance(pos, list) and pos:
        keys = [str(p) for p in pos]
    elif nch:
        keys = _POS_FALLBACK[:nch] if nch <= len(_POS_FALLBACK) \
               else ["Ch%d" % (i + 1) for i in range(nch)]
    else:
        keys = ["FL", "FR"]
    seen, out = {}, []
    for k in keys:
        if k in seen:
            seen[k] += 1; out.append("%s.%d" % (k, seen[k]))
        else:
            seen[k] = 0; out.append(k)
    return out


def sink_channels(name, dump=None):
    return _node_channels(name, dump)


def source_channels(name, dump=None):
    """Capture-channel keys for a source (mic/rig), e.g. ['FL','FR']. A
    measurement rig is 1- or 2-channel; the count is how many calibration
    files it needs, one per capture channel."""
    return _node_channels(name, dump)

def in_thread(fn):
    """Run fn on a daemon thread (off-main-loop subprocess work)."""
    import threading
    threading.Thread(target=fn, daemon=True).start()


# The PipeWire command-line tools we shell out to. Names are the same
# across distributions; the *package* that ships them is not, so we
# check for the tools and let the user install them as their distro
# prefers.
REQUIRED_TOOLS = ["pw-metadata", "pw-dump"]

def missing_tools_message(missing):
    return ("These PipeWire command-line tools are required but were not found "
            "in PATH:\n\n    %s\n\nInstall the PipeWire utilities with your "
            "distribution's package manager and try again." % "  ".join(missing))


PLAY_NODE = "pde-measure-sweep"
CAPTURE_NODE = "pde-measure-capture"
_STREAM_PROPS = ("{ node.name = %s, node.target = %d, "
                 "node.dont-reconnect = true, application.name = "
                 "\"per-device-eq measure\" }")

import json
import os
import re
import subprocess
import sys

from .config import METADATA_NAME
from .audio_backend import AudioBackend


# The heir's own verbs: they lived in py while the trio
# still had twins there; the doomed module only slims now, so the
# code that exists solely for this class lives with this class.
def metadata_get(key):
    r = _run(["pw-metadata", "-n", METADATA_NAME, "0", key])
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
    r = _run(["pw-cli", "set-param", str(node_id), "Props",
              "{ mute = %s }" % ("true" if mute else "false")])
    return r.returncode == 0


def set_sink_volume(sink_id, cubic):
    """wpctl writes through to the device Route where one exists; raw
    Props writes on ALSA sinks do not stick. Raises on failure."""
    r = _run(["wpctl", "set-volume", str(sink_id), "%.4f" % cubic])
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
            return metadata_clear(device)
        return metadata_set(device, value)

    def _push_volume(self, device, cubic):
        sink_id = resolve_sink_id(device)
        set_sink_volume(sink_id, cubic)

    def _push_stream_mutes(self, sink, mute, prior=None):
        if not mute:
            for node_id in (prior or []):
                set_stream_mute(node_id, False)
            return None
        dump = pw_dump()
        sink_id = resolve_sink_id(sink, dump)
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
        dump = pw_dump()
        try:
            sink_id = resolve_sink_id(device, dump)
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

    def missing_requirements(self):
        """Command-line tools the platform still needs."""
        import shutil
        return [t for t in REQUIRED_TOOLS
                if shutil.which(t) is None]

    def meter_available(self):
        """pw-record is needed only by the tier-2 live meter: its
        absence degrades the app to the static tier-1 estimate,
        nothing more. PDEQ_NO_METER=1 forces the degraded mode for
        diagnostics: the GUI then runs without its sink-monitor tap,
        so a measurement can be compared with and without that
        stream present."""
        import shutil
        if os.environ.get("PDEQ_NO_METER"):
            return False
        return shutil.which("pw-record") is not None

    def hook_protocol(self):
        """The protocol the LOADED hook stamped into the channel.
        Returns (found, version): found False = no metadata object
        (WirePlumber or the hook is not up -- the install/restart
        narrations own that story, say nothing extra); found True
        with version None = a pre-versioning hook."""
        r = _run(["pw-metadata", "-n", METADATA_NAME, "0", "protocol"])
        out = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"key:'protocol'\s+value:'([^']*)'", out)
        return ("Found" in out), (m.group(1) if m else None)

    def output_channels(self, device):
        """Channel positions of an output device."""
        return sink_channels(device)

    def input_channels(self, device):
        """Channel positions of an input device."""
        return source_channels(device)

    # -- observed side ---------------------------------------------------

    def _pull(self, dump=None):
        if dump is None:
            dump = pw_dump()
        default = default_sink_from_dump(dump)
        if default is None:
            default = default_sink_name()
        return {
            "sinks": list_sinks(dump, default=default),
            "sources": list_sources(dump),
            "default_sink": default,
        }

    def update(self, dump=None):
        """Refresh from one pw_dump (fetched if not given). Returns
        True if the population changed. Synchronous; tests and the
        birth reconcile call it directly."""
        return self._ingest(self._pull(dump))

    # -- the heartbeat: one poll feeds every window ----------------------

    POLL_S = 3

    _timer = 0
    _busy = False
    _glib = None

    def start(self, interval_s=None):
        """Begin the periodic refresh (idempotent). Uses GLib; only
        the app calls this -- tests drive update() directly."""
        if self._timer:
            return
        from gi.repository import GLib
        self._glib = GLib
        self._timer = GLib.timeout_add_seconds(
            interval_s or self.POLL_S, self._tick)

    def stop(self):
        if self._timer and self._glib is not None:
            self._glib.source_remove(self._timer)
        self._timer = 0

    def _tick(self):
        if self._busy:
            return True                  # previous refresh running
        self._busy = True

        def work():
            snap = None
            try:
                snap = self._pull()
            finally:
                self._glib.idle_add(self._apply_snap, snap)
        in_thread(work)
        return True                      # keep the timer running

    def _apply_snap(self, snap):
        self._busy = False
        if snap is not None:
            self._ingest(snap)
        return False

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
