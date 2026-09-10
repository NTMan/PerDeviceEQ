"""The painter's court. curve_view draws onto a cairo context and
imports no gi, so the whole picture can be judged here: that the
axis is named, that the pen breaks where the sweep abstains, that
the land without harmonic evidence is shaded and said out loud,
and that the legend has hands only when it was drawn. The GTK-blind
suite let two field breakages through in one night -- this is the
answer to that, and it needs no xvfb."""

import math
import os

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
        self.point = None       # cairo's live current point

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
        self.point = None

    def new_path(self):
        self._pts = []
        self.point = None

    def close_path(self):
        pass

    def move_to(self, x, y):
        if self._pts:
            self.segments.append((self._col, self._pts))
        self._pts = [(x, y)]
        self.point = (x, y)

    def line_to(self, x, y):
        self._pts.append((x, y))
        self.point = (x, y)

    def stroke(self):
        if self._pts:
            self.segments.append((self._col, self._pts))
        self._pts = []
        self.point = None

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
    assert face._dress(take)[0] == 1.0
    assert row._dress(thd)[0] == 1.0
    assert st.hit("THD") is True
    assert row._dress(take)[0] == cv.DIM      # the row follows
    assert row._dress(thd)[0] == 1.0
    assert st.hit("THD") is True              # a second click frees
    assert st.pinned == set()


def test_ctrl_adds_a_second_line_and_a_plain_click_replaces():
    """The gap between two lines is the question pinning exists
    for -- THD against the noise floor is the whole distortion
    argument -- so more than one name has to be able to burn."""
    st = cv.Highlight()
    assert st.hit("THD") is True
    assert st.hit("noise", add=True) is True
    assert st.pinned == {"THD", "noise"}
    assert st.hit("noise", add=True) is True      # Ctrl removes
    assert st.pinned == {"THD"}
    assert st.hit("H2") is True                   # plain replaces
    assert st.pinned == {"H2"}
    assert st.hit(None) is True                   # empty clears
    assert st.pinned == set()
    assert st.hit(None) is False                  # nothing to do
    st.pinned = {"H2"}
    assert st.hit(None, add=True) is False        # Ctrl on nothing


def test_the_pointer_is_read_in_every_state():
    """The state that broke the brain: with EVERY name pinned the
    picture looks exactly like nothing pinned, and hover used to
    vanish into the pinned set. It now has a level of its own."""
    st = cv.Highlight()
    take = cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE)
    thd = cv.Curve("THD", np.full(6, -80.0), cv.C_THD,
                   harmonic=True)
    p = cv.Plot(FREQS, [take, thd], -90.0, -24.0, state=st,
                legend=False)
    st.pinned = {"take", "THD"}                   # all of them
    assert p._dress(take) == p._dress(thd)        # indeed identical
    assert p._dress(thd, under="THD")[1] > p._dress(take)[1]
    st.pinned = set()
    assert p._dress(thd, under="THD")[1] > p._dress(take)[1]
    st.pinned = {"take"}
    assert p._dress(thd, under="THD") == (1.0, 1.6)
    assert p._dress(take)[1] == 0.8               # pinned, not hot


def test_a_pinned_name_wears_a_dot():
    st = cv.Highlight()
    curves = [cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE),
              cv.Curve("THD", np.full(6, -80.0), cv.C_THD,
                       harmonic=True)]
    p = cv.Plot(FREQS, curves, -90.0, -24.0, state=st, legend=True)
    assert "THD" in paint(p).texts
    st.hit("THD")
    texts = paint(p).texts
    assert "\u2022 THD" in texts and "take" in texts
    assert [n for _a, _b, _c, _d, n in p.hits] == ["take", "THD"]


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
    assert p.state.pinned == {"THD"}


def test_the_legend_still_wins_over_the_curve_under_it():
    curves = [cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE),
              cv.Curve("THD", np.full(6, -80.0), cv.C_THD,
                       harmonic=True)]
    p = cv.Plot(FREQS, curves, -108.0, -24.0, legend=True)
    paint(p)
    x0, y0, x1, y1, name = p.hits[1]
    assert p.pick_at((x0 + x1) / 2.0, (y0 + y1) / 2.0) == name

def test_the_blue_line_is_one_line_across_canvases():
    """The face draws the MEAN, a row draws ONE take: two words for
    two truths. But the eye sees one blue line, and once H2 and THD
    light together everywhere, a blue line that refuses reads as a
    bug. They share the key, not the name."""
    st = cv.Highlight()
    mean = cv.Curve("mean", np.full(6, -30.0), cv.C_RESPONSE,
                    key="response")
    take = cv.Curve("take", np.full(6, -31.0), cv.C_RESPONSE,
                    key="response")
    red = cv.Curve("off mean", np.full(6, -31.0), cv.C_BAD,
                   legend=False, key="response")
    thd = cv.Curve("THD", np.full(6, -80.0), cv.C_THD,
                   harmonic=True)
    face = cv.Plot(FREQS, [mean, thd], -90.0, -24.0, state=st,
                   legend=True)
    row = cv.Plot(FREQS, [take, red, thd], -90.0, -24.0, state=st,
                  legend=False)
    paint(face)
    key = face.hits[0][4]
    assert key == "response"
    assert st.hit(key) is True
    assert row._dress(take)[0] == 1.0
    assert row._dress(red)[0] == 1.0
    assert row._dress(thd)[0] == cv.DIM
    assert "\u2022 mean" in paint(face).texts


def test_the_pointer_says_the_word_and_pins_the_key():
    st = cv.Highlight()
    take = cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE,
                    key="response")
    p = cv.Plot(FREQS, [take], -96.0, -24.0, state=st,
                legend=False)
    paint(p)
    pw = 700 - cv.ML - cv.MR
    ph = 300 - cv.MT - cv.MB
    x = cv.log_x(1234.0, cv.ML, pw)
    y = cv.MT + (-24.0 - (-30.0)) / 72.0 * ph
    p.set_cursor(x, y)
    c, _v = p.at_cursor()
    assert c.name == "take"
    assert p.pick_at(x, y) == "response"
    assert "take -30.0" in paint(p).texts


def test_a_dimmed_noise_line_dims_its_land():
    st = cv.Highlight()
    noise = cv.Curve("noise", np.full(6, -80.0), cv.C_NOISE,
                     harmonic=True, land=True)
    thd = cv.Curve("THD", np.full(6, -70.0), cv.C_THD,
                   harmonic=True)
    p = cv.Plot(FREQS, [noise, thd], -90.0, -24.0, state=st,
                legend=False)
    assert [c for c in paint(p).colors if abs(c[3] - 0.22) < 1e-9]
    st.hit("THD")
    assert [c for c in paint(p).colors
            if abs(c[3] - 0.22 * cv.DIM) < 1e-9]

def test_nearest_trace_names_the_painted_line():
    """The equalizer builds its curves inside its own draw -- the
    EQ line after its clamp, the target after its shift -- so the
    honest way to name what the eye sees is to read back the points
    that were actually painted."""
    eqc = [(float(x), 100.0, -6.0) for x in range(0, 300, 3)]
    pred = [(float(x), 140.0, -12.0) for x in range(0, 300, 3)]
    traces = {"EQ": eqc, "predicted": pred}
    assert cv.nearest_trace(traces, 150.0, 102.0) == ("EQ", -6.0)
    assert cv.nearest_trace(traces, 150.0, 138.0) == ("predicted",
                                                      -12.0)
    assert cv.nearest_trace(traces, 150.0, 120.0) == (None, None)
    assert cv.nearest_trace({}, 1.0, 1.0) == (None, None)
    assert cv.nearest_trace(None, 1.0, 1.0) == (None, None)


def test_nearest_trace_ignores_a_line_that_is_not_under_x():
    """A trace that stops short must not be named from far away:
    the target line only exists where the law speaks."""
    short = [(float(x), 100.0, -3.0) for x in range(0, 60, 3)]
    assert cv.nearest_trace({"target": short}, 250.0,
                            100.0) == (None, None)
    assert cv.nearest_trace({"target": short}, 30.0,
                            100.0) == ("target", -3.0)


def test_name_the_line_writes_the_word_and_flips_at_the_edge():
    cr = FakeCr()
    rect = (38.0, 10.0, 600.0, 280.0)
    assert cv.name_the_line(cr, rect, (100.0, 120.0), "THD",
                            -48.25, (0, 0, 0, 1)) is True
    assert "THD -48.2" in cr.texts
    assert cr.rects[-1][0] > 100.0       # to the right of the point
    cr2 = FakeCr()
    cv.name_the_line(cr2, rect, (630.0, 120.0), "THD", -48.25,
                     (0, 0, 0, 1))
    assert cr2.rects[-1][0] < 630.0      # flipped at the edge
    assert cv.name_the_line(cr2, rect, None, "THD", -1.0,
                            (0, 0, 0, 1)) is False
    assert cv.name_the_line(cr2, rect, (100.0, 120.0), None, -1.0,
                            (0, 0, 0, 1)) is False

def test_a_label_hands_the_context_back_with_an_empty_path():
    """cairo's arc() joins the live current point to the start of
    the arc with a straight line, and the equalizer draws its band
    handles with arc(). A label that left its point dangling drew a
    dark hairline from itself to the first handle."""
    cr = FakeCr()
    rect = (38.0, 10.0, 600.0, 280.0)
    cv.name_the_line(cr, rect, (100.0, 120.0), "EQ", -14.0,
                     (0, 0, 0, 1))
    assert cr.point is None
    cr = FakeCr()
    cv.draw_crosshair(cr, rect, (100.0, 120.0),
                      lambda px: 1000.0, lambda py: -14.0)
    assert cr.point is None
    p = cv.Plot(FREQS, [cv.Curve("take", np.full(6, -30.0),
                                 cv.C_RESPONSE)],
                -60.0, -24.0, legend=True)
    cr = FakeCr()
    p.set_cursor(300.0, 100.0)
    p.draw(None, cr, 700, 300)
    assert cr.point is None

def test_a_pin_on_a_line_this_canvas_lacks_dims_nothing():
    """A pin is a hand of a line, and a line that is not in the
    picture has no hand in it. Pinning "partner" on the face used
    to grey out whole take rows, which have no partner and no way
    to say why they went quiet."""
    st = cv.Highlight()
    take = cv.Curve("take", np.full(6, -30.0), cv.C_RESPONSE,
                    key="response")
    thd = cv.Curve("THD", np.full(6, -80.0), cv.C_THD,
                   harmonic=True)
    row = cv.Plot(FREQS, [take, thd], -90.0, -24.0, state=st,
                  legend=False)
    assert st.hit("partner") is True
    assert row._dress(take)[0] == 1.0
    assert row._dress(thd)[0] == 1.0
    assert st.pinned == {"partner"}            # memory survives
    assert row._dress(take, under="response")[1] == 1.6
    assert st.hit("THD", add=True) is True
    assert row._dress(take)[0] == cv.DIM       # a real pin bites
    assert row._dress(thd)[0] == 1.0
    assert st.hit("THD", add=True) is True     # and lets go again
    assert row._dress(take)[0] == 1.0


def test_a_lonely_line_can_never_dim_itself():
    """The dead end he found: pin any line but EQ, then hide the
    legend with the eye. Whatever is pinned elsewhere, a picture
    holding one line can only show it lit or plain."""
    st = cv.Highlight()
    eqc = cv.Curve("EQ", np.full(6, -12.0), cv.C_RESPONSE)
    p = cv.Plot(FREQS, [eqc], -36.0, 0.0, state=st, legend=False)
    st.pinned = {"measured", "predicted", "target"}
    assert p._dress(eqc)[0] == 1.0
    assert st.hit("EQ", add=True) is True
    assert p._dress(eqc)[0] == 1.0
    assert p._dress(eqc)[1] == 0.8             # pinned, so thicker

def test_one_dive_cannot_reopen_the_window():
    """A silent input, or a deconvolution that came out empty,
    sends the response to minus two hundred and eighty somewhere.
    That used to buy a four-hundred-decibel canvas in which every
    real line was flattened into the ceiling."""
    mag = np.full(200, -36.0)
    mag[0] = -285.0
    mag[199] = -270.0
    curves = [cv.Curve("take", mag, cv.C_RESPONSE, key="response"),
              cv.Curve("THD", np.full(200, -90.0), cv.C_THD,
                       harmonic=True)]
    lo, hi = cv.window_db(curves)
    assert hi - lo <= cv.MAX_SPAN_DB + 2 * cv.GRID_STEPS[-1]
    assert lo >= -36.0 - cv.MAX_SPAN_DB - cv.GRID_STEPS[-1]
    assert hi >= -36.0                      # the signal is on screen
    assert cv.grid_pitch(hi - lo) <= cv.GRID_STEPS[-1]


def test_a_harmonic_above_the_response_still_fits():
    """The broken-loopback shape: the second harmonic reads level
    with the fundamental. Nonsense as physics, but the window must
    still be a window."""
    curves = [cv.Curve("take", np.full(50, -1.0), cv.C_RESPONSE,
                       key="response"),
              cv.Curve("H2", np.full(50, -1.0), cv.C_H2,
                       harmonic=True)]
    lo, hi = cv.window_db(curves)
    assert hi - lo <= cv.MAX_SPAN_DB + 2 * cv.GRID_STEPS[-1]
    assert lo < -1.0 < hi


# --- the headroom cubes ---------------------------------------------

def _shade(deficit, step, floor=0.15, span=0.55):
    """The law peq_view paints a cube with, in the open so a court can
    read it without GTK: nothing at zero, full at the whole step."""
    t = 0.0 if not step else max(0.0, min(1.0, deficit / step))
    return floor + span * t


def test_a_cube_darkens_with_how_short_the_rung_came():
    """A band that took half the step is not as bad as one that took
    none, and his words for the difference were "here are distortions"
    against "here are already a lot of distortions"."""
    assert _shade(0.0, 2.0) < _shade(1.0, 2.0) < _shade(2.0, 2.0)
    assert abs(_shade(2.0, 2.0) - 0.70) < 1e-9


def test_a_response_that_falls_does_not_overflow_the_shade():
    """Near hard limiting a rig can come back QUIETER than the rung
    below, which makes the shortfall exceed the step it was measured
    against -- his iLoud reads 3.8 dB short of a 2 dB step at 1 kHz on
    the loudest rung. The quantity stays honest; the ink stops."""
    assert _shade(3.8, 2.0) == _shade(2.0, 2.0)


# --- the curve that stops predicting --------------------------------

def _lossy(loss):
    """The law peq_view renames and recolours by, in the open."""
    return any(d is not None and math.isfinite(d) and d > 0.0
               for d in (loss or []))


def test_the_curve_keeps_its_name_while_it_keeps_its_promise():
    """`predicted` is the solver's forecast: measured plus filters, on
    the assumption the rig answers whatever it is given. While that
    holds there is nothing to rename."""
    assert not _lossy(None)
    assert not _lossy([0.0, 0.0, 0.0])
    assert not _lossy([None, float("nan"), 0.0])


def test_it_is_no_longer_a_forecast_once_it_carries_a_measurement():
    """Once part of the line is measured loss rather than arithmetic
    it is not predicting anything -- it is what comes out. His JBL
    gives 3.9 dB less at 20 Hz past about 80% of the knob."""
    assert _lossy([0.0, 0.0, 3.9])


def _delivered(knob, correction_db):
    """What the rig receives at ONE frequency, in the units the map
    speaks: the sweeps that built the map played flat, while music
    goes through the correction first."""
    return knob * 10.0 ** (correction_db / 60.0)


def test_what_the_rig_receives_is_a_curve_not_a_number():
    """Every rung of the map was measured with an UNATTENUATED sweep.
    What a listener plays goes through the correction, and the
    correction is a curve: the preamp takes decibels off everywhere
    and the filters give some back where they lift.

    His words: the bass arrives at the sweep's own level because the
    filters return what the preamp took, while everything that shouts
    is turned down. So different frequencies read different rungs of
    the same map."""
    # a preamp of -10 with a 10 dB lift: the rig gets what the sweep
    # gave it, and the knob reads its own rung
    assert abs(_delivered(0.80, 0.0) - 0.80) < 1e-9
    # two octaves up, where nothing is lifted, it reads much lower
    assert abs(_delivered(0.80, -10.0) - 0.55) < 0.01
    # and where the correction LIFTS more than the preamp cuts, the
    # rig is driven harder than the knob suggests
    assert _delivered(0.80, +4.0) > 0.80


def _advice_band(loss, freqs):
    """The band a loss lives in, and the floor that stops asking for
    it -- the law the advice line states, in the open."""
    a = [x for x in loss]
    bad = [i for i, x in enumerate(a)
           if x is not None and math.isfinite(x) and x > 0]
    if not bad:
        return None
    return freqs[bad[0]], freqs[bad[-1]], max(a[i] for i in bad)


def test_the_line_says_where_it_is_short_and_what_floor_stops_it():
    """The strip and the curve say WHERE a rig runs out and HOW MUCH.
    A listener still has to decide what to do, and both numbers worth
    knowing come straight out of the map: how far past this is, and
    the floor that would stop asking for it.

    The floor offered is the TOP of the band -- everything below is
    being asked for and not delivered, so a floor there stops asking
    without touching anything the rig can still do. Checked against
    his ear on the iLoud: the map put the loss in 40-92 Hz, he tried
    60 Hz by hand, and the noise went."""
    freqs = [20.0 * 2 ** (i / 12.0) for i in range(60)]
    quiet = [0.0] * len(freqs)
    assert _advice_band(quiet, freqs) is None

    loss = list(quiet)
    for i, f in enumerate(freqs):
        if 40.0 <= f <= 92.0:
            loss[i] = 3.4
    lo, hi, worst = _advice_band(loss, freqs)
    assert 39.0 <= lo <= 41.0
    assert 88.0 <= hi <= 93.0
    assert worst == 3.4


def test_the_taste_layer_is_part_of_what_the_rig_receives():
    """A taste layer is not part of a profile -- it composes over the
    correction, after it, for every chain -- and the loss reading had
    been leaving it out.

    His own layer lifts 50 Hz by twelve decibels. Against a preamp of
    -12 that puts 40 Hz back at the sweep's own level while everything
    else sits ten to twenty below, which is exactly his description:
    the bass arrives at full strength and everything that shouts is
    turned down. Without counting it the arithmetic credited the
    driver with twelve decibels it never got to keep, and reported
    clean where his ear and a multitone probe both put the rig four
    decibels short."""
    # the composition is a sum in dB, so a lift can cancel a preamp
    lift, preamp = 12.0, -12.0
    assert abs((lift + preamp) - 0.0) < 1e-9
    # and where nothing is lifted, the preamp stands alone
    assert abs((0.0 + preamp) - (-12.0)) < 1e-9


def test_the_half_step_rule_is_not_the_test_for_a_loss_curve():
    """"Took less than HALF the step" is built to find a hard border
    between neighbouring rungs. Read against a distant base it is far
    too lax: at the level he listens at, his iLoud is 2.05 dB short of
    a step that arrived 8.2 -- audible, real, and nowhere near half.
    A rig that gives up forty percent of every step, steadily, never
    trips it at all, and that is exactly what a port does.

    What counts is whether the deficit is REAL, which is a question
    about measurement: twice the scatter between sweeps, since a loss
    is a difference of two of them."""
    arrived, missing = 8.2, 2.05
    assert missing < 0.5 * arrived          # the old rule says nothing
    assert missing > 2.0 * 0.34             # the scatter at 50 Hz says yes


def test_nothing_under_a_decibel_earns_a_sentence():
    """A deficit can clear the scatter and still be nothing. His
    Tanchjim reads a few hundredths around 35 Hz -- a true measurement
    and an absurd sentence: "short by up to 0.0 dB". Half a readable
    step is the smallest thing worth a line, and below it two sweeps
    of one rig are already closer together than the claim."""
    floor = 0.5 * 2.0
    assert 0.04 < floor                     # the Tanchjim stays quiet
    assert 2.6 > floor                      # the iLoud speaks


def _unsafe_from(speaks_at):
    """The bisection the level strip shades from: the quietest knob
    position at which the rig is already short somewhere. Monotone in
    level, because every mechanism here gets worse with drive and
    never better."""
    if not speaks_at(1.0):
        return None
    lo, hi = 0.0, 1.0
    for _ in range(10):
        mid = 0.5 * (lo + hi)
        if speaks_at(mid):
            hi = mid
        else:
            lo = mid
    return hi


def test_the_strip_shades_from_where_the_rig_stops_following():
    """His iLoud is short from about 70% of the knob upward, with his
    correction and his taste; he settled at 60% by ear on the
    orchestral passage. A rig that never runs short gets no shading at
    all, which is his Tanchjim at every position."""
    edge = _unsafe_from(lambda v: v >= 0.70)
    assert 0.69 < edge < 0.71
    assert _unsafe_from(lambda v: False) is None


def test_unmeasured_is_neither_safe_nor_unsafe():
    """A map ends for a reason, and the reason decides what may be
    said about louder. If the CAPTURE ran out -- the microphone, not
    the rig -- nothing at all is known above the loudest rung, and
    three of his five rigs end that way after answering every rung
    they were given.

    Drawing that stretch as safe would be the same invention this
    project keeps removing, in the comfortable direction. It gets its
    own colour, and it is painted OVER the others so it never inherits
    their verdict."""
    def grey_from(stopped_by, top_level):
        """The law: a budget that ran out explored what it meant to,
        so nothing is unknown; any other ending leaves the ground
        above the loudest rung unmeasured."""
        return None if stopped_by == "rungs" else top_level

    assert grey_from("rungs", 0.80) is None
    assert grey_from("capture", 0.80) == 0.80
    assert grey_from("knob", 0.95) == 0.95
    assert grey_from("asked", 0.50) == 0.50


def test_a_fader_belongs_to_the_hand_while_it_is_held():
    """Two things made the level fader feel like porridge. A write per
    motion event, each a wpctl subprocess, so the hand outruns the
    pipe. And the poller then handing back a level it read up to three
    seconds ago, jumping the handle to where the hand no longer is.

    pavucontrol's answer is the right one: while a control is dragged
    it belongs to the hand, and what the server says about it is
    ignored until the hand lets go and the server has had a moment to
    agree."""
    write_every, hold_after = 0.05, 0.60

    def handle(level, dragging, since_release, from_server):
        """Where the handle sits: the hand's position while held and
        for a moment after, the server's once it has agreed."""
        if dragging or since_release < hold_after:
            return level
        return from_server

    # mid-drag the server's stale reading is ignored
    assert handle(0.62, True, 0.0, 0.85) == 0.62
    # and for a moment after, while the write is still in flight
    assert handle(0.62, False, 0.1, 0.85) == 0.62
    # then the server has the last word again
    assert handle(0.62, False, 1.0, 0.62) == 0.62
    assert write_every < hold_after


def test_a_rungs_loss_does_not_move_when_the_knob_does():
    """What changes with the volume is WHICH rung a frequency reads,
    not what that rung lost. Recomputing every rung per motion event
    cost 268 ms a call, with the take-to-take spread walked again
    inside each iteration, and the fader felt like porridge next to
    gnome-control-center's -- which does nothing per motion but move a
    handle.

    Worked out once and selected per frequency, the same reading takes
    half a millisecond."""
    # two rungs, each with its own loss, and a delivered level that
    # picks between them
    rungs = [(0.60, 0.0), (0.70, 2.5), (0.80, 4.0)]

    def read(delivered):
        take = 0.0
        for level, loss in rungs:
            if delivered >= level:
                take = loss
        return take

    assert read(0.65) == 0.0
    assert read(0.75) == 2.5
    assert read(0.95) == 4.0
    # and nothing about the rungs themselves changed between those
    assert rungs[1][1] == 2.5


def test_the_base_rung_has_to_be_heard_itself():
    """Everything is read against the base, so the base's own noise
    enters every reading. On a walk whose base stood 1.4 dB over the
    noise at 50 Hz the loss at the top read 4.89 dB; against a base
    standing 21.5 dB over it the same rung read 3.92. Nearly a decibel
    of the answer was the reference's hiss.

    It happens when the search settles low -- a sensitive microphone
    reaches the capture ceiling at a lower knob -- and the map then
    starts twelve decibels under that, in the noise. Choosing the base
    by AUDIBILITY costs nothing, the rungs are already walked."""
    def pick(rungs):
        """The law: the quietest rung that is itself heard over a
        quarter of the band."""
        for level, heard_fraction in rungs[:-1]:
            if heard_fraction >= 0.25:
                return level
        return rungs[0][0]

    # a walk whose two quietest rungs are in the noise
    assert pick([(0.30, 0.02), (0.33, 0.10), (0.35, 0.60),
                 (0.38, 0.90)]) == 0.35
    # and one that is audible from the start keeps its first rung
    assert pick([(0.41, 0.70), (0.44, 0.80)]) == 0.41


MAP_METHODS = ("_map_rungs", "_arm_walk", "_map_steps", "_ladder_probes", "_ladder_rows", "_draw_ladder", "_ladder_record", "_ladder_k", "_ladder_word_for", "_draw_hunt", "_wrapped", "_map_state_line", "_map_ref",
               "_map_mask", "_draw_map", "_walking", "_draw_fan",
               "_draw_check", "_map_band", "_map_at")


def map_fake():
    """The window's map methods, lifted out of the class so a canvas
    can be judged without gi.

    A call to a method that was never written passes pyflakes and
    every court here, and fails the moment a canvas repaints: a
    rename left _draw_map calling a _draw_fan that did not exist, and
    the window died on the first redraw. Nothing exercised the
    drawing at all.
    """
    import math
    import re
    import numpy as np
    from perdeviceeq import level_run

    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "perdeviceeq",
        "measure_window.py")).read()

    def grab(name):
        m = re.search(r"\n    (?:@staticmethod\n    )?def %s\(.*?"
                      r"(?=\n    (?:@staticmethod|def|_MAP_ENDS|"
                      r"REF_HEARD))" % name, src, re.S)
        assert m, "%s is not defined at all" % name
        return m.group(0)

    ns = {"math": math, "np": np, "level_run": level_run,
          "FMIN_PLOT": 20.0, "FMAX_PLOT": 20000.0,
          "MAP_MUTE_DB": 1.0, "MAP_ODD_FLOOR_DB": 0.15, "MAP_H": 230,
          "LADDER_ROW_H": 26, "LADDER_SPAN_DB": 2.0,
          "HUNT_SLOTS": 8, "HUNT_FLOOR_DB": -60.0}
    body = "".join(grab(n) for n in MAP_METHODS)
    body += ("\n    _MAP_ENDS = " +
             re.search(r"_MAP_ENDS = (\{.*?\})", src, re.S).group(1) +
             "\n    REF_HEARD = 0.9\n")
    exec("class Fake:\n" + body, ns)
    return ns["Fake"]


def map_rungs(steps):
    """A walk whose rungs climb by `steps` decibels, one per step.

    The grid is the real one -- 958 bins of 96 per octave from 20 Hz
    -- so a pointer anywhere in the plot stands on a bin that was
    drawn, as it does in the field.
    """
    n = 958
    slope = [45.0 - 90.0 * i / (n - 1) for i in range(n)]
    rungs, up = [], 0.0
    for j, d in enumerate([0.0] + list(steps)):
        up += d
        r = {"level": 0.12 * 10.0 ** (up / 60.0),
             "peak_dbfs": -30.0 + up,
             "heard_offset_db": -40.0,
             "mag_db": [up + s for s in slope],
             "stopped_by": "capture" if j == len(steps) else None}
        if j == 0:
            r["scatter_db"] = [0.1] * n
        rungs.append(r)
    return rungs


def map_window(rungs, on=False, partial=None, pick=2, hover=1,
               probes=None):
    import types
    prof = {"passport": {"FL": {"rungs": rungs, "probes": probes or []}},
            "measurement": {"grid": {"f_lo": 20.0, "ppo": 96}}}
    f = map_fake()()
    f.ch_keys = ["FL"]
    f._selected_ch = 0
    f.edit_pid = "p"
    f._map_pick = pick
    f._map_hover = hover
    f._map_odd = None
    f._busy = False
    f._fan_geom = None
    f._map_announce = None
    f._walk_probes = []
    f._ladder_repaint = lambda: False
    class _Lbl:
        def __init__(self): self.t = ""
        def get_text(self): return self.t
        def set_text(self, t): self.t = t
    f.ladder_word = _Lbl()
    f.ladder_title = _Lbl()
    f._hunt_dots = []
    f._hunt_found = None
    f._walk_phase = None
    f._probes_fresh = False
    f._ladder_split = 0
    f._map_partial = rungs[:partial] if partial is not None else []
    f._walk_live = partial is not None
    f.map_area = types.SimpleNamespace(get_height=lambda: 230)
    f.map_view = types.SimpleNamespace(get_active=lambda: on)
    f.parent = types.SimpleNamespace(
        store=types.SimpleNamespace(get=lambda _p: prof))
    return f


def test_the_fan_picks_the_line_the_pointer_is_on():
    """His field report, in one sentence: aiming at the second line
    and getting the third. The ladder steps 6, 6, 2, 2, 2, 2, so the
    lines are not evenly spaced and the even band grid the hit test
    used points somewhere else than the eye does.
    """
    import cairo
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0, 2.0, 2.0])
    # a rig whose loss grows with level, so the STEPS differ and the
    # lines stand apart: on a linear ladder every step lies on zero
    # and there is nothing to aim at
    for k, r in enumerate(rungs):
        r["mag_db"] = [v - 0.3 * k * k for v in r["mag_db"]]
    f = map_window(rungs, on=False)
    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 230))
    f._draw_map(None, cr, 700, 230)

    rows, bin_at, py = f._fan_geom
    x = 350.0
    i = bin_at(x)
    for k in range(len(rungs)):
        if rows[k][i] is None:          # the base has no step to aim at
            continue
        assert f._map_at(x, py(rows[k][i])) == k

    # AND THE LADDER IS ADVERSARIAL, or this court has no teeth: on an
    # uneven ladder the band arithmetic must answer something other
    # than the line the eye is on. Which rung it misses is a property
    # of the axis, not of the defect, so it is not pinned here.
    band = [f._map_band(py(rows[k][i])) for k in range(1, len(rungs))]
    assert band != list(range(1, len(rungs))), "the fixture is too even"


def test_the_shelves_keep_their_bands():
    """The even spacing there is this window's own, so a band IS a
    rung and there is no line to aim at."""
    import cairo
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0, 2.0, 2.0])
    f = map_window(rungs, on=True)
    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 230))
    f._draw_map(None, cr, 700, 230)
    assert f._fan_geom is None
    for y in (30.0, 90.0, 150.0, 200.0):
        assert f._map_at(350.0, y) == f._map_band(y)


def test_nothing_is_under_the_pointer_where_no_line_was_drawn():
    """A fan too short to draw leaves no geometry, and a view that
    cannot be aimed at must not answer at random."""
    import cairo
    f = map_window(map_rungs([])[:1], on=False)
    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 230))
    f._draw_map(None, cr, 700, 230)
    assert f._fan_geom is None
    assert f._map_at(350.0, 120.0) is None


def test_every_map_view_draws_without_gtk():
    """Each view on a plain cairo surface, with the rungs a walk
    actually produces."""
    import types
    import cairo

    Fake = map_fake()
    n = 200
    slope = [45.0 - 90.0 * i / (n - 1) for i in range(n)]
    rungs = []
    for j in range(7):
        r = {"level": 0.12 * 10.0 ** (j * 2.0 / 60.0),
             "peak_dbfs": -30.0 + j * 2.0,
             "heard_offset_db": -40.0,
             "mag_db": [j * 2.0 + s for s in slope],
             "stopped_by": "capture" if j == 6 else None}
        if j == 0:
            r["scatter_db"] = [0.1] * n
        rungs.append(r)
    prof = {"passport": {"FL": {"rungs": rungs}},
            "measurement": {"grid": {"f_lo": 20.0, "ppo": 96}}}

    for on in (False, True):
        for k in (0, 3, 7):
            f = Fake()
            f.ch_keys = ["FL"]
            f._selected_ch = 0
            f.edit_pid = "p"
            f._map_pick = 2
            f._map_hover = 1
            f._map_odd = None
            f._busy = False
            f._fan_geom = None
            f._map_partial = rungs[:k]
            f.map_area = types.SimpleNamespace(get_height=lambda: 230)
            f.map_view = types.SimpleNamespace(get_active=lambda: on)
            f.parent = types.SimpleNamespace(
                store=types.SimpleNamespace(get=lambda _p: prof))
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 230)
            cr = cairo.Context(surf)
            f._draw_map(None, cr, 700, 230)


def test_a_walk_owns_the_canvas():
    """The shelves need five rungs and used to borrow the fan until
    the fifth arrived, then take over mid-walk: axis, colour and the
    shape of every line changed in one frame with nothing said. A
    walk now draws one picture from the first rung to the last.
    """
    import cairo
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0, 2.0, 2.0])
    for n in (2, 5, 7):
        f = map_window(rungs, on=True, partial=n)
        f._busy = True
        assert f._walking()
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 230))
        f._draw_map(None, cr, 700, 230)
        # the fan leaves its geometry; the shelves do not
        assert f._fan_geom is not None, "the shelves took over at %d rungs" % n
    # and the moment the walk ends the toggle is honoured again
    f = map_window(rungs, on=True)
    f._busy = False
    assert not f._walking()
    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 230))
    f._draw_map(None, cr, 700, 230)
    assert f._fan_geom is None


def test_neither_picture_takes_its_scale_from_the_data():
    """A clean walk was stretched to its own worst number, so
    hundredths of a decibel filled the picture and every arriving rung
    rescaled what was already drawn. The fan's axis is the walk's
    reach and the shelves are as tall as the bar -- both known from
    the first rung, neither moving after it.
    """
    import cairo
    import copy
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0, 2.0, 2.0])

    def axis(rs):
        f = map_window(rs, on=False)
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 230))
        f._draw_map(None, cr, 700, 230)
        _rows, _bin_at, py = f._fan_geom
        return py(0.0), py(1.0)          # where zero and +1 dB land

    was = axis(rungs)
    # the same walk with a rung that departs by ten decibels: the axis
    # may not follow it
    bent = copy.deepcopy(rungs)
    bent[4]["mag_db"] = [None if v is None else v - 10.0
                         for v in bent[4]["mag_db"]]
    assert axis(bent) == was
    # and it does not creep as the walk arrives rung by rung
    assert axis(rungs[:3]) == was


def test_the_press_hands_the_canvas_over_before_the_first_result():
    """A walk that has not produced a rung yet and a window with no
    walk look identical in _map_partial, so the canvas kept drawing
    the whole old map until the first new rung landed -- a sweep and
    a half of looking at what the button had already discarded."""
    import cairo
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0, 2.0, 2.0])
    f = map_window(rungs, on=False, partial=0)   # armed, nothing yet
    f._busy = True
    assert f._walking()
    assert f._map_rungs() == []
    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 230))
    f._draw_map(None, cr, 700, 230)              # says so rather than lying
    assert f._fan_geom is None
    # a rebuild that keeps two shows exactly those two, from the press
    f = map_window(rungs, on=False, partial=2)
    f._busy = True
    assert [r["level"] for r in f._map_rungs()] == \
        [r["level"] for r in rungs[:2]]


def test_arming_a_walk_asks_for_the_frame():
    """0336 changed what the canvas would draw and never told the
    widget, so the old map stayed up until the first rung arrived and
    called queue_draw on its way past -- which is exactly what the
    field reported. Setting what a canvas shows and repainting it
    belong in one place."""
    import types
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0, 2.0, 2.0])
    f = map_window(rungs, on=False, pick=2)
    f._walk_live = False
    f._map_partial = []
    f._rebuild_ack = True
    f._sync_relevel = lambda: None
    drawn = []
    f.map_area = types.SimpleNamespace(get_height=lambda: 230,
                                       queue_draw=lambda: drawn.append(1))
    f._arm_walk(True)
    assert f._walk_live
    assert [r["level"] for r in f._map_partial] == \
        [r["level"] for r in rungs[:2]]
    assert drawn, "the canvas was never asked to repaint"

    # a take is not a walk and must leave the map alone
    g = map_window(rungs, on=False, pick=2)
    g._walk_live = False
    g._map_partial = []
    g._rebuild_ack = True
    g._sync_relevel = lambda: None
    g.map_area = types.SimpleNamespace(get_height=lambda: 230,
                                       queue_draw=lambda: drawn.append(1))
    g._arm_walk(False)
    assert not g._walk_live and g._map_partial == []


def test_marking_the_lowest_rung_is_a_choice_not_the_absence_of_one():
    """`if keep:` read index zero as nobody having marked anything, so
    the choice was never spent and every rung above it stayed faded
    for the whole walk -- against a ladder that no longer had them."""
    import types
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0, 2.0, 2.0])
    f = map_window(rungs, on=False, pick=0)
    f._walk_live = False
    f._map_partial = []
    f._rebuild_ack = True
    f._sync_relevel = lambda: None
    f.map_area = types.SimpleNamespace(get_height=lambda: 230,
                                       queue_draw=lambda: None)
    f._arm_walk(True)
    assert f._walk_keep == 0            # remembered as a choice
    assert f._map_pick is None          # and spent
    assert f._map_partial == []         # keeping none of them
    assert [r["level"] for r in f._walk_old] == \
        [r["level"] for r in rungs]


def test_a_step_is_read_against_what_it_asked():
    """On a linear ladder every step delivers what it asked and every
    line lies on zero; a rung that lost three decibels reads -3 on its
    own step and +3 on the next -- the mirror -- and no other step
    hears of it."""
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0, 2.0, 2.0])
    f = map_window(rungs, on=False)
    rows = f._map_steps(rungs)
    assert len(rows) == len(rungs)
    assert all(v is None for v in rows[0])          # the base has no step
    assert max(abs(v) for row in rows[1:] for v in row if v is not None) < 1e-9

    hurt = [dict(r) for r in rungs]
    m = list(hurt[3]["mag_db"])
    for i in range(300, 312):
        m[i] -= 3.0
    hurt[3]["mag_db"] = m
    rows = f._map_steps(hurt)
    assert abs(rows[3][305] + 3.0) < 1e-6           # the step that lost
    assert abs(rows[4][305] - 3.0) < 1e-6           # mirrored on the next
    for k in (1, 2, 5, 6):
        assert abs(rows[k][305]) < 1e-9             # and nowhere else
    # a real loss does not mirror: a rung that falls behind and stays
    # behind reads once and the next step reads zero
    lost = [dict(r) for r in rungs]
    for k in range(3, len(lost)):
        lost[k]["mag_db"] = [v - 3.0 for v in lost[k]["mag_db"]]
    rows = f._map_steps(lost)
    assert abs(rows[3][305] + 3.0) < 1e-6
    assert abs(rows[4][305]) < 1e-9


def test_the_search_is_drawn_as_a_search():
    """A hunt is a walk over LEVEL, so its picture is step against
    level: one dot per probe, joined in order, a line where it
    settled. The level axis is the whole knob, fixed, so no dot moves
    when another lands."""
    import re
    import cairo
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "perdeviceeq",
        "measure_window.py")).read()
    m = re.search(r"\n    def _draw_hunt\(.*?(?=\n    def )", src, re.S)
    assert m, "_draw_hunt is not defined at all"
    ns = {"math": math, "HUNT_SLOTS": 8, "HUNT_FLOOR_DB": -60.0}
    exec("class Fake:\n" + m.group(0), ns)
    f = ns["Fake"]()
    f._ladder_record = lambda: {}    # no search on record here
    f._hunt_found = None
    f._hunt_dots = [(1, 0.15, "quiet"), (2, 0.30, "quiet"),
                    (3, 0.60, "loud"), (4, 0.42, "ok")]
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 48)
    f._draw_hunt(None, cairo.Context(surf), 700, 48)
    f._hunt_found = 0.42
    f._draw_hunt(None, cairo.Context(surf), 700, 48)
    # nothing drawn is fine too: an empty strip is a strip
    f._hunt_dots = []
    f._draw_hunt(None, cairo.Context(surf), 700, 48)


def test_the_passport_canvas_draws_every_row_it_has():
    """Above the rule the rungs by level, loudest on top, numbered
    after the probes; below it the probes with their step numbers; an
    announced row dashed among the rungs at its level, with no curve.
    Every state draws on a bare cairo canvas."""
    import cairo
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0, 2.0, 2.0])
    probes = [{"step": 1, "level": 0.15, "peak_dbfs": -17.0,
               "verdict": "quiet", "mag_db": rungs[1]["mag_db"]},
              {"step": 2, "level": 0.20, "peak_dbfs": -10.0,
               "verdict": "ok", "mag_db": rungs[3]["mag_db"]},
              {"step": 3, "level": 0.17, "peak_dbfs": -13.0,
               "verdict": "quiet", "mag_db": rungs[2]["mag_db"]}]
    f = map_window(rungs, on=False, probes=probes)
    rows = f._ladder_rows()
    kinds = [r[0] for r in rows]
    assert kinds == ["rung"] * 7 + ["probe"] * 3
    # loudest on top, numbered after the last probe
    assert [r[1] for r in rows[:7]] == [10, 9, 8, 7, 6, 5, 4]
    assert rows[6][5] == "base"
    # probes by level, loudest first, with their own steps
    assert [r[1] for r in rows[7:]] == [2, 3, 1]
    assert rows[7][4] == "(2) 20%"
    # an announced level slots among the rungs, dashed, no curve
    f._map_announce = (0.235, "playing")
    rows = f._ladder_rows()
    at = [r[0] for r in rows].index("announce")
    assert rows[at - 1][2] > 0.235 > rows[at + 1][2]
    assert rows[at][3] is None
    for ann in (None, (0.235, "playing"), (0.12, "seating")):
        f._map_announce = ann
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 400)
        f._draw_ladder(None, cairo.Context(surf), 700, 400)
    # nothing at all is a sentence, not an error
    g = map_window([], on=False)
    g._draw_ladder(None, cairo.Context(surf), 700, 400)


def test_the_passport_card_speaks_and_shows_its_choice():
    """The verdict is written when the record is read; the strip draws
    the stored search when none is running; the chosen rung is drawn
    on this canvas too."""
    import cairo
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0, 2.0, 2.0])
    probes = [{"step": 1, "level": 0.15, "verdict": "quiet",
               "mag_db": rungs[1]["mag_db"]},
              {"step": 2, "level": 0.20, "verdict": "ok",
               "mag_db": rungs[3]["mag_db"]}]
    f = map_window(rungs, on=False, probes=probes, pick=4)
    f.parent.store.get(f.edit_pid)["passport"]["FL"]["settled"] = 0.20
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 400)
    f._draw_ladder(None, cairo.Context(surf), 700, 400)
    assert f.ladder_title.get_text() == "FL"
    assert f.ladder_word.get_text().startswith("linear at least to")
    # the strip has something to draw from the record alone
    f._draw_hunt(None, cairo.Context(surf), 700, 48)
    # a slot goes back to its rung
    assert f._ladder_k(f._ladder_rows()[0][1]) == len(rungs) - 1


def test_a_search_announces_below_the_rule_and_starts_empty():
    """While the search runs, its announce is a row among the probes,
    not among the rungs; a fresh search shows no old probes until its
    own land; and a rebuild keeps the record's."""
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0])
    old = [{"step": 1, "level": 0.15, "verdict": "quiet",
            "mag_db": rungs[1]["mag_db"]}]
    f = map_window(rungs, on=False, probes=old)
    # fresh search, nothing landed yet: no old probes, announce below
    f._probes_fresh = True
    f._walk_phase = "search"
    f._map_announce = (0.15, "playing")
    rows = f._ladder_rows()
    assert [r[0] for r in rows].count("probe") == 0
    assert rows[-1][0] == "announce" and f._ladder_split == len(rungs)
    # the first probe lands: it is a row, the announce moves on
    f._walk_probes = [{"step": 1, "level": 0.15, "verdict": "quiet",
                       "mag_db": rungs[1]["mag_db"]}]
    f._map_announce = (0.20, "playing")
    rows = f._ladder_rows()
    kinds = [r[0] for r in rows[f._ladder_split:]]
    assert kinds == ["announce", "probe"]     # by level, loudest first
    # a rebuild keeps the record's probes and announces among the rungs
    f._probes_fresh = False
    f._walk_probes = []
    f._walk_phase = "ladder"
    f._map_announce = (0.235, "playing")
    rows = f._ladder_rows()
    assert [r[0] for r in rows[:f._ladder_split]].count("announce") == 1
    assert [r[0] for r in rows[f._ladder_split:]] == ["probe"]


def test_a_walk_that_never_went_quiet_still_reads():
    """With the margin per bin, a clean rig's base sits tens of dB
    above its floor in every bin: no quiet end to fit, and refusing
    the model left the map with no top, no knee, no verdict."""
    from perdeviceeq import level_run as L
    rungs = map_rungs([6.0, 6.0, 2.0, 2.0])
    base = dict(rungs[0])
    base["floor_db"] = [-50.0] * len(base["mag_db"])
    base["scatter_db"] = [0.03] * len(base["mag_db"])
    m = L.scatter_model(base)
    assert m is not None and m[0] == 0.0 and abs(m[1] - 0.03) < 1e-6
