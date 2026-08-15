"""Where a capture chain stops measuring its own converter.

An input gain sits AFTER the microphone, so it multiplies the signal
and the microphone's own noise by the same factor and cannot change
the ratio between them. The only thing it can change is how far that
whole package sits above the converter's fixed floor. So the useful
question is not "how much gain" but "how much is enough", and it is
answered in SILENCE, without a signal, because the signal cancels out
of the question.

Write the chain down and the shapes follow. Recorded noise is

    (N * Ga + F) * Gd

for an input noise N, an analogue gain Ga before the converter, the
converter's own floor F, and a digital multiplier Gd after it. Three
regimes, and a real ladder walks through them in this order:

  digital    below the hardware control's own range the session
             manager makes up the difference in software, and Gd
             scales the floor along with everything else: slope one,
             exactly, and it never flattens
  converter  F outweighs N * Ga, so moving Ga changes nothing: flat
  input      N * Ga outweighs F and the recording follows: rising

The KNEE is the boundary between the last two. Above it SNR is
constant with gain, because signal and noise rise together; below it
the signal falls toward a fixed floor and SNR goes with it. So the
knee is the least gain that buys the whole of the chain's SNR, and it
is found without ever measuring a signal.

Two things this module is careful about.

It does not require the axis to be true decibels. On a hardware route
it is not: the session manager hands back the value it was asked for
while the card maps it through a taper of its own, and a CM106
answered 26.65 dB of noise for 11.1 dB of request. Flatness survives
any stretching of the x axis, and so does "does it answer at all";
only "the slope is one" does not, and that is exactly the test this
module refuses to make. Every segment is asked FLAT OR RISING, never
flat or slope-one, and what comes out is a control POSITION, which is
what the app sets anyway.

And it judges from segments rather than from the step between
neighbours. Two runs of one ladder on one card in one room disagreed
by 0.3 dB against 2.1 on a single step, ordinary scatter in a noise
reading, and that one step decided whether every slope was climbing,
which decided the verdict. Boundaries are chosen by least squares
over the whole curve, and a segment counts as flat when its rise
across its own span is lost in the scatter the fit itself measures --
so the threshold comes from the data and not from a constant chosen
by hand.
"""

MARGIN_DB = 3.0      # how far above a knee a working point sits
EXCESS_DB = 1.5      # a dwell whose mean stands this far above
                     # its median caught something
CREST_SLACK = 12.0   # dB above the median crest that marks a transient
CREST_NOISE = 4.0    # below this a reading is not noise at all
FLOOR_DB = 1.0       # no segment counts as rising on less than this
SLOPE_MID = 0.5      # where the input and the converter contribute
                     # equally: the knee's own slope, not a guess
SCATTER_K = 3.0      # a rise must beat this many times the scatter
LEAST = 2            # rungs a segment needs to be fitted at all
MAX_K = 5            # three regimes and the two bends between them


class Rung:
    """One step of the ladder: what was asked, what was heard."""

    __slots__ = ("gain_db", "raw", "rms_dbfs", "peak_dbfs", "suspect",
                 "blocks", "excess")

    def __init__(self, gain_db, rms_dbfs, peak_dbfs=None, raw=None,
                 blocks=None):
        self.gain_db = float(gain_db)
        self.raw = raw
        self.rms_dbfs = float(rms_dbfs)
        self.peak_dbfs = (None if peak_dbfs is None else float(peak_dbfs))
        self.suspect = False
        # how much silence stands behind this reading. A rung is an
        # average over the dwell, and a rung backed by three blocks
        # deserves less trust than one backed by thirty-six
        self.blocks = blocks
        # how far this dwell's power mean stood above its median. A
        # stationary floor gives nearly zero; a gap says the dwell
        # caught an event, and that is the transient test that works
        # on a card whose peaks are pinned by fixed spikes
        self.excess = None

    @property
    def crest(self):
        if self.peak_dbfs is None:
            return 0.0
        return self.peak_dbfs - self.rms_dbfs

    def __repr__(self):
        return "Rung(%+.1f -> %.2f dBFS%s)" % (
            self.gain_db, self.rms_dbfs, " !" if self.suspect else "")


class Segment:
    """A stretch of the ladder with one behaviour, and its name."""

    __slots__ = ("rungs", "slope", "kind")

    def __init__(self, rungs, slope, kind):
        self.rungs = list(rungs)
        self.slope = slope
        self.kind = kind                 # "flat" or "rising"

    @property
    def lo(self):
        return self.rungs[0].gain_db

    @property
    def hi(self):
        return self.rungs[-1].gain_db

    @property
    def rise(self):
        return self.slope * (self.hi - self.lo)

    def __repr__(self):
        return "Segment(%s %+.1f..%+.1f, %.2f dB/dB)" % (
            self.kind, self.lo, self.hi, self.slope)


class Verdict:
    """The end of a ladder.

      "knee"      found: `knee_db` is the boundary between the flat
                  stretch and the rising one, `work_db` the suggested
                  position
      "input"     rising throughout -- the input already outweighs the
                  converter across the whole searched range, so no
                  more gain buys SNR and the bottom is the place to be
      "converter" flat throughout -- the converter outweighs the input
                  everywhere here. More gain STILL buys SNR, because
                  the signal would rise while this noise does not, so
                  the top of the range is the place to be and a knee,
                  if the card has one, is above what was walked
      "unclear"   too few rungs, or a shape with no reading

    `software_below` marks a leading stretch that rose and then went
    flat: gain the session manager made up in software below the
    hardware control's range, which scales the converter's floor along
    with everything else and so says nothing about the chain.
    """

    __slots__ = ("kind", "knee_db", "work_db", "rungs", "segments",
                 "scatter", "software_below", "note")

    def __init__(self, kind, rungs, segments=(), knee_db=None,
                 work_db=None, scatter=None, software_below=None, note=""):
        self.kind = kind
        self.rungs = list(rungs)
        self.segments = list(segments)
        self.knee_db = knee_db
        self.work_db = work_db
        self.scatter = scatter
        self.software_below = software_below
        self.note = note

    @property
    def usable(self):
        return self.work_db is not None

    def __repr__(self):
        return "Verdict(%s, work=%s)" % (self.kind, self.work_db)


# ------------------------------------------------------------ the fitting

def fit(rungs):
    """(slope, intercept, residual sum of squares) by least squares."""
    n = len(rungs)
    if n < 2:
        return 0.0, (rungs[0].rms_dbfs if n else 0.0), 0.0
    mx = sum(r.gain_db for r in rungs) / n
    my = sum(r.rms_dbfs for r in rungs) / n
    den = sum((r.gain_db - mx) ** 2 for r in rungs)
    if den <= 1e-12:
        return 0.0, my, sum((r.rms_dbfs - my) ** 2 for r in rungs)
    slope = sum((r.gain_db - mx) * (r.rms_dbfs - my) for r in rungs) / den
    inter = my - slope * mx
    rss = sum((r.rms_dbfs - (slope * r.gain_db + inter)) ** 2 for r in rungs)
    return slope, inter, rss


def _rss(parts):
    return sum(fit(p)[2] for p in parts)


def _partitions(rungs, k, least=LEAST):
    """Every way of cutting the rungs into k runs of at least `least`."""
    n = len(rungs)
    if k == 1:
        return [[rungs]] if n >= least else []
    out = []
    for cut in range(least, n - least * (k - 1) + 1):
        for rest in _partitions(rungs[cut:], k - 1, least):
            out.append([rungs[:cut]] + rest)
    return out


def _variance(parts, n):
    """Residual variance with the degrees of freedom spent on it.

    Raw residual always falls when a boundary is added -- a segment of
    two points fits them exactly -- so choosing by residual alone cuts
    the ladder into pairs. Dividing by the freedom left makes each
    boundary pay for itself.
    """
    free = sum(min(2, len(p)) for p in parts)
    dof = n - free
    return float("inf") if dof <= 0 else _rss(parts) / dof


def split(rungs, max_k=MAX_K, least=LEAST):
    """Cut the ladder where its behaviour changes, at the number of
    segments the data actually supports.

    Five is the ceiling and it is not arbitrary: the chain has three
    regimes and a real bend between each pair, and a piecewise-linear
    reading needs a piece for every one of them. Fewer, and a bend
    gets folded into the flat stretch beside it -- which on a real
    CM106 ladder gave the flat middle a rise of 2.6 dB, enough to call
    it rising and collapse the whole curve into one climb. The bends
    are then merged back into whichever side they resemble.
    """
    good = sorted([r for r in rungs if not r.suspect],
                  key=lambda r: r.gain_db)
    n = len(good)
    if n < least:
        return []
    best, best_v = [good], _variance([good], n)
    for k in range(2, max_k + 1):
        cand = None
        for parts in _partitions(good, k, least):
            v = _variance(parts, n)
            if cand is None or v < cand[1]:
                cand = (parts, v)
        if cand is not None and cand[1] < best_v:
            best, best_v = cand
    return best


def scatter_of(parts):
    """The scatter the fit itself measures: residual RMS about the
    chosen segments. A flat segment is judged against this, so the
    threshold comes from the data rather than from a constant."""
    n = sum(len(p) for p in parts)
    free = sum(min(2, len(p)) for p in parts)
    dof = max(1, n - free)
    return (_rss(parts) / dof) ** 0.5


def describe(rungs, max_k=MAX_K):
    """(segments, scatter) with every segment named flat or rising."""
    parts = split(rungs, max_k=max_k)
    if not parts:
        return [], 0.0
    scatter = scatter_of(parts)
    need = max(FLOOR_DB, SCATTER_K * scatter)
    named = []
    for p in parts:
        slope, _, _ = fit(p)
        rise = slope * (p[-1].gain_db - p[0].gain_db)
        # BY SLOPE, not by total rise. Total rise depends on how long
        # the piece happens to be, so a piece with a negligible slope
        # crosses any fixed threshold given enough span -- and one did:
        # a flat stretch of slope 0.18 over 6.7 dB accumulated 1.2 dB,
        # was called rising, merged with its neighbours, and turned a
        # perfectly ordinary knee into "the input outweighs the
        # converter everywhere".
        #
        # The slope is the right quantity and its threshold is not
        # invented. Slope one means the noise follows the gain exactly,
        # so the input dominates; slope zero means it does not move at
        # all, so the converter does. Where they contribute equally --
        # which is what a knee IS -- the local slope is one half.
        #
        # The rise is still consulted for one thing: a piece whose
        # whole rise is lost in the scatter is flat whatever its
        # fitted slope says, because that slope was fitted to noise.
        named.append((p, "rising" if (slope > SLOPE_MID and rise > need)
                      else "flat"))
    # neighbours that say the same thing are ONE region. A real knee is
    # a curve, not a corner, and least squares happily spends a second
    # boundary inside the bend -- which then leaves the first rising
    # piece made entirely of the transition, with a slope far short of
    # the asymptote and a crossing far below the truth.
    segs = []
    for p, kind in named:
        if segs and segs[-1].kind == kind:
            merged = segs[-1].rungs + list(p)
            segs[-1] = Segment(merged, fit(merged)[0], kind)
        else:
            segs.append(Segment(p, fit(p)[0], kind))
    return segs, scatter


# ------------------------------------------------------------ the walking

def plan(lo_db, hi_db, steps=8):
    """The coarse pass: `steps` rungs spanning the control, ends
    included."""
    steps = max(2, int(steps))
    lo, hi = float(lo_db), float(hi_db)
    if hi <= lo:
        return [lo]
    span = hi - lo
    return [lo + span * i / (steps - 1) for i in range(steps)]


def refine(rungs, points=5):
    """The fine pass: rungs on either side of the knee, one coarse step
    out.

    When the coarse pass found no knee, it refines the middle of the
    walk instead of giving up. The old rule -- refine only what is
    already found -- was circular, and the field walked straight into
    it: a coarse pass of ten rungs put three in the flat stretch, read
    them as one rising line, answered "input", and so never asked for
    the rungs that would have shown the flat stretch to be flat. The
    same rig with fifteen rungs answered "knee" every time.

    A verdict of input or converter is a claim that ONE regime covers
    the whole range, and the cheapest way to test that claim is to
    look harder where the other one would be.
    """
    v = verdict(rungs)
    good = sorted([r for r in rungs if not r.suspect],
                  key=lambda r: r.gain_db)
    if len(good) < 3:
        return []
    if v.kind != "knee" or v.knee_db is None:
        if v.kind not in ("input", "converter"):
            return []
        lo, hi = good[0].gain_db, good[-1].gain_db
        mid = 0.5 * (lo + hi)
        span = 0.25 * (hi - lo)
        points = max(2, int(points))
        return [mid - span + 2 * span * i / (points - 1)
                for i in range(points)]
    step = (good[-1].gain_db - good[0].gain_db) / max(1, len(good) - 1)
    lo = max(good[0].gain_db, v.knee_db - step)
    hi = min(good[-1].gain_db, v.knee_db + step)
    points = max(2, int(points))
    if hi - lo <= 1e-9:
        return []
    return [lo + (hi - lo) * i / (points - 1) for i in range(points)]


def mark_transients(rungs, slack=CREST_SLACK, excess_db=EXCESS_DB):
    """A rung whose dwell caught an event is marked rather than
    believed. Silence is the whole method here.

    TWO TESTS, because one of them cannot work everywhere. A crest
    factor far above the others says a peak arrived -- but only where
    the peaks are free to move, and on a card whose peaks are pinned
    by fixed spikes a contaminated dwell LOWERS the crest instead of
    raising it. The second test does not care: it asks how far the
    dwell's power mean stood above its median, which for a stationary
    floor is nothing and for a dwell that caught something is large.
    That one found a rung reading twelve decibels high that the crest
    test had passed.
    """
    hit = []
    for r in rungs:
        if r.excess is not None and r.excess > excess_db:
            r.suspect = True
            hit.append(r)
    crests = [r.crest for r in rungs if r.peak_dbfs is not None]
    if len(crests) >= 3:
        typical = sorted(crests)[len(crests) // 2]
        for r in rungs:
            if (r.peak_dbfs is not None and r.crest > typical + slack
                    and not r.suspect):
                r.suspect = True
                hit.append(r)
    return hit


def noise_like(peak_dbfs, rms_dbfs, floor=CREST_NOISE):
    """Whether a reading looks like a noise floor at all.

    The crest factor answers it without knowing anything about the
    device: broadband noise runs some 11 to 13 dB of peak over RMS, a
    sine 3, a DC offset or a hard-limited tone essentially none.
    """
    if peak_dbfs is None or rms_dbfs is None:
        return True
    return (peak_dbfs - rms_dbfs) >= floor


# ------------------------------------------------------------ the reading

def already_good(here_db, work_db, margin_db=MARGIN_DB):
    """Whether a control standing at `here_db` is already somewhere the
    ladder would have chosen.

    Above the knee every position has the same SNR, so a fader inside
    the margin band is already right and moving it buys nothing while
    fragmenting a canvas: takes made at different capture gains sit at
    different levels, which lands in the per-frequency spread and reads
    as disagreement. The knee is an extrapolation and wanders a few
    tenths between runs, so without this a re-run nudges the gain every
    time for no reason.
    """
    if here_db is None or work_db is None:
        return False
    return abs(here_db - work_db) <= margin_db



def caption(kind, knee_db, here_db):
    """One line saying where a control stands with respect to a verdict,
    or None when there is nothing to say.

    Deliberately about POSITION rather than about the search: it stays
    true while the hand moves the control, which is why it belongs
    under the control and not in a status line that the next sweep
    overwrites.

    TWO THINGS IT MUST NOT DO, both learned from a line that did them.

    It must not name a place in decibels. The decibels here are the
    CONTROL's axis, not the card's, and the control itself is marked
    in per cent -- so "the knee is at -8.9 dB" named a spot the hand
    had no way to find. The knee is given as the per cent the fader
    shows, which is the only number a hand can act on.

    And it must not make the reader do the physics. BELOW the knee the
    converter's floor dominates, so SNR falls about a decibel for
    every decibel of gain given up -- which means the distance below
    the knee IS the SNR being thrown away, and saying so costs nothing
    and answers the only question worth asking. ABOVE the knee SNR is
    flat, so the distance means nothing there and is not printed:
    what it costs is headroom, and that is what gets said.
    """
    if kind == "knee":
        if here_db is None or knee_db is None:
            return None
        at = 100.0 * (10.0 ** (knee_db / 60.0))
        d = here_db - knee_db
        if d < -0.05:
            return ("about %.0f dB of SNR thrown away -- the knee is "
                    "at %.0f%%" % (-d, at))
        return ("above the knee (%.0f%%) -- more gain buys no SNR, only "
                "lost headroom" % at)
    if kind == "input":
        return ("the input outweighs the converter across this control, "
                "so every position has the same SNR and this one is free")
    if kind == "converter":
        return ("the converter outweighs the input across this control, "
                "so higher is better here and the knee is above what was "
                "walked")
    return None


def _boundary(a, b):
    """Where the two asymptotes meet, in gain.

    The rising asymptote is fitted from the TOP of the rising segment,
    not from all of it. A segment that begins inside the transition
    has its slope dragged down by it -- on a chain whose knee is known
    by arithmetic to be at +22 the whole-segment fit read 0.74 dB/dB
    and put the crossing at 16.6 -- and the further rungs are the only
    ones that have reached the asymptote.
    """
    sa, ia, _ = fit(a.rungs)
    top = b.rungs[max(0, len(b.rungs) - max(2, len(b.rungs) // 2)):]
    sb, ib, _ = fit(top)
    if abs(sb - sa) < 1e-9:
        return (a.hi + b.lo) / 2.0
    return min(max((ia - ib) / (sb - sa), a.lo), b.hi)


def verdict(rungs, margin_db=MARGIN_DB, max_k=MAX_K):
    """Read a finished ladder."""
    rungs = sorted(rungs, key=lambda r: r.gain_db)
    segs, scatter = describe(rungs, max_k=max_k)
    if not segs:
        return Verdict("unclear", rungs,
                       note="too few trusted rungs to fit anything")

    kinds = [s.kind for s in segs]

    # A leading rise that then goes flat is the session manager making
    # up gain in software below the hardware control's range: it scales
    # the converter's floor along with everything else, so it says
    # nothing about the chain and nothing above it depends on it.
    software_below = None
    if len(segs) >= 2 and kinds[0] == "rising" and kinds[1] == "flat":
        software_below = segs[0].hi

    last_knee = None
    for i in range(len(segs) - 1):
        if segs[i].kind == "flat" and segs[i + 1].kind == "rising":
            last_knee = i

    if last_knee is not None:
        k = _boundary(segs[last_knee], segs[last_knee + 1])
        return Verdict("knee", rungs, segs, knee_db=k,
                       work_db=min(k + margin_db, rungs[-1].gain_db),
                       scatter=scatter, software_below=software_below,
                       note="below the knee the converter is what is "
                            "being measured and SNR falls with the "
                            "signal; above it SNR no longer improves "
                            "and headroom is spent for nothing")

    if all(k == "rising" for k in kinds):
        return Verdict("input", rungs, segs, work_db=rungs[0].gain_db,
                       scatter=scatter, note=_INPUT_NOTE)

    if all(k == "flat" for k in kinds):
        return Verdict("converter", rungs, segs, work_db=rungs[-1].gain_db,
                       scatter=scatter, note=_CONVERTER_NOTE)

    return Verdict("unclear", rungs, segs, scatter=scatter,
                   software_below=software_below,
                   note="the curve reads %s, which is no floor under a "
                        "rise and no one regime throughout"
                        % " then ".join(kinds))


_INPUT_NOTE = (
    "the recorded noise answers the control across the whole range, "
    "which can only happen if what is being measured sits BEFORE it: "
    "the input already outweighs the converter here, so no more gain "
    "buys SNR. Work at the bottom of the searched range -- not the "
    "control's minimum, the bottom of what was walked -- and the "
    "headroom is free")

_CONVERTER_NOTE = (
    "the converter outweighs the input across the whole range, so this "
    "noise does not follow the control while a signal would: more gain "
    "still buys SNR here and the top of the range is the place to be. "
    "A knee, if this card has one, is above what was walked")
