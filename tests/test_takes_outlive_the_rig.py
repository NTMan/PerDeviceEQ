"""The takes on the canvas belong to the profile, not to the rig.

His rule, after a UCM rename left a profile's recorded microphone
under a name no node carries any more: the hardware that made a
measurement may be renamed, unplugged or long dead while the profile
is alive and being read, so a window must never hide recorded takes
for want of a live microphone.

The canvas lives in the session, so this is a statement about where
the session is allowed to refuse: construction is silent and
unconditional, and every refusal belongs to the arming half, which is
what a sweep has to get past. Source text, because no test builds a
window.
"""

import pathlib
import re

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "perdeviceeq" / "measure_window.py").read_text(encoding="utf-8")


def _body(name):
    m = re.search(r"\n    def %s\(self.*?(?=\n    def )" % name, SRC,
                  re.S)
    assert m, "method %s not found" % name
    return m.group(0)


def test_building_a_session_has_no_preconditions():
    """Everything before the config is built must be silent. What
    stands after it is the construction FAILING -- missing tools, a
    graph that refuses -- which is a different thing and still has
    to be reported."""
    body = _body("_ensure_session")
    head, sep, _rest = body.partition("cfg = ms.SessionConfig(")
    assert sep, "_ensure_session no longer builds a SessionConfig"
    bad = [ln for ln in head.splitlines()
           if "self._error(" in ln or "return False" in ln]
    assert not bad, (
        "the construction half of _ensure_session refuses before it "
        "builds anything, so a profile's takes stay invisible "
        "without a live rig:\n" + "\n".join(bad))


def test_a_first_session_can_still_be_built():
    """_rebuild_session is the one door a rig change goes through,
    and its guard made that door unreachable for a window that had
    opened with nothing plugged in."""
    body = _body("_rebuild_session")
    code = body.split('"""')[-1]
    assert "if self.session is None:" not in code, (
        "_rebuild_session returns early when there is no session, so "
        "picking a mic in an open window builds none")
