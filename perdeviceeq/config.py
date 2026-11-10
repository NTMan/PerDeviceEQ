# -*- coding: utf-8 -*-
"""Constants, paths and the WirePlumber component config for per-device-eq.

No third-party imports -- this module is safe to import anywhere (CLI, tests,
GUI) without pulling in GTK.
"""

import json
import os
import sys

FS = 48000.0
FMIN, FMAX = 20.0, 20000.0   # audible range: FR plot grid + headroom curve scan
CONFIG_DIR        = os.path.expanduser("~/.config/per-device-eq")
USER_PROFILES_DIR = os.path.join(CONFIG_DIR, "profiles")
BINDINGS_FILE     = os.path.join(CONFIG_DIR, "bindings.json")
UI_STATE_FILE     = os.path.join(CONFIG_DIR, "ui-state.json")

# Data root: the directory that holds wireplumber/, data/ and profiles/ as
# siblings of this `perdeviceeq` package -- i.e. the repo root when running from
# a checkout, or /usr/share/per-device-eq when installed (the package lives at
# <root>/perdeviceeq/, so the data root is its parent directory).
_DATA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYS_PROFILE_DIRS = ["/usr/share/per-device-eq/profiles",
                    os.path.join(_DATA_ROOT, "profiles")]

# Persistence is native to WirePlumber:
#   * a small, STATIC WP Lua hook (shipped in the repo, installed verbatim)
#     applies the in-node EQ on each sink and owns persistence,
#   * a PipeWire metadata object ("per-device-eq", created by WP's own
#     metadata.lua) carries live edits from the app to the hook,
#   * the hook persists graphs to WpState (~/.local/state/wireplumber/) and
#     reloads them at startup, so the EQ survives reboot with no user process;
#     that file is the hook's alone, and the app never reads it.
# WirePlumber 0.5 looks up user scripts in ~/.local/share/wireplumber/scripts.
WP_SCRIPT_DIR  = os.path.expanduser("~/.local/share/wireplumber/scripts")
WP_SCRIPT_NAME = "90-per-device-eq.lua"
WP_SCRIPT      = os.path.join(WP_SCRIPT_DIR, WP_SCRIPT_NAME)
WP_SCRIPT_STALE = os.path.expanduser(  # removed: an earlier build wrote here too
    "~/.config/wireplumber/scripts/90-per-device-eq.lua")
WP_CONF_DIR    = os.path.expanduser("~/.config/wireplumber/wireplumber.conf.d")
WP_CONF        = os.path.join(WP_CONF_DIR, "90-per-device-eq.conf")
METADATA_NAME  = "per-device-eq"

# The version of the LOADED hook. The hook stamps this into the
# metadata on activation (local PROTOCOL in
# wireplumber/90-per-device-eq.lua -- keep them equal); the window
# compares it on every beat and offers a one-click reinstall on a
# mismatch. Bump it on ANY change to the hook, its contract or its
# behaviour: the installed copy does not update with the package,
# and a new number is the only thing that tells an installation
# its hook is old. 4 is the final-node rule (pw_backend.is_final_node);
# 5 is three graphs on a node: correction, taste and preamp.
PROTOCOL = "5"
# The metadata contract, the same in the hook (LISTENER, MEASURE,
# BYPASS, BYPASS_ON and STRIP there, keep them equal). A node carries
# three graphs (eq.py, "what a node plays"): 0 the device's correction,
# 1 the listener's taste, 2 the preamp. A key names a device, the
# listener, the measurement or Bypass.
#
# A device's value is one of two:
#   eq.device_value  its correction (graph 0) with its preamp (graph 2)
#                    in one write, so the correction never plays with a
#                    preamp made for another;
#   LISTENER_KEY     no correction of its own: graph 0 off, and the
#                    listener's taste and "*" preamp on. Written, never a
#                    deleted key: the metadata announces a delete only
#                    for a key it holds, and after a restart it holds
#                    none of what the hook keeps.
# The value under LISTENER_KEY is the listener record
# (eq.listener_value): graph 1, and graph 2 for each device with a
# correction of its own -- its Auto preamp -- with "*" for every other
# device and for all of them in Manual. A device plays the preamp that
# arrived last, in its own value or in the record: each write carries
# the preamp that is right when it is made (audio_backend writes graph
# values in the order they were made). A device is a final node
# (pw_backend.is_final_node); the hook sets nothing on any other.
#
# The value under MEASURE_KEY is the key of the device a measurement
# is sweeping, STRIP when none is: while it is named there the hook
# keeps every graph off it, and gives back what it holds for the device
# when the name goes (audio_backend, moratorium). A measurement never
# writes a device's value, so nothing is read back afterwards.
#
# The value under BYPASS_KEY is BYPASS_ON while Bypass is pressed, STRIP
# when it is not: the hook keeps every graph off every device while it
# is on, and gives each back what it holds when it goes. It belongs to
# the listener, not to a device, and stays until it is released: the
# hook keeps it in its table, the window in ui-state.json.
LISTENER_KEY = "@listener"
MEASURE_KEY = "@measure"
BYPASS_KEY = "@bypass"
BYPASS_ON = "1"
STRIP = ""
# the static hook is shipped next to the package (repo) or system-wide (package)
HOOK_SRC_CANDIDATES = [os.path.join(_DATA_ROOT, "wireplumber", WP_SCRIPT_NAME),
                       "/usr/share/per-device-eq/wireplumber/" + WP_SCRIPT_NAME]
# where the hook persists graphs; used to drive a one-time migration from
# bindings.json into WpState (after that the hook owns the file)

# desktop integration (so the launcher/dock shows our name + icon). The window's
# Wayland app_id equals APP_ID, so the .desktop must be named <APP_ID>.desktop
# and ship an icon themed as <APP_ID>.
APP_ID = "io.github.ntman.PerDeviceEQ"
DESKTOP_FILE_NAME = APP_ID + ".desktop"
ICON_FILE_NAME    = APP_ID + ".svg"
DESKTOP_SRC_CANDIDATES = [os.path.join(_DATA_ROOT, "data", DESKTOP_FILE_NAME),
                          "/usr/share/applications/" + DESKTOP_FILE_NAME]
ICON_SRC_CANDIDATES = [
    os.path.join(_DATA_ROOT, "data", "icons", "hicolor", "scalable", "apps", ICON_FILE_NAME),
    "/usr/share/icons/hicolor/scalable/apps/" + ICON_FILE_NAME]
USER_DESKTOP_FILE = os.path.expanduser("~/.local/share/applications/" + DESKTOP_FILE_NAME)
USER_ICON_FILE    = os.path.expanduser(
    "~/.local/share/icons/hicolor/scalable/apps/" + ICON_FILE_NAME)
SYS_DESKTOP_FILE  = "/usr/share/applications/" + DESKTOP_FILE_NAME

# GtkBuilder design for the GUI (ships in data/ next to the package, or under
# /usr/share/per-device-eq/data when installed -> both resolve via _DATA_ROOT).
UI_FILE_NAME = APP_ID + ".ui"
UI_FILE_CANDIDATES = [os.path.join(_DATA_ROOT, "data", UI_FILE_NAME),
                      "/usr/share/per-device-eq/data/" + UI_FILE_NAME]
# the measurement wizard ships its own GtkBuilder design alongside
MEASURE_UI_FILE_NAME = APP_ID + ".Measure.ui"
MEASURE_UI_FILE_CANDIDATES = [
    os.path.join(_DATA_ROOT, "data", MEASURE_UI_FILE_NAME),
    "/usr/share/per-device-eq/data/" + MEASURE_UI_FILE_NAME]
# user's curated "favorites" set of profile ids (Weather-style quick list)
FAVORITES_FILE = os.path.join(CONFIG_DIR, "favorites.json")
PREF_LAYERS_FILE  = os.path.join(CONFIG_DIR, "preference-layers.json")

# measurement wizard persistence (see perdeviceeq/measure_prefs.py):
# reusable measurement-mic profiles, and per-sink recall of the last mic
# and the last auto-level volume that measured that sink well.
MIC_PROFILES_FILE  = os.path.join(CONFIG_DIR, "mic-profiles.json")
MEASURE_STATE_FILE = os.path.join(CONFIG_DIR, "measure-state.json")

TYPE_TO_LABEL = {"PK": "bq_peaking", "LSC": "bq_lowshelf",
                 "HSC": "bq_highshelf",
                 # sealed types: the zone places these, not the
                 # hand -- they never join TYPE_NAMES below
                 "HP": "bq_highpass", "LP": "bq_lowpass"}
TYPE_NAMES = ["PK", "LSC", "HSC"]
SCHEMA_VERSION = 8      # profile body schema. 8 is the balance: a
#                         channel's level against the others is a
#                         number of the channel ("balance", dB, absent
#                         at 0), not a freq-0 shelf among its bands.
#                         It is a preamp of one channel: the fit writes
#                         it, the hand may move it either way, and the
#                         shared preamp stays one -- Auto counts the
#                         balance in each channel's curve. A 7 body
#                         holds the same number as a band, so it
#                         converts exactly, once, with
#                         tools/convert_v8.py. The converter stays until
#                         the first change no converter can carry over;
#                         then it goes, and the answer is to re-measure.
#
#                         7 is the level map: a
#                         profile now takes its measurement level from
#                         its own passport, and the passport is read
#                         against the disagreement of its own sweeps.
#                         A map walked before that carries a scatter
#                         only where its base was heard -- ten bins of
#                         958 on a Bluetooth walk -- and cannot be
#                         read by the new rule at all.
#
#                         The alternative was a second reading rule
#                         kept alive for old maps, and his objection
#                         to that is the same one that decided epochs
#                         5 and 6: it divides profiles into ones where
#                         the level is named, the fader is held and a
#                         rung can be rebuilt, and ones where none of
#                         that works. Two classes and a compatibility
#                         branch to serve the worse one. A map is
#                         three minutes a channel and a coupler
#                         disturbs nobody.
#
#                         6 added what a take returned that the sweep
#                         never asked for -- another confession
#                         recorded while the sweep played, and like
#                         the harmonics of epoch 5 no converter can
#                         materialize one. So pre-7 profiles are
#                         deprecated and there are no converters --
#                         re-measure with the current core. His words
#                         on why it is not optional: he does not want
#                         half-complete profiles living alongside
#                         whole ones.
# the optional v3 blocks the store/GUI carry verbatim through save /
# import / export; producers own their shape (see perdeviceeq/profiles.py)
# "passport" is the level map, one record per channel. It is here
# rather than beside the named fields below because it is a BLOCK the
# store must carry without understanding: its shape belongs to
# level_run, and _body is a whitelist -- a field it does not name is
# dropped on save, silently, which is exactly what happened when the
# passport was first moved out of measurement. It reached memory,
# save_user ran, and nothing arrived on disk.
V3_BLOCKS = ("provenance", "device", "fit", "measurement", "passport")
CLEAN_ID = "clean"



# ============================ WirePlumber hook (config) ============================
# The hook itself is a STATIC file shipped in the repo (wireplumber/90-per-device-eq.lua);
# integration.install_hook() copies it verbatim. Only the small component config
# is built here.
#
# Two components: WP's own metadata.lua creates our named object; our hook reads it.
HOOK_CONF = (
    '# Installed per-user by per-device-eq; no package manager\n'
    '# owns this file, so no package manager will remove it.\n'
    '# Remove with: per-device-eq.py --uninstall\n'
    '# (Flatpak: flatpak run io.github.ntman.PerDeviceEQ'
    ' --uninstall)\n'
    '# If the app is already gone, remove by hand:\n'
    '#   rm ~/.local/share/wireplumber/scripts/'
    '90-per-device-eq.lua\n'
    '#   rm ~/.config/wireplumber/wireplumber.conf.d/'
    '90-per-device-eq.conf\n'
    '#   systemctl --user restart wireplumber\n'
    'wireplumber.components = [\n'
    '  { name = metadata.lua, type = script/lua, provides = metadata.%(meta)s,\n'
    '    arguments = { metadata.name = %(meta)s } }\n'
    '  { name = "%(script)s", type = script/lua, provides = per-device-eq,\n'
    '    requires = [ metadata.%(meta)s ] }\n'
    ']\n'
    '\n'
    '# The sandboxed app must write the per-device-eq metadata\n'
    '# and node Props; the stock policy confines flatpak clients\n'
    '# to read-only, and self-declared properties are stripped\n'
    '# from confined clients by design. This grants full\n'
    '# permissions to THIS app only, keyed on the kernel-verified\n'
    '# flatpak app id.\n'
    'access.rules = [\n'
    '  { matches = [\n'
    '      { pipewire.access.portal.app_id =\n'
    '          "io.github.ntman.PerDeviceEQ" }\n'
    '    ]\n'
    '    actions = { update-props = {\n'
    '      default_permissions = "all"\n'
    '    } }\n'
    '  }\n'
    ']\n'
    'wireplumber.profiles = {\n'
    '  main = {\n'
    '    metadata.%(meta)s = required\n'
    '    per-device-eq = required\n'
    '  }\n'
    '}\n'
) % {"meta": METADATA_NAME, "script": WP_SCRIPT_NAME}


def read_state(path, default):
    """READ A STATE FILE, AND NEVER LET A BAD ONE BE ERASED BY SILENCE.

    Every file this program keeps -- bindings, profiles, preferences,
    microphone rigs, the window's furniture -- used to answer an
    unparsable file the same way it answered a missing one: with
    nothing. Nothing then got saved back over it, so a single stray
    comma in bindings.json cost every binding on the machine without
    one word on the terminal. The file itself already says what the
    rule should have been, about a row in an old shape: losing a
    binding without a word is the one outcome not worth the tidiness.

    A file that cannot be parsed is moved aside to <path>.bad and
    named on stderr. The program then starts empty, as it did before,
    but the hand's work is still on disk and a typo is a minute's
    repair rather than an evening's.

    A missing file is not an error and says nothing.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError:
        bad = "%s.bad" % path
        try:
            os.replace(path, bad)
            where = bad
        except OSError:
            where = path            # could not move it; it still stands
        print("per-device-eq: %s is not valid JSON -- kept as %s, "
              "starting empty" % (path, where), file=sys.stderr)
        return default
    except OSError as e:
        print("per-device-eq: cannot read %s (%s), starting empty"
              % (path, e), file=sys.stderr)
        return default


def load_ui_state():
    """App-level UI state (window furniture, and the preamp ride). Read by
    the window and by the hook path, which needs the preamp without a
    window to ask."""
    d = read_state(UI_STATE_FILE, {})
    return d if isinstance(d, dict) else {}


def save_ui_state(d):
    try:
        os.makedirs(os.path.dirname(UI_STATE_FILE), exist_ok=True)
        tmp = UI_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, UI_STATE_FILE)
    except OSError:
        pass
