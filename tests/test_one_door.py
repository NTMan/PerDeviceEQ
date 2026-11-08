"""What a control writes goes through the one door, and only the door
paces it.

The window paced writes of its own: the playback fader every 50 ms,
on a thread of its own per write and around the door altogether --
in no order, and unseen by a measurement's moratorium.
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
