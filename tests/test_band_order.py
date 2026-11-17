"""The order of the bands is no content of the history.

The table sorts its own copy of the bands by frequency on every load,
undo included, while the window keeps the profile's order; the first
edit from the table, even one that changes nothing, hands the window
the sorted list. Recorded as held, the same bands in another order
were a new state, and a click on a band's handle after Undo took the
redo away. Runs the real methods over a stub window. Needs gi for
the module; a GTK-less sandbox skips."""
import types

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from perdeviceeq import gui                    # noqa: E402

# a fit hands its bands over in placement order, not by frequency
PLACED = [{"type": "PK", "freq": 3475.8, "gain": -15.59, "q": 0.533},
          {"type": "PK", "freq": 1000.0, "gain": 2.0, "q": 1.0},
          {"type": "HSC", "freq": 18417.2, "gain": -2.04, "q": 0.956},
          {"type": "PK", "freq": 63.0, "gain": -0.94, "q": 0.46}]
SORTED = sorted(PLACED, key=lambda b: b["freq"])


def _win(fl, taste=()):
    w = types.SimpleNamespace(
        current_pid="p", preamp=-3.0, preamp_auto=True,
        ch_keys=["FL"], slots={"FL": gui._slot_from({"bands": fl})},
        pref_layers=types.SimpleNamespace(
            active_id="t",
            layers=[{"id": "t", "name": "T", "bands": list(taste)}]))
    w._slot_to_dict = lambda k: gui.EqWindow._slot_to_dict(w, k)
    w._snapshot = lambda: gui.EqWindow._snapshot(w)
    w._update_undo_buttons = lambda: None
    return w


def test_one_set_of_bands_is_one_record():
    assert (gui.EqWindow._snapshot(_win(PLACED, PLACED))
            == gui.EqWindow._snapshot(_win(SORTED, SORTED)))


def test_another_value_is_still_another_record():
    moved = [dict(b) for b in SORTED]
    moved[0]["gain"] = -1.0
    assert (gui.EqWindow._snapshot(_win(PLACED))
            != gui.EqWindow._snapshot(_win(moved)))


def test_a_click_after_undo_keeps_the_redo():
    """The record was taken while the window held the profile's order;
    the click handed it the table's. Same bands, so no step."""
    w = _win(PLACED)
    seed = w._snapshot()
    edit = dict(seed, preamp=-6.0)
    w._hist, w._hidx, w._pending_sel = [seed, edit], 0, None
    w.slots = {"FL": gui._slot_from({"bands": SORTED})}
    gui.EqWindow._push_history(w)
    assert w._hist == [seed, edit] and w._hidx == 0
