#!/usr/bin/env python3
"""What the level meter's tap is actually wired to.

Prints the sink's channel positions, the meter stream's channel
positions, and every link feeding the stream -- which monitor port
lands on which column of the captured frames. That is the only thing
that decides both the bar labels and which EQ chain is applied where.

Run with the app open and audio playing:  python3 meter_link_probe.py
"""
import json
import subprocess
import sys


def dump():
    r = subprocess.run(["pw-dump"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("pw-dump failed: %s" % r.stderr[-200:])
    return json.loads(r.stdout)


def props(o):
    return ((o.get("info") or {}).get("props") or {}) or (o.get("props") or {})


def main():
    objs = dump()
    nodes, ports, links = {}, [], []
    for o in objs:
        t = (o.get("type") or "").split(":")[-1]
        if t.endswith("Node"):
            nodes[o["id"]] = o
        elif t.endswith("Port"):
            ports.append(o)
        elif t.endswith("Link"):
            links.append(o)

    print("== nodes of interest ==")
    interesting = {}
    for nid, n in nodes.items():
        p = props(n)
        nm = p.get("node.name") or ""
        desc = p.get("node.description") or ""
        if "per-device-eq-meter" in nm or "M62" in nm or "M62" in desc:
            interesting[nid] = nm or desc
            print("  id=%s  %s" % (nid, nm or desc))
            print("      class=%s  position=%s"
                  % (p.get("media.class"), p.get("audio.position")))

    by_id = {}
    for pt in ports:
        p = props(pt)
        by_id[pt["id"]] = (int(p.get("node.id", -1)),
                           p.get("port.direction"),
                           p.get("audio.channel") or p.get("port.name"),
                           p.get("port.id"))

    print("\n== ports of the meter stream, in port order ==")
    mids = [i for i, nm in interesting.items() if "meter" in (nm or "")]
    for pid, (nid, direction, chan, order) in sorted(
            by_id.items(), key=lambda kv: int(kv[1][3] or 0)):
        if nid in mids:
            print("  column %s: port %s  channel=%s  dir=%s"
                  % (order, pid, chan, direction))

    print("\n== links into the meter stream ==")
    rows = []
    for lk in links:
        p = props(lk)
        try:
            op, ip = int(p["link.output.port"]), int(p["link.input.port"])
        except (KeyError, TypeError, ValueError):
            continue
        if by_id.get(ip, (None,))[0] not in mids:
            continue
        src = by_id.get(op, (None, None, "?", "?"))
        dst = by_id.get(ip, (None, None, "?", "?"))
        srcnode = nodes.get(src[0])
        rows.append((int(dst[3] or 0),
                     "  column %s  <-  %s of %s"
                     % (dst[3], src[2],
                        props(srcnode).get("node.name") if srcnode
                        else "node %s" % src[0])))
    for _o, line in sorted(rows):
        print(line)
    if not rows:
        print("  (none -- the tap is not linked to anything)")


if __name__ == "__main__":
    main()
