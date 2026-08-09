"""Which profile channel an editor tab writes into.

A tab wears the name of a SINK channel; a profile's channels name the
sides of a transducer. While a card calls its outputs FL and FR the two
coincide and nobody notices. Point AUX0 at FL and they part: the tab is
AUX0, the correction is FL's. There is no third house -- a tab the map
does not answer for keeps its own name, because bands drawn there are a
correction somebody typed and the profile is the only place to keep one.
"""
from perdeviceeq import eq


PK = {"type": "PK", "freq": 1000, "gain": -3.0, "q": 1.0, "enabled": True}
BOOST = {"type": "PK", "freq": 300, "gain": 6.0, "q": 1.0, "enabled": True}


def test_a_mapped_tab_writes_the_channel_it_feeds_from():
    out = eq.profile_slots({"AUX0": {"bands": [PK]},
                            "AUX1": {"bands": [PK]}},
                           {"AUX0": "FL", "AUX1": "FR"})
    assert set(out) == {"FL", "FR"}


def test_a_slot_the_map_cannot_place_keeps_its_own_name():
    """Defensive: with pairs, every tab has an answer, so this is the
    offline shape -- a slot the map says nothing about is the profile's
    own channel and must not be dropped on the way to disk."""
    out = eq.profile_slots({"AUX0": {"bands": [PK]},
                            "AUX3": {"bands": [BOOST]}},
                           {"AUX0": "FL", "AUX3": None})
    assert set(out) == {"FL", "AUX3"}
    assert out["AUX3"]["bands"] == [BOOST]


def test_several_routes_fed_by_one_side_stay_one_correction():
    """Many-to-one is deliberate on a card that sums its buses: the tabs
    are two views of the same side, not two corrections."""
    out = eq.profile_slots({"AUX0": {"bands": [PK]},
                            "AUX2": {"bands": [PK]}},
                           {"AUX0": "FL", "AUX2": "FL"})
    assert list(out) == ["FL"]


def test_without_a_map_the_tabs_are_the_profile():
    """No live sink to ask: the tabs ARE the profile's channels."""
    out = eq.profile_slots({"FL": {"bands": [PK]},
                            "FR": {"bands": [PK]}}, None)
    assert set(out) == {"FL", "FR"}


def test_tabs_are_the_paired_channels():
    """A tab is a PAIR. An unpaired sink channel -- nothing resolved for
    it, or a hand deleted its pair -- has no tab at all."""
    sink = ["FL", "FR", "FC", "LFE"]
    assert eq.paired_tabs({"FL": "FL", "FR": "FR", "FC": None,
                           "LFE": None}, sink) == ["FL", "FR"]
    assert eq.paired_tabs({k: "FL" for k in sink}, sink) == sink
    assert eq.paired_tabs({}, sink) == sink        # offline: no map
    assert eq.paired_tabs({k: None for k in sink}, sink) == []


def test_an_unpaired_channel_plays_dry(tmp_path, monkeypatch):
    """Deleting a pair pins None, and None is not a name to look up --
    the wire gives that channel the tail alone, which is the whole
    point of being able to delete one."""
    from perdeviceeq import profiles as P
    monkeypatch.setattr(P, "BINDINGS_FILE", str(tmp_path / "b.json"))
    monkeypatch.setattr(P, "USER_PROFILES_DIR", str(tmp_path / "p"))
    monkeypatch.setattr(P, "CONFIG_DIR", str(tmp_path))
    st = P.ProfileStore()
    st.maps = {"n": {"AUX0": "FL", "AUX3": None}}
    assert st.slots_for("n") == ["FL", None]
    body = {"preamp": 0.0, "floor_off": True, "ch_keys": ["FL"],
            "channels": {"FL": {"bands": [PK]}}}
    g = eq.profile_graph(body, slots=st.slots_for("n"))
    # the empty channel carries a transparent filter, so the GAIN is
    # what tells the real band from the placeholder
    assert g.count("gain = -3") == 1


def test_a_deleted_pair_is_pinned_and_survives_a_reconcile(tmp_path,
                                                           monkeypatch):
    """A None the resolver produced and a None a hand chose look the
    same in the map, so the choice is kept apart as a PIN -- otherwise
    the next reconcile hands the pair straight back."""
    from perdeviceeq import profiles as P
    monkeypatch.setattr(P, "BINDINGS_FILE", str(tmp_path / "b.json"))
    monkeypatch.setattr(P, "USER_PROFILES_DIR", str(tmp_path / "p"))
    monkeypatch.setattr(P, "CONFIG_DIR", str(tmp_path))
    st = P.ProfileStore()
    sink = ["FL", "FR", "FC", "LFE"]
    assert set(st.reconcile_map("n", ["FL"], sink).values()) == {"FL"}
    st.pin_channel("n", "FC", None)
    m = st.reconcile_map("n", ["FL"], sink)
    assert m["FC"] is None and m["FR"] == "FL"
    assert eq.paired_tabs(m, sink) == ["FL", "FR", "LFE"]
    st.pin_channel("n", "FC", "FL")            # Add EQ sink, back again
    assert eq.paired_tabs(st.reconcile_map("n", ["FL"], sink),
                          sink) == sink


def test_one_channel_spread_makes_every_tab_a_sibling():
    """A single-channel profile on a ten-channel node: resolve_slots
    spreads it, so every tab feeds from that one side and an edit on one
    is an edit on all."""
    sink = ["FL", "FR", "FC", "LFE", "RL", "RR", "FLC", "FRC", "RC", "SL"]
    cmap = {k: "FL" for k in sink}
    assert eq.sibling_tabs("FL", cmap, sink) == sink[1:]
    assert eq.sibling_tabs("FLC", cmap, sink) == [k for k in sink
                                                  if k != "FLC"]


def test_tabs_on_different_sides_are_not_siblings():
    out = eq.sibling_tabs("AUX0", {"AUX0": "FL", "AUX1": "FR",
                                   "AUX2": "FL"},
                          ["AUX0", "AUX1", "AUX2"])
    assert out == ["AUX2"]


def test_an_unmapped_tab_is_its_own_only_view():
    """It keeps its own name, so nothing else feeds from it."""
    assert eq.sibling_tabs("AUX3", {"AUX0": "FL", "AUX3": None},
                           ["AUX0", "AUX3"]) == []


def test_the_first_band_under_no_eq_spreads(tmp_path, monkeypatch):
    """The field sequence: No EQ has no channels, so the map answers
    nothing for every tab. The first band typed forks a brand-new
    ONE-channel profile, and from that moment the map must spread it
    over the whole sink -- otherwise the edit stays on the tab it was
    typed on, the other tabs keep their pre-edit copies, and the fold
    hands the profile whichever copy came last."""
    from perdeviceeq import profiles as P
    monkeypatch.setattr(P, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(P, "BINDINGS_FILE", str(tmp_path / "b.json"))
    monkeypatch.setattr(P, "USER_PROFILES_DIR", str(tmp_path / "p"))
    (tmp_path / "p").mkdir()
    st = P.ProfileStore()
    node = "m62.direct"
    sink = ["FL", "FR", "FC", "LFE", "RL", "RR", "FLC", "FRC", "RC", "SL"]

    assert set(st.reconcile_map(node, [], sink).values()) == {None}

    pid = st.save_user({"name": "Custom EQ", "preamp": 0.0,
                        "ch_keys": ["FL"],
                        "channels": {"FL": {"bands": [PK]}}})
    p = st.get(pid)
    cmap = st.reconcile_map(node, list(p["ch_keys"]), sink)
    assert set(cmap.values()) == {"FL"}
    assert eq.sibling_tabs("FL", cmap, sink) == sink[1:]

    slots = [(cmap.get(k) or k) for k in sink]
    g = eq.profile_graph(dict(p, floor_off=True), slots=slots)
    assert g.count("gain = -3") == len(sink)


def test_a_tab_is_refilled_when_its_source_changes():
    """Existence is not enough: a slot must follow the channel that
    feeds the tab. An empty slot can appear from nothing more than
    someone asking for it, and a re-added tab then came back blank."""
    tabs = ["FL", "FR"]
    src = {"FL": "FL", "FR": "FR"}
    assert eq.tabs_needing_fill(tabs, {"FL": 1, "FR": 1}, src,
                                {"FL": "FL", "FR": "FR"}) == []
    # FL was unpaired and paired again: the leftover slot is stale
    assert eq.tabs_needing_fill(tabs, {"FL": 1, "FR": 1}, {"FR": "FR"},
                                {"FL": "FL", "FR": "FR"}) == ["FL"]
    # re-paired to another channel: the old bands are the wrong ones
    assert eq.tabs_needing_fill(tabs, {"FL": 1, "FR": 1}, src,
                                {"FL": "FL", "FR": "FL"}) == ["FR"]
    # a tab with no slot at all
    assert eq.tabs_needing_fill(tabs, {"FL": 1}, src,
                                {"FL": "FL", "FR": "FR"}) == ["FR"]
