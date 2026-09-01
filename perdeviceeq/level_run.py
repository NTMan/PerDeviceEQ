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
# HOW BOLDLY THE MAP STRIDES while the rig keeps answering, and it is
# not the same as the finest step it can read. Below the border every
# rung reads alike -- his Tanchjim gave eight identical rows of zeros
# over fourteen decibels -- so sweeps spent there buy nothing. Eight
# decibels covers ground quickly and still halves twice to land inside
# the two decibels two sweeps can be told apart by.
MAP_STRIDE_DB = 8.0
MAP_STEP_DB = MAP_STRIDE_DB     # the walk's opening step
MAP_MAX_RUNGS = 8


def headroom_map(sink, source, channels, start_volume, sink_name=None,
                 analyze=0, sweep=None, freqs=None,
                 pre_silence=None, post_silence=None, play_map=None,
                 on_level=None, should_stop=None,
                 stop_peak_dbfs=AUTO_PEAK_CEIL, step_db=MAP_STEP_DB,
                 max_rungs=MAP_MAX_RUNGS):
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
    ppo = getattr(mc, "GRID_PPO", 96)

    rungs = []
    exact = []              # (level, peak) unrounded, for the decisions
    v = _clamp(start_volume)
    step = float(step_db)
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
    hi_lv = None            # the quietest level known to fall short
    try:
        for i in range(int(max_rungs)):
            if should_stop is not None and should_stop():
                stopped = "asked"
                break
            if on_level is not None:
                on_level(v, i + 1)
            chan, peak_db, clipped, got = _play_rung(
                back, name, sink, source, wav, duration, channels,
                sweep, freqs, analyze, v, play_map)
            mag = np.asarray(got.mag_db, float)
            # the response is relative and the noise absolute, so one
            # offset turns the whole curve into a margin over this
            # take's own floor -- a number rather than a second curve
            off = (float(got.noise_dbfs) - float(got.signal_dbfs)
                   if got.noise_dbfs is not None
                   and got.signal_dbfs is not None else None)
            # ROUNDING IS FOR STORAGE, NOT FOR ARITHMETIC. The walk
            # keeps exact levels and peaks for its own decisions: with
            # a 2 dB step and a peak rounded to two places, the step
            # between two rungs comes out 1.99 and the comparison that
            # asks for MIN_READABLE_STEP throws it away -- so no
            # bracket ever forms and the walk marches to the top.
            exact.append((float(v), float(peak_db)))
            rungs.append({"level": round(float(v), 4),
                          "peak_dbfs": round(peak_db, 2),
                          "spl_db": None,
                          "heard_offset_db": (None if off is None
                                              else round(off, 2)),
                          "mag_db": [None if not math.isfinite(x)
                                     else round(float(x), 2)
                                     for x in mag]})
            if clipped:
                stopped = "capture"
                break
            # WHERE THE NEXT RUNG GOES. Below the border everything
            # answers in full and every rung reads the same: his
            # Tanchjim gave eight identical rows of zeros over
            # fourteen decibels. Spending sweeps there buys nothing,
            # so the walk STRIDES while the answer keeps coming and
            # HALVES once it stops -- the border is what changes, and
            # that is where the sweeps should go.
            #
            # It strides by prediction, never by hope. The peak
            # follows the level one for one, so the walk knows before
            # it plays where a step would land, and it will not put a
            # rung past the capture ceiling. And it strides only from
            # a rung that came back HEALTHY: a bold jump from a rung
            # that was already short would land somewhere nobody
            # asked for, which on an earphone is not a three-second
            # inconvenience.
            short = False
            below = None
            for k in range(len(rungs) - 1):
                if exact[k][0] < v and (below is None
                                        or exact[k][0] > exact[below][0]):
                    below = k
            if below is not None and off is not None:
                asked = asked_db(exact[below], (v, peak_db))
                if asked >= MIN_READABLE_STEP:
                    prev = np.array([np.nan if x is None else x
                                     for x in rungs[below]["mag_db"]],
                                    float)
                    sh, _ok = shortfall(prev, mag, mag - off, asked,
                                        freqs, ppo)
                    short = bool(sh.any())
            if short:
                # the border is between this rung and the one below:
                # close on it rather than climbing away from it
                lo_lv = exact[below][0] if below is not None else None
                if lo_lv is None:
                    stopped = "rungs"
                    break
                hi_lv = v if hi_lv is None else min(hi_lv, v)
                span = 60.0 * math.log10(hi_lv / lo_lv)
                if span < 2.0 * MIN_READABLE_STEP:
                    stopped = "border"
                    break
                step = span / 2.0
                v = lo_lv
            else:
                step = float(step_db)
                # AND A KNOWN-SHORT LEVEL IS A CEILING. Without this
                # the walk halves down to a healthy rung, then strides
                # boldly again and sails straight over the rung it had
                # just found short: 50, 68 (short), 58, and then 79 --
                # bracketing a border it had already passed. Once a
                # level is known to fall short, nothing above it is
                # worth a sweep.
                if hi_lv is not None:
                    room = 60.0 * math.log10(hi_lv / v)
                    if room < 2.0 * MIN_READABLE_STEP:
                        stopped = "border"
                        break
                    step = min(step, room / 2.0)
            # the peak follows the level one for one, so the walk
            # knows before it plays where a step would land
            # the peak follows the level one for one, so the walk
            # knows before it plays where a step would land -- and it
            # asks the rung it is stepping FROM, which after a halving
            # is not the rung played last
            from_peak = peak_db
            for lv, pk in exact:
                if abs(lv - v) < 1e-9:
                    from_peak = pk
            take = min(step, stop_peak_dbfs - from_peak)
            if take < MIN_READABLE_STEP:
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
HEARD_OVER_NOISE_DB = 10.0  # a rung speaks only where it was heard
MIN_READABLE_STEP = 2.0
#                             measured: the scatter between sweeps is
#                             two tenths of a decibel, so a 2 dB step
#                             is read with room and a 1 dB step is not


def asked_db(prev, cur):
    """How much louder a rung ACTUALLY got, in decibels.

    The knob's own ratio is only an intention. Over Bluetooth it is
    not even that: his JBL Tour Pro 3 answers AVRCP's 128-step scale,
    and a rung the walk asked 4.0 dB of arrived 8.0 and 11.0 dB
    louder. Everything downstream compares what came back against
    what was asked, so a fictitious ask makes a fictitious verdict --
    where twice the step arrived and one step was taken, the band is
    a step short and the walk called it "answered in full".

    The capture peak follows the level one for one on every wired rig
    in this project, which is what makes it the honest witness: it
    reports what the rig was actually given, whoever set the volume
    and by whatever scale. Fall back to the knob only when a peak is
    missing.

    `prev` and `cur` are (level, peak_dbfs) pairs.
    """
    lv0, pk0 = prev
    lv1, pk1 = cur
    if pk0 is not None and pk1 is not None:
        return float(pk1) - float(pk0)
    return 60.0 * math.log10(float(lv1) / float(lv0))


def shortfall(prev_mag, cur_mag, heard, asked_db, freqs, ppo):
    """Where a rung bought less than half of what was asked.

    A rig runs out over a REGION, not at one frequency, so the answer
    is the median of a third of an octave -- one bin below the line is
    the spread between sweeps. And the question is only worth asking
    where the rung was HEARD: a response has to stand clear of that
    take's own noise, or the difference of two rungs is the difference
    of two noises. His Adam D3V makes no sound at 25 Hz, and without
    that gate the reading there claimed headroom where there is no
    sound at all.
    """
    prev = np.asarray(prev_mag, float)
    cur = np.asarray(cur_mag, float)
    got = cur - prev
    w = max(3, int(round(ppo / 3.0)))
    sm = np.full(len(got), np.nan)
    for k in range(len(got)):
        seg = got[max(0, k - w // 2):k + w // 2 + 1]
        seg = seg[np.isfinite(seg)]
        if seg.size >= max(2, w // 3):
            sm[k] = float(np.median(seg))
    ok = np.isfinite(sm) & (np.asarray(heard, float) > HEARD_OVER_NOISE_DB)
    return ok & (sm < ANSWER_SHORT * asked_db), ok


def shortfall_db(prev_mag, cur_mag, heard, asked_db, freqs, ppo):
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

    NaN where the rung was not heard: a rig that makes no sound at a
    frequency is not short there, it is absent.
    """
    prev = np.asarray(prev_mag, float)
    cur = np.asarray(cur_mag, float)
    got = cur - prev
    w = max(3, int(round(ppo / 3.0)))
    sm = np.full(len(got), np.nan)
    for k in range(len(got)):
        seg = got[max(0, k - w // 2):k + w // 2 + 1]
        seg = seg[np.isfinite(seg)]
        if seg.size >= max(2, w // 3):
            sm[k] = float(np.median(seg))
    ok = np.isfinite(sm) & (np.asarray(heard, float) > HEARD_OVER_NOISE_DB)
    return np.where(ok, np.maximum(0.0, asked_db - sm), np.nan)


def spans(mask, freqs):
    """Contiguous frequency spans of a boolean mask, as [lo, hi] pairs.

    LISTED rather than summarised as "below N Hz": a rig can run out
    in the bass and again in the middle -- his iLoud does both, the
    port near 60 Hz and the amplifier above 850 at full level -- and
    one bound would swallow everything between.
    """
    f = np.asarray(freqs, float)
    out, start = [], None
    for k, on in enumerate(mask):
        if on and start is None:
            start = k
        elif not on and start is not None:
            out.append([round(float(f[start]), 1),
                        round(float(f[k - 1]), 1)])
            start = None
    if start is not None:
        out.append([round(float(f[start]), 1), round(float(f[-1]), 1)])
    return out


class Probe:
    """One rung: what a sweep at one level said."""

    __slots__ = ("volume", "peak_dbfs", "snr_db", "thd_pct", "thd_bound",
                 "margin_db", "clipped", "phase", "step")

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
         start=AUTO_START_VOLUME, max_adjust=AUTO_MAX_ADJUST):
    """Sweep at rising levels until the rig can see under the device.

    Returns (volume, probes). The volume is the walk's whole product;
    it is NOT left on the hardware, because the moratorium takes the
    measurement volume as a parameter and whoever measures next passes
    this number to their own claim. A lock is not a finding, and
    neither is a borrowed volume.

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
    outdir = tempfile.mkdtemp(prefix="pdeq-level-")
    wav = write_sweep_files(outdir, sweep, pre_silence, post_silence)
    duration = pre_silence + sweep.duration_s + post_silence
    name = sink_name or (sink.get("name") if isinstance(sink, dict)
                         else sink)
    if not name:
        raise ValueError("the moratorium needs the sink's node name")
    ctl = AutoLevel()
    v = start_volume(start)
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
        for step in range(1, int(max_adjust) + 1):
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

            ctl.observe(v, peak_db, snr, clipped, bound, margin)
            p = Probe(volume=v, peak_dbfs=peak_db, snr_db=snr,
                      thd_pct=pct, thd_bound=bound, margin_db=margin,
                      clipped=clipped, phase=ctl.phase(), step=step)
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
