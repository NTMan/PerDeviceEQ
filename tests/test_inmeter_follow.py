"""The input meter reads the rig that is chosen NOW.

The field case: the window was opened on one microphone, a second
one was plugged in and picked, and the bars beside the new rig's
name went on metering the old jack -- the tap had been started once
and only ever asked whether it was running.

The method runs unbound over a stub self, as the closing word's
courts do. It needs gi (measure_window imports GTK at the top), so a
GTK-less sandbox skips and CI with xvfb judges.
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


class _Tap:
    """InputMeter's surface: what it is reading, and whether it is."""

    def __init__(self, node=None, channels=0, alive=True):
        self.node = node
        self.channels = channels
        self._alive = alive and node is not None
        self.started = []
        self.stopped = 0

    def alive(self):
        return self._alive

    def start(self, node, channels):
        self.started.append((node, channels))
        self.node = node
        self.channels = channels
        self._alive = True

    def stop(self):
        self.stopped += 1
        self.node = None
        self.channels = 0
        self._alive = False


def _win(tap, src=("in-2", 2), wanted=True, mic_ch=2):
    w = _Obj()
    w._inmeter = tap
    w.mic_ch = mic_ch
    w._meter_tick = 1                    # no timer wanted in a court
    w._meter_shown = {1: -12.0}
    w._meter_wanted = lambda: wanted
    w._selected_source = lambda: (
        {"id": src[0], "node": "alsa_input.%s" % src[0]} if src else None)
    w._paint_meters = lambda: None
    w._meter_dark = lambda: mw.MeasureWindow._meter_dark(w)
    return w


def _sync(w):
    mw.MeasureWindow._sync_inmeter(w)


def test_a_tap_on_another_rig_moves_to_the_chosen_one():
    tap = _Tap("in-1", 16)
    w = _win(tap, src=("in-2", 2))
    _sync(w)
    assert tap.started == [("in-2", 2)]


def test_the_old_rig_levels_do_not_stay_on_the_bars():
    tap = _Tap("in-1", 16)
    w = _win(tap, src=("in-2", 2))
    _sync(w)
    assert w._meter_shown == {}


def test_a_tap_on_the_chosen_rig_is_left_alone():
    """A restart costs a capture stream and makes every bar drop."""
    tap = _Tap("in-2", 2)
    w = _win(tap, src=("in-2", 2))
    _sync(w)
    assert tap.started == []
    assert tap.stopped == 0


def test_a_width_that_changed_is_a_different_tap():
    """The capture is opened with a column count; the same node in
    another profile is not the same read."""
    tap = _Tap("in-2", 16)
    w = _win(tap, src=("in-2", 2), mic_ch=2)
    _sync(w)
    assert tap.started == [("in-2", 2)]


def test_a_tap_whose_thread_died_is_reopened():
    tap = _Tap("in-2", 2)
    tap._alive = False
    w = _win(tap, src=("in-2", 2))
    _sync(w)
    assert tap.started == [("in-2", 2)]


def test_a_rig_that_is_not_there_stops_the_tap():
    tap = _Tap("in-1", 2)
    w = _win(tap, src=("in-1", 2), wanted=False)
    _sync(w)
    assert tap.stopped == 1
    assert tap.started == []


def test_a_door_row_taps_nothing():
    """A row that speaks for no node yet is not a rig to read."""
    tap = _Tap("in-1", 2)
    w = _win(tap, src=None)
    _sync(w)
    assert tap.stopped == 1
    assert tap.started == []
