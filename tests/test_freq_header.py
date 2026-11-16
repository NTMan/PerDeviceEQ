"""The Freq column's header: its word where the other headers keep
theirs, flush with the fields' edge, and the sort at the column's
right end, in the Tab walk with the header row. The word used to be
the face of a flat button, set in by the button's padding. Needs gi;
a GTK-less sandbox skips and CI with xvfb judges."""
import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from gi.repository import Gtk                  # noqa: E402

from perdeviceeq.peq_view import PeqView       # noqa: E402

PK = {"type": "PK", "freq": 1000.0, "gain": 3.0, "q": 1.0,
      "enabled": True}


def _view(freqs, **kw):
    v = PeqView(lambda *a: None, **kw)
    v.set_bands([dict(PK, freq=f) for f in freqs])
    return v


def test_the_word_reads_like_its_neighbours():
    v = _view([1000.0])
    head = v.grid.get_child_at(2, 0)
    assert isinstance(head, Gtk.Box)
    word = head.get_first_child()
    assert isinstance(word, Gtk.Label)
    assert word.get_label() == "Freq (Hz)"
    assert word.get_xalign() == 0.0
    gain = v.grid.get_child_at(3, 0)
    assert set(word.get_css_classes()) == set(gain.get_css_classes())


def test_the_sort_stands_at_the_columns_right_end():
    v = _view([1000.0])
    sort = v.grid.get_child_at(2, 0).get_last_child()
    assert isinstance(sort, Gtk.Button)
    assert sort.get_icon_name() == "view-sort-ascending-symbolic"
    assert sort.has_css_class("flat")
    assert sort.get_tooltip_text() == "Sort the rows by frequency"
    assert sort.get_prev_sibling().get_hexpand()


def test_the_sort_is_in_the_tab_walk_before_the_bands():
    """The flat button it replaces was in no stop of the walk, so a
    keyboard never reached the sort."""
    v = _view([500.0, 3000.0], on_balance=lambda db: None)
    sort = v.grid.get_child_at(2, 1).get_last_child()
    balance = v.grid.get_child_at(3, 0)
    first_type = v.grid.get_child_at(1, 2)
    assert v.grid._order[:3] == [balance, sort, first_type]


def test_the_sort_orders_the_rows_by_frequency():
    v = _view([500.0, 3000.0])
    v.grid.get_child_at(2, 1).set_value(9000.0)    # rows keep their order
    assert [b.freq for b in v._bands] == [9000.0, 3000.0]
    v.grid.get_child_at(2, 0).get_last_child().emit("clicked")
    assert [b.freq for b in v._bands] == [3000.0, 9000.0]
    assert v.grid.get_child_at(2, 1).get_value() == 3000.0
