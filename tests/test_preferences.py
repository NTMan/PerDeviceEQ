"""User preference layers (perdeviceeq/preferences.py): the taste EQ
store and the graph it plays. A layer is playback-side taste,
device-independent, played on EVERY channel of every device as a graph
of its own (hook protocol 5) -- and it never touches the profile
itself.
"""
from perdeviceeq import eq, preferences


LSC50 = {"type": "LSC", "freq": 50, "gain": 8.0, "q": 1.0,
         "enabled": True}


def _store(tmp_path):
    return preferences.PreferenceLayers(
        path=str(tmp_path / "layers.json"))


def test_store_roundtrip_and_active(tmp_path):
    st = _store(tmp_path)
    assert st.layers == [] and st.active() is None
    lid = st.upsert({"name": "Basshead", "bands": [LSC50, "junk"]})
    assert st.get(lid)["bands"] == [LSC50]      # non-dicts dropped
    st.set_active(lid)
    st2 = _store(tmp_path)                      # re-read from disk
    assert st2.active_id == lid
    assert st2.active_bands() == [LSC50]
    # replace by id keeps identity; unknown active reads as off
    st2.upsert({"id": lid, "name": "Basshead v2", "bands": []})
    assert st2.get(lid)["name"] == "Basshead v2"
    assert st2.active_bands() == []
    st2.set_active("nope")
    assert st2.active() is None
    assert st2.delete(lid) and not st2.delete(lid)
    assert _store(tmp_path).layers == []


def test_deleting_the_active_layer_turns_it_off(tmp_path):
    st = _store(tmp_path)
    lid = st.upsert({"name": "A", "bands": [LSC50]})
    st.set_active(lid)
    st.delete(lid)
    st3 = _store(tmp_path)
    assert st3.active() is None and st3.layers == []


def test_the_layer_is_one_graph_for_every_channel():
    """One port pair, which audioconvert runs on each channel of the
    node: the taste reaches every chain of any device, and the device's
    own graph does not carry it."""
    g = eq.taste_graph([LSC50])
    assert g.count("freq = 50") == 1
    assert 'inputs = [ "eq:In 1" ]' in g
    assert 'outputs = [ "eq:Out 1" ]' in g
    prof = {"preamp": -3.0, "floor_off": True,
            "ch_keys": ["FL", "FR"],
            "channels": {
                "FL": {"bands": [{"type": "PK", "freq": 200,
                                  "gain": -2.0, "q": 1.0,
                                  "enabled": True}]},
                "FR": {"bands": []}}}
    c = eq.correction_graph(prof, ["FL", "FR"])
    assert "freq = 50" not in c
    assert c.count("freq = 200") == 1           # the profile's own


def test_disabled_layer_bands_stay_out_of_the_graph():
    off = dict(LSC50, enabled=False)
    pk = {"type": "PK", "freq": 300, "gain": 2.0, "q": 1.0}
    assert "freq = 50" not in eq.taste_graph([off, pk])


def test_a_layer_with_every_band_off_is_no_graph():
    """A disabled band is not a reason to publish a graph."""
    off = dict(LSC50, enabled=False)
    assert eq.taste_graph([off]) is None
    assert eq.taste_graph([]) is None
