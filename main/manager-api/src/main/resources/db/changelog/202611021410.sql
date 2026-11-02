-- Qwen TTS 配置防呆修复：
-- 1) 清理用户在可视化页面误填的 api_url（OpenAI 兼容地址等非 DashScope 原生端点，
--    会导致 HTTP 404）。仅当已填值不含 DashScope 原生路径特征时移除；
--    2) 供应器 fields 里 api_url 的 label 改为「服务地址(留空即可)」，避免用户误以为必填。

UPDATE `ai_model_config`
SET `config_json` = JSON_REMOVE(`config_json`, '$.api_url')
WHERE `id` IN ('TTS_QwenTTS', 'TTS_QwenAudioTTS')
  AND JSON_EXTRACT(`config_json`, '$.api_url') IS NOT NULL
  AND JSON_UNQUOTE(JSON_EXTRACT(`config_json`, '$.api_url')) NOT LIKE '%/api/v1/services/%';

UPDATE `ai_model_config`
SET `remark` = 'Qwen3-TTS语音合成（非实时 + SSE流式输出）配置说明：
1. 阿里云百炼的上一代 TTS，首包快、成本低（0.8元/万字符），适合追求吞吐和成本的批量播报场景
2. 音频固定 PCM 24000Hz/16bit/单声道，设备采样率非 24kHz 时服务端自动重采样，无需配置
3. 单次请求文本上限 600 字符，超长文本会自动按句切分串行合成
4. model 可选：qwen3-tts-flash
5. voice 音色列表：https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list
6. language_type 可选：Auto/Chinese/English/German/Italian/Portuguese/Spanish/Japanese/Korean/French/Russian，默认 Auto
7. 服务地址(api_url)留空即可使用官方端点，勿填 OpenAI 兼容地址（如 compatible-mode），该协议不被支持
8. 必须使用阿里云百炼原生 API Key（DASHSCOPE_API_KEY），第三方 OpenAI 兼容代理的 Key 不适用
9. 如追求更高拟人度、情感表达与方言能力，建议选用 Qwen-Audio-TTS语音合成
API Key 获取：https://bailian.console.aliyun.com/?tab=model#/api-key'
WHERE `id` = 'TTS_QwenTTS';

UPDATE `ai_model_config`
SET `remark` = 'Qwen-Audio-TTS语音合成（非实时 + SSE流式输出）配置说明：
1. 阿里云百炼新一代 TTS，拟人度和表达力全面优于 Qwen3-TTS，Flash 档面向低延迟交互场景调优（官方口径首包约 300ms）
2. 支持 instruction 自然语言指令（如“请用温柔亲切的语气说话”）、情感/拟声标签、多种中文方言
3. 支持语速 rate(0.5~2.0)、音调 pitch(0.5~2.0)、音量 volume(0~100) 调节，均可留空使用默认值
4. 采样率支持 8000/16000/22050/24000/44100/48000，直接对齐设备采样率，无需重采样
5. 单次请求文本上限 600 字符，超长文本会自动按句切分串行合成
6. model 可选：qwen-audio-3.1-tts-flash（推荐，低延迟）、qwen-audio-3.x-tts-plus（音质最好但吞吐仅约16字/秒，交互场景不建议）
7. voice 音色列表：https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list，注意音色必须属于当前 model，不能跨模型混用
8. 服务地址(api_url)留空即可使用官方端点，勿填 OpenAI 兼容地址（如 compatible-mode），该协议不被支持
9. 必须使用阿里云百炼原生 API Key（DASHSCOPE_API_KEY），第三方 OpenAI 兼容代理的 Key 不适用
API Key 获取：https://bailian.console.aliyun.com/?tab=model#/api-key'
WHERE `id` = 'TTS_QwenAudioTTS';

-- 供应器字段 label 防呆：明确 api_url 非必填
UPDATE `ai_model_provider`
SET `fields` = REPLACE(
    `fields`,
    '{"key":"api_url","label":"服务地址","type":"string"}',
    '{"key":"api_url","label":"服务地址(留空即可)","type":"string"}'
)
WHERE `id` IN ('SYSTEM_TTS_QwenTTS', 'SYSTEM_TTS_QwenAudioTTS');
