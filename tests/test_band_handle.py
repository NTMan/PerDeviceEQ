"""A band's handle stands where the band acts on the scale.

The scale and the EQ line count from the preamp and the channel's
balance; the handles counted from 0 dB. With the preamp at -12.5 a
handle could not rise past +11.5 dB, could sink to -36.5 -- below its
field's -24, which then showed -24.0 for a band playing -31.43 -- and
the dB under a fixed pixel moved whenever Auto moved the preamp.
Drives the real handlers of a PeqView that was never shown; the plot
rectangle is set as a draw would set it. Needs gi; a GTK-less sandbox
skips."""
import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from gi.repository import Gtk                  # noqa: E402

from perdeviceeq.peq_view import PeqView       # noqa: E402

PLOT = (44, 10, 642, 208)                      # ml, mt, width, height
REF = -12.5 - 0.25                             # preamp + balance


class _Press:
    def __init__(self, x, y):
        self.x, self.y = x, y

    def get_current_event_state(self):
        return 0

    def get_start_point(self):
        return True, self.x, self.y


def _view(bands):
    v = PeqView(lambda bands, final: None, preamp=-12.5)
    v.set_bands([{"type": "PK", "freq": f, "gain": g, "q": 1.0,
                  "enabled": True} for f, g in bands])
    v.set_balance(-0.25)
    v._plot = PLOT
    return v


def _gain_field(v):
    c = v.grid.get_first_child()
    while c is not None:
        if (isinstance(c, Gtk.SpinButton)
                and c.get_tooltip_text() == "Gain, dB"):
            return c
        c = c.get_next_sibling()
    raise AssertionError("no gain field")


def test_a_handle_stands_at_the_preamp_balance_and_gain():
    v = _view([(1000.0, 6.0)])
    x = v._x_of(1000.0)
    assert v._hit_band(x, v._y_of(REF + 6.0)) is v._bands[0]
    assert v._hit_band(x, v._y_of(6.0)) is None


def test_the_window_is_the_band_s_range():
    v = _view([(1000.0, 0.0)])
    b = v._bands[0]
    g = _Press(v._x_of(1000.0), v._y_of(REF))
    v._on_drag_begin(g, g.x, g.y)
    v._on_drag_update(g, 0.0, -1000.0)
    assert b.gain == 24.0
    v._on_drag_update(g, 0.0, 1000.0)
    assert b.gain == -24.0
    v._on_drag_end(g, 0.0, 1000.0)
    assert _gain_field(v).get_value() == b.gain


def test_a_band_is_born_counted_from_the_preamp():
    v = _view([])
    g = _Press(v._x_of(200.0), v._y_of(REF + 6.0))
    v._on_drag_begin(g, g.x, g.y)
    v._on_drag_end(g, 0.0, 0.0)
    assert v.get_bands()[0]["gain"] == pytest.approx(6.0)


def test_a_preamp_change_leaves_the_handle_where_it_was_on_screen():
    v = _view([(1000.0, 6.0)])
    x, y = v._x_of(1000.0), v._y_of(REF + 6.0)
    v.set_preamp(-20.0)
    assert v._hit_band(x, y) is v._bands[0]
