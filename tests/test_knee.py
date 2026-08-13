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
    v = knee.verdict(ladder(chain(-118.0, -80.0)))
    assert v.kind == "input"
    assert v.work_db == -40.0          # the bottom of what was walked
    assert v.usable


def test_flat_throughout_wants_the_TOP_of_the_range():
    # the converter outweighs the input everywhere, so this noise does
    # not follow the control while a signal would: more gain still
    # buys SNR, which makes the top the place to be
    v = knee.verdict(ladder(chain(-100.0, -200.0)))
    assert v.kind == "converter"
    assert v.work_db == 40.0
    assert v.usable


def test_too_few_rungs_is_unclear_rather_than_a_guess():
    v = knee.verdict([knee.Rung(0.0, -100.0)])
    assert v.kind == "unclear"
    assert not v.usable


# --- the curves this bench produced ----------------------------------

def test_the_three_region_curve_names_its_software_stretch():
    """A CM106 laddered from -60 dB to 0 on its microphone port.

    Slope one from -60 to -34 is the session manager making up gain in
    software below the hardware control's range: it scales the
    converter's floor along with everything else, which is why it is
    exactly one and never flattens. Then the converter outweighs the
    input and the curve is flat. Then the card's own preamp takes over
    and it rises again. The knee is the SECOND boundary; the first is
    an artefact of the software path and says nothing about the chain.
    """
    measured = [(-60.0, -118.95), (-51.4, -110.59), (-42.9, -101.94),
                (-34.3, -93.49), (-25.7, -86.30), (-17.1, -85.88),
                (-8.6, -83.95), (0.0, -59.13)]
    v = knee.verdict([knee.Rung(g, x) for g, x in measured])
    assert v.kind == "knee"
    assert [s.kind for s in v.segments] == ["rising", "flat", "rising"]
    assert v.software_below == pytest.approx(-34.3, abs=0.1)
    assert -20.0 < v.knee_db < 0.0
    # the software stretch is exactly slope one, which is its signature
    assert v.segments[0].slope == pytest.approx(1.0, abs=0.1)


def test_the_unity_start_run_reads_input():
    """The same card walked from its unity point upward: every rung
    answers the control, so what is measured sits before it."""
    measured = [(-11.1, -85.40), (-9.5, -83.96), (-7.9, -82.24),
                (-6.3, -79.01), (-4.7, -75.47), (-3.2, -71.38),
                (-1.6, -66.95), (0.0, -60.65)]
    v = knee.verdict([knee.Rung(g, x) for g, x in measured])
    assert v.kind == "input"
    assert v.work_db == -11.1


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


def test_refine_brackets_a_knee_and_declines_without_one():
    read = chain(-118.0, -140.0)
    rungs = [knee.Rung(g, read(g)) for g in knee.plan(-40, 40, 8)]
    pts = knee.refine(rungs)
    assert pts and min(pts) < knee.verdict(rungs).knee_db < max(pts)
    flat = [knee.Rung(g, -100.0) for g in knee.plan(-40, 40, 8)]
    assert knee.refine(flat) == []


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


def test_the_caption_says_which_side_of_the_knee_the_control_is_on():
    above = knee.caption("knee", -8.5, -5.5)
    assert "above the knee" in above and "-8.5" in above
    below = knee.caption("knee", -8.5, -13.3)
    assert "BELOW the knee" in below
    assert "thrown away" in below                 # the one wrong place
    # it never promises a QUANTITY of SNR, because the axis is the
    # control's own and not the card's decibels
    assert "SNR" not in above


def test_the_caption_speaks_for_the_other_two_verdicts_too():
    assert "same SNR" in knee.caption("input", None, -60.0)
    assert "higher is better" in knee.caption("converter", None, 0.0)


def test_the_caption_is_silent_when_it_has_nothing_to_say():
    assert knee.caption("unclear", None, -5.5) is None
    assert knee.caption("knee", None, -5.5) is None
    assert knee.caption("knee", -8.5, None) is None
