#!/usr/bin/env python3
"""Watch the app's own level hunt, with room for the whole line.

THE SAME PIPELINE THE WINDOW DRIVES, and nothing else. MeasureSession
with auto_level, the AutoLevel policy inside it, measure_build's judge
of what a distortion figure is -- this tool adds a device lookup and a
printer, and that is deliberately all it adds.

    tools/level_probe.py --sink "Liberty" --mic CM106 --column 0

It once had a second mode that walked its own ladder, which is how the
level curve was first read and how the rule was settled. That mode is
gone. A debugging tool that runs a different algorithm from the thing
it debugs is not debugging that thing, and having the two side by side
invites comparing their answers as though a difference meant a fault.
The ladder is in the history if a device ever needs surveying again.

Nothing is put back: the level is where the hunt left it, as with
every walk in this project.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import measure_build as mb              # noqa: E402
from perdeviceeq import level_run                        # noqa: E402
from perdeviceeq import pw_backend                       # noqa: E402

# bands as the project's own thd_at reads them: a centre and a width in
# octaves. The midband is what the hunt steers by -- what makers publish
# and where the ear is most sensitive to distortion
BANDS = ((63.2, 3.32, "20-200"), (632.5, 3.32, "200-2k"),
         (4472.1, 2.32, "2k-10k"))

def band(take, f0, frac):
    """(percent, clamped) for one band, through measure_build's judge.

    The tool does NOT have an opinion about distortion. thd_at decides
    what a figure is and whether the rig can see under it, here and in
    the session alike.
    """
    return mb.thd_at(getattr(take, "freq_hz", None),
                     getattr(take, "thd_db", None),
                     getattr(take, "thd_noise_db", None),
                     f0=f0, frac=frac)


def word(got):
    if got is None:
        return "-"
    pct, clamped = got
    return ("<=%s" % mb.pct_word(pct)) if clamped else mb.pct_word(pct)


def find(entries, needle):
    n = needle.lower()
    return next((e for e in entries
                 if n in e["name"].lower()
                 or n in (e.get("desc") or "").lower()), None)



def watch_the_hunt(sink, src, width, column, play=None):
    """Drive the same search the window drives, and report it."""
    print("  %-6s %-10s %-9s %-7s %-11s %-7s %s"
          % ("step", "phase", "peak", "SNR", "THD@1k", "margin", "level"))

    def said(p):
        thd = ("n/a" if p.thd_pct is None else
               "%s%s%%" % ("<=" if p.thd_bound else "",
                           mb.pct_word(p.thd_pct)))
        print("  %-6d %-10s %-9.1f %-7s %-11s %-7s %.0f%%"
              % (p.step, p.phase, p.peak_dbfs,
                 "n/a" if p.snr_db is None else "%.1f" % p.snr_db,
                 thd,
                 "n/a" if p.margin_db is None else "%+.1f dB" % p.margin_db,
                 100 * p.volume))

    vol, probes = level_run.hunt(
        sink, {"name": pw_backend.entry_node(src["name"]),
               "id": src["id"]},
        width, sink_name=sink["name"], analyze=column, on_probe=said,
        play_map=play)
    print("\n  ANSWER: %.0f%%" % (100 * vol))
    return vol
    if probes:
        last = probes[-1]
        print("  last sweep: peak %.1f dBFS, SNR %s"
              % (last.peak_dbfs,
                 "n/a" if last.snr_db is None else "%.1f dB" % last.snr_db))
    print("\n  the number is the whole product: nothing was left on the "
          "hardware,")
    print("  because a moratorium takes the measurement volume as a "
          "parameter.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sink", required=True)
    ap.add_argument("--mic", required=True)
    ap.add_argument("--column", type=int, default=0)
    ap.add_argument("--leave", action="store_true",
                    help="leave the sink AT the level the hunt found. "
                         "Without it the moratorium puts the volume "
                         "back where it was, which is what happens "
                         "during a real measurement")
    ap.add_argument("--play", metavar="POS",
                    help="channel position to play into, e.g. FL or FR. "
                         "PipeWire routes a mono sweep by NAME, so this "
                         "sounds ONE speaker; without it the sink "
                         "decides, and a stereo pair playing together "
                         "combs at the microphone. --column is about "
                         "the MICROPHONE and settles nothing here")
    a = ap.parse_args()

    sink = find(pw_backend.list_sinks(), a.sink)
    src = find(pw_backend.list_sources(), a.mic)
    if sink is None or src is None:
        print("no %s matches" % ("output" if sink is None else "microphone"))
        return 1
    width = pw_backend.source_width(src)
    if a.column >= width:
        print("that source has %d capture channels" % width)
        return 1
    print("output : %s" % sink["name"])
    print("mic    : %s  channel %d of %d\n"
          % (src["name"], a.column, width))
    if a.play:
        print("playing: %s only" % a.play)
    print("SWEEPS WILL PLAY. Keep the room quiet and do not move the rig.\n")

    try:
        vol = watch_the_hunt(sink, src, width, a.column, a.play)
        if a.leave and vol is not None:
            # THE MORATORIUM PUTS THE VOLUME BACK. Every probe sets the
            # measurement level and restores it on the way out, so a
            # hunt ends with the knob exactly where it started -- the
            # docstring's "the level is where the hunt left it" is
            # about the ANSWER being the whole product, not about the
            # sink. A second tool that means to start where the hunt
            # settled therefore starts somewhere else entirely: his
            # map read 94% after a hunt that answered 74%, found less
            # than one step of room below the capture ceiling, and
            # stopped after a single rung.
            pw_backend.set_sink_volume(sink["id"], vol)
            print("  and the sink is left at %.0f%%" % (100 * vol))
    except KeyboardInterrupt:
        print("\nstopped.")
    except (RuntimeError, ValueError) as exc:
        print("%s" % exc)
        return 1
    print("\nthe answer is the whole product: nothing is left on the "
          "hardware,\nand the sink's own volume is back where it was "
          "unless --leave said otherwise")
    return 0


if __name__ == "__main__":
    sys.exit(main())
