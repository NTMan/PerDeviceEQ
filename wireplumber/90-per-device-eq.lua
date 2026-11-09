-- 90-per-device-eq.lua — per-output-device EQ for PipeWire.
--
-- Part of per-device-eq (https://github.com/NTMan/PerDeviceEQ).
-- This script is STATIC: it ships in the repository and is installed verbatim;
-- per-device-eq.py never generates it. It is installed PER-USER, so no package
-- manager owns or removes the installed copy: remove it with
-- `per-device-eq.py --uninstall` (Flatpak: `flatpak run
-- io.github.ntman.PerDeviceEQ --uninstall`). If the app itself is already
-- gone (package removed first), remove this hook by hand:
--   rm ~/.local/share/wireplumber/scripts/90-per-device-eq.lua
--   rm ~/.config/wireplumber/wireplumber.conf.d/90-per-device-eq.conf
--   systemctl --user restart wireplumber
-- It is the single writer of the in-node
-- EQ filter-graph on each sink, and the sole owner of the persisted state.
--
-- How it works:
--   * what each device plays is kept in memory -- its correction and the
--     preamp it plays, or explicitly nothing -- with the listener's record,
--     the taste and a preamp per device: the runtime source of truth;
--   * on startup it is seeded from WpState (~/.local/state/wireplumber/),
--     because the metadata object is empty after a PipeWire restart; only a
--     table written under this PROTOCOL is read -- an older hook may have
--     meant something else by its values, and the app's reinstall feeds a
--     new one;
--   * the GUI/CLI push live edits into the "per-device-eq" metadata object; we
--     subscribe to its "changed" signal, update the table, apply to the live
--     nodes, and persist the table back to WpState;
--   * each device -- a sink the sound leaves PipeWire at, see is_final --
--     gets its graphs (re)applied when it reaches the "running" state, which
--     also covers hotplug / Bluetooth reconnect.
-- No background process of the user's is involved: the EQ lives in WirePlumber.

local log   = Log.open_topic("pde")
local META  = "per-device-eq"   -- metadata object name (live edits from the app)
local PROTOCOL = "5"            -- the hook's version; stamped into the metadata
                                -- on activation, compared by the app. Bump it,
                                -- together with PROTOCOL in perdeviceeq/config.py,
                                -- on ANY change to this file: the installed copy
                                -- does not update with the package, and a new
                                -- number is the only thing that makes the window
                                -- offer the new hook
local STATE = "per-device-eq"   -- WpState name -> ~/.local/state/wireplumber/per-device-eq

-- THREE GRAPHS ON A NODE (perdeviceeq/eq.py, "what a node plays").
-- audioconvert runs every audioconvert.filter-graph.N a node holds, one
-- after another, and a write to one number replaces that graph alone.
--   0  the device's correction, at the sink's width;
--   1  the listener's taste;
--   2  the preamp, a flat gain.
-- All three are linear and run in floating point, so their order
-- changes nothing that is heard and nothing clips between them. The
-- taste and the preamp name one port pair, and audioconvert runs a copy
-- of such a graph on each channel of the node from PipeWire 1.6.0,
-- which the app requires.
local GRAPH = "audioconvert.filter-graph."
local ALL = { 0, 1, 2 }
local LISTENER_GRAPHS = { 1, 2 }

-- THE CONTRACT (LISTENER_KEY and STRIP in perdeviceeq/config.py, keep
-- them equal). A key names a device (device_key below) or the listener.
-- A device's value is one of three:
--   { "eq": <graph 0>, "gain": <graph 2> }
--            its correction with its preamp;
--   STRIP    explicitly nothing, every graph off: Bypass, or a
--            measurement in progress;
--   LISTENER no correction of its own: graph 0 off, the listener's
--            taste and "*" preamp on. The app writes this rather than
--            deleting the key: the metadata announces a delete only for
--            a key it holds, and after a restart it holds none of what
--            is persisted here, so a delete would never arrive.
-- The listener's own value is
--   { "taste": <graph 1>, "gain": { "<device key>": <graph 2>, ...,
--                                   "*": <graph 2> } }
-- and a device with a correction of its own plays its entry, else "*";
-- a device with none plays "*". A graph is SPA-JSON and sits in a value
-- as it is; no graph is STRIP.
--
-- A DEVICE PLAYS THE PREAMP THAT ARRIVED LAST, in its own value or in
-- the listener's. The app makes every value with the preamp that is
-- right when it is made, and writes them in the order it made them; one
-- metadata key is one write, so a correction and the preamp made for it
-- travel in one value, and the hook sets what one value says on a node
-- in one call.
local LISTENER = "@listener"

-- No EQ is the ABSENCE of a graph, not a flat one. audioconvert's
-- load_filter_graph() removes the graph when it is handed an empty
-- string. A flat param_eq is still a graph, and one that names no ports
-- declares param_eq's eight outputs (see PARAM_EQ_PORTS in
-- perdeviceeq/eq.py): on a mono node that turned silence into full
-- scale DC under a device set to Clean.
local STRIP = ""

local PROTO_KEY = "@protocol"   -- in the persisted table: what wrote it

local own = {}               -- device key -> { eq =, gain = } or STRIP;
                             -- no key: follows the listener
local listener = nil         -- { taste =, gain = { key/"*" -> graph } },
                             -- nil: never heard
local listener_text = nil    -- the listener's value as written
local nodes  = {}            -- node.name -> live device node (is_final)
local set_on = {}            -- node.name -> graphs last set there, by number
local md     = nil           -- activated metadata proxy
local devices = {}           -- bound id -> device proxy (for the holes)
local state  = State(STATE)  -- WpState handle (GKeyFile under ~/.local/state)

-- A VALUE IS READ BY WIREPLUMBER (Json.Raw(...):parse). Asked for one
-- level, it takes an object apart and hands back whatever is nested
-- deeper as its own text -- which is how a graph comes back whole
-- (push_luajson in module-lua-scripting/api/json.c, the same from
-- 0.4.17 to master).
local function fields(text)
  local ok, t = pcall(function() return Json.Raw(text):parse(1) end)
  if ok and type(t) == "table" then return t end
  return nil
end

-- a graph's text, or STRIP for none; nil for anything else
local function graph(x)
  if x == nil or x == STRIP then return STRIP end
  if type(x) == "string" and x:sub(1, 1) == "{" then return x end
  return nil
end

-- What a device's value says: { eq =, gain = }, STRIP, or nil to
-- follow the listener -- false when it does not read as the contract
-- says, and then it changes nothing.
local function read_device(value)
  if value == nil or value == LISTENER then return nil end
  if value == STRIP then return STRIP end
  local t = fields(value)
  local eq = t and t.eq ~= nil and graph(t.eq)
  local gain = t and graph(t.gain)
  if not eq or not gain then return false end
  return { eq = eq, gain = gain }
end

-- What the listener's value says: { taste =, gain = }, or nil when it
-- does not read as the contract says. No value says nothing: no taste,
-- and no preamp.
local function read_listener(value)
  if value == nil or value == STRIP then
    return { taste = STRIP, gain = {} }
  end
  local t = fields(value)
  local taste = t and t.taste ~= nil and graph(t.taste)
  if not taste then return nil end
  local gain = {}
  if t.gain ~= nil and t.gain ~= STRIP then
    local g = fields(t.gain)
    if g == nil then return nil end
    for k, x in pairs(g) do
      gain[k] = graph(x)
      if not gain[k] then return nil end
    end
  end
  return { taste = taste, gain = gain }
end

-- a device as its value says it, with the preamp it plays: what the
-- table keeps, and what the app reads back for a measurement's restore
local function write_device(v)
  if v == STRIP then return STRIP end
  local function text(g) return g == STRIP and '""' or g end
  return '{ "eq": ' .. text(v.eq) .. ', "gain": ' .. text(v.gain) .. ' }'
end

-- seed the table from persisted state (cold start: metadata is empty)
do
  local ok, p = pcall(function() return state:load() end)
  if ok and p ~= nil and p[PROTO_KEY] == PROTOCOL then
    pcall(function()
      for k, v in pairs(p) do
        if k == LISTENER then
          local l = read_listener(v)
          if l then listener, listener_text = l, v end
        elseif k ~= PROTO_KEY then
          local d = read_device(v)
          if d then own[k] = d end
        end
      end
    end)
  end
end

local function persist()
  local t = { [PROTO_KEY] = PROTOCOL }
  for k, v in pairs(own) do t[k] = write_device(v) end
  if listener_text ~= nil then t[LISTENER] = listener_text end
  pcall(function() state:save(t) end)
end

local function name_of(node)
  local name
  if pcall(function() name = tostring(node.properties["node.name"]) end) then
    return name
  end
  return nil
end

-- graphs `which` of `want` (by number) on a node, in one call
local function put(node, want, which)
  local params = {}
  for _, i in ipairs(which) do
    params[#params + 1] = GRAPH .. i
    params[#params + 1] = want[i]
  end
  local ok, err = pcall(function()
    node:set_param("Props", Pod.Object {
      "Spa:Pod:Object:Param:Props", "Props",
      params = Pod.Struct(params),
    })
  end)
  if not ok then
    log.warning("set_param failed: " .. tostring(err))
    return
  end
  local name = name_of(node)
  if name then
    local mem = set_on[name] or {}
    for _, i in ipairs(which) do mem[i] = want[i] end
    set_on[name] = mem
  end
end

-- THE KEY IS THE NODE AND THE HOLE IN USE, the same rule the app
-- keys on (perdeviceeq/pw_backend.py, device_key). A node name is
-- shared by holes a hand never chose together: a headset answers to
-- one name in Headphones and in Handsfree, and a card with several
-- outputs answers to one name on all of them. The port's own NAME
-- goes in the key, never its description -- descriptions are
-- translated, names are what a card calls its wiring. A node with no
-- card behind it keys as its bare name. A node whose card is not listed
-- yet has no key at all (nil): the bare name would be another device's,
-- and under that name it would play another device's value, or none.
-- The device's arrival applies it (dev_om below).
local function device_key(node)
  local name, devid, cdev
  if not pcall(function()
    name = tostring(node.properties["node.name"])
    devid = node.properties["device.id"]
    cdev  = node.properties["card.profile.device"]
  end) or not name then
    return nil
  end
  if devid == nil then return name end
  local dev = devices[tostring(devid)]
  if dev == nil then return nil end
  local port = nil
  pcall(function()
    for p in dev:iterate_params("Route") do
      local pr = (p:parse() or {}).properties or {}
      -- BOTH fields decide: one device carries an input route and an
      -- output route at once, and on a CM106 they share a card
      -- device index as well
      if pr.direction == "Output"
          and (cdev == nil or tostring(pr.device) == tostring(cdev)) then
        port = pr.name
      end
    end
  end)
  return port and (name .. "#" .. tostring(port)) or name
end

-- ONLY A FINAL NODE IS A DEVICE, the rule the app lists by
-- (perdeviceeq/pw_backend.py, is_final_node): the sound leaves
-- PipeWire there, into a card (device.id) or onto a network
-- (node.network: AirPlay, a pulse tunnel, roc). Every other sink passes
-- it on or keeps it: a loopback, a filter-chain and a combine-stream
-- feed a node of their own group onward, a null sink is a dead end
-- heard through its monitor if at all. A graph on one of those is
-- played on top of the one at the device it feeds, the listener's
-- layers twice, so the hook holds no such node and sets nothing on it,
-- whatever its key says. node.virtual decides nothing: the network
-- sinks set it too. The flag reads as PipeWire reads it (spa_atob):
-- "true" or "1".
local function is_final(node)
  local id, net
  pcall(function()
    id = node.properties["device.id"]
    net = node.properties["node.network"]
  end)
  return id ~= nil or net == "true" or net == "1"
end

-- what a device plays now, graph by graph
local function wanted(key)
  local v = own[key]
  if v == STRIP then return { [0] = STRIP, [1] = STRIP, [2] = STRIP } end
  local l = listener or { taste = STRIP, gain = {} }
  if v ~= nil then return { [0] = v.eq, [1] = l.taste, [2] = v.gain } end
  return { [0] = STRIP, [1] = l.taste, [2] = l.gain["*"] or STRIP }
end

local function apply(node)
  local k = device_key(node)
  if k == nil then return end
  -- nothing known => leave alone
  if own[k] == nil and listener == nil then return end
  put(node, wanted(k), ALL)
end

-- A DEVICE'S VALUE: what it says, on every node of the device in one
-- call each -- all three graphs, since it also takes the listener's
-- layer off or puts it back on.
local function on_device(key, value)
  local v = read_device(value)
  if v == false then
    log.warning("not a value of protocol " .. PROTOCOL .. ", left as it "
                .. "was: " .. key)
    return
  end
  own[key] = v
  persist()
  for _, n in pairs(nodes) do
    if device_key(n) == key then put(n, wanted(key), ALL) end
  end
end

-- THE LISTENER'S VALUE: the taste for every device, and the preamp
-- that arrived last for each. A node playing now hears it now, and only
-- what changed is set there; any other gets all of it when it reaches
-- running, where every graph is set again anyway.
local function on_listener(value)
  local l = read_listener(value)
  if l == nil then
    log.warning("not a value of protocol " .. PROTOCOL .. ", left as it "
                .. "was: " .. LISTENER)
    return
  end
  listener, listener_text = l, value
  for k, v in pairs(own) do
    if v ~= STRIP then v.gain = l.gain[k] or l.gain["*"] or STRIP end
  end
  persist()
  for name, n in pairs(nodes) do
    local k, st = device_key(n), nil
    pcall(function() st = n:get_state() end)
    if k ~= nil and st == "running" and own[k] ~= STRIP then
      local want, mem = wanted(k), set_on[name]
      if mem == nil then
        put(n, want, ALL)
      else
        local which = {}
        for _, i in ipairs(LISTENER_GRAPHS) do
          if mem[i] ~= want[i] then which[#which + 1] = i end
        end
        if #which > 0 then put(n, want, which) end
      end
    end
  end
end

-- ---- devices: where a node's holes are listed ----
-- A lookup table, nothing more. A profile change RESTARTS the node,
-- so the hook hears the switch on its own state-changed path and has
-- no reason to watch the device's params -- verified in the field:
-- the same node came back as headset-output, then headset-hf-output,
-- then headset-output again, once per switch.
dev_om = ObjectManager { Interest { type = "device" } }
dev_om:connect("object-added", function(_, dev)
  local id
  if pcall(function() id = tostring(dev["bound-id"]) end) and id then
    devices[id] = dev
    -- a node that came first had no key; it has one now
    for _, n in pairs(nodes) do
      local d, st
      pcall(function()
        d = n.properties["device.id"]
        st = n:get_state()
      end)
      if d ~= nil and tostring(d) == id and st == "running" then
        apply(n)
      end
    end
  end
end)
dev_om:connect("object-removed", function(_, dev)
  local id
  if pcall(function() id = tostring(dev["bound-id"]) end) and id then
    devices[id] = nil
  end
end)
dev_om:activate()

-- ---- metadata: the live channel from the GUI/CLI ----
md_om = ObjectManager {
  Interest { type = "metadata",
    Constraint { "metadata.name", "equals", META, type = "pw-global" } }
}
md_om:connect("object-added", function(_, m)
  if md then return end
  local feat = (type(Feature) == "table" and Feature.Metadata) and Feature.Metadata.DATA or nil
  m:activate(feat, function(_, err)
    if err then log.warning("metadata activate: " .. tostring(err)); return end
    md = m
    -- stamp the channel protocol; the app compares it at startup
    -- and offers a one-click reinstall on mismatch
    m:set(0, "protocol", "Spa:String:JSON", PROTOCOL)
    m:connect("changed", function(_, subject, key, typ, value)
      if key == nil or key == "protocol" then return end
      if key == LISTENER then on_listener(value) else on_device(key, value) end
    end)
  end)
end)
md_om:activate()

-- ---- sinks: (re)apply when a device reaches running (ports negotiated) ----
sink_om = ObjectManager {
  Interest { type = "node",
    Constraint { "media.class", "equals", "Audio/Sink", type = "pw-global" } }
}
sink_om:connect("object-added", function(_, node)
  local name
  if not pcall(function() name = tostring(node.properties["node.name"]) end) or not name then
    return
  end
  if not is_final(node) then return end   -- passes the sound on: not a device
  nodes[name] = node
  pcall(function()
    node:connect("state-changed", function(n, _old, new)
      if new == "running" then apply(n) end
    end)
  end)
  local st; pcall(function() st = node:get_state() end)
  if st == "running" then apply(node) end
end)
sink_om:connect("object-removed", function(_, node)
  local name
  if pcall(function() name = tostring(node.properties["node.name"]) end) and name then
    nodes[name] = nil
    set_on[name] = nil      -- a node that comes back has no graphs
  end
end)
sink_om:activate()

do
  local n = listener and 1 or 0
  for _ in pairs(own) do n = n + 1 end
  log.info("per-device-eq hook loaded; persisted entries: " .. n)
end
