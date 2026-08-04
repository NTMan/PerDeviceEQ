"""The painter's court. curve_view draws onto a cairo context and
imports no gi, so the whole picture can be judged here: that the
axis is named, that the pen breaks where the sweep abstains, that
the land without harmonic evidence is shaded and said out loud,
and that the legend has hands only when it was drawn. The GTK-blind
suite let two field breakages through in one night -- this is the
answer to that, and it needs no xvfb."""

import numpy as np

from perdeviceeq import curve_view as cv


class Ext:
    def __init__(self, width):
        self.width = width
        self.height = 8


class FakeCr:
    """Records what the painter asked for."""

    def __init__(self):
        self.texts = []
        self.rects = []
        self.segments = []      # (colour, polyline) pairs
        self.colors = []
        self._pts = []
        self._col = (0, 0, 0, 1)

    # colour and pen
    def set_source_rgba(self, r, g, b, a=1.0):
        self._col = (r, g, b, a)
        self.colors.append((r, g, b, a))

    def set_source_rgb(self, r, g, b):
        self.set_source_rgba(r, g, b, 1.0)

    def set_line_width(self, _w):
        pass

    def set_dash(self, _d):
        pass

    def save(self):
        pass

    def restore(self):
        pass

    # geometry
    def rectangle(self, x, y, w, h):
        self.rects.append((x, y, w, h))

    def fill(self):
        self._pts = []

    def close_path(self):
        pass

    def move_to(self, x, y):
        if self._pts:
            self.segments.append((self._col, self._pts))
        self._pts = [(x, y)]

    def line_to(self, x, y):
        self._pts.append((x, y))

    def stroke(self):
        if self._pts:
            self.segments.append((self._col, self._pts))
        self._pts = []

    # text
    def select_font_face(self, *_a):
        pass

    def set_font_size(self, _s):
        pass

    def show_text(self, t):
        self.texts.append(t)

    def text_extents(self, t):
        return Ext(6.0 * len(t))


FREQS = np.array([20.0, 100.0, 1000.0, 5000.0, 10000.0, 20000.0])


def paint(plot, w=700, h=300):
    cr = FakeCr()
    plot.draw(None, cr, w, h)
    return cr


def test_a_harmonic_becomes_a_level_on_the_same_axis():
    mag = np.array([-30.0, -30.0, -30.0])
    ratio = [-50.0, None, -40.0]
    lv = cv.as_level(mag, ratio)
    assert lv[0] == -80.0
    assert np.isnan(lv[1])
    assert lv[2] == -70.0
    assert cv.as_level(mag, None) is None
    assert cv.as_level(mag, [-1.0]) is None      # wrong length


def test_the_window_makes_room_for_the_distortion():
    resp = cv.Curve("take", np.full(3, -30.0), cv.C_RESPONSE)
    thd = cv.Curve("THD", np.array([-88.0, -90.0, np.nan]),
                   cv.C_THD, harmonic=True)
    lo, hi = cv.window_db([resp, thd])
    assert lo <= -93.0 and hi >= -27.0
    assert lo % cv.DB_STEP == 0 and hi % cv.DB_STEP == 0
    assert cv.window_db([]) == (-80.0, 0.0)


def test_the_grid_says_what_it_is():
    p = cv.Plot(FREQS, [cv.Curve("take", np.full(6, -30.0),
                                 cv.C_RESPONSE)], -60.0, -24.0)
    cr = paint(p)
    for lab in ("20", "100", "1k", "10k", "20k", "Hz"):
        assert lab in cr.texts
    assert "-30" in cr.texts or "-36" in cr.texts


def test_the_pen_breaks_where_the_sweep_abstains():
    vals = np.array([-30.0, -31.0, np.nan, np.nan, -33.0, -34.0])
    p = cv.Plot(FREQS, [cv.Curve("take", vals, cv.C_RESPONSE)],
                -60.0, -24.0, legend=False)
    cr = paint(p)
    runs = [pts for col, pts in cr.segments
            if col[:3] == cv.C_RESPONSE[:3] and len(pts) >= 2]
    assert len(runs) == 2


def test_the_land_without_evidence_is_shaded_and_named():
    thd = np.array([-80.0] * 5 + [np.nan])
    p = cv.Plot(FREQS,
                [cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE),
                 cv.Curve("THD", thd, cv.C_THD, harmonic=True)],
                -90.0, -24.0)
    cr = paint(p, w=700)
    assert "no harmonic evidence" in cr.texts
    x10k = cv.log_x(10000.0, cv.ML, 700 - cv.ML - cv.MR)
    assert any(abs(r[0] - x10k) < 1.0 for r in cr.rects)


def test_evidence_ignores_the_response_itself():
    # the response runs to 20 kHz; only the harmonics decide
    resp = cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE)
    thd = cv.Curve("THD", np.array([-80.0] * 4 + [np.nan] * 2),
                   cv.C_THD, harmonic=True)
    assert cv.evidence_hi(FREQS, [resp, thd]) == 5000.0
    assert cv.evidence_hi(FREQS, [resp]) is None


def test_a_drawn_legend_has_hands_and_a_hidden_one_has_none():
    curves = [cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE),
              cv.Curve("THD", np.full(6, -80.0), cv.C_THD,
                       harmonic=True)]
    st = cv.Highlight()
    p = cv.Plot(FREQS, curves, -90.0, -24.0, state=st, legend=True)
    paint(p)
    assert [n for _a, _b, _c, _d, n in p.hits] == ["take", "THD"]
    x0, y0, x1, y1, name = p.hits[1]
    assert p.legend_at((x0 + x1) / 2.0, (y0 + y1) / 2.0) == name
    assert p.legend_at(x1 + 50.0, y1 + 50.0) is None
    mute = cv.Plot(FREQS, curves, -90.0, -24.0, state=st,
                   legend=False)
    paint(mute)
    assert mute.hits == []
    assert mute.legend_at((x0 + x1) / 2.0,
                          (y0 + y1) / 2.0) is None


def test_pinning_a_name_dims_the_others_everywhere():
    st = cv.Highlight()
    take = cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE)
    thd = cv.Curve("THD", np.full(6, -80.0), cv.C_THD,
                   harmonic=True)
    face = cv.Plot(FREQS, [take, thd], -90.0, -24.0, state=st,
                   legend=True)
    row = cv.Plot(FREQS, [take, thd], -90.0, -24.0, state=st,
                  legend=False)
    assert face._alpha(take) == 1.0 and row._alpha(thd) == 1.0
    assert st.hit("THD") is True
    assert row._alpha(take) == cv.DIM      # the row follows
    assert row._alpha(thd) == 1.0
    assert st.hit("THD") is True           # a second click frees
    assert st.pinned is None
    st.hover = "THD"
    assert row._alpha(take) == cv.DIM


def test_the_spread_band_paints_and_the_red_says_untrustworthy():
    mean = np.full(6, -30.0)
    sp = np.array([0.5, 0.5, 0.5, 9.0, 9.0, 9.0])
    p = cv.Plot(FREQS, [cv.Curve("mean", mean, cv.C_RESPONSE)],
                -42.0, -18.0,
                band=(mean - sp / 2.0, mean + sp / 2.0, sp > 5.0))
    cr = paint(p)
    reds = [c for c in cr.colors if c[0] > 0.8 and c[1] < 0.3]
    assert reds, "the untrustworthy half of the band must be red"
    assert len(cr.rects) >= 6

def test_the_floor_reads_the_body_not_the_dives():
    """A handful of third-harmonic dives used to drag the window
    a hundred and thirty decibels down and press the response into
    a flat thread at the top."""
    f = np.geomspace(20.0, 20000.0, 400)
    body = np.full(400, -92.0)
    body[::40] = -130.0
    curves = [cv.Curve("mean", np.full(400, -40.0), cv.C_RESPONSE),
              cv.Curve("THD", body, cv.C_THD, harmonic=True)]
    lo, hi = cv.window_db(curves)
    assert lo > -125.0          # the dives fall out the bottom
    assert lo < -95.0           # the body stays in
    assert len(f) == 400


def test_the_response_never_loses_more_than_the_cap():
    curves = [cv.Curve("mean", np.full(4, -30.0), cv.C_RESPONSE),
              cv.Curve("THD", np.full(4, -400.0), cv.C_THD,
                       harmonic=True)]
    lo, hi = cv.window_db(curves)
    assert lo >= -30.0 - cv.MAX_SPAN_DB - cv.GRID_STEPS[-1]
    assert lo <= -30.0 - cv.MAX_SPAN_DB + cv.GRID_STEPS[-1]


def test_the_response_alone_keeps_its_own_floor():
    curves = [cv.Curve("mean", np.array([-40.0, -20.0]),
                       cv.C_RESPONSE)]
    lo, hi = cv.window_db(curves)
    assert lo <= -43.0 and hi >= -17.0


def test_the_ruler_thins_as_the_window_grows():
    assert cv.grid_pitch(18.0) == 3.0
    assert cv.grid_pitch(84.0) == 12.0
    assert cv.grid_pitch(400.0) == cv.GRID_STEPS[-1]
    p = cv.Plot(FREQS, [cv.Curve("take", np.full(6, -30.0),
                                 cv.C_RESPONSE)],
                -108.0, -24.0, legend=False)
    cr = paint(p)
    dbs = [t for t in cr.texts if t.startswith("-") and t != "-"]
    assert len(dbs) <= cv.GRID_MAX_LINES


def test_smoothing_calms_the_pen_and_keeps_every_abstention():
    f = np.geomspace(20.0, 20000.0, 300)
    v = np.full(300, -90.0)
    v[150] = -120.0
    v[200] = np.nan
    s = cv.smooth_oct(f, v)
    assert -105.0 < s[150] < -90.0        # the spike is calmed
    assert np.isnan(s[200])               # the abstention survives
    assert not np.isnan(s[199])
    assert np.isnan(v[200]) and v[150] == -120.0   # data untouched
    assert cv.smooth_oct(f, None) is None


def test_the_evidence_note_is_said_once():
    thd = np.array([-80.0] * 5 + [np.nan])
    curves = [cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE),
              cv.Curve("THD", thd, cv.C_THD, harmonic=True)]
    face = cv.Plot(FREQS, curves, -90.0, -24.0, legend=False)
    row = cv.Plot(FREQS, curves, -90.0, -24.0, legend=False,
                  say_evidence=False)
    assert "no harmonic evidence" in paint(face).texts
    cr = paint(row)
    assert "no harmonic evidence" not in cr.texts
    x10k = cv.log_x(10000.0, cv.ML, 700 - cv.ML - cv.MR)
    assert any(abs(r[0] - x10k) < 1.0 for r in cr.rects)


def _plot_for_cursor():
    return cv.Plot(FREQS, [cv.Curve("take", np.full(6, -60.0),
                                    cv.C_RESPONSE)],
                   -108.0, -24.0, legend=False)


def test_the_pointer_names_its_point():
    p = _plot_for_cursor()
    pw = 700 - cv.ML - cv.MR
    ph = 300 - cv.MT - cv.MB
    x = cv.log_x(1234.0, cv.ML, pw)
    y = cv.MT + (-24.0 - (-66.0)) / 84.0 * ph
    assert p.set_cursor(x, y) is True
    assert p.set_cursor(x, y) is False        # the same point
    cr = paint(p)
    assert "1.23k" in cr.texts
    assert "-66.0" in cr.texts


def test_the_crosshair_leaves_with_the_pointer():
    p = _plot_for_cursor()
    pw = 700 - cv.ML - cv.MR
    p.set_cursor(cv.log_x(1234.0, cv.ML, pw), 100.0)
    assert "1.23k" in paint(p).texts
    assert p.set_cursor(None, None) is True
    assert "1.23k" not in paint(p).texts


def test_a_pointer_in_the_gutter_draws_nothing():
    """Below the plot and left of it the pointer is over the
    labels, not over data: the picture must be identical to the
    one with no pointer at all."""
    bare = paint(_plot_for_cursor()).texts
    pw = 700 - cv.ML - cv.MR
    for pos in ((cv.log_x(1234.0, cv.ML, pw), 295.0),
                (4.0, 100.0)):
        p = _plot_for_cursor()
        p.set_cursor(*pos)
        assert paint(p).texts == bare


def test_the_pixel_gives_the_frequency_back():
    for f in (20.0, 137.0, 1000.0, 9500.0, 20000.0):
        x = cv.log_x(f, cv.ML, 654.0)
        assert abs(cv.f_at_x(x, cv.ML, 654.0) - f) < f * 0.001


def test_the_readouts_are_short_enough_for_a_gutter():
    assert cv.fmt_hz(48.2) == "48.2"
    assert cv.fmt_hz(137.4) == "137"
    assert cv.fmt_hz(1000.0) == "1k"
    assert cv.fmt_hz(12345.0) == "12.35k"
    assert cv.fmt_db(-38.44) == "-38.4"

def _named_plot():
    curves = [cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE),
              cv.Curve("THD", np.full(6, -80.0), cv.C_THD,
                       harmonic=True)]
    return cv.Plot(FREQS, curves, -108.0, -24.0, legend=False)


def _y_of(v, h=300):
    ph = h - cv.MT - cv.MB
    return cv.MT + (-24.0 - v) / 84.0 * ph


def test_the_line_under_the_pointer_says_its_name():
    """A take row scrolls far from the channel's only legend, so
    the canvas has to explain itself: point at a line, it answers."""
    p = _named_plot()
    paint(p)                       # the rect is learned on draw
    pw = 700 - cv.ML - cv.MR
    x = cv.log_x(1234.0, cv.ML, pw)
    p.set_cursor(x, _y_of(-80.0))
    c, v = p.at_cursor()
    assert c.name == "THD" and abs(v + 80.0) < 1e-6
    assert "THD -80.0" in paint(p).texts


def test_a_pointer_between_the_lines_names_nothing():
    p = _named_plot()
    paint(p)
    pw = 700 - cv.ML - cv.MR
    p.set_cursor(cv.log_x(1234.0, cv.ML, pw), _y_of(-55.0))
    c, v = p.at_cursor()
    assert c is None and v is None
    assert not [t for t in paint(p).texts if t.startswith("THD")]


def test_a_click_pins_the_line_it_lands_on():
    p = _named_plot()
    paint(p)
    pw = 700 - cv.ML - cv.MR
    x = cv.log_x(1234.0, cv.ML, pw)
    assert p.pick_at(x, _y_of(-30.0)) == "take"
    assert p.pick_at(x, _y_of(-55.0)) is None
    assert p.state.hit(p.pick_at(x, _y_of(-80.0))) is True
    assert p.state.pinned == "THD"


def test_the_legend_still_wins_over_the_curve_under_it():
    curves = [cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE),
              cv.Curve("THD", np.full(6, -80.0), cv.C_THD,
                       harmonic=True)]
    p = cv.Plot(FREQS, curves, -108.0, -24.0, legend=True)
    paint(p)
    x0, y0, x1, y1, name = p.hits[1]
    assert p.pick_at((x0 + x1) / 2.0, (y0 + y1) / 2.0) == name
