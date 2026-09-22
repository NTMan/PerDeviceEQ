"""The rig is the one a hand chose, not the one history recorded.

The measure window used to pick its microphone from the profile's
LAST SITTING -- the identity stored with the newest take -- and that
choice outranked the hand's own. It is a closed circle the moment the
recorded rig stops existing: the pick is overruled at every opening,
a take cannot be recorded through a microphone that is not there, and
so the last sitting never changes to say otherwise.

History does not decide the present. A stored sitting is a fact about
takes already made, the picker is a fact about what is plugged in
now, and only two things may move it from code: the memory of what a
hand chose for this sink, and a port arriving after a card switch a
hand asked for.

AST over the source, because no test builds a window.
"""

import ast
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "perdeviceeq" / "measure_window.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)

MOVERS = {"_prefill_from_memory", "_adopt_pending_port"}


def _methods():
    for node in ast.walk(TREE):
        if isinstance(node, ast.ClassDef):
            for fn in node.body:
                if isinstance(fn, (ast.FunctionDef,
                                   ast.AsyncFunctionDef)):
                    yield node.name, fn


def _calls_select(fn):
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "select"
                and isinstance(f.value, ast.Attribute)
                and f.value.attr == "mic_picker"):
            return True
    return False


def test_only_the_hands_memory_moves_the_mic_picker():
    movers = {fn.name for _cls, fn in _methods() if _calls_select(fn)}
    assert movers == MOVERS, (
        "the mic picker is moved from code by %s; only %s may"
        % (sorted(movers), sorted(MOVERS)))
