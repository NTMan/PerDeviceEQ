"""What a device plays is one answer, eq.device_graph, and the window and
the hook feed both ask it.

They used to build the graph separately and disagreed: the feed left the
taste out and computed Auto without it, Auto in the window skipped the
sink channels no profile channel feeds although the graph gave them the
taste, and an empty profile with a preamp was a graph to one and nothing
to the other.
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


# ---- the common entry -------------------------------------------------------

def test_the_common_entry_is_the_listener_s_layers_in_any_width():
    g = P.common_graph([BASS], None)
    assert 'inputs = [ "eq:In 1" ]' in g and 'outputs = [ "eq:Out 1" ]' in g
    assert "freq = 50, gain = 12" in g
    assert "gain = -12" in g                     # Auto over the taste alone
    assert P.common_graph([], None) == P.STRIP
    assert P.common_graph([], -3.0) != P.STRIP   # a ride with no taste


def test_the_window_publishes_the_contract():
    """Bypass is explicitly nothing; a device with no profile follows the
    common entry, which the window publishes with it."""
    fn = next(n for n in ast.walk(ast.parse(pathlib.Path(
        eq.__file__).with_name("gui.py").read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef) and n.name == "_apply_now")
    pubs = [[ast.unparse(a) for a in c.args] for c in ast.walk(fn)
            if isinstance(c, ast.Call)
            and ast.unparse(c.func) == "auth.publish_graph"]
    assert ["node", "STRIP"] in pubs
    assert ["COMMON_KEY", "common"] in pubs


def test_the_window_wishes_in_the_order_it_makes_them():
    """A wish returns at once, so the window makes it on its own thread.
    Handed to a thread each, two wishes could be recorded in either
    order, and the newest would not be the one that lands last."""
    tree = ast.parse(pathlib.Path(eq.__file__).with_name("gui.py")
                     .read_text(encoding="utf-8"))
    for name in ("_apply_now", "_publish_unbound"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        calls = {ast.unparse(c.func) for c in ast.walk(fn)
                 if isinstance(c, ast.Call)}
        assert "pw_backend.in_thread" not in calls


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


# ---- when there is nothing to play ------------------------------------------

def test_nothing_to_play_is_none():
    flat = {"floor_off": True, "ch_keys": [], "channels": {}}
    assert eq.device_graph(flat, ["FL", "FR"], []) is None


def test_the_taste_alone_plays():
    flat = {"floor_off": True, "ch_keys": [], "channels": {}}
    g = eq.device_graph(flat, ["FL", "FR"], [BASS])
    assert g is not None and "freq = 50" in g


def test_a_manual_ride_alone_plays():
    flat = {"floor_off": True, "ch_keys": [], "channels": {}}
    g = eq.device_graph(flat, ["FL", "FR"], [], manual=-3.0)
    assert g is not None and "gain = -3" in g


def test_a_floor_alone_plays():
    """The floor is protection; a profile that holds nothing else still
    has it to play."""
    floored = {"floor_hz": 60.0, "ch_keys": [], "channels": {}}
    g = eq.device_graph(floored, ["FL", "FR"], [])
    assert g is not None and "bq_highpass" in g


def test_auto_is_part_of_the_answer():
    g = eq.device_graph(_pair(channels={"FL": {"bands": [PK]},
                                        "FR": {"bands": [PK]}}),
                        ["FL", "FR"], [])
    assert "gain = -3" in g


# ---- the feed asks the same question ----------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "BINDINGS_FILE", str(tmp_path / "bindings.json"))
    monkeypatch.setattr(P, "USER_PROFILES_DIR", str(tmp_path / "profiles"))
    os.makedirs(tmp_path / "profiles", exist_ok=True)
    return P.ProfileStore()


def _taste(bands):
    layers = preferences.PreferenceLayers()
    layers.set_active(layers.upsert({"name": "Mine", "bands": bands}))


def test_the_feed_carries_the_taste(store):
    _taste([BASS])
    pid = store.save_user(_pair())
    store.set_binding("sink#port", pid)
    store.set_map("sink#port", {"FL": "FL", "FR": "FR"})
    g = store.graph_for_node("sink#port")
    assert g.count("freq = 50, gain = 12") == 2
    want = eq.auto_preamp_db(store.get(pid), [BASS],
                             slots=["FL", "FR"])
    assert "gain = %g" % -want in g


def test_the_feed_and_the_window_call_give_one_string(store):
    """The window calls device_graph with what is on screen; given the
    stored profile, the stored map and the same layers it must be the
    feed's string byte for byte."""
    _taste([BASS])
    pid = store.save_user(_pair())
    store.set_binding("sink#port", pid)
    store.set_map("sink#port", {"FL": "FL", "FR": "FR", "RL": None})
    window = eq.device_graph(store.get(pid), ["FL", "FR", None], [BASS])
    assert store.graph_for_node("sink#port") == window


def test_a_bound_empty_profile_plays_the_taste_after_install(store):
    _taste([BASS])
    pid = store.save_user({"name": "Empty", "floor_off": True,
                           "ch_keys": ["FL"],
                           "channels": {"FL": {"bands": []}}})
    store.set_binding("sink#port", pid)
    assert store.wire_state()["sink#port"] is not None


# ---- the window has no second door ------------------------------------------

def test_the_window_publishes_through_the_shared_answer():
    """The divergence came from two places building a graph. The window's
    publish asks device_graph and builds nothing of its own."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_apply_now")
    calls = {ast.unparse(n.func) for n in ast.walk(fn)
             if isinstance(n, ast.Call)}
    assert "eq.device_graph" in calls
    assert not any(c.endswith("profile_graph") for c in calls)


def test_an_edit_is_heard_when_it_is_made():
    """The publish rode the debounced save, so an edit in the main window
    was heard only after 200 ms of stillness: a slow drag of a band stayed
    silent until the hand stopped. The edit publishes, once the siblings
    hold the slot; the save keeps the disk and the timeline."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    fns = {n.name: n for n in ast.walk(ast.parse(src))
           if isinstance(n, ast.FunctionDef)}

    def lines(fn, name):
        return [c.lineno for c in ast.walk(fns[fn])
                if isinstance(c, ast.Call) and ast.unparse(c.func) == name]

    published = lines("_on_edit", "self._apply_now")
    assert published
    assert lines("_on_edit", "self._mirror_siblings")[0] < published[0]
    assert lines("_save_now", "self._apply_now") == []
