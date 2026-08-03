"""The audio-server seam: one authority manages all the audio
hardware the program touches.

Everything platform-dependent lives behind this class. The abstract
half owns BOTH stores and the whole moratorium logic, once,
platform-free:

  desired   what the program wants: a filter-graph string (or
            nothing) per device, a volume per device, stream mutes;
  observed  what the last snapshot of the server said: sinks,
            sources, the default sink.

Heirs implement only the verbs that touch a real server: push a
graph, push a volume, push stream mutes, pull a snapshot, open
capture and playback streams. Swap the heir and the program rides a
different audio server -- the PulseAudio-to-PipeWire migration is
living memory, and the next one should cost one module, not a
rewrite. Sibling heirs for other platforms make the program
cross-platform without touching anything above this line.

PWState's read task grows into this class: the graph poller becomes
the observed-side sync of the heir, windows subscribe here instead.

The moratorium (a measurement in progress):

  * moratorium_begin(sink, volume) strips the DSP off the measured
    sink, mutes foreign streams, sets the measurement volume, and
    remembers what to restore;
  * while active, every publish/clear/set_volume from anywhere in
    the program is COALESCED into a pending map -- last write per
    key wins -- and nothing touches the server;
  * moratorium_end() restores mutes and volume, then applies the
    pending map in one pass. A forced sweep stop ends it the same
    way. One moratorium at a time; nesting is a programming error
    and raises.

No GTK, no windows, no knowledge of who calls: the presence of a
measure window must never gate code anywhere else.
"""

import threading
from abc import ABC, abstractmethod


class AudioBackend(ABC):
    """Process-wide authority over the audio server.

    Public verbs (the only door the rest of the program uses):

      publish_graph(device, value)   desire a filter graph
      clear_graph(device)            desire a clean device
      set_volume(device, cubic)      desire a device volume
      moratorium_begin(sink, cubic)  measurement starts
      moratorium_end()               measurement over, replay pending
      refresh()                      pull one snapshot into observed
      subscribe(cb)                  observed-state change callbacks
      capture / monitor_capture / play   measurement streams

    Introspection: moratorium_active, pending_count, sinks, sources,
    default_sink.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # desired
        self._graphs = {}          # device -> value or None (clean)
        self._volumes = {}         # device -> cubic float
        # moratorium
        self._moratorium = None    # None or dict(sink=, restore=)
        self._pending = {}         # ("graph"|"volume", device) -> value
        # observed
        self.sinks = []
        self.sources = []
        self.default_sink = None
        self._subs = []

    # -- equalization state -------------------------------------------

    def publish_graph(self, device, value):
        """Desire `value` on `device`. Applied now, or queued until
        the moratorium lifts (last write per key wins)."""
        self._graphs[device] = value
        if self._moratorium is not None:
            self._pending[("graph", device)] = value
            return
        self._push_graph(device, value)

    def clear_graph(self, device):
        """Desire a clean `device` (no DSP)."""
        self._graphs[device] = None
        if self._moratorium is not None:
            self._pending[("graph", device)] = None
            return
        self._push_graph(device, None)

    def set_volume(self, device, cubic):
        """Desire a device volume (cubic, 0..1)."""
        self._volumes[device] = cubic
        if self._moratorium is not None:
            self._pending[("volume", device)] = cubic
            return
        self._push_volume(device, cubic)

    # -- the moratorium ------------------------------------------------

    def moratorium_begin(self, sink, measure_cubic=None,
                         mute_others=True):
        """A measurement claims the server for ONE sweep: mute foreign
        streams (when asked), strip the DSP off `sink`, set the
        measurement volume (when given). The restore is read from the
        SERVER at this very begin -- graph (with its source) and
        volume -- so any process, however fresh, restores what the
        server actually had, every sweep, exactly as the old per-sweep
        law did. Returns the evidence dict the session records as
        eq_profile_state. Raises if one is already active."""
        with self._lock:
            if self._moratorium is not None:
                raise RuntimeError("moratorium already active")
            value, source = self._read_graph(sink)
            self._graphs[sink] = value
            state = {"metadata_key": sink, "profile": value,
                     "profile_source":
                         source if value is not None else None,
                     "bypass": False, "restored": None}
            restore = {
                "graph": value,
                "volume": (self._read_volume(sink)
                           if measure_cubic is not None else None),
                "mutes": (self._push_stream_mutes(sink, True)
                          if mute_others else None),
                "state": state,
            }
            self._moratorium = {"sink": sink, "restore": restore,
                                "muted": mute_others}
            if restore["graph"] is not None:
                self._push_graph(sink, None)
                state["bypass"] = True
            if measure_cubic is not None:
                self._push_volume(sink, measure_cubic)
            return state

    def moratorium_end(self):
        """Measurement over (normal end or forced stop): restore in
        the sweep's own order -- volume back, EQ back, unmute -- then
        apply everything that accumulated, one pass, last write per
        key. Pending writes for the measured sink outrank its
        restore. Fills state["restored"]."""
        with self._lock:
            m = self._moratorium
            if m is None:
                return
            self._moratorium = None
            sink, restore = m["sink"], m["restore"]
            state = restore["state"]
            if ("volume", sink) not in self._pending \
                    and restore["volume"] is not None:
                self._push_volume(sink, restore["volume"])
            if ("graph", sink) not in self._pending \
                    and restore["graph"] is not None:
                ok = bool(self._push_graph(sink,
                                           restore["graph"]))
                state["restored"] = ok
                if not ok:
                    self._restore_failed(sink, restore["graph"])
            if m["muted"]:
                self._push_stream_mutes(sink, False,
                                        restore["mutes"])
            pending, self._pending = self._pending, {}
            for (kind, device), value in pending.items():
                if kind == "graph":
                    self._push_graph(device, value)
                else:
                    self._push_volume(device, value)

    def moratorium_muted_ids(self):
        """Stream ids the active moratorium muted -- the session's
        evidence bookkeeping reads them right after begin."""
        m = self._moratorium
        if m is None:
            return []
        return list(m["restore"]["mutes"] or [])

    @property
    def moratorium_active(self):
        return self._moratorium is not None

    @property
    def pending_count(self):
        return len(self._pending)

    # -- observed side ---------------------------------------------------

    def refresh(self):
        """Pull one snapshot from the server into the observed store;
        notify subscribers when it changed. Synchronous; heirs may
        drive it from a timer."""
        snap = self._pull()
        changed = (snap.get("sinks") != self.sinks
                   or snap.get("sources") != self.sources
                   or snap.get("default_sink") != self.default_sink)
        self.sinks = snap.get("sinks", [])
        self.sources = snap.get("sources", [])
        self.default_sink = snap.get("default_sink")
        if changed:
            for cb in list(self._subs):
                cb(self)
        return changed

    def subscribe(self, cb):
        """Called with this backend after every observed change."""
        self._subs.append(cb)

    # -- heir verbs: the only platform-dependent surface ----------------

    @abstractmethod
    def _push_graph(self, device, value):
        """Make the server wear `value` on `device`; None = clean.
        Returns truthy on success -- the restore's evidence."""

    @abstractmethod
    def _push_volume(self, device, cubic):
        """Make the server set the device volume."""

    @abstractmethod
    def _push_stream_mutes(self, sink, mute, prior=None):
        """Mute (or restore with `prior`) foreign playback streams on
        `sink`. Returns the restore token when muting."""

    @abstractmethod
    def _read_graph(self, device):
        """(value, source) the server holds for `device` right now --
        for seeding the restore in a process that never published."""

    @abstractmethod
    def _read_volume(self, device):
        """The device's current cubic volume, or None."""

    def _restore_failed(self, device, value):
        """A graph restore was refused; heirs say how to recover."""

    @abstractmethod
    def _pull(self):
        """One snapshot of the server: a dict with sinks, sources
        and default_sink, shaped as the program already uses."""

    # -- measurement streams (platform half of the pipeline) ------------

    @abstractmethod
    def capture(self, device, channels, rate):
        """Open a raw capture stream from `device`. Returns a handle
        with .read(nbytes), .terminate() and .alive() -- the
        measurement capture rides this verb."""

    @abstractmethod
    def monitor_capture(self, sink, channels, rate):
        """Capture a sink's monitor (what plays into it) -- the
        level meter rides this verb. Same handle contract as
        capture()."""

    @abstractmethod
    def play(self, device, wav_path, stream_volume=1.0):
        """Play a file into `device`. Returns a handle with .wait(),
        .terminate() and .alive() -- the sweep rides this verb."""
