-- 默认 ASR / LLM / TTS 从小米 MiMo 改为：
-- ASR：豆包语音识别2.0(流式)  LLM：deepseek-chat  TTS：豆包语音合成2.0(流式)
-- 新建智能体（含语音激活绑定）从模板复制这三项，因此模板和仍使用 MiMo 的已有智能体一起切换。
-- 音色用豆包语音合成2.0的「湾湾小何」（ai_tts_voice.id = TTS_HSDSTTS_V2_0001）。

UPDATE `ai_model_config`
SET `is_default` = 0
WHERE `model_type` IN ('ASR', 'LLM', 'TTS');

UPDATE `ai_model_config`
SET `is_default` = 1, `is_enabled` = 1
WHERE `id` IN ('ASR_DoubaoStreamASRV2', 'LLM_DeepSeekLLM', 'TTS_HSDSTTS_V2');

UPDATE `ai_model_config`
SET `config_json` = JSON_SET(`config_json`, '$.model_name', 'deepseek-chat')
WHERE `id` = 'LLM_DeepSeekLLM';

UPDATE `ai_agent_template`
SET `asr_model_id` = 'ASR_DoubaoStreamASRV2'
WHERE `asr_model_id` = 'ASR_MimoASR';

UPDATE `ai_agent_template`
SET `llm_model_id` = 'LLM_DeepSeekLLM'
WHERE `llm_model_id` = 'LLM_MimoLLM';

UPDATE `ai_agent_template`
SET `tts_model_id` = 'TTS_HSDSTTS_V2',
    `tts_voice_id` = 'TTS_HSDSTTS_V2_0001'
WHERE `tts_model_id` = 'TTS_MimoTTS';

UPDATE `ai_agent`
SET `asr_model_id` = 'ASR_DoubaoStreamASRV2'
WHERE `asr_model_id` = 'ASR_MimoASR';

UPDATE `ai_agent`
SET `llm_model_id` = 'LLM_DeepSeekLLM'
WHERE `llm_model_id` = 'LLM_MimoLLM';

UPDATE `ai_agent`
SET `slm_model_id` = 'LLM_DeepSeekLLM'
WHERE `slm_model_id` = 'LLM_MimoLLM';

UPDATE `ai_agent`
SET `tts_model_id` = 'TTS_HSDSTTS_V2',
    `tts_voice_id` = 'TTS_HSDSTTS_V2_0001'
WHERE `tts_model_id` = 'TTS_MimoTTS';
