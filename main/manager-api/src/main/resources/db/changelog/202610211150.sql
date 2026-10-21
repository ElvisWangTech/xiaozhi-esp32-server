-- 默认音色改为小米 MiMo 语音合成。
-- 新建智能体从默认模板复制音色。模板和仍使用火山双流式的已有智能体一起切换，控制台卡片才会显示 MiMo。
-- 音色用 MiMo 的「冰糖」（ai_tts_voice.id = TTS_MimoTTS_0001）。火山音色编号不能留给 MiMo。

UPDATE `ai_model_config`
SET `is_default` = 0
WHERE `model_type` = 'TTS';

UPDATE `ai_model_config`
SET `is_default` = 1, `is_enabled` = 1
WHERE `id` = 'TTS_MimoTTS';

UPDATE `ai_agent_template`
SET `tts_model_id` = 'TTS_MimoTTS',
    `tts_voice_id` = 'TTS_MimoTTS_0001'
WHERE `tts_model_id` = 'TTS_HuoshanDoubleStreamTTS';

UPDATE `ai_agent`
SET `tts_model_id` = 'TTS_MimoTTS',
    `tts_voice_id` = 'TTS_MimoTTS_0001'
WHERE `tts_model_id` = 'TTS_HuoshanDoubleStreamTTS';
