-- HungerDesolate server bridge: read-only player snapshot via TheSim keys.
-- Runs on the dedicated server only. Never logs player names or userids.

local BRIDGE_VERSION = "1.0.0"
local STATE_KEY = "hungerhub_bridge_state"
local STATE_LIMIT = 262144
local MAX_PLAYERS = 128
local MAX_TEXT_CODEPOINTS = 64

local ENV = GLOBAL
local OS = os or (ENV and ENV.os)
local PCALL = pcall or (ENV and ENV.pcall)
local PAIRS = pairs or (ENV and ENV.pairs)
local SIM = TheSim or (ENV and ENV.TheSim)

local function log_fixed(message)
    print("[hungerhub_bridge] " .. message)
end

if not OS or type(OS.time) ~= "function" or type(OS.date) ~= "function" then
    log_fixed("os time/date unavailable; stopping")
    return
end
if not PCALL then
    log_fixed("pcall unavailable; stopping")
    return
end
if not SIM or type(SIM.SetPersistentString) ~= "function" then
    log_fixed("TheSim persistence unavailable; stopping")
    return
end

log_fixed("loaded version=" .. BRIDGE_VERSION .. " mode=readonly")

local function net()
    return (ENV and ENV.TheNet) or TheNet
end

local function shard()
    return (ENV and ENV.TheShard) or TheShard
end

local function players()
    if type(AllPlayers) == "table" then
        return AllPlayers
    end
    return ENV and ENV.AllPlayers
end

local function world_entity()
    return (ENV and ENV.TheWorld) or TheWorld
end

local function is_server()
    local connection = net()
    local ok, value = PCALL(function()
        return connection and connection:GetIsServer()
    end)
    return ok and value == true
end

local function shard_name()
    local current_shard = shard()
    local ok, value = PCALL(function()
        return current_shard and current_shard:GetShardId()
    end)
    if ok and (value == "Master" or value == "Caves") then
        return value
    end
    local connection = net()
    local ok_fallback, master = PCALL(function()
        return connection and connection:GetIsMasterSimulation()
    end)
    if ok_fallback then
        if master == true then
            return "Master"
        end
        if master == false then
            return "Caves"
        end
    end
    return nil
end

-- Keep only valid UTF-8, replace C0/DEL with spaces, and cap codepoints.
local function utf8_sanitize(value, max_codepoints)
    if type(value) ~= "string" then
        return ""
    end
    local out = {}
    local index = 1
    local length = #value
    local count = 0
    while index <= length and count < max_codepoints do
        local byte = string.byte(value, index)
        local size = 0
        if byte < 0x80 then
            size = 1
        elseif byte >= 0xC2 and byte <= 0xDF then
            size = 2
        elseif byte >= 0xE0 and byte <= 0xEF then
            size = 3
        elseif byte >= 0xF0 and byte <= 0xF4 then
            size = 4
        end
        local valid = size > 0 and (index + size - 1) <= length
        if valid and size > 1 then
            for offset = 1, size - 1 do
                local continuation = string.byte(value, index + offset)
                if continuation == nil or continuation < 0x80 or continuation > 0xBF then
                    valid = false
                    break
                end
            end
            if valid and size == 3 then
                local second = string.byte(value, index + 1)
                if (byte == 0xE0 and second < 0xA0) or (byte == 0xED and second > 0x9F) then
                    valid = false
                end
            elseif valid and size == 4 then
                local second = string.byte(value, index + 1)
                if (byte == 0xF0 and second < 0x90) or (byte == 0xF4 and second > 0x8F) then
                    valid = false
                end
            end
        end
        if valid then
            if size == 1 and (byte < 0x20 or byte == 0x7F) then
                out[#out + 1] = " "
            else
                out[#out + 1] = string.sub(value, index, index + size - 1)
            end
            index = index + size
        else
            out[#out + 1] = "?"
            index = index + 1
        end
        count = count + 1
    end
    return table.concat(out)
end

local function json_escape(value)
    local out = {}
    for index = 1, #value do
        local char = string.sub(value, index, index)
        local byte = string.byte(value, index)
        if char == '"' then
            out[#out + 1] = '\\"'
        elseif char == "\\" then
            out[#out + 1] = "\\\\"
        elseif byte < 0x20 or byte == 0x7F then
            out[#out + 1] = string.format("\\u%04x", byte)
        else
            out[#out + 1] = char
        end
    end
    return table.concat(out)
end

local function valid_id(value)
    return type(value) == "string"
        and #value >= 1
        and #value <= 64
        and string.match(value, "^[%w_%-]+$") ~= nil
end

local function write_state(payload)
    local ok = PCALL(function()
        SIM:SetPersistentString(STATE_KEY, payload, false)
    end)
    if not ok then
        log_fixed("state write failed")
    end
end

local sequence = 0
local started = false
local snapshot_logged = false
local key_error_logged = false
local row_error_logged = false

local function snapshot()
    if not is_server() then
        return
    end
    local current_shard = shard_name()
    if not current_shard then
        return
    end
    sequence = sequence + 1
    local rows = {}
    local total = 0
    local all = players()
    if not snapshot_logged then
        snapshot_logged = true
        local key_count = 0
        for _ in PAIRS(all or {}) do
            key_count = key_count + 1
        end
        log_fixed("snapshot keys=" .. tostring(key_count))
    end
    PCALL(function()
        for key, player in PAIRS(all or {}) do
            local userid = nil
            if type(key) == "string" and valid_id(key) then
                userid = key
            else
                local ok_id, value = PCALL(function()
                    if type(player) == "table" or type(player) == "userdata" then
                        return player.userid
                    end
                    return nil
                end)
                if ok_id and type(value) == "string" and valid_id(value) then
                    userid = value
                end
            end
            if not userid then
                if not key_error_logged then
                    key_error_logged = true
                    log_fixed("player key/value skipped key=" .. type(key)
                        .. " value=" .. type(player))
                end
            else
                local ok_row, row = PCALL(function()
                    local name = ""
                    local character = ""
                    if type(player) == "table" or type(player) == "userdata" then
                        name = utf8_sanitize(player.name or "", MAX_TEXT_CODEPOINTS)
                        if type(player.prefab) == "string" then
                            character = utf8_sanitize(player.prefab, MAX_TEXT_CODEPOINTS)
                        end
                    end
                    return '{"userId":"' .. json_escape(userid)
                        .. '","name":"' .. json_escape(name)
                        .. '","character":"' .. json_escape(character) .. '"}'
                end)
                if ok_row and row then
                    rows[#rows + 1] = row
                    total = total + 1
                    if total >= MAX_PLAYERS then
                        break
                    end
                elseif not row_error_logged then
                    row_error_logged = true
                    log_fixed("player row skipped")
                end
            end
        end
    end)
    local payload = '{"schemaVersion":1,"shard":"' .. current_shard
        .. '","sequence":' .. tostring(sequence)
        .. ',"capturedAt":"' .. OS.date("!%Y-%m-%dT%H:%M:%SZ")
        .. '","players":[' .. table.concat(rows, ",") .. "]}"
    if #payload > STATE_LIMIT then
        log_fixed("snapshot too large; skipped")
        return
    end
    write_state(payload)
end

local function start(owner)
    if started then
        return
    end
    if not is_server() then
        log_fixed("start skipped: not server")
        return
    end
    if owner == nil then
        log_fixed("start skipped: world not ready")
        return
    end
    started = true
    owner:DoPeriodicTask(5, function()
        PCALL(snapshot)
    end)
    owner:DoTaskInTime(1, function()
        PCALL(snapshot)
    end)
    log_fixed("started version=" .. BRIDGE_VERSION .. " shard=" .. tostring(shard_name() or "unknown"))
end

AddGamePostInit(function()
    local world = world_entity()
    if world then
        PCALL(start, world)
    end
end)

AddPrefabPostInit("world", function(instance)
    PCALL(start, instance)
end)
