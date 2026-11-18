"""A drag moves a band by the pointer's offset from the press.

It used to put the band under the pointer: a press anywhere on the
11 px handle jumped the band there at the first motion, a half-pixel
tremor of a click included, and a straight vertical drag retuned the
band. Drives the real handlers of a PeqView that was never shown; the
plot rectangle is set as a draw would set it. Needs gi; a GTK-less
sandbox skips."""
import math

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from perdeviceeq.eq import FMAX, FMIN          # noqa: E402
from perdeviceeq.peq_view import PeqView       # noqa: E402

PLOT = (44, 10, 642, 208)                      # ml, mt, width, height
DEC_PX = (math.log10(FMAX) - math.log10(FMIN)) / PLOT[2]
DB_PX = 48.0 / PLOT[3]                         # the +-24 dB window


class _Press:
    """What the handlers ask of the gesture: no modifier held, and
    where the press was."""
    def __init__(self, x, y):
        self.x, self.y = x, y

    def get_current_event_state(self):
        return 0

    def get_start_point(self):
        return True, self.x, self.y


def _view(freq=1000.0, gain=3.0):
    v = PeqView(lambda bands, final: None, preamp=-3.0)
    v.set_bands([{"type": "PK", "freq": freq, "gain": gain, "q": 1.0,
                  "enabled": True}])
    v._plot = PLOT
    return v


def _press(v, dx, dy):
    """A press dx, dy px off the band's handle, as it is drawn."""
    b = v._bands[0]
    g = _Press(v._x_of(max(b.freq, FMIN)) + dx,
               v._y_of(v._band_ref() + b.gain) + dy)
    v._on_drag_begin(g, g.x, g.y)
    assert v._drag_band is b                   # the handle took it
    return b, g


def test_a_press_off_the_handle_s_centre_moves_nothing():
    v = _view()
    b, g = _press(v, 5, -5)
    v._on_drag_update(g, 0.0, 0.0)
    assert (b.freq, b.gain) == (1000.0, 3.0)
    v._on_drag_update(g, 1.0, 0.0)
    assert b.freq == pytest.approx(1000.0 * 10 ** DEC_PX)
    assert b.gain == 3.0


def test_a_vertical_drag_keeps_the_frequency():
    v = _view()
    b, g = _press(v, 5, 0)
    v._on_drag_update(g, 0.0, 40.0)
    assert b.freq == 1000.0
    assert b.gain == pytest.approx(3.0 - 40 * DB_PX)


def test_a_band_below_the_plot_keeps_its_frequency():
    v = _view(freq=10.0)
    b, g = _press(v, 0, 0)
    v._on_drag_update(g, 30.0, 4.0)
    assert b.freq == 10.0
    assert b.gain == pytest.approx(3.0 - 4 * DB_PX)


def test_the_scale_holds_under_the_hand_while_auto_moves_the_preamp():
    """A measured line stays where it was measured, and the window
    widened to keep it in sight once Auto had moved the preamp far
    enough: the dB per pixel changed under the hand mid-drag, and the
    handle fell behind it."""
    v = _view()                                # +3 dB at 1 kHz, -3 preamp
    fr = [20.0 * 1000.0 ** (i / 99) for i in range(100)]
    v.set_curves(fr, [15.0] * 100)
    b, g = _press(v, 0, 0)
    for dy in range(4, 84, 4):
        v._on_drag_update(g, 0.0, -float(dy))
        v.set_preamp(-b.gain)                  # Auto: the peak stays at 0
        assert v._y_of(v._band_ref() + b.gain) == pytest.approx(g.y - dy)
    v._on_drag_end(g, 0.0, -80.0)
    assert v._y_window()[1] >= 15.0            # the release fits it again
