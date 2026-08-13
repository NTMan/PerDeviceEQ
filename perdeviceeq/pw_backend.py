"""PipeWireBackend: the heir of AudioBackend that speaks PipeWire.

Every verb delegates to the battle-tested functions in py
where one exists; what did not exist there yet (stream mute, sink
volume) landed there as this heir's permanent verbs. Reading the
graph dump is this class's native tongue: the foreign-stream reader
lives here, and the session's private copies retire when the weaving
step routes the program through the backend.

One instance per process: use backend().
"""

# ---- engine room (formerly py) ----
# Runtime bridge to PipeWire and, through the 'per-device-eq' metadata object,
# to the WirePlumber Lua hook.
#
# The app never talks to Lua directly: it writes a device's inline graph string
# into the metadata (`metadata_set`) and the hook -- subscribed to that object --
# applies it to the live node and re-applies on every reconnect. Reading state
# (sinks, channels, params, default) is done by shelling out to pw-dump /
# pw-metadata. No GTK here; only stdlib + the PipeWire CLI tools.

def _run(cmd, timeout=2.0):
    """Run a helper. A hung pw-* child is the classic way to freeze the GUI, so
    every call is bounded by a timeout; on timeout/failure we kill the child and
    return a sentinel CompletedProcess instead of blocking forever."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")
    except Exception as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))







def pw_dump():
    try:
        return json.loads(_run(["pw-dump"], timeout=5.0).stdout)
    except Exception:
        return []

def default_sink_name():
    try:
        out = _run(["pw-metadata", "-n", "default", "0", "default.audio.sink"]).stdout
        m = re.search(r"value:'?(\{.*?\})'?", out)
        if m:
            return json.loads(m.group(1)).get("name")
    except Exception:
        pass
    return None


def default_sink_from_dump(dump):
    """Default sink name from the 'default' Metadata object in a pw dump,
    or None -- lets one dump yield the default without a pw-metadata call."""
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Metadata":
            continue
        props = o.get("props") or (o.get("info") or {}).get("props") or {}
        if props.get("metadata.name") != "default":
            continue
        for e in (o.get("metadata") or []):
            if e.get("key") == "default.audio.sink":
                v = e.get("value")
                if isinstance(v, dict):
                    return v.get("name")
                if isinstance(v, str):
                    try:
                        return json.loads(v).get("name")
                    except Exception:
                        pass
    return None


def list_sinks(dump=None, default=None):
    dump = dump if dump is not None else pw_dump()
    if default is None:
        default = default_sink_name()
    sinks = []
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("media.class") == "Audio/Sink":
            name = p.get("node.name")
            if not name:
                continue
            sinks.append({"id": o["id"], "name": name,
                          "desc": p.get("node.description") or name,
                          "prio": p.get("priority.session") or 0,
                          "routes": card_output_ports(name, dump),
                          # the channel list rides along: the dump is
                          # already in hand here, and a window that
                          # asks for it separately pays a pw-dump
                          # SUBPROCESS on the main loop every time
                          "channels": _node_channels(name, dump),
                          "default": name == default})
    sinks.sort(key=lambda s: -(s["prio"] or 0))
    return sinks


def _door_desc(route):
    """A door reads the way the desktop reads: PORT then CARD, and
    nothing else.

    It used to say "(switches the card)", which explained OUR
    machinery rather than the choice. From where a person sits,
    picking a port behind another profile and picking a port that
    happens to have a live node are the same act -- choose another
    output -- and only PipeWire knows the difference. No desktop
    mentions it, and neither do we now.

    The card is still named, because several cards have a port called
    Speakers and a row that says only "Speakers" is a coin toss."""
    port = route.get("description") or route.get("name") or "?"
    card = route.get("card")
    return "%s - %s" % (port, card) if card else port


def _offerable(route):
    """A port with no jack behind it is not an offer.

    PipeWire marks a route `available: no` when nothing is plugged in,
    and a graphics card enumerates one output per connector -- with a
    single television that is one available port and three that lead
    nowhere. The desktop filters on this; not filtering is how the
    list grew four HDMI entries. `unknown` counts as available, which
    is what most sound cards report."""
    return route.get("available", True)


def list_playback_entries_from(sinks):
    """The sinks, plus a DOOR for every output port the loaded profile
    does not carry.

    The same answer the input list already gives, from the other side:
    a card's other outputs are real and the desktop offers them, so
    the app must too. Leaving them out meant going to the desktop's
    own settings to come back to a profile -- for the M62, the whole
    Direct output vanished from the list the moment its Default
    profile was loaded."""
    out, doors, seen = [], [], set()
    for s in sinks or []:
        e = dict(s)
        e["node"] = s["name"]
        out.append(e)
        for r in (s.get("routes") or []):
            if r.get("reachable", True) or not _offerable(r):
                continue
            ident = (r.get("device_id"), r.get("index"))
            if ident in seen:
                continue
            seen.add(ident)
            d = dict(s)
            d["name"] = card_entry_key(r["device_id"], r["index"])
            d["node"] = None
            d["route"] = r
            d["default"] = False
            d["prio"] = -1
            d["desc"] = _door_desc(r)
            doors.append(d)
    return out + doors

def list_sources(dump=None):
    """Audio/Source nodes (measurement mics live here): id, name, desc,
    priority.session, sorted by priority. No 'default' flag on purpose --
    the system default source is the comms/webcam mic, never the
    measurement rig, so the measure window pre-selects the last-used
    source (per-sink recall) instead of the default."""
    dump = dump if dump is not None else pw_dump()
    sources = []
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("media.class") == "Audio/Source":
            name = p.get("node.name")
            if not name:
                continue
            sources.append({"id": o["id"], "name": name,
                            "desc": p.get("node.description") or name,
                            "prio": p.get("priority.session") or 0,
                            "routes": card_input_ports(name, dump),
                            # as on the sink side: the dump is in hand
                            # here, and a window that asks separately
                            # pays a subprocess on the main loop
                            "channels": _node_channels(name, dump),
                            "gain": gain_of_node(o)})
    sources.sort(key=lambda s: -(s["prio"] or 0))
    return sources

def card_output_ports(node_name, dump=None):
    """The output half of card_input_ports: every output port of the
    card behind a node, with the same `reachable` and `profiles`."""
    return _card_ports(node_name, "Output", dump)


def _spa_info(info):
    """A route's `info` is a flat SPA array: a count, then key, value,
    key, value. PipeWire publishes route.hw-volume and route.hw-mute
    there, and pactl's HW_VOLUME_CTRL flag is that same bit under
    another name."""
    if not isinstance(info, (list, tuple)) or len(info) < 3:
        return {}
    body = list(info[1:])
    return {str(k): str(v) for k, v in zip(body[0::2], body[1::2])}


def _card_ports(node_name, want, dump=None):
    """EVERY port of the card behind a node in one direction, not just
    the ones the loaded profile exposes.

    GNOME lists a card's Microphone, Line In and S/PDIF as separate
    entries in its input list. PipeWire has ONE node for the card's
    analog input and calls the choice a ROUTE on the device behind
    it. Our picker lists nodes, so that choice was invisible: the
    app could be listening to the microphone jack while the cable
    sat in line in, and nothing in the window could say so or move
    it.

    A port the loaded profile cannot reach is still real: the M62
    keeps Mic 1, Mic 2 and Aux stereo in behind its Default profile
    while Direct is loaded, and refusing to name them is how this
    app came to offer fewer inputs than the desktop does. Each entry
    carries `reachable` -- whether choosing it costs a profile
    change -- and `profiles`, the profiles that carry it. The caller
    decides what that costs, because switching profile replaces the
    card's OUTPUTS too.

    Returns {index, device_id, card_device, name, description,
    available, profiles, reachable, active}, or an empty list when
    there is no card behind the node, which is the honest answer for
    a virtual node: it has no ports to choose from."""
    dump = dump if dump is not None else pw_dump()
    props = None
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("node.name") == node_name:
            props = p
            break
    if props is None:
        return []
    dev_id = props.get("device.id")
    card_dev = props.get("card.profile.device")
    if dev_id is None or card_dev is None:
        return []
    device = None
    for o in dump:
        if (o.get("type") == "PipeWire:Interface:Device"
                and o.get("id") == dev_id):
            device = o
            break
    if device is None:
        return []
    params = (device.get("info") or {}).get("params") or {}
    # the card's ACTIVE routes include the output one, and route
    # indices are not guaranteed to be unique across directions:
    # matching on the index alone let an active OUTPUT route crown
    # an input port that merely shared its number, and the app then
    # believed the sweep came in through a jack with nothing in it
    active = [r for r in (params.get("Route") or [])
              if r.get("device") == card_dev
              and r.get("direction") == want]
    cur_profile = (params.get("Profile") or [{}])[0].get("index")
    card_desc = ((device.get("info") or {}).get("props") or {}).get(
        "device.description") or ""
    out = []
    for r in params.get("EnumRoute") or []:
        if r.get("direction") != want:
            continue
        devs = list(r.get("devices") or [])
        profs = list(r.get("profiles") or [])
        idx = r.get("index")
        out.append({
            "index": idx,
            "device_id": dev_id,
            "card_device": devs[0] if devs else card_dev,
            "name": r.get("name"),
            "description": r.get("description") or r.get("name"),
            "available": r.get("available") != "no",
            "profiles": profs,
            "card": card_desc,
            # MINE: this node's own device carries it, so the node can
            # be switched to it. REACHABLE: the loaded profile carries
            # it at all, on this node or a sibling. The two are not the
            # same question, and treating them as one is what put a
            # door beside every live sibling -- five outputs of one
            # profile each offering to "switch the card" to the other
            # four. Without a profile list to go by, the node's own
            # device is the only honest answer.
            "mine": card_dev in devs,
            "reachable": (cur_profile in profs if profs
                          else card_dev in devs),
            "active": (card_dev in devs
                       and any(a.get("index") == idx for a in active))})
        # volumeBase and the hw-volume bit are published only for the
        # ACTIVE route, so they can be attached to that one and to no
        # other. Unknown is the honest answer for the rest, and it is
        # what None says here.
        cur = next((a for a in active if a.get("index") == idx), None)
        out[-1]["hw_volume"] = None
        out[-1]["volume_base"] = None
        if cur is not None:
            flag = _spa_info(cur.get("info")).get("route.hw-volume")
            if flag is not None:
                out[-1]["hw_volume"] = (flag == "true")
            base = (cur.get("props") or {}).get("volumeBase")
            if base is not None:
                out[-1]["volume_base"] = float(base)
    return out


def card_input_ports(node_name, dump=None):
    """Every INPUT port of the card behind a node."""
    return _card_ports(node_name, "Input", dump)


def input_routes(node_name, dump=None):
    """The input ports THIS node can be switched between without
    touching the card's profile -- the list the picker has always
    built its rows from."""
    return [r for r in card_input_ports(node_name, dump)
            if r.get("mine", r["reachable"])]


def _profile_devices(classes):
    """The card devices a profile's classes name, by media class.

    pw-dump writes them as a count followed by entries shaped
    ['Audio/Source', n, 'card.profile.devices', [7, 8, 9]] -- walked
    rather than indexed, since the count sits in front and a future
    key could sit between."""
    out = {}
    for item in (classes or []):
        if not isinstance(item, list) or not item:
            continue
        cls = item[0]
        if not isinstance(cls, str):
            continue
        for i, k in enumerate(item):
            if k == "card.profile.devices" and i + 1 < len(item):
                v = item[i + 1]
                if isinstance(v, list):
                    out.setdefault(cls, []).extend(v)
    return out


def card_profiles(node_name, dump=None):
    """The profiles the card behind a node can be switched between:
    {index, description, active, sources, sinks} where sources and
    sinks are the card devices that profile provides. Empty when
    there is no card behind the node."""
    dump = dump if dump is not None else pw_dump()
    dev_id = None
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("node.name") == node_name:
            dev_id = p.get("device.id")
            break
    if dev_id is None:
        return []
    for o in dump:
        if (o.get("type") != "PipeWire:Interface:Device"
                or o.get("id") != dev_id):
            continue
        params = (o.get("info") or {}).get("params") or {}
        cur = (params.get("Profile") or [{}])[0].get("index")
        out = []
        for pr in params.get("EnumProfile") or []:
            devs = _profile_devices(pr.get("classes"))
            out.append({"index": pr.get("index"),
                        "device_id": dev_id,
                        "description": (pr.get("description")
                                        or pr.get("name")),
                        "active": pr.get("index") == cur,
                        "sources": devs.get("Audio/Source") or [],
                        "sinks": devs.get("Audio/Sink") or []})
        return out
    return []


def find_port_entry(entries, want):
    """The LIVE row that carries a port, or None while none does.

    `want` is (device id, card device) -- what a switch was asked for.
    After the card moves, the port arrives on a node with a row of its
    own, and this is how the window finds it without knowing the name
    that node will be given."""
    if not want:
        return None
    dev, card_dev = want
    for e in entries or []:
        if not e.get("node"):
            continue
        rs = list(e.get("routes") or [])
        one = e.get("route")
        if one:
            rs.append(one)
        for r in rs:
            if (r.get("device_id") == dev
                    and r.get("card_device") == card_dev
                    and r.get("mine", True)):
                return e
    return None


def switch_to_port(key, entries):
    """Put the card on a profile that carries the port a row names.

    Returns (device id, card device) so the caller can watch for the
    port arriving, or None when the key is not a door or nothing
    carries it. The caller vetoes its pick either way: the row it
    chose is about to stop existing."""
    dev, idx = card_entry_target(key)
    if dev is None:
        return None
    port = next((r for e in (entries or [])
                 for r in (list(e.get("routes") or [])
                           + ([e["route"]] if e.get("route") else []))
                 if r.get("device_id") == dev
                 and r.get("index") == idx), None)
    prof = (port or {}).get("profiles") or []
    if not prof:
        return None
    target = prof[0]

    def work():
        try:
            set_card_profile(dev, target)
        except Exception as e:
            from . import debug
            debug.log("card profile: %s" % e)

    in_thread(work)
    return dev, port.get("card_device")


def set_card_profile(device_id, index):
    """Switch the card to another profile, kept the way GNOME keeps
    it. This REPLACES the card's nodes -- inputs and outputs both --
    so a caller measuring on one of them has to be told first."""
    r = _run(["pw-cli", "set-param", str(device_id), "Profile",
              "{ index: %d, save: true }" % int(index)])
    if r.returncode != 0:
        raise RuntimeError("pw-cli set-param Profile failed: %s"
                           % ((r.stderr or r.stdout) or "").strip())


ENTRY_SEP = "#"


def entry_key(node, route=None):
    """The identity of a capture ENTRY: a node, or a node on one of
    its card ports. GNOME lists ports as separate inputs and so do
    we now, which means the port stops being a hidden setting and
    becomes part of WHICH RIG this is -- and that is the truth:
    the microphone jack and the line jack of one card are different
    preamps, different gain and different noise, so they deserve
    their own calibration rather than sharing one."""
    if not route:
        return node
    return "%s%s%d" % (node, ENTRY_SEP, int(route["index"]))


CARD_ENTRY = "card:"


def card_entry_key(device_id, index):
    """The identity of a port that has no node yet: the CARD and the
    port, since the node it will speak for does not exist until the
    card is switched to the profile that carries it."""
    return "%s%d%s%d" % (CARD_ENTRY, int(device_id), ENTRY_SEP,
                         int(index))


def is_card_entry(key):
    return str(key or "").startswith(CARD_ENTRY)


def card_entry_target(key):
    """(device id, route index) from such a key, (None, None) from
    anything else."""
    head, idx = split_entry(key)
    if idx is None or not is_card_entry(head):
        return None, None
    try:
        return int(str(head)[len(CARD_ENTRY):]), idx
    except ValueError:
        return None, None


def split_entry(key):
    """(node, route index or None) from an entry key. The graph gets
    the NODE; everything else keeps the identity."""
    if not key:
        return key, None
    node, sep, idx = str(key).rpartition(ENTRY_SEP)
    if not sep or not idx.isdigit():
        return key, None
    return node, int(idx)


def entry_node(key):
    return split_entry(key)[0]


def list_capture_entries_from(sources):
    """The same expansion over a source list already pulled by the
    heartbeat -- the window never runs a dump of its own."""
    out, doors, seen = [], [], set()
    for s in sources or []:
        routes = [r for r in (s.get("routes") or [])
                  if r.get("mine", r.get("reachable", True))]
        for r in (s.get("routes") or []):
            # a port the loaded profile does not carry: listed, so
            # the app offers what the desktop offers, and marked
            # with what choosing it costs. It speaks for no node
            # until the card is switched, which is what its key
            # says -- the card, not a node.
            if r.get("reachable", True) or not _offerable(r):
                continue
            ident = (r.get("device_id"), r.get("index"))
            if ident in seen:
                continue
            seen.add(ident)
            e = dict(s)
            e["name"] = card_entry_key(r["device_id"], r["index"])
            e["node"] = None
            # and no ID either. It was a copy of the LIVE source, so
            # the door kept the live node's id while declaring it
            # spoke for no node -- and a caller that reached past
            # `node` for `id` got a working handle to a device the
            # person had not chosen. The input meter did exactly that
            # and metered a stranger; the capture-gain slider reads
            # the same field and would have turned a stranger's knob.
            # A door speaks for no node: not by its name, not by its
            # id, not by its channels.
            e["id"] = None
            e["channels"] = []
            e["route"] = r
            e["gain"] = None
            e["prio"] = -1
            e["desc"] = _door_desc(r)
            doors.append(e)
        if len(routes) < 2:
            e = dict(s)
            e["node"] = s["name"]
            e["route"] = routes[0] if routes else None
            e["routes"] = routes
            out.append(e)
            continue
        for r in routes:
            e = dict(s)
            e["name"] = entry_key(s["name"], r)
            e["node"] = s["name"]
            e["route"] = r
            e["routes"] = routes
            e["desc"] = "%s - %s" % (r["description"], s["desc"])
            out.append(e)
    return out + doors


def list_capture_entries(dump=None):
    """The input list the way every desktop shows it: one row per
    jack. A card with several input ports contributes one entry per
    port, described the way GNOME describes them ("Line In - CM106
    Like Sound Device"); a node with one port or none contributes
    itself. Each entry carries the node it speaks for and the route
    it means, so nothing downstream has to parse a label."""
    return list_capture_entries_from(list_sources(dump))


def active_input_route(node_name, dump=None):
    """The port a capture node is listening on right now, by
    description, or None when the node has no card ports. Goes into
    the take's passport: "captured with CM106" does not say whether
    the sweep came in through the microphone jack or the line one,
    and those are different measurements."""
    for r in input_routes(node_name, dump):
        if r["active"]:
            return r["description"]
    return None


def set_input_route(route):
    """Switch the card to that port, and keep it (save: true) the way
    GNOME's own switch does -- the route belongs to the device, so
    every application sees the change, which is exactly why the app
    may set it rather than work around it."""
    r = _run(["pw-cli", "set-param", str(route["device_id"]), "Route",
              "{ index: %d, device: %d, save: true }"
              % (int(route["index"]), int(route["card_device"]))])
    if r.returncode != 0:
        raise RuntimeError("pw-cli set-param Route failed: %s"
                           % ((r.stderr or r.stdout) or "").strip())


def node_params(name, dump=None):
    dump = dump if dump is not None else pw_dump()
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("node.name") == name:
            return (o.get("info") or {}).get("params") or {}, o["id"]
    return None, None


def node_props(name, dump=None):
    dump = dump if dump is not None else pw_dump()
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("node.name") == name:
            return p
    return None


def _parse_position(raw):
    """audio.position as pw-dump gives it: the string "[ FL, FR ]" or a
    plain list. Empty when the property is absent or unreadable."""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if not isinstance(raw, str):
        return []
    return [s.strip() for s in raw.strip().strip("[]").split(",")
            if s.strip()]

def resolve_sink_id(name, dump=None):
    _, nid = node_params(name, dump)
    return nid

def graph_loaded(name, dump=None):
    """Best-effort: a loaded in-node graph shows up as an extra Props block
    whose only key is 'params'."""
    params, _ = node_params(name, dump)
    if not params:
        return False
    for d in params.get("Props", []):
        if isinstance(d, dict) and list(d.keys()) == ["params"]:
            return True
    return False

def metadata_set(node_name, graph):
    """Write a device's graph into the 'per-device-eq' metadata. The WP hook is
    subscribed and applies it to the live node (and on every later reconnect).
    Stored as a plain string (no type tag), which the hook reads verbatim."""
    r = _run(["pw-metadata", "-n", METADATA_NAME, "0", node_name, graph])
    return r.returncode == 0 and "Found" in (r.stdout + r.stderr)


def metadata_clear(node_name):
    """Delete a device's key (Clean / unbound). The hook flattens the live node."""
    r = _run(["pw-metadata", "-n", METADATA_NAME, "-d", "0", node_name])
    return r.returncode == 0

_POS_FALLBACK = ["FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR"]



def _node_channels(name, dump=None):
    """Channel keys for any node (sink or source).

    audio.position FIRST, because that is what the node's PORTS are
    called -- what pw-dump, pw-link, the desktop and any capture we open
    on this node will match against by name. The negotiated Format can
    carry a different vocabulary for the same channels: the M62 in its
    ten-channel mode has ports AUX0..AUX9 while its Format still repeats
    the firmware's surround fiction FL..SL. Both lists describe the same
    channels in the same ORDER, so the graph we publish is unaffected --
    filtersN is the Nth channel either way -- but everything that
    matches by NAME breaks, and the window then labels a card with
    channels it does not have. Format is the fallback, then the channel
    count, then stereo.
    """
    dump = dump if dump is not None else pw_dump()
    pos = _parse_position((node_props(name, dump) or {}).get(
        "audio.position"))
    if pos:
        return _dedup_channels(pos)
    params, _ = node_params(name, dump)
    pos, nch = None, None
    if params:
        for blk in (params.get("Format") or []):
            if isinstance(blk, dict):
                if blk.get("position"):
                    pos = blk["position"]
                if blk.get("channels"):
                    nch = blk["channels"]
        if nch is None:
            for d in (params.get("Props") or []):
                if isinstance(d, dict) and isinstance(d.get("channelVolumes"), list):
                    nch = len(d["channelVolumes"])
    if isinstance(pos, list) and pos:
        keys = [str(p) for p in pos]
    elif nch:
        keys = _POS_FALLBACK[:nch] if nch <= len(_POS_FALLBACK) \
               else ["Ch%d" % (i + 1) for i in range(nch)]
    else:
        keys = ["FL", "FR"]
    return _dedup_channels(keys)


def _dedup_channels(keys):
    """A channel name repeated in a map still has to address one
    channel each, so the second one wears a suffix."""
    seen, out = {}, []
    for k in keys:
        if k in seen:
            seen[k] += 1
            out.append("%s.%d" % (k, seen[k]))
        else:
            seen[k] = 0
            out.append(k)
    return out


def playback_channels(name, dump=None):
    """The channel names a sink's PLAYBACK PORTS carry, in port order.

    What a sweep must be aimed with. pw-play routes a mono stream by
    NAME, and the name it matches against is the port's -- so on a card
    whose ports are called FL..SL, asking for AUX0 matches nothing and
    the sweep is spread over every channel instead of one. That is the
    monitor tap's lesson from the other end: labels come from
    audio.position, routing comes from the ports."""
    return _port_channels(name, "in", dump)


def monitor_channels(name, dump=None):
    """The channel names the node's MONITOR PORTS carry, in port order.

    A third vocabulary, and the only one that matters for LINKING: the
    M62 in its ten-channel mode has audio.position AUX0..AUX9, a
    negotiated Format of FL..SL, and ports called monitor_FL..monitor_SL.
    A capture asks for a channel map and PipeWire matches it against the
    PORTS, so a map from any other list matches nothing and the columns
    arrive wherever the fallback puts them -- which is what left the
    level meter reading channels 7 and 8 while the music played on 1
    and 2. Empty when the node or its ports cannot be read; the caller
    then states no map at all.
    """
    return _port_channels(name, "out", dump)


def _port_channels(name, direction, dump=None):
    """A node's port channel names in port order, one direction."""
    dump = dump if dump is not None else pw_dump()
    nid = None
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("node.name") == name:
            nid = o["id"]
            break
    if nid is None:
        return []
    rows = []
    for o in dump:
        if "Port" not in (o.get("type") or ""):
            continue
        p = (o.get("info") or {}).get("props") or {}
        if str(p.get("node.id")) != str(nid):
            continue
        if (p.get("port.direction") or "") != direction:
            continue
        ch = p.get("audio.channel")
        if ch:
            rows.append((int(p.get("port.id") or 0), str(ch)))
    return [c for _i, c in sorted(rows)]


def sink_channels(name, dump=None):
    return _node_channels(name, dump)


def source_channels(name, dump=None):
    """Capture-channel keys for a source (mic/rig), e.g. ['FL','FR']. A
    measurement rig is 1- or 2-channel; the count is how many calibration
    files it needs, one per capture channel."""
    return _node_channels(name, dump)

def in_thread(fn):
    """Run fn on a daemon thread (off-main-loop subprocess work)."""
    import threading
    threading.Thread(target=fn, daemon=True).start()


# The PipeWire command-line tools we shell out to. Names are the same
# across distributions; the *package* that ships them is not, so we
# check for the tools and let the user install them as their distro
# prefers.
REQUIRED_TOOLS = ["pw-metadata", "pw-dump"]

def missing_tools_message(missing):
    return ("These PipeWire command-line tools are required but were not found "
            "in PATH:\n\n    %s\n\nInstall the PipeWire utilities with your "
            "distribution's package manager and try again." % "  ".join(missing))


PLAY_NODE = "pde-measure-sweep"
CAPTURE_NODE = "pde-measure-capture"
_STREAM_PROPS = ("{ node.name = %s, node.target = %d, "
                 "node.dont-reconnect = true, application.name = "
                 "\"per-device-eq measure\" }")

import json
import os
import re
import subprocess
import sys

from .config import METADATA_NAME
from .audio_backend import AudioBackend


# The heir's own verbs: they lived in py while the trio
# still had twins there; the doomed module only slims now, so the
# code that exists solely for this class lives with this class.
def metadata_get(key):
    r = _run(["pw-metadata", "-n", METADATA_NAME, "0", key])
    m = re.search(r"key:'%s' value:'(.*?)' type:" % re.escape(key),
                  r.stdout, re.S)
    return m.group(1) if m else None


def wpstate_get(key):
    """Read a sink's graph from the WirePlumber hook's persisted state
    (a GKeyFile at $XDG_STATE_HOME/wireplumber/per-device-eq). The hook
    seeds its runtime table from here on a cold start and does NOT
    publish persisted graphs into the metadata, so a freshly-booted
    session where the GUI was never opened has the profile ONLY here."""
    base = os.environ.get("XDG_STATE_HOME") \
        or os.path.expanduser("~/.local/state")
    path = os.path.join(base, "wireplumber", "per-device-eq")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("[") or "=" not in line:
                    continue
                k, v = line.split("=", 1)     # node.name has no '=' itself
                if k == key:
                    return v or None
    except OSError:
        pass
    return None


def set_stream_mute(node_id, mute):
    """Props mute on a stream node; True on success. The backend's
    verb; the session's private twin retires when the weaving step
    routes it here."""
    r = _run(["pw-cli", "set-param", str(node_id), "Props",
              "{ mute = %s }" % ("true" if mute else "false")])
    return r.returncode == 0


def gain_state(node_name, dump=None):
    """(cubic gain, "hardware"/"software"/None) for a capture node.

    PipeWire stores channelVolumes linear; every user-facing number
    -- wpctl, GNOME, ours -- is its cube root. softVolumes is the
    part PipeWire multiplies into the SAMPLES: when the card has a
    real gain control the route takes the volume and softVolumes
    stays at unity, and when it does not, the same slider is only
    arithmetic after the converter. The two are worth telling apart
    on a measurement rig -- software gain buys no headroom against
    an analog overload and improves no noise, it just scales what
    was already digitised."""
    dump = dump if dump is not None else pw_dump()
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if p.get("node.name") == node_name:
            return gain_of_node(o)
    return None, None


def fader_kind(routes, gain=None):
    """What a capture fader can actually DO, decided from the active
    route rather than from a value that happens to be in it.

    gain_of_node() can tell a hardware multiplier from a software one,
    but only while the fader is off unity -- at unity both multiply by
    one and it honestly returns None. Unity is where an untouched rig
    sits, so that answer arrives exactly when it is not useful. The
    route carries the same fact statically:

      "analog"      the route has a hardware volume and its unity
                    point is below the top, so there is travel ABOVE
                    unity: real gain, worth searching, buys SNR
      "attenuator"  a hardware volume whose unity point IS the top:
                    it can only cut. Moving it throws resolution away
                    and buys nothing, so it belongs at maximum
      "software"    no hardware volume on the route, or no route at
                    all: the fader is a multiplier in the graph. It
                    buys no headroom and no noise, and belongs at one

    A fader is worth showing in exactly one of these three cases.
    """
    active = next((r for r in (routes or []) if r.get("active")), None)
    if active is None or not active.get("hw_volume"):
        return "software"
    base = active.get("volume_base")
    if base is None:
        # a hardware volume whose unity point was not published: the
        # live reading is the only evidence left, and at unity there
        # is none
        return "analog" if (gain or (None, None))[1] == "hardware" else \
            "attenuator"
    return "analog" if base < 0.999 else "attenuator"


def source_width(src, fallback=2):
    """How many capture columns a source record stands for.

    A source's "channels" is the list of its channel KEYS, not a
    count. Reading it as one crashed a tool, and the window and that
    tool were computing this two ways -- which is how the second one
    came to be wrong. One place now.

    The list from the heartbeat's snapshot is preferred because asking
    the backend costs a pw-dump SUBPROCESS, and this is called from
    the prefill, from the rig selection and from every pair change.
    And there is NO clamp to two: "a measurement rig is 1- or
    2-channel" was true of a USB dongle and false of an interface,
    where a loopback comes back on column 2 of sixteen.
    """
    n = len(src.get("channels") or [])
    if not n:
        try:
            n = len(backend().input_channels(
                src.get("node") or src["name"]))
        except Exception:                              # noqa: BLE001
            n = fallback
    return max(1, n)


def gain_of_node(o):
    """The same reading straight off a node object, so the list of
    sources can carry it from the graph dump the window already
    receives -- nothing in this window may shell out on the main
    loop."""
    d = {}
    for blk in ((o.get("info") or {}).get("params")
                or {}).get("Props") or []:
        if isinstance(blk, dict):
            d.update(blk)
    cv = [float(v) for v in d.get("channelVolumes") or []]
    if not cv:
        return None, None
    cubic = (sum(cv) / len(cv)) ** (1.0 / 3.0)
    sv = [float(v) for v in d.get("softVolumes") or []]
    if not sv or cubic > 0.999:
        # at unity there is nothing to tell apart: both kinds
        # multiply by one, and guessing would be invention
        kind = None
    elif all(abs(v - 1.0) < 1e-6 for v in sv):
        kind = "hardware"
    elif all(abs(a - b) < 1e-6 for a, b in zip(sv, cv)):
        kind = "software"
    else:
        kind = None
    return cubic, kind


def set_gain(node_id, cubic):
    """wpctl writes through to the device Route where one exists,
    which is what makes a card's real input gain move; raw Props
    writes on ALSA nodes do not stick. Raises on failure."""
    r = _run(["wpctl", "set-volume", str(node_id), "%.4f" % cubic])
    if r.returncode != 0:
        raise RuntimeError("wpctl set-volume failed: %s"
                           % (r.stderr or "").strip())


def set_sink_volume(sink_id, cubic):
    """wpctl writes through to the device Route where one exists; raw
    Props writes on ALSA sinks do not stick. Raises on failure."""
    r = _run(["wpctl", "set-volume", str(sink_id), "%.4f" % cubic])
    if r.returncode != 0:
        raise RuntimeError("wpctl set-volume failed: %s"
                           % r.stderr.strip())


class StreamHandle:
    """A live capture or playback stream. read(n) for captures
    (raw interleaved bytes from stdout), wait() for playbacks,
    terminate() and alive() for both; poll(), returncode and
    stderr_read() serve the path courts."""

    def __init__(self, proc):
        self._proc = proc

    def read(self, nbytes):
        return self._proc.stdout.read(nbytes)

    def wait(self, timeout=None):
        return self._proc.wait(timeout=timeout)

    def poll(self):
        return self._proc.poll()

    @property
    def returncode(self):
        return self._proc.returncode

    def stderr_read(self):
        if self._proc.stderr is None:
            return ""
        return self._proc.stderr.read() or ""

    def kill(self):
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()

    def terminate(self, kill_after=3.0):
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=kill_after)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc.wait()

    def alive(self):
        return self._proc.poll() is None


class PipeWireBackend(AudioBackend):
    """The audio server is PipeWire; devices are node names."""

    # -- state verbs ---------------------------------------------------

    def _push_graph(self, device, value):
        if value is None:
            return metadata_clear(device)
        return metadata_set(device, value)

    def _push_volume(self, device, cubic):
        sink_id = resolve_sink_id(device)
        set_sink_volume(sink_id, cubic)

    def _push_stream_mutes(self, sink, mute, prior=None):
        if not mute:
            for node_id in (prior or []):
                set_stream_mute(node_id, False)
            return None
        dump = pw_dump()
        sink_id = resolve_sink_id(sink, dump)
        muted = []
        for st in self._foreign_output_streams(dump, sink_id):
            if st["prior_mute"]:
                continue
            if set_stream_mute(st["id"], True):
                muted.append(st["id"])
        return muted

    @staticmethod
    def _foreign_output_streams(dump, sink_id):
        """Output streams linked into the sink, ours excluded."""
        linked = set()
        for o in dump:
            if not str(o.get("type", "")).endswith("Link"):
                continue
            info = o.get("info") or {}
            if info.get("input-node-id") == sink_id:
                linked.add(info.get("output-node-id"))
        out = []
        for o in dump:
            if not str(o.get("type", "")).endswith("Node"):
                continue
            if o.get("id") not in linked:
                continue
            props = (o.get("info") or {}).get("props") or {}
            if props.get("media.class") != "Stream/Output/Audio":
                continue
            name = props.get("node.name") or ""
            if name.startswith("pde-measure"):
                continue
            prior = False
            params = (o.get("info") or {}).get("params") or {}
            for entry in params.get("Props") or []:
                if "mute" in entry:
                    prior = bool(entry["mute"])
            out.append({"id": o["id"], "node_name": name,
                        "prior_mute": prior})
        return out

    def _read_graph(self, device):
        value = metadata_get(device)
        if value is not None:
            return value, "metadata"
        value = wpstate_get(device)
        return value, ("wpstate" if value is not None else None)

    def _read_volume(self, device):
        dump = pw_dump()
        try:
            sink_id = resolve_sink_id(device, dump)
        except Exception:
            return None
        for o in dump:
            if o.get("id") != sink_id:
                continue
            params = (o.get("info") or {}).get("params") or {}
            for entry in params.get("Props") or []:
                cv = entry.get("channelVolumes")
                if cv:
                    return (sum(cv) / len(cv)) ** (1.0 / 3.0)
        return None

    def _restore_failed(self, device, value):
        print("CRITICAL: failed to restore the EQ profile; put it "
              "back manually:\n  pw-metadata -n per-device-eq 0 "
              "'%s' '%s'" % (device, value), file=sys.stderr)

    def missing_requirements(self):
        """Command-line tools the platform still needs."""
        import shutil
        return [t for t in REQUIRED_TOOLS
                if shutil.which(t) is None]

    def meter_available(self):
        """pw-record is needed only by the tier-2 live meter: its
        absence degrades the app to the static tier-1 estimate,
        nothing more. PDEQ_NO_METER=1 forces the degraded mode for
        diagnostics: the GUI then runs without its sink-monitor tap,
        so a measurement can be compared with and without that
        stream present."""
        import shutil
        if os.environ.get("PDEQ_NO_METER"):
            return False
        return shutil.which("pw-record") is not None

    def hook_protocol(self):
        """The protocol the LOADED hook stamped into the channel.
        Returns (found, version): found False = no metadata object
        (WirePlumber or the hook is not up -- the install/restart
        narrations own that story, say nothing extra); found True
        with version None = a pre-versioning hook."""
        r = _run(["pw-metadata", "-n", METADATA_NAME, "0", "protocol"])
        out = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"key:'protocol'\s+value:'([^']*)'", out)
        return ("Found" in out), (m.group(1) if m else None)

    def output_channels(self, device):
        """Channel positions of an output device."""
        return sink_channels(device)

    def monitor_positions(self, device):
        """What a capture on this node's monitor must ask for."""
        return monitor_channels(device)

    def input_channels(self, device):
        """Channel positions of an input device."""
        return source_channels(device)

    # -- observed side ---------------------------------------------------

    def _pull(self, dump=None):
        if dump is None:
            dump = pw_dump()
        # kept, not thrown away: a window that needs the graph a
        # moment later would otherwise spawn pw-dump again, on the
        # main loop, for a picture this one already holds
        self.last_dump = dump
        default = default_sink_from_dump(dump)
        if default is None:
            default = default_sink_name()
        return {
            "sinks": list_sinks(dump, default=default),
            "sources": list_sources(dump),
            "default_sink": default,
        }

    def update(self, dump=None):
        """Refresh from one pw_dump (fetched if not given). Returns
        True if the population changed. Synchronous; tests and the
        birth reconcile call it directly."""
        return self._ingest(self._pull(dump))

    # -- the heartbeat: one poll feeds every window ----------------------

    POLL_S = 3

    _timer = 0
    _busy = False
    _glib = None

    def start(self, interval_s=None):
        """Begin the periodic refresh (idempotent). Uses GLib; only
        the app calls this -- tests drive update() directly."""
        if self._timer:
            return
        from gi.repository import GLib
        self._glib = GLib
        self._timer = GLib.timeout_add_seconds(
            interval_s or self.POLL_S, self._tick)

    def stop(self):
        if self._timer and self._glib is not None:
            self._glib.source_remove(self._timer)
        self._timer = 0

    def _tick(self):
        if self._busy:
            return True                  # previous refresh running
        self._busy = True

        def work():
            snap = None
            try:
                snap = self._pull()
            finally:
                self._glib.idle_add(self._apply_snap, snap)
        in_thread(work)
        return True                      # keep the timer running

    def _apply_snap(self, snap):
        self._busy = False
        if snap is not None:
            self._ingest(snap)
        return False

    # -- measurement streams ---------------------------------------------

    def capture(self, device, channels, rate):
        """Raw interleaved f32 from `device` (a node id), pinned via
        node.target (NOT --target, which the session manager relinks
        to the default source) and named for the path courts."""
        cmd = ["pw-record", "--raw",
               "-P", _STREAM_PROPS % (CAPTURE_NODE, int(device)),
               "--format", "f32", "--rate", str(int(rate)),
               "--channels", str(int(channels)), "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        return StreamHandle(proc)

    @staticmethod
    def monitor_cmd(sink, channels, rate=48000, positions=None):
        """The pw-record command line for a monitor tap.

        The channel MAP is stated, not left to be guessed. Asking for a
        count alone lets PipeWire pick the default positions for that
        count and matrix the monitor onto them, so the order of the
        columns we read is the STREAM's, not the node's -- while the
        caller labels those columns with the node's own channel names
        and applies one EQ chain per column. On a stereo card the two
        agree by luck; on a ten-channel node they part, and both the
        bars and the filtering land on the wrong channels. Naming the
        node's own positions makes the tap a pass-through.
        """
        pos = list(positions or [])
        # --raw, or pw-record writes a 24-byte .snd header first and the
        # reader eats it as audio. Six float32 samples of offset rotate
        # every channel by six: on a ten-channel node the music showed up
        # on rows 7 and 8 while it played on 1 and 2, and on stereo six
        # samples are exactly three frames, so the rotation is zero and
        # nobody noticed for as long as this code has existed. The
        # measurement capture in this same file always passed it.
        cmd = ["pw-record", "--raw", "--target", str(sink),
               "-P", "{ stream.capture.sink = true,"
                     " node.name = per-device-eq-meter,"
                     " node.dont-reconnect = true,"
                     " application.name = \"Per-Device EQ\" }",
               "--format", "f32", "--rate", str(int(rate)),
               "--channels", str(int(channels))]
        if len(pos) == int(channels):
            cmd += ["--channel-map", ",".join(pos)]
        cmd.append("-")
        return cmd

    def monitor_capture(self, sink, channels, rate=48000,
                        positions=None):
        """Spawn pw-record on a sink's monitor (PRE-EQ tap in the in-node
        topology) streaming raw interleaved f32 to stdout. Returns the Popen;
        the caller owns its lifetime and reads .stdout.

        Privacy note (field-verified observations): this capture alone does
        NOT light GNOME's microphone indicator (monitor-source recordings are
        excluded), and gnome-control-center's Sound page alone does not
        either -- the icon appears only when BOTH run, and dies with the
        panel while this capture keeps going. The trigger is therefore an
        interaction (likely an extra meter stream the panel creates in
        reaction to a foreign recording); `pactl list source-outputs` while
        the icon is lit names the culprit. The stream stays named anyway, so
        mixer UIs show who is listening to what.

        node.dont-reconnect pins the tap to its pipe: without it,
        WirePlumber re-parents a capture whose target died onto the
        DEFAULT sink's monitor, and rerouted streams do not come home
        when the target returns. A sink that forks per card profile
        (IL-DSP: analog and iec958 alternate, one node at a time) can
        die and be reborn between two of our polls, so the wander was
        invisible to the app -- the meter kept dancing to another
        device's music (field catch). With the flag the tap dies with
        its pipe; the GUI notices the dead worker and re-arms."""
        cmd = self.monitor_cmd(sink, channels, rate, positions)
        return StreamHandle(subprocess.Popen(
            cmd, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL))

    def play(self, device, wav_path, stream_volume=1.0,
             channel_map=None):
        """Play a wav into `device` (a node id), pinned and named
        like capture(); stderr is kept for the play court."""
        cmd = ["pw-play", "--volume", "%.1f" % stream_volume,
               "-P", _STREAM_PROPS % (PLAY_NODE, int(device))]
        if channel_map:
            cmd += ["--channel-map", channel_map]
        cmd.append(wav_path)
        # the sweep is aimed by NAME, and a name the sink does not have
        # matches nothing and spreads the sweep over every channel --
        # so what was asked for goes in the log. A whole evening went
        # on reasoning about which channel a sweep reached; one line
        # here answers it.
        from . import debug
        debug.sweep_trace("node %s channel-map=%s"
                          % (device, channel_map
                             or "(none, plain mono)"))
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        return StreamHandle(proc)


_instance = None


def backend():
    """The process-wide PipeWireBackend."""
    global _instance
    if _instance is None:
        _instance = PipeWireBackend()
    return _instance
