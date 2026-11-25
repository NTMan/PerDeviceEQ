"""An undo takes back the edit whose step is still waiting.

An edit's undo step lands on the save timer, 200 ms after it, and
Undo navigated only the steps already landed: Ctrl+Z pressed sooner
went back past the edit to the step before it, the timer then found
the restored state on record, and the edit was gone -- Redo did not
bring it back. Runs the real methods over a stub window. Needs gi; a
GTK-less sandbox skips."""
import functools
import types

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from gi.repository import GLib                 # noqa: E402

from perdeviceeq import gui                    # noqa: E402

A, B, C = ({"pid": "p", "bands": [g]} for g in (1.0, 2.0, 3.0))


@pytest.fixture
def win():
    """A window whose history holds `hist`, standing at `hidx`, with
    the state `now` just edited: its step waits on the save timer."""
    made = []

    def make(hist, hidx, now):
        w = types.SimpleNamespace(
            _hist=[dict(h) for h in hist], _hidx=hidx, _pending_sel=None,
            _restoring=False, _step_due=False, _save_source=0,
            _dev_dirty=False, _born_pending=None, state=dict(now))
        w._snapshot = lambda: dict(w.state)
        w._restore = lambda snap: setattr(w, "state", {
            k: v for k, v in snap.items() if k not in ("sel", "born")})
        w._snap_alive = lambda snap: True
        w._update_undo_buttons = lambda: None
        for name in ("_push_history", "_schedule_save", "_save_now",
                     "_land_save", "_undo", "_redo"):
            setattr(w, name,
                    functools.partial(getattr(gui.EqWindow, name), w))
        w._schedule_save()                     # the edit's step waits
        made.append(w)
        return w
    yield make
    for w in made:                             # a timer the test left
        if w._save_source:
            GLib.source_remove(w._save_source)


def _bands(w):
    return [h["bands"][0] for h in w._hist]


def test_an_undo_at_once_takes_back_the_waiting_edit(win):
    w = win([A, B], 1, C)
    w._undo()
    assert w.state == B and _bands(w) == [1.0, 2.0, 3.0]
    w._redo()
    assert w.state == C


def test_a_redo_at_once_meets_the_edit_made_after_an_undo(win):
    """An edit after an undo drops the redo branch, at once as after
    the timer; the redo finds nothing to bring back, and the edit
    stays."""
    w = win([A, B], 0, C)
    w._redo()
    assert w.state == C and _bands(w) == [1.0, 3.0]
