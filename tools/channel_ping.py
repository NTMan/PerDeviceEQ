#!/usr/bin/env python3
"""Which channel of a sink reaches which ear.

Everything in this hunt has been read off an instrument that turned out
to describe something other than what it claimed. This one has no
instrument in it: it plays a tone into ONE channel of the sink at a
time and asks you what you heard. The answer maps a channel INDEX --
which is what the EQ graph is indexed by, filtersN being the Nth
channel -- onto a physical earpiece, with nothing in between to lie.

Bypass the equaliser (or select No EQ) before running, so the profile
does not colour the result.

    python3 channel_ping.py Direct__Direct__sink
    python3 channel_ping.py Direct__Direct__sink 6 7   # only these
"""
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
import wave

RATE = 48000
SECS = 1.5
FREQ = 440.0
LEVEL = 0.25


def dump():
    r = subprocess.run(["pw-dump"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("pw-dump failed: %s" % r.stderr[-200:])
    return json.loads(r.stdout)


def props(o):
    return ((o.get("info") or {}).get("props") or {})


def find_sink(objs, want):
    for o in objs:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = props(o)
        if p.get("media.class") != "Audio/Sink":
            continue
        if want in (p.get("node.name") or ""):
            return o
    return None


def port_names(objs, nid):
    rows = []
    for o in objs:
        if "Port" not in (o.get("type") or ""):
            continue
        p = props(o)
        if str(p.get("node.id")) != str(nid):
            continue
        if (p.get("port.direction") or "") != "in":
            continue
        if p.get("audio.channel"):
            rows.append((int(p.get("port.id") or 0),
                         str(p.get("audio.channel"))))
    return [c for _i, c in sorted(rows)]


def write_wav(path, nch, ch):
    """A multichannel WAV with the tone in exactly ONE channel.

    A file rather than a pipe: pw-play reads through libsndfile, which
    wants a container it recognises and rejects a raw stream on stdin.
    32-bit PCM because the stdlib wave module writes integer samples
    only, and sndfile reads that without argument.
    """
    n = int(RATE * SECS)
    peak = int(LEVEL * 2147483647)
    frames = bytearray()
    for i in range(n):
        v = math.sin(2.0 * math.pi * FREQ * i / RATE)
        # a short fade at both ends, so a click is not mistaken for a tone
        w = min(1.0, i / 480.0, (n - i) / 480.0)
        frame = [0] * nch
        frame[ch] = int(peak * v * w)
        frames += struct.pack("<%di" % nch, *frame)
    with wave.open(path, "wb") as f:
        f.setnchannels(nch)
        f.setsampwidth(4)
        f.setframerate(RATE)
        f.writeframes(bytes(frames))


def play(target, nch, positions, path):
    cmd = ["pw-play", "--target", str(target)]
    if positions and len(positions) == nch:
        cmd += ["--channel-map", ",".join(positions)]
    cmd.append(path)
    p = subprocess.Popen(cmd, stderr=subprocess.PIPE)
    p.wait(timeout=SECS + 20)
    err = p.stderr.read(2000).decode("utf-8", "replace").strip()
    if err:
        print("    pw-play said: %s" % err.replace("\n", " | ")[:200])


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    objs = dump()
    node = find_sink(objs, sys.argv[1])
    if node is None:
        sys.exit("no sink matching %r" % sys.argv[1])
    name = props(node).get("node.name")
    ports = port_names(objs, node["id"])
    nch = len(ports) or 2
    which = [int(x) for x in sys.argv[2:]] or list(range(nch))

    print("sink: %s\nchannels: %d (%s)\n" % (name, nch, ", ".join(ports)))
    print("Bypass the equaliser first. Write down, for each index, "
          "whether you hear it and in which ear.\n")
    for ch in which:
        if not 0 <= ch < nch:
            continue
        label = ports[ch] if ch < len(ports) else "?"
        print("  index %d  (port %s) ..." % (ch, label))
        sys.stdout.flush()
        path = os.path.join(tempfile.gettempdir(),
                            "pdeq-ping-%d.wav" % ch)
        write_wav(path, nch, ch)
        try:
            play(name, nch, ports, path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        time.sleep(0.6)
    print("\nThe index is what the EQ is numbered by: index N is "
          "filters%s in the published graph." % "N+1")


if __name__ == "__main__":
    main()
