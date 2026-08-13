# -*- coding: utf-8 -*-
"""Persistent measurement preferences for the wizard (no GTK; JSON only).

Two small stores let the measurement mic and its calibration be chosen
once and recalled per output:

  MicProfileStore  reusable measurement-mic profiles -- which PipeWire
                   source node to match, the rig serial, the per-capture-
                   channel calibration files, and the compensation domain
                   (RAW / HEQ / IDF / HPN, which shapes the fit target).
  MeasureMemory    per-sink recall -- the last mic profile used for a sink
                   and the last auto-level volume that measured it well,
                   so re-measuring the same output needs no re-setup.

Both save atomically (tmp + os.replace) and tolerate a missing or
corrupt file by starting empty, mirroring profiles.py. Filesystem + JSON
only, so this imports cleanly anywhere (CLI, tests, GUI) without GTK.
"""
import json
import os
import re
import uuid

from .config import MIC_PROFILES_FILE, MEASURE_STATE_FILE


def _new_id():
    return uuid.uuid4().hex[:12]



def _atomic_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)



def worth_saving(cal, existing, by_hand=False, knees=None):
    """Is there anything about this rig worth writing down?

    A remembered rig, or a calibration, obviously. And a HAND: an
    act on this rig is a statement about it even when nothing else
    has been said yet -- today that act is taking a calibration off,
    and refusing to record it let the next opening read the file
    back and hand it straight to the rig again.

    And a MEASURED SENSITIVITY, for the same reason a calibration
    counts: it is a statement about this rig, it cost half a minute
    of silence to obtain, and it is worth exactly as much on the
    next earphone as on this one. Without this a rig with no
    calibration -- which is every rig until one is chosen -- would
    have its verdict thrown away when the window closed.

    Everything else is a handler firing during load, which must not
    mint a profile for every rig that is merely selected."""
    return (bool(cal) or existing is not None or bool(by_hand)
            or bool(knees))


KNEE_FIELDS = ("gain", "kind", "knee_axis_db", "flat_dbfs",
               "scatter_db", "at")


def sane_knee(body):
    """A stored sensitivity verdict, or None if it says nothing.

    `gain` is the position to stand at, as a cubic volume, and it is
    the only field the app acts on. The rest is what makes a re-run a
    CHECK rather than a repeat: knee_axis_db so the caption can say
    which side of it the control is on, flat_dbfs and scatter_db as
    the fingerprint a second ladder either confirms or contradicts,
    and the date.

    knee_axis_db is named for the CONTROL's axis on purpose. On a
    hardware route that axis is not the card's decibels -- the
    session manager reports what it was asked for while the card maps
    it through a taper of its own -- and a field called plain "db"
    would promise what nobody can deliver.
    """
    if not isinstance(body, dict):
        return None
    out = {}
    for k in KNEE_FIELDS:
        v = body.get(k)
        if v is None:
            continue
        out[k] = str(v) if k in ("kind", "at") else float(v)
    if out.get("gain") is None or not out.get("kind"):
        return None
    return out


def sane_columns(body):
    """{column index as a string: {"cal": path, "knee": {...}}}.

    Called "columns" and not "channels" because that key is TAKEN: it
    once held a capsule COUNT, which was retired because a count
    cannot say which wire carries what. An old file may still carry
    an integer there, so a non-dict is ignored here rather than
    trusted -- reusing a retired key with a new type is a trap for
    whoever opens such a file next.

    One record per column, because a column is a wire and everything
    here belongs to the microphone plugged into it: same owner, same
    lifetime. A new per-column setting is then one line here rather
    than a second dictionary to keep in step with this one.
    """
    out = {}
    if not isinstance(body, dict):
        return out
    for k, v in body.items():
        if not isinstance(v, dict):
            continue
        rec = {}
        if v.get("cal"):
            rec["cal"] = str(v["cal"])
        knee = sane_knee(v.get("knee"))
        if knee:
            rec["knee"] = knee
        if rec:
            out[str(k)] = rec
    return out



def cal_to_store(chosen, remembered, by_hand=False):
    """What a rig's remembered calibration becomes after a change.

    `chosen` is what the window holds right now, keyed by capture
    channel; `remembered` is what the store already has.

    An EMPTY chosen set is ambiguous, and that ambiguity is the
    whole bug: it is what the window holds for a heartbeat while a
    profile is still loading, and it is also exactly what the
    operator means after pressing Remove. Guessing one way wipes a
    remembered rig on a stray handler; guessing the other way makes
    a calibration impossible to take OFF -- which is what the field
    found: the button was missing, and even with a button the store
    would have put the old file straight back.

    So the hand decides. by_hand is an operator's act and empties
    the block; without it an empty set defers to what was
    remembered."""
    cal = {str(k): v for k, v in (chosen or {}).items() if v}
    if cal or by_hand:
        return cal
    return dict(remembered or {})


def serial_from_cal(paths):
    """The rig serial, read from the cal filenames. miniDSP ships
    per-unit cal as {L,R}_{RAW,HEQ,IDF,HPN}_<serial>.txt and UMIKs
    as <model>_<serial>.txt, so the unit's identity is usually
    sitting right in the file name. Every provided file must agree
    on exactly one candidate (a digit run of 5+), otherwise nothing
    is guessed -- a wrong serial is worse than an empty one."""
    sets = []
    for p in paths or ():
        if not p:
            continue
        runs = set(re.findall(r"[0-9]{5,}", os.path.basename(p)))
        if runs:
            sets.append(runs)
    if not sets:
        return ""
    common = set.intersection(*sets)
    return common.pop() if len(common) == 1 else ""


class MicProfileStore:
    """Reusable measurement-mic profiles, keyed by a stable id. A profile
    is a plain dict: {id, name, node_match, serial, cal}, where cal
    maps a capture-channel index (as a string) to a cal-file path.

    A `channels` field used to sit beside it -- the rig's capsule count,
    pinned by hand because a card can enumerate a width it does not
    capture. It is gone: a COUNT cannot say which wire carries what,
    the per-target column picker says exactly that, and a stale stored
    2 was what kept a sixteen-column interface offering L and R.
    """

    def __init__(self):
        self.profiles = {}
        self.reload()

    def reload(self):
        self.profiles = {}
        try:
            with open(MIC_PROFILES_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            for pid, body in data.items():
                if isinstance(body, dict):
                    self.profiles[pid] = self._sane(pid, body)

    @staticmethod
    def _sane(pid, body):
        # ONE shape. A file from before this change is simply not read
        # -- his call, and the right one: the store holds a
        # calibration binding and a working point, both a few seconds
        # to re-enter, and carrying a second shape forever to save
        # those seconds is a worse trade than deleting the file once
        cols = sane_columns(body.get("columns"))
        cal = {k: v["cal"] for k, v in cols.items() if v.get("cal")}
        return {"id": pid, "name": body.get("name") or pid,
                "node_match": body.get("node_match") or "",
                "serial": body.get("serial") or "", "cal": cal,
                "columns": cols}

    @staticmethod
    def _body(p):
        # a WHITELIST: a field missing from here is dropped silently
        # on the next save, which is how a new one gets lost. `cal` is
        # derived from `columns` on read, so it is not written
        return {k: p[k] for k in ("name", "node_match", "serial",
                                  "columns")}

    def get(self, pid):
        return self.profiles.get(pid)

    def ordered(self):
        return sorted(self.profiles.values(),
                      key=lambda p: p["name"].lower())

    def save(self, body):
        """Create or overwrite a profile; returns its id (minted if absent
        or if the given id collides with none)."""
        pid = body.get("id") or _new_id()
        self.profiles[pid] = self._sane(pid, body)
        self._flush()
        return pid

    def delete(self, pid):
        if pid in self.profiles:
            del self.profiles[pid]
            self._flush()
            return True
        return False

    def _flush(self):
        _atomic_write(MIC_PROFILES_FILE,
                      {pid: self._body(p)
                       for pid, p in self.profiles.items()})

    def match(self, key):
        """The profile for this capture identity, or None.

        An identity is a node, or a node and one of its card ports
        ("node#2"). Exact wins. Failing that, a profile that names
        the bare NODE answers for any of that card's jacks: it was
        written before jacks were part of a rig's name, and it means
        "this card" -- refusing it would silently drop a rig's
        calibration the first time the list learned about ports. The
        next save writes the identity in full, so each jack ends up
        with its own rig, which is what
        they physically are.

        A profile that names a JACK never answers for a different
        one, and never for the bare node either: that would be
        guessing."""
        if not key:
            return None
        for p in self.profiles.values():
            if p["node_match"] and p["node_match"] == key:
                return p
        node = str(key).rpartition("#")[0] or None
        if not node:
            return None
        for p in self.profiles.values():
            if p["node_match"] and p["node_match"] == node:
                return p
        return None

    def cal_for(self, pid, channel):
        """The cal-file path a profile assigns to a capture-channel index,
        or None. This is what the window hands finalize(channel, cal=...)."""
        p = self.profiles.get(pid)
        return p["cal"].get(str(channel)) if p else None

    def knee_for(self, pid, channel):
        """The sensitivity verdict measured on that column, or None.

        It belongs to the SOURCE, not to any earphone: choosing a
        different sink does not touch it, which is the whole point --
        a coupler's working point is found once and reused for every
        earphone measured through it.
        """
        p = self.profiles.get(pid)
        if not p:
            return None
        return (p["columns"].get(str(channel)) or {}).get("knee")

    def knees_of(self, pid):
        """{column: verdict} for everything measured on this rig."""
        p = self.profiles.get(pid)
        if not p:
            return {}
        return {k: v["knee"] for k, v in p["columns"].items()
                if v.get("knee")}


class MeasureMemory:
    """Per-sink recall: {sink_node: {"mic_profile": id, "volume": float}}.
    On reopening the window for a sink, its last mic and a starting
    auto-level volume are restored so almost nothing is re-entered."""

    def __init__(self):
        self.state = {}
        self.reload()

    def reload(self):
        try:
            with open(MEASURE_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.state = data if isinstance(data, dict) else {}

    def for_sink(self, sink):
        e = self.state.get(sink)
        return e if isinstance(e, dict) else {}

    def mic_for(self, sink):
        """The sink's remembered mic -- with a sibling fallback.

        A sink's node name encodes the card's ALSA profile
        (...HiFi__Speaker__sink vs ...HiFi_7_1__Speaker__sink vs
        ...analog-stereo are ONE device), so switching the card
        profile forks the identity and would orphan the rig
        memory (field data loss: the cals "disappeared" after a
        profile switch). Exact key wins; otherwise the first
        sibling on the same card stem answers. remember() still
        writes the exact key, so a fork heals itself on first
        use under the new name."""
        e = self.for_sink(sink)
        if "mic_profile" in e:
            return e.get("mic_profile")
        stem = (sink or "").rsplit(".", 1)[0]
        if not stem:
            return None
        for key, entry in self.state.items():
            if (key != sink
                    and key.rsplit(".", 1)[0] == stem
                    and isinstance(entry, dict)
                    and entry.get("mic_profile")):
                return entry["mic_profile"]
        return None

    def volume_for(self, sink, source):
        """The last good auto-level volume for this sink+source pair, or
        None. Volume is keyed by the pair because a more/less sensitive
        mic reads hotter/quieter at the same sink level, so the level that
        worked depends on which mic measured it."""
        vols = self.for_sink(sink).get("volumes")
        v = vols.get(source) if isinstance(vols, dict) else None
        return float(v) if isinstance(v, (int, float)) else None

    def remember(self, sink, mic_profile=None, source=None, volume=None):
        """Update a sink's recall; only the provided fields change. The
        mic profile is per-sink (which mic to preselect); the volume is
        stored under sink+source (needs source to key it)."""
        if not sink:
            return
        e = dict(self.for_sink(sink))
        if mic_profile is not None:
            e["mic_profile"] = mic_profile
        if volume is not None and source:
            vols = dict(e.get("volumes") or {})
            vols[source] = round(float(volume), 4)
            e["volumes"] = vols
        self.state[sink] = e
        _atomic_write(MEASURE_STATE_FILE, self.state)

    def forget_volume(self, sink, source):
        """Drop the remembered volume for a sink+source pair (the wizard's
        re-level: the next sweep finds the level afresh)."""
        e = self.for_sink(sink)
        vols = e.get("volumes")
        if isinstance(vols, dict) and source in vols:
            del vols[source]
            self.state[sink] = e
            _atomic_write(MEASURE_STATE_FILE, self.state)

    def forget(self, sink):
        if sink in self.state:
            del self.state[sink]
            _atomic_write(MEASURE_STATE_FILE, self.state)
