"""The WirePlumber hook, run against a fake WirePlumber
(tests/hook_harness.lua), and the contract it shares with the app.

The hook cannot be imported from Python and was until now checked only in
the field. The harness loads the real file with WirePlumber's objects
replaced by tables and drives it through the events it answers: devices,
sinks, the metadata and its writes, a node reaching running.
"""
import pathlib
import re
import shutil
import subprocess

import pytest

from perdeviceeq import config, eq

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / "wireplumber" / "90-per-device-eq.lua"
LUA = shutil.which("lua") or shutil.which("lua5.4")


def _lua_const(name):
    m = re.search(r'^local %s\s*=\s*"([^"]*)"' % name,
                  HOOK.read_text(encoding="utf-8"), re.M)
    assert m, name
    return m.group(1)


def test_the_hook_and_the_app_share_one_contract():
    assert _lua_const("PROTOCOL") == config.PROTOCOL
    assert _lua_const("LISTENER") == config.LISTENER_KEY
    assert _lua_const("STRIP") == config.STRIP


# ---- what the app writes, read by the hook -----------------------------------

KEY = "bluez_output.AA.1#headset-output"      # the harness's device
PAIR = {"floor_hz": 40.0, "ch_keys": ["FL", "FR"],
        "channels": {"FL": {"bands": [{"type": "PK", "freq": 3000,
                                       "gain": 4.0, "q": 2.0}]},
                     "FR": {"bands": [{"type": "PK", "freq": 3000,
                                       "gain": 2.5, "q": 2.0}]}}}
TASTE = [{"type": "LSC", "freq": 105, "gain": 6.0, "q": 0.7}]


def _long(text):
    """A Lua long string holding `text` as it is."""
    level = 0
    while "]%s]" % ("=" * level) in text:
        level += 1
    return "[%s[%s]%s]" % ("=" * level, text, "=" * level)


def _app_values(path):
    """Values as the app makes them, with the graphs they should give,
    for the harness's last scenario."""
    slots = ["FL", "FR"]
    correction = eq.correction_graph(PAIR, slots)
    own = eq.gain_graph(-3.0)
    auto = eq.gain_graph(eq.preamp_db(PAIR, slots, TASTE))
    star = eq.gain_graph(eq.preamp_db({}, None, TASTE))
    taste = eq.taste_graph(TASTE)
    values = {
        "correction": correction, "taste": taste, "own_gain": own,
        "auto_gain": auto, "star_gain": star,
        "device": eq.device_value(correction, own),
        "listener": eq.listener_value(taste, {"*": star}),
        "listener_auto": eq.listener_value(taste, {"*": star, KEY: auto}),
        "device_kept": eq.device_value(correction, auto),
    }
    assert all(values.values())
    path.write_text("return {\n%s\n}\n" % ",\n".join(
        "  %s = %s" % (k, _long(v)) for k, v in values.items()),
        encoding="utf-8")


@pytest.mark.skipif(LUA is None, reason="no Lua interpreter")
def test_the_hook_against_a_fake_wireplumber(tmp_path):
    app = tmp_path / "app.lua"
    _app_values(app)
    r = subprocess.run([LUA, str(ROOT / "tests" / "hook_harness.lua"),
                        str(HOOK), str(app)], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok   what the app writes is read as it means" in r.stdout
