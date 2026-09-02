#!/usr/bin/env python3
"""Ask a rig for the whole bass at once, the way music does, and see
whether it gives back what a sweep says it should.

    tools/multitone_probe.py --sink "speaker" --mic umik --column 0 \
        --play FL --at 0.66

WHY. A sweep asks for ONE frequency at a time; music asks for the
whole low end together. The standing suspicion was that the second
therefore asks for more, and that a rig could answer every rung of a
sweep and still run out on a chord -- which would explain his iLoud,
whose sweep-walked map calls the level he listens at clean while his
ear does not.

THE ARITHMETIC DOES NOT SUPPORT THAT SUSPICION, and it is worth
writing down before anyone leans on it again. Displacement goes as
amplitude over frequency squared, so a peak-limited signal spends its
stroke on whatever is lowest. Against a lone 50 Hz tone at the same
peak -- which is what the sweep gives at 50 Hz, where his map found
the loss -- four bass tones together ask for 0.87 of the stroke, three
ask 0.95, and seven ask 0.67. Adding tones at a fixed peak makes each
one quieter faster than the sum grows. His own track asks less still:
its 40-60 Hz band at the loudest moment works out at 0.68 and its
60-90 Hz band at 0.37.

So this probe is not a demonstration of anything. It is the direct
question, asked instead of computed: play the low end together at the
level he actually listens at, and see whether the rig delivers.

WHAT IS PLAYED. A handful of tones spread over the band in question,
at the SAME PEAK as the sweep, with randomised phases so the crest
factor stays near a sweep's rather than piling into a spike. Then the
same set at a level 6 dB lower. A rig with headroom gives back 6 dB
less on every tone; one that has run out gives back less than that
where it ran out -- the same question the level map asks, put with a
signal shaped like music.

WHAT IT DOES NOT DO. It does not replace the sweep: no phase, no
impulse, no harmonics separated by their pre-arrival. It answers one
question -- whether the answer changes when the tones arrive together
-- and nothing else.

BEFORE RUNNING IT: tones play at the level given, twice. Set the
card's own analogue level where you keep it.

Runs from any directory. The volume is restored afterwards.
"""

import argparse
import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from perdeviceeq import level_run                            # noqa: E402
from perdeviceeq import measure_core as mc                   # noqa: E402
from perdeviceeq import pw_backend                           # noqa: E402
from perdeviceeq.sweep_io import run_take                    # noqa: E402

# THE TONES. Thirds of an octave through the region a ported box and a
# driver's stroke give out in, which on his rigs is 40-100 Hz, with a
# pair above it as a control: whatever happens down there should not
# happen at 400.
# FOUR OF THEM, not seven. At a fixed peak every extra tone makes all
# of them quieter, and past four the signal asks the driver for less
# stroke than a single tone does -- which would make any rig look
# clean for the wrong reason. Four bass tones ask 0.87 of a lone
# tone's stroke; seven ask 0.67.
DEFAULT_TONES = (40.0, 50.0, 63.0, 80.0)

# AND ONE CONTROL WELL ABOVE THEM, at a quarter of their amplitude.
# It answers "is anything wrong at all, or only down there", and it is
# kept quiet because at a fixed peak every tone steals stroke from the
# others: put a full-level control at 400 Hz and the bass tones drop
# from 0.87 of a lone tone's stroke to 0.70, which would let a rig off
# the hook for the wrong reason.
CONTROL_HZ = 400.0
CONTROL_WEIGHT = 0.25
DEFAULT_SECONDS = 3.0
DEFAULT_DROP_DB = 6.0


def make_multitone(freqs, seconds, fs, peak=0.5, seed=7, weights=None):
    """Tones summed at equal amplitude, phases randomised.

    THE PHASES MATTER. Summed in phase, N tones make a spike N times
    one tone's amplitude and the peak limit then buys almost no power
    -- the rig would be asked for far less than music asks. Random
    phases keep the crest near a sweep's, so the two signals are
    compared at the same peak AND something like the same power.

    The frequencies are nudged onto whole cycles of the buffer so the
    analysis reads them without leakage.
    """
    n = int(round(seconds * fs))
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    exact = []
    x = np.zeros(n)
    w = list(weights or [1.0] * len(freqs))
    for f, a in zip(freqs, w):
        k = max(1, int(round(f * n / fs)))
        ff = k * fs / n
        exact.append(ff)
        x += a * np.sin(2 * np.pi * ff * t + rng.uniform(0, 2 * np.pi))
    x *= peak / np.max(np.abs(x))
    # the same raised-cosine edges the sweep uses, for the same reason
    e = int(round(0.030 * fs))
    if e:
        w = 0.5 * (1 - np.cos(np.linspace(0, math.pi, e)))
        x[:e] *= w
        x[-e:] *= w[::-1]
    return x, exact


def read_tones(chan, freqs, fs):
    """The level of each tone in the capture, in dB, and the noise
    beside it -- measured in the same bin width so the two compare."""
    n = len(chan)
    w = np.hanning(n)
    X = np.abs(np.fft.rfft(chan * w)) ** 2
    f = np.fft.rfftfreq(n, 1.0 / fs)
    out = []
    for ff in freqs:
        i = int(np.argmin(np.abs(f - ff)))
        band = X[max(0, i - 2):i + 3].sum()
        near = np.concatenate([X[max(0, i - 40):max(0, i - 10)],
                               X[i + 10:i + 40]])
        floor = float(np.median(near)) * 5 if near.size else 1e-30
        out.append((10 * np.log10(max(band, 1e-30)),
                    10 * np.log10(max(floor, 1e-30))))
    return out


def find(items, needle):
    n = (needle or "").lower()
    for it in items:
        if n in (it.get("name") or "").lower():
            return it
    for it in items:
        if n in (it.get("desc") or it.get("description") or "").lower():
            return it
    return None


def play_at(back, name, sink, source, wav, duration, channels, fs,
            column, volume, play_map):
    back.moratorium_begin(name, volume, mute_others=True)
    try:
        data, _info = run_take(sink, source, wav, duration, channels,
                               fs, verify=False, channel_map=play_map)
    finally:
        back.moratorium_end()
    a = np.asarray(data)
    return a[:, min(column, a.shape[1] - 1)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sink", required=True)
    ap.add_argument("--mic", required=True)
    ap.add_argument("--column", type=int, default=None)
    ap.add_argument("--play", metavar="POS",
                    help="channel position to play into, e.g. FL")
    ap.add_argument("--at", type=float, default=None,
                    help="cubic volume of the LOUD pass; by default "
                         "wherever the sink's volume already stands")
    ap.add_argument("--drop-db", type=float, default=DEFAULT_DROP_DB,
                    help="how much quieter the reference pass is "
                         "(default %.0f)" % DEFAULT_DROP_DB)
    ap.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    ap.add_argument("--tones", default=None,
                    help="comma-separated frequencies; the default "
                         "covers 40-100 Hz with a control pair above")
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

    loud = a.at
    if loud is None:
        g = sink.get("gain")
        loud = g[0] if isinstance(g, (tuple, list)) else g
        if loud is None:
            print("cannot read where the volume stands; give --at")
            return 1
    quiet = level_run._clamp(loud * 10.0 ** (-abs(a.drop_db) / 60.0))

    tones = ([float(x) for x in a.tones.split(",")] if a.tones
             else list(DEFAULT_TONES) + [CONTROL_HZ])
    weights = [1.0] * len(tones)
    if not a.tones:
        weights[-1] = CONTROL_WEIGHT
    fs = mc.DEFAULT_FS
    sig, exact = make_multitone(tones, a.seconds, fs, weights=weights)
    crest = 20 * math.log10(np.max(np.abs(sig))
                            / np.sqrt(np.mean(sig ** 2)))

    outdir = tempfile.mkdtemp(prefix="pdeq-mt-")
    wav = os.path.join(outdir, "multitone.wav")
    import soundfile as sf
    pre, post = mc.DEFAULT_PRE_SILENCE, mc.DEFAULT_POST_SILENCE
    sf.write(wav, np.concatenate([np.zeros(int(pre * fs)),
                                  sig,
                                  np.zeros(int(post * fs))]
                                 ).astype("float32"), fs,
             subtype="FLOAT")
    duration = pre + a.seconds + post

    print("output : %s" % sink["name"])
    print("mic    : %s  column %d of %d" % (src["name"], column, width))
    if a.play:
        print("playing: %s only" % a.play)
    print("tones  : %s Hz, %.1f s, crest %.1f dB"
          % (", ".join("%.0f" % t for t in exact), a.seconds, crest))
    print("levels : %.0f%% and %.0f%% (%.0f dB apart)"
          % (100 * loud, 100 * quiet, abs(a.drop_db)))
    print("\nTONES WILL PLAY, TWICE.\n")

    back = pw_backend.backend()
    source = {"name": pw_backend.entry_node(src["name"]),
              "id": src["id"]}
    try:
        qa = play_at(back, sink["name"], sink, source, wav, duration,
                     width, fs, column, quiet, a.play)
        la = play_at(back, sink["name"], sink, source, wav, duration,
                     width, fs, column, loud, a.play)
    except (RuntimeError, ValueError) as exc:
        print("%s" % exc)
        return 1
    except KeyboardInterrupt:
        print("\nstopped.")
        return 1
    finally:
        for fn in os.listdir(outdir):
            try:
                os.unlink(os.path.join(outdir, fn))
            except OSError:
                pass
        try:
            os.rmdir(outdir)
        except OSError:
            pass

    qpk = 20 * math.log10(max(float(np.max(np.abs(qa))), 1e-9))
    lpk = 20 * math.log10(max(float(np.max(np.abs(la))), 1e-9))
    asked = lpk - qpk
    print("  peaks: quiet %.1f dBFS, loud %.1f -- so %.1f dB arrived"
          % (qpk, lpk, asked))
    print("  (the peak is the witness: a sink with a scale of its own "
          "does\n   not deliver the decibels the knob was asked for)\n")

    q = read_tones(qa, exact, fs)
    l = read_tones(la, exact, fs)
    print("  %-8s %-10s %-10s %-9s %s"
          % ("tone", "quiet", "loud", "took", "verdict"))
    worst = None
    for ff, (ql, qf), (ll, lf) in zip(exact, q, l):
        took = ll - ql
        heard = ll - lf
        if heard < level_run.HEARD_OVER_NOISE_DB:
            said = "not heard over the noise"
        elif took < level_run.ANSWER_SHORT * asked:
            said = "SHORT by %.1f dB" % (asked - took)
            worst = max(worst or 0.0, asked - took)
        else:
            said = "answered"
        print("  %-8.0f %-10.1f %-10.1f %-9.1f %s"
              % (ff, ql, ll, took, said))
    print()
    if worst is None:
        print("  every tone answered: asking for the whole band at "
              "once\n  found nothing a sweep would have missed.")
    else:
        print("  the rig is %.1f dB short when the tones arrive "
              "TOGETHER.\n  Compare with what the level map says about "
              "the same level:\n  if the map calls this clean, a sweep "
              "is not asking hard enough." % worst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
