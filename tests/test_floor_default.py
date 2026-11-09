"""The floor is a correction: it changes a profile of the user's own.

On Default or a built-in profile the floor button wrote that profile in
place -- Default became a clean.json among the user's profiles, carrying
a floor that was drawn and, since 5.1.0, never played. It now makes the
copy a band edit makes, and the copy is a step of the timeline. An undo
that switches profiles takes the floor of the profile it lands on: the
editor writes its own floor into the profile on every save, and before
this an undo from a profile with a floor wrote that floor into the one
it returned to.
"""
import ast
import json
import os
import pathlib

import pytest

from perdeviceeq import profiles as P


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(P, "BINDINGS_FILE", str(tmp_path / "bindings.json"))
    monkeypatch.setattr(P, "USER_PROFILES_DIR", str(tmp_path / "profiles"))
    os.makedirs(tmp_path / "profiles", exist_ok=True)
    return tmp_path / "profiles"


def test_default_is_the_built_in_whatever_the_user_folder_holds(user_dir):
    (user_dir / "clean.json").write_text(json.dumps({
        "id": P.CLEAN_ID, "name": "Clean (no EQ)",
        "version": P.SCHEMA_VERSION, "channels": {}, "floor_hz": 40.0}))
    default = P.ProfileStore().get(P.CLEAN_ID)
    assert default.get("builtin")
    assert default.get("floor_hz") is None


# ---- the window --------------------------------------------------------------

def _gui():
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    return {n.name: n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef)}


def _first(fn, name, arg=None):
    """Line of the first call to `name` in fn, or None."""
    lines = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == name
             and (arg is None or [ast.unparse(a) for a in n.args] == [arg])]
    return min(lines) if lines else None


@pytest.mark.parametrize("name", ["_on_floor_toggled", "_on_protect_edges"])
def test_the_floor_makes_its_copy_before_it_writes(name):
    """The button and the handle under the graph."""
    fn = _gui()[name]
    fork = _first(fn, "self._fork_for_floor")
    assert fork is not None
    assert fork < _first(fn, "self._save_profile")


def test_a_floor_copy_is_a_step_of_the_timeline():
    fn = _gui()["_fork_for_floor"]
    assert _first(fn, "self._ensure_editable") is not None
    assert _first(fn, "self._schedule_save") is not None
    assert any(isinstance(n, ast.Attribute) and n.attr == "_dev_dirty"
               and isinstance(n.ctx, ast.Store) for n in ast.walk(fn))


def test_switching_the_profile_takes_its_floor():
    fns = _gui()
    loaded = {ast.unparse(t) for n in ast.walk(fns["_load_floor"])
              if isinstance(n, ast.Assign) for t in n.targets}
    assert {"self.floor_off", "self.floor_hz"} <= loaded
    assert _first(fns["_load_profile"], "self._load_floor") is not None
    restore = fns["_restore"]
    took = _first(restore, "self._load_floor")
    assert took is not None
    assert took < _first(restore, "self._save_profile",
                         "self._working_body()")
