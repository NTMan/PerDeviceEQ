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
jurisdiction, the floor it testifies becomes the speaker's
protection, and the score at the front of the strip is the
one number that says whether the rest of the strip deserves
to be read.
