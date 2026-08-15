#!/usr/bin/env python3
"""Watch what a card's Route is actually holding, and what the graph is
making up in software.

Polling this by hand answers the question once. Run it beside a ladder
and it answers it continuously, printing only when something changes,
so the output is the sequence of states rather than a wall of repeats.

    tools/route_watch.py --source CM106

The last column is the one worth watching: channelVolumes divided by
softVolumes is where the HARDWARE control is standing. Below the
card's own floor that number stops moving and the graph makes up the
rest -- five rungs of a ladder on a CM106 sit down there, and they
measure PipeWire's multiplier rather than the chain.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import pw_backend                      # noqa: E402


def routes_of(device_id):
    r = subprocess.run(["pw-dump"], capture_output=True, text=True)
    for o in json.loads(r.stdout or "[]"):
        if o.get("id") != device_id:
            continue
        for rt in ((o.get("info") or {}).get("params") or {}).get("Route") or []:
            if isinstance(rt, dict) and rt.get("direction") == "Input":
                p = rt.get("props") or {}
                return (rt.get("name"), p.get("channelVolumes"),
                        p.get("softVolumes"))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="substring of the source's name or description")
    ap.add_argument("--column", type=int, default=0)
    ap.add_argument("--period", type=float, default=0.25)
    a = ap.parse_args()

    src = next((s for s in pw_backend.list_sources()
                if a.source.lower() in s["name"].lower()
                or a.source.lower() in s.get("desc", "").lower()), None)
    if src is None:
        print("no source matches %r" % a.source)
        return 1
    route = next((r for r in src.get("routes") or [] if r.get("active")), None)
    if not route:
        print("that source has no active input route")
        return 1
    dev = route["device_id"]
    print("source : %s" % src["name"])
    print("device : %s  route %s\n" % (dev, route.get("name")))
    print("  %-10s %-10s %-10s %s"
          % ("route", "soft", "hardware", "= route / soft, the card itself"))

    last = None
    try:
        while True:
            got = routes_of(dev)
            if got:
                _, cv, sv = got
                if cv and a.column < len(cv):
                    c = float(cv[a.column])
                    s = float((sv or [1.0] * len(cv))[a.column]) or 1.0
                    hw = c / s
                    key = (round(c, 6), round(s, 6))
                    if key != last:
                        last = key
                        print("  %-10.6f %-10.4f %-10.6f  %6.1f dB  %5.1f%%"
                              % (c, s, hw,
                                 20.0 * math.log10(hw) if hw > 0 else -999.0,
                                 100.0 * (hw ** (1 / 3.0))))
            time.sleep(a.period)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
