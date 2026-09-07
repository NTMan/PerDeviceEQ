#!/usr/bin/env python3
"""Draw a level map as one line per rung, and look at it.

The map has been stored whole since the day it was written -- every
rung keeps its response across the grid, not a verdict -- and nothing
has ever drawn it. It reaches a hand as one number: the percentage
where the walk stopped. That number said "the rig gave out at 89%"
and could not say HOW, and the difference matters: rungs that spread
apart evenly are compression, rungs that part in one place are a
port, a resonance or a limiter. One evening was spent arguing about
which of those a stall at 15.5 dB was, with nothing to look at.

So this is a looking-glass, not a feature. It writes an SVG next to
nothing and changes no behaviour; what the window should eventually
draw is a question to answer AFTER seeing what these lines do.

    tools/map_fan.py PROFILE.json [-o fan.svg] [--channel FL]

Each rung is drawn as its response MINUS the base rung, so a rig that
follows its knob draws flat lines stacked by the level asked for, and
every departure from that is the rig failing to follow. The base is
the quietest rung, which is what the map itself reads everything
against.
"""

import argparse
import json
import math
import os
import sys

W, H = 900, 520
PAD_L, PAD_R, PAD_T, PAD_B = 64, 78, 22, 44


def _grid_freqs(grid, n):
    lo, ppo = float(grid["f_lo"]), float(grid["ppo"])
    return [lo * 2.0 ** (i / ppo) for i in range(n)]


def _maps(prof):
    """(channel, rungs) for every map in the profile, in order.

    Sessions are walked rather than indexed because a profile can
    carry several, and because one of them may be the pseudo session
    keyed 'headroom' that an older cut wrote -- it holds a real map
    and the reader in the window steps straight over it.
    """
    m = prof.get("measurement") or {}
    out = []
    for sid, blk in sorted((m.get("sessions") or {}).items()):
        blk = blk or {}
        hr = blk.get("headroom")
        if isinstance(hr, dict):
            for ch, rungs in sorted(hr.items()):
                if rungs:
                    out.append((ch, rungs, sid))
        elif isinstance(blk, dict) and sid == "headroom":
            for ch, rungs in sorted(blk.items()):
                if rungs:
                    out.append((ch, rungs, sid))
    return out


def _fan(rungs, freqs):
    """Every rung against the quietest one, in decibels."""
    rungs = sorted(rungs, key=lambda r: r["level"])
    base = rungs[0]["mag_db"]
    lines = []
    for r in rungs:
        row = []
        for i, x in enumerate(r["mag_db"]):
            b = base[i] if i < len(base) else None
            row.append(None if x is None or b is None else x - b)
        lines.append((r["level"], row, r.get("stopped_by")))
    return lines


def _svg(lines, freqs, title, path):
    xs = [math.log10(f) for f in freqs]
    x0, x1 = xs[0], xs[-1]
    vals = [v for _, row, _ in lines for v in row if v is not None]
    if not vals:
        raise SystemExit("the map carries no readable response")
    y0, y1 = min(vals) - 1.0, max(vals) + 1.0

    def px(x):
        return PAD_L + (x - x0) / (x1 - x0) * (W - PAD_L - PAD_R)

    def py(v):
        return H - PAD_B - (v - y0) / (y1 - y0) * (H - PAD_T - PAD_B)

    p = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" '
         'height="%d" viewBox="0 0 %d %d">' % (W, H, W, H),
         '<rect width="%d" height="%d" fill="#fbfbfb"/>' % (W, H),
         '<text x="%d" y="16" font-family="sans-serif" font-size="12" '
         'fill="#333">%s</text>' % (PAD_L, title)]
    for f in (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000):
        if not freqs[0] <= f <= freqs[-1]:
            continue
        x = px(math.log10(f))
        p.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" '
                 'stroke="#e2e2e2"/>' % (x, PAD_T, x, H - PAD_B))
        p.append('<text x="%.1f" y="%d" font-family="sans-serif" '
                 'font-size="10" fill="#888" text-anchor="middle">'
                 '%s</text>'
                 % (x, H - PAD_B + 14,
                    "%dk" % (f // 1000) if f >= 1000 else f))
    step = 1 if (y1 - y0) < 12 else (2 if (y1 - y0) < 30 else 5)
    v = math.ceil(y0 / step) * step
    while v <= y1:
        y = py(v)
        p.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" '
                 'stroke="%s"/>'
                 % (PAD_L, y, W - PAD_R, y,
                    "#c9c9c9" if abs(v) < 1e-9 else "#ededed"))
        p.append('<text x="%d" y="%.1f" font-family="sans-serif" '
                 'font-size="10" fill="#888" text-anchor="end">'
                 '%+d</text>' % (PAD_L - 6, y + 3, v))
        v += step
    n = max(1, len(lines) - 1)
    for k, (level, row, stopped) in enumerate(lines):
        t = k / n
        col = "#%02x%02x%02x" % (int(40 + 195 * t), int(90 - 40 * t),
                                 int(200 - 160 * t))
        d, pen = [], False
        for i, val in enumerate(row):
            if val is None:
                pen = False
                continue
            d.append("%s%.1f %.1f" % ("L" if pen else "M",
                                      px(xs[i]), py(val)))
            pen = True
        p.append('<path d="%s" fill="none" stroke="%s" '
                 'stroke-width="%s" opacity="0.9"/>'
                 % (" ".join(d), col, "1.8" if stopped else "1.1"))
        # the label sits at the line's RIGHT END, where the lines are
        # furthest apart: hung at the middle they piled on top of each
        # other exactly where the rig was still following its knob
        last = next((v for v in reversed(row) if v is not None), 0.0)
        p.append('<text x="%d" y="%.1f" font-family="sans-serif" '
                 'font-size="9" fill="%s">%d%%%s</text>'
                 % (W - PAD_R + 5, py(last) + 3, col,
                    round(level * 100),
                    (" %s" % stopped) if stopped else ""))
    p.append('</svg>')
    with open(path, "w") as fh:
        fh.write("\n".join(p))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profile")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--channel", default=None,
                    help="only this channel; default is every map found")
    args = ap.parse_args(argv)

    with open(args.profile) as fh:
        prof = json.load(fh)
    grid = (prof.get("measurement") or {}).get("grid") or {}
    found = _maps(prof)
    if not found:
        print("no map in %s -- the profile carries takes or nothing, "
              "but no walk" % os.path.basename(args.profile))
        return 1
    if not grid:
        print("the profile has a map but no grid, so its rungs cannot "
              "be placed on a frequency axis")
        return 1
    made = 0
    for ch, rungs, sid in found:
        if args.channel and ch != args.channel:
            continue
        n = max(len(r["mag_db"]) for r in rungs)
        lines = _fan(rungs, _grid_freqs(grid, n))
        out = args.out or ("map-fan-%s.svg" % ch)
        if args.out and len([c for c, _, _ in found
                             if not args.channel or c == args.channel]) > 1:
            root, ext = os.path.splitext(args.out)
            out = "%s-%s%s" % (root, ch, ext or ".svg")
        _svg(lines, _grid_freqs(grid, n),
             "%s -- %s, %d rungs, every rung against the quietest"
             % (prof.get("name", "?"), ch, len(rungs)), out)
        print("%s: %d rungs %.0f%%..%.0f%% -> %s"
              % (ch, len(rungs), 100 * min(r["level"] for r in rungs),
                 100 * max(r["level"] for r in rungs), out))
        made += 1
    if not made:
        print("no map for channel %s" % args.channel)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
