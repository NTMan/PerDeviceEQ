#!/usr/bin/env python3
"""Walk the OUTPUT level and watch what distortion does.

The input ladder had one enemy: below the knee the converter's floor
swallowed the signal, and above it nothing got worse. The output level
has two. Too quiet and the take is written in noise; too loud and the
transducer bends. So the answer is not a point but a BAND, and its top
edge is where distortion stops being flat and starts climbing.

This does not decide where that is. It plays real sweeps at a ladder
of output levels and prints what came back, so the shape of the curve
can be looked at before any rule is written about it. That order is
deliberate: the input ladder only started working when we stopped
looking for a threshold and started reading the shape.

    tools/level_probe.py --sink "Liberty" --mic CM106 --column 0

Per rung it prints the recorded level, the SNR, and THD in three
bands, because a single number at 1 kHz is not the ceiling -- a
transducer bends in the bass first, and the band that bends decides
the ceiling for the whole take.

Nothing is left behind: the output level is restored on the way out,
however the walk ends.
"""

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                      # noqa: E402

from perdeviceeq import knee, pw_backend                # noqa: E402
from perdeviceeq import measure_session as ms           # noqa: E402

BANDS = ((20.0, 200.0, "20-200"), (200.0, 2000.0, "200-2k"),
         (2000.0, 10000.0, "2k-10k"))


def band_thd_pct(take, lo, hi):
    """Worst THD in a band, as a percentage of the fundamental.

    WORST, not average: a transducer that bends only at 40 Hz is bent,
    and a mean over the band would hide it behind the clean decades
    on either side.
    """
    f = np.asarray(take.freq_hz, dtype=float)
    thd = np.asarray(take.thd_db, dtype=float)
    mag = np.asarray(take.mag_db, dtype=float)
    m = (f >= lo) & (f <= hi) & np.isfinite(thd) & np.isfinite(mag)
    if not m.any():
        return None
    rel = thd[m] - mag[m]              # harmonics against the fundamental
    return 100.0 * (10.0 ** (float(np.max(rel)) / 20.0))


def find_sink(needle):
    for s in pw_backend.list_sinks():
        if (needle.lower() in s["name"].lower()
                or needle.lower() in s.get("desc", "").lower()):
            return s
    return None


def find_source(needle):
    for s in pw_backend.list_sources():
        if (needle.lower() in s["name"].lower()
                or needle.lower() in s.get("desc", "").lower()):
            return s
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sink", required=True,
                    help="substring of the output's name or description")
    ap.add_argument("--mic", required=True,
                    help="substring of the microphone's name or description")
    ap.add_argument("--column", type=int, default=0,
                    help="capture column the microphone arrives on")
    ap.add_argument("--lo", type=float, default=20.0,
                    help="lowest output level, per cent")
    ap.add_argument("--hi", type=float, default=100.0,
                    help="highest output level, per cent")
    ap.add_argument("--steps", type=int, default=8)
    a = ap.parse_args()

    sink = find_sink(a.sink)
    if sink is None:
        print("no output matches %r" % a.sink)
        return 1
    src = find_source(a.mic)
    if src is None:
        print("no microphone matches %r" % a.mic)
        return 1
    print("output : %s" % sink["name"])
    print("mic    : %s  column %d" % (src["name"], a.column))

    before = (sink.get("volume") or (None, None))[0]
    sink_id = sink["id"]
    print("level  : %s"
          % ("%.0f%%" % (before * 100.0) if before else "unknown"))
    print("\nPLAYING SWEEPS. Keep the room quiet and do not move the rig.\n")

    cfg = ms.SessionConfig(
        sink=sink["name"], source=pw_backend.entry_node(src["name"]),
        channels=pw_backend.source_width(src), auto_level=False,
        mute_others=True, device=sink.get("desc"),
        start_volume=None)

    rungs = []
    print("  %-7s %-9s %-7s %s" % ("level", "peak", "SNR", "worst THD %"))
    print("  %-7s %-9s %-7s %s"
          % ("", "dBFS", "dB", "  ".join(n for _, _, n in BANDS)))
    try:
        with ms.MeasureSession(cfg) as s:
            for pct in knee.plan(a.lo, a.hi, a.steps):
                s.set_level(pct / 100.0)
                out = s.take(0, analyze=a.column)
                if out.kind != "take":
                    # auto_level is off, so nothing should move the
                    # level under us; say so rather than silently
                    # recording a rung that is not what was asked for
                    print("  %-7.0f  refused: %s" % (pct, out.kind))
                    continue
                t = out.take
                thds = [band_thd_pct(t, lo, hi) for lo, hi, _ in BANDS]
                rungs.append((pct, t, thds))
                print("  %-7.0f %-9.1f %-7.1f %s"
                      % (pct, t.peak_dbfs, t.snr_db,
                         "  ".join("%7s" % ("%.3f" % v if v is not None
                                            else "-") for v in thds)))
    except KeyboardInterrupt:
        print("\nstopped.")
    except Exception as e:                              # noqa: BLE001
        print("\n%s: %s" % (type(e).__name__, e))
    finally:
        if before:
            try:
                pw_backend.set_sink_volume(sink_id, before)
                print("\nrestored the level to %.0f%%" % (before * 100.0))
            except Exception as e:                      # noqa: BLE001
                print("\nCOULD NOT RESTORE the level: %s" % e)

    if len(rungs) >= 3:
        print("\n  the bass band, level against distortion:")
        for pct, t, thds in rungs:
            v = thds[0]
            bar = "#" * int(max(0, min(40, 8 + 8 * math.log10(v))))\
                if v else ""
            print("    %3.0f%%  %8s  %s"
                  % (pct, "%.3f" % v if v else "-", bar))
    return 0


if __name__ == "__main__":
    sys.exit(main())
