# -*- coding: utf-8 -*-
"""Profile store: load reusable EQ profiles (system read-only + user
read-write), the built-in Clean profile, and the node.name -> profile bindings.

Schema v3 = the v2 playback body (one shared preamp, per-channel bands)
plus four OPTIONAL dict blocks the store carries verbatim: `provenance`
(where the profile came from), `device` (what it corrects), `fit` (how
the bands were derived and from which takes) and `measurement` (the
canvas: the single rig with its per-capture-channel cal points, the
sessions, and the per-take uncalibrated magnitudes on the profile's
log grid). A profile without `measurement` is an import or a hand-made
one. The blocks' shape is owned by their producers; the store only
guarantees a save/load round-trip never strips them.

No GTK. Filesystem + JSON only.
"""

import hashlib
import os, sys, json, uuid

from .config import (SYS_PROFILE_DIRS, USER_PROFILES_DIR, BINDINGS_FILE,
                     CONFIG_DIR, CLEAN_ID, SCHEMA_VERSION, V3_BLOCKS,
                     load_ui_state)
from .eq import (profile_graph, profile_has_content,
                 resolve_slots, auto_preamp_db)


def _new_id():
    return uuid.uuid4().hex[:12]


def _clean_profile():
    return {"id": CLEAN_ID, "name": "Clean (no EQ)", "apply_all": True,
            "version": SCHEMA_VERSION, "preamp": 0.0, "ch_keys": [],
            "all": {"bands": []}, "channels": {}, "builtin": True,
            "path": None}


PLAYBACK_KEYS = ("apply_all", "preamp", "ch_keys", "all", "channels")
SHAPE_KEYS = ("apply_all", "ch_keys", "all", "channels")


def _channel_is_earned(key, block, out, stored):
    """True when a stored channel is worth carrying past a save: it holds
    bands, or a take in either body measured it. Provenance, not presence."""
    if any((block or {}).get("bands") or []):
        return True
    for body in (out, stored):
        for t in (((body or {}).get("measurement") or {}).get("takes") or []):
            if (t or {}).get("channel") == key:
                return True
    return False


def playback_sha256(p):
    """sha256 over the canonical playback body, with the preamp
    PINNED to 0.0 in the hash: preamp is gain staging -- the
    Safe/Session headroom the app derives and the user rides -- not
    the shape the fit produced, so riding it must never read as
    editing the fit. Every fit ever stamped had preamp exactly 0.0,
    which keeps all stored output_sha256 values valid. The fit
    stamps its output with this, so `edited` is derived instead of
    sticky: change a band and it appears, undo back to the fitted
    shape and it clears."""
    body = {k: p.get(k) for k in PLAYBACK_KEYS}
    body["preamp"] = 0.0
    ck = body.get("ch_keys")
    if isinstance(ck, list):
        # channel-list ORDER is presentation, not playback:
        # every channel gets its own chain and the seating
        # order changes no audible bit. A measurement can mint
        # the fit with FR first while the editor always seats
        # FL first, so hashing the order made `edited` stick
        # forever after the first editor save -- the
        # architect's field bisector (his own post-undo
        # profile) convicted exactly this key.
        body["ch_keys"] = sorted(ck)
    # protection is staging, like preamp, not fit editing:
    # pinned out of the hash so riding the floor or the
    # ceiling never reads as editing the fit, and every
    # stored output_sha256 stays valid
    for k in ("floor_off", "floor_hz"):
        body.pop(k, None)
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def editor_body(body, stored):
    """The editor rebuilds only the PLAYBACK body (it edits sound,
    not history). This reattaches the stored profile's v3 blocks so
    a debounced save can never strip a canvas, and keeps the fit's
    `edited` mark truthful. A fit carrying output_sha256 (the hash
    of the playback body it produced) gets the mark DERIVED: diverge
    and it appears, undo back to the exact fitted sound and it
    clears. A fit from before the output hash falls back to the
    sticky rule: any divergence sets the mark, only a re-fit clears
    it."""
    out = dict(body)
    # an editor-assembled body is a v3 body and says so: the
    # native package export packs it directly, and its schema
    # gate must not refuse the app's own working profile
    out.setdefault("version", SCHEMA_VERSION)
    for key in V3_BLOCKS:
        block = (stored or {}).get(key)
        if isinstance(block, dict) and block and key not in out:
            out[key] = block
    # ...and never strip a CHANNEL THAT WAS EARNED. The editor's view is keyed
    # by the SINK's live map, so a profile visited through a
    # mono flap (LDAC falling to HFP, a DAC in a mono state)
    # assembles a body with only the sink's keys -- one
    # autosave then buried the stored FL/FR band lists. The
    # profile owns its layout; the sink owns only the route:
    # stored channels the view does not carry ride through
    # every save, and ch_keys keeps them reachable.
    #
    # Earned, though, and not merely present. A key also enters
    # a profile by ACCIDENT: the editor seeds a slot for every
    # channel the sink offers, so opening a stereo pair's
    # profile on a ten-channel card wrote ten empty slots, and
    # this rule then kept them for good. Two visits to a card
    # that renames its channels left twenty keys under a
    # correction that has two, and the graph built from them
    # was refused whole. A channel is kept when it carries
    # bands or when a take measured it; an empty slot nobody
    # touched is not a layout, it is a leftover.
    sch = (stored or {}).get("channels") or {}
    mine = out.get("channels") or {}
    keep = {k: v for k, v in sch.items()
            if k not in mine and _channel_is_earned(k, v, out, stored)}
    if keep:
        out["channels"] = {**keep, **mine}
        keys = list(out.get("ch_keys") or [])
        keys += [k for k in ((stored or {}).get("ch_keys")
                             or sch.keys())
                 if k not in keys and k in out["channels"]]
        out["ch_keys"] = keys
    fit = out.get("fit")
    if isinstance(fit, dict):
        ref = fit.get("output_sha256")
        if ref:                     # derived: undo clears the mark
            ed = playback_sha256(out) != ref
            if bool(fit.get("edited")) != ed:
                out["fit"] = dict(fit, edited=ed)
        elif (isinstance(stored, dict) and not fit.get("edited")
                and any(out.get(k) != stored.get(k)
                        for k in SHAPE_KEYS)):
            out["fit"] = dict(fit, edited=True)   # pre-hash fits
    return out


class ProfileStore:
    """Loads profiles from system (read-only) + user dirs and the bindings map.
    A built-in Clean profile is always present. 'No binding == Clean'."""
    def __init__(self):
        self.profiles = {}
        self.bindings = {}
        self.reload()

    def reload(self):
        self.profiles = {}
        for d in SYS_PROFILE_DIRS:          # system first (read-only)
            self._load_dir(d, builtin=True)
        self._load_dir(USER_PROFILES_DIR, builtin=False)   # user can override
        if CLEAN_ID not in self.profiles:
            self.profiles[CLEAN_ID] = _clean_profile()
        else:
            self.profiles[CLEAN_ID]["builtin"] = True
        self.bindings = self._load_bindings()

    def _load_dir(self, d, builtin):
        if not os.path.isdir(d):
            return
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(d, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    p = json.load(f)
            except Exception:
                continue
            if not isinstance(p, dict):
                continue
            if p.get("version") != SCHEMA_VERSION:
                print("per-device-eq: skipping %s (profile schema "
                      "v%s, pre-THD -- deprecated; a harmonic "
                      "confession cannot be converted in, re-measure "
                      "with the current core)"
                      % (path, p.get("version", 1)), file=sys.stderr)
                continue
            pid = p.get("id") or os.path.splitext(fn)[0]
            p["id"] = pid
            p.setdefault("name", pid)
            p.setdefault("apply_all", True)
            p.setdefault("all", {"preamp": 0.0, "bands": []})
            p.setdefault("channels", {})
            p.setdefault("ch_keys", [])
            p["builtin"] = builtin
            p["path"] = path
            self.profiles[pid] = p          # user dir overrides system on id clash

    def get(self, pid):
        return self.profiles.get(pid) or self.profiles[CLEAN_ID]

    def has(self, pid):
        """Existence, honestly: get() falls back to CLEAN by design
        and must not be used as a liveness test."""
        return pid in self.profiles

    def ordered(self):
        def key(p):
            grp = 0 if p["id"] == CLEAN_ID else (1 if p["builtin"] else 2)
            return (grp, p["name"].lower())
        return sorted(self.profiles.values(), key=key)

    @staticmethod
    def _sane_slot(s):
        return {"bands": list((s or {}).get("bands") or [])}

    @classmethod
    def _body(cls, p):
        body = {"id": p["id"], "name": p.get("name", p["id"]),
                "version": SCHEMA_VERSION,
                "apply_all": bool(p.get("apply_all", True)),
                "ch_keys": list(p.get("ch_keys") or []),
                "all": cls._sane_slot(p.get("all")),
                "channels": {k: cls._sane_slot(v)
                             for k, v in (p.get("channels") or {}).items()}}
        # speaker protection is a first-class citizen of the
        # schema: the QA field run caught save_user stripping
        # these -- handles snapped back on release and the
        # override never reached the wire
        if p.get("floor_off"):
            body["floor_off"] = True
        for key in ("floor_hz",):
            v = p.get(key)
            if v is not None:
                body[key] = float(v)
        prefs = p.get("fit_prefs")
        if isinstance(prefs, dict) and prefs:
            # the dial's parked word (refit.resolve_fit_params
            # spends it): a preference, not history -- carried,
            # never invented
            body["fit_prefs"] = dict(prefs)
        for key in V3_BLOCKS:            # carried verbatim, never made up
            block = p.get(key)
            if isinstance(block, dict) and block:
                body[key] = block
        return body

    def save_user(self, prof):
        """Write/overwrite a user profile (.json named by id). Returns the id."""
        os.makedirs(USER_PROFILES_DIR, exist_ok=True)
        pid = prof.get("id") or _new_id()
        body = self._body({**prof, "id": pid})
        path = os.path.join(USER_PROFILES_DIR, "%s.json" % pid)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(body, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        rec = dict(body); rec["builtin"] = False; rec["path"] = path
        self.profiles[pid] = rec
        return pid

    def delete_user(self, pid):
        p = self.profiles.get(pid)
        if not p or p.get("builtin") or not p.get("path"):
            return False
        try:
            os.remove(p["path"])
        except OSError:
            pass
        self.profiles.pop(pid, None)
        # any bindings pointing here fall back to Clean (drop the entry)
        for node in [n for n, i in self.bindings.items() if i == pid]:
            self.bindings.pop(node, None)
        self.save_bindings()
        return True

    # ---- bindings ----
    def _load_bindings(self):
        """bindings.json holds either a bare profile id per node, as it always
        did, or a record. The record exists because a node needs to remember
        more than which profile it wears: it also remembers WHICH CHANNEL OF
        THE PROFILE FEEDS WHICH OF ITS OWN -- the sink owns the route, the
        profile owns the earphone, and the correspondence between them
        belongs to neither.

        Read into two dicts so that everything written against the old shape
        keeps working: self.bindings stays node -> profile id, self.maps
        carries node -> {sink channel: profile channel or None}, in sink
        order. A node with no map is written back in the bare form, so a
        file only grows a record once someone actually maps something.
        """
        binds, maps, pins, locs = {}, {}, {}, {}
        try:
            with open(BINDINGS_FILE, encoding="utf-8") as f:
                b = json.load(f)
        except Exception:
            b = {}
        if isinstance(b, dict):
            for node, v in b.items():
                if isinstance(v, dict):
                    pid = v.get("profile")
                    if pid:
                        binds[node] = pid
                    m = v.get("map")
                    if isinstance(m, dict) and m:
                        maps[node] = {k: (x or None) for k, x in m.items()}
                    q = v.get("pinned")
                    if isinstance(q, dict) and q:
                        pins[node] = {k: (x or None) for k, x in q.items()}
                    l = v.get("local")
                    if isinstance(l, dict) and l:
                        locs[node] = {k: {"bands": list(
                            (x or {}).get("bands") or [])}
                            for k, x in l.items()
                            if (x or {}).get("bands")}
                elif v:
                    binds[node] = v
        self.maps = maps
        self.pins = pins
        self.locals = locs
        return binds

    def save_bindings(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        out = {}
        extra = [n for n in list(getattr(self, "maps", {}))
                 + list(getattr(self, "locals", {}))
                 if n not in self.bindings]
        for node in list(self.bindings) + extra:
            pid = self.bindings.get(node)
            m = getattr(self, "maps", {}).get(node)
            q = getattr(self, "pins", {}).get(node)
            l = getattr(self, "locals", {}).get(node)
            if m or q or l:
                rec = {"profile": pid, "map": m}
                if q:
                    rec["pinned"] = q
                if l:
                    rec["local"] = l
                out[node] = rec
            else:
                out[node] = pid
        tmp = BINDINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        os.replace(tmp, BINDINGS_FILE)

    # ---- channel map: which profile channel feeds which sink channel ----
    def map_for(self, node):
        return dict(getattr(self, "maps", {}).get(node) or {})

    def set_map(self, node, m):
        """The effective map, as the hook will read it. Written by
        reconcile_map; a hand goes through pin_channel."""
        if not node:
            return
        maps = getattr(self, "maps", None)
        if maps is None:
            maps = self.maps = {}
        if m:
            maps[node] = dict(m)
        else:
            maps.pop(node, None)
        self.save_bindings()

    def pins_for(self, node):
        return dict(getattr(self, "pins", {}).get(node) or {})

    def pin_channel(self, node, sink_ch, prof_ch):
        """A hand chose where this sink channel feeds from -- including the
        choice to feed it from nothing, which is why pins are kept apart
        from the effective map: a None that a person meant and a None that
        the resolver produced look identical in the map, and treating the
        second as deliberate makes it permanent."""
        if not node or not sink_ch:
            return
        pins = getattr(self, "pins", None)
        if pins is None:
            pins = self.pins = {}
        pins.setdefault(node, {})[sink_ch] = prof_ch or None
        self.save_bindings()

    def unpin_channel(self, node, sink_ch):
        pins = getattr(self, "pins", {}).get(node)
        if pins and sink_ch in pins:
            pins.pop(sink_ch)
            if not pins:
                self.pins.pop(node, None)
            self.save_bindings()

    def slots_for(self, node):
        """The map as profile_graph wants it: one entry per sink channel, in
        sink order. None when the node has no map and the caller should fall
        back to the profile's own layout."""
        m = getattr(self, "maps", {}).get(node)
        return list(m.values()) if m else None

    # ---- bands a hand drew on a route this profile does not cover ----
    def local_for(self, node):
        return {k: list(v.get("bands") or []) for k, v
                in (getattr(self, "locals", {}).get(node) or {}).items()}

    def set_local(self, node, sink_ch, bands):
        """Bands tuned by ear on a channel the profile does not feed. They
        belong to the OUTPUT, not to the earphone: they stay when the
        profile changes, they never travel in a package, and a profile that
        later covers this channel simply overrides them without erasing
        them."""
        if not node or not sink_ch:
            return
        locs = getattr(self, "locals", None)
        if locs is None:
            locs = self.locals = {}
        if bands:
            locs.setdefault(node, {})[sink_ch] = {"bands": list(bands)}
        else:
            (locs.get(node) or {}).pop(sink_ch, None)
            if not locs.get(node):
                locs.pop(node, None)
        self.save_bindings()

    def locals_for(self, node, sink_keys):
        """Parallel to slots_for: one entry per sink channel, in sink order,
        each a band list or None."""
        loc = self.local_for(node)
        return [loc.get(k) or None for k in (sink_keys or [])]

    def reconcile_map(self, node, prof_keys, sink_keys):
        """The map for `node` brought up to date with the sink in front of it
        and the profile on it, without throwing away a deliberate choice.

        A sink can change width under one identity -- the same card in
        another mode -- and a profile can be swapped for one with different
        channels. Only a PINNED entry survives that, and only while its sink
        channel and its target both still exist; everything else is answered
        again by resolve_slots. Pins that no longer apply are forgotten here,
        so a stale choice cannot outlive the thing it was about.

        The effective map is written back because the hook reads it: at login
        it applies graphs with no sink to ask, and the map is the only record
        of how wide each node is.
        """
        sink = list(sink_keys or [])
        have = set(prof_keys or [])
        pinned = self.pins_for(node)
        auto = resolve_slots(prof_keys, sink)
        out, keep = {}, {}
        for i, ch in enumerate(sink):
            if ch in pinned and (pinned[ch] is None or pinned[ch] in have):
                out[ch] = pinned[ch]
                keep[ch] = pinned[ch]
            else:
                out[ch] = auto[i]
        if keep != pinned:
            self.pins[node] = keep
            if not keep:
                self.pins.pop(node, None)
        if out != self.map_for(node) or keep != pinned:
            self.set_map(node, out)
        return out

    def binding_for(self, node):
        return self.bindings.get(node)

    def set_binding(self, node, pid):
        if not node:
            return
        if pid is None or pid == CLEAN_ID:   # no binding == Clean
            self.bindings.pop(node, None)
        else:
            self.bindings[node] = pid
        self.save_bindings()

    def effective_preamp(self, p, node=None):
        """What the wire gets. In Auto it is computed here and nowhere
        stored; in Manual it is the number the user rides, which lives in
        the app's ui state beside the mode. Neither is a property of the
        earphone, so neither is written into the profile: the preamp card
        sits above every profile in the window and belongs to the app.

        The hook computes Auto exactly as it publishes -- without the taste
        layer, which it does not apply either -- so its number and its
        graph agree.
        """
        st = load_ui_state()
        if not bool(st.get("preamp_auto", True)):
            return float(st.get("preamp", 0.0) or 0.0)
        loc = list(self.local_for(node).values()) if node else None
        t = auto_preamp_db(p, local=loc)
        return -t if t else 0.0

    def graph_for_node(self, node):
        pid = self.bindings.get(node)
        if not pid or pid == CLEAN_ID:
            return None                      # hook leaves the node alone
        p = self.profiles.get(pid)
        if not p:
            return None
        m = getattr(self, "maps", {}).get(node) or {}
        return profile_graph(dict(p, preamp=self.effective_preamp(p, node)),
                             slots=self.slots_for(node),
                             local=self.locals_for(node, list(m)))

    def presets(self):
        """{node.name: graph_string} for every node bound to a non-Clean,
        content-ful profile. Pushed into the metadata (--apply, and the one-time
        migration of existing bindings into the hook's persistent state)."""
        out = {}
        for node, pid in self.bindings.items():
            if not pid or pid == CLEAN_ID:
                continue
            p = self.profiles.get(pid)
            if not p or not profile_has_content(p):
                continue
            out[node] = self.graph_for_node(node)
        return out
