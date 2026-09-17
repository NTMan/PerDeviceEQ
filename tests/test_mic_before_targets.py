"""A rig is set up before the profile has sides.

His order, and the app's own: the microphone is settled first and
needs to know nothing -- not the target, not the routing. So pointing
a capsule at a target happens on a profile that has no targets yet,
and the answer has to survive until they arrive. It did not: the
assignment was refused on the way in and dropped again on the way
out, and both capsule tabs went on reading "captures nothing" after
the targets were added.

Real methods unbound over a stub self, against a real mic store.
"""

import pytest

from perdeviceeq import measure_prefs


class _Obj:
    pass


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(measure_prefs, "MIC_PROFILES_FILE",
                        str(tmp_path / "mics.json"), raising=False)
    return measure_prefs.MicProfileStore()


@pytest.fixture
def mw(monkeypatch):
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
    except ValueError as e:                  # gi without the typelibs
        pytest.skip(str(e))
    from perdeviceeq import measure_window, pw_backend
    monkeypatch.setattr(pw_backend, "live_device_key", lambda n: n,
                        raising=False)
    return measure_window


def _win(mw, store, ch_keys, cols, width=2):
    w = _Obj()
    w.mic_store = store
    w.ch_keys = list(ch_keys)
    w.mic_cols = list(cols)
    w.mic_ch = width
    w.mic_col = None
    w.cal = {}
    w.mic_of = {}
    w._takes_pending = None
    w.sink_node = "sink"
    w.memory = _Obj()
    w.memory.remember = lambda *a, **k: None
    w.mic_picker = _Obj()
    w.mic_picker.core = _Obj()
    w.mic_picker.core.node = "in-1"
    w._selected_source = lambda: {"name": "in-1", "desc": "E.A.R.S"}
    w._knees_for_store = lambda src, existing: {}
    for name in ("_recompute_mic", "_rebuild_map_slots", "_update_pult"):
        setattr(w, name, lambda *a: None)
    w._stored_takes = lambda: mw.MeasureWindow._stored_takes(w)
    w._persist_mic = lambda by_hand=False: mw.MeasureWindow._persist_mic(
        w, by_hand)
    return w


def test_a_capsule_can_be_pointed_before_a_target_exists(mw, store):
    w = _win(mw, store, [], [])
    mw.MeasureWindow._point_col(w, 0, ["FL"])
    mw.MeasureWindow._point_col(w, 1, ["FR"])
    pid = store.match("in-1")["id"]
    assert store.takes_of(pid) == {"FL": 0, "FR": 1}


def test_and_the_strip_reads_it_once_the_targets_arrive(mw, store):
    w = _win(mw, store, [], [])
    mw.MeasureWindow._point_col(w, 0, ["FL"])
    mw.MeasureWindow._point_col(w, 1, ["FR"])
    later = _win(mw, store, ["FL", "FR"], [0, 1])
    later.mic_of = mw.MeasureWindow._mic_of_map(later)
    assert mw.MeasureWindow._takes_of_col(later, 0) == ["FL"]
    assert mw.MeasureWindow._takes_of_col(later, 1) == ["FR"]


def test_a_narrower_profile_does_not_erase_the_other_capsule(mw, store):
    """Two capsules pointed under a two-sided profile, then a
    one-sided profile in front of the same rig and any save at all --
    the close does one."""
    w = _win(mw, store, ["FL", "FR"], [0, 1])
    mw.MeasureWindow._point_col(w, 0, ["FL"])
    mw.MeasureWindow._point_col(w, 1, ["FR"])
    pid = store.match("in-1")["id"]
    narrow = _win(mw, store, ["FL"], [0, 1])
    mw.MeasureWindow._persist_mic(narrow)
    assert store.takes_of(pid) == {"FL": 0, "FR": 1}
    assert store.columns_of(pid) == [0, 1]
