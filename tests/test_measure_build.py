"""The canvas write side (measure_build) against the pw-* shims: an
accepted take persists the moment it exists (commit_take), deletion is
physical and prunes emptied sessions (remove_takes), and the fit is
settled from the whole stored canvas with per-channel progress
(refit_and_save). The serialization stays pinned by the pure tests.
ProfileStore's config paths are redirected into tmp so the real
~/.config is untouched.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from perdeviceeq import measure_build, measure_session as ms
from perdeviceeq import measure_core as mc
from perdeviceeq import profiles
from perdeviceeq import profiles as profiles_mod
from perdeviceeq import refit
from perdeviceeq.profiles import ProfileStore

ROOT = Path(__file__).resolve().parent.parent
SHIMS = ROOT / "tests" / "shims"

GRAPH = ("{ nodes = [ { type = builtin name = eq label = param_eq "
         "config = { filters = [ { type = bq_peaking, freq = 200, "
         "gain = 9.6, q = 2.25 } ] } } ] }")


@pytest.fixture
def shim_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "metadata.json").write_text(json.dumps({"test_sink": GRAPH}))
    (state / "volume.json").write_text(json.dumps({"cubic": 0.30}))
    monkeypatch.setenv("PDE_SHIM_DIR", str(state))
    monkeypatch.setenv("PDE_SHIM_REPO", str(ROOT))
    monkeypatch.setenv("PDE_SHIM_PLAY_SECONDS", "0.9")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("PATH", "%s%s%s"
                       % (SHIMS, os.pathsep, os.environ["PATH"]))
    return state


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles_mod, "USER_PROFILES_DIR",
                        str(tmp_path / "profiles"))
    monkeypatch.setattr(profiles_mod, "SYS_PROFILE_DIRS", [])
    monkeypatch.setattr(profiles_mod, "CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(profiles_mod, "BINDINGS_FILE",
                        str(tmp_path / "cfg" / "bindings.json"))
    return ProfileStore()


def _cfg(tmp_path, **kw):
    kw.setdefault("samples", 131072)
    return ms.SessionConfig(sink="test_sink", source="test_source",
                            channels=2, save_dir=str(tmp_path / "takes"),
                            **kw)


def _session(tmp_path, takes):
    ses = ms.MeasureSession(_cfg(tmp_path))
    with ses:
        for ch, analyze in takes:
            ses.take(ch, analyze=analyze)
    return ses


def _bare(store, pid="inc", name="Inc"):
    return store.save_user({"id": pid, "name": name, "version": 3,
                            "preamp": 0.0, "ch_keys": [],
                            "channels": {}})


def test_take_dict_resamples_onto_profile_grid():
    freqs = mc.log_grid()
    coarse = freqs[::4]
    rec = ms.TakeRecord(1, 0, coarse,
                        np.linspace(-3.0, 3.0, len(coarse)),
                        5.0, 40.0, -6.0, 0, 0, None,
                        capture_channel=1, created_utc="t")
    d = measure_build.take_dict(rec, "s1", "FL", freqs)
    assert len(d["mag_db_uncal"]) == len(freqs)
    assert d["channel"] == "FL" and d["session"] == "s1"
    assert d["capture_channel"] == 1 and d["created_utc"] == "t"
    # edges survive the log-f interpolation (and the 0.01 dB rounding)
    assert d["mag_db_uncal"][0] == -3.0
    assert d["mag_db_uncal"][-1] == 3.0


def test_fingerprint_tracks_takes_cal_and_params():
    m = {"grid": {"f_lo": 20.0}, "source": {"cal": {}},
         "takes": [{"id": "a", "mag_db_uncal": [0.0, 1.0]},
                   {"id": "b", "mag_db_uncal": [2.0]}]}
    p = {"bands": 10}
    base = measure_build.fit_fingerprint(m, ["a", "b"], p)
    assert measure_build.fit_fingerprint(m, ["a", "b"], p) == base
    assert measure_build.fit_fingerprint(m, ["a"], p) != base
    assert measure_build.fit_fingerprint(m, ["a", "b"],
                                         {"bands": 12}) != base
    m["takes"][0]["mag_db_uncal"][0] = 0.5
    assert measure_build.fit_fingerprint(m, ["a", "b"], p) != base
    m["source"]["cal"] = {"0": {"sha256": "x"}}
    assert measure_build.fit_fingerprint(m, ["a", "b"], p) != base


def test_commit_take_builds_the_canvas(shim_state, store, tmp_path):
    pid = _bare(store)
    flat = tmp_path / "flat.txt"
    flat.write_text("20 0.0\n1000 0.0\n20000 0.0\n")
    tilt = tmp_path / "tilt.txt"
    tilt.write_text("20 0.0\n1000 -3.0\n20000 -6.0\n")
    ses = ms.MeasureSession(_cfg(tmp_path))
    with ses:
        ses.take(0)
        ids = measure_build.commit_take(
            store, pid, ses, 0, "FL", ses.takes_of(0)[-1].id,
            cal=str(flat), source={"name": "EARS", "serial": "861"})
        assert len(store.get(pid)["measurement"]["takes"]) == 1
        ses.take(0)
        measure_build.commit_take(
            store, pid, ses, 0, "FL", ses.takes_of(0)[-1].id,
            cal=str(flat), canvas_session=ids["session"])
        ses.take(1)
        measure_build.commit_take(
            store, pid, ses, 1, "FR", ses.takes_of(1)[-1].id,
            cal=str(tilt), canvas_session=ids["session"])
    p = store.get(pid)
    m = p["measurement"]
    assert m["grid"] == {"f_lo": mc.GRID_F_LO, "f_hi": mc.GRID_F_HI,
                         "ppo": mc.GRID_PPO}
    assert "source" not in m         # v4: the rig lives on sessions
    (sid, sess), = m["sessions"].items()
    assert sess["source"]["node_match"] == "test_source"
    assert sess["source"]["serial"] == "861"
    flat_sha = hashlib.sha256(flat.read_bytes()).hexdigest()
    tilt_sha = hashlib.sha256(tilt.read_bytes()).hexdigest()
    assert set(m["cal_library"]) == {flat_sha, tilt_sha}
    assert m["cal_library"][flat_sha]["file"] == "flat.txt"
    assert sess["sink"]["node_name"] == "test_sink"
    assert sess["sweep"]["fs"] == ses.sweep.fs
    n = len(mc.log_grid())
    assert len(m["takes"]) == 3
    for t in m["takes"]:
        assert t["session"] == sid
        assert len(t["mag_db_uncal"]) == n
        assert isinstance(t["created_utc"], str)
        assert t["capture_channel"] == {"FL": 0, "FR": 1}[t["channel"]]
        assert t["cal_sha"] == {"FL": flat_sha,
                                "FR": tilt_sha}[t["channel"]]
    assert p["provenance"] == {"kind": "measured"}
    assert "fit" not in p                    # commits never fit
    events = []
    measure_build.refit_and_save(
        store, pid,
        progress=lambda *a: events.append(a))
    p = store.get(pid)
    fit = p["fit"]
    assert not refit.fit_is_stale(p)
    assert sorted(fit["takes"]) == sorted(t["id"] for t in m["takes"])
    assert fit["inputs_sha256"] == measure_build.fit_fingerprint(
        p["measurement"], fit["takes"], fit["params"])
    assert fit["output_sha256"] == profiles.playback_sha256(p)
    for key in ("FL", "FR"):
        assert p["channels"][key]["bands"]
    fr = [e[0] for e in events]
    assert fr == sorted(fr) and fr.count(1.0) == 1
    assert events[-1][:2] == (1.0, None)
    assert {e[1] for e in events[:-1]} == {"FL", "FR"}


def test_commit_take_accepts_a_foreign_rig(shim_state, store,
                                           tmp_path):
    """The append gate fell (field doctrine): a foreign-rig take
    commits and the canvas grows; schema v5 keeps the truth per
    sitting -- each session wears its own rig stamp."""
    pid = _bare(store, "g", "Gate")
    ses = _session(tmp_path, [(0, 0)])
    measure_build.commit_take(store, pid, ses, 0, "FL",
                              ses.takes_of(0)[-1].id,
                              source={"serial": "861"})
    ses2 = _session(tmp_path, [(0, 0)])
    rid = ses2.takes_of(0)[-1].id
    measure_build.commit_take(store, pid, ses2, 0, "FL", rid,
                              source={"serial": "999"})
    m = store.get(pid)["measurement"]
    assert len(m["takes"]) == 2          # the canvas grew
    stamps = [m["sessions"][t["session"]]["source"]["serial"]
              for t in m["takes"]]
    assert stamps == ["861", "999"]      # truth per sitting


def test_remove_takes_prunes_sessions_and_stales_the_fit(
        shim_state, store, tmp_path):
    pid = _bare(store, "rm", "Rm")
    ses = _session(tmp_path, [(0, 0), (0, 0)])
    ids = measure_build.commit_take(store, pid, ses, 0, "FL",
                                    ses.takes_of(0)[0].id)
    measure_build.commit_take(store, pid, ses, 0, "FL",
                              ses.takes_of(0)[1].id,
                              canvas_session=ids["session"])
    measure_build.refit_and_save(store, pid)
    m = store.get(pid)["measurement"]
    first, second = [t["id"] for t in m["takes"]]
    sid, = m["sessions"]
    assert measure_build.remove_takes(store, pid, ["nope"]) == 0
    assert measure_build.remove_takes(store, pid, [first]) == 1
    p = store.get(pid)
    assert [t["id"] for t in p["measurement"]["takes"]] == [second]
    assert sid in p["measurement"]["sessions"]   # still referenced
    assert refit.fit_is_stale(p)                 # consumed take gone
    assert measure_build.remove_takes(store, pid, [second]) == 1
    p = store.get(pid)
    assert p["measurement"]["takes"] == []
    assert p["measurement"]["sessions"] == {}    # pruned with it


def test_refit_and_save_respects_hand_edits(shim_state, store,
                                            tmp_path):
    pid = _bare(store, "ed", "Ed")
    ses = _session(tmp_path, [(0, 0)])
    measure_build.commit_take(store, pid, ses, 0, "FL",
                              ses.takes_of(0)[-1].id)
    measure_build.refit_and_save(store, pid)
    p = dict(store.get(pid))
    p["fit"] = dict(p["fit"], edited=True)
    store.save_user(p)
    with pytest.raises(refit.RefitError, match="edited"):
        measure_build.refit_and_save(store, pid)
    measure_build.refit_and_save(store, pid, allow_edited=True)
    assert store.get(pid)["fit"]["edited"] is False


def _cal_file(tmp_path, name="c.txt", db=1.0):
    p = tmp_path / name
    p.write_text("20 0.0\n1000 %.1f\n20000 0.0\n" % db)
    return str(p)


def test_reassign_cal_moves_by_sha(shim_state, store, tmp_path):
    """The bulk re-hang the schema was built for: every take on
    old_sha moves to the new cal in one stroke; others and raw
    takes are untouched; the old library entry survives."""
    pid = _bare(store, "rh", "Rehang")
    ses = _session(tmp_path, [(0, 0), (0, 0)])
    cal_a = _cal_file(tmp_path, "a.txt", 1.0)
    ids = measure_build.commit_take(store, pid, ses, 0, "FL",
                                    ses.takes_of(0)[0].id,
                                    cal=cal_a)
    measure_build.commit_take(store, pid, ses, 0, "FL",
                              ses.takes_of(0)[1].id, cal=cal_a,
                              canvas_session=ids["session"])
    ses2 = _session(tmp_path, [(0, 0)])
    measure_build.commit_take(store, pid, ses2, 0, "FL",
                              ses2.takes_of(0)[-1].id)  # raw
    m0 = store.get(pid)["measurement"]
    old_sha = m0["takes"][0]["cal_sha"]
    assert old_sha and m0["takes"][2]["cal_sha"] is None
    cal_b = _cal_file(tmp_path, "b.txt", 2.0)
    moved = measure_build.reassign_cal(store, pid, old_sha, cal_b)
    assert moved == 2
    m = store.get(pid)["measurement"]
    new_shas = {t["cal_sha"] for t in m["takes"][:2]}
    assert len(new_shas) == 1 and old_sha not in new_shas
    assert m["takes"][2]["cal_sha"] is None
    assert old_sha in m["cal_library"]        # history kept
    assert next(iter(new_shas)) in m["cal_library"]
    assert measure_build.reassign_cal(store, pid, "nope",
                                      cal_b) == 0


def test_cal_groups_shapes_the_manage_dialog():
    """The canvas grouped by cal origin: one group per sha (None
    = raw), ordered by first appearance, counting takes and
    naming the rigs that used it -- the pure core the Manage
    dialog renders."""
    m = {"cal_library": {"aa": {"file": "L.txt", "points": []},
                         "bb": {"file": "R.txt", "points": []}},
         "sessions": {"s1": {"source": {"name": "EARS"}},
                      "s2": {"source": {"name": "Umik"}}},
         "takes": [
             {"id": "1", "session": "s1", "cal_sha": "aa"},
             {"id": "2", "session": "s1", "cal_sha": "aa"},
             {"id": "3", "session": "s2", "cal_sha": None},
             {"id": "4", "session": "s2", "cal_sha": "aa"},
             {"id": "5", "session": "s2", "cal_sha": "bb"}]}
    gs = measure_build.cal_groups(m)
    assert [(g["sha"], g["file"], g["count"], g["rigs"])
            for g in gs] == [
        ("aa", "L.txt", 3, ["EARS", "Umik"]),
        (None, None, 1, ["Umik"]),
        ("bb", "R.txt", 1, ["Umik"])]
    assert measure_build.cal_groups({}) == []


def test_the_solver_version_stales_old_fits(monkeypatch):
    """The architect's decree: changing the solver must make
    every automatically fitted result belong to ANOTHER
    solver -- stale to the Auto button on one press, no
    random-band poking to break the equalizer. The version is
    folded into the fingerprint, so a bump changes it."""
    from perdeviceeq import fit_peq
    m = {"grid": {"f_lo": 20.0, "f_hi": 20000.0, "ppo": 48},
         "takes": [{"id": "t1", "mag_db_uncal": [0.0, 1.0],
                    "cal_sha": "abc"}]}
    params = {"bands": 12, "f_lo": 35.0, "f_hi": 20000.0}
    a = measure_build.fit_fingerprint(m, ["t1"], params)
    monkeypatch.setattr(fit_peq, "SOLVER_VERSION",
                        fit_peq.SOLVER_VERSION + 1)
    b = measure_build.fit_fingerprint(m, ["t1"], params)
    assert a != b


def test_mean_confession_averages_and_abstains():
    from perdeviceeq import measure_build as mb
    class R:
        def __init__(self, thd, h2=None, h3=None, nfl=None):
            self.thd_db = thd
            self.h2_db = h2
            self.h3_db = h3
            self.thd_noise_db = nfl
    a = R([-40.0, None, -50.0], h2=[-45.0, -50.0, None],
          nfl=[-60.0, -60.0, -60.0])
    b = R([-46.0, -44.0, None], h2=[-51.0, -50.0, None],
          nfl=[-66.0, -60.0, None])
    out = mb.mean_confession([a, b])
    assert out is not None
    thd, h2, h3, nfl = out
    assert abs(thd[0] - (-42.03)) < 0.1
    assert abs(thd[1] - (-44.0)) < 1e-6
    assert abs(thd[2] - (-50.0)) < 1e-6
    assert h3 is None
    assert abs(nfl[0] - (-62.03)) < 0.1
    silent = R(None)
    assert mb.mean_confession([silent]) is None


def test_stored_take_carries_the_confession():
    """Schema 5: the confession rides the take itself -- rounded
    values, abstentions as None (JSON has no NaN), so deleting a
    take deletes one index and nothing else."""
    freqs = mc.log_grid(20.0, 20000.0, 12)
    n = len(freqs)
    rec = ms.TakeRecord(
        "t1", 0, list(freqs), [0.0] * n, 1.0, 40.0, -12.0, 0, 0,
        None, thd_db=[-40.0] * (n - 1) + [float("nan")],
        h2_db=[-50.0] * n)
    from perdeviceeq import measure_build as mb
    out = mb.take_dict(rec, "s1", "FL", freqs)
    assert out["thd_db"][0] == -40.0
    assert out["thd_db"][-1] is None
    assert out["h2_db"][5] == -50.0
    assert "h3_db" not in out          # absent confession stays absent

def test_thd_at_gives_the_number_a_datasheet_prints():
    """One frequency, one percent -- and an honest mark when the
    reading is really the rig's own floor."""
    f = np.geomspace(20.0, 20000.0, 400)
    thd = np.full(400, -52.0)
    pct, clamped = measure_build.thd_at(f, thd,
                                        np.full(400, -80.0))
    assert abs(pct - 0.251) < 0.005
    assert clamped is False
    assert measure_build.thd_at(f, thd, np.full(400, -53.0))[1]
    assert measure_build.thd_at(f, thd)[1] is False
    assert measure_build.thd_at(f, np.full(400, np.nan)) is None
    assert measure_build.thd_at(f, None) is None
    assert measure_build.thd_at(f, [-52.0]) is None


def test_thd_at_reads_the_frequency_it_was_asked_for():
    f = np.array([100.0, 1000.0, 10000.0])
    thd = [-40.0, -60.0, -20.0]
    assert abs(measure_build.thd_at(f, thd)[0] - 0.1) < 1e-6
    assert abs(measure_build.thd_at(f, thd, f0=100.0)[0]
               - 1.0) < 1e-6
    thd = [-40.0, None, -20.0]
    assert measure_build.thd_at(f, thd) is None

def test_a_calibration_can_be_taken_off_recorded_takes(
        shim_state, store, tmp_path):
    """A coupler cal that ended up on a loopback of bare wire is a
    wrong READING, not a wrong recording: the take stores its
    magnitude uncalibrated and names the cal it was read with. So
    taking it off is the same act as moving it, and it is bulk."""
    pid = _bare(store, "off", "TakeItOff")
    ses = _session(tmp_path, [(0, 0), (0, 0)])
    cal_a = _cal_file(tmp_path, "coupler.txt", 1.0)
    ids = measure_build.commit_take(store, pid, ses, 0, "FL",
                                    ses.takes_of(0)[0].id,
                                    cal=cal_a)
    measure_build.commit_take(store, pid, ses, 0, "FL",
                              ses.takes_of(0)[1].id, cal=cal_a,
                              canvas_session=ids["session"])
    ses2 = _session(tmp_path, [(0, 0)])
    measure_build.commit_take(store, pid, ses2, 0, "FL",
                              ses2.takes_of(0)[-1].id)  # raw
    m0 = store.get(pid)["measurement"]
    sha = m0["takes"][0]["cal_sha"]
    moved = measure_build.reassign_cal(store, pid, sha, None)
    assert moved == 2
    m = store.get(pid)["measurement"]
    assert [t.get("cal_sha") for t in m["takes"]] == [None] * 3
    assert sha in m["cal_library"]        # history is kept
    # nothing left to take off is not an operation
    assert measure_build.reassign_cal(store, pid, sha, None) == 0
    assert measure_build.reassign_cal(store, pid, None, None) == 0

def test_the_headline_reads_what_the_pen_draws():
    """A single narrow tone landing on 1 kHz used to become "the
    device's THD" while the drawn line, smoothed a twelfth of an
    octave, showed something else entirely -- the header and the
    picture under it disagreed by tens of decibels."""
    f = np.geomspace(20.0, 20000.0, 958)
    quiet = np.full(958, -80.0)
    i = int(np.argmin(np.abs(f - 1000.0)))
    spiked = quiet.copy()
    spiked[i] = -40.0                      # one interference tone
    pct_q = measure_build.thd_at(f, quiet)[0]
    pct_s = measure_build.thd_at(f, spiked)[0]
    assert abs(pct_q - 0.01) < 0.001
    assert abs(pct_s - pct_q) < 0.001      # the spike buys nothing
    # a band that is genuinely raised IS reported
    raised = quiet.copy()
    band = (f >= 1000 * 2 ** (-1 / 24.)) & (f <= 1000 * 2 ** (1 / 24.))
    raised[band] = -40.0
    assert abs(measure_build.thd_at(f, raised)[0] - 1.0) < 0.01
    # abstention across the whole band is still an abstention
    gone = quiet.copy()
    gone[band] = np.nan
    assert measure_build.thd_at(f, gone) is None
    # the floor mark reads the same band
    assert measure_build.thd_at(f, quiet,
                                np.full(958, -82.0))[1] is True
    assert measure_build.thd_at(f, quiet,
                                np.full(958, -95.0))[1] is False


def test_the_takes_say_which_column_saw_them():
    """A dropdown touched today must not change how measurements
    made yesterday are read."""
    class R(object):
        def __init__(self, c):
            self.capture_channel = c

    assert measure_build.capsule_of([R(0), R(0)]) == 0
    assert measure_build.capsule_of([R(1)]) == 1
    assert measure_build.capsule_of([R(0), R(1)]) is None
    assert measure_build.capsule_of([R(None), R(None)], 1) == 1
    assert measure_build.capsule_of([R(None), R(0)], 0) == 0
    assert measure_build.capsule_of([R(None), R(1)], 0) is None
    assert measure_build.capsule_of([], 0) is None
    assert measure_build.capsule_of(None, 0) is None


def test_the_partner_claim_never_decides_whether_to_draw():
    """It says what may be claimed, not whether the comparison is
    allowed: a column is a wire, not a microphone, and nothing in
    the graph can tell a two-capsule fixture from one coupler
    replugged."""
    claim = measure_build.partner_claim
    assert claim(True, True) == "level"
    assert claim(True, False) == "shape"
    assert claim(False, True) == "shape"
    assert claim(False, False) == "shape"
    assert claim(None, True) == "shape"


def test_a_distortion_figure_keeps_its_significant_digits():
    """Two decimals turned a real measurement into a rounding
    artifact: seven thousandths of a percent printed as "0.01%",
    which is what a reading forty per cent larger prints too. The
    scale spans five orders of magnitude and no fixed decimal count
    serves all of it."""
    w = measure_build.pct_word
    assert w(0.0080) == "0.0080"
    assert w(0.0065) == "0.0065"
    assert w(0.26) == "0.26"
    assert w(1.4) == "1.4"
    assert w(35.2) == "35"
    assert w(0.0) == "0"
    assert w(None) is None
    assert w(float("nan")) is None
    assert w(0.0080, digits=3) == "0.00800"
