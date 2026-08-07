"""A profile keeps the channels it earned, not the ones it was shown.

The editor's channel list comes from the SINK, so opening a stereo pair's
profile on a ten-channel card used to seed ten slots and the store then kept
them for good: two visits to a card that renames its channels left twenty
keys under a correction that has two. A channel is earned by carrying bands
or by having been measured; an empty slot nobody touched is a leftover.
"""
from perdeviceeq.profiles import editor_body


PK = {"type": "PK", "freq": 1000, "gain": -3.0, "q": 1.0, "enabled": True}


def _body(channels, **kw):
    b = {"name": "Pair", "apply_all": False, "preamp": 0.0,
         "ch_keys": list(channels), "channels": channels}
    b.update(kw)
    return b


def test_a_stereo_pair_stays_a_stereo_pair():
    """The whole point: a visit to a wide card must not widen the profile."""
    stored = _body({"FL": {"bands": [PK]}, "FR": {"bands": [PK]}})
    out = editor_body(_body({"FL": {"bands": [PK]},
                             "FR": {"bands": [PK]}}), stored)
    assert list(out["channels"]) == ["FL", "FR"]
    assert set(out["ch_keys"]) == {"FL", "FR"}


def test_bands_are_never_stripped():
    """The mono flap this rule was built for: a body assembled from a sink
    that reported one channel must not bury the pair's work."""
    stored = _body({"FL": {"bands": [PK]}, "FR": {"bands": [PK]}})
    out = editor_body(_body({"MONO": {"bands": []}}), stored)
    assert out["channels"]["FL"]["bands"] == [PK]
    assert out["channels"]["FR"]["bands"] == [PK]
    assert "FL" in out["ch_keys"] and "FR" in out["ch_keys"]


def test_an_empty_stored_channel_is_dropped():
    """A leftover from an earlier sink leaves on the next save."""
    stored = _body({"FL": {"bands": [PK]}, "FR": {"bands": [PK]},
                    "AUX7": {"bands": []}})
    out = editor_body(_body({"FL": {"bands": [PK]},
                             "FR": {"bands": [PK]}}), stored)
    assert "AUX7" not in out["channels"]
    assert "AUX7" not in out["ch_keys"]


def test_twenty_keys_come_back_as_two():
    """His profile, exactly: ten surround names and ten AUX names picked up
    from one card in two states, with bands under FL and FR only."""
    keys = ["FL", "FR", "FC", "LFE", "RL", "RR", "FLC", "FRC", "RC", "SL"] \
        + ["AUX%d" % i for i in range(10)]
    chans = {k: {"bands": []} for k in keys}
    chans["FL"] = {"bands": [PK]}
    chans["FR"] = {"bands": [PK]}
    out = editor_body(_body({"FL": {"bands": [PK]}, "FR": {"bands": [PK]}}),
                      _body(chans))
    assert set(out["channels"]) == {"FL", "FR"}
    assert set(out["ch_keys"]) == {"FL", "FR"}


def test_a_measured_channel_is_earned_even_while_flat():
    """Measured and needing no correction is a fact about the earphone; it
    is not the same as never having been there."""
    takes = {"takes": [{"id": "t1", "channel": "RL"}]}
    stored = _body({"FL": {"bands": [PK]}, "RL": {"bands": []}},
                   measurement=takes)
    out = editor_body(_body({"FL": {"bands": [PK]}}, measurement=takes),
                      stored)
    assert "RL" in out["channels"]


def test_a_disabled_band_still_counts_as_work():
    off = dict(PK, enabled=False)
    stored = _body({"FL": {"bands": [PK]}, "RR": {"bands": [off]}})
    out = editor_body(_body({"FL": {"bands": [PK]}}), stored)
    assert "RR" in out["channels"]


def test_ch_keys_never_names_a_channel_that_is_gone():
    """ch_keys is the profile's own layout, and the graph is built from it
    when no sink map is known: a name with nothing behind it would cost a
    filter set and buy nothing."""
    stored = _body({"FL": {"bands": [PK]}, "AUX3": {"bands": []},
                    "AUX4": {"bands": []}})
    out = editor_body(_body({"FL": {"bands": [PK]}}), stored)
    assert set(out["ch_keys"]) == set(out["channels"])
