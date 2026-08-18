# -*- coding: utf-8 -*-
"""GTK4 / libadwaita measurement wizard window (ROADMAP increment 4).

A per-sink modal: pick the measurement mic and its per-channel cal above
a GNOME-"Test Speakers"-style ring, click a speaker to run one sweep on
that channel, watch the takes accumulate per channel below (mini FR
curve, SNR, clip flag) and delete the bad ones, and once each channel has
three clean takes build a profile and switch the main editor to it so the
result is heard at once.

This is a thin VIEW: all the real work is in tested, GTK-free modules --
measure_session (one sweep -> TakeOutcome, spread, discard, quality),
measure_build (finalize + fit_peq.fit_profiles + save + bind) and
measure_prefs (mic profiles + per-sink recall). A sweep blocks for
seconds, so take() runs on a worker thread and results are marshalled
back with GLib.idle_add, the same pattern meter.py uses for capture.
"""
import math
import os
import re
import threading
import time

import numpy as np

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import (Gtk, GLib, Gdk, Gio, GObject, Adw,
                           Pango)                    # noqa: E402

from . import config, measure_build       # noqa: E402
from . import curve_view as cv                      # noqa: E402
from . import curve_export                          # noqa: E402
from . import pw_backend
from . import chantabs                               # noqa: E402
from . import inmeter                                # noqa: E402
from . import knee                                   # noqa: E402
from . import knee_run                               # noqa: E402
from . import level_run                              # noqa: E402
from . import eq                                     # noqa: E402
from . import debug
from .picker import NodePicker                       # noqa: E402
from . import measure_core as mc                    # noqa: E402
from . import measure_session as ms
from . import sweep_io                 # noqa: E402
from . import measure_prefs                         # noqa: E402


CLEAN_TARGET = 3            # clean takes per channel before "all clean"

# The failures this window is WRITTEN to have: a refusal, a measurement
# error, a Stop. They are reported to the hand in words and need no
# traceback. Anything else is a defect and gets one on stderr.
EXPECTED_FAILURES = (sweep_io.MeasureCancelled, sweep_io.MeasureError,
                     sweep_io.RefusalError)

# Where each channel sits on the ring, as a compass angle from the front
# (0 = straight ahead, positive = clockwise toward the right), so a
# speaker is drawn where it physically belongs the way GNOME's speaker
# test lays them out, instead of being spread evenly in channel order.
# LFE has no direction; park it at the bottom. Screen angle = this - 90.
FIT_BANDS = 12
FIT_FLO = 20.0
FMIN_PLOT, FMAX_PLOT = 20.0, 20000.0
# the architect's decree: stop economising pixels down the
# vertical -- one axis has to carry the response AND its
# harmonics fifty decibels below it, and that needs room
FACE_H, ROW_H = 300, 200


def _ctrl_down(gesture):
    """True when Ctrl rides this click: add-or-remove, the
    modifier every file manager already taught the hand."""
    return bool(gesture.get_current_event_state()
                & Gdk.ModifierType.CONTROL_MASK)


SPEAKER_NAMES = {
    "FL": "Front Left", "FR": "Front Right", "FC": "Front Center",
    "LFE": "Subwoofer", "RL": "Rear Left", "RR": "Rear Right",
    "SL": "Side Left", "SR": "Side Right",
}


def _speaker_name(key):
    return SPEAKER_NAMES.get(key, key)


def _stride_idx(n, cap=240):
    """Indices for drawing at most ~cap points of an n-point curve.
    Resize-time redraws are Python-loop-bound, every DrawingArea in
    the window repaints on every frame of a drag, and a thumbnail
    cannot show 958 points anyway. The last point always rides."""
    if n <= cap:
        return range(n)
    step = max(1, n // cap)
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def _ui_path():
    """First existing measurement .ui path (ships in data/, or installed)."""
    for p in config.MEASURE_UI_FILE_CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "measurement design not found; looked in:\n  "
        + "\n  ".join(config.MEASURE_UI_FILE_CANDIDATES))


def _node_identity(node):
    """A rig's node identity past the usb enumeration tail:
    alsa_input.usb-X_00002-00.analog-stereo and _00003-00 are
    the same instrument replugged."""
    return re.sub(r"_\d+-\d+(?=\.)", "", node or "")


class MeasureWindow(Adw.Window):
    """Measurement wizard for one output sink."""

    def __init__(self, parent, sink_node, sink_desc, edit_pid=None):
        super().__init__()
        self.parent = parent
        self.sink_node = sink_node
        self.sink_desc = sink_desc
        self.edit_pid = edit_pid            # editing this profile
        self.edit_prof = (parent.store.get(edit_pid)
                          if edit_pid else None)
        self.set_title("Edit profile" if edit_pid
                       else "Measure speakers")
        self.set_default_size(1100, 760)  # opens two-column
        self.set_size_request(480, 600)   # the narrow floor
        # NOT modal: GNOME attaches modal transients to the parent
        # and re-centers them on every frame of an interactive
        # resize, so the window fights the pointer. The incremental
        # model needs no modality anyway -- the profile is live and
        # every take is persisted the moment it lands. transient_for
        # stays for stacking.
        self.set_transient_for(parent)
        # The one hazard modality used to mask: closing the MAIN
        # window mid-session would kill this one without teardown,
        # leaving foreign streams muted and the bypass engaged.
        # Close ourselves first (cancelling a sweep if one is in the
        # air), then let the parent go.
        self._parent_close_id = parent.connect(
            "close-request", self._on_parent_close)
        # Client-side modality, the same trick Adw.Dialog plays
        # inside a window: the parent goes insensitive for the
        # session -- input blocked, header buttons included -- while
        # the compositor sees a plain window and leaves the resize
        # alone. Restored on any close.
        parent.set_sensitive(False)

        _born = time.monotonic()
        self.mic_store = measure_prefs.MicProfileStore()
        self.memory = measure_prefs.MeasureMemory()
        # The backend handle is born before anything asks it:
        # sources are read a few lines below, _sink_present later.
        self._pw = pw_backend.backend()
        self._pw_unsub = None
        # The ring is TARGETS -- the sides of the transducer this
        # profile describes -- not the channels of whatever card is
        # selected. It used to be the sink's channels, which is how a
        # ten-channel card put AUX0 in a take's passport and how a
        # two-channel one left ten speakers on the ring behind it.
        # A profile that declares none is born from the SINK, a few
        # lines below once the graph has been read.
        prof = self.edit_prof or {}
        self.ch_keys = list(prof.get("ch_keys")
                            or list((prof.get("channels") or {})))
        self.n_ch = len(self.ch_keys)

        _t = time.monotonic()
        # only when there is nothing to read yet. The heartbeat pulls
        # on its own schedule and holds the result, so a second dump
        # here bought freshness measured in one tick and cost 159 ms
        # of the 316 this window took to open -- half of it, for a
        # list the next tick would have corrected anyway.
        if getattr(self._pw, "last_dump", None) is None:
            self._pw.update()
        debug.timing("pw.update (a dump)", _t)
        self.sources = pw_backend.list_capture_entries_from(
            self._pw.sources)
        if not self.ch_keys:
            # BORN FROM THE SINK: the channels this card names
            # spatially are the ones it has declared, and a profile
            # measured here describes those sides. A card offering
            # only AUX0..AUXn has declared nothing, so it starts
            # empty and the hand pairs it -- once, for that sink.
            self.ch_keys = eq.spatial_targets(
                self._pw_output_channels(self.sink_node))
            self.n_ch = len(self.ch_keys)
        self.cal = {}               # mic capture-channel idx -> cal
        self.mic_ch = 0             # the CARD's width, for the meter
        self.mic_cols = []          # columns this rig has DECLARED
        self.mic_col = None         # the declared column in view
        self.col_tabs = None        # its strip, built with the card
        self._col_of_label = {}     # the strip speaks the card's names
        self.mic_of = {}            # sink channel -> analyzed mic ch
        self.session = None         # created on first measure
        self._entered = False
        self._pending_port = None   # an input awaiting its profile
        self._pending_out = None    # an output awaiting its profile
        self._busy = False
        # ONE STOP FOR THE ONE LONG THING THAT CAN BE RUNNING: only a
        # single job runs at a time (_busy guards that), so the flag
        # means "the hand has asked the running job to stop" and every
        # job clears it as it starts. Born here because a reader must
        # never depend on some other job having run first.
        self._stop_asked = False
        self._loud_ack = False
        self._canvas_ids = {}       # (ch, live rec.id) -> canvas id
        self._canvas_session = None  # one session entry per sitting
        self._mic_gone = False      # selected rig left the graph
        self._sink_gone = False
        self.fit_lo, self.fit_hi = FIT_FLO, FMAX_PLOT
        self._big = {}              # subject -> (window, area, hold)
        # each handle follows the statistics until dragged
        self._hi_auto = True
        self._lo_auto = True
        self._spread_driver = None      # LOO verdict, set on refresh
        try:                       # bundled action icons: needed when
            theme = Gtk.IconTheme.get_for_display(  # run from git; the
                Gdk.Display.get_default())          # RPM installs them
            theme.add_search_path(os.path.join(     # into hicolor
                os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "data", "icons"))
        except Exception:
            pass
        self._page = None            # selected channel's page widgets
        self._selected_ch = 0        # target the row has selected
        # what a ladder found, per (source node, active route, capture
        # column). The route belongs in the key because a node name
        # does NOT distinguish jacks: a CM106 answers to one node name
        # on both its microphone and its line input, and the knee of
        # one says nothing about the other.
        self._knee = {}
        self.tabs = None             # the shared channel row
        self._inmeter = inmeter.InputMeter()
        self._meter_bars = {}        # column -> Gtk.LevelBar in the list
        self._meter_shown = {}       # column -> the dB the eye sees
        self._meter_tick = None

        _t = time.monotonic()
        self._build_ui()
        debug.timing("_build_ui", _t)
        self.connect("map", lambda *_: self._sync_inmeter())
        self.connect("unmap", lambda *_: self._inmeter.stop())
        self._select_channel(0)
        self.connect("close-request", self._on_close)
        _t = time.monotonic()
        self._prefill_from_memory()
        debug.timing("_prefill_from_memory", _t)
        _t = time.monotonic()
        self._select_profile_rig()
        debug.timing("_select_profile_rig", _t)
        _t = time.monotonic()
        self._ensure_session(arm=False, quiet=True)
        debug.timing("_ensure_session", _t)
        _t = time.monotonic()
        self._refresh_all()
        debug.timing("_refresh_all", _t)
        debug.timing("MeasureWindow total", _born)
        self._pw_unsub = self._pw.subscribe(self._on_pw_state)
        self._pw.start()
        # Birth reconcile: PWState notifies on CHANGE only, so a
        # home already gone at open would stay un-announced until
        # the graph happens to move -- no banner, no locks, a
        # split state (reachable since the edit opens on an
        # absent home). At idle, so every widget the gone costume
        # touches exists: ordering has bitten this constructor
        # before. Guarded on a non-empty pump; a still-empty pump
        # fills on the first poll, which IS a change and
        # notifies.
        if self._pw.sinks:
            GLib.idle_add(self._on_pw_state, self._pw)

    # ---- layout -----------------------------------------------------------
    def _build_ui(self):
        _t = time.monotonic()
        b = Gtk.Builder.new_from_file(_ui_path())
        debug.timing("Gtk.Builder (the .ui)", _t)
        self.set_content(b.get_object("content"))
        # ---- adaptive layout. Adw.MultiLayoutView owns both
        # arrangements declaratively (narrow single column / wide two
        # columns with a pinned left side and the single scroller on
        # the right); the breakpoint only names which one applies.
        # No reparenting in allocation callbacks, no scroller games:
        # the upstream-blessed pattern.
        mlv = b.get_object("mlv")
        bp = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("min-width: 940sp"))
        bp.add_setter(mlv, "layout-name", "wide")
        self.add_breakpoint(bp)

        # the sink is a choice, not a sentence, and it IS the
        # window's title -- the same centered picker grammar as
        # the main window's device picker, minus following. New
        # profiles arrive with the main window's sink; edits
        # arrive with the profile's own home; this switches to
        # any other route without losing the sitting.
        self.gone_banner = b.get_object("gone_banner")
        self.mic_banner = b.get_object("mic_banner")
        # the output picker sits in the CONTENT now, above its own
        # fader, and the header carries the profile's name instead --
        # a control belongs beside the thing it moves
        self.sink_dd = b.get_object("sink_row")
        self.picker = NodePicker(self.sink_dd, self._on_sink_pick,
                                 ellipsis=34)
        self._tame_scroll(self.sink_dd)
        self.picker.select(self.sink_node, self.sink_desc)
        self._refresh_sinks_from(self._pw)
        self.name_row = b.get_object("name_row")
        self.name_row.set_text(
            (self.edit_prof or {}).get("name") or self.sink_desc)
        # the header used to be the output picker; the picker moved to
        # its fader, so the header says WHICH PROFILE this is -- edited
        # below, shown above
        # the name is edited IN the header, not in a row that repeats
        # it: one field, in the place the eye already reads for "which
        # profile is this". A GtkEditableLabel is a Gtk.Editable, so
        # get_text and set_text read the same as the row's did.

        self._build_mic_controls(b.get_object("source_row"),
                                 b.get_object("mic_group"))

        # the slot itself, with no wrapper of its own: an extra box
        # with 12 of margin made this card narrower than the rig card
        # beside it, and two cards on one page must share an edge
        ring_host = b.get_object("ring_host")
        ring_host.set_orientation(Gtk.Orientation.VERTICAL)
        # it carries a single child, so this only ever mattered when
        # it carried more; kept at the page's own eighteen so it
        # cannot reintroduce a stray gap if something is added
        ring_host.set_spacing(18)
        # EIGHTEEN, the same as the outer column puts between slots.
        # This box used to hold the ring and a label under it, where a
        # tight six was right; it holds cards now, and the odd one out
        # was visible at once -- a wide gap above the targets and a
        # narrow one below them, in a page whose whole point is that
        # the cards read as one sequence.
        ring_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                           spacing=18)
        # the ring centers in the space RIGHT of the fader, and
        # the status line below shares that axis (its lead bin
        # is size-grouped with the fader)
        ring_col.set_hexpand(True)
        self._build_measure_col()
        # HIS ORDER, and it is a dependency rather than a taste: the
        # microphone is settled first and knows nothing about targets;
        # the target comes next, because without one there is nothing
        # to play to; the output and its level after that; and play
        # and stop last, when everything is prepared. The output card
        # is built in the ui file and placed here, since only this
        # knows where the targets and the transport rows are.
        # THE TARGETS RIDE IN THE SINK'S CARD, between its picker and
        # its level, the same shape the microphone's card has: pick the
        # thing, then say which of its channels, then the one number
        # that moves. They had a card of their own, which said they
        # were a third thing beside the microphone and the output --
        # but a target is a channel OF the sink, so this is where the
        # eye already looks for it.
        #
        # ONE DIFFERENCE FROM THE MIC CARD, and it must not be implied
        # away: the level below is NOT per target. The mic card's rows
        # follow the tab above them; the sweep level is one number for
        # the rig, and its own row says so.
        b.get_object("tabs_host").set_child(self._tab_box)
        self._tab_row = b.get_object("tabs_host")
        ring_col.append(b.get_object("out_group"))
        ring_col.append(self._act_grp)
        self.ready_hint = Gtk.Label(xalign=0.5)
        self.ready_hint.add_css_class("success")
        self.ready_hint.set_wrap(True)
        self.ready_hint.set_max_width_chars(46)
        self.ready_hint.set_text(
            "Ready to fit -- close this window to hear your "
            "best version.")
        self.ready_hint.set_visible(False)
        ring_col.append(self.ready_hint)
        ring_host.append(ring_col)
        # Each fader under the picker it belongs to: the output level
        # below the output, the input gain below the input. They used
        # to flank the ring, and the reason they were kept apart --
        # two identical sliders side by side read as a louder/quieter
        # pair -- is answered better by neighbourhood than by
        # distance. Each one now sits beside the thing it moves.
        # each fader is a ROW of the group, not a box after it: a
        # preferences group puts a plain widget below the whole list,
        # which is how both sliders ended up at the bottom together
        # instead of each under its own picker (field).
        vbox = Gtk.Box(spacing=6)
        for side in ("start", "end"):
            getattr(vbox, "set_margin_" + side)(12)
        vbox.set_margin_top(6)
        vbox.set_margin_bottom(6)
        vbox.append(self.vol_spin)
        vbox.append(self.relevel_btn)
        b.get_object("vol_host").set_child(vbox)
        # THE FADER IS NOT HERE. A capture column is a wire with its
        # own gain -- the hardware says so, a CM106 declaring cvolume
        # and taking 60% and 80% independently -- so the control that
        # sets it belongs in the card that speaks for the column,
        # beside that column's calibration and its working point. This
        # row stays in the ui file and stands empty rather than being
        # deleted, so the mic card keeps its shape if it is ever wanted
        # back.
        b.get_object("gain_host").set_visible(False)
        # the lead bin stays: it kept the status line clear of the
        # fader column, and with the faders gone from the sides it is
        # simply empty
        # The architect's word on the walk: auto-level rides
        # SECOND, right after the fader -- the two speak the
        # same language. The widget keeps its settled home
        # under the fader; the jump is focus-only: Tab off the
        # fader lands on auto-level, and from there the natural
        # order takes over.

        def _tab(kv, back_to, fwd_to):
            def on_key(_c, keyval, _code, state):
                shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
                if keyval == Gdk.KEY_Tab and not shift:
                    return fwd_to()
                if (keyval == Gdk.KEY_ISO_Left_Tab
                        or (keyval == Gdk.KEY_Tab and shift)):
                    return back_to()
                return False
            c = Gtk.EventControllerKey()
            c.connect("key-pressed", on_key)
            kv.add_controller(c)

        _tab(self.vol_spin,
             back_to=lambda: False,
             fwd_to=lambda: self.relevel_btn.grab_focus())
        _tab(self.relevel_btn,
             back_to=lambda: self.vol_spin.grab_focus(),
             fwd_to=lambda: False)
        self._rebuild_map_slots()

        _t = time.monotonic()
        b.get_object("channel_host").append(self._build_page())
        debug.timing("_build_page", _t)
        _t = time.monotonic()
        fa = self._build_fit_area()
        debug.timing("_build_fit_area", _t)
        # The walk needs no hand now. The ring was a Gtk.Fixed, where
        # "next" is undefined, so every neighbour was named by hand --
        # fader, auto-level, into the ring, back out, then the takes
        # card. A ROW is already an order: the tabs walk left to right
        # and hand on to what follows them. His question, and he was
        # right to ask it.
        for side in ("start", "end", "bottom"):
            getattr(fa, "set_margin_" + side)(12)
        fa.set_margin_top(6)
        # always in sight: the range lives on the card, below
        # the fold -- only the take rows tuck away
        self._page["card"].append(fa)

    def _build_mic_controls(self, source_row, mic_group):
        # the row IS the picker (AdwComboRow); the popup shows
        # the full name, the row ellipsizes the selected one --
        # the ellipsis cap stays so a monster ALSA description
        # never dictates the window's minimum width
        self.source_dd = source_row
        self.mic_picker = NodePicker(self.source_dd,
                                     self._on_mic_pick,
                                     ellipsis=34,
                                     # this row may legitimately hold
                                     # no choice, and an AdwComboRow
                                     # cannot show that by itself
                                     placeholder="Not chosen")
        self.mic_picker.refresh(self.sources)
        self.source_dd.set_sensitive(bool(self.sources))
        self._tame_scroll(self.source_dd)
        self.mic_group = mic_group
        self._recompute_mic()
        self._rebuild_cal_row()

    def _build_measure_col(self):
        """The rows this window owns: the target tabs, the transport,
        and the two faders. Each is CARDED by the window, not here.

        This was a RING -- a disc with a speaker per channel placed by
        compass angle. It went for a reason that killed the idea rather
        than the drawing: a target is the normalisation of a physical
        channel, and half the cards in this app answer AUX0..AUX9,
        which has no place in space at all. A bus number cannot be
        drawn around a listener. And the ring's own count of tabs came
        from the CARD, so a two-channel profile on a ten-channel card
        showed ten speakers -- his first and best argument against it,
        which no amount of geometry answered.

        What the ring did carry and this keeps: the count bubble in
        three tones, and the transport standing still while the row
        above it changes shape.
        """
        # THE ROWS, NOT THE CARDS. This builds the tab row, the
        # transport and the two faders; where each lands is the
        # window's business, because only it knows the order the page
        # is read in. The tabs went to the sink's card, the capture
        # rows to the microphone's, and the transport keeps a card of
        # its own at the bottom.
        # the same arrangement as the main window's tab row: the tabs
        # lead, the two controls sit together at the END, remove before
        # add. Here they ride the group's header, which is where a
        # preferences group puts what acts on its rows.
        row = Gtk.Box(spacing=6)
        for side in ("start", "end"):
            getattr(row, "set_margin_" + side)(12)
        row.set_margin_top(6)
        row.set_margin_bottom(6)
        bar = Gtk.Box(spacing=0)
        bar.set_valign(Gtk.Align.CENTER)
        self.tabs = chantabs.ChannelTabs(bar, self._on_tab_pick)
        row.append(bar)
        # beside the tabs, at the end of their row -- where the main
        # window has always kept them. In the group's header they read
        # as acting on the whole card rather than on the tabs
        pair_box = Gtk.Box(spacing=6, hexpand=True,
                           valign=Gtk.Align.CENTER,
                           halign=Gtk.Align.END)
        row.append(pair_box)
        # the same door the main window has, and for the same reason:
        # a profile with no targets has nowhere to type and nothing to
        # sweep, so the way in must be here too -- this window is where
        # New lands
        self._add_btn = Gtk.MenuButton(icon_name="list-add-symbolic",
                                       valign=Gtk.Align.CENTER)
        self._add_btn.add_css_class("flat")
        # The action lives on THIS window, under a prefix of its own.
        # Not "win.": that is the main window's map, whose pair-add
        # edits the MAIN window's profile -- and an Adw.Window is not
        # an ApplicationWindow, so it has no action map to add to. A
        # group inserted here gives one.
        pair = Gio.SimpleAction.new("pair-add",
                                    GLib.VariantType.new("s"))
        pair.connect("activate",
                     lambda _a, p: self._add_pair(p.get_string()))
        group = Gio.SimpleActionGroup()
        group.add_action(pair)
        self.insert_action_group("measure", group)
        self._del_btn = Gtk.Button(icon_name="window-close-symbolic",
                                   valign=Gtk.Align.CENTER)
        self._del_btn.add_css_class("flat")
        self._del_btn.connect("clicked", lambda _b: self._del_pair())
        pair_box.append(self._del_btn)      # remove, then add
        pair_box.append(self._add_btn)
        self._dress_tabs()
        self._tab_box = row          # the window cards it, see __init__

        # the capture row belongs UNDER the tabs, his call and his
        # reason: the column is bound to the target that plays the
        # sweep, so it reads downwards -- choose the target, then say
        # what captures it, then what corrects it. ONE column, the
        # selected tab's: a picker per target laid side by side asked
        # the eye to match a column to a tab by position, which is
        # what the tabs are for.
        self._cap_rows = []          # the rows below the tabs

        self.play_btn = self._pult_btn(
            "media-playback-start-symbolic",
            "Measure the selected channel", self._on_play)
        self.stop_btn = self._pult_btn(
            "media-playback-stop-symbolic", "Stop the sweep",
            self._on_stop)
        self.stop_btn.set_sensitive(False)
        # The transport is the LAST ROW of the card, centred, with
        # the status line under it -- inside the card, not loose
        # beneath it. Everything above is settings, touched once and
        # left; this is what a hand comes back to, so it sits where
        # the hand ends up. As an ordinary action row it put the
        # status in a subtitle, and a sentence the window needs to say
        # does not belong in the small print of a control; a row whose
        # child is a box gives the buttons and the sentence each a
        # place of their own.
        pult = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                       spacing=6)
        pult.set_halign(Gtk.Align.CENTER)
        pult.append(self.play_btn)
        pult.append(self.stop_btn)
        self.center = Gtk.Label(xalign=0.5)
        self.center.add_css_class("dim-label")
        self.center.set_wrap(True)
        self.center.set_max_width_chars(46)
        self.center.set_justify(Gtk.Justification.CENTER)
        act = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        act.set_halign(Gtk.Align.CENTER)
        for side in ("start", "end"):
            getattr(act, "set_margin_" + side)(12)
        act.set_margin_top(12)
        act.set_margin_bottom(12)
        act.append(pult)
        act.append(self.center)
        self._act_row = Adw.PreferencesRow()
        self._act_row.set_activatable(False)
        self._act_row.set_child(act)
        # its OWN card, and the last one. Play and stop are done when
        # everything else is prepared -- the microphone, the target,
        # the level -- so they sit at the bottom of the page and
        # nothing can be added beneath them by accident.
        self._act_grp = Adw.PreferencesGroup()
        self._act_grp.add(self._act_row)

        # The faders and auto-level were born inside the ring's method
        # and outlive it: the fader is the sweep's level, auto-level
        # speaks the same language, and the input gain sits where the
        # signal returns. Their vertical shape is the ring's last
        # inheritance -- they become horizontal rows under their own
        # pickers when the picker layout is settled.
        adj = Gtk.Adjustment(lower=0, upper=100, step_increment=1,
                             page_increment=5)
        self.vol_spin = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        self.vol_spin.set_draw_value(True)
        self.vol_spin.set_value_pos(Gtk.PositionType.RIGHT)
        self.vol_spin.set_digits(0)
        self.vol_spin.set_hexpand(True)
        self.vol_spin.set_tooltip_text(
            "Sweep playback level (%). Auto-level sets it; drag to "
            "override if it misses.")
        self.vol_spin.connect("value-changed", self._on_vol_edited)
        self._tame_scroll(self.vol_spin)
        gadj = Gtk.Adjustment(lower=0, upper=100, step_increment=1,
                              page_increment=5)
        self.gain_spin = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=gadj)
        self.gain_spin.set_draw_value(True)
        self.gain_spin.set_value_pos(Gtk.PositionType.RIGHT)
        self.gain_spin.set_digits(0)
        self.gain_spin.set_hexpand(True)
        self.gain_spin.connect("value-changed", self._on_gain_edited)
        self._tame_scroll(self.gain_spin)
        self._gain_guard = False
        self._gain_seeded = None
        # what this window has PUT on each capture column, keyed the
        # way a verdict is. The card is read for a column the first
        # time it is met and never again: a snapshot is a beat behind,
        # and a beat is exactly the gap a tab switch falls into.
        self._gain_set = {}
        self._take_gain = None
        self.relevel_btn = self._pult_btn(
            "pde-level-symbolic",
            "Measure the playback level now (probe sweeps only)",
            self._on_relevel)
        self.relevel_btn.set_halign(Gtk.Align.CENTER)

    def _pult_btn(self, icon, tip, cb):
        b = Gtk.Button()
        b.add_css_class("flat")
        b.add_css_class("circular")
        b.set_valign(Gtk.Align.CENTER)
        b.set_child(Gtk.Image.new_from_icon_name(icon))
        b.set_tooltip_text(tip)
        b.connect("clicked", cb)
        return b

    def _build_page(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        graph = Gtk.DrawingArea()
        graph.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Mean response of the channel with its distortion "
             "and the reading's own noise floor"])
        graph.set_content_height(FACE_H)
        graph.set_hexpand(True)
        # The summary IS the accordion's face: the channel's
        # result with a chevron on the right -- the same expander
        # grammar as the main window's cards, Revealer breath
        # included; a click anywhere on the face folds the take
        # rows underneath.
        lb = Gtk.ListBox()
        # the architect's grammar: the dot and the SNR line
        # already separate take from take, so the drawn line
        # keeps ONE job -- dividing the cal groups
        lb.set_show_separators(False)
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        face = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       spacing=6)
        for side in ("top", "bottom", "start", "end"):
            getattr(face, "set_margin_" + side)(12)
        trow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                       spacing=6)
        title = Gtk.Label(xalign=0.0)
        title.add_css_class("heading")
        trow.append(title)
        header = Gtk.Label(xalign=0.0)
        header.add_css_class("dim-label")
        header.add_css_class("caption")
        header.set_hexpand(True)
        trow.append(header)
        chev = Gtk.Image.new_from_icon_name("pan-up-symbolic")
        chev.set_valign(Gtk.Align.CENTER)
        # born hidden: a fresh session opens with zero takes and
        # _refresh_takes is not called until something changes
        # (field round five caught the gap), so the initial
        # state must not promise a fold either
        chev.set_visible(False)
        trow.append(chev)
        face.append(trow)
        # the face follows the channel tabs, so its button asks
        # which channel is on screen at the moment it is pressed
        graph_box = self._zoomed(
            graph, lambda: ("face", self._selected_ch),
            "Open this graph in its own window")
        # the gate lives on the WRAPPER now: hiding the canvas
        # inside it left the overlay visible and the face blank
        graph_box.set_visible(False)
        face.append(graph_box)
        # the legend's hands: hover lights a name, a click pins
        # it. A hit CLAIMS the sequence so the face's own click
        # (which folds the takes) does not fire underneath.
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_legend_motion)
        motion.connect("leave", self._on_legend_leave)
        graph.add_controller(motion)
        lclick = Gtk.GestureClick()
        lclick.set_button(1)
        lclick.connect("pressed", self._on_legend_press)
        graph.add_controller(lclick)
        face.add_css_class("card-header")
        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("released", self._on_takes_face)
        face.add_controller(click)
        rev = Gtk.Revealer()
        rev.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN)
        rev.set_transition_duration(200)
        rev.set_reveal_child(True)
        lb.set_header_func(self._take_header)
        rev.set_child(lb)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("card")
        card.append(face)
        card.append(rev)
        col.append(card)
        self._takes_open = True
        self._hl = cv.Highlight()
        self._face = None
        self._win = (-80.0, 0.0)
        self._page = {"title": title, "header": header,
                      "graph": graph, "graph_box": graph_box,
                      "plot": None,
                      "canvases": [graph],
                      "takes_list": lb,
                      "takes_rev": rev, "card": card,
                      "chevron": chev, "take_rows": []}
        return col

    def _on_takes_face(self, *_):
        if not self._page["take_rows"]:
            return
        self._takes_open = not self._takes_open
        self._page["chevron"].set_from_icon_name(
            "pan-up-symbolic" if self._takes_open
            else "pan-down-symbolic")
        self._page["takes_rev"].set_reveal_child(
            self._takes_open)

    def _conf_curves(self, freqs, mag, thd, h2, h3, noise):
        """The confession as LEVELS on the response's own axis:
        the harmonic ratio lifted onto the fundamental, which is
        what a REW distortion plot shows and what makes the gap
        between signal and distortion readable at a glance. A
        line the take cannot testify about is simply absent."""
        out = []
        for name, arr, col, wd in (("H2", h2, cv.C_H2, 1.0),
                                   ("H3", h3, cv.C_H3, 1.0),
                                   ("THD", thd, cv.C_THD, 1.6)):
            lv = cv.as_level(mag, arr)
            if lv is not None:
                out.append(cv.Curve(name,
                                    cv.smooth_oct(freqs, lv),
                                    col, wd, harmonic=True))
        lv = cv.as_level(mag, noise)
        if lv is not None:
            out.append(cv.Curve("noise", cv.smooth_oct(freqs, lv),
                                cv.C_NOISE, 1.2,
                                harmonic=True, land=True))
        return out

    def _take_curves(self, rec, mean=None, shift=0.0):
        """One take's lines: its raw curve, the red segments where
        the (gain-compensated) take strays from the channel mean
        past the trust threshold -- a lone bad take lights alone, a
        collective scatter lights the same region on EVERY row --
        and its own confession."""
        mag = np.asarray(rec.mag_db, float)
        out = [cv.Curve("take", mag, cv.C_RESPONSE, 1.4,
                        key="response")]
        if mean is not None:
            m = np.asarray(mean, float)
            bad = np.abs(mag + shift - m) > ms.SPREAD_MAX_DB
            out.append(cv.Curve("off mean",
                                np.where(bad, mag, np.nan),
                                cv.C_BAD, 1.8, legend=False,
                                key="response"))
        if not ms.testified(rec):
            return out          # noise cannot confess to harmonics
        out.extend(self._conf_curves(
            rec.freq_hz, mag,
            getattr(rec, "thd_db", None),
            getattr(rec, "h2_db", None),
            getattr(rec, "h3_db", None),
            getattr(rec, "thd_noise_db", None)))
        return out

    def _take_passport(self, ch, rec):
        """The take's provenance off its canvas passport (schema
        v4: the session's rig stamp, the take's own cal). Returns
        (group, tooltip): group is (key, label) for a take
        whose rig differs from the current one, None otherwise.
        The label is the FULL rig name for a ListBox section
        header -- the architect's palette verdict: a truncated
        mark repeated on every row is the smell, several rows
        under one dictionary value get ONE full-width title,
        and the native rig stays headerless (unmarked is the
        home team). The tooltip keeps the take's own passport,
        degrading gracefully:
        name falls back to the node, the serial appears when
        known, the cal names its file and sha, raw says raw. The
        serial-else-node comparison is informational; nothing is
        gated on it (field doctrine: statistics judge)."""
        if not self.edit_pid:
            return None, None
        m = ((self.parent.store.get(self.edit_pid) or {})
             .get("measurement") or {})
        cid = self._canvas_ids.get((ch, rec.id), rec.id)
        take = next((t for t in m.get("takes") or []
                     if t.get("id") == cid), None)
        if take is None:
            return None, None
        stamp = (((m.get("sessions") or {})
                  .get(take.get("session")) or {})
                 .get("source") or {})
        # Identity law (field-diagnosed on the liberty
        # profile): the NODE decides, normalized past its usb
        # instance tail, so a replugged rig stays itself; the
        # serial speaks only to tell twins apart. Serials copy
        # through the mic store and serial_from_cal, and a
        # store entry saved with a foreign cal in the slots
        # carried the E.A.R.S serial into the Umik -- serial
        # equality must never veto what the nodes say.
        s = stamp.get("serial") or ""
        cur = (self._source_info() or {}).get("serial") or ""
        same_node = (_node_identity(stamp.get("node_match"))
                     == _node_identity(
                         self.mic_picker.core.node))
        if not same_node:
            foreign = True
        elif s and cur and s != cur:
            foreign = True          # twin models, one node name
        else:
            foreign = False
        name = stamp.get("name") or stamp.get("node_match")
        parts = []
        if name:
            head = "Captured with %s" % name
            if s:
                head += " S/N %s" % s
            parts.append(head)
        g = take.get("capture_gain") or None
        if g and g[0] is not None:
            parts.append("input gain %d%%%s"
                         % (round(float(g[0]) * 100.0),
                            " (%s)" % g[1] if g[1] else ""))
        sha = take.get("cal_sha")
        if sha:
            e = (m.get("cal_library") or {}).get(sha) or {}
            parts.append("cal %s (sha %s)"
                         % (e.get("file") or "?", sha[:16]))
        else:
            parts.append("raw capture")
        tip = " \u00b7 ".join(parts) if parts else None
        group = None
        if foreign and name:
            group = (s or stamp.get("node_match") or name,
                     head)
        return group, tip

    def _take_header(self, row, before):
        """The architect's three-rule grammar: a group OPENS
        with its capsule information, right-aligned; take is
        separated from take by its own signal line (the list
        draws no separators of its own); a group CLOSES with a
        gray rule. The closer lives in the NEXT group's header
        slot, so a boundary between two groups stacks rule
        then title. The native rig stays untitled -- the home
        team opens implicitly and closes like everyone."""
        group = getattr(row, "_rig_group", None)
        prev = (getattr(before, "_rig_group", None)
                if before is not None else None)

        def _rule():
            sep = Gtk.Separator(
                orientation=Gtk.Orientation.HORIZONTAL)
            sep.set_margin_start(12)
            sep.set_margin_end(12)
            sep.set_margin_top(6)
            sep.set_margin_bottom(3)
            return sep

        if group and group != prev:
            head = group[1]
            n = getattr(row, "_rig_run_len", 0)
            if n:
                head += " \u00b7 %d take%s" % (
                    n, "" if n == 1 else "s")
            lbl = Gtk.Label(label=head, xalign=1.0,
                            wrap=True)
            lbl.add_css_class("caption")
            lbl.add_css_class("dim-label")
            lbl.set_margin_start(12)
            lbl.set_margin_end(12)
            lbl.set_margin_top(3)
            lbl.set_margin_bottom(3)
            if before is None:
                row.set_header(lbl)
            else:
                box = Gtk.Box(
                    orientation=Gtk.Orientation.VERTICAL)
                box.append(_rule())
                box.append(lbl)
                row.set_header(box)
        elif group is None and prev is not None:
            row.set_header(_rule())
        else:
            row.set_header(None)

    def _make_take_row(self, ch, rec, lo, hi, driver=None,
                       mean=None, shift=0.0):
        q = ms.take_quality(rec)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        body.set_margin_top(6)
        body.set_margin_bottom(6)
        body.set_margin_start(12)
        body.set_margin_end(12)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot = Gtk.Label(label="\u25cf")
        dot.add_css_class({ms.TAKE_CLEAN: "success",
                           ms.TAKE_FLAGGED: "warning",
                           ms.TAKE_CLIPPED: "error"}.get(q, "dim-label"))
        head.append(dot)
        if q == ms.TAKE_SILENT:
            # name the absence instead of describing it. "SNR n/a
            # -inf dBFS" reads as a measurement with two poor numbers;
            # this take is not a poor measurement, it is none.
            info = "no sweep heard on this column"
        elif rec.clipped:
            info = "clipped  %.1f dBFS" % rec.peak_dbfs
        else:
            snr = ("SNR %.1f dB" % rec.snr_db
                   if rec.snr_db is not None else "SNR n/a")
            info = "%s  %.1f dBFS" % (snr, rec.peak_dbfs)
            if rec.noise_dbfs is not None:
                info += "  noise %.0f" % rec.noise_dbfs
        if rec.wav_path is None and rec.created_utc:
            info = "%s  \u00b7  %s" % (str(rec.created_utc)[:10],
                                       info)
        drives = driver is not None and driver[0] == rec.id
        if drives:
            info += "  ·  spread driver"
        group, passport = self._take_passport(ch, rec)
        lbl = Gtk.Label(label=info, xalign=0.0, hexpand=True)
        # the info line must never dictate the window's width:
        # with a foreign-rig mark appended, its natural width
        # exceeded the window and AdwToolbarView complained on
        # every resize (requested 1110, 1100 available). The
        # mark yields first -- ellipsis at the end -- and the
        # row's tooltip already carries the full passport.
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.add_css_class("caption")
        lbl.add_css_class("warning" if drives else "dim-label")
        if drives:
            lbl.set_tooltip_text(
                "This take drives the spread: deleting it wins back "
                "%.1f octaves of trustworthy band. Reseat and "
                "remeasure." % driver[1])
        head.append(lbl)
        rm = Gtk.Button()
        rm.add_css_class("flat")
        rm.set_child(Gtk.Image.new_from_icon_name("user-trash-symbolic"))
        rm.set_tooltip_text("Delete this take")
        rm.connect("clicked", self._make_discard_cb(ch, rec.id))
        head.append(rm)
        if passport:
            # the passport rides the HEADER line only: on the whole
            # row it covered the graph the moment the pointer came
            # to read it, which is exactly when it is least welcome
            head.set_tooltip_text(passport)
        body.append(head)

        curve = Gtk.DrawingArea()
        curve.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Frequency response of this take"])
        curve.set_content_width(150)
        curve.set_content_height(ROW_H)
        plot = cv.Plot(rec.freq_hz,
                       self._take_curves(rec, mean, shift),
                       lo, hi, state=self._hl, legend=False,
                       say_evidence=False)
        curve.set_draw_func(plot.draw)
        self._wire_cursor(curve, {"plot": plot})
        self._page["canvases"].append(curve)
        body.append(self._zoomed(
            curve, ("take", ch, rec.id),
            "Open this take in its own window"))

        # wrap in an explicit row: add_row auto-wraps a bare widget in a
        # GtkListBoxRow, and then remove() cannot drop it, so rows pile up
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_child(body)
        row._rig_group = group
        return row

    def _build_fit_area(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lbl = Gtk.Label(xalign=0.0)
        lbl.set_markup("<b>EQ range</b>  <span size='small'>red bars "
                       "are the take-to-take spread; the handles "
                       "follow it until you drag them (cautious while "
                       "takes are few, so they may sit inside the "
                       "red). Reseat between takes or the spread "
                       "flatters the seating.</span>")
        lbl.set_wrap(True)
        box.append(lbl)
        self.range_area = Gtk.DrawingArea()
        self.range_area.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["EQ range over the take-to-take spread"])
        self.range_area.set_content_height(90)
        self.range_area.set_hexpand(True)
        self.range_area.set_draw_func(self._draw_range)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._range_drag_begin)
        drag.connect("drag-update", self._range_drag_update)
        self.range_area.add_controller(drag)
        box.append(self.range_area)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.range_label = Gtk.Label(xalign=0.0)
        self.range_label.add_css_class("dim-label")
        self.range_label.set_hexpand(True)
        row.append(self.range_label)
        row.append(Gtk.Label(label="Bands"))
        self.bands_spin = Gtk.SpinButton.new_with_range(1, 20, 1)
        self._tame_scroll(self.bands_spin)
        prof = self.edit_prof or {}
        seed = ((prof.get("fit_prefs") or {}).get(
            "bands", ((prof.get("fit") or {}).get("params")
                      or {}).get("bands", FIT_BANDS)))
        self.bands_spin.set_value(int(seed))
        self.bands_spin.set_tooltip_text("Max biquads per channel; the fit "
                                         "stops early once the worst "
                                         "residual is under ~0.5 dB")
        row.append(self.bands_spin)
        box.append(row)
        self._range_plot = None
        self._drag_handle = None
        self._update_range_label()
        return box

    def _freq_to_x(self, f):
        if not self._range_plot:
            return 0
        ml, _mt, pw_, _ph = self._range_plot
        lo, hi = math.log10(FMIN_PLOT), math.log10(FMAX_PLOT)
        f = min(max(float(f), FMIN_PLOT), FMAX_PLOT)
        return ml + (math.log10(f) - lo) / (hi - lo) * pw_

    def _x_to_freq(self, x):
        ml, _mt, pw_, _ph = self._range_plot
        lo, hi = math.log10(FMIN_PLOT), math.log10(FMAX_PLOT)
        frac = min(1.0, max(0.0, (x - ml) / max(1, pw_)))
        return 10 ** (lo + frac * (hi - lo))

    def _max_spread(self):
        if self.session is None:
            return None, None
        freqs, best = None, None
        for i in range(self.n_ch):
            sp = self.session.spread_db(i)
            if sp is None:
                continue
            vals = [float(x) for x in sp]
            freqs = list(self.session.takes_of(i)[0].freq_hz)
            best = vals if best is None else [max(a, b)
                                              for a, b in zip(best, vals)]
        return best, freqs

    def _draw_range(self, _area, cr, w, h, *_):
        ml, mr, mt, mb = 6, 6, 6, 16
        pw_ = max(1, w - ml - mr)
        ph = max(1, h - mt - mb)
        self._range_plot = (ml, mt, pw_, ph)
        lo, hi = math.log10(FMIN_PLOT), math.log10(FMAX_PLOT)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.10)
        cr.rectangle(ml, mt, pw_, ph)
        cr.fill()
        spread, freqs = self._max_spread()
        if spread and freqs:
            top = max(ms.SPREAD_MAX_DB, max(spread))
            idx = _stride_idx(len(freqs))
            bw = max(1.0, pw_ / max(1, len(idx)))
            for j in idx:
                gx = self._freq_to_x(freqs[j])
                if spread[j] >= ms.SPREAD_MAX_DB:
                    cr.set_source_rgba(0.87, 0.19, 0.19, 0.85)
                else:
                    cr.set_source_rgba(0.5, 0.5, 0.5, 0.5)
                bar = min(1.0, spread[j] / top) * ph
                cr.rectangle(gx, mt + ph - bar, bw, bar)
                cr.fill()
        xlo = self._freq_to_x(self.fit_lo)
        xhi = self._freq_to_x(self.fit_hi)
        cr.set_source_rgba(0.22, 0.52, 0.90, 0.18)
        cr.rectangle(xlo, mt, max(1, xhi - xlo), ph)
        cr.fill()
        for hx in (xlo, xhi):
            cr.set_source_rgb(0.22, 0.52, 0.90)
            cr.set_line_width(2)
            cr.move_to(hx, mt)
            cr.line_to(hx, mt + ph)
            cr.stroke()
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.8)
        cr.set_font_size(10)
        for fhz, txt in ((100, "100"), (1000, "1k"), (10000, "10k")):
            gx = ml + (math.log10(fhz) - lo) / (hi - lo) * pw_
            cr.move_to(gx + 2, h - 4)
            cr.show_text(txt)

    def _range_drag_begin(self, _g, sx, _sy):
        self._drag_handle = None
        if not self._range_plot:
            return
        xlo = self._freq_to_x(self.fit_lo)
        xhi = self._freq_to_x(self.fit_hi)
        self._drag_handle = "lo" if abs(sx - xlo) <= abs(sx - xhi) else "hi"

    def _range_drag_update(self, g, ox, _oy):
        if self._drag_handle is None or not self._range_plot:
            return
        ok, sx, _sy = g.get_start_point()
        if not ok:
            return
        f = self._x_to_freq(sx + ox)
        if self._drag_handle == "lo":
            self.fit_lo = max(FMIN_PLOT, min(f, self.fit_hi - 1))
            self._lo_auto = False           # the user took the handle
        else:
            self.fit_hi = min(FMAX_PLOT, max(f, self.fit_lo + 1))
            self._hi_auto = False           # the user took the handle
        self.range_area.queue_draw()
        self._update_range_label()

    def _update_range_label(self):
        lo_a = getattr(self, "_lo_auto", True)
        hi_a = getattr(self, "_hi_auto", True)
        auto = (" · auto" if lo_a and hi_a else
                " · auto hi" if hi_a else
                " · auto lo" if lo_a else "")
        self.range_label.set_text(
            "Fit %d – %d Hz%s"
            % (round(self.fit_lo), round(self.fit_hi), auto))

    def _auto_fit_range(self):
        """Park each handle at its edge of trust after every change
        to the takes: the full sweep while there are no statistics,
        the start of the red otherwise -- the ceiling from the top,
        the floor from the bottom (a leaky-seal bass is a blind zone
        exactly like an HF cliff). A manual drag of a handle
        disengages that handle's automation for the rest of the
        window; the other keeps following."""
        if self.session is None:
            return
        hi, lo = self.fit_hi, self.fit_lo
        if self._hi_auto:
            ceil = self.session.trusted_ceiling_hz()
            hi = FMAX_PLOT if ceil is None else min(FMAX_PLOT, ceil)
        if self._lo_auto:
            floor = self.session.trusted_floor_hz()
            lo = FIT_FLO if floor is None else max(FIT_FLO, floor)
        lo = min(lo, hi / 2.0)              # keep at least an octave
        if abs(hi - self.fit_hi) >= 1.0:
            self.fit_hi = hi
        if abs(lo - self.fit_lo) >= 0.1:
            self.fit_lo = lo
        self._update_range_label()

    # ---- drawing ----------------------------------------------------------
    # ---- prefill / refresh ------------------------------------------------
    def _prefill_from_memory(self):
        pid = self.memory.mic_for(self.sink_node)
        prof = self.mic_store.get(pid) if pid else None
        debug.mic_trace("prefill sink=%r pid=%r prof=%s nm=%r "
                     "live=%d" % (self.sink_node, pid,
                                  bool(prof),
                                  (prof or {}).get("node_match"),
                                  len(self.sources)))
        if prof:
            # cal BEFORE the dropdown: set_selected fires the
            # source-change handler synchronously, and
            # _persist_mic must never see an empty self.cal
            # while a remembered rig exists (the field wipe).
            # Restore every stored capsule, not range(mic_ch)
            # -- the channel count may not be restored yet.
            for k, path in (prof.get("cal") or {}).items():
                try:
                    self.cal[int(k)] = path
                except (TypeError, ValueError):
                    pass
        if prof and prof.get("node_match"):
            debug.mic_trace("prefill select nm")
            e = self._entry_for(prof["node_match"])
            self.mic_picker.select(
                (e or {}).get("name") or prof["node_match"],
                (e or {}).get("desc")
                or prof.get("name") or prof["node_match"])
            self._adopt_selected_source()
        debug.mic_trace("prefill done core=%r"
                     % self.mic_picker.core.node)
        self._sync_cal_labels()

    def _select_profile_rig(self):
        """An edit belongs to its rig: the mic of the profile's
        LAST sitting is selected at birth, present or gone --
        never a silent substitute (field doctrine). Per-sink
        memory still rules new profiles."""
        m = ((self.edit_prof or {}).get("measurement") or {})
        takes = m.get("takes") or []
        sid = takes[-1].get("session") if takes else None
        stored = (((m.get("sessions") or {}).get(sid) or {})
                  .get("source") or {})
        node = stored.get("node_match")
        if not node:
            return
        e = self._entry_for(node)
        self.mic_picker.select(
            (e or {}).get("name") or node,
            (e or {}).get("desc") or stored.get("name") or node)
        self._adopt_selected_source()

    def _sync_cal_labels(self):
        """Set and unset must read apart at a glance: the row's
        subtitle wears the check mark and the chosen file's
        name, the button flips Choose/Change, and the row's
        tooltip carries the path plus the cal sha16 -- the same
        fingerprint the profile's rig block records. Unset says
        plainly that the capture channel runs raw."""
        labels = self._mic_labels()
        # the COLUMN, not the row's position: there is one row now
        # and it speaks for whichever column the tab in view reads --
        # column 2 on his loopback. Enumerating would have read the
        # calibration of column 0 and written it back there too.
        for i, row in zip(getattr(self, "cal_cols", []),
                          getattr(self, "cal_rows", [])):
            path = self.cal.get(i)
            btn = self.cal_btns[i]
            if not path:
                badge = self.cal_badges.get(i)
                if badge is not None:
                    badge.set_visible(False)
                clear = getattr(self, "cal_clears", {}).get(i)
                if clear is not None:
                    clear.set_visible(False)
                row.set_subtitle("not set -- the capture "
                                 "channel runs raw")
                row.set_tooltip_text(
                    "Calibration for the rig's %s capture "
                    "channel; its RAW/HEQ/IDF/HPN domain is "
                    "the compensation" % labels[i])
                btn.set_label("Choose\u2026")
                continue
            clear = getattr(self, "cal_clears", {}).get(i)
            if clear is not None:
                clear.set_visible(True)
            row.set_subtitle("\u2713 " + os.path.basename(path))
            cnt, sev, brk = self._cal_testimony(path)
            badge = self.cal_badges.get(i)
            if badge is not None:
                for c in ("green", "amber", "red"):
                    badge.remove_css_class(c)
                if cnt is not None:
                    badge.set_label(str(cnt))
                    badge.add_css_class(sev)
                badge.set_visible(cnt is not None)
            tip = path
            try:
                sha = measure_build.cal_sha_cached(path)
                tip += ("\nsha256 %s -- the profile's rig "
                        "fingerprint records this" % sha[:16])
            except OSError:
                pass
            if brk:
                tip += "\n" + brk
            row.set_tooltip_text(tip)
            btn.set_label("Change\u2026")
        # the sensitivity line speaks for the same column as
        # these titles do, so it is refreshed with them and
        # cannot lag behind by a heartbeat
        self._refresh_knee_caption()

    def _selected_source(self):
        """The LIVE source entry behind the picker's choice; None
        when nothing is chosen or the chosen rig is gone (callers
        that can live with a gone rig ask the picker itself)."""
        name = self.mic_picker.core.node
        if name is None:
            return None
        return next((s for s in self.sources
                     if s["name"] == name), None)

    def _entry_for(self, key):
        """The entry a stored identity means today. An exact key
        wins; a bare node name -- what older profiles carry, and
        what a rig is called before anyone thought about jacks --
        resolves to that card's LIVE jack, because that is the rig
        the graph is actually offering right now."""
        if not key:
            return None
        exact = next((s for s in self.sources
                      if s["name"] == key), None)
        if exact is not None:
            return exact
        node = pw_backend.entry_node(key)
        mine = [s for s in self.sources
                if (s.get("node") or s["name"]) == node]
        live = next((s for s in mine
                     if (s.get("route") or {}).get("active")), None)
        return live or (mine[0] if mine else None)

    def _source_name(self):
        s = self._selected_source()
        return s["name"] if s else None

    def _set_volume_display(self, v):
        if self._sink_gone or v is None:
            return
        self.vol_spin.handler_block_by_func(self._on_vol_edited)
        self.vol_spin.set_value(round(100 * v))
        self.vol_spin.handler_unblock_by_func(self._on_vol_edited)

    def _on_vol_edited(self, _spin):
        """Manual override of the sweep level -- the stop-crane when
        auto-level misses. Set it on the session and stop auto-levelling
        so it sticks for the next sweep. Before a session the hand on
        the knob WINS too: the value is remembered for this sink and
        mic and the next session builds on it instead of hunting --
        the old path canceled the relevel and then let the refresh
        overwrite the hand's number with the memory's."""
        v = self.vol_spin.get_value() / 100.0
        if self.session is not None:
            self.session.set_level(v)
        src = self._source_name()
        if src:
            # the LAST fader value is always restorable: the
            # pair remembers every hand, in-session or before,
            # and because it is remembered IMMEDIATELY, any
            # later refresh reads the hand's own number back
            self.memory.remember(self.sink_node, source=src,
                                 volume=v)
        self._refresh_volume()

    def _refresh_volume(self):
        """The spin always shows the last established sweep level --
        the session's current, the remembered one, or the level the
        hunt will start from. Never the sink's LISTENING volume: that
        fallback once invented a number nobody asked for. The caption
        under the fader names the level's source, so a remembered 33
        and a pending hunt can never wear the same face again."""
        v = None
        if self.session is not None:
            v = getattr(self.session, "_v_cur", None)
        debug.mic_trace("refresh session_v=%r" % v)
        if v is None:
            # NO ESTABLISHED LEVEL, so the fader shows the pair's
            # memory -- a hand edit is remembered immediately, so
            # this read-back IS the hand. It runs with a session
            # alive too: one born before anything was measured
            # holds no level, and the sink's listening volume is
            # not a substitute for one. No memory means ZERO on
            # the dial: an obviously wrong number is the cue to
            # press auto-level, never a hidden fifteen.
            src = self._source_name()
            v = (self.memory.volume_for(self.sink_node, src)
                 if src else None)
            debug.mic_trace("refresh mem_v=%r src=%r"
                            % (v, src))
        self._set_volume_display(v if v is not None else 0.0)

    def _on_relevel(self, _btn):
        """Measure the level here and now: forget the remembered value
        and run the level search on the selected channel.

        The spin STANDS STILL through it and lands on the answer at the
        end -- the search narrates itself in the status line, and the
        control shows a decision rather than a process. Nothing has to
        be discarded afterwards: the search plays its own sweeps and
        produces no takes to pretend away."""
        if self._busy:
            return
        src = self._source_name()
        if src:
            self.memory.forget_volume(self.sink_node, src)
        self._refresh_all()
        self._start_measure(self._selected_ch, level_only=True)

    def _clean_count(self, ch):
        if self.session is None:
            return 0
        return sum(1 for r in self.session.takes_of(ch)
                   if ms.take_quality(r) == ms.TAKE_CLEAN)

    def _refresh_cal_manage(self):
        """The Manage row states the canvas's cal reality --
        "2 calibrations \u00b7 12 takes" -- and hides on a
        canvas with no history (nothing to manage)."""
        row = getattr(self, "cal_manage_row", None)
        if row is None:
            return
        m = (((self.parent.store.get(self.edit_pid) or {})
              .get("measurement")) if self.edit_pid else None) or {}
        groups = measure_build.cal_groups(m)
        row.set_visible(bool(groups))
        if groups:
            takes = sum(g["count"] for g in groups)
            cals = sum(1 for g in groups if g["sha"])
            row.set_subtitle("%d calibration%s \u00b7 %d take%s"
                             % (cals, "" if cals == 1 else "s",
                                takes, "" if takes == 1 else "s"))

    def _refresh_all(self):
        ready = self.session is not None
        self._dress_tabs()
        self._refresh_cal_manage()
        for i in range(self.n_ch):
            n = self._clean_count(i)
            if n < CLEAN_TARGET:
                ready = False
            status = self._channel_status(i)
            total = len(self.session.takes_of(i)) if self.session else 0
            if self.tabs is not None and i < len(self.ch_keys):
                self.tabs.set_status(self.ch_keys[i],
                                     str(total) if total else None,
                                     status)
        self._spread_driver = (self.session.spread_driver()
                               if self.session else None)
        self._rebuild_page()
        self._update_pult()
        show = (bool(ready) and not self._busy
                and self.edit_pid is not None
                and self._should_autofit(self.edit_pid))
        self.ready_hint.set_visible(show)
        self._refresh_volume()
        self._auto_fit_range()
        if getattr(self, "range_area", None) is not None:
            self.range_area.queue_draw()

    def _channel_status(self, ch):
        """Ring status for a channel: 'done' (enough clean takes), 'bad'
        (a clipped take), 'warn' (takes disagree, max spread past the
        threshold), or '' (neutral / still going)."""
        if self.session is None:
            return ""
        takes = self.session.takes_of(ch)
        if not takes:
            return ""
        if self._clean_count(ch) >= CLEAN_TARGET:
            return "done"
        if any(ms.take_quality(r) in (ms.TAKE_CLIPPED, ms.TAKE_SILENT)
               for r in takes):
            # silence is as red as clipping and for the same reason:
            # neither is a measurement, and a tab that looks neutral
            # invites another sweep down the same dead column
            return "bad"
        spread = self.session.spread_db(ch)
        if spread is not None and len(spread) \
                and max(spread) >= ms.SPREAD_MAX_DB:
            return "warn"
        return ""

    def _rebuild_page(self):
        if self._page is None:
            return
        lb = self._page["takes_list"]
        ch = self._selected_ch
        if not (0 <= ch < len(self.ch_keys)):
            # NO TARGET, NO SUBJECT. This panel is about one side of a
            # transducer, and a profile that declares none leaves it
            # with nothing to be about -- so it says that, once, here,
            # rather than every reader of self.ch_keys[ch] growing its
            # own guard. The state is reachable now that the window no
            # longer invents FL and FR for a profile that has neither.
            for row in self._page["take_rows"]:
                lb.remove(row)
            self._page["take_rows"].clear()
            self._page["title"].set_text("No target")
            self._page["header"].set_markup(
                "add a target and the output it plays through")
            lb.set_visible(False)
            self._page["chevron"].set_visible(False)
            self._face = None
            self._refresh_summary(ch, [])
            return
        n = self._clean_count(ch)
        has_bad = self.session is not None and any(
            ms.take_quality(r) != ms.TAKE_CLEAN
            for r in self.session.takes_of(ch))
        mark = " \u2713" if n >= CLEAN_TARGET else ""
        warn = " \u26a0" if has_bad else ""
        self._page["title"].set_text(
            "Takes %s" % self.ch_keys[ch])
        self._page["header"].set_markup(
            "%d/%d clean%s%s%s"
            % (n, CLEAN_TARGET, mark, warn, self._thd_word(ch)))
        for row in self._page["take_rows"]:
            lb.remove(row)
        self._page["take_rows"].clear()
        self._page["canvases"] = [self._page["graph"]]
        # ONE vertical window for the whole channel -- the face and
        # every row are painted on the same axis, harmonics and
        # noise floor included, so the grid aligns down the list
        # and neighbouring rows compare line to line
        takes, face, mean, shifts, self._win = \
            self._channel_view(ch)
        self._face = face
        lo, hi = self._win
        rows = []
        for rec in takes:
            row = self._make_take_row(ch, rec, lo, hi,
                                      driver=self._spread_driver,
                                      mean=mean,
                                      shift=shifts.get(rec.id, 0.0))
            rows.append(row)
            lb.append(row)
            self._page["take_rows"].append(row)
        # the run length rides the first row of each foreign
        # run: the header closes its membership in words
        # ("· N takes") before the eye reaches the closer
        i = 0
        while i < len(rows):
            g = getattr(rows[i], "_rig_group", None)
            j = i + 1
            while j < len(rows) and getattr(
                    rows[j], "_rig_group", None) == g:
                j += 1
            if g:
                rows[i]._rig_run_len = j - i
            i = j
        lb.set_visible(bool(takes))
        # an empty fold has nothing to promise: the chevron
        # leaves with the takes instead of wagging at nothing
        self._page["chevron"].set_visible(bool(takes))
        self._refresh_summary(ch, takes)

    def _face_curves(self, ch, takes):
        """The channel's result as lines: the mean response over
        the takes, the take-to-take spread as a band around it,
        the mirror partner as a dashed ghost, and the mean
        confession lifted onto the mean. Level moves between takes
        are compensated with the session's recorded gains, so the
        mean matches what finalize will build. None when the
        channel has no takes -- and then nothing is drawn at all,
        because a confession without a take confesses nothing."""
        if not takes or self.session is None:
            return None
        shifts = self.session.comp_shift_db(ch)
        by_id = {}
        if shifts is not None:
            by_id = {r.id: s for r, s
                     in zip(self.session.takes_of(ch), shifts)}
        # a take that heard nothing is not a worse measurement,
        # it is an absence of one: it never joins a mean, not even
        # when nothing better exists
        heard = [r for r in takes if ms.testified(r)]
        clean = [r for r in heard
                 if ms.take_quality(r) == ms.TAKE_CLEAN]
        base = clean or heard
        if not base:
            return None
        mean = sum(r.mag_db + by_id.get(r.id, 0.0)
                   for r in base) / len(base)
        spread = self.session.spread_db(ch)
        sp = np.asarray(
            spread if spread is not None else mean * 0.0, float)
        ghost, glabel = self._partner_ghost(ch)
        # both means say WHOSE they are, in the legend. The
        # partner's name used to sit alone in the top right corner
        # of the canvas -- a decision from before there was any
        # legend, and to a new pair of eyes those two letters are
        # an artefact rather than a label. The keys stay put, so
        # the highlight machinery does not notice the rename.
        curves = [cv.Curve("%s mean" % self.ch_keys[ch], mean,
                           cv.C_RESPONSE, 1.8, key="response")]
        if ghost is not None:
            curves.append(cv.Curve(glabel, ghost, cv.C_GHOST,
                                   1.2, dash=(4.0, 3.0),
                                   key="partner"))
        curves.extend(self._conf_curves(
            base[0].freq_hz, mean,
            *(measure_build.mean_confession(base)
              or (None, None, None, None))))
        band = (mean - sp / 2.0, mean + sp / 2.0,
                sp >= ms.SPREAD_MAX_DB)
        return base[0].freq_hz, curves, band

    def _refresh_summary(self, ch, takes):
        """The face canvas. One gate for the whole picture: no
        takes, no canvas -- the old code hid the response and
        returned BEFORE the distortion canvas, which then kept
        yesterday's confession hanging over an empty list."""
        area = self._page["graph"]
        if not takes or self._face is None:
            self._page["plot"] = None
            self._page["graph_box"].set_visible(False)
            self._refresh_big()
            return
        freqs, curves, band = self._face
        plot = cv.Plot(freqs, curves, self._win[0], self._win[1],
                       band=band, state=self._hl, legend=True,
                       dim_outside=(self.fit_lo, self.fit_hi))
        self._page["plot"] = plot
        area.set_draw_func(plot.draw)
        self._page["graph_box"].set_visible(True)
        area.queue_draw()
        self._refresh_big()

    def _thd_word(self, ch):
        """The datasheet's own figure, next to the clean count:
        THD at 1 kHz, where every manufacturer quotes it. A "<="
        says the reading is sitting on our own noise floor, so the
        number is this rig's ceiling and the device is somewhere
        below it. No SPL claim rides along."""
        self._page["header"].set_tooltip_text(
            "Total harmonic distortion at 1 kHz, the frequency "
            "datasheets quote. Measured at the drive of these "
            "takes, NOT at a calibrated 94 dB SPL. \u2264 means "
            "the reading sits on this rig's own noise floor: the "
            "device is below the number, by how much the rig "
            "cannot say.")
        takes = self.session.takes_of(ch) if self.session else []
        heard = [r for r in takes if ms.testified(r)]
        clean = [r for r in heard
                 if ms.take_quality(r) == ms.TAKE_CLEAN]
        base = clean or heard
        if not base:
            return ""
        conf = measure_build.mean_confession(base)
        if conf is None:
            return ""
        thd, _h2, _h3, noise = conf
        got = measure_build.thd_at(base[0].freq_hz, thd, noise)
        if got is None:
            return ""
        pct, clamped = got
        word = measure_build.pct_word(pct)
        if word is None:
            return ""
        return " \u00b7 <small>THD@1k %s%s%%</small>" % (
            "\u2264" if clamped else "", word)

    def _zoomed(self, area, subject, tip):
        """Every canvas wears a corner button that opens it large.
        Not a gesture: the click on a take canvas already pins a
        line and a press on the equalizer graph already builds a
        band, so magnification gets a hand of its own -- and a
        double click was declined as a source of mis-clicks. The
        button hides until the pointer arrives, so the picture
        stays clean."""
        ov = Gtk.Overlay()
        ov.set_child(area)
        bar = Gtk.Box(spacing=6)
        bar.set_halign(Gtk.Align.END)
        bar.set_valign(Gtk.Align.START)
        bar.set_margin_top(6)
        bar.set_margin_end(6)
        bar.set_visible(False)

        def now():
            return subject() if callable(subject) else subject

        copy = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy.set_tooltip_text("Copy this picture to the clipboard")
        copy.connect("clicked",
                     lambda b: self._copy_canvas(now(), b))
        save = Gtk.Button.new_from_icon_name(
            "document-save-symbolic")
        save.set_tooltip_text("Save this picture as a file")
        save.connect("clicked",
                     lambda b: self._save_canvas(now(), b))
        big = Gtk.Button.new_from_icon_name(
            "view-fullscreen-symbolic")
        big.set_tooltip_text(tip)
        big.connect("clicked", lambda _b: self._open_big(now()))
        for btn in (copy, save, big):
            btn.add_css_class("osd")
            btn.add_css_class("circular")
            btn.set_can_focus(False)
            bar.append(btn)
        ov.add_overlay(bar)
        mo = Gtk.EventControllerMotion()
        mo.connect("enter", lambda *_a: bar.set_visible(True))
        mo.connect("leave", lambda *_a: bar.set_visible(False))
        ov.add_controller(mo)
        return ov

    def _copy_canvas(self, subject, btn=None):
        """The picture into the clipboard, in both languages at
        once: the vector for an editor that speaks it and the
        raster for everything else. Whoever pastes picks -- we do
        not have to guess. Drawn again at export size by the same
        painter, so nothing is a screenshot and nothing is
        stretched."""
        plot = self._big_plot(subject)
        if plot is None:
            return
        try:
            svg = curve_export.svg_bytes(plot)
            png = curve_export.png_bytes(plot)
        except Exception:
            return
        parts = [Gdk.ContentProvider.new_for_bytes(
                     "image/svg+xml", GLib.Bytes.new(svg)),
                 Gdk.ContentProvider.new_for_bytes(
                     "image/png", GLib.Bytes.new(png))]
        try:
            # a GdkTexture as well: GTK takers ask for the object,
            # not for bytes, and the bytes alone would leave them
            # with an empty paste
            val = GObject.Value(
                Gdk.Texture,
                Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)))
            parts.append(Gdk.ContentProvider.new_for_value(val))
        except Exception:
            pass
        self.get_clipboard().set_content(
            Gdk.ContentProvider.new_union(parts))
        if btn is not None:
            btn.set_icon_name("object-select-symbolic")
            GLib.timeout_add(1500, self._copy_settled, btn)

    def _copy_settled(self, btn):
        btn.set_icon_name("edit-copy-symbolic")
        return False

    def _save_canvas(self, subject, btn=None):
        """The picture as a FILE, because a git-backed site wants
        the image on disk beside the article and a plain relative
        link in the markdown. The link itself is not ours to write:
        it is relative to a document root this app knows nothing
        about. The NAME is ours, so it is generated and, once the
        file is written, placed in the clipboard ready to paste."""
        plot = self._big_plot(subject)
        if plot is None:
            return
        dlg = Gtk.FileDialog()
        dlg.set_title("Save this picture")
        dlg.set_initial_name(curve_export.export_name(
            "pdeq", self._export_parts(subject)))

        def done(dialog, res):
            try:
                gfile = dialog.save_finish(res)
                path = gfile.get_path() if gfile else None
                if not path:
                    return
                # the extension decides the language: .png for a
                # raster, anything else takes the vector
                data = (curve_export.png_bytes(plot)
                        if path.lower().endswith(".png")
                        else curve_export.svg_bytes(plot))
                with open(path, "wb") as fh:
                    fh.write(data)
            except Exception:
                return
            try:
                self.get_clipboard().set_content(
                    Gdk.ContentProvider.new_for_value(
                        GObject.Value(str, os.path.basename(path))))
            except Exception:
                pass
            if btn is not None:
                btn.set_icon_name("object-select-symbolic")
                GLib.timeout_add(1500, self._save_settled, btn)

        dlg.save(self, None, done)

    def _export_parts(self, subject):
        """Whose device, which channel, which canvas -- the
        architect's naming scheme, with the canvas kept because a
        channel's mean and its second take would otherwise want the
        same file name."""
        ch = (self.ch_keys[subject[1]]
              if subject[1] < len(self.ch_keys) else "")
        if subject[0] == "face":
            what = "mean"
        else:
            takes = (self.session.takes_of(subject[1])
                     if self.session else [])
            ids = [r.id for r in takes]
            n = ids.index(subject[2]) + 1 if subject[2] in ids else 0
            what = "take%d" % n
        return (self.name_row.get_text(), ch, what)

    def _save_settled(self, btn):
        btn.set_icon_name("document-save-symbolic")
        return False

    def _channel_view(self, ch):
        """Everything any canvas of this channel needs: its takes,
        the face pieces, the mean, the per-take gain compensation
        and the ONE vertical window they all share. In one place
        because the large window may show a different channel than
        the page does, and its grid must still match the small
        canvas it was opened from."""
        takes = self.session.takes_of(ch) if self.session else []
        mean, shifts = None, {}
        if self.session is not None and takes:
            mean, _sp = self.session.average_and_spread(ch)
            sh = self.session.comp_shift_db(ch)
            if sh is not None:
                shifts = {r.id: s for r, s in zip(takes, sh)}
        face = self._face_curves(ch, takes)
        allc = list(face[1]) if face else []
        band = face[2] if face else None
        for rec in takes:
            allc.extend(self._take_curves(
                rec, mean, shifts.get(rec.id, 0.0)))
        return takes, face, mean, shifts, cv.window_db(allc, band)

    def _big_plot(self, subject):
        """A fresh painter for what the large window points AT --
        never a picture copied at opening time, which would go
        quietly stale the moment a take is added or re-measured.
        None means the subject is gone and the window must go."""
        if self.session is None:
            return None
        kind, ch = subject[0], subject[1]
        if ch >= len(self.ch_keys):
            return None
        takes, face, mean, shifts, win = self._channel_view(ch)
        if kind == "face":
            if face is None:
                return None
            freqs, curves, band = face
            return cv.Plot(freqs, curves, win[0], win[1],
                           band=band, state=self._hl, legend=True,
                           dim_outside=(self.fit_lo, self.fit_hi))
        rec = next((r for r in takes if r.id == subject[2]), None)
        if rec is None:
            return None
        return cv.Plot(rec.freq_hz,
                       self._take_curves(rec, mean,
                                         shifts.get(rec.id, 0.0)),
                       win[0], win[1], state=self._hl, legend=True)

    def _big_title(self, subject):
        ch = self.ch_keys[subject[1]] if subject[1] < len(
            self.ch_keys) else "?"
        if subject[0] == "face":
            return "%s \u00b7 mean" % ch
        takes = (self.session.takes_of(subject[1])
                 if self.session else [])
        ids = [r.id for r in takes]
        n = ids.index(subject[2]) + 1 if subject[2] in ids else 0
        return "%s \u00b7 take %d of %d" % (ch, n, len(ids))

    def _open_big(self, subject):
        """One window per subject: a second click on the same
        canvas raises the window that is already open instead of
        breeding twins. Not modal, so two channels can sit side by
        side and be compared -- which is the whole reason the large
        view exists -- and NOT transient for this window either,
        because the field showed what that costs: a maximised large
        window sits above its parent forever, and then the only way
        to reach the channel tabs and pick the other earpiece is to
        dig the parent out from under it, which Alt+` cannot do
        across a transient. So the window joins the application
        instead -- it stacks and cycles like any other -- and the
        rule that it must not outlive the measure window is carried
        by the close handler alone, which never needed transient-for
        to do its job."""
        held = self._big.get(subject)
        if held is not None:
            held[0].present()
            return
        plot = self._big_plot(subject)
        if plot is None:
            return
        area = Gtk.DrawingArea()
        area.set_hexpand(True)
        area.set_vexpand(True)
        area.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["The same curves, large"])
        hold = {"plot": plot}
        area.set_draw_func(
            lambda a, cr, w, h, *_: hold["plot"].draw(a, cr, w, h))
        self._wire_cursor(area, hold)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.append(area)
        head = Adw.HeaderBar()
        cbtn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        cbtn.set_tooltip_text("Copy this picture to the clipboard")
        cbtn.connect("clicked",
                     lambda b: self._copy_canvas(subject, b))
        head.pack_end(cbtn)
        sbtn = Gtk.Button.new_from_icon_name(
            "document-save-symbolic")
        sbtn.set_tooltip_text("Save this picture as a file")
        sbtn.connect("clicked",
                     lambda b: self._save_canvas(subject, b))
        head.pack_end(sbtn)
        view = Adw.ToolbarView()
        view.add_top_bar(head)
        view.set_content(box)
        win = Adw.Window()
        win.set_title(self._big_title(subject))
        win.set_default_size(1000, 680)
        app = self.get_application()
        if app is not None:
            win.set_application(app)
        win.set_content(view)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_big_key, win)
        win.add_controller(keys)
        win.connect("close-request", self._on_big_close, subject)
        self._big[subject] = (win, area, hold)
        win.present()

    def _on_big_key(self, _c, keyval, _code, _state, win):
        if keyval == Gdk.KEY_Escape:
            win.close()
            return True
        return False

    def _on_big_close(self, _win, subject):
        self._big.pop(subject, None)
        return False

    def _refresh_big(self):
        """After every rebuild the large windows ask again. A
        subject that has disappeared -- take deleted, channel gone
        -- closes its window: there is nothing left to show."""
        for subject, (win, area, hold) in list(self._big.items()):
            plot = self._big_plot(subject)
            if plot is None:
                win.close()
                continue
            hold["plot"] = plot
            win.set_title(self._big_title(subject))
            area.queue_draw()

    def _wire_cursor(self, area, hold):
        """A canvas answers the pointer on its own: the crosshair
        is a property of the canvas under the hand, not of the
        page, so only that one canvas repaints. The painter is
        reached through a holder because a large window keeps its
        canvas while the page hands it a fresh painter."""
        def moved(_c, x, y):
            plot = hold["plot"]
            if plot is not None and plot.set_cursor(x, y):
                area.queue_draw()

        def gone(*_a):
            plot = hold["plot"]
            if plot is not None and plot.set_cursor(None, None):
                area.queue_draw()

        def pressed(gesture, _n, x, y):
            plot = hold["plot"]
            if plot is None:
                return
            if plot.state.hit(plot.pick_at(x, y),
                              _ctrl_down(gesture)):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self._repaint_curves()

        mo = Gtk.EventControllerMotion()
        mo.connect("motion", moved)
        mo.connect("leave", gone)
        area.add_controller(mo)
        cl = Gtk.GestureClick()
        cl.set_button(1)
        cl.connect("pressed", pressed)
        area.add_controller(cl)

    def _repaint_curves(self):
        for a in self._page.get("canvases", []):
            a.queue_draw()
        for _w, area, _h in self._big.values():
            area.queue_draw()

    def _on_legend_motion(self, _ctrl, x, y):
        plot = self._page.get("plot") if self._page else None
        if plot is None:
            return
        name = plot.legend_at(x, y)
        if name != plot.state.hover:
            plot.state.hover = name
            self._repaint_curves()
        if plot.set_cursor(x, y):
            self._page["graph"].queue_draw()

    def _on_legend_leave(self, _ctrl):
        plot = self._page.get("plot") if self._page else None
        if plot is None:
            return
        left = plot.set_cursor(None, None)
        if plot.state.hover is not None:
            plot.state.hover = None
            self._repaint_curves()
        elif left:
            self._page["graph"].queue_draw()

    def _on_legend_press(self, gesture, _n, x, y):
        plot = self._page.get("plot") if self._page else None
        if plot is None:
            return
        # a press that changes nothing is not claimed, so the
        # card's own fold click still works through the canvas
        if plot.state.hit(plot.pick_at(x, y), _ctrl_down(gesture)):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._repaint_curves()

    def _partner_ghost(self, ch):
        """(curve, label) of the mirror partner's compensated mean --
        a dashed reference for the PAIR's symmetry -- or (None, None).
        The ghost is drawn WHERE IT LANDED. The drive difference
        is subtracted when it is knowable, because that is
        accounting for what we did to the machine and not a claim
        about the measurement; nothing else is corrected. It used to
        be slid onto this channel by the 200-2000 Hz band means
        whenever the level claim could not be made, which lined the
        curves up beautifully and deleted the very thing the
        operator was looking at: a level gap between two channels is
        a FACT, and it is the first sign of a different coupler, a
        moved gain knob or a seal that leaks on one side. Bitter
        truth over sweet lie -- the gap stays on the graph and the
        label says only that the level is not ours to vouch for.

        The ghost is drawn whenever there is something to draw. It
        used to be suppressed when the two channels sat on different
        capture columns, on the theory that different columns mean
        different capsules -- which hid the comparison from everyone
        whose fixture has two working capsules, and hid it on the
        strength of an equation that is false anyway: a column is a
        wire, not a microphone. Nothing is drawn only when the
        channel has no L<->R partner or the partner has no takes."""
        if self.session is None:
            return None, None
        pk = ms.mirror_key(self.ch_keys[ch])
        if pk is None or pk not in self.ch_keys:
            return None, None
        p = self.ch_keys.index(pk)
        if not self.session.takes_of(p):
            return None, None
        # the TAKES say which column saw them: a dropdown touched
        # today must not change how yesterday's takes are read
        cap_p = measure_build.capsule_of(self.session.takes_of(p),
                                         self.mic_of.get(p, p))
        cap_c = measure_build.capsule_of(self.session.takes_of(ch),
                                         self.mic_of.get(ch, ch))
        pavg, _sp = self.session.average_and_spread(p)
        if pavg is None:
            return None, None
        shift = self.session.drive_shift_db(p, ch)
        claim = measure_build.partner_claim(
            cap_p is not None and cap_p == cap_c, shift is not None)
        where = pavg + shift if shift is not None else pavg
        if claim == "level":
            return where, "%s mean" % pk
        return where, "%s mean, no level claim" % pk

    # ---- callbacks (config) -----------------------------------------------
    def _on_pw_state(self, st):
        """The shared PWState refresh drives the whole window: keep the
        input list current, then reconcile the target sink against the
        graph. One pipewire poll feeds this instead of a window timer."""
        self._refresh_sources_from(st.sources)
        self._reconcile_source(st)
        self._refresh_sinks_from(st)
        self._reconcile_sink(st)
        return False

    def _refresh_sinks_from(self, st):
        """The shared picker mirrors the graph; the doctrine
        (the target sink always listed, gone when the graph
        lost it, the selection never dangling) lives in
        picker.py now, one implementation for both windows."""
        rows = pw_backend.list_playback_entries_from(st.sinks)
        self.picker.refresh(rows)
        hit = pw_backend.find_port_entry(rows,
                                         getattr(self, "_pending_out",
                                                 None))
        if hit is not None:
            self._pending_out = None
            self._retarget(hit["node"], hit["desc"])

    def _on_sink_pick(self, node, desc):
        """A user pick from the shared picker; vetoed while a
        sweep runs (the dropdown is insensitive then, this is
        the second lock on the same door)."""
        if self._busy:
            return False
        if pw_backend.is_card_entry(node):
            # an output behind another profile: switch, remember what
            # to watch for, and veto -- the row just chosen is about
            # to stop existing, and the one that replaces it has a
            # name we cannot know yet
            self._pending_out = pw_backend.switch_to_port(
                node, self.picker.core.sinks)
            return False
        self._retarget(node, desc)

    def _retarget(self, node, desc):
        """Move the sitting to another output, keeping everything
        that is the sitting's: the profile, its takes, the rig and
        the cals. The park-then-rebuild is _on_close's own
        sequence -- the canvas is persisted first, the session
        torn down, then reconstructed on the new sink, which
        re-adopts the stored takes. Volume memory and the mic
        home are per-sink and follow the switch."""
        if node == self.sink_node or self._busy:
            return
        pid = self._ensure_pid()
        self._apply_name(pid)
        if self.session is not None and self._entered:
            try:
                self.session.__exit__(None, None, None)
            except Exception:
                pass
            self._entered = False
        self.session = None
        self.sink_node = node
        self.sink_desc = desc
        # legal even when the retarget arrives through a pick:
        # the shell defers its mirror to idle (the synchronous
        # select here is what segfaulted the gone-to-live
        # retarget in the field)
        self.picker.select(node, desc)
        try:
            self._persist_mic()          # the new home learns the rig
        except Exception:
            pass
        self._ensure_session(arm=False, quiet=True)
        self._refresh_volume()
        GLib.idle_add(self._on_pw_state, self._pw)
        self._refresh_all()
        self._update_pult()

    def _reconcile_sink(self, st):
        """The session belongs to its sink: alive or Unavailable.
        Auto-chasing the default stays dead -- stay-or-go prompts
        made sense only when the wizard owned unsaved takes. A
        USER retarget is different (field verdict): the profile
        is the headphones, the sink is merely the route, so the
        header picker moves the sitting deliberately -- parked
        first, rebuilt on the new sink, takes re-adopted."""
        alive = any(s["name"] == self.sink_node for s in st.sinks)
        self._set_sink_gone(not alive)

    def _refresh_sources_from(self, sources):
        """The mic picker wears the sink picker's doctrine: the
        selected rig is never substituted. An unplugged mic
        keeps its row, the mic banner names the state, and only
        the user (or the rig's return) moves anything --
        auto-falling to row 0 is how a foreign mic used to hide
        the measured takes."""
        prev = [s["name"] for s in self.sources]
        self.sources = pw_backend.list_capture_entries_from(sources)
        self.mic_picker.refresh(self.sources)
        self._adopt_pending_port()
        self._lock_baked_rows()
        if [s["name"] for s in self.sources] != prev:
            self._refresh_all()

    def _sink_present(self):
        return any(s["name"] == self.sink_node for s in self._pw.sinks)

    def _source_present(self):
        node = pw_backend.entry_node(self.mic_picker.core.node)
        return bool(node) and any(
            s["name"] == node for s in self._pw.sources)

    def _reconcile_source(self, st):
        """The rig is never substituted: a vanished mic keeps
        the selection, the banner names the state, the pult
        locks, measuring waits. On the rig's return the mic
        state re-derives and the session rebuilds
        against it."""
        node = pw_backend.entry_node(self.mic_picker.core.node)
        gone = bool(node) and not any(
            s["name"] == node for s in st.sources)
        if gone == self._mic_gone:
            return
        self._mic_gone = gone
        self.mic_banner.set_revealed(gone)
        self._update_pult()
        if not gone and node:
            self._adopt_selected_source()


    def _set_sink_gone(self, gone):
        if gone == self._sink_gone:
            return
        self._sink_gone = gone
        self.vol_spin.set_sensitive(not gone)
        self._update_pult()
        # field verdict: the banner names the state and the
        # insensitivity shows where it bites -- no homebrew
        # badges, no loose prose outside a card
        self.gone_banner.set_revealed(gone)
        if not gone:
            self._refresh_all()

    def _on_mic_pick(self, node, desc):
        """A deliberate re-pick from the mic picker: the pick IS
        the exit from the gone state (field doctrine: only the
        user or the rig's return moves the mic).

        A row for a port behind another profile speaks for no node
        yet, so it is a DOOR rather than a choice: switch the card
        and veto the pick, and the port arrives as an ordinary row
        when the graph settles. Nothing here handles the output
        going away with it -- an output that leaves is an output
        that leaves, whether a profile switch or a pulled cable
        took it, and the window already has one banner and one
        heartbeat for that."""
        if pw_backend.is_card_entry(node):
            return self._switch_card_for(node)
        if self._mic_gone:
            self._mic_gone = False
            self.mic_banner.set_revealed(False)
            self._update_pult()
        self._apply_entry_route()
        self._adopt_selected_source()

    def _switch_card_for(self, key):
        """Put the card on the profile that carries this port, then
        let the ordinary refresh find it. Returns False so the
        picker rolls its selection back: the row that was chosen is
        about to stop existing, and the row that replaces it is a
        different one."""
        self._pending_port = pw_backend.switch_to_port(key,
                                                       self.sources)
        return False

    def _adopt_pending_port(self):
        """After a profile switch the port arrives as an ordinary
        row; select it once, then forget it. One shot on purpose --
        a port that never arrives must not keep moving the
        selection under the person's hand."""
        hit = pw_backend.find_port_entry(
            self.sources, getattr(self, "_pending_port", None))
        if hit is None:
            return
        self._pending_port = None
        self.mic_picker.select(hit["name"], hit["desc"])
        self._on_mic_pick(hit["name"], hit["desc"])

    def _apply_entry_route(self):
        """A row names a jack, so choosing it moves the card there
        -- the route belongs to the device, which is why GNOME
        follows us and why we must follow it. Off the main loop:
        pw-cli is a subprocess and a route change makes the graph
        churn. Nothing is read back here; the next heartbeat brings
        the new state."""
        src = self._selected_source() or {}
        route = src.get("route")
        if not route or route.get("active"):
            return

        def work():
            try:
                pw_backend.set_input_route(route)
            except Exception as e:
                debug.log("input route: %s" % e)

        pw_backend.in_thread(work)

    def _adopt_selected_source(self):
        src = self._selected_source()
        debug.mic_trace("adopt src=%r core=%r in_list=%s"
                     % ((src or {}).get("name"),
                        self.mic_picker.core.node,
                        any(s["name"] == self.mic_picker.core.node
                            for s in self.sources)))
        if not src:
            return
        self._recompute_mic()
        prof = self.mic_store.match(src["name"])
        self.cal = {}
        if prof:
            for i in range(self.mic_ch):
                path = prof.get("cal", {}).get(str(i))
                if path:
                    self.cal[i] = path
        # and its remembered sensitivity, so the line is there before
        # anything is measured. A working point costs half a minute of
        # silence and is worth the same on every earphone measured
        # through this microphone -- there is no reason to find it twice
        self._load_knees(src)
        self._rebuild_cal_row()
        # the rig is row context: take passports compare against
        # the SELECTED mic, so a rig switch rebuilds the rows.
        # Field-caught with the liberty profile: opened native
        # (E.A.R.S), switched to the Umik in place -- the rows
        # kept their open-time _rig_group=None and no header
        # appeared, while the tooltip (built unconditionally)
        # kept telling the truth. The data was clean; the rows
        # were stale.
        self._rebuild_page()
        self._rebuild_map_slots()
        self._sync_cal_labels()
        self._persist_mic()
        self._rebuild_session()
        # THE LEVEL TRAVELS ONE WAY ONLY: _ensure_session reads
        # the pair's memory when it builds the session, and
        # _refresh_volume reads it when no level is established
        # yet. A restore here was a second door to the same
        # number, and it was shut anyway -- _rebuild_session
        # above leaves self.session None until the constructor
        # arms it, so the branch could never fire at open.
        self._refresh_volume()
        # the field's stale verdict: the pult judged BEFORE the
        # mic was born (constructor order), the prefill then
        # bound the canonical node, and nobody re-judged -- the
        # "mic not resolved" text and the locked buttons
        # outlived their own truth until the next graph event.
        # The law: the court sits again after every move of the
        # rig -- prefill, profile restore, user pick, cal alike.
        self._update_pult()

    def _rebuild_session(self):
        """The input or the capture width changed: the session's cfg
        is baked at construction, so rebuild it and re-adopt the
        stored takes rather than measure with a stale one.

        An ENTERED session is torn down first, the same __exit__ the
        window's own close and retarget already use. It used to be
        locked instead, and the lock could not do the job it was
        given: it never protected a profile from mixed rigs (close
        the window, reopen, and any mic measures into the same
        takes), while it did stop a person who had just swept the
        wrong jack from moving to the right one. It also never
        lifted -- _entered is set by the first sweep and cleared by
        nothing, so a sweep cancelled with Stop, or a take deleted,
        left the row dead with nothing recorded."""
        if self.session is None:
            return
        if self._entered:
            try:
                self.session.__exit__(None, None, None)
            except Exception:
                pass
            self._entered = False
        self.session = None
        self._canvas_ids = {}
        self._canvas_session = None
        self._ensure_session(arm=False, quiet=True)
        self._refresh_all()

    def _play_map(self):
        """Which sink channel each target sweeps through.

        The binding answers it -- the same pairing the main window
        edits -- so a target moved onto another output is measured
        there too, instead of the ring's own position being taken for
        an output index. None per target where nothing is paired, and
        None for the whole map with no live sink, which leaves the
        session on its old index-for-index behaviour."""
        node = self.sink_node
        if not node:
            return None
        try:
            sink_keys = self._pw_output_channels(node)
            cmap = self.parent.store.reconcile_map(
                node, list(self.ch_keys), sink_keys)
        except Exception as e:
            debug.log("play map: %s" % e)
            return None
        if not sink_keys:
            return None
        out = []
        for key in self.ch_keys:
            out.append(next((i for i, s in enumerate(sink_keys)
                             if cmap.get(s) == key), None))
        return tuple(out)

    def _pw_output_channels(self, node):
        """The sink's channels, off the heartbeat's own snapshot.

        Asking pw_backend directly costs a pw-dump SUBPROCESS, and this
        is called from the tab row and the Add menu -- at window open
        and again on every refresh. That is what made the window take a
        visible moment to appear. The rule this window already learned
        once, when switching a route froze it long enough for the shell
        to offer Force Quit: anything that shells out rides the
        heartbeat's dump or goes off the main loop."""
        for s in (self._pw.sinks or []):
            if s.get("name") == node:
                return list(s.get("channels") or [])
        return []

    def _assert_entry_route(self):
        """Put the card on the jack this rig IS, right before the
        sweep. The identity carries the jack now, so there is
        nothing separate to remember -- but the route belongs to the
        device and anyone may move it, and a sweep aimed at the
        wrong jack records silence. Synchronous on purpose: this
        runs on the worker thread, before the sound."""
        route = (self._selected_source() or {}).get("route")
        if not route or route.get("active"):
            return
        try:
            pw_backend.set_input_route(route)
        except Exception as e:
            debug.log("assert input port: %s" % e)
            return
        debug.mic_trace("input port asserted: %s"
                        % route.get("description"))

    def _recompute_mic(self):
        self.mic_ch = self._mic_channels()
        self.mic_cols = self._declared_cols()
        if self.mic_col not in self.mic_cols:
            self.mic_col = self.mic_cols[0] if self.mic_cols else None
        self.mic_of = self._default_mic_of()

    def _declared_cols(self):
        """Which columns of this card carry a capsule, per the hand.

        The card's width is not an answer: it says how many wires
        exist, and one microphone on a two-column card is not two
        microphones. Presence of a per-column record IS the
        declaration -- see measure_prefs.sane_columns, which keeps an
        empty one for exactly this reason -- so a rig that has been
        calibrated or walked already declares those columns and needs
        nothing said twice.
        """
        src = self._selected_source()
        prof = self.mic_store.match(src["name"]) if src else None
        if not prof:
            return []
        return [c for c in self.mic_store.columns_of(prof["id"])
                if 0 <= c < self.mic_ch]

    def _declare_col(self, col):
        """A hand says this wire carries a capsule."""
        if col in self.mic_cols or not (0 <= col < self.mic_ch):
            return
        self.mic_cols = sorted(self.mic_cols + [col])
        self.mic_col = col
        self._persist_mic(by_hand=True)
        self._recompute_mic()
        self._rebuild_map_slots()
        self._update_pult()

    def _undeclare_col(self, col):
        """And takes it back. The record goes with it, calibration and
        sensitivity included: his call, and the price is one file to
        re-choose and one ladder to re-walk."""
        if col not in self.mic_cols:
            return
        self.mic_cols = [c for c in self.mic_cols if c != col]
        self.cal.pop(col, None)
        self.mic_col = self.mic_cols[0] if self.mic_cols else None
        self._persist_mic(by_hand=True)
        self._recompute_mic()
        self._rebuild_map_slots()
        self._update_pult()

    def _mic_channels(self):
        src = self._selected_source()
        if not src:
            # NO MICROPHONE, NO COLUMNS. Two was a placeholder from
            # when the width was clamped there anyway, and it showed as
            # a card with channels L and R, a sensitivity fader and an
            # offer to calibrate "L" -- for a microphone that had not
            # been chosen. The same fiction the targets carried until
            # they were born from the card instead of from a constant.
            return 0
        # No stored override. It existed because a card could enumerate
        # a width it did not capture, and a hand could correct it -- but
        # the correction was a COUNT, and a count cannot say which wire
        # carries what. The per-target column picker says it exactly,
        # and a stale stored 2 was what kept his sixteen-column
        # interface showing L and R.
        return pw_backend.source_width(src)

    def _mic_labels(self):
        """A capture column wears the name the CARD gives it.

        His ports read capture_AUX0..capture_AUX15 and the picker was
        offering Column 0..Column 15 -- a numbering I invented while
        the width was still clamped to two and there was nothing real
        to read. The source has carried its channel names in the
        heartbeat's snapshot since the width became honest, so the
        column is called what the card calls it, and a name in the
        picker matches a name in pw-top.

        Mono and L/R survive for a rig that has no names of its own,
        because that is what everyone calls those."""
        src = self._selected_source() or {}
        names = list(src.get("channels") or [])
        if len(names) == self.mic_ch and self.mic_ch > 2:
            return names
        if not self.mic_ch:
            return []
        if self.mic_ch == 1:
            return ["Mono"]
        if self.mic_ch == 2:
            return names if len(names) == 2 else ["L", "R"]
        return ["Column %d" % i for i in range(self.mic_ch)]

    def _default_mic_of(self):
        """Which DECLARED column each target starts on.

        One declared column and there is nothing to choose: every
        target reads it, and the question is never asked. Two or more
        and this is still a guess -- the right side on the second, the
        rest on the first -- which is why it dies when the target names
        its column by hand. It picks from the DECLARED set now, not
        from the card's width: a two-wire card carrying one capsule
        used to send FR to column 1, a wire nobody had said was in use.
        """
        cols = self.mic_cols
        if not cols:
            return {}
        first = cols[0]
        second = cols[1] if len(cols) > 1 else first
        m = {}
        for k, key in enumerate(self.ch_keys):
            m[k] = second if key.upper().endswith("R") else first
        return m

    def _cal_testimony(self, path):
        """The slot's cloud: ONE number -- foreign profiles --
        in a colored pill, everything verbal in the tooltip
        (the architect's dress code: every row equally
        dressed, the noise off the surface). The color is the
        WEIGHT of the statistical anomaly, never a verdict --
        the analog doctrine stands, the one who knows the
        analog layer judges. Native-only biography is an echo
        and shows nothing. Returns (count, severity, tip) or
        three Nones."""
        try:
            sha = measure_build.cal_sha_cached(path)
        except OSError:
            return None, None, None
        entries = measure_build.cal_biography(
            self.parent.store.profiles.values(), sha)
        if not entries:
            return None, None, None
        me = _node_identity(self.mic_picker.core.node)
        f_prof, n_prof = set(), set()
        f_takes = 0
        f_lines = []
        for e in entries:
            if _node_identity(e["node_match"]) == me:
                n_prof.update(e["profiles"])
                continue
            f_prof.update(e["profiles"])
            f_takes += e["count"]
            f_lines.append("%s: %s" % (e["name"], ", ".join(
                "%s (%d)" % (pn, c)
                for pn, c in sorted(e["profiles"].items()))))
        sev = measure_build.badge_severity(
            len(n_prof), len(f_prof))
        if sev is None:
            return None, None, None
        tip = ("used with another rig in %d profile%s "
               "\u00b7 %d take%s"
               % (len(f_prof), "" if len(f_prof) == 1 else "s",
                  f_takes, "" if f_takes == 1 else "s"))
        for ln in f_lines:
            tip += "\n" + ln
        if not n_prof:
            tip += "\nthis pairing has no prior takes"
        return len(f_prof), sev, tip

    _badge_css_installed = False

    @classmethod
    def _install_badge_css(cls):
        if cls._badge_css_installed:
            return
        cls._badge_css_installed = True
        css = Gtk.CssProvider()
        data = """
        .cal-badge {
          border-radius: 10px;
          padding: 1px 8px;
          font-size: 0.85em;
        }
        .cal-badge.green {
          background-color: alpha(@success_bg_color, .2);
          color: @success_color;
        }
        .cal-badge.amber {
          background-color: alpha(@warning_bg_color, .25);
          color: @warning_color;
        }
        .cal-badge.red {
          background-color: alpha(@error_bg_color, .2);
          color: @error_color;
        }
        """
        if hasattr(css, "load_from_string"):
            css.load_from_string(data)
        else:
            css.load_from_data(data.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _rebuild_cal_row(self):
        self._install_badge_css()
        grp = getattr(self, "cal_group", None)
        if grp is None:
            return
        for row in (list(getattr(self, "cal_rows", []))
                    + list(getattr(self, "knee_rows", []))):
            if row.get_parent() is not None:
                grp.remove(row)
        self.cal_rows = []
        # their own list: cal_rows is zipped with cal_cols in
        # _sync_cal_labels, and a sensitivity row in that list would be
        # paired with the next column and given a calibration's text
        self.knee_rows = []
        self.cal_cols = []
        self.cal_btns = {}
        self.cal_badges = {}
        self.cal_clears = {}
        labels = self._mic_labels()
        # ONE column: the one the tab in view reads. A row per column
        # asked which of sixteen belonged to the target on screen; the
        # answer is directly above it now.
        # THE COLUMN IN VIEW, which is the tab above these rows. It
        # used to be the column the selected TARGET reads, back when
        # the card had one channel for the whole rig and no strip of
        # its own -- so the fader under a picker pointed at whatever
        # the target picker said.
        used = [self.mic_col] if self.mic_col is not None else []
        for i in used:
            # ONE row: the fader, its search, and the line that says
            # where it stands. No heading -- a fader in this card IS
            # the sensitivity, and a title would cost a line of a card
            # he is deliberately shortening. The column is named by
            # the row above it.
            frow = Adw.PreferencesRow()
            frow.set_activatable(False)
            # SIX, not two: the audit keeps every spacing on the 6px
            # grid, and a stray two here was the one finding in the
            # whole window
            fcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            fbox = Gtk.Box(spacing=6)
            for side in ("start", "end"):
                getattr(fbox, "set_margin_" + side)(12)
            fbox.set_margin_top(6)
            # ONE scale, re-parented rather than rebuilt: it carries
            # the operator's hand position, and a fresh widget on every
            # tab change would drop it
            old_parent = self.gain_spin.get_parent()
            if old_parent is not None:
                old_parent.remove(self.gain_spin)
            fbox.append(self.gain_spin)
            # a microphone with an arrow down its stand, beside the
            # fader it walks. The word "Find" said nothing about WHAT
            # was being found, and this row has no heading to say it
            kb = Gtk.Button()
            kb.set_icon_name("pde-find-gain-symbolic")
            kb.set_valign(Gtk.Align.CENTER)
            kb.add_css_class("flat")
            kb.set_tooltip_text(
                "Walk this input's gain in SILENCE -- nothing is played "
                "-- and find where the microphone's own noise stops "
                "being buried in the converter's. Below that point SNR "
                "is being thrown away; above it more gain buys nothing "
                "and costs headroom. Decibels here are the CONTROL's "
                "own axis, not the card's.")
            kb.connect("clicked", self._on_knee)
            kb.set_sensitive(self._knee_btn_live())
            fbox.append(kb)
            fcol.append(fbox)
            krow = Gtk.Label(xalign=0.0)
            krow.add_css_class("dim-label")
            krow.add_css_class("caption")
            krow.set_wrap(True)
            for side in ("start", "end"):
                getattr(krow, "set_margin_" + side)(12)
            krow.set_margin_bottom(6)
            fcol.append(krow)
            frow.set_child(fcol)
            grp.add(frow)
            self.knee_rows.append(frow)
            self.knee_row = krow
            self.knee_btn = kb
            row = Adw.ActionRow()
            row.set_title("%s calibration" % labels[i])
            badge = Gtk.Label()
            badge.add_css_class("cal-badge")
            badge.set_valign(Gtk.Align.CENTER)
            badge.set_visible(False)
            row.add_suffix(badge)
            self.cal_badges[i] = badge
            rm = Gtk.Button(label="Remove")
            rm.set_valign(Gtk.Align.CENTER)
            rm.add_css_class("flat")
            rm.set_visible(False)
            rm.set_tooltip_text(
                "Measure with no calibration on this capture "
                "channel. Takes already recorded keep the "
                "calibration they were made with.")
            rm.connect("clicked", self._make_cal_clear_cb(i))
            row.add_suffix(rm)
            self.cal_clears[i] = rm
            btn = Gtk.Button(label="Choose\u2026")
            btn.set_valign(Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.connect("clicked", self._make_cal_cb(i))
            row.add_suffix(btn)
            row.set_activatable_widget(btn)
            grp.add(row)
            self.cal_rows.append(row)
            self.cal_cols.append(i)
            self.cal_btns[i] = btn
            # SENSITIVITY, sibling of the calibration and for the same
            # reason: both are the provenance of the microphone plugged
            # into THIS column, they share an owner and a lifetime, and
            # the store keeps them in one record. On the microphone's
            # fader row it read as a property of the card, and that row
            # cannot say which column a knee was measured on -- this
            # row's own title does, so the line under it stays short.
        self._ensure_cal_manage_row()
        self._refresh_cal_manage()
        self._sync_cal_labels()

    def _ensure_cal_manage_row(self):
        """The recorded-calibration library, in the RIG's card.

        It was under the capture column for a while, which read as if
        it belonged to the target being measured. It does not: it is
        every calibration this app has ever recorded, and it belongs
        beside the microphone."""
        if not hasattr(self, "cal_manage_row"):
            r = Adw.ActionRow()
            r.set_title("Recorded calibrations")
            b = Gtk.Button(label="Manage\u2026")
            b.set_valign(Gtk.Align.CENTER)
            b.add_css_class("flat")
            b.connect("clicked", lambda *_: self._open_cal_manager())
            r.add_suffix(b)
            r.set_activatable_widget(b)
            self.cal_manage_row = r
            self.mic_group.add(r)
        else:
            # the rows above it are removed and re-added on every
            # rebuild, and Adw.PreferencesGroup.add appends, so this
            # one has to be pushed back down or it climbs above them.
            # Same shape as the transport: an invariant belongs to the
            # code that adds rows.
            r = self.cal_manage_row
            if r.get_parent() is not None:
                self.mic_group.remove(r)
                self.mic_group.add(r)

    def _rebuild_map_slots(self):
        """The MICROPHONE's rows: which channel it arrives on, what
        that channel's sensitivity is, and its calibration.

        They live in the microphone's own card because none of them
        needs to know a target. A rig is set up in that order -- the
        microphone first, knowing nothing about routing; then the
        output, which can only be chosen once there is something to
        capture it with; then the level. Choosing the capture channel
        eight times, once per target of a 7.1 set, was the cost of
        having it under the tabs.
        """
        grp = getattr(self, "mic_group", None)
        if grp is None:
            return
        # EACH REMOVES ITS OWN. The cal rows belong to
        # _rebuild_cal_row, which has three callers and must clear
        # them wherever it runs; this one owns the column picker. They
        # were both clearing both, so the second pass asked the group
        # to drop a row it no longer held and Adw said so in the
        # terminal -- a warning nobody planned is one that will hide a
        # real one later.
        for row in getattr(self, "_cap_rows", []):
            if row.get_parent() is not None:
                grp.remove(row)
        self._cap_rows = []
        # THE DECLARED COLUMNS ARE THE PICKER. The row that used to
        # stand here asked which single channel the microphone arrives
        # on -- one answer for the whole rig, re-asked every session
        # and never written down. A card's wires are not the question:
        # which of them carry a capsule is, and only a hand knows it.
        if self.mic_ch:
            row = self._build_col_row()
            grp.add(row)
            self._cap_rows.append(row)
        self.cal_group = grp
        self._rebuild_cal_row()
        self._dress_act_row()

    def _build_col_row(self):
        """The strip of declared columns, with the door to declare one
        more and the one that takes the shown one back."""
        row = Adw.PreferencesRow()
        row.set_activatable(False)
        box = Gtk.Box(spacing=6)
        for side in ("start", "end"):
            getattr(box, "set_margin_" + side)(12)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        bar = Gtk.Box(spacing=0)
        bar.set_valign(Gtk.Align.CENTER)
        self.col_tabs = chantabs.ChannelTabs(bar, self._on_col_pick)
        labels = self._mic_labels()
        keys = [labels[c] if c < len(labels) else "Column %d" % c
                for c in self.mic_cols]
        self._col_of_label = dict(zip(keys, self.mic_cols))
        self.col_tabs.rebuild(
            keys, selected=(self._label_of_col(self.mic_col)
                            if self.mic_col is not None else None))
        box.append(bar)
        if self.mic_col is not None:
            live = Gtk.LevelBar()
            live.set_min_value(0.0)
            live.set_max_value(1.0)
            live.set_size_request(90, -1)
            live.set_valign(Gtk.Align.CENTER)
            live.set_value(self._meter_fraction(self.mic_col))
            self._meter_bars[self.mic_col] = live
            box.append(live)
        if not self.mic_cols:
            # the card is there and nothing has been said about it yet,
            # which is a state to invite out of, not to report
            lab = Gtk.Label(label="no capture channel declared yet",
                            xalign=0.0)
            lab.add_css_class("dim-label")
            lab.set_valign(Gtk.Align.CENTER)
            box.append(lab)
        end = Gtk.Box(spacing=6, hexpand=True,
                      valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
        box.append(end)
        rm = Gtk.Button(icon_name="list-remove-symbolic",
                        valign=Gtk.Align.CENTER)
        rm.add_css_class("flat")
        rm.set_tooltip_text(
            "Take this capture channel back. Its calibration and its "
            "measured sensitivity go with it.")
        rm.set_sensitive(self.mic_col is not None)
        rm.connect("clicked", lambda _b: self._undeclare_col(
            self.mic_col))
        end.append(rm)
        add = Gtk.MenuButton(icon_name="list-add-symbolic",
                             valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.set_tooltip_text(
            "Declare another capture channel. Knock on the capsule "
            "and watch which one moves.")
        free = [c for c in range(self.mic_ch)
                if c not in self.mic_cols]
        add.set_sensitive(bool(free))
        add.set_popover(self._col_add_popover(free))
        end.append(add)
        row.set_child(box)
        return row

    def _label_of_col(self, col):
        labels = self._mic_labels()
        if col is None:
            return None
        return (labels[col] if col < len(labels)
                else "Column %d" % col)

    def _on_col_pick(self, key):
        col = self._col_of_label.get(key)
        if col is None or col == self.mic_col:
            return
        self.mic_col = col
        self._rebuild_cal_row()
        self._update_pult()

    def _col_add_popover(self, free):
        """The columns not yet declared, each with its own live level.

        The picker meters what it offers, because a card with sixteen
        columns tells you nothing about which one the microphone is on
        -- knock on the capsule, watch the list. The bars are
        registered by COLUMN, not by row position: this list is a
        subset, and a bar keyed by position would have shown a
        neighbour's level.
        """
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, "set_margin_" + side)(6)
        labels = self._mic_labels()
        for c in free:
            b = Gtk.Button()
            b.add_css_class("flat")
            inner = Gtk.Box(spacing=12)
            lbl = Gtk.Label(label=(labels[c] if c < len(labels)
                                   else "Column %d" % c), xalign=0.0)
            lbl.set_hexpand(True)
            meter = Gtk.LevelBar()
            meter.set_min_value(0.0)
            meter.set_max_value(1.0)
            meter.set_size_request(90, -1)
            meter.set_valign(Gtk.Align.CENTER)
            meter.set_value(self._meter_fraction(c))
            self._meter_bars[c] = meter
            inner.append(lbl)
            inner.append(meter)
            b.set_child(inner)
            b.connect("clicked", self._make_declare_cb(pop, c))
            box.append(b)
        pop.set_child(box)
        return pop

    def _make_declare_cb(self, pop, col):
        def cb(_b):
            pop.popdown()
            self._declare_col(col)
        return cb

    def _say(self, text):
        """What the window has to say, on its own line under the
        transport -- where a sentence can be read."""
        if getattr(self, "center", None) is not None:
            self.center.set_text(text or "")

    def _dress_act_row(self):
        """The play button names its target: a triangle alone leaves
        the person to remember which tab it obeys."""
        btn = getattr(self, "play_btn", None)
        if btn is None:
            return
        ch = self._selected_ch
        btn.set_tooltip_text(
            "Measure %s" % _speaker_name(self.ch_keys[ch])
            if 0 <= ch < len(self.ch_keys) else "Measure")

    # ---- the input meter, one bar per capture column ------------------
    #
    # His idea, and the best one of the evening: a card with sixteen
    # columns tells you nothing about which one the microphone is on.
    # Knock on the capsule, watch the list, see the column move. No
    # measurement, no guessing, no probe script.
    METER_FLOOR = -60.0          # the bottom of the bars
    METER_FALL = 40.0            # dB per second the display falls

    def _meter_wanted(self):
        """A tap costs a capture stream, so it runs only when it can
        be both useful and harmless: the window on screen, a rig that
        is actually THERE, and no sweep in flight -- during a sweep
        the only thing this window offers is Stop, and the capture
        belongs to the take.

        Present, not merely chosen, and by the window's OWN test --
        the same pair that darkens the transport and raises the
        banner. The picker keeps a vanished rig on purpose, so that a
        mic pulled out and put back is the same rig; asking the picker
        whether a mic exists answers a different question, and the
        field showed it at once: the banner said the mic was gone
        while the bars beside it still moved. One window, one idea of
        what present means."""
        return bool(self.get_mapped() and not self._busy
                    and self.mic_ch and self._source_present()
                    and not self._mic_gone)

    def _sync_inmeter(self):
        want = self._meter_wanted()
        if want and not self._inmeter.alive():
            src = self._selected_source() or {}
            # its own claim to a live node, then the id. A door row
            # declares node = None and carries no id now, but a meter
            # is the last place to trust one field alone: what it taps
            # must be the node the picker resolves to and nothing else.
            node = src.get("id")
            if node is None or not src.get("node"):
                return
            try:
                self._inmeter.start(node, self.mic_ch)
            except Exception as e:
                debug.log("input meter: %s" % e)
                return
            if self._meter_tick is None:
                self._meter_tick = GLib.timeout_add(
                    66, self._on_meter_tick)
        elif not want and self._inmeter.alive():
            self._inmeter.stop()
            self._meter_dark()

    def _meter_dark(self):
        """A meter with nothing behind it reads EMPTY.

        Leaving the last levels on the bars would be the same lie as a
        green dot on digital silence: a picture that outlives the thing
        it pictured."""
        self._meter_shown = {}
        self._paint_meters()

    def _on_meter_tick(self):
        if not self._meter_wanted():
            self._inmeter.stop()
            self._meter_tick = None
            self._meter_dark()
            return False
        peaks = self._inmeter.latest()
        fall = self.METER_FALL * 0.066
        shown = self._meter_shown
        for i in range(self.mic_ch):
            live = peaks[i] if peaks and i < len(peaks) else self.METER_FLOOR
            was = shown.get(i, self.METER_FLOOR)
            shown[i] = live if live >= was else max(live, was - fall)
        self._paint_meters()
        return True

    def _meter_fraction(self, ch):
        db = self._meter_shown.get(ch, self.METER_FLOOR)
        lo = self.METER_FLOOR
        return min(1.0, max(0.0, (db - lo) / (0.0 - lo)))

    def _paint_meters(self):
        for ch, bar in list(self._meter_bars.items()):
            try:
                bar.set_value(self._meter_fraction(ch))
            except Exception:
                self._meter_bars.pop(ch, None)




    def _open_cal_manager(self):
        """The cal history, reified the HIG way: a boxed list in
        a dialog, one row per cal origin, bulk reassign on each
        (the operation is by-sha by design). The refilled list
        after a move IS the feedback."""
        if not self.edit_pid:
            return
        dlg = Adw.Dialog()
        dlg.set_title("Recorded calibrations")
        dlg.set_content_width(440)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(12)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.add_css_class("boxed-list")
        box.append(lb)
        tv.set_content(box)
        dlg.set_child(tv)

        def refill():
            child = lb.get_first_child()
            while child:
                nxt = child.get_next_sibling()
                lb.remove(child)
                child = nxt
            m = ((self.parent.store.get(self.edit_pid) or {})
                 .get("measurement")) or {}
            for g in measure_build.cal_groups(m):
                row = Adw.ActionRow()
                row.set_title(g["file"] or "Raw capture")
                n = g["count"]
                sub = "%d take%s" % (n, "" if n == 1 else "s")
                if len(g["rigs"]) == 1:
                    sub += " \u00b7 " + g["rigs"][0]
                elif g["rigs"]:
                    # a cal that served several rigs answers
                    # HOW MANY TIMES on each -- the sitting's
                    # word on the inventory
                    sub += " \u00b7 " + ", ".join(
                        "%s (%d)" % (r, g["rig_counts"][r])
                        for r in g["rigs"])
                row.set_subtitle(sub)
                if g["sha"]:
                    rm = Gtk.Button(label="Remove")
                    rm.set_valign(Gtk.Align.CENTER)
                    rm.add_css_class("flat")
                    rm.set_tooltip_text(
                        "Read these takes raw. The stored "
                        "magnitude is uncalibrated either way -- "
                        "the calibration is applied at reading "
                        "time, so taking it off corrects a "
                        "reading rather than the recording.")
                    rm.connect("clicked", self._make_reassign_cb(
                        g["sha"], g["file"], g["count"], refill,
                        to_raw=True))
                    row.add_suffix(rm)
                b = Gtk.Button(label=("Reassign\u2026" if g["sha"]
                                      else "Assign\u2026"))
                b.set_valign(Gtk.Align.CENTER)
                b.add_css_class("flat")
                b.connect("clicked", self._make_reassign_cb(
                    g["sha"], g["file"], g["count"], refill))
                row.add_suffix(b)
                row.set_activatable_widget(b)
                lb.append(row)
        refill()
        dlg.present(self)
        return dlg

    def _make_reassign_cb(self, sha, fname, count, refill,
                          to_raw=False):
        """Chooser, then a plain-words confirmation, then the
        bulk move; the fit stales honestly through its
        fingerprint and the Re-fit machinery offers the
        recompute. With to_raw the chooser is skipped: there is
        no file to pick, the takes simply go back to being read
        without a calibration."""
        def ask_and_move(path):
            ask = Adw.AlertDialog(
                heading="Move %d take%s?"
                        % (count, "" if count == 1 else "s"),
                body="%s \u2192 %s\nThe old calibration "
                     "stays in the library; the fit will be "
                     "marked stale."
                     % (fname or "raw capture",
                        os.path.basename(path) if path
                        else "raw capture"))
            ask.add_response("cancel", "Cancel")
            ask.add_response("move", "Move")
            ask.set_default_response("move")
            ask.set_close_response("cancel")

            def done2(_d, resp):
                if resp != "move":
                    return
                measure_build.reassign_cal(
                    self.parent.store, self.edit_pid, sha, path)
                refill()
                self._refresh_all()
            ask.connect("response", done2)
            ask.present(self)

        def cb(_btn):
            if to_raw:
                ask_and_move(None)
                return
            dialog = Gtk.FileDialog()
            dialog.set_title("Choose the calibration to move "
                             "%d take%s onto"
                             % (count, "" if count == 1 else "s"))

            def done(d, res):
                try:
                    gfile = d.open_finish(res)
                except GLib.Error:
                    return
                path = gfile.get_path() if gfile else None
                if not path:
                    return
                ask_and_move(path)
            dialog.open(self, None, done)
        return cb

    def _make_cal_cb(self, ch):
        def cb(_btn):
            dialog = Gtk.FileDialog()
            dialog.set_title("Choose cal for rig channel %s"
                             % self._mic_labels()[ch])

            def done(d, res):
                try:
                    gfile = d.open_finish(res)
                except GLib.Error:
                    return
                path = gfile.get_path() if gfile else None
                if path:
                    self.cal[ch] = path
                    self._sync_cal_labels()
                    self._persist_mic()
            dialog.open(self, None, done)
        return cb

    def _make_cal_clear_cb(self, ch):
        """Take the calibration off this capture channel. The
        library keeps the file and every take keeps the sha it was
        measured with -- provenance is not undone by a later
        choice. Only what the NEXT take will carry changes."""
        def cb(_btn):
            if not self.cal.get(ch):
                return
            self.cal.pop(ch, None)
            self._sync_cal_labels()
            self._persist_mic(by_hand=True)
        return cb

    def _make_discard_cb(self, ch, take_id):
        def cb(_btn):
            if self.session is None or self._busy:
                return
            try:
                self.session.discard(ch, take_id)
            except ms.MeasureError:
                return
            cid = self._canvas_ids.pop((ch, take_id), None)
            if cid is not None and self.edit_pid:
                try:
                    measure_build.remove_takes(
                        self.parent.store, self.edit_pid, [cid])
                except Exception as e:
                    self._error("Could not delete the stored take: "
                                "%s" % e)
            self._refresh_all()
        return cb

    # ---- measurement ------------------------------------------------------
    def _dress_tabs(self):
        """Draw one tab per TARGET, with the sink channel it plays
        through in small type. The ring built its speakers in the same
        breath as itself and never had to be told again; a row has to
        be, because the target list moves with the profile."""
        if getattr(self, "tabs", None) is None:
            return
        keys = list(self.ch_keys)
        pm = self._play_map()
        sink = []
        try:
            sink = self._pw_output_channels(self.sink_node)
        except Exception:
            sink = []
        mate = {}
        for i, key in enumerate(keys):
            idx = pm[i] if (pm and i < len(pm)) else None
            if idx is not None and 0 <= idx < len(sink):
                mate[key] = sink[idx]
        self._dress_add_button()
        got = self.tabs.rebuild(keys, mate,
                                keys[self._selected_ch]
                                if 0 <= self._selected_ch < len(keys)
                                else None)
        if got in keys:
            self._selected_ch = keys.index(got)

    def _dress_add_button(self):
        """Offer every target for every free output. The first level is
        the profile's own channels followed by the rest of the target
        vocabulary, because a profile with none has to be able to gain
        its first."""
        btn = getattr(self, "_add_btn", None)
        if btn is None:
            return
        try:
            sink = self._pw_output_channels(self.sink_node)
        except Exception:
            sink = []
        cmap = {}
        if sink:
            try:
                cmap = self.parent.store.reconcile_map(
                    self.sink_node, list(self.ch_keys), sink)
            except Exception:
                cmap = {}
        free = [c for c in sink if not cmap.get(c)]
        have = list(self.ch_keys)
        menu = Gio.Menu()
        for target in have + [t for t in eq.TARGETS if t not in have]:
            sub = Gio.Menu()
            for ch in free:
                item = Gio.MenuItem.new(ch, None)
                item.set_action_and_target_value(
                    "measure.pair-add",
                    GLib.Variant("s", "%s|%s" % (target, ch)))
                sub.append_item(item)
            menu.append_submenu(target, sub)
        btn.set_menu_model(menu if free else None)
        btn.set_sensitive(bool(free))
        btn.set_tooltip_text(
            "Choose the output a target plays on -- a target already "
            "here MOVES to it" if free else
            "Every output channel already carries a target")
        rm = getattr(self, "_del_btn", None)
        if rm is None:
            return
        ch = self._selected_ch
        empty = (0 <= ch < len(self.ch_keys)
                 and self._target_is_empty(ch))
        rm.set_sensitive(empty)
        rm.set_tooltip_text(
            "Remove this target" if empty else
            "This target has measurements -- delete its takes first")

    def _target_is_empty(self, ch):
        """Nothing has been measured or typed on this target.

        Takes and bands both count: a target carries the takes of this
        sitting and whatever curve the main window has on it, and
        neither is something a stray click should be able to erase."""
        if self.session is not None and self.session.takes_of(ch):
            return False
        key = self.ch_keys[ch] if 0 <= ch < len(self.ch_keys) else None
        p = self.parent.store.get(self.edit_pid) if self.edit_pid else {}
        band = (((p or {}).get("channels") or {}).get(key) or {})
        return not (band.get("bands") or [])

    def _del_pair(self):
        """Remove the target in view -- the way back from a wrong Add.

        Only while it is EMPTY. In the main window an x costs nothing:
        the bands stay in the profile and the route merely plays dry.
        Here a tab IS a target, so removing it would take its takes
        with it, and a measurement is the one thing in this window
        nobody can make again by clicking. So the button goes dead
        instead, and says why."""
        ch = self._selected_ch
        if not (0 <= ch < len(self.ch_keys)) or not self._target_is_empty(ch):
            return
        if not (0 <= ch < len(self.ch_keys)):
            return
        target = self.ch_keys[ch]
        store = self.parent.store
        if self.edit_pid:
            p = store.get(self.edit_pid) or {}
            keys = [k for k in (p.get("ch_keys")
                                or list((p.get("channels") or {})))
                    if k != target]
            chans = {k: v for k, v in (p.get("channels") or {}).items()
                     if k != target}
            body = dict(p)
            body["channels"] = chans
            body["ch_keys"] = keys
            store.save_user(body)
        try:
            cur = store.reconcile_map(
                self.sink_node, list(self.ch_keys),
                self._pw_output_channels(self.sink_node))
        except Exception:
            cur = {}
        for out, val in list(cur.items()):
            if val == target:
                store.pin_channel(self.sink_node, out, None)
        p = store.get(self.edit_pid) if self.edit_pid else {}
        self.ch_keys = list((p or {}).get("ch_keys")
                            or list(((p or {}).get("channels") or {})))
        self.n_ch = len(self.ch_keys)
        self._selected_ch = min(ch, self.n_ch - 1)
        self._recompute_mic()
        self._rebuild_map_slots()
        self._rebuild_session()
        self._refresh_all()

    def _add_pair(self, value):
        """A pair declares its target: the profile gains that side,
        empty, and the output is pinned to it. Same rule as the main
        window -- choosing FL for an output says this profile has an
        FL side played there."""
        target, _, sink_ch = (value or "").partition("|")
        if not (target and sink_ch and self.sink_node):
            return
        store = self.parent.store
        pid = self._ensure_pid()
        p = store.get(pid) or {}
        keys = list(p.get("ch_keys") or list((p.get("channels") or {})))
        if target not in keys:
            chans = dict(p.get("channels") or {})
            chans.setdefault(target, {"bands": []})
            body = dict(p)
            body["channels"] = chans
            body["ch_keys"] = keys + [target]
            store.save_user(body)
        # HERE A TARGET MEASURES THROUGH ONE OUTPUT. The main window
        # adds a route -- many-to-one is deliberate on a card that sums
        # its buses -- but a sweep goes somewhere, singular, so
        # choosing an output for a target that already has one MOVES
        # it. Without this the pick simply added a second route, the
        # resolver kept answering with the first, and the tab went on
        # showing the old output while the hand had said another.
        # the CURRENT MAP, not the pins: an output can carry a target
        # because the resolver guessed it, and a guess left standing
        # answers again the moment it is asked -- so the old output has
        # to be pinned to nothing, not merely left unpinned
        try:
            cur = store.reconcile_map(
                self.sink_node, list(self.ch_keys),
                self._pw_output_channels(self.sink_node))
        except Exception:
            cur = {}
        for ch, val in list(cur.items()):
            if val == target and ch != sink_ch:
                store.pin_channel(self.sink_node, ch, None)
        store.pin_channel(self.sink_node, sink_ch, target)
        p = store.get(pid) or {}
        self.ch_keys = list(p.get("ch_keys")
                            or list((p.get("channels") or {})))
        self.n_ch = len(self.ch_keys)
        self._recompute_mic()
        self._rebuild_map_slots()
        self._rebuild_session()
        self._refresh_all()

    def _on_tab_pick(self, key):
        """A hand chose a target. The window's currency is the index
        into ch_keys; the row speaks names, so this is the one place
        the two meet."""
        if key in self.ch_keys:
            self._select_channel(self.ch_keys.index(key))

    def _select_channel(self, ch):
        self._selected_ch = ch
        if self.tabs is not None and 0 <= ch < len(self.ch_keys):
            self.tabs.select(self.ch_keys[ch])
        # the capture row, its calibration AND its gain belong to the
        # tab in view, so they are redrawn with it
        self._rebuild_map_slots()
        self._refresh_gain()
        self._rebuild_page()
        self._update_pult()

    def _on_play(self, _btn):
        self._start_measure(self._selected_ch)

    def _on_stop(self, _btn):
        # ONE STOP FOR WHATEVER IS RUNNING. Both searches watch this
        # flag between sweeps -- the gain ladder between rungs, the
        # level search between probes -- so stopping either costs
        # nothing but the time already spent. A take is not polled: it
        # is cancelled through the session, which kills the sweep in
        # flight.
        self._stop_asked = True
        if self.session is not None and self._busy:
            self.session.cancel()            # aborts the sweep in flight

    def _refresh_gain(self):
        """The capture gain, read from the same graph dump the
        window already has. It is a property of the RIG, not of a
        take: two takes of one canvas measured at different input
        gains cannot be compared, and their spread stops meaning
        anything -- which is exactly how five and a half decibels
        once looked like a crooked sound card."""
        row = getattr(self, "gain_spin", None)
        if row is None:
            return
        src = self._selected_source()
        if not src:
            # NOTHING is claimed about an input the window has not
            # reached. fader_kind of an empty record is honestly
            # "software" -- there is no element in nothing -- and that
            # was being printed as a fact about the device: "this
            # input has no gain of its own", under a card whose own
            # status line said the mic was not resolved. A partial
            # denial is worse than none, because the fields that still
            # work look authoritative.
            self._fader_kind = None
            self._gain_kind = None
            self._gain_ok = False
            self._gain_seeded = None
            row.set_sensitive(False)
            row.set_tooltip_text("No measurement mic resolved.")
            self._refresh_knee_caption()
            return
        cubic, kind = src.get("gain") or (None, None)
        # the COLUMN's own gain where the card has one: the folded
        # reading is true of neither channel the moment they differ
        col = self.mic_of.get(self._selected_ch, 0)
        per = list(src.get("gains") or [])
        if 0 <= col < len(per):
            cubic = per[col]
        # what the fader CAN do is a property of the route, not of the
        # value standing in it: at unity a hardware multiplier and a
        # software one look the same, and unity is where an untouched
        # rig sits.
        self._fader_kind = pw_backend.fader_kind(src.get("routes"),
                                                 src.get("gain"))
        self._gain_kind = kind
        self._gain_ok = self._fader_kind == "analog"
        row.set_sensitive(self._gain_ok)
        if not self._gain_ok:
            # shown, not hidden: an absent control cannot be told from
            # a broken app, while a dead one at full says something
            # about the device. It is pinned rather than left where it
            # was, because a multiplier that buys nothing still costs
            # the take its level.
            self._gain_seeded = None
            self._gain_guard = True
            row.set_value(100)
            self._gain_guard = False
            row.set_tooltip_text(
                {"attenuator": "This input's gain can only attenuate: "
                               "it sits after the converter, so "
                               "lowering it throws resolution away "
                               "and buys no headroom. Held at full.",
                 "software": "This input has no gain of its own -- "
                             "this fader would only be a multiplier "
                             "in software, which buys no headroom and "
                             "improves no noise. Held at full."}[
                     self._fader_kind])
            self._refresh_knee_caption()
            return
        self._seed_fader(row, self._knee_key()
                         or (src.get("node") or src.get("name")), cubic)
        note = {"hardware": "The card's own gain: it moves the "
                            "signal BEFORE the converter, so it "
                            "buys real headroom and real noise.",
                "software": "A software multiplier: it scales what "
                            "was already digitised, so it buys no "
                            "headroom against an analog overload "
                            "and improves no noise.",
                None: "Capture gain."}[kind]
        note += (" Every take records the gain it was measured at; "
                 "takes made at different gains are not comparable.")
        row.set_tooltip_text(note)
        self._refresh_knee_caption()

    def _assert_capture_gain(self):
        """Put the card where the slider says, right before the
        sweep. One write, no comparison, no remembering and nothing
        put back: the slider IS the card's gain while this window is
        open, and the operator is the only one who moves it.

        Everything cleverer than this failed in the field within a
        minute. Baking the value into the session let a number from
        window-open time pull the slider back mid-sweep; keeping the
        slider in sync with the graph on every heartbeat let a lagging
        dump do the same; and locking it after the first sweep made
        the one thing the operator actually wanted impossible --
        record, see the clip, lower the gain, record again."""
        if self._take_gain is None:
            return
        src = self._selected_source() or {}
        nid = src.get("id")
        if nid is None:
            return
        try:
            self._write_gain(src, self.mic_of.get(self._selected_ch, 0),
                             self._take_gain)
        except Exception as e:
            debug.log("capture gain: %s" % e)

    # ---- the working gain, found rather than guessed -----------------
    #
    # The fader says what it CAN do; this says where on it to stand.
    # An input gain sits after the microphone, so it multiplies the
    # signal and the microphone's own noise together and cannot change
    # the ratio between them -- all it changes is how far that package
    # sits above the converter's fixed floor. Below the point where the
    # input's noise overtakes that floor, SNR is being thrown away in
    # the converter; above it, more gain buys nothing and costs
    # headroom. The point is found in SILENCE, because the signal
    # cancels out of the question.

    def _knee_btn_live(self):
        """Whether the search may run at all.

        NOT gated on the pult's `live`: the ladder plays nothing, so
        it wants a source and has no use for a sink. Gated on the
        fader being analogue, because that is the only kind with
        anything to search.

        It lives here rather than only in _update_pult because the row
        is REBUILT -- a fresh button is sensitive by default, and a
        rebuild after the pult had spoken left an enabled Find under a
        card that had just said the mic was not resolved. The same
        shape as the transport landing below the rows: a state kept by
        somebody else is one the next builder misses.
        """
        return bool(not self._busy
                    and self._source_present() and not self._mic_gone
                    and getattr(self, "_fader_kind", None) == "analog")

    def _on_knee(self, _btn):
        if self._busy:
            return
        src = self._selected_source()
        if not src:
            self._error("Pick a measurement mic first.")
            return
        self._stop_asked = False
        self._busy = True
        self._set_row_sensitive(False)
        self._update_pult()
        self._say("Finding the working gain -- keep the room quiet, "
                  "nothing is played.")
        threading.Thread(target=self._knee_worker, args=(src,),
                         daemon=True).start()

    def _knee_worker(self, src):
        col = self.mic_of.get(self._selected_ch, 0)
        out = {"error": None, "verdict": None, "walk": None, "src": src}
        try:
            def said(rung, done, total):
                self._post_status(
                    "gain %+.1f dB -> %.1f dBFS   (rung %d of %d)"
                    % (rung.gain_db, rung.rms_dbfs, done, total))

            out["verdict"], out["walk"] = knee_run.ladder(
                src, col, on_rung=said,
                should_stop=lambda: self._stop_asked,
                # the ladder measures SILENCE, so claim the output
                # this window is measuring against for its duration:
                # otherwise a notification lands in the noise floor
                # and the transient test has to throw the rung away
                quiet_sink=self.sink_node)
        except Exception as e:                          # noqa: BLE001
            out["error"] = e
            debug.crashed("the gain ladder", e,
                          expected=EXPECTED_FAILURES)
        GLib.idle_add(self._knee_done, out)

    def _knee_done(self, out):
        self._busy = False
        self._set_row_sensitive(True)
        self._update_pult()
        if out["error"] is not None:
            self._say("Could not walk the gain: %s" % out["error"])
            return
        if self._stop_asked:
            # a walk does not put anything back, so a stopped one has
            # left the fader on the rung it halted on. Said plainly
            # rather than left for the eye to notice
            self._say("Stopped. The gain is on the last step walked.")
            return
        v = out["verdict"]
        if v is None or not v.usable:
            self._say("No working gain found: %s"
                      % (v.note if v is not None else "nothing measured"))
            return
        self._apply_knee(out["src"], v)

    def _knees_for_store(self, src, existing):
        """Verdicts to write: what this session measured on this
        source's ACTIVE route, plus whatever the store already had for
        columns this session did not touch.

        The route is not part of the stored key -- one microphone
        profile stands for one jack, which is what node_match already
        says -- so only the verdicts belonging to the route in view
        may be written, or a line-in ladder would overwrite the
        microphone's.
        """
        out = dict((existing or {}).get("columns") and
                   {k: v["knee"] for k, v
                    in existing["columns"].items() if v.get("knee")}
                   or {})
        name = src.get("name")
        route = next((r.get("name") for r in src.get("routes") or []
                      if r.get("active")), None)
        for (node, rt, col), v in self._knee.items():
            if node != name or rt != route:
                continue
            rec = measure_prefs.sane_knee(v)
            if rec:
                out[str(col)] = rec
        return out

    def _load_knees(self, src):
        """Bring a rig's remembered verdicts into this session, so the
        sensitivity line is there before anything is measured and a
        ladder is a CHECK rather than a first acquaintance."""
        if not src or not src.get("name"):
            return
        prof = self.mic_store.match(src["name"])
        if not prof:
            return
        route = next((r.get("name") for r in src.get("routes") or []
                      if r.get("active")), None)
        for col, rec in self.mic_store.knees_of(prof["id"]).items():
            try:
                key = (src["name"], route, int(col))
            except (TypeError, ValueError):
                continue
            self._knee.setdefault(key, dict(rec))

    def _seed_fader(self, row, seed, cubic):
        """Put a value on the fader when the column under it changes.

        THE HAND OWNS IT. The card is read for a column the first time
        that column is met, and after that the window shows what was
        last put on it -- by a hand on the fader or by the search,
        which moves it the same way.

        Reading the card at every switch is what made this hard: the
        graph's snapshot is a beat behind, a tab switch happens inside
        that beat, and the seed then hands back the value the column
        held BEFORE the drag. And since the seed runs once per column,
        the wrong number stays -- no later pass asks again. Leaving an
        L tab for an R one and coming back showed a number the card
        did not hold.
        """
        if self._gain_seeded == seed:
            return
        self._gain_seeded = seed
        held = self._gain_set.get(seed)
        self._gain_guard = True
        row.set_value(held if held is not None
                      else round((cubic or 0.0) * 100.0))
        self._gain_guard = False

    def _column_cubics(self, src):
        """What every capture column of this rig should be standing at.

        A Route takes the WHOLE list, so setting one column means
        saying what all of them are. Carrying the others across from
        the graph's snapshot is what broke: it is a beat behind, two
        drags land inside one beat, and the second write hands the
        card the first column's pre-drag value. This window knows what
        it put on each column; for a column it never touched, the card
        is the answer.
        """
        gains = list(src.get("gains") or [])
        name = src.get("name")
        route = next((r.get("name") for r in src.get("routes") or []
                      if r.get("active")), None)
        out = []
        for i, g in enumerate(gains):
            held = self._gain_set.get((name, route, i))
            out.append(held / 100.0 if held is not None else g)
        return out

    def _write_gain(self, src, col, cubic):
        """Put ONE capture column's gain on the card.

        Three callers want this -- the fader, the search and the
        pre-flight before a sweep -- and written out three times the
        search kept the whole-node call and quietly moved both
        columns. A rule kept in three places is one that will be
        missed in one of them.
        """
        route = next((r for r in (src.get("routes") or [])
                      if r.get("active")), None)
        nid = src.get("id")
        want = self._column_cubics(src)
        if (route or {}).get("channel_volumes") and 0 <= col < len(want):
            want[col] = cubic
            pw_backend.set_route_volumes(route, want)
        elif nid is not None:
            pw_backend.set_gain(nid, cubic)

    def _knee_key(self):
        """Which input a ladder's answer belongs to.

        The ROUTE is in the key because a node name does not
        distinguish jacks: a CM106 answers to one node name on both its
        microphone and its line input. And the COLUMN is in it because
        a card's columns are different wires -- on an M62, column 0 is
        Mic1 and column 1 is Mic2. Nothing needs erasing when any of
        the three changes: the lookup simply finds no answer, which is
        the truth.
        """
        src = self._selected_source() or {}
        name = src.get("name")
        if not name:
            return None
        route = next((r.get("name") for r in src.get("routes") or []
                      if r.get("active")), None)
        return (name, route, self.mic_of.get(self._selected_ch, 0))

    def _apply_knee(self, src, v):
        """Remember what the ladder found, and move the fader only if
        it is not already standing somewhere equivalent.

        Above the knee every position has the same SNR, so a fader
        already inside the margin band is already right, and moving it
        would buy nothing while fragmenting the canvas: takes made at
        different capture gains sit at different levels, which lands in
        the per-frequency spread and reads as disagreement. The knee is
        an extrapolation and wanders by a few tenths between runs, so
        without this a re-run nudges the gain every time for no reason.
        """
        key = self._knee_key()
        row = getattr(self, "gain_spin", None)
        if key is not None:
            self._knee[key] = {
                "kind": v.kind, "knee_db": v.knee_db,
                "work_db": v.work_db,
                # what the store keeps: the position to stand at, and
                # the numbers that let a second ladder agree or not
                "gain": knee_run.db_to_cubic(v.work_db)
                if v.work_db is not None else None,
                "knee_axis_db": v.knee_db,
                "flat_dbfs": next((s.rungs[-1].rms_dbfs
                                   for s in v.segments
                                   if s.kind == "flat"), None),
                "scatter_db": v.scatter,
                "at": time.strftime("%Y-%m-%d")}
        row = getattr(self, "gain_spin", None)
        cur_db = (knee_run.cubic_to_db(row.get_value() / 100.0)
                  if row is not None else None)
        if knee.already_good(cur_db, v.work_db):
            self._say("Gain left where it was -- already in the band the "
                      "ladder would have chosen.")
        else:
            cubic = knee_run.db_to_cubic(v.work_db)
            if row is not None:
                self._gain_guard = True
                row.set_value(round(cubic * 100.0))
                self._gain_guard = False
            # the search moves the fader, so it owns the value the
            # same way a hand would
            if key is not None:
                self._gain_set[key] = round(cubic * 100.0)
            col = self.mic_of.get(self._selected_ch, 0)

            def work():
                try:
                    self._write_gain(src, col, cubic)
                except Exception as e:                  # noqa: BLE001
                    debug.log("capture gain: %s" % e)

            pw_backend.in_thread(work)
            self._say("Gain set to %d%%." % round(cubic * 100.0))
        self._refresh_knee_caption()

    def _refresh_knee_caption(self):
        """Where the gain stands relative to the knee measured on the
        column in view, recomputed every time the fader moves and
        every time the tab changes. With two capsules in two jacks the
        two tabs carry two different lines, which is how a pair
        standing at different sensitivities becomes visible at all."""
        krow = getattr(self, "knee_row", None)
        row = getattr(self, "gain_spin", None)
        if krow is None:
            return
        if getattr(self, "_fader_kind", None) is None:
            krow.set_text("no measurement mic resolved")
            return
        if not getattr(self, "_gain_ok", False):
            krow.set_text("this input has no gain of its own")
            return
        v = self._knee.get(self._knee_key())
        text = None
        if v is not None and row is not None:
            v = dict(v)
            v.setdefault("knee_db", v.get("knee_axis_db"))
            text = knee.caption(v["kind"], v.get("knee_db"),
                                knee_run.cubic_to_db(
                                    row.get_value() / 100.0))
        krow.set_text(text or "not measured yet")

    def _on_gain_edited(self, scale):
        if self._gain_guard:
            return
        src = self._selected_source() or {}
        nid = src.get("id")
        if nid is None:
            return
        cubic = scale.get_value() / 100.0
        self._refresh_knee_caption()
        col = self.mic_of.get(self._selected_ch, 0)
        seed = self._knee_key()
        if seed is not None:
            self._gain_set[seed] = round(scale.get_value())

        def work():
            try:
                self._write_gain(src, col, cubic)
            except Exception as e:
                debug.log("capture gain: %s" % e)

        pw_backend.in_thread(work)

    def _lock_baked_rows(self):
        """Nothing is locked here any more; the name is kept because
        the row refresh still runs through it.

        Both rows used to go dead the moment a sweep had run, on the
        grounds that the session bakes the input and the capture
        width into its config. The config really is baked -- but the
        session can be rebuilt, which is what the window already
        does when the OUTPUT changes, and the lock could not do the
        job it was given. It never kept a profile to one rig: close
        the window, reopen it, and any input measures into the same
        takes. It never lifted either, since _entered is set by the
        first sweep and cleared by nothing, so a sweep cancelled
        with Stop -- or a take deleted straight after -- left the
        rows dead with nothing recorded. And it stood in the way of
        the one case that matters: sweeping the wrong jack and
        wanting the right one.

        What survives is the bounds check in measure_session.take(),
        which is an array bound rather than a policy: one line, and
        it cannot be wrong about a rebuilt session."""
        have = bool(self.sources or self.mic_picker.core.node)
        self.source_dd.set_sensitive(have)
        self._refresh_gain()
        self.source_dd.set_tooltip_text(None)

    def _update_pult(self):
        self._sync_inmeter()
        """The pult is the shared gone lock (field verdict): a
        sweep needs a speaker AND a mic, so both sweep triggers
        -- play and the releveler -- obey both ends of the
        chain. The rig's identity gates nothing: a mixed canvas
        is judged by its own spread (spread_trust_bound sinks
        the trust and shrinks the trusted band), and the mic row
        names a foreign rig as a fact. The speakers stay free:
        takes are per channel, and browsing the neighbor's pile
        must survive a gone device."""
        self._lock_baked_rows()
        # A SWEEP NEEDS SOMETHING TO MEASURE, which is a fourth end of
        # the chain and was never checked: with no target the play
        # button used to be lit and the answer came from the sweep
        # itself, as a failed measurement.
        live = (self._sink_present() and not self._sink_gone
                and self._source_present() and not self._mic_gone
                and bool(self.ch_keys))
        debug.mic_trace("pult live=%s sinkP=%s sinkG=%s srcP=%s "
                     "micG=%s busy=%s core=%r"
                     % (live, self._sink_present(),
                        self._sink_gone,
                        self._source_present(),
                        self._mic_gone, self._busy,
                        self.mic_picker.core.node))
        if not live and not self._busy:
            # the tracer law, third service: a locked pult
            # names the missing end out loud -- the field saw
            # every control gray while "everything is there",
            # and nothing said WHICH of the four truths failed
            # or what NAME the window was looking for.
            if not self.ch_keys:
                miss = ("nothing to measure yet -- add a target and "
                        "the output it plays through")
            elif not self._sink_present() or self._sink_gone:
                miss = "sink offline: %s" % (self.sink_node
                                             or "?")
            elif self.mic_picker.core.node:
                miss = ("mic offline: %s"
                        % self.mic_picker.core.node)
            elif self.memory.mic_for(self.sink_node):
                # the field's silent lock: a remembered mic LABEL
                # that never resolved to a live node. None is not
                # "gone" (you cannot leave without being born), so
                # the mic banner sleeps -- this names the state and
                # the way out.
                miss = "mic not resolved -- re-pick the mic"
            else:
                # NOTHING was ever chosen for this sink, which is not
                # a failure and must not read as one. "re-pick" says
                # something was lost; the reflex it earned in the
                # field was to go looking for the breakage. This is an
                # invitation, and it is the only line here that is.
                miss = "pick a measurement mic to start"
            debug.mic_trace("pult %r core=%r"
                         % (miss, self.mic_picker.core.node))
            self._say(miss)
            self._pult_missed = True
        elif not self._busy and getattr(self, "_pult_missed",
                                        False):
            # the court announces the acquittal too: a verdict
            # written before the mic was born used to outlive
            # its own truth -- the live passes moved the
            # buttons but never erased the text, and the field
            # judged the window by the words
            self._say("Ready")
            self._pult_missed = False
        self.play_btn.set_sensitive(not self._busy and live)
        if getattr(self, "relevel_btn", None) is not None:
            self.relevel_btn.set_sensitive(not self._busy and live)
        if getattr(self, "knee_btn", None) is not None:
            self.knee_btn.set_sensitive(self._knee_btn_live())
        self.stop_btn.set_sensitive(self._busy)
        if getattr(self, "sink_dd", None) is not None:
            # mid-sweep the route is not a choice
            self.sink_dd.set_sensitive(not self._busy)

    def _ensure_session(self, arm=True, quiet=False):
        """Construct the session separately from ARMING it.
        Construction is read-only (node identities, layout, sweep
        synthesis) and happens at window open, so the profile's
        stored takes are adopted and visible before a single sweep;
        __enter__ (the tempdir, foreign-stream muting, the profile
        bypass, the start volume) waits for the first sweep. quiet
        suppresses the dialogs for the opportunistic open-time
        attempt -- no mic picked yet is not an error there."""
        if self.session is None:
            mic = self.mic_picker.core.node
            if not mic:
                if not quiet:
                    self._error("Pick a measurement mic first.")
                return False
            if not self.mic_ch:
                # A REMEMBERED NAME THAT NEVER RESOLVED still fills
                # the picker, and the width comes from the live card,
                # so there is none to build a session on. It used to
                # be two by default, which built a session for a
                # microphone that is not there.
                if not quiet:
                    self._error("That microphone is not on the "
                                "system right now.")
                return False
            # A SESSION IS BUILT ONCE, in the constructor, so nothing
            # about a single run may be settled here. Which sweeps
            # hunt is not a property of a session at all: the
            # auto-level button runs the search and hands the number
            # over, and the flag that used to be passed here went out
            # of SessionConfig with the search itself.
            _t = time.monotonic()
            cfg = ms.SessionConfig(
                sink=self.sink_node,
                source=pw_backend.entry_node(mic),
                channels=self.mic_ch,
                mute_others=True, device=self.sink_desc,
                play_map=self._play_map(),
                start_volume=self.memory.volume_for(
                    self.sink_node, self._source_name()))
            try:
                # an absent home births the session unresolved:
                # the canvas adopts, statistics and refits run,
                # parking works; the graph (and every live
                # precondition, now FRESH) waits for arming
                self.session = ms.MeasureSession(
                    cfg, resolve=(self._sink_present()
                                  and self._source_present()),
                    dump=getattr(self._pw, "last_dump", None))
                debug.timing("  session construct", _t)
            except ms.RefusalError as e:
                self.session = None
                if not quiet:
                    self._error(str(e))
                return False
            except Exception as e:               # missing tools, etc.
                self.session = None
                if not quiet:
                    self._error("Could not start: %s" % e)
                return False
            self._adopt_canvas()
        if arm and not self._entered:
            try:
                self.session.__enter__()
                self._entered = True
            except ms.RefusalError as e:
                if not quiet:
                    self._error(str(e))
                return False
            except Exception as e:
                if not quiet:
                    self._error("Could not start: %s" % e)
                return False
        return True

    def _start_measure(self, ch, level_only=False):
        if self._busy:
            return
        if not self._loud_ack:
            self._confirm_loud(
                lambda: self._start_measure(ch, level_only))
            return
        # A LEVEL OF ZERO IS NOT A REQUEST TO GUESS. It used to start
        # a hunt, which was my invention and nobody asked for it -- and
        # it punched a hole in the fader law this window is built on:
        # the sweep plays the fader's number, ALWAYS, so the behaviour
        # is always predictable. Implicit behaviour is worse than a
        # refusal that names the next move.
        if not level_only and self.vol_spin.get_value() <= 0:
            self._error("The measurement level is at zero, so the sweep "
                        "would be silent.\n\nSet a level, or press the "
                        "auto-level button to measure one.")
            return
        if not self._ensure_session():
            return
        if self._sink_gone or self._mic_gone:
            return    # play is locked; stray starts no-op here
        self._level_only = level_only
        # THE ONE DOOR of the fader law: every take starts by
        # setting the session level to the fader's number, read
        # on the main thread here, applied by the worker after
        # the session is entered. One door, zero shield flags.
        # the fader's number, snapshotted here because a worker thread
        # cannot read a widget. Not a mode, not a signal: a level
        self._take_level = self.vol_spin.get_value() / 100.0
        # the input gain rides the SAME door, read here on the main
        # thread: baking it into the session let a stale number from
        # window-open time pull the slider back mid-sweep
        self._take_gain = (self.gain_spin.get_value() / 100.0
                           if getattr(self, "_gain_ok", False)
                           else None)
        self._stop_asked = False
        self._busy = True
        self._set_row_sensitive(False)
        self._update_pult()
        self._say(
            "Measuring the level on %s\u2026" % self.ch_keys[ch]
            if level_only else
            "Measuring %s\u2026" % self.ch_keys[ch])
        t = threading.Thread(target=self._measure_worker, args=(ch,),
                             daemon=True)
        t.start()

    def _hunt_level(self, ch):
        """Find the level, on the worker thread, and return it.

        The search is a DIFFERENT JOB with a different owner. It plays
        its own sweeps, claims the hardware for each, and hands back a
        number; nothing it does is a take, so nothing has to be
        discarded afterwards to pretend it was not one.
        """
        def said(p):
            thd = ("n/a" if p.thd_pct is None else
                   "%s%s%%%s" % ("<=" if p.thd_bound else "",
                                 measure_build.pct_word(p.thd_pct),
                                 "" if p.margin_db is None else
                                 " (%+.0f dB over its floor)" % p.margin_db))
            self._post_status(
                "%s: leveling %d%%  (%s, peak %.1f dBFS, SNR %s, "
                "THD@1k %s, step %d)"
                % (self.ch_keys[ch], round(100 * p.volume), p.phase,
                   p.peak_dbfs,
                   "%.1f" % p.snr_db if p.snr_db is not None else "n/a",
                   thd, p.step))

        vol, probes = level_run.hunt(
            self.session.sink, self.session.source,
            self.session.cfg.channels,
            sink_name=self.session.sink_ident["name"],
            analyze=self.mic_of.get(ch, 0),
            sweep=self.session.sweep, freqs=self.session.freqs,
            pre_silence=self.session.cfg.pre_silence,
            post_silence=self.session.cfg.post_silence,
            play_map=self.session._channel_map(ch),
            on_probe=said, should_stop=lambda: self._stop_asked)
        return vol, level_run.summary(vol, probes)

    def _measure_worker(self, ch):
        """One take on a worker thread, or one level search.

        THE FADER DOES NOT FOLLOW A SEARCH: an intermediate level is
        not a setting anyone chose, so the search narrates itself in
        the status line and the control learns the answer at the end.
        """
        result = {"error": None, "outcome": None, "level": None,
                  "found": None}
        try:
            self._assert_entry_route()
            self._assert_capture_gain()
            if self._level_only:
                result["level"], result["found"] = self._hunt_level(ch)
                self._post_status(
                    "%s: level %d%%"
                    % (self.ch_keys[ch], round(100 * result["level"])))
            else:
                self.session.set_level(self._take_level)
                result["outcome"] = self.session.take(
                    ch, analyze=self.mic_of.get(ch, 0))
        except Exception as e:
            result["error"] = e
            debug.crashed("the measurement", e, expected=EXPECTED_FAILURES)
        GLib.idle_add(self._measure_done, ch, result)

    def _post_status(self, text):
        GLib.idle_add(self._say, text)

    def _measure_done(self, ch, result):
        self._busy = False
        self._set_row_sensitive(True)
        self._update_pult()
        self._say("Ready")
        err = result["error"]
        if isinstance(err, sweep_io.MeasureCancelled):
            self._refresh_all()              # Stop: quiet, nothing stored
            return False
        if err is not None:
            self._error("Measurement failed: %s" % err)
            self._refresh_all()
            return False
        out = result.get("outcome")
        if (out is not None and getattr(out, "kind", "") == "take"
                and out.take is not None):
            self._commit_live_take(ch, out.take)
        # A SEARCH RETURNS A NUMBER, and this is where it lands: on the
        # session, on the fader and in the memory, in that order. It
        # was never on the hardware to begin with -- the moratorium
        # takes the measurement volume as a parameter, so whoever
        # sweeps next passes this number to their own claim.
        found = result.get("level")
        if found is not None:
            self.session.set_level(found, found=result.get("found"))
        v = getattr(self.session, "_v_cur", None)
        src = self._source_name()
        if v is not None and src:
            self.memory.remember(self.sink_node, source=src, volume=v)
        # AND NOW THE FADER LANDS: _refresh_all ends in _refresh_volume,
        # which no longer refuses because the hunt has finished.
        self._refresh_all()
        return False

    def _set_row_sensitive(self, on):
        if self.tabs is not None:
            self.tabs.set_sensitive(on)

    # ---- the incremental contract ------------------------------------
    def _ensure_pid(self):
        """The profile this window edits. A fresh window creates it
        on first need -- the first committed take, or the plain close
        that still leaves an empty profile behind (New's contract).

        Where it BELONGS is decided at closing time, not here.
        Creation happens at whichever moment first needs a profile
        to exist -- a committed take, a retarget, the plain close --
        and the retarget is the trap: it mints the profile while the
        window still points at the sink it is LEAVING, so binding
        here handed a new profile to the output the operator had
        just walked away from, and the main window dutifully
        switched to it."""
        if self.edit_pid:
            return self.edit_pid
        store = self.parent.store
        # IT DECLARES WHAT THE WINDOW IS SHOWING. Minting it empty was
        # honest only while the window invented FL and FR to display;
        # the targets came from the sink at open, and this writes them
        # down so the profile and the tabs cannot disagree.
        keys = list(self.ch_keys)
        pid = store.save_user({
            "name": self._profile_name(),
            "preamp": 0.0, "ch_keys": keys,
            "channels": {k: {"bands": []} for k in keys}})
        self.edit_pid = pid
        self._minted = pid
        return pid

    def _settle_home(self, pid):
        """Give a profile this window MINTED a home, if it earned
        one: the sink it ended up measuring, and only when that sink
        is the one the parent is playing. Measuring a device you are
        not listening to must not reach across and change what you
        ARE listening to. An edited profile is never rehomed -- its
        home is not this window's business."""
        if getattr(self, "_minted", None) != pid or not pid:
            return
        if not self.sink_node or self.sink_node != getattr(
                self.parent, "node", None):
            return
        try:
            self.parent.store.set_binding(self.sink_node, pid)
        except Exception as e:
            debug.log("settle home: %s" % e)

    def _apply_name(self, pid):
        name = self._profile_name()
        store = self.parent.store
        prof = store.get(pid)
        if prof and prof.get("name") != name:
            store.save_user(dict(prof, name=name))

    def _commit_live_take(self, ch, rec):
        """An accepted take is a fact of the profile the moment it
        exists: kill the app, pull the plug -- the take survives.
        Creates the profile on a fresh window's first take, threads
        one canvas session entry through the sitting, and remembers
        the canvas id so this row's trash can deletes from the
        profile too."""
        try:
            pid = self._ensure_pid()
            col = rec.capture_channel
            cal = None
            if col is not None:
                cal = self.cal.get(col, self.cal.get(str(col)))
            ids = measure_build.commit_take(
                self.parent.store, pid, self.session, ch,
                self.ch_keys[ch], rec.id, cal=cal,
                source=self._source_info(),
                canvas_session=self._canvas_session)
            self._canvas_session = ids["session"]
            self._canvas_ids[(ch, rec.id)] = ids["take"]
        except Exception as e:
            self._error("Could not store the take: %s" % e)

    def _adopt_canvas(self):
        """Seed the fresh session with the profile's stored takes so
        the counts, the spread statistics and the take list span the
        whole history instead of one sitting. Adoption is
        unconditional -- a foreign rig blocks ADDING, not viewing
        -- and adopted takes come back as records without samples
        (the canvas magnitudes on the canvas grid); their trash
        cans delete from the profile."""
        if not self.edit_pid or self.session is None:
            return
        prof = self.parent.store.get(self.edit_pid) or {}
        m = prof.get("measurement") or {}
        takes = m.get("takes") or []
        if not takes:
            return
        # The rig's identity gates nothing (field doctrine): a
        # mixed canvas is judged by its own statistics -- the
        # per-take-calibrated spread feeds spread_trust_bound,
        # trust sinks, the trusted band shrinks. The per-take
        # passports (schema v5) will mark foreign takes in the
        # take rows; the whole-canvas subtitle enumeration died
        # with measurement.source.
        g = m.get("grid") or {}
        freqs = mc.log_grid(float(g.get("f_lo", mc.GRID_F_LO)),
                            float(g.get("f_hi", mc.GRID_F_HI)),
                            int(g.get("ppo", mc.GRID_PPO)))
        key_to_ch = {k: i for i, k in
                     enumerate(self.ch_keys[:self.n_ch])}

        def conf(a):
            # stored abstentions are None (JSON has no NaN);
            # the pen wants NaN back
            if a is None:
                return None
            return [float("nan") if v is None else float(v)
                    for v in a]

        for t in takes:
            ch = key_to_ch.get(t.get("channel"))
            if ch is None:
                continue
            rec = ms.TakeRecord(
                t.get("id"), ch, freqs,
                t.get("mag_db_uncal") or [],
                t.get("delay_ms"), t.get("snr_db"),
                t.get("peak_dbfs"), int(t.get("clipped") or 0),
                int(t.get("repaired") or 0), None,
                chan_vol=t.get("chan_vol"),
                soft_vol=t.get("soft_vol"),
                noise_dbfs=t.get("noise_dbfs"),
                capture_channel=t.get("capture_channel"),
                created_utc=t.get("created_utc"),
                h2_db=conf(t.get("h2_db")),
                h3_db=conf(t.get("h3_db")),
                thd_db=conf(t.get("thd_db")),
                thd_noise_db=conf(t.get("thd_noise_db")))
            self.session.adopt_take(ch, rec)
            self._canvas_ids[(ch, rec.id)] = rec.id
        self._refresh_all()

    def _should_autofit(self, pid):
        """Full house and an unsettled fit: three clean takes on
        every channel (the session adopted the history, so the
        counts span it) and a fit that is absent, stale or does not
        cover the canvas. Hand-edited fits are never discarded
        silently -- the editor's Re-fit asks."""
        if self.session is None:
            return False
        if any(self._clean_count(i) < CLEAN_TARGET
               for i in range(self.n_ch)):
            return False
        prof = self.parent.store.get(pid) or {}
        m = prof.get("measurement") or {}
        if not m.get("takes"):
            return False
        fit = prof.get("fit")
        if not fit:
            return True
        if fit.get("edited"):
            return False
        want = int(self.bands_spin.get_value_as_int())
        have = (fit.get("params") or {}).get("bands")
        if have is not None and int(have) != want:
            return True    # the dial moved past the fitted budget
        from . import refit
        ids = {t.get("id") for t in m["takes"]}
        return (refit.fit_is_stale(prof)
                or bool(ids - set(fit.get("takes") or [])))

    def _parent_reload(self, pid):
        """Freshen the parent after a session WITHOUT changing what
        the device plays.

        Editing a profile is not choosing it. This used to load the
        edited profile into the parent whenever it belonged to the
        parent's current device, so opening any inactive profile
        just to look at it switched the machine over on close: the
        field put the original back more than twenty times in one
        day. The fit runner already had the right rule and this
        borrows it -- reload only what is ALREADY playing, otherwise
        refresh the list and leave the sound alone.

        A newly created profile still lands active, and by the
        honest route rather than a special case: creating it bound
        it to the sink, and the parent reloads that binding when the
        window closes."""
        try:
            if (self.parent.node == self.sink_node
                    and self.parent.current_pid == pid):
                self.parent._select_device(self.sink_node,
                                           load=False)
                self.parent._load_profile(pid)
            else:
                self.parent._populate_picker()
        except Exception:
            pass
        return False

    def _persist_mic(self, by_hand=False):
        """Save the chosen mic + its per-capture-channel cal (bound to the
        source node) and remember the mic for this sink as soon as either
        changes -- not only at create."""
        src = self._selected_source()
        if not src:
            debug.mic_trace("persist skip core=%r"
                         % self.mic_picker.core.node)
            return
        existing = self.mic_store.match(src["name"])
        # an empty set means "still loading" from a handler and
        # "take it off" from a hand -- measure_prefs decides which,
        # and only a hand may empty the block
        cal = measure_prefs.cal_to_store(
            self.cal, (existing or {}).get("cal"), by_hand)
        # a measured sensitivity is a statement about this rig, worth
        # writing down for the same reason a calibration is -- and a
        # rig with no calibration is every rig until one is chosen
        knees = self._knees_for_store(src, existing)
        if not measure_prefs.worth_saving(cal, existing, by_hand,
                                          knees=knees,
                                          columns=self.mic_cols):
            return
        chans = {str(c): {} for c in self.mic_cols}
        for k, path in cal.items():
            chans.setdefault(str(k), {})["cal"] = path
        for k, rec in knees.items():
            chans.setdefault(str(k), {})["knee"] = rec
        body = {"name": src["desc"], "node_match": src["name"],
                "serial": ((existing or {}).get("serial", "")
                           or measure_prefs.serial_from_cal(cal.values())),
                "columns": chans}
        if existing:
            body["id"] = existing["id"]
        pid = self.mic_store.save(body)
        self.memory.remember(self.sink_node, mic_profile=pid)

    def _source_info(self):
        """What the session cannot know about the rig: its display
        name and serial (from the saved mic profile matching the
        selected source, when one exists). Feeds measure_build's
        source block; called on the main thread before the worker."""
        name = self.mic_picker.core.node
        if not name:
            return None
        existing = self.mic_store.match(name)
        return {"name": self.mic_picker.core.desc,
                "serial": ((existing or {}).get("serial", "")
                           or measure_prefs.serial_from_cal(
                               self.cal.values()))}

    def _profile_name(self):
        return (self.name_row.get_text().strip() or self.sink_desc)

    # ---- scroll taming (wheel must not change spin/dropdown values) ----
    def _tame_scroll(self, widget):
        """Keep the wheel from editing a value; scroll the page."""
        ctrl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL)
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctrl.connect("scroll", self._on_widget_scroll)
        widget.add_controller(ctrl)

    def _on_widget_scroll(self, ctrl, dx, dy):
        """CAPTURE-phase handler backing _tame_scroll: forward the
        wheel to the enclosing scrolled page and swallow it here, so
        the hovered value is left untouched."""
        w = ctrl.get_widget()
        sw = w.get_ancestor(Gtk.ScrolledWindow) if w else None
        if sw is not None:
            adj = sw.get_vadjustment()
            if adj is not None:
                step = adj.get_step_increment()
                if step <= 0:
                    step = 30.0
                new = adj.get_value() + dy * step
                new = max(adj.get_lower(),
                          min(new,
                              adj.get_upper() - adj.get_page_size()))
                adj.set_value(new)
        return True

    # ---- dialogs / teardown -----------------------------------------------
    def _confirm_loud(self, on_ok):
        dlg = Adw.AlertDialog(
            heading="This will play loudly",
            body="A measurement sweep will now play on this device at the "
                 "measurement level. Take your headphones off your head if "
                 "they are not on the rig.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("go", "Play sweep")
        dlg.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("go")
        dlg.set_close_response("cancel")

        def on_resp(_d, resp):
            if resp == "go":
                self._loud_ack = True
                on_ok()
        dlg.connect("response", on_resp)
        dlg.present(self)

    def _error(self, text):
        dlg = Adw.AlertDialog(heading="Measurement", body=text)
        dlg.add_response("close", "Close")
        dlg.set_default_response("close")
        dlg.present(self)

    def _on_parent_close(self, *_):
        if self._busy and self.session is not None:
            try:
                self.session.cancel()
            except Exception:
                pass
            self._busy = False
        self.close()
        return False                     # the parent proceeds

    def _park_bands_pref(self, pid, bands):
        """No fit runs at this close, but the dial's word must
        outlive the window (the field: 12 -> 15, close, reopen,
        12 again). Parked as `fit_prefs`; the next fit -- the
        editor's Re-fit included -- resolves and consumes it
        (see refit.resolve_fit_params)."""
        prof = self.parent.store.get(pid)
        if not prof or prof.get("builtin"):
            return
        prefs = prof.get("fit_prefs") or {}
        params = (prof.get("fit") or {}).get("params") or {}
        have = prefs.get("bands", params.get("bands", FIT_BANDS))
        if int(have) == int(bands):
            return
        body = dict(prof)
        body["fit_prefs"] = {**prefs, "bands": int(bands)}
        try:
            self.parent.store.save_user(body)
        except Exception:
            pass

    def _on_close(self, *_):
        if self._busy:
            return True                  # a sweep is in the air
        for win, _a, _h in list(self._big.values()):
            win.close()                  # no orphaned pictures
        pid = self._ensure_pid()         # New: even empty stays
        self._apply_name(pid)
        self._settle_home(pid)           # where it ENDED up, if minted
        try:
            self._persist_mic()
        except Exception:
            pass
        fit = self._should_autofit(pid)
        bands = self.bands_spin.get_value_as_int()
        if not fit:
            self._park_bands_pref(pid, bands)
        f_lo, f_hi = float(self.fit_lo), float(self.fit_hi)
        if getattr(self, "_parent_close_id", None) is not None:
            try:
                self.parent.disconnect(self._parent_close_id)
            except Exception:
                pass
            self._parent_close_id = None
        self.parent.set_sensitive(True)
        self._teardown()
        GLib.idle_add(self._parent_reload, pid)
        if fit:                # the parent shows the progress OSD
            GLib.idle_add(self.parent._start_profile_fit, pid,
                          bands, f_lo, f_hi)
        return False

    def _teardown(self):
        if getattr(self, "_pw_unsub", None) is not None:
            self._pw_unsub()
            self._pw_unsub = None
        if self.session is not None and self._entered:
            try:
                self.session.__exit__(None, None, None)
            except Exception:
                pass
            self._entered = False
