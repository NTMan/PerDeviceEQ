-- Runs wireplumber/90-per-device-eq.lua against a fake WirePlumber and
-- checks what it puts on each node. Driven by tests/test_hook.py:
--   lua tests/hook_harness.lua wireplumber/90-per-device-eq.lua [app.lua]
-- Every scenario loads a fresh copy of the hook; a failed check raises
-- an error, which exits non-zero. app.lua, when given, holds values the
-- app made (tests/test_hook.py writes it), for the scenario that reads
-- them back.

local HOOK = assert(arg[1], "usage: hook_harness.lua <hook.lua> [app.lua]")
local APP = arg[2]

-- ---- WirePlumber's JSON, as the hook asks it -------------------------------
-- Json.Raw(text):parse(n), after push_luajson in WirePlumber's
-- module-lua-scripting/api/json.c: an object or an array is taken apart
-- while n lasts, and below that a container comes back as its own text;
-- a string comes back unescaped and anything else -- a bare word, a
-- container out of depth -- as it is (spa_json_parse_string). The text
-- is read as SPA-JSON: ':', '=' and ',' separate like spaces, and a
-- word needs no quotes.

local SEP = { [" "] = true, ["\t"] = true, ["\n"] = true, ["\r"] = true,
              [","] = true, [":"] = true, ["="] = true }

-- where the element that starts at i ends: a container with all it
-- holds, a string with its quotes, or a word
local function stop(s, i)
  local c = s:sub(i, i)
  if c == "{" or c == "[" then
    local depth, j, quoted = 0, i, false
    while j <= #s do
      local d = s:sub(j, j)
      if quoted then
        if d == "\\" then j = j + 1 elseif d == '"' then quoted = false end
      elseif d == '"' then
        quoted = true
      elseif d == "{" or d == "[" then
        depth = depth + 1
      elseif d == "}" or d == "]" then
        depth = depth - 1
        if depth == 0 then return j end
      end
      j = j + 1
    end
    error("unbalanced: " .. s)
  elseif c == '"' then
    local j = i + 1
    while j <= #s do
      local d = s:sub(j, j)
      if d == "\\" then j = j + 2
      elseif d == '"' then return j
      else j = j + 1 end
    end
    error("unterminated: " .. s)
  end
  local j = i
  while j <= #s and not SEP[s:sub(j, j)] and not s:sub(j, j):match("[%]}]") do
    j = j + 1
  end
  return j - 1
end

-- the elements of a container's text, in order
local function elements(s)
  local out, i = {}, 2
  while true do
    while i <= #s and SEP[s:sub(i, i)] do i = i + 1 end
    if i > #s or s:sub(i, i):match("[%]}]") then return out end
    local j = stop(s, i)
    out[#out + 1] = s:sub(i, j)
    i = j + 1
  end
end

local ESC = { n = "\n", r = "\r", b = "\b", t = "\t", f = "\f" }

local function unquote(s)
  if s:sub(1, 1) ~= '"' then return s end
  local out, i = {}, 2
  while i < #s do
    local c = s:sub(i, i)
    if c == "\\" then
      local n = s:sub(i + 1, i + 1)
      out[#out + 1] = ESC[n] or n
      i = i + 2
    else
      out[#out + 1] = c
      i = i + 1
    end
  end
  return table.concat(out)
end

local function push(s, n)
  if s == "null" then return nil end
  if s == "true" then return true end
  if s == "false" then return false end
  if s:match("^%-?%d+%.?%d*$") then return tonumber(s) end
  local c = s:sub(1, 1)
  if c == "[" and n > 0 then
    local t = {}
    for i, e in ipairs(elements(s)) do t[i] = push(e, n - 1) end
    return t
  end
  if c == "{" and n > 0 then
    local t, e = {}, elements(s)
    for i = 1, #e - 1, 2 do t[unquote(e[i])] = push(e[i + 1], n - 1) end
    return t
  end
  return unquote(s)
end

local Json = { Raw = function(text)
  return { parse = function(_, n) return push(text, n or math.huge) end }
end }

-- ---- the fake -------------------------------------------------------------

local function world(saved)
  local w = { applied = {}, last = {}, calls = {}, saved = nil }
  local env = setmetatable({}, { __index = _G })
  env.Log = { open_topic = function()
    return { warning = function() end, info = function() end }
  end }
  env.Json = Json
  env.State = function()
    return {
      load = function() return saved end,
      save = function(_, t)
        local c = {}
        for k, v in pairs(t) do c[k] = v end
        w.saved = c
      end,
    }
  end
  env.Interest = function(t) return t end
  env.Constraint = function(t) return t end
  env.Pod = { Object = function(t) return t end,
              Struct = function(t) return t end }
  env.ObjectManager = function()
    local om = { cbs = {} }
    function om:connect(sig, cb) self.cbs[sig] = cb end
    function om:activate() end
    return om
  end
  assert(loadfile(HOOK, "t", env))()
  w.env = env

  function w.device(id, routes)
    local d = { ["bound-id"] = id }
    function d:iterate_params()
      local i = 0
      return function()
        i = i + 1
        local r = routes[i]
        if r == nil then return nil end
        return { parse = function() return { properties = r } end }
      end
    end
    env.dev_om.cbs["object-added"](env.dev_om, d)
    return d
  end

  -- props: any further node properties, as WirePlumber hands them
  -- (strings). A set_param is one call: the graphs it sets, by number,
  -- go to last[name] and over applied[name], and calls[name] counts it.
  function w.node(name, devid, cdev, st, props)
    local n = { properties = { ["node.name"] = name, ["device.id"] = devid,
                               ["card.profile.device"] = cdev },
                st = st or "running", cbs = {} }
    for k, v in pairs(props or {}) do n.properties[k] = v end
    function n:connect(sig, cb) self.cbs[sig] = cb end
    function n:get_state() return self.st end
    function n:set_param(_, pod)
      local call = {}
      for i = 1, #pod.params, 2 do
        local num = tonumber(pod.params[i]:match("filter%-graph%.(%d+)$"))
        call[num] = pod.params[i + 1]
      end
      local a = w.applied[name] or {}
      for k, v in pairs(call) do a[k] = v end
      w.applied[name], w.last[name] = a, call
      w.calls[name] = (w.calls[name] or 0) + 1
    end
    env.sink_om.cbs["object-added"](env.sink_om, n)
    return n
  end

  function w.gone(n)
    env.sink_om.cbs["object-removed"](env.sink_om, n)
  end

  -- as in WirePlumber, a signal nobody connected to does nothing
  function w.running(n)
    n.st = "running"
    local cb = n.cbs["state-changed"]
    if cb then cb(n, "idle", "running") end
  end

  function w.metadata()
    local m = { cbs = {}, sets = {} }
    function m:activate(_, cb) cb(self, nil) end
    function m:set(_, key, _, value) self.sets[key] = value end
    function m:connect(sig, cb) self.cbs[sig] = cb end
    env.md_om.cbs["object-added"](env.md_om, m)
    w.md = m
    return m
  end

  -- what the app writes: pw-metadata -n per-device-eq 0 <key> <value>
  function w.write(key, value)
    w.md.cbs["changed"](w.md, 0, key, "Spa:String:JSON", value)
  end

  return w
end

local function eq(got, want, what)
  if got ~= want then
    error(string.format("%s: got %s, want %s", what,
                        tostring(got), tostring(want)), 2)
  end
end

-- A stand-in graph: an object, as a graph is. The values are built the
-- way the app builds them (perdeviceeq/eq.py, device_value and
-- listener_value).
local function G(name) return "{ g = " .. name .. " }" end

local function DEV(eq_name, gain)
  local function v(x) return x and G(x) or '""' end
  return '{ "eq": ' .. v(eq_name) .. ', "gain": ' .. v(gain) .. ' }'
end

local function LIS(taste, gains)
  local parts = {}
  for k, g in pairs(gains or {}) do
    parts[#parts + 1] = string.format("%q", k) .. ": " .. G(g)
  end
  table.sort(parts)
  return '{ "taste": ' .. (taste and G(taste) or '""')
      .. ', "gain": { ' .. table.concat(parts, ", ") .. ' } }'
end

-- what a node plays, graph 0, 1 and 2: a stand-in's name, "-" for no
-- graph, "?" for one never set
local function show(a)
  if a == nil then return nil end
  local out = {}
  for i = 0, 2 do
    local v = a[i]
    out[#out + 1] = v == nil and "?" or v == "" and "-"
        or (v:match("^{ g = (.-) }$") or v)
  end
  return table.concat(out, " ")
end

local HP = { direction = "Output", device = 1, name = "headset-output" }
local HF = { direction = "Output", device = 1, name = "headset-hf-output" }
local BT = "bluez_output.AA.1"
local KEY = BT .. "#headset-output"
local CARD = "alsa_output.card"

-- ---- the scenarios --------------------------------------------------------

local scenarios = {}

scenarios["the protocol is stamped"] = function()
  local w = world(nil)
  w.metadata()
  eq(w.md.sets["protocol"], "5", "stamp")
end

scenarios["a device's value sets every graph of its node in one call"] =
function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  local n = w.node(BT, 70, 1)
  eq(w.applied[BT], nil, "nothing known yet")
  w.write("@listener", LIS("T", { ["*"] = "S" }))
  eq(show(w.applied[BT]), "- T S", "the listener reaches it")
  local before = w.calls[BT]
  w.write(KEY, DEV("OWN", "P"))
  eq(w.calls[BT] - before, 1, "one call")
  eq(show(w.last[BT]), "OWN T P", "its correction and preamp, the taste on")
  w.applied[BT] = nil
  w.running(n)
  eq(show(w.applied[BT]), "OWN T P", "re-applied on running")
end

scenarios["a device with no correction follows the listener"] = function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  w.node(BT, 70, 1)
  w.write("@listener", LIS("T", { ["*"] = "S", [KEY] = "K" }))
  w.write(KEY, DEV("OWN", "P"))
  w.write(KEY, "@listener")
  eq(show(w.applied[BT]), "- T S", "graph 0 off, and the * preamp")
  w.write("@listener", LIS("T2", { ["*"] = "S2", [KEY] = "K" }))
  eq(show(w.applied[BT]), "- T2 S2", "the next record")
  eq(w.saved[KEY], nil, "following leaves no entry")
end

scenarios["explicitly nothing takes every graph off"] = function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  w.node(BT, 70, 1)
  w.write("@listener", LIS("T", { ["*"] = "S" }))
  w.write(KEY, DEV("OWN", "P"))
  w.write(KEY, "")
  eq(show(w.applied[BT]), "- - -", "stripped")
  eq(w.saved[KEY], "", "persisted as explicitly nothing")
  local before = w.calls[BT]
  w.write("@listener", LIS("T2", { ["*"] = "S2", [KEY] = "P2" }))
  eq(w.calls[BT], before, "the listener does not reach it")
  w.write(KEY, DEV("OWN", "P3"))
  eq(show(w.applied[BT]), "OWN T2 P3", "back, under the listener's taste")
end

scenarios["a device plays the preamp that arrived last"] = function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  w.node(BT, 70, 1)
  w.write("@listener", LIS("T", { ["*"] = "S", [KEY] = "P1" }))
  w.write(KEY, DEV("OWN", "P2"))
  eq(show(w.applied[BT]), "OWN T P2", "from its own value")
  w.write("@listener", LIS("T2", { ["*"] = "S", [KEY] = "P3" }))
  eq(show(w.applied[BT]), "OWN T2 P3", "from the record")
  eq(w.saved[KEY], DEV("OWN", "P3"), "the table keeps the one it plays")
  w.write("@listener", LIS("T2", { ["*"] = "M" }))
  eq(show(w.applied[BT]), "OWN T2 M", "Manual: the ride, for all")
end

scenarios["the listener sets only what changed, on a node that plays"] =
function()
  local w = world(nil)
  w.device(70, { HP })
  w.device(71, {})
  w.metadata()
  w.node(BT, 70, 1)
  local idle = w.node(CARD, 71, 0, "idle")
  w.write("@listener", LIS("T", { ["*"] = "S", [KEY] = "P" }))
  w.write(KEY, DEV("OWN", "P"))
  eq(w.applied[CARD], nil, "an idle node waits")
  local before = w.calls[BT]
  w.write("@listener", LIS("T2", { ["*"] = "S", [KEY] = "P" }))
  eq(w.calls[BT] - before, 1, "one call")
  eq(show(w.last[BT]), "? T2 ?", "the taste alone")
  w.write("@listener", LIS("T2", { ["*"] = "S", [KEY] = "P" }))
  eq(w.calls[BT] - before, 1, "nothing changed, nothing set")
  w.write("@listener", LIS("T2", { ["*"] = "S", [KEY] = "P4" }))
  eq(show(w.last[BT]), "? ? P4", "the preamp alone")
  w.running(idle)
  eq(show(w.applied[CARD]), "- T2 S", "all of it on running")
end

scenarios["a node the hook never set gets every graph from the listener"] =
function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  w.node(BT, 70, 1)
  eq(w.applied[BT], nil, "nothing known: left alone")
  w.write("@listener", LIS("T", { ["*"] = "S" }))
  eq(show(w.last[BT]), "- T S", "all three at once")
end

scenarios["a value that does not read as the contract says changes nothing"] =
function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  w.node(BT, 70, 1)
  w.write("@listener", LIS("T", { ["*"] = "S" }))
  w.write(KEY, DEV("OWN", "P"))
  local before = w.calls[BT]
  w.write(KEY, G("OLD"))                    -- a graph of protocol 4
  w.write(KEY, "OWN")
  w.write(KEY, '{ "eq": "OWN" }')           -- a string, not a graph
  w.write("@listener", G("OLD"))
  w.write("@listener", '{ "taste": "T" }')
  w.write("@listener", '{ "taste": "", "gain": { "*": "S" } }')
  eq(w.calls[BT], before, "nothing set")
  eq(show(w.applied[BT]), "OWN T P", "as it was")
  eq(w.saved[KEY], DEV("OWN", "P"), "nor kept")
  eq(w.saved["@listener"], LIS("T", { ["*"] = "S" }), "nor the record")
end

scenarios["a node that passes the sound on is not a device"] = function()
  -- a filter-chain's sink, and a null sink: no card, no network
  local w = world({ ["@protocol"] = "5",
                    ["@listener"] = LIS("T", { ["*"] = "S" }),
                    ["effect_input.eq"] = DEV("OLD", "P") })
  w.metadata()
  local fc = w.node("effect_input.eq", nil, nil, "running",
                    { ["node.link-group"] = "filter-chain-1-2",
                      ["node.virtual"] = "true" })
  local null = w.node("null-sink", nil, nil, "running",
                      { ["factory.name"] = "support.null-audio-sink" })
  eq(w.applied["effect_input.eq"], nil, "a value persisted for it")
  w.write("@listener", LIS("T2", { ["*"] = "S2" }))
  w.write("effect_input.eq", DEV("OWN", "P"))
  w.write("null-sink", DEV("OWN", "P"))
  w.write("effect_input.eq", "@listener")
  w.write("null-sink", "")
  w.running(fc)
  w.running(null)
  eq(w.applied["effect_input.eq"], nil, "the chain: nothing, ever")
  eq(w.applied["null-sink"], nil, "the null sink: nothing, ever")
end

scenarios["a network sink is a device"] = function()
  -- AirPlay, a pulse tunnel, roc: no card, and the sound leaves there
  local w = world(nil)
  w.metadata()
  local n = w.node("raop_sink.room", nil, nil, "running",
                   { ["node.network"] = "true", ["node.virtual"] = "true" })
  w.write("@listener", LIS("T", { ["*"] = "S" }))
  eq(show(w.applied["raop_sink.room"]), "- T S", "the listener reaches it")
  w.write("raop_sink.room", DEV("OWN", "P"))
  eq(show(w.applied["raop_sink.room"]), "OWN T P", "keyed by its bare name")
  w.applied["raop_sink.room"] = nil
  w.running(n)
  eq(show(w.applied["raop_sink.room"]), "OWN T P", "re-applied on running")
  -- the flag as PipeWire reads it: "1" is true as well
  w.node("roc_sink.desk", nil, nil, "running", { ["node.network"] = "1" })
  eq(show(w.applied["roc_sink.desk"]), "- T S", "1 is a network too")
end

scenarios["the key is the node and the hole"] = function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  local n = w.node(BT, 70, 1)
  w.write("@listener", LIS("T", { ["*"] = "S" }))
  w.write(KEY, DEV("OWN", "P"))
  eq(show(w.applied[BT]), "OWN T P", "headphones")
  -- a profile switch restarts the node under the other hole
  w.gone(n)
  w.device(70, { HF })
  w.applied[BT] = nil
  w.node(BT, 70, 1)
  eq(show(w.applied[BT]), "- T S", "handsfree has no value of its own")
end

scenarios["the persisted table carries its protocol"] = function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  w.write("@listener", LIS("T", { ["*"] = "S" }))
  w.write(KEY, DEV("OWN", "P"))
  w.write(KEY, "@listener")
  eq(w.saved["@protocol"], "5", "stamped")
  eq(w.saved["@listener"], LIS("T", { ["*"] = "S" }), "the record as written")
  eq(w.saved[KEY], nil, "following leaves no entry")
end

scenarios["a table of this protocol seeds the hook"] = function()
  local w = world({ ["@protocol"] = "5",
                    ["@listener"] = LIS("T", { ["*"] = "S", [KEY] = "K" }),
                    [KEY] = DEV("OWN", "P") })
  w.device(70, { HP })
  w.node(BT, 70, 1)
  eq(show(w.applied[BT]), "OWN T P", "its own preamp, not the record's")
  w.device(71, {})                    -- a card with no ports: bare key
  w.node(CARD, 71, 0)
  eq(show(w.applied[CARD]), "- T S", "the listener from the table")
end

scenarios["what the table keeps for a device is a value it reads back"] =
function()
  -- a measurement strips a device and writes back what the app read,
  -- from the metadata or, after a restart, from this table
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  w.node(BT, 70, 1)
  w.write(KEY, DEV("OWN", "P"))
  w.write("@listener", LIS("T", { ["*"] = "S", [KEY] = "P2" }))
  local kept = w.saved[KEY]
  w.write(KEY, "")
  eq(show(w.applied[BT]), "- - -", "stripped for the sweep")
  w.write(KEY, kept)
  eq(show(w.applied[BT]), "OWN T P2", "back as it played")
end

scenarios["a table of another protocol is not read"] = function()
  local w = world({ [KEY] = DEV("OLD", "P"), [CARD] = DEV("OLD", "P") })
  w.device(70, { HP })
  w.device(71, {})
  w.node(BT, 70, 1)
  w.node(CARD, 71, 0)
  eq(w.applied[BT], nil, "old own value ignored")
  eq(w.applied[CARD], nil, "old bare key ignored")
end

scenarios["a table of the previous protocol is not read"] = function()
  -- the upgrade: the hook is new, its state file is the old hook's
  local w = world({ ["@protocol"] = "4", ["@taste"] = G("TASTE"),
                    [KEY] = G("OLD") })
  w.device(70, { HP })
  w.node(BT, 70, 1)
  eq(w.applied[BT], nil, "nothing from a protocol 4 table")
end

scenarios["a node seen before its card waits for it"] = function()
  local w = world(nil)
  w.metadata()
  w.write("@listener", LIS("T", { ["*"] = "S" }))
  w.write(KEY, DEV("OWN", "P"))
  w.node(BT, 70, 1)                   -- running, its card not listed yet
  eq(w.applied[BT], nil, "no key yet: left alone, not given the listener's")
  w.device(70, { HP })
  eq(show(w.applied[BT]), "OWN T P", "its own value once the card is listed")
end

if APP then
  -- values the app made, with real graphs in them (tests/test_hook.py)
  scenarios["what the app writes is read as it means"] = function()
    local app = dofile(APP)
    local w = world(nil)
    w.device(70, { HP })
    w.metadata()
    w.node(BT, 70, 1)
    w.write("@listener", app.listener)
    w.write(KEY, app.device)
    eq(w.applied[BT][0], app.correction, "graph 0")
    eq(w.applied[BT][1], app.taste, "graph 1")
    eq(w.applied[BT][2], app.own_gain, "graph 2, its own")
    w.write("@listener", app.listener_auto)
    eq(w.applied[BT][2], app.auto_gain, "graph 2, from the record")
    eq(w.saved[KEY], app.device_kept, "the table keeps what it plays")
    w.write(KEY, "@listener")
    eq(w.applied[BT][0], "", "no correction")
    eq(w.applied[BT][2], app.star_gain, "graph 2, the * entry")
  end
end

local names = {}
for name in pairs(scenarios) do names[#names + 1] = name end
table.sort(names)
for _, name in ipairs(names) do
  local ok, err = pcall(scenarios[name])
  if not ok then
    io.stderr:write("FAIL ", name, ": ", tostring(err), "\n")
    os.exit(1)
  end
  print("ok   " .. name)
end
