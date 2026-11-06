-- 性格模板绑定豆包语音合成2.0音色。
-- 湾湾小何 → 湾湾小何，星际游子 → 云舟，英语老师 → Vivi。
-- sort 原来是无符号，不能用负数表示下线，先改成有符号。
-- 好奇男孩、汪汪队长暂时下线：sort < 0，不进入语音选择，也不作为新建智能体的默认模板。

ALTER TABLE `ai_agent_template`
    MODIFY COLUMN `sort` INT NULL DEFAULT 0 COMMENT '排序权重，小于0表示下线';

UPDATE `ai_agent_template`
SET `tts_model_id` = 'TTS_HSDSTTS_V2',
    `tts_voice_id` = 'TTS_HSDSTTS_V2_0001'
WHERE `agent_name` = '湾湾小何';

UPDATE `ai_agent_template`
SET `tts_model_id` = 'TTS_HSDSTTS_V2',
    `tts_voice_id` = 'TTS_HSDSTTS_V2_0014'
WHERE `agent_name` = '星际游子';

UPDATE `ai_agent_template`
SET `tts_model_id` = 'TTS_HSDSTTS_V2',
    `tts_voice_id` = 'TTS_HSDSTTS_V2_0002'
WHERE `agent_name` = '英语老师';

UPDATE `ai_agent_template`
SET `sort` = -4
WHERE `agent_name` = '好奇男孩'
  AND (`sort` IS NULL OR `sort` >= 0);

UPDATE `ai_agent_template`
SET `sort` = -5
WHERE `agent_name` = '汪汪队长'
  AND (`sort` IS NULL OR `sort` >= 0);
