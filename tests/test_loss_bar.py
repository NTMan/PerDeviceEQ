"""The main window's loss reading: what it paints and what it does
not. Needs gi to import the window; without it the module skips and
CI with xvfb judges."""

import types

import numpy as np
import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from perdeviceeq import gui, level_run, measure_core as mc  # noqa: E402


def _window(rungs):
    prof = {"measurement": {"grid": {"f_lo": 20.0, "f_hi": 20000.0,
                                     "ppo": 96}, "takes": []},
            "passport": {"FL": {"rungs": rungs}}}
    return types.SimpleNamespace(
        store=types.SimpleNamespace(get=lambda pid: prof),
        current_pid="p", _loss_cache=None,
        _spread=lambda ppo, n: np.full(n, 0.02))


def test_a_loss_under_half_a_readable_step_paints_nothing():
    """His TWS at their working level: the peak rises a hair more
    than the curve on every rig, a few tenths across the band, and
    the curve went red and renamed itself "what you hear" over a line
    that had not visibly moved -- while the advice, the strip and the
    cubes waited for half a readable step. One bar now: the tenths
    paint nothing, a real loss stands at its size."""
    freqs = np.asarray(mc.log_grid())
    n = len(freqs)
    base = {"level": 0.12, "peak_dbfs": -30.0, "mag_db": [0.0] * n,
            "scatter_db": [0.02] * n, "heard_offset_db": -50.0}
    # the peak rose 20.3 dB, the curve 20.0 everywhere -- three
    # tenths short across the band -- and 3 dB more below 100 Hz
    top = {"level": 0.26, "peak_dbfs": -9.7,
           "mag_db": [20.0 - (3.0 if f < 100.0 else 0.0) for f in freqs],
           "heard_offset_db": -50.0}
    got = gui.EqWindow._loss_prepared(_window([base, top]))
    fq, prepared = got
    (_level, d), = prepared[0]
    assert float(np.max(d[fq > 200.0])) == 0.0
    assert 2.5 < float(np.median(d[fq < 80.0])) < 3.5
    assert level_run.WORTH_A_LINE_DB == 0.5 * level_run.MIN_READABLE_STEP
