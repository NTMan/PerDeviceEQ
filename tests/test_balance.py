"""The balance: a channel's level against the other channels, a number of
the channel ("balance", dB) and not a band (config.SCHEMA_VERSION 8). It
plays as a flat gain at the head of its chain, Auto counts it, the store
keeps it to the hundredth and leaves out a 0, and the meter hears it."""
import os
import threading

import numpy as np
import pytest

from perdeviceeq import eq, profiles
from perdeviceeq.meter import MeterEngine

PK = {"type": "PK", "freq": 1000.0, "gain": 6.0, "q": 1.0,
      "enabled": True}


def _pair(fl=None, fr=None, preamp=0.0):
    return {"preamp": preamp, "ch_keys": ["FL", "FR"],
            "channels": {"FL": dict({"bands": [PK]}, **(fl or {})),
                         "FR": dict({"bands": [PK]}, **(fr or {}))},
            "floor_off": True}


def test_the_balance_leads_its_chain_on_the_preamp():
    g = eq.profile_graph(_pair(fl={"balance": -1.3}, preamp=-2.0))
    one, two = g.split("filters2 = ")
    assert "freq = 0, gain = -3.3, q = 1.0" in one
    assert "freq = 0, gain = -2, q = 1.0" in two
    # in the correction graph the preamp is graph 2's: the balance alone
    g = eq.correction_graph(_pair(fl={"balance": -1.3}, preamp=-2.0))
    one, two = g.split("filters2 = ")
    assert "freq = 0, gain = -1.3, q = 1.0" in one
    assert "bq_highshelf" not in two


def test_a_balance_alone_is_a_correction():
    p = {"ch_keys": ["FL", "FR"], "floor_off": True,
         "channels": {"FL": {"bands": [], "balance": -0.5},
                      "FR": {"bands": []}}}
    assert eq.has_correction(p)
    assert eq.correction_graph(p) is not None
    p["channels"]["FL"]["balance"] = 0.0
    assert not eq.has_correction(p)


def test_auto_counts_the_balance():
    """Both chains peak at +6; FL's balance takes 2 off its peak and FR's
    adds 1 to its own, so FR sets Safe."""
    assert eq.auto_preamp_db(_pair()) == 6.0
    assert eq.auto_preamp_db(
        _pair(fl={"balance": -2.0}, fr={"balance": 1.0})) == 7.0
    assert eq.auto_preamp_db(_pair(fl={"balance": -2.0})) == 6.0


def test_the_store_keeps_it_to_the_hundredth_and_leaves_out_a_zero():
    sane = profiles.ProfileStore._sane_slot
    assert sane({"bands": [PK], "balance": -1.304}) == {
        "bands": [PK], "balance": -1.3}
    assert sane({"bands": [PK], "balance": 0.0}) == {"bands": [PK]}
    assert sane({"bands": [PK], "balance": -0.001}) == {"bands": [PK]}
    assert sane({"bands": [PK]}) == {"bands": [PK]}
    # so a body hashes the same whichever way it got there
    a = dict(_pair(), channels={"FL": {"bands": [PK], "balance": 0.0},
                                "FR": {"bands": [PK]}})
    b = profiles.ProfileStore._body(dict(a, id="x"))
    assert profiles.playback_sha256(b) == \
        profiles.playback_sha256(profiles.ProfileStore._body(
            dict(_pair(), id="x")))


def test_a_save_keeps_a_channel_that_has_only_its_balance(tmp_path,
                                                          monkeypatch):
    """A channel is carried past a save when it holds bands, a balance or
    takes: the editor's view may not carry it, the profile still does."""
    stored = {"id": "p", "name": "P", "version": profiles.SCHEMA_VERSION,
              "ch_keys": ["FL", "FR"],
              "channels": {"FL": {"bands": [PK]},
                           "FR": {"bands": [], "balance": -0.8}}}
    body = {"id": "p", "name": "P", "ch_keys": ["FL"],
            "channels": {"FL": {"bands": [PK]}}}
    out = profiles.editor_body(body, stored)
    assert out["channels"]["FR"] == {"bands": [], "balance": -0.8}
    assert out["ch_keys"] == ["FL", "FR"]


def test_the_meter_hears_each_channels_balance():
    frames = []
    eng = MeterEngine(frames.append, fs=48000)
    eng.set_chains(-1.0, [[], []], balances_db=[-6.0, 0.0])
    n = 48000
    x = np.zeros((n, 2), dtype=np.float32)
    s = 0.5 * np.sin(2 * np.pi * 997 * np.arange(n) / 48000)
    x[:, 0] = s
    x[:, 1] = s
    r, w = os.pipe()

    def feed():
        with os.fdopen(w, "wb") as fw:
            fw.write(x.tobytes())
    t = threading.Thread(target=feed)
    t.start()
    with os.fdopen(r, "rb") as fr:
        eng._run(fr)
    t.join()
    pk0 = max(f["peaks_db"][0] for f in frames)
    pk1 = max(f["peaks_db"][1] for f in frames)
    assert pk1 == pytest.approx(20 * np.log10(0.5) - 1.0, abs=0.05)
    assert pk0 - pk1 == pytest.approx(-6.0, abs=0.01)
