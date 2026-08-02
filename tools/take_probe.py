#!/usr/bin/env python3
"""Probe a first capture after idle, one condition at a time.

Background: the first sweep of a GUI session after a long idle
period used to lose 13-19 dB from ~1.6 s onward. The cause was the
level-meter machinery republishing the device's EQ graph in the
middle of a take (see commit "eq: never publish the graph while a
measure window is open"). This probe is how the trigger was
isolated: each mode reproduces exactly one ingredient of a real
session after a silent arming wait, so a regression shows up as a
mid-sweep level knife in one train and not the others.

Every mode waits out its own silent arming period (default 600 s),
synthesizes its own exponential sweep (20 Hz - 20 kHz, 5.46 s,
-6 dBFS), plays it through --sink while recording --source, and
prints the level train along the sweep's trajectory via
sweep_track.py from the same directory.

Modes (--mode, comma-separated, in order):
  baseline        plain play + record, nothing else
  monitor         a sink-monitor capture (stream.capture.sink=true,
                  f32/48k/2ch, the level meter's dress) opens 10 s
                  before the sweep and is held through it
  second-capture  a second recording holds the source open with a
                  10 s head start
  mono            the recording uses --channels 1 (the session's
                  capture format)
  playback-first  a 12 s tone is played with nothing recording,
                  then the sweep runs immediately: splits which
                  side of the chain holds a wake-up state
  slow-reader     the monitor capture streams to a deliberately
                  slow stdout consumer, held through the sweep
  republish       a held monitor capture plus a metadata graph
                  publish and clear under it (two hook rebuilds,
                  using a neutral, acoustically transparent graph)
  live-app        no imitation: recorders only (pw-top -b frames
                  and a source tap); launch the real app yourself
                  while the window is open -- the probe watches
                  --raw-dir for a new take01.wav and stops 20 s
                  after it appears

Usage:
  take_probe.py --sink SINK_NODE --source SOURCE_NODE
                [--mode baseline] [--wait SECONDS]
                [--metadata-name per-device-eq]
                [--raw-dir ~/pdeq-raw]

Standalone, stdlib only; needs the PipeWire command-line tools and
a sibling sweep_track.py.
"""

import argparse
import math
import os
import struct
import subprocess
import sys
import threading
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))

NEUTRAL_GRAPH = ("{ nodes = [ { type = builtin name = eq "
                 "label = param_eq config = { filters = [ "
                 "{ type = bq_peaking, freq = 1000, gain = 0, "
                 "q = 1.0 } ] } } ] }")

SWEEP_WAV = "/tmp/take-probe-sweep.wav"
TONE_WAV = "/tmp/take-probe-tone.wav"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def synth_sweep(path, fs=48000, n=262144, f0=20.0, f1=20000.0,
                level_db=-6.0, lead=0.5, tail=0.5):
    dur = n / fs
    lnr = math.log(f1 / f0)
    amp = 10.0 ** (level_db / 20.0)
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(fs)
    frames = bytearray()
    for _ in range(int(lead * fs)):
        frames += struct.pack("<h", 0)
    for i in range(n):
        t = i / fs
        ph = 2 * math.pi * f0 * dur / lnr * (
            math.exp(t / dur * lnr) - 1.0)
        frames += struct.pack("<h",
                              int(32767 * amp * math.sin(ph)))
    for _ in range(int(tail * fs)):
        frames += struct.pack("<h", 0)
    w.writeframes(bytes(frames))
    w.close()


def synth_tone(path, fs=48000, seconds=12.0, freq=1000.0,
               level_db=-6.0):
    amp = 10.0 ** (level_db / 20.0)
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(fs)
    frames = bytearray()
    for k in range(int(seconds * fs)):
        v = amp * math.sin(2 * math.pi * freq * k / fs)
        frames += struct.pack("<h", int(32767 * v))
    w.writeframes(bytes(frames))
    w.close()


def silence(seconds):
    print("-- silence %d s: walk away, this waits by itself --"
          % seconds)
    left = seconds
    while left > 0:
        step = min(60, left)
        time.sleep(step)
        left -= step
        print("   %d s left" % left)


class Probe:
    def __init__(self, a):
        self.sink = a.sink
        self.source = a.source
        self.meta = a.metadata_name
        self.raw_dir = os.path.expanduser(a.raw_dir)

    def monitor_tap(self, name, drain):
        tap = subprocess.Popen(
            ["pw-record", "--raw", "-P",
             "{ stream.capture.sink = true, node.name = %s }"
             % name,
             "--format", "f32", "--rate", "48000",
             "--channels", "2", "--target", self.sink, "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def _reader():
            while True:
                chunk = tap.stdout.read(65536)
                if not chunk:
                    return
                if drain == "slow":
                    time.sleep(0.2)

        threading.Thread(target=_reader, daemon=True).start()
        return tap

    def court(self, out_path, mono=False, extra=None):
        play = subprocess.Popen(
            ["pw-play", "--target", self.sink, SWEEP_WAV])
        rec_cmd = ["pw-record"]
        if mono:
            rec_cmd += ["--channels", "1"]
        rec_cmd += ["--target", self.source, out_path]
        rec = subprocess.Popen(rec_cmd)
        time.sleep(9.0)
        for p in (rec, play):
            if p.poll() is None:
                p.terminate()
        for p in (rec, play):
            p.wait()
        if extra is not None and extra.poll() is None:
            extra.terminate()
            extra.wait()

    def track(self, path):
        r = run([sys.executable,
                 os.path.join(HERE, "sweep_track.py"), path])
        print(r.stdout, end="")
        if r.returncode != 0:
            print(r.stderr[-300:])

    def cell(self, mode):
        out = "/tmp/take-probe-%s.wav" % mode
        if mode == "baseline":
            self.court(out)
        elif mode == "monitor":
            tap = self.monitor_tap("take-probe-monitor", "fast")
            time.sleep(10.0)
            self.court(out, extra=tap)
        elif mode == "second-capture":
            tap = subprocess.Popen(
                ["pw-record", "--target", self.source,
                 "/tmp/take-probe-second.wav"])
            time.sleep(10.0)
            self.court(out, extra=tap)
        elif mode == "mono":
            self.court(out, mono=True)
        elif mode == "playback-first":
            print("spending the wake with playback only: "
                  "12 s tone, nothing recording")
            subprocess.run(["pw-play", "--target", self.sink,
                            TONE_WAV])
            self.court(out)
        elif mode == "slow-reader":
            tap = self.monitor_tap("take-probe-slow", "slow")
            time.sleep(10.0)
            self.court(out, extra=tap)
        elif mode == "republish":
            tap = self.monitor_tap("take-probe-republish",
                                   "fast")
            time.sleep(2.0)
            print("publishing a neutral graph under a held "
                  "monitor (hook rebuild #1)")
            run(["pw-metadata", "-n", self.meta, "0",
                 self.sink, NEUTRAL_GRAPH])
            time.sleep(1.0)
            print("clearing the key (hook rebuild #2); the "
                  "sweep follows in the same breath")
            run(["pw-metadata", "-n", self.meta, "-d", "0",
                 self.sink])
            self.court(out, extra=tap)
        elif mode == "live-app":
            return self.live_app(out)
        else:
            sys.exit("unknown mode: %s" % mode)
        self.track(out)
        return out

    def live_app(self, out):
        top_log = open("/tmp/take-probe-pwtop.log", "w")
        top = subprocess.Popen(["pw-top", "-b"],
                               stdout=top_log,
                               stderr=subprocess.DEVNULL)
        tap = subprocess.Popen(
            ["pw-record", "--target", self.source, out])
        subprocess.run(["notify-send", "recorders live",
                        "launch the app now: one session, "
                        "one sweep"], capture_output=True)

        def _dirs():
            try:
                return set(os.listdir(self.raw_dir))
            except OSError:
                return set()

        before = _dirs()
        print("RECORDERS LIVE, window up to 600 s -- no "
              "rush, the probe watches %s for a new "
              "take01.wav and stops by itself. Launch the "
              "app in another terminal, run one session "
              "with one sweep, close it." % self.raw_dir)
        waited, seen = 0, None
        while waited < 600:
            time.sleep(5)
            waited += 5
            if waited % 30 == 0:
                print("   window: %d s left" % (600 - waited))
            for d in _dirs() - before:
                t1 = os.path.join(self.raw_dir, d,
                                  "take01.wav")
                if os.path.isfile(t1):
                    seen = t1
            if seen:
                print("take01 seen (%s); letting the "
                      "session finish 20 s" % seen)
                time.sleep(20)
                break
        for p in (tap, top):
            if p.poll() is None:
                p.terminate()
        for p in (tap, top):
            p.wait()
        top_log.close()
        self.track(out)
        print("graph frames: /tmp/take-probe-pwtop.log")
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sink", required=True,
                    help="playback node name (the device under "
                         "test)")
    ap.add_argument("--source", required=True,
                    help="capture node name (the measurement "
                         "mic)")
    ap.add_argument("--mode", default="baseline")
    ap.add_argument("--wait", type=int, default=600)
    ap.add_argument("--metadata-name", default="per-device-eq")
    ap.add_argument("--raw-dir", default="~/pdeq-raw")
    a = ap.parse_args()

    synth_sweep(SWEEP_WAV)
    synth_tone(TONE_WAV)
    probe = Probe(a)
    results = []
    for m in [x.strip() for x in a.mode.split(",") if x.strip()]:
        print("\n== mode: %s ==" % m)
        silence(a.wait)
        results.append(probe.cell(m))
    subprocess.run(["notify-send", "take probe done",
                    "paste the trains"], capture_output=True)
    print("\ndone: %s" % ", ".join(results))


if __name__ == "__main__":
    main()
