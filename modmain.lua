local json = require("json")
local G = GLOBAL
local protected_call = G.pcall

local LOG_PREFIX = "[JEV_DST_STATE]"
local SAMPLE_INTERVAL = GetModConfigData("sample_interval") or 1.0
local SCAN_RADIUS = GetModConfigData("scan_radius") or 12
local MAX_NEARBY = 40
local PLAYER_WAIT_INTERVAL = 0.5
local NAV_CELL_SIZE = 4
local NAV_OBSERVE_RADIUS = SCAN_RADIUS
local NAV_LEG_DISTANCE = 4
local NAV_PROBE_STEP = 0.75
local NAV_ARRIVAL_DISTANCE = 1.5
local NAV_BLACKLIST_SECONDS = 30

local EXCLUDE_TAGS = { "INLIMBO", "NOCLICK", "FX", "DECOR" }
local RELEVANT_TAGS =
{
    "pickable",
    "CHOP_workable",
    "MINE_workable",
    "DIG_workable",
    "_inventoryitem",
    "hostile",
    "monster",
    "_combat",
    "fire",
    "campfire",
}

-- Conservative whole-target workloads from the shipped DST scripts. Trees
-- use the largest normal growth stage because growth stage/work-left is not
-- replicated to clients. Known rock prefabs can use their exact maximum.
local DEFAULT_CHOP_WORK = 15
local MINE_WORK_BY_PREFAB =
{
    rock1 = 6,
    rock2 = 6,
    rock_flintless = 6,
    rock_flintless_med = 4,
    rock_flintless_low = 2,
    rock_moon = 6,
    rock_moon_shell = 6,
    moonglass_rock = 6,
    rock_petrified_tree = 3,
    rock_petrified_tree_med = 3,
    rock_petrified_tree_tall = 4,
    rock_petrified_tree_short = 2,
    rock_petrified_tree_old = 1,
}

local TOOL_MAX_USES = { axe = 100, pickaxe = 33 }

local WEAPON_PREFAB_SCORE =
{
    axe = 27,
    pickaxe = 27,
    spear = 34,
    spear_wathgrithr = 42,
    batbat = 42,
    tentaclespike = 51,
    hambat = 59,
    ruins_bat = 59,
    nightsword = 68,
    glasscutter = 68,
}

local RELEVANT_PREFABS =
{
    grass = true,
    sapling = true,
    sapling_moon = true,
    twigs = true,
    cutgrass = true,
    flint = true,
    rocks = true,
    log = true,
    carrot_planted = true,
    carrot = true,
    berries = true,
    berrybush = true,
    berrybush2 = true,
    juicyberrybush = true,
    evergreen = true,
    evergreen_sparse = true,
    deciduoustree = true,
    campfire = true,
    firepit = true,
    torch = true,
    spiderden = true,
    hound = true,
    tentacle = true,
}

local PICKABLE_SOURCE_PREFABS =
{
    grass = true,
    sapling = true,
    sapling_moon = true,
    berrybush = true,
    berrybush2 = true,
    juicyberrybush = true,
}

local function round(value, digits)
    if value == nil then
        return nil
    end
    local scale = 10 ^ (digits or 0)
    return math.floor(value * scale + 0.5) / scale
end

local function safe_call(fn, fallback)
    local ok, result = protected_call(fn)
    if ok then
        return result
    end
    return fallback
end

local function read_vitals(player)
    local vitals = {}
    local replica = player.replica

    if replica ~= nil and replica.health ~= nil then
        vitals.health = round(safe_call(function() return replica.health:GetCurrent() end, 0), 1)
        vitals.health_max = round(safe_call(function() return replica.health:Max() end, 0), 1)
        vitals.dead = safe_call(function() return replica.health:IsDead() end, false)
    end
    if replica ~= nil and replica.hunger ~= nil then
        vitals.hunger = round(safe_call(function() return replica.hunger:GetCurrent() end, 0), 1)
        vitals.hunger_max = round(safe_call(function() return replica.hunger:Max() end, 0), 1)
    end
    if replica ~= nil and replica.sanity ~= nil then
        vitals.sanity = round(safe_call(function() return replica.sanity:GetCurrent() end, 0), 1)
        vitals.sanity_max = round(safe_call(function() return replica.sanity:Max() end, 0), 1)
    end

    return vitals
end

local function item_record(item, slot)
    if item == nil or not item:IsValid() then
        return nil
    end

    local count = 1
    if item.replica ~= nil and item.replica.stackable ~= nil then
        count = safe_call(function() return item.replica.stackable:StackSize() end, 1)
    end

    local durability_percent = nil
    if item.components ~= nil and item.components.finiteuses ~= nil then
        durability_percent = safe_call(function() return item.components.finiteuses:GetPercent() end, nil)
    elseif item.replica ~= nil and item.replica.inventoryitem ~= nil then
        local classified = item.replica.inventoryitem.classified
        local encoded = classified ~= nil and classified.percentused ~= nil
            and safe_call(function() return classified.percentused:value() end, 255) or 255
        if encoded ~= 255 then
            durability_percent = encoded / 100
        end
    end

    local weapon_damage = nil
    local weapon = item.prefab ~= "torch"
        and (item:HasTag("weapon") or WEAPON_PREFAB_SCORE[item.prefab or ""] ~= nil)
    if weapon and item.components ~= nil and item.components.weapon ~= nil then
        weapon_damage = safe_call(function() return item.components.weapon:GetDamage(G.ThePlayer, nil) end, nil)
    end
    if weapon_damage == nil then
        weapon_damage = WEAPON_PREFAB_SCORE[item.prefab or ""]
    end

    return
    {
        slot = slot,
        guid = item.GUID,
        prefab = item.prefab or "unknown",
        count = count,
        durability_percent = durability_percent ~= nil and round(durability_percent, 3) or nil,
        max_uses = TOOL_MAX_USES[item.prefab or ""],
        weapon = weapon,
        weapon_damage = weapon_damage ~= nil and round(weapon_damage, 1) or nil,
    }
end

local function read_inventory(player)
    local result = { items = {}, equipped = {} }
    local inventory = player.replica ~= nil and player.replica.inventory or nil
    if inventory == nil then
        return result
    end

    local items = safe_call(function() return inventory:GetItems() end, {}) or {}
    for slot, item in pairs(items) do
        local record = item_record(item, slot)
        if record ~= nil then
            table.insert(result.items, record)
        end
    end
    table.sort(result.items, function(a, b) return a.slot < b.slot end)

    local equips = safe_call(function() return inventory:GetEquips() end, {}) or {}
    for equip_slot, item in pairs(equips) do
        local record = item_record(item, tostring(equip_slot))
        if record ~= nil then
            table.insert(result.equipped, record)
        end
    end
    table.sort(result.equipped, function(a, b) return tostring(a.slot) < tostring(b.slot) end)

    return result
end

local function entity_tags(entity)
    local tags = {}
    for _, tag in ipairs(RELEVANT_TAGS) do
        if entity:HasTag(tag) then
            table.insert(tags, tag)
        end
    end
    return tags
end

local function can_be_picked(entity)
    local pickable = entity.components ~= nil and entity.components.pickable or nil
    if pickable ~= nil then
        return entity:HasTag("pickable")
            and safe_call(function() return pickable:CanBePicked() end, false)
    end
    -- Remote clients do not have the server component, but its current
    -- availability is replicated through the pickable tag.
    return entity:HasTag("pickable")
end

local function is_relevant(entity)
    if RELEVANT_PREFABS[entity.prefab or ""] then
        return true
    end
    for _, tag in ipairs(RELEVANT_TAGS) do
        if entity:HasTag(tag) then
            return true
        end
    end
    return false
end

local function has_nearby_lit_fire(player, radius)
    if player == nil or not player:IsValid() then
        return false
    end
    local x, y, z = player.Transform:GetWorldPosition()
    local entities = G.TheSim:FindEntities(x, y, z, radius or 8, nil, EXCLUDE_TAGS)
    for _, entity in ipairs(entities) do
        if entity:IsValid()
            and (entity.prefab == "campfire" or entity.prefab == "firepit")
            and entity:HasTag("fire") then
            return true
        end
    end
    return false
end

local function read_nearby(player)
    local nearby = {}
    local px, py, pz = player.Transform:GetWorldPosition()
    local entities = G.TheSim:FindEntities(px, py, pz, SCAN_RADIUS, nil, EXCLUDE_TAGS)

    for _, entity in ipairs(entities) do
        if entity ~= player and entity:IsValid() and is_relevant(entity) then
            local x, y, z = entity.Transform:GetWorldPosition()
            local dx = x - px
            local dz = z - pz
            local record =
            {
                guid = entity.GUID,
                prefab = entity.prefab or "unknown",
                distance = round(math.sqrt(dx * dx + dz * dz), 2),
                dx = round(dx, 2),
                dz = round(dz, 2),
                tags = entity_tags(entity),
            }
            if PICKABLE_SOURCE_PREFABS[entity.prefab or ""] then
                record.pickable = can_be_picked(entity)
            end
            if entity:HasTag("CHOP_workable") then
                record.work_required = DEFAULT_CHOP_WORK
            elseif entity:HasTag("MINE_workable") then
                record.work_required = MINE_WORK_BY_PREFAB[entity.prefab or ""]
            end
            local combat = player.replica ~= nil and player.replica.combat or nil
            record.attackable = entity:HasAnyTag("hostile", "monster")
                and combat ~= nil
                and safe_call(function() return combat:CanTarget(entity) end, false)
            table.insert(nearby, record)
        end
    end

    table.sort(nearby, function(a, b) return a.distance < b.distance end)
    while #nearby > MAX_NEARBY do
        table.remove(nearby)
    end
    return nearby
end

local function read_crafting(player)
    local result = { torch = false, campfire = false, axe = false, pickaxe = false }
    local builder = player.replica ~= nil and player.replica.builder or nil
    if builder ~= nil then
        result.torch = safe_call(function() return builder:CanBuild("torch") end, false)
        result.campfire = safe_call(function() return builder:CanBuild("campfire") end, false)
        result.axe = safe_call(function() return builder:CanBuild("axe") end, false)
        result.pickaxe = safe_call(function() return builder:CanBuild("pickaxe") end, false)
    end
    return result
end

local function read_camera()
    local camera = G.TheCamera
    if camera == nil then
        return {}
    end

    local right = safe_call(function() return camera:GetRightVec() end, nil)
    local down = safe_call(function() return camera:GetDownVec() end, nil)
    return
    {
        heading = round(safe_call(function() return camera:GetHeading() end, 0), 2),
        right = right ~= nil and { x = round(right.x, 5), z = round(right.z, 5) } or nil,
        forward = down ~= nil and { x = round(-down.x, 5), z = round(-down.z, 5) } or nil,
    }
end

local NAV_NEIGHBORS =
{
    { -1, -1 }, { 0, -1 }, { 1, -1 },
    { -1,  0 },             { 1,  0 },
    { -1,  1 }, { 0,  1 }, { 1,  1 },
}

local function nav_key(gx, gz)
    return tostring(gx) .. ":" .. tostring(gz)
end

local function nav_grid_coordinate(value)
    return math.floor(value / NAV_CELL_SIZE + 0.5)
end

local function get_navigation_memory(world)
    if world._jev_dst_navigation == nil then
        world._jev_dst_navigation =
        {
            cells = {},
            target = nil,
            blacklist = {},
            last_player_cell = nil,
        }
    end
    return world._jev_dst_navigation
end

local function observe_navigation_cells(player, world, memory)
    local map = world.Map
    local px, py, pz = player.Transform:GetWorldPosition()
    local pgx = nav_grid_coordinate(px)
    local pgz = nav_grid_coordinate(pz)
    local radius_cells = math.ceil(NAV_OBSERVE_RADIUS / NAV_CELL_SIZE)
    local now = G.GetTime()

    for gx = pgx - radius_cells, pgx + radius_cells do
        for gz = pgz - radius_cells, pgz + radius_cells do
            local x = gx * NAV_CELL_SIZE
            local z = gz * NAV_CELL_SIZE
            local distance = math.sqrt((x - px) * (x - px) + (z - pz) * (z - pz))
            if distance <= NAV_OBSERVE_RADIUS + NAV_CELL_SIZE * 0.75 then
                local key = nav_key(gx, gz)
                local cell = memory.cells[key] or { gx = gx, gz = gz, x = x, z = z, visits = 0 }
                cell.passable = safe_call(function()
                    return map:IsPassableAtPoint(x, 0, z, false, false)
                end, false)
                cell.last_seen = now
                memory.cells[key] = cell
            end
        end
    end

    local player_key = nav_key(pgx, pgz)
    if memory.last_player_cell ~= player_key then
        local cell = memory.cells[player_key]
        if cell ~= nil then
            cell.visits = (cell.visits or 0) + 1
        end
        memory.last_player_cell = player_key
    end
end

local function frontier_candidates(player, memory)
    local px, py, pz = player.Transform:GetWorldPosition()
    local now = G.GetTime()
    local candidates = {}
    for key, cell in pairs(memory.cells) do
        if cell.passable and (memory.blacklist[key] or 0) <= now then
            local information_gain = 0
            for _, offset in ipairs(NAV_NEIGHBORS) do
                if memory.cells[nav_key(cell.gx + offset[1], cell.gz + offset[2])] == nil then
                    information_gain = information_gain + 1
                end
            end
            if information_gain > 0 then
                local distance = math.sqrt((cell.x - px) * (cell.x - px) + (cell.z - pz) * (cell.z - pz))
                if distance > NAV_ARRIVAL_DISTANCE then
                    local score = information_gain * 10 - distance * 0.5 - (cell.visits or 0) * 6
                    table.insert(candidates,
                    {
                        key = key,
                        gx = cell.gx,
                        gz = cell.gz,
                        x = cell.x,
                        z = cell.z,
                        information_gain = information_gain,
                        visits = cell.visits or 0,
                        score = score,
                        distance = distance,
                    })
                end
            end
        end
    end
    table.sort(candidates, function(a, b)
        return a.score == b.score and a.distance < b.distance or a.score > b.score
    end)
    return candidates
end

local function make_navigation_leg(player, map, target)
    local px, py, pz = player.Transform:GetWorldPosition()
    local dx = target.x - px
    local dz = target.z - pz
    local remaining = math.sqrt(dx * dx + dz * dz)
    if remaining <= NAV_ARRIVAL_DISTANCE then
        return nil, "arrived"
    end
    local leg_distance = math.min(NAV_LEG_DISTANCE, remaining)
    local nx = dx / remaining
    local nz = dz / remaining
    local travelled = NAV_PROBE_STEP
    while travelled < leg_distance do
        if not safe_call(function()
            return map:IsPassableAtPoint(px + nx * travelled, 0, pz + nz * travelled, false, false)
        end, false) then
            return nil, "blocked"
        end
        travelled = travelled + NAV_PROBE_STEP
    end
    local leg_x = px + nx * leg_distance
    local leg_z = pz + nz * leg_distance
    if not safe_call(function()
        return map:IsPassableAtPoint(leg_x, 0, leg_z, false, false)
    end, false) then
        return nil, "blocked"
    end
    return
    {
        x = leg_x,
        z = leg_z,
        dx = leg_x - px,
        dz = leg_z - pz,
        distance = leg_distance,
    }, nil
end

local function read_navigation(player)
    local world = G.TheWorld
    local map = world ~= nil and world.Map or nil
    if world == nil or map == nil then
        return {}
    end
    local memory = get_navigation_memory(world)
    observe_navigation_cells(player, world, memory)
    local candidates = frontier_candidates(player, memory)
    local now = G.GetTime()
    local leg = nil
    local status = "no_frontier"

    if memory.target ~= nil then
        leg, status = make_navigation_leg(player, map, memory.target)
        if status == "arrived" then
            memory.target = nil
        elseif status == "blocked" then
            memory.blacklist[memory.target.key] = now + NAV_BLACKLIST_SECONDS
            memory.target = nil
        end
    end

    if memory.target == nil then
        for _, candidate in ipairs(candidates) do
            if (memory.blacklist[candidate.key] or 0) <= now then
                local candidate_leg, candidate_status = make_navigation_leg(player, map, candidate)
                if candidate_leg ~= nil then
                    memory.target = candidate
                    leg = candidate_leg
                    status = "ready"
                    break
                elseif candidate_status == "blocked" then
                    memory.blacklist[candidate.key] = now + NAV_BLACKLIST_SECONDS
                end
            end
        end
    elseif leg ~= nil then
        status = "ready"
    end

    local observed_cells = 0
    for _ in pairs(memory.cells) do
        observed_cells = observed_cells + 1
    end
    local target = memory.target
    if target ~= nil then
        local px, py, pz = player.Transform:GetWorldPosition()
        target.distance = math.sqrt((target.x - px) * (target.x - px) + (target.z - pz) * (target.z - pz))
    end
    return
    {
        mode = "frontier",
        status = status,
        cell_size = NAV_CELL_SIZE,
        observed_cells = observed_cells,
        frontier_count = #candidates,
        target = target ~= nil and
        {
            x = round(target.x, 2),
            z = round(target.z, 2),
            distance = round(target.distance, 2),
            information_gain = target.information_gain,
            visits = target.visits,
            score = round(target.score, 2),
        } or nil,
        leg = leg ~= nil and
        {
            x = round(leg.x, 2),
            z = round(leg.z, 2),
            dx = round(leg.dx, 2),
            dz = round(leg.dz, 2),
            distance = round(leg.distance, 2),
        } or nil,
    }
end

local function make_state(player)
    local x, y, z = player.Transform:GetWorldPosition()
    local world = G.TheWorld ~= nil and G.TheWorld.state or {}
    local camera = read_camera()

    return
    {
        schema = 6,
        observed_at = round(G.GetTime(), 3),
        player =
        {
            guid = player.GUID,
            prefab = player.prefab or "unknown",
            position = { x = round(x, 2), y = round(y, 2), z = round(z, 2) },
            vitals = read_vitals(player),
            inventory = read_inventory(player),
        },
        world =
        {
            day = (world.cycles or 0) + 1,
            cycles = world.cycles or 0,
            phase = world.phase or "unknown",
            time = round(world.time or 0, 4),
            time_in_phase = round(world.timeinphase or 0, 4),
            is_day = world.isday == true,
            is_dusk = world.isdusk == true,
            is_night = world.isnight == true,
        },
        camera = camera,
        navigation = read_navigation(player),
        crafting = read_crafting(player),
        nearby = read_nearby(player),
    }
end

local function emit_state(player)
    if player == nil or not player:IsValid() or player ~= G.ThePlayer then
        return
    end

    local ok, encoded = protected_call(function() return json.encode(make_state(player)) end)
    if ok then
        print(LOG_PREFIX .. encoded)
    else
        print("[JEV_DST_ERROR]state_encode_failed:" .. tostring(encoded))
    end
end

local function start_telemetry(player)
    if player._jev_dst_telemetry_task ~= nil then
        return
    end

    print("[JEV_DST]telemetry_started interval=" .. tostring(SAMPLE_INTERVAL)
        .. " radius=" .. tostring(SCAN_RADIUS))
    emit_state(player)
    player._jev_dst_telemetry_task = player:DoPeriodicTask(SAMPLE_INTERVAL, emit_state)

    player:ListenForEvent("onremove", function()
        if player._jev_dst_telemetry_task ~= nil then
            player._jev_dst_telemetry_task:Cancel()
            player._jev_dst_telemetry_task = nil
        end
    end)
end

local function craft_torch()
    local player = G.ThePlayer
    if player == nil or not player:IsValid() then
        print("[JEV_DST_ACTION_ERROR]craft_torch:no_local_player")
        return
    end

    local builder = player.replica ~= nil and player.replica.builder or nil
    local recipe = G.GetValidRecipe("torch")
    if builder == nil or recipe == nil then
        print("[JEV_DST_ACTION_ERROR]craft_torch:builder_or_recipe_unavailable")
        return
    end
    if builder:IsBusy() then
        print("[JEV_DST_ACTION_ERROR]craft_torch:builder_busy")
        return
    end
    if not builder:CanBuild("torch") then
        print("[JEV_DST_ACTION_ERROR]craft_torch:not_craftable")
        return
    end

    builder:MakeRecipeFromMenu(recipe)
    print("[JEV_DST_ACTION]craft_torch:requested")
end

local function craft_item(recipe_name)
    local player = G.ThePlayer
    if player == nil or not player:IsValid() then
        print("[JEV_DST_ACTION_ERROR]craft_" .. recipe_name .. ":no_local_player")
        return
    end

    local builder = player.replica ~= nil and player.replica.builder or nil
    local recipe = G.GetValidRecipe(recipe_name)
    if builder == nil or recipe == nil then
        print("[JEV_DST_ACTION_ERROR]craft_" .. recipe_name .. ":builder_or_recipe_unavailable")
        return
    end
    if builder:IsBusy() or not builder:CanBuild(recipe_name) then
        print("[JEV_DST_ACTION_ERROR]craft_" .. recipe_name .. ":not_craftable_or_busy")
        return
    end

    builder:MakeRecipeFromMenu(recipe)
    print("[JEV_DST_ACTION]craft_" .. recipe_name .. ":requested")
end

local function find_nearest_with_tag(player, radius, tag)
    local x, y, z = player.Transform:GetWorldPosition()
    local entities = G.TheSim:FindEntities(x, y, z, radius, { tag }, EXCLUDE_TAGS)
    local nearest = nil
    local nearest_distance = nil
    for _, entity in ipairs(entities) do
        if entity ~= player and entity:IsValid() then
            local distance = player:GetDistanceSqToInst(entity)
            if nearest == nil or distance < nearest_distance then
                nearest = entity
                nearest_distance = distance
            end
        end
    end
    return nearest
end

local function durability_percent(item)
    if item == nil or not item:IsValid() then
        return nil
    end
    if item.components ~= nil and item.components.finiteuses ~= nil then
        return safe_call(function() return item.components.finiteuses:GetPercent() end, nil)
    end
    local classified = item.replica ~= nil and item.replica.inventoryitem ~= nil
        and item.replica.inventoryitem.classified or nil
    local encoded = classified ~= nil and classified.percentused ~= nil
        and safe_call(function() return classified.percentused:value() end, 255) or 255
    return encoded ~= 255 and encoded / 100 or nil
end

local function conservative_uses(item, tool_prefab)
    local percent = durability_percent(item)
    local maximum = TOOL_MAX_USES[tool_prefab]
    if percent == nil or maximum == nil then
        return 0
    end
    local encoded = math.floor(percent * 100 + 0.5)
    for uses = 0, maximum do
        if math.floor(uses / maximum * 100 + 0.5) == encoded then
            return uses
        end
    end
    return 0
end

local function required_work(target, target_tag)
    if target_tag == "CHOP_workable" then
        return DEFAULT_CHOP_WORK
    end
    return MINE_WORK_BY_PREFAB[target.prefab or ""]
end

local function find_sufficient_tool(inventory, tool_prefab, required)
    local best = nil
    local best_uses = -1
    local equipped = inventory:GetEquippedItem(G.EQUIPSLOTS.HANDS)
    if equipped ~= nil and equipped.prefab == tool_prefab then
        best = equipped
        best_uses = conservative_uses(equipped, tool_prefab)
    end
    local items = safe_call(function() return inventory:GetItems() end, {}) or {}
    for _, inventory_tool in pairs(items) do
        if inventory_tool ~= nil and inventory_tool:IsValid() and inventory_tool.prefab == tool_prefab then
            local uses = conservative_uses(inventory_tool, tool_prefab)
            if uses > best_uses then
                best = inventory_tool
                best_uses = uses
            end
        end
    end
    return best_uses >= required and best or nil, best_uses
end

local function perform_work_action(action, target_tag, tool_prefab)
    local player = G.ThePlayer
    if player == nil or not player:IsValid() then
        print("[JEV_DST_ACTION_ERROR]" .. string.lower(action.id) .. ":no_local_player")
        return
    end
    local inventory = player.replica ~= nil and player.replica.inventory or nil
    local controller = player.components ~= nil and player.components.playercontroller or nil
    if inventory == nil or controller == nil then
        print("[JEV_DST_ACTION_ERROR]" .. string.lower(action.id) .. ":controller_unavailable")
        return
    end

    local px, py, pz = player.Transform:GetWorldPosition()
    local candidates = G.TheSim:FindEntities(px, py, pz, 6, { target_tag }, EXCLUDE_TAGS)
    local target, required, tool, available, nearest_distance = nil, nil, nil, 0, nil
    for _, candidate in ipairs(candidates) do
        if candidate ~= player and candidate:IsValid() then
            local candidate_required = required_work(candidate, target_tag)
            local candidate_tool, candidate_available = nil, 0
            if candidate_required ~= nil then
                candidate_tool, candidate_available = find_sufficient_tool(inventory, tool_prefab, candidate_required)
            end
            local distance = player:GetDistanceSqToInst(candidate)
            if candidate_tool ~= nil and (target == nil or distance < nearest_distance) then
                target = candidate
                required = candidate_required
                tool = candidate_tool
                available = candidate_available
                nearest_distance = distance
            end
        end
    end
    if target == nil or required == nil or tool == nil then
        print("[JEV_DST_ACTION_ERROR]" .. string.lower(action.id) .. ":target_or_tool_unavailable")
        return
    end

    if player._jev_dst_work_task ~= nil then
        player._jev_dst_work_task:Cancel()
        player._jev_dst_work_task = nil
    end

    local attempts = 0
    local function schedule(delay, fn)
        player._jev_dst_work_task = player:DoTaskInTime(delay, fn)
    end
    local function dispatch()
        player._jev_dst_work_task = nil
        if not player:IsValid() then
            return
        end
        if not target:IsValid() or not target:HasTag(target_tag) then
            print("[JEV_DST_ACTION]" .. string.lower(action.id) .. ":completed target=" .. tostring(target.GUID))
            return
        end
        if not tool:IsValid() then
            print("[JEV_DST_ACTION_ERROR]" .. string.lower(action.id) .. ":tool_broke_before_completion")
            return
        end
        if safe_call(function() return controller:IsBusy() end, false) then
            schedule(0.15, dispatch)
            return
        end
        local buffaction = controller:GetActionButtonAction(target)
        if buffaction == nil or buffaction.action ~= action then
            schedule(0.2, dispatch)
            return
        end
        if not controller.ismastersim then
            if controller.locomotor == nil then
                buffaction.non_preview_cb = function()
                    controller:RemoteActionButton(buffaction, true)
                end
            else
                buffaction.preview_cb = function()
                    controller:RemoteActionButton(buffaction, true)
                end
            end
        end
        controller:DoAction(buffaction)
        attempts = attempts + 1
        if attempts > required + 5 then
            print("[JEV_DST_ACTION_ERROR]" .. string.lower(action.id) .. ":completion_timeout")
            return
        end
        schedule(0.55, dispatch)
    end

    local equipped = inventory:GetEquippedItem(G.EQUIPSLOTS.HANDS)
    if equipped == nil or equipped.prefab ~= tool_prefab then
        inventory:UseItemFromInvTile(tool)
        schedule(0.3, dispatch)
    else
        dispatch()
    end
    print("[JEV_DST_ACTION]" .. string.lower(action.id) .. ":started target=" .. tostring(target.GUID)
        .. " required=" .. tostring(required) .. " available=" .. tostring(available))
end

local SAFE_FOOD_PRIORITY =
{
    "berries_cooked",
    "carrot_cooked",
    "berries",
    "carrot",
    "seeds_cooked",
    "seeds",
}

local function eat_safe_food()
    local player = G.ThePlayer
    local inventory = player ~= nil and player.replica ~= nil and player.replica.inventory or nil
    if player == nil or not player:IsValid() or inventory == nil then
        print("[JEV_DST_ACTION_ERROR]eat_safe_food:player_or_inventory_unavailable")
        return
    end
    local food = nil
    for _, prefab in ipairs(SAFE_FOOD_PRIORITY) do
        food = inventory:FindItem(function(item)
            return item ~= nil and item:IsValid() and item.prefab == prefab
        end)
        if food ~= nil then
            break
        end
    end
    if food == nil then
        print("[JEV_DST_ACTION_ERROR]eat_safe_food:no_safe_food")
        return
    end
    inventory:UseItemFromInvTile(food)
    print("[JEV_DST_ACTION]eat_safe_food:requested prefab=" .. tostring(food.prefab))
end

local function attack_nearest_hostile()
    local player = G.ThePlayer
    local controller = player ~= nil and player.components ~= nil and player.components.playercontroller or nil
    if player == nil or not player:IsValid() or controller == nil then
        print("[JEV_DST_ACTION_ERROR]attack_nearest_hostile:controller_unavailable")
        return
    end
    local x, y, z = player.Transform:GetWorldPosition()
    local candidates = G.TheSim:FindEntities(x, y, z, 8, nil, EXCLUDE_TAGS)
    local target = nil
    local target_distance = nil
    for _, entity in ipairs(candidates) do
        if entity ~= player and entity:IsValid() and entity:HasAnyTag("hostile", "monster")
            and player.replica ~= nil and player.replica.combat ~= nil
            and safe_call(function() return player.replica.combat:CanTarget(entity) end, false) then
            local distance = player:GetDistanceSqToInst(entity)
            if target == nil or distance < target_distance then
                target = entity
                target_distance = distance
            end
        end
    end
    if target == nil then
        print("[JEV_DST_ACTION_ERROR]attack_nearest_hostile:no_hostile_target")
        return
    end
    -- Use the game's mouse-attack path. The second argument marks this as a
    -- primary/left-click attack, matching a player left-clicking the target.
    controller:DoAttackButton(target, true)
    print("[JEV_DST_ACTION]attack_nearest_hostile:left_click target=" .. tostring(target.GUID))
end

local function build_campfire()
    local player = G.ThePlayer
    local builder = player ~= nil and player.replica ~= nil and player.replica.builder or nil
    local recipe = G.GetValidRecipe("campfire")
    if player == nil or not player:IsValid() or builder == nil or recipe == nil then
        print("[JEV_DST_ACTION_ERROR]build_campfire:builder_unavailable")
        return
    end
    if G.TheWorld == nil or not G.TheWorld.state.isnight then
        print("[JEV_DST_ACTION_ERROR]build_campfire:not_night")
        return
    end
    if has_nearby_lit_fire(player, 8) then
        print("[JEV_DST_ACTION_ERROR]build_campfire:lit_fire_nearby")
        return
    end
    local already_buffered = builder:IsBuildBuffered(recipe.name)
    if builder:IsBusy() or (not already_buffered and not builder:CanBuild("campfire")) then
        print("[JEV_DST_ACTION_ERROR]build_campfire:not_craftable_or_busy")
        return
    end

    local controller = player.components ~= nil and player.components.playercontroller or nil
    if controller == nil then
        print("[JEV_DST_ACTION_ERROR]build_campfire:controller_unavailable")
        return
    end

    -- Match DoRecipeClick for recipes with placers: first buffer/craft the
    -- structure, then enter placement mode. MakeRecipeAtPoint alone is
    -- rejected by the server when no buffered campfire exists.
    if not already_buffered then
        builder:BufferBuild(recipe.name)
    end
    if not builder:IsBuildBuffered(recipe.name) then
        print("[JEV_DST_ACTION_ERROR]build_campfire:buffer_failed")
        return
    end
    controller:StartBuildPlacementMode(recipe, nil)
    print("[JEV_DST_ACTION]build_campfire:buffered")

    player:DoTaskInTime(0.35, function()
        if not player:IsValid() or not builder:IsBuildBuffered(recipe.name) then
            print("[JEV_DST_ACTION_ERROR]build_campfire:buffer_lost_before_placement")
            return
        end

        local px, py, pz = player.Transform:GetWorldPosition()
        local point = nil
        local radii = { 1.25, 1.5, 1.75, 2.0 }
        for _, radius in ipairs(radii) do
            for angle = 0, 315, 45 do
                local radians = angle * G.DEGREES
                local candidate = G.Vector3(px + math.cos(radians) * radius, 0, pz + math.sin(radians) * radius)
                if builder:CanBuildAtPoint(candidate, recipe, 0) then
                    point = candidate
                    break
                end
            end
            if point ~= nil then
                break
            end
        end
        if point == nil then
            controller:CancelPlacement()
            print("[JEV_DST_ACTION_ERROR]build_campfire:no_valid_position")
            return
        end

        if controller.placer ~= nil then
            controller.placer.Transform:SetPosition(point.x, point.y, point.z)
            controller.placer.Transform:SetRotation(0)
        end
        builder:MakeRecipeAtPoint(recipe, point, 0)
        controller:CancelPlacement()
        print("[JEV_DST_ACTION]build_campfire:placed x=" .. tostring(round(point.x, 2))
            .. " z=" .. tostring(round(point.z, 2)))
    end)
end

local function equip_torch()
    local player = G.ThePlayer
    if player == nil or not player:IsValid() then
        print("[JEV_DST_ACTION_ERROR]equip_torch:no_local_player")
        return
    end
    if G.TheWorld == nil or not G.TheWorld.state.isnight then
        print("[JEV_DST_ACTION_ERROR]equip_torch:not_night")
        return
    end
    if has_nearby_lit_fire(player, 8) then
        print("[JEV_DST_ACTION_ERROR]equip_torch:lit_fire_nearby")
        return
    end

    local inventory = player.replica ~= nil and player.replica.inventory or nil
    if inventory == nil then
        print("[JEV_DST_ACTION_ERROR]equip_torch:inventory_unavailable")
        return
    end

    local torch = inventory:FindItem(function(item)
        return item ~= nil and item:IsValid() and item.prefab == "torch"
    end)
    if torch == nil then
        print("[JEV_DST_ACTION_ERROR]equip_torch:no_torch")
        return
    end

    -- Mirror a player clicking the item in an inventory tile. EquipActionItem
    -- is an action-system auto-equip helper and does not directly equip here.
    inventory:UseItemFromInvTile(torch)
    print("[JEV_DST_ACTION]equip_torch:requested guid=" .. tostring(torch.GUID))
end

local function unequip_torch()
    local player = G.ThePlayer
    if player == nil or not player:IsValid() then
        print("[JEV_DST_ACTION_ERROR]unequip_torch:no_local_player")
        return
    end
    if G.TheWorld ~= nil and G.TheWorld.state.isnight and not has_nearby_lit_fire(player, 8) then
        print("[JEV_DST_ACTION_ERROR]unequip_torch:no_alternate_light")
        return
    end

    local inventory = player.replica ~= nil and player.replica.inventory or nil
    if inventory == nil then
        print("[JEV_DST_ACTION_ERROR]unequip_torch:inventory_unavailable")
        return
    end

    local hands_item = inventory:GetEquippedItem(G.EQUIPSLOTS.HANDS)
    if hands_item == nil or hands_item.prefab ~= "torch" then
        print("[JEV_DST_ACTION_ERROR]unequip_torch:torch_not_equipped")
        return
    end

    inventory:TakeActiveItemFromEquipSlot(G.EQUIPSLOTS.HANDS)
    player:DoTaskInTime(0.1, function()
        if player:IsValid() and player.replica ~= nil and player.replica.inventory ~= nil then
            player.replica.inventory:ReturnActiveItem()
        end
    end)
    print("[JEV_DST_ACTION]unequip_torch:requested guid=" .. tostring(hands_item.GUID))
end

local function equip_best_weapon()
    local player = G.ThePlayer
    local inventory = player ~= nil and player.replica ~= nil and player.replica.inventory or nil
    if player == nil or not player:IsValid() or inventory == nil then
        print("[JEV_DST_ACTION_ERROR]equip_weapon:player_or_inventory_unavailable")
        return
    end

    local equipped = inventory:GetEquippedItem(G.EQUIPSLOTS.HANDS)
    if equipped ~= nil and equipped.prefab ~= "torch"
        and (equipped:HasTag("weapon") or WEAPON_PREFAB_SCORE[equipped.prefab or ""] ~= nil) then
        print("[JEV_DST_ACTION]equip_weapon:already_equipped guid=" .. tostring(equipped.GUID))
        return
    end

    local best = nil
    local best_score = nil
    local items = safe_call(function() return inventory:GetItems() end, {}) or {}
    for _, item in pairs(items) do
        if item ~= nil and item:IsValid() and item.prefab ~= "torch"
            and (item:HasTag("weapon") or WEAPON_PREFAB_SCORE[item.prefab or ""] ~= nil) then
            local score = WEAPON_PREFAB_SCORE[item.prefab or ""] or 1
            if item.components ~= nil and item.components.weapon ~= nil then
                score = safe_call(function() return item.components.weapon:GetDamage(player, nil) end, score)
            end
            if best == nil or score > best_score then
                best = item
                best_score = score
            end
        end
    end
    if best == nil then
        print("[JEV_DST_ACTION_ERROR]equip_weapon:no_weapon")
        return
    end

    inventory:UseItemFromInvTile(best)
    print("[JEV_DST_ACTION]equip_weapon:requested prefab=" .. tostring(best.prefab)
        .. " guid=" .. tostring(best.GUID))
end

-- Reserved bridge keys. The external controller emits these keys; the mod
-- translates them into bounded semantic actions and validates prerequisites.
G.TheInput:AddKeyUpHandler(G.KEY_KP_MULTIPLY, craft_torch)
G.TheInput:AddKeyUpHandler(G.KEY_KP_DIVIDE, equip_torch)
G.TheInput:AddKeyUpHandler(G.KEY_KP_MINUS, unequip_torch)
G.TheInput:AddKeyUpHandler(G.KEY_KP_PLUS, function() craft_item("axe") end)
G.TheInput:AddKeyUpHandler(G.KEY_KP_PERIOD, function() craft_item("pickaxe") end)
G.TheInput:AddKeyUpHandler(G.KEY_F6, build_campfire)
G.TheInput:AddKeyUpHandler(G.KEY_F7, eat_safe_food)
G.TheInput:AddKeyUpHandler(G.KEY_F8, function() perform_work_action(G.ACTIONS.CHOP, "CHOP_workable", "axe") end)
G.TheInput:AddKeyUpHandler(G.KEY_F9, function() perform_work_action(G.ACTIONS.MINE, "MINE_workable", "pickaxe") end)
G.TheInput:AddKeyUpHandler(G.KEY_F10, attack_nearest_hostile)
G.TheInput:AddKeyUpHandler(G.KEY_F11, equip_best_weapon)

-- ThePlayer is often still nil while player prefabs are being initialized on a
-- joining client. Poll from the world instead of making a one-shot comparison.
-- A world task is also cleaned up automatically when leaving the world.
AddPrefabPostInit("world", function(world)
    local attempts = 0
    local bootstrap_task = nil

    print("[JEV_DST]world_ready waiting_for_local_player")

    bootstrap_task = world:DoPeriodicTask(PLAYER_WAIT_INTERVAL, function()
        attempts = attempts + 1
        local player = G.ThePlayer

        if player ~= nil and player:IsValid() then
            if bootstrap_task ~= nil then
                bootstrap_task:Cancel()
                bootstrap_task = nil
            end
            print("[JEV_DST]local_player_ready attempts=" .. tostring(attempts)
                .. " prefab=" .. tostring(player.prefab))
            start_telemetry(player)
        elseif attempts == 1 or attempts % 20 == 0 then
            print("[JEV_DST]waiting_for_local_player attempts=" .. tostring(attempts))
        end
    end)
end)
