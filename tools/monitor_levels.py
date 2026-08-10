#!/usr/bin/env python3
"""Per-channel level of a sink's monitor, measured WITHOUT the app.

A yardstick for the level meter: it opens the same monitor the meter
does and prints which channels actually carry sound, so a disagreement
between the meter and this is the meter's.

--raw is not optional. Without it pw-record writes a 24-byte .snd
header, and a reader that takes those bytes for audio is offset by six
float32 samples -- which rotates a ten-channel capture by six and a
stereo one by none. That is exactly how the meter came to show a
ten-channel card's music six rows away from where it played, and how
this probe, in its first form, agreed with the meter instead of
catching it.

The channel map is asked for by the names the PORTS carry. A node can
answer three ways about the same channels -- audio.position, the
negotiated Format, and the ports -- and a capture is matched against
the ports.

    python3 monitor_levels.py                 # the running sink
    python3 monitor_levels.py <node.name>

Play something while it runs. It takes about three seconds.
"""
import json
import math
import struct
import subprocess
import sys

SECS = 3.0
RATE = 48000


def dump():
    r = subprocess.run(["pw-dump"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("pw-dump failed: %s" % r.stderr[-200:])
    return json.loads(r.stdout)


def props(o):
    return ((o.get("info") or {}).get("props") or {})


def find_sink(objs, want):
    best = None
    for o in objs:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = props(o)
        if p.get("media.class") != "Audio/Sink":
            continue
        name = p.get("node.name") or ""
        if want and want not in name:
            continue
        state = (o.get("info") or {}).get("state")
        if want or state == "running":
            return o
        best = best or o
    return best


def port_names(objs, nid):
    rows = []
    for o in objs:
        if "Port" not in (o.get("type") or ""):
            continue
        p = props(o)
        if str(p.get("node.id")) != str(nid):
            continue
        if (p.get("port.direction") or "") != "out":
            continue
        if p.get("audio.channel"):
            rows.append((int(p.get("port.id") or 0),
                         str(p.get("audio.channel"))))
    return [c for _i, c in sorted(rows)]


def parse_position(raw):
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if not isinstance(raw, str):
        return []
    return [s.strip() for s in raw.strip().strip("[]").split(",")
            if s.strip()]


def capture(target, nch, positions):
    cmd = ["pw-record", "--raw", "--target", str(target),
           "-P", "{ stream.capture.sink = true,"
                 " node.name = monitor-levels-probe }",
           "--format", "f32", "--rate", str(RATE),
           "--channels", str(nch)]
    if positions and len(positions) == nch:
        cmd += ["--channel-map", ",".join(positions)]
    cmd.append("-")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    want = int(RATE * SECS) * nch * 4
    data = b""
    while len(data) < want:
        chunk = p.stdout.read(want - len(data))
        if not chunk:
            break
        data += chunk
    p.terminate()
    try:
        err = p.stderr.read(4000).decode("utf-8", "replace").strip()
    except Exception:
        err = ""
    n = len(data) // (4 * nch) * nch
    if not n:
        return None, err or "no audio captured"
    vals = struct.unpack("<%df" % n, data[:n * 4])
    peaks = [0.0] * nch
    for i, v in enumerate(vals):
        c = i % nch
        a = abs(v)
        if a > peaks[c]:
            peaks[c] = a
    return [20.0 * math.log10(x) if x > 1e-7 else -140.0
            for x in peaks], err


def main():
    objs = dump()
    want = sys.argv[1] if len(sys.argv) > 1 else None
    node = find_sink(objs, want)
    if node is None:
        sys.exit("no sink found (name filter: %r)" % want)
    name = props(node).get("node.name")
    ports = port_names(objs, node["id"])
    pos = parse_position(props(node).get("audio.position"))
    nch = len(ports) or len(pos) or 2
    print("sink: %s" % name)
    print("  ports        : %s" % ", ".join(ports))
    print("  audio.position: %s" % ", ".join(pos))
    print("  channels     : %d\n" % nch)

    peaks, err = capture(name, nch, ports)
    if err:
        print("pw-record said: %s" % err.replace("\n", " | ")[:300])
    if peaks is None:
        print("nothing captured -- was anything playing?")
        return
    print("peaks: %s" % ", ".join("%d:%.1f" % (i, v)
                                  for i, v in enumerate(peaks)))
    loud = [i for i, v in enumerate(peaks) if v > -60.0]
    print("loud channels: %s"
          % (loud if loud else "none -- was anything playing?"))
    print("\nThe meter in the window should light the same rows. "
          "Index N is filters%s in the published graph." % "N+1")


if __name__ == "__main__":
    main()
