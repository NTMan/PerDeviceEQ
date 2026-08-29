#!/usr/bin/env python3
"""Measure a vent's own noise: what comes back that is NOT a multiple
of the tone that provoked it.

    tools/vent_probe.py --sink "Speaker" --mic Umik --column 0

WHY, and how it was arrived at. A day of measurement could not tell
his Adam D3V from his iLoud Micro Monitor, because every quantity the
app has is about HARMONICS -- and harmonics are the cone's business,
not the port's. At matched level the Adams show MORE of them. He kept
saying the tools were looking for the wrong thing, and he was right.

The control that settled it was his: the same 50 Hz tone recorded
twice, once with the vent open and once with a rag stuffed in it.

    band, re the tone      open      plugged
    400-800 Hz            -29.6      -45.2     the vent's own noise
    third harmonic        -29.1       -4.2     the CONE, freed of load

Plugging the port removed 15.6 dB from a NON-HARMONIC hump at 400-800
Hz while the harmonics went UP by as much as twenty-five decibels,
because an unloaded cone travels further. No figure that adds
everything together can separate those two, which is why none of them
did.

So this probe measures one thing: the energy that is not near any
multiple of the tone, in the decade where the hump lives. It plays a
tone rather than a sweep, because a sweep never dwells long enough for
a vent to reach its steady rush, and it walks the level, because
turbulence has a THRESHOLD -- it is absent and then it is present,
where a harmonic grows smoothly.

One more mark of the vent, from the same recording: the hump pulses at
the tone's OWN rate, not at twice it. Air leaving and returning would
stamp it twice per cycle; once per cycle means the two half-cycles are
not alike. The probe reports that ratio, because a hump that pulses at
twice the tone is a different animal from this one.

Runs from any directory. The level is left where the walk stopped.
"""

import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import measure_core as mc               # noqa: E402
from perdeviceeq import pw_backend                       # noqa: E402
from perdeviceeq.sweep_io import run_take                # noqa: E402

# where his vent's hump sat: eight to sixteen times the tone, well
# clear of the harmonics that matter and below the room's hiss
DEFAULT_BAND = (400.0, 800.0)
HARMONIC_HALFWIDTH_HZ = 4.0     # what counts as "on" a multiple
ORDERS = 200                    # multiples masked out


def find(entries, needle):
    n = needle.lower()
    return next((e for e in entries
                 if n in e["name"].lower()
                 or n in (e.get("desc") or "").lower()), None)


def tone_wav(path, f0, fs, seconds, level_dbfs):
    """A plain tone with a short fade at each end, written where
    run_take expects its material."""
    import soundfile as sf
    n = int(fs * seconds)
    t = np.arange(n) / float(fs)
    x = np.sin(2.0 * np.pi * f0 * t) * (10.0 ** (level_dbfs / 20.0))
    ramp = int(fs * 0.05)
    if ramp * 2 < n:
        w = np.hanning(2 * ramp)
        x[:ramp] *= w[:ramp]
        x[-ramp:] *= w[ramp:]
    sf.write(path, x.astype(np.float32), fs)
    return n / float(fs)


def measure(rec, fs, f0, band):
    """Split what came back into the tone's own family and the rest.

    Returns (tone_db, harm_db, vent_db, pulse_ratio_db) -- all but the
    first relative to the tone.
    """
    n = 1 << 17
    if len(rec) < n:
        n = 1 << int(math.floor(math.log2(max(len(rec), 1024))))
    w = np.hanning(n)
    acc = np.zeros(n // 2 + 1)
    k = 0
    for s in range(0, len(rec) - n, n // 2):
        acc += np.abs(np.fft.rfft(rec[s:s + n] * w)) ** 2
        k += 1
    if k == 0:
        return None
    acc /= k
    f = np.fft.rfftfreq(n, 1.0 / fs)
    i0 = int(np.argmin(np.abs(f - f0)))
    ref = float(acc[max(0, i0 - 3):i0 + 4].sum())
    if ref <= 0:
        return None

    mask = np.ones_like(acc, bool)
    for m in range(1, ORDERS + 1):
        fm = m * f0
        if fm > f[-1]:
            break
        lo = int(np.searchsorted(f, fm - HARMONIC_HALFWIDTH_HZ))
        hi = int(np.searchsorted(f, fm + HARMONIC_HALFWIDTH_HZ))
        mask[max(0, lo):hi + 1] = False

    inband = (f >= band[0]) & (f <= band[1])
    vent = float(acc[mask & inband].sum())
    harm = 0.0
    for m in (2, 3, 4, 5):
        j = int(np.argmin(np.abs(f - m * f0)))
        harm += float(acc[max(0, j - 3):j + 4].sum())

    # AND HOW IT PULSES. A vent that only lets go on one half-cycle
    # stamps its noise at the tone's own rate; air moving alike in
    # both directions would stamp it at twice.
    X = np.fft.rfft(rec)
    ff = np.fft.rfftfreq(len(rec), 1.0 / fs)
    X[(ff < band[0]) | (ff > band[1])] = 0
    env = np.abs(np.fft.irfft(X, len(rec)))
    m2 = 1 << 15
    wm = np.hanning(m2)
    ea = np.zeros(m2 // 2 + 1)
    kk = 0
    for s in range(0, len(env) - m2, m2 // 2):
        seg = env[s:s + m2] - env[s:s + m2].mean()
        ea += np.abs(np.fft.rfft(seg * wm)) ** 2
        kk += 1
    ratio = float("nan")
    if kk:
        ea /= kk
        fe = np.fft.rfftfreq(m2, 1.0 / fs)
        j1 = int(np.argmin(np.abs(fe - f0)))
        j2 = int(np.argmin(np.abs(fe - 2 * f0)))
        p1 = float(ea[max(0, j1 - 2):j1 + 3].sum())
        p2 = float(ea[max(0, j2 - 2):j2 + 3].sum())
        if p2 > 0:
            ratio = 10.0 * math.log10(max(p1, 1e-30) / p2)

    return (10.0 * math.log10(ref),
            10.0 * math.log10(max(harm, 1e-30) / ref),
            10.0 * math.log10(max(vent, 1e-30) / ref),
            ratio)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sink", required=True)
    ap.add_argument("--mic", required=True)
    ap.add_argument("--column", type=int, default=None)
    ap.add_argument("--tone", type=float, default=50.0,
                    help="the tone to hold, in Hz (default 50, near "
                         "where a small monitor's vent is tuned)")
    ap.add_argument("--band", type=float, nargs=2, default=DEFAULT_BAND,
                    metavar=("LO", "HI"),
                    help="where to look for the vent's noise "
                         "(default 400 800, where his sat)")
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--from-dbfs", type=float, default=-30.0,
                    help="quietest tone played (default -30)")
    ap.add_argument("--rungs", type=int, default=6)
    ap.add_argument("--step-db", type=float, default=3.0)
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

    import tempfile
    outdir = tempfile.mkdtemp(prefix="pdeq-vent-")
    wav = os.path.join(outdir, "tone.wav")
    source = {"name": pw_backend.entry_node(src["name"]),
              "id": src["id"]}
    back = pw_backend.backend()

    print("output : %s" % sink["name"])
    print("mic    : %s  column %d of %d" % (src["name"], column, width))
    print("tone   : %g Hz, vent band %g-%g Hz\n"
          % (a.tone, a.band[0], a.band[1]))
    print("A TONE WILL PLAY, and it climbs. The level is the graph's;")
    print("bring the card's own output down first, but not to zero.\n")
    print("  %-8s %-9s %-11s %-11s %s"
          % ("dBFS", "peak", "harmonics", "VENT NOISE", "pulse 1x/2x"))

    rows = []
    for i in range(a.rungs):
        lvl = a.from_dbfs + i * a.step_db
        dur = tone_wav(wav, a.tone, mc.DEFAULT_FS, a.seconds, lvl)
        print("  %-8.0f playing..." % lvl, end="\r", flush=True)
        back.moratorium_begin(sink["name"], None, mute_others=True)
        try:
            data, _ = run_take(sink, source, wav, dur + 1.0, width,
                               mc.DEFAULT_FS, verify=False)
        finally:
            back.moratorium_end()
        chan = np.asarray(data)[:, min(column, data.shape[1] - 1)]
        pk = float(np.max(np.abs(chan))) if chan.size else 0.0
        pk_db = 20.0 * math.log10(pk) if pk > 0 else -120.0
        got = measure(chan, mc.DEFAULT_FS, a.tone, tuple(a.band))
        if got is None:
            print("  %-8.0f %-9.1f nothing came back" % (lvl, pk_db))
            continue
        _, harm, vent, ratio = got
        rows.append((lvl, vent, harm))
        print("  %-8.0f %-9.1f %-11.1f %-11.1f %s"
              % (lvl, pk_db, harm, vent,
                 "--" if not np.isfinite(ratio) else "%+.1f dB" % ratio))
        if pk_db > -3.0:
            print("\n  the capture is at the top of its scale -- stopping")
            break

    print("\n  vent noise and harmonics are BOTH relative to the tone.")
    print("  A vent has a threshold: look for the rung where its column")
    print("  jumps while the harmonic column keeps its steady climb.")
    if len(rows) >= 3:
        x = np.array([r[0] for r in rows])
        jump = np.diff([r[1] for r in rows])
        step = np.diff([r[2] for r in rows])
        j = int(np.argmax(jump - step))
        if jump[j] - step[j] > 3.0:
            print("  Sharpest such rung: %g dBFS, where the vent rose "
                  "%+.1f dB\n  against the harmonics' %+.1f."
                  % (x[j + 1], jump[j], step[j]))
        else:
            print("  No rung stands out: nothing here behaves like a "
                  "vent letting go.")
    print("\n  A RAG IN THE PORT IS THE CONTROL. Plugged, the vent")
    print("  column should collapse while the harmonics RISE, because")
    print("  an unloaded cone travels further.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
