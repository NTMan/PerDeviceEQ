#!/usr/bin/env python3
"""Convert schema 7 profiles to schema 8, once.

Schema 8 keeps a channel's balance -- its level against the other
channels -- as a number of the channel ("balance", dB), where schema 7
kept it as a band: a high shelf below 1 Hz, which plays as a flat gain.
The conversion moves each such shelf out of the bands into the balance
(two add up), rounded to the hundredth, as the fit writes it; a disabled
one played nothing and is dropped. Nothing else changes, and the device
plays what it played. A fit the hand never edited stays unedited: its
output_sha256 is stamped again over the new body. provenance.migrated_from
holds the address of the schema 7 body (perdeviceeq/pdeq.py).

    convert_v8.py              every profile in the store
                               (~/.config/per-device-eq/profiles)
    convert_v8.py FILE...      these files: store profiles (.json) or
                               packages (.pdeq)
    convert_v8.py -n ...       say what would change, write nothing

Quit the app first: a window that is open keeps its own copy and saves
it back. A store profile is rewritten in place, and the schema 7 file
stays beside it as <name>.json.v7, which the store does not read. A
package is a new object with a new address: it is written beside the old
one as <name>.v8.pdeq, and the old file stays as it is.

The tool stays until the first schema change that no converter can
carry over. Then it goes, and an old profile is re-measured.
"""
import argparse
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from perdeviceeq import pdeq                          # noqa: E402
from perdeviceeq.config import USER_PROFILES_DIR      # noqa: E402
from perdeviceeq.profiles import (ProfileStore,       # noqa: E402
                                  playback_sha256)

FROM, TO = 7, 8


def _is_flat(b):
    """A schema 7 balance: a high shelf below 1 Hz, which PipeWire plays
    as a flat gain."""
    return b.get("type") == "HSC" and float(b.get("freq", 0.0)) < 1.0


def convert(body):
    """(the schema 8 body, what moved) of a schema 7 body. The body comes
    out as the store writes it (ProfileStore._body). What moved is one
    phrase per channel that had a flat shelf."""
    new = json.loads(json.dumps(body))
    moved = []
    for key, chan in (new.get("channels") or {}).items():
        bands = list((chan or {}).get("bands") or [])
        flat = [b for b in bands if _is_flat(b)]
        if not flat:
            continue
        on = [b for b in flat if b.get("enabled", True)]
        bal = round(sum(float(b.get("gain", 0.0)) for b in on), 2)
        chan["bands"] = [b for b in bands if not _is_flat(b)]
        if bal:
            chan["balance"] = bal
        word = "%s balance %+.2f dB" % (key, bal)
        if len(on) > 1:
            word += " (%d shelves)" % len(on)
        if len(on) < len(flat):
            word += ", %d disabled shelf dropped" % (len(flat) - len(on))
        moved.append(word)
    new["version"] = TO
    new = ProfileStore._body(new)
    fit = new.get("fit")
    if fit and fit.get("output_sha256") == playback_sha256(body):
        new["fit"] = dict(fit, output_sha256=playback_sha256(new))
    new["provenance"] = dict(new.get("provenance") or {},
                             migrated_from=pdeq.payload_sha256(body))
    return new, moved


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _fit_word(body):
    fit = body.get("fit") or {}
    if not fit.get("output_sha256"):
        return ""
    if fit["output_sha256"] == playback_sha256(body):
        return "; the fit stays unedited"
    return "; the fit was edited by hand and stays so"


def convert_file(path, dry=False):
    """One file converted, or not, and a line that says which."""
    pkg = path.endswith(".pdeq")
    try:
        body = _read(path)
    except (OSError, ValueError) as e:
        return "%s: not read (%s)" % (path, e)
    if not isinstance(body, dict):
        return "%s: not a profile" % path
    v = body.get("version", 1)
    if v == TO:
        return "%s: already schema %d" % (path, TO)
    if v != FROM:
        return ("%s: schema %s, which no converter carries over; "
                "re-measure the device" % (path, v))
    if pkg:
        body = {k: x for k, x in body.items() if k not in pdeq.RUNTIME_KEYS}
    else:
        body.setdefault("id", os.path.splitext(os.path.basename(path))[0])
    new, moved = convert(body)
    what = ", ".join(moved) or "no balance to move"
    if pkg:
        out = path[:-len(".pdeq")] + ".v8.pdeq"
        line = "%s -> %s: %s%s" % (path, out, what, _fit_word(body))
        if not dry:
            _write(out, pdeq.pdeq_pack(new))
        return line
    line = "%s: %s%s; schema 7 kept as %s.v7" % (
        path, what, _fit_word(body), os.path.basename(path))
    if not dry:
        shutil.copy2(path, path + ".v7")
        _write(path, json.dumps(new, indent=2, ensure_ascii=False))
    return line


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert schema 7 profiles and packages to schema 8, "
                    "once: the balance leaves the bands.")
    ap.add_argument("files", nargs="*",
                    help="profiles (.json) or packages (.pdeq); none means "
                         "every profile in %s" % USER_PROFILES_DIR)
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="say what would change, write nothing")
    args = ap.parse_args(argv)
    files = args.files or sorted(
        glob.glob(os.path.join(USER_PROFILES_DIR, "*.json")))
    if not files:
        print("no profiles in %s" % USER_PROFILES_DIR)
        return 0
    if args.dry_run:
        print("dry run: nothing is written")
    for path in files:
        print(convert_file(path, dry=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
