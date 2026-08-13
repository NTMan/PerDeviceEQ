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


def gain_of(source):
    """The cubic gain a source record is standing at, or None."""
    return (source.get("gain") or (None, None))[0]


def active_route(source):
    """The input route a source is currently on, or None."""
    return next((r for r in (source.get("routes") or [])
                 if r.get("active")), None)


def channel_gain(node_name, channel):
    """One column's own gain, as a cubic, or None.

    A source record folds its channels into one number, which is right
    while they agree and wrong the moment they do not -- and they are
    independent in the hardware. Anything that speaks for one column
    reads that column.
    """
    for o in pw_backend.pw_dump():
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("node.name") != node_name:
            continue
        gains = pw_backend.channel_gains_of_node(o)
        return gains[channel] if 0 <= channel < len(gains) else None
    return None


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

    def __init__(self, source, column=0, dwell=DWELL_S, settle=SETTLE_S):
        self.source = source
        self.column = int(column)
        self.dwell = float(dwell)
        self.settle = float(settle)
        self.width = pw_backend.source_width(source)
        # per-column when the route publishes its channels, and the
        # whole node otherwise. A ladder measures ONE column, so
        # moving the others is at best noise on a canvas and at worst
        # a second capsule dragged off its own working point
        self.route = active_route(source)
        self.per_channel = bool((self.route or {}).get("channel_volumes"))
        self.before = (channel_gain(source["name"], self.column)
                       if self.per_channel else gain_of(source))
        self.rungs = []
        self.restored = None            # True, False, or None if untouched
        self._meter = inmeter.InputMeter()

    # -- lifecycle ------------------------------------------------------

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def open(self):
        if self.column >= self.width:
            raise ValueError("column %d of a %d-column source"
                             % (self.column, self.width))
        self._meter.start(self.source["id"], self.width)
        time.sleep(0.6)
        if not self._meter.alive():
            self._meter.stop()
            raise RuntimeError("the capture did not start")

    def close(self):
        self._meter.stop()
        if self.before is None:
            return
        try:
            self._set(self.before)
            self.restored = True
        except Exception:                              # noqa: BLE001
            # said, not swallowed: a walk that could not put the gain
            # back has left the card somewhere the operator did not
            # choose, and the caller has to be able to say so
            self.restored = False

    # -- the two things it can do ---------------------------------------

    def _set(self, cubic):
        """Move this column's gain, and only this column's."""
        if self.per_channel:
            pw_backend.set_channel_gain(self.route, self.column, cubic)
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
        got = (channel_gain(self.source["name"], self.column)
               if self.per_channel else read_back(self.source["name"]))
        real = cubic_to_db(got)
        rms, peak, _dc, blocks = self._gather(self.dwell, with_blocks=True)
        if rms is None:
            return None
        rung = knee.Rung(real if real is not None else db, rms,
                         peak_dbfs=peak, raw=got, blocks=blocks)
        self.rungs.append(rung)
        return rung

    # -- the worker ------------------------------------------------------

    def _gather(self, seconds, with_blocks=False):
        """Average the dwell rather than sample its end.

        One block is 43 ms and a noise floor read off 43 ms wanders by
        more than a dB: two runs of one ladder on one card disagreed by
        3.8 dB on the top rung, and that disagreement flipped a
        verdict. Power averages, peaks take the highest, and the poll
        runs faster than the meter publishes so no block is missed.
        """
        deadline = time.time() + seconds
        power = dcpow = 0.0
        count = 0
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
            power += 10.0 ** (v / 10.0)
            dcpow += 10.0 ** (dc[self.column] / 10.0)
            count += 1
            p = peak[self.column]
            top = p if top is None else max(top, p)
        if not count:
            return (None, None, None, 0) if with_blocks else (None, None, None)
        mean = 10.0 * math.log10(power / count)
        off = 10.0 * math.log10(dcpow / count)
        return (mean, top, off, count) if with_blocks else (mean, top, off)


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
           dwell=DWELL_S, refine=True, on_rung=None, should_stop=None):
    """Walk the whole thing and read it.

    `on_rung(rung, done, total)` is called after every step, and
    `should_stop()` is asked before each; a walk that stops early
    still puts the gain back and still returns what it has.
    """
    with Walk(source, column, dwell=dwell) as w:
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
