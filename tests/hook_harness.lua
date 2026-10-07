-- Runs wireplumber/90-per-device-eq.lua against a fake WirePlumber and
-- checks what it puts on each node. Driven by tests/test_hook.py:
--   lua tests/hook_harness.lua wireplumber/90-per-device-eq.lua
-- Every scenario loads a fresh copy of the hook; a failed check raises
-- an error, which exits non-zero.

local HOOK = assert(arg[1], "usage: hook_harness.lua <hook.lua>")

-- ---- the fake -------------------------------------------------------------

local function world(saved)
  local w = { applied = {}, saved = nil }
  local env = setmetatable({}, { __index = _G })
  env.Log = { open_topic = function()
    return { warning = function() end, info = function() end }
  end }
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

  function w.node(name, devid, cdev, st)
    local n = { properties = { ["node.name"] = name, ["device.id"] = devid,
                               ["card.profile.device"] = cdev },
                st = st or "running", cbs = {} }
    function n:connect(sig, cb) self.cbs[sig] = cb end
    function n:get_state() return self.st end
    function n:set_param(_, pod) w.applied[name] = pod.params[2] end
    env.sink_om.cbs["object-added"](env.sink_om, n)
    return n
  end

  function w.gone(n)
    env.sink_om.cbs["object-removed"](env.sink_om, n)
  end

  function w.running(n)
    n.cbs["state-changed"](n, "idle", "running")
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

local HP = { direction = "Output", device = 1, name = "headset-output" }
local HF = { direction = "Output", device = 1, name = "headset-hf-output" }
local BT = "bluez_output.AA.1"
local KEY = BT .. "#headset-output"

-- ---- the scenarios --------------------------------------------------------

local scenarios = {}

scenarios["the protocol is stamped"] = function()
  local w = world(nil)
  w.metadata()
  eq(w.md.sets["protocol"], "3", "stamp")
end

scenarios["a device with no value of its own plays the common entry"] =
function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  local n = w.node(BT, 70, 1)
  eq(w.applied[BT], nil, "nothing known yet")
  w.write("@taste", "TASTE")
  eq(w.applied[BT], "TASTE", "common reaches it")
  w.write(KEY, "OWN")
  eq(w.applied[BT], "OWN", "its own value wins")
  w.write("@taste", "TASTE2")
  eq(w.applied[BT], "OWN", "common no longer reaches it")
  w.write(KEY, "@taste")
  eq(w.applied[BT], "TASTE2", "following common again")
  w.applied[BT] = nil
  w.running(n)
  eq(w.applied[BT], "TASTE2", "re-applied on running")
end

scenarios["explicitly nothing is not the common entry"] = function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  w.node(BT, 70, 1)
  w.write("@taste", "TASTE")
  w.write(KEY, "")
  eq(w.applied[BT], "", "stripped")
  w.write("@taste", "TASTE2")
  eq(w.applied[BT], "", "a common change does not reach it")
  eq(w.saved[KEY], "", "persisted as explicitly nothing")
end

scenarios["no common entry strips a device that followed it"] = function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  w.node(BT, 70, 1)
  w.write("@taste", "TASTE")
  w.write("@taste", "")
  eq(w.applied[BT], "", "common emptied")
  w.write("@taste", "TASTE")
  w.write("@taste", nil)
  eq(w.applied[BT], "", "common deleted")
end

scenarios["a virtual sink does not take the common entry"] = function()
  local w = world(nil)
  w.metadata()
  w.node("effect_input.eq", nil, nil)
  w.write("@taste", "TASTE")
  eq(w.applied["effect_input.eq"], nil, "left alone")
  w.write("effect_input.eq", "OWN")
  eq(w.applied["effect_input.eq"], "OWN", "its own value still applies")
  w.write("effect_input.eq", "@taste")
  eq(w.applied["effect_input.eq"], "", "following: nothing, it has no card")
end

scenarios["the key is the node and the hole"] = function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  local n = w.node(BT, 70, 1)
  w.write("@taste", "TASTE")
  w.write(KEY, "OWN")
  eq(w.applied[BT], "OWN", "headphones")
  -- a profile switch restarts the node under the other hole
  w.gone(n)
  w.device(70, { HF })
  w.applied[BT] = nil
  w.node(BT, 70, 1)
  eq(w.applied[BT], "TASTE", "handsfree has no value of its own")
end

scenarios["the persisted table carries its protocol"] = function()
  local w = world(nil)
  w.device(70, { HP })
  w.metadata()
  w.write("@taste", "TASTE")
  w.write(KEY, "OWN")
  w.write(KEY, "@taste")
  eq(w.saved["@protocol"], "3", "stamped")
  eq(w.saved["@taste"], "TASTE", "common kept")
  eq(w.saved[KEY], nil, "following leaves no entry")
end

scenarios["a table of this protocol seeds the hook"] = function()
  local w = world({ ["@protocol"] = "3", ["@taste"] = "TASTE",
                    [KEY] = "OWN" })
  w.device(70, { HP })
  w.node(BT, 70, 1)
  eq(w.applied[BT], "OWN", "own value from the table")
  w.node("alsa_output.card", 71, 0)
  eq(w.applied["alsa_output.card"], "TASTE", "common from the table")
end

scenarios["a table of another protocol is not read"] = function()
  local w = world({ [KEY] = "OLD", ["alsa_output.card"] = "OLD" })
  w.device(70, { HP })
  w.node(BT, 70, 1)
  w.node("alsa_output.card", 71, 0)
  eq(w.applied[BT], nil, "old own value ignored")
  eq(w.applied["alsa_output.card"], nil, "old bare key ignored")
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
