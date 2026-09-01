"""The level search: its policy, and the field runs that shaped it."""

import math

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
    full = level_run.shortfall_db(prev, prev + 4.0, heard, 4.0,
                                  freqs, mc.GRID_PPO)
    assert np.nanmax(full) == 0.0
    # took none of it
    none = level_run.shortfall_db(prev, prev, heard, 4.0,
                                  freqs, mc.GRID_PPO)
    assert abs(float(np.nanmax(none)) - 4.0) < 1e-9
    # took half
    half = level_run.shortfall_db(prev, prev + 2.0, heard, 4.0,
                                  freqs, mc.GRID_PPO)
    assert abs(float(np.nanmax(half)) - 2.0) < 1e-9
    # and a frequency the rig does not reproduce is absent, not short
    quiet = np.full(len(freqs), 0.0)
    out = level_run.shortfall_db(prev, prev, quiet, 4.0,
                                 freqs, mc.GRID_PPO)
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


def test_the_map_strides_while_the_rig_answers():
    """Below the border every rung reads alike -- his Tanchjim gave
    eight identical rows of zeros over fourteen decibels -- so sweeps
    spent there buy nothing. The walk strides while the answer keeps
    coming and halves once it stops, which is where the sweeps are
    worth spending.

    And a level known to fall short is a CEILING: without that the
    walk halves down to a healthy rung, strides boldly again and
    sails over the very rung it had just found short."""
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
            db = 60.0 * math.log10(v / 0.5)
            mag = np.full(len(freqs), db)
            inb = (freqs >= 20.0) & (freqs <= 90.0)
            mag[inb] -= 2.0 * max(0.0, 60.0 * math.log10(v / border))
            return None, -30.0 + db, False, Got(mag)
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
        level_run._play_rung = rig(0.62)
        rungs = level_run.headroom_map({"name": "x"}, {"name": "y"}, 2,
                                       0.5, sink_name="x", freqs=freqs)
        # three sweeps, not eight, and the border is bracketed
        assert len(played) <= 4
        assert rungs[-1]["stopped_by"] == "border"
        # nothing was played above the level already known to be short
        assert max(played) <= 0.69
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
