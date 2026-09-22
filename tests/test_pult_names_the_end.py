"""A locked pult names the end of the chain that actually failed.

The field case: a microphone plugged in, answering and metering, and
the window saying "mic offline" under its name. It was not a fact
about the microphone at all -- nothing had been pointed at the target
yet, the rung that says so was gated on a capture channel having been
declared, and the offline line sat at the bottom of the ladder as a
catch-all, so it collected everything the rungs above it let past.

A verdict that names the wrong end is worse than none: the field goes
and looks for a breakage there. So each rung speaks only for the end
it tests, and the leftovers are nobody's.

AST over the source, because no test builds a window.
"""

import ast
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "perdeviceeq" / "measure_window.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def _method(name):
    for node in ast.walk(TREE):
        if (isinstance(node, ast.FunctionDef) and node.name == name):
            return node
    raise AssertionError("method %s not found" % name)


def _seg(node):
    return ast.get_source_segment(SRC, node) or ""


def _assigns_miss(branch):
    """A rung writes the line itself -- the enclosing if, which
    merely contains the ladder, does not."""
    return any(isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "miss"
                       for t in n.targets)
               for n in branch.body)


def _rungs():
    """[(test source or None for the else, body source)] of the
    ladder that writes the pult's missing-end line."""
    for node in ast.walk(_method("_update_pult")):
        if not isinstance(node, ast.If) or not _assigns_miss(node):
            continue
        out, cur = [], node
        while True:
            out.append((_seg(cur.test),
                        "\n".join(_seg(s) for s in cur.body)))
            if len(cur.orelse) == 1 and isinstance(cur.orelse[0],
                                                   ast.If):
                cur = cur.orelse[0]
                continue
            if cur.orelse:
                out.append(
                    (None, "\n".join(_seg(s) for s in cur.orelse)))
            return out
    raise AssertionError("the pult's missing-end ladder is gone")


def test_offline_is_said_only_of_a_microphone_that_is_absent():
    said = [t for t, body in _rungs() if "mic offline" in body]
    assert len(said) == 1, "the offline line is said in %d places" % len(said)
    test = said[0] or ""
    assert ("_source_present" in test or "_mic_gone" in test), (
        "the pult calls a microphone offline on the strength of "
        "%r, which does not ask whether it is there" % test)


def test_a_target_nobody_captures_is_named_before_any_declaration():
    """The rule is that one capsule in use captures every target, so
    a rig with nothing in use captures nothing -- and that is the
    state a card whose stored record died lands in."""
    bad = [t for t, _b in _rungs() if t and "mic_cols" in t]
    assert not bad, (
        "a rung is gated on a declared capture channel, which is "
        "exactly what the person is being asked to do: %r" % bad)
