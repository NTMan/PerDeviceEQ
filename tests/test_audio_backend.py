"""The AudioBackend contract, proven against a fake heir: no shims,
no PipeWire, no GTK -- the platform-free half must hold everywhere."""

import threading

import pytest

from perdeviceeq.audio_backend import AudioBackend
from perdeviceeq.config import STRIP


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
        self.server_graph = (value, "metadata")
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
    assert f.flush(5)
    assert ("graph", "dev", "G1") in f.log
    assert ("volume", "dev", 0.5) in f.log


def test_moratorium_coalesces_last_write_wins():
    f = FakeBackend()
    f.publish_graph("dev", "G1")
    assert f.flush(5)
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
                          ("graph", "dev", STRIP),
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
    assert f.flush(5)
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
    assert tail == [("graph", "dev", STRIP), ("graph", "dev", "SRV")]
    assert ("volume", "dev", 0.61) in f.log


def test_a_device_with_no_value_of_its_own_is_stripped_too():
    """It plays the common entry, so there is a graph on it to strip;
    the restore puts it back to following that entry."""
    f = FakeBackend()
    state = f.moratorium_begin("dev", None, mute_others=False)
    assert state["bypass"] is True and state["profile"] is None
    f.moratorium_end()
    assert [e for e in f.log if e[0] == "graph"] == \
        [("graph", "dev", STRIP), ("graph", "dev", None)]
    assert state["restored"] is True
    assert all(e[0] != "volume" for e in f.log)
    assert all(e[0] != "mutes" for e in f.log)


def test_volume_none_skips_the_dose_and_the_restore():
    f = FakeBackend()
    f.publish_graph("dev", "TASTE")
    assert f.flush(5)
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



# ---- the graph is the device's, the knobs are the node's ------------------

class DeviceKeyed(FakeBackend):
    """An heir whose graphs are published under node#port while volume
    and mute stay on the node -- the PipeWire arrangement since the key
    became the node and the port in use."""

    def __init__(self):
        super().__init__()
        self.server = {"dev#port": "G"}
        self.graph_reads = []

    def _graph_key(self, sink):
        return sink + "#port"

    def _read_graph(self, device):
        self.graph_reads.append(device)
        value = self.server.get(device)
        return value, ("metadata" if value is not None else None)

    def _push_graph(self, device, value):
        self.server[device] = value
        return super()._push_graph(device, value)


def test_the_moratorium_strips_the_graph_the_device_wears():
    f = DeviceKeyed()
    state = f.moratorium_begin("dev", 0.074)
    assert state["metadata_key"] == "dev#port"
    assert state["profile"] == "G" and state["bypass"] is True
    assert ("graph", "dev#port", STRIP) in f.log
    assert set(f.graph_reads) == {"dev#port"}
    assert ("volume", "dev", 0.074) in f.log        # the knob is the node's
    f.moratorium_end()
    assert ("graph", "dev#port", "G") in f.log
    assert state["restored"] is True
    assert not [e for e in f.log if e[0] == "graph" and e[1] == "dev"]


def test_a_write_queued_for_the_device_outranks_the_restore():
    """The window publishes under the device key; while a measurement
    runs that write is queued, and it is the newer wish."""
    f = DeviceKeyed()
    f.moratorium_begin("dev", None)
    f.publish_graph("dev#port", "NEW")
    f.moratorium_end()
    graphs = [e for e in f.log if e[0] == "graph"]
    assert graphs == [("graph", "dev#port", STRIP),
                      ("graph", "dev#port", "NEW")]


# ---- one writer: the newest wish of a device lands, and lands last ---------

class Gated(FakeBackend):
    """A server write that waits for the test to let it through, so that
    wishes can be made while one is in flight."""

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.entered = threading.Event()

    def _push_graph(self, device, value):
        self.entered.set()
        self.gate.wait(5)
        return super()._push_graph(device, value)


def _graphs(f, device=None):
    return [e[1:] for e in f.log
            if e[0] == "graph" and device in (None, e[1])]


def test_a_drag_lands_its_first_wish_and_its_last_only():
    """A wish made while a write is in flight replaces the one waiting:
    the eight in between never reach the server, and the last one does,
    after everything else."""
    f = Gated()
    f.publish_graph("dev", "G0")
    assert f.entered.wait(5)
    for i in range(1, 10):
        f.publish_graph("dev", "G%d" % i)
    f.gate.set()
    assert f.flush(5)
    assert _graphs(f) == [("dev", "G0"), ("dev", "G9")]


def test_a_wish_made_during_a_whole_pass_goes_out_after_it():
    f = Gated()
    t = threading.Thread(target=f.publish_state,
                         args=({"a": "A", "b": "B"},))
    t.start()
    assert f.entered.wait(5)
    f.publish_graph("c", "C")
    f.publish_graph("a", "A2")          # newer than the pass's own
    f.gate.set()
    t.join(5)
    assert f.flush(5)
    out = _graphs(f)
    assert out[:2] == [("a", "A"), ("b", "B")]
    assert sorted(out[2:]) == [("a", "A2"), ("c", "C")]


def test_a_wish_waiting_when_a_measurement_starts_lands_after_it():
    """Whether the writer gets to it before the strip or the strip comes
    first, nothing lands on the measured device while it is stripped,
    and the wish is what it wears afterwards."""
    f = Gated()
    f.publish_graph("other", "X")
    assert f.entered.wait(5)
    f.publish_graph("dev", "NEW")
    t = threading.Thread(target=f.moratorium_begin, args=("dev", None),
                         kwargs={"mute_others": False})
    t.start()
    f.gate.set()
    t.join(5)
    assert f.moratorium_active
    assert f.flush(5)
    assert _graphs(f, "dev")[-1] == ("dev", STRIP)
    f.moratorium_end()
    assert f.flush(5)
    assert _graphs(f, "dev")[-1] == ("dev", "NEW")


def test_flush_answers_at_once_when_nothing_waits():
    assert FakeBackend().flush(0)
