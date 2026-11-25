"""The editor reports the bands only when they changed.

A click on a handle used to be reported, and the owner took it for an
edit: it reset the headroom session's latched peak, rewrote the
profile and put the same graph on the server again. So was the
release of a drag that came back to where it began. Drives the real
handlers of a PeqView that was never shown; the plot rectangle is set
as a draw would set it. Needs gi; a GTK-less sandbox skips."""
import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from perdeviceeq.peq_view import PeqView       # noqa: E402

PLOT = (44, 10, 642, 208)                      # ml, mt, width, height


class _Press:
    def __init__(self, x, y):
        self.x, self.y = x, y

    def get_current_event_state(self):
        return 0

    def get_start_point(self):
        return True, self.x, self.y


def _view(bands=((1000.0, 3.0),)):
    edits = []
    v = PeqView(lambda bands, final: edits.append(final), preamp=-3.0)
    v.set_bands([{"type": "PK", "freq": f, "gain": g, "q": 1.0,
                  "enabled": True} for f, g in bands])
    v._plot = PLOT
    return v, edits


def _on_handle(v):
    b = v._bands[0]
    return _Press(v._x_of(b.freq), v._y_of(v._band_ref() + b.gain))


def test_a_click_on_a_handle_is_no_edit():
    v, edits = _view()
    g = _on_handle(v)
    v._on_drag_begin(g, g.x, g.y)
    v._on_drag_end(g, 0.0, 0.0)
    assert edits == []


def test_a_motion_of_nothing_is_no_edit_either():
    v, edits = _view()
    g = _on_handle(v)
    v._on_drag_begin(g, g.x, g.y)
    v._on_drag_update(g, 0.0, 0.0)
    v._on_drag_end(g, 0.0, 0.0)
    assert edits == []


def test_a_drag_is_an_edit():
    v, edits = _view()
    g = _on_handle(v)
    v._on_drag_begin(g, g.x, g.y)
    v._on_drag_update(g, 0.0, -4.0)
    v._on_drag_end(g, 0.0, -4.0)
    assert edits == [False, True]


def test_a_band_born_by_a_click_settles_at_the_release():
    """The press reports the new band live, as the start of a drag;
    the release settles it, once."""
    v, edits = _view(())
    g = _Press(v._x_of(200.0), v._y_of(v._band_ref() + 6.0))
    v._on_drag_begin(g, g.x, g.y)
    v._on_drag_end(g, 0.0, 0.0)
    assert edits == [False, True] and len(v.get_bands()) == 1


def test_a_drag_back_to_where_it_began_settles_nothing():
    """Live reports went out on the way; at the end the bands are as
    they were before the press, and there is nothing to settle."""
    v, edits = _view()
    g = _on_handle(v)
    v._on_drag_begin(g, g.x, g.y)
    v._on_drag_update(g, 0.0, -4.0)
    v._on_drag_update(g, 0.0, 0.0)
    v._on_drag_end(g, 0.0, 0.0)
    assert edits == [False, False]
