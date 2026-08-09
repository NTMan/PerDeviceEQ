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


def test_an_unmapped_tab_keeps_its_own_name():
    """The crash-test case: a ten-channel node in front of a stereo
    profile. Typing on a channel the profile does not reach makes the
    profile carry that channel -- visible, editable, and saved, rather
    than swallowed by a binding nobody can see."""
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


def test_an_unmapped_tab_is_played_as_well_as_saved():
    """The other half of the same rule: the editor writes such a tab
    under its own name, so the WIRE has to look for it there. A slot
    list of bare Nones publishes ten tail-only chains and the bands the
    profile really holds are never heard."""
    body = {"preamp": 0.0, "floor_off": True, "ch_keys": ["FLC"],
            "channels": {"FLC": {"bands": [PK]}}}
    sink = ["FL", "FR", "FC", "LFE", "RL", "RR", "FLC", "FRC", "RC", "SL"]
    cmap = {k: None for k in sink}
    slots = [(cmap.get(k) or k) for k in sink]
    g = eq.profile_graph(body, slots=slots)
    # the empty channels carry a transparent filter at the same freq,
    # so the GAIN is what tells the real band from the placeholders
    assert g.count("gain = -3") == 1


def test_the_store_slot_list_falls_back_to_the_sink_name(tmp_path,
                                                         monkeypatch):
    from perdeviceeq import profiles as P
    monkeypatch.setattr(P, "BINDINGS_FILE", str(tmp_path / "b.json"))
    monkeypatch.setattr(P, "USER_PROFILES_DIR", str(tmp_path / "p"))
    monkeypatch.setattr(P, "CONFIG_DIR", str(tmp_path))
    st = P.ProfileStore()
    st.maps = {"n": {"AUX0": "FL", "AUX3": None}}
    assert st.slots_for("n") == ["FL", "AUX3"]


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
