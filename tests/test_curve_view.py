"""The painter's court. curve_view draws onto a cairo context and
imports no gi, so the whole picture can be judged here: that the
axis is named, that the pen breaks where the sweep abstains, that
the land without harmonic evidence is shaded and said out loud,
and that the legend has hands only when it was drawn. The GTK-blind
suite let two field breakages through in one night -- this is the
answer to that, and it needs no xvfb."""

import math
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
