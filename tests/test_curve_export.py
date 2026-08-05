"""The exporter's court. curve_export writes with real cairo onto
real surfaces, so everything here is checked on the actual bytes: no
<text> nodes in the vector, a raster that is drawn rather than
stretched, light paper regardless of theme, and no pointer inside a
picture that will be pasted somewhere else."""

import numpy as np
import pytest

cairo = pytest.importorskip("cairo")

from perdeviceeq import curve_export as ce      # noqa: E402
from perdeviceeq import curve_view as cv        # noqa: E402

FREQS = np.geomspace(20.0, 20000.0, 200)


def _plot(state=None):
    mag = -36.0 + 6.0 * np.sin(np.log(FREQS))
    thd = np.full(200, -88.0)
    thd[150:] = np.nan
    return cv.Plot(
        FREQS,
        [cv.Curve("take", mag, cv.C_RESPONSE, key="response"),
         cv.Curve("THD", thd, cv.C_THD, harmonic=True),
         cv.Curve("noise", np.full(200, -99.0), cv.C_NOISE,
                  harmonic=True, land=True)],
        -108.0, -24.0, state=state, legend=True)


def test_the_vector_carries_no_font_dependency():
    data = ce.svg_bytes(_plot())
    assert data[:5] == b"<?xml"
    assert b"<svg" in data
    assert b"<text" not in data       # labels went out as outlines
    assert b"<path" in data
    assert b"1200" in data[:400]      # the fixed export geometry


def test_the_raster_is_drawn_and_not_stretched():
    data = ce.png_bytes(_plot())
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    surf = cairo.ImageSurface.create_from_png(__import__(
        "io").BytesIO(data))
    assert surf.get_width() == ce.EXPORT_W * ce.RASTER_SCALE
    assert surf.get_height() == ce.EXPORT_H * ce.RASTER_SCALE


def test_the_paper_is_light_whatever_the_theme():
    data = ce.png_bytes(_plot(), w=40, h=20, scale=1)
    surf = cairo.ImageSurface.create_from_png(__import__(
        "io").BytesIO(data))
    px = bytes(surf.get_data()[:4])
    assert px[0] > 200 and px[1] > 200 and px[2] > 200


def test_the_pointer_is_not_in_the_picture():
    """The crosshair and the name under it answer a hand that will
    not be there when the picture is pasted."""
    st = cv.Highlight()
    plot = _plot(st)
    bare = ce.svg_bytes(plot)
    pw = ce.EXPORT_W - cv.ML - cv.MR
    plot.set_cursor(cv.log_x(1000.0, cv.ML, pw), 200.0)
    st.hover = "THD"
    assert ce.svg_bytes(plot) == bare
    assert plot.cursor is not None        # and the canvas is intact
    assert st.hover == "THD"
    assert cv.TEXT_AS_PATH is False       # the flag went back


def test_a_pin_is_a_statement_and_travels():
    st = cv.Highlight()
    plot = _plot(st)
    bare = ce.svg_bytes(plot)
    st.hit("THD")
    assert ce.svg_bytes(plot) != bare

def test_the_name_follows_the_architects_scheme():
    import datetime
    day = datetime.date(2026, 8, 5)
    assert ce.export_name("pdeq", ("Tanchjim Origin", "FL"),
                          day) == \
        "pdeq-Tanchjim_Origin-FL-2026-08-05.svg"
    assert ce.export_name("taste", ("Makhail's taste",), day) == \
        "taste-Makhail's_taste-2026-08-05.svg"
    assert ce.export_name("pdeq", ("Tanchjim Origin", "FR",
                                  "take2"), day, "png") == \
        "pdeq-Tanchjim_Origin-FR-take2-2026-08-05.png"
    # a part with nothing usable in it drops out, it does not
    # leave an empty slot behind
    assert ce.export_name("pdeq", ("\u041d\u0430\u0443\u0448",
                                   "FL"), day) == \
        "pdeq-FL-2026-08-05.svg"
    assert ce.export_name("", (), None) != ".svg"


def test_a_part_carries_nothing_a_url_would_choke_on():
    assert ce.url_part("Tanchjim Origin") == "Tanchjim_Origin"
    assert ce.url_part("IL-DSP Analog Stereo") == \
        "IL_DSP_Analog_Stereo"
    assert ce.url_part("  a / b \\ c ? d # e  ") == "a_b_c_d_e"
    assert ce.url_part("__x__") == "x"
    assert ce.url_part(None) == ""
    assert ce.url_part("\u041c\u0438\u0448\u0430") == ""
    for bad in (" ", "/", "\\", "?", "#", "&", '"', "\u00b7"):
        assert bad not in ce.url_part("a%sb" % bad)

class _Painter:
    """Anything that can draw itself is exportable -- the contract
    is draw() plus an optional quiet() for state a render at another
    size would disturb. The equalizer graph is the real user of it;
    this stands in for it, since gi does not live here."""

    def __init__(self):
        self.geometry = "live"
        self.drew = []

    def draw(self, _area, cr, w, h, *_a):
        self.geometry = "%dx%d" % (w, h)
        self.drew.append((w, h))
        cr.set_source_rgb(0.1, 0.1, 0.1)
        cr.rectangle(0, 0, w, h)
        cr.fill()

    def quiet(self):
        import contextlib

        @contextlib.contextmanager
        def keeper():
            keep = self.geometry
            try:
                yield
            finally:
                self.geometry = keep
        return keeper()


def test_any_painter_can_be_exported_and_left_as_it_was():
    p = _Painter()
    data = ce.svg_bytes(p, w=200, h=100)
    assert b"<svg" in data
    assert p.drew == [(200, 100)]
    assert p.geometry == "live"        # the render was undone
    assert cv.TEXT_AS_PATH is False


class _Bare(_Painter):
    """No quiet() at all -- an exporter must not require one."""
    quiet = None


def test_a_painter_without_quiet_is_still_exportable():
    p = _Bare()
    png = ce.png_bytes(p, w=40, h=20, scale=1)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert p.drew == [(40, 20)]
