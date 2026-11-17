"""A field the hand only passed through keeps what it holds.

GtkSpinButton re-reads its text whenever it loses focus and sets the
value it parses, and the text shows the value rounded to the field's
digits: a fitted band's 822.37 Hz became 822 with nothing typed -- an
edit nobody made, with its undo step and its save. Needs gi and a
display; a GTK-less sandbox skips and CI with xvfb judges."""
import ast
import pathlib
import time

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from gi.repository import GLib, Gtk            # noqa: E402

from perdeviceeq import focus                  # noqa: E402
from perdeviceeq.peq_view import PeqView       # noqa: E402


def _settle(seconds=0.3):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.005)


@pytest.fixture
def table():
    edits, balance = [], []
    v = PeqView(lambda bands, final: edits.append(bands),
                on_balance=balance.append)
    v.set_bands([{"type": "PK", "freq": 822.37, "gain": 5.24,
                  "q": 0.483, "enabled": True},
                 {"type": "PK", "freq": 3000.0, "gain": 1.0, "q": 1.0,
                  "enabled": True}])
    v.set_balance(-0.2337)
    win = Gtk.Window()
    win.set_child(v)
    win.present()
    _settle()
    yield v, edits, balance
    win.destroy()


def _field(v, col, row=0):
    return v.grid.get_child_at(col, 1 + 1 + row)   # Balance, header


def _pass_through(v, w):
    w.grab_focus()
    _settle(0.1)
    v.grid.get_child_at(1, 3).grab_focus()         # on to the next row
    _settle()


def test_passing_through_the_fields_changes_nothing(table):
    v, edits, balance = table
    for col in (2, 3, 4):
        _pass_through(v, _field(v, col))
    _pass_through(v, v.grid.get_child_at(3, 0))    # the Balance
    b = v._bands[0]
    assert (b.freq, b.gain, b.q) == (822.37, 5.24, 0.483)
    assert edits == [] and balance == []
    assert v.grid.get_child_at(3, 0).get_value() == -0.2337


def test_a_typed_value_is_an_edit(table):
    v, edits, _ = table
    f = _field(v, 2)
    f.grab_focus()
    f.set_text("900")
    _settle(0.1)
    v.grid.get_child_at(1, 3).grab_focus()
    _settle()
    assert v._bands[0].freq == 900.0
    assert edits and edits[-1][0]["freq"] == 900.0


def test_a_step_is_an_edit(table):
    v, edits, _ = table
    _field(v, 2).spin(Gtk.SpinType.STEP_FORWARD, 1.0)
    assert v._bands[0].freq == pytest.approx(823.37)
    assert len(edits) == 1


def test_the_programs_own_write_goes_through():
    """The session clamp moves the preamp by less than the field
    shows: -6.37 to -6.36 reads -6.4 either way and is still the new
    value."""
    sp = Gtk.SpinButton.new_with_range(-24.0, 24.0, 0.5)
    sp.set_digits(1)
    sp.set_value(-6.37)
    seen = []
    put = focus.connect_value(sp, lambda s: seen.append(s.get_value()))
    put(-6.36)
    assert seen == [-6.36] and sp.get_value() == -6.36


def test_the_preamp_field_is_held_the_same_way():
    """The session clamp lands an unrounded value, and a hand passing
    the field was taken for a turn, which un-presses Auto."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    calls = [ast.unparse(c) for c in ast.walk(ast.parse(src))
             if isinstance(c, ast.Call)]
    assert ("focus.connect_value(self.preamp_spin, self._on_preamp)"
            in calls)
    assert not [c for c in calls
                if c.startswith("self.preamp_spin.set_value(")]
