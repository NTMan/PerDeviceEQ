# -*- coding: utf-8 -*-
"""Take a painter's picture out of the app: vector for editors that
speak it, raster for everything else.

Both come from the SAME painter that draws on screen -- the second
dividend of a curve_view that knows nothing about gi. Nothing is
screenshotted and nothing is upscaled: the picture is drawn again at
the export size, so lines are re-hinted and labels re-laid at that
size instead of being stretched.

Three rules keep an exported picture honest and portable. The paper
is explicitly light, not the app's theme, so a paste into a document
does not land grey-on-nothing. The labels go out as OUTLINES, since
cairo would otherwise write <text> nodes that render in whatever
font the reader happens to own. And the pointer is not in the
picture: the crosshair and the name under it are answers to a hand
that will not be there. Pins DO stay -- a pinned line is a statement
the author made on purpose.
"""
import contextlib
import datetime
import io
import re

import cairo

from . import curve_view as cv

EXPORT_W, EXPORT_H = 1200, 675
RASTER_SCALE = 2            # 2400x1350 -- crisp on a dense screen
PAPER = (1.0, 1.0, 1.0)


def url_part(text):
    """One part of a file name, by the architect's rule: keep the
    letters as they are and replace whatever a URL would rather not
    carry -- spaces included -- with an underscore. CASE IS KEPT: a
    file name is also a label, and "Tanchjim_Origin" reads while
    "tanchjim-origin" only sorts.

    The hyphen goes to an underscore too, because the hyphen is what
    joins the parts: without that rule "IL-DSP Analog Stereo" would
    make a name nobody could take apart again. The apostrophe stays
    -- a URL carries it without escaping, and only a shell minds."""
    out = re.sub(r"[^A-Za-z0-9._']+", "_", (text or "").strip())
    return re.sub(r"_+", "_", out).strip("_")


def export_name(prefix, parts, when=None, ext="svg"):
    """prefix-part-part-date.ext, e.g.
    pdeq-Tanchjim_Origin-FL-mean-2026-08-05.svg. The app cannot
    offer a markdown LINK -- that is relative to a document root
    per-device-eq knows nothing about -- but it can offer a name
    worth pasting, and the name has to be generated for that to be
    true."""
    day = (when or datetime.date.today()).isoformat()
    bits = [url_part(prefix)]
    bits += [url_part(p) for p in (parts or ())]
    bits.append(day)
    stem = "-".join(b for b in bits if b) or "graph"
    return "%s.%s" % (stem, ext)


@contextlib.contextmanager
def _quiet(painter):
    """Any object that can draw itself may be exported: it needs
    draw(area, cr, w, h) and, if it has state a render would
    disturb, a quiet() of its own. The equalizer graph has plenty --
    the pointer, but also the plot geometry and the traces it reads
    back for hit-testing -- and an export at 1200x675 would leave
    those describing a canvas that does not exist. The flag for
    outlined labels is restored here in either case."""
    paths = cv.TEXT_AS_PATH
    own = getattr(painter, "quiet", None)
    try:
        if own is None:
            yield
        else:
            with own():
                yield
    finally:
        cv.TEXT_AS_PATH = paths


def _paint(cr, plot, w, h):
    cr.set_source_rgb(*PAPER)
    cr.rectangle(0, 0, w, h)
    cr.fill()
    plot.draw(None, cr, w, h)


def svg_bytes(plot, w=EXPORT_W, h=EXPORT_H):
    """The picture as SVG, labels as outlines."""
    buf = io.BytesIO()
    surf = cairo.SVGSurface(buf, w, h)
    cr = cairo.Context(surf)
    with _quiet(plot):
        cv.TEXT_AS_PATH = True
        _paint(cr, plot, w, h)
    surf.finish()
    return buf.getvalue()


def png_bytes(plot, w=EXPORT_W, h=EXPORT_H, scale=RASTER_SCALE):
    """The picture as PNG, drawn at scale and never enlarged after
    the fact."""
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24,
                              int(w * scale), int(h * scale))
    cr = cairo.Context(surf)
    cr.scale(scale, scale)
    with _quiet(plot):
        _paint(cr, plot, w, h)
    buf = io.BytesIO()
    surf.write_to_png(buf)
    return buf.getvalue()
