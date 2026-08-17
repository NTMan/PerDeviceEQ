"""In-process tests for the MeasureSession single-take API (increment 4,
part 1 of the GTK wizard).

Same fake pw-* executables as the end-to-end CLI tests, but driven as a
library -- exactly what the wizard will do: construct a session, click a
"speaker" (take(channel)), watch the fan (takes_of/spread_db), throw a
bad take away (discard), finalize one result.json per channel. The CLI
contract itself is pinned by test_measure_run.py; this file pins the
API shapes and the accumulation semantics.
"""
import json
import os
import threading
from pathlib import Path

import numpy as np
import pytest

from perdeviceeq.pde_audit import DEMO_PROFILE, chain_curve
from perdeviceeq import measure_session as ms

ROOT = Path(__file__).resolve().parent.parent
SHIMS = ROOT / "tests" / "shims"

# The minimal live graph for construction-time tests that resolve at
# birth without the shim PATH: an alsa sink with a Props volume block
# and an alsa source. Tests that need subprocess behavior request the
# shim_state fixture instead.
TWO_NODE_GRAPH = [
    {"id": 1, "type": "PipeWire:Interface:Node",
     "info": {"props": {"node.name": "test_sink",
                        "media.class": "Audio/Sink",
                        "device.api": "alsa"},
              "params": {"Props": [{
                  "channelVolumes": [0.074087, 0.074087],
                  "mute": False}]}}},
    {"id": 2, "type": "PipeWire:Interface:Node",
     "info": {"props": {"node.name": "test_source",
                        "media.class": "Audio/Source",
                        "device.api": "alsa"}}},
]
F_LO_CHECK, F_HI_CHECK = 40.0, 16000.0
TOL_DB = 0.5

# any non-trivial graph string; the session must treat it as opaque
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


def make_cfg(tmp_path, **kw):
    kw.setdefault("samples", 131072)      # 2.7 s sweep: enough for the
    return ms.SessionConfig(              # 0.5 dB closure, fast in CI
        sink="test_sink", source="test_source",
        save_dir=str(tmp_path / "takes"), **kw)


def assert_matches_chain(freqs, mag_db):
    ref = chain_curve(DEMO_PROFILE["channels"]["FL"], 48000,
                      np.asarray(freqs))
    band = (np.asarray(freqs) >= F_LO_CHECK) \
        & (np.asarray(freqs) <= F_HI_CHECK)
    err = np.asarray(mag_db, dtype=float)[band] - ref[band]
    err -= np.median(err)                 # absolute level is arbitrary
    assert np.max(np.abs(err)) < TOL_DB


# --- the fan lifecycle: take -> spread -> discard -> finalize --------------

def test_take_spread_discard_finalize(shim_state, tmp_path):
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with ses:
        out1 = ses.take(0)
        assert out1.kind == "take"
        assert out1.take.id == 1
        assert out1.take.channel == 0
        assert out1.spread_db is None                 # one take, no fan yet
        assert out1.take.clipped == 0
        assert out1.take.repaired == 0
        # shim delay (~800 ms) + the wav's own 1.0 s pre-silence
        assert 1700.0 < out1.take.delay_ms < 1900.0
        assert os.path.basename(out1.take.wav_path) == "take01.wav"
        assert os.path.exists(out1.take.wav_path)
        assert_matches_chain(out1.take.freq_hz, out1.take.mag_db)
        assert ses.path_clean["verified"] is True
        assert ses.path_clean["capture"]["verified"] is True

        out2 = ses.take(0)
        assert out2.take.id == 2
        assert abs(out2.take.delay_ms - out1.take.delay_ms) < 2.0
        spread = out2.spread_db
        assert spread is not None and len(spread) == len(ses.freqs)
        assert np.all(np.isfinite(spread))
        assert float(np.max(spread)) < 0.5            # synthetic: tiny fan

        # the profile was bypassed DURING the sound
        snap = json.loads((shim_state / "meta_at_play_1.json").read_text())
        assert "test_sink" not in snap

        dropped = ses.discard(0, out1.take.id)
        assert dropped.id == 1
        assert [r.id for r in ses.takes_of(0)] == [2]
        assert ses.spread_db(0) is None               # fan collapsed
        assert os.path.exists(dropped.wav_path)       # evidence stays

        out3 = ses.take(0)
        assert out3.take.id == 3                      # ids never reused
        assert os.path.basename(out3.take.wav_path) == "take03.wav"
        assert out3.spread_db is not None

    # bypass restored verbatim on exit
    assert json.loads((shim_state / "metadata.json").read_text()) \
        == {"test_sink": GRAPH}
    assert ses.eq_state == {"metadata_key": "test_sink", "profile": GRAPH,
                            "profile_source": "metadata", "bypass": True,
                            "restored": True}

    out_json = tmp_path / "result.json"
    r = ses.finalize(0, str(out_json))
    assert out_json.exists()
    assert r["schema"] == "pde-measurement"
    assert r["takes"]["count"] == 2                   # takes 2 and 3
    assert len(r["levels"]["capture_peak_dbfs"]) == 2
    assert r["levels"]["sink_volume"] == pytest.approx(0.30, abs=1e-3)
    assert r["levels"]["auto_level"]["enabled"] is False
    assert_matches_chain(r["data"]["freq_hz"], r["data"]["mag_db_raw"])
    # the sink volume was never written
    assert not (shim_state / "volume_log.json").exists()


# --- one session, both ears: the analyzed channel is a take argument -------

def test_two_channels_accumulate_side_by_side(shim_state, tmp_path):
    ses = ms.MeasureSession(make_cfg(tmp_path, channels=2))
    with ses:
        left = ses.take(0)
        right = ses.take(1)
    assert [r.id for r in ses.takes_of(0)] == [left.take.id] == [1]
    assert [r.id for r in ses.takes_of(1)] == [right.take.id] == [2]
    out_l = tmp_path / "left.json"
    out_r = tmp_path / "right.json"
    rl = ses.finalize(0, str(out_l))
    rr = ses.finalize(1, str(out_r))
    assert out_l.exists() and out_r.exists()
    for r in (rl, rr):
        assert r["takes"]["count"] == 1
        assert len(r["levels"]["capture_peak_dbfs"]) == 1
        assert_matches_chain(r["data"]["freq_hz"], r["data"]["mag_db_raw"])


# --- auto-level: probes move the volume and are not accumulated ------------



def test_foreign_stream_refuses_in_the_constructor(shim_state, tmp_path,
                                                   monkeypatch):
    monkeypatch.setenv("PDE_SHIM_FOREIGN", "1")
    with pytest.raises(ms.RefusalError, match="firefox"):
        ms.MeasureSession(make_cfg(tmp_path))
    assert not (shim_state / "played.json").exists()  # nothing played
    assert json.loads((shim_state / "metadata.json").read_text()) \
        == {"test_sink": GRAPH}                       # bypass never engaged

    monkeypatch.setenv("PDE_SHIM_FOREIGN", "0")
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with pytest.raises(ms.MeasureError, match="not entered"):
        ses.take(0)                                   # guard: no `with` yet
    assert not (shim_state / "played.json").exists()


# --- take quality classification: the single source of truth ---------------

def _q_rec(clipped=0, peak_dbfs=-10.0, snr_db=50.0, repaired=0):
    return ms.TakeRecord(id=1, channel=0, freq_hz=None, mag_db=None,
                         delay_ms=0.0, snr_db=snr_db, peak_dbfs=peak_dbfs,
                         clipped=clipped, repaired=repaired, wav_path="x")


def test_take_quality_thresholds():
    from perdeviceeq import measure_core as mc
    assert ms.take_quality(_q_rec()) == ms.TAKE_CLEAN
    # clipping is unusable, and wins over everything else
    assert ms.take_quality(_q_rec(clipped=3)) == ms.TAKE_CLIPPED
    assert ms.take_quality(
        _q_rec(clipped=3, peak_dbfs=0.0, snr_db=1.0)) == ms.TAKE_CLIPPED
    # a hot peak (at or above HOT_DBFS) is flagged, not clean
    assert ms.take_quality(_q_rec(peak_dbfs=ms.HOT_DBFS)) == ms.TAKE_FLAGGED
    assert ms.take_quality(
        _q_rec(peak_dbfs=ms.HOT_DBFS + 0.5)) == ms.TAKE_FLAGGED
    assert ms.take_quality(
        _q_rec(peak_dbfs=ms.HOT_DBFS - 0.5)) == ms.TAKE_CLEAN
    # low SNR is flagged
    assert ms.take_quality(
        _q_rec(snr_db=mc.SNR_WARN_DB - 1.0)) == ms.TAKE_FLAGGED
    assert ms.take_quality(_q_rec(snr_db=mc.SNR_WARN_DB)) == ms.TAKE_CLEAN
    # NO SNR AT ALL IS NOT A TAKE. This court used to pin the opposite
    # -- None meant "unknown, and unknown is not low, so clean" -- and
    # the field showed what that costs: a sweep aimed at a column
    # nothing came back on produced a flat line at -240 dBFS wearing a
    # green dot and counting toward three clean takes. Unknown is not
    # the same as fine.
    assert ms.take_quality(_q_rec(snr_db=None)) == ms.TAKE_SILENT
    assert ms.take_quality(
        _q_rec(snr_db=float("-inf"))) == ms.TAKE_SILENT
    # and silence wins over every other complaint: there is nothing
    # there to be hot or clipped about
    assert ms.take_quality(
        _q_rec(snr_db=None, clipped=3, peak_dbfs=0.0)) == ms.TAKE_SILENT
    # a repaired single-sample glitch stays clean
    assert ms.take_quality(_q_rec(repaired=1)) == ms.TAKE_CLEAN


# --- finalize(cal=): per-channel calibration override ----------------------

def test_finalize_cal_override_per_channel(shim_state, tmp_path):
    import numpy as np
    flat = tmp_path / "flat.txt"
    flat.write_text("20 0.0\n1000 0.0\n20000 0.0\n")
    tilt = tmp_path / "tilt.txt"                 # -6 dB by 20 kHz
    tilt.write_text("20 0.0\n1000 -3.0\n20000 -6.0\n")
    ses = ms.MeasureSession(make_cfg(tmp_path, cal=str(flat)))
    with ses:
        ses.take(0)
    # explicit override wins over cfg.cal
    r_flat = ses.finalize(0, str(tmp_path / "f.json"), cal=str(flat))
    r_tilt = ses.finalize(0, str(tmp_path / "t.json"), cal=str(tilt))
    assert os.path.basename(r_flat["cal_file"]) == "flat.txt"
    assert os.path.basename(r_tilt["cal_file"]) == "tilt.txt"
    # same capture, different cal subtracted -> raw curves differ, and the
    # pre-cal magnitude is identical (only the cal application changed)
    raw_flat = np.asarray(r_flat["data"]["mag_db_raw"], dtype=float)
    raw_tilt = np.asarray(r_tilt["data"]["mag_db_raw"], dtype=float)
    assert float(np.max(np.abs(raw_flat - raw_tilt))) > 2.0
    unc_flat = np.asarray(r_flat["data"]["mag_db_uncal"], dtype=float)
    unc_tilt = np.asarray(r_tilt["data"]["mag_db_uncal"], dtype=float)
    assert float(np.max(np.abs(unc_flat - unc_tilt))) < 1e-9
    # no cal argument falls back to the session's cfg.cal (flat here)
    r_default = ses.finalize(0, str(tmp_path / "d.json"))
    assert os.path.basename(r_default["cal_file"]) == "flat.txt"


# --- start_volume (apply a remembered level) and relevel() -----------------

def test_start_volume_is_what_sweeps_play_at(shim_state, tmp_path):
    ses = ms.MeasureSession(make_cfg(tmp_path, start_volume=0.5))
    with ses:
        assert ses._v_cur == pytest.approx(0.5)



def test_take_analyze_column_decoupled(shim_state, tmp_path):
    # analyze capture column 1 but store the take under profile channel 0
    ses = ms.MeasureSession(make_cfg(tmp_path, channels=2))
    with ses:
        out = ses.take(0, analyze=1)
        assert out.kind == "take"
        assert len(ses.takes_of(0)) == 1
        assert ses.takes_of(1) == []


# --- Stop: the sweep is interruptible ------------------------------------

def test_run_take_cancelled_raises_and_stores_nothing(shim_state, tmp_path):
    """A cancel set before the sweep makes run_take raise MeasureCancelled
    and kill its children; the session captured nothing."""
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with ses:
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(ms.MeasureCancelled):
            ms.run_take(ses.sink, ses.source, ses.wav, ses.wav_duration,
                        ses.cfg.channels, ses.sweep.fs, verify=False,
                        cancel=cancel)
        assert list(ses.takes_of(0)) == []    # nothing stored


def test_cancel_flag_is_cleared_at_each_take(shim_state, tmp_path):
    """cancel() while idle must not abort the next sweep: take() clears the
    flag as it starts, so a stray Stop is harmless."""
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with ses:
        ses.cancel()                          # stray Stop, nothing playing
        out = ses.take(0)                     # must still play and capture
        assert out.kind == "take"
        assert [r.id for r in ses.takes_of(0)] == [1]

# --- level moves between takes: recorded, compensated, reported ------------

def test_gain_comp_factors_policy():
    """Align onto the quietest gain, downward only; any unknown or
    unusable gain disables the whole set (no guessing)."""
    f = ms.gain_comp_factors([0.5, 1.0, 0.25])
    assert f == [pytest.approx(0.5), pytest.approx(0.25), 1.0]
    assert ms.gain_comp_factors([0.3, 0.3]) == [1.0, 1.0]
    assert ms.gain_comp_factors([]) is None
    assert ms.gain_comp_factors([0.5, None]) is None
    assert ms.gain_comp_factors([0.5, 0.0]) is None
    assert ms.gain_comp_factors([0.5, -1.0]) is None
    assert ms.gain_comp_factors([0.5, float("nan")]) is None


def test_sink_applied_volumes_reads_props():
    dump = [{"id": 7, "type": "PipeWire:Interface:Node",
             "info": {"props": {"media.class": "Audio/Sink"},
                      "params": {"Props": [
                          {"mute": False,
                           "channelVolumes": [0.064, 0.064],
                           "softVolumes": [1.0, 1.0]}]}}}]
    cv, sv = ms.sink_applied_volumes(dump, 7)
    assert cv == [0.064, 0.064]
    assert sv == [1.0, 1.0]                   # hardware-volume shape
    assert ms.sink_applied_volumes(dump, 8) == ([], [])


def test_level_move_between_takes_is_compensated(shim_state, tmp_path):
    """The stop-crane between takes of one channel used to smear the
    mean and widen the corridor by pure bookkeeping; with the applied
    gains recorded per take, the known move is removed exactly and the
    result sits at the quietest take's level, saying so in `levels`."""
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with ses:
        out1 = ses.take(0)
        assert out1.take.chan_vol == pytest.approx(0.027, rel=1e-3)
        assert out1.take.soft_vol == pytest.approx(0.027, rel=1e-3)
        ses.set_level(0.60)                   # the manual stop-crane
        out2 = ses.take(0)
        assert out2.take.soft_vol == pytest.approx(0.216, rel=1e-3)
        # +18 dB of bookkeeping is gone from the fan and the mean
        assert float(np.max(out2.spread_db)) < 0.5
        avg, sp = ses.average_and_spread(0)
        assert float(np.max(sp)) < 0.5
        shifts = ses.comp_shift_db(0)
        assert shifts[0] == pytest.approx(0.0, abs=1e-9)
        assert shifts[1] == pytest.approx(-18.062, abs=0.05)
        # one unknown gain disables compensation for the channel
        rec2 = ses._takes[0][1][0]
        rec2.soft_vol = None
        assert ses.comp_shift_db(0) is None
        assert float(np.max(ses.spread_db(0))) > 10.0
        rec2.soft_vol = 0.216
    r = ses.finalize(0, str(tmp_path / "result.json"))
    lv = r["levels"]
    assert lv["take_soft_volumes"] == [pytest.approx(0.027, rel=1e-3),
                                       pytest.approx(0.216, rel=1e-3)]
    assert lv["take_channel_volumes"] == lv["take_soft_volumes"]
    assert lv["gain_comp_db"][0] == pytest.approx(0.0, abs=1e-6)
    assert lv["gain_comp_db"][1] == pytest.approx(-18.062, abs=0.05)
    # the reference is the quietest take: its cubic, not the last level
    assert lv["sink_volume"] == pytest.approx(0.30, abs=1e-3)
    med_r = float(np.median(np.asarray(r["data"]["mag_db_raw"])))
    med_1 = float(np.median(np.asarray(out1.take.mag_db)))
    assert abs(med_r - med_1) < 0.5
    assert_matches_chain(r["data"]["freq_hz"], r["data"]["mag_db_raw"])

# --- SNR-targeted leveling: verdicts, ceiling prediction, refusal ----------




def _inject_pair(ses, ch, delta, lo_hz=0.0, hi_hz=1e9, base_id=1):
    """Two takes at one gain whose curves differ by `delta` dB inside
    [lo_hz, hi_hz) and agree elsewhere."""
    f = np.asarray(ses.freqs, float)
    m1 = np.zeros_like(f)
    m2 = m1.copy()
    m2[(f >= lo_hz) & (f < hi_hz)] += delta
    r1 = ms.TakeRecord(base_id, ch, ses.freqs, m1, 5.0, 50.0, -6.0,
                       0, 0, "a", chan_vol=0.1, soft_vol=0.1)
    r2 = ms.TakeRecord(base_id + 1, ch, ses.freqs, m2, 5.0, 50.0,
                       -6.0, 0, 0, "b", chan_vol=0.1, soft_vol=0.1)
    ses._takes[ch] = [(r1, None), (r2, None)]


def test_trusted_ceiling_follows_the_statistics(shim_state, tmp_path):
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with ses:
        f_top = float(np.asarray(ses.freqs)[-1])
        assert ses.trusted_ceiling_hz() is None       # no statistics
        # identical takes: the whole band is trusted
        _inject_pair(ses, 0, 0.0)
        assert ses.trusted_ceiling_hz() == pytest.approx(f_top)
        # an HF cliff pulls the ceiling to its edge exactly
        _inject_pair(ses, 0, 8.0, lo_hz=12000.0)      # spread ~5.7 dB
        c = ses.trusted_ceiling_hz()
        assert 11000.0 <= c <= 12000.0
        # a red island mid-band is the strip's business, not the
        # ceiling's
        _inject_pair(ses, 0, 8.0, lo_hz=500.0, hi_hz=700.0)
        assert ses.trusted_ceiling_hz() == pytest.approx(f_top)
        # the ceiling is the min across measured channels
        _inject_pair(ses, 0, 0.0)
        _inject_pair(ses, 1, 8.0, lo_hz=9000.0, base_id=3)
        c = ses.trusted_ceiling_hz()
        assert 8000.0 <= c <= 9000.0


def _inject_takes(ses, ch, deltas, lo_hz, base_id=0):
    """Takes at one gain: curve k is flat plus deltas[k] above lo_hz."""
    f = np.asarray(ses.freqs, float)
    entries = []
    for k, d in enumerate(deltas):
        m = np.zeros_like(f)
        m[f >= lo_hz] += d
        entries.append((ms.TakeRecord(base_id + k + 1, ch, ses.freqs,
                                      m, 5.0,
                                      50.0, -6.0, 0, 0, str(k),
                                      chan_vol=0.1, soft_vol=0.1),
                        None))
    ses._takes[ch] = entries


def test_trust_is_earned_not_diluted(shim_state, tmp_path):
    """The observed bounce: two takes 4.5 dB apart in the highs pull
    the ceiling down, a third agreeing take SHRINKS the sample std
    under the threshold and the point estimate would trust again.
    Under the confidence bound the ceiling stays down at three,
    stays down at four, is earned back at five agreeing takes -- or
    instantly by deleting the outlier."""
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with ses:
        f_top = float(np.asarray(ses.freqs)[-1])
        seq = [0.0, 4.5, 0.2, 0.1, 0.15]
        for n in (2, 3, 4):
            _inject_takes(ses, 0, seq[:n], lo_hz=15000.0)
            assert ses.trusted_ceiling_hz() < 15000.0, n
        _inject_takes(ses, 0, seq, lo_hz=15000.0)         # five takes
        assert ses.trusted_ceiling_hz() == pytest.approx(f_top)
        # deleting the outlier restores trust immediately
        _inject_takes(ses, 0, [0.0, 0.2], lo_hz=15000.0)
        assert ses.trusted_ceiling_hz() == pytest.approx(f_top)


def test_spread_driver_flags_the_outlier_only(shim_state, tmp_path):
    """LOO: the take whose removal raises the ceiling gets flagged;
    evenly spread scatter flags nothing (deleting any one take fixes
    nothing); two takes are never judged; across channels the best
    single improvement wins."""
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with ses:
        # a pair cannot be judged
        _inject_takes(ses, 0, [0.0, 4.5], lo_hz=15000.0)
        assert ses.spread_driver() is None
        # the bounce sample: the 4.5 take is the driver; the win is
        # the ~0.4 octaves above 15 kHz
        _inject_takes(ses, 0, [0.0, 4.5, 0.2], lo_hz=15000.0)
        drv = ses.spread_driver()
        assert drv is not None and drv[0] == 2
        assert 0.3 < drv[1] < 0.6
        # even scatter: no single removal clears the threshold
        _inject_takes(ses, 0, [0.0, 6.0, 12.0], lo_hz=15000.0)
        assert ses.spread_driver() is None
        # two channels: the outlier is found in the right one
        _inject_takes(ses, 0, [0.0, 0.05, 0.1], lo_hz=15000.0)
        _inject_takes(ses, 1, [0.0, 4.5, 0.2], lo_hz=12000.0,
                      base_id=10)
        drv = ses.spread_driver()
        assert drv is not None and drv[0] == 12
        assert 0.6 < drv[1] < 0.9


def test_driver_judges_bandwidth_not_the_ceiling(shim_state, tmp_path):
    """The field case: a seal-leak take poisons the bass while the
    ceiling stays pinned by HF scatter present in EVERY take. A
    ceiling-only verdict stayed silent; the bandwidth verdict must
    flag the leak with the octaves it holds hostage."""
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with ses:
        f = np.asarray(ses.freqs, float)
        hf = [0.0, 8.0, 4.0]         # pairwise-red above 16k, all takes
        leak = [0.0, -8.0, 0.0]      # take 2 alone loses the bass
        entries = []
        for k in range(3):
            m = np.zeros_like(f)
            m[f >= 16000.0] += hf[k]
            m[f <= 700.0] += leak[k]
            entries.append((ms.TakeRecord(k + 1, 0, ses.freqs, m,
                                          5.0, 50.0, -6.0, 0, 0,
                                          str(k), chan_vol=0.1,
                                          soft_vol=0.1), None))
        ses._takes[0] = entries
        assert ses.trusted_ceiling_hz() < 16000.0   # pinned by HF
        drv = ses.spread_driver()
        assert drv is not None and drv[0] == 2
        assert drv[1] > 4.0          # ~5 octaves of bass won back


def test_trusted_floor_follows_the_statistics(shim_state, tmp_path):
    """Mirror of the ceiling: a bass cliff (leaky seal every other
    take) lifts the floor to its edge; a mid-band island does not."""
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with ses:
        f0 = float(np.asarray(ses.freqs)[0])
        assert ses.trusted_floor_hz() is None      # no statistics
        _inject_pair(ses, 0, 0.0)
        assert ses.trusted_floor_hz() == pytest.approx(f0)
        _inject_pair(ses, 0, 8.0, lo_hz=0.0, hi_hz=700.0)
        fl = ses.trusted_floor_hz()
        assert 700.0 <= fl <= 780.0
        # a red island mid-band is the strip's business, not the
        # floor's
        _inject_pair(ses, 0, 8.0, lo_hz=2000.0, hi_hz=3000.0)
        assert ses.trusted_floor_hz() == pytest.approx(f0)

def test_mirror_key_pairs():
    assert ms.mirror_key("FL") == "FR"
    assert ms.mirror_key("SR") == "SL"
    assert ms.mirror_key("RL") == "RR"
    assert ms.mirror_key("FC") is None
    assert ms.mirror_key("LFE") is None
    assert ms.mirror_key("MONO") is None


def test_drive_shift_mirrors_the_trim_gate(shim_state, tmp_path):
    """The ghost's level accounting must follow balance_trims: exact
    on software volume, zero on one shared hardware volume, refused
    when the difference is unknowable."""
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with ses:
        f = np.asarray(ses.freqs, float)

        def put(ch, soft, chan, base_id):
            m = np.zeros_like(f)
            ses._takes[ch] = [
                (ms.TakeRecord(base_id + i, ch, ses.freqs, m, 5.0,
                               50.0, -6.0, 0, 0, str(i),
                               chan_vol=chan[i], soft_vol=soft[i]),
                 None) for i in range(len(soft))]

        # software volume, different drives: exact dB
        put(0, [0.064, 0.064], [0.064, 0.064], 1)
        put(1, [0.0689, 0.0689], [0.0689, 0.0689], 10)
        d = ses.drive_shift_db(1, 0)
        assert d == pytest.approx(20 * np.log10(0.064 / 0.0689),
                                  abs=1e-6)
        # one shared hardware volume: the law cancels, shift is zero
        put(0, [1.0, 1.0], [0.216, 0.216], 1)
        put(1, [1.0, 1.0], [0.216, 0.216], 10)
        assert ses.drive_shift_db(1, 0) == pytest.approx(0.0)
        # hardware volume at different positions: unknowable
        put(0, [1.0, 1.0], [0.216, 0.216], 1)
        put(1, [1.0, 1.0], [0.064, 0.064], 10)
        assert ses.drive_shift_db(1, 0) is None
        # a take without recorded gains disables the ghost
        put(0, [None, 0.064], [0.064, 0.064], 1)
        put(1, [0.064, 0.064], [0.064, 0.064], 10)
        assert ses.drive_shift_db(1, 0) is None
        # an unmeasured partner has nothing to show
        ses._takes.pop(1)
        assert ses.drive_shift_db(1, 0) is None


def test_meas_volume_reports_a_real_write(shim_state, tmp_path,
                                          monkeypatch):
    """The BT warm-up keys off whether the sink volume was actually
    written; a no-op toggle must not trigger it, a bluez sink with a
    fresh change must."""
    ses = ms.MeasureSession(make_cfg(tmp_path, start_volume=0.6))
    with ses:
        ses.volume_start = 0.6
        ses._v_cur = 0.6
        assert ses._meas_volume_arg() is None
        ses._v_cur = 0.4
        assert ses._meas_volume_arg() == 0.4
        # the warm-up itself is best-effort and bluez-gated in take();
        # calling it directly must never raise even without pw-play
        monkeypatch.setenv("PATH", str(tmp_path))
        ses._warm_sink()


def test_ephemeral_outdir_wiped_and_takes_stamped(shim_state, tmp_path):
    """No save_dir: the working dir is a throwaway tempdir, gone with
    the session; each accepted take is stamped with the analyze column
    and its UTC time (the canvas needs both)."""
    cfg = ms.SessionConfig(sink="test_sink", source="test_source",
                           channels=2, samples=131072)
    ses = ms.MeasureSession(cfg)
    assert ses.outdir is None            # nothing until __enter__
    with ses:
        out = ses.outdir
        assert out and os.path.isdir(out)
        ses.take(0)
        ses.take(1, analyze=0)           # right cup on the left mic
        assert (Path(out) / "take01.wav").exists()
        r0 = ses.takes_of(0)[0]
        r1 = ses.takes_of(1)[0]
        assert r0.capture_channel == 0
        assert r1.capture_channel == 0   # the analyze column, not 1
        assert r0.created_utc and "T" in r0.created_utc
    assert not os.path.exists(out)       # wiped with the session
    assert ses.outdir is None and ses.wav is None


def test_adopt_take_joins_the_statistics(shim_state, tmp_path):
    """A canvas take adopted into a fresh session behaves like one
    of its own -- listed, counted, spread-judged, discardable -- but
    finalize() refuses the channel: adopted takes carry magnitudes,
    not samples."""
    cfg = ms.SessionConfig(sink="test_sink", source="test_source",
                           channels=2, samples=131072,
                           save_dir=str(tmp_path / "takes"))
    ses = ms.MeasureSession(cfg)
    with ses:
        ses.take(0)
        live = ses.takes_of(0)[-1]
        ghost = ms.TakeRecord(
            "abc123def456", 0, live.freq_hz,
            [float(v) for v in live.mag_db],
            5.0, 45.0, -6.0, 0, 0, None,
            chan_vol=live.chan_vol, soft_vol=live.soft_vol,
            noise_dbfs=-80.0, capture_channel=0,
            created_utc="2026-07-10T00:00:00+00:00")
        ses.adopt_take(0, ghost)
        recs = ses.takes_of(0)
        assert len(recs) == 2 and recs[-1].id == "abc123def456"
        assert ms.take_quality(ghost) == ms.TAKE_CLEAN
        mean, spread = ses.average_and_spread(0)
        assert mean is not None and spread is not None
        with pytest.raises(ms.MeasureError, match="adopted"):
            ses.finalize(0)
        ses.discard(0, "abc123def456")
        assert len(ses.takes_of(0)) == 1
        ses.finalize(0)                  # pure live takes: fine again


def test_offline_birth_carries_the_canvas(shim_state, tmp_path):
    """A session for an absent sink constructs unresolved (the
    gone Measure window is livable -- field verdict): adoption,
    statistics and discard all work; __enter__ consults the
    graph and refuses while the sink is still away."""
    cfg = ms.SessionConfig(sink="no_such_sink",
                           source="test_source", channels=1,
                           samples=131072,
                           save_dir=str(tmp_path / "takes"))
    ses = ms.MeasureSession(cfg, resolve=False)
    assert ses.sink is None and ses.volume_start is None
    assert ses.sink_ident["name"] == "no_such_sink"
    freqs = ms.mc.log_grid()
    ghost = ms.TakeRecord(
        "abc123def456", 0, freqs,
        [0.0 for _ in freqs],
        5.0, 45.0, -6.0, 0, 0, None,
        noise_dbfs=-80.0, capture_channel=0,
        created_utc="2026-07-10T00:00:00+00:00")
    ses.adopt_take(0, ghost)
    assert len(ses.takes_of(0)) == 1
    mean, spread = ses.average_and_spread(0)
    assert mean is not None
    with pytest.raises(ms.RefusalError):
        ses.__enter__()
    ses.discard(0, "abc123def456")
    assert ses.takes_of(0) == []


def test_enter_resolves_a_deferred_birth(shim_state, tmp_path):
    """resolve=False defers the graph to __enter__: the live
    preconditions run FRESH at arming and the session fills its
    identities there."""
    ses = ms.MeasureSession(make_cfg(tmp_path), resolve=False)
    assert ses.sink is None and not ses._resolved
    with ses:
        assert ses._resolved and ses.sink is not None
        assert ses.sink_ident["name"] == "test_sink"
        assert ses.volume_start is not None


def test_take_fails_honestly_when_an_end_left(shim_state, tmp_path,
                                              monkeypatch):
    """Ids are recycled addresses: a take must re-resolve both
    ends by name from a FRESH dump and refuse plainly when one
    is absent -- never spawn against an id resolved at enter
    time (the field caught a webcam wearing the mic's recycled
    number)."""
    ses = ms.MeasureSession(make_cfg(tmp_path))
    with ses:
        full = ms.pw_dump()
        no_mic = [o for o in full
                  if (o.get("info", {}).get("props", {})
                      .get("node.name")) != "test_source"]
        monkeypatch.setattr(ms, "pw_dump", lambda: no_mic)
        with pytest.raises(ms.MeasureError,
                           match="failed to open"):
            ses.take(0)
        no_sink = [o for o in full
                   if (o.get("info", {}).get("props", {})
                       .get("node.name")) != "test_sink"]
        monkeypatch.setattr(ms, "pw_dump", lambda: no_sink)
        with pytest.raises(ms.MeasureError,
                           match="failed to open"):
            ses.take(0)


def test_debug_env_keeps_the_raw(tmp_path, monkeypatch):
    """PDEQ_DEBUG_RAW joins two organs that always existed --
    save_dir (persistent session dir, never reaped) and
    raw_capture_dump (multichannel raw%02d.wav per take) -- so
    an investigation can receive the raw material without
    stuffing megabytes of base64 into the profile. An explicit
    save_dir outranks the environment hand."""
    monkeypatch.setattr(ms, "pw_dump",
                        lambda: TWO_NODE_GRAPH)
    monkeypatch.setenv(ms.DEBUG_RAW_ENV, str(tmp_path / "dbg"))
    cfg = ms.SessionConfig(sink="test_sink",
                           source="test_source",
                           samples=131072)
    ses = ms.MeasureSession(cfg)
    assert not ses._ephemeral
    assert cfg.raw_capture_dump is True
    assert ses.outdir and str(tmp_path / "dbg") in ses.outdir

    own = ms.SessionConfig(sink="test_sink",
                           source="test_source",
                           samples=131072,
                           save_dir=str(tmp_path / "own"))
    ses2 = ms.MeasureSession(own)
    assert not ses2._ephemeral
    assert own.raw_capture_dump is False
    assert str(tmp_path / "own") in ses2.outdir


def test_await_sink_volume_reads_back(monkeypatch):
    """The settle is a readback, not a faith in transports: the
    field corpse was a take whose head played at the listening
    volume, our level ramping in 2.6 s later -- a 16 dB step
    mid-sweep. The await polls until channelVolumes land."""
    seq = [([0.9 ** 3, 0.9 ** 3], [1.0, 1.0]),
           ([0.9 ** 3, 0.9 ** 3], [1.0, 1.0]),
           ([0.614 ** 3, 0.614 ** 3], [1.0, 1.0])]
    calls = {"n": 0}

    def fake_dump():
        return "dump"

    def fake_applied(dump, sink_id):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]
    monkeypatch.setattr(ms, "pw_dump", fake_dump)
    monkeypatch.setattr(ms, "sink_applied_volumes",
                        fake_applied)
    assert ms.await_sink_volume(7, 0.614, timeout_s=2.0)
    assert calls["n"] == 3

    calls["n"] = 0
    seq[:] = [([0.9 ** 3, 0.9 ** 3], [1.0, 1.0])]
    assert not ms.await_sink_volume(7, 0.614, timeout_s=0.15)


def test_start_volume_outranks_sink_read(tmp_path, monkeypatch):
    """The explicit hand beats the environment: an unarmed
    session wears cfg.start_volume from birth, not the sink's
    current cubic; without the hand, the sink read stands."""
    monkeypatch.setattr(ms, "pw_dump",
                        lambda: TWO_NODE_GRAPH)
    cfg = ms.SessionConfig(sink="test_sink",
                           source="test_source",
                           samples=131072,
                           start_volume=0.42)
    ses = ms.MeasureSession(cfg)
    assert abs(ses._v_cur - 0.42) < 1e-9

    bare = ms.SessionConfig(sink="test_sink",
                            source="test_source",
                            samples=131072)
    ses2 = ms.MeasureSession(bare)
    assert ses2._v_cur == ses2.volume_start

def test_a_take_that_heard_nothing_never_testifies():
    """Nothing plugged in, the wrong port, a sweep that never left
    the sink: the capture still becomes a record, the delay
    detector locks onto noise and the magnitude comes out two
    hundred decibels down. That is not a bad measurement, it is the
    absence of one."""
    class R(object):
        def __init__(self, snr):
            self.snr_db = snr

    assert ms.testified(R(62.1)) is True
    assert ms.testified(R(0.0)) is True
    assert ms.testified(R(-12.0)) is True       # bad, but heard
    assert ms.testified(R(float("-inf"))) is False
    assert ms.testified(R(float("inf"))) is False
    assert ms.testified(R(float("nan"))) is False
    assert ms.testified(R(None)) is False
    assert ms.testified(object()) is False

def test_a_deaf_take_widens_nothing(monkeypatch):
    """The pink field: a capture with no onset used to enter the
    mean AND the spread, so the corridor opened to the width of the
    canvas and every real take counted as straying from it."""
    class Rec(object):
        def __init__(self, tid, mag, snr):
            self.id = tid
            self.mag_db = np.asarray(mag, float)
            self.snr_db = snr
            self.soft_vol = 1.0
            self.chan_vol = 1.0

    good1 = Rec("a", [0.0, 0.0, 0.0], 60.0)
    good2 = Rec("b", [1.0, 1.0, 1.0], 55.0)
    deaf = Rec("c", [-200.0, -200.0, -200.0], float("-inf"))
    ses = ms.MeasureSession.__new__(ms.MeasureSession)
    ses._takes = {0: [(good1, None), (good2, None), (deaf, None)]}
    ses._comp_factors = lambda entries: None
    mean, spread = ses.average_and_spread(0)
    # the mean is a POWER average of 0 and 1 dB, not 0.5
    assert 0.4 < float(np.mean(mean)) < 0.7
    assert float(np.max(spread)) < 1.0
    # and with nothing but the deaf take there is no mean at all
    ses._takes = {0: [(deaf, None)]}
    assert ses.average_and_spread(0) == (None, None)


# ---- the sweep is aimed through the pairing -------------------------------

def test_a_sweep_is_aimed_at_the_paired_output():
    """A target names a side of a transducer; a sink channel names a
    route. Which route carries which side is the binding's answer, and
    the sweep has to follow it -- on a card whose outputs are called
    AUX0..AUX9 the ring's own position is not an output index."""
    cfg = ms.SessionConfig(sink="s", source="m", channels=1,
                           play_map=(6, 7))
    ses = ms.MeasureSession.__new__(ms.MeasureSession)
    ses.cfg = cfg
    ses.sink_layout = ["AUX%d" % i for i in range(10)]
    assert ses._channel_map(0) == "AUX6"
    assert ses._channel_map(1) == "AUX7"


def test_without_a_pairing_the_old_index_for_index_stands():
    cfg = ms.SessionConfig(sink="s", source="m", channels=1)
    ses = ms.MeasureSession.__new__(ms.MeasureSession)
    ses.cfg = cfg
    ses.sink_layout = ["FL", "FR"]
    assert ses._channel_map(0) == "FL"
    assert ses._channel_map(1) == "FR"


def test_an_unpaired_target_has_no_output_to_aim_at():
    """None would play the plain mono sweep into EVERY channel, and the
    curve that came back would look like a measurement while being a
    mixture -- so the map says nothing and take() refuses."""
    cfg = ms.SessionConfig(sink="s", source="m", channels=1,
                           play_map=(0, None))
    ses = ms.MeasureSession.__new__(ms.MeasureSession)
    ses.cfg = cfg
    ses.sink_layout = ["FL", "FR"]
    assert ses._channel_map(0) == "FL"
    assert ses._channel_map(1) is None
    assert ses._sink_index(1) is None


def test_a_session_can_be_handed_the_graph_it_needs():
    """Building a session resolved used to spawn pw-dump from the
    constructor -- at every window open and again at every change of
    rig or pairing, on the main loop, between the click and the window.
    The caller usually holds that picture already."""
    import inspect
    sig = inspect.signature(ms.MeasureSession.__init__)
    assert "dump" in sig.parameters
    src = inspect.getsource(ms.MeasureSession.__init__)
    assert "dump if dump is not None else pw_dump()" in src


def test_a_take_that_heard_nothing_is_not_a_clean_take():
    """The bug he found in the field, as an assertion. A sweep aimed at
    a column nothing comes back on still produces a record: the
    deconvolution runs on noise and out comes a flat line at -240 dBFS.
    Every test in the judge asks whether something is WRONG, and
    nothing is wrong with silence -- so it came out clean, wore a green
    dot, and counted toward three clean takes."""
    assert ms.take_quality(_q_rec(snr_db=None)) == ms.TAKE_SILENT
    assert ms.take_quality(_q_rec(snr_db=float("-inf"))) == ms.TAKE_SILENT
    assert ms.take_quality(_q_rec(snr_db=float("nan"))) == ms.TAKE_SILENT
    # and it is not merely "not clean" -- it is its own verdict, so a
    # caller can say "no sweep heard" rather than "poor measurement"
    assert ms.TAKE_SILENT not in (ms.TAKE_CLEAN, ms.TAKE_FLAGGED,
                                  ms.TAKE_CLIPPED)
    # testified() has known this all along; the judge just never asked
    assert ms.testified(_q_rec(snr_db=None)) is False
    assert ms.testified(_q_rec(snr_db=40.0)) is True
