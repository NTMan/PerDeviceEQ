"""The taste and the preamp ride belong to the listener, and every device's
graph carries them. A change made in the window used to reach only the
device in view; the others kept the old taste or ride in the hook until
the window was opened on each of them. Now a change publishes every bound
device again, once the edits settle.
"""
import ast
import os
import pathlib

import pytest

from perdeviceeq import preferences
from perdeviceeq import profiles as P


CUT = {"type": "PK", "freq": 1000, "gain": -3.0, "q": 1.0, "enabled": True}
DISK = {"type": "LSC", "freq": 50, "gain": 12.0, "q": 1.0, "enabled": True}
HELD = {"type": "LSC", "freq": 80, "gain": 6.0, "q": 1.0, "enabled": True}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "BINDINGS_FILE", str(tmp_path / "bindings.json"))
    monkeypatch.setattr(P, "USER_PROFILES_DIR", str(tmp_path / "profiles"))
    os.makedirs(tmp_path / "profiles", exist_ok=True)
    return P.ProfileStore()


def _bind(store, node):
    pid = store.save_user({"name": "Pair", "floor_off": True,
                           "ch_keys": ["FL", "FR"],
                           "channels": {"FL": {"bands": [CUT]},
                                        "FR": {"bands": [CUT]}}})
    store.set_binding(node, pid)
    store.set_map(node, {"FL": "FL", "FR": "FR"})


def test_the_layers_the_window_holds_win_over_the_disk(store):
    """The window republishes with what it holds right now; the disk may
    still have the state from before the edit."""
    layers = preferences.PreferenceLayers()
    layers.set_active(layers.upsert({"name": "Old", "bands": [DISK]}))
    _bind(store, "a#port")
    _bind(store, "b#port")
    wire = store.wire_state(([HELD], -4.0))
    for g in wire.values():
        assert "freq = 80" in g and "freq = 50" not in g
        assert "gain = -4" in g
    assert "freq = 50" in store.wire_state()["a#port"]


# ---- the window wires its changes to that ----------------------------------

def _gui():
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    return {n.name: n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef)}


def _calls(fn):
    return {ast.unparse(n.func) for n in ast.walk(fn)
            if isinstance(n, ast.Call)}


@pytest.mark.parametrize("name", ["_save_preamp_state", "_taste_refresh",
                                  "_on_taste_view_changed"])
def test_a_listener_change_reaches_every_device(name):
    assert "self._listener_changed" in _calls(_gui()[name])


def test_the_republish_is_the_feed_s_answer_without_the_device_in_view():
    fn = _gui()["_republish_others"]
    calls = _calls(fn)
    assert "self.store.wire_state" in calls
    assert "wire.pop" in calls
    assert not any(c.endswith("profile_graph") or c.endswith("device_graph")
                   for c in calls)


def test_undo_of_a_taste_or_a_ride_is_written_and_republished():
    calls = _calls(_gui()["_restore"])
    assert "self._listener_state" in calls
    assert "self._save_preamp_state" in calls
