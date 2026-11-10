"""Bypass is the listener's: it takes every output raw -- the correction,
the taste and the preamp -- and only its button releases it. A change of
output, an edit, another profile, the measurement window and a restart
leave it as it is (config.py, BYPASS_KEY)."""
import ast
import pathlib
import types


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


def _method(name, **names):
    """An EqWindow method run without GTK: its own source, with the
    names it reads handed in."""
    module = ast.Module(body=[_class_method("EqWindow", name)],
                        type_ignores=[])
    exec(compile(module, "gui.py", "exec"), names)
    return names[name]


class _Row:
    def __init__(self, on):
        self.on = on

    def get_active(self):
        return self.on


def test_a_press_is_written_under_its_own_key_and_kept():
    seen, saved = [], []
    auth = types.SimpleNamespace(
        publish_graph=lambda k, v: seen.append(("publish", k, v)))
    names = dict(pw_backend=types.SimpleNamespace(backend=lambda: auth),
                 BYPASS_KEY="@bypass", BYPASS_ON="1", STRIP="",
                 save_ui_state=lambda d: saved.append(dict(d)))
    on_bypass = _method("_on_bypass", **names)
    win = types.SimpleNamespace(
        live=True, bypass_row=_Row(True), _ui_state={"device_card": True},
        view=types.SimpleNamespace(
            set_active=lambda a: seen.append(("view", a))),
        _update_meter=lambda: seen.append("meter"),
        _update_headroom=lambda: seen.append("headroom"))
    win._publish_bypass = types.MethodType(
        _method("_publish_bypass", **names), win)
    on_bypass(win)
    assert seen == [("view", False), ("publish", "@bypass", "1"),
                    "meter", "headroom"]
    assert saved == [{"device_card": True, "bypass": True}]
    seen.clear()
    win.bypass_row.on = False
    on_bypass(win)
    assert ("publish", "@bypass", "") in seen
    assert saved[-1] == {"device_card": True, "bypass": False}


def test_only_its_button_releases_it():
    """Nothing in the window sets the button but the opening, which shows
    it as it was left: no change of output, edit, profile or measurement
    window turns it off."""
    calls = [n for n in ast.walk(_tree()) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "self.bypass_row.set_active"]
    init = _class_method("EqWindow", "__init__")
    assert [ast.unparse(c) for c in calls] == [
        "self.bypass_row.set_active(bool(self._ui_state.get('bypass')))"]
    assert init.lineno <= calls[0].lineno <= init.end_lineno


def test_the_window_opens_with_it_as_it_was_left():
    """Shown before its handler is connected -- the opening is not a
    press -- and written to the hook before the device's value, so the
    hook's table and the window's settings agree from the first turn and
    the device is not heard with its correction on the way."""
    init = _class_method("EqWindow", "__init__")

    def line(text):
        return [n.lineno for n in ast.walk(init)
                if isinstance(n, ast.Call) and ast.unparse(n) == text]

    shown = line("self.bypass_row.set_active("
                 "bool(self._ui_state.get('bypass')))")
    wired = line("self.bypass_row.connect('notify::active', "
                 "self._on_bypass)")
    loaded = line("self._load_profile(self.current_pid, apply=True)")
    written = line("self._publish_bypass()")
    assert len(shown) == len(wired) == len(loaded) == len(written) == 1
    assert shown[0] < wired[0]
    assert written[0] < loaded[0]
