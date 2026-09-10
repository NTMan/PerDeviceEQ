"""The run's last word reaches the line.

A rebuild that refused looked exactly like a button that did
nothing: the seating sweep played and the window went back to
"Ready" with no sentence anywhere, because the report and the
pult's state share one line and the state was always posted last.
These run the REAL methods unbound over a stub self. They need gi
(measure_window imports GTK at the top), so a GTK-less sandbox
skips and CI with xvfb judges.
"""

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:      # gi without the typelibs
    pytest.skip(str(e), allow_module_level=True)

from perdeviceeq import level_run, measure_window as mw  # noqa: E402


class _Obj:
    pass


class _Idle:
    """GLib.idle_add with no main loop: run it now, so a court sees
    what a frame would have seen."""

    @staticmethod
    def idle_add(fn, *a):
        fn(*a)
        return 0


def _cfg():
    c = _Obj()
    c.channels = 2
    c.pre_silence = 1.0
    c.post_silence = 0.5
    c.ppo = 96
    return c


def _session():
    s = _Obj()
    s.sink = {"name": "sink"}
    s.source = {"name": "source"}
    s.cfg = _cfg()
    s.sink_ident = {"name": "sink"}
    s.sweep = None
    s.freqs = None
    s._channel_map = lambda ch: None
    s.set_headroom = lambda *a: None
    s.set_level = lambda *a, **k: None
    s._v_cur = None
    return s


def _win():
    w = _Obj()
    w.ch_keys = ["FL", "FR"]
    w.mic_of = {0: 0, 1: 0}
    w.session = _session()
    w.said = []
    w._say = w.said.append
    w._walk_why = None
    w._ladder_repaint = lambda: False
    w._walk_settled = None
    w._walk_probes = []
    w._stop_asked = False
    w._level_only = True
    w._busy = True
    w._map_pick = None
    w._map_partial = []
    w._map_live = lambda *a: None
    w._store_headroom = lambda *a: None
    w._sync_relevel = lambda: None
    w._set_row_sensitive = lambda on: None
    w._update_pult = lambda: None
    w._refresh_all = lambda: None
    w._error = lambda *a: None
    w._commit_live_take = lambda *a: None
    w._source_name = lambda: None
    w.memory = _Obj()
    w.memory.remember = lambda *a, **k: None
    w.sink_node = "sink"
    w.drawn = []
    w.map_area = _Obj()
    w.map_area.queue_draw = lambda: w.drawn.append(1)
    w._assert_entry_route = lambda: None
    w._assert_capture_gain = lambda: None
    return w


def _done(win, result):
    mw.MeasureWindow._measure_done(win, 0, result)
    return win.said[-1]


# ---- the line at the end of a run ---------------------------------

def test_the_word_the_run_ended_with_is_the_word_on_the_line():
    win = _win()
    assert _done(win, {"error": None, "outcome": None, "level": 0.24,
                       "found": None, "word": "FL: level 24%"}) \
        == "FL: level 24%"


def test_ready_only_when_the_run_has_nothing_to_report():
    win = _win()
    assert _done(win, {"error": None, "outcome": None, "level": None,
                       "found": None, "word": None}) == "Ready"


# ---- the walk's two refusals --------------------------------------

def _refuse(monkeypatch, exc):
    win = _win()
    monkeypatch.setattr(mw, "GLib", _Idle)

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(level_run, "headroom_map", boom)
    stored = []
    win._store_headroom = lambda *a: stored.append(a)
    got = mw.MeasureWindow._walk_map(win, 0, lambda *a: None, 0.12, [])
    return win, got, stored


def test_a_seating_that_moved_leaves_its_reason(monkeypatch):
    win, got, stored = _refuse(
        monkeypatch, level_run.SeatingChanged("the rig is not sitting "
                                              "where they were measured"))
    assert got == (None, None)
    assert not stored
    assert "not sitting" in (win._walk_why or "")


def test_a_walk_that_threw_leaves_its_reason(monkeypatch):
    win, got, stored = _refuse(monkeypatch, RuntimeError("wpctl failed"))
    assert got == (None, None)
    assert not stored
    assert "wpctl failed" in (win._walk_why or "")


# ---- and the two halves together, which is his case ---------------

def test_a_refusal_survives_to_the_line(monkeypatch):
    """The whole path: the walk refuses, the worker composes, the
    line keeps it. This is what read as silence -- the reason was
    posted one idle callback before "Ready" and never rendered."""
    win = _win()
    monkeypatch.setattr(mw, "GLib", _Idle)
    seen = {}
    win._measure_done = lambda ch, result: seen.update(result)

    def hunt(ch):
        win._walk_why = "the level map could not be walked: wpctl failed"
        return None, None
    win._hunt_level = hunt

    mw.MeasureWindow._measure_worker(win, 0)
    assert _done(_win(), dict(seen, error=None)) == (
        "FL: search stopped -- the level is unchanged  "
        "(the level map could not be walked: wpctl failed)")


def test_a_run_that_walked_cleanly_says_its_level(monkeypatch):
    win = _win()
    monkeypatch.setattr(mw, "GLib", _Idle)
    seen = {}
    win._measure_done = lambda ch, result: seen.update(result)
    win._hunt_level = lambda ch: (0.24, None)

    mw.MeasureWindow._measure_worker(win, 0)
    assert _done(_win(), dict(seen, error=None)) == "FL: level 24%"


def test_the_end_of_a_walk_asks_for_the_frame():
    """The rungs move back from the walk's own stack to what is on
    disk here, and nothing told the canvas: the last live frame stood
    until a pointer or a resize forced a repaint, which is why the
    lines came back only when the mouse crossed them."""
    win = _win()
    _done(win, {"error": None, "outcome": None, "level": None,
                "found": None, "word": None})
    assert not win._walk_live
    assert win.drawn, "the canvas was never asked to repaint"
