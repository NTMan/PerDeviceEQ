"""The taste and the preamp ride belong to the listener, and every device
plays them. A change made in the window used to reach only the device in
view, then every bound device once the edits settled -- one write per
device, fourteen on his machine. Now one write reaches every device at
once: the listener's record, made on every move of the hand (hook
protocol 5).
"""
import ast
import os
import pathlib
import types

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
    return pid


def test_the_layers_the_window_holds_win_over_the_disk(store):
    """The window publishes with what it holds right now; the disk may
    still have the state from before the edit."""
    layers = preferences.PreferenceLayers()
    layers.set_active(layers.upsert({"name": "Old", "bands": [DISK]}))
    _bind(store, "a#port")
    rec = store.listener_value(([HELD], -4.0))
    assert "freq = 80" in rec and "freq = 50" not in rec
    assert "gain = -4" in rec
    assert "gain = -4" in store.device_value("a#port", ([HELD], -4.0))
    assert "freq = 50" in store.listener_value()


# ---- the window wires its changes to that ----------------------------------

def _gui():
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    return {n.name: n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef)}


def _calls(fn):
    return {ast.unparse(n.func) for n in ast.walk(fn)
            if isinstance(n, ast.Call)}


def _method(name, **names):
    """A window method run without GTK: its own source, with the names
    it reads handed in."""
    module = ast.Module(body=[_gui()[name]], type_ignores=[])
    exec(compile(module, "gui.py", "exec"), names)
    return names[name]


@pytest.mark.parametrize("name", ["_save_preamp_state", "_taste_refresh",
                                  "_on_taste_view_changed"])
def test_a_listener_change_reaches_every_device(name):
    assert "self._publish_listener" in _calls(_gui()[name])


@pytest.mark.parametrize("name", ["_taste_refresh", "_on_taste_view_changed"])
def test_a_taste_change_writes_no_device_s_key(name):
    """The device's value carries no taste. Its meter does, and the
    record carries its preamp."""
    calls = _calls(_gui()[name])
    assert "self._apply_now" not in calls
    assert "self._update_meter" in calls


def test_every_move_of_a_taste_drag_is_written():
    """No pass waits for the drag to end: every move is a wish, and the
    door writes the newest in its turn."""
    fn = _gui()["_on_taste_view_changed"]
    publish = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
               and ast.unparse(c.func) == "self._publish_listener"]
    assert publish
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and "final" in ast.unparse(node.test):
            assert not any(c in publish for c in ast.walk(node))


def test_the_record_is_the_store_s_answer_and_one_write():
    auth = types.SimpleNamespace(calls=[])
    auth.publish_graph = lambda k, v: auth.calls.append((k, v))
    publish = _method("_publish_listener",
                      pw_backend=types.SimpleNamespace(backend=lambda: auth),
                      LISTENER_KEY="@listener")
    asked = []
    win = types.SimpleNamespace(
        live=True, _listener_layers=lambda: ("taste", None),
        _held=lambda: {"dev#port": ("body", ["FL"])},
        store=types.SimpleNamespace(
            listener_value=lambda layers, held:
            asked.append((layers, held)) or "REC"))
    publish(win)
    assert auth.calls == [("@listener", "REC")]
    assert asked == [(("taste", None), {"dev#port": ("body", ["FL"])})]
    win.live = False
    publish(win)
    assert len(auth.calls) == 1


def test_undo_of_a_taste_or_a_ride_is_written_and_published():
    calls = _calls(_gui()["_restore"])
    assert "self._listener_state" in calls
    assert "self._save_preamp_state" in calls


# ---- a saved profile plays on every device bound to it ---------------------

def test_a_saved_profile_reaches_every_device_bound_to_it(store):
    """The record works out the preamp of a device not in view from the
    store, so its correction must be the store's too: when the window
    saves the profile in view, every other device bound to it gets its
    value. A save that changed nothing it plays writes nothing."""
    pid = _bind(store, "a#port")
    store.set_binding("b#port", pid)
    store.set_binding("view#port", pid)
    auth = types.SimpleNamespace(calls=[])
    auth.publish_graph = lambda k, v: auth.calls.append(("pub", k, v))
    auth.clear_graph = lambda k: auth.calls.append(("clear", k))
    sharers = _method("_publish_sharers",
                      pw_backend=types.SimpleNamespace(backend=lambda: auth))
    win = types.SimpleNamespace(
        live=True, node="view", store=store, _shared={},
        _dev=lambda: "view#port", _is_device=lambda: True,
        _listener_layers=lambda: ([], None))
    sharers(win, pid)
    assert sorted(c[1] for c in auth.calls) == ["a#port", "b#port"]
    assert all(c[2] == store.device_value("a#port", ([], None))
               for c in auth.calls)
    sharers(win, pid)
    assert len(auth.calls) == 2
    store.save_user(dict(store.get(pid), channels={}))   # nothing to play
    sharers(win, pid)
    assert sorted(auth.calls[2:]) == [("clear", "a#port"),
                                      ("clear", "b#port")]


@pytest.mark.parametrize("name", ["_save_now", "_on_floor_toggled",
                                  "_on_protect_edges", "_declare_channel"])
def test_the_window_saves_the_profile_in_view_through_one_method(name):
    calls = _calls(_gui()[name])
    assert "self._save_profile" in calls
    assert "self.store.save_user" not in calls


def test_an_undo_saves_through_it_too():
    """Its other save brings a deleted profile back from the graveyard,
    before the state lands on it."""
    saves = [[ast.unparse(a) for a in c.args]
             for c in ast.walk(_gui()["_restore"])
             if isinstance(c, ast.Call)
             and ast.unparse(c.func) == "self._save_profile"]
    assert saves == [["self._working_body()"]]


def test_the_one_method_publishes_the_others():
    calls = _calls(_gui()["_save_profile"])
    assert "self.store.save_user" in calls
    assert "self._publish_sharers" in calls


def test_a_measured_profile_reaches_the_others_when_the_window_closes():
    assert "self._publish_sharers" in _calls(_gui()["_on_measure_closed"])


# ---- undo puts the ride back as it was ---------------------------------------

def test_the_snapshot_holds_every_key_the_restore_reads():
    """The restore reads the preamp mode with Auto as the default. From Aug 7
    no snapshot carried the mode, so every undo in Manual switched to Auto,
    and since the restore writes the ride (5.1.0) the manual value was lost
    with it."""
    fns = _gui()
    written = {ast.literal_eval(k)
               for n in ast.walk(fns["_snapshot"]) if isinstance(n, ast.Return)
               for k in n.value.keys}
    read = set()
    for n in ast.walk(fns["_restore"]):
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "snap.get":
            read.add(ast.literal_eval(n.args[0]))
        elif isinstance(n, ast.Subscript) and ast.unparse(n.value) == "snap":
            read.add(ast.literal_eval(n.slice))
    assert "preamp_auto" in read
    assert read <= written


def test_a_press_of_auto_is_a_step_of_the_timeline():
    """With the mode in the snapshot and the press left off the timeline,
    undoing an edit made after pressing Auto would land the entry from
    before the press and release Auto."""
    assert "self._schedule_save" in _calls(_gui()["_set_preamp_auto"])


# ---- a turn of the ride edits no profile -------------------------------------

def test_a_turn_of_the_preamp_forks_no_profile():
    """A turn on Default made an empty "Custom EQ" and bound the device to
    it, and on a built-in profile made a copy of it: the turn ran the edit
    pipeline, which began by forking anything that is not a user profile.
    Since Aug 7 the preamp is not kept in a profile, so the copy held
    nothing the turn changed."""
    fns = _gui()
    assert "self._on_edit" in _calls(fns["_on_preamp"])
    for name in ("_on_preamp", "_on_edit"):
        assert "self._ensure_editable" not in _calls(fns[name])


@pytest.mark.parametrize("name", ["_on_view_changed", "_apply_rew_import"])
def test_an_edit_of_the_bands_still_forks_a_built_in(name):
    assert "self._ensure_editable" in _calls(_gui()[name])


# ---- a text import brings bands, not the preamp ------------------------------

def test_a_text_import_leaves_the_preamp_alone():
    """The file's Preamp line is REW's answer for its own bands; it knows
    nothing of the taste, the floor or the other channels. In Manual the
    import gave that number, or Safe on an output with more than one
    channel, to the device in view only: nothing saved it and no other
    device got it, and a restart brought the hand's value back."""
    fn = _gui()["_apply_rew_import"]
    assert not any(isinstance(n, ast.Attribute) and n.attr == "preamp"
                   and isinstance(n.ctx, ast.Store) for n in ast.walk(fn))
