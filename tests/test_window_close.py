"""What was done in the window lands when it closes. An edit's save
waits 200 ms for the next one, on a timer of the main loop, which ends
with the window; what the window wished from the server waits for the
door's writer, and the writer is a daemon thread, which dies with the
process -- and so is a sweep's worker, which gives its device back."""
import ast
import pathlib
import types

import pytest


def _tree():
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "perdeviceeq" / "gui.py").read_text(encoding="utf-8")
    return ast.parse(src)


def _class_method(cls, name):
    for c in ast.walk(_tree()):
        if isinstance(c, ast.ClassDef) and c.name == cls:
            for n in c.body:
                if isinstance(n, ast.FunctionDef) and n.name == name:
                    return n
    raise LookupError("%s.%s" % (cls, name))


def _method(cls, name, **names):
    """A method run without GTK: its own source, with the names it
    reads handed in."""
    module = ast.Module(body=[_class_method(cls, name)], type_ignores=[])
    exec(compile(module, "gui.py", "exec"), names)
    return names[name]


def _window(seen, save=None):
    def flush():
        if save is not None:
            raise save
        seen.append("save")
    return types.SimpleNamespace(
        _flush_pending_save=flush,
        _meter=types.SimpleNamespace(stop=lambda: seen.append("meter")),
        _pw_unsub=lambda: seen.append("unsub"))


def test_the_window_closes_through_its_handler():
    calls = [ast.unparse(c) for c in ast.walk(_class_method("EqWindow",
                                                           "__init__"))
             if isinstance(c, ast.Call)
             and ast.unparse(c.func) == "self.connect"]
    assert "self.connect('close-request', self._on_close)" in calls


def test_a_closing_window_lands_the_save_still_waiting():
    seen = []
    win = _window(seen)
    assert _method("EqWindow", "_on_close")(win) is False
    assert seen == ["save", "meter", "unsub"]
    assert win._pw_unsub is None


def test_a_save_that_fails_lets_the_meter_go_all_the_same():
    seen = []
    win = _window(seen, save=OSError("disk full"))
    with pytest.raises(OSError):
        _method("EqWindow", "_on_close")(win)
    assert seen == ["meter", "unsub"]


def test_the_application_lands_the_door_before_it_ends():
    seen = []
    back = types.SimpleNamespace(land=lambda t: seen.append(("land", t)))
    adw = types.SimpleNamespace(Application=types.SimpleNamespace(
        do_shutdown=lambda app: seen.append(("chain", app))))
    shutdown = _method("EqApplication", "do_shutdown",
                       pw_backend=types.SimpleNamespace(
                           backend=lambda: back),
                       Adw=adw, LAND_TIMEOUT_S=1.0)
    app = object()
    shutdown(app)
    assert seen == [("land", 1.0), ("chain", app)]
