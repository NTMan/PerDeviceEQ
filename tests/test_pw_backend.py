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
    # every port the card has rides along now, each saying whether
    # reaching it costs a profile change
    got = {r["description"]: r["reachable"] for r in analog["routes"]}
    assert got == {"Microphone": True, "Line In": True,
                   "Digital Input": False}
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


# ---- a node's channel NAMES come from its ports ---------------------------

def _m62_dump():
    """The M62 in its ten-channel mode, as pw-dump really shows it: the
    ports are AUX0..AUX9 while the negotiated Format still repeats the
    firmware's surround fiction."""
    return [{"id": 137, "type": "PipeWire:Interface:Node",
             "info": {"props": {
                 "node.name": "m62.direct",
                 "audio.position": "[ AUX0, AUX1, AUX2, AUX3, AUX4, "
                                   "AUX5, AUX6, AUX7, AUX8, AUX9 ]"},
                      "params": {"Format": [{
                          "channels": 10,
                          "position": ["FL", "FR", "FC", "LFE", "RL",
                                       "RR", "FLC", "FRC", "RC",
                                       "SL"]}]}}}]


def test_channel_names_come_from_the_ports_not_the_format():
    from perdeviceeq import pw_backend as pw
    keys = pw._node_channels("m62.direct", _m62_dump())
    assert keys == ["AUX%d" % i for i in range(10)]


def test_the_format_still_answers_when_the_ports_do_not():
    from perdeviceeq import pw_backend as pw
    d = _m62_dump()
    d[0]["info"]["props"].pop("audio.position")
    assert pw._node_channels("m62.direct", d)[:3] == ["FL", "FR", "FC"]


def test_a_repeated_channel_name_still_addresses_one_channel_each():
    from perdeviceeq import pw_backend as pw
    d = _m62_dump()
    d[0]["info"]["props"]["audio.position"] = "[ MONO, MONO ]"
    assert pw._node_channels("m62.direct", d) == ["MONO", "MONO.1"]


def _m62_ports_dump():
    """The same node with its ports: audio.position says AUX, the ports
    say FL..SL. Linking matches the PORTS."""
    d = _m62_dump()
    names = ["FL", "FR", "FC", "LFE", "RL", "RR", "FLC", "FRC", "RC", "SL"]
    for i, ch in enumerate(names):
        for direction in ("in", "out"):
            d.append({"id": 200 + i * 2 + (direction == "out"),
                      "type": "PipeWire:Interface:Port",
                      "info": {"props": {
                          "node.id": "137", "port.direction": direction,
                          "audio.channel": ch, "port.id": str(i)}}})
    return d


def test_a_tap_asks_for_the_names_the_ports_carry():
    from perdeviceeq import pw_backend as pw
    d = _m62_ports_dump()
    assert pw.monitor_channels("m62.direct", d) == [
        "FL", "FR", "FC", "LFE", "RL", "RR", "FLC", "FRC", "RC", "SL"]
    # ...which is NOT what the tabs wear, and that is the whole point
    assert pw._node_channels("m62.direct", d)[0] == "AUX0"


def test_no_ports_means_no_map_rather_than_a_wrong_one():
    from perdeviceeq import pw_backend as pw
    assert pw.monitor_channels("m62.direct", _m62_dump()) == []
    assert pw.monitor_channels("nope", _m62_ports_dump()) == []


# ---- ports the current profile cannot reach ------------------------------

def _m62_card_dump():
    """His M62 as pw-dump really shows it: the Direct profile is
    loaded, and Mic 1, Mic 2 and Aux stereo in sit in Default."""
    classes = lambda src, snk: [2,
                                ["Audio/Source", len(src),
                                 "card.profile.devices", list(src)],
                                ["Audio/Sink", len(snk),
                                 "card.profile.devices", list(snk)]]
    return [
        {"id": 346, "type": "PipeWire:Interface:Device",
         "info": {"props": {"device.name": "alsa_card.usb-Topping_M62-00",
                            "device.description": "Topping M62"},
                  "params": {
                      "Profile": [{"index": 2,
                                   "description": "Direct M62"}],
                      "EnumProfile": [
                          {"index": 0, "description": "Off",
                           "classes": [0]},
                          {"index": 1, "description": "Default",
                           "classes": classes([7, 8, 9],
                                              [2, 3, 4, 5, 6])},
                          {"index": 2, "description": "Direct M62",
                           "classes": classes([1], [0])},
                          {"index": 3, "description": "Pro Audio",
                           "classes": classes([11], [10])}],
                      "Route": [{"index": 1, "device": 1,
                                 "direction": "Input"}],
                      "EnumRoute": [
                          {"index": 1, "direction": "Input",
                           "description": "Direct M62",
                           "devices": [1], "profiles": [2]},
                          {"index": 7, "direction": "Input",
                           "description": "Aux stereo in",
                           "devices": [7], "profiles": [1]},
                          {"index": 8, "direction": "Input",
                           "description": "Mic 2",
                           "devices": [8], "profiles": [1]},
                          {"index": 9, "direction": "Input",
                           "description": "Mic 1",
                           "devices": [9], "profiles": [1]}]}}},
        {"id": 177, "type": "PipeWire:Interface:Node",
         "info": {"props": {
             "node.name": "m62.source", "media.class": "Audio/Source",
             "node.description": "Direct M62",
             "device.id": 346, "card.profile.device": 1}}}]


def test_every_input_port_is_named_even_behind_another_profile():
    from perdeviceeq import pw_backend as pw
    rs = pw.card_input_ports("m62.source", _m62_card_dump())
    got = {r["description"]: r for r in rs}
    assert set(got) == {"Direct M62", "Aux stereo in", "Mic 2", "Mic 1"}
    assert got["Direct M62"]["reachable"] and got["Direct M62"]["active"]
    for name in ("Mic 1", "Mic 2", "Aux stereo in"):
        assert not got[name]["reachable"]
        assert got[name]["profiles"] == [1]      # Default carries them
        assert not got[name]["active"]


def test_the_row_list_still_offers_only_what_is_reachable():
    """Deliberately unchanged for now: a row's key carries the node it
    speaks for, and a port behind another profile has no node yet."""
    from perdeviceeq import pw_backend as pw
    d = _m62_card_dump()
    src = {"name": "m62.source", "desc": "Direct M62", "prio": 0,
           "routes": pw.input_routes("m62.source", d)}
    assert len(pw.card_input_ports("m62.source", d)) == 4
    rows = pw.list_capture_entries_from([src])
    assert len(rows) == 1
    assert rows[0]["node"] == "m62.source"


def test_the_profiles_and_what_each_one_would_give():
    from perdeviceeq import pw_backend as pw
    ps = {p["description"]: p for p
          in pw.card_profiles("m62.source", _m62_card_dump())}
    assert ps["Direct M62"]["active"] is True
    assert ps["Direct M62"]["sources"] == [1]
    assert ps["Direct M62"]["sinks"] == [0]
    # the cost of reaching Mic 1: five sinks where there was one
    assert ps["Default"]["sources"] == [7, 8, 9]
    assert ps["Default"]["sinks"] == [2, 3, 4, 5, 6]
    assert ps["Off"]["sources"] == [] and ps["Off"]["sinks"] == []


def test_a_port_behind_another_profile_gets_a_row_of_its_own():
    """It is listed, because the desktop lists it, and its row says
    what choosing it costs. It speaks for no node until the card is
    switched -- which is what its key names: the card, not a node."""
    from perdeviceeq import pw_backend as pw
    d = _m62_card_dump()
    rows = pw.list_capture_entries_from(pw.list_sources(d))
    doors = [r for r in rows if r["node"] is None]
    assert [r["route"]["description"] for r in doors] == [
        "Aux stereo in", "Mic 2", "Mic 1"]
    # named the way the desktop names it: port, then card
    assert doors[-1]["desc"] == "Mic 1 - Topping M62"
    dev, idx = pw.card_entry_target(doors[-1]["name"])
    assert (dev, idx) == (346, 9)                 # Mic 1
    assert pw.is_card_entry(doors[-1]["name"])
    # and a real row is still a real row
    real = [r for r in rows if r["node"]]
    assert [r["node"] for r in real] == ["m62.source"]
    assert pw.card_entry_target(real[0]["name"]) == (None, None)


def _m62_with_outputs(active):
    """The card with its output ports, on a chosen profile: 2 is Direct
    (one sink, card device 0), 1 is Default (five sinks, devices 2..6)."""
    d = _m62_card_dump()
    d[0]["info"]["params"]["Profile"] = [{"index": active}]
    d[0]["info"]["params"]["EnumRoute"] += [
        {"index": 0, "direction": "Output", "description": "Direct M62",
         "devices": [0], "profiles": [2]}] + [
        {"index": 20 + i, "direction": "Output",
         "description": "Playback %d/%d" % (2 * i + 1, 2 * i + 2),
         "devices": [2 + i], "profiles": [1]} for i in range(5)]
    if active == 2:
        nodes = [(0, "Direct M62")]
    else:
        nodes = [(2 + i, "M62 Playback %d/%d" % (2 * i + 1, 2 * i + 2))
                 for i in range(5)]
    for dev, desc in nodes:
        d.append({"id": 400 + dev, "type": "PipeWire:Interface:Node",
                  "info": {"props": {
                      "node.name": "m62.sink.%d" % dev,
                      "media.class": "Audio/Sink",
                      "node.description": desc,
                      "device.id": 346,
                      "card.profile.device": dev}}})
    return d


def test_an_output_behind_another_profile_gets_a_door():
    """On Direct there is one output node, and the five outputs of the
    Default profile are reachable only by switching the card."""
    from perdeviceeq import pw_backend as pw
    rows = pw.list_playback_entries_from(
        pw.list_sinks(_m62_with_outputs(2), default=""))
    doors = [r for r in rows if r["node"] is None]
    assert [r["route"]["description"] for r in doors] == [
        "Playback 1/2", "Playback 3/4", "Playback 5/6",
        "Playback 7/8", "Playback 9/10"]
    # a door names its CARD too: several cards have a port called
    # Speakers, and a row that says only "Speakers" is a coin toss
    assert doors[0]["desc"] == "Playback 1/2 - Topping M62"
    real = [r for r in rows if r["node"]]
    assert [r["desc"] for r in real] == ["Direct M62"]


def test_live_siblings_of_one_profile_are_not_doors():
    """His field catch: on Default the card has FIVE output nodes, and
    every one of them offered to switch the card to the other four --
    a door beside each live sibling. Reachability is a question about
    the PROFILE, not about which node happens to carry the port."""
    from perdeviceeq import pw_backend as pw
    rows = pw.list_playback_entries_from(
        pw.list_sinks(_m62_with_outputs(1), default=""))
    doors = [r for r in rows if r["node"] is None]
    assert [r["route"]["description"] for r in doors] == ["Direct M62"]
    assert len([r for r in rows if r["node"]]) == 5




def test_a_port_with_nothing_plugged_in_is_not_offered():
    """A graphics card enumerates one output per connector: with a
    single television that is one available port and three that lead
    nowhere. The desktop filters on availability; the list grew four
    HDMI rows because we did not."""
    from perdeviceeq import pw_backend as pw
    d = _m62_with_outputs(2)
    for r in d[0]["info"]["params"]["EnumRoute"]:
        if r.get("description", "").startswith("Playback 5"):
            r["available"] = "no"
    rows = pw.list_playback_entries_from(pw.list_sinks(d, default=""))
    doors = [r["route"]["description"] for r in rows if r["node"] is None]
    assert "Playback 5/6" not in doors
    assert "Playback 7/8" in doors


def test_the_switch_says_what_to_watch_for():
    """Vetoing the pick is only half of it: the row chosen is about to
    stop existing and the one that replaces it has a name nobody can
    know yet, so the switch reports the port and the window waits for
    it. Without this the first pick only killed the old output and the
    person had to choose again from the refreshed list."""
    from perdeviceeq import pw_backend as pw
    d = _m62_with_outputs(1)                    # Default is loaded
    rows = pw.list_playback_entries_from(pw.list_sinks(d, default=""))
    door = next(r for r in rows if r["node"] is None)
    assert pw.card_entry_target(door["name"])[0] == 346

    calls = []
    real = pw.in_thread
    pw.in_thread = lambda fn: calls.append(fn)
    try:
        want = pw.switch_to_port(door["name"], rows)
    finally:
        pw.in_thread = real
    assert want == (346, 0)                     # the Direct sink device
    assert len(calls) == 1

    # nothing carries it yet, and once the card has moved it is found
    assert pw.find_port_entry(rows, want) is None
    after = pw.list_playback_entries_from(
        pw.list_sinks(_m62_with_outputs(2), default=""))
    hit = pw.find_port_entry(after, want)
    assert hit is not None and hit["desc"] == "Direct M62"


def test_a_sweep_is_aimed_with_the_port_names():
    """Labels come from audio.position, routing comes from the PORTS.
    The M62 answers AUX0..AUX9 for its position list while its ports
    are called FL..SL, so a sweep tagged AUX0 matched no port at all
    and was spread over the whole card instead of one channel."""
    from perdeviceeq import pw_backend as pw
    from perdeviceeq import measure_session as ms
    d = _m62_ports_dump()
    d.append({"id": 137, "type": "PipeWire:Interface:Node",
              "info": {"props": {"node.name": "m62.sink",
                                 "media.class": "Audio/Sink",
                                 "device.id": 346,
                                 "card.profile.device": 0}}})
    # _m62_ports_dump already gives node 137 its ports, both ways
    assert pw.playback_channels("m62.sink", d)[:3] == ["FL", "FR", "FC"]
    assert ms._aim_layout("m62.sink", d)[:3] == ["FL", "FR", "FC"]
    # the label list is the other one, and stays that way
    assert pw._node_channels("m62.direct", d)[0] == "AUX0"


def test_without_ports_the_position_list_still_answers():
    from perdeviceeq import measure_session as ms
    assert ms._aim_layout("m62.direct", _m62_dump())[0] == "AUX0"


def test_a_sink_entry_carries_its_channels():
    """The window must never run a subprocess to learn them. Asking
    pw_backend directly costs a pw-dump, and the tab row and the Add
    menu ask at open and on every refresh -- which is what made the
    measurement window take a visible moment to appear. The dump is
    already in hand where the list is built, so the channels ride
    along, the way the ports already do."""
    from perdeviceeq import pw_backend as pw
    d = _m62_with_outputs(2)
    sink = next(s for s in pw.list_sinks(d, default="")
                if s["name"] == "m62.sink.0")
    # the same answer the direct reader gives, from the same dump --
    # that is the whole point, not any particular list
    assert sink["channels"] == pw._node_channels("m62.sink.0", d)
    assert sink["channels"], "a sink always has channels"
    assert sink["routes"], "the ports ride along too"


def test_a_source_entry_carries_its_channels_too():
    """The sink side learned this and the source side had not, so
    _mic_channels -- called from the prefill, from the rig selection
    and from every pair change -- paid a pw-dump subprocess each time.
    Removing the stored capsule count was right; replacing it with a
    trip outside was not."""
    from perdeviceeq import pw_backend as pw
    d = _m62_card_dump()
    src = next(s for s in pw.list_sources(d)
               if s["name"] == "m62.source")
    assert src["channels"] == pw._node_channels("m62.source", d)
    assert src["channels"], "a source always has channels"


def test_the_heartbeat_hands_its_dump_over():
    """The window opens on the picture the heartbeat already paid for.
    Pulling a second one bought freshness worth one tick and cost half
    the window's opening time."""
    from perdeviceeq import audio_backend as ab
    assert hasattr(ab.AudioBackend, "last_dump")
    assert ab.AudioBackend.last_dump is None, "born empty, filled by a pull"


def test_a_door_speaks_for_no_node_by_any_field():
    """A door row is a port behind another card profile. It is built
    as a COPY of the live source, so it used to keep that source's id
    and channel list while declaring node = None -- and a caller that
    reached past `node` for `id` got a working handle to a device the
    person had not chosen. The input meter did that and metered a
    stranger while the banner said the mic was gone."""
    from perdeviceeq import pw_backend as pw
    src = {"name": "m62.direct", "id": 61, "desc": "M62 Direct",
           "channels": ["AUX%d" % i for i in range(16)],
           "routes": [{"index": 1, "device_id": 7, "mine": True,
                       "reachable": True, "description": "Direct",
                       "available": True, "profiles": [2]},
                      {"index": 7, "device_id": 7, "mine": False,
                       "reachable": False, "description": "Aux",
                       "available": True, "profiles": [1]}]}
    doors = [e for e in pw.list_capture_entries_from([src])
             if e.get("node") is None]
    assert doors, "the unreachable port is offered as a door"
    for d in doors:
        assert d["id"] is None
        assert d["channels"] == []


def test_a_channel_list_arrives_as_text_with_brackets():
    """pw-dump hands audio.position as SPA JSON text. A second parser
    that split it on commas printed "[ AUX0" and "AUX15 ]" in the
    field -- one parser for one question."""
    from perdeviceeq import pw_backend as pw
    assert pw._parse_position("[ AUX0, AUX1 ]") == ["AUX0", "AUX1"]
    assert pw._parse_position(["AUX0", "AUX1"]) == ["AUX0", "AUX1"]
    assert pw._parse_position("[ FL, FR ]") == ["FL", "FR"]
    assert pw._parse_position(None) == []


def test_a_column_gain_is_read_from_the_route_it_is_written_to():
    """A hardware capture gain is set on the device's Route, and the
    node's own Props do not always follow: the field saw a route
    holding the two values that had just been set while the node still
    reported the old one for the first column, so the window seeded
    its fader from a number the card did not hold."""
    from perdeviceeq.pw_backend import _column_gains
    node = {"info": {"params": {"Props": [
        {"channelVolumes": [0.008, 0.822567]}]}}}       # the stale node
    routes = [{"active": True,
               "channel_volumes": [0.125, 0.822567]}]   # the card's truth
    got = _column_gains(node, routes)
    assert got[0] == pytest.approx(0.5, abs=1e-4)       # 50%, as set
    # the graph prints channelVolumes rounded, so the cube root lands
    # a whisker under the exact 0.9375
    assert got[1] == pytest.approx(0.9375, abs=1e-3)


def test_a_route_without_channels_falls_back_to_the_node():
    """Which is what a device with one shared control actually has."""
    from perdeviceeq.pw_backend import _column_gains
    node = {"info": {"params": {"Props": [
        {"channelVolumes": [0.008, 0.822567]}]}}}
    got = _column_gains(node, [{"active": True}])
    assert got[0] == pytest.approx(0.2, abs=1e-4)
    assert _column_gains(node, []) == pytest.approx(got, abs=1e-4)
