#!/usr/bin/env python3
"""Track a recorded exponential sweep along its own frequency
trajectory and print the in-band level over sweep time.

A wideband RMS train cannot see a sweep whose windows sit at
the level of this rig's DC/infrasonic floor; this tool follows
the sweep's known instantaneous frequency with a Goertzel bin
per hop, so the floor is out of band and a mid-sweep gain step
is unmissable.

Sweep model (the app's defaults): exponential 20 Hz .. 20 kHz,
262144 samples at 48 kHz (5.4613 s), constant amplitude. The
recording's sweep onset is found automatically by scanning
candidate onsets and picking the one with the most in-band
energy along the trajectory.

Usage:
  sweep_track.py RECORDING.wav [more.wav ...]

Prints, per file: the onset, then "t=.. f=..Hz  L=.. dB" lines
every 0.25 s of sweep time. Standalone, stdlib only, absolute
or relative paths alike.
"""

import cmath
import math
import struct
import sys

FS = 48000
# THE SWEEP THIS TRACKS IS THE ONE THE APP PLAYS, and it must follow
# it. measure_core.DEFAULT_N is the authority; this file stays free of
# imports so it can be dropped on a machine with nothing installed, so
# the number is mirrored here and has to be changed with it. It was
# 262144 for weeks after the measurement halved to 131072, and a
# tracker reading the wrong trajectory reports a level train for a
# sweep that was never played.
#
# --samples N overrides it for a recording of any other length.
N_SWEEP = 131072
F0, F1 = 20.0, 20000.0
DUR = N_SWEEP / FS
LNR = math.log(F1 / F0)


def read_wav(path):
    b = open(path, "rb").read()
    assert b[:4] == b"RIFF" and b[8:12] == b"WAVE"
    i, fmt, data = 12, None, None
    while i + 8 <= len(b):
        cid = b[i:i + 4]
        sz = struct.unpack("<I", b[i + 4:i + 8])[0]
        body = b[i + 8:i + 8 + sz]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
        i += 8 + sz + (sz & 1)
    tag, nch, fs, _, _, bits = fmt
    n = len(data)
    if tag == 3 and bits == 32:
        x = struct.unpack("<%df" % (n // 4), data)
    elif tag == 1 and bits == 16:
        x = [v / 32768.0 for v in
             struct.unpack("<%dh" % (n // 2), data)]
    elif tag == 1 and bits == 32:
        x = [v / 2147483648.0 for v in
             struct.unpack("<%di" % (n // 4), data)]
    else:
        sys.exit("unhandled wav: tag=%d bits=%d" % (tag, bits))
    return x[::nch], fs


def inst_freq(ts):
    return F0 * math.exp(ts / DUR * LNR)


def band_level(x, fs, center, f):
    """Goertzel level in dB at frequency f around sample
    center, window sized to at least 8 cycles (min 5 ms)."""
    w = max(int(0.005 * fs), int(8.0 * fs / f))
    a = center - w // 2
    if a < 0 or a + w > len(x):
        return None
    rot = cmath.exp(-2j * math.pi * f / fs)
    acc, ph = 0j, 1.0 + 0j
    for v in x[a:a + w]:
        acc += v * ph
        ph *= rot
    return 20 * math.log10(abs(acc) / w + 1e-12)


def onset_score(x, fs, t0, probes):
    s = 0.0
    for ts in probes:
        lv = band_level(x, fs, int((t0 + ts) * fs),
                        inst_freq(ts))
        if lv is not None:
            s += 10 ** (lv / 20.0)
    return s


FAST = (3.3, 4.0, 4.7)          # short windows, cheap
FULL = (0.3, 0.8, 1.3, 1.9, 2.6, 3.3, 4.0, 4.7)


def find_onset(x, fs):
    """Two-stage search over the WHOLE file: a coarse pass
    with the fast high-frequency probes every 100 ms, then a
    fine pass with the full probe set on a 50 ms grid around
    the coarse winner. A 9 s court and a 4-minute tap are
    the same one command."""
    t_max = max(0.0, len(x) / fs - DUR + 0.3)
    best_t0, best = 0.0, -1.0
    t0 = 0.0
    while t0 <= t_max:
        sc = onset_score(x, fs, t0, FAST)
        if sc > best:
            best, best_t0 = sc, t0
        t0 += 0.1
    lo = max(0.0, best_t0 - 0.3)
    hi = min(t_max, best_t0 + 0.3)
    t0, best = lo, -1.0
    fine_t0 = lo
    while t0 <= hi:
        sc = onset_score(x, fs, t0, FULL)
        if sc > best:
            best, fine_t0 = sc, t0
        t0 += 0.05
    return fine_t0


def main():
    args = sys.argv[1:]
    if "--samples" in args:
        i = args.index("--samples")
        globals()["N_SWEEP"] = int(args[i + 1])
        globals()["DUR"] = N_SWEEP / FS
        del args[i:i + 2]
    sys.argv = [sys.argv[0]] + args
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for path in sys.argv[1:]:
        x, fs = read_wav(path)
        best_t0 = find_onset(x, fs)
        print("%s  onset=%.2fs" % (path, best_t0))
        ts = 0.25
        while ts < DUR - 0.1:
            f = inst_freq(ts)
            lv = band_level(x, fs,
                            int((best_t0 + ts) * fs), f)
            if lv is not None:
                print("  t=%4.2fs f=%7.1fHz  L=%6.1f dB"
                      % (ts, f, lv))
            ts += 0.25
        print()


if __name__ == "__main__":
    main()
