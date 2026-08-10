"""Tests for the measurement preference stores (mic profiles, per-sink
recall). The CONFIG_DIR paths are redirected into tmp so the real
~/.config is never touched.
"""
import pytest

from perdeviceeq import measure_prefs as mp


@pytest.fixture
def paths(tmp_path, monkeypatch):
    micf = tmp_path / "mic-profiles.json"
    memf = tmp_path / "measure-state.json"
    monkeypatch.setattr(mp, "MIC_PROFILES_FILE", str(micf))
    monkeypatch.setattr(mp, "MEASURE_STATE_FILE", str(memf))
    return micf, memf


# --- mic profiles ----------------------------------------------------------

def test_mic_profile_roundtrip(paths):
    micf, _ = paths
    s = mp.MicProfileStore()
    assert s.ordered() == []
    pid = s.save({"name": "miniDSP EARS", "serial": "860-3052",
                  "node_match": "alsa_input.usb-miniDSP_ears",
                  "cal": {"0": "/c/L_RAW.txt", "1": "/c/R_RAW.txt"}})
    assert micf.exists()
    s2 = mp.MicProfileStore()                    # reload from disk
    p = s2.get(pid)
    assert p["name"] == "miniDSP EARS"
    assert p["serial"] == "860-3052"
    assert s2.cal_for(pid, 0) == "/c/L_RAW.txt"
    assert s2.cal_for(pid, 1) == "/c/R_RAW.txt"
    assert s2.cal_for(pid, 2) is None            # unmapped channel
    assert s2.match("alsa_input.usb-miniDSP_ears")["id"] == pid
    assert s2.match("some_other_mic") is None


def test_mic_profile_overwrite_same_id(paths):
    s = mp.MicProfileStore()
    pid = s.save({"name": "first", "cal": {"0": "/a.txt"}})
    s.save({"id": pid, "name": "renamed", "cal": {"0": "/b.txt"}})
    assert len(mp.MicProfileStore().profiles) == 1
    assert mp.MicProfileStore().get(pid)["name"] == "renamed"
    assert mp.MicProfileStore().cal_for(pid, 0) == "/b.txt"


def test_mic_profile_delete(paths):
    s = mp.MicProfileStore()
    pid = s.save({"name": "x"})
    assert s.delete(pid) is True
    assert s.get(pid) is None
    assert mp.MicProfileStore().get(pid) is None
    assert s.delete("nope") is False


def test_mic_profile_store_survives_junk(paths):
    micf, _ = paths
    micf.write_text("this is not json")
    s = mp.MicProfileStore()                     # must not raise
    assert s.ordered() == []
    pid = s.save({"name": "ok"})                 # and still writable
    assert mp.MicProfileStore().get(pid)["name"] == "ok"


# --- per-sink recall -------------------------------------------------------

def test_measure_memory_per_sink_and_source(paths):
    _, memf = paths
    m = mp.MeasureMemory()
    assert m.mic_for("sink_a") is None
    assert m.volume_for("sink_a", "srcA") is None
    m.remember("sink_a", mic_profile="mic1", source="srcA", volume=0.62)
    assert memf.exists()
    m2 = mp.MeasureMemory()
    assert m2.mic_for("sink_a") == "mic1"
    assert m2.volume_for("sink_a", "srcA") == pytest.approx(0.62)
    # volume is per source: another mic on the same sink is separate
    assert m2.volume_for("sink_a", "srcB") is None
    m2.remember("sink_a", source="srcB", volume=0.40)
    m3 = mp.MeasureMemory()
    assert m3.volume_for("sink_a", "srcA") == pytest.approx(0.62)
    assert m3.volume_for("sink_a", "srcB") == pytest.approx(0.40)
    assert m3.mic_for("sink_a") == "mic1"      # unchanged by volume writes
    # sinks are independent
    assert m3.volume_for("sink_b", "srcA") is None
    # re-level drops just that pair's volume
    m3.forget_volume("sink_a", "srcA")
    m4 = mp.MeasureMemory()
    assert m4.volume_for("sink_a", "srcA") is None
    assert m4.volume_for("sink_a", "srcB") == pytest.approx(0.40)
    m4.forget("sink_a")
    assert mp.MeasureMemory().mic_for("sink_a") is None


def test_measure_memory_ignores_junk(paths):
    _, memf = paths
    memf.write_text("not json")
    m = mp.MeasureMemory()                        # must not raise
    assert m.mic_for("x") is None
    m.remember("x", mic_profile="m")             # and still writable
    assert mp.MeasureMemory().mic_for("x") == "m"


def test_a_rig_no_longer_stores_a_capsule_count(paths):
    """The count is gone and does not come back on a reload. It was a
    hand's correction for a card that enumerates a width it does not
    capture -- but a COUNT cannot say which wire carries what, the
    per-target column picker says exactly that, and a stale stored 2
    was what kept a sixteen-column interface offering L and R."""
    s = mp.MicProfileStore()
    pid = s.save({"name": "Umik", "node_match": "umik.0",
                  "cal": {"0": "/c/umik.txt"}, "channels": 1})
    assert "channels" not in s.get(pid)
    s2 = mp.MicProfileStore()                    # reload from disk
    assert "channels" not in s2.get(pid)
    assert s2.get(pid)["cal"] == {"0": "/c/umik.txt"}


def test_serial_from_cal_reads_the_unit_identity():
    f = mp.serial_from_cal
    assert f(["/x/L_RAW_8603052.txt",
              "/x/R_RAW_8603052.txt"]) == "8603052"
    assert f(["UMIK-1_700123.txt"]) == "700123"
    # disagreeing files guess nothing; a wrong serial is worse
    assert f(["L_RAW_8603052.txt", "R_RAW_9999999.txt"]) == ""
    # short digit runs are gain labels and dates, not serials
    assert f(["L_0dB.txt"]) == ""
    # ambiguity inside one name resolves via the common candidate
    assert f(["L_RAW_8603052_20260714.txt",
              "R_RAW_8603052.txt"]) == "8603052"
    assert f([]) == "" and f([None, ""]) == ""


def test_mic_memory_survives_a_card_profile_switch(paths):
    """Field regression: one USB DAC appeared under three sink
    names as its ALSA profile changed, and the rig memory looked
    wiped on each new name. The exact key must win, a sibling on
    the same card stem must answer otherwise, and writes must go
    to the exact key so the fork heals under the new name."""
    m = mp.MeasureMemory()
    hifi = "alsa_output.usb-Generic_USB_Audio-00.HiFi__Speaker__sink"
    seven = "alsa_output.usb-Generic_USB_Audio-00.HiFi_7_1__Speaker__sink"
    fresh = "alsa_output.usb-Generic_USB_Audio-00.analog-stereo"
    m.remember(hifi, mic_profile="ears")
    # the fresh profile name has no entry -- the sibling answers
    assert mp.MeasureMemory().mic_for(fresh) == "ears"
    # an exact entry always beats the sibling
    m.remember(seven, mic_profile="umik")
    assert mp.MeasureMemory().mic_for(seven) == "umik"
    assert mp.MeasureMemory().mic_for(hifi) == "ears"
    # learning under the fresh name writes the exact key
    m2 = mp.MeasureMemory()
    m2.remember(fresh, mic_profile="ears")
    assert m2.state[fresh]["mic_profile"] == "ears"
    # a different card never borrows this rig
    assert mp.MeasureMemory().mic_for(
        "alsa_output.pci-0000_00_1f.3.analog-stereo") is None
    # and bluez keys fall back across their trailing instance too
    m2.remember("bluez_output.F4_9D.1", mic_profile="ears")
    assert mp.MeasureMemory().mic_for("bluez_output.F4_9D.2") == "ears"


def test_the_level_caption_and_the_hand_that_wins(tmp_path):
    """The pre-session hand edit persists: MeasureMemory
    keeps the volume under sink+source, so the next build
    starts from the hand, not from a hunt."""
    import perdeviceeq.measure_prefs as _mp
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(_mp, "MEASURE_STATE_FILE",
                        str(tmp_path / "m.json"),
                        raising=False)
    try:
        mem = _mp.MeasureMemory()
        mem.remember("sinkA", source="umik24", volume=0.42)
        assert abs(mem.volume_for("sinkA", "umik24")
                   - 0.42) < 1e-9
        assert mem.volume_for("sinkA", "other") is None
    finally:
        monkeypatch.undo()

def test_only_a_hand_can_take_the_calibration_off():
    """An empty chosen set means two different things: a profile
    still loading, and an operator who pressed Remove. Guessing
    one way wipes a remembered rig; guessing the other makes the
    calibration impossible to take off."""
    keep = {"0": "/cal/mic.txt"}
    assert mp.cal_to_store({0: "/cal/new.txt"},
                                      keep) == {"0": "/cal/new.txt"}
    # a stray handler with nothing in RAM yet must not wipe it
    assert mp.cal_to_store({}, keep) == keep
    assert mp.cal_to_store(None, keep) == keep
    # a hand empties it, remembered or not
    assert mp.cal_to_store({}, keep, by_hand=True) == {}
    assert mp.cal_to_store({}, None, by_hand=True) == {}
    # empty paths are not calibrations
    assert mp.cal_to_store({0: "", 1: None}, keep) == keep
    assert mp.cal_to_store({0: "", 1: "/c/b.txt"},
                                      keep) == {"1": "/c/b.txt"}
    # the caller's dict is never handed back by reference
    out = mp.cal_to_store({}, keep)
    out["0"] = "/cal/other.txt"
    assert keep == {"0": "/cal/mic.txt"}


def test_a_rig_is_named_by_its_jack_too(paths):
    """The port is part of WHICH RIG this is: the microphone jack
    and the line jack of one card are different preamps, different
    gain and different noise, so they carry their own calibration
    rather than sharing one. The store needs no new field for that
    -- the identity already says it."""
    s = mp.MicProfileStore()
    mic = s.save({"name": "CM106 (Microphone)",
                  "node_match": "alsa_input.usb-0d8c-00#1",
                  "serial": "", "cal": {"0": "/c/mic.txt"},
                  "channels": 1})
    line = s.save({"name": "CM106 (Line In)",
                   "node_match": "alsa_input.usb-0d8c-00#2",
                   "serial": "", "cal": {"0": "/c/line.txt"},
                   "channels": 1})
    again = mp.MicProfileStore()
    assert again.match("alsa_input.usb-0d8c-00#1")["id"] == mic
    assert again.match("alsa_input.usb-0d8c-00#2")["id"] == line
    assert again.get(mic)["cal"]["0"] == "/c/mic.txt"
    assert again.get(line)["cal"]["0"] == "/c/line.txt"


def test_a_rig_named_before_jacks_answers_for_its_card(paths):
    """The list learned about ports and every identity gained a
    jack. A profile written before that names the bare NODE and
    means "this card": refusing it would silently drop the rig's
    calibration the first time the app was
    updated. A profile that names a jack answers for that jack
    only."""
    s = mp.MicProfileStore()
    old = s.save({"name": "CM106", "node_match": "alsa_input.x",
                  "serial": "", "cal": {"0": "/c/old.txt"},
                  "channels": 1})
    assert s.match("alsa_input.x")["id"] == old
    assert s.match("alsa_input.x#1")["id"] == old   # any jack
    assert s.match("alsa_input.x#2")["id"] == old
    assert s.match("alsa_input.other") is None
    assert s.match("") is None and s.match(None) is None
    # once a jack is named, it answers for itself and nothing else
    jack = s.save({"name": "CM106 line", "node_match":
                   "alsa_input.x#2", "serial": "",
                   "cal": {"0": "/c/line.txt"}, "channels": 1})
    assert s.match("alsa_input.x#2")["id"] == jack
    assert s.match("alsa_input.x")["id"] == old
    assert s.match("alsa_input.x#1")["id"] == old


def test_a_hand_alone_is_worth_a_rig_profile():
    """An act of a hand says something about the rig even
    when nothing else is on file yet. Refusing to save it was a real
    bug: Mono was picked on a jack with no profile, nothing was
    written, and the next opening read the channel count off the
    graph and answered Stereo."""
    assert mp.worth_saving({}, None, by_hand=True) is True
    assert mp.worth_saving({"0": "/c/a.txt"}, None) is True
    assert mp.worth_saving({}, {"id": "p1"}) is True
    # a handler firing during load must not mint a profile
    assert mp.worth_saving({}, None) is False
    assert mp.worth_saving({}, None, by_hand=False) is False
