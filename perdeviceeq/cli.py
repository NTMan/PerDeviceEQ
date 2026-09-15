# -*- coding: utf-8 -*-
"""CLI command implementations (no GTK). These back the --list / --list-profiles
/ --inspect / --apply flags; argument parsing and dispatch live in the launcher.
"""

import json, sys

from .config import CLEAN_ID
from .profiles import ProfileStore
from .pw_backend import (list_sinks, list_sources, node_params,
                         backend)


def cmd_list():
    for s in list_sinks():
        mark = "*" if s["default"] else " "
        print("%s[%4d] %s\t%s" % (mark, s["id"], s["name"], s["desc"]))
    return 0

def cmd_list_sources():
    for s in list_sources():
        print(" [%4d] %s\t%s" % (s["id"], s["name"], s["desc"]))
    return 0


def cmd_list_profiles():
    store = ProfileStore()
    rev = {}
    for node, pid in store.bindings.items():
        rev.setdefault(pid, []).append(node)
    for p in store.ordered():
        kind = "clean" if p["id"] == CLEAN_ID else ("builtin" if p["builtin"] else "user")
        bound = rev.get(p["id"], [])
        extra = ("  <- " + ", ".join(bound)) if bound else ""
        print("[%-7s] %-28s %s%s" % (kind, p["name"], p["id"], extra))
    return 0

def cmd_inspect(name):
    params, nid = node_params(name)
    if nid is None:
        print("sink not found: %s" % name, file=sys.stderr)
        return 1
    print("Sink id=%s name=%s\n" % (nid, name))
    print(json.dumps(params, indent=2, ensure_ascii=False))
    return 0

def cmd_apply():
    """Push every bound device's graph into the 'per-device-eq' metadata; the WP
    hook applies it. Requires the hook to be installed (run --install-hook once)."""
    up, ver = backend().wait_for_hook()
    if not up:
        print("the hook is not listening -- nothing was sent. Wait a "
              "moment and try again, or run --install once.",
              file=sys.stderr)
        return 1
    sent, total = backend().publish_state(ProfileStore().wire_state())
    print("sent %d of %d device(s) with a graph (hook protocol %s)"
          % (sent, total, ver))
    return 0 if sent == total else 1
