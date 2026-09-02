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
    of the five rigs here. Lowering the input gain for the walk
    would move that ceiling -- and this is the one place in the
    project where the right answer may be NOT to measure.

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
  went green.
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
  honest interaction with headroom.
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
  use case). Then: read `filter-graph.0` before writing; non-empty
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
  clipped two rigs at 0.0 dBFS.
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

## Upstream notes

* **Resolved:** PipeWire filter-graph ate softVolume/softMute so channel
  volumes applied twice and the level collapsed after enabling EQ --
  fixed in 1.6.8 (work item 5344).
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
