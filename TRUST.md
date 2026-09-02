# Trust: what a measurement must earn

This document explains the trust court -- the machinery that
decides what a measurement is allowed to back. Part I is for
anyone reading the strip; Part II is the arithmetic.

Some tools accept any single sweep and leave the judgment to
the operator. This one requires evidence: repeatability is
earned, not assumed, and a fit has no jurisdiction where the
measurement cannot testify.

## Part I -- reading the verdict

The device strip opens with the verdict: "Trust 100 ·
36.4-20k Hz". The number is the SCORE; the range is the
CONTROLLED BAND, the only territory where the measurement is
allowed to back an EQ. Everything the solver does -- the zone
edges on the graph, the gold target, the bands themselves --
lives inside that band.

Nothing about the verdict is stored. It is recomputed from
the takes every time the profile opens, because a score
written into a file would go stale the moment a take is
deleted or a calibration swapped. Every deduction the court
makes lands in the strip's tooltip as plain text, verbatim.

The hardest rule is the simplest: fewer than two takes means
score zero and no band at all. A single sweep cannot earn a
zone, and without a zone the fit has nowhere to stand. To
raise the score, take more clean takes: three clean takes set
the base at 100, and only penalties can lower it from there.

A take is CLEAN when it is neither clipped nor flagged by the
session's own health checks. Clipped and flagged takes still
live in the archive -- chronology is sacred -- but they earn
nothing.

## Part II -- the arithmetic

### The controlled band

Per frequency, the judged quantity is the upper-confidence
bound on the take-to-take spread -- the session's own
statistic, not a reimplementation -- gated at the session's
spread limit. Band edges are found by the same
at-least-1/6-octave-run scan the measurement wizard uses, so
a lucky lone frequency cannot open a band by itself. The
result is intersected with the sweep coverage of the takes
that actually fed the average: no take reached there, no
band there. Channels are judged separately and combined by
max -- the worst channel rules, exactly like the live
session.

### The score

The base comes from the number of clean takes:

    0 clean -> 25    1 clean -> 45
    2 clean -> 70    3 or more -> 100

The base is then degraded MULTIPLICATIVELY by four factors,
each a linear ramp with a floor:

* median in-band spread: 0.5 dB or better costs nothing,
  2.0 dB or worse applies the full penalty, floor 0.6;
* worst take SNR: a ramp spanning 15 dB below the session's
  warn line, floor 0.5;
* age of the NEWEST take: younger than 90 days costs
  nothing, 730 days or older applies the full penalty,
  floor 0.8;
* fit coverage: when the stored fit reaches past the
  controlled band, the uncovered fraction of the fit range
  degrades the score, floor 0.5.

Every factor that bites appends a sentence to `reasons`, and
the view shows those sentences verbatim -- the court explains
itself in its own words or not at all.

### What this buys

The solver's laws (see SOLVER.md) assume their input is
worth obeying. The trust court is what makes that assumption
honest: the zone it certifies becomes the fit's frequency
jurisdiction and the honesty marks on the graph, and the
score at the front of the strip is the one number that says
whether the rest of the strip deserves to be read.

## Part III -- what the court does not judge

The court judges the MEASUREMENT. It has nothing to say
about the device the measurement is of, and two organs that
look like they belong to it do not.

### Distortion is evidence, not a term in the score

Every take records what it heard besides the tone: the
second and third harmonic, the total harmonic distortion,
and the same figure with the noise included. None of it
moves the score, and none of it narrows the band.

That looks inconsistent beside the SNR penalty above, and
the difference is the whole point. Noise is a property of
the MEASUREMENT -- it says how much of what was captured is
not the device -- so a noisy take really did learn less, and
the court docks it. Distortion is a property of the DEVICE.
A driver that rasps at 30 Hz is being measured correctly;
the take is telling the truth, at some cost to the device's
reputation and none to its own. Docking the score for it
would punish the instrument for the honesty of its report,
and would quietly make a bad speaker look like a bad
measurement.

So the harmonics are published rather than judged. They are
drawn under each take's response in the takes list,
summarised as `THD@1k` in the panel's header, and kept in
the profile. They are NOT painted as a hint to cut: a strip
that did that was tried and withdrawn, because a ratio has a
denominator and a denominator collapses wherever a rig makes
no sound -- it marked a subwoofer's own silence below 35 Hz
as dirt, and marked a monitor driven past its port as
defective when the cure was less level. What the editor
paints instead is measured against the rig itself at another
volume, and has no denominator to collapse. A profile whose
takes carry no confession is not
painted at all: an absence of evidence and an absence of
distortion must not wear the same colour.

Where they bear on trust, they bear on yours, not the
court's. A boost drawn where the device already rasps buys
more rasp, and that is a reason to doubt the EQ decision --
never a reason to doubt the sweep that revealed it.

### The Floor is a hand, not a verdict

An earlier version of this document said the zone testifies
the floor and the floor becomes the speaker's protection.
That authority has been withdrawn. The Floor is a fully
manual organ now: Off, or Set to a frequency a hand chose,
seeded at 40 Hz merely as a place to grab. The ceiling is
gone entirely.

The reason is a confusion the old arrangement invited. A
zone edge says where the MEASUREMENT stops being
repeatable. A floor says where the DRIVER stops being able.
Those are different questions, they resolve to different
frequencies, and letting one number answer both means
cutting bass wherever the measurement merely got noisy --
protection asserted on evidence that was never about
excursion. The zone keeps what it can actually testify to:
jurisdiction for the fit, and the marks on the graph that
say where the curve is worth reading. It never gates
playback.

What replaced the automatic floor is the hand, better
informed: sweeping the handle moves the shading on the level
strip, the red stretch of the curve and the marks under it
live, so a floor is swept up until what the rig cannot
follow goes out from under the finger, rather than by ear
alone.

That informing has a limit worth stating here, since this
file is about what the program may claim. With the preamp on
Auto a floor buys no level at all: Auto follows the chain's
own peak, so headroom the floor frees is handed straight to
the neighbouring bands and the marks climb rather than
clear. The editor shows this while it happens and does not
choose for anyone.
