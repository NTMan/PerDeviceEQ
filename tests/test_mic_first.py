"""The microphone's own card asks nothing of the profile.

His three-step order: the microphone is settled first and needs to
know nothing, then the target, then the level. The test that follows
from it -- if a control needs to know a target, it does not belong in
the microphone's card -- and the field case that minted this court: a
card declaring no spatial names starts with an empty target list, and
the door to declare a capture channel was shut until targets existed.
Source text, because no test builds a window.
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


def test_the_capture_channel_door_does_not_wait_for_a_target():
    body = _body("_build_col_row")
    gates = [ln for ln in body.splitlines()
             if "set_sensitive" in ln and "ch_keys" in ln]
    assert not gates, (
        "a control in the microphone's card is gated on the profile's "
        "targets:\n" + "\n".join(gates))
