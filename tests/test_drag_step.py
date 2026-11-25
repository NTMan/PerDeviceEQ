"""A drag of a band is one undo step.

The step rode the save timer, which every live report of a drag
re-armed as any edit does, so each rest of the hand longer than the
timer was a step of its own: one drag of a taste band took ten
presses of Ctrl+Z to undo. Runs the real methods over a stub window,
the timer on GLib's main loop. Needs gi; a GTK-less sandbox skips."""
import functools
import time
import types

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from gi.repository import GLib                 # noqa: E402

from perdeviceeq import gui                    # noqa: E402


@pytest.fixture(autouse=True)
def _quick_timer(monkeypatch):
    monkeypatch.setattr(gui, "_SAVE_DEBOUNCE_MS", 20)


def _rest(seconds=0.1):
    """The hand holds still, longer than the timer."""
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.002)


def _pk(gain):
    return {"type": "PK", "freq": 1000.0, "gain": gain, "q": 1.0,
            "enabled": True}


def _win():
    """The device card's owner and the taste card's, down to the save
    timer. A save keeps the device's gains, a step the device's and
    the taste's."""
    layer = {"id": "t", "name": "Layer 1", "bands": []}
    w = types.SimpleNamespace(
        _loading=False, _restoring=False, _step_due=False,
        _save_source=0, _dev_dirty=False, _born_pending=None,
        current_pid="p", bands=[], _bal=[], saves=[], steps=[],
        view=types.SimpleNamespace(
            graph=types.SimpleNamespace(queue_draw=lambda: None)),
        pref_layers=types.SimpleNamespace(
            active=lambda: layer, upsert=layer.update))
    for name in ("_sync_map", "_mirror_siblings", "_update_headroom",
                 "_apply_now", "_canvas_refresh", "_ensure_editable",
                 "_update_meter", "_publish_listener"):
        setattr(w, name, lambda: None)
    w._editable = lambda pid: True
    w._working_body = lambda: [b.gain for b in w.bands]
    w._save_profile = w.saves.append
    w._push_history = lambda born=None: w.steps.append(
        ([b.gain for b in w.bands], [b["gain"] for b in layer["bands"]]))
    for name in ("_on_view_changed", "_on_taste_view_changed", "_on_edit",
                 "_schedule_save", "_save_now", "_land_save"):
        setattr(w, name, functools.partial(getattr(gui.EqWindow, name), w))
    return w


def test_a_drag_is_one_step_however_often_the_hand_rests():
    w = _win()
    for gain in (1.0, 2.0, 3.0):
        w._on_view_changed([_pk(gain)], False)
        _rest()
    assert w.steps == []
    assert w.saves == [[1.0], [2.0], [3.0]]    # kept as the hand went
    w._on_view_changed([_pk(3.0)], True)       # the release
    _rest()
    assert w.steps == [([3.0], [])]


def test_a_drag_of_a_taste_band_is_one_step():
    w = _win()
    for gain in (1.0, 2.0):
        w._on_taste_view_changed([_pk(gain)], False)
        _rest()
    assert w.steps == []
    w._on_taste_view_changed([_pk(2.0)], True)
    _rest()
    assert w.steps == [([], [2.0])]


def test_an_edit_just_before_the_grab_keeps_its_own_step():
    """Its step waits on the timer when the drag begins; it lands
    then, on the state it was made on, not in the middle of the drag
    with the drag's first stretch in it."""
    w = _win()
    w._on_view_changed([_pk(1.0)], True)       # a field, say
    w._on_view_changed([_pk(2.0)], False)      # the grab, at once
    _rest()
    w._on_view_changed([_pk(3.0)], True)       # the release
    _rest()
    assert w.steps == [([1.0], []), ([3.0], [])]


def test_so_does_a_taste_edit_just_before_the_grab():
    w = _win()
    w._on_taste_view_changed([_pk(1.0)], True)
    w._on_taste_view_changed([_pk(2.0)], False)
    _rest()
    w._on_taste_view_changed([_pk(3.0)], True)
    _rest()
    assert w.steps == [([], [1.0]), ([], [3.0])]
