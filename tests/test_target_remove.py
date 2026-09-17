"""Removing a target in the measure window.

The field sequence this was cut for: new measurement, remove one of
the two targets, both tabs go, the microphone's plus goes dead with
them, the close mints a profile with no sides, and the profile that
was playing before comes back one channel of correction short.

These run the REAL method unbound over a stub self. They need gi
(measure_window imports GTK at the top), so a GTK-less sandbox skips
and CI with xvfb judges.
"""

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:      # gi without the typelibs
    pytest.skip(str(e), allow_module_level=True)

from perdeviceeq import measure_window as mw  # noqa: E402


class _Obj:
    pass


class _Store:
    """Enough of ProfileStore to answer, and a record of what was
    written -- the pins especially, since writing one is the thing
    this method must not do."""

    def __init__(self, profiles=None):
        self.profiles = profiles or {}
        self.saved = []
        self.pinned = []
        self.reconciled = []

    def get(self, pid):
        return self.profiles.get(pid)

    def save_user(self, body):
        self.saved.append(body)
        self.profiles[body.get("id", "p1")] = body
        return body.get("id", "p1")

    def pin_channel(self, node, ch, target):
        self.pinned.append((node, ch, target))

    def reconcile_map(self, node, prof_keys, sink_keys):
        self.reconciled.append((node, list(prof_keys), list(sink_keys)))
        return {c: c for c in sink_keys}


def _win(keys, edit_pid=None, profiles=None, takes=()):
    w = _Obj()
    w.ch_keys = list(keys)
    w.n_ch = len(w.ch_keys)
    w._selected_ch = 1
    w.edit_pid = edit_pid
    w.sink_node = "sink"
    w.parent = _Obj()
    w.parent.store = _Store(profiles)
    w.session = _Obj()
    w.session.takes_of = lambda ch: list(takes)
    w._target_is_empty = lambda ch: mw.MeasureWindow._target_is_empty(
        w, ch)
    w._pw_output_channels = lambda node: ["FL", "FR"]
    for name in ("_recompute_mic", "_rebuild_map_slots",
                 "_rebuild_session", "_refresh_all"):
        setattr(w, name, lambda *a: None)
    return w


def _del(w):
    mw.MeasureWindow._del_pair(w)


def test_a_fresh_window_keeps_the_targets_it_did_not_remove():
    """The window's own list is what is being edited. There is no
    profile in a fresh window, and reading the answer back out of the
    store took both tabs for one click."""
    w = _win(["FL", "FR"])
    _del(w)
    assert w.ch_keys == ["FL"]
    assert w.n_ch == 1


def test_removing_a_target_pins_nothing():
    """A pin is a fact about the card and outlives the profile. This
    is a statement about one profile's sides."""
    w = _win(["FL", "FR"])
    _del(w)
    assert w.parent.store.pinned == []


def test_an_edited_profile_loses_that_side_and_keeps_the_rest():
    prof = {"id": "p1", "ch_keys": ["FL", "FR"],
            "channels": {"FL": {"bands": [1]}, "FR": {"bands": []}}}
    w = _win(["FL", "FR"], edit_pid="p1", profiles={"p1": prof})
    _del(w)
    assert w.ch_keys == ["FL"]
    body = w.parent.store.saved[-1]
    assert body["ch_keys"] == ["FL"]
    assert list(body["channels"]) == ["FL"]
    assert w.parent.store.pinned == []


def test_a_target_with_takes_is_not_removed():
    """The one thing in this window nobody can make again by
    clicking."""
    w = _win(["FL", "FR"], takes=("take-1",))
    _del(w)
    assert w.ch_keys == ["FL", "FR"]
    assert w.parent.store.saved == []


def test_a_target_carrying_bands_is_not_removed():
    prof = {"id": "p1", "ch_keys": ["FL", "FR"],
            "channels": {"FR": {"bands": [{"f": 100}]}}}
    w = _win(["FL", "FR"], edit_pid="p1", profiles={"p1": prof})
    _del(w)
    assert w.ch_keys == ["FL", "FR"]
    assert w.parent.store.saved == []
