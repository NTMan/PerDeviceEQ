#!/usr/bin/env python3
"""Which capture columns of a source actually carry anything.

    python3 tools/input_census.py <source-node-name> [seconds]

Records the source WIDE for a few seconds and prints one line per
column: its name, its peak, and a bar. Run it while a source plays --
Bluetooth, OTG, coax, the analogue jack -- and the columns that move
are that input's columns.

Why this exists: his M62 declares sixteen capture columns and there is
no document anywhere saying what they carry. The measurement window's
meter answers the same question live; this answers it as a TABLE, which
is what an ALSA UCM pull request can be shown.

Runs from any directory. Nothing is written anywhere.
"""

import math
import os
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import pw_backend                    # noqa: E402

FS = 48000
FLOOR_DB = -90.0


def _dump():
    """The graph, or an empty one when pw-dump is not installed --
    a tool that dies on a stack trace teaches nothing."""
    import json
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True)
    except FileNotFoundError:
        print("pw-dump is not on PATH -- is PipeWire installed?")
        return []
    try:
        return json.loads(out.stdout)
    except Exception:
        return []


def node_channels(name):
    """The source's own column names -- THE APP'S parser, not a second
    one. Mine split audio.position on commas and handed back "[ AUX0"
    and "AUX15 ]", because the property arrives as SPA JSON text. One
    parser for one question."""
    return list(pw_backend._node_channels(name, _dump()) or [])


def sources():
    """Every capture node in the graph, name and description."""
    out = []
    for o in _dump():
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = ((o.get("info") or {}).get("props") or {})
        if p.get("media.class") != "Audio/Source":
            continue
        out.append((p.get("node.name") or "?",
                    p.get("node.description") or ""))
    return sorted(set(out))


def node_id(name):
    for o in _dump():
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = ((o.get("info") or {}).get("props") or {})
        if p.get("node.name") == name:
            return o.get("id")
    return None


def db(x):
    return FLOOR_DB if x <= 0 else max(FLOOR_DB, 20.0 * math.log10(x))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        found = sources()
        if found:
            print("capture nodes in the graph right now:")
            for n_, d in found:
                print("  %s%s" % (n_, ("   -- " + d) if d else ""))
        return 2
    name = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    names = node_channels(name)
    nid = node_id(name)
    if nid is None:
        # a tool that knows the answer and withholds it is a tool that
        # sends someone back to grep. The name is hard to guess and
        # easy to list.
        print("no such node in the graph: %s" % name)
        found = sources()
        if found:
            print("\ncapture nodes that ARE here:")
            for n_, d in found:
                print("  %s%s" % (n_, ("   -- " + d) if d else ""))
        else:
            print("\nno capture nodes at all -- is the card plugged in?")
        return 1
    n = len(names) or 2
    print("%s -- %d columns, %.0f s" % (name, n, secs))
    if not names:
        print("(the node declares no channel names; assuming stereo)")
    props = ('{ node.name = input-census, node.target = %d, '
             'node.dont-reconnect = true }' % int(nid))
    cmd = ["pw-record", "--raw", "-P", props, "--format", "f32",
           "--rate", str(FS), "--channels", str(n), "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    peaks = [0.0] * n
    want = int(FS * secs) * n * 4
    got = 0
    try:
        while got < want:
            chunk = proc.stdout.read(min(1 << 16, want - got))
            if not chunk:
                break
            got += len(chunk)
            frames = len(chunk) // (4 * n)
            if not frames:
                continue
            vals = struct.unpack("<%df" % (frames * n),
                                 chunk[:frames * n * 4])
            for i, v in enumerate(vals):
                a = -v if v < 0 else v
                c = i % n
                if a > peaks[c]:
                    peaks[c] = a
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
    if not got:
        print("nothing was captured -- is the node still there?")
        return 1
    print()
    for i, p in enumerate(peaks):
        d = db(p)
        bar = "#" * int(max(0, (d + 60.0) / 60.0 * 40))
        label = names[i] if i < len(names) else "col %d" % i
        print("  %-8s %7.1f dBFS  %s" % (label, d, bar))
    live = [names[i] if i < len(names) else str(i)
            for i, p in enumerate(peaks) if db(p) > -60.0]
    print()
    print("carrying signal: %s" % (", ".join(live) if live else "none"))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("LC_ALL", "C")
    sys.exit(main())
