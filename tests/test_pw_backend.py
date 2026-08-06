"""PipeWireBackend against a recorded _run and a fake dump: every
verb must speak the right command -- no shims, no live PipeWire."""

import types

import pytest

from perdeviceeq import pw_backend as pwb
from perdeviceeq.pw_backend import PipeWireBackend, StreamHandle


FAKE_DUMP = [
    {"id": 10, "type": "PipeWire:Interface:Node",
     "info": {"props": {"node.name": "test_sink",
                        "node.description": "Test Sink",
                        "media.class": "Audio/Sink",
                        "device.api": "alsa"},
              "params": {"Props": [{
                  "channelVolumes": [0.343, 0.343]}]}}},
    {"id": 11, "type": "PipeWire:Interface:Node",
     "info": {"props": {"node.name": "test_source",
                        "node.description": "Test Source",
                        "media.class": "Audio/Source",
                        "device.api": "alsa"}}},
    {"id": 20, "type": "PipeWire:Interface:Node",
     "info": {"props": {"node.name": "music-player",
                        "media.class": "Stream/Output/Audio"}}},
    {"id": 21, "type": "PipeWire:Interface:Node",
     "info": {"props": {"node.name": "already-muted",
                        "media.class": "Stream/Output/Audio"},
              "params": {"Props": [{"mute": True}]}}},
    {"id": 22, "type": "PipeWire:Interface:Node",
     "info": {"props": {"node.name": "pde-measure-sweep",
                        "media.class": "Stream/Output/Audio"}}},
    {"id": 30, "type": "PipeWire:Interface:Link",
     "info": {"output-node-id": 20, "input-node-id": 10}},
    {"id": 31, "type": "PipeWire:Interface:Link",
     "info": {"output-node-id": 21, "input-node-id": 10}},
    {"id": 32, "type": "PipeWire:Interface:Link",
     "info": {"output-node-id": 22, "input-node-id": 10}},
]


@pytest.fixture
def rig(monkeypatch):
    calls = []

    def fake_run(cmd, timeout=2.0):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="",
                                     stderr="")

    monkeypatch.setattr(pwb, "_run", fake_run)
    monkeypatch.setattr(pwb, "pw_dump", lambda: FAKE_DUMP)
    return PipeWireBackend(), calls


def test_graph_publish_and_clear_speak_metadata(rig):
    b, calls = rig
    b.publish_graph("test_sink", "{ nodes = [] }")
    b.clear_graph("test_sink")
    assert ["pw-metadata", "-n", "per-device-eq", "0",
            "test_sink", "{ nodes = [] }",
            "Spa:String:JSON"] in calls or any(
        c[:5] == ["pw-metadata", "-n", "per-device-eq", "0",
                  "test_sink"] and "{ nodes = [] }" in c
        for c in calls)
    assert any(c[:3] == ["pw-metadata", "-n", "per-device-eq"]
               and "-d" in c for c in calls)


def test_volume_resolves_the_sink_and_speaks_wpctl(rig):
    b, calls = rig
    b.set_volume("test_sink", 0.42)
    assert ["wpctl", "set-volume", "10", "0.4200"] in calls


def test_mutes_touch_only_unmuted_foreigners(rig):
    b, calls = rig
    prior = b._push_stream_mutes("test_sink", True)
    assert prior == [20]
    assert ["pw-cli", "set-param", "20", "Props",
            "{ mute = true }"] in calls
    assert not any(c[:3] == ["pw-cli", "set-param", "21"]
                   for c in calls)
    assert not any(c[:3] == ["pw-cli", "set-param", "22"]
                   for c in calls)
    b._push_stream_mutes("test_sink", False, prior)
    assert ["pw-cli", "set-param", "20", "Props",
            "{ mute = false }"] in calls


def test_read_graph_falls_back_to_wpstate(rig, monkeypatch):
    b, _ = rig
    monkeypatch.setattr(pwb, "metadata_get", lambda k: None)
    monkeypatch.setattr(pwb, "wpstate_get",
                        lambda k: "PERSISTED")
    assert b._read_graph("test_sink") == ("PERSISTED", "wpstate")
    monkeypatch.setattr(pwb, "metadata_get",
                        lambda k: "LIVE")
    assert b._read_graph("test_sink") == ("LIVE", "metadata")


def test_read_volume_takes_the_cube_root(rig):
    b, _ = rig
    v = b._read_volume("test_sink")
    assert abs(v - 0.343 ** (1.0 / 3.0)) < 1e-9


def test_moratorium_full_cycle_over_the_fake_server(rig):
    b, calls = rig
    b.publish_graph("test_sink", "TASTE")
    b.moratorium_begin("test_sink", 0.074)
    b.publish_graph("test_sink", "NEW")
    b.moratorium_end()
    assert ["wpctl", "set-volume", "10", "0.0740"] in calls
    joined = ["|".join(c) for c in calls]
    assert any("NEW" in j for j in joined)
    taste_writes = [j for j in joined
                    if "TASTE" in j and "pw-metadata" in j]
    assert len(taste_writes) == 1     # never republished mid-take


def test_pull_shapes_from_the_dump(rig):
    b, _ = rig
    b.refresh()
    assert b.default_sink is None or isinstance(
        b.default_sink, (str, dict))
    names = {s.get("name") or s.get("node_name")
             for s in b.sinks} if b.sinks and isinstance(
        b.sinks[0], dict) else set(b.sinks)
    assert any("test_sink" in str(n) for n in names)


def test_stream_handle_lifecycle():
    class FakeProc:
        def __init__(self):
            self.terminated = False
            self.stdout = types.SimpleNamespace(
                read=lambda n: b"x" * n)

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.terminated = True
            return 0

    h = StreamHandle(FakeProc())
    assert h.alive()
    assert h.read(4) == b"xxxx"
    h.terminate()
    assert not h.alive()


def test_capture_speaks_the_measure_dialect(rig, monkeypatch):
    b, _ = rig
    seen = {}

    class FakeProc:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
            self.stdout = None
            self.stderr = None

        def poll(self):
            return None

    monkeypatch.setattr(pwb.subprocess, "Popen", FakeProc)
    b.capture(11, 1, 48000)
    cmd = seen["cmd"]
    assert cmd[0:2] == ["pw-record", "--raw"]
    props = cmd[cmd.index("-P") + 1]
    assert "node.name = pde-measure-capture" in props
    assert "node.target = 11" in props
    assert "node.dont-reconnect = true" in props
    assert "--target" not in cmd
    assert cmd[-1] == "-"


def test_play_speaks_the_measure_dialect(rig, monkeypatch):
    b, _ = rig
    seen = {}

    class FakeProc:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
            self.stdout = None
            self.stderr = None

        def poll(self):
            return None

    monkeypatch.setattr(pwb.subprocess, "Popen", FakeProc)
    b.play(10, "/tmp/x.wav", stream_volume=1.0,
           channel_map="FL,FR")
    cmd = seen["cmd"]
    assert cmd[0] == "pw-play"
    assert cmd[cmd.index("--volume") + 1] == "1.0"
    props = cmd[cmd.index("-P") + 1]
    assert "node.name = pde-measure-sweep" in props
    assert "node.target = 10" in props
    assert cmd[cmd.index("--channel-map") + 1] == "FL,FR"
    assert cmd[-1] == "/tmp/x.wav"

def _card_dump():
    """One USB card: an analog input node on card device 0, with a
    microphone route and a line-in route, and an unrelated S/PDIF
    node on card device 1. Shapes mirror pw-dump."""
    return [
        {"id": 40, "type": "PipeWire:Interface:Node",
         "info": {"props": {
             "node.name": "alsa_input.usb-0d8c-00.analog-stereo",
             "media.class": "Audio/Source",
             "device.id": 50, "card.profile.device": 0}}},
        {"id": 41, "type": "PipeWire:Interface:Node",
         "info": {"props": {
             "node.name": "alsa_input.usb-0d8c-00.iec958-stereo",
             "media.class": "Audio/Source",
             "device.id": 50, "card.profile.device": 1}}},
        {"id": 42, "type": "PipeWire:Interface:Node",
         "info": {"props": {"node.name": "virtual-thing",
                            "media.class": "Audio/Source"}}},
        {"id": 50, "type": "PipeWire:Interface:Device",
         "info": {"params": {
             "EnumRoute": [
                 {"index": 1, "direction": "Input", "name": "mic",
                  "description": "Microphone", "devices": [0],
                  "available": "unknown"},
                 {"index": 2, "direction": "Input", "name": "linein",
                  "description": "Line In", "devices": [0],
                  "available": "yes"},
                 {"index": 3, "direction": "Input", "name": "spdif",
                  "description": "Digital Input", "devices": [1],
                  "available": "no"},
                 {"index": 9, "direction": "Output", "name": "out",
                  "description": "Speakers", "devices": [0],
                  "available": "yes"}],
             "Route": [
                 {"index": 2, "device": 0, "direction": "Input",
                  "name": "linein"},
                 # the active OUTPUT route shares its number with
                 # an input port: the field's own card does this
                 {"index": 1, "device": 0, "direction": "Output",
                  "name": "out"}]}}},
    ]


def test_the_card_ports_behind_one_capture_node():
    """GNOME lists Microphone and Line In as separate entries;
    PipeWire has one node and calls the choice a route on the device
    behind it. Our picker lists nodes, so the choice was invisible."""
    d = _card_dump()
    got = pwb.input_routes(
        "alsa_input.usb-0d8c-00.analog-stereo", d)
    assert [r["description"] for r in got] == ["Microphone", "Line In"]
    assert [r["index"] for r in got] == [1, 2]
    assert [r["active"] for r in got] == [False, True]
    assert all(r["device_id"] == 50 for r in got)
    assert all(r["card_device"] == 0 for r in got)
    # the S/PDIF node is a different card device: its own port only
    other = pwb.input_routes(
        "alsa_input.usb-0d8c-00.iec958-stereo", d)
    assert [r["description"] for r in other] == ["Digital Input"]
    assert other[0]["available"] is False
    assert other[0]["active"] is False


def test_a_node_without_a_card_has_no_ports():
    d = _card_dump()
    assert pwb.input_routes("virtual-thing", d) == []
    assert pwb.input_routes("no-such-node", d) == []
    assert pwb.active_input_route("virtual-thing", d) is None


def test_the_active_port_is_what_the_passport_records():
    """"Captured with CM106" does not say whether the sweep came in
    through the microphone jack or the line one, and those are
    different measurements."""
    d = _card_dump()
    node = "alsa_input.usb-0d8c-00.analog-stereo"
    assert pwb.active_input_route(node, d) == "Line In"
    dev = [o for o in d if o["id"] == 50][0]
    dev["info"]["params"]["Route"] = [
        {"index": 1, "device": 0, "direction": "Input"}]
    assert pwb.active_input_route(node, d) == "Microphone"
    dev["info"]["params"]["Route"] = []
    assert pwb.active_input_route(node, d) is None


def test_the_ports_ride_the_same_dump_as_the_sources():
    """The window must never run a subprocess to learn the ports:
    switching a route makes the graph churn exactly when a poll is
    slowest, and reading it on the main loop froze the app for
    seconds -- long enough for the shell to offer Force Quit."""
    d = _card_dump()
    src = {s["name"]: s for s in pwb.list_sources(d)}
    analog = src["alsa_input.usb-0d8c-00.analog-stereo"]
    assert [r["description"] for r in analog["routes"]] == [
        "Microphone", "Line In"]
    assert src["virtual-thing"]["routes"] == []


def test_the_input_list_shows_one_row_per_jack():
    """Every desktop lists ports as separate inputs; so do we. A
    card with two input ports contributes two rows, described the
    way GNOME describes them, and each row carries the node it
    speaks for and the route it means."""
    rows = pwb.list_capture_entries(_card_dump())
    assert [r["name"] for r in rows][:2] == [
        "alsa_input.usb-0d8c-00.analog-stereo#1",
        "alsa_input.usb-0d8c-00.analog-stereo#2"]
    assert all(r["node"] == "alsa_input.usb-0d8c-00.analog-stereo"
               for r in rows[:2])
    assert rows[0]["desc"].startswith("Microphone - ")
    assert rows[1]["desc"].startswith("Line In - ")
    assert rows[1]["route"]["active"] is True
    # one port or none: the node speaks for itself, key unchanged
    solo = [r for r in rows if r["name"] == "virtual-thing"][0]
    assert solo["node"] == "virtual-thing" and solo["route"] is None


def test_the_graph_gets_the_node_and_the_rest_keeps_the_key():
    node = "alsa_input.usb-0d8c-00.analog-stereo"
    assert pwb.entry_key(node) == node
    assert pwb.entry_key(node, {"index": 2}) == node + "#2"
    assert pwb.split_entry(node + "#2") == (node, 2)
    assert pwb.split_entry(node) == (node, None)
    assert pwb.entry_node(node + "#2") == node
    assert pwb.entry_node(node) == node
    assert pwb.split_entry(None) == (None, None)
    # a name that merely contains a hash is not an entry key
    assert pwb.split_entry("weird#name") == ("weird#name", None)


def test_an_output_route_cannot_crown_an_input_port():
    """Route indices are not unique across directions. Matching on
    the index alone let the card's ACTIVE OUTPUT route mark an input
    port that merely shared its number -- so the app announced that
    the sweep came in through the microphone jack while the cable
    sat in line in and nothing was plugged into the mic at all."""
    d = _card_dump()
    got = {r["description"]: r["active"]
           for r in pwb.input_routes(
               "alsa_input.usb-0d8c-00.analog-stereo", d)}
    assert got == {"Microphone": False, "Line In": True}
    assert pwb.active_input_route(
        "alsa_input.usb-0d8c-00.analog-stereo", d) == "Line In"


def _gain_dump(cv, sv=None):
    props = {"channelVolumes": cv}
    if sv is not None:
        props["softVolumes"] = sv
    return [{"id": 40, "type": "PipeWire:Interface:Node",
             "info": {"props": {"node.name": "src",
                                "media.class": "Audio/Source"},
                      "params": {"Props": [props]}}}]


def test_hardware_gain_reads_apart_from_a_software_multiplier():
    """Software gain buys no headroom against an analog overload
    and improves no noise -- it scales what was already digitised.
    On a measurement rig the two are worth telling apart."""
    half = 0.125                      # cubic 0.5
    g, kind = pwb.gain_state("src", _gain_dump([half, half],
                                               [1.0, 1.0]))
    assert abs(g - 0.5) < 1e-9 and kind == "hardware"
    g, kind = pwb.gain_state("src", _gain_dump([half, half],
                                               [half, half]))
    assert abs(g - 0.5) < 1e-9 and kind == "software"
    # unity everywhere says nothing about which one it would be
    g, kind = pwb.gain_state("src", _gain_dump([1.0, 1.0],
                                               [1.0, 1.0]))
    assert abs(g - 1.0) < 1e-9 and kind is None
    # no Props at all, or no such node: an abstention, not a zero
    assert pwb.gain_state("src", _gain_dump([])) == (None, None)
    assert pwb.gain_state("nope", _gain_dump([0.5])) == (None, None)


def test_the_gain_rides_the_same_dump_as_the_sources():
    """Nothing in the measure window may shell out on the main
    loop: the capture gain travels with the source list, out of the
    graph snapshot the window already receives."""
    d = _card_dump()
    node = [o for o in d if o["id"] == 40][0]
    node["info"]["params"] = {"Props": [{
        "channelVolumes": [0.125, 0.125],
        "softVolumes": [1.0, 1.0]}]}
    src = {s["name"]: s for s in pwb.list_sources(d)}
    g, kind = src["alsa_input.usb-0d8c-00.analog-stereo"]["gain"]
    assert abs(g - 0.5) < 1e-9 and kind == "hardware"
    assert src["virtual-thing"]["gain"] == (None, None)
