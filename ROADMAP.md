# per-device-eq Roadmap

Goal: any output device corrected through per-device-eq should sound the
same to *this* listener — measured correction per device, taste per
listener, headroom that never lies.

## Shipped

### 5.2.0 (Nov 9, 2026)

- Only a final node, a card or a network sink, is corrected: network
  outputs play the taste, and nothing is set on a filter-chain, a
  loopback or a null sink. A default that is not a device is shown as
  it is, with its correction disabled.
- The hook moves to protocol 4, and every change to the hook now comes
  with a new number, so the window offers to install it again.
- An edit in the main window, the playback fader and the floor handle
  are heard as they move. Graph writes go through one writer, at most
  20 a second per device, and a write the server refuses is logged
  with its key. The other bound devices get a taste change when the
  drag ends.
- Undo keeps a manual preamp, a turn of the preamp no longer copies
  the profile, and a text import brings the bands without the preamp.
- A removed profile stops playing on every device it was bound to.
- A floor set on Default or a built-in profile makes a copy, as a band
  edit does.

### 5.1.0 (Oct 14, 2026)

- The taste plays on every device, one with no profile of its own
  included, through the hook's common entry (hook protocol 3).
- The window and the hook build what a device plays with one function.
- A graph names one port pair per channel, so a mono node such as a
  headset in Handsfree no longer gets full-scale DC (pipewire#5473).
  Clean and Bypass remove the graph instead of applying a flat one.
- A measurement takes the device's own EQ off under its device key.
- The hook reads back keys with a space or a bracket, as UCM ports have.

### 5.0.2 (Oct 4, 2026; profile schema 7)

- The level search and the level passport: walked per channel from
  below the working level, kept in the profile with its conditions.
- Distortion per take (THD, H2, H3, THD+N), graphed in the takes panel.
- The floor is set by hand (Off, Set), with the level map under its
  handle.
- A device is its node and the port in use, on the microphone side too.
- The routing rework: capture columns declared by hand, targets in the
  sink's card.
- A profile carries its measurement, rig and calibration passports, and
  a package is named by its content.
- Faster fits: the argmax mask at saturated anchors and a windowed joint
  refine.
- The Auto preamp lands again when the floor is toggled.
- Profiles from before 5.0 are skipped, not migrated: a take either
  recorded its distortion or it did not.

### 4.x and earlier

- Measurement: Farina sweep through the selected sink, per-capsule
  calibration, averaging across seatings, per-take quality gates, and a
  biquad fit whose range follows the take-to-take spread.
- Headroom: an estimate that includes the taste, live post-EQ meters,
  the Auto preamp and a session clamp.
- The taste layer: named preference EQs over any profile on any device.
- One undo timeline for device, taste and profile switches.
- A measurement session belongs to its sink; only New takes the current
  output.
- The adaptive two-column measurement window and one band editor for
  the device and the taste.
- Integration and CLI: `--install`, `--uninstall`, `--list-sinks`,
  `--list-sources`.
- The export wizard (3.2.0) and the profile package.
- The AppImage (4.0.1), Flatpak bundles and COPR.
- CI that sees the GUI: pyflakes, every `.ui` loaded, the suite under
  Xvfb, the Flatpak built for both arches.
- The GNOME HIG pass, with its audit in CI (HIG.md).
- A calibration slot shows the file's history across all profiles, and
  the inventory reassigns calibrations in bulk.

## WIP

- The next release, in main since 5.2.0:
  - The correction, the taste and the preamp are separate filter graphs
    on a node (hook protocol 5): a change of the taste or the preamp is
    one write, and every device that is playing hears it as the hand
    moves. A device's preamp travels with its correction.
  - A measurement no longer writes the device's EQ: it names the device
    to the hook, which keeps it bare for the sweep and then plays what
    it holds for it.
  - An edit made just before the window closes is saved, and what the
    window sent reaches the hook; a sweep the close cancels gives its
    device back.
  - Bypass takes every output raw and stays, through a change of
    output, an edit and a restart, until it is released.
  - A channel's balance is a number of the channel, not a band: a
    Balance row above its band table, either sign by hand. Profile
    schema 8; tools/convert_v8.py converts schema 7 profiles and
    packages once.
  - An export carries Safe over the channels it carries, each at its
    balance; the window's preamp, Auto or set by hand, stays home.
  - A saved profile plays on every device bound to it.
- A reference profile and a comparison of desktop speakers (iLoud Micro
  Monitor, NUX AXON 3, Adam D3V) with and without the sub. Waiting on a
  place for the sub: a room notch of about 20 dB at 40-41 Hz.

## Planned

- One undo step per drag.
- Find why one graph write of a pass went missing (Nov 7).
- Level passport, to decide: the first sweep of a search; the mirror
  mark; rebuilding from a rung and from a probe; takes from sittings
  with different conditions on one canvas; whether clipped and flagged
  takes count toward the two takes a band needs (TRUST.md).
- Level passport, faults found by reading the code: its conditions are
  never read; the knee cap depends on the order of channel names; a
  rebuild from a rung above the base is not checked for seating; the
  heading blames the capture for every walk without a knee; two
  tooltips describe old behaviour; a click below the rule can select
  the next row.
- Level map: a map is bound to its chain, and a common axis would carry
  it to another amplifier; whether every rung needs two sweeps
  (tools/scatter_probe.py).
- The UMIK-2's analogue preamp over its vendor HID interface, with the
  M62 work, and only for rigs whose own chain already reaches their
  border.
- Once a PipeWire release reports a node's loaded graph (master
  d4329600d): check the hook's half of "a node seen before its card",
  and detect another writer's graph and offer to take over or leave it,
  per device.
- One undo stack per measurement-window sitting: take deletion,
  calibration reassignment, refits.
- Loopback calibration of an output: a REW soundcard cal per sink, so a
  take carries both the microphone's and the output's calibration.
- Frames: a canvas carries the rig it was measured on; bridge ORIGIN
  across EARS and the 711 coupler; arbitrary targets only if EARS-flat
  plus the taste fails to match an EARS-to-Harman profile.
- Hardware PEQ export: per-device capability tables, bank naming,
  multi-bank export.
- Earbuds' own EQ and phone EQ apps as export targets: measure a vendor
  app's band shapes one slider at a time and fit gains for entry by
  hand.
- Write-ups of what the measurements established, demo videos, a
  comparison with static AutoEq presets.
- Flathub: the submission was closed under its AI policy
  (flathub#9484); re-engage if the policy changes, or ship an own
  OSTree repository.
