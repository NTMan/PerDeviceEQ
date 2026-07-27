# The solver and its map

This document explains what the fit graph draws and what the
solver actually computes. Part I is for anyone reading the
graph; Part II is the mathematics.

## Part I -- reading the graph

Five things are drawn. **measured** (white) is the smoothed
measurement of your device, with a ribbon showing the spread
between takes. **predicted** (green) is what the corrected
device will measure like: measured plus the EQ. **EQ** (blue)
is the correction itself, preamp included. **target** (gold
dash) is the curve the solver is lawfully aiming at. Dashed
gray verticals with a light shade beyond them are the edges of
the **trust zone**: the band where the measurement is honest
enough to act on.

### The two jurisdictions

The law of the fit has two axes, and the graph draws both.

The zone edges answer WHERE the law applies. Outside them the
measurement is not trusted (a speaker's roll-off drowning in
room noise, a rig running out of bandwidth), so the solver
neither places bands there nor owes anything there.

The boost cap answers HOW MUCH may be given at a point. Every
band may boost at most `max_boost` decibels (6 by default).
Cuts are not capped. This is why the gold target is not a
straight line: wherever the measured curve sags below the
level line by MORE than the cap, the target sags with it and
runs at exactly `measured + cap`. That sag is not a failure
and not taste; it is the price list of physics, drawn.

### The level is free

The whole construction floats: the metric removes the mean
error before judging, because a constant offset rides in the
preamp, not in the bands. On the graph the gold dash is drawn
at the prediction's own level for the same reason -- its SHAPE
is the law, its height follows the prediction it judges.

### The two numbers

The device strip carries two numbers. **fit** is the largest
distance between predicted and target after that level
alignment: how well the solver reached the target the law
allows it. **flat** is the same distance measured against the
STRAIGHT line, unpayable debt included: how far the corrected
result stands from the wish. A profile can carry "fit 0.53"
and "flat 7.3" on the same strip without contradiction, and
without the first number reading like a finish line drawn
right after the start.

The honest analogy is weight classes, not finish lines: fit
says whether the declared class weight was lifted cleanly,
flat is the absolute record, and the cap -- the class -- is
printed on the scoreboard (it is a per-profile parameter, in
the gold curve and in this document, movable only by the
owner's hand). Claiming a record without naming the class
would be cheating; naming both numbers is the cure. When flat
equals fit, the strip shows only fit -- there is no second
story to tell.

### For the audiophile

The straight line is the wish. Gold is the wish minus
physics. Green is us. Judge the solver by the gap between
green and gold; judge your transducer by the gap between gold
and straight.

Why not force the straight line? Because a fourteen-decibel
hole filled by brute boost costs fourteen decibels of preamp
headroom on the ENTIRE track, drives the transducer hardest
exactly where it already surrendered, and at the extremes of
the band often equalizes the measurement rig's own resonances
rather than anything an ear receives. Serious correctors all
cap their boosts; this one draws the consequence instead of
hiding it.

### The floor strip is a different organ

Speaker protection (the Floor button and the handle strip) is
about sealed high-pass stages that keep a small speaker alive
below its cliff. It engages only where the measured zone
testifies a cliff exists. The zone EDGES on the graph carry no
such gate: they are drawn always, because telling the truth
about where the measurement ends is not a protection feature.

## Part II -- the mathematics

### The target

The grid is clipped to the trust zone `[lo, hi]`. The level
line is the mean of the smoothed measurement over the zone.
The desired correction is `line - measured`, and the lawful
target caps its boosts:

    target = min(line - measured, max_boost)

Cuts are unbounded below. Everything the solver does is
measured against this curve.

### The metric

With `e = target - response`, the fit residual is the
level-free Chebyshev norm:

    fit = max | e - mean(e) |

The mean is removed because the preamp owns the constant.

### The greedy

Bands are placed one seat at a time at the argmax of the
masked residual. The masks are the laws: points outside the
zone are not pickable (jurisdiction); a boost point where the
NET response already stands within epsilon of the cap is not
pickable (the cap is a net law); anchors that parked twice are
retired for their sign. Band type follows zone position
(shelves at the edges, peaks inside), and a shelf that parks
at zero gain gets a second opinion as a narrow peak before its
anchor is written off.

Each band wears two leashes: its frequency may travel one
octave around its anchor, and its gain is boxed to the sign it
was placed with -- a cut may deepen or vanish but never flip
into a boost. After every placement the WHOLE chain is
re-solved jointly (trust-region least squares); the leashes
are what keep a converged joint solve from rebuilding
pathologies band by band.

The loop counts SEATS, not iterations: a twins merge and a
double-parked drop both refund their seat, and a hard cap of
three iterations per seat guards termination.

### Twins

A truly converged joint refine can slide two same-type
same-sign bands into ONE seat -- parameter for parameter, a
+12 pretending to be two +6 knobs. Twins merge: gains sum,
clipped into the sign's box so a boost pair pays the cap's
price visibly, and the survivor's crown is parked in a narrow
halo so the pair cannot re-form forever (place, converge,
merge, re-pick provably cycles without the park).

### The Jacobian

The response in dB is a SUM of band magnitudes, so each band
owns exactly three Jacobian columns and the matrix assembles
band by band. The RBJ biquad coefficients are differentiated
in closed form with respect to `(log10 f, gain_dB, q)`,
un-normalized -- the `a0` division cancels in `|H|^2`. With
`c1 = cos w`, `c2 = cos 2w`:

    N(w) = b0^2 + b1^2 + b2^2
         + 2 (b0 b1 + b1 b2) c1 + 2 b0 b2 c2
    M    = (10 / ln 10) (ln N - ln D)
    dM   = (10 / ln 10) (dN / N - dD / D)

and `dN`, `dD` are bilinear in the coefficients and their
partials. One Jacobian costs about two responses. The
finite-difference era paid `(3n + 1)` responses per iteration
-- a 43x multiplier at fourteen bands -- and its big solves
died against the evaluation budget instead of converging.

### The prune

Each band's removal is tried: the strongest overlapping
neighbours (a bounded number of seats) re-solve against the
target with the frozen rest folded in, and a pointwise court
accepts the drop only if no grid point gets worse than the
original fit allowed. The error side of the bound is a kept
band, never a worse fit.

### The deep polish

The final step of every fit is a seeded basin-hopping polish
over the whole chain, judged by the true metric. The smooth
objective is a log-sum-exp softened level-free max plus a
smooth penalty keeping the net response inside the cap's
tolerance:

    a   = | e - mean(e) |
    L   = max(a) + ln( mean( exp( beta (a - max(a)) ) ) ) / beta
    pen = k * sum( relu( response - cap - margin )^2 )

Its gradient is assembled analytically from the band columns:
centering subtracts the column means, softmax weights pick the
working points, the penalty adds its own term. Local solves
are L-BFGS-B with that gradient; hops are seeded uniform steps
accepted by a Metropolis rule.

Guarantees: the walk is SEEDED (one canvas, one answer, so
provenance holds), and the polish is NEVER worse -- the
candidate must beat the incumbent on the true level-free max
while keeping every law (net under cap tolerance, no twins in
a seat, sign boxes, shelf Q ceiling), or the incumbent stays.
Across different scipy builds the walk may fork between
equivalent basins on synthetic canvases; field canvases have
landed identical to the hundredth.

The polish exists because a sequential greedy cannot see
comb-shaped optima: on a field canvas the best legal solution
was a picket fence -- three pinned narrow boosts with cuts
woven between -- that beat the greedy's best by half a
decibel, and no ordering of greedy placements finds it.

### The ceilings

Two ceilings bound what any solver can honestly deliver.
Physics: content deeper than the cap leaves exactly
`desired - cap` on the graph, by construction. Information:
the room drifts between sessions (on this project's rig,
1.5-2 dB rms with narrow notches wandering by five decibels
and more day to day), so residuals far below the drift are
fitting a curve that will not be there tomorrow. The solver
stops being the bottleneck at the target's own floor; beyond
it, gains come from smarter targets, not tighter fits.
