"""What a control writes goes through the one door, and only the door
paces it.

The window paced writes of its own: the playback fader every 50 ms,
on a thread of its own per write and around the door altogether --
in no order, and unseen by a measurement's moratorium -- and the
floor's handle once per 30 ms in the strip and once per 120 ms in
the window, so the last place the hand stopped at could go unheard
until the release.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "perdeviceeq"


def _fns(module):
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)}


def _calls(fn):
    return {ast.unparse(c.func) for c in ast.walk(fn)
            if isinstance(c, ast.Call)}


def test_the_fader_wishes_through_the_door():
    calls = _calls(_fns("gui.py")["_level_dragged"])
    assert "auth.set_volume" in calls
    assert "pw_backend.in_thread" not in calls
    assert "pw_backend.set_sink_volume" not in calls


def test_the_fader_hands_over_every_level_it_shows():
    fns = _fns("level_strip.py")
    assert "self._move" in _calls(fns["_begin"])
    assert "self._move" in _calls(fns["_update"])
    assert "time.monotonic" not in _calls(fns["_update"])
    assert "self._cb" in _calls(fns["_move"])
    assert "self._cb" not in _calls(fns["_end"])


def test_the_floor_hands_over_every_move_and_each_is_published():
    strip = _calls(_fns("peq_view.py")["_protect_drag_update"])
    assert "self._protect_cb" in strip
    assert "time.monotonic" not in strip
    window = _calls(_fns("gui.py")["_on_protect_edges"])
    assert "self._apply_now" in window
    assert "time.monotonic" not in window
