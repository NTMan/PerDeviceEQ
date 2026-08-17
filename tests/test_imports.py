"""Static courts that need no import, for the code no test can run.

Every intra-package import resolves: `from .x import Name` finds both
x.py and Name at its top level. Every call into the package passes
arguments its target accepts. And every attribute a class reads exists
by the time it is read.

Minted after field startup breaks in one series (hig.py's ratchet
rule: a dispute settled by hand becomes a mechanical rule) -- first a
patch importing a module its commit did not carry, then a name its
module did not define, then a call still passing a keyword argument
the moved-out code had taken away with it, then a flag read by one job
and created only by another. py_compile compiles without importing,
pyflakes stops at module borders, the GUI modules cannot be imported
here at all (no gi), and none of those breaks is an import; AST is the
check that CAN run, no imports executed.

The call court resolves only what it can be sure of: a module alias
(`ms.SessionConfig`), a directly imported name, or a top-level name in
the calling module itself. A target whose signature it cannot read --
a class with a base, anything reached through an object -- is skipped
rather than guessed at, and so is a call that spreads **kwargs. That
is the price of no false alarms: these courts prove code wrong, never
right.
"""
import ast
import collections
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "perdeviceeq"


def _scan(body, names):
    for n in body:
        if isinstance(n, (ast.ClassDef, ast.FunctionDef,
                          ast.AsyncFunctionDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
                elif isinstance(t, (ast.Tuple, ast.List)):
                    for e in t.elts:
                        if isinstance(e, ast.Name):
                            names.add(e.id)
        elif isinstance(n, ast.AnnAssign):
            if isinstance(n.target, ast.Name):
                names.add(n.target.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.Try):
            for part in (n.body, n.orelse, n.finalbody):
                _scan(part, names)
            for h in n.handlers:
                _scan(h.body, names)
        elif isinstance(n, ast.If):
            _scan(n.body, names)
            _scan(n.orelse, names)


def _top_names(tree):
    names = set()
    _scan(tree.body, names)
    return names


def _pkg_trees():
    return {p.stem: ast.parse(p.read_text(encoding="utf-8"))
            for p in sorted(PKG.glob("*.py"))}


def test_intra_package_imports_resolve():
    trees = _pkg_trees()
    tops = {m: _top_names(t) for m, t in trees.items()}
    bad = []
    for mod, tree in trees.items():
        for n in ast.walk(tree):
            if not isinstance(n, ast.ImportFrom) or n.level != 1:
                continue
            if n.module is None:         # from . import x, y
                for a in n.names:
                    if (a.name not in trees
                            and a.name not in tops["__init__"]):
                        bad.append("%s: from . import %s"
                                   % (mod, a.name))
                continue
            if n.module not in trees:
                bad.append("%s: from .%s import ..."
                           % (mod, n.module))
                continue
            for a in n.names:
                if a.name != "*" and a.name not in tops[n.module]:
                    bad.append("%s: from .%s import %s"
                               % (mod, n.module, a.name))
    assert not bad, bad


# --- what a call may pass ---------------------------------------------

def _func_sig(fn, drop_first=False):
    a = fn.args
    pos = [x.arg for x in a.posonlyargs] + [x.arg for x in a.args]
    if drop_first and pos:
        pos = pos[1:]                    # self
    return {"pos": pos, "kw": [x.arg for x in a.kwonlyargs],
            "star": a.vararg is not None, "kwargs": a.kwarg is not None}


def _decorators(cls):
    out = set()
    for d in cls.decorator_list:
        f = d.func if isinstance(d, ast.Call) else d
        out.add(f.attr if isinstance(f, ast.Attribute)
                else getattr(f, "id", ""))
    return out


def _class_sig(cls):
    """Its __init__ if written, else its dataclass fields. None means
    unreadable, and an unreadable target is not judged."""
    for n in cls.body:
        if isinstance(n, ast.FunctionDef) and n.name == "__init__":
            return _func_sig(n, drop_first=True)
    if "dataclass" in _decorators(cls):
        return {"pos": [n.target.id for n in cls.body
                        if isinstance(n, ast.AnnAssign)
                        and isinstance(n.target, ast.Name)],
                "kw": [], "star": False, "kwargs": False}
    return None


def _module_sigs(tree):
    out = {}
    for n in tree.body:
        if isinstance(n, ast.FunctionDef):
            out[n.name] = _func_sig(n)
        elif isinstance(n, ast.ClassDef):
            bases = [b for b in n.bases
                     if not (isinstance(b, ast.Name)
                             and b.id == "object")]
            out[n.name] = None if bases else _class_sig(n)
    return out


def _local_map(tree, trees):
    """Which local names in this file mean a package module, and which
    mean one symbol out of one."""
    alias, direct = {}, {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            base = mod.split(".")[-1]
            if n.level == 1 and n.module is None:
                for a in n.names:        # from . import measure_core
                    if a.name in trees:
                        alias[a.asname or a.name] = a.name
            elif n.level == 1 and base in trees:
                for a in n.names:
                    direct[a.asname or a.name] = (base, a.name)
            elif mod == "perdeviceeq":
                for a in n.names:
                    if a.name in trees:
                        alias[a.asname or a.name] = a.name
            elif mod.startswith("perdeviceeq.") and base in trees:
                for a in n.names:
                    direct[a.asname or a.name] = (base, a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                base = a.name.split(".")[-1]
                if a.name.startswith("perdeviceeq.") and base in trees:
                    alias[a.asname or base] = base
    return alias, direct


def _target(call, sigs, alias, direct, me):
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        mod = alias.get(f.value.id)
        if mod and f.attr in sigs[mod]:
            return mod, f.attr, sigs[mod][f.attr]
    elif isinstance(f, ast.Name):
        if f.id in direct:
            mod, sym = direct[f.id]
            if sym in sigs[mod]:
                return mod, sym, sigs[mod][sym]
        elif me and f.id in sigs[me]:
            return me, f.id, sigs[me][f.id]
    return None


def test_every_call_into_the_package_fits_its_target():
    trees = _pkg_trees()
    sigs = {m: _module_sigs(t) for m, t in trees.items()}
    files = (sorted(PKG.glob("*.py")) + sorted((ROOT / "tools").glob("*.py"))
             + sorted(ROOT.glob("*.py")))
    bad, seen = [], 0
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        alias, direct = _local_map(tree, trees)
        me = p.stem if p.stem in trees else None
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            t = _target(n, sigs, alias, direct, me)
            if t is None or t[2] is None:
                continue
            mod, sym, sg = t
            if any(k.arg is None for k in n.keywords):
                continue                 # a spread hides its names
            seen += 1
            takes = set(sg["pos"]) | set(sg["kw"])
            for k in n.keywords:
                if k.arg not in takes and not sg["kwargs"]:
                    bad.append("%s:%d: %s.%s() takes no %s"
                               % (p.name, n.lineno, mod, sym, k.arg))
            if not sg["star"] and len(n.args) > len(sg["pos"]):
                bad.append("%s:%d: %s.%s() takes %d positional, given %d"
                           % (p.name, n.lineno, mod, sym,
                              len(sg["pos"]), len(n.args)))
    assert not bad, bad
    # a court that resolves nothing passes for the wrong reason
    assert seen > 300, seen


# --- born before it is asked -------------------------------------------

# A method named as a value to one of these runs as a continuation of
# the flow that named it, so it can be trusted to run after its caller.
# A handler passed to connect() is NOT in this list on purpose: it runs
# when a hand acts, in whatever order the hand chooses.
DEFERRED = {"idle_add", "timeout_add", "timeout_add_seconds", "Thread"}


def _class_graph(cls):
    """(methods, edges, assignments, reads) of one class, where an edge
    is `self.m()` or `self.m` handed to something that will call it."""
    meths = {m.name: m for m in cls.body
             if isinstance(m, ast.FunctionDef)}
    edges = collections.defaultdict(set)
    sets = collections.defaultdict(set)
    reads = collections.defaultdict(set)
    for name, m in meths.items():
        for n in ast.walk(m):
            if (isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name)
                    and n.value.id == "self"):
                if isinstance(n.ctx, ast.Store):
                    sets[n.attr].add(name)
                elif isinstance(n.ctx, ast.Load):
                    reads[n.attr].add(name)
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if (isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and f.value.id == "self" and f.attr in meths):
                edges[name].add(f.attr)
            fn = (f.attr if isinstance(f, ast.Attribute)
                  else getattr(f, "id", ""))
            if fn in DEFERRED:
                for a in list(n.args) + [k.value for k in n.keywords]:
                    if (isinstance(a, ast.Attribute)
                            and isinstance(a.value, ast.Name)
                            and a.value.id == "self"
                            and a.attr in meths):
                        edges[name].add(a.attr)
    return meths, edges, sets, reads


def _reachable(edges, start):
    seen, stack = set(), [start]
    while stack:
        for nxt in edges[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def test_every_attribute_is_born_before_it_is_read():
    """An attribute is born if some method the constructor reaches
    assigns it. One that is not may still be read safely -- by a method
    its own setter reaches, the way a worker reads what the click that
    started it wrote. Read anywhere else, it depends on some unrelated
    job having run first, and that is the shape of the crash that made
    this court: a level search asking a flag the gain ladder creates.
    """
    bad = []
    for p in sorted(PKG.glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for cls in [n for n in ast.walk(tree)
                    if isinstance(n, ast.ClassDef)]:
            meths, edges, sets, reads = _class_graph(cls)
            if "__init__" not in meths:
                continue          # no constructor: no birthplace to know
            born_in = _reachable(edges, "__init__") | {"__init__"}
            born = {a for a, ms in sets.items() if ms & born_in}
            for attr, readers in sorted(reads.items()):
                if attr in born or attr not in sets:
                    continue      # born, or not this class's to assign
                safe = set()
                for setter in sets[attr]:
                    safe |= _reachable(edges, setter) | {setter}
                for r in sorted(readers - safe):
                    bad.append("%s: %s.%s read in %s(), assigned only in %s"
                               % (p.name, cls.name, attr, r,
                                  ", ".join(sorted(sets[attr]))))
    assert not bad, bad
