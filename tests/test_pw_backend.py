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
