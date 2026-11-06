"""Deleting a profile tells the hook about every device that wore it.

The store drops their bindings, and a device with no binding plays the
common entry. The hook keeps a device's last value until that key is
written again, and no later pass writes a key that has no binding, so a
device not in view went on playing the deleted profile until the window
was opened on it.
"""
import ast
import os
import pathlib

import pytest

from perdeviceeq import profiles as P


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(P, "BINDINGS_FILE", str(tmp_path / "bindings.json"))
    monkeypatch.setattr(P, "USER_PROFILES_DIR", str(tmp_path / "profiles"))
    os.makedirs(tmp_path / "profiles", exist_ok=True)
    return P.ProfileStore()


def _user(store, name):
    return store.save_user({"name": name, "channels": {}})


def test_deleting_returns_every_device_it_unbinds(store):
    gone, kept = _user(store, "Gone"), _user(store, "Kept")
    store.set_binding("a#port", gone)
    store.set_binding("b#port", gone)
    store.set_binding("c#port", kept)
    assert sorted(store.delete_user(gone)) == ["a#port", "b#port"]
    assert store.bindings == {"c#port": kept}
    assert P.ProfileStore().bindings == {"c#port": kept}


def test_a_profile_nobody_wears_unbinds_nobody(store):
    assert store.delete_user(_user(store, "Alone")) == []


def test_a_built_in_is_not_deleted(store):
    assert store.delete_user(P.CLEAN_ID) == []
    assert store.has(P.CLEAN_ID)


# ---- the window hands them to the hook ---------------------------------------

def _gui():
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    return {n.name: n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef)}


def _calls(fn):
    return {ast.unparse(n.func) for n in ast.walk(fn)
            if isinstance(n, ast.Call)}


@pytest.mark.parametrize("name", ["_confirm_delete", "_undo"])
def test_every_deletion_is_handed_to_the_hook(name):
    """The Remove button, and an undo that buries a profile it made:
    what delete_user returns goes straight to the hook."""
    fn = _gui()[name]
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    deletes = [n for n in calls
               if ast.unparse(n.func) == "self.store.delete_user"]
    handed = [a for n in calls
              if ast.unparse(n.func) == "self._publish_unbound"
              for a in n.args]
    assert deletes
    assert all(any(d is a for a in handed) for d in deletes)


def test_the_unbound_follow_the_common_entry():
    calls = _calls(_gui()["_publish_unbound"])
    assert "auth.clear_graph" in calls
    assert not any(c.endswith("publish_graph") for c in calls)
