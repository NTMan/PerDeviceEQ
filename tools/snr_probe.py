#!/usr/bin/env python3
"""Walk the capture gain with a TONE playing, and measure SNR directly.

The silent ladder measures noise and infers what SNR must be doing.
This measures it, which is how the inference was checked -- and the
answer changed what the project believes about its own hardware.

WHAT IT SETTLED, on a CM106 into a coupler:

  SNR IS FLAT ABOVE THE KNEE. 31.15, 31.59, 31.98, 31.47 dB across
  the top four rungs -- a plateau 0.83 dB wide, which is measurement
  scatter. The margin above the knee costs nothing, and the working
  point rule stands.

  THE NOISE FOLLOWS THE REAL GAIN ONE FOR ONE. Above the knee the
  tone rose 3.06 dB per dB of the control's axis and the noise 3.08.
  That is ordinary amplified input noise. The ladder's exponent of
  about six, which looked like a preamp getting noisier as it is
  turned up, is this axis stretched threefold by the card's taper.

  AND THE CARD'S DECLARED SCALE IS WRONG. Its ALSA element reports a
  linear 27.06 dB; the tone measured 38.98, with 0.31 dB per dB at
  the bottom and 3.06 at the top. Three pairs of adjacent rungs read
  identically -- one hardware step under two requests -- while
  softVolumes stayed at 1.0, the graph believing it got what it asked
  for. A tone is the only witness to real gain that does not depend
  on the card telling the truth about itself.

    tools/snr_probe.py --sink "Liberty" --mic CM106 --column 0

Nothing is left behind but the gain, which stays where the last rung
put it, as with every walk.
"""

import argparse
import math
import os
import sys
import tempfile
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                      # noqa: E402

from perdeviceeq import knee, knee_run, pw_backend      # noqa: E402
from perdeviceeq import measure_session as ms           # noqa: E402

RATE = 48000
DWELL_S = 1.0


def tone_file(freq, seconds, rate=RATE):
    """A steady sine, written once and looped by the player.

    Whole cycles only: a fragment that does not close leaves a step at
    the loop point, and a step is broadband -- it would land in the
    noise this tool is trying to measure.
    """
    n = int(round(seconds * rate / (rate / float(freq)))) * int(rate / freq)
    t = np.arange(n, dtype=np.float64) / rate
    x = 0.5 * np.sin(2.0 * math.pi * freq * t)
    path = os.path.join(tempfile.mkdtemp(prefix="snrprobe-"), "tone.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def measure(block, channel, freq, rate=RATE):
    """Tone level and the noise around it, both in dBFS.

    One capture gives both: the tone is a single bin, everything else
    in the band is noise. Harmonics are dropped -- they belong to
    distortion, which is a different question and has its own tool.
    """
    x = block[:, channel]
    if x.size < 4096:
        return None, None
    n = 1 << int(math.floor(math.log(x.size, 2)))
    x = x[:n]
    x = x - x.mean()                     # the DC offset is not noise

    # BLACKMAN-HARRIS, not Hann. A tone sixty decibels above the floor
    # leaks into its neighbours, and Hann's sidelobes start at -31 dB:
    # tested against a built signal, the floor read twenty decibels
    # high. This window's start below -90, which is the range being
    # measured.
    w = (0.35875 - 0.48829 * np.cos(2 * math.pi * np.arange(n) / (n - 1.0))
         + 0.14128 * np.cos(4 * math.pi * np.arange(n) / (n - 1.0))
         - 0.01168 * np.cos(6 * math.pi * np.arange(n) / (n - 1.0)))
    sp = np.abs(np.fft.rfft(x * w))
    f = np.fft.rfftfreq(n, 1.0 / rate)
    k = int(np.argmin(np.abs(f - freq)))

    # a tone's RMS: its main lobe against the window's coherent gain.
    # The factor is checked against built signals rather than derived
    # twice -- a stray root of two put every reading 3.02 dB high, and
    # constant at every level, which is what a scale error looks like
    lobe = slice(max(0, k - 8), k + 9)
    tone_rms = float(np.sqrt(np.sum(sp[lobe] ** 2))) / float(np.sum(w))

    keep = np.ones(sp.size, dtype=bool)
    keep[:max(1, int(20.0 * n / rate))] = False        # below the band
    keep[lobe] = False
    for h in range(2, 11):                             # harmonics
        kh = int(round(h * freq * n / rate))
        if kh < sp.size:
            keep[max(0, kh - 8):kh + 9] = False
    # noise power, against the window's power gain rather than its
    # coherent gain -- broadband and a tone are normalised differently
    noise_rms = float(np.sqrt(2.0 * np.sum(sp[keep] ** 2)
                              / (n * float(np.sum(w ** 2)))))
    db = lambda v: 20.0 * math.log10(max(v, 1e-12))    # noqa: E731
    return db(tone_rms), db(noise_rms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sink", required=True)
    ap.add_argument("--mic", required=True)
    ap.add_argument("--column", type=int, default=0)
    ap.add_argument("--freq", type=float, default=1000.0)
    ap.add_argument("--lo", type=float, default=None,
                    help="lowest gain; the card's floor by default")
    ap.add_argument("--hi", type=float, default=0.0)
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--dwell", type=float, default=DWELL_S)
    a = ap.parse_args()

    sink = next((s for s in pw_backend.list_sinks()
                 if a.sink.lower() in s["name"].lower()
                 or a.sink.lower() in (s.get("desc") or "").lower()), None)
    src = next((s for s in pw_backend.list_sources()
                if a.mic.lower() in s["name"].lower()
                or a.mic.lower() in (s.get("desc") or "").lower()), None)
    if sink is None or src is None:
        print("no %s matches" % ("output" if sink is None else "microphone"))
        return 1
    width = pw_backend.source_width(src)
    if a.column >= width:
        print("that source has %d capture channels" % width)
        return 1

    print("output : %s" % sink["name"])
    print("mic    : %s  channel %d of %d" % (src["name"], a.column, width))
    print("A TONE WILL PLAY. Leave the rig alone until this finishes.")

    # LONG ENOUGH FOR THE WHOLE WALK, rather than looped: pw-play has
    # no loop, and a file restarted between rungs would put a gap and a
    # step into the very band being measured. One rung costs a settle,
    # a dwell and a capture; the tail is for the slow ones.
    need = a.steps * (a.dwell * 2.0 + 2.5) + 15.0
    path = tone_file(a.freq, need)
    print("tone   : %.0f Hz, %.0f seconds -- long enough for the walk\n"
          % (a.freq, need))
    play = pw_backend.backend().play(int(sink["id"]), path,
                                     stream_volume=1.0)
    time.sleep(1.0)
    if play.poll() is not None:
        print("pw-play died: %s" % (play.stderr_read() or "")[:200])
        return 1

    rows = []
    try:
        with knee_run.Walk(src, a.column, dwell=a.dwell) as w:
            lo = a.lo
            if lo is None:
                floor = w.hardware_floor_db(restore=False)
                lo = floor if floor is not None else -30.0
                print("floor  : %.1f dB -- the card's own control stops "
                      "here\n" % lo)
            print("  %-9s %-10s %-10s %s"
                  % ("gain", "tone", "noise", "SNR"))
            for db in knee.plan(lo, a.hi, a.steps):
                r = w.visit(db)
                if r is None:
                    continue
                cap = ms.CaptureStream(int(src["id"]), width, RATE)
                try:
                    cap.wait_frames(int(a.dwell * RATE), a.dwell + 3.0)
                    block = cap.data()
                finally:
                    cap.stop()
                tone, noise = measure(block, a.column, a.freq)
                if tone is None:
                    continue
                rows.append((r.gain_db, tone, noise))
                print("  %+7.1f dB %8.2f  %9.2f  %8.2f dB"
                      % (r.gain_db, tone, noise, tone - noise))
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        play.terminate(kill_after=3)

    if len(rows) >= 4:
        print("\n  the answer this was built for:")
        best = max(rows, key=lambda r: r[1] - r[2])
        print("    SNR is highest at %+.1f dB (%.2f dB)"
              % (best[0], best[1] - best[2]))
        top = rows[-1]
        print("    at the top of the walk it is %.2f dB, which is %+.2f"
              % (top[1] - top[2], (top[1] - top[2]) - (best[1] - best[2])))
        print("    a margin ABOVE the best point costs whatever that "
              "difference is")
    print("\nthe gain is where the last rung left it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
