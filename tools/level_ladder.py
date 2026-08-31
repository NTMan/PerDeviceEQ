#!/usr/bin/env python3
"""Walk the playback level a decibel at a time and report what each
rung reads, with the hunt's own brake on the way up.

    tools/level_ladder.py --sink "Playback 1/2" --mic "IN 1"

WHY IT EXISTS, and why it is not a second hunt. The search steers by a
yes/no -- is the distortion figure a measurement or a bound -- and
stops at the first rung that clears it. On a very quiet chain that
happens at the first probe, so the level it picks carries the smallest
margin the bar allows. Whether one more decibel would cost anything is
a question about the SLOPE of the device's own distortion curve, and
nobody has measured that slope on a chain this clean. This tool
measures it: stated rungs, one take each, no decision at the end.

It decides exactly two things, and neither is invented here.

WHEN TO STOP CLIMBING. `AutoLevel.verdict` says "loud" and the climb
ends, the same sentence the search obeys, plus a peak ceiling of its
own that is reached earlier. **Nothing is ever raised without asking
the previous rung first.**

WHEN TO REFUSE TO CLIMB AT ALL. Playing a decibel louder must arrive
as about a decibel louder. If it does not, the microphone is not in
the path being measured, and every further rung is a sweep into
nothing. This is not a hypothetical: a whole evening's ladders were
run with the coupler plugged into a different card, and the eight
rungs came back with distortion figures between six and a hundred and
seventy per cent. The tool said "channel 0 of 2" in small print and
went on measuring the room.

AND THE THING THAT FOLLOWS IS THE RESPONSE, NOT THE PEAK. His CM106
carries a DC offset some thirty-six decibels above its own noise, so
the peak sample belongs to that offset and not to the sweep: seven
decibels of level went by with the peak parked at -39, on a chain
whose microphone had just been confirmed by knocking on the capsule.
The sweep's own level is the response the deconvolution recovers,
which cannot contain a constant, and that is what is watched here.
The peak stays in the table, because it is what the search's brake
reads and what clipping shows up in.

BEFORE RUNNING IT: the sweep plays into whatever is on the coupler.
Bring the card's own analogue output level down first, and start below
where you listen -- the first rung is played at `--start` and nothing
is climbed until its peak has been read.

Runs from any directory. The level is left where the ladder stopped,
as with every walk in this project.
"""

import argparse
import json
import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from perdeviceeq import measure_build as mb              # noqa: E402
from perdeviceeq import measure_core as mc               # noqa: E402
from perdeviceeq import level_run                        # noqa: E402
from perdeviceeq import pw_backend                       # noqa: E402
from perdeviceeq.sweep_io import run_take, write_sweep_files  # noqa: E402

# a ceiling of this tool's own, reached well before the search's -2.0:
# the question here lives between the crossing and the peak floor, and
# there is nothing to learn above it worth the risk to an earphone
STOP_PEAK_DBFS = -6.0

# How much of a level rise must arrive at the capture before the walk
# is believed, as a least-squares slope of peak against level. Half is
# generous -- a chain in the path tracks nearly one for one -- and it
# leaves room for a device whose own compression eats some of the rise.
#
# THE FIRST RUNG IS DROPPED FROM THE FIT: a chain wakes up on it. His
# bluetooth walk jumped ten decibels from the first rung to the second
# and then stood still for six more, and a fit including that jump
# reads +3.1 and walks on happily. Dropping it, the same walk reads
# +0.05 and stops, while both good wired walks read exactly +1.00
# either way.
TRACK_MIN = 0.5
TRACK_AFTER = 5         # rungs to collect; the fit uses the last four

# HOW SMALL A STEP CAN STILL BE READ. The answer to "did the rig give
# what we asked" is the difference of two sweeps, and two sweeps of
# the same rig at the same level do not agree perfectly. Measured on
# three of his rigs in the region where they answer honestly: asked
# 2.0 dB, the per-frequency answer ran 1.8 to 2.2 on the subwoofer,
# 2.0 to 2.0 on the Adams, and asked 3.0 it ran 3.0 to 3.1 on a
# Tanchjim in the coupler. Two tenths of a decibel of scatter, so a
# 2 dB step is read with room to spare and a 1 dB step is not. The
# search stops halving here and reports the ceiling as a bracket.
MIN_READABLE_STEP = 2.0


def find(entries, needle):
    n = needle.lower()
    return next((e for e in entries
                 if n in e["name"].lower()
                 or n in (e.get("desc") or "").lower()), None)


def rung(sink, src, name, wav, duration, width, fs, sweep, freqs,
         column, v, at_hz=1000.0, keep=None, index=0):
    """One sweep at volume `v`, read the way the session reads it.

    `keep` is a directory to write the capture into, so the walk can
    be re-read afterwards with a question it was not asked at the
    time. It exists because a level walk is EXPENSIVE to redo: the
    volume has to be moved in exact steps, and a hand on a slider is
    not a step -- ask a person to raise it "one notch" five times and
    the analysis afterwards will be about the person, not the rig.
    """
    back = pw_backend.backend()
    back.moratorium_begin(name, v, mute_others=True)
    try:
        data, _ = run_take(sink, src, wav, duration, width, fs,
                           verify=False)
    finally:
        back.moratorium_end()
    chan = np.asarray(data)[:, min(column, data.shape[1] - 1)]
    if keep:
        import soundfile as sf
        os.makedirs(keep, exist_ok=True)
        sf.write(os.path.join(keep, "rung%02d.wav" % index),
                 np.asarray(data), fs)
        with open(os.path.join(keep, "rung%02d.json" % index), "w") as fh:
            json.dump({"index": index, "volume_cubic": float(v),
                       "n_samples": sweep.n_samples, "fs": sweep.fs,
                       "f_start": sweep.f_start, "f_end": sweep.f_end,
                       "column": column}, fh, indent=1)
    peak = float(np.max(np.abs(chan))) if chan.size else 0.0
    peak_db = 20.0 * math.log10(peak) if peak > 0 else -120.0
    clipped = peak >= 0.999
    got = mc.analyze_take(chan, sweep, freqs)
    # the sweep's own level: the median of the recovered response
    # across the band the coupler can be trusted in. A constant
    # offset cannot survive the deconvolution, so this follows the
    # level where the peak sample does not.
    band = (np.asarray(freqs) >= 100.0) & (np.asarray(freqs) <= 8000.0)
    mag = np.asarray(got.mag_db, float)[band]
    mag = mag[np.isfinite(mag)]
    resp = float(np.median(mag)) if mag.size else None
    at = mb.thd_at(freqs, got.thd_db, got.thd_noise_db, f0=at_hz)
    margin = mb.thd_margin_db(freqs, got.thd_db, got.thd_noise_db,
                              f0=at_hz)
    snr = (float(got.snr_db) if got.snr_db is not None
           and math.isfinite(float(got.snr_db)) else None)
    # the response against the take's OWN noise, per frequency: a
    # question about a rung is only worth asking where the rung was
    # heard at all
    over = (np.asarray(got.mag_db, float)
            - (float(got.noise_dbfs) - float(got.signal_dbfs)))
    return (peak_db, resp, snr, clipped, at, margin,
            np.asarray(got.mag_db, float), over)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sink", required=True)
    ap.add_argument("--mic", required=True)
    ap.add_argument("--column", type=int, default=None,
                    help="which capture column to read; required when "
                         "the source has more than one")
    ap.add_argument("--start", type=float, default=0.15,
                    help="cubic volume of the FIRST rung (default 0.15,"
                         " the search's own start)")
    ap.add_argument("--step-db", type=float, default=4.0,
                    help="how much louder to ask for while the rig "
                         "still answers (default 4.0). The step HALVES "
                         "each time a rung comes back short, so the "
                         "walk closes on the ceiling instead of "
                         "marching past it")
    ap.add_argument("--rungs", type=int, default=10,
                    help="most rungs to play (default 10); the walk "
                         "usually stops sooner, at the ceiling or a "
                         "brake")
    ap.add_argument("--even", action="store_true",
                    help="keep the step fixed: a plain ladder of equal "
                         "rungs rather than a search")
    ap.add_argument("--keep", metavar="DIR",
                    help="write every rung's capture here, so the walk "
                         "can be re-read later with a question it was "
                         "not asked at the time")
    ap.add_argument("--at", type=float, default=1000.0,
                    help="frequency the distortion figure is read at, "
                         "in Hz (default 1000, the datasheet's). A "
                         "port chuffs in the bass, so --at 40 is where"
                         " that lives")
    ap.add_argument("--stop-peak", type=float, default=STOP_PEAK_DBFS,
                    help="stop climbing once a rung reads above this "
                         "peak, in dBFS (default %.1f)" % STOP_PEAK_DBFS)
    a = ap.parse_args()

    sink = find(pw_backend.list_sinks(), a.sink)
    src = find(pw_backend.list_sources(), a.mic)
    if sink is None or src is None:
        print("no %s matches" % ("output" if sink is None else "mic"))
        return 1
    width = pw_backend.source_width(src)

    # NOT GUESSED. A stereo source has two columns and the coupler is
    # on one of them; taking the first silently is how a walk ends up
    # measuring an empty input.
    column = a.column
    if column is None:
        if width > 1:
            print("%s has %d capture columns; say which one the "
                  "coupler is on with --column 0..%d"
                  % (src["name"], width, width - 1))
            print("tools/input_census.py shows which one moves when "
                  "you knock on the capsule")
            return 1
        column = 0
    if column >= width:
        print("that source has %d capture channels" % width)
        return 1

    sweep = mc.default_sweep()
    freqs = mc.log_grid()
    outdir = tempfile.mkdtemp(prefix="pdeq-ladder-")
    wav = write_sweep_files(outdir, sweep, mc.DEFAULT_PRE_SILENCE,
                            mc.DEFAULT_POST_SILENCE)
    duration = (mc.DEFAULT_PRE_SILENCE + sweep.duration_s
                + mc.DEFAULT_POST_SILENCE)
    source = {"name": pw_backend.entry_node(src["name"]),
              "id": src["id"]}

    print("output : %s" % sink["name"])
    print("mic    : %s  column %d of %d" % (src["name"], column, width))
    print("ladder : %s from %.0f%%, stopping at a peak above %.1f dBFS"
          % ("%d even rungs of %.1f dB" % (a.rungs, a.step_db) if a.even
             else "a search, up to %d rungs, %.1f dB at a time and "
                  "halving" % (a.rungs, a.step_db),
             100 * a.start, a.stop_peak))
    print("SWEEPS WILL PLAY. Bring the card's own output level down "
          "first.\n")
    head = "THD@%s" % (("%gk" % (a.at / 1000.0)) if a.at >= 1000.0
                       else "%g" % a.at)
    print("  %-7s %-8s %-9s %-9s %-7s %-11s %-8s %s"
          % ("rung", "level", "peak", "response", "SNR", head,
             "margin", "verdict"))

    rows, resps, curves = [], [], []
    step = a.step_db
    lo = hi = None          # the bracket the ceiling is known to be in
    v = level_run._clamp(a.start)
    try:
        for i in range(1, a.rungs + 1):
            # BEFORE the sound. A level is worth knowing while it can
            # still be refused, not once it has been in someone's ears.
            print("  %-7d %-8.0f%% about to sweep..." % (i, 100 * v),
                  end="\r", flush=True)
            peak, resp, snr, clipped, at, margin, curve, over = rung(
                sink, source, sink["name"], wav, duration, width,
                sweep.fs, sweep, freqs, column, v, a.at,
                keep=a.keep, index=i)
            said = level_run.AutoLevel.verdict(
                peak, snr, clipped, at[1] if at else None)
            thd = ("n/a" if at is None else
                   "%s%s%%" % ("<=" if at[1] else "",
                               mb.pct_word(at[0])))
            print("  %-7d %-8.0f%% %-9.1f %-9s %-7s %-11s %-8s %s"
                  % (i, 100 * v, peak,
                     "n/a" if resp is None else "%.1f" % resp,
                     "n/a" if snr is None else "%.1f" % snr, thd,
                     "n/a" if margin is None else "%+.1f dB" % margin,
                     said))
            if resp is not None:
                resps.append(resp)
            curves.append((v, curve, over, peak))
            if at is not None and margin is not None and not at[1]:
                rows.append((peak, 20.0 * math.log10(at[0] / 100.0)))

            # DOES THE CAPTURE FOLLOW THE LEVEL? If not, the mic is
            # not in this path and the remaining rungs are sweeps
            # into nothing.
            if len(resps) == TRACK_AFTER:
                y = np.array(resps[1:])
                x = np.arange(y.size, dtype=float) * a.step_db
                got = float(np.polyfit(x, y, 1)[0])
                if got < TRACK_MIN:
                    print("\n  the capture followed the level at %.2f "
                          "dB per dB over rungs 2..%d -- stopping."
                          % (got, TRACK_AFTER))
                    print("  A capture in the path being measured "
                          "tracks the level nearly one for one, so "
                          "either")
                    print("  the microphone is not in this path (wrong "
                          "card, wrong column, or the")
                    print("  coupler is somewhere else), or the device "
                          "is limiting hard. Check the")
                    print("  first before suspecting the second.")
                    break

            if clipped or said == "loud":
                print("\n  the search's own brake said %s -- stopping"
                      % ("clipped" if clipped else "loud"))
                break
            if peak > a.stop_peak:
                print("\n  past the ladder's own ceiling (%.1f dBFS) "
                      "-- stopping" % a.stop_peak)
                break
            # THE STEP IS A SEARCH, not a march. While the rig
            # answers, ask boldly; the moment a rung comes back short
            # somewhere, halve and close in. Below MIN_READABLE_STEP
            # the answer is scatter rather than the rig, so the walk
            # stops there with the ceiling bracketed to that width.
            if not a.even and len(curves) >= 2:
                below = _below(curves, len(curves) - 1)
                asked = (None if below is None else
                         60.0 * math.log10(curves[-1][0] / below[0]))
                short = None
                if asked is not None and asked >= MIN_READABLE_STEP:
                    short, _ = _short_of(below[1], curves[-1][1],
                                         freqs, asked, mc.GRID_PPO,
                                         heard=curves[-1][2])
                if short is not None and short.any():
                    hi = min(hi, curves[-1][0]) if hi else curves[-1][0]
                elif short is not None:
                    lo = max(lo, curves[-1][0]) if lo else curves[-1][0]
                # THE SEARCH CLOSES A BRACKET, and what runs out is the
                # bracket rather than the step. His subwoofer answered
                # at 39% and fell short at 46 and again at 42: the
                # ceiling is between 39 and 42, which is three
                # decibels and still readable, but a walk that watches
                # only its own step size had already halved twice and
                # stopped. Ask instead how wide the bracket is, and
                # aim at the middle of what is left.
                if lo and hi:
                    span = 60.0 * math.log10(hi / lo)
                    # SPLITTING IT MUST LEAVE A READABLE STEP. A
                    # bracket of 3.7 dB is wider than the limit and
                    # still cannot be halved: each half is 1.85 dB,
                    # and the rung that came back would be compared
                    # over less than a sweep can tell. His subwoofer
                    # bracketed 39-45% and this is exactly the case.
                    if span < 2.0 * MIN_READABLE_STEP:
                        print("\n  the ceiling is between %.0f%% and "
                              "%.0f%% -- closer than a sweep can tell"
                              % (100 * lo, 100 * hi))
                        break
                    step = span / 2.0
                    print("  ...the ceiling is between %.0f%% and "
                          "%.0f%%; splitting it" % (100 * lo, 100 * hi))
                    v = lo
            # AND THE WALL ASKS THE RUNG WE ARE STEPPING FROM, which
            # after a split is not the rung played last. His subwoofer
            # bracketed 39-45%, set out to halve from 39 -- and the
            # wall measured its room from the peak at 45, saw none,
            # and stopped the search one sweep short of its answer.
            # From 39% the next rung would have landed near -15 dBFS,
            # nine decibels under the ceiling.
            from_peak = peak
            for c in curves:
                if abs(c[0] - v) < 1e-9:
                    from_peak = c[3]
                    break
            peak = from_peak
            # THE CEILING IS A WALL, not a line the walk notices
            # afterwards. The peak follows the level one for one --
            # measured on every ladder in this file -- so the walk
            # knows before it plays where a step would land. His
            # Tanchjim run printed "stopping at a peak above -6.0"
            # and then played a rung that read -2.1, because the
            # bold step jumped clean over the ceiling and the brake
            # only looked afterwards. An instrument must not play
            # louder than it said it would.
            room = a.stop_peak - peak
            take = min(step, room)
            # AND THE APPROACH IS ONE STRIDE, not a march. A walk that
            # starts far below where the rig is heard spends its rungs
            # climbing through silence: from 10% a speaker needs eight
            # sweeps of 4 dB before anything happens. The peak says how
            # far there is to go, so the FIRST step covers most of it
            # at once -- and only most, because the ceiling this closes
            # on is the CAPTURE's, not the rig's, and a stride that
            # lands exactly on it would leave nothing to search.
            #
            # It is a stride and not a leap on purpose. The search this
            # tool measures once doubled its cubic volume -- eighteen
            # decibels a step -- and sent a sweep into an earphone at
            # 0.0 dBFS. Predicting the landing is what makes a bold
            # step honest; boldness without the prediction is that
            # accident again.
            if len(curves) == 1 and room > 2.0 * step:
                take = max(step, room - 2.0 * step)
            if take < MIN_READABLE_STEP:
                print("\n  %s -- stopping"
                      % ("the next step would pass the peak ceiling "
                         "(%.1f dBFS)" % a.stop_peak if room < step
                         else "there is no readable step left to take"))
                break
            v = level_run._clamp(v * 10.0 ** (take / 60.0))
    except KeyboardInterrupt:
        print("\nstopped.")
    except (RuntimeError, ValueError) as exc:
        print("%s" % exc)
        return 1

    # the whole point: how much distortion a decibel of level buys,
    # counted only over rungs whose figure was a MEASUREMENT
    if len(rows) >= 2:
        x = np.array([r[0] for r in rows])
        y = np.array([r[1] for r in rows])
        k = float(np.polyfit(x, y, 1)[0])
        print("\n  slope over %d measured rungs: %.2f dB of distortion "
              "per dB of level" % (len(rows), k))
    else:
        print("\n  too few measured rungs to fit a slope")

    report_headroom(curves, freqs, a.step_db)
    print("  the level is where the ladder stopped")
    return 0


# WHAT A RUNG BOUGHT, which is his question and the plainest one this
# tool can answer. Ask for two decibels more and a rig with headroom
# gives two decibels more; one that has run out gives nothing, and
# every further turn of the knob buys distortion alone.
#
# It needs no threshold from anybody. The step is what WE asked for,
# the answer is what the deconvolution recovered, and short of half
# the step is short by any reading.
#
# TWO THINGS KEEP SCATTER OUT, and both are physics rather than a
# number. A rig runs out over a REGION, so the answer is read as the
# median of a third of an octave -- one bin below the line is the
# spread between sweeps. And headroom never comes BACK: if a rig
# stopped answering at 30 Hz on one rung it cannot answer there on a
# louder one, so a shortfall counts only when the next rung is short
# in the same place. His subwoofer's second rung otherwise reported
# 473-497 Hz from a single quiet sweep, and the third answered 1.7 dB
# there.
#
# THE REGIONS ARE LISTED, not summarised as "below N Hz". A rig can
# run out in the bass and again somewhere in the middle, and one
# bound would silently swallow everything between them.
#
# HIS SUBWOOFER, ten rungs of 2 dB from 25%, is why this exists:
#
#     40%   did not answer 20-30 Hz
#     43%   20-34 Hz
#     46%   20-39 Hz
#     50%   20-41 Hz
#
# Above 50 Hz it answered the full step on every rung to the end.
# That is exactly what he had heard -- "past half the knob it does
# not get louder" -- and it says WHERE, which no single tone could.
#
# HIS ADAM D3V IS THE OPPOSITE and is why the noise gate above had to
# be added: it answers in full on all ten rungs, while the frequency
# it can be heard down to walks DOWNWARD with the level -- 60, 55,
# 50, 46, 43, 41 Hz. Nothing is running out there; the bottom is
# climbing out of the noise. Without the gate the report called the
# silence below 30 Hz "answered in full", which claims headroom
# exactly where the rig makes no sound.
ANSWER_SHORT = 0.5          # of the asked step; below this it is scatter

# AND THE QUESTION IS ONLY WORTH ASKING WHERE THE RUNG WAS HEARD.
# His Adam D3V does not reproduce 25 Hz at all -- the microphone hears
# it 47 to 74 dB under that rung's own loudest point, and the readings
# jump about with no pattern because they are two noises subtracted.
# The report called that "answered in full", which is worse than
# saying nothing: it claims headroom exactly where there is no sound.
#
# A rung speaks at a frequency when its response stands clear of that
# take's own noise floor -- the same test the whole project uses for
# whether a figure is a measurement or a bound. Ten decibels is the
# margin at which the difference of two rungs is the rig rather than
# the floor.
HEARD_OVER_NOISE_DB = 10.0


def _short_of(prev, cur, freqs, step_db, ppo, heard=None):
    """Frequencies where a rung bought less than half of what was
    asked, read over a third of an octave -- and only where the rung
    was heard over the take's own noise.

    THE WHOLE BAND IS ASKED, which took his correction. The first cut
    of this looked below 500 Hz only, on my assumption that running
    out of headroom is a bass affair. It is not: his iLoud Micro
    Monitor, at 86% of the knob, stops answering from 854 to 1271 Hz
    and again from 1624 to 2248 -- the voice, not the port -- and the
    limit I had put in would have hidden every bit of it. He knew it
    was there because the speaker's own lamp turns red on overload,
    and it does so on the high part of the sweep before the low.
    """
    got = cur - prev
    w = max(3, int(round(ppo / 3.0)))
    sm = np.full(len(got), np.nan)
    for k in range(len(got)):
        seg = got[max(0, k - w // 2):k + w // 2 + 1]
        seg = seg[np.isfinite(seg)]
        if seg.size >= max(2, w // 3):
            sm[k] = float(np.median(seg))
    ok = np.isfinite(sm)
    if heard is not None:
        ok = ok & (np.asarray(heard, float) > HEARD_OVER_NOISE_DB)
    return ok & (sm < ANSWER_SHORT * step_db), ok


def _below(curves, i):
    """The loudest rung QUIETER than curves[i].

    The search halves by stepping BACK, so the rung played last is not
    always the loudest one played: his subwoofer went 39, 45, then 42.
    Subtracting the previous rung in playing order then asks what a
    quieter sweep bought over a louder one, and the answer is negative
    across the whole band -- the run listed thirty-four regions from
    20 Hz to 20 kHz, which is the arithmetic complaining rather than
    the rig. Order by LEVEL, not by time.
    """
    ref = curves[i][0]
    lower = [c for j, c in enumerate(curves) if j != i and c[0] < ref]
    return max(lower, key=lambda c: c[0]) if lower else None


def _runs(mask, freqs):
    """Contiguous frequency spans of a boolean mask."""
    f = np.asarray(freqs, float)
    out, start = [], None
    for k, on in enumerate(mask):
        if on and start is None:
            start = k
        elif not on and start is not None:
            out.append((f[start], f[k - 1])); start = None
    if start is not None:
        out.append((f[start], f[-1]))
    return out


def report_headroom(curves, freqs, step_db, ppo=None):
    """Per rung, the regions where it bought less than it asked."""
    if len(curves) < 2:
        return
    if ppo is None:
        ppo = mc.GRID_PPO
    pairs = [None]
    for i in range(1, len(curves)):
        b = _below(curves, i)
        step = (None if b is None else
                60.0 * math.log10(curves[i][0] / b[0]))
        pairs.append(None if step is None or step < MIN_READABLE_STEP
                     else _short_of(b[1], curves[i][1], freqs, step,
                                    ppo, heard=curves[i][2]))
    short = [None] + [None if p is None else p[0] for p in pairs[1:]]
    asked = [None] + [None if p is None else p[1] for p in pairs[1:]]
    print("\n  WHAT EACH RUNG BOUGHT, per frequency: asked %.1f dB"
          % step_db)
    print("  %-8s %-10s %s" % ("rung", "level", "did not answer"))
    for i in range(1, len(curves)):
        if short[i] is None:
            print("  %-8d %-10s %s"
                  % (i + 1, "%.0f%%" % (100 * curves[i][0]),
                     "-- too close to the rung below to tell"))
            continue
        m = short[i]
        # headroom never comes back, so a shortfall counts only where
        # the LOUDER rung is short too; the last rung has no witness
        if i + 1 < len(curves) and short[i + 1] is not None:
            m = m & short[i + 1]
        spans = _runs(m, freqs)
        # WHERE THE RUNG COULD BE ASKED AT ALL, as one bound rather
        # than a list. The room's own comb chops the heard region
        # into dozens of slivers -- one rung listed twelve -- and
        # what matters is only how far down the rig still made a
        # sound, so the lowest heard frequency is reported and the
        # holes above it are the room's business.
        f = np.asarray(freqs, float)
        lo = float(f[asked[i]].min()) if asked[i].any() else None
        if lo is None:
            said = "-- nothing rose over the noise"
        elif spans:
            said = ", ".join("%.0f-%.0f Hz" % t for t in spans)
        else:
            said = "-- answered in full, down to %.0f Hz" % lo
        print("  %-8d %-10s %s"
              % (i + 1, "%.0f%%" % (100 * curves[i][0]), said))
    print("\n  a rig with headroom answers the whole step; one that has"
          "\n  run out answers nothing, and the knob then buys only "
          "distortion.")
    print("  \"down to\" is how far the rig still made a sound over the"
          "\n  take's own noise -- below that there is nothing to ask"
          " about.")


if __name__ == "__main__":
    sys.exit(main())
