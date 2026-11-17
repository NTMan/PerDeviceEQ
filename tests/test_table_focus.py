"""The band table's frame stays and a hand in a row keeps its field.

A sort, an add and a delete rebuilt the whole grid, so the widget
under the hand died with the rest: GTK then sent the focus forward
from nothing, some 300 ms later, to the walk's first stop, the
Balance, and the pointer resting on the sort no longer hovered it.
Needs gi and a display; a GTK-less sandbox skips and CI with xvfb
judges."""
import time

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from gi.repository import GLib, Gtk            # noqa: E402

from perdeviceeq.peq_view import PeqView       # noqa: E402


def _band(f):
    return {"type": "PK", "freq": f, "gain": 1.0, "q": 1.0,
            "enabled": True}


def _settle(seconds=0.8):
    """Run the main loop past the moment GTK moves the focus of a
    destroyed widget."""
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.005)


@pytest.fixture
def table():
    v = PeqView(lambda *a: None, on_import_file=lambda: None,
                on_balance=lambda db: None)
    v.set_bands([_band(f) for f in (500.0, 3000.0, 8000.0)])
    win = Gtk.Window()
    win.set_child(v)
    win.present()
    _settle(0.3)
    yield v, win
    win.destroy()


def _top(v):
    return 1                     # the Balance row stands above the header


def _cell(v, row, col):
    return v.grid.get_child_at(col, _top(v) + 1 + row)


def _sort(v):
    return v.grid.get_child_at(2, _top(v)).get_last_child()


def _add(v):
    acts = v.grid.get_child_at(1, _top(v) + 1 + len(v._bands))
    return acts.get_first_child()


def _holds(win, w):
    f = win.get_focus()
    return f is not None and (f is w or f.is_ancestor(w))


def _press(button):
    button.grab_focus()
    button.activate()
    _settle()


def test_a_sort_keeps_the_focus_on_the_sort(table):
    v, win = table
    _cell(v, 0, 2).set_value(9000.0)
    _press(_sort(v))
    assert [b.freq for b in v._bands] == [3000.0, 8000.0, 9000.0]
    assert _holds(win, _sort(v))
    # and Tab goes on from there, into the first row
    win.child_focus(Gtk.DirectionType.TAB_FORWARD)
    assert _holds(win, _cell(v, 0, 1))


def test_a_delete_hands_the_focus_to_the_row_that_took_its_place(table):
    v, win = table
    _press(_cell(v, 0, 7))
    assert [b.freq for b in v._bands] == [3000.0, 8000.0]
    assert _holds(win, _cell(v, 0, 7))


def test_the_last_row_deleted_hands_it_to_the_row_above(table):
    v, win = table
    _press(_cell(v, 2, 7))
    assert _holds(win, _cell(v, 1, 7))


def test_with_no_row_left_add_band_takes_it(table):
    v, win = table
    for _ in range(3):
        _press(_cell(v, 0, 7))
    assert v._bands == []
    assert _holds(win, _add(v))


def test_an_add_keeps_the_focus_on_add_band(table):
    v, win = table
    _press(_add(v))
    assert len(v._bands) == 4
    assert _holds(win, _add(v))


def test_a_field_keeps_its_band_when_another_row_goes(table):
    """The graph deletes a band by its handle: the hand in another
    band's field stays in that band's field, wherever its row went."""
    v, win = table
    _cell(v, 2, 3).grab_focus()           # the 8000 Hz band's gain
    _settle(0.1)
    v._bands.remove(v._bands[0])          # as the right-click does
    v._rebuild_table()
    _settle()
    assert v._bands[1].freq == 8000.0
    assert _holds(win, _cell(v, 1, 3))


def test_the_frame_outlives_the_rows(table):
    """The sort under the pointer stays the widget it is, so it stays
    hovered and focused; so do Add band and the Balance."""
    v, win = table
    sort, add, balance = _sort(v), _add(v), v.grid.get_child_at(3, 0)
    _cell(v, 0, 2).set_value(9000.0)
    _press(sort)
    _press(add)
    v.set_bands([_band(100.0)])
    assert _sort(v) is sort and _add(v) is add
    assert v.grid.get_child_at(3, 0) is balance
