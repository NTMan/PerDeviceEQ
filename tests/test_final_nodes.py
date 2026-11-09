"""Only a node the sound leaves PipeWire at is a device: a card behind it
(device.id) or a network (node.network). A filter-chain, a loopback or a
combine-stream passes the sound on to one of those, a null sink keeps it,
and a correction bound to such a node played on top of the one at the
device it fed -- the taste with it, twice. The window offered every sink,
so such a node could be opened and bound, and the hook applied its own
value to it. Now the window offers only final nodes, and the hook holds
no other (tests/hook_harness.lua).

The default is still shown whatever it is. Opening on a device in its
place was a guess, and with the pin pressed the header named an output
the sound did not go to. When the node in view is no device, the window
greys out what would correct it and publishes and binds nothing under
its key.
"""
import ast
import pathlib
import types

from perdeviceeq import pw_backend as pwb
from perdeviceeq.picker import PickerCore

CARD = "alsa_output.usb-card.analog-stereo"
AIRPLAY = "raop_sink.living-room"
CHAIN = "effect_input.eq"
NULL = "null-sink"


def _sink(nid, name, prio, **props):
    p = {"node.name": name, "media.class": "Audio/Sink",
         "node.description": name, "priority.session": prio}
    p.update(props)
    return {"id": nid, "type": "PipeWire:Interface:Node",
            "info": {"props": p}}


def _dump():
    """As pw-dump prints them: device.id a number, the flags booleans.
    The two that are not devices rank above the two that are."""
    return [
        _sink(401, CARD, 1000, **{"device.id": 346}),
        _sink(402, AIRPLAY, 500, **{"node.network": True,
                                    "node.virtual": True}),
        _sink(403, CHAIN, 2000, **{"node.link-group": "filter-chain-1-2",
                                   "node.virtual": True}),
        _sink(404, NULL, 3000,
              **{"factory.name": "support.null-audio-sink"}),
    ]


def _listing(default=""):
    return {s["name"]: s for s in pwb.list_sinks(_dump(), default=default)}


def test_the_sound_leaves_pipewire_at_a_card_or_a_network():
    got = {name: s["final"] for name, s in _listing().items()}
    assert got == {CARD: True, AIRPLAY: True, CHAIN: False, NULL: False}


def test_the_question_is_the_hook_s_too():
    """The hook reads properties as text, pw-dump prints the flag as JSON;
    every spelling PipeWire takes for true answers alike, and
    node.virtual alone does not make a device or unmake one."""
    for yes in (True, 1, "true", "1"):
        assert pwb.is_final_node({"node.network": yes})
    for no in (False, 0, "false", "0", None):
        assert not pwb.is_final_node({"node.network": no})
    assert not pwb.is_final_node({"node.virtual": True})
    assert pwb.is_final_node({"device.id": 346, "node.virtual": True})


def test_only_a_final_node_is_offered():
    core = PickerCore()
    core.set_sinks(pwb.list_playback_entries_from(
        pwb.list_sinks(_dump(), default="")))
    assert [name for name, _ in core.rows()] == [CARD, AIRPLAY]


def test_a_node_that_is_not_offered_can_still_be_looked_up():
    """The listing keeps it: everything else still finds a node by name,
    the picker only does not offer it."""
    assert CHAIN in _listing() and NULL in _listing()


def test_the_window_opens_on_the_default_whatever_it_is():
    for default in (CARD, AIRPLAY, CHAIN, NULL):
        sinks = pwb.list_sinks(_dump(), default=default)
        assert pwb.node_to_open(sinks) == default


def test_without_a_default_the_window_opens_on_nothing():
    """No guess in its place: the first sink by priority was a null sink
    here, and the first device an output the sound does not go to."""
    assert pwb.node_to_open(pwb.list_sinks(_dump(), default="")) is None
    assert pwb.node_to_open([]) is None


# ---- the window --------------------------------------------------------------

def _gui():
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    return {n.name: n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef)}


def _method(name, **names):
    """A window method run without GTK: its own source, with the names
    it reads handed in."""
    module = ast.Module(body=[_gui()[name]], type_ignores=[])
    exec(compile(module, "gui.py", "exec"), names)
    return names[name]


def test_the_window_opens_where_the_listing_says():
    calls = {ast.unparse(c.func) for c in ast.walk(_gui()["_init_devices"])
             if isinstance(c, ast.Call)}
    assert "pw_backend.node_to_open" in calls


def test_any_default_is_followed():
    follow = _method("_maybe_follow")
    picked = []
    win = types.SimpleNamespace(
        follow_btn=types.SimpleNamespace(get_active=lambda: True),
        node=CARD, _select_device=lambda name, load: picked.append(name))
    for default in (CHAIN, NULL, AIRPLAY):
        follow(win, default)
    assert picked == [CHAIN, NULL, AIRPLAY]


class _Switch:
    """A widget as far as the window's state goes: on, off, or untouched."""

    def __init__(self):
        self.on = None

    def set_sensitive(self, on):
        self.on = on

    def set_revealed(self, on):
        self.on = on


def _window(node, sinks):
    win = types.SimpleNamespace(
        node=node, sinks=sinks, _seen_final={},
        device_card=_Switch(), bypass_row=_Switch(),
        not_device_banner=_Switch())
    win._is_device = types.MethodType(_method("_is_device"), win)
    return win


def _state(win):
    return (win.device_card.on, win.bypass_row.on,
            win.not_device_banner.on)


def test_no_device_in_view_greys_out_its_correction():
    sync = _method("_sync_device_state")
    win = _window(CHAIN, pwb.list_sinks(_dump(), default=CHAIN))
    sync(win)
    assert _state(win) == (False, False, True)
    win.node = CARD
    sync(win)
    assert _state(win) == (True, True, False)


def test_a_gone_node_keeps_its_answer_and_a_new_one_waits():
    """The panel survives a gone node, and so does what the listing said
    of it. A node never listed is not corrected until it shows, and no
    banner calls it anything yet."""
    sync = _method("_sync_device_state")
    win = _window(CARD, pwb.list_sinks(_dump(), default=CARD))
    sync(win)
    win.sinks = []
    sync(win)
    assert _state(win) == (True, True, False)
    win.sinks = pwb.list_sinks(_dump(), default=NULL)
    win.node = NULL
    sync(win)
    win.sinks = []
    sync(win)
    assert _state(win) == (False, False, True)
    win.node = "alsa_output.not-listed-yet"
    sync(win)
    assert _state(win) == (False, False, False)


class _Auth:
    def __init__(self):
        self.calls = []

    def publish_graph(self, key, value):
        self.calls.append(("publish", key, value))

    def clear_graph(self, key):
        self.calls.append(("clear", key))


def test_no_device_gets_nothing_under_its_key():
    """A taste or preamp edit made while it is in view still reaches
    every device through the common entry. Its own key is not written,
    not even by a Bypass left pressed."""
    auth = _Auth()
    apply_now = _method(
        "_apply_now",
        pw_backend=types.SimpleNamespace(backend=lambda: auth),
        common_graph=lambda taste, manual: "common(%s)" % taste,
        COMMON_KEY="@taste", CLEAN_ID="clean", STRIP="", eq=None)
    win = types.SimpleNamespace(
        live=True, node=CHAIN, _update_meter=lambda: None,
        _dev=lambda: CHAIN, _is_device=lambda: False,
        pref_layers=types.SimpleNamespace(active_bands=lambda: "bass"),
        preamp_auto=True, preamp=0.0, current_pid="mine",
        bypass_row=types.SimpleNamespace(get_active=lambda: True))
    apply_now(win)
    assert auth.calls == [("publish", "@taste", "common(bass)")]


def test_the_window_binds_through_one_method_that_refuses_no_device():
    """An undo walking back to another profile binds too; it goes
    through the same method."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    writes = {fn.name for fn in ast.walk(ast.parse(src))
              if isinstance(fn, ast.FunctionDef)
              for c in ast.walk(fn) if isinstance(c, ast.Call)
              and ast.unparse(c.func).endswith("store.set_binding")}
    assert writes == {"_bind"}
    bind = _method("_bind")
    bound = []
    win = types.SimpleNamespace(
        store=types.SimpleNamespace(
            set_binding=lambda key, pid: bound.append((key, pid))),
        _dev=lambda: CHAIN, _is_device=lambda: False)
    bind(win, "mine")
    assert bound == []
    win._dev, win._is_device = (lambda: CARD), (lambda: True)
    bind(win, "mine")
    assert bound == [(CARD, "mine")]
