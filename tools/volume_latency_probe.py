#!/usr/bin/env python3
"""Measure how long a sink volume change takes to reach
the audio, and whether the value polled from pw-dump can be
trusted as the applied gain.

Plays a 12 s 1 kHz tone into SINK while recording SOURCE,
changes the sink volume twice mid-tone (down, then up) and
prints three timestamps for each change on one axis whose
zero is the recorder stream's transition to running:

  t_write     the set-volume command returned
  t_readback  the first pw-dump poll showing the new value
  t_audio     the level step found in the recorded samples

The recording is checked before any timing conclusion:
the 1 kHz tone advances exactly five cycles per 5 ms
analysis hop, so its phase must stay constant -- any phase
jump marks lost or duplicated capture frames and is printed
with its time (a gap of a whole number of milliseconds
keeps the phase and cannot be seen this way). Timing
results are refused when the audio precedes its own command
or when the audio interval between the two changes differs
from the command interval by more than 30 ms.

Reference numbers from this rig: apply latency 46-63 ms,
polled value never earlier than the audio; a synthetic
108.4 ms cut is detected as a 2.49 rad phase jump against
2.51 expected.

Usage:
  volume_latency_probe.py SINK_NAME SOURCE_NAME
  volume_latency_probe.py --verdict-only [WAV]
"""

import cmath
import json
import math
import struct
import subprocess
import sys
import time
import wave

WAV = "/tmp/volume-latency-probe.wav"


def synth_tone(path):
    fs, dur, amp = 48000, 12.0, 10 ** (-6 / 20)
    n = int(fs * dur)
    fade = 240
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(fs)
    frames = bytearray()
    for i in range(n):
        a = amp
        if i < fade:
            a *= i / fade
        elif i > n - fade:
            a *= (n - i) / fade
        frames += struct.pack("<h", int(
            32767 * a * math.sin(
                2 * math.pi * 1000.0 * i / fs)))
    w.writeframes(bytes(frames))
    w.close()
    return path


def pwdump():
    out = subprocess.run(["pw-dump"], capture_output=True,
                         text=True).stdout
    return json.loads(out)


def node_state(dump, name):
    for o in dump:
        try:
            if o["info"]["props"]["node.name"] != name:
                continue
            cv = sv = None
            for p in o["info"]["params"]["Props"]:
                if "channelVolumes" in p:
                    cv = [round(v, 6) for v in
                          p["channelVolumes"]]
                    sv = [round(v, 6) for v in
                          p.get("softVolumes") or []]
            return o["id"], cv, sv
        except (KeyError, TypeError):
            continue
    return None, None, None


def recorder_running(dump):
    for o in dump:
        try:
            props = o["info"]["props"]
            if (props.get("media.class") ==
                    "Stream/Input/Audio"
                    and "pw-record" in
                    (props.get("application.name") or
                     props.get("node.name") or "")):
                return o["info"]["state"] == "running"
        except (KeyError, TypeError):
            continue
    return False


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
        sys.exit("unhandled wav: tag=%d bits=%d"
                 % (tag, bits))
    return x[::nch], fs


def audio_clocks(path):
    x, fs = read_wav(path)
    hop = int(0.005 * fs)
    n = len(x) // hop
    w = cmath.exp(-2j * math.pi * 1000.0 / fs)
    band = []
    acc_list = []
    for k in range(n):
        seg = x[k * hop:(k + 1) * hop]
        acc, rot = 0j, 1.0 + 0j
        for v in seg:
            acc += v * rot
            rot *= w
        acc_list.append(acc)
        band.append(20 * math.log10(abs(acc) / hop + 1e-12))
    t = [(k + 0.5) * hop / fs for k in range(n)]

    def med(a, b):
        vals = sorted(band[k] for k in range(n)
                      if a < t[k] < b)
        return vals[len(vals) // 2] if vals else -120.0

    hi, lo, hi2 = med(2.5, 3.9), med(4.8, 6.4), med(7.2, 9.5)
    print("1k band: pre=%.1f between=%.1f after=%.1f dB"
          % (hi, lo, hi2))
    # capture integrity check: at 1 kHz and a 5 ms hop the
    # tone advances exactly five whole cycles per hop, so
    # its phase must stay constant; a phase jump marks lost
    # or duplicated frames. A gap of a whole number of
    # milliseconds keeps the phase and cannot be seen here;
    # PipeWire quantum-sized losses (e.g. 21.33 ms) can
    ph = [cmath.phase(a) for a in acc_list]
    tears = []
    for k in range(1, n):
        if band[k] < hi - 15 or band[k - 1] < hi - 15:
            continue
        d = (ph[k] - ph[k - 1] + math.pi) % (2 * math.pi) \
            - math.pi
        if abs(d) > 0.6:
            tears.append((t[k], d))
    if tears:
        print("capture gaps detected: %d"
              % len(tears))
        for tt, d in tears[:12]:
            print("  t=%6.3fs  phase jump %+.2f rad"
                  % (tt, d))
    if hi - lo < 3:
        print("no clear step in the record -- is the "
              "earphone in the coupler?")
        return None, None

    def edge(a, b, frm, to):
        half = (frm + to) / 2.0
        for k in range(n):
            if a < t[k] < b:
                if (to < frm and band[k] < half) or \
                        (to > frm and band[k] > half):
                    return t[k]
        return None

    return edge(3.9, 4.8, hi, lo), edge(6.3, 7.4, lo, hi2)


def verdict(stamps, td, tu):
    if td is None or tu is None:
        print("RESULT: no level step found in the record "
              "-- is the earphone sealed in the coupler?")
        return
    (wd, rd), (wu, ru) = stamps
    if td < wd or tu < wu:
        print("RESULT UNRELIABLE: the audio step precedes "
              "its own command; the recording lost frames "
              "(see gaps above) -- no timing conclusion")
        return
    if abs((tu - td) - (wu - wd)) > 0.03:
        print("RESULT UNRELIABLE: audio interval differs "
              "from the command interval by %.0f ms; the "
              "recording lost or duplicated frames -- no "
              "timing conclusion"
              % (abs((tu - td) - (wu - wd)) * 1000))
        return
    print("t_audio(down) = %.3f   t_audio(up) = %.3f"
          % (td, tu))
    print("apply latency: %+.0f ms / %+.0f ms "
          "(audio - write)"
          % ((td - wd) * 1000, (tu - wu) * 1000))
    print("observation:   %+.0f ms / %+.0f ms "
          "(audio - readback)"
          % ((td - rd) * 1000, (tu - ru) * 1000))
    print("intervals: write %.3f  readback %.3f  "
          "audio %.3f s" % (wu - wd, ru - rd, tu - td))
    mars = max((rd - td), (ru - tu))
    if mars > 0.06:
        print("RESULT: polled value changes %.0f ms "
              "BEFORE the audio -- do not trust pw-dump "
              "as applied gain here" % (mars * 1000))
    else:
        print("RESULT: polled value tracks the applied "
              "gain; observation never earlier than the "
              "audio")


def main():
    if sys.argv[1:2] == ["--verdict-only"]:
        path = sys.argv[2] if len(sys.argv) > 2 else WAV
        td, tu = audio_clocks(path)
        if td:
            print("t_audio(down)=%.3f t_audio(up)=%s"
                  % (td, "%.3f" % tu if tu else "?"))
        return
    sink, source = sys.argv[1], sys.argv[2]
    tone = synth_tone("/tmp/pdeq-tone-1k.wav")
    sid, before, sv = node_state(pwdump(), sink)
    if sid is None:
        sys.exit("sink not found: %s" % sink)
    print("sink id=%d  cv before=%s  sv=%s"
          % (sid, before, sv))
    rec = play = None
    stamps = []
    try:
        rec = subprocess.Popen(
            ["pw-record", "--target", source, WAV])
        t_lim = time.monotonic() + 5.0
        t_rec0 = None
        while time.monotonic() < t_lim:
            if recorder_running(pwdump()):
                t_rec0 = time.monotonic()
                break
            time.sleep(0.02)
        if t_rec0 is None:
            sys.exit("recorder never reached running")
        print("recorder running (axis zero taken)")
        play = subprocess.Popen(
            ["pw-play", "--target", sink, tone])
        time.sleep(4.0)
        prev = before
        for step, vol in (("down", "0.60"),
                          ("up", "0.85")):
            t0 = time.monotonic()
            subprocess.run(["wpctl", "set-volume",
                            str(sid), vol], check=True)
            tw = time.monotonic() - t_rec0
            print("t_write(%s)   = %.3f" % (step, tw))
            tr = None
            while time.monotonic() - t0 < 5.0:
                _, cv, _ = node_state(pwdump(), sink)
                if cv and cv != prev:
                    tr = time.monotonic() - t_rec0
                    print("t_readback(%s)= %.3f (cv %s)"
                          % (step, tr, cv))
                    prev = cv
                    break
                time.sleep(0.01)
            stamps.append((tw, tr))
            time.sleep(2.5)
        play.wait()
        time.sleep(0.5)
    finally:
        for p in (rec, play):
            if p is not None and p.poll() is None:
                p.terminate()
        if before:
            subprocess.run(
                ["wpctl", "set-volume", str(sid),
                 "%.4f" % (before[0] ** (1.0 / 3.0))])
    td, tu = audio_clocks(WAV)
    verdict(stamps, td, tu)


if __name__ == "__main__":
    main()
