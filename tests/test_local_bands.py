"""Bands a hand drew on a route the profile does not cover.

A sink channel with no profile channel behind it still has an equaliser,
and turning it is a decision about THIS OUTPUT: it stays when the profile
changes, it never travels in a package, and a profile that later covers the
channel overrides it without erasing it. So it lives with the binding, in
bindings.json, beside the map and the pins.
"""
import json
import os

import pytest

from perdeviceeq import config, eq
from perdeviceeq import profiles as P


NODE = "alsa_output.usb-Topping_M62-00.HiFi__Line1__sink"
PK = {"type": "PK", "freq": 1000, "gain": -3.0, "q": 1.0, "enabled": True}
BOOST = {"type": "PK", "freq": 300, "gain": 6.0, "q": 1.0, "enabled": True}


@pytest.fixture
def store(tmp_path, monkeypatch):
    for mod in (P, config):
        monkeypatch.setattr(mod, "CONFIG_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(mod, "UI_STATE_FILE",
                            str(tmp_path / "ui-state.json"), raising=False)
    monkeypatch.setattr(P, "BINDINGS_FILE", str(tmp_path / "bindings.json"))
    monkeypatch.setattr(P, "USER_PROFILES_DIR", str(tmp_path / "profiles"))
    os.makedirs(tmp_path / "profiles", exist_ok=True)
    return P.ProfileStore()


def _pair():
    return {"name": "Pair", "apply_all": False, "floor_off": True,
            "ch_keys": ["FL", "FR"],
            "channels": {"FL": {"bands": [PK]}, "FR": {"bands": [PK]}}}


def _written(tmp_path):
    with open(tmp_path / "bindings.json", encoding="utf-8") as f:
        return json.load(f)


# ---- storage --------------------------------------------------------------

def test_local_bands_live_with_the_output(store, tmp_path):
    store.set_binding(NODE, "abc123")
    store.set_local(NODE, "AUX3", [BOOST])
    rec = _written(tmp_path)[NODE]
    assert rec["local"] == {"AUX3": {"bands": [BOOST]}}
    assert P.ProfileStore().local_for(NODE) == {"AUX3": [BOOST]}


def test_clearing_a_channel_leaves_no_trace(store, tmp_path):
    store.set_binding(NODE, "abc123")
    store.set_local(NODE, "AUX3", [BOOST])
    store.set_local(NODE, "AUX3", [])
    assert store.local_for(NODE) == {}
    assert _written(tmp_path)[NODE] == "abc123"


def test_they_survive_a_profile_change(store):
    """The worry that started this: switch away and back, and the tuning is
    where it was, because it was never the profile's."""
    store.set_binding(NODE, "abc123")
    store.set_local(NODE, "AUX3", [BOOST])
    store.set_binding(NODE, "def456")
    assert store.local_for(NODE) == {"AUX3": [BOOST]}


def test_one_output_s_tuning_does_not_reach_another(store):
    store.set_local(NODE, "AUX3", [BOOST])
    assert store.local_for("other") == {}


def test_locals_line_up_with_the_sink(store):
    store.set_local(NODE, "AUX2", [BOOST])
    assert store.locals_for(NODE, ["AUX0", "AUX1", "AUX2"]) == \
        [None, None, [BOOST]]


# ---- what reaches the wire ------------------------------------------------

def test_an_unmapped_channel_plays_what_the_hand_drew():
    g = eq.profile_graph(_pair(), slots=["FL", "FR", None],
                         local=[None, None, [BOOST]])
    assert g.count("filters") == 3
    assert g.count("gain = -3") == 2
    assert "gain = 6" in g


def test_the_profile_wins_where_it_reaches():
    """One source per channel: a mapped channel ignores a local entry
    rather than summing with it, so the window can always name the owner."""
    g = eq.profile_graph(_pair(), slots=["FL", "FR"],
                         local=[[BOOST], [BOOST]])
    assert "gain = 6" not in g
    assert g.count("gain = -3") == 2


def test_the_hook_publishes_them_too(store):
    pid = store.save_user(_pair())
    store.set_binding(NODE, pid)
    store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR", "AUX2"])
    store.set_local(NODE, "AUX2", [BOOST])
    g = store.graph_for_node(NODE)
    assert g.count("filters") == 3
    assert "gain = 6" in g


# ---- headroom -------------------------------------------------------------

def test_a_route_boost_pulls_the_shared_preamp_down():
    """One preamp serves the whole output, so a +6 drawn by ear on a route
    must be in the maximum that sets it -- otherwise it clips past every
    guard."""
    p = _pair()
    assert eq.auto_preamp_db(p) == 0.0          # cuts only
    assert eq.auto_preamp_db(p, local=[[BOOST]]) == pytest.approx(6.0)


def test_the_effective_preamp_sees_the_routes(store):
    pid = store.save_user(_pair())
    store.set_binding(NODE, pid)
    store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR", "AUX2"])
    store.set_local(NODE, "AUX2", [BOOST])
    assert store.effective_preamp(store.get(pid), NODE) == pytest.approx(-6.0)


# ---- which house an edit lands in -----------------------------------------

def test_a_mapped_tab_writes_the_channel_it_feeds_from():
    """The tab says AUX0, the correction is FL's. Without the map the two
    coincide and nobody notices; with it, they must not be confused."""
    prof, route = eq.split_slots({"AUX0": {"bands": [PK]},
                                  "AUX1": {"bands": [PK]}},
                                 {"AUX0": "FL", "AUX1": "FR"})
    assert set(prof) == {"FL", "FR"}
    assert route == {}


def test_an_unmapped_tab_writes_the_route():
    prof, route = eq.split_slots({"AUX0": {"bands": [PK]},
                                  "AUX3": {"bands": [BOOST]}},
                                 {"AUX0": "FL", "AUX3": None})
    assert set(prof) == {"FL"}
    assert route == {"AUX3": {"bands": [BOOST]}}


def test_several_routes_fed_by_one_side_stay_one_correction():
    """Many-to-one is deliberate on a card that sums its buses: the tabs
    are two views of the same side, not two corrections."""
    prof, route = eq.split_slots({"AUX0": {"bands": [PK]},
                                  "AUX2": {"bands": [PK]}},
                                 {"AUX0": "FL", "AUX2": "FL"})
    assert list(prof) == ["FL"]
    assert route == {}


def test_the_all_tab_is_not_a_channel():
    prof, route = eq.split_slots({"all": {"bands": [PK]},
                                  "FL": {"bands": [PK]}},
                                 {"FL": "FL"})
    assert set(prof) == {"FL"}
    assert route == {}


def test_without_a_map_the_tabs_are_the_profile():
    """No live sink to ask: the old behaviour, unchanged."""
    prof, route = eq.split_slots({"FL": {"bands": [PK]},
                                  "FR": {"bands": [PK]}}, None)
    assert set(prof) == {"FL", "FR"}
    assert route == {}
