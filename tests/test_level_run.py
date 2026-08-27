"""The level search: its policy, and the field runs that shaped it."""

import math

import pytest

from perdeviceeq import level_run
from perdeviceeq import measure_session as ms          # noqa: F401


# --- the auto-level's third question ---------------------------------

def test_hot_and_clean_is_not_enough_while_the_figure_is_a_bound():
    """Peak and SNR say the capture is usable. They cannot say whether
    the distortion figure is a MEASUREMENT -- on his rig the midband
    stayed a bound until a recorded peak near -16 dBFS, well inside
    the old window, so the hunt stopped early and every THD number
    afterwards was a ceiling."""
    hot_and_clean = dict(peak=-8.0, snr=55.0)
    assert level_run.AutoLevel.verdict(thd_bound=False, **hot_and_clean) == "ok"
    assert level_run.AutoLevel.verdict(thd_bound=True, **hot_and_clean) == "quiet"
    assert level_run.AutoLevel.verdict(thd_bound=None, **hot_and_clean) == "ok"


def test_at_the_ceiling_a_bound_is_accepted_rather_than_chased():
    """There is nowhere left to climb, and a take that says its figure
    is a bound beats no take at all."""
    at_ceiling = level_run.AUTO_PEAK_CEIL - 0.5
    assert level_run.AutoLevel.verdict(at_ceiling, 55.0, thd_bound=True) == "ok"


def test_past_the_ceiling_is_loud_whatever_the_figure_says():
    assert level_run.AutoLevel.verdict(level_run.AUTO_PEAK_CEIL + 1.0, 55.0,
                                thd_bound=False) == "loud"


def test_a_quiet_probe_stays_quiet():
    assert level_run.AutoLevel.verdict(-40.0, 20.0, thd_bound=False) == "quiet"


def _walk(judge, start=0.15, steps=24):
    """Drive a whole hunt over a synthetic chain. `judge(v)` returns
    the (peak, snr, bound) that chain would give at volume v. Returns
    (settled level, every volume tried)."""
    a = level_run.AutoLevel()
    v, seen = start, []
    for _ in range(steps):
        pk, snr, bound = judge(v)
        a.observe(v, pk, snr, False, bound)
        seen.append(v)
        if a.settled():
            break
        nv = a.next_volume(v)
        if abs(nv - v) < 1e-4:
            break
        v = nv
    return (a.ok[0] if a.ok else None), seen


def test_the_hunt_does_not_overshoot_the_peak_floor():
    """WHAT THAT COURT ALWAYS GUARDED, said as the harm rather than as
    the mechanism.

    Its first form asserted that the crossing outranks the peak floor,
    because on his Liberty 5 Pro through the coupler the crossing sits
    at a peak of -13.6 dBFS, BELOW the floor, and a cut that asked the
    floor first rejected that level and sailed to full volume. He
    caught it from a screenshot.

    But the runaway was the RAMP: doubling the cubic is eighteen
    decibels a step, so one rejection threw the hunt across everything
    above it. The creep cured that, and his ladders since put only
    4.6 dB between the crossing and the floor on that same chain, with
    the measured figure not moving across them at all. So the rule
    worth keeping is not "the floor never binds" -- it is that the
    hunt lands NEAR the floor rather than beyond it.

    His own ladder, as measured, with the rungs between filled in.
    """
    def liberty(v):
        # peak follows the level one for one; the crossing at -13.6
        pk = -13.6 + 60.0 * math.log10(max(v, 1e-6) / 0.30)
        return pk, 50.0, pk < -13.6

    got, seen = _walk(liberty)
    assert got is not None, seen
    peak = -13.6 + 60.0 * math.log10(got / 0.30)
    # Near the aim, not beyond it, and nowhere near full volume. The
    # tolerance is ONE CREEP STEP, because that is the search's whole
    # resolution: the step is a ratio on the CUBIC volume, and 1.12
    # there is 60*log10(1.12) = 2.95 dB, not the decibel it reads as.
    # What this court forbids is a runaway, not the granularity.
    step_db = 60.0 * math.log10(level_run.AUTO_RAMP_NEAR)
    aim = level_run.AUTO_PEAK_FLOOR + level_run.AUTO_PEAK_BAND
    assert peak <= aim + step_db + 0.2, (peak, step_db, seen)
    assert max(seen) < 0.9, seen


def test_without_a_judgement_the_peak_floor_still_rules():
    """Nothing is said about the figure on a clipped probe, or before
    an analysis exists. The old behaviour has to survive there."""
    assert level_run.AutoLevel.verdict(-13.6, 50.0, False, None) == "quiet"
    assert level_run.AutoLevel.verdict(-7.5, 56.2, False, None) == "ok"


def test_a_dirty_probe_is_quiet_whatever_the_figure_says():
    """SNR is the lower guard and it comes first: a figure that looks
    like a measurement under a noisy capture is not one."""
    assert level_run.AutoLevel.verdict(-13.6, 20.0, False, False) == "quiet"


def test_the_hunt_closes_on_the_lowest_ok_not_the_first():
    """The ramp doubles, so it steps straight over the crossing: 4, 8,
    16, 32, 64 walks past a crossing at 79 and lands on full volume --
    the old behaviour wearing new clothes, which is what he saw in a
    screenshot. An ok level is a CEILING to close on."""
    def judge(v):
        # A PHYSICAL CHAIN: the peak follows the level. It used to
        # stand still at -25 and -13 whatever the volume, which is the
        # very signature a whole afternoon was spent identifying as a
        # broken rig -- and it put both "ok" rungs under the peak
        # floor. The rule under test is the CLOSING, not the peak.
        pk = (level_run.AUTO_PEAK_FLOOR + level_run.AUTO_PEAK_BAND
              + 1.0 + 60.0 * math.log10(max(v, 1e-6) / 0.80))
        return pk, (45.0 if v < 0.76 else 50.0), v < 0.76

    a = level_run.AutoLevel()
    v, seen = 0.04, []
    for _ in range(14):
        pk, snr, bound = judge(v)
        a.observe(v, pk, snr, False, bound)
        seen.append(round(v * 100))
        if a.settled():
            break
        v = a.next_volume(v)
    assert a.settled()
    assert 76 <= round(a.ok[0] * 100) <= 84, seen
    assert max(seen) >= 90 or a.ok[0] < 0.9   # it did come back down


def test_settling_needs_a_quiet_below_the_ok():
    """Ok on the very first probe: nothing to close on, take it."""
    a = level_run.AutoLevel()
    a.observe(0.5, -10.0, 50.0, False, False)
    assert a.settled() and a.ok[0] == 0.5


def test_a_wide_bracket_is_not_settled():
    a = level_run.AutoLevel()
    a.observe(0.10, -30.0, 45.0, False, True)
    a.observe(0.80, -12.0, 50.0, False, False)
    assert not a.settled()


def test_the_hunt_steers_by_the_analysed_snr_not_the_quick_one():
    """The quick estimate finds the sweep's onset as the first crossing
    of ten times the pre-roll RMS. That cannot work on a card whose
    pre-roll carries fixed spikes: his CM106 sits at -34 dBFS there, so
    the threshold lands at -14 and every probe quieter than that
    reports no onset at all.

    The field showed what that costs: SNR n/a on every ramp step, the
    hunt steering with no lower guard, ramping to the ceiling and
    declaring the rig hopeless -- on a take the analysis scored at
    50 dB. Two estimators, two verdicts about one recording.
    """
    import numpy as np
    fs = 48000
    chan = np.zeros(fs, dtype=np.float64)
    chan[::4096] = 10 ** (-34.0 / 20.0)          # the fixed spikes
    chan[fs // 2:] += 10 ** (-25.0 / 20.0)       # a sweep quieter than 10x
    ses = ms.MeasureSession.__new__(ms.MeasureSession)
    ses.sweep = type("S", (), {"fs": fs})()
    snr, noise = ms.MeasureSession._quick_snr(ses, chan)
    assert snr is None, "the quick estimate is expected to fail here"


def test_a_closed_bracket_ends_the_hunt_even_on_a_quiet_probe():
    """The stop must not require THIS probe to be the ok one. As the
    bracket narrows the probes land on the QUIET side, and demanding
    both left his hunt circling 69, 64, 67, 68, 69 until it ran out of
    steps and stopped short on a bound -- with the answer, 69, already
    in hand three sweeps earlier."""
    a = level_run.AutoLevel()

    def peak(v):                      # a physical chain, as above
        return (level_run.AUTO_PEAK_FLOOR + level_run.AUTO_PEAK_BAND
                + 0.5 + 60.0 * math.log10(max(v, 1e-6) / 0.69))

    for v, bound in ((0.15, True), (0.30, True), (0.60, True),
                     (0.80, False), (0.69, False), (0.64, True)):
        a.observe(v, peak(v), 45.0, False, bound)
    assert a.settled()
    assert a.ok[0] == pytest.approx(0.69)
    # and the probe that closed it was judged quiet, not ok
    assert a.verdict(peak(0.64), 45.0, False, True) == "quiet"


def test_a_take_warns_from_the_snr_it_reports():
    """One recording, one figure. The take prints the analysed SNR and
    used to warn from the quick estimate, so his run showed "SNR 46.1
    dB" over "WARNING: low SNR (2.9 dB)" about the same sweep. The
    quick one is the wrong of the two on a card whose pre-roll carries
    fixed spikes."""
    import inspect
    src = inspect.getsource(ms.MeasureSession._accept)
    head = src[:src.index("notes.append(\"WARNING: low SNR")]
    assert "analyze_take" in head, "the analysis must come first"
    assert "t.snr_db" in head, "the warning must read the analysed SNR"


def test_a_hunt_never_starts_from_zero():
    """A multiplicative ramp cannot move from zero: it doubles to zero,
    sees no change and stops after one sweep. A rig nobody has measured
    shows ZERO on the fader, so this is not a corner case. Zero is not
    quieter than the quiet start; it is nothing."""
    for asked in (None, 0.0, 0.5, 1.0):
        v = level_run.start_volume(asked)
        assert v == pytest.approx(level_run.AUTO_START_VOLUME)
        assert level_run.AutoLevel().next_volume(v) > v
    # something genuinely quieter is kept, because the ramp can move
    assert level_run.start_volume(0.05) == pytest.approx(0.05)


def test_the_ramp_stops_doubling_once_the_margin_says_it_is_close():
    """Doubling the cubic volume is EIGHTEEN decibels a step, which
    guarantees an overshoot at the crossing -- and then a coin flip in
    the bound test decides whether the hunt closes DOWN from eighty or
    UP from a hundred. Two consecutive ladders on one earphone chose 74
    and 89 that way, 4.8 dB apart, on readings of 0.25 and 0.23 per
    cent at the same level."""
    far = level_run.AutoLevel()
    far.observe(0.60, -22.0, 45.0, False, True, margin_db=-30.0)
    assert not far.near
    assert far.next_volume(0.60) == pytest.approx(0.80)   # ceiling

    close = level_run.AutoLevel()
    close.observe(0.60, -22.0, 45.0, False, True, margin_db=-1.0)
    assert close.near
    assert close.next_volume(0.60) == pytest.approx(0.672, abs=1e-3)


def test_the_margin_and_the_mark_cannot_disagree():
    """Same band, same median, one bar between them."""
    import numpy as np
    from perdeviceeq import measure_build as mb
    f = np.array([980.0, 1000.0, 1020.0])
    for gap in (0.5, 2.9, 3.1, 12.0):
        thd = np.full(3, -60.0)
        noise = np.full(3, -60.0 - gap)
        margin = mb.thd_margin_db(f, thd, noise)
        bound = mb.thd_is_bound(f, thd, noise)
        assert margin == pytest.approx(gap)
        assert bound == (margin < 3.0)


# --- the policy's own courts, moved here with the policy ---------------
# These build AutoLevel directly, so they belong beside it rather than
# beside the command line that used to own it.



def test_autolevel_steps_up_but_never_blasts_when_quiet():
    ac = level_run.AutoLevel()
    ac.observe(0.15, -45.0, None, False)
    nv = ac.next_volume(0.15)
    assert nv > 0.15                                 # move toward the target
    assert nv <= 0.15 * level_run.AUTO_RAMP                 # bounded ramp per step
    assert nv <= level_run.AUTO_EXPLORE_CEIL                # no full-volume probe

def test_autolevel_brackets_and_stays_below_the_loud_side():
    ac = level_run.AutoLevel()
    ac.observe(0.2, -20.0, 25.0, False)              # too quiet (low SNR)
    ac.observe(0.8, 0.0, None, True)                 # clipped -> loud
    nv = ac.next_volume(0.8)
    assert 0.2 < nv < 0.8                            # interpolated inside
    assert nv <= 0.8 * level_run.AUTO_CLIP_BACKOFF          # kept below the clip

def test_autolevel_never_returns_to_a_clipping_level():
    ac = level_run.AutoLevel()
    ac.observe(0.3, -30.0, 30.0, False)
    ac.observe(1.0, 0.5, None, True)
    assert ac.next_volume(1.0) <= 1.0 * level_run.AUTO_CLIP_BACKOFF

def test_autolevel_ceiling_lifts_when_stuck_below_window():
    # a probe sitting at the explore ceiling but still below the window
    # means the device needs more: the ceiling must lift past its start
    ac = level_run.AutoLevel()
    ac.observe(level_run.AUTO_EXPLORE_CEIL, -20.0, 25.0,
               False)                                # at ceiling, quiet
    nv = ac.next_volume(level_run.AUTO_EXPLORE_CEIL)
    assert nv > level_run.AUTO_EXPLORE_CEIL                 # allowed to go higher now

def test_autolevel_bisects_between_brackets():
    # once bracketed, the next probe is the geometric midpoint of the two
    # -- no slope/law assumption, so it converges on a steep BT law where
    # a slope estimate overshoots
    ac = level_run.AutoLevel()
    ac.observe(0.30, -20.0, 25.0, False)              # too quiet
    ac.observe(0.90, -1.0, 60.0, False)               # past the ceiling
    nv = ac.next_volume(0.90)
    assert nv == pytest.approx((0.30 * 0.90) ** 0.5, abs=1e-6)


# --- the glitch probe imports and parses (hardware tool, smoke only) --------


def test_the_climb_is_aimed_and_never_predicts_a_clip():
    """His field run, the first after the floor gained its band: probe
    at 15% came back at a peak of -16.4 dBFS, the ramp doubled to 30%,
    and the sweep hit 0.0 dBFS -- clipped into an earphone on a card
    that can destroy one. The ramp doubles the CUBIC, eighteen
    decibels a step, and it had only ever been safe because the search
    settled before taking a second one.

    The peak follows the level one for one, so the step is chosen."""
    a = level_run.AutoLevel()
    a.observe(0.15, -16.4, 46.1, False, False, margin_db=4.0)
    nv = a.next_volume(0.15)
    predicted = -16.4 + 60.0 * math.log10(nv / 0.15)
    assert predicted <= level_run.AUTO_PEAK_CEIL, (nv, predicted)
    # it crosses the aim rather than landing on it, by half a decibel:
    # a step that lands exactly there leaves the probe a rounding
    # error below and the search asks for the same level forever
    aim = level_run.AUTO_PEAK_FLOOR + level_run.AUTO_PEAK_BAND
    assert aim < predicted <= aim + 1.0, (nv, predicted)


def test_a_climb_with_nothing_measured_yet_still_ramps():
    """Before any quiet probe is recorded there is no peak to aim by,
    and the old ramp is what remains."""
    a = level_run.AutoLevel()
    assert a.next_volume(0.15) == pytest.approx(0.15 * level_run.AUTO_RAMP)
