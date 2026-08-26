"""A tab carries its own meter."""
import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    if not Gtk.init_check():
        pytest.skip("no display", allow_module_level=True)
except Exception:                                   # noqa: BLE001
    pytest.skip("no GTK", allow_module_level=True)

from perdeviceeq.chantabs import ChannelTabs


def _tabs(meters):
    box = Gtk.Box()
    t = ChannelTabs(box, lambda k: None)
    t.rebuild(["A", "B", "C"], meters=meters)
    return t


def test_every_tab_gets_one_not_just_the_chosen():
    t = _tabs(True)
    assert sorted(k for k, _ in t.meters()) == ["A", "B", "C"]


def test_without_meters_a_tab_has_none():
    t = _tabs(False)
    assert t.meters() == []
    assert t.meter("A") is None
