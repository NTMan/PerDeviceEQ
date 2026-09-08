#!/usr/bin/env python3
"""Play every rung of a ladder SEVERAL TIMES and keep each sweep, so
the disagreement between them can be modelled instead of guessed.

    tools/scatter_probe.py --sink "Speakers" --mic UMIK --play FL \\
        --out ~/scatter-room.json
    tools/scatter_probe.py --sink ... --mic ... --repeats 3 --rungs 7

WHAT THE QUESTION IS. A map's readings are gated by how far a rung
stood above the noise, and on a coupler that gate is far too strict:
measured across four channels of two rigs, the error of a rung-to-rung
difference does not grow at all down to an SNR of minus fifteen
decibels -- median 0.03 dB, spread 0.1 dB, the same as at plus twenty.
A swept-sine deconvolution concentrates the sweep and spreads
uncorrelated noise, so a stationary hiss does not reach the answer.

A ROOM IS NOT STATIONARY, and that is where the gate earns its keep.
On a walk with a UMIK-2 in a room, three sweeps at ONE level disagreed
by 20.5 dB in the bins where the SNR was below zero, while the same
three on a coupler never left half a decibel. Rumble, traffic and a
lift are not rejected by the deconvolution, and a log sweep dwells
longest exactly where they live.

SO THE SCATTER IS THE HONEST GATE -- it measures what actually harms a
reading, and needs no constant chosen per rig. The open question is
what it COSTS. If the scatter of a rung follows from its SNR plus a
floor, one measured rung calibrates a whole ladder and a walk keeps
its single extra sweep. Existing data says most of that: from an SNR
of zero to twelve the two channels of one room walk and the additive
noise model agree within half an octave of each other. It also says
the model is not the whole story -- at minus four decibels the two
channels of the SAME walk read 9.70 and 3.69, two and a half times
apart, and above forty-five both stop falling at about a quarter of a
decibel where the model predicts a fiftieth. There is a floor, and it
is not noise.

Ten bins a cell is not enough to settle either. This tool collects
enough to settle both.

WHAT IT WRITES. Every sweep's magnitude, whole, and the noise floor
the analyser measured for that same sweep -- so a floor that moved
between the first rung and the last shows up as itself, and a scatter
that grew is not mistaken for the level when it was the evening. NOT a
computed scatter: gathering costs an evening and disturbs a room, the
model is not chosen yet, and a curve thrown away cannot be got back
without playing the sweeps again.

BEFORE RUNNING IT: sweeps play at and above the starting level. This
is a calibration, not a measurement -- it answers how many sweeps a
rung needs, once, and is not meant to be run again.

Runs from any directory. The volume is left where the walk stopped.
"""

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from perdeviceeq import level_run                            # noqa: E402
from perdeviceeq import measure_core as mc                   # noqa: E402
from perdeviceeq import pw_backend                           # noqa: E402
from perdeviceeq.sweep_io import write_sweep_files           # noqa: E402


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
    """Where the knob stands, from the listing already fetched: the
    backend's cache is filled by the application's poller and is empty
    in a tool's own process."""
    g = sink.get("gain")
    return g[0] if isinstance(g, (tuple, list)) else g


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sink", required=True)
    ap.add_argument("--mic", required=True)
    ap.add_argument("--column", type=int, default=None,
                    help="which capture column to read; required when "
                         "the source has more than one")
    ap.add_argument("--play", metavar="POS",
                    help="channel position to play into, e.g. FL")
    ap.add_argument("--out", required=True,
                    help="where to write the sweeps, as JSON")
    ap.add_argument("--repeats", type=int, default=2,
                    help="sweeps per rung (default 2). Two answer "
                         "whether a rung agrees with itself; three "
                         "say how the disagreement is distributed")
    ap.add_argument("--rungs", type=int, default=7,
                    help="how many levels (default 7). The point is "
                         "COVERAGE IN SNR, not the rig's ceiling: from "
                         "quiet enough that a room is already winning "
                         "to loud enough that the floor shows")
    ap.add_argument("--step-db", type=float, default=6.0,
                    help="dB between rungs (default 6.0, wider than a "
                         "map's 2 dB because this walks the SNR axis "
                         "rather than looking for where a rig gives "
                         "way)")
    ap.add_argument("--below", type=float, default=18.0,
                    help="how far below the current volume to begin, "
                         "in dB (default 18)")
    ap.add_argument("--start", type=float, default=None,
                    help="cubic volume of the first rung, overriding "
                         "--below")
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
            print("%s has %d capture columns; say which one with "
                  "--column 0..%d" % (src["name"], width, width - 1))
            return 1
        column = 0
    if column >= width:
        print("that source has %d capture channels" % width)
        return 1

    start = a.start
    if start is None:
        here = current_volume(sink)
        if here is None:
            print("cannot read where %s's volume stands; give --start"
                  % sink["name"])
            return 1
        start = max(0.02, here * 10.0 ** (-abs(a.below) / 60.0))
        print("volume : %.0f%% now, so the walk begins %.0f dB below "
              "it at %.0f%%" % (100 * here, a.below, 100 * start))

    sweep = mc.default_sweep()
    freqs = np.asarray(mc.log_grid())
    pre, post = mc.DEFAULT_PRE_SILENCE, mc.DEFAULT_POST_SILENCE
    duration = pre + sweep.duration_s + post
    name = sink["name"]
    source = {"name": pw_backend.entry_node(src["name"]), "id": src["id"]}
    outdir = os.path.join(os.path.dirname(os.path.abspath(a.out)) or ".",
                          ".pdeq-scatter-sweep")
    os.makedirs(outdir, exist_ok=True)
    wav = write_sweep_files(outdir, sweep, pre, post)
    back = pw_backend.backend()

    print("output : %s" % name)
    print("mic    : %s  column %d of %d" % (src["name"], column, width))
    if a.play:
        print("playing: %s only" % a.play)
    print("ladder : %d rungs from %.0f%%, %.1f dB apart, %d sweeps each"
          % (a.rungs, 100 * start, a.step_db, a.repeats))
    print("       : %d sweeps in all, about %.0f seconds of sound"
          % (a.rungs * a.repeats, a.rungs * a.repeats * duration))
    print("\nSWEEPS WILL PLAY, AT AND ABOVE %.0f%%.\n" % (100 * start))

    out = {"kind": "scatter-probe", "version": 1,
           "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "sink": name, "source": source["name"], "column": column,
           "play": a.play, "step_db": a.step_db, "repeats": a.repeats,
           "grid": {"f_lo": mc.GRID_F_LO, "f_hi": mc.GRID_F_HI,
                    "ppo": mc.GRID_PPO},
           "rungs": []}

    v = level_run._clamp(start)
    try:
        for i in range(a.rungs):
            sweeps = []
            for k in range(a.repeats):
                print("  rung %d at %.0f%%, sweep %d of %d ..."
                      % (i, 100 * v, k + 1, a.repeats))
                chan, peak_db, clipped, got = level_run._play_rung(
                    back, name, sink, source, wav, duration, width,
                    sweep, freqs, column, v, a.play)
                sweeps.append({
                    "peak_dbfs": round(peak_db, 2),
                    "clipped": bool(clipped),
                    "noise_dbfs": (round(float(got.noise_dbfs), 2)
                                   if got.noise_dbfs is not None else None),
                    "mag_db": [None if not np.isfinite(x)
                               else round(float(x), 3)
                               for x in got.mag_db]})
                if clipped:
                    print("    clipped; the ladder stops here")
            out["rungs"].append({"index": i, "level": round(float(v), 6),
                                 "sweeps": sweeps})
            if any(s["clipped"] for s in sweeps):
                break
            if max(s["peak_dbfs"] for s in sweeps) > a.stop_peak:
                print("  the capture is near full scale; the ladder "
                      "stops here")
                break
            nv = level_run._clamp(v * 10.0 ** (a.step_db / 60.0))
            if nv <= v:
                print("  the knob is at its top")
                break
            v = nv
    except KeyboardInterrupt:
        print("\nstopped -- what was played is written anyway.")

    with open(a.out, "w") as fh:
        json.dump(out, fh)
    n = sum(len(r["sweeps"]) for r in out["rungs"])
    print("\n  %d sweeps over %d rungs -> %s"
          % (n, len(out["rungs"]), a.out))
    print("  every sweep is kept whole; nothing is reduced to a "
          "scatter here,\n  because the model that reads them is not "
          "chosen yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
