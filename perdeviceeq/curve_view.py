# -*- coding: utf-8 -*-
"""One painter for every curve the measure window draws.

The window used to carry three unrelated painters -- the take row,
the channel summary and the group confession -- each with its own
axis, its own idea of a grid and no legend at all. This module is
the single component they all become.

It is deliberately GTK-free: it paints onto a cairo context handed
to it, so the whole picture (the grid, its labels, the legend
rectangles, the broken pen at an abstention) is judged by tests in
a sandbox that has no gi. The window keeps only the DrawingArea
and the pointer. The GTK-blind suite let two field breakages
through in one night; a painter that can be read by a test is the
answer to that.

ONE AXIS carries everything. Harmonics are drawn as LEVELS -- the
fundamental plus the measured ratio -- and not on a private ratio
scale, so the vertical gap between a response and its distortion
IS the number the eye is after, the way a REW distortion plot
reads. Where nothing can testify at all (above the sweep top over
two there is no second harmonic to catch) the land is shaded and
named, so a pen that stops reads as "no evidence" instead of "no
distortion".
"""
import math

import numpy as np

FMIN_PLOT, FMAX_PLOT = 20.0, 20000.0
TICKS_HZ = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
ML, MR, MT, MB = 38, 8, 10, 18          # plot margins in pixels
DB_STEP = 6.0                           # horizontal ruler pitch
PAD_DB = 3.0                            # air above and below data

C_RESPONSE = (0.22, 0.52, 0.90, 1.00)
C_GHOST = (0.45, 0.45, 0.45, 0.90)
C_BAD = (0.87, 0.23, 0.23, 1.00)
C_H2 = (0.76, 0.13, 0.13, 0.85)
C_H3 = (0.90, 0.57, 0.00, 0.85)
C_THD = (0.15, 0.15, 0.15, 1.00)
C_NOISE = (0.50, 0.50, 0.50, 0.85)
DIM = 0.28                              # alpha factor of an unlit line


def stride_idx(n, cap=240):
    """Indices for drawing at most ~cap points of an n-point curve.
    Resize-time redraws are Python-loop-bound and every canvas in
    the window repaints on every frame of a drag. The last point
    always rides."""
    if n <= cap:
        return range(n)
    step = max(1, n // cap)
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def log_x(freq, x0, w):
    """x pixel for a frequency on the log axis FMIN..FMAX_PLOT."""
    lo, hi = math.log10(FMIN_PLOT), math.log10(FMAX_PLOT)
    f = min(max(float(freq), FMIN_PLOT), FMAX_PLOT)
    return x0 + (math.log10(f) - lo) / (hi - lo) * w


def tick_label(f):
    return ("%dk" % (f // 1000)) if f >= 1000 else str(int(f))


def as_level(mag, ratio):
    """A harmonic's LEVEL on the response's own axis: the measured
    ratio re fundamental lifted onto the fundamental. None in, None
    out; an abstention stays an abstention."""
    if mag is None or ratio is None:
        return None
    m = np.asarray(mag, float)
    r = np.array([np.nan if v is None else float(v)
                  for v in ratio], float)
    if len(r) != len(m):
        return None
    with np.errstate(all="ignore"):
        return m + r


class Curve:
    """One named line. `harmonic` marks the lines that live or die
    with the sweep's harmonic evidence; `land` shades everything
    under the line (the instrument's own noise floor)."""

    def __init__(self, name, values, rgba, width=1.4, dash=None,
                 harmonic=False, land=False, legend=True):
        self.name = name
        self.values = None if values is None else np.asarray(
            values, float)
        self.rgba = rgba
        self.width = width
        self.dash = dash
        self.harmonic = harmonic
        self.land = land
        self.legend = legend


class Highlight:
    """Which legend name is lit. Hover lights, a click pins, a
    click on the pinned name releases it -- peq_view's grammar,
    shared by every canvas of one channel so pinning THD on the
    summary dims the rest on every take row too."""

    def __init__(self):
        self.hover = None
        self.pinned = None

    def lit(self):
        return self.pinned or self.hover

    def hit(self, name):
        """A click: returns True when the state changed."""
        if name is None:
            if self.pinned is None:
                return False
            self.pinned = None
            return True
        self.pinned = None if self.pinned == name else name
        return True


def _finite(arr):
    if arr is None:
        return np.zeros(0)
    a = np.asarray(arr, float)
    return a[np.isfinite(a)]


def window_db(curves, band=None):
    """The shared vertical window: every finite value of every
    curve, plus the spread band, plus air, snapped to the ruler's
    pitch. Computed ONCE per channel and handed to every canvas so
    the grids align down the list."""
    vals = [_finite(c.values) for c in curves if c is not None]
    if band is not None:
        vals.append(_finite(band[0]))
        vals.append(_finite(band[1]))
    vals = [v for v in vals if len(v)]
    if not vals:
        return -80.0, 0.0
    lo = float(min(float(v.min()) for v in vals)) - PAD_DB
    hi = float(max(float(v.max()) for v in vals)) + PAD_DB
    lo = math.floor(lo / DB_STEP) * DB_STEP
    hi = math.ceil(hi / DB_STEP) * DB_STEP
    if hi - lo < DB_STEP * 2:
        hi = lo + DB_STEP * 2
    return lo, hi


def evidence_hi(freqs, curves):
    """Highest frequency where any harmonic line still testifies,
    or None when none of them ever does."""
    f = np.asarray(freqs, float)
    top = None
    for c in curves:
        if c is None or not c.harmonic or c.values is None:
            continue
        ok = np.isfinite(np.asarray(c.values, float))
        if not ok.any():
            continue
        hf = float(f[:len(ok)][ok].max())
        top = hf if top is None else max(top, hf)
    return top


class Plot:
    """The painter. Build it with the channel's shared window, hand
    its draw() to a DrawingArea, and ask legend_at() where the
    pointer is."""

    def __init__(self, freqs, curves, lo, hi, band=None,
                 state=None, legend=True, title=None,
                 dim_outside=None):
        self.freqs = np.asarray(freqs, float)
        self.curves = [c for c in curves if c is not None]
        self.lo, self.hi = float(lo), float(hi)
        self.band = band
        self.state = state if state is not None else Highlight()
        self.legend = legend
        self.title = title
        self.dim_outside = dim_outside
        self.hits = []
        self.ev_hi = evidence_hi(self.freqs, self.curves)

    def _alpha(self, curve):
        lit = self.state.lit()
        if lit is None or not curve.legend or curve.name == lit:
            return 1.0
        return DIM

    def legend_at(self, x, y):
        """The legend name under the pointer, or None. A canvas
        that drew no legend has no hands (0046's law)."""
        for x0, y0, x1, y1, name in self.hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    def draw(self, _area, cr, w, h, *_):
        pw = max(1, w - ML - MR)
        ph = max(1, h - MT - MB)
        span = max(1e-6, self.hi - self.lo)

        def x_of(f):
            return log_x(f, ML, pw)

        def y_of(v):
            y = MT + (self.hi - float(v)) / span * ph
            return max(MT - 1.0, min(MT + ph + 1.0, y))

        cr.set_source_rgba(0.5, 0.5, 0.5, 0.10)
        cr.rectangle(ML, MT, pw, ph)
        cr.fill()
        self._grid(cr, x_of, y_of, pw, ph)
        self._dead_land(cr, x_of, pw, ph)
        self._spread(cr, x_of, y_of, pw)
        for c in self.curves:
            self._line(cr, c, x_of, y_of, ph)
        self._outside(cr, x_of, pw, ph)
        if self.legend:
            self._legend(cr)
        else:
            self.hits = []
        if self.title:
            cr.set_source_rgba(0.4, 0.4, 0.4, 0.9)
            cr.set_font_size(10)
            ext = cr.text_extents(self.title)
            cr.move_to(ML + pw - ext.width - 4, MT + 12)
            cr.show_text(self.title)

    def _grid(self, cr, x_of, y_of, pw, ph):
        """The ruler, and it says what it is: decades named on the
        log axis, dB named down the left."""
        cr.select_font_face("Sans", 0, 0)
        cr.set_font_size(9)
        cr.set_line_width(1.0)
        for f in TICKS_HZ:
            x = x_of(f)
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.25)
            cr.move_to(x, MT)
            cr.line_to(x, MT + ph)
            cr.stroke()
            cr.set_source_rgba(0.4, 0.4, 0.4, 0.85)
            lab = tick_label(f)
            ext = cr.text_extents(lab)
            cr.move_to(x - ext.width / 2.0, MT + ph + 13)
            cr.show_text(lab)
        g = math.ceil(self.lo / DB_STEP) * DB_STEP
        while g <= self.hi + 1e-9:
            y = y_of(g)
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.25)
            cr.move_to(ML, y)
            cr.line_to(ML + pw, y)
            cr.stroke()
            cr.set_source_rgba(0.4, 0.4, 0.4, 0.85)
            cr.move_to(2, y + 3)
            cr.show_text("%d" % int(round(g)))
            g += DB_STEP
        cr.set_source_rgba(0.4, 0.4, 0.4, 0.85)
        cr.set_font_size(9)
        cr.move_to(2, MT + ph + 13)
        cr.show_text("Hz")

    def _dead_land(self, cr, x_of, pw, ph):
        """Above the last frequency any harmonic can be caught the
        sweep has no evidence -- shade it and name it, so a pen
        that stops is not read as a clean device."""
        if self.ev_hi is None or self.ev_hi >= FMAX_PLOT:
            return
        x = x_of(self.ev_hi)
        if x >= ML + pw - 2:
            return
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.12)
        cr.rectangle(x, MT, ML + pw - x, ph)
        cr.fill()
        cr.set_source_rgba(0.45, 0.45, 0.45, 0.95)
        cr.set_font_size(9)
        lab = "no harmonic evidence"
        ext = cr.text_extents(lab)
        # inside the shaded land when it fits, otherwise leaning
        # on its left edge -- the words must never be the thing
        # that gets cropped, they are the whole point of the mark
        if ext.width < (ML + pw - x) - 6:
            tx = x + 4
        else:
            tx = max(ML + 2, x - 4 - ext.width)
        cr.move_to(tx, MT + ph - 5)
        cr.show_text(lab)

    def _outside(self, cr, x_of, pw, ph):
        """Grey what the fit does not touch: outside the EQ range
        the picture is information, not a promise."""
        if self.dim_outside is None:
            return
        flo, fhi = self.dim_outside
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.38)
        xlo, xhi = x_of(flo), x_of(fhi)
        if xlo > ML:
            cr.rectangle(ML, MT, xlo - ML, ph)
            cr.fill()
        if xhi < ML + pw:
            cr.rectangle(xhi, MT, ML + pw - xhi, ph)
            cr.fill()

    def _spread(self, cr, x_of, y_of, pw):
        """The take-to-take spread as a band around the mean, red
        where it stops being trustworthy."""
        if self.band is None:
            return
        blo, bhi, bad = self.band
        idx = stride_idx(len(self.freqs))
        bw = max(1.0, pw / max(1, len(idx)))
        for j in idx:
            if j >= len(blo) or j >= len(bhi):
                break
            if not (np.isfinite(blo[j]) and np.isfinite(bhi[j])):
                continue
            x = x_of(self.freqs[j])
            yt, yb = y_of(bhi[j]), y_of(blo[j])
            if bad is not None and j < len(bad) and bad[j]:
                cr.set_source_rgba(0.87, 0.19, 0.19, 0.35)
            else:
                cr.set_source_rgba(0.22, 0.52, 0.90, 0.18)
            cr.rectangle(x, yt, bw, max(1.0, yb - yt))
            cr.fill()

    def _line(self, cr, c, x_of, y_of, ph):
        if c.values is None:
            return
        a = self._alpha(c)
        r, g, b, al = c.rgba
        idx = stride_idx(len(self.freqs))
        if c.land:
            self._shade_land(cr, c, x_of, y_of, idx, ph)
        if c.dash:
            cr.save()
            cr.set_dash(list(c.dash))
        cr.set_source_rgba(r, g, b, al * a)
        cr.set_line_width(c.width)
        pen = False
        for j in idx:
            v = c.values[j] if j < len(c.values) else float("nan")
            if not np.isfinite(v):
                if pen:
                    cr.stroke()
                pen = False
                continue
            x, y = x_of(self.freqs[j]), y_of(v)
            cr.line_to(x, y) if pen else cr.move_to(x, y)
            pen = True
        if pen:
            cr.stroke()
        if c.dash:
            cr.restore()

    def _shade_land(self, cr, c, x_of, y_of, idx, ph):
        """Everything under the noise line is the instrument's own
        floor: a reading inside it is the rig speaking."""
        cr.set_source_rgba(0.55, 0.55, 0.55, 0.22)
        started = False
        x0 = xl = None
        for j in idx:
            v = c.values[j] if j < len(c.values) else float("nan")
            if not np.isfinite(v):
                continue
            x, y = x_of(self.freqs[j]), y_of(v)
            if not started:
                x0 = x
                cr.move_to(x, y)
                started = True
            else:
                cr.line_to(x, y)
            xl = x
        if not started:
            return
        cr.line_to(xl, MT + ph)
        cr.line_to(x0, MT + ph)
        cr.close_path()
        cr.fill()

    def _legend(self, cr):
        """Names in their own colours, each with a hit rectangle:
        hover lights, click pins."""
        self.hits = []
        cr.select_font_face("Sans", 0, 0)
        cr.set_font_size(9)
        lx, ly = ML + 8, MT + 12
        lit = self.state.lit()
        for c in self.curves:
            if not c.legend:
                continue
            r, g, b, al = c.rgba
            a = al if (lit is None or c.name == lit) else al * DIM
            cr.set_source_rgba(r, g, b, a)
            cr.set_line_width(2.0)
            cr.move_to(lx, ly - 3)
            cr.line_to(lx + 12, ly - 3)
            cr.stroke()
            cr.move_to(lx + 16, ly)
            cr.show_text(c.name)
            tw = cr.text_extents(c.name).width
            self.hits.append((lx - 4, ly - 12,
                              lx + 16 + tw + 4, ly + 6, c.name))
            lx += 16 + tw + 12
