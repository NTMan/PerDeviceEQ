"""The playback level, with the stretch of it the rig cannot follow.

IT IS A DUPLICATE OF THE SYSTEM KNOB, not a second volume: it reads
the sink's own level and writes back to it. He was already using the
volume this way -- turning the desktop's slider while watching the
graph until the red went away -- and a control and the picture it
changes belong on one screen.

IT LIVES ABOVE THE DEVICE CARD because it is not about the device's
correction. It is the level the whole chain is driven at.

THREE STATES, and the third is the one that took a correction. Green
is measured and followed. Red is measured and not followed. GREY IS
NOT MEASURED -- above the loudest rung a map reached, and a map often
stops because the microphone ran out of room rather than because the
rig did: three of his five rigs end that way, his Tanchjim among them,
after answering every rung it was given. Drawing that stretch as safe
would be the same invention this project keeps removing, in the
comfortable direction.
"""

import math
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk                                # noqa: E402

PAD = 10.0
HEIGHT = 22


class LevelStrip(Gtk.DrawingArea):
    def __init__(self, on_change=None):
        super().__init__()
        self.set_content_height(HEIGHT)
        self.set_hexpand(True)
        self.set_visible(False)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Playback level, with the region this rig cannot follow"])
        self.set_draw_func(self._draw)
        self._level = None          # cubic volume now
        self._unsafe = None         # where the rig stops following
        self._unknown = None        # where measurement stopped
        self._cb = on_change
        self._drag = None
        self._emit = 0.0            # last write, for the throttle
        self._held = 0.0            # until when to ignore the server
        g = Gtk.GestureDrag()
        g.connect("drag-begin", self._begin)
        g.connect("drag-update", self._update)
        g.connect("drag-end", self._end)
        self.add_controller(g)

    # WHAT MAKES A FADER FEEL LIKE PORRIDGE, and both halves of it
    # were here. One: a write per motion event, each one a wpctl
    # subprocess, so the hand outruns the pipe. Two: the poller then
    # hands back a level it read up to three seconds ago and the
    # handle jumps back to where the hand no longer is.
    #
    # pavucontrol's answer, and it is the right one: while a control
    # is being dragged it belongs to the hand, and what the server
    # says about it is ignored until the hand lets go and the server
    # has had a moment to agree.
    _WRITE_EVERY = 0.05
    _HOLD_AFTER = 0.60

    def set_state(self, volume=None, unsafe=None, unknown=None):
        self._unsafe = unsafe
        self._unknown = unknown
        # the strip appears as soon as there is a level to show, even
        # while a drag owns the handle -- only the HANDLE is the
        # hand's, not whether the widget exists
        self.set_visible(volume is not None)
        if self._drag is None and time.monotonic() >= self._held:
            self._level = volume
        self.queue_draw()

    def _geo(self):
        return PAD, max(1.0, self.get_width() - 2 * PAD)

    def _draw(self, _a, cr, w, h, *_):
        v = self._level
        if v is None:
            return
        x0, span = self._geo()
        mid = h / 2.0
        cr.set_source_rgba(0.45, 0.75, 0.50, 0.35)
        cr.rectangle(x0, mid - 2, span, 4)
        cr.fill()
        u = self._unsafe
        if u is not None and u < 1.0:
            cr.set_source_rgba(0.90, 0.30, 0.25, 0.45)
            cr.rectangle(x0 + span * u, mid - 4, span * (1.0 - u), 8)
            cr.fill()
        k = self._unknown
        if k is not None and k < 1.0:
            # NOT SAFE AND NOT UNSAFE. It is drawn in a colour that
            # claims nothing, and it is drawn OVER the others so a
            # stretch nobody measured never inherits their verdict.
            cr.set_source_rgba(0.60, 0.60, 0.64, 0.40)
            cr.rectangle(x0 + span * k, mid - 4, span * (1.0 - k), 8)
            cr.fill()
        cx = x0 + span * max(0.0, min(1.0, v))
        cr.set_source_rgba(0.95, 0.95, 0.98, 0.95)
        cr.arc(cx, mid, 6.0, 0, 2 * math.pi)
        cr.fill()

    def _at(self, x):
        x0, span = self._geo()
        return max(0.0, min(1.0, (x - x0) / span))

    def _begin(self, g, sx, sy):
        self._drag = sx
        self._level = self._at(sx)
        self.queue_draw()

    def _update(self, g, dx, dy):
        if self._drag is None:
            return
        self._level = self._at(self._drag + dx)
        self.queue_draw()
        now = time.monotonic()
        if self._cb and now - self._emit > self._WRITE_EVERY:
            self._emit = now
            self._cb(self._level)

    def _end(self, g, dx, dy):
        if self._drag is None:
            return
        self._drag = None
        # the last position always goes through, throttle or not
        self._held = time.monotonic() + self._HOLD_AFTER
        if self._cb:
            self._cb(self._level)
