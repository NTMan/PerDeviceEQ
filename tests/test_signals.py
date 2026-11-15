"""A signal ends a measuring program the way its own way out does.

Left alone, SIGTERM and SIGHUP end a Python process without running a
line of Python, and PyGObject turns SIGINT into Application.quit, which
closes no window. A sweep in the air then left the sink at the
measurement's level and unmuted, its device named to the hook and
foreign streams muted. The command-line half is proven in a child
process; the window half is the real method over a stub, and the
launcher's wiring with the application replaced.
"""
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_a_measuring_tool_runs_its_finally_on_the_signal(sig):
    code = textwrap.dedent("""
        import os, signal, time
        from perdeviceeq.audio_backend import end_on_signals
        end_on_signals()
        try:
            os.kill(os.getpid(), %d)
            time.sleep(10)
        finally:
            print("finally ran", flush=True)
    """ % int(sig))
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=30)
    assert "finally ran" in proc.stdout
    assert proc.returncode == 128 + int(sig)


@pytest.fixture
def gui():
    """The window half needs gi; a sandbox without it skips here and
    keeps the command-line half."""
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
    except ValueError as e:      # gi without the typelibs
        pytest.skip(str(e))
    from perdeviceeq import gui as module
    return module


class _Win:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def close(self):
        self.log.append(("close", self.name))


class _App:
    def __init__(self, windows):
        self.log = []
        self._windows = [_Win(self.log, n) for n in windows]

    def get_windows(self):
        return list(self._windows)

    def quit(self):
        self.log.append(("quit",))


def test_the_signal_closes_the_windows_as_the_user_would(gui):
    """The main window's close cancels a take and saves a pending
    edit; the last window gone ends the application."""
    app = _App(["main", "measure"])
    gui.EqApplication.end_by_signal(app)
    assert app.log == [("close", "main"), ("close", "measure")]


def test_with_no_window_the_signal_ends_the_application(gui):
    app = _App([])
    gui.EqApplication.end_by_signal(app)
    assert app.log == [("quit",)]


def test_the_launcher_hands_the_three_signals_to_the_application(
        gui, monkeypatch):
    got = {}
    ended = []

    class FakeApp:
        def end_by_signal(self):
            ended.append(True)

        def run(self, _argv):
            return 0

    monkeypatch.setattr(gui, "EqApplication", FakeApp)
    monkeypatch.setattr(signal, "signal",
                        lambda s, h: got.__setitem__(s, h))
    assert gui.launch_gui() == 0
    assert set(got) == {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    got[signal.SIGTERM](signal.SIGTERM, None)
    assert ended == [True]
