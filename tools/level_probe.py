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
from perdeviceeq import measure_session as ms            # noqa: E402
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



def config_for(sink, src, width, hunt=True):
    return ms.SessionConfig(
        sink=sink["name"], source=pw_backend.entry_node(src["name"]),
        channels=width, auto_level=hunt, mute_others=True,
        device=sink.get("desc"), start_volume=None)


def watch_the_hunt(cfg, column, guard=14):
    """Drive the session's own auto-level and report every probe."""
    print("  %-6s %-10s %-9s %-7s %-11s %s"
          % ("step", "phase", "peak", "SNR", "THD@1k", "level"))
    with ms.MeasureSession(cfg) as s:
        for _ in range(guard):
            out = s.take(0, analyze=column)
            if out.kind == "level_probe":
                lv = out.level or {}
                pct = lv.get("thd_pct")
                thd = ("n/a" if pct is None else
                       "%s%s%%" % ("<=" if lv.get("thd_bound") else "",
                                   mb.pct_word(pct)))
                print("  %-6s %-10s %-9.1f %-7s %-11s %.0f%% -> %.0f%%"
                      % ("%d/%d" % (lv.get("step", 0),
                                    lv.get("max_steps", 0)),
                         lv.get("phase", "?"),
                         lv.get("peak_dbfs", float("nan")),
                         ("%.1f" % lv["snr_db"]) if lv.get("snr_db")
                         is not None else "n/a",
                         thd,
                         100 * lv.get("volume_from", 0),
                         100 * lv.get("volume_to", 0)))
                continue
            gave_up = None
            if out.kind == "level_stuck":
                lv = out.level or {}
                gave_up = lv.get("why") or "level stuck"
                out = s.accept_level()
            if out.kind == "take" and out.take is not None:
                t = out.take
                # ONE WORD FOR ONE OUTCOME. Printing "gave up" and then
                # "settled" about the same run said two opposite things
                # in four lines.
                print("\n  %s at %.0f%%, recorded peak %.1f dBFS, "
                      "SNR %.1f dB"
                      % ("STOPPED SHORT" if gave_up else "SETTLED",
                         100 * (s._v_cur or 0), t.peak_dbfs, t.snr_db))
                if gave_up:
                    print("    the hunt could not reach its target: %s"
                          % gave_up)
                for f0, frac, name in BANDS:
                    print("    %-8s %s%%" % (name, word(band(t, f0, frac))))
                for n in out.notes or []:
                    print("    note: %s" % n)
                return
            print("  unexpected outcome: %s" % out.kind)
            return
    print("\n  the hunt did not settle within %d sweeps" % guard)





def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sink", required=True)
    ap.add_argument("--mic", required=True)
    ap.add_argument("--column", type=int, default=0)
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
    print("SWEEPS WILL PLAY. Keep the room quiet and do not move the rig.\n")

    cfg = config_for(sink, src, width, hunt=True)
    try:
        watch_the_hunt(cfg, a.column)
    except KeyboardInterrupt:
        print("\nstopped.")
    except (RuntimeError, ValueError) as exc:
        print("%s" % exc)
        return 1
    print("\nthe level is where the hunt left it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
