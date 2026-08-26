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

import sys
import threading
import traceback
from abc import ABC, abstractmethod


def _route_sig(routes):
    """A card's ports as the population sees them: the flags that
    decide what a chooser offers, and nothing that moves on its own.
    """
    return tuple((r.get("index"), r.get("mine"), r.get("reachable"),
                  r.get("available"), r.get("active"))
                 for r in (routes or []))


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

    def _queue(self, kind, device, value):
        self._pending[(kind, device)] = value
        names = [f.name for f in
                 traceback.extract_stack(limit=7)][:-2]
        print("backend: queued %s for %s until the moratorium "
              "lifts (caller: %s)"
              % (kind, device, " > ".join(names)),
              file=sys.stderr)

    def publish_graph(self, device, value):
        """Desire `value` on `device`. Applied now, or queued until
        the moratorium lifts (last write per key wins; the queueing
        is logged with the caller's path)."""
        with self._lock:
            self._graphs[device] = value
            if self._moratorium is not None:
                self._queue("graph", device, value)
                return
            self._push_graph(device, value)

    def clear_graph(self, device):
        """Desire a clean `device` (no DSP)."""
        with self._lock:
            self._graphs[device] = None
            if self._moratorium is not None:
                self._queue("graph", device, None)
                return
            self._push_graph(device, None)

    def set_volume(self, device, cubic):
        """Desire a device volume (cubic, 0..1)."""
        with self._lock:
            self._volumes[device] = cubic
            if self._moratorium is not None:
                self._queue("volume", device, cubic)
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

    def volume_of(self, device):
        """The device's current volume (cubic), or None."""
        return self._read_volume(device)

    @property
    def pending_count(self):
        return len(self._pending)

    # -- observed side ---------------------------------------------------

    def refresh(self):
        """Pull one snapshot from the server into the observed store;
        notify subscribers when it changed. Synchronous; heirs may
        drive it from a timer. Change means the POPULATION changed
        (names or the default), not any property: a volume tick must
        not rebuild every picker."""
        return self._ingest(self._pull())

    last_dump = None        # the graph picture the last pull paid for

    def _ingest(self, snap):
        self.sinks = snap.get("sinks", [])
        self.sources = snap.get("sources", [])
        self.default_sink = snap.get("default_sink")
        # THE PORTS BELONG IN THE SIGNATURE, not just the names. A
        # jack event changes no name and no default: pulling the
        # cable out of a microphone socket only flips that route's
        # `available`, and a signature built from names went on
        # saying nothing had happened, so a row for an empty socket
        # stayed and a door for a jack just plugged in never came.
        # The desktop reacts to both, and it reacts to them because
        # this is what it is watching.
        #
        # These are the flags the listing reads and the census
        # prints, and none of them ticks with a volume: an index, who
        # owns it, whether the loaded profile carries it, whether a
        # jack is behind it, and whether it is the one in use.
        sig = (tuple((s.get("name"), _route_sig(s.get("routes")))
                     for s in self.sinks),
               tuple((s.get("name"), _route_sig(s.get("routes")))
                     for s in self.sources),
               self.default_sink)
        changed = sig != getattr(self, "_sig", None)
        self._sig = sig
        if changed:
            self._notify()
        return changed

    def _notify(self):
        for cb in list(self._subs):
            try:
                cb(self)
            except Exception:
                pass

    def subscribe(self, cb):
        """Register cb(state), called after each observed change.
        Returns a callable that unsubscribes; windows MUST call it
        on teardown so a closed window's callback is not fired on
        dead widgets."""
        self._subs.append(cb)

        def off():
            try:
                self._subs.remove(cb)
            except ValueError:
                pass
        return off

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
    def play(self, device, wav_path, stream_volume=1.0,
             channel_map=None):
        """Play a file into `device`. Returns a handle with .wait(),
        .poll(), .returncode, .stderr_read(), .terminate() and
        .alive() -- the sweep rides this verb."""
