"""The AudioBackend contract, proven against a fake heir: no shims,
no PipeWire, no GTK -- the platform-free half must hold everywhere."""

import threading
import time

import pytest

from perdeviceeq.audio_backend import AudioBackend
from perdeviceeq.config import MEASURE_KEY, STRIP


class FakeBackend(AudioBackend):
    """Applies to a log instead of a server."""

    def __init__(self):
        super().__init__()
        self.log = []
        self.server = {}           # the metadata: key -> value

    server_volume = None
    server_mute = None

    def _push_graph(self, device, value):
        self.log.append(("graph", device, value))
        self.server[device] = value
        return True

    def _read_graph(self, device):
        return self.server.get(device)

    def _read_volume(self, device):
        return self.server_volume

    def _push_volume(self, device, cubic):
        self.log.append(("volume", device, cubic))
        self.server_volume = cubic

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


def test_moratorium_coalesces_levels_last_write_wins():
    f = FakeBackend()
    f.moratorium_begin("dev", 0.074)
    f.set_volume("other", 0.4)
    f.set_volume("other", 0.5)
    f.set_volume("third", 0.2)
    assert f.pending_count == 2
    assert not [e for e in f.log if e[1] in ("other", "third")]
    f.moratorium_end()
    assert [e for e in f.log if e[1] == "other"] == \
        [("volume", "other", 0.5)]
    assert ("volume", "third", 0.2) in f.log


def test_a_graph_goes_out_during_a_measurement():
    """The hook keeps the measured device bare whatever it is told, so
    nothing need wait: another device's value and the listener's go
    out in their turn."""
    f = FakeBackend()
    f.moratorium_begin("dev", None, mute_others=False)
    f.publish_graph("other", "O")
    f.publish_graph("@listener", "L")
    assert f.flush(5)
    assert f.pending_count == 0
    assert [e for e in f.log if e[0] == "graph"] == \
        [("graph", MEASURE_KEY, "dev"), ("graph", "other", "O"),
         ("graph", "@listener", "L")]
    f.moratorium_end()


def test_begin_names_the_device_mutes_and_doses():
    f = FakeBackend()
    f.server["dev"] = "TASTE"
    f.server_volume = 0.61
    state = f.moratorium_begin("dev", 0.074)
    assert f.log[-3:] == [("mutes", "dev", True),
                          ("graph", MEASURE_KEY, "dev"),
                          ("volume", "dev", 0.074)]
    assert state == {"metadata_key": "dev", "profile": "TASTE",
                     "bypass": True, "restored": None}


def test_end_gives_back_the_level_then_the_device():
    """The device's own value is never written: the hook gives back
    what it holds once the name goes."""
    f = FakeBackend()
    f.server["dev"] = "TASTE"
    f.server_volume = 0.61
    state = f.moratorium_begin("dev", 0.074)
    f.moratorium_end()
    assert f.log[-3:] == [("volume", "dev", 0.61),
                          ("graph", MEASURE_KEY, STRIP),
                          ("mutes", "dev", False)]
    assert state["restored"] is True
    assert not f.moratorium_active
    assert not [e for e in f.log if e[:2] == ("graph", "dev")]


def test_a_level_wished_for_the_measured_sink_outranks_its_restore():
    f = FakeBackend()
    f.server_volume = 0.61
    f.moratorium_begin("dev", 0.074)
    f.set_volume("dev", 0.3)
    f.moratorium_end()
    assert [e[2] for e in f.log if e[:2] == ("volume", "dev")] == \
        [0.074, 0.3]


def test_a_refused_release_is_said_with_the_way_back():
    class Refusing(FakeBackend):
        failed = None

        def _push_graph(self, device, value):
            super()._push_graph(device, value)
            return value != STRIP

        def _restore_failed(self, device, value):
            self.failed = (device, value)

    f = Refusing()
    state = f.moratorium_begin("dev", None, mute_others=False)
    f.moratorium_end()
    assert state["restored"] is False
    assert f.failed == (MEASURE_KEY, STRIP)


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


def test_a_device_the_metadata_knows_nothing_of_is_named_too():
    """It may play the listener's taste and preamp, or what the hook
    kept from before a cold start of PipeWire: the hook knows, the
    metadata need not. It is named either way, and nothing is read
    back to give it back."""
    f = FakeBackend()
    state = f.moratorium_begin("dev", None, mute_others=False)
    assert state["bypass"] is True and state["profile"] is None
    f.moratorium_end()
    assert [e for e in f.log if e[0] == "graph"] == \
        [("graph", MEASURE_KEY, "dev"), ("graph", MEASURE_KEY, STRIP)]
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
    f.set_volume("dev", 0.3)
    err = capsys.readouterr().err
    assert "queued volume for dev" in err
    assert "caller:" in err
    f.moratorium_end()
    assert ("volume", "dev", 0.3) in f.log


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
        self.server["dev#port"] = "G"
        self.graph_reads = []

    def _graph_key(self, sink):
        return sink + "#port"

    def _read_graph(self, device):
        self.graph_reads.append(device)
        return super()._read_graph(device)


def test_the_moratorium_names_the_device_and_leaves_its_value_be():
    f = DeviceKeyed()
    state = f.moratorium_begin("dev", 0.074)
    assert state["metadata_key"] == "dev#port"
    assert state["profile"] == "G" and state["bypass"] is True
    assert ("graph", MEASURE_KEY, "dev#port") in f.log
    assert set(f.graph_reads) == {"dev#port", MEASURE_KEY}
    assert ("volume", "dev", 0.074) in f.log        # the knob is the node's
    f.moratorium_end()
    assert ("graph", MEASURE_KEY, STRIP) in f.log
    assert state["restored"] is True
    assert f.server["dev#port"] == "G"
    assert not [e for e in f.log
                if e[0] == "graph" and e[1] in ("dev", "dev#port")]


def test_a_value_for_the_measured_device_goes_out_at_once():
    """The window publishes under the device key; while a measurement
    runs, the hook keeps the device bare and plays this value, the
    newest, once the name goes. Nothing holds it back, and nothing
    writes over it."""
    f = DeviceKeyed()
    f.moratorium_begin("dev", None)
    f.publish_graph("dev#port", "NEW")
    assert f.flush(5)
    f.moratorium_end()
    graphs = [e for e in f.log if e[0] == "graph"]
    assert graphs == [("graph", MEASURE_KEY, "dev#port"),
                      ("graph", "dev#port", "NEW"),
                      ("graph", MEASURE_KEY, STRIP)]


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


def test_a_graph_waiting_when_a_measurement_starts_goes_out():
    """A wish still waiting for its device's turn when a measurement
    starts lands in that turn, and the measurement writes nothing but
    the name."""
    f = FakeBackend()
    f.WRITE_PACE_S = 0.3
    f.publish_graph("dev", "OLD")
    assert f.flush(5)
    f.publish_graph("dev", "NEW")          # its turn is 0.3 s away
    f.moratorium_begin("dev", None, mute_others=False)
    assert f.flush(5)
    assert _graphs(f, "dev") == [("dev", "OLD"), ("dev", "NEW")]
    f.moratorium_end()
    assert _graphs(f, MEASURE_KEY) == [(MEASURE_KEY, "dev"),
                                       (MEASURE_KEY, STRIP)]


def test_flush_answers_at_once_when_nothing_waits():
    assert FakeBackend().flush(0)


# ---- the pace: a device only in its turn, and the last wish still last -----

class Timed(FakeBackend):
    """A write that takes `cost` seconds, logged with its start and its
    end on the clock the writer reads."""

    def __init__(self, cost=0.002):
        super().__init__()
        self.cost = cost
        self.writes = []
        self.volumes = []

    def _push_graph(self, device, value):
        start = time.monotonic()
        time.sleep(self.cost)
        self.writes.append((device, value, start, time.monotonic()))
        return super()._push_graph(device, value)

    def _push_volume(self, device, cubic):
        start = time.monotonic()
        time.sleep(self.cost)
        self.volumes.append((device, cubic, start, time.monotonic()))
        super()._push_volume(device, cubic)


def _gaps(writes, device):
    """From the end of each write of `device` to the start of the next."""
    w = [x for x in writes if x[0] == device]
    return [b[2] - a[3] for a, b in zip(w, w[1:])]


def test_a_drag_writes_its_device_once_per_turn_and_ends_on_its_last_wish():
    """A wish every millisecond for 0.3 s. Each gap is at least the pace
    however busy the machine is -- a busy machine only widens it -- so
    the count has a ceiling: the writes inside the drag, and one after."""
    f = Timed()
    stop = time.monotonic() + 0.3
    n = 0
    while time.monotonic() < stop:
        f.publish_graph("dev", "G%d" % n)
        n += 1
        time.sleep(0.001)
    assert f.flush(5)
    assert min(_gaps(f.writes, "dev")) >= f.WRITE_PACE_S
    assert f.writes[-1][:2] == ("dev", "G%d" % (n - 1))
    assert len(f.writes) <= 0.3 / f.WRITE_PACE_S + 2


class Refusing(FakeBackend):
    """A server that does not accept the graph for one device."""

    def _push_graph(self, device, value):
        super()._push_graph(device, value)
        return device != "bad"


def test_a_graph_the_server_did_not_accept_is_said_with_its_key(capsys):
    """Through the writer and through a whole pass alike. In a field
    capture (Nov 7) one key was missing from one pass, and nothing in
    the program would have said so."""
    f = Refusing()
    f.publish_graph("bad", "G")
    f.publish_graph("good", "G")
    assert f.flush(5)
    assert f.publish_state({"bad": "P", "good": "P"}) == (1, 2)
    err = capsys.readouterr().err.splitlines()
    said = "per-device-eq: the server did not accept the graph for bad"
    assert err.count(said) == 2
    assert not any("good" in line for line in err)


def test_a_turn_counts_from_a_write_the_whole_pass_made():
    f = Timed()
    f.publish_state({"dev": "P"})
    f.publish_graph("dev", "W")
    assert f.flush(5)
    assert [w[1] for w in f.writes] == ["P", "W"]
    assert _gaps(f.writes, "dev")[0] >= f.WRITE_PACE_S


# ---- graph values land in the order they were made -------------------------

class GatedOne(FakeBackend):
    """A server whose write for one key waits for the test."""

    def __init__(self, held):
        super().__init__()
        self.held = held
        self.gate = threading.Event()
        self.entered = threading.Event()

    def _push_graph(self, device, value):
        if device == self.held:
            self.entered.set()
            self.gate.wait(5)
        return super()._push_graph(device, value)


def test_a_record_made_after_a_device_value_lands_after_it():
    """A device's preamp travels in its own value and in the listener's
    record, and the hook plays the one that arrives last. Both were made
    while another write was in flight, the device's just after its last
    write: taken by turn, the record went first and the device value,
    made before it, landed last."""
    f = GatedOne("x")
    f.publish_graph("dev", "D0")
    assert f.flush(5)
    f.publish_graph("x", "X")
    assert f.entered.wait(5)
    f.publish_graph("dev", "D1")          # its turn: 50 ms after D0
    f.publish_graph("@listener", "L1")    # its turn: now
    f.gate.set()
    assert f.flush(5)
    assert _graphs(f) == [("dev", "D0"), ("x", "X"), ("dev", "D1"),
                          ("@listener", "L1")]


def test_a_value_made_again_goes_behind_the_ones_made_since():
    """The order is the last wish's: the device value made after the
    record carries what the record did not know yet."""
    f = Timed()
    f.publish_graph("dev", "D0")
    assert f.flush(5)
    f.publish_graph("dev", "D1")
    f.publish_graph("@listener", "L1")
    f.publish_graph("dev", "D2")
    assert f.flush(5)
    assert [w[:2] for w in f.writes] == [("dev", "D0"),
                                         ("@listener", "L1"), ("dev", "D2")]


# ---- a level is a wish like a graph -----------------------------------------

def test_a_fader_drag_keeps_the_turn_and_ends_on_its_last_level():
    """The fader paced its own writes and put each on a thread of its
    own, around this door. Through it, its levels keep the same turn
    as a graph, and the last level the hand chose is the last to land."""
    f = Timed()
    stop = time.monotonic() + 0.3
    n = 0
    while time.monotonic() < stop:
        f.set_volume("dev", n / 1000.0)
        n += 1
        time.sleep(0.001)
    assert f.flush(5)
    assert min(_gaps(f.volumes, "dev")) >= f.WRITE_PACE_S
    assert f.volumes[-1][:2] == ("dev", (n - 1) / 1000.0)
    assert len(f.volumes) <= 0.3 / f.WRITE_PACE_S + 2


def test_a_level_does_not_wait_for_the_turn_of_a_graph():
    """Two knobs of one device are two turns."""
    f = Timed()
    f.WRITE_PACE_S = 10.0
    f.publish_graph("dev", "G")
    assert f.flush(2)
    f.set_volume("dev", 0.5)
    assert f.flush(2)
    assert f.volumes[-1][:2] == ("dev", 0.5)


def test_a_level_waiting_when_a_measurement_starts_lands_after_it():
    """Whether the writer gets to it before the measurement or not, the
    measurement plays at its own level, and afterwards the device keeps
    the level the hand chose -- not the one the measurement found."""
    f = Gated()
    f.server_volume = 0.61
    f.publish_graph("other", "X")
    assert f.entered.wait(5)
    f.set_volume("dev", 0.3)
    t = threading.Thread(target=f.moratorium_begin, args=("dev", 0.074),
                         kwargs={"mute_others": False})
    t.start()
    f.gate.set()
    t.join(5)
    assert f.moratorium_active
    assert f.flush(5)
    levels = [e[2] for e in f.log if e[:2] == ("volume", "dev")]
    assert levels[-1] == 0.074
    f.moratorium_end()
    assert f.flush(5)
    levels = [e[2] for e in f.log if e[:2] == ("volume", "dev")]
    assert levels[-1] == 0.3


# ---- an ending program lands what it owes -----------------------------------

class EndedBy(FakeBackend):
    """Knows which thread cleared the measurement's name."""

    ended_by = None

    def _push_graph(self, device, value):
        if device == MEASURE_KEY and value == STRIP:
            self.ended_by = threading.current_thread()
        return super()._push_graph(device, value)


def test_land_waits_for_a_wish_still_waiting_for_its_turn():
    f = FakeBackend()
    f.WRITE_PACE_S = 0.3
    f.publish_graph("dev", "OLD")
    assert f.flush(5)
    f.publish_graph("dev", "NEW")          # its turn is 0.3 s away
    assert f.land(5)
    assert _graphs(f, "dev") == [("dev", "OLD"), ("dev", "NEW")]


def test_land_lets_a_measurement_end_on_its_own_thread():
    """A sweep cancelled by the close ends where it runs, and puts its
    level back there; the landing waits for it."""
    f = EndedBy()
    f.server_volume = 0.61
    f.moratorium_begin("dev", 0.074, mute_others=False)
    t = threading.Timer(0.1, f.moratorium_end)
    t.start()
    began = time.monotonic()
    assert f.land(4)
    assert time.monotonic() - began < 2       # told, not timed out
    t.join(5)
    assert f.ended_by is t
    assert _graphs(f, MEASURE_KEY) == [(MEASURE_KEY, "dev"),
                                       (MEASURE_KEY, STRIP)]


def test_land_ends_a_measurement_that_does_not_end_in_time():
    f = EndedBy()
    f.server_volume = 0.61
    state = f.moratorium_begin("dev", 0.074, mute_others=False)
    assert f.land(0.1)
    assert not f.moratorium_active
    assert f.ended_by is threading.current_thread()
    assert state["restored"] is True
    assert [e[2] for e in f.log if e[:2] == ("volume", "dev")] == \
        [0.074, 0.61]


def test_land_waits_for_an_end_in_progress():
    """The end holds the server for its whole pass. A landing that came
    back in the middle of it would let the process die before the name
    is cleared."""
    class Slow(FakeBackend):
        def __init__(self):
            super().__init__()
            self.gate = threading.Event()
            self.entered = threading.Event()

        def _push_graph(self, device, value):
            if device == MEASURE_KEY and value == STRIP:
                self.entered.set()
                self.gate.wait(5)
            return super()._push_graph(device, value)

    f = Slow()
    f.moratorium_begin("dev", None, mute_others=False)
    ender = threading.Thread(target=f.moratorium_end)
    ender.start()
    assert f.entered.wait(5)
    done = []
    lander = threading.Thread(target=lambda: done.append(f.land(5)))
    lander.start()
    lander.join(0.2)
    assert done == []
    f.gate.set()
    lander.join(5)
    ender.join(5)
    assert done == [True]
    assert _graphs(f, MEASURE_KEY) == [(MEASURE_KEY, "dev"),
                                       (MEASURE_KEY, STRIP)]
