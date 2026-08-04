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
DB_STEP = 6.0                           # ruler pitch of last resort
GRID_STEPS = (3.0, 6.0, 12.0, 24.0, 48.0)
GRID_MAX_LINES = 10                     # denser than this is hatching
PAD_DB = 3.0                            # air above and below data
MAX_SPAN_DB = 80.0                      # response to the deepest floor
FLOOR_Q = 2.0                           # percentile that sets the floor
SMOOTH_OCT = 1.0 / 12.0                 # display smoothing of harmonics
CROSS_DASH = (3.0, 3.0)                 # the crosshair is dashed
NEAR_PX = 14.0                          # how close counts as ON a line

C_RESPONSE = (0.22, 0.52, 0.90, 1.00)
C_GHOST = (0.45, 0.45, 0.45, 0.90)
C_BAD = (0.87, 0.23, 0.23, 1.00)
C_H2 = (0.76, 0.13, 0.13, 0.85)
C_H3 = (0.90, 0.57, 0.00, 0.85)
C_THD = (0.15, 0.15, 0.15, 1.00)
C_NOISE = (0.50, 0.50, 0.50, 0.85)
DIM = 0.28                              # alpha factor of an unlit line


TEXT_AS_PATH = False        # exporters flip this; see curve_export


def show_text(cr, text):
    """Every label goes through here so an exporter can ask for
    OUTLINES instead of glyphs. cairo writes a <text> node into an
    SVG by default, and that node renders in whatever font the
    reader happens to have -- or fails to have. A picture meant for
    a blog must not depend on that."""
    if TEXT_AS_PATH:
        cr.text_path(text)
        cr.fill()
    else:
        cr.show_text(text)


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


def grid_pitch(span):
    """The ruler adapts. Six decibels over a hundred-decibel window
    is nineteen lines, which the eye reads as hatching and not as a
    ruler; pick the finest pitch that still keeps the count sane."""
    for p in GRID_STEPS:
        if span / p <= GRID_MAX_LINES:
            return p
    return GRID_STEPS[-1]


def smooth_oct(freqs, values, frac=SMOOTH_OCT):
    """Fractional-octave smoothing of the PEN, not of the data.
    A harmonic line read per bin is hairy and a hairy line reads as
    noise even when it is signal; a twelfth-octave window is what a
    published distortion plot shows. Two laws keep it honest: the
    stored arrays are untouched (this runs on the copy handed to the
    painter), and an abstention stays exactly where it was -- a NaN
    is never filled in from its neighbours, so the line still ends
    where the evidence ends."""
    if values is None:
        return None
    v = np.asarray(values, float)
    f = np.asarray(freqs, float)
    if len(v) != len(f) or len(v) < 3:
        return v
    lg = np.log2(np.maximum(f, 1e-9))
    half = frac / 2.0
    lo = np.searchsorted(lg, lg - half, side="left")
    hi = np.searchsorted(lg, lg + half, side="right")
    ok = np.isfinite(v)
    filled = np.where(ok, v, 0.0)
    csum = np.concatenate(([0.0], np.cumsum(filled)))
    ccnt = np.concatenate(([0], np.cumsum(ok.astype(int))))
    n = ccnt[hi] - ccnt[lo]
    s = csum[hi] - csum[lo]
    with np.errstate(all="ignore"):
        out = np.where(n > 0, s / np.maximum(n, 1), np.nan)
    return np.where(ok, out, np.nan)


def tick_label(f):
    return ("%dk" % (f // 1000)) if f >= 1000 else str(int(f))


def f_at_x(x, x0, w):
    """The inverse of log_x: which frequency sits under a pixel."""
    lo, hi = math.log10(FMIN_PLOT), math.log10(FMAX_PLOT)
    t = min(1.0, max(0.0, (float(x) - x0) / max(1e-6, w)))
    return 10.0 ** (lo + t * (hi - lo))


def fmt_hz(f):
    """The pointer's frequency, short enough for a gutter."""
    if f >= 1000.0:
        s = ("%.2f" % (f / 1000.0)).rstrip("0").rstrip(".")
        return s + "k"
    if f >= 100.0:
        return "%d" % int(round(f))
    return ("%.1f" % f).rstrip("0").rstrip(".")


def fmt_db(v):
    return "%.1f" % v


def draw_crosshair(cr, rect, cursor, f_of, v_of,
                   rgba=(0.5, 0.5, 0.5, 0.55),
                   text_rgba=(0.25, 0.25, 0.25, 0.95),
                   bg_rgba=(1.0, 1.0, 1.0, 0.80)):
    """The pointer names its point: two dashed hairlines in the
    grid's own colour running out to the fixed axes, and the value
    written where each one lands -- frequency in the bottom gutter,
    decibels in the left one. The words live in the gutters so the
    curves stay clean, and the whole thing leaves with the pointer.
    rect is (ml, mt, pw, ph); f_of and v_of turn a pixel into a
    value. Returns False when the pointer is outside the plot."""
    if cursor is None:
        return False
    x, y = cursor
    ml, mt, pw, ph = rect
    if not (ml <= x <= ml + pw and mt <= y <= mt + ph):
        return False
    cr.save()
    cr.set_dash(list(CROSS_DASH))
    cr.set_line_width(1.0)
    cr.set_source_rgba(*rgba)
    cr.move_to(x, mt)
    cr.line_to(x, mt + ph)
    cr.move_to(ml, y)
    cr.line_to(ml + pw, y)
    cr.stroke()
    cr.restore()
    cr.select_font_face("Sans", 0, 0)
    cr.set_font_size(9)
    _readout(cr, fmt_hz(f_of(x)), x, mt + ph + 13, 0.5,
             text_rgba, bg_rgba)
    _readout(cr, fmt_db(v_of(y)), ml - 2, y + 3, 1.0,
             text_rgba, bg_rgba)
    return True


def nearest_trace(traces, x, y, max_px=NEAR_PX):
    """The line a pointer is standing on, out of polylines already
    drawn: traces maps a name to a list of (x, y, value). Returns
    (name, value) or (None, None).

    Pure on purpose. The equalizer graph builds its curves inside
    its own draw -- the EQ line after its clamp, the target after
    its shift -- so the honest way to name what the eye sees is to
    read back the points that were actually put on the canvas. That
    arithmetic belongs here, where a court can reach it, and not in
    a module the sandbox cannot import."""
    best, bv, bd = None, None, float(max_px)
    for name, pts in (traces or {}).items():
        cand, cd = None, None
        for px, py, v in pts:
            dx = abs(px - x)
            if cd is None or dx < cd:
                cand, cd = (py, v), dx
        if cand is None or cd is None or cd > 3.0:
            continue
        d = abs(cand[0] - y)
        if d < bd:
            best, bv, bd = name, cand[1], d
    return best, bv


def name_the_line(cr, rect, cursor, name, value, rgba,
                  bg_rgba=(1.0, 1.0, 1.0, 0.80)):
    """The line under the pointer says its name and its value, in
    its own colour, beside the crosshair."""
    if cursor is None or name is None or value is None:
        return False
    ml, mt, pw, _ph = rect
    x, y = cursor
    cr.select_font_face("Sans", 0, 0)
    cr.set_font_size(9)
    lab = "%s %s" % (name, fmt_db(value))
    ext = cr.text_extents(lab)
    tx = x + 8
    if tx + ext.width > ml + pw - 2:
        tx = x - 8 - ext.width
    ty = max(mt + 10, y - 6)
    cr.set_source_rgba(*bg_rgba)
    cr.rectangle(tx - 2, ty - 10, ext.width + 4, 12)
    cr.fill()
    cr.set_source_rgba(*rgba)
    cr.move_to(tx, ty)
    show_text(cr, lab)
    # show_text leaves a live current point, and cairo's arc()
    # JOINS the current point to the start of the arc with a
    # straight line. The equalizer draws its band handles with
    # arc(), so a label left dangling drew a dark hairline from
    # itself to the first handle. A painter must hand the context
    # back with an empty path.
    cr.new_path()
    return True


def _readout(cr, text, x, y, anchor, text_rgba, bg_rgba):
    """One value on its axis, over a backdrop so it does not
    collide with the tick label it stands on."""
    ext = cr.text_extents(text)
    tx = x - ext.width * anchor
    cr.set_source_rgba(*bg_rgba)
    cr.rectangle(tx - 2, y - 10, ext.width + 4, 12)
    cr.fill()
    cr.set_source_rgba(*text_rgba)
    cr.move_to(tx, y)
    show_text(cr, text)
    cr.new_path()               # never hand back a live point


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
    under the line (the instrument's own noise floor).

    `key` is what the highlight matches on, and it is not always
    the name. The face's blue line is the MEAN over the takes and a
    row's blue line is ONE take -- two different truths that must
    keep their two different words -- but to the eye and to the
    hand they are the same line, so H2 and THD lighting together
    across canvases while the blue one refused was read as a bug.
    Both carry key "response": the words stay honest, the light
    behaves as the eye expects. Curves with legend=False borrow
    their parent's key so an overlay follows the line it belongs
    to."""

    def __init__(self, name, values, rgba, width=1.4, dash=None,
                 harmonic=False, land=False, legend=True, key=None):
        self.name = name
        self.key = key or name
        self.values = None if values is None else np.asarray(
            values, float)
        self.rgba = rgba
        self.width = width
        self.dash = dash
        self.harmonic = harmonic
        self.land = land
        self.legend = legend


class Highlight:
    """Which names are lit, shared by every canvas of one channel
    so a pin made on the third take row dims the rest everywhere.

    THREE LEVELS, because two were not enough. Pinning answers
    "how far apart are these two" -- the gap between THD and the
    noise floor is the whole distortion question, and a gap cannot
    be seen by lighting one line at a time. Hovering answers "which
    line is this", which is a different question and must keep
    working in EVERY state: with nothing pinned, with two pinned,
    and with ALL pinned -- the state that used to look identical to
    nothing pinned and made the pointer seem broken. So the pointer
    no longer joins the pinned set; it gets a level of its own,
    above it.

    A plain click replaces the set, Ctrl adds or removes, a click
    on nothing clears -- the file manager idiom, so there is
    nothing to learn. A plain click on the only pinned name lets
    it go."""

    def __init__(self):
        self.hover = None
        self.pinned = set()

    def hit(self, name, add=False):
        """A click: returns True when the state changed."""
        before = set(self.pinned)
        if name is None:
            if not add:
                self.pinned = set()
        elif add:
            self.pinned.symmetric_difference_update({name})
        elif before == {name}:
            self.pinned = set()
        else:
            self.pinned = {name}
        return self.pinned != before


def _finite(arr):
    if arr is None:
        return np.zeros(0)
    a = np.asarray(arr, float)
    return a[np.isfinite(a)]


def window_db(curves, band=None, max_span=MAX_SPAN_DB):
    """The shared vertical window, computed ONCE per channel and
    handed to every canvas so the grids align down the list.

    The floor is NOT the minimum. A handful of dives in the third
    harmonic used to drag the window to -132 and press the response
    -- the curve the whole picture exists for -- into the top
    fifteen percent of the canvas as a flat thread. So the floor
    reads a low percentile of the harmonic lines (the body of the
    distortion, not its spikes) and is then held to at most
    max_span below the response itself. What falls out the bottom
    is a few excursions of a line that is already at the noise."""
    resp, harm = [], []
    for c in curves:
        if c is None:
            continue
        (harm if c.harmonic else resp).append(_finite(c.values))
    if band is not None:
        resp.append(_finite(band[0]))
        resp.append(_finite(band[1]))
    resp = [v for v in resp if len(v)]
    harm = [v for v in harm if len(v)]
    if not resp and not harm:
        return -80.0, 0.0
    allv = np.concatenate(resp + harm)
    hi = float(allv.max()) + PAD_DB
    if harm:
        hv = np.concatenate(harm)
        floor = float(np.percentile(hv, FLOOR_Q)) - PAD_DB
    else:
        floor = float(allv.min()) - PAD_DB
    if resp:
        rv = np.concatenate(resp)
        floor = max(floor, float(rv.max()) - max_span)
        floor = min(floor, float(rv.min()) - PAD_DB)
    pitch = grid_pitch(max(hi - floor, 1e-6))
    lo = math.floor(floor / pitch) * pitch
    hi = math.ceil(hi / pitch) * pitch
    if hi - lo < pitch * 2:
        hi = lo + pitch * 2
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
                 dim_outside=None, say_evidence=True):
        self.freqs = np.asarray(freqs, float)
        self.curves = [c for c in curves if c is not None]
        self._keys = {c.key for c in self.curves}
        self.lo, self.hi = float(lo), float(hi)
        self.band = band
        self.state = state if state is not None else Highlight()
        self.legend = legend
        self.title = title
        self.dim_outside = dim_outside
        self.say_evidence = say_evidence
        self.cursor = None
        self._rect = None
        self.hits = []
        self.ev_hi = evidence_hi(self.freqs, self.curves)

    def at_cursor(self):
        """(curve, value) of the line the pointer is standing on,
        or (None, None). This is what lets a canvas explain itself
        without a legend: the take rows scroll far away from the
        one legend the channel has, and the architect asked what
        happens then. Point at a line and it says its name."""
        if self.cursor is None or self._rect is None:
            return None, None
        x, y = self.cursor
        ml, mt, pw, ph = self._rect
        if not (ml <= x <= ml + pw and mt <= y <= mt + ph):
            return None, None
        f = f_at_x(x, ml, pw)
        j = int(np.argmin(np.abs(np.log10(
            np.maximum(self.freqs, 1e-9)) - math.log10(f))))
        span = max(1e-6, self.hi - self.lo)
        best, bv, bd = None, None, NEAR_PX
        for c in self.curves:
            if c.values is None or not c.legend or j >= len(c.values):
                continue
            v = float(c.values[j])
            if not np.isfinite(v):
                continue
            cy = mt + (self.hi - v) / span * ph
            d = abs(cy - y)
            if d < bd:
                best, bv, bd = c, v, d
        return best, bv

    def pick_at(self, x, y):
        """The name a click lands on: a legend name if the pointer
        is in the legend, otherwise the line under the pointer.
        The legend keeps its hands, and a row that never drew one
        borrows the same grip from the curve itself."""
        name = self.legend_at(x, y)
        if name is not None:
            return name
        self.set_cursor(x, y)
        c, _v = self.at_cursor()
        return None if c is None else c.key

    def set_cursor(self, x, y):
        """Pointer moved (or left, with x None). True when the
        canvas needs a repaint."""
        c = None if x is None else (float(x), float(y))
        if c == self.cursor:
            return False
        self.cursor = c
        return True

    def _dress(self, curve, under=None):
        """(alpha factor, extra width) for one line. Under the
        pointer: full colour and the thickest pen, in any state.
        Pinned: full colour, thicker. Neither, with something
        pinned: stepped back but still legible -- a comparison
        needs its neighbours visible.

        Only the pins THIS canvas can show count. A pin is a hand
        of a line, and a line that is not in the picture has no
        hand in it: pinning "partner" on the face used to grey out
        whole take rows, which have no partner and no way to say
        why they went quiet. The pin itself is kept -- it is
        memory, and it starts biting again the moment its line
        comes back."""
        if curve.key in (under, self.state.hover):
            return 1.0, 1.6
        pins = self.state.pinned & self._keys
        if pins:
            if curve.key in pins:
                return 1.0, 0.8
            return DIM, 0.0
        return 1.0, 0.0

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
        self._rect = (ML, MT, pw, ph)
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
        uc, uv = self.at_cursor()
        under = None if uc is None else uc.key
        for c in self.curves:
            self._line(cr, c, x_of, y_of, ph, under)
        self._outside(cr, x_of, pw, ph)
        if self.legend:
            self._legend(cr, under)
        else:
            self.hits = []
        if draw_crosshair(cr, (ML, MT, pw, ph), self.cursor,
                          lambda px: f_at_x(px, ML, pw),
                          lambda py: self.hi - (py - MT) / ph * span):
            self._name_the_line(cr, pw, uc, uv)
        if self.title:
            cr.set_source_rgba(0.4, 0.4, 0.4, 0.9)
            cr.set_font_size(10)
            ext = cr.text_extents(self.title)
            cr.move_to(ML + pw - ext.width - 4, MT + 12)
            show_text(cr, self.title)
            cr.new_path()

    def _name_the_line(self, cr, pw, c, v):
        if c is None:
            return
        r, g, b, _a = c.rgba
        name_the_line(cr, (ML, MT, pw, 0), self.cursor,
                      c.name, v, (r, g, b, 1.0))

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
            show_text(cr, lab)
        pitch = grid_pitch(max(self.hi - self.lo, 1e-6))
        g = math.ceil(self.lo / pitch) * pitch
        while g <= self.hi + 1e-9:
            y = y_of(g)
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.25)
            cr.move_to(ML, y)
            cr.line_to(ML + pw, y)
            cr.stroke()
            cr.set_source_rgba(0.4, 0.4, 0.4, 0.85)
            cr.move_to(2, y + 3)
            show_text(cr, "%d" % int(round(g)))
            g += pitch
        cr.set_source_rgba(0.4, 0.4, 0.4, 0.85)
        cr.set_font_size(9)
        cr.move_to(2, MT + ph + 13)
        show_text(cr, "Hz")

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
        if not self.say_evidence:
            return                       # said once, on the face
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
        show_text(cr, lab)

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

    def _line(self, cr, c, x_of, y_of, ph, under=None):
        if c.values is None:
            return
        a, dw = self._dress(c, under)
        r, g, b, al = c.rgba
        idx = stride_idx(len(self.freqs))
        if c.land:
            self._shade_land(cr, c, x_of, y_of, idx, ph, a)
        if c.dash:
            cr.save()
            cr.set_dash(list(c.dash))
        cr.set_source_rgba(r, g, b, al * a)
        cr.set_line_width(c.width + dw)
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

    def _shade_land(self, cr, c, x_of, y_of, idx, ph, a=1.0):
        """Everything under the noise line is the instrument's own
        floor: a reading inside it is the rig speaking. The shade
        steps back with its line, or a dimmed noise curve would
        keep shouting through its own fill."""
        cr.set_source_rgba(0.55, 0.55, 0.55, 0.22 * a)
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

    def _legend(self, cr, under=None):
        """Names in their own colours, each with a hit rectangle.
        A pinned name wears a dot: brightness alone cannot say
        whether nothing is pinned or everything is, and a state
        the eye cannot read is a state the hand will fight."""
        self.hits = []
        cr.select_font_face("Sans", 0, 0)
        cr.set_font_size(9)
        lx, ly = ML + 8, MT + 12
        for c in self.curves:
            if not c.legend:
                continue
            r, g, b, al = c.rgba
            a, _dw = self._dress(c, under)
            cr.set_source_rgba(r, g, b, al * a)
            cr.set_line_width(2.0)
            cr.move_to(lx, ly - 3)
            cr.line_to(lx + 12, ly - 3)
            cr.stroke()
            lab = ("\u2022 " + c.name
                   if c.key in self.state.pinned else c.name)
            cr.move_to(lx + 16, ly)
            show_text(cr, lab)
            tw = cr.text_extents(lab).width
            self.hits.append((lx - 4, ly - 12,
                              lx + 16 + tw + 4, ly + 6, c.key))
            lx += 16 + tw + 12
