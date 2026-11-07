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

Windows subscribe here, and the heir's heartbeat -- one poll for
every window -- keeps the observed side in sync.

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

Outside a measurement a graph wish is coalesced too. publish_graph
and clear_graph record the wish and return at once; one writer
thread puts the newest wish of each device on the server, one write
at a time and a device no sooner than GRAPH_PACE_S after its last
write, so the last wish always lands last however fast they come. A
whole wire state, the moratorium and a volume still write before
they return. flush() waits for the writer.

No GTK, no windows, no knowledge of who calls: the presence of a
measure window must never gate code anywhere else.
"""

import sys
import threading
import time
import traceback
from abc import ABC, abstractmethod

from . import debug
from .config import STRIP


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
      flush(timeout)                 wait until the wishes are out
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
        # the server: whoever holds it is the one write in progress
        self._lock = threading.Lock()
        # the stores below; held only for a moment, never across a
        # write, so a wish never waits for the server
        self._wish_lock = threading.Lock()
        self._idle = threading.Condition(self._wish_lock)
        # desired
        self._graphs = {}          # device -> value or None (clean)
        self._volumes = {}         # device -> cubic float
        self._dirty = {}           # devices whose newest wish is not out
        self._writing = False      # the writer thread is at work
        self._written = {}         # device -> when its last write ended
        # moratorium
        self._moratorium = None    # None or dict(sink=, restore=)
        self._pending = {}         # ("graph"|"volume", device) -> value
        # observed
        self.sinks = []
        self.sources = []
        self.default_sink = None
        self._subs = []

    # -- equalization state -------------------------------------------

    def _graph_key(self, sink):
        """The key a sink's filter graph is published under.

        The moratorium is handed a NODE, because volume, mute and the
        foreign streams belong to the node. The graph does not: it is
        published for the device the node is playing through, and the
        window and the hook both address it by that key. An heir whose
        devices are its nodes keeps this default."""
        return sink

    def _queue(self, kind, device, value):
        self._pending[(kind, device)] = value
        names = [f.name for f in
                 traceback.extract_stack(limit=7)][:-2]
        print("backend: queued %s for %s until the moratorium "
              "lifts (caller: %s)"
              % (kind, device, " > ".join(names)),
              file=sys.stderr)

    def publish_graph(self, device, value):
        """Desire `value` on `device` and return at once: the writer
        puts it on the server (_wish). During a measurement it is
        queued until the moratorium lifts (last write per key wins;
        the queueing is logged with the caller's path)."""
        self._wish(device, value)

    def clear_graph(self, device):
        """Desire no value of its own for `device`: with a card behind
        it, it plays the common entry (config.COMMON_KEY)."""
        self._wish(device, None)

    # A DEVICE IS WRITTEN NO SOONER THAN THIS AFTER ITS LAST WRITE
    # ENDED. Every write is a pw-metadata run, after which the hook
    # rewrites its state file and sets the graph on the node again,
    # and a drag wishes a graph on every move of the mouse. Merging
    # what arrives during a write bounds nothing when a write is
    # short: on his machine (Nov 7) 73 writes went out in one second,
    # so a write took 14 ms or less, and a taste drag put up to 72
    # writes a second on the device in view. 50 ms is the cap agreed
    # with him, not a measured threshold: at most 20 writes a second
    # for one device. The newest wish waits for its turn, so what is
    # heard can trail the hand by up to 50 ms and two writes -- the
    # one in flight when the hand stopped, and its own.
    GRAPH_PACE_S = 0.05

    def _wish(self, device, value):
        """Record a graph wish; one writer thread delivers the wishes.

        A drag wishes a graph on every move of the mouse. Written one
        per wish, each on a thread of its own carrying its own value,
        the writes waited on a lock that keeps no order, so the write
        that landed last need not have been the last wish. Now a wish
        only replaces the one still waiting for its device, and the
        writer takes one device at a time, each in its turn
        (GRAPH_PACE_S): the newest value every time, and the last
        wish lands last."""
        with self._wish_lock:
            self._graphs[device] = value
            if self._moratorium is not None:
                self._queue("graph", device, value)
                return
            self._dirty[device] = None
            if self._writing:
                return
            self._writing = True
        threading.Thread(target=self._write_wishes, daemon=True,
                         name="pde-graph-writer").start()

    def _write_wishes(self):
        """The writer: the newest wish of each waiting device, one
        write at a time and a device only in its turn; of the devices
        whose turn has come, the one wished first. It sleeps, holding
        nothing, until the soonest turn, and stands down in the same
        moment it finds nothing waiting, so a wish made after that
        starts a new writer."""
        while True:
            with self._lock:
                with self._wish_lock:
                    if not self._dirty:
                        self._writing = False
                        self._idle.notify_all()
                        return
                    device, wait = self._next_turn()
                    if device is not None:
                        del self._dirty[device]
                        value = self._graphs[device]
                if device is not None:
                    try:
                        self._put_graph(device, value)
                    except Exception:
                        traceback.print_exc()
                    continue
            time.sleep(wait)

    def _next_turn(self):
        """(device, 0) for the first waiting device whose turn has
        come, else (None, seconds until the soonest turn). Called with
        the stores held."""
        now = time.monotonic()
        soonest = None
        for device in self._dirty:
            ended = self._written.get(device)
            if ended is None or now - ended >= self.GRAPH_PACE_S:
                return device, 0.0
            turn = ended + self.GRAPH_PACE_S
            soonest = turn if soonest is None else min(soonest, turn)
        return None, soonest - now

    def _put_graph(self, device, value):
        """Every graph write, from every path, with the server held:
        a device's turn counts from the end of its last write,
        whoever made it.

        A write the server did not accept is said on stderr with its
        key, whoever made it, too. The writer dropped the answer and
        the window's pass over the other devices dropped the count,
        so such a write left nothing behind."""
        try:
            ok = self._push_graph(device, value)
        finally:
            with self._wish_lock:
                self._written[device] = time.monotonic()
        if not ok:
            debug.log("the server did not accept the graph for %s"
                      % device)
        return ok

    def flush(self, timeout=None):
        """Wait until every graph wished so far is on the server, or
        queued behind a measurement. True if that happened in time."""
        with self._idle:
            return self._idle.wait_for(lambda: not self._writing,
                                       timeout)

    def publish_state(self, wire):
        """Put a whole wire_state on the wire. Returns (sent, total).

        `total` counts the entries with a value to send -- a device's
        own graph, and the common entry -- not every binding: a device
        that is only told to follow the common entry is not counted.
        `sent` counts the writes the server accepted.

        What that proves is exactly one thing -- the write reached the
        metadata object. The hook does not answer for a graph it
        applied, so no caller may say "applied"; the honest word is
        sent.

        It writes before it returns, the whole state in one hold of
        the server: a wish for one of its devices still waiting for the
        writer is superseded by this newer one, and a wish made during
        the pass goes out after it. It does not wait for a device's
        turn; the turn is the writer's.
        """
        sent = total = 0
        with self._lock:
            for device, value in (wire or {}).items():
                with self._wish_lock:
                    self._graphs[device] = value
                    queued = self._moratorium is not None
                    if queued:
                        self._queue("graph", device, value)
                    else:
                        self._dirty.pop(device, None)
                if value is not None:
                    total += 1
                if queued:
                    continue          # queued, not sent: do not count it
                ok = self._put_graph(device, value)
                if value is not None:
                    sent += 1 if ok else 0
        return sent, total

    def set_volume(self, device, cubic):
        """Desire a device volume (cubic, 0..1). Written before it
        returns, between the writer's graph writes."""
        with self._lock:
            with self._wish_lock:
                self._volumes[device] = cubic
                queued = self._moratorium is not None
                if queued:
                    self._queue("volume", device, cubic)
            if not queued:
                self._push_volume(device, cubic)

    # -- the moratorium ------------------------------------------------

    def moratorium_begin(self, sink, measure_cubic=None,
                         mute_others=True):
        """A measurement claims the server for ONE sweep: mute foreign
        streams (when asked), strip the DSP off `sink`, set the
        measurement volume (when given). The restore is read from the
        SERVER at this very begin -- graph (with its source), volume
        and the sink's own mute -- so any process, however fresh,
        restores what the server actually had, every sweep, exactly as
        the old per-sweep law did. Returns the evidence dict the
        session records as eq_profile_state. Raises if one is already
        active.

        THE SINK'S OWN MUTE IS CLEARED with the volume, and this is
        his finding: a desktop fader dragged to its far left does not
        set zero, it sets MUTE, and a measurement that only writes a
        volume then plays a whole sweep into a muted card. Setting a
        level and leaving a mute standing is not setting a level. It
        is restored at the end like everything else here.
        """
        with self._lock:
            with self._wish_lock:
                if self._moratorium is not None:
                    raise RuntimeError("moratorium already active")
            key = self._graph_key(sink)
            value, source = self._read_graph(key)
            state = {"metadata_key": key, "profile": value,
                     "profile_source":
                         source if value is not None else None,
                     "bypass": False, "restored": None}
            restore = {
                "graph": value,
                "volume": (self._read_volume(sink)
                           if measure_cubic is not None else None),
                # ALWAYS, not only when a level is written. A mute is
                # not part of setting a level, it is the other half of
                # the same act: we move the knob and put it back, and
                # the mute is a knob. Tying it to the volume left a
                # hole exactly where nothing needed writing -- a sink
                # already standing at the measurement level, and muted,
                # got neither the write nor the unmute and played a
                # whole sweep into silence
                "mute": self._read_mute(sink),
                "mutes": (self._push_stream_mutes(sink, True)
                          if mute_others else None),
                "state": state,
            }
            with self._wish_lock:
                # wishes still waiting for the writer go behind the
                # measurement with every later one: none may land
                # inside it. One for the measured device is newer than
                # what the server had, so it outranks the restore
                for device in self._dirty:
                    self._pending[("graph", device)] = self._graphs[device]
                self._dirty.clear()
                if ("graph", key) not in self._pending:
                    self._graphs[key] = value
                self._moratorium = {"sink": sink, "key": key,
                                    "restore": restore,
                                    "muted": mute_others}
            # ALWAYS, found or not: a device with no value of its own
            # plays the common entry, so there is a graph on it either way
            self._put_graph(key, STRIP)
            state["bypass"] = True
            if restore["mute"]:
                self._push_mute(sink, False)
            if measure_cubic is not None:
                self._push_volume(sink, measure_cubic)
            # AND IT DOES NOT RETURN UNTIL IT IS IN FORCE. Every push
            # above is a metadata write the server applies when it
            # gets to it, and this returned the instant they were
            # SENT -- so a caller could start a sweep into a sink that
            # still wore its correction.
            #
            # That is not a theory. One rung of a walk came back 26 dB
            # quiet, and subtracting the profile's own correction
            # curve from the shortfall halved its spread and flattened
            # its tilt: the sweep had been played through the EQ. It
            # was his recollection that put us onto it -- the same
            # thing happened once before, when the bypass was a
            # separate toggle rather than this.
            #
            # The takes waited for the volume and nothing waited for
            # the graph; the map's rungs waited for neither. Waiting
            # here covers both, because both come through this door.
            #
            # The wait is NOT recorded in the evidence dict. That dict
            # is a contract two courts compare whole, and a duration
            # differs on every run -- a number that cannot be equal to
            # itself does not belong in a record whose job is to be
            # compared.
            self._await_moratorium(sink, key, measure_cubic)
            return state

    # how long to wait for the pushes above to be visible on the
    # server. A sweep is twenty seconds and a metadata write is
    # milliseconds, so this is generous by two orders and still
    # cheaper than one ruined rung
    SETTLE_TIMEOUT_S = 1.5
    SETTLE_POLL_S = 0.01

    def _await_moratorium(self, sink, key, measure_cubic):
        """Block until the server shows the moratorium in force, or
        the timeout runs out.

        NOT AN ERROR ON TIMEOUT. A backend that cannot read one of
        these back would otherwise refuse every measurement, and the
        old behaviour -- not waiting at all -- is what this replaces:
        a bounded wait that sometimes gives up is strictly better than
        no wait, and the base rung's own check catches what slips
        through.
        """
        t0 = time.monotonic()
        while time.monotonic() - t0 < self.SETTLE_TIMEOUT_S:
            try:
                value, _src = self._read_graph(key)
            except Exception:
                value = None
            # STRIP itself: None is "follows the common entry", which
            # is the state before the write, not after it
            ok = value == STRIP
            if ok and measure_cubic is not None:
                try:
                    now = self._read_volume(sink)
                except Exception:
                    now = None
                # the server rounds; a hundredth of a cubic step is
                # far below anything a rung can tell apart
                ok = ok and (now is not None
                             and abs(now - measure_cubic) <= 0.01)
            if ok:
                break
            time.sleep(self.SETTLE_POLL_S)

    def moratorium_end(self):
        """Measurement over (normal end or forced stop): restore in
        the sweep's own order -- volume and the sink's own mute back,
        EQ back, foreign streams unmuted -- then
        apply everything that accumulated, one pass, last write per
        key. Pending writes for the measured sink outrank its
        restore. Fills state["restored"]."""
        with self._lock:
            with self._wish_lock:
                m = self._moratorium
                if m is None:
                    return
                self._moratorium = None
                # a wish made from here on goes to the writer, which
                # waits for the server until this pass is done
                pending, self._pending = self._pending, {}
            sink, key, restore = m["sink"], m["key"], m["restore"]
            state = restore["state"]
            if ("volume", sink) not in pending \
                    and restore["volume"] is not None:
                self._push_volume(sink, restore["volume"])
            # after the volume, not before: a card set back to muted
            # and then given a level would be left silent with a
            # number on the fader. Outside that branch, though: the
            # mute is restored whether or not a level was written,
            # because it is now cleared the same way
            if ("volume", sink) not in pending \
                    and restore.get("mute"):
                self._push_mute(sink, True)
            if ("graph", key) not in pending:
                ok = bool(self._put_graph(key,
                                          restore["graph"]))
                state["restored"] = ok
                if not ok:
                    self._restore_failed(key, restore["graph"])
            if m["muted"]:
                self._push_stream_mutes(sink, False,
                                        restore["mutes"])
            for (kind, device), value in pending.items():
                if kind == "graph":
                    self._put_graph(device, value)
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
        # (found, version) of the loaded hook, as of this beat. It is
        # in the signature below: a hook that dies, or comes back, or
        # comes back speaking another protocol, is a change the window
        # has to hear -- it is the difference between a correction
        # being applied and not.
        self.hook = snap.get("hook", (None, None))
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
               self.default_sink,
               self.hook)
        changed = sig != getattr(self, "_sig", None)
        self._sig = sig
        if changed:
            self._notify()
        # THE VOLUME GETS ITS OWN WORD, quietly. It is deliberately not
        # in the signature above -- the desktop moves it constantly and
        # rebuilding the listing on every notch would be absurd -- but
        # a graph that draws what a rig can no longer deliver has to
        # follow the knob, and the tick above has just refreshed the
        # number anyway. Listeners here get told and nobody else pays.
        vols = tuple((s.get("name"), s.get("gain")) for s in self.sinks)
        if vols != getattr(self, "_vol_sig", None):
            self._vol_sig = vols
            for cb in list(getattr(self, "_vol_subs", ())):
                try:
                    cb(self)
                except Exception:
                    pass
        return changed

    def subscribe_volume(self, cb):
        """Register cb(state), called when a sink's volume changed.

        Separate from subscribe() on purpose: that one means the graph
        itself changed and everything must be rebuilt, which a volume
        never warrants.
        """
        if not hasattr(self, "_vol_subs"):
            self._vol_subs = []
        self._vol_subs.append(cb)
        return lambda: self._vol_subs.remove(cb)

    def _notify(self):
        for cb in list(self._subs):
            try:
                cb(self)
            except Exception as e:
                # NOT SILENTLY. The catch is right -- one window's
                # broken callback must not stop the next one's -- but
                # swallowing the reason turned every such break into a
                # ghost: the beat arrived, half of a window's refresh
                # ran, and nothing anywhere said why the other half
                # did not.
                debug.crashed("pw state subscriber", e)

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
        """Make the server wear `value` on `device`: a graph, STRIP
        for explicitly nothing, None for no value of its own. Returns
        truthy on success -- the restore's evidence."""

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

    @abstractmethod
    def _read_mute(self, device):
        """Whether the DEVICE itself is muted right now, or None when
        the server does not say. Its own flag, not its streams'."""

    @abstractmethod
    def _push_mute(self, device, mute):
        """Set the device's own mute."""

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
