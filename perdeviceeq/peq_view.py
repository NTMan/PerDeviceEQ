# -*- coding: utf-8 -*-
"""PeqView: the one parametric-EQ editor.

A response graph over a band table, rendered and driven identically
wherever an EQ is edited -- the device correction and the taste
layers must look and feel like the same instrument, because they
are. The view owns NO storage: every edit is reported through
``on_changed(bands, final)`` with the bands as plain dicts, and the
owner decides what persistence, undo and application mean (the
correction editor keeps an undo stack; a taste layer writes
through). Context curves -- a measurement behind a correction --
are injected via :meth:`set_curves`, never fetched.

Rendering and interaction are lifted verbatim from the main
window's editor: the same margins, palette, dB window around the
preamp, 11 px handle hit radius, create-on-empty-plot, the
frequency guard for sub-plot trim bands, remove on right click.
"""
import math
import time

import gi
from . import focus
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

from . import curve_view as cv
from . import eq
from .eq import FMIN, FMAX

DB_MAX = 24.0
_TYPES = ["PK", "LSC", "HSC"]
# the four house lines, for the label that names one of them
_LINE_COL = {"measured": (0.85, 0.85, 0.90),
             "predicted": (0.45, 0.95, 0.55),
             "EQ": (0.30, 0.78, 1.0),
             "target": (0.95, 0.85, 0.40)}


def _log_freqs(n=240):
    a, b = math.log10(FMIN), math.log10(FMAX)
    return [10 ** (a + (b - a) * i / (n - 1)) for i in range(n)]


def _hsv(h, s, v):
    i = int(h * 6.0); f = h * 6.0 - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    return [(v, t, p), (q, v, p), (p, v, t),
            (p, q, v), (t, p, v), (v, p, q)][i % 6]


def _band_color(f):
    """Rainbow by log frequency (blue=low .. red=high)."""
    lf = math.log10(min(FMAX, max(FMIN, f)))
    t = (lf - math.log10(FMIN)) / (math.log10(FMAX) - math.log10(FMIN))
    return _hsv((1.0 - t) * 0.66, 0.65, 1.0)


def _tame_scroll(widget, handler):
    ctrl = Gtk.EventControllerScroll.new(
        Gtk.EventControllerScrollFlags.VERTICAL)
    ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    ctrl.connect("scroll", handler)
    widget.add_controller(ctrl)


class PeqView(Gtk.Box):
    """Graph + band table; edits go to ``on_changed(bands, final)``.

    ``final`` is False during a live drag and True when the edit
    settles (drag end, a spin/dropdown/toggle change, add, remove);
    write-through owners can persist on every call and do the
    expensive follow-ups only on final ones.
    """

    def __init__(self, on_changed, preamp=0.0, compact=False,
                 on_import_file=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL,
                         spacing=6)
        self._on_changed = on_changed
        # an editor verb, not an "import": replaces THIS view's
        # bands from a parametric-EQ text file. Rendered only
        # where the owner wires it (the device channel card) --
        # a document-level import lives in the profile menu.
        self._on_import_file = on_import_file
        self._preamp = float(preamp)
        self._bands = []
        self._floor = []  # sealed stages: drawn, never listed
        self._zone = None  # trust edges: drawn always
        self._tgt = None   # lawful target: the gain law's silhouette
        self._pin = set()         # click-pinned curve names
        self._hover = None        # legend name under pointer
        self._cursor = None       # pointer for the crosshair
        self._legend_hits = []    # (x0,y0,x1,y1,name)
        self._curves = None         # (freqs, measured, spread, band)
        self._traces = {}           # name -> [(x, y, value)] as drawn
        self._under = None          # the line under the pointer
        self._under_v = None
        self._plot = None
        self._drag_band = None
        self._loading = False
        self._active = True

        self.graph = Gtk.DrawingArea()
        self.graph.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["EQ curve: band responses and their sum"])
        self.graph.set_content_height(150 if compact else 220)
        self.graph.set_hexpand(True)
        self.graph.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.graph.add_controller(drag)
        mo = Gtk.EventControllerMotion()
        mo.connect("motion", self._on_motion)
        mo.connect("leave", self._on_hover_leave)
        self.graph.add_controller(mo)
        rclick = Gtk.GestureClick()
        rclick.set_button(3)
        rclick.connect("pressed", self._on_right_click)
        self.graph.add_controller(rclick)
        self.append(self.graph)

        # the architect's spec verbatim: the Measure window's
        # EQ-range handles repeated under the PEQ graph -- two
        # draggable edges on the graph's own log axis, shown
        # only while speaker protection is engaged
        self.protect_strip = Gtk.DrawingArea()
        self.protect_strip.set_content_height(26)
        self.protect_strip.set_hexpand(True)
        self.protect_strip.set_visible(False)
        self.protect_strip.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Speaker protection range"])
        self.protect_strip.set_draw_func(self._draw_protect)
        pdrag = Gtk.GestureDrag()
        pdrag.connect("drag-begin", self._protect_drag_begin)
        pdrag.connect("drag-update", self._protect_drag_update)
        pdrag.connect("drag-end", self._protect_drag_end)
        self.protect_strip.add_controller(pdrag)
        self.append(self.protect_strip)
        self._protect = None
        self._protect_cb = None
        self._protect_drag = None
        self._protect_emit = 0.0

        self.grid = focus.OrderedGrid(column_spacing=4,
                                      row_spacing=4)
        self._focus_stops = []
        for side in ("top", "bottom", "start", "end"):
            getattr(self.grid, "set_margin_" + side)(3)
        self.append(self.grid)
        self._rebuild_table()

    # ---- public API --------------------------------------------------
    def set_bands(self, band_dicts):
        """Replace the whole band list (dicts; the view keeps Band
        objects internally so a drag can mutate freq/gain).

        A load is the one moment the table may reorder itself:
        the solver hands its bands over in placement order and
        nobody is looking at a row yet.
        """
        self._bands = [eq.Band.from_dict(b) for b in (band_dicts or [])]
        self._bands.sort(key=lambda b: b.freq)
        self._drag_band = None
        self._rebuild_table()
        self.graph.queue_draw()

    def get_bands(self):
        return [b.to_dict() for b in self._bands]

    def set_protection(self, lo, visible, on_change=None):
        """Feed the protection strip: lo is the floor's
        frequency, the ONE handle -- the ceiling came down and
        the zone lost its ticks here (a fit organ has no
        business marking a manual one). on_change(lo, final)
        fires throttled during a drag and once with final=True
        at its end."""
        self._protect = lo
        self._protect_cb = on_change
        self.protect_strip.set_visible(bool(visible)
                                       and lo is not None)
        self.protect_strip.queue_draw()

    def _strip_geo(self):
        pl = self._plot
        w = self.protect_strip.get_width()
        if pl and pl[2] > 0:
            return pl[0], pl[2]
        return 10.0, max(1.0, w - 20.0)

    def _px_of(self, f, ml, pw_):
        t = ((math.log10(max(FMIN, min(FMAX, f)))
              - math.log10(FMIN))
             / (math.log10(FMAX) - math.log10(FMIN)))
        return ml + t * pw_

    def _pf_of(self, x, ml, pw_):
        t = min(1.0, max(0.0, (x - ml) / pw_))
        return 10 ** (math.log10(FMIN)
                      + t * (math.log10(FMAX) - math.log10(FMIN)))

    def _draw_protect(self, _a, cr, w, h, *_):
        if not self._protect:
            return
        lo = self._protect
        ml, pw_ = self._strip_geo()
        xr = ml + pw_
        mid = h / 2.0
        cr.set_line_width(1.0)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.5)
        cr.move_to(ml, mid)
        cr.line_to(xr, mid)
        cr.stroke()
        xlo = self._px_of(lo, ml, pw_)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.18)
        cr.rectangle(ml, 2, max(0.0, xlo - ml), h - 4)
        cr.fill()
        cr.set_source_rgba(0.21, 0.52, 0.89, 0.95)
        cr.set_line_width(3.0)
        cr.move_to(xlo, 2)
        cr.line_to(xlo, h - 2)
        cr.stroke()
        # the number rides the handle: the strip caption could not
        # hold it (it was the tail that got cut off), and a floor
        # is set by watching where you are as you drag
        cr.select_font_face("Sans", 0, 0)
        cr.set_font_size(9)
        lab = cv.fmt_hz(lo) + " Hz"
        ext = cr.text_extents(lab)
        tx = xlo + 5
        if tx + ext.width > xr:
            tx = xlo - 5 - ext.width
        cr.move_to(max(ml, tx), 11)
        cr.show_text(lab)

    def _protect_drag_begin(self, _g, sx, _sy):
        self._protect_drag = bool(self._protect)

    def _protect_drag_update(self, g, ox, _oy):
        if not self._protect_drag or not self._protect:
            return
        ok, sx, _sy = g.get_start_point()
        if not ok:
            return
        ml, pw_ = self._strip_geo()
        f = self._pf_of(sx + ox, ml, pw_)
        lo = max(FMIN, min(f, FMAX / 1.2))
        self._protect = lo
        self.protect_strip.queue_draw()
        now = time.monotonic()
        if self._protect_cb and now - self._protect_emit > 0.15:
            self._protect_emit = now
            self._protect_cb(lo, False)

    def _protect_drag_end(self, *_a):
        if self._protect_drag and self._protect \
                and self._protect_cb:
            self._protect_cb(self._protect, True)
        self._protect_drag = None

    def set_zone(self, zone):
        """The measured trust zone's BOTH edges, drawn always:
        a light shade beyond them and a dashed line on each.
        The graph tells where the measurement's honesty ends
        even when the floor organ sleeps -- the architect asked
        why an 18.4 kHz border he computed was nowhere to be
        seen."""
        self._zone = zone
        self.graph.queue_draw()

    def set_target(self, tgt):
        """The curve the solver LAWFULLY aims at, aligned to
        set_curves' grid; None entries mark points outside the
        trust zone. Drawn as a dashed gold line at predicted's
        own level -- the metric is level-free (the constant
        rides in the preamp), so the target's SHAPE is the law
        and its height follows the prediction it judges."""
        self._tgt = tgt
        self.graph.queue_draw()

    def _names(self):
        """The lines this picture WILL contain, decided before the
        brush touches it.

        Reading it back from what had been drawn made the answer one
        frame old, and one frame is forever when the frame that
        changed the picture is the last one: switching the eye off
        left EQ dimmed by a line that was already gone, until the
        pointer came back to the canvas and asked for another frame.
        The target's threshold lives here and the drawing asks THIS
        method, so the two can never drift apart."""
        names = {"EQ"}
        if self._curves is None:
            return names
        fo = self._curves[0]
        names |= {"measured", "predicted"}
        tgt = self._tgt
        if tgt is not None and len(tgt) == len(fo):
            if sum(1 for t in tgt if t is not None) > 8:
                names.add("target")
        return names

    def _lit(self):
        """The pinned names THIS PICTURE ACTUALLY CONTAINS.

        The pointer used to be folded in here, which made hover
        invisible whenever everything was pinned -- the state that
        already looks like nothing pinned. It has its own level in
        _dress now.

        And the set is intersected with the lines this picture
        contains, because otherwise the eye button built a dead
        end: pin any name but EQ while the legend is there, switch
        the legend off, and EQ stood dimmed by an absent line with
        no hand left to lift it. 0046 said a hidden legend has no
        hands; a pin is a hand too. The pins stay in memory and
        return with the eye."""
        return set(self._pin) & self._names()

    def _dress(self, name, r, g, b, a, w):
        """Highlight dressing: lit curves brighten and thicken,
        the rest step back to a readable shadow (0.35x, not
        darkness -- divergence is a comparison and the
        neighbours must stay legible). Nothing lit = the house
        dress unchanged."""
        if name in (self._hover, self._under):
            return r, g, b, min(1.0, a + 0.25), w + 1.6
        lit = self._lit()
        if lit:
            if name in lit:
                a, w = min(1.0, a + 0.25), w + 0.8
            else:
                a, w = a * 0.35, max(0.8, w - 0.4)
        return r, g, b, a, w

    def _legend_at(self, x, y):
        if self._curves is None:
            return None           # a hidden legend has no hands
        for x0, y0, x1, y1, lab in self._legend_hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return lab
        return None

    def _on_motion(self, _c, x, y):
        """REW-style: hovering a legend name lights its curve,
        leaving releases it; the pointer cursor over the legend
        says the names are live. A click pins the highlight --
        either road leads to the same light. The repaint goes
        to the CANVAS: a GTK4 DrawingArea re-renders only when
        queued itself, and the field photo proved it -- the
        cursor flipped while the curves stood frozen."""
        hit = self._legend_at(x, y)
        moved = self._cursor != (x, y)
        self._cursor = (x, y)
        if hit != self._hover:
            self._hover = hit
            self.graph.set_cursor_from_name(
                "pointer" if hit else None)
            self.graph.queue_draw()
        elif moved:
            self.graph.queue_draw()

    def _on_hover_leave(self, *_a):
        self._cursor = None
        if self._hover:
            self._hover = None
            self.graph.set_cursor_from_name(None)
        self.graph.queue_draw()

    def set_floor(self, band_dicts):
        """Sealed floor stages: the curve and the prediction
        wear them, the table never does -- their handle lives
        in the Measure window, and its name is trust."""
        self._floor = [eq.Band.from_dict(b)
                       for b in (band_dicts or [])]
        self.graph.queue_draw()

    def set_active(self, active):
        """Bypass dimming: an inactive EQ draws gray with a dashed
        zero line, exactly like the main editor always did."""
        self._active = bool(active)
        self.graph.queue_draw()

    def set_preamp(self, v):
        """The dB window follows the preamp, like the main editor."""
        self._preamp = float(v or 0.0)
        self.graph.queue_draw()

    def set_curves(self, freqs=None, measured=None, spread=None,
                   band=None):
        """Context behind the EQ: a measured curve (with an optional
        spread fan and certified band) plus the predicted result,
        which the view derives as measured + response. None clears."""
        if freqs is None or measured is None:
            self._curves = None
        else:
            meas = list(measured)
            spr = list(spread) if spread is not None else None
            fan = [m + (spr[i] if spr else 0.0)
                   for i, m in enumerate(meas)]
            dip = [m - (spr[i] if spr else 0.0)
                   for i, m in enumerate(meas)]
            # extremes cached HERE: _y_window runs per mapped point
            # during hit tests and must stay O(1); recomputing these
            # lists inside it made the draw quadratic
            self._curves = (list(freqs), meas, spr,
                            tuple(band) if band is not None else None,
                            min(dip), max(fan))
        self.graph.queue_draw()

    # ---- geometry -----------------------------------------------------
    def _y_window(self):
        lo, hi = -DB_MAX + self._preamp, DB_MAX + self._preamp
        c = self._curves
        if c is not None:
            lo = min(lo, c[4])
            hi = max(hi, c[5])
        return lo, hi

    def _x_of(self, f):
        ml, mt, pw_, ph = self._plot
        return ml + (math.log10(f) - math.log10(FMIN)) / \
            (math.log10(FMAX) - math.log10(FMIN)) * pw_

    def _y_of(self, db):
        ml, mt, pw_, ph = self._plot
        lo, hi = self._y_window()
        return mt + (hi - db) / (hi - lo) * ph

    def _f_of(self, x):
        ml, mt, pw_, ph = self._plot
        if pw_ <= 0:
            return None
        t = min(1.0, max(0.0, (x - ml) / pw_))
        return 10 ** (math.log10(FMIN)
                      + t * (math.log10(FMAX) - math.log10(FMIN)))

    def _db_of(self, y):
        ml, mt, pw_, ph = self._plot
        if ph <= 0:
            return None
        lo, hi = self._y_window()
        t = min(1.0, max(0.0, (y - mt) / ph))
        return hi - t * (hi - lo)

    def _line_at(self, x, y):
        """(name, dB) of the curve the pointer stands on. Reads the
        polylines recorded during the last draw, so what gets named
        is exactly what was painted; that makes it one frame old,
        which no pointer can notice because every motion queues its
        own redraw. Silent over a band handle: there the hand is
        aiming to grab, not to read."""
        if self._plot is None or self._hit_band(x, y) is not None:
            return None, None
        ml, mt, pw_, ph = self._plot
        if not (ml <= x <= ml + pw_ and mt <= y <= mt + ph):
            return None, None
        # a polyline from the last frame may name a line the eye
        # has just switched off; the roster decides who exists
        live = self._names()
        return cv.nearest_trace(
            {k: v for k, v in (self._traces or {}).items()
             if k in live}, x, y)

    def _hit_band(self, x, y, r=11):
        if not self._plot:
            return None
        best, bestd = None, r * r
        wlo, whi = self._y_window()
        for b in self._bands:
            bx = self._x_of(max(b.freq, FMIN))  # freq-0 trim: left edge
            by = self._y_of(max(wlo, min(whi, b.gain)))
            d = (bx - x) ** 2 + (by - y) ** 2
            if d <= bestd:
                best, bestd = b, d
        return best

    # ---- drawing ------------------------------------------------------
    def _draw(self, _area, cr, w, h, *_):
        ml, mr, mt, mb = 44, 10, 10, 22
        pw_, ph = max(1, w - ml - mr), max(1, h - mt - mb)
        self._plot = (ml, mt, pw_, ph)
        cr.set_source_rgb(0.12, 0.12, 0.14); cr.paint()
        cr.rectangle(ml, mt, pw_, ph)
        cr.set_source_rgb(0.08, 0.08, 0.10); cr.fill()
        wlo, whi = self._y_window()
        lg_lo = math.log10(FMIN)
        lg_span = math.log10(FMAX) - lg_lo

        def x_of(f):
            return ml + (math.log10(f) - lg_lo) / lg_span * pw_

        def y_of(db):
            return mt + (whi - db) / (whi - wlo) * ph

        cr.set_line_width(1.0)
        cr.select_font_face("Sans", 0, 0); cr.set_font_size(9)
        for f in (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000,
                  20000):
            x = x_of(f)
            cr.set_source_rgba(1, 1, 1, 0.10)
            cr.move_to(x, mt); cr.line_to(x, mt + ph); cr.stroke()
            cr.set_source_rgba(1, 1, 1, 0.45)
            lab = ("%dk" % (f // 1000)) if f >= 1000 else str(f)
            cr.move_to(x - 8, mt + ph + 14); cr.show_text(lab)
        for db in range(int(math.ceil(wlo / 6.0)) * 6,
                        int(math.floor(whi)) + 1, 6):
            y = y_of(db)
            cr.set_source_rgba(1, 1, 1, 0.16 if db == 0 else 0.08)
            cr.move_to(ml, y); cr.line_to(ml + pw_, y); cr.stroke()
            cr.set_source_rgba(1, 1, 1, 0.45)
            cr.move_to(4, y + 3); cr.show_text("%+d" % db)

        if self._zone is not None:
            zlo, zhi = self._zone
            for f0, left in ((zlo, True), (zhi, False)):
                if not (FMIN < f0 < FMAX):
                    continue
                x = x_of(f0)
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.10)
                if left:
                    cr.rectangle(ml, mt, max(0.0, x - ml), ph)
                else:
                    cr.rectangle(x, mt, max(0.0, ml + pw_ - x),
                                 ph)
                cr.fill()
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.55)
                cr.set_dash([3, 3], 0)
                cr.move_to(x, mt)
                cr.line_to(x, mt + ph)
                cr.stroke()
                cr.set_dash([], 0)

        if self._curves is None:
            # the eye is off: no curves, no legend -- and no
            # GHOST legend either. The hit rectangles used to
            # outlive the drawing, leaving an invisible hover
            # strip that kept toggling curve highlights on a
            # plot that showed none of them.
            self._legend_hits = []
            self._hover = None
        traces = {}
        if self._cursor is not None:
            self._under, self._under_v = self._line_at(*self._cursor)
        else:
            self._under, self._under_v = None, None
        if self._curves is not None:
            fo, meas, spread, band = self._curves[:4]
            cr.save()
            cr.rectangle(ml, mt, pw_, ph)
            cr.clip()
            if spread is not None:
                lit = self._lit()
                sa = (0.14 if not lit or "measured" in lit
                      or self._hover == "measured" else 0.05)
                cr.set_source_rgba(0.55, 0.65, 0.85, sa)
                for i, f in enumerate(fo):
                    x = x_of(f)
                    y = y_of(meas[i] + spread[i])
                    cr.move_to(x, y) if i == 0 else cr.line_to(x, y)
                for i in range(len(fo) - 1, -1, -1):
                    cr.line_to(x_of(fo[i]),
                               y_of(meas[i] - spread[i]))
                cr.close_path()
                cr.fill()
            mr, mg, mb, ma, mw = self._dress(
                "measured", 0.85, 0.85, 0.90, 0.55, 1.2)
            cr.set_source_rgba(mr, mg, mb, ma)
            cr.set_line_width(mw)
            tr_m = []
            for i, f in enumerate(fo):
                x, y = x_of(f), y_of(meas[i])
                tr_m.append((x, y, meas[i]))
                cr.move_to(x, y) if i == 0 else cr.line_to(x, y)
            cr.stroke()
            traces["measured"] = tr_m
            resp = eq.response_db(self._preamp,
                                  self._bands + self._floor, fo)
            tgt = self._tgt
            if "target" in self._names():
                sel = [i for i, t in enumerate(tgt)
                       if t is not None]
                shift = (sum(meas[i] + resp[i] for i in sel)
                         - sum(tgt[i] for i in sel)) / len(sel)
                tr, tg, tb, ta, tw = self._dress(
                    "target", 0.95, 0.85, 0.40, 0.55, 1.0)
                cr.set_source_rgba(tr, tg, tb, ta)
                cr.set_line_width(tw)
                cr.set_dash([5, 3], 0)
                first = True
                tr_t = []
                for i in sel:
                    x = x_of(fo[i])
                    y = y_of(tgt[i] + shift)
                    tr_t.append((x, y, tgt[i] + shift))
                    cr.move_to(x, y) if first \
                        else cr.line_to(x, y)
                    first = False
                cr.stroke()
                cr.set_dash([], 0)
                traces["target"] = tr_t
            pr, pg, pb, pa, pw2 = self._dress(
                "predicted", 0.45, 0.95, 0.55, 0.90, 1.5)
            cr.set_source_rgba(pr, pg, pb, pa)
            cr.set_line_width(pw2)
            tr_p = []
            for i, f in enumerate(fo):
                v = meas[i] + resp[i]
                x, y = x_of(f), y_of(v)
                tr_p.append((x, y, v))
                cr.move_to(x, y) if i == 0 else cr.line_to(x, y)
            cr.stroke()
            traces["predicted"] = tr_p
            cr.set_source_rgba(0, 0, 0, 0.30)
            if band is not None:
                blo, bhi = max(band[0], FMIN), min(band[1], FMAX)
                if blo > FMIN:
                    cr.rectangle(ml, mt, x_of(blo) - ml, ph)
                    cr.fill()
                if bhi < FMAX:
                    cr.rectangle(x_of(bhi), mt,
                                 ml + pw_ - x_of(bhi), ph)
                    cr.fill()
            else:                    # nothing certified: dim it all
                cr.rectangle(ml, mt, pw_, ph)
                cr.fill()
            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(9)
            lx, ly = ml + 10, mt + 14
            labels = [
                ("measured", (0.85, 0.85, 0.90, 0.9)),
                ("predicted", (0.45, 0.95, 0.55, 0.9)),
                ("EQ", (0.30, 0.78, 1.0, 0.9))]
            if self._tgt is not None:
                labels.append(("target", (0.95, 0.85, 0.40, 0.9)))
            self._legend_hits = []
            lit = self._lit()
            for lab, rgba in labels:
                r0, g0, b0, a0 = rgba
                if lit and lab not in lit and lab != self._hover:
                    a0 *= 0.35
                cr.set_source_rgba(r0, g0, b0, a0)
                cr.set_line_width(2.0)
                cr.move_to(lx, ly - 3)
                cr.line_to(lx + 14, ly - 3)
                cr.stroke()
                # a pinned name wears a dot: brightness alone
                # cannot tell "nothing pinned" from "all pinned"
                shown = "\u2022 " + lab if lab in lit else lab
                cr.move_to(lx + 18, ly)
                cr.show_text(shown)
                tw2 = cr.text_extents(shown).width
                self._legend_hits.append(
                    (lx - 4, ly - 14, lx + 18 + tw2 + 4,
                     ly + 8, lab))
                lx += 18 + tw2 + 14
            cr.restore()

        cv.draw_crosshair(
            cr, (ml, mt, pw_, ph), self._cursor,
            self._f_of, self._db_of,
            rgba=(1.0, 1.0, 1.0, 0.35),
            text_rgba=(1.0, 1.0, 1.0, 0.85),
            bg_rgba=(0.08, 0.08, 0.10, 0.90))
        freqs = _log_freqs(int(max(60, pw_)))
        curve = eq.response_db(self._preamp,
                                self._bands + self._floor, freqs)
        if self._active:
            er, eg, eb, ea, ew = self._dress(
                "EQ", 0.30, 0.78, 1.0, 1.0, 2.0)
        else:
            er, eg, eb, ea, ew = self._dress(
                "EQ", 0.6, 0.6, 0.6, 0.7, 2.0)
        cr.set_source_rgba(er, eg, eb, ea)
        cr.set_line_width(ew)
        tr_e = []
        for i, f in enumerate(freqs):
            db = max(wlo, min(whi, curve[i]))
            px, py = x_of(f), y_of(db)
            tr_e.append((px, py, db))
            cr.move_to(px, py) if i == 0 else cr.line_to(px, py)
        cr.stroke()
        traces["EQ"] = tr_e
        self._traces = traces
        if self._under is not None:
            r0, g0, b0 = _LINE_COL.get(self._under, (1.0, 1.0, 1.0))
            cv.name_the_line(cr, (ml, mt, pw_, ph), self._cursor,
                             self._under, self._under_v,
                             (r0, g0, b0, 1.0),
                             bg_rgba=(0.08, 0.08, 0.10, 0.90))
        if not self._active:
            cr.set_source_rgba(0.30, 0.78, 1.0, 0.5)
            cr.set_line_width(1.5); cr.set_dash([4, 4], 0)
            cr.move_to(ml, y_of(0))
            cr.line_to(ml + pw_, y_of(0)); cr.stroke()
            cr.set_dash([], 0)

        for b in self._bands:
            bx = x_of(max(b.freq, FMIN))
            by = y_of(max(wlo, min(whi, b.gain)))
            r, g, bl = _band_color(b.freq)
            cr.arc(bx, by, 5.5, 0, 2 * math.pi)
            if b.enabled:
                cr.set_source_rgb(r, g, bl); cr.fill_preserve()
                cr.set_source_rgba(0, 0, 0, 0.55)
                cr.set_line_width(1.0); cr.stroke()
            else:
                cr.set_source_rgba(r, g, bl, 0.7)
                cr.set_line_width(1.5); cr.stroke()

    # ---- graph interaction ---------------------------------------------
    def _on_drag_begin(self, gesture, sx, sy):
        self._drag_band = None
        add = bool(gesture.get_current_event_state()
                   & Gdk.ModifierType.CONTROL_MASK)
        lab = self._legend_at(sx, sy)
        if lab is None and add:
            # Ctrl is the selection modifier and never a sculptor:
            # under it this graph pins the line it is standing on,
            # exactly as a take canvas does, and no band is born.
            # That leaves ONE deliberate difference between the two
            # pictures -- a bare click here builds a biquad.
            name, _v = self._line_at(sx, sy)
            if name is not None:
                self._pin.symmetric_difference_update({name})
                self.graph.queue_draw()
            return
        if lab is not None:
            # a click on the legend pins the highlight -- and
            # never gives birth to a band under the names. Plain
            # click replaces the set, Ctrl adds or removes, and a
            # plain click on the only pinned name lets it go: the
            # measure window's canvases speak the same grammar
            if add:
                self._pin.symmetric_difference_update({lab})
            elif self._pin == {lab}:
                self._pin = set()
            else:
                self._pin = {lab}
            self.graph.queue_draw()
            return
        if not self._plot:
            return
        b = self._hit_band(sx, sy)
        created = False
        if b is None:                 # empty spot -> a band is born
            f = self._f_of(sx); db = self._db_of(sy)
            if f is None or db is None:
                return
            b = eq.Band("PK", f, db, 1.0, True)
            self._bands.append(b)
            created = True
        self._drag_band = b
        if created:
            self._rebuild_table()
            self._emit(True)
        self.graph.queue_draw()

    def _on_drag_update(self, gesture, ox, oy):
        if self._drag_band is None or not self._plot:
            return
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        f = self._f_of(sx + ox); db = self._db_of(sy + oy)
        if f is not None and self._drag_band.freq >= FMIN:
            # a sub-plot band (the freq-0 balance trim) keeps its
            # frequency under drag -- the plot cannot express it and
            # a vertical gain drag must not retune it to 20 Hz
            self._drag_band.freq = f
        if db is not None:
            self._drag_band.gain = db
        self.graph.queue_draw()
        self._emit(False)             # live; no table rebuild

    def _on_drag_end(self, gesture, ox, oy):
        if self._drag_band is None:
            return
        self._drag_band = None
        self._rebuild_table()
        self._emit(True)

    def _on_right_click(self, gesture, n, x, y):
        b = self._hit_band(x, y)
        if b is not None and b in self._bands:
            self._bands.remove(b)
            self._rebuild_table()
            self.graph.queue_draw()   # the handle must die with it
            self._emit(True)

    # ---- the band table -------------------------------------------------
    def _rebuild_table(self):
        self._focus_stops = []
        child = self.grid.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.grid.remove(child)
            child = nxt
        heads = ("", "Type", "Freq (Hz)", "Gain (dB)", "Q",
                 "On", "", "")
        for c, t in enumerate(heads):
            lbl = Gtk.Label(label=t, xalign=0.0)
            lbl.add_css_class("dim-label")
            lbl.add_css_class("caption")
            if t.startswith("Freq"):
                self.grid.attach(self._sort_head(lbl), c, 0, 1, 1)
            else:
                self.grid.attach(lbl, c, 0, 1, 1)
        # the rows ARE the storage order, verbatim -- nothing
        # reshuffles under the hand that is editing it
        shown = self._bands
        for i, b in enumerate(shown):
            self._attach_band(i, b)
        def _action(icon, label):
            # flat buttons carry their affordance in hover alone;
            # an icon restores it at rest (the GNOME list-action
            # idiom), without the visual weight of a border
            b = Gtk.Button()
            b.add_css_class("flat")
            box = Gtk.Box(spacing=6)
            box.append(Gtk.Image.new_from_icon_name(icon))
            box.append(Gtk.Label(label=label))
            b.set_child(box)
            return b
        acts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                       spacing=6, halign=Gtk.Align.START)
        # the toolbar class is the lawful home for flat here:
        # the container declares the idiom and rule H3 has a
        # bar to point at; flat is ALSO declared on the buttons
        # (belt: stylesheet flattening is not trusted alone)
        acts.add_css_class("toolbar")
        addb = _action("list-add-symbolic", "Add band")
        addb.connect("clicked", self._on_add)
        acts.append(addb)
        if self._on_import_file is not None:
            repl = _action("document-open-symbolic",
                           "Replace bands from file\u2026")
            repl.set_tooltip_text(
                "Replace this channel's bands from a "
                "parametric-EQ text file (REW / AutoEq)")
            repl.connect("clicked",
                         lambda *_: self._on_import_file())
            acts.append(repl)
        self.grid.attach(acts, 1, len(self._bands) + 1, 7, 1)
        # the walk's verdict: Tab reads a band row as written --
        # type, freq, gain, Q, on/off, delete -- the on/off is
        # second to last, not first, whatever geometry says
        self._focus_stops.append(acts)
        self.grid.set_focus_order(self._focus_stops)

    def _sort_head(self, lbl):
        """The Freq header, as a hand the user can lay on."""
        btn = Gtk.Button()
        btn.set_child(lbl)
        btn.add_css_class("flat")
        btn.set_tooltip_text("Sort the rows by frequency")
        btn.connect("clicked", self._on_sort_freq)
        return btn

    def _on_sort_freq(self, *_a):
        """Order the rows by frequency, once, on request.

        No edit is emitted: the response does not depend on the
        order, and a load sorts anyway, so the click buys the
        reading order without forking a built-in profile or
        resetting the headroom session behind the user's back.
        """
        if len(self._bands) < 2:
            return
        self._bands.sort(key=lambda b: b.freq)
        self._rebuild_table()

    def _attach_band(self, i, b):
        row = i + 1
        dot = Gtk.DrawingArea()
        dot.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Band color"])
        dot.set_content_width(12)
        dot.set_content_height(12)
        dot.set_valign(Gtk.Align.CENTER)
        dot.set_draw_func(self._make_dot_draw(b))
        self.grid.attach(dot, 0, row, 1, 1)
        dd = Gtk.DropDown.new_from_strings(_TYPES)
        dd.set_selected(_TYPES.index(b.type)
                        if b.type in _TYPES else 0)
        dd.connect("notify::selected",
                   lambda d, *_a, b=b:
                   self._write(b, "type", _TYPES[d.get_selected()]))
        _tame_scroll(dd, self._on_widget_scroll)
        self.grid.attach(dd, 1, row, 1, 1)
        self._focus_stops.append(dd)
        col = 2
        for key, lo, hi, step, dig, tip in (
                ("freq", 10.0, 20000.0, 1.0, 0, "Frequency, Hz"),
                ("gain", -24.0, 24.0, 0.1, 1, "Gain, dB"),
                ("q", 0.1, 10.0, 0.05, 2, "Q")):
            sp = Gtk.SpinButton.new_with_range(lo, hi, step)
            sp.set_digits(dig)
            sp.set_hexpand(True)
            sp.set_width_chars(5)
            sp.set_max_width_chars(5)
            sp.set_tooltip_text(tip)
            sp.set_value(float(getattr(b, key)))
            sp.connect("value-changed",
                       lambda spb, key=key, b=b:
                       self._write(b, key, spb.get_value()))
            _tame_scroll(sp, self._on_widget_scroll)
            self.grid.attach(sp, col, row, 1, 1)
            self._focus_stops.append(sp)
            col += 1
        sw = Gtk.CheckButton()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(bool(b.enabled))
        sw.set_tooltip_text("Band on/off")
        sw.connect("toggled",
                   lambda swb, b=b:
                   self._write(b, "enabled", swb.get_active()))
        self.grid.attach(sw, 5, row, 1, 1)
        self._focus_stops.append(sw)
        sep = Gtk.Separator(
            orientation=Gtk.Orientation.VERTICAL)
        self.grid.attach(sep, 6, row, 1, 1)
        tr = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        tr.add_css_class("flat")
        tr.set_tooltip_text("Delete this band")
        tr.connect("clicked", lambda *_a, b=b: self._on_del(b))
        self.grid.attach(tr, 7, row, 1, 1)
        self._focus_stops.append(tr)

    def _make_dot_draw(self, b):
        def draw(_a, cr, w, h, *_):
            r, g, bl = _band_color(b.freq)
            cr.arc(w / 2.0, h / 2.0, min(w, h) / 2.0 - 1,
                   0, 2 * math.pi)
            if b.enabled:
                cr.set_source_rgb(r, g, bl); cr.fill()
            else:
                cr.set_source_rgba(r, g, bl, 0.7)
                cr.set_line_width(1.5); cr.stroke()
        return draw

    # ---- edits ----------------------------------------------------------
    def _write(self, b, key, val):
        if self._loading or b not in self._bands:
            return
        setattr(b, key, val if key in ("type", "enabled")
                else float(val))
        self.graph.queue_draw()
        self._emit(True)

    def _on_add(self, *_):
        self._bands.append(eq.Band("PK", 1000.0, 0.0, 1.0, True))
        self._rebuild_table()
        self.graph.queue_draw()
        self._emit(True)

    def _on_del(self, b):
        if b in self._bands:
            self._bands.remove(b)
            self._rebuild_table()
            self.graph.queue_draw()
            self._emit(True)

    def _emit(self, final):
        if not self._loading:
            self._on_changed(self.get_bands(), final)

    # ---- scroll taming ---------------------------------------------------
    def _on_widget_scroll(self, ctrl, dx, dy):
        """Forward the wheel to the enclosing scrolled page; the
        hovered value stays untouched."""
        w = ctrl.get_widget()
        sw = w.get_ancestor(Gtk.ScrolledWindow) if w else None
        if sw is not None:
            adj = sw.get_vadjustment()
            if adj is not None:
                step = adj.get_step_increment()
                if step <= 0:
                    step = 30.0
                new = adj.get_value() + dy * step
                new = max(adj.get_lower(),
                          min(new,
                              adj.get_upper() - adj.get_page_size()))
                adj.set_value(new)
        return True


class CollapsibleCard(Gtk.Box):
    """The notification pattern GNOME Shell draws by hand, in GTK
    parts: a .card whose clickable header row (rotating chevron on
    the right) sits over a Gtk.Revealer body with a slide-down
    transition. Header children that consume clicks (buttons, menu
    buttons) keep them; a click anywhere else on the row toggles.
    on_toggled(expanded) fires after every user toggle, so the
    owner can persist the state."""

    def __init__(self, expanded=False, on_toggled=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("card")
        self._on_toggled = on_toggled
        self._last = None
        self._header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # the expander-row header is a structured container:
        # rule H3 treats it like a bar (see tools/hig_audit.py)
        self._header.add_css_class("card-header")
        for side in ("top", "bottom"):
            getattr(self._header, "set_margin_" + side)(6)
        for side in ("start", "end"):
            getattr(self._header, "set_margin_" + side)(12)
        self._chevron = Gtk.Image.new_from_icon_name(
            "pan-down-symbolic")
        self._chevron.set_valign(Gtk.Align.CENTER)
        self._header.append(self._chevron)
        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("released", self._on_header_click)
        self._header.add_controller(click)
        self._rev = Gtk.Revealer()
        self._rev.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._rev.set_transition_duration(200)
        self._rev.set_reveal_child(bool(expanded))
        self.append(self._header)
        self.append(self._rev)
        self._sync_chevron()

    def add_header(self, w, expand=False):
        """Insert a header widget before the chevron, in call
        order."""
        if expand:
            w.set_hexpand(True)
        if self._last is None:
            self._header.prepend(w)
        else:
            self._header.insert_child_after(w, self._last)
        self._last = w

    def set_body(self, w):
        self._rev.set_child(w)

    def get_expanded(self):
        return self._rev.get_reveal_child()

    def set_expanded(self, v):
        self._rev.set_reveal_child(bool(v))
        self._sync_chevron()

    def _sync_chevron(self):
        self._chevron.set_from_icon_name(
            "pan-up-symbolic" if self.get_expanded()
            else "pan-down-symbolic")

    def _on_header_click(self, *_):
        self.set_expanded(not self.get_expanded())
        if self._on_toggled:
            self._on_toggled(self.get_expanded())
