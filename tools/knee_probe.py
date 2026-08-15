#!/usr/bin/env python3
"""Walk a capture input's gain in SILENCE and find where it stops
measuring its own converter.

    tools/knee_probe.py --list
    tools/knee_probe.py --source M62 --column 0
    tools/knee_probe.py --source CM106 --steps 10 --dwell 2.0

NOTHING IS PLAYED. The ladder is read off silence, because an input
gain multiplies the signal and the microphone's own noise by the same
factor and so cannot change the ratio between them -- only how far
that package sits above the converter's fixed floor. The signal
cancels out of the question, which is why no signal is needed to
answer it.

The gain is put back on the way out, including on Ctrl-C.

This rides the program's own parts and adds none of its own: the
source list and the fader classification come from pw_backend, the
capture from inmeter (one stream held open for the whole ladder rather
than a process per rung), and the policy from knee. A tool that grew
its own node lookup and its own capture is how three things came to be
written twice here before.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import knee, knee_run, pw_backend     # noqa: E402

cubic_to_db = knee_run.cubic_to_db
db_to_cubic = knee_run.db_to_cubic






def find_source(needle):
    srcs = pw_backend.list_sources()
    if not needle:
        return None, srcs
    low = needle.lower()
    hits = [s for s in srcs
            if low in s["name"].lower() or low in (s["desc"] or "").lower()]
    if len(hits) == 1:
        return hits[0], srcs
    return None, hits or srcs




def main():
    ap = argparse.ArgumentParser(
        description="Find a capture input's gain knee, in silence.")
    ap.add_argument("--list", action="store_true",
                    help="show the sources and what their faders can do")
    ap.add_argument("--source", help="name or description substring")
    ap.add_argument("--column", type=int, default=0,
                    help="which capture column to listen to")
    ap.add_argument("--route", metavar="NAME",
                    help="switch the card to this input port first (a "
                         "substring of its name or description) and put "
                         "the old one back afterwards. A card's ports are "
                         "different measurements: a coupler in the "
                         "microphone jack is not heard on line in")
    ap.add_argument("--lo", type=float, default=-60.0,
                    help="bottom of the ladder in dB (default -60). The "
                         "ladder spans the control on purpose: the flat "
                         "stretch it needs as a reference often lies "
                         "BELOW the unity point, and starting at unity "
                         "cuts the left half of the knee away")
    ap.add_argument("--hi", type=float, default=0.0,
                    help="top of the ladder in dB (default 0)")
    ap.add_argument("--steps", type=int, default=10)
    ap.add_argument("--dwell", type=float, default=1.5,
                    help="seconds of silence per rung")
    ap.add_argument("--no-refine", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="ladder an input whose fader is not analogue "
                         "(there is nothing to find, but the curve may "
                         "still be worth seeing)")
    args = ap.parse_args()

    src, candidates = find_source(args.source)
    if args.list or src is None:
        if src is None and args.source:
            print("no single source matches %r; candidates:" % args.source)
        for s in candidates:
            kind = pw_backend.fader_kind(s.get("routes"), s.get("gain"))
            act = next((r for r in (s.get("routes") or []) if r.get("active")),
                       None)
            print("  %-10s %-46s %s" % (kind, s["name"], s["desc"]))
            for r in s.get("routes") or []:
                print("       %s %-22s %s"
                      % ("*" if r.get("active") else " ",
                         r.get("name"), r.get("description") or ""))
            if act is None and (s.get("routes") or []):
                print("       (no active input route)")
        return 0 if args.list else 1

    was_route = next((r for r in (src.get("routes") or [])
                      if r.get("active")), None)
    if args.route:
        low = args.route.lower()
        want = [r for r in (src.get("routes") or [])
                if low in (r.get("name") or "").lower()
                or low in (r.get("description") or "").lower()]
        if len(want) != 1:
            print("no single input port matches %r; this card has:"
                  % args.route)
            for r in src.get("routes") or []:
                print("   %s %-22s %s" % ("*" if r.get("active") else " ",
                                          r.get("name"),
                                          r.get("description") or ""))
            return 1
        if not want[0].get("active"):
            pw_backend.set_input_route(want[0])
            time.sleep(1.0)
            src, _ = find_source(args.source)
            if src is None:
                print("the source vanished after the port change")
                return 1

    kind = pw_backend.fader_kind(src.get("routes"), src.get("gain"))
    print("source : %s" % src["name"])
    # which jack, spelled out: the classification and the ladder are
    # both per ROUTE, and a card with a microphone port and a line one
    # gives two different answers on the same node
    print("port   : %s"
          % (pw_backend.active_input_route(src["name"]) or "none"))
    print("fader  : %s" % kind)
    if kind != "analog" and not args.force:
        print("\nNothing to search: only an analogue gain has travel above")
        print("unity, and only that travel can buy SNR. An attenuator and")
        print("a software multiplier both belong at full. --force ladders")
        print("it anyway.")
        return 1

    # a source's "channels" is the list of its channel KEYS, not a
    # count -- the same list the window turns into a width with len().
    # A column is a wire, so the names are printed: on a sixteen-column
    # interface the default 0 is a guess and the operator needs to see
    # which wire it is
    names = list(src.get("channels") or [])
    channels = len(names) or 2
    if args.column >= channels:
        print("column %d out of range: this source has %d (%s)"
              % (args.column, channels, ", ".join(names) or "unnamed"))
        return 1

    unity = knee_run.unity_db(src)
    if unity is not None:
        # printed, not obeyed: the flat stretch a knee is measured
        # against often lies BELOW it. It does say which part of the
        # walk the card can only attenuate in
        print("unity   : %.1f dB (cubic %.3f) -- below this the card "
              "attenuates" % (unity, db_to_cubic(unity)))
    before = knee_run.gain_of(src)
    print("columns : %d (%s)" % (channels, ", ".join(names) or "unnamed"))
    print("listening on column %d%s"
          % (args.column,
             " (%s)" % names[args.column] if args.column < len(names) else ""))
    print("current : %s"
          % ("%.3f (%.1f dB)" % (before, cubic_to_db(before) or 0.0)
             if before else "unknown"))
    print("NOTHING IS PLAYED. Keep the room quiet until this finishes.\n")

    def say(rung, done, total):
        ex = getattr(rung, "excess", None)
        print("  %7.1f dB   rms %8.2f   peak %8.2f   (%d blocks, %d/%d)%s"
              % (rung.gain_db, rung.rms_dbfs, rung.peak_dbfs,
                 getattr(rung, "blocks", 0), done, total,
                 "   CAUGHT SOMETHING (+%.1f dB over the median)" % ex
                 if ex is not None and ex > knee.EXCESS_DB else ""))

    rungs = []
    try:
        with knee_run.Walk(src, args.column, dwell=args.dwell) as w:
            ac, peak_db, dc_db = w.listen(1.5)
            if ac is not None:
                print("column   : rms %.2f dBFS about the mean, peak %.2f, "
                      "offset %.2f" % (ac, peak_db, dc_db))
                if dc_db - ac > 10.0:
                    # an offset is not noise and is not measured as one;
                    # the ladder walks the AC part underneath it. But an
                    # offset that size eats headroom and is a fault
                    print("  the column carries a DC OFFSET %.1f dB above "
                          "its own noise." % (dc_db - ac))
                    print("  The ladder measures the noise about it, which "
                          "is the right thing,")
                    print("  but an offset that large is worth chasing on "
                          "its own: it eats")
                    print("  headroom and no gain setting will remove it.")
                elif not knee.noise_like(peak_db, ac):
                    print("  this column is not silent: crest %.1f dB."
                          % (peak_db - ac))
                    print("  Broadband noise carries 11 to 13 dB of crest "
                          "and a sine 3. A few")
                    print("  dB or less is a tone or something clipped, and "
                          "a ladder walked over")
                    print("  a signal describes the CONTROL rather than the "
                          "chain's floor.")
                    print("  Check what is feeding this column and that the "
                          "port above is the")
                    print("  jack you mean. --force ladders it anyway.")
                    if not args.force:
                        return 1
                print("")
            # LISTENED TO FIRST, at his own gain, and only then is the
            # card asked where its control stops -- which hands
            # straight over to the walk. The other order put the
            # column down to the floor, back up to where it was, and
            # straight back down again, and the route watch showed
            # that round trip before anyone thought to look for it
            floor = w.hardware_floor_db(restore=False)
            if floor is not None and floor > args.lo:
                print("floor    : %.1f dB (cubic %.3f) -- the CARD's own "
                      "control stops here;" % (floor, db_to_cubic(floor)))
                print("           below it the graph makes up the "
                      "difference, so the walk starts here\n")
                args.lo = floor
            print("  %-10s %-16s %-11s %s" % ("gain", "", "noise", "peak"))
            for db in knee.plan(args.lo, args.hi, args.steps):
                r = w.visit(db)
                if r is not None:
                    say(r, len(w.rungs), args.steps)
            knee.mark_transients(w.rungs)
            if not args.no_refine:
                fine = knee.refine(w.rungs)
                if fine:
                    print("\nrefining between %.1f and %.1f dB"
                          % (min(fine), max(fine)))
                    for db in fine:
                        r = w.visit(db)
                        if r is not None:
                            say(r, len(w.rungs), args.steps + len(fine))
                    knee.mark_transients(w.rungs)
            rungs = w.rungs
        if w.restored:
            print("\nrestored the gain to %.3f" % w.before)
        elif w.restored is False:
            print("\nCOULD NOT RESTORE the gain; set it back by hand")
    except KeyboardInterrupt:
        print("\nstopped.")
        rungs = rungs or []
    except (RuntimeError, ValueError) as exc:
        print("%s" % exc)
        return 1
    finally:
        # the port belongs to the device, so every application saw the
        # change and every application must see it put back
        if was_route is not None and not was_route.get("active"):
            try:
                pw_backend.set_input_route(was_route)
                print("restored the port to %s" % was_route.get("name"))
            except Exception as exc:                    # noqa: BLE001
                print("COULD NOT RESTORE the port: %s" % exc)

    if any(r.suspect for r in rungs):
        print("\nrungs marked suspect caught a transient -- something made")
        print("a noise during them, and they took no part in the fit.")

    v = knee.verdict(rungs)
    print("\n%s" % _draw(v.rungs))
    if v.segments:
        print("  the curve reads, in order:")
        for seg in v.segments:
            print("    %-7s %+6.1f .. %+6.1f dB   %5.2f dB per dB"
                  % (seg.kind, seg.lo, seg.hi, seg.slope))
        print("  scatter between rungs: %.2f dB" % (v.scatter or 0.0))
    if v.software_below is not None:
        print("  everything below %.1f dB is gain made up in SOFTWARE: it"
              % v.software_below)
        print("  scales the converter's floor along with the signal, which "
              "is why")
        print("  it is exactly slope one, and it says nothing about the "
              "chain.")
    print("VERDICT: %s" % v.kind)
    if v.knee_db is not None:
        print("  knee at about %.1f dB (cubic %.3f)"
              % (v.knee_db, db_to_cubic(v.knee_db)))
    if v.work_db is not None:
        print("  suggested working gain %.1f dB (cubic %.3f)"
              % (v.work_db, db_to_cubic(v.work_db)))
    print("  %s" % v.note)
    return 0


def _draw(rungs, width=44):
    good = [r for r in rungs if r.rms_dbfs > -400]
    if len(good) < 2:
        return ""
    lo = min(r.rms_dbfs for r in good)
    hi = max(r.rms_dbfs for r in good)
    span = max(hi - lo, 1.0)
    out = []
    for r in sorted(rungs, key=lambda x: x.gain_db):
        n = int(round((r.rms_dbfs - lo) / span * (width - 1)))
        out.append("  %7.1f dB %9.2f %s %s"
                   % (r.gain_db, r.rms_dbfs, "!" if r.suspect else " ",
                      " " * n + "#"))
    return "\n".join(out)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
