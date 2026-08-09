# -*- coding: utf-8 -*-
"""The dial's parked word: resolve_fit_params resolution order
(explicit > pending fit_prefs > last params > defaults) and the
store's _body carrying fit_prefs through a save."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from perdeviceeq import refit                        # noqa: E402
from perdeviceeq.profiles import ProfileStore        # noqa: E402


def test_pending_prefs_beat_old_params():
    prof = {"fit": {"params": {"bands": 12, "f_lo": 40.0}},
            "fit_prefs": {"bands": 15}}
    p = refit.resolve_fit_params(prof)
    assert p["bands"] == 15
    assert p["f_lo"] == 40.0


def test_explicit_argument_beats_pending_prefs():
    prof = {"fit": {"params": {"bands": 12}},
            "fit_prefs": {"bands": 15}}
    assert refit.resolve_fit_params(prof, bands=9)["bands"] == 9


def test_defaults_when_the_profile_is_bare():
    p = refit.resolve_fit_params({})
    assert p["bands"] == 10
    assert p["f_lo"] == 20.0


def test_store_body_carries_the_parked_word():
    prof = {"id": "x", "name": "X",
            "preamp": 0.0, "ch_keys": [], "channels": {},
            "fit_prefs": {"bands": 15}}
    body = ProfileStore._body(prof)
    assert body["fit_prefs"] == {"bands": 15}
    prof.pop("fit_prefs")
    assert "fit_prefs" not in ProfileStore._body(prof)
