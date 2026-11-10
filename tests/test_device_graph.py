"""What a device plays is one answer, and the window and the hook feed
both ask it: ProfileStore.device_value for a device's key and
ProfileStore.listener_value for the listener's record (hook protocol 5;
config.py has the contract).

They used to build the graph separately and disagreed: the feed left the
taste out and computed Auto without it, Auto in the window skipped the
sink channels no profile channel feeds although the graph gave them the
taste, and an empty profile with a preamp was a graph to one and nothing
to the other.

A node now carries three graphs -- the correction, the taste and the
preamp -- and a device's key carries its correction with its own
preamp, so the two never play apart.
"""
import ast
import math
import os
import pathlib
import random

import pytest

from perdeviceeq import eq, preferences
from perdeviceeq import profiles as P


CUT = {"type": "PK", "freq": 50, "gain": -10.0, "q": 0.7, "enabled": True}
BASS = {"type": "LSC", "freq": 50, "gain": 12.0, "q": 1.0, "enabled": True}
PK = {"type": "PK", "freq": 1000, "gain": 3.0, "q": 1.0, "enabled": True}


def _pair(**kw):
    p = {"name": "Pair", "floor_off": True, "ch_keys": ["FL", "FR"],
         "channels": {"FL": {"bands": [CUT]}, "FR": {"bands": [CUT]}}}
    p.update(kw)
    return p


def _peak(bands):
    return eq.curve_max_db(0.0, [eq.Band.from_dict(b) for b in bands])


def _gui():
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    return {n.name: n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef)}


def _calls(fn):
    return {ast.unparse(n.func) for n in ast.walk(fn)
            if isinstance(n, ast.Call)}


# ---- three graphs -----------------------------------------------------------

def test_the_correction_is_the_sink_s_width_with_no_preamp():
    g = eq.correction_graph(_pair(preamp=-6.0), ["FL", "FR", None])
    assert '"eq:In 3"' in g and '"eq:In 4"' not in g
    assert g.count("gain = -10") == 2
    assert "bq_highshelf" not in g


def test_the_taste_and_the_preamp_fit_a_node_of_any_width():
    for g in (eq.taste_graph([BASS]), eq.gain_graph(-3.0)):
        assert 'inputs = [ "eq:In 1" ]' in g
        assert 'outputs = [ "eq:Out 1" ]' in g
    assert "freq = 50, gain = 12" in eq.taste_graph([BASS])
    assert "bq_highshelf, freq = 0, gain = -3" in eq.gain_graph(-3.0)


def test_no_graph_is_none():
    flat = {"floor_off": True, "ch_keys": [], "channels": {}}
    assert eq.correction_graph(flat, ["FL", "FR"]) is None
    assert eq.correction_graph(_pair(channels={
        "FL": {"bands": [dict(CUT, enabled=False)]}}), ["FL"]) is None
    assert eq.taste_graph([]) is None
    assert eq.gain_graph(0.0) is None and eq.gain_graph(-0.0) is None


def test_a_floor_alone_is_a_correction():
    """The floor is protection; a profile that holds nothing else still
    has it to play."""
    floored = {"floor_hz": 60.0, "ch_keys": [], "channels": {}}
    g = eq.correction_graph(floored, ["FL", "FR"])
    assert g is not None and "bq_highpass" in g


def test_the_values_carry_graphs_as_they_are():
    """A graph is SPA-JSON, so it sits in a value as it is -- the hook
    takes it back whole (tests/hook_harness.lua) -- and no graph is an
    empty string."""
    c, g = eq.correction_graph(_pair(), ["FL", "FR"]), eq.gain_graph(-3.0)
    assert eq.device_value(c, g) == '{ "eq": %s, "gain": %s }' % (c, g)
    assert eq.device_value(c, None) == '{ "eq": %s, "gain": "" }' % c
    t = eq.taste_graph([BASS])
    assert eq.listener_value(t, {"*": g, "a#[Out] Line1": None}) == \
        '{ "taste": %s, "gain": { "*": %s, "a#[Out] Line1": "" } }' % (t, g)
    assert eq.listener_value(None, {"*": None}) == \
        '{ "taste": "", "gain": { "*": "" } }'


# ---- Auto -------------------------------------------------------------------

def test_auto_counts_a_channel_no_profile_channel_feeds():
    """The unfed channel carries the taste alone, and the taste alone
    rises higher than the taste over a bass cut."""
    paired = eq.auto_preamp_db(_pair(), [BASS], ["FL", "FR"])
    wider = eq.auto_preamp_db(_pair(), [BASS], ["FL", "FR", None])
    assert wider > paired
    assert wider == pytest.approx(_peak([BASS]), abs=0.1)


def test_auto_without_slots_keeps_the_profile_layout():
    assert eq.auto_preamp_db(_pair(), [BASS]) == \
        eq.auto_preamp_db(_pair(), [BASS], ["FL", "FR"])


def _band(rnd):
    return {"type": rnd.choice(["PK", "PK", "LSC", "HSC"]),
            "freq": 10 ** rnd.uniform(math.log10(15), math.log10(23000)),
            "gain": rnd.uniform(-15.0, 15.0), "q": rnd.uniform(0.3, 10.0),
            "enabled": rnd.random() > 0.1}


def test_auto_from_the_kept_curves_is_the_number_over_the_whole_chain():
    """Auto sums two kept curves, a device's and the taste's, instead of
    working out each chain with the taste appended. The points and the
    sums are the same, so the number is too."""
    rnd = random.Random(5)
    for _ in range(300):
        keys = ["FL", "FR", "FC"][:rnd.randint(0, 3)]
        p = {"ch_keys": keys,
             "channels": {k: {"bands": [_band(rnd) for _ in
                                        range(rnd.randint(0, 10))]}
                          for k in keys}}
        if rnd.random() < 0.5:
            p["floor_hz"] = rnd.uniform(25.0, 90.0)
        else:
            p["floor_off"] = True
        taste = [_band(rnd) for _ in range(rnd.randint(0, 4))]
        slots = rnd.choice([None, keys, keys + [None]])
        whole = [_peak(c + taste) for c in eq._chains(p, slots=slots)]
        want = max(0.0, math.ceil(max(whole) * 10.0 - 1e-9) / 10.0)
        assert eq.auto_preamp_db(p, taste, slots) == want


def test_the_preamp_is_the_ride_or_auto():
    p, slots = _pair(channels={"FL": {"bands": [PK]},
                               "FR": {"bands": [PK]}}), ["FL", "FR"]
    assert eq.preamp_db(p, slots, [], -2.5) == -2.5
    assert eq.preamp_db(p, slots, []) == -3.0
    assert eq.preamp_db(_pair(), slots, []) == 0.0     # a cut asks nothing
    assert eq.preamp_db({}, None, [BASS]) == \
        -eq.auto_preamp_db({}, [BASS])                 # the taste alone


# ---- the feed asks the store --------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "BINDINGS_FILE", str(tmp_path / "bindings.json"))
    monkeypatch.setattr(P, "USER_PROFILES_DIR", str(tmp_path / "profiles"))
    os.makedirs(tmp_path / "profiles", exist_ok=True)
    return P.ProfileStore()


def _taste(bands):
    layers = preferences.PreferenceLayers()
    layers.set_active(layers.upsert({"name": "Mine", "bands": bands}))


def _bound(store, node="sink#port", body=None, cmap=None):
    pid = store.save_user(body or _pair())
    store.set_binding(node, pid)
    store.set_map(node, cmap or {"FL": "FL", "FR": "FR"})
    return pid


def test_a_device_value_is_its_correction_with_its_preamp(store):
    pid = _bound(store)
    slots = ["FL", "FR"]
    want = eq.auto_preamp_db(store.get(pid), [BASS], slots)
    assert want > 0
    assert store.device_value("sink#port", ([BASS], None)) == \
        eq.device_value(eq.correction_graph(store.get(pid), slots),
                        eq.gain_graph(-want))
    assert store.device_value("sink#port", ([BASS], -1.5)) == \
        eq.device_value(eq.correction_graph(store.get(pid), slots),
                        eq.gain_graph(-1.5))


def test_the_feed_reads_the_listener_from_disk(store):
    _taste([BASS])
    pid = _bound(store)
    want = eq.auto_preamp_db(store.get(pid), [BASS], ["FL", "FR"])
    assert "gain = %g" % -want in store.device_value("sink#port")
    assert eq.taste_graph([BASS]) in store.listener_value()


def test_a_device_with_no_correction_follows_the_listener(store):
    assert store.device_value("nobody#port", ([BASS], None)) is None
    store.set_binding("clean#port", P.CLEAN_ID)
    assert store.device_value("clean#port", ([BASS], None)) is None
    _bound(store, "empty#port", {"name": "Empty", "floor_off": True,
                                 "ch_keys": ["FL"],
                                 "channels": {"FL": {"bands": []}}})
    assert store.device_value("empty#port", ([BASS], -3.0)) is None
    store.set_binding("gone#port", "no-such-profile")
    assert store.device_value("gone#port", ([BASS], None)) is None


def test_the_listener_record_in_auto_and_in_manual(store):
    """Auto: a preamp per device with a correction of its own, over that
    correction and the taste, and "*" over the taste alone. Manual: the
    ride, once."""
    pid = _bound(store, body=_pair(channels={"FL": {"bands": [PK]},
                                              "FR": {"bands": [PK]}}))
    store.set_binding("clean#port", P.CLEAN_ID)
    taste = eq.taste_graph([BASS])
    own = -eq.auto_preamp_db(store.get(pid), [BASS], ["FL", "FR"])
    star = -eq.auto_preamp_db({}, [BASS])
    assert store.listener_value(([BASS], None)) == eq.listener_value(
        taste, {"*": eq.gain_graph(star), "sink#port": eq.gain_graph(own)})
    assert store.listener_value(([BASS], -4.0)) == eq.listener_value(
        taste, {"*": eq.gain_graph(-4.0)})


def test_the_window_holds_the_device_in_view_ahead_of_the_store(store):
    """An edit is heard before it is saved: what the window holds
    answers for its device, in its value and in the record."""
    _bound(store)
    ahead = _pair(channels={"FL": {"bands": [PK]}, "FR": {"bands": [PK]}})
    held = {"sink#port": (ahead, ["FL", "FR"])}
    assert store.device_value("sink#port", ([], None), held) == \
        eq.device_value(eq.correction_graph(ahead, ["FL", "FR"]),
                        eq.gain_graph(-3.0))
    assert eq.gain_graph(-3.0) in store.listener_value(([], None), held)
    held = {"sink#port": None}                   # Clean in the window
    assert store.device_value("sink#port", ([], None), held) is None
    assert '"sink#port"' not in store.listener_value(([BASS], None), held)
    # bound in the window only: still a device of the record
    held = {"new#port": (ahead, ["FL", "FR"])}
    assert '"new#port"' in store.listener_value(([], None), held)


def test_the_feed_and_the_window_call_give_one_string(store):
    """The window asks with what is on screen; given the stored profile,
    the stored map and the same layers it must be the feed's string byte
    for byte."""
    pid = _bound(store, cmap={"FL": "FL", "FR": "FR", "RL": None})
    layers = ([BASS], None)
    window = store.device_value(
        "sink#port", layers,
        {"sink#port": (store.get(pid), ["FL", "FR", None])})
    assert store.device_value("sink#port", layers) == window
    assert store.wire_state(layers)["sink#port"] == window


def test_the_wire_holds_the_listener_and_every_binding(store):
    from perdeviceeq.config import BYPASS_KEY, LISTENER_KEY, STRIP
    _bound(store)
    _bound(store, "empty#port", {"name": "Empty", "floor_off": True,
                                 "ch_keys": ["FL"],
                                 "channels": {"FL": {"bands": []}}})
    wire = store.wire_state(([BASS], None))
    assert set(wire) == {BYPASS_KEY, LISTENER_KEY, "sink#port",
                         "empty#port"}
    assert wire[BYPASS_KEY] == STRIP       # not pressed
    assert wire[LISTENER_KEY] == store.listener_value(([BASS], None))
    assert wire["empty#port"] is None      # plays the taste: it follows


def test_the_wire_carries_bypass_first_while_it_is_pressed(store):
    """--apply writes the wire in order: Bypass first, so a device written
    after it is not heard with its correction on the way."""
    from perdeviceeq import config
    _bound(store)
    config.save_ui_state({"bypass": True})
    wire = store.wire_state(([BASS], None))
    assert list(wire)[0] == config.BYPASS_KEY
    assert wire[config.BYPASS_KEY] == config.BYPASS_ON
    assert wire["sink#port"] == store.device_value("sink#port",
                                                   ([BASS], None))


# ---- the window has no second door ------------------------------------------

def test_the_window_publishes_the_contract():
    """A device's value is the store's answer, with the device in view as
    the window holds it, Bypass pressed or not: Bypass goes out under its
    own key (_publish_bypass) and leaves the value as it is."""
    fn = _gui()["_apply_now"]
    pubs = [[ast.unparse(a) for a in c.args] for c in ast.walk(fn)
            if isinstance(c, ast.Call)
            and ast.unparse(c.func) == "auth.publish_graph"]
    assert pubs == [["node", "value"]]
    calls = _calls(fn)
    assert "self.bypass_row.get_active" not in calls
    assert "self.store.device_value" in calls
    assert "self._held" in calls
    assert not any(c.startswith("eq.") for c in calls)


def test_the_window_wishes_in_the_order_it_makes_them():
    """A wish returns at once, so the window makes it on its own thread.
    Handed to a thread each, two wishes could be recorded in either
    order, and the newest would not be the one that lands last."""
    fns = _gui()
    for name in ("_apply_now", "_publish_listener", "_publish_sharers",
                 "_publish_unbound", "_publish_bypass"):
        assert "pw_backend.in_thread" not in _calls(fns[name])


def test_an_edit_is_heard_when_it_is_made():
    """The publish rode the debounced save, so an edit in the main window
    was heard only after 200 ms of stillness: a slow drag of a band stayed
    silent until the hand stopped. The edit publishes, once the siblings
    hold the slot; the save keeps the disk and the timeline."""
    fns = _gui()

    def lines(fn, name):
        return [c.lineno for c in ast.walk(fns[fn])
                if isinstance(c, ast.Call) and ast.unparse(c.func) == name]

    published = lines("_on_edit", "self._apply_now")
    assert published
    assert lines("_on_edit", "self._mirror_siblings")[0] < published[0]
    assert lines("_save_now", "self._apply_now") == []
