#!/usr/bin/env python3
"""Does a per-column capture gain written through the Route land?

0166 writes one capture column by setting channelVolumes on the
device's Route -- the same object the app already writes to switch a
port. That was proved against fakes and never against the card, and
the field says the number does not arrive. This asks the card.

    tools/route_gain_probe.py --source CM106 --column 0 --to 0.5

It prints the route it found, the command it issues verbatim, and the
value read back afterwards, so the answer is "it landed" or "it did
not" rather than an opinion. Nothing is left behind: the previous
volumes are restored on the way out.
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import pw_backend                      # noqa: E402


def dump():
    r = subprocess.run(["pw-dump"], capture_output=True, text=True)
    return json.loads(r.stdout)


def active_input_route(d):
    """(route record, its props) for a device's active input route."""
    for r in ((d.get("info") or {}).get("params") or {}).get("Route") or []:
        if isinstance(r, dict) and r.get("direction") == "Input":
            return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="substring of the source node name or description")
    ap.add_argument("--column", type=int, default=0)
    ap.add_argument("--to", type=float, default=None,
                    help="cubic volume to write (0..1); by default a "
                         "value deliberately unlike the one already "
                         "there, so a write that does nothing cannot "
                         "pass for a write that landed")
    a = ap.parse_args()

    src = next((s for s in pw_backend.list_sources()
                if a.source.lower() in s["name"].lower()
                or a.source.lower() in s["desc"].lower()), None)
    if src is None:
        print("no source matches %r" % a.source)
        return 1
    print("source : %s" % src["name"])

    route = next((r for r in src.get("routes") or [] if r.get("active")), None)
    if not route:
        print("that source has no active input route")
        return 1
    print("route  : %s  device_id=%s index=%s card_device=%s"
          % (route.get("name"), route.get("device_id"), route.get("index"),
             route.get("card_device")))
    before = route.get("channel_volumes")
    print("before : channelVolumes=%s" % (before,))
    if not before:
        print("the route publishes no channelVolumes -- per-column writing")
        print("cannot apply to this card, and the app must fall back to")
        print("the whole node here.")
        return 1
    if not 0 <= a.column < len(before):
        print("column %d of a %d-channel route" % (a.column, len(before)))
        return 1

    want = list(before)
    to = a.to
    if to is None:
        # NOT a fixed default: 0.5 cubed is 0.125, which happened to be
        # exactly what the card already held, so the first run of this
        # probe reported a write as landed while writing nothing
        cur = before[a.column] ** (1.0 / 3.0)
        to = 0.30 if cur > 0.55 else 0.80
        print("writing %.2f (the column stands at %.2f)" % (to, cur))
    want[a.column] = max(0.0, min(1.0, to)) ** 3
    body = ("{ index: %d, device: %d, props: { channelVolumes: [ %s ] },"
            " save: true }"
            % (int(route["index"]), int(route["card_device"]),
               ", ".join("%.6f" % v for v in want)))
    cmd = ["pw-cli", "set-param", str(route["device_id"]), "Route", body]
    print("\nissuing:\n  %s\n" % " ".join(
        (("'%s'" % c) if " " in c else c) for c in cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    print("exit   : %d" % r.returncode)
    if r.stdout.strip():
        print("stdout : %s" % r.stdout.strip())
    if r.stderr.strip():
        print("stderr : %s" % r.stderr.strip())

    time.sleep(0.5)
    after = None
    for d in dump():
        if d.get("id") == route.get("device_id"):
            cur = active_input_route(d)
            after = (cur or {}).get("props", {}).get("channelVolumes")
    print("\nafter  : channelVolumes=%s" % (after,))
    if after and len(after) == len(want):
        ok = abs(after[a.column] - want[a.column]) < 1e-4
        kept = all(abs(x - y) < 1e-4 for i, (x, y)
                   in enumerate(zip(after, want)) if i != a.column)
        print("column %d landed : %s" % (a.column, "YES" if ok else "NO"))
        print("others unchanged: %s" % ("YES" if kept else "NO"))
    else:
        print("could not read the route back")

    # nothing is left behind
    subprocess.run(["pw-cli", "set-param", str(route["device_id"]), "Route",
                    "{ index: %d, device: %d,"
                    " props: { channelVolumes: [ %s ] }, save: true }"
                    % (int(route["index"]), int(route["card_device"]),
                       ", ".join("%.6f" % v for v in before))],
                   capture_output=True, text=True)
    print("\nrestored the previous volumes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
