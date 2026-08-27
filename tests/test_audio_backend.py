"""The AudioBackend contract, proven against a fake heir: no shims,
no PipeWire, no GTK -- the platform-free half must hold everywhere."""

import pytest

from perdeviceeq.audio_backend import AudioBackend


class FakeBackend(AudioBackend):
    """Applies to a log instead of a server."""

    def __init__(self):
        super().__init__()
        self.log = []

    server_graph = (None, None)
    server_volume = None
    server_mute = None

    def _push_graph(self, device, value):
        self.log.append(("graph", device, value))
        return True

    def _read_graph(self, device):
        return self.server_graph

    def _read_volume(self, device):
        return self.server_volume

    def _push_volume(self, device, cubic):
        self.log.append(("volume", device, cubic))

    def _read_mute(self, device):
        return self.server_mute

    def _push_mute(self, device, mute):
        self.log.append(("mute", device, mute))

    def _push_stream_mutes(self, sink, mute, prior=None):
        self.log.append(("mutes", sink, mute))
        return ["token"] if mute else None

    def _pull(self):
        return {"sinks": [{"name": "s1"}],
                "sources": [{"name": "m1"}],
                "default_sink": "s1"}

    def capture(self, device, channels, rate):
        raise NotImplementedError

    def monitor_capture(self, sink, channels, rate):
        raise NotImplementedError

    def play(self, device, wav_path, stream_volume=1.0):
        raise NotImplementedError


def test_bare_abstract_refuses_birth():
    with pytest.raises(TypeError):
        AudioBackend()


def test_publish_outside_moratorium_hits_the_server():
    f = FakeBackend()
    f.publish_graph("dev", "G1")
    f.set_volume("dev", 0.5)
    assert ("graph", "dev", "G1") in f.log
    assert ("volume", "dev", 0.5) in f.log


def test_moratorium_coalesces_last_write_wins():
    f = FakeBackend()
    f.publish_graph("dev", "G1")
    f.moratorium_begin("dev", 0.074)
    f.publish_graph("dev", "G2")
    f.publish_graph("dev", "G3")
    f.set_volume("other", 0.5)
    assert f.pending_count == 2
    assert all(e != ("graph", "dev", "G2") for e in f.log)
    assert all(e != ("graph", "dev", "G3") for e in f.log)
    f.moratorium_end()
    assert ("graph", "dev", "G3") in f.log
    assert ("volume", "other", 0.5) in f.log
    assert all(e != ("graph", "dev", "G2") for e in f.log)


def test_begin_strips_mutes_and_doses():
    f = FakeBackend()
    f.server_graph = ("TASTE", "metadata")
    f.server_volume = 0.61
    state = f.moratorium_begin("dev", 0.074)
    assert f.log[-3:] == [("mutes", "dev", True),
                          ("graph", "dev", None),
                          ("volume", "dev", 0.074)]
    assert state["bypass"] is True
    assert state["profile"] == "TASTE"
    assert state["profile_source"] == "metadata"


def test_end_restores_when_nothing_pended():
    f = FakeBackend()
    f.server_graph = ("TASTE", "metadata")
    f.server_volume = 0.61
    state = f.moratorium_begin("dev", 0.074)
    f.moratorium_end()
    assert f.log[-3:] == [("volume", "dev", 0.61),
                          ("graph", "dev", "TASTE"),
                          ("mutes", "dev", False)]
    assert state["restored"] is True
    assert not f.moratorium_active


def test_pending_on_the_measured_sink_outranks_restore():
    f = FakeBackend()
    f.publish_graph("dev", "TASTE")
    f.moratorium_begin("dev", 0.074)
    f.clear_graph("dev")
    f.moratorium_end()
    tail = [e for e in f.log if e[0] == "graph"][-1]
    assert tail == ("graph", "dev", None)
    assert all(e != ("graph", "dev", "TASTE")
               or f.log.index(e) < 2 for e in f.log)


def test_nested_moratorium_raises():
    f = FakeBackend()
    f.moratorium_begin("dev", 0.074)
    with pytest.raises(RuntimeError):
        f.moratorium_begin("dev", 0.074)


def test_forced_stop_is_the_same_end():
    f = FakeBackend()
    f.moratorium_begin("dev", 0.074)
    f.moratorium_end()
    f.moratorium_end()          # idempotent after the stop
    assert not f.moratorium_active


def test_observe_notifies_on_change_only():
    f = FakeBackend()
    seen = []
    f.subscribe(lambda b: seen.append(b.default_sink))
    assert f.refresh() is True
    assert f.refresh() is False
    assert seen == ["s1"]
    assert f.sinks == [{"name": "s1"}]
    assert f.sources == [{"name": "m1"}]


def test_seed_restores_what_the_server_had():
    f = FakeBackend()
    f.server_graph = ("SRV", "wpstate")
    f.server_volume = 0.61
    state = f.moratorium_begin("dev", 0.074)
    assert state["profile"] == "SRV"
    assert state["profile_source"] == "wpstate"
    f.moratorium_end()
    tail = [e for e in f.log if e[0] == "graph"]
    assert tail == [("graph", "dev", None), ("graph", "dev", "SRV")]
    assert ("volume", "dev", 0.61) in f.log


def test_clean_server_means_no_strip_and_no_restore():
    f = FakeBackend()
    f.moratorium_begin("dev", None, mute_others=False)
    f.moratorium_end()
    assert all(e[0] != "graph" for e in f.log)
    assert all(e[0] != "volume" for e in f.log)
    assert all(e[0] != "mutes" for e in f.log)


def test_volume_none_skips_the_dose_and_the_restore():
    f = FakeBackend()
    f.publish_graph("dev", "TASTE")
    f.moratorium_begin("dev", None)
    f.moratorium_end()
    assert all(e[0] != "volume" for e in f.log)


def test_queueing_speaks_with_the_callers_voice(capsys):
    f = FakeBackend()
    f.moratorium_begin("dev", None, mute_others=False)
    f.publish_graph("dev", "LATE")
    err = capsys.readouterr().err
    assert "queued graph for dev" in err
    assert "caller:" in err
    f.moratorium_end()
    assert ("graph", "dev", "LATE") in f.log


# --- a fader at its far left is a MUTE, not a zero ----------------

def test_a_muted_sink_is_unmuted_for_the_sweep_and_muted_back():
    """His find: GNOME mutes rather than zeroes at the far left, and a
    measurement that only writes a volume plays a whole sweep into a
    silent card."""
    f = FakeBackend()
    f.server_mute = True
    f.server_volume = 0.4
    f.moratorium_begin("s1", measure_cubic=0.2)
    assert ("mute", "s1", False) in f.log
    assert f.log.index(("mute", "s1", False)) \
        < f.log.index(("volume", "s1", 0.2))
    f.log.clear()
    f.moratorium_end()
    # the volume comes back first, then the mute: a card muted and
    # then given a level is left silent with a number on the fader
    assert f.log.index(("volume", "s1", 0.4)) \
        < f.log.index(("mute", "s1", True))


def test_an_unmuted_sink_is_left_alone():
    f = FakeBackend()
    f.server_mute = False
    f.server_volume = 0.4
    f.moratorium_begin("s1", measure_cubic=0.2)
    f.moratorium_end()
    assert not [e for e in f.log if e[0] == "mute"]

