"""The AudioBackend contract, proven against a fake heir: no shims,
no PipeWire, no GTK -- the platform-free half must hold everywhere."""

import pytest

from perdeviceeq.audio_backend import AudioBackend


class FakeBackend(AudioBackend):
    """Applies to a log instead of a server."""

    def __init__(self):
        super().__init__()
        self.log = []

    def _push_graph(self, device, value):
        self.log.append(("graph", device, value))

    def _push_volume(self, device, cubic):
        self.log.append(("volume", device, cubic))

    def _push_stream_mutes(self, sink, mute, prior=None):
        self.log.append(("mutes", sink, mute))
        return ["token"] if mute else None

    def _pull(self):
        return {"sinks": ["s1"], "sources": ["m1"],
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
    f.publish_graph("dev", "TASTE")
    f.set_volume("dev", 0.61)
    f.moratorium_begin("dev", 0.074)
    assert f.log[-3:] == [("mutes", "dev", True),
                          ("graph", "dev", None),
                          ("volume", "dev", 0.074)]


def test_end_restores_when_nothing_pended():
    f = FakeBackend()
    f.publish_graph("dev", "TASTE")
    f.set_volume("dev", 0.61)
    f.moratorium_begin("dev", 0.074)
    f.moratorium_end()
    assert f.log[-3:] == [("mutes", "dev", False),
                          ("volume", "dev", 0.61),
                          ("graph", "dev", "TASTE")]
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
    assert f.sinks == ["s1"] and f.sources == ["m1"]
