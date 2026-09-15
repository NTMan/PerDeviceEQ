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
--   * graphs are kept in an in-memory table `graphs` (node.name -> graph string),
--     which is the runtime source of truth;
--   * on startup the table is seeded from WpState (~/.local/state/wireplumber/),
--     because the metadata object is empty after a PipeWire restart;
--   * the GUI/CLI push live edits into the "per-device-eq" metadata object; we
--     subscribe to its "changed" signal, update the table, apply to the live
--     node, and persist the table back to WpState;
--   * each sink gets its graph (re)applied when it reaches the "running" state,
--     which also covers hotplug / Bluetooth reconnect.
-- No background process of the user's is involved: the EQ lives in WirePlumber.

local log   = Log.open_topic("pde")
local META  = "per-device-eq"   -- metadata object name (live edits from the app)
local PROTOCOL = "2"            -- channel protocol; stamped into the metadata on
                                -- activation, compared by the app (bump together
                                -- with PROTOCOL in perdeviceeq/config.py on any
                                -- breaking change to the graph string or the
                                -- metadata contract)
local STATE = "per-device-eq"   -- WpState name -> ~/.local/state/wireplumber/per-device-eq

-- identity / flat graph: a single 0 dB filter. Applied to strip EQ when a device
-- is set to Clean. Must stay in sync with build_graph(0.0, []) in per-device-eq.py.
local FLAT = "{ nodes = [ { type = builtin name = eq label = param_eq config = "
          .. "{ filters = [ { type = bq_peaking, freq = 1000, gain = 0.0, q = 1.0 } ] } } ] }"

local graphs = {}            -- device key -> graph string (runtime source of truth)
local nodes  = {}            -- node.name -> live Audio/Sink node proxy
local md     = nil           -- activated metadata proxy
local devices = {}           -- bound id -> device proxy (for the holes)
local state  = State(STATE)  -- WpState handle (GKeyFile under ~/.local/state)

-- seed the table from persisted state (cold start: metadata is empty)
do
  local ok, p = pcall(function() return state:load() end)
  if ok and p ~= nil then
    pcall(function()
      for k, v in pairs(p) do graphs[k] = v end
    end)
  end
end

local function persist()
  pcall(function() state:save(graphs) end)
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
-- card behind it keys as its bare name.
local function device_key(node)
  local name, devid, cdev
  if not pcall(function()
    name = tostring(node.properties["node.name"])
    devid = node.properties["device.id"]
    cdev  = node.properties["card.profile.device"]
  end) or not name then
    return nil
  end
  local dev = devid ~= nil and devices[tostring(devid)] or nil
  if dev == nil then return name end
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

local function apply(node)
  local k = device_key(node)
  local g = k and graphs[k] or nil
  if g then set_graph(node, g) end   -- no entry => Clean / unbound => leave alone
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
      if key == "protocol" then return end
      -- the key names a DEVICE; the node to write to is whichever
      -- live node currently answers to it
      local target = nil
      for _, n in pairs(nodes) do
        if device_key(n) == key then target = n break end
      end
      if value ~= nil and value ~= "" then
        graphs[key] = value
        if target then set_graph(target, value) end
      else
        graphs[key] = nil                  -- key cleared (Clean) -> strip EQ
        if target then set_graph(target, FLAT) end
      end
      persist()
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
  log.info("per-device-eq hook loaded; " .. n .. " persisted graph(s)")
end
