#!/usr/bin/env python3
"""Walk the playback level a decibel at a time and report what each
rung reads, with the hunt's own brake on the way up.

    tools/level_ladder.py --sink "Playback 1/2" --mic "IN 1"

WHY IT EXISTS, and why it is not a second hunt. The search steers by a
yes/no -- is the distortion figure a measurement or a bound -- and
stops at the first rung that clears it. On a very quiet chain that
happens at the first probe, so the level it picks carries the smallest
margin the bar allows. Whether one more decibel would cost anything is
a question about the SLOPE of the device's own distortion curve, and
nobody has measured that slope on a chain this clean. This tool
measures it: stated rungs, one take each, no decision at the end.

It decides exactly two things, and neither is invented here.

WHEN TO STOP CLIMBING. `AutoLevel.verdict` says "loud" and the climb
ends, the same sentence the search obeys, plus a peak ceiling of its
own that is reached earlier. **Nothing is ever raised without asking
the previous rung first.**

WHEN TO REFUSE TO CLIMB AT ALL. Playing a decibel louder must arrive
as about a decibel louder. If it does not, the microphone is not in
the path being measured, and every further rung is a sweep into
nothing. This is not a hypothetical: a whole evening's ladders were
run with the coupler plugged into a different card, and the eight
rungs came back with distortion figures between six and a hundred and
seventy per cent. The tool said "channel 0 of 2" in small print and
went on measuring the room.

AND THE THING THAT FOLLOWS IS THE RESPONSE, NOT THE PEAK. His CM106
carries a DC offset some thirty-six decibels above its own noise, so
the peak sample belongs to that offset and not to the sweep: seven
decibels of level went by with the peak parked at -39, on a chain
whose microphone had just been confirmed by knocking on the capsule.
The sweep's own level is the response the deconvolution recovers,
which cannot contain a constant, and that is what is watched here.
The peak stays in the table, because it is what the search's brake
reads and what clipping shows up in.

BEFORE RUNNING IT: the sweep plays into whatever is on the coupler.
Bring the card's own analogue output level down first, and start below
where you listen -- the first rung is played at `--start` and nothing
is climbed until its peak has been read.

Runs from any directory. The level is left where the ladder stopped,
as with every walk in this project.
"""

import argparse
import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import measure_build as mb              # noqa: E402
from perdeviceeq import measure_core as mc               # noqa: E402
from perdeviceeq import level_run                        # noqa: E402
from perdeviceeq import pw_backend                       # noqa: E402
from perdeviceeq.sweep_io import run_take, write_sweep_files  # noqa: E402

# a ceiling of this tool's own, reached well before the search's -2.0:
# the question here lives between the crossing and the peak floor, and
# there is nothing to learn above it worth the risk to an earphone
STOP_PEAK_DBFS = -6.0

# How much of a level rise must arrive at the capture before the walk
# is believed, as a least-squares slope of peak against level. Half is
# generous -- a chain in the path tracks nearly one for one -- and it
# leaves room for a device whose own compression eats some of the rise.
#
# THE FIRST RUNG IS DROPPED FROM THE FIT: a chain wakes up on it. His
# bluetooth walk jumped ten decibels from the first rung to the second
# and then stood still for six more, and a fit including that jump
# reads +3.1 and walks on happily. Dropping it, the same walk reads
# +0.05 and stops, while both good wired walks read exactly +1.00
# either way.
TRACK_MIN = 0.5
TRACK_AFTER = 5         # rungs to collect; the fit uses the last four


def find(entries, needle):
    n = needle.lower()
    return next((e for e in entries
                 if n in e["name"].lower()
                 or n in (e.get("desc") or "").lower()), None)


def rung(sink, src, name, wav, duration, width, fs, sweep, freqs,
         column, v):
    """One sweep at volume `v`, read the way the session reads it."""
    back = pw_backend.backend()
    back.moratorium_begin(name, v, mute_others=True)
    try:
        data, _ = run_take(sink, src, wav, duration, width, fs,
                           verify=False)
    finally:
        back.moratorium_end()
    chan = np.asarray(data)[:, min(column, data.shape[1] - 1)]
    peak = float(np.max(np.abs(chan))) if chan.size else 0.0
    peak_db = 20.0 * math.log10(peak) if peak > 0 else -120.0
    clipped = peak >= 0.999
    got = mc.analyze_take(chan, sweep, freqs)
    # the sweep's own level: the median of the recovered response
    # across the band the coupler can be trusted in. A constant
    # offset cannot survive the deconvolution, so this follows the
    # level where the peak sample does not.
    band = (np.asarray(freqs) >= 100.0) & (np.asarray(freqs) <= 8000.0)
    mag = np.asarray(got.mag_db, float)[band]
    mag = mag[np.isfinite(mag)]
    resp = float(np.median(mag)) if mag.size else None
    at = mb.thd_at(freqs, got.thd_db, got.thd_noise_db)
    margin = mb.thd_margin_db(freqs, got.thd_db, got.thd_noise_db)
    snr = (float(got.snr_db) if got.snr_db is not None
           and math.isfinite(float(got.snr_db)) else None)
    return peak_db, resp, snr, clipped, at, margin


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sink", required=True)
    ap.add_argument("--mic", required=True)
    ap.add_argument("--column", type=int, default=None,
                    help="which capture column to read; required when "
                         "the source has more than one")
    ap.add_argument("--start", type=float, default=0.15,
                    help="cubic volume of the FIRST rung (default 0.15,"
                         " the search's own start)")
    ap.add_argument("--step-db", type=float, default=1.0)
    ap.add_argument("--rungs", type=int, default=8)
    ap.add_argument("--stop-peak", type=float, default=STOP_PEAK_DBFS,
                    help="stop climbing once a rung reads above this "
                         "peak, in dBFS (default %.1f)" % STOP_PEAK_DBFS)
    a = ap.parse_args()

    sink = find(pw_backend.list_sinks(), a.sink)
    src = find(pw_backend.list_sources(), a.mic)
    if sink is None or src is None:
        print("no %s matches" % ("output" if sink is None else "mic"))
        return 1
    width = pw_backend.source_width(src)

    # NOT GUESSED. A stereo source has two columns and the coupler is
    # on one of them; taking the first silently is how a walk ends up
    # measuring an empty input.
    column = a.column
    if column is None:
        if width > 1:
            print("%s has %d capture columns; say which one the "
                  "coupler is on with --column 0..%d"
                  % (src["name"], width, width - 1))
            print("tools/input_census.py shows which one moves when "
                  "you knock on the capsule")
            return 1
        column = 0
    if column >= width:
        print("that source has %d capture channels" % width)
        return 1

    sweep = mc.generate_sweep(262144, 48000, 20.0, 20000.0)
    freqs = mc.log_grid()
    outdir = tempfile.mkdtemp(prefix="pdeq-ladder-")
    wav = write_sweep_files(outdir, sweep, 1.0, 0.5)
    duration = 1.0 + sweep.duration_s + 0.5
    source = {"name": pw_backend.entry_node(src["name"]),
              "id": src["id"]}

    print("output : %s" % sink["name"])
    print("mic    : %s  column %d of %d" % (src["name"], column, width))
    print("ladder : %d rungs of %.1f dB from %.0f%%, stopping at a "
          "peak above %.1f dBFS\n"
          % (a.rungs, a.step_db, 100 * a.start, a.stop_peak))
    print("SWEEPS WILL PLAY. Bring the card's own output level down "
          "first.\n")
    print("  %-7s %-8s %-9s %-9s %-7s %-11s %-8s %s"
          % ("rung", "level", "peak", "response", "SNR", "THD@1k",
             "margin", "verdict"))

    rows, resps = [], []
    v = level_run._clamp(a.start)
    try:
        for i in range(1, a.rungs + 1):
            peak, resp, snr, clipped, at, margin = rung(
                sink, source, sink["name"], wav, duration, width,
                sweep.fs, sweep, freqs, column, v)
            said = level_run.AutoLevel.verdict(
                peak, snr, clipped, at[1] if at else None)
            thd = ("n/a" if at is None else
                   "%s%s%%" % ("<=" if at[1] else "",
                               mb.pct_word(at[0])))
            print("  %-7d %-8.0f%% %-9.1f %-9s %-7s %-11s %-8s %s"
                  % (i, 100 * v, peak,
                     "n/a" if resp is None else "%.1f" % resp,
                     "n/a" if snr is None else "%.1f" % snr, thd,
                     "n/a" if margin is None else "%+.1f dB" % margin,
                     said))
            if resp is not None:
                resps.append(resp)
            if at is not None and margin is not None and not at[1]:
                rows.append((peak, 20.0 * math.log10(at[0] / 100.0)))

            # DOES THE CAPTURE FOLLOW THE LEVEL? If not, the mic is
            # not in this path and the remaining rungs are sweeps
            # into nothing.
            if len(resps) == TRACK_AFTER:
                y = np.array(resps[1:])
                x = np.arange(y.size, dtype=float) * a.step_db
                got = float(np.polyfit(x, y, 1)[0])
                if got < TRACK_MIN:
                    print("\n  the capture followed the level at %.2f "
                          "dB per dB over rungs 2..%d -- stopping."
                          % (got, TRACK_AFTER))
                    print("  A capture in the path being measured "
                          "tracks the level nearly one for one, so "
                          "either")
                    print("  the microphone is not in this path (wrong "
                          "card, wrong column, or the")
                    print("  coupler is somewhere else), or the device "
                          "is limiting hard. Check the")
                    print("  first before suspecting the second.")
                    break

            if clipped or said == "loud":
                print("\n  the search's own brake said %s -- stopping"
                      % ("clipped" if clipped else "loud"))
                break
            if peak > a.stop_peak:
                print("\n  past the ladder's own ceiling (%.1f dBFS) "
                      "-- stopping" % a.stop_peak)
                break
            v = level_run._clamp(v * 10.0 ** (a.step_db / 60.0))
    except KeyboardInterrupt:
        print("\nstopped.")
    except (RuntimeError, ValueError) as exc:
        print("%s" % exc)
        return 1

    # the whole point: how much distortion a decibel of level buys,
    # counted only over rungs whose figure was a MEASUREMENT
    if len(rows) >= 2:
        x = np.array([r[0] for r in rows])
        y = np.array([r[1] for r in rows])
        k = float(np.polyfit(x, y, 1)[0])
        print("\n  slope over %d measured rungs: %.2f dB of distortion "
              "per dB of level" % (len(rows), k))
    else:
        print("\n  too few measured rungs to fit a slope")
    print("  the level is where the ladder stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
