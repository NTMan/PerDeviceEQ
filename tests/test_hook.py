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

from perdeviceeq import config

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
    assert _lua_const("COMMON") == config.COMMON_KEY
    assert _lua_const("STRIP") == config.STRIP


@pytest.mark.skipif(LUA is None, reason="no Lua interpreter")
def test_the_hook_against_a_fake_wireplumber():
    r = subprocess.run([LUA, str(ROOT / "tests" / "hook_harness.lua"),
                        str(HOOK)], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
