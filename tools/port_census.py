#!/usr/bin/env python3
"""Every card port a picker can see, and the four flags that decide
what it does with it.

    tools/port_census.py            # both directions
    tools/port_census.py --in
    tools/port_census.py --out

One block per node: what the graph calls it, which card it belongs to,
which port it is listening on or playing through right now, and then
every port of that card with the flags the listing reads.

    mine        the port belongs to the node's own card device
    reachable   the loaded profile carries it, so it needs no door
    available   PipeWire says a jack is behind it (`unknown` counts)
    active      it is the one in use right now

A row becomes a DOOR when it is offerable and NOT reachable, so those
two columns are the ones to read when a picker offers something the
desktop does not, or the other way round. `active` is the one to read
when a row shows its node's description instead of a port name: no
active port, nothing to show.

Runs from any directory. Nothing is written anywhere.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import pw_backend as pw              # noqa: E402


def flags(r):
    out = []
    for k in ("mine", "reachable", "available", "active"):
        v = r.get(k)
        out.append("%s=%s" % (k, "?" if v is None else
                              ("yes" if v else "no")))
    return "  ".join(out)


def block(entry, routes):
    print("%s" % (entry.get("desc") or entry["name"]))
    print("    node   %s" % entry["name"])
    print("    card   %s (%s)" % (entry.get("card_desc"),
                                  entry.get("card")))
    print("    port   %s" % (entry.get("port") or
                             "-- none active --"))
    if not routes:
        print("    (this card declares no ports)")
    for r in routes:
        door = ("DOOR" if (not r.get("reachable", True)
                           and r.get("available", True)) else "    ")
        print("    %s %-28s %s" % (door, r.get("description")
                                   or r.get("name"), flags(r)))
    print()


def main():
    want_in = "--out" not in sys.argv
    want_out = "--in" not in sys.argv
    dump = pw.pw_dump()
    if want_in:
        print("=== capture ===\n")
        for s in pw.list_sources(dump):
            block(s, pw.card_input_ports(s["name"], dump))
    if want_out:
        print("=== playback ===\n")
        for s in pw.list_sinks(dump):
            block(s, pw.card_output_ports(s["name"], dump))
    return 0


if __name__ == "__main__":
    sys.exit(main())
