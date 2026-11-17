"""A push that brings nothing new is no step of the history.

The redo branch used to go first and the comparison after, so an edit
that changed nothing -- a field re-reading its own rounded text, a
click on a graph handle without a drag -- landing after an undo took
the redo away and added nothing. Runs the real method over a stub
window. Needs gi for the module; a GTK-less sandbox skips."""
import types

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from perdeviceeq import gui                    # noqa: E402

A = {"pid": "p", "bands": [822.37]}
B = {"pid": "p", "bands": [823.37]}
C = {"pid": "p", "bands": [900.0]}


def _win(hist, hidx, now, pending=None):
    w = types.SimpleNamespace(_hist=[dict(h) for h in hist], _hidx=hidx,
                              _pending_sel=pending)
    w._snapshot = lambda: dict(now)
    w._update_undo_buttons = lambda: None
    return w


def _push(w, **kw):
    gui.EqWindow._push_history(w, **kw)
    return [{k: v for k, v in h.items() if k != "sel"} for h in w._hist]


def test_an_edit_that_changes_nothing_keeps_the_redo():
    w = _win([A, B], 0, A)                    # one undo back from B
    assert _push(w) == [A, B]
    assert w._hidx == 0


def test_an_edit_that_changes_something_drops_the_redo():
    w = _win([A, B], 0, C)
    assert _push(w) == [A, C]
    assert w._hidx == 1


def test_nothing_new_leaves_a_deferred_switch_deferred():
    """A switch made mid-history waits for a real edit to enter it."""
    switch = {"pid": "q", "bands": []}
    w = _win([A, B], 0, switch, pending=dict(switch))
    assert _push(w) == [A, B]
    assert w._hidx == 0 and w._pending_sel == switch


def test_a_real_edit_enters_the_deferred_switch_first():
    switch = {"pid": "q", "bands": []}
    edit = {"pid": "q", "bands": [100.0]}
    w = _win([A, B], 0, edit, pending=dict(switch))
    assert _push(w) == [A, switch, edit]
    assert w._hist[1].get("sel") and w._hidx == 2
