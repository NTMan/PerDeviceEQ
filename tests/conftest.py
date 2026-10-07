"""Shared pytest plumbing for per-device-eq audit tests.

Fixtures are generated on the fly by tools/make_fixtures.py (deterministic,
seed-pinned) — no binary test data is stored in git.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))   # CLI tools (audit_*, etc.)
sys.path.insert(0, str(ROOT))             # perdeviceeq package


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("fixtures")
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_fixtures.py"), str(out)],
        check=True,
        capture_output=True,
    )
    return out


@pytest.fixture(autouse=True)
def _listener_state_is_the_test_s(tmp_path, monkeypatch):
    """The hook feed reads the listener's layers -- the active taste and
    the preamp ride -- from the user's config. A test must not hear the
    taste or the ride of whoever runs it."""
    from perdeviceeq import config, preferences
    monkeypatch.setattr(preferences, "PREF_LAYERS_FILE",
                        str(tmp_path / "preference-layers.json"))
    monkeypatch.setattr(config, "UI_STATE_FILE",
                        str(tmp_path / "ui-state.json"))
