"""The knee policy against chains whose answer is known by arithmetic.

A capture chain in silence is two noises adding in power: the
converter's own floor, fixed in dBFS, and the input's noise, which
rides the gain. Their sum crosses slope one-half exactly where they
are equal, so a synthetic chain with a floor at F and an input noise
of M at zero gain has its knee at F - M dB, to the decimal. That makes
these courts arithmetic rather than approximate.
"""

import math

import pytest

from perdeviceeq import knee


def chain(floor_db, mic_at_zero_db):
    def read(gain_db):
        return 10.0 * math.log10(10 ** (floor_db / 10.0)
                                 + 10 ** ((mic_at_zero_db + gain_db) / 10.0))
    return read


def ladder(read, lo=-40.0, hi=40.0, steps=8, with_refine=True):
    rungs = [knee.Rung(g, read(g)) for g in knee.plan(lo, hi, steps)]
    if with_refine:
        rungs += [knee.Rung(g, read(g)) for g in knee.refine(rungs)]
    return rungs


def test_a_knee_inside_the_range_is_found_where_the_arithmetic_puts_it():
    v = knee.verdict(ladder(chain(-118.0, -140.0)))
    assert v.kind == "knee"
    assert v.knee_db == pytest.approx(22.0, abs=1.0)
    assert v.work_db == pytest.approx(28.0, abs=1.0)
    assert v.usable


def test_an_input_that_already_dominates_has_no_knee_and_wants_the_minimum():
    v = knee.verdict(ladder(chain(-118.0, -80.0)))
    assert v.kind == "input"
    assert v.work_db == -40.0
    assert v.usable


def test_a_converter_that_rules_everywhere_yields_no_working_point():
    v = knee.verdict(ladder(chain(-100.0, -200.0)))
    assert v.kind == "converter"
    assert not v.usable


def test_too_few_rungs_is_unclear_rather_than_a_guess():
    v = knee.verdict([knee.Rung(0.0, -100.0), knee.Rung(10.0, -90.0)])
    assert v.kind == "unclear"
    assert not v.usable


def test_refine_declines_when_the_curve_never_flattens():
    # the input dominates from the first step, so there is no crossing
    # to bracket and refining would invent one
    rungs = [knee.Rung(g, chain(-118.0, -80.0)(g))
             for g in knee.plan(-40, 40, 8)]
    assert knee.refine(rungs) == []


def test_refine_brackets_the_crossing():
    read = chain(-118.0, -140.0)
    rungs = [knee.Rung(g, read(g)) for g in knee.plan(-40, 40, 8)]
    pts = knee.refine(rungs)
    assert pts, "a curve with a knee must be worth refining"
    assert min(pts) < 22.0 < max(pts)


def test_a_transient_is_marked_and_kept_out_of_the_fit():
    read = chain(-118.0, -140.0)
    rungs = [knee.Rung(g, read(g), peak_dbfs=read(g) + 9.0)
             for g in knee.plan(-40, 40, 8)]
    clean_slopes = len(knee.slopes(rungs))
    # a door slams during one rung: the level it recorded is nonsense
    # and its crest gives it away
    rungs[2].rms_dbfs = -40.0
    rungs[2].peak_dbfs = -5.0
    marked = knee.mark_transients(rungs)
    assert marked == [rungs[2]]
    assert rungs[2].suspect
    # dropped from the fit: one rung gone is one slope fewer. The
    # midpoints cannot be checked instead -- on an evenly spaced
    # ladder the pair that closes over a dropped rung has exactly
    # that rung's gain as its midpoint
    assert len(knee.slopes(rungs)) == clean_slopes - 1
    # and, the property that actually matters, a poisoned reading does
    # not move the answer
    v = knee.verdict(rungs)
    assert v.kind == "knee"
    assert v.knee_db == pytest.approx(22.0, abs=2.0)


def test_the_plan_spans_the_control_ends_included():
    pts = knee.plan(-40, 40, 8)
    assert len(pts) == 8
    assert pts[0] == -40.0 and pts[-1] == 40.0


def test_the_margin_never_walks_off_the_top_of_the_control():
    # knee at 30 dB with only 33 dB of control: work would be 36 and
    # is clamped to what the card can actually be set to
    v = knee.verdict(ladder(chain(-100.0, -130.0), lo=0.0, hi=33.0))
    assert v.kind == "knee"
    assert v.knee_db == pytest.approx(30.0, abs=2.0)
    assert v.work_db == 33.0


def test_a_knee_too_close_to_the_ceiling_is_unclear_rather_than_claimed():
    # the crossing sits 2 dB below the top, so the ladder never sees a
    # pair climb steeply enough. Saying so is better than naming a
    # knee the evidence does not carry
    v = knee.verdict(ladder(chain(-100.0, -138.0), lo=0.0, hi=40.0))
    assert v.kind == "unclear"
    assert not v.usable


def test_a_reading_with_no_crest_is_not_a_noise_floor():
    # his CM106 line in: rms -67.40, peak -67.31. Nine hundredths of a
    # dB of crest is a DC offset or a tone, and a ladder walked over
    # one measures the control's transfer curve, not the chain's floor
    assert not knee.noise_like(-67.31, -67.40)
    # a sine has 3 dB and is still not noise
    assert not knee.noise_like(-6.02, -9.03)
    # broadband noise carries 11 to 13
    assert knee.noise_like(-60.0, -72.0)
    # nothing to object with
    assert knee.noise_like(None, None)


def test_an_offset_is_not_a_floor():
    # the meter reports RMS about the mean, so a chain carrying a large
    # DC offset still reports the noise underneath it rather than the
    # offset. knee.noise_like() then judges the AC part on its own
    assert knee.noise_like(-60.0, -72.0)      # noise under any offset
    assert not knee.noise_like(-34.53, -34.62)


def test_a_curve_that_climbs_before_it_flattens_has_no_knee():
    """A CM106's real ladder, read off its microphone port.

    Slope one from -60 to -34, a plateau from -25 to -8.6, then a
    climb steeper than one. Three regions of a control rather than a
    floor and a rise, and an earlier version of this module took the
    plateau for the floor and named a knee at -24.5 dB that is not in
    the data. Nothing in a ladder that begins by climbing is a
    crossing: the bottom of it was never in the converter's region.
    """
    measured = [(-60.0, -118.95), (-51.4, -110.59), (-42.9, -101.94),
                (-34.3, -93.49), (-25.7, -86.30), (-17.1, -85.88),
                (-8.6, -83.95), (-6.4, -76.00), (-4.3, -73.13),
                (-2.1, -67.43), (0.0, -61.79)]
    rungs = [knee.Rung(g, v) for g, v in measured]
    v = knee.verdict(rungs)
    assert v.kind == "unclear"
    assert not v.usable
    assert knee.refine(rungs) == []
