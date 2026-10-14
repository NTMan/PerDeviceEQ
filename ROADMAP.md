# per-device-eq Roadmap

Goal: any output device corrected through per-device-eq should sound the
same to *this* listener — measured correction per device, taste per
listener, headroom that never lies.

---

## Sprint (opened Jul 25, 2026)

The graceful move: this list was drawn in the old chat, edited
by the architect point by point, and minted here so the next
chat opens with "here is the project, here are the rules --
the sprint begins".
His goal lines ride each task verbatim.

1. **The gain ladder, finale.** Score the 24 dB and 18 dB
   rungs against the pre-registered bets (24: noise -62..-63,
   SNR ~43; 18: noise ~-63, SNR starved), close the curve on
   four points. His field notes already in: 18 dB got a second
   chance -- the least-squeezed correction of all, sweeps loud
   enough to shake clothes, some takes redone; 24 dB the most
   frictionless -- six greens on the first pass. Goal: learn
   what a UMIK-2 would actually buy, and which UMIK-1 gain
   serves his field measurements best -- crown the gain, and
   a written verdict on the UMIK-2 by data. Shelf seed noted: a retake counter per
   session (many attempts = suffering, few = first-take
   greens; his own caveat -- not every failure is the gain's).

   **Verdict (Jul 25).** Both bets refuted, loudly. The 24 rung
   read noise -68.5 dBFS -- past its own -67 refutation line --
   with SNR 42.8 on the money; the 18 rung read -73.1 with the
   ladder's best SNR 46.7, starvation beaten by the volume
   knob (chan_vol 0.216 -> 0.512 lives in the file). The
   two-point chain model died: the clean same-session 24/18
   pair puts the room at -57.0 dBFS (36-equivalent) and the
   chain at -77.5, crossover at gain 15.5 dB -- below the
   jumper's lowest step, so the room rules every rung; the 30
   rung's excess was +1.9 dB of morning ambient. Take
   repeatability sits at 1-2 dB rms and stays flat from SNR 39
   to 47 -- the deliverable is not SNR-limited anywhere on the
   ladder; the true ceiling is inter-session drift in narrow
   notches (1.5-2 kHz, 9 kHz, +/-20 dB). THE CROWN: 24 dB.
   Zero slow beats and six first-take greens at the cadence
   floor (11 s), 8+ dB of capture headroom at the honest
   listening drive; the 18 rung bought its SNR by pushing the
   speaker into its limiter (the acoustics returned 2.5-3 dB
   less than the electricity promised -- task 5 territory).
   UMIK-2: refused by data. The mic's electronics sit 20 dB
   under the room at equal gain and never surface in the
   jumper's range; SNR here is sweep SPL minus room ambient,
   and both free levers (the knob, the hour) are already in
   hand. The shelf seed grew a design: the retake counter
   derives from stored take timestamps -- gap against the
   session's own cadence floor -- no schema change, suffering
   read as evidence.

2. **The battle fit of the iLoud.** Goal (formulated as
   owed): a reference profile for LIFE, not for the ladder --
   the mic reseated so the spread speaks for the listening
   area, the cal worn so the curve is comparable across rigs
   and on the exchange, the crowned gain from task 1; the
   zone and the floor reborn from honest statistics, then the
   ear's 54 Hz judged against the new zone edge. This profile
   is the yardstick the AXON and the sub will be measured
   against.

   **Verdict (Aug 26): still open, and now bigger than it was.**
   The hardware is all here and none of it sold -- iLoud Micro
   Monitor, NUX AXON 3, Adam D3V which was never in the plan,
   and the sub. So the comparison is no longer one reference
   against the ladder but four desktop speakers, with the sub
   and without, on one rig and one method. That is an article
   about choosing desktop speakers for a computer, and it is
   what the yardstick is for.

   **Status (Oct 14): waiting on where the sub can stand.** A
   notch of about 20 dB at 40-41 Hz stays with the monitors
   switched off, so it is the room and not the crossover. The
   sub was tried under the desk, in the front corner and at
   the back wall (Sep 12-20). In the corner the bass became
   localisable. At the middle of the back wall the notch is
   gone, and that spot cannot be used: the kitchen cabinets
   begin 60 cm behind the chair, and a sub there stands in the
   passage. The sub is back under the desk, and its own
   limiter starts at about 45-48% of the volume fader. An Adam
   Sub8 is under consideration and waits on the same question:
   it would bring headroom and its own phase and crossover
   controls, and it does not move a room mode.

3. **Preamp automation closes its loop.** His bug filed as a
   question -- why must the automation be jiggled after a
   floor toggle -- root found (the loading gate muted the
   re-land) and fixed. Goal: fix the preamp automation
   bug; acceptance = his Auto number moves by itself on
   the toggle, no hand on the automation.

   **Verdict (Aug 26): done.** The re-land runs outside the
   loading gate in `_on_floor_toggled`, and his Auto number
   moves by itself on the toggle.

4. **The solver's beasts.** (a) the argmax mask at saturated
   anchors -- the 58/88 duplicates die, the boost cap regains
   meaning; (b) the windowed joint refine -- the hour shrinks
   (band 10 cost +41 s, 2.2M evaluations); (c) the floor-aware
   base -- the solver optimizes with the floor fixed in its
   model, honest at the edge. Goal: faster, leaner math --
   a smaller residual max on a bigger budget -- with the standing honesty that the reported
   residual measures the TRUE uncapped target, so unfillable
   nulls keep their visible price. Status: (a) landed Jul 25 --
   with the diagnosis corrected in the field: the argmax was
   the accomplice, the joint refine's sign flip was the parent
   (see "fit: the sign leash and the saturated anchor's halo").

   **Verdict (Aug 26).** (b) landed and the speed-up is felt:
   `_refine` leashes every band's frequency to `span_oct`
   octaves around its anchor (`GREEDY_SPAN_OCT` in placement,
   `PRUNE_SPAN_OCT` in the prune), and the prune re-refines
   only the bands a removed one actually reaches
   (`PRUNE_OVERLAP_DB`), holding the rest. (c) is REJECTED by
   the architect rather than pending: the solver does not need
   to know about the floor, because the blind zone computed
   from the takes -- and widenable by hand -- already says
   where it must not spend.

5. **The stress probe enters the sprint.** Challenged in by
   the architect (my parking reasons did not survive: it
   algorithmizes well, the goal is reachable -- only the
   queue's thickness held it out). Prototype on the ready
   30 dB canvas: gated bursts over what measured quiet, the
   same mic and cal listening for harmonics and burst garbage,
   distortion-vs-frequency at a loudness. Its deliverable is
   the PRECONDITION for task 6: an auto floor no worse than
   his ear (the ear found 54 where the zone said 38.3 -- the
   probe must find 54 too).

   **Verdict (Aug 26): the measurement shipped, the automatic
   floor did not.** Every take now confesses `thd_db`, `h2_db`,
   `h3_db` and `thd_noise_db`; the takes panel graphs them, and
   the EQ page carries a warning band along the floor handle's
   own strip. The automatic floor was tried and its result did
   not satisfy the architect, which decided task 6.

   **Second verdict (Sep 2): the warning band was withdrawn,
   and what replaced it is task 12.** The band asked "how much
   dirt is there", and that question has no honest answer here:
   every form of it is a RATIO, and a denominator collapses
   wherever a rig makes no sound. It marked a subwoofer's own
   silence below 35 Hz as dirt; it marked an iLoud driven past
   its port as defective when the cure was less level, and
   advised cutting to 100 Hz where 54 was the answer. Three
   separate gates were fitted over one evening and the leak
   came back in a new place each time. The take fields stay --
   a rag in the port drops `hohd_db` by twenty decibels, which
   no other quantity showed so cleanly, and they guard the take
   itself, since deconvolution assumes linearity.

6. **[Off | Auto | Set].** The Floor button becomes an
   AdwToggleGroup (he found the component) speaking the
   Measure grammar: Auto follows the zone -- and after task 5,
   the probe's floor(L); grabbing a handle flips to Set; Off
   sleeps the stages and hides the strip. Precedent stands:
   the session-loudness preamp mode coexists with Auto, so
   Manual is not dubious. Goal: the floor working in
   auto, in manual, and off.

   **Verdict (Aug 26): resolved the other way -- Off and Set,
   no Auto.** The floor is a fully manual organ; the ceiling
   came down entirely and the trust zone lost its protection
   authority, keeping its fit jurisdiction and its honesty
   marks on the graph. A hand sweeps the handle until the
   distortion is gone -- and since Aug 26 it sweeps with the
   warning band under its finger rather than by ear alone.

   **Amended (Sep 2).** The band is gone; the handle now sweeps
   with the level map under it -- the strip's shading, the red
   stretch of the curve, the marks and the advice line, all
   live. Two findings the handle had been hiding: the floor was
   never in the chain the reading summed, so cutting lower
   walked the safe level BACKWARDS; and with the preamp on Auto
   a floor buys no level at all, because Auto hands the freed
   headroom to the neighbouring bands and the marks climb
   rather than clear.

7. **Exchange v1, the design round.** The agenda grew by his
   questions: is it a service page of its own; what lives on
   it -- search over speakers and headphones, photos and
   graphs, who provided the profile and on what rig it was
   measured. Goal: decide what the exchange
   service is -- architecture, storage, CMS, frameworks.
   The v1 floor from the old Next stands under the round: a
   static sha-addressed index of .pdeq packages (a git repo
   can be the whole backend) plus in-app browse and import
   through the existing unpack door; the maintainer's
   cross-check against published curves marks a package
   verified, unverified stays visible and labeled; accept
   when a published profile installs from inside the app with
   its provenance shown.
   Boundary notes stand: the package knows its subject; the
   architect's git-CMS idea (md files rendered client-side,
   publish = git commit && git push) is ITS OWN project, not
   mixed into per-device-eq -- but the exchange's reading room
   is its natural first client if he builds it.

   **Verdict (Aug 26): settled without a service.** The profile
   is self-sufficient -- it carries its own measurement, its rig
   and its calibration passports -- and content addressing was
   added so a package names itself. No exchange service is being
   built; the git-CMS idea stays its own project.

8. **The sub and the AXON, on hardware arrival.** Goal:
   full-weight sound -- headphone-grade, but on speakers. Deliverable includes an INSTRUCTION -- how to
   wire and tune the 2.1 stack (crossover seam, level, phase,
   ARC X off, their DSP frozen flat for measurement) with our
   own UMIK as the instrument.

   **Verdict (Aug 26): the hardware is all here.** AXON and Adam
   D3V both measured, the sub measured with and without, and the
   comparison task 2 wants is now possible.

9. **Release 5.0.0, the finale.** Goal: ship every sprint
   feature -- bump_version, notes for speaker protection with
   its range handles, the zone in the fit, the headroom fixes;
   COPR, flatpak bundle and AppImage converge.
   **The number changed from 4.1.0 on Aug 26, and not as
   ceremony**: the profile body schema is 5, and a profile of
   any earlier version is skipped rather than migrated.
   Distortion is not metadata that can be filled in later -- a
   take either recorded its harmonics while the sweep played or
   it did not -- so there is nothing to convert, and the notes
   must say plainly that earlier profiles want re-measuring.

   **Shipped (Oct 4) as 5.0.2, on profile schema 7** (6 on
   Aug 29 for `unasked_db`, 7 on Sep 8 for the level map). The
   5.0.0 and 5.0.1 builds failed in every COPR chroot: the
   spec's `%check` imported a module deleted on Aug 4, and the
   spec is built only on a release, so nothing saw it for two
   months. `%check` now runs the installed launcher, and the
   Tests lane builds the RPM on every push. The tag was cut on
   a tree whose three version carriers still said 5.0.0, so
   5.0.2 called itself 5.0.0 in About and in AppStream; 5.1.0
   (Oct 14) was bumped before it was tagged.

10. **The globals-row icon family.** Goal: visual polish
    -- icons for the eye, Floor and Auto strip when the family
    is picked.
    **Status (Aug 26): three exist and all are drawn**,
    `pde-find-gain-symbolic` (a microphone beside a ladder of
    levels) and `pde-level-symbolic`, which was a ruler and is
    now that same shape with a loudspeaker in the microphone's
    place -- the architect's call, and the right one: the two
    rows do the same work on the two ends of the chain. An
    earlier line here named the WRONG glyph as the rejected
    one. Still stock or textual: the eye
    (`view-reveal-symbolic`), and Floor and Auto, which are
    words, and Auto STAYS a word: the button carries a state,
    not an action, and every candidate glyph either lies (a
    wand promises magic where this is arithmetic), abbreviates
    the same word to one letter, or turns the state into a
    reload. There are two Auto toggles in the app, the
    preamp's and the fit's, and a picture on one of them would
    split a pair that should look alike. The floor is the
    opposite case and that is why it got one: "Floor" is a
    term rather than an ordinary word, and the glyph shows the
    shape where the word only names it.

11. **The routing rework, never on this list.** It changed
    how a microphone and a sink are stored -- capture columns
    declared by hand, a microphone nobody chose having no
    columns, targets riding in the sink's card rather than in
    a constant, a target captured by the capsule a hand points
    at. Twenty-two commits, and the architect's verdict on it
    is that it was worth the disruption.

12. **What the rig can still follow.** What task 5's band was
    reaching for, asked as a question that has an answer. Not
    "how much dirt", which is a ratio and collapses, but "at
    what level does this rig stop delivering what the
    correction asks of it" -- measured against the rig itself
    at another volume, so there is no denominator.

    **Shipped (Sep 2).** The level search walks upward from
    well below the level it measures at, two decibels a rung,
    and stores level, capture peak and the whole response for
    each. The editor reads it at the level the rig RECEIVES --
    knob, preamp, filters, floor and taste, which is a curve
    and not a number -- and paints the volume strip green, red
    and grey, turns the prediction red exactly where it stops
    predicting and renames it, marks the thirds of an octave,
    and states in one line what floor would stop asking.

    Confirmed by ear three times on two rigs: the search
    bracketed an iLoud at 41-48% and he heard it clear at 44%;
    a floor at 54 Hz cured a passage that a floor at 100 would
    have over-cut; and the marks on an orchestral passage went
    out at the level he had settled on by hand. A multitone
    probe, asked because a sweep was suspected of being too
    gentle, agreed with the sweep to within its own scatter --
    that suspicion is dead, and the excursion arithmetic in
    tools/multitone_probe.py says why.

    **Open, in order of how soon it will be wanted:**

    (a) *The map belongs to a chain, not to a transducer.* A
    Tanchjim measured through an M62 says nothing useful about
    the same earphone on a weaker amplifier: the knob is not a
    common axis. The fix is cheap and not yet cut -- the
    capture PEAK is the common axis, it is already stored on
    every rung, and the peak follows the level one for one
    within a chain, so a single level_probe run on the new
    amplifier anchors the old map. Until then the profile
    should say the map is chain-bound.

    (b) *One sweep per rung.* Over Bluetooth one rung came back
    0.78 dB down across a few dozen hertz that two other runs
    did not see. Two sweeps and a median would settle it, at
    twice the time; no wired rig has shown it.

    (c) *One earphone's map did not repeat, and the reason is
    probably the seal.* Three walks of a JBL Tour Pro 3
    differed by 1.5 dB below 30 Hz with nothing at 1 kHz, which
    is a seal's signature and not a driver's. It is ONE
    specimen: the other five in-ear rigs here never showed it,
    but neither did they show a border at all -- theirs sits
    above what the microphone can reach, so there was nothing
    to disagree about. What is established is that a rig
    standing AT its border reads unstably there, not that
    in-ear maps expire.

    (d) *Above the loudest rung, nothing is known* whenever the
    capture rather than the rig ended the walk, which is three
    of the five rigs here. Lowering the input gain moves that
    ceiling, but only where the gain is analogue. ON THE UMIK-2
    NOTHING LINUX CAN REACH IS: both controls it publishes sit
    after conversion, so cutting one moves the peak in the FILE
    and leaves the converter where it was. A ceiling reached
    that way is not the rig's, and a map read above the old one
    was taken through a converter already at or past full
    scale.

    The microphone itself is not the problem. It has an
    analogue preamp, and by two field reports it leaves the
    factory at +18 dB out of a 0..+42 range, with the number
    written into the calibration file. So on a default unit
    eighteen decibels of acoustic headroom are sitting there --
    behind a Mac/Windows tool, off every bus Linux speaks. That
    is a task, not a wall, and it is filed with the M62 work.

    And this remains the one place in the project where the
    right answer may be NOT to measure.

    A loudspeaker past its border makes a noise. An earphone
    past its border may not come back, and the reason its
    border is out of reach is usually that its own amplifier
    cannot get there -- on a TWS the weak amplifier IS the
    protection. Driving an IEM from something that can reach
    removes it and puts the consequence on us. His words:
    a serious amplifier kills an IEM.

    So if the ceiling is ever moved, it moves for rigs whose
    own chain could already reach the border, and grey stays
    grey for the rest. Reaching further into an earphone is not
    a better measurement; it is a broken earphone with a
    number attached.

    **The open list, Oct 14.** (a) stands, and its cheap fix
    needs a second look: since Sep 10 the step a rung took is
    read from the rise of its own curve, because the capture
    peak over-reads by the harmonics riding on it (e2b5225), so
    the peak is not the clean common axis (a) took it for. (b)
    stands: rungs are single and judged against the base pair's
    scatter. `tools/scatter_probe.py` (Sep 8) plays every rung
    twice and keeps every sweep, to settle by arithmetic whether
    every rung needs two; that is not settled. (c) and (d)
    stand. On (d), the UMIK-2's preamp is reachable only through
    miniDSP's tool for Mac and Windows, and moving it moves the
    sensitivity its calibration file states, which is computed
    for the factory +18 dB.

    **Second shipment (Sep 7-11): the level passport.** The map
    left the session block, where a remeasure pruned it, and
    became a passport per channel, kept in the profile beside
    the takes with the conditions it was walked in. His reversal
    of Sep 8 shaped it: the search stays as the passport's first
    phase, because it alone looks at distortion, and the ladder
    reads the rest. One press on the channel in view runs both --
    probes from 15% towards a capture peak of -10.5 dBFS, then a
    ladder from 12 dB below the settled level whose first rung
    is a pair, two 6 dB strides, then 2 dB steps until the
    capture or the knob runs out. A rung's step is the rise of
    its own curve, and every reading is judged against the base
    pair's own scatter over a third of an octave. The fader
    carries the search's answer, one for all channels, the
    louder answer when there is no knee, a 2 dB step under a
    knee found below it. The passport card at the top of the
    right column is the walk's only picture: rungs above a rule,
    probes below it, the coming sweep dashed. A chosen rung is
    measured again with everything above it, without a search; a
    chosen probe resumes the search. Profile schema 7 (Sep 8)
    makes the passport the source of a profile's measurement
    level.

    **Open, his to decide (Sep 10):**
    - the first sweep of a search draws nothing, having no
      neighbour. The two priced answers are playing the first
      probe as a pair (one more sweep) or the mirror rule;
      neither is chosen;
    - the mirror mark waits for an event on the step picture,
      and the iLoud is the rig to judge it on;
    - trying a rebuild from a rung and from a probe in the field
      together, as he wanted;
    - whether takes from sittings with different conditions may
      share one canvas.

    **Found Oct 14 by reading the code against what was agreed**
    (not yet seen in the field):
    - the passport's conditions are written and never read, so a
      passport walked through another output or microphone still
      sets and holds the fader. Agreed Sep 8 was that diverged
      conditions free the fader and the window says why;
    - the knee cap depends on the order of the channel names: a
      louder answer met after a capped channel lifts the fader
      back over that channel's knee (the working level in
      level_run.py);
    - the seating check on a rebuild compares only when the kept
      top rung is the base, the one rung with a scatter of its
      own, so a rebuild from a higher rung is not checked;
    - the heading says "the capture ran out first" whenever there
      is no knee, also for a walk ended by the knob, the rung
      budget or Stop;
    - the fader's tooltip still says the weaker side sets the
      level, and the level button's says "probe sweeps only",
      though a press walks the ladder too;
    - a click on a row below the rule can select its neighbour:
      the hit test does not count the rule's height.
    - TRUST.md said clipped and flagged takes earn nothing. The
      code gives them no base, but lets every take on the canvas
      into the average and the spread and counts it toward the
      two takes a band needs, so two clipped takes certify a band
      at a score of 25. Which of the two is meant is his call;
      TRUST.md now says what the code does and points here.

13. **A device is its node and the port in use.** His decision
    of Sep 15: a device is identified the same way everywhere,
    and once the port was chosen, everywhere includes the
    microphone side. A Bluetooth headset in Headphones and in
    Handsfree, or one card's line out and its headphone out,
    are different devices with their own profiles. Keying by
    channel width was abandoned (he removed those patches
    himself) and keying by card profile was rejected. The
    microphone side moved in the same series -- a measurement
    rig is its node and its jack -- at the price of re-binding
    his microphone profiles, which he did not count: data made
    again in a second is not worth protecting.
    **Shipped (Sep 15-17)**, with one spelling for every key
    (`port_key`): the port by its name, never by a route index.
    The input list once wrote `#3` where the device door wrote
    `#analog-input-mic`, and stored keys never matched.

    **The limit he accepted (Oct 7).** The key does not carry
    the USB path. His two M62s report the same serial, so the
    bus path is the only thing that tells them apart;
    WirePlumber names them `-00` and `-00.2` in the order it
    sees them, and with both in Pro mode their profiles swap if
    that order changes. Between the two evils he chose
    independence from the USB port a card is plugged into. A
    name WirePlumber gives while the previous object still
    holds the usual one (`...sink.2`, `-00.3`) is a new device
    to the app, and a profile bound under it plays only while
    the card carries that name.

14. **The taste on every device, and what the hook is told.**
    Four faults found in the field on Oct 7, fixed in one
    release:
    - A Bluetooth headset in Handsfree crackled under any EQ
      from this app. `param_eq` declares eight ports whatever
      its config names, a graph naming no ports takes that
      width, and on a mono node PipeWire up to 1.6.9 turns the
      seven unwritten outputs into full-scale DC
      (pipewire#5473). Every graph now names one port pair per
      chain, and Clean and Bypass remove the graph instead of
      applying a flat one, which was eight wide too.
    - Since Sep 15 a measurement played through the device's
      own EQ: graphs were keyed `node#port` while the
      moratorium still spoke the bare node name. It strips and
      restores the graph under the device key now.
    - The hook's state file could not be read back for a key
      with a space or a bracket in it, which every UCM port
      has, because WirePlumber escapes them.
    - A device with no profile played the taste only after the
      window had been opened on it, and the window and the hook
      feed built a device's graph two different ways.

    One function now builds what a device plays -- correction,
    taste and preamp -- for the window and the feed alike. He
    chose to merge in the app rather than in the Lua hook once
    `--apply` over his 14 bound devices measured 0.75 s, and
    said the choice may come up again. A change of the taste or
    of the manual preamp republishes every bound device. The
    hook speaks protocol 3: a common entry `@taste` (the taste
    and the preamp, in a form that fits any width) plays on
    every node with a card and no value of its own; `""` is
    explicitly nothing (Bypass, a measurement); a device told
    to follow the common entry gets `@taste` written as its
    value, never a deleted key, because the metadata announces
    a delete only for a key it holds; and a table persisted
    under another protocol is not read. A device whose port is
    not known for a moment keeps its last key, in the app and
    in the hook. **Shipped as 5.1.0 (Oct 14).**

    **Open:**

    (a) *Old entries in the hook's table.* A new protocol
    starts from an empty table, but within protocol 3 `--apply`
    only writes: an entry whose binding was removed stays until
    something writes over it. Two dead M62 keys were removed by
    hand after the protocol 3 install. His question of Oct 7,
    whether `--apply` should clear the table and at what cost,
    is undecided.

    (b) *The hook's half of "a node seen before its card".* It
    is covered by `tests/hook_harness.lua` only: PipeWire 1.6.x
    does not report the graph a node has loaded, so the field
    cannot see it before a release carries master's d4329600d.

    (c) *Undo reset a manual preamp to Auto.* The restore
    reads the preamp mode from the history snapshot, with Auto
    as the default. From Jul 17 the snapshot recorded the mode
    and a press of Auto was an undo step; on Aug 7, when the
    preamp moved out of the profile (eafdade), both were
    dropped and the restore kept reading. From then every undo
    or redo in Manual switched to Auto for the session. In
    5.1.0 the restore also writes the ride and republishes
    every device, so the switch persisted and the manual value
    was lost. Found Oct 14 by reading the code. **Fixed in the
    code the same day:** both halves are back, because the
    snapshot alone would turn the fault around -- an undo after
    pressing Auto would release it. Not yet tried in the
    window.

    (d) *A floor on Default.* The floor button is live on
    Default, and toggling it saves the built-in Clean profile as
    `clean.json` among the user's profiles. Since 5.1.0 a device
    on Default plays only the common entry, so that floor is
    drawn and does not play. What Default with a floor should
    mean is undecided: no floor on Default, or a device on
    Default with a floor publishing a graph of its own.

    (e) *A turn of the preamp forked the profile.* The turn ran
    the edit pipeline, which began by forking anything that is
    not a user profile: on Default it made an empty "Custom EQ"
    and bound the device to it, on a built-in profile a
    "(custom)" copy. That made sense while the preamp was kept
    in the profile; since Aug 7 the copy holds nothing the turn
    changed, and since 5.1.0 the device on Default got a graph
    of its own instead of following the common entry. Found
    Oct 14 by reading the code. **Fixed in the code on Oct 15:**
    the fork stays with the edits of the profile -- the bands,
    an import, a new channel -- and a turn of the preamp makes
    none. Not yet tried in the window.

## Shipped

- **The cal wears its provenance.** Closed as testimony, not
  verdict: the architect's analog doctrine rules the domain --
  a cal belongs to the analog layer in front of the hole, a
  hole has no serial, and even usb node grammar cannot split
  an integrated mic from an interface input, so "the wrong
  cal" is machine-undecidable and the machine testifies while
  the one who sees the analog layer judges. The slot wears the
  assigned file's biography (content-addressed by sha,
  house-wide across every profile's takes, silent when the
  story is all native); the noise lives in the cloud's shadow
  -- one number, the count of foreign profiles, in a colored
  pill whose color weighs the statistical anomaly, with every
  word in the tooltip; the inventory dialog counts per rig and
  its Reassign remains the bulk re-cal door. The round also
  minted H10 (the list dresses evenly, jurisdiction inside the
  row's own population) which caught the wizard's band budget
  on its first sighted run and the CI-only two-line target
  rows the workstation could never see, and the takes list now
  speaks the architect's three-rule grammar: a group opens
  with its capsule line right-aligned, takes are separated by
  their own signal lines, a gray rule closes every group --
  chronology sacred throughout.

- **AppImage.** Released with 4.0.1 and field-clean on the
  architect's station: a single-file door for everyone not on
  Fedora, attached to every release with a zsync auto-update
  channel, and built on every push under the same continuous
  discipline as the code -- a CLI smoke for the Python spine
  and a GUI smoke that opens a real window. The harvest is a
  plain dnf --installroot on the oldest supported Fedora (a
  policy, not a number: the base is the glibc floor, bumped
  once per cycle), and the bundle obeys the host-families
  law the field taught one catch at a time: what couples to
  host state leaves (the loader and glibc, the GPU drivers
  and their kernel spine, the C++ runtime mesa resolves
  through us, fontconfig with its /etc/fonts grammar), while
  skew-stable cargo stays (dispatch loaders, the protocol
  client libs, freetype). Debian 13 and Mint 22 sit below the
  floor until a source-built base earns its keep.

- **GNOME HIG pass.** Closed in both halves. The mechanical
  floor: nine rules (H1-H9, the last one dynamic -- the audit
  presses Tab itself and proves the walk rounds the room),
  zero findings across the whole house -- both windows in both
  channel costumes, the command dialog, About, the export
  wizard with every preview, the cal history -- under the CI
  ratchet, cairo-rendered so headless containers judge too.
  The manual half lives as the charter's checklist in HIG.md,
  every line a check with evidence; the sittings it settled
  became rules (the wardrobe, the focus grammar) or design
  (one rig, one header). Two field bugs became permanent
  machine rules on the way.

- **Profile package.** The architect's ruling shaped it: the exchange
  artifact is the store's own canonical body, bare -- no envelope, no
  wrapper version, the profile's own schema version is the one
  contract (design review peeled off the embedded sha and the wrapper
  before a line shipped). pack() writes deterministic bytes and the
  file's sha256 IS its address; unpack() refuses directionally (newer
  body -> newer build, older -> the migration tool); absorb() never
  destroys -- byte-identical keep, spoken no-op on the same address,
  remint with provenance on collision; the import dialog shows the
  package_report passport. Byte-stable roundtrip is pinned by test.
- **CI that sees the GUI.** Two Actions lanes. Tests: Fedora container
  with the real toolkit -- pyflakes over the whole tree, every `.ui`
  loaded by GtkBuilder after `Adw.init()` (a stricter judge than
  `gtk4-builder-tool`, which cannot see libadwaita types), a headless
  import smoke of the Gtk modules, and the full suite under Xvfb.
  Flatpak: manifest built for both arches on every push, bundles
  attached to releases. Acceptance met in the field: the lane turned
  red on a real import-time error (a missing typelib) before it ever
  went green. Since Oct 4 the Tests lane also builds the RPM from the
  pushed tree in a clean Fedora container, the way COPR's build root
  does, so a spec that would fail a release fails the push first.
- **Export wizard.** One window-level action (the primary menu is its
  home) that bakes the COMPOSED chain -- device + active taste, preamp
  included -- and asks one question: where is this going? Shipped in
  3.2.0 with the writer classes and the null-test acceptance the sprint
  wrote for it.
- **Measurement.** The app is the instrument: Farina sweep out through the
  selected sink, capture from the rig, per-capsule calibration, impulse
  windowing, magnitude-RMS averaging across seatings, SNR-targeted
  auto-leveling, per-take quality gating, and a constrained biquad fit
  with an EQ range that follows the take-to-take spread. Incremental core:
  every take persists the moment it lands; sessions resume; delete + refit
  are first-class.
- **Headroom.** Tier-1 worst-chain estimate composing the taste layer,
  tier-2 live post-EQ meters in the preamp card, an Auto preamp MODE that
  lands the composed Safe (device + taste) and a session clamp that
  absorbs real peaks mid-playback. The fit lands bands only -- the mode
  owns the gain.
- **Taste layer.** Named preference EQs over any profile on any device,
  a Taste card with an in-place editor, picker with rename/delete, and
  honest interaction with headroom. Since 5.1.0 it also plays on a
  device with no profile of its own, through the hook's common entry,
  and a change reaches every device at once (task 14).
- **One undo timeline.** Device, taste and profile switches share a
  single history: selection is not an edit, steps are single, births and
  deletions are revocable within the session (the graveyard), and the
  arrows never lie about whether anything would change.
- **Sink honesty.** Edit aims at the profile's own sink; only New picks up
  the current output; a session belongs to its sink — alive or
  Unavailable, no retargeting, no selection stealing.
- **Adaptive UI.** Adw.MultiLayoutView two-column measurement window,
  client-side modality, draw budgets, the PeqView component shared by
  device and taste, cards everywhere (device, taste, preamp, ring,
  takes), and a long tail of layout fixes.
- **Integration and CLI.** `install_full` as the one source behind both
  `--install` and the GUI dialog; `--uninstall`; symmetric
  `--list-sinks` / `--list-sources`; the launcher's tools gate.

## Direction

- **The stress probe (distortion hunts itself).** The
  architect's thesis: if it is given to a human, a robot can
  be taught -- the automation must end up better than any
  leather bag. His ear-sweep found the iLoud floor at 54 Hz;
  the probe automates that ear. After the sweep, take what
  measured QUIET -- exactly the regions the fit will boost --
  and replay them at the TARGET loudness, the drive the
  post-EQ chain will actually apply: distortions invisible to
  the polite sweep (rub, buzz, grunt) surface right where the
  equalizer would summon them. Detection is the same mic and
  cal listening for what does not belong -- harmonic energy
  at 2f/3f and burst-shape garbage against the clean tone --
  yielding a distortion-vs-frequency curve AT a loudness. Its
  fruits, in order: the floor finds itself (the lowest clean
  frequency at THIS volume -- the volume-tracked floor door
  gets its data source, floor(L) is this curve's zero
  crossing); per-band boost caps (a dip that grunts when
  driven is an unfillable null with a physical reason -- the
  solver's cap doctrine earns evidence); and guard rails for
  the probe itself (short gated bursts, protective abort on
  runaway, the neighbors' ceiling respected). The ear stays
  the referee; the robot merely runs the sweep it was taught.

- **The hole earns a passport (loopback cal).** Born at the
  architect's speaker bench: with an analog hole in the chain
  the interface itself colors the sweep, and the cure the
  field already practices is loopback calibration -- output
  wired to input, the card's own transfer measured; REW does
  this today and exports a soundcard cal file. The doctrine
  closes on itself: a hole has no serial, but it can carry a
  MEASURED transfer function -- a passport earned by
  measurement, not by name. Shape at the smallest honest
  size: import the ready REW cal onto a per-sink out-cal
  slot; a take then testifies two-sided, cal_sha for the mic
  and out_cal_sha for the hole, applied as a chain -- and the
  whole provenance grammar (biography, jurisdiction, the
  cloud) generalizes to the second axis with no new law. An
  in-house loopback wizard can come later. Promotion decided
  after the exchange ships.

- **The audition room (A/B on the listener's own material).**
  Born from the architect's IEM hunt: YouTube comparisons are
  a fixed pair on fixed unlicensed tracks, and the exchange
  can beat that on every axis -- any pair from the base, the
  listener's OWN material, blind ABX with a score, and curves
  that carry provenance stamps instead of trust-me. The math
  is kind: simulating X and Y through the listener's unknown
  headphones Z is (X-Z) vs (Y-Z), whose difference is X-Y for
  any Z -- the comparison is exact even when Z is not in the
  base, only the absolute timbre is colored. Processing lives
  entirely in the browser (Web Audio, a delta-curve filter),
  so the material never leaves the listener's machine --
  licensing and privacy solved by architecture. The page owes
  one honest line: frequency character transfers; distortion,
  fit, insertion depth and unit variance do not. A third room
  of the reading hall beside the index and the pages; the
  package stays bare. Promotion decided after exchange v1.

- **Frames and targets (architecture settled; the debate narrowed to
  scope).** Two domains that never mix. Playback stays device + taste
  + preamp, forever; everything frame-related lives on the MEASUREMENT
  side. A canvas carries frame provenance (the rig, or
  bridged-via-<bridge.json sha>); the measurement card shows it and
  the fit consumes it silently, translating canvas or target between
  rig coordinates BEFORE fitting. A rig delta must never enter the
  PipeWire chain: applying it in playback would "fix" the listener's
  ear for the difference between stands.
  The listener's anchor is itself the target: "EARS-flat plus the
  taste layer" IS the perceptual goal expressed in EARS coordinates,
  and a target in any other frame is anchor + D from the bridge. The
  bridge is the old Contra -- frame-compatibility machinery -- made
  empirical: D is measured, not read from a PDF. The canonical bridge
  should average 2-3 reference IEMs (seat-rig interaction is
  per-device); a single ORIGIN is the first approximation.
  Amp inserts (the UTWS case): a combo is just another device with its
  own sink and its own measured profile -- the amp's delta rides
  inside for free, no fourth playback layer. A same-rig differential
  delta (wired vs through-the-amp, the best case for EARS: same-rig,
  full-band trust) is a SYNTHESIS aid only -- predict a starting
  profile without remeasuring -- and is never transferable across
  IEMs: output impedance interacts with the specific IEM's impedance
  curve.
  Next concrete steps: bridge ORIGIN across EARS and the 711 clone
  when it arrives; add the frame property to the canvas. Deciding
  experiment for full arbitrary-curve targets stays: an EARS-flat
  profile plus a taste layer on one IEM should match an
  EARS-to-Harman profile without taste, by ear and by curve; if it
  does not, targets earn their complexity.
- **Profile exchange service.** Static, sha-addressed index of `.pdeq`
  packages first (a git repo can be the backend); accounts, ratings and
  comments only if the static thing proves too small.
- **Verified measurements.** Opt-in device-fingerprint sharing once the
  service exists, a popularity list, and the maintainer verifying the
  top of it -- by buying and measuring, or by cross-checking submitted
  packages against published curves.
- **Hardware PEQ, the full story.** Per-device capability tables (band
  counts, Q ranges, shelf types, preamp granularity), bank naming and
  multi-bank export where the hardware has them.
- **Mobile (Android and iOS): the bud-side doctrine.** The platform
  fact first: iOS has no system PEQ and cannot have one -- third-party
  apps cannot touch each other's audio, Headphone Accommodations serves
  AirPods/Beats only. So the one common denominator for a
  give-and-forget solution is DSP inside the earbuds via the vendor's
  companion app, and that path is primary, not a fallback: it survives
  phone swaps, adds no phone battery cost or latency, and covers every
  source. Vendor graphic EQs hide their band shapes, but the DSP sits
  in the bud, so the shapes are MEASURABLE: one slider at a time to max
  on the rig, subtract the zero run, and the fit degenerates into
  least squares over the measured basis -- exact gains for hand entry,
  the missing import stops hurting. Measure in the ANC mode the person
  actually wears, with HearID/adaptive presets off. Target curves for a
  dead donor device (the Falcon case): take the DELTA of the two
  models from ONE published measurement base -- same-rig differences
  transfer across frames far better than absolute curves. On Android,
  Wavelet is the optional upgrade (session-effect global EQ, zero
  hassle, imports GraphicEQ); RootlessJamesDSP is a true parametric
  but lives on stream capture -- latency, DRM apps silently escaping,
  permission rituals -- fine for an enthusiast, wrong for a gift. The
  export wizard's registry (sprint item 1) is where all of these live
  as targets.
- **Advocacy.** Write-ups of the established facts below (the BT
  loudness/limiter findings deserve their own post), short demo videos,
  and a comparison page against static AutoEq presets.

## Parked

- **Flathub, parked at the door.** Submission flathub/flathub#9484
  was closed under Flathub's Generative AI policy, which covers
  AI-assisted code and content -- a line this project's development
  openly falls under, so the closure is not contested. The manifest
  is real, CI-built for both arches, and ships as bundles on every
  release already; what a store would add is auto-updates and
  discovery. Re-engage if the policy matures the way its own
  "mature, well-maintained projects" exception hints, or ship an
  own OSTree repository instead and owe nobody a door.
- **The foreign hand.** Gated on PipeWire's filter-graph read-back
  shipping in a release and reaching Fedora (master already exposes
  applied graphs on the properties -- pipewire#5345 carries our
  use case; as of Oct 14 no release does, 1.6.9 reports an empty
  string). Then: read `filter-graph.0` before writing; non-empty
  and not what we last wrote means another writer -- surface a
  takeover-or-leave choice, per device, remembered. Ownership of a
  sink's EQ belongs to the user; the tool's job is to make the
  contest visible and the choice durable. Rejected in advance:
  silent clobber (a war nobody sees), silent yield (breaks the
  persistence promise silently), and chaining into `.1` (two
  competing corrections are acoustic garbage; chaining is for
  complementary filters).
- **Measure-window Undo, sitting-scoped.** One undo stack for the whole
  window, alive from open to close: take deletion, cal reassignment,
  re-fits -- every destructive gesture joins it, and the stack dies with
  the window (the profile on disk stays the artifact). The cal-reassign
  dialog deliberately shipped without a toast-Undo because a single
  orphan Undo raises worse questions than it answers; once the stack
  exists, per-action Undo toasts become legitimate and can return.
  Ctrl+Z binding rides along.
- **Profile state journal** (old Task 4): log band edits / bypass / preamp
  changes with timestamps; `--dump-state` for measurement notes.
- **Hardware: 711-clone coupler** (old Task 5): buy an IEC 60318-4 clone,
  verify against 3–4 IEMs with published curves, record its trusted
  range. EARS remains for over-ears and same-rig deltas.
- **Upstream: DeaDBeeF SRC + pipewire-alsa** (old Task 6): file with a
  synthetic repro; two rate converters with conflicting ratio views.

---

## Established facts worth not re-deriving

- **The capture peak follows the level one for one within a chain**, and
  is the only honest witness of what a rig was given: a knob decibel is
  not a decibel on a sink that keeps its own scale. His JBL delivered
  7.0 dB for 4.1 asked, 1.72 to one, and a wall predicting from the knob
  clipped two rigs at 0.0 dBFS. IT WITNESSES ONLY WHILE WHAT SETS IT
  STANDS BEFORE THE CONVERTER. A digital input gain scales the peak in
  the file and leaves the voltage at the converter alone, so a take can
  read -24 dBFS with the converter at full scale and nothing in the
  recording shows the difference. **Corrected Sep 10: within a walk
  the step a rung took is the rise of its own curve, not of the
  peak.** The raw recording's largest sample is where the harmonics
  add to the fundamental, so the peak rose 0.14 dB more than the
  curve at the top of the Origin's walk and 0.29 dB on the Denon
  (Farina 2000; e2b5225).
- **Both capture gains the UMIK-2 publishes are digital**, and the
  descriptor says so without any measurement: its whole audio topology
  is input terminal 1 (Microphone) -> feature unit 11 -> output
  terminal 22 (USB Streaming), and a feature unit downstream of the
  input terminal is downstream of conversion. There is no extension
  unit. The walk agrees: cut one control by hand and drive the other
  over its whole range and the recorded noise falls from -120.9 to
  -170.0 dBFS, slope one, no floor anywhere, seven runs, which the room
  scaled by 100 dB predicts within half a decibel while every
  arrangement putting the converter inside the pair predicts 3 dB
  higher. The analogue preamp is on the vendor HID interface only,
  hidraw, usage page 0xFF00, 64 bytes each way.
- **One element can wear more than one control, and the desktop drives
  one of them.** The UMIK-2's feature unit publishes a master plus two
  per-channel volumes; ALSA splits them into `Mic Capture Volume`
  numid=4 (the pair) and numid=5 (the master), each 0..50 over -50..0
  dB, and PipeWire moves the pair while never looking at the master.
  The M62 is the other shape of the same trap -- there the digital trim
  and the analogue preamp really are two elements. Either way a fader
  honestly at 100% describes nothing, and `fader_kind`, which reads one
  route and one element, covers neither.
- **Two sweeps of one rig at one level disagree by about 0.2 dB** in the
  midrange and close to 1 dB at 20 Hz over Bluetooth. So a 2 dB step is
  readable and a 1 dB step is not, and a loss must clear TWICE that
  scatter to be worth drawing -- a loss is a difference of two sweeps.
- **Loss of output is monotone in level.** A port breaks up faster with
  flow, a driver runs further out of stroke, a limiter clamps harder;
  none of them improves with drive. That is what lets a bisection find
  where the shading starts, and what lets "below the quietest rung" be
  inferred rather than measured.
- **A sweep is not too gentle.** The suspicion that music asks more,
  because the whole low end arrives together, does not survive the
  arithmetic: displacement goes as amplitude over frequency squared, so
  at a fixed peak every added tone makes the others quieter faster than
  the sum grows. Four bass tones ask 0.87 of a lone tone's stroke, seven
  ask 0.67, and one real track's loudest bass moment asks 0.68. A
  multitone probe agreed with the sweep to within its own scatter.
- **Held tones do not sag.** Across five rigs a tone held for 8-23 s
  moved by at most 0.19 dB, so neither voice-coil heating nor a slow
  limiter acts within a walk. Pauses between rungs are unnecessary.
- **With the preamp on Auto, a floor buys no level.** Auto follows the
  chain's own peak, so headroom the floor frees goes straight to the
  neighbouring bands: the marks climb rather than clear. Cutting to
  40 Hz doubled them and cost thirteen points; with the preamp fixed the
  same floor took the safe level from 70% to 95%.
- Sink monitor in in-node topology = **pre-EQ** (Bypass-toggle experiment);
  post-EQ level must be computed, which the meter does.
- BT absolute volume does not protect against quantization clipping;
  software stream gain does.
- Constant BT latency is harmless to sweep FR; clock drift and timing
  references are the actual hazards — average by magnitude only, never
  time-domain, and never use an acoustic timing reference over BT.
- EARS: trust < ~2 kHz absolute, full range for same-rig deltas; the
  ~2.3 kHz cut is listener-validated by ear, the 14.4 kHz filter trio was
  rig resonance.
- PipeWire's param_eq shelves use plain Q (`alpha = sin(w0)/(2Q)`), not the
  RBJ slope form; `perdeviceeq.eq` and the audit match it to 1e-10.
- Mic calibration files are per-incidence-angle: 0° aimed at the active
  speaker for per-speaker sweeps, 90° up for speakers all around; below
  the room transition they coincide.
- Mic cal promises a flat pressure sensor at the capsule and nothing
  more: after honest cals it is the SIMULATED EARS that differ (pinna
  vs coupler), so a nonzero bridge D between two calibrated rigs is a
  legitimate frame difference, not evidence against anyone's file.
- When one coupler serves both channels in turn (EARS with earbuds
  seated one at a time), take-to-take spread reads SEATING, not
  hardware -- the FL/FR trust-band asymmetry on the first live bridge
  was left-bud-in-right-ear repeatability. One coupler + one cal also
  keeps the fit's cross-channel balance trims valid by construction.
- **A room is not stationary.** On a coupler the error of a
  rung-to-rung difference does not grow down to an SNR of -15 dB
  (median 0.03 dB, spread 0.1 dB): a swept-sine deconvolution spreads
  stationary hiss. In a room three sweeps at one level on a UMIK-2
  disagreed by 20.5 dB in the bins below 0 dB SNR. So the scatter
  between repeated sweeps gates a reading, not a model of noise by SNR
  (06d1f42, 856503d).
- **`param_eq` is eight ports wide whatever its config names.** A
  filter graph that names no ports takes its width from that
  descriptor, and on a mono node PipeWire up to 1.6.9 averages the
  seven unwritten outputs into the one channel: full-scale DC
  (pipewire#5473, fixed in master by 42cef4c). Naming one port pair per
  chain makes a graph exactly as wide as its sink on every version. It
  also keeps eight filter banks: `filtersN` is clamped to 1..8, so on a
  ten-channel node `filters9` and `filters10` land in bank 8 on top of
  `filters8`, and channels past the eighth need a second node.
- **An empty string removes a filter graph** (audioconvert's
  `load_filter_graph()`); a flat `param_eq` is still a graph, with all
  of its width.
- **PipeWire's metadata announces a delete only for a key it holds**
  (`impl_set_property`). After a PipeWire restart it holds nothing a
  hook persisted, so a delete is not a signal a hook can rely on; a
  written value is.
- **WirePlumber escapes the keys of its state files**: a backslash, a
  space, `=`, `[` and `]` become `\\`, `\s`, `\e`, `\o` and `\c`, so the
  UCM port `[Out] Line1` is stored as `\oOut\c\sLine1`.
- **Restarting WirePlumber drops Bluetooth audio.** The bluez5 monitor
  lives in WirePlumber's process, so the endpoints it registered with
  BlueZ go with it; whether a headset comes back by itself depends on
  the headset (his JBL does not).
- **Two identical USB cards differ only by their bus path.** His two
  M62s report the same `device.serial`, and ALSA card numbers follow
  the probe order. WirePlumber makes names unique by appending `.2`,
  `.3` in the order it sees objects, and frees a name only when its
  object is removed.
- **PipeWire 1.6.x does not report a node's loaded filter graph.** Its
  Props carry `audioconvert.filter-graph.disable` and an empty
  `audioconvert.filter-graph`; master reports what is loaded
  (d4329600d). Until a release carries that, what a node plays is
  checked by ear.

## Upstream notes

* **Resolved:** PipeWire filter-graph ate softVolume/softMute so channel
  volumes applied twice and the level collapsed after enabling EQ --
  fixed in 1.6.8 (work item 5344).
* **Resolved in master, not released:** a filter graph on a mono sink
  turned silence into full-scale DC -- pipewire#5473, filed Sep 14. Our
  MR 3003 was closed: Wim Taymans fixed it in 42cef4c ("filter-graph:
  support PORT_PAIR nodes"), since the MR would have broken the sofa
  filter. No release up to 1.6.9 carries it; 5.1.0 names its ports and
  does not depend on it.
* pipewire#5423 (an ALSA node under UCM: port names follow the
  driver's channel map while audio.position does not), moved out of
  wireplumber#983 on Aug 12 -- open, no reply as of Oct 14.
* alsa-ucm-conf PR 826, the Topping M62 profile -- open. On Oct 3 he
  asked for its CI run to be approved; alsa-ucm-conf master has not
  moved since Aug 31 (checked Oct 14).
* The M62 kernel series (analogue controls, the output selector) has
  been under review as v8 since Oct 3.
* WirePlumber: a fresh `stream.capture.sink` stream against a settled BT
  sink with an in-node filter-graph deterministically comes up with one
  monitor port unlinked; only a node reconfigure (graph republish)
  completes the links. Repro + workaround live in per-device-eq (the
  400 ms republish nudge and the dead-channel watchdog).
* xdg-desktop-portal, ready to file: no portal exists for restarting a
  user service, and the only mechanism today -- flatpak-spawn --host
  via org.freedesktop.Flatpak -- is arbitrary-code-on-host by design,
  so every app that installs host-daemon integration ships an
  instruction sentence instead (ours: the WirePlumber hook needs one
  `systemctl --user restart wireplumber`). Any proposal must dodge the
  trap our own case demonstrates: restarting a unit whose inputs the
  sandbox writes IS executing what you wrote, so the design has to be
  consent-shaped -- units declared statically in the manifest (the
  Background/autostart pattern), a dialog naming the unit, remembered
  per-unit grants, restart-only (no start/stop/enable of arbitrary
  units), user units only. Precedent for lifecycle portals: the
  restart-self portal, Flatpak 1.0. The filing-ready issue text is
  drafted (see the sprint notes); target: flatpak/xdg-desktop-portal.
* gnome-shell (observed once, repro unknown): the quick-settings output
  picker's checkmark desynced from the actual default sink while
  per-device-eq was active. Capture kit for the next occurrence:
  `pw-metadata 0 default.audio.sink` vs `pactl get-default-sink` vs the
  picker's checkmark — whichever disagrees is the stale layer; run
  `pactl subscribe` during EQ edits to see sink remove/add storms.
