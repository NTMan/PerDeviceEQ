#!/usr/bin/env python3
"""Which capture columns of a source actually carry anything.

    python3 tools/input_census.py <source-node-name> [seconds] [--plain]

On a terminal it draws LIVE bars, one per column, until you press q:
knock on a capsule and the column that moves is the column. Two
numbers per row -- what is there now, and the loudest thing since the
last reset (r), which is what answers the question after the knock is
over. Piped, or with --plain, it records for N seconds instead and
prints a table, which is the form a pull request can be shown.

Why this exists: his M62 declares sixteen capture columns and there is
no document anywhere saying what they carry. The measurement window's
meter answers the same question live; this answers it as a TABLE, which
is what an ALSA UCM pull request can be shown.

Runs from any directory. Nothing is written anywhere.
"""

import math
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import inmeter                       # noqa: E402
from perdeviceeq import pw_backend                    # noqa: E402

FLOOR_DB = inmeter.FLOOR_DB


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


def bar(d, width=40, lo=-60.0):
    n = int(max(0.0, min(1.0, (d - lo) / (0.0 - lo))) * width)
    return "#" * n


def table(names, peaks, secs):
    print()
    for i, d in enumerate(peaks):
        label = names[i] if i < len(names) else "col %d" % i
        print("  %-8s %7.1f dBFS  %s" % (label, d, bar(d)))
    live = [names[i] if i < len(names) else str(i)
            for i, d in enumerate(peaks) if d > -60.0]
    print()
    print("carrying signal: %s" % (", ".join(live) if live else "none"))


def watch(meter, names, n):
    """Live bars until q. Knock on a capsule and read the column.

    Two numbers per row: what is there NOW and the loudest thing seen
    since the last reset. The hold is the one that answers "which
    column did that knock land on" after the knock is over.
    """
    import curses

    def run(scr):
        curses.curs_set(0)
        scr.nodelay(True)
        hold = [FLOOR_DB] * n
        shown = [FLOOR_DB] * n
        while True:
            k = scr.getch()
            if k in (ord("q"), 27):
                return hold
            if k in (ord("r"), ord("R")):
                hold = [FLOOR_DB] * n
            live = meter.latest()
            for i in range(n):
                d = live[i] if live and i < len(live) else FLOOR_DB
                shown[i] = d if d >= shown[i] else max(d, shown[i] - 3.0)
                if d > hold[i]:
                    hold[i] = d
            scr.erase()
            h, w = scr.getmaxyx()
            width = max(10, min(50, w - 34))
            scr.addnstr(0, 0, "knock on a capsule -- the column that "
                        "moves is the column", w - 1)
            scr.addnstr(1, 0, "q quit    r reset the hold", w - 1)
            for i in range(min(n, h - 4)):
                label = names[i] if i < len(names) else "col %d" % i
                row = "  %-8s %7.1f  %-*s  hold %7.1f" % (
                    label, shown[i], width, bar(shown[i], width),
                    hold[i])
                scr.addnstr(3 + i, 0, row, w - 1)
            scr.refresh()
            time.sleep(0.06)

    return curses.wrapper(run)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        found = sources()
        if found:
            print("capture nodes in the graph right now:")
            for n_, d in found:
                print("  %s%s" % (n_, ("   -- " + d) if d else ""))
        return 2
    args = [a for a in sys.argv[1:] if a != "--plain"]
    plain = "--plain" in sys.argv or not sys.stdout.isatty()
    name = args[0]
    secs = float(args[1]) if len(args) > 1 else 10.0
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
    meter = inmeter.InputMeter()
    try:
        meter.start(nid, n)
    except Exception as e:
        print("could not open the capture: %s" % e)
        return 1
    try:
        if plain:
            print("%s -- %d columns, %.0f s" % (name, n, secs))
            if not names:
                print("(the node declares no channel names; "
                      "assuming stereo)")
            peaks = [FLOOR_DB] * n
            end = time.monotonic() + secs
            while time.monotonic() < end:
                live = meter.latest()
                if live:
                    peaks = [max(a, b) for a, b in zip(peaks, live)]
                time.sleep(0.05)
            table(names, peaks, secs)
        else:
            peaks = watch(meter, names, n)
            print("%s -- %d columns" % (name, n))
            table(names, peaks, secs)
    finally:
        meter.stop()
    return 0


if __name__ == "__main__":
    os.environ.setdefault("LC_ALL", "C")
    sys.exit(main())
