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
    drawn = []
    win._ladder_repaint = lambda: drawn.append(1)
    _done(win, {"error": None, "outcome": None, "level": None,
                "found": None, "word": None})
    assert not win._walk_live
    assert drawn, "the canvas was never asked to repaint"


# ---- the walk's rungs stay on the canvas until the walk ends -------

def test_the_walk_keeps_its_rungs_until_it_ends(monkeypatch):
    """His screenshot: the moment the last rung landed, the map said
    "no level map for this channel yet", the passport canvas lost
    every rung it had just drawn, and a second later everything was
    back. _walk_map emptied the walk's live rungs on the worker while
    _walk_live still said a walk was on, so a frame drawn in that
    second read a walk with no rungs. The rungs stay with the walk;
    _measure_done hands the canvas back to the disk, on the thread
    that draws."""
    win = _win()
    monkeypatch.setattr(mw, "GLib", _Idle)
    rungs = [{"level": 0.12, "mag_db": [0.0]},
             {"level": 0.15, "mag_db": [0.0]}]

    def walk(*a, **k):
        k["on_step"](list(rungs))        # handed over as it is taken
        return list(rungs)
    monkeypatch.setattr(level_run, "headroom_map", walk)
    monkeypatch.setattr(level_run, "working_level",
                        lambda *a, **k: (0.15, None))
    win._map_live = lambda r: setattr(win, "_map_partial", list(r))
    win._walk_live = True
    mw.MeasureWindow._walk_map(win, 0, lambda *a: None, 0.12, None)
    # the worker has returned and the walk is still on: a frame drawn
    # now sees the rungs it saw a moment ago
    assert win._walk_live and win._map_partial == rungs
    _done(win, {"error": None, "outcome": None, "level": 0.15,
                "found": None, "word": "FL: level 15%"})
    assert not win._walk_live and win._map_partial == []


# ---- a search resumed from a step ------------------------------------

def test_a_resumed_search_keeps_the_steps_it_replayed(monkeypatch):
    """A rebuild chosen on a probe row resumes the search from that
    step: the steps before it go to the controller as replay, not to
    the room, and the record written afterwards holds them first and
    the played ones after -- one search, in the order it stands."""
    win = _win()
    monkeypatch.setattr(mw, "GLib", _Idle)
    seen = {}

    def hunt(*a, **k):
        seen.update(k)
        p = level_run.Probe(volume=0.19, peak_dbfs=-10.0, step=3,
                            verdict="ok", phase="closing",
                            mag_db=[0.0], floor_db=[-60.0])
        return 0.19, [p]
    monkeypatch.setattr(level_run, "hunt", hunt)
    win._walk_map = lambda *a, **k: (0.19, None)
    win._post_status = lambda *a: None
    win._probe_judged = lambda p: False
    win._ladder_announce = lambda *a: False
    win._walk_keep = None
    win._walk_replay = [{"step": 1, "level": 0.15, "peak_dbfs": -17.0},
                        {"step": 2, "level": 0.17, "peak_dbfs": -14.0}]
    mw.MeasureWindow._hunt_level(win, 0)
    assert seen["replay"] == win._walk_replay
    assert [p["step"] for p in win._walk_probes] == [1, 2, 3]
    assert win._walk_settled == 0.19
