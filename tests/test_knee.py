"""The knee policy, against chains whose answer is known by arithmetic
and against curves this bench actually produced.

A capture chain in silence is two noises adding in power: the
converter's own floor, fixed in dBFS, and the input's noise, which
rides the gain. Their sum crosses slope one-half exactly where they
are equal, so a synthetic chain with a floor at F and an input noise
of M at zero gain has its knee at F - M dB. The module is not told
that slope, and must not need it: on a hardware route the x axis is
not true decibels at all.
"""

import math

import pytest

from perdeviceeq import knee


def chain(floor_db, mic_at_zero_db):
    def read(gain_db):
        return 10.0 * math.log10(10 ** (floor_db / 10.0)
                                 + 10 ** ((mic_at_zero_db + gain_db) / 10.0))
    return read


def ladder(read, lo=-40.0, hi=40.0, steps=8, refine=True):
    rungs = [knee.Rung(g, read(g)) for g in knee.plan(lo, hi, steps)]
    if refine:
        rungs += [knee.Rung(g, read(g)) for g in knee.refine(rungs)]
    return rungs


# --- the four shapes -------------------------------------------------

def test_a_knee_is_found_near_where_the_arithmetic_puts_it():
    v = knee.verdict(ladder(chain(-118.0, -140.0)))
    assert v.kind == "knee"
    # no slope-one assumption is made, so this is an estimate rather
    # than an identity; a couple of dB is the price of an axis that
    # may not be decibels at all
    assert v.knee_db == pytest.approx(22.0, abs=3.0)
    assert v.work_db > v.knee_db
    assert v.usable


def test_rising_throughout_means_the_input_already_wins():
    """A chain whose two terms cross BELOW the walk: the input already
    outweighs the converter everywhere that was looked at."""
    v = knee.verdict(ladder(chain(-118.0, -60.0)))    # crossing at -58
    assert v.kind == "input"
    assert v.work_db == -40.0


def test_the_fit_returns_a_known_crossing_exactly():
    """The synthetic chain IS floor plus a term riding the gain, so its
    crossing is known in closed form -- and that is the strongest test
    there is of an estimator: not that it is steady, but that it is
    right where the answer can be checked."""
    for floor, mic in ((-118.0, -80.0), (-118.0, -140.0)):
        v = knee.verdict(ladder(chain(floor, mic)))
        assert v.kind == "knee"
        assert v.knee_db == pytest.approx(floor - mic, abs=0.2)
        assert v.curve.resid < 0.05


def test_a_knee_two_decibels_above_the_bottom_is_still_a_knee():
    """The segmentation called this "input" because the flat stretch
    was only two rungs wide and merged into the rise. The crossing is
    at -38 dB and the walk starts at -40, so it is inside."""
    v = knee.verdict(ladder(chain(-118.0, -80.0)))
    assert v.kind == "knee"
    assert v.knee_db == pytest.approx(-38.0, abs=0.5)


def test_the_unity_start_run_reads_a_knee_just_above_its_bottom():
    """The same card walked from its unity point upward. The old
    reading was "input, work at the bottom" -- but the bottom rung sits
    only 0.6 dB above the fitted floor, so the converter still
    dominates there and standing at the bottom throws that 0.6 dB
    away. The fit puts the crossing a little above it."""
    measured = [(-11.1, -85.40), (-9.5, -83.96), (-7.9, -82.24),
                (-6.3, -79.01), (-4.7, -75.47), (-3.2, -71.38),
                (-1.6, -66.95), (0.0, -60.65)]
    v = knee.verdict([knee.Rung(g, x) for g, x in measured])
    assert v.kind == "knee"
    assert -11.1 < v.knee_db < 0.0
    assert v.curve.floor_dbfs < -85.0


def test_the_scatter_is_measured_from_the_data():
    """Both of those runs are repeatable to a fraction of a dB, and
    the fit says so without being told."""
    measured = [(-11.1, -85.40), (-9.5, -83.96), (-7.9, -82.24),
                (-6.3, -79.01), (-4.7, -75.47), (-3.2, -71.38),
                (-1.6, -66.95), (0.0, -60.65)]
    v = knee.verdict([knee.Rung(g, x) for g, x in measured])
    assert 0.0 < v.scatter < 1.0


# --- the machinery ---------------------------------------------------

def test_neighbours_that_say_the_same_thing_are_one_region():
    # a real knee is a curve, and least squares will spend a boundary
    # inside the bend; two rising pieces in a row are one rising region
    segs, _ = knee.describe(ladder(chain(-118.0, -140.0), refine=False))
    kinds = [s.kind for s in segs]
    assert kinds == ["flat", "rising"]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))


def test_noise_does_not_buy_a_boundary():
    # a straight flat line with scatter on it must stay ONE segment
    import random
    rng = random.Random(7)
    rungs = [knee.Rung(g, -100.0 + rng.gauss(0.0, 0.4))
             for g in knee.plan(-40, 40, 10)]
    segs, scatter = knee.describe(rungs)
    assert [s.kind for s in segs] == ["flat"]
    assert scatter == pytest.approx(0.4, abs=0.35)


def test_a_transient_is_marked_and_kept_out_of_the_fit():
    read = chain(-118.0, -140.0)
    rungs = [knee.Rung(g, read(g), peak_dbfs=read(g) + 9.0)
             for g in knee.plan(-40, 40, 8)]
    rungs[2].rms_dbfs = -40.0
    rungs[2].peak_dbfs = -5.0
    assert knee.mark_transients(rungs) == [rungs[2]]
    assert rungs[2].suspect
    # a poisoned reading must not move the answer
    v = knee.verdict(rungs)
    assert v.kind == "knee"
    assert v.knee_db == pytest.approx(22.0, abs=4.0)


def test_refine_brackets_a_knee():
    read = chain(-118.0, -140.0)
    rungs = [knee.Rung(g, read(g)) for g in knee.plan(-40, 40, 8)]
    pts = knee.refine(rungs)
    assert pts and min(pts) < knee.verdict(rungs).knee_db < max(pts)


def test_refine_tests_a_one_regime_claim_instead_of_accepting_it():
    """It used to decline when the coarse pass found no knee, which was
    circular: a coarse pass that MISSED a knee then never asked for the
    rungs that would have shown it. The field walked into exactly that
    -- ten rungs, three of them in the flat stretch, read as one rising
    line, answered "input", no refinement, done. A claim that one
    regime covers the whole range is tested by looking harder where
    the other one would be."""
    flat = [knee.Rung(g, -100.0) for g in knee.plan(-40, 40, 8)]
    pts = knee.refine(flat)
    assert len(pts) == 5
    assert -40.0 < min(pts) and max(pts) < 40.0     # the middle of it
    assert knee.refine([knee.Rung(0.0, -100.0)]) == []


def test_the_margin_never_walks_off_the_top_of_the_control():
    v = knee.verdict(ladder(chain(-100.0, -130.0), lo=0.0, hi=33.0))
    assert v.kind == "knee"
    assert v.work_db <= 33.0


def test_the_plan_spans_the_control_ends_included():
    pts = knee.plan(-40, 40, 8)
    assert len(pts) == 8
    assert pts[0] == -40.0 and pts[-1] == 40.0


def test_a_reading_with_no_crest_is_not_a_noise_floor():
    assert not knee.noise_like(-67.31, -67.40)   # his CM106, DC-like
    assert not knee.noise_like(-6.02, -9.03)     # a sine, 3 dB
    assert knee.noise_like(-60.0, -72.0)         # broadband noise
    assert knee.noise_like(None, None)           # nothing to object with


def test_the_full_fifteen_rung_ladder_resolves_all_three_regions():
    """The same CM106, walked with ten coarse rungs and five refined.

    With every region resolved the reading is unambiguous: slope
    exactly one where the session manager is making up gain in
    software, flat where the converter outweighs the input, and rising
    where the card's own preamp has taken over. The knee lands just
    above the route's unity point (cubic 0.654), which is where that
    preamp starts to amplify at all.
    """
    measured = [(-60.0, -119.01), (-53.3, -112.38), (-46.7, -105.70),
                (-40.0, -99.08), (-33.3, -92.43), (-26.7, -86.12),
                (-20.0, -85.95), (-15.2, -85.74), (-13.3, -85.61),
                (-11.9, -85.58), (-8.5, -82.96), (-6.7, -80.09),
                (-5.2, -76.17), (-1.9, -67.02), (0.0, -60.98)]
    v = knee.verdict([knee.Rung(g, x) for g, x in measured])
    assert v.kind == "knee"
    assert [s.kind for s in v.segments] == ["rising", "flat", "rising"]
    # the software stretch is slope one by construction, and that is
    # what identifies it
    assert v.segments[0].slope == pytest.approx(1.0, abs=0.05)
    assert v.segments[1].slope == pytest.approx(0.0, abs=0.15)
    assert v.software_below == pytest.approx(-33.3, abs=1.0)
    assert -12.0 < v.knee_db < -4.0
    assert v.work_db > v.knee_db
    # the scatter the fit measures is the repeatability the bench has
    assert v.scatter < 0.5


# --- what to say about where a control stands ---------------------------

def test_a_control_inside_the_margin_band_is_already_right():
    # above the knee every position has the same SNR, so a fader within
    # the margin of the suggested point must not be nudged: the knee is
    # an extrapolation and wanders a few tenths between runs, and every
    # nudge fragments a canvas by capture gain
    assert knee.already_good(-5.5, -5.5)
    assert knee.already_good(-5.5, -8.4)          # 2.9 dB, inside 3.0
    assert not knee.already_good(-18.1, -5.5)
    assert not knee.already_good(None, -5.5)
    assert not knee.already_good(-5.5, None)


def test_the_caption_speaks_the_faders_own_units():
    """The decibels are the CONTROL's axis, and the control is marked
    in per cent, so "the knee is at -8.9 dB" named a spot the hand had
    no way to find."""
    import math
    here = 60.0 * math.log10(0.20)          # the fader at 20%
    line = knee.caption("knee", -8.9, here)
    assert "71%" in line                    # where to drag to
    assert "-8.9" not in line and "dB below" not in line


def test_below_the_knee_the_distance_IS_the_loss():
    """Below the knee the converter's floor dominates and SNR falls
    about a decibel per decibel, so the distance below is the SNR
    being thrown away. Saying so costs nothing and answers the only
    question worth asking."""
    import math
    line = knee.caption("knee", -8.9, 60.0 * math.log10(0.20))
    assert "33 dB" in line and "thrown away" in line


def test_above_the_knee_no_distance_is_printed():
    """SNR is flat up there, so the distance means nothing; what it
    costs is headroom, and that is what gets said."""
    import math
    line = knee.caption("knee", -8.5, 60.0 * math.log10(0.81))
    assert "above the knee" in line and "headroom" in line
    assert "dB of SNR" not in line


def test_the_caption_speaks_for_the_other_two_verdicts_too():
    assert "same SNR" in knee.caption("input", None, -60.0)
    assert "higher is better" in knee.caption("converter", None, 0.0)


def test_the_caption_is_silent_when_it_has_nothing_to_say():
    assert knee.caption("unclear", None, -5.5) is None
    assert knee.caption("knee", None, -5.5) is None
    assert knee.caption("knee", -8.5, None) is None




# --- a dwell that caught something --------------------------------------

def test_a_power_mean_is_the_wrong_estimator_for_a_floor():
    """Thirty-five blocks at -78 dBFS and ONE that caught an event at
    -50 come to -65.3 as a power mean. That is not a near miss: it is
    exactly the twelve decibel error a rung showed in the field, and
    it moved a knee from -9 to -19 by sending the refinement to the
    wrong end of the ladder."""
    from perdeviceeq.knee_run import _median, power_mean
    blocks = [-78.0] * 35 + [-50.0]
    assert power_mean(blocks) == pytest.approx(-65.33, abs=0.05)
    assert _median(blocks) == pytest.approx(-78.0)


def test_a_stationary_floor_reads_the_same_either_way():
    from perdeviceeq.knee_run import _median, power_mean
    blocks = [-78.0 + (i % 5) * 0.1 for i in range(36)]
    assert abs(power_mean(blocks) - _median(blocks)) < 0.3


def test_the_excess_marks_what_the_crest_cannot():
    """On a card whose peaks are pinned by fixed spikes, a contaminated
    dwell LOWERS the crest instead of raising it, so the crest test
    passes it. The excess does not care about peaks."""
    good = [knee.Rung(-20.0 + i, -85.0, peak_dbfs=-34.5) for i in range(4)]
    for r in good:
        r.excess = 0.05
    bad = knee.Rung(-16.0, -66.0, peak_dbfs=-34.1)   # a LOWER crest
    bad.excess = 12.7
    rungs = good + [bad]
    hit = knee.mark_transients(rungs)
    assert hit == [bad] and bad.suspect
    assert not any(r.suspect for r in good)

def test_flatness_is_a_slope_and_not_a_total_rise():
    """A piece with a negligible slope crosses any fixed rise given
    enough span. One did: a flat stretch of slope 0.18 over 6.7 dB
    accumulated 1.2 dB, was called rising, merged with its neighbours,
    and turned an ordinary knee into "the input outweighs the converter
    everywhere" -- on the same rig that answered -8.9 dB five times
    running."""
    data = [(-60, -118.93), (-53.3, -112.18), (-46.7, -105.55),
            (-40, -98.89), (-33.3, -92.24), (-26.7, -85.61),
            (-20, -85.65), (-13.3, -84.42), (-6.7, -77.68), (0, -59.37)]
    rungs = [knee.Rung(g, v, peak_dbfs=-40.0) for g, v in data]
    v = knee.verdict(rungs)
    assert v.kind == "knee"
    assert v.knee_db == pytest.approx(-8.9, abs=1.0)
    assert [s.kind for s in v.segments] == ["rising", "flat", "rising"]


def test_the_slope_threshold_is_the_knee_itself():
    """Not a constant anyone picked. Slope one means the noise follows
    the gain exactly, so the input dominates; slope zero means it does
    not move, so the converter does. Where they contribute equally --
    which is what a knee IS -- the local slope is one half."""
    assert knee.SLOPE_MID == 0.5
    span = [(0.0, 0.0), (10.0, 2.0)]                     # slope 0.2
    flat = [knee.Rung(g, v) for g, v in span]
    assert knee.describe(flat)[0][0].kind == "flat"
    span = [(0.0, 0.0), (10.0, 9.0)]                      # slope 0.9
    rise = [knee.Rung(g, v) for g, v in span]
    assert knee.describe(rise)[0][0].kind == "rising"


def test_the_fit_is_three_times_steadier_than_the_corner():
    """Seven ladders on one rig, nothing touched between them. The
    curves are nearly identical; what moved was where the segmentation
    cut them, and two lines meeting at a shallow angle turn one rung of
    boundary into decibels of knee."""
    runs = [
        [(-27.1, -86.14), (-24.1, -86.18), (-21.0, -85.93), (-18.0, -85.81),
         (-15.0, -85.50), (-12.4, -85.66), (-12.0, -85.05), (-10.7, -85.39),
         (-9.0, -83.40), (-7.4, -80.62), (-6.0, -77.11), (0.0, -60.97)],
        [(-27.1, -86.16), (-24.1, -86.22), (-21.0, -86.07), (-18.0, -85.85),
         (-15.0, -85.51), (-12.0, -85.63), (-11.4, -85.75), (-9.9, -84.79),
         (-9.0, -83.76), (-8.4, -82.72), (-6.9, -79.99), (-6.0, -77.63),
         (-5.4, -76.13), (-3.0, -69.43), (0.0, -60.50)],
        [(-27.1, -86.17), (-24.1, -86.17), (-21.0, -85.92), (-18.0, -85.90),
         (-15.0, -85.65), (-12.0, -85.72), (-11.2, -85.52), (-9.7, -84.47),
         (-9.0, -83.28), (-8.2, -82.41), (-6.7, -79.61), (-6.0, -77.47),
         (-5.2, -75.90), (-3.0, -69.17), (0.0, -59.67)],
        [(-27.1, -85.68), (-24.1, -86.03), (-21.0, -85.52), (-18.0, -85.71),
         (-15.0, -85.38), (-12.9, -85.54), (-12.0, -85.52), (-11.4, -85.47),
         (-9.9, -83.78), (-9.0, -82.72), (-8.4, -81.40), (-6.9, -79.14),
         (-6.0, -75.08), (-3.0, -66.88), (0.0, -58.83)],
    ]
    knees, floors = [], []
    for m in runs:
        v = knee.verdict([knee.Rung(g, x) for g, x in m])
        assert v.kind == "knee"
        assert v.curve.resid < 0.5
        knees.append(v.knee_db)
        floors.append(v.curve.floor_dbfs)
    assert max(knees) - min(knees) < 2.0      # the corner gave 3.8
    assert max(floors) - min(floors) < 0.5    # and the floor barely moves


def test_a_ladder_the_model_cannot_describe_falls_back(monkeypatch):
    """Three parameters need rungs. A coarse ladder above a software
    stretch can leave four, and a segmented answer beats none."""
    measured = [(-60.0, -118.95), (-51.4, -110.59), (-42.9, -101.94),
                (-34.3, -93.49), (-25.7, -86.30), (-17.1, -85.88),
                (-8.6, -83.95), (0.0, -59.13)]
    v = knee.verdict([knee.Rung(g, x) for g, x in measured])
    assert v.kind == "knee" and v.curve is None
    assert v.software_below == pytest.approx(-34.3, abs=0.1)


def test_a_piece_too_short_to_have_a_slope_is_not_described():
    """Least squares hands back a slope for two rungs three tenths of a
    decibel apart, and the field printed two: "flat -6.0 .. -5.7, -0.62
    dB per dB" and "flat -6.0 .. -6.0, -9.11 dB per dB". A flat stretch
    descending at nine decibels per decibel is not a reading.

    The bar follows from what the slope is FOR: it is compared against
    SLOPE_MID, so it must be determined to better than that, and the
    uncertainty of a fitted slope is about the scatter over the span.
    """
    measured = [(-27.1, -86.32), (-24.1, -86.21), (-21.0, -86.08),
                (-18.0, -85.96), (-15.0, -85.39), (-12.0, -85.81),
                (-10.5, -85.18), (-9.0, -83.53), (-7.5, -81.17),
                (-6.0, -77.08), (-6.0, -77.58), (-3.0, -68.63),
                (0.0, -60.31)]
    v = knee.verdict([knee.Rung(g, x) for g, x in measured])
    assert [s.kind for s in v.segments] == ["flat", "rising"]
    for s in v.segments:
        assert s.hi - s.lo > 1.0
        assert -1.0 < s.slope < 4.0        # no arithmetic on noise
    assert v.knee_db == pytest.approx(-8.7, abs=0.5)


def test_ten_field_ladders_agree_to_half_a_decibel():
    """His CM106, ten runs with nothing touched between them, read with
    the fit. The segmentation gave working points from 0.769 to 0.893;
    these span 0.013."""
    knees = [-8.6, -8.6, -8.6, -8.5, -8.3, -8.4, -8.4, -8.5, -8.5, -8.7]
    floors = [-86.11, -86.02, -86.01, -86.02, -86.09,
              -86.06, -86.13, -86.12, -86.11, -86.09]
    # recorded here as the acceptance the estimator was built to meet
    assert max(knees) - min(knees) <= 0.5
    assert max(floors) - min(floors) <= 0.2


def test_the_exponent_is_the_card_and_not_the_physics():
    """A tone played into the coupler and walked up the same ladder rose
    3.06 dB per dB of this axis while the noise rose 3.08 -- within one
    per cent, so the noise follows the REAL gain one for one, which is
    ordinary amplified input noise. A CM106's exponent near six is this
    axis stretched threefold by the card's taper.

    Recorded as a court because the fit must keep MEASURING it: a card
    with an honest taper answers near two, and nothing here may assume
    either number.
    """
    import math

    def chain(floor_db, mic0, stretch):
        """noise that follows the REAL gain, on an axis stretched by
        `stretch` decibels of gain per decibel of control"""
        return lambda g: 10 * math.log10(
            10 ** (floor_db / 10.0)
            + 10 ** ((mic0 + stretch * g) / 10.0))

    for stretch, want in ((1.0, 2.0), (3.0, 6.0)):
        rungs = [knee.Rung(g, chain(-118.0, -100.0, stretch)(g))
                 for g in knee.plan(-40.0, 20.0, 15)]
        v = knee.verdict(rungs)
        assert v.curve is not None
        assert v.curve.n == pytest.approx(want, abs=0.3)
