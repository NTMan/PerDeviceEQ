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

def test_the_default_name_survives_git_and_a_url():
    import datetime
    day = datetime.date(2026, 8, 5)
    assert ce.export_name("Tanchjim Origin", "FL \u00b7 mean",
                          day) == \
        "tanchjim-origin-fl-mean-2026-08-05.svg"
    assert ce.export_name("IL-DSP Analog Stereo",
                          "FR \u00b7 take 2 of 3", day, "png") == \
        "il-dsp-analog-stereo-fr-take-2-of-3-2026-08-05.png"
    # a name with nothing ASCII in it still yields a usable file
    assert ce.export_name("\u041d\u0430\u0443\u0448", "",
                          day) == "2026-08-05.svg"
    assert ce.export_name("", "", None) != ".svg"
    for bad in (" ", "/", "\\", "'", '"', "\u00b7"):
        assert bad not in ce.export_name("a b/c \u00b7 d",
                                         "e'f", day)


def test_the_slug_is_narrow_on_purpose():
    assert ce.slug("Hello, World!") == "hello-world"
    assert ce.slug("--a--b--") == "a-b"
    assert ce.slug(None) == ""
    assert ce.slug("\u041c\u0438\u0448\u0430") == ""
