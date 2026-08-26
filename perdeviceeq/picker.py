# -*- coding: utf-8 -*-
"""The node picker: one doctrine for every graph-node chooser
-- the sink pickers of both windows and the Measure window's
mic picker.

Both headers carry the same widget with the same doctrine: the
picker mirrors the graph, but the current node is always
listed, even when the graph lost it, so the selection never
dangles. The row itself stays clean: the WINDOW names the gone
state (the banner under the main header, the ring note in
Measure); naming it twice was field-vetoed. Rebuilds RESTORE
the selection; only a user pick
or an explicit select() moves it. Letting GtkDropDown default to
row 0 after a rebuild is what once painted a foreign sink over a
pinned panel when the pinned sink died.

The windows differ around the picker, not inside it: the main
window wraps it in Follow-the-default (on by default) and vetoes
picks while following; the Measure window never follows and
vetoes picks while a sweep runs. Both feed it from the one
PWState heartbeat.

Split in the pipewire.py tradition: PickerCore is plain data and
plain rules, importable and testable with no GTK at all;
NodeMenu is the thin GTK shell around a GtkMenuButton. It shows
the core's TWO levels -- a card with several nodes becomes a
submenu, a card with one stays a leaf -- and a pick arrives by
NAME through a stateful action, which is what draws the mark on
the chosen row and what removed the whole index-versus-snapshot
apparatus the old dropdown shell needed.
"""

import sys

from . import debug


INVALID_POSITION = 0xFFFFFFFF   # Gtk.INVALID_LIST_POSITION


class PickerCore:
    """Rows, placement and pick semantics, GTK-free."""

    def __init__(self, placeholder=None):
        self.sinks = []          # [{"name":..., "desc":...}, ...]
        self.node = None
        self.desc = ""
        # What to show while nothing is chosen. An AdwComboRow cannot
        # show emptiness -- while its model has rows, one of them is
        # displayed -- so "nothing" has to BE a row rather than the
        # absence of one. Only a picker that can legitimately hold no
        # choice wants this: a sink is always chosen, a measurement
        # mic is not.
        self.placeholder = placeholder

    def set_sinks(self, sinks):
        """Adopt a fresh graph snapshot; while the node is alive
        its desc follows the graph (renames), while it is gone
        the last known desc keeps its row readable."""
        self.sinks = list(sinks)
        self.desc = next((s["desc"] for s in self.sinks
                          if s["name"] == self.node), self.desc)

    def set_node(self, name, desc=None):
        """Move the current node; desc resolves from the graph
        when not given, falling back to the previous desc."""
        debug.mic_trace("set_node %r -> %r via %s"
               % (self.node, name,
                  sys._getframe(1).f_code.co_name))
        self.node = name
        if desc is not None:
            self.desc = desc
        else:
            self.desc = next((s["desc"] for s in self.sinks
                              if s["name"] == name), self.desc)

    def alive(self, name=None):
        name = self.node if name is None else name
        return any(s["name"] == name for s in self.sinks)

    def rows(self):
        """The visible rows: the graph, plus the current node
        when the graph lost it (kept at the top, like the
        Measure picker minted it; no suffix -- the window
        already names the gone state)."""
        rows = [(s["name"], s["desc"]) for s in self.sinks]
        if self.node and all(n != self.node for n, _ in rows):
            rows.insert(0, (self.node, self.desc))
        if self.node is None and self.placeholder:
            rows.insert(0, (None, self.placeholder))
        return rows

    def groups(self):
        """The same rows arranged for a TWO-LEVEL chooser.

        Returns a list of entries in the order rows() gives, each
        either a leaf `(label, node, None)` or a group
        `(label, None, [(node, label), ...])`.

        THE RULE THAT KEEPS IT FROM BEING AN IRRITATION: a card with
        ONE node stays a LEAF at the top level. A UMIK-1 is still one
        click; only a card like the M62, whose UCM profile hands out
        six inputs and five outputs, grows a second level. Group where
        grouping pays and nowhere else.

        NAMES ARE PASSED THROUGH, NEVER EDITED. A child shows what
        PipeWire calls it; if that repeats the card's own name, that
        is the graph's redundancy to answer for and not ours. The one
        exception is not an exception: a DOOR is a row this app
        composes itself out of a port and a card ("Analogue Output -
        M62"), and under the card's own submenu it shows the port
        half, which it carries as a field. Nothing is taken apart by
        looking for a separator -- a port really called
        "Line - Rear panel" would not survive that, and a name is
        evidence, not decoration.
        """
        rows = self.rows()
        by_card = {}
        for s in self.sinks:
            if s.get("card") is not None:
                by_card.setdefault(s["card"], []).append(s["name"])
        card_of = {s["name"]: s.get("card") for s in self.sinks}
        label_of = {s["name"]: s.get("card_desc") for s in self.sinks}
        port_of = {s["name"]: s.get("port") for s in self.sinks}
        out, seen = [], set()
        for node, desc in rows:
            card = card_of.get(node)
            # a gone node, the placeholder, or a lone card: a leaf
            if card is None or len(by_card.get(card, ())) < 2:
                out.append((desc, node, None))
                continue
            if card in seen:
                continue
            seen.add(card)
            head = label_of.get(node) or desc
            kids = [(n, port_of.get(n) or d)
                    for n, d in rows if card_of.get(n) == card]
            out.append((head, None, kids))
        return out

    def pick(self, name):
        """Resolve a user pick BY NAME. Returns (node, desc) for a
        real move, None for a name the snapshot does not carry or
        for re-picking the current node -- a gone row IS the current
        choice, so picking it is a no-op.

        By name rather than by index, and that is the whole of it:
        the old chooser handed back a row NUMBER, which meant every
        pick had to be resolved against the snapshot the widget was
        built from rather than against the graph, because the two
        drift between a pick and its reconciliation. A name does not
        drift."""
        row = next(((n, d) for n, d in self.rows() if n == name), None)
        if row is None or row[0] == self.node:
            return None
        return row


class NodeMenu:
    """The GTK shell: a GtkMenuButton whose model is the core's two
    levels, one per picker.

    refresh() and select() are the windows' two doors, exactly as
    before, and on_pick(node, desc) returning False is still a veto.
    Three things are different, and each removes a hazard rather than
    adding a feature.

    A PICK ARRIVES BY NAME. The item carries the node name as its
    action target, so nothing has to be resolved against a stale row
    snapshot and there is no index to go out of date. The whole
    machinery the dropdown needed for that is gone with it, including
    the rule against touching the model inside the widget's own
    selection signal, which cost three field segfaults.

    THE CHECK COMES FROM THE ACTION. The pick action is STATEFUL and
    the items carry targets, which is what makes a menu item draw a
    radio mark -- so the chosen row is marked exactly as a combo row
    marked it. A submenu cannot carry a mark and the group does NOT
    spell the chosen child into its own label: that read as the
    card's name followed by a name beginning with the card's name.
    The row itself already shows the full name of what is chosen,
    before the menu is opened at all.

    THE MODEL IS NOT REBUILT WHILE THE MENU IS OPEN. The heartbeat
    calls refresh() several times a second, and a model swapped under
    an open popover moves rows under the pointer. A rebuild that
    lands while it is open is held until it closes.
    """

    def __init__(self, button, on_pick, ellipsis=None,
                 placeholder=None, action="pick"):
        # gi arrives here, not at module scope: the core above stays
        # importable in the GTK-less test sandbox (the pipewire.py
        # rule), and by construction time the app has long loaded gi
        # with its versions required.
        from gi.repository import Gio, GLib
        self._Gio = Gio
        self._GLib = GLib
        self.core = PickerCore(placeholder)
        self.btn = button
        self.on_pick = on_pick
        self._ellipsis = ellipsis
        self._shown = None       # the structure the model was built from
        self._dirty = False      # a rebuild deferred past an open menu
        group = Gio.SimpleActionGroup()
        self._act = Gio.SimpleAction.new_stateful(
            action, GLib.VariantType.new("s"), GLib.Variant("s", ""))
        self._act.connect("activate", self._on_activate)
        group.add_action(self._act)
        button.insert_action_group("picker", group)
        self._name = "picker." + action
        button.connect("notify::active", self._on_toggled)
        self._sync()

    # ---- the two doors ------------------------------------------

    def refresh(self, sinks):
        """Adopt a fresh graph snapshot and mirror it."""
        self.core.set_sinks(sinks)
        self._sync()

    def select(self, name, desc=None):
        """Move the selection from code -- the one legal mover
        besides a user pick."""
        self.core.set_node(name, desc)
        self._sync()

    # ---- the mirror ----------------------------------------------

    def _clip(self, d):
        e = self._ellipsis
        if e and d and len(d) > e:
            return d[:e - 1] + "\u2026"
        return d

    def _shape(self):
        """The menu as plain data, so a rebuild happens only when
        something a reader would notice actually changed. The chosen
        node is part of the shape, because a group's label names its
        chosen child."""
        out = []
        for label, node, kids in self.core.groups():
            if kids is None:
                out.append((label, node, None))
                continue
            out.append((label, None, tuple(kids)))
        return out

    def _sync(self):
        shape = self._shape()
        if shape != self._shown:
            self._shown = shape
            if self.btn.get_active():
                self._dirty = True       # the menu is open; wait
            else:
                self._build(shape)
        # the mark follows the core even when the shape did not move
        node = self.core.node or ""
        if self._act.get_state().get_string() != node:
            self._act.set_state(self._GLib.Variant("s", node))
        self.btn.set_label(self._clip(self.core.desc or
                                      self.core.placeholder or ""))

    def _build(self, shape):
        Gio, GLib = self._Gio, self._GLib
        menu = Gio.Menu()
        for label, node, kids in shape:
            if kids is None:
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value(
                    self._name, GLib.Variant("s", node or ""))
                menu.append_item(item)
                continue
            sub = Gio.Menu()
            for n, d in kids:
                it = Gio.MenuItem.new(d, None)
                it.set_action_and_target_value(
                    self._name, GLib.Variant("s", n))
                sub.append_item(it)
            menu.append_submenu(label, sub)
        self.btn.set_menu_model(menu)
        self._dirty = False

    def _on_toggled(self, *_):
        if not self.btn.get_active() and self._dirty:
            self._build(self._shown)

    # ---- a pick ---------------------------------------------------

    def _on_activate(self, _action, target):
        name = target.get_string() or None
        hit = self.core.pick(name)
        debug.mic_trace("menu pick %r hit=%r" % (name, hit))
        if hit is None:
            return
        node, desc = hit
        # The core LEADS the callback: everything the window does
        # inside on_pick (resolving the selection, rebuilding a
        # session, persisting) must see the PICKED node, not the
        # previous one -- the field ran every mic pick against the
        # pick before it. A veto rolls the core back.
        prev = (self.core.node, self.core.desc)
        self.core.set_node(node, desc)
        vetoed = self.on_pick(node, desc) is False
        if vetoed:
            self.core.set_node(*prev)
        self._sync()
