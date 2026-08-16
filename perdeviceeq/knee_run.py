"""Walking a real input's gain ladder.

[[knee]] holds the policy and touches no hardware; this holds the
hardware and decides nothing. Between them sits the seam that lets a
synthetic chain drive the whole search in a test, and it is worth
keeping: every question about what a curve MEANS belongs on the other
side of it.

What lives here is the part that has to know about devices. Setting a
gain and waiting for the card to take it. Holding ONE capture open for
the whole ladder rather than spawning a process per rung. Averaging a
dwell instead of sampling the end of it. And reading the x coordinate
back off the card rather than trusting the number that was asked for,
because a hardware control quantises to its own steps.

The gain is put back when the walk ends, however it ends.

Both the measure window and tools/knee_probe.py come through here, so
there is one implementation of the walk and one of the arithmetic that
turns a volume into an axis.
"""

import math
import time

from . import inmeter, knee, pw_backend

SETTLE_S = 0.35      # after a write, before believing what is heard
POLL_S = 0.02        # faster than the meter publishes, so none is missed
DWELL_S = 1.5        # silence averaged per rung
FLOOR_PROBE = 0.05   # cubic: low enough that any card's own control
                     # has bottomed out, and not zero, which is mute


def cubic_to_db(cubic):
    """The axis a ladder walks.

    wpctl speaks the cubic volume; a route's own props are linear, and
    20*log10 of those is what pactl prints -- 0.287501 reads -10.83
    there, and it does here. linear = cubic**3, so the two agree at
    60*log10(cubic).

    On a hardware route this is not the card's own decibels: the
    session manager hands back the value it was asked for while the
    card maps it through a taper of its own. That is why [[knee]] asks
    only whether a stretch is flat or rising, never whether its slope
    is one.
    """
    if not cubic or cubic <= 0.0:
        return None
    return 60.0 * math.log10(min(1.0, float(cubic)))


def db_to_cubic(db):
    return min(1.0, 10.0 ** (float(db) / 60.0))


def _median(xs):
    """The middle value. In decibels or in linear units it is the same
    sample, since the median only cares about order."""
    ys = sorted(xs)
    n = len(ys)
    if not n:
        return None
    if n % 2:
        return ys[n // 2]
    return 0.5 * (ys[n // 2 - 1] + ys[n // 2])


def power_mean(xs):
    """What the dwell used to be read with, kept for one purpose: how
    far it stands above the median says the dwell caught an event."""
    if not xs:
        return None
    return 10.0 * math.log10(
        sum(10.0 ** (x / 10.0) for x in xs) / float(len(xs)))


def gain_of(source):
    """The cubic gain a source record is standing at, or None."""
    return (source.get("gain") or (None, None))[0]


def active_route(source):
    """The input route a source is currently on, or None."""
    return next((r for r in (source.get("routes") or [])
                 if r.get("active")), None)



def read_back(node_name):
    """The gain the card actually took, not the one it was asked for.
    A hardware control quantises to its own steps, and an axis made of
    requests rather than of readings carries that error into the fit."""
    for s in pw_backend.list_sources():
        if s["name"] == node_name:
            return gain_of(s)
    return None


class Walk:
    """One ladder on one input, as a context manager.

    Opening starts the capture and remembers the gain; leaving stops
    the capture and puts the gain back, whether the walk finished, was
    stopped, or raised.
    """

    def __init__(self, source, column=0, dwell=DWELL_S, settle=SETTLE_S,
                 quiet_sink=None):
        self.source = source
        # THE LADDER MEASURES SILENCE, so anything the machine plays
        # lands in it. There are three consumers of the hardware -- the
        # sensitivity search, the level search and the take -- and only
        # one may hold it at a time; until now only the take said so.
        # The ladders showed the price: rungs marked CAUGHT SOMETHING
        # at +1.8 and +4.5 dB over the median, thrown away by the
        # transient test that should not have had to catch them.
        #
        # NAMED FOR WHAT IT DOES, because the moratorium mutes foreign
        # streams on ONE sink and cannot quiet a room. A caller that
        # knows which output could be heard passes it; one that does
        # not passes nothing and gets the old behaviour.
        self.quiet_sink = quiet_sink
        self._claim = None
        self.column = int(column)
        self.dwell = float(dwell)
        self.settle = float(settle)
        self.width = pw_backend.source_width(source)
        # per-column when the route publishes its channels, and the
        # whole node otherwise. A ladder measures ONE column, so
        # moving the others is at best noise on a canvas and at worst
        # a second capsule dragged off its own working point
        self.route = active_route(source)
        cv = (self.route or {}).get("channel_volumes")
        self.per_channel = bool(cv)
        # the other columns are read ONCE, at the start of the walk,
        # and held: a Route takes the whole list, and re-reading it
        # per rung would mean carrying values from a snapshot that is
        # a beat behind our own last write. During a ladder this
        # window is the only writer, so the list it starts with is the
        # list that stays true.
        self.others = [float(v) ** (1.0 / 3.0) for v in (cv or [])]
        self.floor_db = None            # the card's own, when it has one
        # where the column stood when the walk began. NOT a value to
        # go back to when the walk ends -- the walk exists to replace
        # it -- but the floor probe borrows the column for a moment
        # and has to hand it back before anything else looks at it
        self.before = (pw_backend.route_channel_cubic(self.route,
                                                      self.column)
                       if self.per_channel else gain_of(source))
        self.rungs = []
        self._meter = inmeter.InputMeter()

    # -- lifecycle ------------------------------------------------------

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def hardware_floor_db(self, restore=True):
        """Where the card's own control stops, on the ladder's axis, or
        None when it does not stop inside the walk.

        Asked rather than assumed: set the column as low as it goes and
        read back what the HARDWARE took. channelVolumes is the
        request, softVolumes is what the graph is making up on top, and
        their quotient is the card. Below the floor the request keeps
        falling while the quotient stands still.

        This is worth one extra write because of what it saves. On a
        CM106 the floor is at -27 dB, so a walk from -60 spends five of
        its fifteen rungs below it -- measuring PipeWire's own
        multiplier, not the chain. Those are exactly the rungs that
        produce a stretch of slope one, which is the thing the reading
        has twice had to be taught to discount.
        """
        if not self.per_channel or self.column >= len(self.others):
            return None
        want = list(self.others)
        # NOT zero. Zero is mute, and a muted channel reports a
        # hardware position of zero rather than a floor -- the first
        # version of this asked for zero, got zero, and concluded the
        # card had no floor on every card it was ever run on.
        want[self.column] = FLOOR_PROBE
        pw_backend.set_route_volumes(self.route, want)
        time.sleep(self.settle)
        hw = pw_backend.route_hw_position(self.route, self.column)
        # and put the column back, unless a rung is about to set it
        # anyway. Restoring between the probe and the first rung is
        # visible on the card as a pointless round trip -- down to the
        # floor, back up to where it was, straight back down -- and it
        # showed up in the route watch as an anomaly before it showed
        # up as a thought. The caller that walks immediately says so.
        if restore and self.before is not None:
            pw_backend.set_route_volumes(
                self.route, [self.before if i == self.column else v
                             for i, v in enumerate(self.others)])
            time.sleep(self.settle)
        if not hw or hw <= 0.0:
            return None
        floor = 20.0 * math.log10(min(1.0, hw))
        # a card that took what it was asked for has not shown a floor:
        # it may simply go lower than the probe
        asked = 20.0 * math.log10(FLOOR_PROBE ** 3)
        return None if floor <= asked + 0.2 else floor

    def open(self):
        if self.column >= self.width:
            raise ValueError("column %d of a %d-column source"
                             % (self.column, self.width))
        if self.quiet_sink:
            back = pw_backend.backend()
            self._claim = back.moratorium_begin(self.quiet_sink,
                                                mute_others=True)
        self._meter.start(self.source["id"], self.width)
        time.sleep(0.6)
        if not self._meter.alive():
            self._meter.stop()
            raise RuntimeError("the capture did not start")

    def leave_at(self, cubic):
        """Put the column at the working point the walk found."""
        self._set(cubic)

    def close(self):
        """A walk does not put anything back.

        It exists to find a working point that something afterwards
        will use, so restoring the old gain would erase the only thing
        the run produced. Both callers set the point themselves: the
        window applies it, the tool leaves it. A run that is stopped
        halfway leaves the column on the rung it halted on, which is
        the price of not having a hidden second policy.

        The claim on the hardware IS given back, because that is not a
        finding -- it is a lock.
        """
        self._meter.stop()
        # read defensively: a court builds a Walk without __init__ to
        # exercise one method, and close() must not care
        if getattr(self, "_claim", None) is not None:
            self._claim = None
            try:
                pw_backend.backend().moratorium_end()
            except Exception:                       # noqa: BLE001
                pass

    # -- the two things it can do ---------------------------------------

    def _set(self, cubic):
        """Move this column's gain, and only this column's."""
        if self.per_channel and self.column < len(self.others):
            want = list(self.others)
            want[self.column] = cubic
            pw_backend.set_route_volumes(self.route, want)
        else:
            pw_backend.set_gain(self.source["id"], cubic)

    def listen(self, seconds=None):
        """(rms about the mean, peak, offset) for the chosen column,
        averaged over `seconds`, without touching the gain. This is
        what says whether the column is silent enough to ladder."""
        return self._gather(seconds if seconds is not None else self.dwell)

    def visit(self, db):
        """Set the gain, wait for the card, and listen. Returns a Rung
        whose gain is what the card TOOK, or None if nothing was
        heard."""
        self._set(db_to_cubic(db))
        time.sleep(self.settle)
        got = (pw_backend.route_channel_cubic(self.route, self.column)
               if self.per_channel else read_back(self.source["name"]))
        real = cubic_to_db(got)
        rms, peak, _dc, blocks, excess = self._gather(
            self.dwell, with_blocks=True)
        if rms is None:
            return None
        rung = knee.Rung(real if real is not None else db, rms,
                         peak_dbfs=peak, raw=got, blocks=blocks)
        rung.excess = excess
        self.rungs.append(rung)
        return rung

    # -- the worker ------------------------------------------------------

    def _gather(self, seconds, with_blocks=False):
        """Read the dwell with the MEDIAN of its blocks, not their mean.

        One block is 43 ms and a floor read off 43 ms wanders by more
        than a dB, so the dwell has to be read whole. But averaging it
        in POWER is the worst possible way to do that, and the field
        proved it: thirty-five blocks at -78 dBFS and ONE that caught
        an event at -50 come to -65.3 as a power mean, which is
        exactly the 12 dB error a rung showed. A power mean is
        dominated by its loudest sample by construction.

        A noise floor is stationary, so the median is the estimator
        that fits it, and one bad block in thirty-six moves it not at
        all. The mean is kept only to tell that something happened:
        far above the median means the dwell caught an event, and that
        is a better transient test than the crest factor, which on
        this card cannot work -- its peaks are pinned by fixed spikes
        near -34.5 dBFS, so a contaminated block LOWERS the crest
        instead of raising it.

        Peaks still take the highest: a peak is a maximum by
        definition, and the point of it is to catch the loudest thing
        that happened.
        """
        deadline = time.time() + seconds
        vals = []
        dcs = []
        top = None
        seen = None
        while time.time() < deadline:
            time.sleep(POLL_S)
            rms = self._meter.latest_rms()
            peak = self._meter.latest()
            dc = self._meter.latest_dc()
            if rms is None or peak is None or dc is None:
                continue
            v = rms[self.column]
            if v == seen:
                continue                    # the same block twice
            seen = v
            vals.append(v)
            dcs.append(dc[self.column])
            p = peak[self.column]
            top = p if top is None else max(top, p)
        if not vals:
            return ((None, None, None, 0, None) if with_blocks
                    else (None, None, None))
        mid = _median(vals)
        off = _median(dcs)
        # how far the old estimator stands above the new one: for a
        # stationary floor they agree, and a gap means the dwell
        # caught something
        excess = power_mean(vals) - mid
        return ((mid, top, off, len(vals), excess) if with_blocks
                else (mid, top, off))


def unity_db(source):
    """The gain at which the active route gives unity, or None.

    Printed rather than obeyed: the flat stretch a knee is measured
    against often lies BELOW it, so starting a ladder there cuts the
    left half of the answer away. It does say which part of the walk
    the card can only attenuate in.
    """
    active = next((r for r in (source.get("routes") or [])
                   if r.get("active")), None)
    base = (active or {}).get("volume_base")
    if not base or not 0.0 < base < 1.0:
        return None
    return 20.0 * math.log10(base)


def ladder(source, column=0, lo_db=-60.0, hi_db=0.0, steps=10,
           dwell=DWELL_S, refine=True, on_rung=None, should_stop=None,
           from_floor=True, quiet_sink=None):
    """Walk the whole thing and read it.

    The walk starts at the CARD's floor when it has one, not at the
    number passed in: below that point the card is standing still and
    the graph is making up the difference, so those rungs measure a
    multiplier rather than a chain.

    `on_rung(rung, done, total)` is called after every step, and
    `should_stop()` is asked before each; a walk that stops early
    still puts the gain back and still returns what it has.
    """
    with Walk(source, column, dwell=dwell,
              quiet_sink=quiet_sink) as w:
        floor = w.hardware_floor_db(restore=False) if from_floor else None
        if floor is not None and floor > lo_db:
            w.floor_db = floor
            lo_db = floor
        points = knee.plan(lo_db, hi_db, steps)
        total = len(points)
        for i, db in enumerate(points):
            if should_stop is not None and should_stop():
                return knee.verdict(w.rungs), w
            r = w.visit(db)
            if r is not None and on_rung is not None:
                on_rung(r, i + 1, total)
        knee.mark_transients(w.rungs)
        if refine:
            fine = knee.refine(w.rungs)
            total += len(fine)
            for i, db in enumerate(fine):
                if should_stop is not None and should_stop():
                    break
                r = w.visit(db)
                if r is not None and on_rung is not None:
                    on_rung(r, len(w.rungs), total)
            knee.mark_transients(w.rungs)
        return knee.verdict(w.rungs), w
