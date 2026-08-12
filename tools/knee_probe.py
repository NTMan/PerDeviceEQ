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
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import inmeter, knee, pw_backend      # noqa: E402


def cubic_to_db(cubic):
    """wpctl speaks the cubic volume; a route's own props are linear,
    and 20*log10 of those is what pactl prints. linear = cubic**3, so
    the two agree at 60*log10(cubic)."""
    if not cubic or cubic <= 0.0:
        return None
    return 60.0 * math.log10(min(1.0, float(cubic)))


def db_to_cubic(db):
    return min(1.0, 10.0 ** (float(db) / 60.0))


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


def read_back(node_name):
    """The gain the card actually took, not the one we asked for. A
    hardware control quantises to its own steps, and an axis made of
    requests rather than of readings carries that error into the fit."""
    for s in pw_backend.list_sources():
        if s["name"] == node_name:
            return (s.get("gain") or (None, None))[0]
    return None


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

    # Below unity the control attenuates, and attenuation cannot move
    # the input relative to the converter's floor -- so a knee, if the
    # chain has one, is at or above the point where the card gives
    # unity gain. A CM106 laddered from -60 spent six of its eight
    # rungs down in the attenuation range and left two for the part
    # that matters. The route publishes that point as volumeBase,
    # linear, and 20*log10 of it is the same axis this walks.
    active = next((r for r in (src.get("routes") or []) if r.get("active")),
                  None)
    base = (active or {}).get("volume_base")
    lo = args.lo
    if base and 0.0 < base < 1.0:
        # printed, not used as a boundary: it tells you which part of
        # the walk the card only attenuates in, and the software
        # stretch usually ends near it
        print("unity   : %.1f dB (cubic %.3f) -- below this the card "
              "attenuates" % (20.0 * math.log10(base), base ** (1 / 3.0)))

    before = (src.get("gain") or (None, None))[0]
    print("columns : %d (%s)" % (channels, ", ".join(names) or "unnamed"))
    print("listening on column %d%s"
          % (args.column,
             " (%s)" % names[args.column] if args.column < len(names) else ""))
    print("current : %s"
          % ("%.3f (%.1f dB)" % (before, cubic_to_db(before) or 0.0)
             if before else "unknown"))
    print("NOTHING IS PLAYED. Keep the room quiet until this finishes.\n")

    meter = inmeter.InputMeter()
    rungs = []

    def restore():
        if before is not None:
            try:
                pw_backend.set_gain(src["id"], before)
                print("\nrestored the gain to %.3f" % before)
            except Exception as exc:                    # noqa: BLE001
                print("\nCOULD NOT RESTORE the gain: %s" % exc)
        # the port belongs to the device, so every application saw the
        # change and every application must see it put back
        if was_route is not None and not was_route.get("active"):
            try:
                pw_backend.set_input_route(was_route)
                print("restored the port to %s" % was_route.get("name"))
            except Exception as exc:                    # noqa: BLE001
                print("COULD NOT RESTORE the port: %s" % exc)

    def visit(db):
        pw_backend.set_gain(src["id"], db_to_cubic(db))
        time.sleep(0.35)                     # let the card take it
        got = read_back(src["name"])
        real = cubic_to_db(got)
        # AVERAGE the dwell, do not sample the end of it. One block is
        # 43 ms, and a noise floor read off 43 ms wanders by more than
        # a dB: two runs of this ladder disagreed by 3.8 dB on the top
        # rung and the disagreement flipped a verdict. Power averages,
        # peaks take the highest.
        deadline = time.time() + args.dwell
        power, count, top, seen = 0.0, 0, None, None
        while time.time() < deadline:
            # the meter publishes a block every 43 ms and only its
            # LATEST; polling at 150 ms caught ten of the thirty-five
            # a 1.5 s dwell contains, and the third that was thrown
            # away is the residual scatter. Poll faster than the
            # blocks arrive and the duplicate check below drops the
            # repeats
            time.sleep(0.02)
            rms = meter.latest_rms()
            peak = meter.latest()
            if rms is None or peak is None:
                continue
            v = rms[args.column]
            if v == seen:
                continue                     # the same block twice
            seen = v
            power += 10.0 ** (v / 10.0)
            count += 1
            p = peak[args.column]
            top = p if top is None else max(top, p)
        if not count:
            return None
        mean_db = 10.0 * math.log10(power / count)
        r = knee.Rung(real if real is not None else db, mean_db,
                      peak_dbfs=top, raw=got)
        rungs.append(r)
        print("  %7.1f dB (asked %+6.1f)   rms %8.2f   peak %8.2f   "
              "(%d blocks)"
              % (r.gain_db, db, r.rms_dbfs, r.peak_dbfs, count))
        return r

    try:
        meter.start(src["id"], channels)
        time.sleep(0.6)
        if not meter.alive():
            print("the capture did not start; is the source in use?")
            return 1
        # before the ladder: is this silence? A crest factor near zero
        # is a DC offset or a tone, not a floor, and a ladder over a
        # signal measures the control rather than the chain
        settle = time.time() + 1.5
        first = None
        while time.time() < settle:
            time.sleep(0.2)
            rms, peak, dc = (meter.latest_rms(), meter.latest(),
                             meter.latest_dc())
            if rms is not None and peak is not None and dc is not None:
                first = (rms[args.column], peak[args.column],
                         dc[args.column])
        if first is not None:
            ac, peak_db, dc_db = first
            print("column   : rms %.2f dBFS about the mean, peak %.2f, "
                  "offset %.2f" % (ac, peak_db, dc_db))
            if dc_db - ac > 10.0:
                # an offset is not noise and is not measured as one; the
                # ladder walks the AC part underneath it. But an offset
                # this size eats headroom and is a fault of its own
                print("  the column carries a DC OFFSET %.1f dB above its "
                      "own noise." % (dc_db - ac))
                print("  The ladder measures the noise about it, which is "
                      "the right thing,")
                print("  but an offset that large is worth chasing on its "
                      "own: it eats")
                print("  headroom and no gain setting will remove it.")
            elif not knee.noise_like(peak_db, ac):
                print("  this column is not silent: crest %.1f dB."
                      % (peak_db - ac))
                print("  Broadband noise carries 11 to 13 dB of crest and a "
                      "sine 3. A few")
                print("  dB or less is a tone or something clipped, and a "
                      "ladder walked over")
                print("  a signal describes the CONTROL rather than the "
                      "chain's floor.")
                print("  Check what is feeding this column and that the port "
                      "above is the")
                print("  jack you mean. --force ladders it anyway.")
                if not args.force:
                    return 1
            print("")
        print("  %-10s %-16s %-11s %s" % ("gain", "", "noise", "peak"))
        for db in knee.plan(lo, args.hi, args.steps):
            visit(db)
        knee.mark_transients(rungs)
        if not args.no_refine:
            fine = knee.refine(rungs)
            if fine:
                print("\nrefining between %.1f and %.1f dB"
                      % (min(fine), max(fine)))
                for db in fine:
                    visit(db)
                knee.mark_transients(rungs)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        meter.stop()
        restore()

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
