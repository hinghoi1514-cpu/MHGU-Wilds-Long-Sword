-- Read-only action -> animation identification for the MHGU port.
-- Depends on the already-installed CatLib. No hooks, action requests, or writes
-- to hunter/game data. Output: reframework/data/codex_ls_motion_trace.json.
local ok, CatLib = pcall(require, '_CatLib')
local okPlayer, Player = pcall(require, '_CatLib.game.player')
if not ok or not okPlayer then
    log.error('[codex_ls] Existing CatLib was unavailable; trace disabled')
    return
end
local enabled, rows, last, status = true, {}, '', 'Waiting for a longsword hunter'
local file = 'codex_ls_motion_trace.json'
local function save()
    json.dump_file(file, {format=1, purpose='Longsword action and source clip identification', records=rows})
end
local function sample()
    if not enabled or #rows >= 2048 then return end
    local character = Player.GetCharacter()
    if not character or Player.GetWeaponType() ~= 3 then return end
    local motion = CatLib.GetPlayerMotionData()
    if not motion then return end
    local ctrl = character:get_BaseActionController()
    local action = ctrl and ctrl:get_CurrentAction()
    if not action then return end
    local actionType = action:get_type_definition():get_full_name()
    local node = tostring(motion.MotionNodeMotionName or motion.MotionNodeName or '')
    local key = actionType .. '|' .. tostring(motion.MotionBankID) .. '|' .. tostring(motion.MotionID) .. '|' .. node
    if key == last then return end
    last = key
    local row = {time=os.date('%Y-%m-%d %H:%M:%S'), action_type=actionType,
                 motion_bank=motion.MotionBankID, motion_id=motion.MotionID,
                 motion_name=node, frame=motion.Frame, end_frame=motion.EndFrame}
    local good, guide = pcall(function() return action:get_field('_ActionGuideID') end)
    if good then row.action_guide_id = guide end
    rows[#rows+1] = row
    save()
    status = actionType .. ' -> ' .. node .. ' (#' .. tostring(motion.MotionID) .. ')'
end
re.on_frame(function()
    local success, err = pcall(sample)
    if not success then status = tostring(err) end
end)
re.on_draw_ui(function()
    if imgui.tree_node('Codex LS motion trace') then
        local changed, value = imgui.checkbox('Record action-to-motion changes', enabled)
        if changed then enabled = value; save() end
        imgui.text(status)
        imgui.text('Records: ' .. tostring(#rows) .. ' / 2048')
        imgui.text('Read-only: no hunter values or game actions are changed.')
        if imgui.button('Save trace') then save() end
        imgui.tree_pop()
    end
end)
re.on_script_reset(save)
