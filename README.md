🇷🇺 Читайте инструкцию на русском: [README.ru.md](README.ru.md)

# per-device-eq — measure and correct any output device, inside PipeWire

`per-device-eq` measures your speakers, headphones or IEMs with your own
measurement rig, fits a parametric EQ to the result, and applies it as an
in-node filter-graph **directly inside the real sink** — no virtual sink, no
extra node, no background process. A small WirePlumber hook re-applies the
correction whenever the device starts playing, so it survives reboot, hotplug
and Bluetooth reconnect with nothing of yours running.

On top of the per-device correction sits a **taste layer**: your personal EQ,
composed after whatever profile is active on every device -- a device with
no profile of its own included -- without ever touching the measured
profiles.

![Per-Device EQ — Main window](per-device-eq-main.png)

![Per-Device EQ — Measurement window](per-device-eq-measurements.png)

Projects like REW, AutoEq and EasyEffects inspired this one with the results
they achieve; I wanted the same correctness with more comfort, so the whole
loop — measure, fit, apply, keep — lives in one app.

How the fit thinks — the gold target it lawfully aims at, why that target
is not a straight line, and the full mathematics of the solver — lives in
[SOLVER.md](SOLVER.md). What a measurement must earn before the fit will
trust it — the score, the controlled band, and why a single sweep earns
nothing — lives in [TRUST.md](TRUST.md).

### What per-device-eq gives you

- **Built-in measurement.** Sweep generation, capture, per-capsule mic
  calibration, take averaging and a constrained parametric fit — the app is
  the instrument. No external measurement software involved.
- **Incremental takes.** Every accepted sweep is persisted the moment it
  lands. Add takes across sessions, delete a bad one, re-fit — the profile
  only improves.
- **What the rig can still follow.** The level search walks upward past
  the level it measures at and records where the rig stops delivering
  what the correction asks of it. The volume strip shades the part of
  the knob it cannot follow, the curve turns red there and calls itself
  *what you hear*, and a line says what floor would stop asking. Every
  take also stores its harmonics and its own noise floor.
- **Per output device.** Each output, together with the port it plays
  through — built-in speakers, HDMI, a specific Bluetooth headset (by MAC)
  in Headphones and in Handsfree, a card's line out and its headphone
  out — remembers its own EQ and gets it back automatically.
- **Taste, separated from correction.** Named preference layers
  ("Basshead", one per listener on a shared machine) ride over any profile
  and never flip a profile's `edited` mark. They play on every device, one
  without a profile included.
- **Honest headroom.** A shared preamp with a Safe suggestion computed over
  the *composition* (profile + taste), a live post-EQ meter, and a fit that
  lands gain-staged instead of clipping.
- **Interactive editor.** Drag bands on the response graph, per-channel EQ,
  bypass A/B, Undo/Redo (`Ctrl+Z` / `Ctrl+Shift+Z`), a trust line over the
  graph and one-click Re-fit.

---

## Requirements & install

### AppImage (any distro with PipeWire)

Grab `PerDeviceEQ-<version>-x86_64.AppImage` from the
[releases page](https://github.com/NTMan/PerDeviceEQ/releases),
make it executable and run it -- no root, no install:

    chmod +x PerDeviceEQ-*-x86_64.AppImage
    ./PerDeviceEQ-*-x86_64.AppImage

The bundle carries Python, GTK 4 and libadwaita; PipeWire's
own client tools (`pw-dump`, `pw-cat`) come from your distro,
as on every PipeWire desktop. Floor: glibc 2.42 -- Fedora
43+, Ubuntu 25.10+/26.04 LTS, and any rolling distro (Arch,
Gentoo, openSUSE Tumbleweed) qualifies by nature; Debian 13
and Mint 22 sit below the floor and are out until a
source-built-stack build on an older base earns its keep. The
build base tracks the oldest supported Fedora. The `.zsync`
file next to it is the auto-update channel for
AppImageUpdate.

**PipeWire ≥ 1.6** (the in-node `audioconvert.filter-graph` is required),
**WirePlumber**, **GTK 4** with **libadwaita ≥ 1.6**, **PyGObject**,
**PyCairo**, **Python 3**. Measuring and the live meter additionally need
**python3-numpy**, **python3-scipy** and **python3-soundfile**. At runtime
the app also calls `pw-metadata` and `pw-dump`; if either is missing it says
so on launch.

### Fedora (COPR) — recommended

```
sudo dnf copr enable mikhail/per-device-eq
sudo dnf install per-device-eq
```

This installs the `per-device-eq` launcher, the WirePlumber hook (under
`/usr/share/per-device-eq/`), and the desktop entry + icon. Start it from
your application menu as **Per-Device EQ**, or run `per-device-eq`. On first
launch a banner at the top of the window says the integration is not
installed; its **Install** button puts the WirePlumber hook into your user
session (WirePlumber restarts once). Remove everything later with
`per-device-eq --uninstall`. After that the EQ is restored automatically on
every reboot and reconnect.

### The hook outlives the package

Removing the package (`dnf remove per-device-eq`,
`flatpak uninstall io.github.ntman.PerDeviceEQ`) does NOT remove
the per-user hook: an RPM must not touch user homes, and Flatpak
has no uninstall scripts at all -- this is platform design, and
it is the same for both. Remove the integration BEFORE removing
the package (*Remove integration* in the main menu, or
`--uninstall`). If the package is already gone, the hook keeps
applying the last EQ; both installed files carry these removal
instructions in their headers:

```
rm ~/.local/share/wireplumber/scripts/90-per-device-eq.lua
rm ~/.config/wireplumber/wireplumber.conf.d/90-per-device-eq.conf
systemctl --user restart wireplumber
```

### Updating

The app hands the hook its EQs through PipeWire metadata, and an update
can change the format of that exchange -- 5.1.0 did (hook protocol 3).
The installed hook is a copy and does not update with the package. Until
it is replaced, the window's banner reads *The hook speaks protocol 2,
this app speaks 3*; its **Retry** button installs the hook that came with
the update and hands it every device's EQ, the same as
`per-device-eq --install`. Installing restarts WirePlumber once, and a
Bluetooth headset playing at that moment drops; reconnect it if it does
not come back by itself.

### Run from source

```
# Fedora; other distros ship the same tools under different package names:
sudo dnf install gtk4 libadwaita python3-gobject python3-cairo \
    python3-numpy python3-scipy python3-soundfile \
    pipewire pipewire-utils wireplumber
git clone https://github.com/NTMan/PerDeviceEQ.git
cd per-device-eq
chmod +x per-device-eq.py
./per-device-eq.py
```

To install the system integration from a checkout -- the WirePlumber
hook plus the menu entry and icon (reversible; writes only under
`~/.local` and `~/.config`):

```
./per-device-eq.py --install
./per-device-eq.py --uninstall
```

### Build the RPM yourself

From the root of a checkout on Fedora -- the same build CI runs on every
push:

```
sudo dnf install rpm-build dnf5-plugins
sudo dnf builddep per-device-eq.spec
rpmbuild -bb --build-in-place per-device-eq.spec
```

The package lands in `~/rpmbuild/RPMS/noarch/`.

### Flatpak

Every release carries a bundle for x86_64 and one for aarch64 on the
[releases page](https://github.com/NTMan/PerDeviceEQ/releases):

```
flatpak install --user ./per-device-eq-x86_64.flatpak
```

It needs the GNOME 50 runtime, which comes from Flathub
(`flatpak remote-add --user --if-not-exists flathub
https://dl.flathub.org/repo/flathub.flatpakrepo`). The app itself is not on
Flathub: the submission was closed under Flathub's policy on AI-assisted
code. Install the WirePlumber hook with *Install integration* in the main
menu; the sandbox cannot restart WirePlumber, so the app shows the command
to run on the host.

---

# Measuring a device

You need a measurement rig the device can play into: an ear/headphone rig
(miniDSP EARS or a 711-class coupler) for headphones and IEMs, or a USB
measurement mic for speakers, plus its per-capsule calibration files.

Mount the device on the rig, then open the profile picker on the output
you are correcting and press **+** (New) — editing an existing profile
opens the same window on the profile's *own* device. The window is read
top to bottom, and that order is a dependency rather than a habit: the
microphone is settled first and needs to know nothing about what will be
played; the target comes second, because without one there is nothing to
play to; the level comes last, when both ends exist. Nothing is guessed
on your behalf, and every answer is remembered, so the next profile
measured on the same bench starts at step 4.

1. **Pick the measurement mic** in *Measurement mic* — the input the
   sweep is captured on.
2. **Declare the capture channels it has.** Press **+** on the strip,
   choose a target and then the capture channel that hears it; the
   submenu shows a live level per channel, so the answer is a knock and a
   click. A rig has the channels a hand declared and no others: one
   declared channel captures every target, and with several each target
   is pointed at one. Each channel carries its own **calibration** file
   (*Choose…* / *Change…*), and the bar on its tab is live — knock on a
   capsule and the tab it arrives on moves, which is how you tell which
   is which without unplugging anything.
3. **Calibrate the sensitivity.** The button beside the gain fader walks
   that input's gain **in silence** — nothing is played — and finds the
   point where the microphone's own noise stops being buried in the
   converter's. Below it signal-to-noise is being thrown away; above it
   more gain buys nothing and costs headroom. An input with no analogue
   gain of its own, like a UMIK-1, says so under the fader and skips
   this step; one whose gain can only cut, like a UMIK-2's, is held at
   full and skips it too.
4. **Pick the output** in *Output* — where the sweep is played.
5. **Declare the channels to measure.** The targets start as the
   output's named channels. **+** adds one and asks which output channel
   plays it, and the cross removes a target that has no takes yet. Each
   capsule's tab prints which targets it captures.
6. **Measure the level.** The button beside the output's fader searches
   for the playback level with probe sweeps, then walks a ladder upward
   from 12 dB below that level, for the channel in view (see *What the
   rig can still follow*). It is one level for the whole rig: the fader
   shows it and holds it while the profile has a passport. Without one
   the fader is yours, and it starts at the level last used for this
   output and microphone.
7. **Land about three clean takes per channel.** A green dot marks a
   clean take: the sweep was heard, nothing clipped, and the curve stands
   at least 10 dB over its own noise in most of the band. With three or
   more takes, one that differs from the others by more than 3 dB over a
   third of an octave, where the others agree, turns amber and the takes
   card says where. Re-seat the device between takes: the take-to-take spread
   is what tells the fit which frequencies to trust. Every take is saved
   the moment it completes, and the trash can on a take removes it.

The card above the takes shows the channel's mean response with the
spread band; the **EQ range** handles below it follow the trustworthy
band until you drag them, and **Bands** sets the fit's filter budget.
Close the window and the fit runs right on the main window's graph with
a per-channel progress bar, lands gain-staged (Safe preamp), and the
profile is playing. The **Auto** button above the graph re-fits after you
add or remove takes later, and the Device card's header line says how far
the fit can be trusted and whether it is stale.

### What a take confesses

A take records more than the response. It confesses the second and third
harmonic, the total harmonic distortion and the noise the room and the
chain contributed; they are drawn under the response in the takes list
and summarised in the panel's header as `THD@1k`. Where a harmonic falls
outside the sweep the pen stops instead of guessing, and the graph says
*no harmonic evidence* over that region.

### What the rig can still follow

A correction assumes the rig is LINEAR: ask for 6 dB more at 60 Hz and
expect 6 dB more. Past some level that stops being true — a port
breaks up, a driver runs out of stroke, a limiter clamps — and the
prediction on screen is then simply wrong.

The level button in the measurement window measures where that begins,
and the profile keeps the answer as a **passport** for each channel,
drawn at the top of the window's right column. One press plays two
things:

* **The search.** Probe sweeps, starting at 15% of the volume, look for
  the level that puts the capture peak between −11 and −2 dBFS with at
  least 41 dB over the noise, aiming at −10.5. Each probe is a row below
  the rule, with its verdict: *loud*, *quiet* or *ok*. Takes are
  recorded at the level the search settles on.
* **The ladder.** It starts 12 dB below that level and plays the first
  rung twice: how far the two sweeps disagree is the scatter every later
  reading is judged against. It reaches the search's level in two 6 dB
  strides and goes on in 2 dB steps until the capture has no room left
  under −2 dBFS, the knob is at the top, or twelve rungs are played.
  Each rung is a row above the rule, loudest on top, drawn against the
  rung below with the rise it took as a whole subtracted, so a flat line
  is a rig that followed.

The card's heading says where the rig stopped following (*knee at N%*)
or how far it is known to be linear. Where a knee sits below the
search's level, takes are recorded a 2 dB step under it. While a
passport gives a level, the fader shows it and cannot be dragged.

To redo part of a walk, click a row and press the button again: a rung
is measured again together with everything above it, keeping the rungs
below and without searching; a probe resumes the search from that step.
One press plays at most twelve probes and thirteen ladder sweeps.

What the editor does with that map:

* **The level strip above the device card** duplicates the system
  volume — it reads the sink and writes back to it, it is not a second
  volume. Green is measured and followed, **red** is measured and not
  followed, and **grey is not measured**: a walk often ends because the
  MICROPHONE ran out of room rather than the rig, and painting that
  stretch green would be a guess.
* **The green curve** — the *predicted* response, shown with the eye
  button — **turns red where the rig cannot follow**, and renames itself
  *what you hear*, because that part of it is a measurement rather than
  a forecast.
* **Marks under the graph** show the thirds of an octave involved.
* **A line under them** says how far past the rig this is and the floor
  that would stop asking for it.

All of that is read at the level the rig actually RECEIVES, which is
not the knob: the correction, its preamp, the floor and the taste layer
all stand in between, and they are a curve rather than a number. A
frequency lifted 10 dB against a preamp of −10 gets exactly what the
sweep gave it; two octaves up it gets ten less.

### The Floor

The **Floor** is a high-pass under the correction: the answer to a
woofer asked for excursion it does not have. Sweeping it, everything
above follows the handle live — the strip's shading, the curve, the
marks and the line — so the trade is visible while it is made.

Two things about it are worth knowing before reaching for it.

**With Auto preamp on, a floor buys no volume.** Auto follows the
chain's own peak, so cutting the floor higher frees headroom that Auto
immediately hands to the neighbouring bands: the marks do not clear,
they climb. On one small ported monitor here, cutting to 40 Hz doubled
the marks and cost thirteen points of safe level; the same floor with
the preamp held fixed took the safe level from 70% to 95%.

**And a floor cannot fix what the rig has not got.** Where the marks
say a band is short, the choices are to ask for less of it — a floor,
or less volume — or to give that band to something that can play it.
The tool does not choose; it shows what each choice costs.

### What the map does not cover

* **It belongs to a CHAIN, not to a transducer.** The knob is measured
  through one amplifier; the same earphone on a weaker one reaches a
  different level at the same percentage, and the map does not
  transfer. Re-run the level search after changing amplifier, sink, or
  the card's own analogue level.
* **A rig standing AT its border reads unstably there.** Three walks of
  one earphone differed by 1.5 dB below 30 Hz with nothing at 1 kHz —
  a seal's signature rather than a driver's. That is one specimen; the
  other in-ear rigs tested never showed it, but neither did they reach
  a border at all.
* **Above the loudest rung it says nothing** when the capture stopped
  the walk rather than the rig. That is the grey stretch, and on three
  of the five rigs tested here it is where the walk ended. It is left
  grey on purpose: a speaker past its border makes a noise, an earphone
  past its border may not come back, and on a TWS the modest amplifier
  is itself the protection.
* **Every rung but the first is one sweep.** The first is played twice
  and its scatter judges the rest. Over Bluetooth one rung once came
  back 0.78 dB down across a few dozen hertz that two other runs did not
  see. Wired rigs have not shown it.

### Speakers with a UMIK

Measure at the listening position with the mic at ear height. miniDSP
ships two calibration files per unit and they are not interchangeable:
each corrects the capsule for ONE incidence angle, and the difference
lives in the treble, where the capsule stops being omnidirectional.
This flow drives one speaker per sweep, so for a stereo pair point the
mic **at the active speaker** and load the **0°** file: the direct
on-axis sound is what the correction acts on, and 0° is the curve
measured individually for your unit. For a multichannel rig point the
mic **at the ceiling** with the **90°** file: with speakers all around
there is no on-axis to aim at, and a vertical mic meets every
horizontal arrival at the same 90°, one geometry for every channel.
Below the room transition (roughly 200 Hz) the files coincide, so none
of this matters for bass. "Reseat between takes" still means moving
the mic a hand's width around the seat.

A UMIK-2 has an analogue preamp, but only miniDSP's tool for Mac and
Windows reaches it: both capture controls Linux sees sit after the
converter and can only cut, so the app holds them at full. Its
calibration file is computed for the factory setting of +18 dB, and
moving the preamp moves the sensitivity the file states.

### Taste: your EQ over every device

The **Taste** card holds your preference EQ layers: named, hand-dialed EQs
composed after the device's correction on *every* device. Its button shows
the active layer and opens **Preference EQ layers**, where **+** makes a
new layer, a click switches to another — handy when two people share the
machine — the pencil renames and the cross deletes; **No layer** turns the
taste off. Unfold the card to edit the active layer: the same graph and
band table as the device's. Layers never modify the measured profiles, and
the headroom hints account for the composition, so a bass shelf on top
cannot clip behind the meter's back. The active layer plays on every
output with a sound card, one with no profile of its own included, and a
change reaches all of them at once.

### Everyday use

- **Output picker:** with the pin pressed, as at every launch, the window
  follows the system's default output; release it to stay on the output
  you pick. Ports of a card's other profiles are listed too, and picking
  one switches the card to that profile.
- **Profile picker** (the button in the Device card's header): switch the
  profile bound to the current output and port; **+** measures a new one,
  the folder icon imports a profile shared by someone else, and each row
  can be duplicated, edited or removed. `Default (no EQ)` means no
  correction of the device's own: the taste and the preamp still play on
  it, the floor does not.
- **Bypass** to A/B against the raw sound: it takes the whole chain off
  the device in view — bands, floor, taste and preamp. Loading a profile
  or a device, or editing a band, turns it off. It is not saved in the
  profile, but the hook keeps it: a device left in Bypass stays raw,
  across a reboot too, until the window is opened on it again.
- **Preamp:** **Auto** gives each device its Safe value, the largest boost
  of its composed curve turned into a cut; changing the number switches to
  one manual value for every device.
- **Tune by hand:** drag a point on the graph to move a band, click empty
  space to add one, right-click to remove; or edit the table. Hand edits
  release **Auto** and mark the fit `edited`; pressing **Auto** re-fits
  and asks before discarding them. Prefer making taste adjustments in the
  **Taste** layer, not in the device correction: corrections stay
  measured, and every device keeps sounding equally right.
- **Per-channel EQ:** each tab pairs one of the output's channels with a
  channel of the profile. A profile with one channel plays on all of
  them; otherwise channels pair by name, or by position when no names
  match. **+** plays a profile channel on another output channel; the
  cross on a tab leaves that output without the correction, though it
  still gets the floor, the taste and the preamp.

---

## Command line

```
./per-device-eq.py --list-sinks      # list sinks (default marked with *)
./per-device-eq.py --list-sources    # list capture sources
./per-device-eq.py --list-profiles   # list profiles and their device bindings
./per-device-eq.py --inspect NAME    # dump a sink's params (node.name)
./per-device-eq.py --apply           # send every bound device's EQ, and the taste for the rest, to the hook now
./per-device-eq.py --install         # install the hook + desktop integration
./per-device-eq.py --uninstall       # remove the hook + desktop integration
./per-device-eq.py --bridge A B      # the measured delta between two profiles of one device on two rigs
```

`--bridge` writes to `bridge-out/` unless `--out DIR` says otherwise;
`--published CURVE` adds a trusted curve of the same device, in freq/dB
text, to check B against.

## Files

| Path                                                             | What                                                                     |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `~/.config/per-device-eq/profiles/*.json`                        | your profiles (bands, takes, fit metadata)                               |
| `~/.config/per-device-eq/preference-layers.json`                 | the taste layers and which one is active                                 |
| `~/.config/per-device-eq/bindings.json`                          | per device (`node.name#port`, or `node.name` for a node with no card): its profile, and which profile channel feeds which of its own |
| `~/.config/per-device-eq/ui-state.json`                          | the preamp mode and its manual value, and which of the Device and Taste cards are open |
| `~/.config/per-device-eq/favorites.json`                         | the built-in profiles pinned to the picker                               |
| `~/.config/per-device-eq/mic-profiles.json`                      | measurement microphones: the source and jack they match, each capture channel's calibration and working point |
| `~/.config/per-device-eq/measure-state.json`                     | per output: the last microphone used and the last level that measured it well |
| `~/.local/share/wireplumber/scripts/90-per-device-eq.lua`        | the persistence hook (a static script, installed verbatim from the repo) |
| `~/.local/state/wireplumber/per-device-eq`                       | the hook's table, written by the hook: each device's own graph or an explicit nothing, and the taste entry `@taste` that plays on the rest; read at startup only if it carries the hook's protocol |
| `~/.config/wireplumber/wireplumber.conf.d/90-per-device-eq.conf` | loads the hook and creates the `per-device-eq` metadata object           |
| `~/.config/per-device-eq/export-targets/*.json`                  | your own export targets (see below); never created by the app, read if a hand makes it |
| `profiles/clean.json`, `/usr/share/per-device-eq/profiles/`      | built-in / system profiles                                               |

### Export targets of your own

The export wizard's list of targets is built-ins plus JSON drop-ins from
`~/.config/per-device-eq/export-targets/`. The app never creates that
directory; make it and the wizard reads it.

One file holds one target object or a list of them. A target needs an
`id`, a `name` and a `writer` from the set the wizard knows
(`pdeq`, `parametric`, `graphiceq`, `poweramp`, `fixed`), and may carry
`ext`, `note`, `max_bands` and `presets`. **A drop-in whose `id` matches
a built-in replaces it in place**; new ids are appended in filename
order. A malformed file or entry is skipped with a line on stderr rather
than taking the wizard down.

That is the seam for a player we do not ship a target for, and for
overriding one we do ship when a particular receiver has its own habits.

```json
{ "id": "my-player", "name": "My player", "writer": "parametric",
  "ext": ".txt", "max_bands": 8,
  "note": "eight bands, no shelves" }
```

## Development: audit tools & tests

The `tools/` directory contains the measurement/clipping audit toolkit; the
development plan lives in [ROADMAP.md](ROADMAP.md).

| Tool                      | Purpose                                                                    |
| ------------------------- | -------------------------------------------------------------------------- |
| `perdeviceeq/pde_audit.py`| shared RBJ biquad library, clip statistics, demo profile                   |
| `tools/audit_peaks.py`    | peak / clip counter for float32 captures                                   |
| `tools/audit_headroom.py` | pre-EQ capture × profile → post-EQ peak, clip count, recommended preamp    |
| `tools/make_fixtures.py`  | deterministic clean/hot-master test fixtures (seed-pinned)                 |

### Capturing audio for audits

The sink monitor taps **pre-EQ** in the in-node topology, so a capture shows
what *enters* the EQ; `audit_headroom.py` computes what *leaves* it:

```
pw-record -P '{ stream.capture.sink = true }' \
          --target <sink-name> --format f32 capture.wav
```

`--format f32` is mandatory — integer formats destroy over-full-scale peaks
at write time, and those peaks are the whole point of the audit. Real
captures (including copyrighted material) belong in `tests/fixtures-local/`
(gitignored), never in the repository.

### Tests

```
python3 -m pytest tests/
```

Fixtures are generated on the fly by `tests/conftest.py` — deterministic and
seed-pinned, so no binary test data is stored in git.

The hook has tests of its own: `tests/test_hook.py` runs
`wireplumber/90-per-device-eq.lua` against a fake WirePlumber
(`tests/hook_harness.lua`) and needs a Lua interpreter (`lua`); without one
that test is skipped.

## Known issues

- **Volume drop after enabling EQ -- fixed in PipeWire 1.6.8.** On sinks
  with hardware volume, the first volume change made *after* an in-node
  EQ was active could collapse the real output level while the reported
  volume looked correct: the filter-graph ate the softVolume/softMute
  properties instead of passing them through, so channel volumes applied
  twice, in hardware and again in software. Fixed upstream in **1.6.8**
  (<https://gitlab.freedesktop.org/pipewire/pipewire/-/work_items/5344>);
  on 1.6.7 and older, set the volume before enabling the EQ, or
  `systemctl --user restart wireplumber`.
- **Two identical cards can swap profiles.** Two units of one model may
  report the same serial, and WirePlumber names them `...-00` and
  `...-00.2` in the order it sees them. A profile is bound to that name,
  not to the USB port, so if the cards appear in the other order (after a
  reboot or a re-plug) their profiles swap. Binding to the USB port would
  tie a profile to the socket instead, and moving a card to another port
  would lose it; this app chose the first.
- **Installing or updating the hook restarts WirePlumber**, and a
  Bluetooth headset playing at that moment drops. Reconnect it if it does
  not come back by itself.
