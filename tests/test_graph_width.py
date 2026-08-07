"""The published graph is as wide as the SINK, not as wide as the profile.

A profile keeps channels the sink does not have -- save_user never strips a
stored channel -- and param_eq silently refuses a config naming more
channels than the node carries, taking the whole chain with it, taste
included. Two pieces: resolve_slots answers which profile channel feeds
each sink channel, profile_graph builds exactly that.
"""
from perdeviceeq import eq


PK = {"type": "PK", "freq": 1000, "gain": -3.0, "q": 1.0, "enabled": True}
LSC = {"type": "LSC", "freq": 50, "gain": 6.0, "q": 1.0, "enabled": True}

SURROUND = ["FL", "FR", "FC", "LFE", "RL", "RR", "FLC", "FRC", "RC", "SL"]
AUX = ["AUX%d" % i for i in range(10)]


def _wide():
    """A stereo correction wearing the keys of two past sinks: ten surround
    names from a card that declared a fake map, ten AUX names from the same
    card before that. Only FL and FR carry bands."""
    keys = SURROUND + AUX
    chans = {k: {"bands": []} for k in keys}
    chans["FL"] = {"bands": [PK]}
    chans["FR"] = {"bands": [PK]}
    return {"preamp": -6.0, "apply_all": False, "floor_off": True,
            "ch_keys": keys, "all": {"bands": []}, "channels": chans}


def _sets(graph):
    return graph.count("filters")


# ---- resolution -----------------------------------------------------------

def test_names_win_when_they_agree():
    assert eq.resolve_slots(["FL", "FR"], ["FL", "FR"]) == ["FL", "FR"]


def test_a_partial_agreement_is_still_an_agreement():
    """FL and FR are themselves wherever they appear; the rest get nothing
    rather than being handed a side they are not."""
    assert eq.resolve_slots(["FL", "FR"], SURROUND) == \
        ["FL", "FR"] + [None] * 8


def test_position_carries_a_card_that_shares_no_names():
    """AUX0..AUX9 against a stereo profile: matching by name alone would
    leave the card silently uncorrected."""
    assert eq.resolve_slots(["FL", "FR"], AUX) == \
        ["FL", "FR"] + [None] * 8


def test_a_sink_narrower_than_the_profile_takes_what_fits():
    assert eq.resolve_slots(SURROUND, ["AUX0", "AUX1"]) == ["FL", "FR"]


def test_no_sink_no_slots():
    assert eq.resolve_slots(["FL", "FR"], []) == []


# ---- the graph ------------------------------------------------------------

def test_graph_follows_the_sink_not_the_profile():
    p = _wide()
    assert _sets(eq.profile_graph(p)) == 20          # what the profile holds
    g = eq.profile_graph(p, slots=["FL", "FR"])
    assert _sets(g) == 2                             # what the sink can take
    assert "filters3" not in g


def test_a_slot_with_no_profile_channel_still_gets_a_chain():
    """Silence is not an option: an unfed sink channel carries the tail --
    floor and taste -- like every other, or the composition would be
    lopsided between channels."""
    g = eq.profile_graph(_wide(), extra=[LSC],
                         slots=["FL", "FR", None, None])
    assert _sets(g) == 4
    assert g.count("freq = 50") == 4                 # taste on all four
    assert g.count("gain = -3") == 2                 # correction on two


def test_taste_survives_a_narrow_sink():
    """The regression that cost an evening: a twenty-key profile on a
    two-channel node published a graph param_eq would not build, so the
    node played dry and the taste layer vanished with the correction."""
    g = eq.profile_graph(_wide(), extra=[LSC], slots=["FL", "FR"])
    assert _sets(g) == 2
    assert g.count("freq = 50") == 2


def test_the_same_side_may_feed_several_routes():
    """A card that sums five stereo buses into one output wants the left
    correction on every left bus."""
    g = eq.profile_graph(_wide(), slots=["FL", "FR", "FL", "FR"])
    assert _sets(g) == 4
    assert g.count("gain = -3") == 4


def test_without_slots_nothing_changes():
    """Callers that do not know the sink keep the profile's own layout."""
    p = _wide()
    assert eq.profile_graph(p) == eq.profile_graph(p, slots=None)
    assert eq.profile_graph(p) == eq.profile_graph(p, slots=[])
