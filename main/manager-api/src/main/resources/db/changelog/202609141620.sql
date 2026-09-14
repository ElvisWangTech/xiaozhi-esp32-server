-- 小米 MiMo ASR / TTS / LLM
delete from `ai_model_provider` where id = 'SYSTEM_ASR_MimoASR';
INSERT INTO `ai_model_provider` (`id`, `model_type`, `provider_code`, `name`, `fields`, `sort`, `creator`, `create_date`, `updater`, `update_date`) VALUES
('SYSTEM_ASR_MimoASR', 'ASR', 'mimo', '小米MiMo语音识别', '[{"key":"base_url","type":"string","label":"基础URL"},{"key":"model_name","type":"string","label":"模型名称"},{"key":"api_key","type":"string","label":"API密钥"},{"key":"language","type":"string","label":"语种(auto/zh/en)"},{"key":"output_dir","type":"string","label":"输出目录"}]', 15, 1, NOW(), 1, NOW());

delete from `ai_model_config` where id = 'ASR_MimoASR';
INSERT INTO `ai_model_config` VALUES ('ASR_MimoASR', 'ASR', 'MimoASR', '小米MiMo语音识别', 0, 1, '{\"type\": \"mimo\", \"api_key\": \"\", \"base_url\": \"https://api.xiaomimimo.com/v1\", \"model_name\": \"mimo-v2.5-asr\", \"language\": \"zh\", \"output_dir\": \"tmp/\"}', NULL, NULL, 15, NULL, NULL, NULL, NULL);

UPDATE `ai_model_config` SET
`doc_link` = 'https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/Speech-Recognition',
`remark` = '小米 MiMo ASR 配置说明：
1. 登录 https://mimo.mi.com/ 获取 API Key
2. 供应商必须选「小米MiMo语音识别」，不要用 OpenAI语音识别去填 MiMo 地址
3. 基础URL填 https://api.xiaomimimo.com/v1（API根地址），不要填 /audio/transcriptions
4. 模型名称固定为 mimo-v2.5-asr
5. 语种：auto / zh / en，中文场景建议填 zh
6. 容器需能解析 api.xiaomimimo.com，DNS 失败时给 Docker 配置 223.5.5.5 等
详细接入说明见仓库 docs/mimo-asr-tts-integration.md
' WHERE `id` = 'ASR_MimoASR';

delete from `ai_model_provider` where id = 'SYSTEM_TTS_MimoTTS';
INSERT INTO `ai_model_provider` (`id`, `model_type`, `provider_code`, `name`, `fields`, `sort`, `creator`, `create_date`, `updater`, `update_date`) VALUES
('SYSTEM_TTS_MimoTTS', 'TTS', 'mimo', '小米MiMo语音合成', '[{"key":"api_key","type":"string","label":"API密钥"},{"key":"base_url","type":"string","label":"基础URL"},{"key":"model","type":"string","label":"模型"},{"key":"voice","type":"string","label":"音色"},{"key":"format","type":"string","label":"音频格式"},{"key":"style_prompt","type":"string","label":"风格提示(可选)"},{"key":"output_dir","type":"string","label":"输出目录"}]', 24, 1, NOW(), 1, NOW());

delete from `ai_model_config` where id = 'TTS_MimoTTS';
INSERT INTO `ai_model_config` VALUES ('TTS_MimoTTS', 'TTS', 'MimoTTS', '小米MiMo语音合成', 0, 1, '{\"type\": \"mimo\", \"api_key\": \"\", \"base_url\": \"https://api.xiaomimimo.com/v1\", \"model\": \"mimo-v2.5-tts\", \"voice\": \"冰糖\", \"format\": \"wav\", \"style_prompt\": \"\", \"output_dir\": \"tmp/\"}', NULL, NULL, 24, NULL, NULL, NULL, NULL);

UPDATE `ai_model_config` SET
`doc_link` = 'https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/speech-synthesis-v2.5',
`remark` = '小米 MiMo TTS 配置说明：
1. 登录 https://mimo.mi.com/ 获取 API Key
2. 供应商必须选「小米MiMo语音合成」，不要用 OpenAI TTS 去填 MiMo 地址
3. 基础URL填 https://api.xiaomimimo.com/v1
4. 预置音色模型用 mimo-v2.5-tts；音色可选 mimo_default、冰糖、茉莉、苏打、白桦、Mia、Chloe、Milo、Dean
5. 风格提示为可选，会作为 user 消息控制语气
详细接入说明见仓库 docs/mimo-asr-tts-integration.md
' WHERE `id` = 'TTS_MimoTTS';

delete from `ai_tts_voice` where tts_model_id = 'TTS_MimoTTS';
INSERT INTO `ai_tts_voice` VALUES ('TTS_MimoTTS_0001', 'TTS_MimoTTS', '冰糖', '冰糖', '中文', NULL, NULL, NULL, NULL, 1, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_MimoTTS_0002', 'TTS_MimoTTS', '茉莉', '茉莉', '中文', NULL, NULL, NULL, NULL, 2, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_MimoTTS_0003', 'TTS_MimoTTS', '苏打', '苏打', '中文', NULL, NULL, NULL, NULL, 3, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_MimoTTS_0004', 'TTS_MimoTTS', '白桦', '白桦', '中文', NULL, NULL, NULL, NULL, 4, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_MimoTTS_0005', 'TTS_MimoTTS', 'MiMo默认', 'mimo_default', '中文/英文', NULL, NULL, NULL, NULL, 5, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_MimoTTS_0006', 'TTS_MimoTTS', 'Mia', 'Mia', '英文', NULL, NULL, NULL, NULL, 6, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_MimoTTS_0007', 'TTS_MimoTTS', 'Chloe', 'Chloe', '英文', NULL, NULL, NULL, NULL, 7, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_MimoTTS_0008', 'TTS_MimoTTS', 'Milo', 'Milo', '英文', NULL, NULL, NULL, NULL, 8, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_MimoTTS_0009', 'TTS_MimoTTS', 'Dean', 'Dean', '英文', NULL, NULL, NULL, NULL, 9, NULL, NULL, NULL, NULL);

delete from `ai_model_config` where id = 'LLM_MimoLLM';
INSERT INTO `ai_model_config` VALUES ('LLM_MimoLLM', 'LLM', 'MimoLLM', '小米MiMo大模型', 0, 1, '{\"type\": \"openai\", \"base_url\": \"https://api.xiaomimimo.com/v1\", \"model_name\": \"mimo-v2.5\", \"api_key\": \"\"}', NULL, NULL, 16, NULL, NULL, NULL, NULL);

UPDATE `ai_model_config` SET
`doc_link` = 'https://mimo.mi.com/docs/zh-CN/quick-start/summary/model',
`remark` = '小米 MiMo 大模型配置说明：
1. 登录 https://mimo.mi.com/ 获取 API Key
2. 供应器选 OpenAI接口即可（兼容 Chat Completions）
3. 基础URL：https://api.xiaomimimo.com/v1
4. 模型名称：mimo-v2.5 或 mimo-v2.5-pro
5. 语音识别/合成请分别使用 MimoASR、MimoTTS，不要把大模型地址填进 OpenAI ASR/TTS
' WHERE `id` = 'LLM_MimoLLM';
