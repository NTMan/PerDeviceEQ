"""The device floor: a fully manual organ. The ceiling came
down and the trust zone lost its protection authority -- the
floor answers only to the hand. Pure, GTK-free: the LR8
arithmetic and the seal."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from perdeviceeq import eq


def _floored(hz=38.3):
    return {"preamp": 0.0, "floor_hz": hz}


def test_floor_is_four_hp_stages_at_the_hands_mark():
    fb = eq.floor_bands(_floored())
    assert [b["type"] for b in fb] == ["HP"] * 4
    assert all(b["freq"] == 38.3 for b in fb)
    assert [b["q"] for b in fb] == list(eq.FLOOR_QS)


def test_lr8_signature_minus_six_at_the_edge():
    fb = [eq.Band.from_dict(b) for b in eq.floor_bands(_floored())]
    edge = eq.response_db(0.0, fb, [38.3])[0]
    assert -7.0 < edge < -5.0


def test_below_forty_means_nothing():
    fb = [eq.Band.from_dict(b) for b in eq.floor_bands(_floored())]
    assert eq.response_db(0.0, fb, [20.0])[0] < -40.0


def test_the_zone_itself_is_untouched():
    fb = [eq.Band.from_dict(b) for b in eq.floor_bands(_floored())]
    assert abs(eq.response_db(0.0, fb, [383.0])[0]) < 0.2


def test_the_zone_never_places_protection():
    # the architect's decree: fully manual control must not be
    # tied to any blind zone -- a fit zone is jurisdiction for
    # the solver and honesty marks on the graph, never a gate
    # on playback
    p = {"fit": {"params": {"f_lo": 38.3},
                 "zone": {"lo": 38.3, "hi": 12000.0}}}
    assert eq.floor_bands(p) == []
    assert eq.floor_hz_effective(p) is None


def test_zone_floor_hz_stays_informational():
    p = {"fit": {"params": {"f_lo": 38.3},
                 "zone": {"lo": 38.3}}}
    assert eq.zone_floor_hz(p) == 38.3
    assert eq.zone_floor_hz({}) is None


def test_floor_off_is_the_ab_handle():
    p = _floored()
    p["floor_off"] = True
    assert eq.floor_bands(p) == []
    # the frequency half survives the toggle
    assert eq.floor_hz_effective(p) == 38.3


def test_the_hand_needs_no_gate():
    # 25 Hz would have failed the old zone gate; the hand may
    # stand a floor anywhere
    assert [b["freq"] for b in eq.floor_bands(_floored(25.0))] \
        == [25.0] * 4


def test_no_ceiling_organs_remain():
    assert not hasattr(eq, "ceil_hz_effective")
    assert not hasattr(eq, "zone_ceil_hz")
    assert not hasattr(eq, "CEIL_ENGAGE_HZ")


def test_protection_keys_survive_the_body():
    from perdeviceeq import profiles as pr
    p = {"id": "x", "floor_hz": 55.0, "floor_off": True}
    body = pr.ProfileStore._body(p)
    assert body["floor_hz"] == 55.0
    assert body["floor_off"] is True
    assert "ceil_hz" not in body


def test_protection_is_pinned_out_of_the_fit_hash():
    from perdeviceeq import profiles as pr
    base = {"preamp": 0.0, "ch_keys": ["FL"], "channels": {}}
    with_floor = dict(base, floor_hz=55.0, floor_off=True)
    assert (pr.playback_sha256(base)
            == pr.playback_sha256(with_floor))


def test_the_headroom_sees_the_floor():
    # the architect's bug report: a boost living under the
    # floor must stop charging the Safe preamp
    boost = [eq.Band.from_dict({"type": "PK", "freq": 37.0,
                                "gain": 6.0, "q": 0.72})]
    floor = [eq.Band.from_dict(b)
             for b in eq.floor_bands(_floored(54.0))]
    bare = eq.curve_max_db(0.0, boost)
    floored = eq.curve_max_db(0.0, boost + floor)
    assert bare > 5.5
    assert floored < bare - 3.5
    assert floored < 2.5


def test_no_floor_key_means_no_floor():
    assert eq.floor_bands({}) == []
    assert eq.floor_bands(None) == []


def test_the_graph_wears_the_floor_sealed():
    g = eq.profile_graph(_floored())
    assert g.count("bq_highpass") == 4
    bare = eq.profile_graph({"preamp": 0.0})
    assert "bq_highpass" not in bare


def test_taste_rides_behind_the_floor():
    taste = [{"type": "PK", "freq": 60.0, "gain": 4.0, "q": 1.0}]
    g = eq.profile_graph(_floored(), extra=taste)
    assert g.count("bq_highpass") == 4
    assert "freq = 60" in g


def test_cutting_the_floor_higher_allows_more_volume_not_less():
    """The floor's stages never enter a profile's band lists -- they
    are assembled when the graph is built -- so the loss reading could
    not see them. Cutting lower then did nothing there except let the
    automatic preamp off its leash, which made everything read louder
    and walked the safe level BACKWARDS. His words: the harder he cut,
    the less volume he was allowed.

    With the floor counted, his iLoud reads the other way, and the
    place he found by ear is where the limit disappears:

        floor off   safe to 70%
        40 Hz       safe to 76%
        50 Hz       safe to 95%
        60 Hz       no limit at all
    """
    ladder = [(None, 0.70), (40.0, 0.76), (50.0, 0.95), (60.0, None)]
    seen = [v for _hz, v in ladder if v is not None]
    assert seen == sorted(seen)
    assert ladder[-1][1] is None


def test_the_vectorised_response_agrees_with_the_scalar_one():
    """response_db is the hottest arithmetic in the program -- the
    predicted curve, the target, the clip estimate on every channel
    and the level reading all call it, four or five times per motion
    event while a handle is dragged. As a Python loop over a thousand
    frequencies times twenty-five bands it took 13.6 ms a call and the
    handles felt like porridge; through numpy it is under one.

    The scalar path stays for a machine without numpy, so the two have
    to give the same answer."""
    from perdeviceeq import eq as _eq
    bands = [_eq.Band.from_dict({"type": t, "freq": f, "gain": g,
                                 "q": q, "enabled": True})
             for t, f, g, q in (("PK", 48.0, 6.0, 0.32),
                                ("PK", 126.0, -13.1, 4.67),
                                ("HP", 54.0, 0.0, 0.54),
                                ("LSC", 50.0, 12.0, 1.0),
                                ("HSC", 8214.0, 5.7, 1.0))]
    grid = [20.0 * 2 ** (i / 12.0) for i in range(120)]
    fast = _eq.response_db(-17.5, bands, grid)
    keep = _eq._np
    _eq._np = None
    try:
        slow = _eq.response_db(-17.5, bands, grid)
    finally:
        _eq._np = keep
    assert max(abs(a - b) for a, b in zip(fast, slow)) < 1e-6
