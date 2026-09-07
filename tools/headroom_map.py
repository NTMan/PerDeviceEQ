#!/usr/bin/env python3
"""Walk UP from where the volume already stands and draw what the rig
stops delivering, three ways, so a shape can be chosen before anyone
draws it in a window.

    tools/headroom_map.py --sink "Playback 1/2" --mic "IN 1" --play FL

WHY IT IS SEPARATE FROM THE SEARCH. The two want opposite things. A
search wants ONE number -- a level safe enough to measure at -- and
stops the moment it has it. A map's output is a CURVE ALONG THE KNOB,
every rung is a point of it, and the rungs above a ceiling are the
interesting ones because that is where the loss grows. Bolting the map
onto the end of the search made it inherit the search's bold step and
its habit of stopping early: his JBL Tour Pro 3 came back with rungs
at 80% and 93% and nothing between, while the change he can hear sits
at 84-85%.

So this starts wherever the sink's volume is NOW -- the level the
listener actually uses -- and climbs from there in the finest step it
can read, until the capture peak or the rung count stops it.

WHAT IT DRAWS. The same data three ways, because the shape of the
answer is not yet settled:

  * a FAMILY OF CURVES, one per rung, frequency across and loss down
    -- the geometry of the equal-loudness contours, which reads
    without a legend: where a line dips, that rung is not delivering
  * a FIELD, frequency across and level down, shaded by loss -- all of
    it at once, at the price of reading colour instead of height
  * a TABLE of the numbers behind both

THE STEP IS TWO DECIBELS, measured rather than chosen: two sweeps of
one rig at one level disagree by about two tenths of a decibel in the
midrange, so a 2 dB step is read with room to spare and a 1 dB step is
not.

AND THE STEP IS SIZED BY WHAT ARRIVES, not by the knob. A bluez sink
answers AVRCP's own 128-step scale and can deliver 8 dB when asked for
4; the capture peak follows the level one for one and is the honest
witness of what the rig was actually given.

BEFORE RUNNING IT: sweeps play at and ABOVE the current volume. Set
the volume where you listen, and bring the card's own analogue level
to where you keep it -- a map belongs to a chain, and changing the
chain afterwards makes it a map of a machine that no longer exists.

Runs from any directory. The volume is left where the walk stopped.
"""

import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from perdeviceeq import level_run                            # noqa: E402
from perdeviceeq import measure_core as mc                   # noqa: E402
from perdeviceeq import pw_backend                           # noqa: E402


def find(items, needle):
    n = (needle or "").lower()
    for it in items:
        if n in (it.get("name") or "").lower():
            return it
    for it in items:
        if n in (it.get("desc") or it.get("description") or "").lower():
            return it
    return None


def current_volume(sink):
    """Where the knob stands, from the listing we already fetched.

    NOT from the backend's cache: that is filled by the poller the
    application runs, and in a tool's own process it is empty -- the
    first cut asked it and got "cannot read where the volume stands"
    every time. list_sinks() carries the node's gain because a window
    that asks separately pays a pw-dump, and the same field serves
    here for nothing.
    """
    g = sink.get("gain")
    return g[0] if isinstance(g, (tuple, list)) else g


def losses(rungs, freqs, ppo):
    """Loss per rung against the quietest one, in dB, NaN where the
    rung was not heard."""
    base = rungs[0]
    bm = np.array([np.nan if x is None else float(x)
                   for x in base["mag_db"]], float)
    out = []
    for r in rungs[1:]:
        rise = level_run.asked_db((base["level"], base["peak_dbfs"]),
                                  (r["level"], r["peak_dbfs"]))
        cm = np.array([np.nan if x is None else float(x)
                       for x in r["mag_db"]], float)
        off = r.get("heard_offset_db")
        if off is None:
            out.append((r, rise, np.full(len(cm), np.nan)))
            continue
        with np.errstate(all="ignore"):
            d = level_run.shortfall_db(bm, cm, cm - off, rise,
                                       freqs, ppo)
        out.append((r, rise, d))
    return out


BARS = (20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400,
        500, 800, 1250, 2000, 3150, 5000, 8000, 12500)


def at(freqs, curve, f):
    v = float(np.interp(np.log(f), np.log(freqs), curve))
    return v if np.isfinite(v) else None


def draw_family(rows, freqs, height=14, floor=None):
    """One line per rung, frequency across, loss DOWNWARD.

    The geometry of the equal-loudness contours, and it reads the same
    way: a line that stays at the top is a rung the rig delivered, and
    a line that dips is a rung it did not. Where the lines fan apart
    evenly the loss grows smoothly; where three sit together and one
    drops away, something switched.
    """
    # THE SCALE FOLLOWS THE DATA. A fixed floor flattens every rung
    # past it into one row, which is exactly the difference this view
    # exists to show.
    if floor is None:
        deep = [at(freqs, d, f) for _r, _s, d in rows for f in BARS]
        deep = [v for v in deep if v is not None]
        floor = max(2.0, math.ceil(max(deep or [2.0])))
    print("\n  LOSS BY RUNG -- 0 dB at the top, %.0f dB at the bottom"
          % floor)
    W = len(BARS) * 3
    grid = [[" "] * W for _ in range(height)]
    marks = "0123456789ABCDEF"
    for k, (r, _rise, d) in enumerate(rows):
        ch = marks[k % len(marks)]
        for i, f in enumerate(BARS):
            v = at(freqs, d, f)
            if v is None:
                continue
            y = int(round(min(1.0, max(0.0, v / floor)) * (height - 1)))
            x = i * 3 + 1
            # AND MARKS DO NOT EAT EACH OTHER. Rungs that agree land on
            # one cell, and whichever was drawn last would otherwise be
            # the only one seen -- so a shared cell says "several".
            grid[y][x] = ch if grid[y][x] == " " else "*"
    for y, line in enumerate(grid):
        lab = ""
        if y == 0:
            lab = "0 dB"
        elif y == height - 1:
            lab = "%.0f" % floor
        print("  %5s |%s" % (lab, "".join(line)))
    print("  %5s +%s" % ("", "-" * W))
    print("  %5s  %s" % ("", "".join(
        ("%-3s" % ("%dk" % (f // 1000) if f >= 1000 else str(f)))[:3]
        for f in BARS)))
    print("\n  " + "   ".join(
        "%s = %.0f%%" % (marks[k % len(marks)], 100 * r["level"])
        for k, (r, _s, _d) in enumerate(rows)))


def draw_field(rows, freqs):
    """Frequency across, level DOWN the page, shaded by loss."""
    shade = " .:-=+*#%@"
    deep = [at(freqs, d, f) for _r, _s, d in rows for f in BARS]
    deep = [v for v in deep if v is not None]
    _floor = max(2.0, math.ceil(max(deep or [2.0])))
    print("\n  THE SAME AS A FIELD -- darker is more missing, "
          "full at %.0f dB" % _floor)
    print("  %-7s %s" % ("level", "".join(
        ("%-3s" % ("%dk" % (f // 1000) if f >= 1000 else str(f)))[:3]
        for f in BARS)))
    for r, _rise, d in rows:
        row = ""
        for f in BARS:
            v = at(freqs, d, f)
            t = 0.0 if v is None else min(1.0, max(0.0, v / _floor))
            row += shade[min(len(shade) - 1,
                             int(t * (len(shade) - 0.01)))] * 3
        print("  %-7s %s" % ("%.0f%%" % (100 * r["level"]), row))


def draw_table(rows, freqs):
    pts = (20, 25, 32, 40, 50, 63, 80, 100, 200, 1000)
    print("\n  AND THE NUMBERS -- dB of output missing")
    print("  %-7s %-9s %s" % ("level", "arrived",
                              "  ".join("%6d" % p for p in pts)))
    for r, rise, d in rows:
        cells = []
        for p in pts:
            v = at(freqs, d, p)
            cells.append("    --" if v is None else "%6.1f" % v)
        print("  %-7s %-9.1f %s"
              % ("%.0f%%" % (100 * r["level"]), rise, "  ".join(cells)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sink", required=True)
    ap.add_argument("--mic", required=True)
    ap.add_argument("--column", type=int, default=None,
                    help="which capture column to read; required when "
                         "the source has more than one")
    ap.add_argument("--play", metavar="POS",
                    help="channel position to play into, e.g. FL. "
                         "PipeWire routes a mono sweep by NAME, so this "
                         "sounds ONE speaker and the microphone hears "
                         "one source")
    ap.add_argument("--start", type=float, default=None,
                    help="cubic volume of the FIRST rung; by default a "
                         "few steps BELOW where the volume stands, so "
                         "the walk climbs through the level you listen "
                         "at instead of starting on top of it")
    ap.add_argument("--below", type=float, default=6.0,
                    help="how far below the current volume to begin, "
                         "in dB (default 6.0). The quiet rungs are the "
                         "reference every louder one is read against, "
                         "so there must be room for them")
    ap.add_argument("--step-db", type=float,
                    default=level_run.MAP_STEP_DB,
                    help="how much louder each rung asks for "
                         "(default %.1f, the finest step two sweeps "
                         "can be told apart by)" % level_run.MAP_STEP_DB)
    ap.add_argument("--rungs", type=int, default=level_run.MAP_MAX_RUNGS)
    ap.add_argument("--keep", metavar="DIR",
                    help="write each rung's capture into DIR, with a "
                         "sidecar naming the level and the sweep. A "
                         "hand on a slider is not a step and a walk "
                         "disturbs a room for a minute; the questions "
                         "asked of one evening's rungs afterwards are "
                         "not all known while it runs")
    ap.add_argument("--stop-peak", type=float,
                    default=level_run.AUTO_PEAK_CEIL,
                    help="stop before a capture peak above this")
    a = ap.parse_args()

    sink = find(pw_backend.list_sinks(), a.sink)
    src = find(pw_backend.list_sources(), a.mic)
    if sink is None or src is None:
        print("no %s matches" % ("output" if sink is None else "mic"))
        return 1
    width = pw_backend.source_width(src)
    column = a.column
    if column is None:
        if width > 1:
            print("%s has %d capture columns; say which one the coupler "
                  "is on with --column 0..%d"
                  % (src["name"], width, width - 1))
            return 1
        column = 0
    if column >= width:
        print("that source has %d capture channels" % width)
        return 1

    start = a.start
    if start is None:
        # THE WALK CLIMBS THROUGH WHERE YOU LISTEN, not up from it.
        # Starting at the current volume gives a map with no reference
        # and often nowhere to go: his JBL sat at 94%, which left less
        # than one readable step under the capture ceiling, and the
        # walk stopped after a single rung with nothing to compare.
        # The quiet rungs are what every louder one is read against.
        here = current_volume(sink)
        if here is None:
            print("cannot read where %s's volume stands; give --start"
                  % sink["name"])
            return 1
        start = max(0.02, here * 10.0 ** (-abs(a.below) / 60.0))
        print("volume : %.0f%% now, so the walk begins %.0f dB below "
              "it at %.0f%%" % (100 * here, a.below, 100 * start))
    print("output : %s" % sink["name"])
    print("mic    : %s  column %d of %d" % (src["name"], column, width))
    if a.play:
        print("playing: %s only" % a.play)
    print("map    : from %.0f%% upward, %.1f dB a rung, up to %d rungs, "
          "stopping at a peak above %.1f dBFS"
          % (100 * start, a.step_db, a.rungs, a.stop_peak))
    print("\nSWEEPS WILL PLAY, AT AND ABOVE THE CURRENT VOLUME.\n")

    freqs = np.asarray(mc.log_grid())
    said = []

    def note(v, i):
        said.append(v)
        print("  rung %d at %.0f%% ..." % (i, 100 * v))

    def keep(index, v, chan, sweep):
        if not a.keep:
            return
        import soundfile as sf
        os.makedirs(a.keep, exist_ok=True)
        sf.write(os.path.join(a.keep, "rung%02d.wav" % index),
                 np.asarray(chan), sweep.fs)
        with open(os.path.join(a.keep, "rung%02d.json" % index),
                  "w") as fh:
            json.dump({"index": index, "volume_cubic": float(v),
                       "n_samples": sweep.n_samples, "fs": sweep.fs,
                       "f_start": sweep.f_start, "f_end": sweep.f_end,
                       "column": 0}, fh, indent=1)

    try:
        rungs = level_run.headroom_map(
            sink, {"name": pw_backend.entry_node(src["name"]),
                   "id": src["id"]},
            width, start, sink_name=sink["name"], analyze=column,
            freqs=freqs, play_map=a.play, on_level=note,
            on_rung=keep,
            stop_peak_dbfs=a.stop_peak, step_db=a.step_db,
            max_rungs=a.rungs)
    except (RuntimeError, ValueError) as exc:
        print("%s" % exc)
        return 1
    except KeyboardInterrupt:
        print("\nstopped.")
        return 1

    if len(rungs) < 2:
        print("\n  only one rung was played -- nothing to compare")
        return 1
    rows = losses(rungs, freqs, mc.GRID_PPO)
    draw_family(rows, freqs)
    draw_field(rows, freqs)
    draw_table(rows, freqs)
    print("\n  every rung is read against the quietest one, so the room "
          "\n  cancels: what is left is what the rig stopped delivering.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
