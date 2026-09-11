"""The level search: find the sink volume a measurement should use.

THREE CONSUMERS OF THE HARDWARE, one at a time -- the sensitivity
search, this, and the take. They are independent, and the only thing
that passes between them is a NUMBER: this walk's product is a sink
volume, which the take is then told to use. Nothing here knows what a
take is, and MeasureSession does not know this exists.

It stands on sweep_io, the same floor the session stands on -- a
sweep, run_take, analyze_take, and a moratorium to claim the hardware.
Not on the session, and no longer through a function-level import to
dodge a cycle: there is no cycle to dodge. knee_run.Walk has the
same shape for the capture gain; this is its twin for the playback
level.

WHAT IT LOOKS FOR, settled by field ladders on two earphones: the
lowest level at which the distortion figure at 1 kHz is a MEASUREMENT
rather than a bound the rig cannot see under. That is also, by
construction, the level of least measured distortion -- below the
crossing the reading is the rig's own floor and above it the device is
climbing -- with the guard that keeps it from walking to silence.
"""

import math
import os
import tempfile

import numpy as np

from . import measure_build as mb
from . import measure_core as mc
from . import pw_backend
from .sweep_io import run_take, write_sweep_files

AUTO_RAMP = 2.0                 # coarse step while nothing is bracketed
AUTO_RAMP_NEAR = 1.12           # ~3 dB, once the margin says the
                                # crossing is close: doubling the CUBIC
                                # volume is EIGHTEEN decibels and
                                # overshoots exactly where precision is
                                # wanted
AUTO_NEAR_DB = 6.0              # "close" = within this of clearing the
                                # floor gap
AUTO_NEAR_STEPS = 3             # creep at most this often: a device
                                # quieter than the rig at every level
                                # would be crept after forever, and
                                # such a rig must reach the top to say so
AUTO_SETTLE_RATIO = 1.12        # stop closing on the lowest ok within
                                # this much volume -- about a decibel,
                                # finer than the crossing's own wobble
AUTO_SNR_MARGIN_DB = 1.0        # aim past clean, not onto its edge
AUTO_PEAK_FLOOR = -12.0         # quieter wastes capture robustness

# ONE LEVEL SERVES A WHOLE RIG, and a rig's channels are not equally
# sensitive: his Origin in the coupler answers 1.1 dB louder on FL
# than on FR, so a hunt that lands the PROBED channel exactly on the
# floor leaves the other one just under it -- which is what his first
# profile after the floor came back showed, FL at -11.0 and FR at
# -12.1. The band is the room left for that difference. It is not a
# second floor: the floor says where a capture stops being robust,
# this says how far apart two channels of one rig may be and still
# both clear it.
#
# A decibel is what his own ladders make it worth: on the M62 it moves
# the landing from -11.2 to -10.2 for +0.4 dB of measured distortion,
# and on the CM106 from -11.8 to -10.9 with the reading going DOWN,
# because there it is still tracking its own floor.
AUTO_PEAK_BAND = 1.0
AUTO_PEAK_CEIL = -2.0           # louder risks the converter

# BELOW THIS A CAPTURE IS SILENT, not quiet. analyze_take reports
# -120 dBFS for a channel that is digitally silent, and a real room
# with a real microphone does not come back at -90 either: a sweep
# played into a rig the microphone can hear lands tens of decibels
# above that even at the bottom of a walk. So a reading down here
# means the capture is not in the path at all.
SILENT_CAPTURE_DBFS = -90.0
AUTO_EXPLORE_CEIL = 0.80        # the ramp stops here until a probe AT
                                # it is still quiet
AUTO_START_VOLUME = 0.15        # cubic; "start quiet"
AUTO_MAX_ADJUST = 12            # four to climb, three to creep, three
                                # to close is ten; a sweep is ~8.5 s so
                                # the ceiling only spends on a rig that
                                # needs it
AUTO_CLIP_BACKOFF = 0.7


def _clamp(v):
    return max(0.0, min(1.0, float(v)))


def start_volume(current=None):
    """Where a search begins: quiet, but never at zero.

    A multiplicative ramp cannot move from zero -- it doubles to zero,
    sees no change and stops after one sweep. A rig nobody has
    measured shows zero, so this is not a corner case. Zero is not
    quieter than the quiet start; it is nothing.
    """
    return _clamp(min(current or AUTO_START_VOLUME, AUTO_START_VOLUME))


class AutoLevel:
    """Bracket-and-close on the lowest usable level.

    Ramp up until a level is judged ok, then close between the highest
    quiet probe and the lowest ok one. The first ok is NOT the answer:
    the ramp doubles, so it can step straight over the crossing.
    """

    def __init__(self):
        self.lo = None            # (v, peak): highest too-quiet probe
        self.hi = None            # (v, peak): lowest too-loud / clipped
        self.ok = None            # (v, peak): LOWEST probe judged ok
        self.near = False         # the margin says the crossing is close
        self.crept = 0            # fine steps spent looking for it
        self.ceil = AUTO_EXPLORE_CEIL

    @staticmethod
    def verdict(peak, snr, clipped=False, thd_bound=None):
        """'loud' past the safe peak ceiling, 'ok' when hot enough AND
        clean enough AND the rig can see under the device, else 'quiet'.

        THE THIRD QUESTION is thd_bound, and a field ladder settled it.
        Peak and SNR say the capture is usable; they cannot say whether
        the distortion figure is a MEASUREMENT. On a CM106 into a
        coupler the midband stayed a bound until a recorded peak near
        -16 dBFS, well inside the old window, so the hunt stopped early
        and every THD number afterwards was a ceiling.

        THE PEAK FLOOR BINDS IN EVERY BRANCH. It used to be asked
        only where the crossing had nothing to say, because on his
        Liberty the crossing arrives at a peak of -13.6 dBFS -- below
        the floor -- and an early cut that rejected that level sailed
        to full volume. The runaway was the RAMP, though, not the
        order of the questions: doubling the cubic threw the hunt
        across everything above one rejection, and the creep has since
        cured it.

        What the field then measured is why the floor is back: on both
        of his chains, climbing from the crossing up to the floor
        costs NOTHING in the reading. The M62 moved +0.00 and +0.97 dB
        of distortion for 2.8 dB of level; the CM106 moved -0.11,
        -0.96 and -1.85 for 4.6 dB. Clearing the crossing says the
        figure is VISIBLE. It does not say the capture is ROBUST, and
        those are two questions -- so the second one is asked again,
        and the decibels it buys are free.

        The last-decibel escape stays: a device that cannot reach the
        floor at full volume must be able to say so rather than be
        walked to silence.

        NOT A PRECISE OPTIMUM, and the code should not pretend
        otherwise: three ladders on one earphone put the crossing at
        75, 75 and 79 per cent, a wobble of 2.6 dB, because the
        question is a yes/no asked where the curve is flat.
        """
        if clipped or peak > AUTO_PEAK_CEIL:
            return "loud"
        if snr is None:
            return "quiet"
        last_dB = peak >= AUTO_PEAK_CEIL - 1.0
        if not (snr >= mc.SNR_WARN_DB + AUTO_SNR_MARGIN_DB
                or (snr >= mc.SNR_WARN_DB and last_dB)):
            return "quiet"
        if peak < AUTO_PEAK_FLOOR + AUTO_PEAK_BAND and not last_dB:
            return "quiet"
        if thd_bound is True:
            return "ok" if last_dB else "quiet"
        return "ok"

    def observe(self, v, peak, snr, clipped, thd_bound=None,
                margin_db=None):
        # THE RAMP STOPS DOUBLING NEAR THE ANSWER -- but only when
        # approaching from BELOW, only once the capture is worth
        # believing, and only a few times. A margin past the bar means
        # the crossing is behind us and the closing phase owns the
        # search; a margin under an untrustworthy capture means
        # nothing; and a margin that hovers at the bar without ever
        # clearing it would be crept after forever. All three cost
        # REACH, and the ramp is allowed twelve adjustments.
        self.near = (margin_db is not None and snr is not None
                     and not clipped and snr >= mc.SNR_WARN_DB
                     and self.crept < AUTO_NEAR_STEPS
                     and mb.THD_FLOOR_GAP_DB - AUTO_NEAR_DB < margin_db
                     < mb.THD_FLOOR_GAP_DB)
        if self.near:
            self.crept += 1
        said = self.verdict(peak, snr, clipped, thd_bound)
        if said == "loud":
            p = 0.0 if clipped else peak
            if self.hi is None or v < self.hi[0]:
                self.hi = (v, p)
        elif said == "quiet":
            if self.lo is None or v > self.lo[0]:
                self.lo = (v, peak)
            if v >= self.ceil - 1e-3:      # at the ceiling, still quiet
                self.ceil = 1.0            # -> the device needs more
        else:
            if self.ok is None or v < self.ok[0]:
                self.ok = (v, peak)
        return said

    def settled(self):
        """True once the LOWEST ok level is known well enough to stop.

        An ok level is a CEILING to close on, not a destination, and
        the stop must not require THIS probe to be the ok one: as the
        bracket narrows the probes land on the QUIET side, and
        demanding both left a field hunt circling 69, 64, 67, 68, 69
        with the answer already in hand.
        """
        if self.ok is None:
            return False
        if self.lo is None:
            return True                    # ok at the first probe
        return self.ok[0] / max(self.lo[0], 1e-6) <= AUTO_SETTLE_RATIO

    def phase(self):
        """What the search is doing, in one word, for the operator."""
        if self.ok is not None:
            return "closing"
        if self.hi is not None:
            return "bracketed"
        return "ramp"

    def next_volume(self, v):
        if self.ok and self.lo:                  # closing on the lowest ok
            nv = math.sqrt(self.lo[0] * self.ok[0])
        elif self.lo and self.hi:                # bracketed: bisect
            nv = math.sqrt(self.lo[0] * self.hi[0])
        elif self.hi:                            # too loud, no floor yet
            nv = self.hi[0] * AUTO_CLIP_BACKOFF
        else:                                    # climb for the loud end
            nv = min(self._climb(v), self.ceil)
        return _clamp(nv)

    def _climb(self, v):
        """How much louder to ask for when there is no bracket yet.

        THE RAMP DOUBLES THE CUBIC, which is eighteen decibels a
        step. That was survivable only because the search used to
        settle before taking a second one: his very first probe
        cleared the bar and the ramp never ran. With the floor aimed
        at, more probes are quiet, and the first field run doubled
        15% to 30% and came back at 0.0 dBFS -- a sweep clipped into
        an earphone, on a card that can destroy one.

        So the step is CHOSEN rather than doubled. The peak follows
        the level one for one -- measured, 1.00 dB per dB on both of
        his ladders -- so the search knows exactly how far it has to
        go: enough to reach the aim, never enough to predict a peak
        past the ceiling, and never more than the ramp would have
        asked anyway.
        """
        step_db = 60.0 * math.log10(AUTO_RAMP_NEAR if self.near
                                    else AUTO_RAMP)
        creep_db = 60.0 * math.log10(AUTO_RAMP_NEAR)
        pk = self.lo[1] if self.lo else None
        if pk is None or not math.isfinite(pk):
            return v * 10.0 ** (step_db / 60.0)

        # AND SILENCE IS NOT QUIETNESS. A capture that hears NOTHING
        # reports the floor, and the arithmetic above then sees a
        # hundred decibels of room and asks for the whole ramp: his
        # Tanchjim walk went 15%, 30%, 60% and would have gone on,
        # because the coupler was not hearing the earphone at all.
        # Doubling into an earphone is the accident this ramp was
        # rewritten to prevent, and a silent capture is exactly when
        # it comes back.
        #
        # Nothing above is worth asking for until someone looks at
        # the coupler, the column or the sink, so the walk creeps
        # instead. Creeping is safe and gets nowhere, which is the
        # honest answer to "the microphone is not in this path".
        if pk <= SILENT_CAPTURE_DBFS:
            return v * 10.0 ** (min(creep_db,
                                    AUTO_PEAK_CEIL - pk) / 60.0)

        # THE CEILING CAPS EVERY STEP, whatever the reason for asking.
        # The first cut capped it only where the peak was BELOW the
        # aim and fell back to the full ramp where it was already
        # above -- which is exactly where a probe sits when it is
        # quiet for some OTHER reason, an untrustworthy SNR being the
        # one that bit. His run: peak already at -10.5, nothing left
        # to want, so the ramp doubled 19% to 38% and the sweep hit
        # 0.0 dBFS. No step may predict a peak past the ceiling.
        head = AUTO_PEAK_CEIL - pk
        # PAST the aim, not onto it: a step landing exactly on the aim
        # leaves the probe a rounding error below it, judged quiet
        # again, and the search asks for the same level forever.
        want = (AUTO_PEAK_FLOOR + AUTO_PEAK_BAND + 0.5) - pk
        if want > 0.0:
            db = min(step_db, want, head)
        else:
            # already past the aim and still not accepted: creep,
            # because whatever is refusing is not the level
            db = min(creep_db, head)
        return v * 10.0 ** (db / 60.0) if db > 0.0 else v


# THE MAP WALKS IN THE FINEST STEP IT CAN READ, which is not the step
# the search uses. They want different things: a search wants the
# ceiling quickly and a bold step gets there in three sweeps, while the
# map's output is a CURVE ALONG THE KNOB and its step is that curve's
# resolution. His JBL Tour Pro 3 came back with rungs at 80% and 93%
# and nothing between: the curve could only be green or red, while he
# could hear the change well before 93%.
#
# Two decibels is the floor, measured rather than chosen: two sweeps of
# one rig at one level disagree by about two tenths of a decibel, so a
# 2 dB step is read with room and a 1 dB step is not.
# HOW WIDE THE MAP'S STEP IS, and it is chosen for the BORDER rather
# than for speed.
#
# A bold stride does cover ground: eight decibels a rung reaches the
# top in two sweeps where two decibels takes four. But a border can
# only be pinned as tightly as the step that found it. A bracket
# narrower than two readable steps cannot be split -- each half would
# be under the two decibels two sweeps can be told apart by -- so a
# stride of eight leaves a bracket of about four and stops there,
# whatever else it does.
#
# Measured on his JBL, one rig, one sitting: a walk of 2 dB rungs went
# 74, 80, 87 and bracketed the border at 80-87%, which is 2.2 dB wide
# and contains the 84-85% where he hears the change. A walk of 8 dB
# rungs went 74, 86 and bracketed 74-86% -- 3.9 dB, no narrower than
# the stride, and it cannot be improved. One sweep saved, half the
# precision lost, and the border is the whole point.
MAP_STEP_DB = 2.0
MAP_MAX_RUNGS = 12

# HOW FAR BELOW THE SEARCH'S LEVEL THE MAP BEGINS.
#
# It used to begin AT it, and that put the whole map in the wrong
# place. The search settles at the loudest level a take can be made
# at; a listener plays much quieter than that, because the correction
# stands in between. On his iLoud, with a preamp of -17.5 and his
# taste layer, a knob at 85% delivers the equivalent of 58% at 50 Hz
# and 47% at 80 -- while the map ran from 66% up. Almost the whole
# range he actually uses lay BELOW the quietest rung, where a map
# says nothing at all.
#
# The other half of the same mistake: every rung is read against the
# first one, so the first one's loss is zero BY DEFINITION. Starting
# at the search's level meant the reference was the loudest level in
# the profile, and whether the rig was already running out there was
# never asked. A reference has to be quiet enough to be believed.
#
# Twelve decibels reaches down to a third of the search's level, which
# on his rigs covers where he listens and leaves room under it.
MAP_BELOW_DB = 12.0
MAP_DOWN_STEP_DB = 6.0   # the descent's step, three times the climb's
# HOW MANY OF THEM, counted rather than compared. The descent used to
# end where the level passed the one the search settled at, and with
# a 12 dB descent in 6 dB strides the third rung lands EXACTLY on that
# level: which side of it the comparison falls on is decided by the
# last bit of a float. One walk came back 12.2, 15.4, 19.4, 24.4, 26.3
# -- three coarse strides and a single fine one before the capture
# ceiling -- and the walk before it, from the same rig, had four fine
# rungs. A coin toss, not a rig.
#
# The count says the same thing and cannot be ambiguous: two strides
# down, everything after them fine.
MAP_DOWN_STEPS = 2


PASSPORT = "passport"    # where a map lives in a profile, one record
                         # per channel, OUTSIDE measurement


class SeatingChanged(Exception):
    """The rig is not sitting where the kept rungs were measured.

    Raised when a walk is asked to rebuild from a chosen rung and the
    one below it, played again, no longer answers as it did. Rungs
    from two seatings look exactly like a rig that gave out, and a
    map's rungs are never repeated -- so the spread that catches this
    between takes does not exist here and nothing downstream could
    notice.
    """


SEATING_K = 3.0          # how many times its own scatter a rung may
                         # be missed by and still be the same seating


# The third-octave series, 20 Hz to 20 kHz: the names a band is
# called by when it is named.
THIRDS_HZ = (20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0,
             160.0, 200.0, 250.0, 315.0, 400.0, 500.0, 630.0, 800.0,
             1000.0, 1250.0, 1600.0, 2000.0, 2500.0, 3150.0, 4000.0,
             5000.0, 6300.0, 8000.0, 10000.0, 12500.0, 16000.0, 20000.0)


def disagreement(diff, heard, scatter, ppo, k=SEATING_K, floor=0.0):
    """Where two sweeps that should be the same are not.

    ONE RULE FOR EVERY PLACE THAT ASKS IT. The two sweeps of the base,
    the kept rung played again for a rebuild, the takes of a channel
    -- each is a set of sweeps meant to be identical, and each place
    had judged them by a median over the whole band, which a shelf a
    quarter of the band wide does not move: his Liberty's leakage
    compensation put 3.9 dB below 300 Hz into one take of three and
    the three counted as clean, and a base replayed in the other
    state would have passed the seating check by the same arithmetic.

    Read the way the map reads a step: `diff` per bin on the walk's
    grid, `ppo` bins to the octave from 20 Hz, taken as the median of
    each third of an octave -- an event lasts a region, one bin is
    the spread between sweeps -- over the bins `heard` says are worth
    reading. A band disagrees when that median is more than `k` times
    what two undisturbed sweeps of the same rig disagree by in it
    (`scatter`, per bin, from the walk's own pair) and never by less
    than `floor`, which is the caller's business: the map's tolerance
    is a readable step, the takes' is nothing.

    Returns [(f_lo, f_hi, median_db, bar_db), ...] for the bands over
    the bar, in frequency order; empty when nothing disagrees.
    """
    d = np.asarray(diff, float)
    h = np.asarray(heard, bool)
    sc = np.asarray(scatter, float)
    n = min(len(d), len(h), len(sc))
    w = max(3, int(round(float(ppo) / 3.0)))
    out = []
    for j in range(len(THIRDS_HZ) - 1):
        lo, hi = j * w, min((j + 1) * w, n)
        if lo >= n:
            break
        idx = np.arange(lo, hi)
        idx = idx[h[idx] & np.isfinite(d[idx])]
        if idx.size < max(2, w // 3):
            continue
        med = float(np.median(d[idx]))
        own = sc[idx]
        own = own[np.isfinite(own)]
        bar = max(float(floor),
                  k * (float(np.median(own)) if own.size else 0.0))
        if abs(med) > bar:
            out.append((THIRDS_HZ[j], THIRDS_HZ[j + 1], med, bar))
    return out


def band_words(bands):
    """'100-315 Hz, 1.25-2 kHz': the disagreeing bands named, with
    neighbours joined into one range."""
    runs = []
    for lo, hi, _m, _b in bands:
        if runs and abs(runs[-1][1] - lo) < 1e-9:
            runs[-1][1] = hi
        else:
            runs.append([lo, hi])

    def hz(f):
        if f >= 1000.0:
            return ("%g" % (f / 1000.0))
        return "%g" % f
    words = []
    for lo, hi in runs:
        if hi >= 1000.0 and lo >= 1000.0:
            words.append("%s\u2013%s kHz" % (hz(lo), hz(hi)))
        elif hi >= 1000.0:
            words.append("%s Hz\u2013%s kHz" % (hz(lo), hz(hi)))
        else:
            words.append("%s\u2013%s Hz" % (hz(lo), hz(hi)))
    return ", ".join(words)


# What ended a walk, and whether the map it left is finished. The
# walk itself records only one word on its loudest rung.
FINISHED = ("capture", "knob", "rungs")


def map_state(rungs):
    """(finished, what) for a stored map.

    A MAP IS NOT A THING YOU CAN LOOK AT AND TELL. Eleven rungs of a
    walk that ran out of capture and eleven of a walk somebody stopped
    look exactly alike -- same shape, same spacing, same everything --
    and only the second one is missing the rungs that would have
    answered the question it exists for.

    The word is on the loudest rung and the walk puts it there:
    "capture" when the microphone ran out of room, "knob" when the
    volume reached its top, "rungs" when the budget of sweeps ran out,
    "asked" when a hand said stop. The first three are the walk ending
    on its own terms. The fourth is not an end, it is an interruption,
    and a map that carries it is unfinished however many rungs it has.

    A map with no word at all predates the walk recording one, and is
    reported the same way as an interruption: what is not known is not
    claimed.
    """
    got = sorted(rungs or [], key=lambda r: r["level"])
    if not got:
        return False, None
    what = got[-1].get("stopped_by")
    return (what in FINISHED), what


MODEL_LOW_SNR = 6.0      # the fit must reach at least this
                         # far down to constrain A


def scatter_model(rung):
    """(A, floor) for this walk, fitted from one rung, or None.

    A rung's two sweeps disagree by an amount that follows its SIGNAL
    TO NOISE RATIO plus a constant floor, and the whole point is that
    A is not a property of the world but of THIS EVENING: measured, it
    is six to seven decibels in a room and near nothing on a coupler,
    where the error of a rung-to-rung difference does not grow at all
    down to an SNR of minus fifteen. A swept-sine deconvolution
    spreads uncorrelated noise and concentrates the sweep, so a
    stationary hiss never reaches the answer -- but a room is not
    stationary, and rumble arrives whole.

    ONE RUNG IS ENOUGH because its own SNR spans sixty or seventy
    decibels across frequency: on a room walk each of the quiet rungs
    fitted within a third of the answer that all of them together
    gave. The loud ones cannot -- they never see a low SNR -- and the
    base of a walk is the quietest rung there is, which is why it is
    the one played twice.

    Two runs of the same room, deliberately made four decibels apart
    in noise floor, fitted A = 6.38 and 7.22 with floors of 0.100 and
    0.107: thirteen per cent apart and seven, which is what makes the
    model worth carrying at all.
    """
    sc = rung.get("scatter_db")
    mag = rung.get("mag_db")
    if not sc or not mag:
        return None
    if rung.get("heard_offset_db") is None and not rung.get("floor_db"):
        return None
    marg = margin_of(rung)
    n = min(len(sc), len(mag), len(marg))
    snr, val = [], []
    for i in range(n):
        if sc[i] is None or mag[i] is None or not math.isfinite(marg[i]):
            continue
        s = float(marg[i])
        v = float(sc[i])
        if math.isfinite(s) and math.isfinite(v) and v > 0:
            snr.append(s)
            val.append(v)
    if len(snr) < 40:
        return None
    snr = np.asarray(snr)
    val = np.asarray(val)
    xs, ys = [], []
    for a in np.arange(np.floor(snr.min()), snr.max(), 3.0):
        k = (snr >= a) & (snr < a + 3.0)
        if k.sum() >= 6:
            xs.append(a + 1.5)
            ys.append(float(np.median(val[k])))
    # AND THE FIT HAS TO HAVE SEEN THE QUIET END. A is what the
    # scatter does as the signal falls away, so a curve that never
    # goes there cannot constrain it: on a room walk the loudest rung,
    # whose SNR started at 22 dB, fitted A = 16.7 where every quiet
    # rung and all of them together said 5 to 8. An unconstrained A
    # can come out near zero and throw the gate wide open, which is
    # exactly the failure this replaced.
    #
    # An old profile, whose scatter was kept only where the base was
    # heard, has no quiet end by construction and therefore gets no
    # model -- it keeps the rule it was walked under.
    if len(xs) < 4 or min(xs) > MODEL_LOW_SNR:
        # A WALK THAT NEVER WENT QUIET HAS NO A TO FIT, AND NEEDS NONE.
        # With the margin read per bin against the measured floor,
        # his Origin's base sits 37 to 81 dB above its noise in every
        # bin; the old broadband margin had put it at 8 to 30 and the
        # fit had, by that accident, a quiet end to see. Refusing the
        # model here left the whole map unreadable -- no linear top,
        # no knee, no verdict -- for the cleanest walk the rig can
        # make. Every rung of a walk is at least as far from its noise
        # as the base is, so no bin will ever be asked about at a
        # margin the base did not see: the floor alone is the model,
        # valid everywhere the walk was. (Too few SNR bins to fit is
        # the same case: a margin that hardly varies across the band.)
        return (0.0, float(np.median(val)))
    xs = np.asarray(xs)
    ys = np.asarray(ys)
    # a coarse search rather than an optimiser: two parameters over a
    # decade each, and nothing here needs three digits
    best = None
    for A in np.geomspace(0.02, 60.0, 60):
        for F in np.geomspace(0.01, 3.0, 40):
            pred = np.sqrt((A * 10.0 ** (-xs / 20.0)) ** 2 + F ** 2)
            err = float(np.sum(np.abs(np.log(pred / ys))))
            if best is None or err < best[0]:
                best = (err, A, F)
    return (float(best[1]), float(best[2])) if best else None


def expected_scatter(model, snr):
    """What two sweeps of a rung at this SNR would disagree by."""
    A, F = model
    s = np.asarray(snr, float)
    return np.sqrt((A * 10.0 ** (-s / 20.0)) ** 2 + F ** 2)


def linear_top(rungs, ppo=None):
    """The loudest rung this channel still followed, or None.

    A rung "followed" when it delivered the step it was asked for
    everywhere it could be heard. shortfall() already answers that,
    per frequency, against the rung below -- with the answer read as
    the median of a third of an octave, because a rig runs out over a
    region and one bin below the line is the spread between sweeps,
    and only where the rung stood clear of its own noise, because
    otherwise the difference of two rungs is the difference of two
    noises.

    THE STEP IS THE CURVE'S OWN RISE, not the knob and not the capture
    peak. A knob decibel is not a decibel: his JBL answered 8 and 11
    dB to a 4 dB ask, and a fictitious ask makes a fictitious
    verdict; and the peak over-reads by the harmonics the curve is
    cleaned of (see asked_db).

    Returned as the LEVEL, because the level is what a fader is set
    to and what both channels share.
    """
    got = sorted(rungs or [], key=lambda r: r["level"])
    if len(got) < 2:
        return None
    ppo = float(ppo or 96.0)
    # NO MODEL, NO READING. The scatter of this walk's own sweeps is
    # what says whether a bin can be read at all, and a map that
    # cannot supply it is not a map this code knows how to read.
    model = scatter_model(got[0])
    if model is None:
        return None
    top = got[0]["level"]
    # A STEP THE KNOB TOOK AND THE DEVICE DID NOT IS NOT A FAILURE.
    # Liberty 5 answers Bluetooth's own scale in jumps: ten rungs
    # arrived at -24.1, -20.0, -20.1, -16.0, -16.0, -11.9, -7.9, -7.9,
    # -5.0, -2.9 dBFS, so every other rung landed where the one below
    # it already was. Judged pair by pair, the second of each such
    # pair delivers nothing and reads as the rig giving out -- which
    # is how a rig that followed to the top of its walk came back with
    # a working level of 41% instead of 75%, and every take after it
    # would have been made twenty decibels too quiet.
    #
    # So the comparison is against the last rung far enough BELOW to
    # be worth comparing with. Under MIN_READABLE_STEP nothing is
    # asked, nothing can be answered, and the reference stays put
    # until the asking adds up.
    ref = got[0]
    for cur in got[1:]:
        ask = asked_db(ref, cur)
        if ask < MIN_READABLE_STEP:
            top = cur["level"]
            continue
        prev = ref
        mag = np.asarray([np.nan if v is None else v
                          for v in cur.get("mag_db") or []], float)
        pmag = np.asarray([np.nan if v is None else v
                           for v in prev.get("mag_db") or []], float)
        n = min(len(mag), len(pmag))
        if n == 0:
            break
        heard = margin_of(cur, n)[:n]
        short, ok = shortfall(pmag[:n], mag[:n], heard, ask, None, ppo,
                              expected_scatter(model, heard))
        if not ok.any() or short.any():
            break
        top, ref = cur["level"], cur
    return top


def working_level(maps, ppo=None, settled=None):
    """The loudest level EVERY channel still follows. (level, why)

    This is what a passport is for. The takes of one canvas share one
    level -- one sink, one knob -- so the level that may be used is
    the quietest of the tops, and a rig whose channels differ decides
    it by its weaker side. His Origin answers 1.1 dB louder on one
    side than the other, and that is the ordinary case rather than a
    corner one.

    `why` names the channel that set it, because a number with no
    author is the kind this project has spent a day removing.

    None when no channel has a map worth reading: a level nobody
    measured must not arrive wearing the authority of one.
    """
    best, who = None, None
    for ch, rungs in sorted((maps or {}).items()):
        done, _what = map_state(rungs)
        if not done:
            continue
        top = linear_top(rungs, ppo=ppo)
        if top is None:
            continue
        # THE FADER CARRIES THE RECORDING LEVEL, NOT THE LOUDEST RUNG
        # CHECKED. The search's answer is the level that puts the
        # capture in its window -- SNR enough, headroom enough -- and
        # that is what a take should be played at. The top of the map
        # is something else: on a rig whose knee is out of reach it is
        # simply where the microphone ran out, peak -2 dBFS, no margin
        # for a click at all. His Origin: linear to 26%, no knee found,
        # and the fader stood at 26 because "checked this far" had
        # been read as "may go this far".
        #
        # So the level is the search's, capped by the knee when the
        # map found one below it -- with a fine step of margin, since
        # a take played on the knee itself is played where the rig
        # has just begun to give up. A map with no knee caps nothing.
        # Passports written before the search was remembered have no
        # answer to use and fall back to the top, as before.
        lvl = (settled or {}).get(ch)
        knee = knee_of(rungs, ppo=ppo)
        if lvl is None:
            # no search remembered: the top, as before, and the
            # quieter channel decides as it always did
            if best is None or top < best:
                best, who = top, ch
            continue
        # ONE FADER FOR ALL CHANNELS, AND WITH NO KNEE THE LOUDER WINS.
        # Each channel's search found the level that puts ITS capture
        # in the window; his Origin answers 19% on one side and 20% on
        # the other. The window is a range, not a point, so the louder
        # answer keeps both sides inside it and buys the quieter side
        # a decibel of SNR. The quieter answer would buy nothing. Only
        # a knee below a channel's answer overrules, and then it is
        # that channel's knee, with a fine step of margin, that caps
        # the whole fader.
        if best is None or lvl > best:
            best, who = lvl, ch
        if knee is not None:
            cap = knee * 10.0 ** (-KNEE_MARGIN_DB / 60.0)
            if cap < best:
                best, who = cap, ch
    return best, who


KNEE_MARGIN_DB = 2.0     # a fine step below the knee, so a take is
                         # never played where the rig has just begun
                         # to give up


def knee_of(rungs, ppo=None):
    """Where the rig stopped following, or None when it never did.

    The loudest rung that followed is the top of the map only when
    the walk ended because the CAPTURE ran out; then nothing above it
    was refused and there is no knee -- only "linear at least this
    far". When a rung above the loudest followed one exists, that rung
    did not follow, and the loudest followed one is the knee.
    """
    rs = sorted((r for r in rungs or [] if r.get("level") is not None),
                key=lambda r: r["level"])
    if len(rs) < 2:
        return None
    top = linear_top(rs, ppo=ppo)
    if top is None:
        return None
    return top if top < rs[-1]["level"] - 1e-9 else None


def settled_of(prof):
    """The level each channel's search settled at, from the passport."""
    out = {}
    for ch, rec in (prof.get(PASSPORT) or {}).items():
        v = (rec or {}).get("settled")
        if v is not None:
            out[ch] = float(v)
    return out


def rolled_back(rungs, keep):
    """The map truncated to its lowest `keep` rungs.

    A MAP IS A STACK. Each rung is read against the ones below it and
    the step to the next is computed from the last pair, so a rung
    taken later than its neighbours was taken in conditions they know
    nothing about. Pulling one out of the middle would leave a hole
    that can only be refilled at a level nobody recorded, in a seating
    nobody checked. So a rung is dropped by dropping everything above
    it, and the walk goes on from the new top.
    """
    keep = max(0, int(keep))
    return sorted(rungs or [], key=lambda r: r["level"])[:keep]


def _ppo_of(freqs):
    """Bins per octave of a log grid, from its first two points."""
    f = np.asarray(freqs, float)
    if f.size < 2 or f[0] <= 0 or f[1] <= f[0]:
        return mc.GRID_PPO
    return max(1, int(round(1.0 / math.log2(f[1] / f[0]))))


def _check_seating(base, again, k=SEATING_K, ppo=None):
    """Raise SeatingChanged if the kept top rung no longer answers as
    it did. Compared only where it was heard and only against its own
    scatter -- a threshold of ours would be a number nobody measured.

    BY THIRDS OF AN OCTAVE, not one median over the whole band. The
    whole-band median was chosen to ignore the spikes one sweep
    always has, and it does -- and it also ignores a shelf a quarter
    of the band wide: his Liberty's leakage compensation put 3.9 dB
    below 300 Hz into one state of the bud and not the other, and a
    base replayed in the other state would have passed, with
    three-quarters of its bins agreeing to a tenth. A region is what
    an event or a state lasts; a bin is the spread between sweeps.

    The scale is the base's own measured scatter in each band, not a
    model of it by SNR: on the Origin the pair disagrees by 0.3 dB at
    20 Hz and by 0.02 at a kilohertz with the margin over noise the
    same in both, so a global floor would refuse every walk at the
    bottom of the band. The map's tolerance, a readable step, stays
    as the floor of the bar.

    A rung with no scatter of its own predates the second base sweep.
    Nothing can be checked then and nothing is claimed.
    """
    old = base.get("mag_db") or []
    sc = base.get("scatter_db")
    if not old or not sc:
        return
    new_mag = np.asarray(getattr(again, "mag_db", again), float)
    n = min(len(old), len(sc), len(new_mag))
    old_mag = np.asarray([np.nan if v is None else v
                          for v in old[:n]], float)
    own = np.asarray([np.nan if v is None else v for v in sc[:n]], float)
    marg = margin_of(base, n)
    heard = np.isfinite(marg) & (marg >= HEARD_OVER_NOISE_DB)
    bands = disagreement(new_mag[:n] - old_mag, heard, own,
                         ppo or mc.GRID_PPO, k, floor=MIN_READABLE_STEP)
    if bands:
        worst = max(bands, key=lambda b: abs(b[2]))
        raise SeatingChanged(
            "the kept rungs answer %.1f dB differently at %s now, "
            "against %.1f dB of their own scatter: the rig is not "
            "sitting where they were measured, so nothing may be "
            "built on top of them"
            % (abs(worst[2]), band_words(bands), worst[3]))



def sweep_record(v, peak_db, got):
    """What one sweep of a walk leaves behind, whichever half of the
    walk it served.

    A probe and a rung are the same kind of thing -- a sweep at a
    known level with a known peak, a measured curve and the floor it
    was measured over -- and were built in two places with two copies
    of the same rounding, one of which had lost the broadband offset
    along the way. One record, and whatever else a sweep says (the
    search's observation, the base's pair) is added to it.

    ROUNDING IS FOR STORAGE, NOT FOR ARITHMETIC: a walk that has to
    decide something from a level or a peak keeps the exact number.
    """
    floor = getattr(got, "thd_noise_db", None)
    # the response is relative and the noise absolute, so one offset
    # turns the whole curve into a margin over this take's own floor
    noise, signal = (getattr(got, "noise_dbfs", None),
                     getattr(got, "signal_dbfs", None))
    off = (float(noise) - float(signal)
           if noise is not None and signal is not None else None)
    return {"level": round(float(v), 4),
            "peak_dbfs": round(float(peak_db), 2),
            "heard_offset_db": None if off is None else round(off, 2),
            # THE FLOOR, PER BIN: the harmonic measurement's own noise
            # floor re the response, which is the margin with its sign
            # flipped. One number for the whole band called a base
            # deaf across its middle.
            "floor_db": [None if not math.isfinite(x) else round(float(x), 1)
                         for x in np.asarray(
                             floor if floor is not None else [], float)],
            "mag_db": [None if not math.isfinite(x) else round(float(x), 2)
                       for x in np.asarray(
                           getattr(got, "mag_db", []), float)]}


def probe_records(probes):
    """The search's sweeps as passport records, in the order played.

    Each is the sweep's record with the search's observation on it --
    peak, SNR, clipping, the THD bound and the margin the controller
    was shown, and what it made of them -- so a search is remembered
    whole: what it played, in what order, what it saw and what it
    judged. That is what lets a search be RESUMED from its record
    rather than replayed through the speakers.
    """
    out = []
    for p in probes or []:
        # EXACT, NOT ROUNDED: the level and the peak are what the
        # controller is shown again when a search is resumed, and a
        # peak rounded to two places asked for 0.4654 where the
        # search had asked 0.4653. What decides must be stored as it
        # was decided on.
        out.append({"step": int(getattr(p, "step", 0) or 0),
                    "level": float(p.volume),
                    "peak_dbfs": float(p.peak_dbfs),
                    "snr_db": getattr(p, "snr_db", None),
                    "clipped": bool(getattr(p, "clipped", False)),
                    "thd_pct": getattr(p, "thd_pct", None),
                    "thd_bound": getattr(p, "thd_bound", None),
                    "margin_db": getattr(p, "margin_db", None),
                    "verdict": getattr(p, "verdict", None),
                    "phase": getattr(p, "phase", None),
                    "mag_db": list(getattr(p, "mag_db", None) or []),
                    "heard_offset_db": getattr(p, "heard_offset_db", None),
                    "floor_db": list(getattr(p, "floor_db", None) or [])})
    return out


def margin_of(rec, n=None):
    """Each bin's margin over the take's own floor, in dB, as an array
    with NaN where there is none.

    THE FLOOR IS PER BIN, and the analysis already measures it: the
    floor of the harmonic measurement, the grey line under every take.
    The old margin was one number for the whole band -- the broadband
    SNR of the sweep -- laid over a per-bin magnitude, and it was off
    by tens of decibels wherever the noise is not average: it called
    the base of a ladder deaf across the middle of the band, 8 dB of
    margin by its arithmetic against 45 by the floor, and every line
    read against that base opened with a hole.
    """
    mag = np.asarray([np.nan if v is None else v
                      for v in rec.get("mag_db") or []], float)
    floor = rec.get("floor_db")
    if floor:
        fl = np.asarray([np.nan if v is None else v for v in floor], float)
        # WHERE THE HARMONIC MEASUREMENT STOPS, THE FLOOR DOES NOT
        # LEAP. Harmonics of the top octave fall past the band's edge,
        # so the floor is not measured there -- 97 bins of 958 above
        # 12 kHz -- and reading "not measured" as "not heard" blanked
        # the top of every line on every rung, which is the blindness
        # this floor was brought in to end. The nearest measured
        # floor stands in: the noise does not change character at
        # the bin where the measurement ran out of room.
        good = np.isfinite(fl)
        if good.any() and not good.all():
            idx = np.arange(len(fl))
            fl = np.interp(idx, idx[good], fl[good])
        m = min(len(mag), len(fl))
        out = np.full(len(mag), np.nan)
        out[:m] = -fl[:m]
        out[np.isnan(mag)] = np.nan
    else:
        off = rec.get("heard_offset_db")
        out = (mag - float(off)) if off is not None \
            else np.where(np.isnan(mag), np.nan, np.inf)
    if n is not None and len(out) < n:
        out = np.concatenate([out, np.full(n - len(out), np.nan)])
    return out


def passport_of(prof):
    """The maps a profile carries, {channel: record}.

    A MAP IS ITS OWN UNIT OF MEASUREMENT and outlives everything else
    in the profile. It used to live inside a session block, and a
    session block is pruned the moment its last take is deleted -- so
    remeasuring a rig threw away the walk that described it, silently,
    and one was lost exactly that way. It also reached disk only when
    a session already existed, which an earlier cut worked around by
    keying a pseudo session 'headroom' whose block the readers then
    stepped straight over.

    Neither is a home. A map describes what the rig can follow; takes
    describe its response. They are answers to different questions and
    they die on different days.
    """
    got = prof.get(PASSPORT)
    return dict(got) if isinstance(got, dict) else {}


def maps_of(prof):
    """{channel: rungs} from wherever this profile keeps them.

    New home first, then the session blocks, so a profile written
    before the move still draws. The pseudo session is read too: it
    holds a real walk and nothing has ever looked at it.
    """
    out = {}
    m = prof.get("measurement") or {}
    for sid, blk in sorted((m.get("sessions") or {}).items()):
        blk = blk or {}
        hr = blk.get("headroom")
        if isinstance(hr, dict):
            for ch, rungs in hr.items():
                if rungs:
                    out[str(ch)] = rungs
        elif sid == PASSPORT_LEGACY_SID and isinstance(blk, dict):
            for ch, rungs in blk.items():
                if rungs and isinstance(rungs, list):
                    out[str(ch)] = rungs
    for ch, rec in passport_of(prof).items():
        rungs = (rec or {}).get("rungs")
        if rungs:
            out[str(ch)] = rungs
    return out


PASSPORT_LEGACY_SID = "headroom"


def _next_step_db(exact, v, from_peak, taken, step_db,
                  stop_peak_dbfs):
    """How much louder the next rung asks for, in decibels.

    `taken` is how many rungs the ladder already holds, which is what
    decides coarse from fine: the first MAP_DOWN_STEPS strides cover
    the descent and everything above them is walked finely. Shared by
    the loop and by the first rung of a rebuild, so a rebuilt map is
    stepped exactly like a fresh one -- and because the rule is
    positional, a rebuild needs nothing recorded to know where it is.
    """
    step = float(step_db)
    if taken is not None and taken <= MAP_DOWN_STEPS:
        step = max(step, MAP_DOWN_STEP_DB)
    ratio = 2.0
    if len(exact) >= 2:
        (lv0, pk0), (lv1, pk1) = exact[-2], exact[-1]
        knob = 60.0 * math.log10(lv1 / lv0)
        if knob > 0.5:
            ratio = max(1.0, (pk1 - pk0) / knob)
    room = (stop_peak_dbfs - from_peak) / max(ratio, 1e-6)
    return min(step, room)


def _first_step(exact, v, taken, step_db, stop_peak_dbfs):
    """The step off a kept rung, using that rung's own peak."""
    from_peak = None
    for lv, pk in exact:
        if abs(lv - v) < 1e-9:
            from_peak = pk
    if from_peak is None:
        from_peak = exact[-1][1] if exact else stop_peak_dbfs
    return max(MIN_READABLE_STEP,
               _next_step_db(exact, v, from_peak, taken, step_db,
                             stop_peak_dbfs))


def headroom_map(sink, source, channels, start_volume, sink_name=None,
                 analyze=0, sweep=None, freqs=None,
                 pre_silence=None, post_silence=None, play_map=None,
                 on_level=None, should_stop=None, on_rung=None,
                 on_step=None,
                 stop_peak_dbfs=AUTO_PEAK_CEIL, step_db=MAP_STEP_DB,
                 max_rungs=MAP_MAX_RUNGS, have=None):
    """Climb from the level the search settled at, keeping what each
    rung bought -- the map of where this rig stops answering.

    WHY IT IS AN EPILOGUE AND NOT THE SEARCH. The two want opposite
    things: the search must stop as soon as it can name a safe level,
    while the map has to go UP until something gives or the capture
    runs out. But the tedious half is already done by the time the
    search settles -- the quiet rungs, the approach -- so the map
    costs three or four sweeps rather than ten.

    IT IS ALSO OPTIONAL. A profile without one is a profile that
    cannot say where the rig runs out; nothing else about it changes.

    Returns a list of rungs, quietest first, each carrying the level,
    the capture peak, the response, and the offset that turns the
    response into a margin over that take's own noise. The reading
    rule is deliberately NOT baked in: the curves are kept whole so a
    later rule can be applied to old profiles without playing a note.
    Three different rules were tried on this data in one evening.

    `spl_db` is reserved and always None. It needs one calibration
    against a sound level meter held AT THE MICROPHONE, and until
    that exists a ceiling can only be named in the units of the knob
    that produced it.
    """
    sweep = sweep or mc.default_sweep()
    freqs = mc.log_grid() if freqs is None else freqs
    pre = mc.DEFAULT_PRE_SILENCE if pre_silence is None else pre_silence
    post = (mc.DEFAULT_POST_SILENCE if post_silence is None
            else post_silence)
    name = sink_name or (sink.get("name") if isinstance(sink, dict)
                         else sink)
    if not name:
        raise ValueError("the moratorium needs the sink's node name")
    outdir = tempfile.mkdtemp(prefix="pdeq-map-")
    wav = write_sweep_files(outdir, sweep, pre, post)
    duration = pre + sweep.duration_s + post
    back = pw_backend.backend()

    rungs = list(have or [])
    # (level, peak) unrounded, for the decisions. Seeded from the
    # rungs being kept, because the step is chosen from the ratio
    # between the last two and a rebuild must not rediscover it.
    exact = [(float(r["level"]), float(r["peak_dbfs"])) for r in rungs
             if r.get("peak_dbfs") is not None]
    scatter = None          # the base rung against a repeat of itself
    v = _clamp(start_volume)
    if rungs:
        # REBUILDING FROM A CHOSEN RUNG. Everything at or above it was
        # dropped by the caller; the walk climbs on from the highest
        # one kept. It costs ONE extra sweep, and that sweep is not
        # optional: the kept top rung is played again and has to
        # agree with itself, or the new rungs and the old ones are
        # about two different rigs.
        top = max(rungs, key=lambda r: r["level"])
        v = _clamp(float(top["level"]))
        # BEFORE THE SOUND, like every other sweep here. This one is
        # played to check the seating, and it went out silent: a hand
        # watching a rebuild saw a sweep begin with no level and no
        # step on the line. The rule that puts the announcement first
        # exists because a level is worth knowing while it can still
        # be refused, and a sweep nobody announced is the one case it
        # was written against.
        if on_level is not None:
            on_level(v, "seating")
        try:
            again = _play_rung(back, name, sink, source, wav, duration,
                               channels, sweep, freqs, analyze,
                               float(top["level"]), play_map)[3]
        except Exception:
            again = None
        if again is not None:
            _check_seating(top, again, ppo=_ppo_of(freqs))
        max_rungs = max(0, int(max_rungs) - len(rungs))
        # AND THE FIRST NEW RUNG GOES ABOVE THE KEPT ONE, not on top
        # of it. The loop plays wherever v stands, so seeding v with
        # the kept top made the walk measure that level a second time
        # and append it: his rebuilt map came back with two rungs at
        # 12%. The step is chosen the same way every other step is,
        # once, before the loop begins.
        v = _clamp(v * 10.0 ** (_first_step(exact, v, len(rungs),
                                            step_db, stop_peak_dbfs)
                                / 60.0))
    # WHAT STOPPED THE WALK. It never stops because the rig gave out
    # -- a map does not bracket, it climbs past a ceiling on purpose,
    # since the rungs above one are where the loss grows. So the
    # reason is always about the WALK: "capture" when the microphone
    # ran out of room, "knob" when the volume is at its top, "rungs"
    # when the budget of sweeps ran out, "asked" when a caller said
    # stop.
    #
    # It is recorded because it decides what may be said about levels
    # ABOVE the loudest rung. If the top rung already shows a loss,
    # louder is worse and that may be stated: the mechanisms here are
    # monotone in level -- a port breaks up faster with flow, a driver
    # runs further out of stroke, a limiter clamps harder. But if the
    # top rung is clean and the CAPTURE stopped us, nothing at all is
    # known up there and the rig may well be fine: his Tanchjim
    # answered every rung of fourteen decibels and stopped only
    # because the microphone did.
    #
    # Three of his five rigs stop at the microphone rather than at
    # themselves, so the difference is not a corner case.
    stopped = "rungs"
    try:
        for i in range(int(max_rungs)):
            if should_stop is not None and should_stop():
                stopped = "asked"
                break
            # WHAT THIS SWEEP IS FOR, not only where it is played.
            # Two sweeps announced identically say nothing about why
            # the second one is sounding, and there are two of them
            # for two different reasons: a fresh walk plays its base
            # twice at ONE level to measure the scatter every later
            # reading is judged against, and a rebuild replays the
            # kept top at ITS level to check the rig still sits where
            # it did. Read off a line that says only "step 1" twice,
            # the first looks like a stutter and the second like a
            # step played at the wrong volume.
            if on_level is not None:
                on_level(v, i + 1)
            chan, peak_db, clipped, got = _play_rung(
                back, name, sink, source, wav, duration, channels,
                sweep, freqs, analyze, v, play_map)

            # THE FIRST RUNG IS PLAYED TWICE, and the pair is the
            # map's own measure of how much two sweeps of one rig
            # disagree. Everything above is read against this rung, so
            # its scatter is the floor a loss has to clear to be worth
            # believing -- and taking that floor from the profile's
            # TAKES instead tied a map to a measurement it has nothing
            # to do with. A map is a property of the rig; the takes
            # are an answer about its response. One sweep more, and
            # the two come apart.
            if i == 0 and not have:
                if on_level is not None:
                    on_level(v, "scatter")
                again = _play_rung(back, name, sink, source, wav,
                                   duration, channels, sweep, freqs,
                                   analyze, v, play_map)[3]
                a = np.asarray(got.mag_db, float)
                b = np.asarray(again.mag_db, float)
                # A BASE THAT DISAGREES WITH ITSELF BY A WHOLE LEVEL
                # IS NOT A SCATTER, IT IS A RUINED SWEEP -- and this
                # is the rung everything else is read against, so it
                # is the worst one to keep quietly. Measured, the two
                # sweeps of a rung differ by tenths of a decibel: a
                # tenth on a coupler, a fifth in a room. One walk came
                # back with 26 dB between them, its correction still
                # engaged for the first sweep.
                #
                # The louder of the two is kept, because these
                # failures take sound AWAY -- a bypass that did not
                # land, a volume that did not, a link still waking.
                # None of them makes a sweep louder than the truth.
                lift = float(np.nanmedian(b - a))
                if abs(lift) > MIN_READABLE_STEP:
                    if lift > 0:
                        got, a = again, b
                    b = a
                scatter = np.abs(a - b)
                # AND THE BASE IS BUILT FROM THE PAIR, not taken from
                # one of them. Every line of the fan is a rung minus
                # this one, so whatever is in this ONE recording is
                # copied, inverted, into every line of the picture --
                # and it stays there through every rebuild, because a
                # rebuild keeps the base and re-measures the rest. His
                # field report: the same saw survived four rebuilds
                # and vanished the moment the base itself was walked
                # again.
                #
                # Worse, it is the one rung the reading cannot name: a
                # fault in the end rung of a ladder reads as zero at
                # itself and lands on its neighbour, so the sweep that
                # poisons every line is invisible to the detector that
                # exists to find such things.
                #
                # The pair is the cure and it costs no sound, since
                # the second sweep is already played. Where the two
                # agree, average: the base's own noise falls by root
                # two. Where they disagree by more than the walk's own
                # scale, one of them has an EVENT in it -- a click, a
                # bark, a chair -- and the quieter is the honest
                # choice, because an event adds energy and never takes
                # it away.
                got = _Rebased(got, blend_base(a, b))
                # KEPT EVERYWHERE, including where the rung was not
                # heard. It used to be blanked there, on the ground
                # that two noises compared give whatever the room felt
                # like -- true, and it was harmless while nothing read
                # it. Now the scatter is what DECIDES whether a bin
                # can be read at all, and blanking it exactly where
                # the question is hardest leaves the decision with
                # nothing to go on: on a Bluetooth walk it survived at
                # ten bins of 958.
                #
                # A large number here is not a trap, it is the answer:
                # 33.95 dB at the edges of an iLoud walk says the
                # reference is worthless there, which is precisely
                # what a reader needs to be told.

            # A CALLER MAY WANT THE CAPTURE ITSELF. Keeping the wav is
            # not the package's business -- but a walk that disturbs a
            # room for a minute and throws the recordings away is a
            # walk that has to be repeated for every new question, and
            # this project has re-read one evening's rungs a dozen
            # times. The hook hands them over; what to do with them is
            # the caller's.
            if on_rung is not None:
                on_rung(i + 1, v, chan, sweep)
            # ROUNDING IS FOR STORAGE, NOT FOR ARITHMETIC. The walk
            # keeps exact levels and peaks for its own decisions: with
            # a 2 dB step and a peak rounded to two places, the step
            # between two rungs comes out 1.99 and the comparison that
            # asks for MIN_READABLE_STEP throws it away -- so no
            # bracket ever forms and the walk marches to the top.
            exact.append((float(v), float(peak_db)))
            rung = sweep_record(v, peak_db, got)
            # only a FRESH base carries the pair: a rebuild adds rungs
            # above one that already has a scatter, and a second one
            # halfway up would put two floors in one map
            rung["scatter_db"] = ([None if not math.isfinite(x)
                                   else round(float(x), 3)
                                   for x in scatter]
                                  if i == 0 and not have else None)
            rung["spl_db"] = None
            rungs.append(rung)
            # HANDED OVER AS IT IS TAKEN. A map used to appear all at
            # once when the walk ended, so a hand watching a rig climb
            # had a blank canvas and a status line for a minute -- and
            # the whole point of drawing rungs is to see a bad one
            # while there is still a walk to stop.
            if on_step is not None:
                on_step(list(rungs))
            if clipped:
                stopped = "capture"
                break
            # WHERE THE NEXT RUNG GOES: one readable step up, every
            # time, until a brake stops the walk.
            #
            # IT DOES NOT CLOSE ON THE BORDER. Bracketing is the
            # SEARCH's job -- it wants one number and stops the moment
            # it has it. A map's rungs are the points of a curve, and
            # the ones ABOVE a border are the most valuable of all,
            # because that is where the loss grows. An earlier cut of
            # this walked boldly and halved onto the border, and then
            # stopped there: on his iLoud it ended at 57% while the
            # level he listens at delivers 70% at 40 Hz, so the map
            # stopped just short of the only part he needed.
            #
            # With an even step the border is located to that step
            # anyway, and the climb continues past it. Nothing is
            # given up.
            # COARSE UNTIL THE INTERESTING PART. The rungs below the
            # level the search settled at exist for two reasons: to
            # reach down to where a listener actually plays, since the
            # correction stands between and takes 13 to 16 dB off on
            # his three profiles, and to give the reading a reference
            # quiet enough to be believed. Neither wants RESOLUTION
            # down there -- the border is above, and that is where the
            # fine step belongs.
            #
            # Six rungs of the descent become two or three, and the
            # verdict does not move: thinning the bottom of his own
            # two ladders from 2 dB to 6 left both channels of both
            # rigs on exactly the level they had. Four sweeps a
            # channel, and four fewer chances for an earphone to fall
            # out mid-walk.
            #
            # It reads EASIER, not harder: every rung is judged
            # against the base, so a bigger step is a bigger ask.
            # the peak follows the level one for one, so the walk
            # knows before it plays where a step would land
            from_peak = peak_db
            for lv, pk in exact:
                if abs(lv - v) < 1e-9:
                    from_peak = pk
            take = _next_step_db(exact, v, from_peak, len(rungs),
                                 step_db, stop_peak_dbfs)
            if take < MAP_TOP_STEP_DB:
                stopped = "capture"
                break
            nxt = _clamp(v * 10.0 ** (take / 60.0))
            # AND THE KNOB HAS A TOP. Once it is there the walk cannot
            # buy another rung, and asking for one plays the same sweep
            # again and again -- a synthetic rig with room to spare
            # took five identical rungs at 100%.
            if nxt <= v + 1e-9:
                stopped = "knob"
                break
            v = nxt
    finally:
        for fn in os.listdir(outdir):
            try:
                os.unlink(os.path.join(outdir, fn))
            except OSError:
                pass
        try:
            os.rmdir(outdir)
        except OSError:
            pass
    rungs.sort(key=lambda r: r["level"])
    if rungs:
        rungs[-1]["stopped_by"] = stopped
    return rungs


def summary(volume, probes):
    """What the search did, for the passport.

    THE SEARCH DESCRIBES ITSELF and the session merely carries it. A
    session records takes; asking it to also report how a level was
    chosen would put the search back inside it through the schema.
    """
    ok = [p for p in probes if not p.clipped and p.snr_db is not None]
    return {"enabled": True,
            "initial": round(probes[0].volume, 4) if probes else None,
            "adjustments": len(probes),
            "final": round(float(volume), 4),
            "in_window": bool(ok) and any(
                AUTO_PEAK_FLOOR <= p.peak_dbfs <= AUTO_PEAK_CEIL
                for p in ok)}


# WHAT A RUNG BOUGHT, and the rule for reading it. It lives here
# rather than in the tool that first used it, because the map it
# builds now lands in a profile: one implementation, so a walk and a
# take cannot come to different conclusions about the same rig.
#
# Ask for four decibels more and a rig with headroom gives four; one
# that has run out gives nothing, and every further turn of the knob
# buys distortion alone. Nothing here is a threshold anyone chose:
# the step is what WE asked for, the answer is what the deconvolution
# recovered, and short of half the step is short by any reading.
ANSWER_SHORT = 0.5          # of the asked step; below this it is scatter
# WHETHER A RUNG CAN BE A REFERENCE -- not whether a bin can be read.
# The two questions shared this constant for a long time and are not
# the same. Everything is read against the base, so the base's own
# noise enters every reading and it has to be audible; but a bin of a
# rung that IS audible reads to a tenth of a decibel far below this,
# because a deconvolution spreads stationary noise and concentrates
# the sweep. Readability is decided by the disagreement of this
# walk's own sweeps, in shortfall().
HEARD_OVER_NOISE_DB = 10.0
MIN_READABLE_STEP = 2.0
#                             measured: the scatter between sweeps is
#                             two tenths of a decibel, so a 2 dB step
#                             is read with room and a 1 dB step is not
WORTH_A_LINE_DB = 0.5 * MIN_READABLE_STEP
#                             the smallest loss worth a word or a
#                             colour: half a readable step. A deficit
#                             can clear the scatter and still be
#                             nothing -- a coupler resolves hundredths,
#                             and "short by up to 0.0 dB" is a true
#                             measurement and an absurd sentence. Not
#                             a taste: below this, two sweeps of one
#                             rig are already closer together than
#                             the claim. The advice line, the strip,
#                             the cubes and the curve all read it.


def asked_db(prev, cur):
    """How much louder a rung ACTUALLY got, in decibels: the rise of
    its own curve.

    The knob's ratio is only an intention. Over Bluetooth it is not
    even that: his JBL Tour Pro 3 answers AVRCP's 128-step scale, and
    a rung the walk asked 4.0 dB of arrived 8.0 and 11.0 dB louder.
    Everything downstream compares what came back against what was
    asked, so a fictitious ask makes a fictitious verdict -- where
    twice the step arrived and one step was taken, the band is a step
    short and the walk called it "answered in full".

    THE CAPTURE PEAK WAS THE WITNESS, AND IT OVER-READS. On every rig
    he owns, wired Origin included, the peak of the recording rose a
    few tenths of a decibel more than the curve at the peak's own
    frequency, and more the louder the rung: 0.14 dB at the top of
    the Origin's walk, 0.29 on the Denon. The mechanism is the one
    the whole method rests on (Farina, 2000): the exponential sweep
    puts each harmonic order at its own time in the deconvolved
    response, ahead of the linear one, and the curve is read after
    them -- so the curve carries no harmonic; the raw recording has
    them all in the same samples as the fundamental, and its largest
    sample is the instant where they add. The peak carries every
    harmonic the curve was cleaned of, and their share grows about a
    decibel per decibel of level. From each take's own harmonics at
    the frequency where its recording peaks, grown to the top rung,
    the excess comes out 0.08 to 0.20 dB against 0.00 to 0.29
    observed: the right sign on every linear rig, the size within a
    factor of two. Every reading against the peak was short by that
    everywhere, and the loss curve went red on it.

    The fundamental's own level is the honest witness, and the curve
    already is it: the sweep file is the same at every rung, so a
    rung rose by what its curve rose, read as the median over the
    bins both rungs heard. Over Bluetooth that is still the device's
    own scale -- the Liberty's curves rose 8.00, 0.01 and 3.06 dB to
    knob asks of 6, 2 and 2. What neither this nor the peak can see
    is a rig compressing the whole band alike; the peak compresses
    with it. The knob's ratio is the fallback where no curve can
    speak. The peak keeps its one real job, the capture ceiling.

    `prev` and `cur` are rung records: level, mag_db and the floor.
    """
    pm = np.asarray([np.nan if v is None else v
                     for v in prev.get("mag_db") or []], float)
    cm = np.asarray([np.nan if v is None else v
                     for v in cur.get("mag_db") or []], float)
    n = min(len(pm), len(cm))
    if n:
        mp, mq = margin_of(prev, n), margin_of(cur, n)
        with np.errstate(invalid="ignore"):
            ok = (np.isfinite(pm[:n]) & np.isfinite(cm[:n])
                  & np.isfinite(mp) & np.isfinite(mq)
                  & (mp >= HEARD_OVER_NOISE_DB)
                  & (mq >= HEARD_OVER_NOISE_DB))
        if ok.sum() >= max(1, n // 8):
            return float(np.median((cm[:n] - pm[:n])[ok]))
    return 60.0 * math.log10(float(cur["level"]) / float(prev["level"]))


def running_median(x, w, need=1, fill=np.nan):
    """The median of a window of `w` bins centred on each bin, over
    the finite values in it; `fill` where fewer than `need` are.

    THE LOOP THIS REPLACES COST THE WINDOW ITS SPEED. Four places
    took a third-octave median bin by bin -- a Python loop over 958
    bins calling np.median on a slice, thirty microseconds a call --
    and the passport's verdict runs it once per step of the ladder
    for linear_top and once again for the knee: eleven thousand
    calls, a third of a second, on every repaint of the passport
    canvas and again at every draw. Opening the measure window and
    switching a channel waited on it.

    One sort of an (n, w) view does the same arithmetic at once: NaN
    sorts last, so the median of each row's finite values is the
    middle of its first `count` entries -- exactly what np.median of
    the filtered slice returned, bin for bin.
    """
    x = np.asarray(x, float)
    n = x.size
    if n == 0:
        return np.full(0, fill)
    h = int(w) // 2
    padded = np.concatenate([np.full(h, np.nan), x, np.full(h, np.nan)])
    win = np.lib.stride_tricks.sliding_window_view(padded, 2 * h + 1)
    srt = np.sort(win, axis=1)                    # NaN sorts to the end
    cnt = np.isfinite(win).sum(axis=1)
    lo = np.take_along_axis(srt, np.maximum(cnt - 1, 0)[:, None] // 2,
                            axis=1)[:, 0]
    hi = np.take_along_axis(srt, (np.maximum(cnt, 1)[:, None] // 2),
                            axis=1)[:, 0]
    med = 0.5 * (lo + hi)
    return np.where(cnt >= max(1, int(need)), med, fill)


def shortfall(prev_mag, cur_mag, heard, asked_db, freqs, ppo,
              scatter):
    """Where a rung bought less than half of what was asked.

    A rig runs out over a REGION, not at one frequency, so the answer
    is the median of a third of an octave -- one bin below the line is
    the spread between sweeps.

    AND ONLY WHERE THE READING IS WORTH MORE THAN ITS OWN NOISE. What
    that means was got wrong for a long time. The rule was a signal to
    noise ratio of ten decibels, and on a coupler it hid bins that are
    accurate to a tenth: measured over four channels of two rigs, the
    error of a rung-to-rung difference is 0.03 dB with a spread of 0.1
    and does not grow at all down to an SNR of minus fifteen. A
    swept-sine deconvolution spreads uncorrelated noise and
    concentrates the sweep, so a stationary hiss never reaches the
    answer, and a number measured in the silence between sweeps says
    nothing about it.

    A ROOM IS NOT STATIONARY. On a UMIK-2 walk three sweeps at one
    level disagreed by 20.5 dB where the SNR was below zero, while the
    same three on a coupler never left half a decibel. Rumble and
    traffic are not rejected, and a log sweep dwells longest where
    they live.

    So the gate is the EXPECTED DISAGREEMENT of this walk's own
    sweeps, when the caller can supply it -- a bin is worth asking
    about while two sweeps of it would land closer together than the
    shortfall being looked for. That is stricter than ten decibels in
    a room and wide open on a coupler, which is the whole point: one
    rule, measured each time, instead of a constant that can only suit
    one of them.

    THERE IS NO SECOND RULE. A map walked before the scatter was
    recorded everywhere cannot be read by this one, and keeping the
    old ratio alive for it would divide profiles into ones where the
    level is named and the fader is held, and ones where none of that
    works -- with a branch here to serve the worse half. His decision,
    and the same one that decided the two epochs before it. Such maps
    are re-walked; three minutes a channel, and a coupler disturbs
    nobody.
    """
    prev = np.asarray(prev_mag, float)
    cur = np.asarray(cur_mag, float)
    got = cur - prev
    w = max(3, int(round(ppo / 3.0)))
    sm = running_median(got, w, need=max(2, w // 3))
    keep = np.asarray(scatter, float) < ANSWER_SHORT * asked_db
    ok = np.isfinite(sm) & keep
    return ok & (sm < ANSWER_SHORT * asked_db), ok


def shortfall_db(prev_mag, cur_mag, heard, asked_db, freqs, ppo,
                 scatter):
    """HOW SHORT a rung came, per frequency, in decibels.

    `shortfall` answers yes or no, and that is enough to find a
    ceiling but not to say how far past it you are. This is the same
    reading kept as a quantity: zero where the rig took the whole
    step, `asked_db` where it took none of it. The scale needs no
    threshold because both ends are given -- we chose the step, and
    the rig either delivered it or did not.

    On his iLoud it separates degrees the way he asked for: at 43% of
    the knob 63 Hz falls 1.1 dB short of a 2 dB step, at 50% it is
    1.9 -- almost nothing left -- and by 86% the deficit has walked
    DOWN to 50 Hz and a fresh one appears at a kilohertz, where the
    amplifier rather than the port gives out.

    NaN where the reading is not worth its own noise, judged the same
    way shortfall() judges it: a rig that makes no sound at a
    frequency is not short there, it is absent.
    """
    prev = np.asarray(prev_mag, float)
    cur = np.asarray(cur_mag, float)
    got = cur - prev
    w = max(3, int(round(ppo / 3.0)))
    sm = running_median(got, w, need=max(2, w // 3))
    ok = np.isfinite(sm) & (np.asarray(scatter, float)
                            < ANSWER_SHORT * asked_db)
    return np.where(ok, np.maximum(0.0, asked_db - sm), np.nan)


MAP_TOP_STEP_DB = 1.0    # the LAST rung may be shorter than the
                         # nominal fine step. Refusing it because the
                         # room left was 1.93 dB rather than 2.00 threw
                         # away the most valuable rung in the map --
                         # the walk's own reasoning is that the rungs
                         # above the border matter most -- and made the
                         # ladder's LENGTH turn on hundredths: one walk
                         # cleared the old threshold by 0.01 dB and
                         # took seven rungs, the next missed by 0.07
                         # and took six, same rig and same ceiling.
                         # What a step must be is READABLE, and 1.9 dB
                         # reads as easily as 2.0 against a walk whose
                         # own scatter is hundredths.


class _Rebased:
    """An analysis with its magnitude replaced.

    The base rung's curve is built from the pair of sweeps that
    measured its scatter; everything else the analysis carries --
    noise, harmonics, the SNR -- belongs to the sweep that produced
    it and is passed through untouched.
    """

    __slots__ = ("_it", "mag_db")

    def __init__(self, it, mag_db):
        object.__setattr__(self, "_it", it)
        object.__setattr__(self, "mag_db", mag_db)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_it"), name)


MAP_TOP_STEP_DB = 1.0    # the LAST rung may be shorter than the
                         # nominal fine step. Refusing it because the
                         # room left was 1.93 dB rather than 2.00 threw
                         # away the most valuable rung in the map --
                         # the walk's own reasoning is that the rungs
                         # above the border matter most -- and made the
                         # ladder's LENGTH turn on hundredths: one walk
                         # cleared the old threshold by 0.01 dB and
                         # took seven rungs, the next missed by 0.07
                         # and took six, same rig and same ceiling.
                         # What a step must be is READABLE, and 1.9 dB
                         # reads as easily as 2.0 against a walk whose
                         # own scatter is hundredths.

BASE_EVENT_K = 6.0       # times the LOCAL disagreement of the pair,
                         # and a bin is an event rather than noise
BASE_EVENT_FLOOR_DB = 0.1   # but never below this, so a stretch that
                         # agrees perfectly does not make every
                         # hundredth of a decibel a hole


def blend_base(a, b):
    """Two sweeps of the base rung, made into one curve.

    Where they agree, the average: the base's own noise falls by root
    two, and it is the one curve every other rung is read against, so
    that is worth having for free.

    WHERE THEY DISAGREE BY MORE THAN THEIR OWN SCALE, NEITHER. An
    earlier cut of this took the quieter, on the reasoning that an
    event adds energy and never takes it away -- true of a click, a
    bark, a chair, and false of a dropout. His FR base carried one:
    ten bins around 1.37 kHz, down to -3.36 dB, present in one sweep
    of the pair and not the other, and the rule picked the sweep that
    had it. Twenty-nine bins of a hundred and five, all of them the
    damaged one.

    Neither is what the data supports. Two sweeps of one rung that
    disagree by decibels in a bin do not agree on what the rig did
    there, and the honest answer is that the bin has no reference --
    which is the map's own doctrine already: a large scatter is the
    answer, not a trap. A blank propagates by itself, since every
    rung is read against this one.

    The whole-band rule above keeps its direction, and should: a
    bypass that did not land takes sound away across the band, so
    there the louder wins. That is a known direction over a known
    span; this is one bin with neither.

    ONE OWNER, because a base gets a second sweep in two different
    places: a fresh walk plays the pair for the scatter, and a rebuild
    plays the seating check at exactly the base's level. Both hand it
    here.
    """
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    apart = np.abs(a - b)
    # THE SCALE COMES FROM THE NEIGHBOURHOOD, not from the whole band
    # and not from a constant. An event is a bin where the two sweeps
    # disagree many times more than they usually disagree AT THAT
    # FREQUENCY -- which is the lesson the mask already learned in the
    # other direction, and it has to be learned here too.
    #
    # A single median over the whole band cannot serve both sweeps
    # this is handed. A fresh pair is seconds apart and agrees to
    # hundredths everywhere, so the constant governed and was about
    # right. The seating sweep is HOURS from the base it is compared
    # with, and a seating that moved half a decibel in the treble is
    # ordinary -- his own two channels showed +0.47 and +0.73 up
    # there. The bass and the mid still agree, so the whole-band
    # median stays near zero, the constant takes over, and the entire
    # top of the band is called a dropout and blanked. That is the
    # blindness he reported at 8 to 9 kHz.
    #
    # Locally the same drift raises its own scale and passes, while a
    # ten-bin hole inside a quiet stretch stands out as sharply as
    # ever: on his FR pair this keeps the floor of the dropout and
    # drops the fringe around it, thirty bins down to three.
    scale = _local_scale(apart)
    wide = apart > np.maximum(BASE_EVENT_FLOOR_DB, BASE_EVENT_K * scale)
    return np.where(wide, np.nan, 0.5 * (a + b))


def _local_scale(apart, w=33):
    """The median disagreement around each bin, over a third of an
    octave of the 1/96 grid."""
    n = len(apart)
    out = np.empty(n, float)
    for i in range(n):
        win = apart[max(0, i - w // 2):i + w // 2 + 1]
        win = win[np.isfinite(win)]
        out[i] = np.median(win) if win.size else 0.0
    return out


class Probe:
    """One sweep of the search: its record and what the controller
    was shown and made of it, while the search is running."""

    __slots__ = ("volume", "peak_dbfs", "snr_db", "thd_pct", "thd_bound",
                 "margin_db", "clipped", "phase", "step", "verdict",
                 "mag_db", "heard_offset_db", "floor_db")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _play_rung(back, name, sink, source, wav, duration, channels,
               sweep, freqs, analyze, v, play_map):
    """One sweep at volume `v`: claim, play, release, analyse.

    Shared by the search and the headroom map so a rung means the
    same thing in both.
    """
    back.moratorium_begin(name, v, mute_others=True)
    try:
        data, _info = run_take(sink, source, wav, duration, channels,
                               sweep.fs, verify=False,
                               channel_map=play_map)
    finally:
        back.moratorium_end()
    chan = np.asarray(data)[:, min(analyze, data.shape[1] - 1)]
    peak = float(np.max(np.abs(chan))) if chan.size else 0.0
    peak_db = 20.0 * math.log10(peak) if peak > 0 else -120.0
    return chan, peak_db, peak >= 0.999, mc.analyze_take(chan, sweep,
                                                         freqs)


def hunt(sink, source, channels, sink_name=None, analyze=0,
         sweep=None, freqs=None,
         pre_silence=mc.DEFAULT_PRE_SILENCE,
         post_silence=mc.DEFAULT_POST_SILENCE, play_map=None,
         on_probe=None, on_level=None, should_stop=None,
         start=AUTO_START_VOLUME, max_adjust=AUTO_MAX_ADJUST,
         replay=None):
    """Sweep at rising levels until the rig can see under the device.

    Returns (volume, probes). The volume is the walk's whole product;
    it is NOT left on the hardware, because the moratorium takes the
    measurement volume as a parameter and whoever measures next passes
    this number to their own claim. A lock is not a finding, and
    neither is a borrowed volume.

    `replay` RESUMES A SEARCH FROM ITS RECORD. The controller is a
    pure function of what it has been shown -- a peak, an SNR, a
    clipping flag, a THD bound and a margin per probe -- so the
    probes of a recorded search, shown to a fresh controller in
    order, put it exactly where it stood after them, and the search
    goes on from the step after the last one replayed, with the
    speakers hearing nothing of the replay. A choice on the passport
    canvas of "from step 3" keeps steps 1 and 2 this way. The probes
    returned are the NEW ones only; the caller keeps what it replayed.

    `on_probe(probe)` is called after each sweep and `should_stop()` is
    asked before each, so a caller can narrate and interrupt.

    `on_level(volume, step)` is called BEFORE each sweep, and this one
    is about safety rather than narration. Announcing the level after
    the sweep tells a person what already played into their ears: his
    near miss was a walk that reached 80% with the wrong earphone in
    the coupler, and the only reason the next rung -- full volume --
    did not play is that he read the line and hit stop in time. A
    level is worth knowing while it can still be refused.
    """
    sweep = sweep or mc.default_sweep()
    freqs = mc.log_grid() if freqs is None else freqs
    name = sink_name or (sink.get("name") if isinstance(sink, dict)
                         else sink)
    if not name:
        raise ValueError("the moratorium needs the sink's node name")
    ctl = AutoLevel()
    v = start_volume(start)
    first = 1
    for r in replay or []:
        v = float(r["level"])
        ctl.observe(v, float(r["peak_dbfs"]), r.get("snr_db"),
                    bool(r.get("clipped")), r.get("thd_bound"),
                    r.get("margin_db"))
        first += 1
    if replay:
        # the record may already hold the answer; then nothing plays
        if ctl.settled():
            return _clamp(ctl.ok[0]), []
        nv = ctl.next_volume(v)
        if abs(nv - v) < 1e-3:            # nowhere left to go
            return _clamp(ctl.ok[0] if ctl.ok is not None else v), []
        v = nv
    outdir = tempfile.mkdtemp(prefix="pdeq-level-")
    wav = write_sweep_files(outdir, sweep, pre_silence, post_silence)
    duration = pre_silence + sweep.duration_s + post_silence
    probes = []
    back = pw_backend.backend()
    # A SEARCH THAT WAS INTERRUPTED IS NOT A SEARCH, and the caller
    # has to be able to tell. His case, and it is the ordinary one:
    # a level was settled on days ago, a new search is started by
    # accident, he catches it and stops -- and the half-finished walk
    # then overwrites the good number with wherever it happened to be
    # standing. Answering None lets a caller keep what it had.
    interrupted = False

    try:
        for step in range(first, int(max_adjust) + 1):
            if should_stop is not None and should_stop():
                interrupted = True
                break
            # BEFORE THE SOUND, not after it
            if on_level is not None:
                on_level(v, step)
            # ONE CLAIM PER SWEEP, as the take does: the level moves
            # between rungs, and the moratorium is where a measurement
            # volume is set
            # THE NAME, NOT THE OBJECT: run_take wants the resolved
            # node and the moratorium wants the node's name, and the
            # two are not the same thing in this codebase
            chan, peak_db, clipped, got = _play_rung(
                back, name, sink, source, wav, duration, channels,
                sweep, freqs, analyze, v, play_map)
            pct = bound = margin = None
            if not clipped:
                at = mb.thd_at(freqs, got.thd_db, got.thd_noise_db)
                if at:
                    pct, bound = at
                margin = mb.thd_margin_db(freqs, got.thd_db,
                                          got.thd_noise_db)
            snr = (float(got.snr_db)
                   if got.snr_db is not None
                   and math.isfinite(float(got.snr_db)) else None)

            verdict = ctl.observe(v, peak_db, snr, clipped, bound, margin)
            # THE CURVE RIDES WITH THE PROBE. It was analysed in full
            # and thrown away, and a search whose sweeps leave no
            # record is a black box: a level was found and nothing
            # could say what the sweeps that found it looked like.
            rec = sweep_record(v, peak_db, got)
            p = Probe(volume=v, peak_dbfs=peak_db, snr_db=snr,
                      thd_pct=pct, thd_bound=bound, margin_db=margin,
                      clipped=clipped, phase=ctl.phase(), step=step,
                      verdict=verdict, mag_db=rec["mag_db"],
                      heard_offset_db=rec["heard_offset_db"],
                      floor_db=rec["floor_db"])
            probes.append(p)
            if on_probe is not None:
                on_probe(p)

            # A CAPTURE THAT HEARS NOTHING ENDS THE WALK. Creeping is
            # safe but gets nowhere, and a search that creeps to the
            # top of the knob has spent a minute of sweeps to learn
            # what the second probe already showed. Two silent probes
            # in a row mean the microphone is not in this path --
            # wrong card, wrong column, or the coupler is somewhere
            # else -- and no volume will fix that.
            silent = [q for q in probes[-2:]
                      if q.peak_dbfs <= SILENT_CAPTURE_DBFS]
            if len(silent) == 2:
                raise RuntimeError(
                    "the capture heard nothing at %.0f%% and %.0f%%: "
                    "the microphone is not in this path (wrong card, "
                    "wrong column, or the coupler is elsewhere)"
                    % (100 * silent[0].volume, 100 * silent[1].volume))

            if ctl.settled():
                return _clamp(ctl.ok[0]), probes
            nv = ctl.next_volume(v)
            if abs(nv - v) < 1e-3:            # nowhere left to go
                break
            v = nv
    finally:
        for name in os.listdir(outdir):
            try:
                os.unlink(os.path.join(outdir, name))
            except OSError:
                pass
        try:
            os.rmdir(outdir)
        except OSError:
            pass

    if interrupted:
        return None, probes
    # nothing satisfied every question: hand back the best seen rather
    # than nothing, and let the caller say so
    if ctl.ok is not None:
        return _clamp(ctl.ok[0]), probes
    return _clamp(v), probes
