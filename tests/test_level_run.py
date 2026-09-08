"""The level search: its policy, and the field runs that shaped it."""

import math
import random

import numpy as np
import pytest

from perdeviceeq import measure_core as mc
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


def test_no_step_predicts_a_peak_past_the_ceiling():
    """His second field run, after the climb was first aimed: probe
    at 19% came back at a peak of -10.5, already PAST the aim, so
    there was nothing left to want -- and the first cut fell back to
    the full ramp there, doubled to 38%, and the sweep hit 0.0 dBFS.

    A probe sits past the aim and is still refused when something
    OTHER than the level is refusing it, an untrustworthy SNR being
    the case in hand. Whatever the reason, the ceiling caps the step.
    """
    a = level_run.AutoLevel()
    v = 0.19
    a.observe(v, -10.5, 34.1, False, False, margin_db=10.0)
    nv = a.next_volume(v)
    predicted = -10.5 + 60.0 * math.log10(nv / v)
    assert predicted <= level_run.AUTO_PEAK_CEIL, (nv, predicted)
    # and it creeps rather than ramps: the level is not the problem
    step = 60.0 * math.log10(level_run.AUTO_RAMP_NEAR)
    assert predicted - (-10.5) == pytest.approx(step, abs=0.2)


def test_the_level_is_announced_before_the_sweep(monkeypatch, tmp_path):
    """HIS NEAR MISS, and the reason this hook exists: a walk reached
    80% with the wrong earphone in the coupler, and the only thing
    that stopped the next rung -- full volume -- was that he read the
    line and hit stop. A level announced AFTER the sweep is a level
    that has already been in someone's ears.

    So on_level fires before the sound, and with the volume that is
    about to play."""
    seen = []

    class FakeBack:
        def moratorium_begin(self, *a, **k):
            # by the time a claim is made the level must already have
            # been announced
            assert seen, "the sweep was claimed before it was announced"

        def moratorium_end(self):
            pass

    monkeypatch.setattr(level_run.pw_backend, "backend",
                        lambda: FakeBack())
    monkeypatch.setattr(level_run, "write_sweep_files",
                        lambda *a, **k: str(tmp_path / "s.wav"))
    # NOT SILENCE: a capture at the digital floor now ends the walk,
    # because a microphone that hears nothing is not in the path and
    # no volume fixes that. This court is about the ORDER of the
    # announcement, so it hands back something audible.
    monkeypatch.setattr(
        level_run, "run_take",
        lambda *a, **k: (np.full((48000, 1), 0.01), {}))

    level_run.hunt({"name": "s"}, {"name": "m"}, 1, sink_name="s",
                   on_level=lambda v, step: seen.append((round(v, 4),
                                                         step)),
                   max_adjust=2)
    assert seen and seen[0][1] == 1
    assert seen[0][0] == pytest.approx(level_run.AUTO_START_VOLUME,
                                       abs=0.05)


def test_the_shortfall_is_also_a_quantity():
    """A ceiling is a yes/no, but "past it" has degrees: his iLoud
    falls 1.1 dB short of a 2 dB step at 43% of the knob and 1.9 at
    50%, which is the difference between a hint and nothing left.

    Both ends of the scale are given rather than chosen -- we picked
    the step, and the rig either delivered it or did not."""
    freqs = np.asarray(mc.log_grid())
    prev = np.zeros(len(freqs))
    heard = np.full(len(freqs), 30.0)
    # took the whole 4 dB step everywhere
    quietly = np.full(len(freqs), 0.1)      # a coupler's own floor
    full = level_run.shortfall_db(prev, prev + 4.0, heard, 4.0,
                                  freqs, mc.GRID_PPO, quietly)
    assert np.nanmax(full) == 0.0
    # took none of it
    none = level_run.shortfall_db(prev, prev, heard, 4.0,
                                  freqs, mc.GRID_PPO, quietly)
    assert abs(float(np.nanmax(none)) - 4.0) < 1e-9
    # took half
    half = level_run.shortfall_db(prev, prev + 2.0, heard, 4.0,
                                  freqs, mc.GRID_PPO, quietly)
    assert abs(float(np.nanmax(half)) - 2.0) < 1e-9
    # and a bin whose two sweeps would land further apart than the
    # shortfall being looked for is absent, not short
    quiet = np.full(len(freqs), 0.0)
    out = level_run.shortfall_db(prev, prev, quiet, 4.0,
                                 freqs, mc.GRID_PPO,
                                 np.full(len(freqs), 9.0))
    assert not np.isfinite(out).any()


def test_the_step_is_what_arrived_not_what_the_knob_promised():
    """The knob's ratio is an intention. Over Bluetooth it is not even
    that: his JBL Tour Pro 3 answers AVRCP's own 128-step scale, and
    rungs the walk asked 4.0 dB of arrived 8.0 and 14.1 dB louder.

    Everything downstream compares what came back against what was
    asked, so a fictitious ask makes a fictitious verdict: where twice
    the step arrived and one step was taken, the band is a step short
    and the walk called it answered in full. The capture peak follows
    the level one for one on every wired rig here, which is what makes
    it the honest witness -- it reports what the rig was given,
    whoever set the volume and by whatever scale."""
    # the knob says 4 dB and the peak agrees: a wired rig
    assert abs(level_run.asked_db((0.40, -20.0), (0.50, -16.1))
               - 3.9) < 1e-9
    # the knob says 4 dB and twice that arrived: his Bluetooth JBL
    assert abs(level_run.asked_db((0.59, -22.4), (0.69, -14.4))
               - 8.0) < 1e-9
    # no peak to ask, so the knob is all there is
    got = level_run.asked_db((0.40, None), (0.50, None))
    assert abs(got - 60.0 * math.log10(0.5 / 0.4)) < 1e-9


def test_the_map_records_what_stopped_it():
    """Whether anything may be said about levels ABOVE the loudest
    rung depends on what ended the walk.

    A clean top rung that ended at the CAPTURE says nothing about
    louder: his Tanchjim answered every rung of fourteen decibels and
    stopped only because the microphone ran out of room, and three of
    his five rigs stop that way. A top rung that already shows a loss
    does say something, because the mechanisms are monotone in level.

    The rig itself never stops the walk -- a map climbs past a ceiling
    on purpose, since the rungs above one are where the loss grows."""
    freqs = np.asarray(mc.log_grid())

    class Got:
        def __init__(self, mag):
            self.mag_db = mag
            self.noise_dbfs = -80.0
            self.signal_dbfs = -40.0

    def rig(head_db, start):
        """A rig that answers everything; only the capture limits it."""
        def play(back, name, sink, source, wav, duration, channels,
                 sweep, fr, analyze, v, play_map):
            db = 60.0 * math.log10(v / start)
            return None, -level_run.AUTO_PEAK_CEIL * 0 - head_db + db, \
                False, Got(np.full(len(freqs), db))
        return play

    class Back:
        def moratorium_begin(self, *a, **k):
            pass

        def moratorium_end(self, *a, **k):
            pass

    real_backend = level_run.pw_backend.backend
    real_rung = level_run._play_rung
    level_run.pw_backend.backend = lambda: Back()
    try:
        # eight decibels of room under the ceiling: the microphone
        # stops the walk long before the rig would have
        level_run._play_rung = rig(8.0, 0.5)
        rungs = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2,
                                       0.5, sink_name="x", freqs=freqs)
        assert rungs[-1]["stopped_by"] == "capture"
        # and with the volume already at its top there is nowhere to go
        level_run._play_rung = rig(40.0, 1.0)
        rungs = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2,
                                       1.0, sink_name="x", freqs=freqs)
        assert rungs[-1]["stopped_by"] == "knob"
    finally:
        level_run._play_rung = real_rung
        level_run.pw_backend.backend = real_backend


def test_an_interrupted_search_is_not_a_search():
    """A level settled on days ago, a new search started by accident,
    caught and stopped -- and the half-finished walk used to overwrite
    the good number with wherever it happened to be standing.

    Half a walk knows nothing, so it answers None and every caller
    keeps what it had."""
    freqs = np.asarray(mc.log_grid())

    class Got:
        mag_db = None
        thd_db = None
        thd_noise_db = None
        snr_db = 45.0

    def play(back, name, sink, source, wav, duration, channels,
             sweep, fr, analyze, v, play_map):
        g = Got()
        g.mag_db = np.zeros(len(freqs))
        g.thd_db = np.full(len(freqs), -60.0)
        g.thd_noise_db = np.full(len(freqs), -80.0)
        return None, -30.0, False, g

    class Back:
        def moratorium_begin(self, *a, **k):
            pass

        def moratorium_end(self, *a, **k):
            pass

    real_backend = level_run.pw_backend.backend
    real_rung = level_run._play_rung
    level_run.pw_backend.backend = lambda: Back()
    level_run._play_rung = play
    try:
        vol, probes = level_run.hunt(
            {"name": "x"}, {"name": "y"}, 2, sink_name="x",
            freqs=freqs, should_stop=lambda: True)
        assert vol is None
        assert probes == []
    finally:
        level_run._play_rung = real_rung
        level_run.pw_backend.backend = real_backend


def test_the_map_climbs_past_the_border_rather_than_closing_on_it():
    """Bracketing is the SEARCH's job: it wants one number and stops
    the moment it has it. A map's rungs are the points of a curve, and
    the ones ABOVE a border are the most valuable, because that is
    where the loss grows.

    An earlier cut walked boldly and halved onto the border, then
    stopped: on his iLoud it ended at 57% while the level he listens
    at delivers 70% at 40 Hz, so it stopped just short of the only
    part he needed. With an even step the border is located to that
    step anyway."""
    freqs = np.asarray(mc.log_grid())
    played = []

    class Got:
        def __init__(self, mag):
            self.mag_db = mag
            self.noise_dbfs = -80.0
            self.signal_dbfs = -40.0

    def rig(border):
        def play(back, name, sink, source, wav, duration, channels,
                 sweep, fr, analyze, v, play_map):
            played.append(v)
            db = 60.0 * math.log10(v / 0.42)
            mag = np.full(len(freqs), db)
            inb = (freqs >= 40.0) & (freqs <= 90.0)
            mag[inb] -= 2.0 * max(0.0, 60.0 * math.log10(v / border))
            return None, -22.0 + db, False, Got(mag)
        return play

    class Back:
        def moratorium_begin(self, *a, **k):
            pass

        def moratorium_end(self, *a, **k):
            pass

    real_backend = level_run.pw_backend.backend
    real_rung = level_run._play_rung
    level_run.pw_backend.backend = lambda: Back()
    try:
        level_run._play_rung = rig(0.55)
        rungs = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2,
                                       0.42, sink_name="x", freqs=freqs)
        lv = sorted(r["level"] for r in rungs)
        # it did not stop at the border it found around 55%
        assert lv[-1] > 0.80
        # and the rungs are an even ladder, so the border is located
        # to one step without any closing in
        steps = [60.0 * math.log10(b / a) for a, b in zip(lv, lv[1:])]
        assert max(steps) - min(steps) < 0.3
    finally:
        level_run._play_rung = real_rung
        level_run.pw_backend.backend = real_backend

def test_a_silent_capture_does_not_send_the_ramp_climbing():
    """SILENCE IS NOT QUIETNESS. analyze_take reports -120 dBFS for a
    channel that is digitally silent, and the ramp's arithmetic then
    sees a hundred decibels of room and asks for the whole step: his
    Tanchjim walk went 15%, 30%, 60% and would have gone on, because
    the coupler was not hearing the earphone at all.

    Doubling into an earphone is the accident this ramp was rewritten
    to prevent. A silent capture creeps instead, and two silent probes
    end the walk -- no volume fixes a microphone that is not in the
    path."""
    ctl = level_run.AutoLevel()
    ctl.observe(0.15, -120.0, None, False)
    assert ctl.next_volume(0.15) < 0.18          # creeping, not doubling

    heard = level_run.AutoLevel()
    heard.observe(0.15, -45.0, 30.0, False)
    assert heard.next_volume(0.15) > 0.25        # a real quiet probe climbs

    freqs = np.asarray(mc.log_grid())
    played = []

    class Got:
        def __init__(self):
            self.mag_db = np.zeros(len(freqs))
            self.snr_db = 2.0
            self.thd_db = np.full(len(freqs), -20.0)
            self.thd_noise_db = np.full(len(freqs), -20.0)

    def silent(back, name, sink, source, wav, duration, channels,
               sweep, fr, analyze, v, play_map):
        played.append(v)
        return None, -120.0, False, Got()

    class Back:
        def moratorium_begin(self, *a, **k):
            pass

        def moratorium_end(self, *a, **k):
            pass

    real_backend = level_run.pw_backend.backend
    real_rung = level_run._play_rung
    level_run.pw_backend.backend = lambda: Back()
    level_run._play_rung = silent
    try:
        with pytest.raises(RuntimeError):
            level_run.hunt({"name": "x"}, {"name": "y"}, 2,
                           sink_name="x", freqs=freqs)
        assert len(played) == 2
    finally:
        level_run._play_rung = real_rung
        level_run.pw_backend.backend = real_backend


def test_a_knob_decibel_is_not_a_decibel():
    """On a sink with a volume scale of its own the delivered gain
    outruns the asked one: his JBL answers AVRCP's 128 steps and
    arrived 7.0 dB louder for 4.1 asked, 1.72 to one.

    A wall that predicts its landing from the knob therefore lands
    somewhere else, and the bolder the stride the worse it is: two of
    his rigs came back at 0.0 dBFS, clipped, from a step computed to
    reach exactly -2.0. The walk measures the ratio as it goes, and
    before it has one it assumes the worst it has ever seen."""
    freqs = np.asarray(mc.log_grid())
    peaks = []

    class Got:
        def __init__(self, mag):
            self.mag_db = mag
            self.noise_dbfs = -80.0
            self.signal_dbfs = -40.0

    def bluetooth(start, pk0, gain_ratio):
        """A sink that delivers `gain_ratio` dB for every knob dB."""
        def play(back, name, sink, source, wav, duration, channels,
                 sweep, fr, analyze, v, play_map):
            db = 60.0 * math.log10(v / start) * gain_ratio
            peaks.append(pk0 + db)
            return None, pk0 + db, False, \
                Got(np.full(len(freqs), db))
        return play

    class Back:
        def moratorium_begin(self, *a, **k):
            pass

        def moratorium_end(self, *a, **k):
            pass

    real_backend = level_run.pw_backend.backend
    real_rung = level_run._play_rung
    level_run.pw_backend.backend = lambda: Back()
    try:
        level_run._play_rung = bluetooth(0.73, -9.8, 1.72)
        level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.73,
                               sink_name="x", freqs=freqs)
        # nothing was played past the capture ceiling
        assert max(peaks) <= level_run.AUTO_PEAK_CEIL + 0.1
    finally:
        level_run._play_rung = real_rung
        level_run.pw_backend.backend = real_backend


def test_the_map_begins_below_the_search_not_at_it():
    """The search settles at the loudest level a take can be made at.
    A listener plays much quieter, because the correction stands in
    between: on his iLoud, with a preamp of -17.5 and his taste layer,
    a knob at 85% delivers the equivalent of 58% at 50 Hz and 47% at
    80 -- while the map ran from 66% up, so almost the whole range he
    uses lay BELOW the quietest rung, where a map says nothing.

    And the reference matters as much as the range: every rung is read
    against the first, whose loss is therefore zero BY DEFINITION.
    Starting at the search's level made the loudest level in the
    profile the thing everything else was believed against."""
    assert level_run.MAP_BELOW_DB > 0
    start = 0.66 * 10.0 ** (-level_run.MAP_BELOW_DB / 60.0)
    assert 0.40 < start < 0.45
    # and the budget reaches back up past where the search settled
    top = start * 10.0 ** (level_run.MAP_STEP_DB
                           * (level_run.MAP_MAX_RUNGS - 1) / 60.0)
    assert top > 0.66


def test_the_map_measures_its_own_scatter():
    """A loss has to clear the disagreement between two sweeps to be
    worth believing, and that number used to come from the profile's
    TAKES -- which tied a map to a measurement it has nothing to do
    with, and threw the map away when a hand ran only the search.

    A map is a property of the rig. It plays its base rung twice and
    measures the scatter itself, in the same conditions as the loss it
    gates: 0.05 dB on a quiet chain here, 0.37 on a noisy one."""
    freqs = np.asarray(mc.log_grid())
    rng = np.random.default_rng(3)

    class Got:
        def __init__(self, mag):
            self.mag_db = mag
            self.noise_dbfs = -80.0
            self.signal_dbfs = -40.0

    def rig(noise):
        def play(back, name, sink, source, wav, duration, channels,
                 sweep, fr, analyze, v, play_map):
            db = 60.0 * math.log10(v / 0.4)
            mag = np.full(len(freqs), db) + rng.normal(0, noise,
                                                       len(freqs))
            return None, -25.0 + db, False, Got(mag)
        return play

    class Back:
        def moratorium_begin(self, *a, **k):
            pass

        def moratorium_end(self, *a, **k):
            pass

    real_backend = level_run.pw_backend.backend
    real_rung = level_run._play_rung
    level_run.pw_backend.backend = lambda: Back()
    try:
        level_run._play_rung = rig(0.05)
        quiet = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2,
                                       0.4, sink_name="x", freqs=freqs,
                                       max_rungs=4)
        level_run._play_rung = rig(0.4)
        noisy = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2,
                                       0.4, sink_name="x", freqs=freqs,
                                       max_rungs=4)
    finally:
        level_run._play_rung = real_rung
        level_run.pw_backend.backend = real_backend

    def median(rungs):
        a = np.array([np.nan if x is None else float(x)
                      for x in rungs[0]["scatter_db"]], float)
        return float(np.nanmedian(a))

    assert median(quiet) < median(noisy)
    # and only the base carries it -- the others are read against it
    assert all(r.get("scatter_db") is None for r in quiet[1:])
    # WHERE A RIG MAKES NO SOUND the two sweeps compare two noises,
    # and the difference is whatever the room felt like: his iLoud
    # read 0.06 dB across the band and 33.95 at the edges of the grid.
    # The gate is closed there anyway, but a number that size sitting
    # in a profile is a trap for whoever takes its maximum.
    a = np.array([np.nan if x is None else float(x)
                  for x in quiet[0]["scatter_db"]], float)
    assert np.nanmax(a) < 2.0


# --- a map outlives the takes it was walked beside --------------------

def _rung(level, n=4):
    return {"level": level, "mag_db": [0.0] * n, "stopped_by": None}


def test_a_map_in_its_own_home_is_read():
    prof = {"passport": {"FL": {"rungs": [_rung(0.3), _rung(0.5)]}}}
    got = level_run.maps_of(prof)
    assert list(got) == ["FL"] and len(got["FL"]) == 2


def test_a_map_in_an_old_session_block_is_still_read():
    """Profiles written before the move must not go blind."""
    prof = {"measurement": {"sessions": {
        "s1": {"headroom": {"FR": [_rung(0.4)]}}}}}
    assert list(level_run.maps_of(prof)) == ["FR"]


def test_the_pseudo_session_is_read_too():
    """An earlier cut had nowhere to put a map when the profile had no
    session, so it keyed one 'headroom' -- and the window's reader has
    stepped over it ever since, because it looks for a block WITH a
    headroom key rather than a block that IS one."""
    prof = {"measurement": {"sessions": {
        "headroom": {"FL": [_rung(0.2), _rung(0.6)]}}}}
    assert len(level_run.maps_of(prof)["FL"]) == 2


def test_the_new_home_wins_over_an_old_block():
    prof = {"passport": {"FL": {"rungs": [_rung(0.9)]}},
            "measurement": {"sessions": {
                "s1": {"headroom": {"FL": [_rung(0.1), _rung(0.2)]}}}}}
    got = level_run.maps_of(prof)["FL"]
    assert len(got) == 1 and got[0]["level"] == 0.9


def test_a_passport_survives_what_kills_a_session():
    """The reason for the move, in one court. remove_takes prunes any
    session with no takes left, so a map stored inside one died the
    moment its takes were deleted -- and one was lost exactly so."""
    prof = {"passport": {"FL": {"rungs": [_rung(0.5)]}},
            "measurement": {"takes": [], "sessions": {}}}
    assert level_run.maps_of(prof)["FL"]


def test_a_profile_with_no_map_reads_as_none():
    assert level_run.maps_of({"measurement": {"sessions": {}}}) == {}
    assert level_run.maps_of({}) == {}


# --- which rung does not belong with the rest -------------------------

def _ladder(k=11, n=200, drive=2.0, steps=None):
    """A rig that follows its knob, with a little noise.

    Each rung carries the capture peak, because that is the axis the
    reading is fitted against: a ladder is smooth in LEVEL, and the
    rungs are not evenly spaced once the descent goes coarse.
    """
    rng = random.Random(4)
    out = []
    at = 0.0
    for j in range(k):
        if j and steps:
            at += steps[(j - 1) % len(steps)]
        elif j:
            at += drive
        out.append({"level": 0.3 * 10.0 ** (at / 60.0),
                    "peak_dbfs": -40.0 + at,
                    "mag_db": [at + rng.gauss(0, 0.02)
                               for _ in range(n)]})
    return out


def test_a_clean_ladder_has_no_odd_rung():
    res, floor = level_run.odd_rung_out(_ladder())
    assert floor < 0.1
    for row in res:
        v = [x for x in row if x is not None]
        assert (sum(x * x for x in v) / len(v)) ** 0.5 < 0.1


def test_one_spoiled_rung_lights_one_rung_and_not_its_neighbours():
    """Against NEIGHBOURS a fault lights three -- the culprit at
    weight one and each neighbour at a half -- and which of the three
    did it cannot be read off the picture. Against the trend of all,
    it lights one."""
    got = _ladder()
    for i in range(40, 90):                       # a bark, one rung
        got[5]["mag_db"][i] += 2.0
    res, floor = level_run.odd_rung_out(got)

    def rms(k):
        v = [x for x in res[k] if x is not None]
        return (sum(x * x for x in v) / len(v)) ** 0.5
    assert rms(5) > 20 * floor
    for k in (3, 4, 6, 7):
        assert rms(k) < 4 * floor


def test_three_spoiled_rungs_are_named_separately():
    got = _ladder()
    for i in range(10, 30):
        got[2]["mag_db"][i] += 3.0
    for i in range(60, 120):
        got[5]["mag_db"][i] += 2.0
    for i in range(150, 190):
        got[8]["mag_db"][i] -= 2.5
    res, floor = level_run.odd_rung_out(got)
    def rms(k):
        v = [x for x in res[k] if x is not None]
        return (sum(x * x for x in v) / len(v)) ** 0.5
    hot = [k for k in range(len(res)) if rms(k) > 4 * floor]
    assert hot == [2, 5, 8]


def test_a_rig_that_compresses_smoothly_is_not_odd():
    """Everything a rig does to the step it does gradually, because
    every mechanism here is monotone in level. Only what is NOT the
    rig should stand out."""
    got = _ladder()
    for j, r in enumerate(got):
        for i in range(0, 60):                    # bass giving way
            r["mag_db"][i] -= 0.35 * j * j / 10.0
    res, floor = level_run.odd_rung_out(got)
    for row in res:
        v = [x for x in row if x is not None]
        assert (sum(x * x for x in v) / len(v)) ** 0.5 < 8 * max(floor,
                                                                0.02)


def test_too_few_rungs_says_nothing():
    res, floor = level_run.odd_rung_out(_ladder(k=4))
    assert res == [] and floor == 0.0


# --- rebuilding from a chosen rung ------------------------------------

def test_rolling_back_drops_everything_above():
    """A map is a STACK. Each rung is read against the ones below it
    and the step to the next comes from the last pair, so a rung
    pulled from the middle leaves a hole that can only be refilled at
    a level nobody recorded, in a seating nobody checked."""
    got = [{"level": 0.1}, {"level": 0.2}, {"level": 0.3},
           {"level": 0.4}]
    assert [r["level"] for r in level_run.rolled_back(got, 2)] == \
        [0.1, 0.2]
    assert level_run.rolled_back(got, 0) == []
    assert len(level_run.rolled_back(got, 99)) == 4
    assert level_run.rolled_back(None, 3) == []


def test_rolling_back_sorts_by_level_not_by_arrival():
    got = [{"level": 0.3}, {"level": 0.1}, {"level": 0.2}]
    assert [r["level"] for r in level_run.rolled_back(got, 2)] == \
        [0.1, 0.2]


class _Got:
    def __init__(self, mag):
        self.mag_db = mag
        self.noise_dbfs = -80.0
        self.signal_dbfs = 0.0


def _rig(played, head_db=30.0, start=0.4, bump=0.0):
    def play(back, name, sink, source, wav, duration, channels,
             sweep, fr, analyze, v, play_map):
        played.append(round(float(v), 4))
        db = 60.0 * math.log10(v / start)
        return None, -head_db + db, False, _Got(
            np.full(len(fr), db + bump))
    return play


def _fake_backend(monkeypatch, play):
    class Back:
        def moratorium_begin(self, *a, **k):
            pass

        def moratorium_end(self, *a, **k):
            pass
    monkeypatch.setattr(level_run.pw_backend, "backend", lambda: Back())
    monkeypatch.setattr(level_run, "_play_rung", play)


def test_a_rebuild_keeps_what_was_below_and_climbs_from_it(monkeypatch):
    freqs = np.array([100.0, 1000.0, 10000.0])
    played = []
    _fake_backend(monkeypatch, _rig(played))
    first = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                                   sink_name="x", freqs=freqs,
                                   max_rungs=4)
    kept = level_run.rolled_back(first, 2)
    played.clear()
    got = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                                 sink_name="x", freqs=freqs,
                                 max_rungs=4, have=kept)
    assert [r["level"] for r in got[:2]] == [r["level"] for r in kept]
    assert len(got) > len(kept)
    assert min(played) >= min(r["level"] for r in kept)


def test_a_rig_that_moved_refuses_to_be_built_on(monkeypatch):
    """Rungs from two seatings look exactly like a rig that gave out,
    and a map's rungs are never repeated, so nothing downstream could
    notice. The kept top is played again and has to agree."""
    freqs = np.array([100.0, 1000.0, 10000.0])
    played = []
    _fake_backend(monkeypatch, _rig(played))
    first = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                                   sink_name="x", freqs=freqs,
                                   max_rungs=3)
    assert first[0]["scatter_db"] is not None
    _fake_backend(monkeypatch, _rig(played, bump=9.0))
    with pytest.raises(level_run.SeatingChanged):
        level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                               sink_name="x", freqs=freqs,
                               max_rungs=6,
                               have=level_run.rolled_back(first, 1))


def test_a_kept_rung_with_no_scatter_is_built_on_without_a_check(
        monkeypatch):
    freqs = np.array([100.0, 1000.0, 10000.0])
    played = []
    _fake_backend(monkeypatch, _rig(played))
    first = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                                   sink_name="x", freqs=freqs,
                                   max_rungs=3)
    old = [dict(r, scatter_db=None) for r in first[:1]]
    _fake_backend(monkeypatch, _rig(played, bump=9.0))
    got = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                                 sink_name="x", freqs=freqs,
                                 max_rungs=6, have=old)
    assert len(got) > len(old)


def test_the_odd_rung_reading_is_the_same_fit_column_by_column():
    """It fits every frequency at once now, from one Vandermonde,
    because one fit per bin cost 224 ms on an eleven rung walk and
    the pointer asks for a repaint on every motion event. The answer
    has to be the answer the slow way gave."""
    got = _ladder(k=9, n=60)
    for i in range(10, 25):
        got[4]["mag_db"][i] += 2.0
    res, floor = level_run.odd_rung_out(got)

    # the same thing, one column at a time
    import numpy as _np
    cols = [r["mag_db"] for r in got]
    ks = _np.arange(len(got), dtype=float)
    slow = []
    for k in range(len(got)):
        slow.append([None] * len(cols[0]))
    for i in range(len(cols[0])):
        y = _np.array([c[i] for c in cols], float)
        c = _np.polyfit(ks, y, 2)
        drop = int(_np.argmax(_np.abs(y - _np.polyval(c, ks))))
        m = _np.ones(len(got), bool)
        m[drop] = False
        c = _np.polyfit(ks[m], y[m], 2)
        for k in range(len(got)):
            slow[k][i] = float(y[k] - _np.polyval(c, k))
    for k in range(len(got)):
        for i in range(len(cols[0])):
            assert abs(res[k][i] - slow[k][i]) < 1e-6


# --- finished or interrupted ------------------------------------------

def test_a_walk_that_ended_on_its_own_terms_is_finished():
    """Eleven rungs of a walk that ran out of capture and eleven of a
    walk somebody stopped look exactly alike on the picture, and only
    the second is missing the rungs that would have answered the
    question the map exists for."""
    for what in ("capture", "knob", "rungs"):
        got = [{"level": 0.2}, {"level": 0.4, "stopped_by": what}]
        assert level_run.map_state(got) == (True, what)


def test_a_walk_somebody_stopped_is_not():
    got = [{"level": 0.2}, {"level": 0.4, "stopped_by": "asked"}]
    assert level_run.map_state(got) == (False, "asked")


def test_the_word_is_read_off_the_loudest_rung():
    """Order on disk is not guaranteed, and the walk puts the word on
    the rung it stopped at, which is the loudest one."""
    a = [{"level": 0.4, "stopped_by": "capture"}, {"level": 0.2}]
    b = [{"level": 0.2}, {"level": 0.4, "stopped_by": "capture"}]
    assert level_run.map_state(a) == level_run.map_state(b) == \
        (True, "capture")
    # a word on a quieter rung is not the end of the walk
    c = [{"level": 0.2, "stopped_by": "capture"}, {"level": 0.4}]
    assert level_run.map_state(c) == (False, None)


def test_a_map_with_no_word_claims_nothing():
    """Maps walked before the reason was recorded carry none. What is
    not known is not claimed, so they read as unfinished."""
    assert level_run.map_state([{"level": 0.2}, {"level": 0.4}]) == \
        (False, None)
    assert level_run.map_state([]) == (False, None)
    assert level_run.map_state(None) == (False, None)


def test_a_walk_records_why_it_stopped(monkeypatch):
    freqs = np.array([100.0, 1000.0, 10000.0])
    played = []
    _fake_backend(monkeypatch, _rig(played, head_db=6.0))
    got = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                                 sink_name="x", freqs=freqs,
                                 max_rungs=8)
    done, what = level_run.map_state(got)
    assert done and what == "capture"


# --- the level every channel still follows -----------------------------

def _rungs(levels, loss_from=None, n=200, ppo=96, A=0.05, floor=0.1):
    """A ladder whose steps arrive in full, or fall short from a given
    rung upward.

    Its base carries a scatter, because that is what a map is read
    against now: the response slopes across the band so the base's own
    SNR spans it, which is what lets one rung fit the model.
    """
    out = []
    slope = [45.0 - 90.0 * i / (n - 1) for i in range(n)]
    for j, lv in enumerate(levels):
        mag = [j * 4.0 + sl for sl in slope]
        if loss_from is not None and j >= loss_from:
            for i in range(n // 3):        # the bottom third gives way
                mag[i] -= 3.0 * (j - loss_from + 1)
        r = {"level": lv, "peak_dbfs": -40.0 + j * 4.0,
             "heard_offset_db": -40.0, "mag_db": mag,
             "stopped_by": "capture" if j == len(levels) - 1 else None}
        if j == 0:
            r["scatter_db"] = [
                math.sqrt((A * 10.0 ** (-(m + 40.0) / 20.0)) ** 2
                          + floor ** 2) for m in mag]
        out.append(r)
    return out


def test_a_rig_that_follows_all_the_way_tops_out_at_its_last_rung():
    got = _rungs([0.10, 0.16, 0.25, 0.40])
    assert level_run.linear_top(got) == 0.40


def test_a_rig_that_gives_way_tops_out_below_it():
    """The step it did not deliver is where following stopped."""
    got = _rungs([0.10, 0.16, 0.25, 0.40], loss_from=2)
    assert level_run.linear_top(got) == 0.16


def test_the_working_level_is_set_by_the_weaker_channel():
    """One sink, one knob, so the takes of one canvas share a level:
    it is the quietest of the tops, and a rig whose sides differ is
    decided by its weaker one."""
    maps = {"FL": _rungs([0.10, 0.16, 0.25, 0.40]),
            "FR": _rungs([0.10, 0.16, 0.25, 0.40], loss_from=2)}
    v, who = level_run.working_level(maps)
    assert (v, who) == (0.16, "FR")


def test_an_unfinished_map_does_not_set_a_level():
    """A walk somebody stopped is missing the rungs that would have
    said where following ends."""
    got = _rungs([0.10, 0.16, 0.25, 0.40])
    got[-1]["stopped_by"] = "asked"
    assert level_run.working_level({"FL": got}) == (None, None)


def test_no_map_names_no_level():
    assert level_run.working_level({}) == (None, None)
    assert level_run.working_level(None) == (None, None)


def test_the_step_is_read_from_the_capture_peak_not_the_knob():
    """A knob decibel is not a decibel: his JBL answered 8 and 11 dB
    to a 4 dB ask, and a fictitious ask makes a fictitious verdict."""
    got = _rungs([0.10, 0.16, 0.25, 0.40])
    for r in got:                       # the knob lies, the peak does not
        r["level"] = 0.10 + 0.001 * got.index(r)
    assert level_run.linear_top(got) == got[-1]["level"]


def test_a_step_the_device_did_not_take_is_not_a_failure():
    """His Liberty 5 answers Bluetooth's own scale in jumps: rungs
    arrived at -24.1, -20.0, -20.1, -16.0, -16.0 dBFS, so every other
    one landed where the rung below it already was. Judged pair by
    pair the second of each such pair delivers nothing and reads as
    the rig giving out, and a rig that followed to the top of its walk
    came back with a working level of 41% instead of 75%."""
    peaks = [-24.06, -19.96, -20.10, -15.97, -16.01, -11.90,
             -7.92, -7.94, -4.97, -2.89]
    lv = [0.3786, 0.4088, 0.4414, 0.4766, 0.5146, 0.5557, 0.6,
          0.6479, 0.6995, 0.7554]
    got = []
    for j, (v, pk) in enumerate(zip(lv, peaks)):
        n = 200
        slope = [45.0 - 90.0 * i / (n - 1) for i in range(n)]
        r = {"level": v, "peak_dbfs": pk, "heard_offset_db": -40.0,
             "mag_db": [pk - peaks[0] + sl for sl in slope],
             "stopped_by": "capture" if j == len(lv) - 1 else None}
        if j == 0:
            r["scatter_db"] = [
                math.sqrt((0.05 * 10.0 ** (-(m + 40.0) / 20.0)) ** 2
                          + 0.1 ** 2) for m in r["mag_db"]]
        got.append(r)
    assert level_run.linear_top(got) == 0.7554


def test_the_reference_stays_put_until_the_asking_adds_up():
    """Under MIN_READABLE_STEP nothing is asked, so nothing can be
    answered: the rung is carried and the comparison waits."""
    got = _rungs([0.10, 0.11, 0.16, 0.25])
    for r, pk in zip(got, (-40.0, -39.9, -36.0, -32.0)):
        r["peak_dbfs"] = pk
    assert level_run.linear_top(got) == 0.25


def test_a_walk_hands_over_its_rungs_as_it_takes_them(monkeypatch):
    """A map used to appear all at once when the walk ended, so a hand
    watching a rig climb had a blank canvas for a minute -- and the
    whole point of drawing rungs is to see a bad one while there is
    still a walk to stop."""
    freqs = np.array([100.0, 1000.0, 10000.0])
    played, seen = [], []
    _fake_backend(monkeypatch, _rig(played))
    got = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                                 sink_name="x", freqs=freqs,
                                 max_rungs=4,
                                 on_step=lambda rs: seen.append(len(rs)))
    # one handover per rung, each carrying every rung so far
    assert seen == list(range(1, len(got) + 1))


def test_the_handover_carries_a_copy(monkeypatch):
    """The list handed over must not be the walk's own, or a caller
    holding it would watch its rungs change under it."""
    freqs = np.array([100.0, 1000.0, 10000.0])
    played, kept = [], []
    _fake_backend(monkeypatch, _rig(played))
    level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                           sink_name="x", freqs=freqs, max_rungs=3,
                           on_step=kept.append)
    assert [len(x) for x in kept] == list(range(1, len(kept) + 1))


def test_a_base_sweep_that_lost_a_whole_level_is_not_kept(monkeypatch):
    """One walk came back with 26 dB between the two sweeps of its
    base, its correction still engaged for the first. Measured, two
    sweeps of a rung differ by tenths of a decibel."""
    freqs = np.array([100.0, 1000.0, 10000.0])
    played = []
    calls = {"n": 0}

    def rig(back, name, sink, source, wav, duration, channels,
            sweep, fr, analyze, v, play_map):
        played.append(round(float(v), 4))
        calls["n"] += 1
        db = 60.0 * math.log10(v / 0.4)
        # the FIRST sweep of all is 26 dB down, as his was
        drop = 26.0 if calls["n"] == 1 else 0.0
        return None, -30.0 + db - drop, False, _Got(
            np.full(len(fr), db - drop))
    _fake_backend(monkeypatch, rig)
    rungs = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                                   sink_name="x", freqs=freqs,
                                   max_rungs=3)
    base = rungs[0]
    # the good sweep is the one kept, so the base is NOT 26 dB down
    assert base["mag_db"][0] > -1.0
    # and its scatter is the honest zero, not 26
    assert max(base["scatter_db"]) < 1.0


def test_two_agreeing_base_sweeps_are_left_alone(monkeypatch):
    freqs = np.array([100.0, 1000.0, 10000.0])
    played = []
    _fake_backend(monkeypatch, _rig(played))
    rungs = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                                   sink_name="x", freqs=freqs,
                                   max_rungs=3)
    assert rungs[0]["scatter_db"] is not None
    assert max(rungs[0]["scatter_db"]) < 0.001


# --- what a bin's reading is worth, measured rather than assumed ------

def _scatter_rung(A, floor, n=400, lo_snr=-15.0, hi_snr=60.0, seed=5):
    """A rung whose two sweeps disagree the way the model says."""
    rng = random.Random(seed)
    snr = [lo_snr + (hi_snr - lo_snr) * i / (n - 1) for i in range(n)]
    off = -70.0
    return {"heard_offset_db": off,
            "mag_db": [off + s for s in snr],
            "scatter_db": [
                math.sqrt((A * 10.0 ** (-s / 20.0)) ** 2 + floor ** 2)
                * math.exp(rng.gauss(0, 0.15)) for s in snr]}


def test_the_model_is_recovered_from_one_rung():
    """One rung is enough because its own SNR spans sixty decibels
    across frequency; the base of a walk is the quietest there is,
    which is why it is the one played twice."""
    A, F = level_run.scatter_model(_scatter_rung(7.0, 0.10))
    assert 4.0 < A < 12.0
    assert 0.05 < F < 0.20


def test_a_coupler_fits_almost_no_noise_term():
    """On a coupler the error of a rung-to-rung difference does not
    grow down to an SNR of minus fifteen: a deconvolution spreads
    stationary noise and concentrates the sweep."""
    A, F = level_run.scatter_model(_scatter_rung(0.05, 0.10))
    assert A < 1.0


def test_a_rung_that_never_saw_a_low_snr_gets_no_model():
    """A is what the scatter does as the signal falls away, so a curve
    that never goes there cannot constrain it -- and an unconstrained
    A near zero throws the gate wide open."""
    assert level_run.scatter_model(
        _scatter_rung(7.0, 0.10, lo_snr=25.0)) is None


def test_an_old_profile_keeps_the_rule_it_was_walked_under():
    """Scatter used to be kept only where the base was heard, so those
    profiles have no quiet end by construction."""
    r = _scatter_rung(7.0, 0.10)
    r["scatter_db"] = [None if s < 10 else v
                       for s, v in zip(
                           [m - r["heard_offset_db"] for m in r["mag_db"]],
                           r["scatter_db"])]
    assert level_run.scatter_model(r) is None


def test_the_gate_follows_the_scatter_not_the_ratio():
    """Same bins, same SNR, two different evenings: the coupler's are
    worth reading and the room's are not."""
    n = 60
    prev = np.zeros(n)
    cur = np.full(n, 4.0)
    heard = np.full(n, 2.0)               # two decibels over the noise
    quiet = level_run.expected_scatter((0.05, 0.10), heard)
    loud = level_run.expected_scatter((7.0, 0.10), heard)
    _s1, ok1 = level_run.shortfall(prev, cur, heard, 4.0, None, 96.0,
                                   scatter=quiet)
    _s2, ok2 = level_run.shortfall(prev, cur, heard, 4.0, None, 96.0,
                                   scatter=loud)
    assert ok1.all() and not ok2.any()


def test_the_descent_is_coarse_and_the_climb_is_fine(monkeypatch):
    """The rungs below the search's level are there to reach where a
    listener plays and to give the reading a quiet reference; neither
    wants resolution. Thinning his own two ladders from 2 dB to 6
    below that level left every channel on the level it had."""
    freqs = np.array([100.0, 1000.0, 10000.0])
    played = []
    _fake_backend(monkeypatch, _rig(played, head_db=40.0, start=0.2))
    level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.2,
                           sink_name="x", freqs=freqs, max_rungs=8,
                           fine_from=0.4)
    pairs = [(a, round(60 * math.log10(b / a), 1))
             for a, b in zip(played, played[1:]) if b > a]
    below = [d for a, d in pairs if a < 0.4]
    above = [d for a, d in pairs if a >= 0.4]
    assert below and all(d >= level_run.MAP_DOWN_STEP_DB - 0.1
                         for d in below)
    assert above and all(d <= level_run.MAP_STEP_DB + 0.1
                         for d in above)


def test_without_a_mark_every_step_is_the_fine_one(monkeypatch):
    freqs = np.array([100.0, 1000.0, 10000.0])
    played = []
    _fake_backend(monkeypatch, _rig(played, head_db=40.0, start=0.2))
    level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.2,
                           sink_name="x", freqs=freqs, max_rungs=6)
    steps = [round(60 * math.log10(b / a), 1)
             for a, b in zip(played, played[1:]) if b > a]
    assert steps and all(d <= level_run.MAP_STEP_DB + 0.1
                         for d in steps)


def test_an_uneven_ladder_does_not_flag_its_own_handover():
    """A ladder is smooth in LEVEL, and fitting it against rung number
    assumes even spacing. His descent went coarse -- 12.2, 15.4, 19.4
    then 20.9, 22.6, 24.4, 26.4, six decibels apart and then two -- and
    the curve that is smooth in level is kinked in index. The kink
    lands on the handover rung, and it was flagged on every walk."""
    got = _ladder(k=7, steps=[6.0, 6.0, 2.0, 2.0, 2.0, 2.0])
    res, floor = level_run.odd_rung_out(got)
    for k in range(len(got)):
        v = [x for x in res[k] if x is not None]
        rms = (sum(x * x for x in v) / len(v)) ** 0.5
        assert rms < max(0.15, 4 * floor), "rung %d flagged" % k


def test_a_rung_with_no_peak_leaves_the_reading_unmade():
    """The peak IS the axis, so a ladder that does not record it
    cannot be read this way at all -- better than reading it against
    a made-up one."""
    got = _ladder(k=7)
    got[3]["peak_dbfs"] = None
    assert level_run.odd_rung_out(got) == ([], 0.0)


def test_a_rebuild_does_not_measure_the_rung_it_kept(monkeypatch):
    """The loop plays wherever v stands, and seeding v with the kept
    top made the walk measure that level a second time and append it:
    his rebuilt map came back with two rungs at 12%."""
    freqs = np.array([100.0, 1000.0, 10000.0])
    played = []
    _fake_backend(monkeypatch, _rig(played))
    first = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                                   sink_name="x", freqs=freqs,
                                   max_rungs=5)
    kept = level_run.rolled_back(first, 2)
    got = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2, 0.4,
                                 sink_name="x", freqs=freqs,
                                 max_rungs=5, have=kept)
    lv = [round(r["level"], 6) for r in got]
    assert len(lv) == len(set(lv)), "a level was measured twice: %s" % lv
    assert lv[:2] == [round(r["level"], 6) for r in kept]
