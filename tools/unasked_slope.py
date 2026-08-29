#!/usr/bin/env python3
"""Does what a rig returns unasked FOLLOW the level, or sit still?

    tools/unasked_slope.py --sink "pcm2704" --mic Umik --column 0

WHY THIS EXISTS, and it is his objection rather than my idea. The
strip that grades a cabinet by how much it returns unasked needs marks
somewhere, and I set them twice from readings and once from a gap in
his collection -- which is calibrating an instrument to the person
holding it. He asked for the objective distinction instead.

There is one, and it does not need a threshold. `unasked_db` is
measured AGAINST WHAT WAS PLAYED. So if the residue is the room and
the microphone, it does not care what we play: raise the drive by a
decibel and the ratio falls by a decibel, a slope of -1. If the
cabinet is making the residue itself, it rises with the drive and the
slope is near zero or above. A whole decibel per decibel apart, and
noise cannot fake it, because noise does not know what we are playing.

THREE RUNGS, which is what the arithmetic asks for and no more. The
take-to-take spread of this quantity is about 3 dB, so three rungs
three or four decibels apart span nine to twelve and pin the slope to
about 0.2 -- five times finer than the gap it has to resolve. Two
rungs cannot tell a slope from the spread; a fourth adds little.

It is a PROBE. If the slope separates his rigs the way the physics
says it should, the marks can go and the measurement can carry a
slope instead. If it does not, we will know that before another
schema is spent on it.

Runs from any directory. The level is left where the walk stopped.
"""

import argparse
import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import measure_core as mc               # noqa: E402
from perdeviceeq import pw_backend                       # noqa: E402
from perdeviceeq.sweep_io import run_take, write_sweep_files  # noqa: E402


def find(entries, needle):
    n = needle.lower()
    return next((e for e in entries
                 if n in e["name"].lower()
                 or n in (e.get("desc") or "").lower()), None)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sink", required=True)
    ap.add_argument("--mic", required=True)
    ap.add_argument("--column", type=int, default=None)
    ap.add_argument("--start", type=float, default=None,
                    help="cubic volume of the first rung; default is "
                         "whatever the sink already stands at")
    ap.add_argument("--rungs", type=int, default=3)
    ap.add_argument("--step-db", type=float, default=3.5)
    ap.add_argument("--at", type=float, nargs="*",
                    default=[25, 32, 40, 50],
                    help="drive frequencies to report, in Hz")
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
            print("%s has %d capture columns; say which with --column"
                  % (src["name"], width))
            return 1
        column = 0

    back = pw_backend.backend()
    v0 = a.start or back.volume_of(sink["name"]) or 0.2
    sweep = mc.generate_sweep()
    freqs = np.asarray(mc.log_grid())
    outdir = tempfile.mkdtemp(prefix="pdeq-slope-")
    wav = write_sweep_files(outdir, sweep, 1.10, 0.5)
    duration = 1.10 + sweep.duration_s + 0.5
    source = {"name": pw_backend.entry_node(src["name"]),
              "id": src["id"]}

    print("output : %s" % sink["name"])
    print("mic    : %s  column %d of %d" % (src["name"], column, width))
    print("walk   : %d rungs of %.1f dB from %.0f%%\n"
          % (a.rungs, a.step_db, 100 * v0))
    print("SWEEPS WILL PLAY, and they climb. Bring the card's own")
    print("output down first, but not to zero.\n")
    head = "  %-8s %-9s %s" % ("level", "peak", "  ".join(
        "%8.0fHz" % h for h in a.at))
    print(head)

    peaks, rows = [], []
    v = v0
    for i in range(a.rungs):
        back.moratorium_begin(sink["name"], v, mute_others=True)
        try:
            data, _ = run_take(sink, source, wav, duration, width,
                               sweep.fs, verify=False)
        finally:
            back.moratorium_end()
        chan = np.asarray(data)[:, min(column, data.shape[1] - 1)]
        pk = float(np.max(np.abs(chan))) if chan.size else 0.0
        pk_db = 20.0 * math.log10(pk) if pk > 0 else -120.0
        got = mc.analyze_take(chan, sweep, freqs)
        arr = (None if got.unasked_db is None else
               np.asarray([np.nan if x is None else x
                           for x in got.unasked_db], float))
        cells = []
        vals = []
        for h in a.at:
            x = (float("nan") if arr is None else
                 float(np.interp(np.log(h), np.log(freqs), arr)))
            vals.append(x)
            cells.append("%10s" % ("--" if not np.isfinite(x)
                                   else "%+.1f" % x))
        print("  %-8.0f%% %-9.1f %s" % (100 * v, pk_db, "".join(cells)))
        peaks.append(pk_db)
        rows.append(vals)
        v = min(1.0, v * 10.0 ** (a.step_db / 60.0))

    print()
    if len(peaks) >= 3:
        P = np.asarray(peaks)
        R = np.asarray(rows)
        print("  slope of the unasked return against the CAPTURED peak:")
        for j, h in enumerate(a.at):
            col = R[:, j]
            ok = np.isfinite(col)
            if ok.sum() < 3:
                print("     %5.0f Hz   too few readings" % h)
                continue
            k = float(np.polyfit(P[ok], col[ok], 1)[0])
            says = ("the ROOM: it does not know what we played"
                    if k < -0.6 else
                    "the CABINET: it grows with the drive"
                    if k > -0.3 else "undecided")
            print("     %5.0f Hz   %+.2f dB per dB   %s" % (h, k, says))
        print()
        print("  -1 is a residue that ignores the drive entirely, 0 is")
        print("  one that follows it exactly. Noise cannot sit near 0,")
        print("  because noise does not know what we are playing.")
    print("  the level is where the walk stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
