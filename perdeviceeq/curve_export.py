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
import io

import cairo

from . import curve_view as cv

EXPORT_W, EXPORT_H = 1200, 675
RASTER_SCALE = 2            # 2400x1350 -- crisp on a dense screen
PAPER = (1.0, 1.0, 1.0)


class _Quiet:
    """The pointer steps out of the picture for the duration of the
    render, and steps back in afterwards -- the canvas on screen
    must not notice that it was copied."""

    def __init__(self, plot):
        self.plot = plot
        self.cursor = None
        self.hover = None
        self.paths = False

    def __enter__(self):
        self.cursor = self.plot.cursor
        self.hover = self.plot.state.hover
        self.paths = cv.TEXT_AS_PATH
        self.plot.cursor = None
        self.plot.state.hover = None
        return self

    def __exit__(self, *_exc):
        self.plot.cursor = self.cursor
        self.plot.state.hover = self.hover
        cv.TEXT_AS_PATH = self.paths
        return False


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
    with _Quiet(plot):
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
    with _Quiet(plot):
        _paint(cr, plot, w, h)
    buf = io.BytesIO()
    surf.write_to_png(buf)
    return buf.getvalue()
