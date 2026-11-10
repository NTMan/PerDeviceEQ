"""The Balance row of a device channel's editor, and the window behind it.

The row stands above the column header, a gain field with two decimals
and either sign and nothing else of a band's. The window keeps the
balance in the channel's slot: the tab in view shows it, an edit of it
is an edit of the correction like a band's, a file of bands leaves it
alone, and the meter and the headroom reading hear it. Needs gi; a
GTK-less sandbox skips and CI with xvfb judges."""
import ast
import pathlib
import types

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from gi.repository import Gtk                  # noqa: E402

from perdeviceeq import eq, gui                # noqa: E402
from perdeviceeq.peq_view import PeqView       # noqa: E402

PK = {"type": "PK", "freq": 1000.0, "gain": 3.0, "q": 1.0,
      "enabled": True}


def _text(w):
    return w.get_label() if isinstance(w, Gtk.Label) else None


class _Row:
    def __init__(self, on):
        self.on = on

    def get_active(self):
        return self.on


def _win(slots, **kw):
    w = types.SimpleNamespace(slots=slots, **kw)
    w._slot = lambda ch: slots.setdefault(ch, gui._new_slot())
    return w


def test_the_row_stands_above_the_header():
    got = []
    v = PeqView(lambda *a: None, on_balance=got.append)
    v.set_bands([PK])
    v.set_balance(-1.3)
    assert _text(v.grid.get_child_at(1, 0)) == "Balance"
    spin = v.grid.get_child_at(3, 0)
    assert isinstance(spin, Gtk.SpinButton)
    assert spin.get_digits() == 2
    assert spin.get_value() == pytest.approx(-1.3)
    lo, hi = spin.get_range()
    assert lo < 0.0 < hi
    # nothing else of a band's: no colour, type, frequency, Q,
    # switch or bin
    assert [c for c in (0, 2, 4, 5, 6, 7)
            if v.grid.get_child_at(c, 0) is not None] == []
    # the header sets it off from the bands under it
    assert _text(v.grid.get_child_at(1, 1)) == "Type"
    assert isinstance(v.grid.get_child_at(1, 2), Gtk.DropDown)
    assert got == []                   # showing it is not an edit
    spin.set_value(-1.37)
    assert got == [-1.37]
    # a table rebuilt keeps what it showed
    v.set_bands([PK, dict(PK, freq=2000.0)])
    assert v.grid.get_child_at(3, 0).get_value() == pytest.approx(-1.37)


def test_the_taste_has_no_row():
    v = PeqView(lambda *a: None)
    v.set_bands([PK])
    assert _text(v.grid.get_child_at(1, 0)) == "Type"


def test_the_window_gives_the_device_view_the_row_and_the_taste_none():
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    views = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and ast.unparse(n.func) == "PeqView"]
    kw = {ast.unparse(n.args[0]): {k.arg for k in n.keywords}
          for n in views}
    assert "on_balance" in kw["self._on_view_changed"]
    assert "on_balance" not in kw["self._on_taste_view_changed"]


def test_a_slot_holds_the_balance_and_gives_it_back():
    s = gui._slot_from({"bands": [PK], "balance": -1.3})
    assert s["balance"] == -1.3
    assert isinstance(s["bands"][0], eq.Band)
    win = _win({"FL": s, "FR": gui._slot_from({"bands": [PK]})})
    assert gui.EqWindow._slot_to_dict(win, "FL") == {
        "bands": [PK], "balance": -1.3}
    assert gui.EqWindow._slot_to_dict(win, "FR") == {"bands": [PK]}


def test_the_tab_in_view_shows_its_balance():
    shown = {}
    view = types.SimpleNamespace(
        export_parts=None,
        set_bands=lambda b: shown.update(bands=b),
        set_balance=lambda db: shown.update(balance=db),
        set_floor=lambda f: None, set_zone=lambda z: None,
        set_preamp=lambda p: None)
    win = _win({"FR": gui._slot_from({"bands": [PK], "balance": -0.4})},
               _loading=False, tabs=None, preamp=-3.0, current_pid="p",
               preamp_spin=types.SimpleNamespace(set_value=lambda v: None),
               store=types.SimpleNamespace(get=lambda pid: {"name": "P"}),
               view=view, _sync_view_curves=lambda: None,
               _update_headroom=lambda: None,
               _dress_pair_controls=lambda: None)
    gui.EqWindow._load_slot(win, "FR")
    assert win.cur_ch == "FR"
    assert shown == {"bands": [PK], "balance": -0.4}


def test_an_edit_of_the_balance_is_an_edit_of_the_correction():
    """The slot first, so that a built-in forked by the edit is forked
    with it; then the device hears it, Auto follows and the save and the
    undo step come, as for a band."""
    seen = []
    win = _win({"FL": gui._new_slot()}, _loading=False, cur_ch="FL")
    win._ensure_editable = lambda: seen.append(
        ("fork", win.slots["FL"]["balance"]))
    win._on_edit = lambda: seen.append("edit")
    gui.EqWindow._on_balance_changed(win, -0.75)
    assert seen == [("fork", -0.75), "edit"]
    win._loading = True                # a tab being shown is no edit
    gui.EqWindow._on_balance_changed(win, -2.0)
    assert win.slots["FL"]["balance"] == -0.75 and len(seen) == 2


def test_a_file_of_bands_leaves_the_balance():
    seen = []
    win = _win({"FL": gui._slot_from({"bands": [PK], "balance": -1.3})},
               cur_ch="FL", _load_slot=lambda ch: seen.append(ch),
               _ensure_editable=lambda: None, _on_edit=lambda: None)
    bands = [eq.Band.from_dict(dict(PK, gain=-2.0))]
    gui.EqWindow._apply_rew_import(win, bands)
    assert win.slots["FL"]["bands"] is bands
    assert win.slots["FL"]["balance"] == -1.3
    assert seen == ["FL"]


def test_the_meter_hears_each_channels_balance():
    win = _win({"FL": gui._slot_from({"bands": [PK], "balance": -1.3}),
                "FR": gui._slot_from({"bands": [PK]})},
               bypass_row=_Row(False), sink_keys=["FL", "FR", "LFE"],
               ch_keys=["FL", "FR"], preamp=-4.0,
               pref_layers=types.SimpleNamespace(active_bands=lambda: []))
    pre, chains, bals = gui.EqWindow._meter_chains(win)
    assert pre == -4.0 and bals == [-1.3, 0.0, 0.0]
    assert chains[2] == []             # unpaired: the tail alone
    win.bypass_row = _Row(True)
    assert gui.EqWindow._meter_chains(win) == (
        0.0, [[], [], []], [0.0, 0.0, 0.0])


def test_what_the_rig_receives_counts_the_balance():
    win = _win({"FL": gui._slot_from({"bands": [], "balance": -1.0})},
               bands=[], cur_ch="FL", preamp=-3.0, current_pid="p",
               store=types.SimpleNamespace(
                   get=lambda pid: {"floor_off": True}),
               pref_layers=types.SimpleNamespace(active_bands=lambda: []))
    got = gui.EqWindow._delivered_db(win, [100.0, 1000.0])
    assert list(got) == pytest.approx([-4.0, -4.0])
