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


def _word(got):
    """A percentage, marked when it is only a ceiling."""
    if got is None:
        return "-"
    pct, clamped = got
    return ("<=%.3f" % pct) if clamped else ("%.3f" % pct)


MIN_SNR_DB = 6.0      # below this the sweep did not arrive
FUND_OVER_NOISE = 20.0   # a THD figure needs a fundamental to be a
                         # fraction OF; without one it is a ratio to
                         # nothing and prints as nothing


def usable(take):
    """Did the sweep arrive at all? Returns None or a plain reason.

    Asked before any number is computed. The first field run of this
    tool printed distortion figures of ten to the fourteenth per cent
    -- a ratio to a fundamental that was not there -- and a peak that
    sat at -34.7 dBFS on EVERY level from a fifth to full, which is
    this card's own fixed spikes and not a sweep. The arithmetic was
    right; printing it at all was the fault.
    """
    if take.snr_db is None or not math.isfinite(float(take.snr_db)):
        return "no SNR: the analysis found no sweep in the recording"
    if float(take.snr_db) < MIN_SNR_DB:
        return ("SNR %.1f dB -- nothing recognisable arrived"
                % float(take.snr_db))
    return None


def band_thd_pct(take, lo, hi, floor_gap=3.0):
    """Distortion in a band as a percentage, and whether it is a
    CEILING rather than a measurement.

    `thd_db` is ALREADY in dB re the fundamental -- measure_build.thd_at
    turns it straight into a percentage -- and the first version of this
    subtracted the fundamental again. That added the fundamental's own
    level to every figure: thousands of per cent where the sweep was
    quiet, and about eight where the truth was a few tenths.

    MEDIAN over the band, following thd_at rather than my own instinct
    for the worst point. The reason is written there and the field
    taught it: one narrow interference tone that lands in the band
    would otherwise buy the whole number, and the drawn line, smoothed
    a twelfth of an octave, would disagree with the figure above it.

    Returns (percent, clamped) or None. Clamped means the reading sits
    within floor_gap of the measurement's OWN noise, so it is a bound
    the rig cannot see under -- exactly the "<=" the app already draws.
    """
    f = np.asarray(take.freq_hz, dtype=float)
    thd = np.array([np.nan if v is None else float(v)
                    for v in take.thd_db], dtype=float)
    if len(thd) != len(f):
        return None
    band = (f >= lo) & (f <= hi) & np.isfinite(thd)
    if not band.any():
        return None
    v = float(np.median(thd[band]))
    clamped = False
    nz = getattr(take, "thd_noise_db", None)
    if nz is not None and len(nz) == len(f):
        na = np.array([np.nan if x is None else float(x)
                       for x in nz], dtype=float)
        nok = band & np.isfinite(na)
        if nok.any() and v - float(np.median(na[nok])) < floor_gap:
            clamped = True
    return 100.0 * (10.0 ** (v / 20.0)), clamped


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
    print("  %-7s %-9s %-7s %s"
          % ("", "dBFS", "dB", "  ".join(n for _, _, n in BANDS)))
    try:
        with ms.MeasureSession(cfg) as s:
            # SAY WHAT WAS DONE ABOUT THE EQ, because the alternative
            # is the operator wondering. A session bypasses this
            # project's own profile on the way in and puts it back on
            # the way out -- but a correction living in the DEVICE, in
            # its maker's app, is beyond any of that, and the two look
            # identical from here unless the run says which it did.
            eq = getattr(s, "eq_state", None) or {}
            if eq.get("profile") is not None:
                print("  profile: %s -- BYPASSED for this run, restored "
                      "after" % (eq.get("profile_source") or "found"))
            else:
                print("  profile: none on this output")
            print("  anything the DEVICE applies to itself is outside "
                  "this and stays on.\n")
            print("  %-7s %-9s %-7s %s"
                  % ("level", "peak", "SNR", "worst THD %"))
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
                why = usable(t)
                if why is not None:
                    print("  %-7.0f  %s" % (pct, why))
                    if not rungs:
                        print("\n  STOPPING: the first level produced no "
                              "sweep, so nothing below it will either.")
                        print("  Check that the earphone is in the coupler "
                              "and playing, that the")
                        print("  output picked is the one it is on, and "
                              "that the capture channel")
                        print("  is the one the microphone arrives on.")
                        break
                    continue
                thds = [band_thd_pct(t, lo, hi) for lo, hi, _ in BANDS]
                rungs.append((pct, t, thds))
                print("  %-7.0f %-9.1f %-7.1f %s"
                      % (pct, t.peak_dbfs, t.snr_db,
                         "  ".join("%10s" % _word(v) for v in thds)))
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
            v = thds[0][0] if thds[0] else None
            if not v:
                print("    %3.0f%%  %8s" % (pct, "-"))
                continue
            # a decade of THD per eight columns, so 0.01% and 1% are
            # visibly different rather than both "full"
            bar = "#" * int(max(1, min(40, 24 + 8 * math.log10(v))))
            print("    %3.0f%%  %8.4f  %s" % (pct, v, bar))
    return 0


if __name__ == "__main__":
    sys.exit(main())
