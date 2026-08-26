"""PickerCore: the shared sink-picker doctrine, GTK-free.

The selection never dangles: the current node is always among
the rows even when the graph lost it (no suffix: the window
names the gone state); placement
restores, only a pick moves; picks resolve against the rows the
widget was built from, not against a fresher graph."""
from perdeviceeq.picker import PickerCore


def _s(name, desc=None):
    return {"name": name, "desc": desc or name}


def _core(sinks, node=None, desc=None):
    c = PickerCore()
    c.set_sinks(sinks)
    if node is not None:
        c.set_node(node, desc)
    return c


def test_rows_mirror_the_graph():
    c = _core([_s("a", "A"), _s("b", "B")], "b")
    assert c.rows() == [("a", "A"), ("b", "B")]
    assert c.rows()[1][0] == "b"
    assert c.alive()


def test_gone_row_tops_the_list_and_keeps_the_desc():
    c = _core([_s("a", "A"), _s("b", "B")], "b")
    c.set_sinks([_s("a", "A")])          # b left the graph
    assert c.rows()[0] == ("b", "B")
    assert c.rows()[0][0] == "b"
    assert not c.alive()
    assert c.alive("a")


def test_desc_follows_the_graph_while_alive():
    c = _core([_s("a", "old name")], "a")
    c.set_sinks([_s("a", "new name")])   # the sink was renamed
    assert c.desc == "new name"
    c.set_sinks([])                      # gone: last desc kept
    assert c.rows() == [("a", "new name")]


def test_pick_by_name_moves_strips_and_rejects():
    """A pick names the node, so nothing has to be resolved against a
    snapshot that may already have moved."""
    c = _core([_s("a", "A"), _s("b", "B")], "gone", "Gone")
    assert c.pick("gone") is None        # the gone row IS the choice
    assert c.pick("nobody") is None      # not in the snapshot
    assert c.pick("a") == ("a", "A")


def test_select_resolves_desc_with_fallback():
    c = _core([_s("a", "A")], "a")
    assert c.desc == "A"
    c.set_node("ghost")                  # not in the graph
    assert c.desc == "A"                 # last known desc survives
    c.set_node("a", "Renamed")
    assert c.desc == "Renamed"


# ---- the GTK shell, executed against a stub gi ----------------
# The shell drives a GtkMenuButton and a stateful action. The fakes
# below are the two of them, no more: the menu is data, the action
# carries the mark, and a rebuild must not land while the menu is
# open.

import sys
import types


class FakeMenu:
    def __init__(self):
        self.items = []          # (label, target) or (label, FakeMenu)

    def append_item(self, item):
        self.items.append((item.label, item.target))

    def append_submenu(self, label, sub):
        self.items.append((label, sub))


class FakeMenuItem:
    def __init__(self, label, _detail):
        self.label, self.target = label, None

    @staticmethod
    def new(label, detail):
        return FakeMenuItem(label, detail)

    def set_action_and_target_value(self, name, target):
        self.action, self.target = name, target.s


class FakeVariant:
    def __init__(self, _fmt, s):
        self.s = s

    def get_string(self):
        return self.s

    def __eq__(self, other):
        return isinstance(other, FakeVariant) and other.s == self.s


class FakeAction:
    def __init__(self, name, _vt, state):
        self.name, self.state, self._cb = name, state, None

    @staticmethod
    def new_stateful(name, vt, state):
        return FakeAction(name, vt, state)

    def connect(self, _sig, cb):
        self._cb = cb

    def get_state(self):
        return self.state

    def set_state(self, v):
        self.state = v

    def activate(self, name):
        self._cb(self, FakeVariant("s", name))


class FakeGroup:
    def __init__(self):
        self.actions = []

    def add_action(self, a):
        self.actions.append(a)


class FakeButton:
    def __init__(self):
        self.menu = None
        self.label = None
        self.active = False
        self.builds = 0
        self._toggled = []

    def insert_action_group(self, _prefix, group):
        self.group = group

    def connect(self, _sig, cb):
        self._toggled.append(cb)

    def set_menu_model(self, m):
        self.menu = m
        self.builds += 1

    def set_label(self, s):
        self.label = s

    def get_active(self):
        return self.active

    def open(self):
        self.active = True

    def close(self):
        self.active = False
        for cb in self._toggled:
            cb(self, None)

    def labels(self):
        return [lab for lab, _ in (self.menu.items if self.menu else [])]


def _shell(monkeypatch, veto=False, ellipsis=None, placeholder=None):
    gio = types.SimpleNamespace(SimpleActionGroup=FakeGroup,
                                SimpleAction=FakeAction,
                                Menu=FakeMenu, MenuItem=FakeMenuItem)
    glib = types.SimpleNamespace(
        Variant=FakeVariant,
        VariantType=types.SimpleNamespace(new=lambda s: s))
    repo = types.ModuleType("gi.repository")
    repo.Gio, repo.GLib = gio, glib
    gi = types.ModuleType("gi")
    gi.repository = repo
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    from perdeviceeq.picker import NodeMenu
    btn = FakeButton()
    picks = []

    def cb(node, desc):
        picks.append((node, desc))
        if veto:
            return False

    return (NodeMenu(btn, cb, ellipsis=ellipsis,
                     placeholder=placeholder), btn, picks)


def test_shell_builds_the_two_levels(monkeypatch):
    p, btn, _ = _shell(monkeypatch)
    p.refresh(UMIK + M62)
    assert btn.labels() == ["Umik-1 Gain 24dB", "M62"]
    sub = btn.menu.items[1][1]
    assert [lab for lab, _ in sub.items] == ["IN 1", "IN 2", "AUX"]


def test_shell_marks_the_chosen_node(monkeypatch):
    """The mark is the ACTION's state, which is what makes a menu item
    draw one at all."""
    p, btn, _ = _shell(monkeypatch)
    p.refresh(UMIK + M62)
    p.select("aux")
    assert p._act.get_state().get_string() == "aux"


def test_shell_group_names_its_chosen_child(monkeypatch):
    """A submenu cannot carry a mark, so the group says where it is."""
    p, btn, _ = _shell(monkeypatch)
    p.refresh(UMIK + M62)
    p.select("aux")
    assert btn.labels()[1] == "M62 \u00b7 AUX"


def test_shell_pick_arrives_by_name(monkeypatch):
    p, btn, picks = _shell(monkeypatch)
    p.refresh(UMIK + M62)
    p._act.activate("in2")
    assert picks == [("in2", "IN 2")] or picks == [("in2", "M62 IN 2")]
    assert p.core.node == "in2"


def test_shell_core_leads_the_callback(monkeypatch):
    seen = []
    p, btn, _ = _shell(monkeypatch)
    p.on_pick = lambda n, d: seen.append(p.core.node)
    p.refresh(UMIK + M62)
    p._act.activate("in1")
    assert seen == ["in1"]          # never the pick before it


def test_shell_veto_rolls_the_core_back(monkeypatch):
    p, btn, picks = _shell(monkeypatch, veto=True)
    p.refresh(UMIK + M62)
    p.select("umik")
    p._act.activate("aux")
    assert picks == [("aux", "AUX")] or picks == [("aux", "M62 AUX")]
    assert p.core.node == "umik"


def test_shell_holds_a_rebuild_until_the_menu_closes(monkeypatch):
    """The heartbeat refreshes several times a second; a model swapped
    under an open popover moves rows out from under the pointer."""
    p, btn, _ = _shell(monkeypatch)
    p.refresh(UMIK)
    before = btn.builds
    btn.open()
    p.refresh(UMIK + M62)
    assert btn.builds == before      # held
    btn.close()
    assert btn.builds == before + 1  # and it lands


def test_shell_label_shows_the_placeholder_then_the_choice(monkeypatch):
    p, btn, _ = _shell(monkeypatch, placeholder="Choose a mic")
    p.refresh(UMIK)
    assert btn.label == "Choose a mic"
    p.select("umik")
    assert btn.label == "Umik-1 Gain 24dB"


def test_shell_clips_a_monster_description(monkeypatch):
    p, btn, _ = _shell(monkeypatch, ellipsis=10)
    p.refresh(UMIK)
    p.select("umik")
    assert len(btn.label) == 10 and btn.label.endswith("\u2026")


# --- two levels: a card with several nodes, and one without --------

M62 = [{"name": "in1", "desc": "M62 IN 1", "card": 7,
        "card_desc": "M62"},
       {"name": "in2", "desc": "M62 IN 2", "card": 7,
        "card_desc": "M62"},
       {"name": "aux", "desc": "M62 AUX", "card": 7, "card_desc": "M62"}]
UMIK = [{"name": "umik", "desc": "Umik-1 Gain 24dB", "card": 3,
         "card_desc": "Umik-1"}]


def test_a_lone_card_stays_one_click():
    """The rule that keeps the second level from being an irritation:
    a card with one node is a LEAF, not a submenu of one."""
    assert _core(UMIK).groups() == [("Umik-1 Gain 24dB", "umik", None)]


def test_a_wide_card_becomes_one_group_in_place():
    out = _core(UMIK + M62).groups()
    assert out[0] == ("Umik-1 Gain 24dB", "umik", None)
    head, node, kids = out[1]
    assert (head, node) == ("M62", None)
    assert [n for n, _ in kids] == ["in1", "in2", "aux"]
    assert len(out) == 2          # the card appears ONCE, in place


def test_the_child_label_drops_the_card_it_is_already_under():
    _, _, kids = _core(M62).groups()[0]
    assert [d for _, d in kids] == ["IN 1", "IN 2", "AUX"]


def test_a_label_that_does_not_start_with_the_card_is_left_alone():
    rows = [dict(r) for r in M62]
    rows[2]["desc"] = "Line input"
    _, _, kids = _core(rows).groups()[0]
    assert [d for _, d in kids] == ["IN 1", "IN 2", "Line input"]


def test_a_node_the_graph_lost_is_a_leaf_at_the_top():
    """It has no card in the snapshot, so it cannot be grouped -- and
    it must stay visible, because it is the current choice."""
    out = _core(M62, "gone", "Gone mic").groups()
    assert out[0] == ("Gone mic", "gone", None)
    assert out[1][0] == "M62"


def test_rows_without_a_card_are_ungrouped():
    out = _core([_s("a"), _s("b")]).groups()
    assert [n for _, n, _ in out] == ["a", "b"]
