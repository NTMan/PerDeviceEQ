"""Where a capture chain stops measuring its own converter.

An input gain sits AFTER the microphone, so it multiplies the signal
and the microphone's own noise by the same factor and cannot change
the ratio between them. The only thing it can change is how far that
whole package sits above the converter's fixed floor. So the useful
question is not "how much gain" but "how much is enough", and it is
answered in SILENCE, without a signal, because the signal cancels out
of the question:

    low gain    the converter's floor dominates; the recorded level
                does not move when the gain does          -> slope ~ 0
    high gain   the input dominates; every dB of gain adds a dB to
                the recording                             -> slope ~ 1

The crossing is the knee. Above it SNR is CONSTANT with gain, because
signal and noise rise together; below it SNR decays toward the
converter. So the knee is where SNR reaches its plateau, and a working
point sits a few dB above it -- further up buys nothing and only eats
headroom.

This module holds the policy and none of the plumbing: which rungs to
visit, when a reading is not to be trusted, and what a finished curve
means. Nothing here opens a device or knows what a window is, so the
whole search can be driven by a fake in a test.

What this module deliberately does NOT do is decide whether a fader is
analogue at all. pw_backend.fader_kind() settles that from the route
before anything is measured, and a ladder only ever runs on a fader
that has travel above unity.
"""

CLIMB = 0.5          # slope at which the input is taken to dominate
FLAT = 0.3           # slope below which the converter still rules
MARGIN_DB = 6.0      # how far above the knee a working point sits
CREST_SLACK = 12.0   # dB above the median crest that marks a transient
CREST_NOISE = 4.0    # below this a reading is not noise at all


class Rung:
    """One step of the ladder: what was asked, what was heard."""

    __slots__ = ("gain_db", "raw", "rms_dbfs", "peak_dbfs", "suspect")

    def __init__(self, gain_db, rms_dbfs, peak_dbfs=None, raw=None):
        self.gain_db = float(gain_db)
        self.raw = raw
        self.rms_dbfs = float(rms_dbfs)
        self.peak_dbfs = (None if peak_dbfs is None else float(peak_dbfs))
        self.suspect = False

    @property
    def crest(self):
        if self.peak_dbfs is None:
            return 0.0
        return self.peak_dbfs - self.rms_dbfs

    def __repr__(self):
        return "Rung(%+.1f dB -> %.2f dBFS%s)" % (
            self.gain_db, self.rms_dbfs, " !" if self.suspect else "")


class Verdict:
    """The end of a ladder, in one of four shapes.

    kind is one of:

      "knee"      found: `knee_db` is the crossing, `work_db` the
                  suggested working point
      "input"     the noise answers the control at every step, so what
                  is measured sits before it: the input dominates the
                  converter across the whole searched range and no more
                  gain buys SNR. The bottom of that range is the working
                  point. This verdict needs only the SIGN of the slope,
                  which is why it survives an axis that is not true dB
      "converter" flat everywhere -- the converter dominates across the
                  whole range. Either the knee lies above the top of
                  the ladder, or the control does not reach the gain
      "unclear"   the curve bends but never reaches the climbing slope,
                  or too few rungs survived to judge
    """

    __slots__ = ("kind", "knee_db", "work_db", "rungs", "note")

    def __init__(self, kind, rungs, knee_db=None, work_db=None, note=""):
        self.kind = kind
        self.rungs = list(rungs)
        self.knee_db = knee_db
        self.work_db = work_db
        self.note = note

    @property
    def usable(self):
        """Whether the ladder produced a gain to work at."""
        return self.work_db is not None

    def __repr__(self):
        return "Verdict(%s, work=%s)" % (self.kind, self.work_db)


def noise_like(peak_dbfs, rms_dbfs, floor=CREST_NOISE):
    """Whether a reading looks like a noise floor at all.

    The whole method rests on listening to silence, so it is worth
    asking whether silence is what is being heard. The crest factor
    answers it without knowing anything about the device: broadband
    noise runs some 11 to 13 dB of peak over RMS, a sine 3, and a DC
    offset or a hard-limited tone essentially none. A reading whose
    peak sits within a few dB of its RMS is a signal, and a ladder
    walked over a signal measures the CONTROL's transfer curve rather
    than the chain's floor -- a perfectly good measurement, but not
    the one being asked for.
    """
    if peak_dbfs is None or rms_dbfs is None:
        return True             # nothing to object with
    return (peak_dbfs - rms_dbfs) >= floor


def plan(lo_db, hi_db, steps=8):
    """The coarse pass: `steps` rungs spanning the control, ends
    included. Coarse first and fine later is much shorter than one
    fine sweep, and the bottom of a gain control is the least
    interesting part of it."""
    steps = max(2, int(steps))
    lo, hi = float(lo_db), float(hi_db)
    if hi <= lo:
        return [lo]
    span = hi - lo
    return [lo + span * i / (steps - 1) for i in range(steps)]


def refine(rungs, points=5):
    """The fine pass: rungs between the two coarse steps that straddle
    the knee. Returns an empty list when there is nothing to refine --
    no crossing, or the coarse pair is already adjacent."""
    pair = _crossing_pair(rungs)
    if pair is None:
        return []
    a, b = pair
    lo, hi = a.gain_db, b.gain_db
    points = max(2, int(points))
    if hi - lo <= 1e-9:
        return []
    return [lo + (hi - lo) * i / (points - 1) for i in range(points)]


def mark_transients(rungs, slack=CREST_SLACK):
    """A rung whose crest factor stands far above its neighbours caught
    an event -- a door, a chair, a notification. Silence is the whole
    method here, so such a rung is marked rather than believed. Returns
    the rungs that were marked."""
    crests = [r.crest for r in rungs if r.peak_dbfs is not None]
    if len(crests) < 3:
        return []
    typical = sorted(crests)[len(crests) // 2]
    hit = []
    for r in rungs:
        if r.peak_dbfs is not None and r.crest > typical + slack:
            r.suspect = True
            hit.append(r)
    return hit


def slopes(rungs):
    """(midpoint gain, d recorded / d gain) between consecutive trusted
    rungs. Suspect rungs take no part in the fit."""
    good = [r for r in rungs if not r.suspect]
    out = []
    for a, b in zip(good, good[1:]):
        dg = b.gain_db - a.gain_db
        if abs(dg) < 1e-9:
            continue
        out.append(((a.gain_db + b.gain_db) / 2.0,
                    (b.rms_dbfs - a.rms_dbfs) / dg))
    return out


def _crossing_pair(rungs):
    """The first pair that climbs, but only once something flat has
    been seen. A curve that climbs from its very first step has no
    knee in this range -- the input already dominates -- and calling
    its bottom a crossing would invent one."""
    good = [r for r in rungs if not r.suspect]
    seen_flat = False
    for a, b in zip(good, good[1:]):
        dg = b.gain_db - a.gain_db
        if abs(dg) < 1e-9:
            continue
        slope = (b.rms_dbfs - a.rms_dbfs) / dg
        if slope < FLAT:
            seen_flat = True
            continue
        if not seen_flat:
            # the curve climbs before it has ever been flat, so the
            # bottom of this ladder is not in the converter's region
            # and there is no crossing here to find. A CM106 answered
            # with slope one, then a plateau, then a climb steeper than
            # one -- three regions of a control, not a knee -- and
            # taking the plateau for a floor named a knee that is not
            # in the data
            return None
        if slope > CLIMB:
            return a, b
    return None


def _knee_db(rungs, pair):
    """Where the two asymptotes meet.

    Taking the midpoint of the straddling pair biases the answer
    upwards, because a pair's AVERAGE slope only passes CLIMB after
    the crossing has happened -- on a synthetic chain whose knee is
    known to be +22 dB that read +24.3. The floor is flat and the
    climb has slope one by construction, so intersecting them is both
    exact and insensitive to how coarse the ladder was.
    """
    good = [r for r in rungs if not r.suspect]
    flat = [r.rms_dbfs for r in good if r.gain_db <= pair[0].gain_db]
    if not flat:
        return (pair[0].gain_db + pair[1].gain_db) / 2.0
    level = sorted(flat)[len(flat) // 2]
    top = good[-1]
    return top.gain_db + (level - top.rms_dbfs)


def verdict(rungs, margin_db=MARGIN_DB):
    """Read a finished ladder."""
    rungs = sorted(rungs, key=lambda r: r.gain_db)
    sl = slopes(rungs)
    if len(sl) < 2:
        return Verdict("unclear", rungs,
                       note="too few trusted rungs to judge a slope")

    values = [s for _, s in sl]
    if all(s > CLIMB for s in values):
        bottom = rungs[0]
        return Verdict("input", rungs, work_db=bottom.gain_db,
                       note="the recorded noise answers the control at "
                            "every step, which can only happen if what "
                            "is being measured sits BEFORE it: the "
                            "input already dominates the converter here, "
                            "so no more gain buys SNR. Work at the "
                            "bottom of the searched range -- not the "
                            "control's minimum, the bottom of what was "
                            "walked -- and the headroom is free")
    if all(s < FLAT for s in values):
        return Verdict("converter", rungs,
                       note="the converter dominates across the whole "
                            "range: either the knee is above the top of "
                            "this control, or the control does not reach "
                            "the gain")

    pair = _crossing_pair(rungs)
    if pair is None:
        return Verdict("unclear", rungs,
                       note="the curve bends but never climbs steeply "
                            "enough to name a crossing")
    knee = _knee_db(rungs, pair)
    work = min(knee + margin_db, rungs[-1].gain_db)
    return Verdict("knee", rungs, knee_db=knee, work_db=work,
                   note="below the knee the converter is what is being "
                        "measured; above it SNR no longer improves and "
                        "headroom is spent for nothing")
