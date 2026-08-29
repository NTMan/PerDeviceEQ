"""Synthetic-loop tests for the measurement core (ROADMAP Task 3, incr. 1).

No audio hardware: the "room" is a known pde_audit biquad chain, the "mic" is
ideal. The loop must close within +-0.5 dB in 40 Hz - 16 kHz. If it does not,
fix the core, do not relax the tolerance: in synthetics there is no mic and
no room, only math.
"""
import json
import math

import numpy as np
import pytest

from perdeviceeq.pde_audit import (
    DEMO_PROFILE, apply_chain, chain_curve)
from perdeviceeq import measure_core as mc

FS = 48000
F_LO_CHECK, F_HI_CHECK = 40.0, 16000.0
TOL_DB = 0.5


@pytest.fixture(scope="module")
def sweep():
    return mc.generate_sweep()          # 256k @ 48k, -6 dBFS, 20-20k


def band(freqs):
    return (freqs >= F_LO_CHECK) & (freqs <= F_HI_CHECK)


def synth_recording(sweep, bands, delay_s=1.0, tail_s=0.5, noise_sigma=1e-3,
                    seed=0, k2=0.0):
    """Sweep through a known chain (+ optional x + k2*x**2 distortion at the
    'speaker') into a recording with leading silence and additive noise."""
    y = apply_chain(sweep.signal, bands, sweep.fs) if bands \
        else sweep.signal.copy()
    if k2:
        y = y + k2 * y * y
    rec = np.concatenate([np.zeros(int(delay_s * sweep.fs)), y,
                          np.zeros(int(tail_s * sweep.fs))])
    rec += noise_sigma * np.random.default_rng(seed).standard_normal(len(rec))
    return rec


def raw_curve(result):
    return (np.asarray(result["data"]["freq_hz"]),
            np.asarray(result["data"]["mag_db_raw"], dtype=float))


# --- excitation ---------------------------------------------------------

def test_sweep_level_length_and_fades(sweep):
    assert sweep.n_samples == mc.DEFAULT_N
    assert np.max(np.abs(sweep.signal)) == pytest.approx(10 ** (-6 / 20),
                                                         rel=1e-4)
    assert abs(sweep.signal[0]) < 1e-6 and abs(sweep.signal[-1]) < 1e-3


def test_inverse_sweep_flattens_the_sweep(sweep):
    inv = mc.inverse_sweep(sweep)
    m = 2 * sweep.n_samples
    d = np.fft.irfft(np.fft.rfft(sweep.signal, m) * np.fft.rfft(inv, m), m)
    assert np.max(np.abs(d)) == pytest.approx(1.0, rel=1e-6)
    f = np.fft.rfftfreq(m, 1 / sweep.fs)
    sel = (f >= 100) & (f <= 10000)
    db = 20 * np.log10(np.abs(np.fft.rfft(d, m))[sel])
    db -= np.median(db)                 # peak-normalization offsets the level
    assert np.max(np.abs(db)) < 0.1     # mid-band flat within +-0.1 dB


# --- the loop -----------------------------------------------------------

@pytest.mark.parametrize("ch", ["FL", "FR"])
def test_loop_recovers_chain_curve(sweep, ch):
    bands = DEMO_PROFILE["channels"][ch]
    rec = synth_recording(sweep, bands, seed=1)
    result = mc.process_takes([rec], sweep)
    freqs, mag = raw_curve(result)
    sel = band(freqs)
    ref = chain_curve(bands, FS, freqs[sel])
    err = np.abs(mag[sel] - ref)
    assert err.max() <= TOL_DB, f"max err {err.max():.3f} dB"
    assert not result["warnings"]


def test_harmonic_distortion_is_windowed_out(sweep):
    bands = DEMO_PROFILE["channels"]["FL"]
    clean = mc.process_takes([synth_recording(sweep, bands, seed=2)], sweep)
    dirty = mc.process_takes(
        [synth_recording(sweep, bands, seed=2, k2=0.05)], sweep)
    freqs, mag_c = raw_curve(clean)
    _, mag_d = raw_curve(dirty)
    sel = band(freqs)
    ref = chain_curve(bands, FS, freqs[sel])
    assert np.abs(mag_d[sel] - ref).max() <= TOL_DB
    # Farina windowing: the curve must not move when distortion is added.
    assert np.abs(mag_d[sel] - mag_c[sel]).max() <= 0.2


def test_three_takes_with_delay_scatter_converge(sweep):
    bands = DEMO_PROFILE["channels"]["FR"]
    recs = [synth_recording(sweep, bands, delay_s=d, seed=s)
            for d, s in [(0.96, 3), (1.00, 4), (1.04, 5)]]   # +-40 ms
    result = mc.process_takes(recs, sweep)
    freqs, mag = raw_curve(result)
    sel = band(freqs)
    ref = chain_curve(bands, FS, freqs[sel])
    assert np.abs(mag[sel] - ref).max() <= TOL_DB
    spread = np.asarray(result["data"]["spread_db"], dtype=float)
    assert spread[sel].max() < 0.3      # magnitude-only averaging converged
    # 80 ms peak-to-peak delay jitter must trip the ROADMAP BT warning.
    assert result["takes"]["delay_jitter_ms"] == pytest.approx(80.0, abs=1.0)
    # the transport is unknown here (offline processing): the warning
    # stays cautious about a possible wireless link
    assert any("wireless" in w for w in result["warnings"])


def test_stable_delays_do_not_warn(sweep):
    bands = DEMO_PROFILE["channels"]["FL"]
    recs = [synth_recording(sweep, bands, seed=s) for s in (6, 7)]
    result = mc.process_takes(recs, sweep)
    assert result["takes"]["delay_jitter_ms"] <= 1.0
    assert mc.BT_JITTER_WARNING not in result["warnings"]


# --- SNR ----------------------------------------------------------------

def test_low_snr_warns(sweep):
    bands = DEMO_PROFILE["channels"]["FL"]
    y = apply_chain(sweep.signal, bands, FS)
    sigma = float(np.sqrt(np.mean(y ** 2))) * 10 ** (-20 / 20)
    rec = synth_recording(sweep, bands, noise_sigma=sigma, seed=8)
    result = mc.process_takes([rec], sweep)
    assert result["takes"]["snr_min_db"] == pytest.approx(20.0, abs=3.0)
    assert any(w.startswith("low SNR") for w in result["warnings"])


def test_clean_loop_snr_is_sane(sweep):
    rec = synth_recording(sweep, DEMO_PROFILE["channels"]["FL"], seed=9)
    result = mc.process_takes([rec], sweep)
    assert result["takes"]["snr_min_db"] > mc.SNR_WARN_DB


def test_dc_and_drift_are_not_noise(sweep):
    """A capture parked off zero must not read as a noisy one.

    613 LSB of DC is -34.6 dBFS; counted broadband it swamps a floor
    that sits below -80 dBFS in the measured band.
    """
    bands = DEMO_PROFILE["channels"]["FL"]
    rec = synth_recording(sweep, bands, seed=10)
    clean = mc.process_takes([rec], sweep)["takes"]["snr_min_db"]
    t = np.arange(len(rec)) / FS
    stained = rec + 613 / 32768 + 3e-3 * np.sin(2 * np.pi * 1.5 * t)
    got = mc.process_takes([stained], sweep)["takes"]["snr_min_db"]
    assert got == pytest.approx(clean, abs=1.0)


# --- mic calibration ------------------------------------------------------

def test_mic_cal_parse_and_apply(sweep, tmp_path):
    cal = tmp_path / "umik.txt"
    cal.write_text('"Sens Factor =-.6383dB, SERNO: 7000000"\n'
                   "* comment line\n"
                   "20 0.0\n"
                   "1000 1.0 45.0\n"      # optional phase column
                   "20000 3.0\n")
    fr, db = mc.load_mic_cal(str(cal))
    assert list(fr) == [20.0, 1000.0, 20000.0]
    rec = synth_recording(sweep, [], seed=10)      # flat chain
    result = mc.process_takes([rec], sweep, cal=str(cal))
    freqs, mag = raw_curve(result)
    for f, expect in [(1000.0, -1.0), (100.0, -0.411)]:
        i = int(np.argmin(np.abs(freqs - f)))
        assert mag[i] == pytest.approx(expect, abs=0.1)
    assert result["cal_file"] == str(cal)


# --- result schema ----------------------------------------------------------

def test_result_schema_carries_increment2_stubs(sweep, tmp_path):
    rec = synth_recording(sweep, [], seed=11)
    result = mc.process_takes([rec], sweep, device="bt_sink.node.name")
    out = tmp_path / "m.json"
    mc.save_result(result, out)
    r = json.loads(out.read_text())
    assert r["schema"] == mc.SCHEMA and r["schema_version"] == 1
    assert r["device"] == "bt_sink.node.name"
    assert r["eq_profile_state"] == {"profile": None, "bypass": None}
    assert r["levels"]["sweep_level_dbfs"] == -6.0
    assert set(r["levels"]) == {"sink_volume", "stream_volume",
                                "sweep_level_dbfs"}
    assert r["path_clean"] == {"verified": None, "unknown_nodes": []}
    assert r["foreign_streams"] == []
    n = len(r["data"]["freq_hz"])
    assert n == len(r["data"]["mag_db_raw"]) == len(
        r["data"]["mag_db_smoothed"])
    assert r["data"]["spread_db"] is None          # single take
    assert r["takes"]["count"] == 1


def test_jitter_warning_gated_by_transport(sweep):
    """Start-time jitter between takes is pw-play spawn timing on a
    wired sink (each take aligns on its own impulse); the wireless
    wording must fire only for a bluez sink, stay cautious when the
    transport is unknown, and stay silent on known-wired."""
    bands = DEMO_PROFILE["channels"]["FL"]
    recs = [synth_recording(sweep, bands, delay_s=1.0, seed=1),
            synth_recording(sweep, bands, delay_s=1.02, seed=2)]

    def jitter_warns(r):
        return [w for w in r["warnings"] if "jitter" in w
                or "wireless" in w]

    bt = mc.process_takes(recs, sweep, sink_api="bluez5")
    assert bt["takes"]["delay_jitter_ms"] > mc.JITTER_WARN_MS
    assert any("wireless" in w for w in jitter_warns(bt))
    assert bt["sink_api"] == "bluez5"

    unknown = mc.process_takes(recs, sweep)
    assert any("wireless" in w for w in jitter_warns(unknown))

    wired = mc.process_takes(recs, sweep, sink_api="alsa")
    assert jitter_warns(wired) == []
    assert wired["sink_api"] == "alsa"


def test_the_sweep_confesses_known_harmonics():
    """The Farina confession, judged against analytic truth: a
    memoryless nonlinearity y = x + a*x^2 + b*x^3 on the house
    sweep must read back as H2 = 20log10(a*A/2) and
    H3 = 20log10(b*A^2/4) re the fundamental (A is the sweep
    amplitude), flat with frequency; a linear pass must read
    silence. The images sit in every take already -- this pins
    that the math finally reads them."""
    fs = 48000
    sweep = mc.generate_sweep(n_samples=2 * fs, fs=fs,
                              f_start=20.0, f_end=20000.0)
    a, b = 0.02, 0.04
    x = sweep.signal
    lead = np.zeros(fs // 2)
    y = np.concatenate([lead, x + a * x * x + b * x ** 3,
                        np.zeros(fs // 2)])
    freqs = mc.log_grid(50.0, 8000.0, 48)
    t = mc.analyze_take(y, sweep, freqs)
    A = 10.0 ** (sweep.level_dbfs / 20.0)
    exp2 = 20.0 * math.log10(a * A / 2.0)
    exp3 = 20.0 * math.log10(b * A * A / 4.0)
    band = (freqs >= 100.0) & (freqs <= 4000.0)
    h2 = np.asarray(t.h2_db, float)[band]
    h3 = np.asarray(t.h3_db, float)[band]
    assert np.all(np.isfinite(h2)) and np.all(np.isfinite(h3))
    assert abs(float(np.median(h2)) - exp2) < 0.7, (
        float(np.median(h2)), exp2)
    assert abs(float(np.median(h3)) - exp3) < 0.7, (
        float(np.median(h3)), exp3)
    assert float(np.max(np.abs(h2 - exp2))) < 1.5
    thd = np.asarray(t.thd_db, float)[band]
    ref = 10.0 * np.log10(10 ** (exp2 / 10.0)
                          + 10 ** (exp3 / 10.0))
    assert abs(float(np.median(thd)) - ref) < 0.8

    lin = np.concatenate([lead, x, np.zeros(fs // 2)])
    tl = mc.analyze_take(lin, sweep, freqs)
    h2l = np.asarray(tl.h2_db, float)[band]
    assert float(np.median(h2l)) < -70.0


def test_the_noise_floor_rides_the_confession():
    """The gray line law: a clean sweep in noise must read THD
    hugging its own floor (both are the same noise, measured
    the same way), and a truly distorted sweep must stand far
    above it. The field corpse: a quiet run whose mid-band THD
    stopped following the drive because it had landed on this
    floor, invisibly."""
    fs = 48000
    sweep = mc.generate_sweep(n_samples=2 * fs, fs=fs,
                              f_start=20.0, f_end=20000.0)
    rng = np.random.default_rng(11)
    x = sweep.signal
    # a realistic second of pre-silence: the session's own
    # pre_silence is 1.0 s, and the quiet land BEFORE the
    # earliest harmonic image is where the floor is read
    lead = np.zeros(fs)
    freqs = mc.log_grid(50.0, 8000.0, 48)
    band = (freqs >= 100.0) & (freqs <= 4000.0)

    lin = np.concatenate([lead, x, np.zeros(fs // 2)])
    lin = lin + rng.normal(0.0, 10 ** (-70.0 / 20.0),
                           len(lin))
    tl = mc.analyze_take(lin, sweep, freqs)
    thd = np.asarray(tl.thd_db, float)[band]
    nfl = np.asarray(tl.thd_noise_db, float)[band]
    assert np.all(np.isfinite(nfl))
    assert abs(float(np.median(thd - nfl))) < 4.0

    a, b = 0.02, 0.04
    y = np.concatenate([lead, x + a * x * x + b * x ** 3,
                        np.zeros(fs // 2)])
    y = y + rng.normal(0.0, 10 ** (-70.0 / 20.0), len(y))
    td = mc.analyze_take(y, sweep, freqs)
    gap = (np.asarray(td.thd_db, float)
           - np.asarray(td.thd_noise_db, float))[band]
    assert float(np.median(gap)) > 15.0


# --- what the sweep never asked for ---------------------------------

def test_a_clean_rig_returns_nothing_it_was_not_given():
    """A sweep plays ONE frequency at a time, so anything coming back
    at that moment on some other frequency was not asked for."""
    sw = mc.generate_sweep(mc.DEFAULT_N, 48000, 20.0, 20000.0)
    freqs = mc.log_grid()
    rng = np.random.default_rng(4)
    pre = np.zeros(int(1.1 * sw.fs))
    rec = np.concatenate([pre, sw.signal, np.zeros(int(0.5 * sw.fs))])
    rec = rec + rng.normal(0, 1e-6, rec.size)
    a = mc.unasked_return(rec, sw, freqs)
    assert a is not None
    v = np.asarray(a, float)
    got = v[np.isfinite(v)]
    assert got.size > 10
    assert float(np.nanmedian(got)) < -40.0, float(np.nanmedian(got))


def test_a_rig_that_returns_what_it_was_not_given_is_caught():
    """HIS PAIR is why this exists: harmonics could not tell his Adam
    D3V from his iLoud Micro Monitor -- the Adams show MORE of them --
    and this separates them by 29 dB from a single recording. Here the
    same thing synthetically: a rig that answers a low drive with a
    band it was never given."""
    sw = mc.generate_sweep(mc.DEFAULT_N, 48000, 20.0, 20000.0)
    freqs = mc.log_grid()
    rng = np.random.default_rng(5)
    t = np.arange(sw.n_samples) / sw.fs
    f_inst = sw.f_start * np.exp(t / sw.sweep_rate_l)
    noise = rng.normal(0, 1.0, sw.n_samples)
    X = np.fft.rfft(noise)
    ff = np.fft.rfftfreq(noise.size, 1.0 / sw.fs)
    X[(ff < 400) | (ff > 800)] = 0
    hiss = np.fft.irfft(X, noise.size)
    hiss *= 0.02 / (np.std(hiss) + 1e-12)
    dirty = sw.signal + (f_inst < 60.0) * hiss
    pre = np.zeros(int(1.1 * sw.fs))
    post = np.zeros(int(0.5 * sw.fs))
    clean = mc.unasked_return(
        np.concatenate([pre, sw.signal, post]), sw, freqs)
    got = mc.unasked_return(
        np.concatenate([pre, dirty, post]), sw, freqs)
    f = np.asarray(freqs)
    low = (f > 25) & (f < 55)
    a = float(np.nanmedian(np.asarray(clean, float)[low]))
    b = float(np.nanmedian(np.asarray(got, float)[low]))
    assert b - a > 15.0, (a, b)


def test_it_corrects_for_the_band_the_mask_eats():
    """The asked-for mask grows with the drive: with eleven orders
    masked, a drive of 73 Hz covers 400-800 entirely and the residue
    would read as silence. The share still free is divided out, and
    the reading abstains when too little of the band is left."""
    sw = mc.generate_sweep(mc.DEFAULT_N, 48000, 20.0, 20000.0)
    freqs = mc.log_grid()
    pre = np.zeros(int(1.1 * sw.fs))
    rec = np.concatenate([pre, sw.signal, np.zeros(int(0.5 * sw.fs))])
    v = np.asarray(mc.unasked_return(rec, sw, freqs), float)
    f = np.asarray(freqs)
    # and nothing is reported once the tracked orders reach the
    # band: past there the gaps between widely spaced orders count
    # as unasked and a strong harmonic's skirt leaks into them. His
    # first schema-6 profile showed it as a false dark band at 200
    # Hz, where the second harmonic sits on the band's edge.
    top = mc.UNASKED_BAND[0] / mc.UNASKED_ORDERS
    assert not np.isfinite(v[f >= top]).any()
    # and the readings that remain are real numbers, not -inf
    got = v[np.isfinite(v)]
    assert got.size and float(np.min(got)) > -200.0
