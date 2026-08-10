"""The channel map lives with the binding, not with the profile.

A profile's channels name the sides of a transducer; a sink's channels name
routes. The correspondence between them belongs to neither, so it is kept
where the node's other route facts are kept: in bindings.json, next to the
profile id.
"""
import json
import os

import pytest

from perdeviceeq import profiles as P


NODE = "alsa_output.usb-Topping_M62-00.HiFi__Line1__sink"
OTHER = "alsa_output.usb-miniDSP_IL-DSP-00.analog-stereo"
PK = {"type": "PK", "freq": 1000, "gain": -3.0, "q": 1.0, "enabled": True}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(P, "BINDINGS_FILE", str(tmp_path / "bindings.json"))
    monkeypatch.setattr(P, "USER_PROFILES_DIR", str(tmp_path / "profiles"))
    os.makedirs(tmp_path / "profiles", exist_ok=True)
    return P.ProfileStore()


def _written(tmp_path):
    with open(tmp_path / "bindings.json", encoding="utf-8") as f:
        return json.load(f)


# ---- file format ----------------------------------------------------------

def test_a_bare_id_is_still_a_binding(store, tmp_path):
    """Files written before maps existed keep working untouched."""
    with open(tmp_path / "bindings.json", "w", encoding="utf-8") as f:
        json.dump({NODE: "abc123"}, f)
    s = P.ProfileStore()
    assert s.binding_for(NODE) == "abc123"
    assert s.map_for(NODE) == {}
    assert s.slots_for(NODE) is None


def test_a_node_without_a_map_is_written_back_bare(store, tmp_path):
    """No map, no record: the file only grows a record once someone maps."""
    store.set_binding(NODE, "abc123")
    assert _written(tmp_path) == {NODE: "abc123"}


def test_a_mapped_node_is_written_as_a_record(store, tmp_path):
    store.set_binding(NODE, "abc123")
    store.set_map(NODE, {"AUX0": "FL", "AUX1": "FR", "AUX2": None})
    rec = _written(tmp_path)[NODE]
    assert rec["profile"] == "abc123"
    assert rec["map"] == {"AUX0": "FL", "AUX1": "FR", "AUX2": None}


def test_a_record_survives_a_reload(store, tmp_path):
    store.set_binding(NODE, "abc123")
    store.set_map(NODE, {"AUX0": "FL", "AUX1": "FR"})
    s = P.ProfileStore()
    assert s.binding_for(NODE) == "abc123"
    assert s.slots_for(NODE) == ["FL", "FR"]


def test_one_node_s_map_does_not_reach_another(store):
    """Two identical cards on one machine are two outputs, not one."""
    store.set_binding(NODE, "abc123")
    store.set_binding(OTHER, "abc123")
    store.set_map(NODE, {"AUX0": "FL", "AUX1": "FR"})
    assert store.slots_for(OTHER) is None


# ---- reconciliation -------------------------------------------------------

def test_a_first_bind_answers_itself(store):
    m = store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR"])
    assert m == {"FL": "FL", "FR": "FR"}


def test_a_card_with_no_shared_names_falls_to_position(store):
    m = store.reconcile_map(NODE, ["FL", "FR"], ["AUX0", "AUX1", "AUX2"])
    assert m == {"AUX0": "FL", "AUX1": "FR", "AUX2": None}


def test_a_deliberate_choice_survives(store):
    """A third route fed by the left side as well: reconciling must not
    undo a choice a hand made."""
    store.reconcile_map(NODE, ["FL", "FR"], ["AUX0", "AUX1", "AUX2"])
    store.pin_channel(NODE, "AUX2", "FL")
    m = store.reconcile_map(NODE, ["FL", "FR"], ["AUX0", "AUX1", "AUX2"])
    assert m == {"AUX0": "FL", "AUX1": "FR", "AUX2": "FL"}


def test_a_widened_sink_keeps_what_was_and_answers_the_rest(store):
    """The card changed mode and grew from two channels to ten."""
    store.set_map(NODE, {"FL": "FL", "FR": "FR"})
    m = store.reconcile_map(NODE, ["FL", "FR"],
                            ["FL", "FR", "FC", "LFE"])
    assert m == {"FL": "FL", "FR": "FR", "FC": None, "LFE": None}


def test_a_narrowed_sink_drops_the_channels_it_no_longer_has(store):
    store.set_map(NODE, {"FL": "FL", "FR": "FR", "FC": None, "LFE": None})
    assert store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR"]) == \
        {"FL": "FL", "FR": "FR"}


def test_a_target_the_new_profile_lacks_is_answered_again(store):
    """A map pointing at SL is meaningless once a stereo profile is bound."""
    store.set_map(NODE, {"AUX0": "SL", "AUX1": "RC"})
    assert store.reconcile_map(NODE, ["FL", "FR"], ["AUX0", "AUX1"]) == \
        {"AUX0": "FL", "AUX1": "FR"}


def test_reconciling_an_unchanged_map_writes_nothing(store, tmp_path):
    store.set_binding(NODE, "abc123")
    store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR"])
    before = os.stat(tmp_path / "bindings.json").st_mtime_ns
    store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR"])
    assert os.stat(tmp_path / "bindings.json").st_mtime_ns == before


# ---- what reaches the wire ------------------------------------------------

def test_the_hook_s_graph_is_cut_by_the_stored_map(store):
    """graph_for_node runs where the sink cannot be asked, so the map is the
    only thing that knows how wide the node is."""
    pid = store.save_user({"name": "Pair",
                           "floor_off": True, "preamp": 0.0,
                           "ch_keys": ["FL", "FR", "FC", "LFE"],
                           "channels": {"FL": {"bands": [PK]},
                                        "FR": {"bands": [PK]},
                                        "FC": {"bands": []},
                                        "LFE": {"bands": []}}})
    store.set_binding(NODE, pid)
    assert store.graph_for_node(NODE).count("filters") == 4
    store.set_map(NODE, {"AUX0": "FL", "AUX1": "FR"})
    g = store.graph_for_node(NODE)
    assert g.count("filters") == 2
    assert g.count("gain = -3") == 2


# ---- a hand's choice, kept apart from the resolver's answer ---------------

def test_the_resolver_s_answer_is_never_mistaken_for_a_choice(store):
    """The bug this file's second half exists for: reconciling once while no
    profile was loaded wrote a map of all None, and every later reconcile
    read those Nones as deliberate and kept them, so the node stayed
    uncorrected for good."""
    store.reconcile_map(NODE, [], ["FL", "FR"])          # nothing to map yet
    assert store.map_for(NODE) == {"FL": None, "FR": None}
    assert store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR"]) == \
        {"FL": "FL", "FR": "FR"}


def test_a_pinned_none_is_deliberate_and_stays(store):
    store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR"])
    store.pin_channel(NODE, "FR", None)
    assert store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR"]) == \
        {"FL": "FL", "FR": None}


def test_a_pin_survives_a_reload(store):
    store.set_binding(NODE, "abc123")
    store.reconcile_map(NODE, ["FL", "FR"], ["AUX0", "AUX1", "AUX2"])
    store.pin_channel(NODE, "AUX2", "FL")
    s = P.ProfileStore()
    assert s.pins_for(NODE) == {"AUX2": "FL"}
    assert s.reconcile_map(NODE, ["FL", "FR"], ["AUX0", "AUX1", "AUX2"]) == \
        {"AUX0": "FL", "AUX1": "FR", "AUX2": "FL"}


def test_a_pin_whose_channel_is_gone_is_forgotten(store):
    store.pin_channel(NODE, "AUX9", "FR")
    store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR"])
    assert store.pins_for(NODE) == {}


def test_a_pin_outlives_a_profile_that_lacks_its_channel(store):
    """The routing is a fact about the CARD. A profile that has no
    channel of that name cannot use the pin, but it must not destroy
    it -- selecting No EQ, which has no channels at all, once wiped
    every routing choice on the card off the disk (field report)."""
    store.pin_channel(NODE, "AUX0", "SL")
    m = store.reconcile_map(NODE, ["FL", "FR"], ["AUX0", "AUX1"])
    assert m["AUX0"] == "FL"                  # unusable: resolver answers
    assert store.pins_for(NODE) == {"AUX0": "SL"}
    m = store.reconcile_map(NODE, ["SL"], ["AUX0", "AUX1"])
    assert m["AUX0"] == "SL"                  # the profile is back


def test_no_eq_does_not_wipe_the_routing(store):
    """His sequence: AUX6 paired to FL by hand, then No EQ, then any
    profile again -- the pair has to still be there."""
    store.pin_channel(NODE, "AUX6", "FL")
    sink = ["AUX%d" % i for i in range(10)]
    store.reconcile_map(NODE, [], sink)                    # No EQ
    assert store.pins_for(NODE) == {"AUX6": "FL"}
    m = store.reconcile_map(NODE, ["FL", "FR"], sink)
    assert m["AUX6"] == "FL"


def test_unpinning_returns_the_channel_to_the_resolver(store):
    store.pin_channel(NODE, "FR", None)
    store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR"])
    store.unpin_channel(NODE, "FR")
    assert store.reconcile_map(NODE, ["FL", "FR"], ["FL", "FR"]) == \
        {"FL": "FL", "FR": "FR"}


def test_pins_are_written_beside_the_map(store, tmp_path):
    store.set_binding(NODE, "abc123")
    store.reconcile_map(NODE, ["FL", "FR"], ["AUX0", "AUX1"])
    store.pin_channel(NODE, "AUX1", None)
    rec = _written(tmp_path)[NODE]
    assert rec["map"] == {"AUX0": "FL", "AUX1": "FR"}
    assert rec["pinned"] == {"AUX1": None}


def test_a_hand_made_pair_does_not_spread(store):
    """One channel spreads because nothing said where it goes. Once a
    hand has said, the guess stops: adding a single pair by hand used
    to put a tab on every free output of the card, all showing the
    same channel -- eight tabs for one deliberate choice (field)."""
    sink = ["AUX%d" % i for i in range(6)]
    # nothing pinned: the spread is the whole point of one channel
    assert set(store.reconcile_map(NODE, ["FL"], sink).values()) == {"FL"}
    store.pin_channel(NODE, "AUX0", "FL")
    m = store.reconcile_map(NODE, ["FL"], sink)
    assert m["AUX0"] == "FL"
    assert all(m[k] is None for k in sink[1:])


def test_two_channels_still_take_a_pin_as_an_extra_route(store):
    """The one-to-one answers are untouched: with two channels a pin
    ADDS a route rather than multiplying one, which is the deliberate
    many-to-one a summing card wants."""
    store.pin_channel(NODE, "AUX2", "FL")
    m = store.reconcile_map(NODE, ["FL", "FR"], ["AUX0", "AUX1", "AUX2"])
    assert m == {"AUX0": "FL", "AUX1": "FR", "AUX2": "FL"}


def test_moving_a_target_leaves_its_old_output_behind(store):
    """What the measurement window does when a target already has an
    output: it MOVES, it does not gain a second route. The main window
    adds one -- many-to-one is deliberate on a card that sums its buses
    -- but a sweep goes somewhere singular, and while the old route
    stood the resolver kept answering with it and the tab kept showing
    it (field)."""
    sink = ["AUX%d" % i for i in range(8)]
    m = store.reconcile_map(NODE, ["FL", "FR"], sink)
    assert m["AUX0"] == "FL"                     # the resolver's guess

    def move(target, to):                        # what _add_pair does
        # the CURRENT MAP, not the pins: AUX0 carries FL because the
        # resolver GUESSED it, and a guess left standing answers again
        cur = store.reconcile_map(NODE, ["FL", "FR"], sink)
        for ch, val in list(cur.items()):
            if val == target and ch != to:
                store.pin_channel(NODE, ch, None)
        store.pin_channel(NODE, to, target)

    move("FL", "AUX6")
    m = store.reconcile_map(NODE, ["FL", "FR"], sink)
    assert m["AUX6"] == "FL"
    assert m["AUX0"] is None                     # not a second route
    assert m["AUX1"] == "FR"                     # the other side stands
    move("FL", "AUX7")
    m = store.reconcile_map(NODE, ["FL", "FR"], sink)
    assert m["AUX7"] == "FL" and m["AUX6"] is None
