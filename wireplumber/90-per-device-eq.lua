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
--   * graphs are kept in an in-memory table `graphs` (device key -> graph
--     string), which is the runtime source of truth;
--   * on startup the table is seeded from WpState (~/.local/state/wireplumber/),
--     because the metadata object is empty after a PipeWire restart; only a
--     table written under this PROTOCOL is read -- an older one means
--     something else by its values, and the app's reinstall feeds a new one;
--   * the GUI/CLI push live edits into the "per-device-eq" metadata object; we
--     subscribe to its "changed" signal, update the table, apply to the live
--     node, and persist the table back to WpState;
--   * each sink gets its graph (re)applied when it reaches the "running" state,
--     which also covers hotplug / Bluetooth reconnect.
-- No background process of the user's is involved: the EQ lives in WirePlumber.

local log   = Log.open_topic("pde")
local META  = "per-device-eq"   -- metadata object name (live edits from the app)
local PROTOCOL = "3"            -- channel protocol; stamped into the metadata on
                                -- activation, compared by the app (bump together
                                -- with PROTOCOL in perdeviceeq/config.py on any
                                -- breaking change to the graph string or the
                                -- metadata contract)
local STATE = "per-device-eq"   -- WpState name -> ~/.local/state/wireplumber/per-device-eq

-- THE CONTRACT (COMMON_KEY and STRIP in perdeviceeq/config.py, keep
-- them equal). A key names a device (device_key below) or COMMON. A
-- device's value is its own graph, or STRIP for explicitly nothing --
-- Bypass, or a measurement in progress. A device with no value of its
-- own plays COMMON: the listener's taste and preamp, in a graph that
-- fits a node of any width. To say "no value of its own" the app writes
-- COMMON's name as the value rather than deleting the key: the metadata
-- announces a delete only for a key it holds, and after a restart it
-- holds none of what is persisted here, so a delete would never arrive.
local COMMON = "@taste"

-- No EQ is the ABSENCE of a graph, not a flat one. audioconvert's
-- load_filter_graph() removes the graph when it is handed an empty
-- string. A flat param_eq is still a graph, and one that names no ports
-- declares param_eq's eight outputs (see PARAM_EQ_PORTS in
-- perdeviceeq/eq.py): on a mono node that turned silence into full
-- scale DC under a device set to Clean.
local STRIP = ""

local PROTO_KEY = "@protocol"   -- in the persisted table: what wrote it

local graphs = {}            -- key -> graph or STRIP; no key: plays COMMON
local nodes  = {}            -- node.name -> live Audio/Sink node proxy
local md     = nil           -- activated metadata proxy
local devices = {}           -- bound id -> device proxy (for the holes)
local state  = State(STATE)  -- WpState handle (GKeyFile under ~/.local/state)

-- seed the table from persisted state (cold start: metadata is empty)
do
  local ok, p = pcall(function() return state:load() end)
  if ok and p ~= nil and p[PROTO_KEY] == PROTOCOL then
    pcall(function()
      for k, v in pairs(p) do
        if k ~= PROTO_KEY then graphs[k] = v end
      end
    end)
  end
end

local function persist()
  local t = { [PROTO_KEY] = PROTOCOL }
  for k, v in pairs(graphs) do t[k] = v end
  pcall(function() state:save(t) end)
end

local function set_graph(node, graph)
  local ok, err = pcall(function()
    node:set_param("Props", Pod.Object {
      "Spa:Pod:Object:Param:Props", "Props",
      params = Pod.Struct { "audioconvert.filter-graph.0", graph },
    })
  end)
  if not ok then log.warning("set_param failed: " .. tostring(err)) end
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
-- and under that name it would take COMMON instead of its own value.
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

-- A node with a card behind it is where the sound comes out. A virtual
-- sink -- a null sink, a loopback, an effect chain -- passes audio on to
-- one, and the listener's layers belong on the way out, once.
local function has_card(node)
  local id
  pcall(function() id = node.properties["device.id"] end)
  return id ~= nil
end

-- what a node plays: its own value, else COMMON if it has a card, else
-- nothing is known about it (nil)
local function wanted(node, key)
  local g = graphs[key]
  if g == nil and has_card(node) then g = graphs[COMMON] end
  return g
end

local function apply(node)
  local k = device_key(node)
  if k == nil then return end
  local g = wanted(node, k)
  if g ~= nil then set_graph(node, g) end   -- nothing known => leave alone
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
      if key ~= COMMON and value == COMMON then
        value = nil                        -- no value of its own
      end
      graphs[key] = value
      persist()
      -- a device key reaches whichever live node answers to it now;
      -- COMMON reaches every node with a card and no value of its own
      for _, n in pairs(nodes) do
        local k = device_key(n)
        if k ~= nil and (k == key or (key == COMMON and graphs[k] == nil
                                      and has_card(n))) then
          set_graph(n, wanted(n, k) or STRIP)
        end
      end
    end)
  end)
end)
md_om:activate()

-- ---- sinks: (re)apply when a sink reaches running (ports negotiated) ----
sink_om = ObjectManager {
  Interest { type = "node",
    Constraint { "media.class", "equals", "Audio/Sink", type = "pw-global" } }
}
sink_om:connect("object-added", function(_, node)
  local name
  if not pcall(function() name = tostring(node.properties["node.name"]) end) or not name then
    return
  end
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
  end
end)
sink_om:activate()

do
  local n = 0; for _ in pairs(graphs) do n = n + 1 end
  log.info("per-device-eq hook loaded; persisted entries: " .. n)
end
