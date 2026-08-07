"""The preamp is a ride, not a property of the earphone.

In Auto it is derived from the composition -- correction, floor and the
taste layer -- and one of those inputs lives outside the profile, so storing
the answer inside the profile meant that changing the taste rewrote every
profile that had been opened. In Manual it is a decision the user makes at
the always-visible preamp card, above every profile. Neither belongs in a
profile file, and neither travels in a package.
"""
import json
import os

import pytest

from perdeviceeq import config, eq
from perdeviceeq import pdeq
from perdeviceeq import profiles as P


PK6 = {"type": "PK", "freq": 1000, "gain": 6.0, "q": 1.0, "enabled": True}
LSC12 = {"type": "LSC", "freq": 50, "gain": 12.0, "q": 1.0, "enabled": True}


def _profile(**kw):
    b = {"id": "abc123", "name": "Pair", "version": P.SCHEMA_VERSION,
         "apply_all": False, "floor_off": True,
         "ch_keys": ["FL", "FR"],
         "all": {"bands": []},
         "channels": {"FL": {"bands": [PK6]}, "FR": {"bands": [PK6]}}}
    b.update(kw)
    return b


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


def _ui(tmp_path, **kw):
    with open(tmp_path / "ui-state.json", "w", encoding="utf-8") as f:
        json.dump(kw, f)


# ---- the number itself ----------------------------------------------------

def test_auto_clears_the_loudest_channel():
    """+6 of correction wants -6 of headroom."""
    assert eq.auto_preamp_db(_profile()) == pytest.approx(6.0)


def test_the_taste_layer_counts_when_it_is_applied():
    """The window composes taste over correction, so its Auto includes it;
    this is the input that does not live in the profile."""
    assert eq.auto_preamp_db(_profile(), extra=[LSC12]) > 6.0


def test_the_worst_channel_sets_the_shared_value():
    p = _profile(channels={"FL": {"bands": [PK6]},
                           "FR": {"bands": [dict(PK6, gain=9.0)]}})
    assert eq.auto_preamp_db(p) == pytest.approx(9.0)


def test_a_flat_profile_asks_for_nothing():
    p = _profile(apply_all=True, all={"bands": []})
    assert eq.auto_preamp_db(p) == 0.0


# ---- where it does not live ----------------------------------------------

def test_the_store_does_not_write_the_preamp(store, tmp_path):
    pid = store.save_user(_profile(preamp=-16.4))
    with open(tmp_path / "profiles" / ("%s.json" % pid),
              encoding="utf-8") as f:
        on_disk = json.load(f)
    assert "preamp" not in on_disk
    assert "preamp_auto" not in on_disk


def test_a_package_does_not_carry_the_ride():
    text = pdeq.pdeq_pack(_profile(preamp=-16.4))
    assert "preamp" not in text


def test_two_rides_are_the_same_package():
    """A loudness decision made on one chain must not change the address of
    the correction it was made for."""
    assert (pdeq.payload_sha256(_profile(preamp=-16.4))
            == pdeq.payload_sha256(_profile(preamp=0.0)))


# ---- what the hook publishes ---------------------------------------------

def test_the_hook_computes_auto_when_no_one_chose(store, tmp_path):
    pid = store.save_user(_profile())
    store.set_binding("sink", pid)
    assert store.effective_preamp(store.get(pid)) == pytest.approx(-6.0)
    assert "gain = -6" in store.graph_for_node("sink")


def test_the_hook_obeys_a_manual_ride(store, tmp_path):
    _ui(tmp_path, preamp_auto=False, preamp=-3.0)
    pid = store.save_user(_profile())
    store.set_binding("sink", pid)
    assert store.effective_preamp(store.get(pid)) == pytest.approx(-3.0)
    assert "gain = -3" in store.graph_for_node("sink")


def test_auto_in_the_ui_state_still_computes(store, tmp_path):
    _ui(tmp_path, preamp_auto=True, preamp=-99.0)
    pid = store.save_user(_profile())
    store.set_binding("sink", pid)
    assert store.effective_preamp(store.get(pid)) == pytest.approx(-6.0)
