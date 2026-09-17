"""Editing a profile opens the window on a NODE.

A binding is keyed by device -- the node and the hole in use -- and
the measure window is opened on a node name. The chooser compared the
two lists as if they were one, matched nothing, and fell through to
the binding itself, so the window was handed a key no sink answers to
and said the output device was gone while it was playing. It bit a
profile with no takes, where there is no last sitting to fall back
on.

The real method unbound over a stub self. It needs gi (gui imports
GTK at the top), so a GTK-less sandbox skips and CI with xvfb judges.
"""

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                      # gi without the typelibs
    pytest.skip(str(e), allow_module_level=True)

from perdeviceeq import gui, pw_backend       # noqa: E402

NODE = "alsa_output.usb-Topping_M62-00.HiFi__Line1__sink"
KEY = NODE + "#Line1"


class _Obj:
    pass


def _app(monkeypatch, bindings, sinks, opened):
    monkeypatch.setattr(pw_backend, "live_device_key",
                        lambda n: KEY if n == NODE else n)
    a = _Obj()
    a.live = True
    a.node = "some-other-sink"
    a._measure_win = None
    a.profile_popover = _Obj()
    a.profile_popover.popdown = lambda: None
    a.store = _Obj()
    a.store.bindings = dict(bindings)
    a.sinks = [{"name": n, "desc": n} for n in sinks]
    a._open_measure_for = lambda node, pid: opened.append((node, pid))
    return a


def test_a_profile_with_no_takes_opens_on_its_bound_node(monkeypatch):
    opened = []
    a = _app(monkeypatch, {KEY: "p1"}, [NODE], opened)
    gui.EqWindow._edit_profile(a, {"id": "p1"})
    assert opened == [(NODE, "p1")]


def test_where_it_was_last_measured_still_wins(monkeypatch):
    opened = []
    other = "alsa_output.other"
    a = _app(monkeypatch, {KEY: "p1"}, [NODE, other], opened)
    p = {"id": "p1", "measurement": {
        "takes": [{"session": "s1"}],
        "sessions": {"s1": {"sink": {"node_name": other}}}}}
    gui.EqWindow._edit_profile(a, p)
    assert opened == [(other, "p1")]


def test_a_bound_device_that_is_not_here_opens_on_its_node(monkeypatch):
    """Honestly gone: a node name that no live sink carries, so the
    window raises its banner instead of being handed a key."""
    opened = []
    a = _app(monkeypatch, {KEY: "p1"}, [], opened)
    gui.EqWindow._edit_profile(a, {"id": "p1"})
    assert opened == [(NODE, "p1")]
