-- 新增阿里云百炼 Qwen3-TTS / Qwen-Audio-TTS 语音合成供应器与模型配置
-- 对应 xiaozhi-server 端 provider：core/providers/tts/qwen_tts.py、qwen_audio_tts.py
-- 接入方式：非实时语音合成 + SSE 流式输出（X-DashScope-SSE: enable）

-- ============================================================
-- 1. 供应器定义（新增模型弹窗“供应商”下拉 + 调用信息字段可视化渲染）
-- ============================================================
DELETE FROM `ai_model_provider` WHERE `id` = 'SYSTEM_TTS_QwenTTS';
INSERT INTO `ai_model_provider` (`id`, `model_type`, `provider_code`, `name`, `fields`, `sort`, `creator`, `create_date`, `updater`, `update_date`) VALUES
('SYSTEM_TTS_QwenTTS', 'TTS', 'qwen_tts', 'Qwen3-TTS语音合成',
'[{"key":"api_key","label":"API密钥","type":"password"},{"key":"model","label":"模型名称","type":"string"},{"key":"voice","label":"默认音色","type":"string"},{"key":"language_type","label":"语言类型","type":"string"},{"key":"api_url","label":"服务地址","type":"string"},{"key":"max_chars","label":"单次请求文本上限","type":"number"},{"key":"output_dir","label":"输出目录","type":"string"}]',
21, 1, NOW(), 1, NOW());

DELETE FROM `ai_model_provider` WHERE `id` = 'SYSTEM_TTS_QwenAudioTTS';
INSERT INTO `ai_model_provider` (`id`, `model_type`, `provider_code`, `name`, `fields`, `sort`, `creator`, `create_date`, `updater`, `update_date`) VALUES
('SYSTEM_TTS_QwenAudioTTS', 'TTS', 'qwen_audio_tts', 'Qwen-Audio-TTS语音合成',
'[{"key":"api_key","label":"API密钥","type":"password"},{"key":"model","label":"模型名称","type":"string"},{"key":"voice","label":"默认音色","type":"string"},{"key":"instruction","label":"语音指令","type":"string"},{"key":"language_hints","label":"语言提示","type":"string"},{"key":"rate","label":"语速(0.5~2.0)","type":"number"},{"key":"pitch","label":"音调(0.5~2.0)","type":"number"},{"key":"volume","label":"音量(0~100)","type":"number"},{"key":"api_url","label":"服务地址","type":"string"},{"key":"max_chars","label":"单次请求文本上限","type":"number"},{"key":"output_dir","label":"输出目录","type":"string"}]',
22, 1, NOW(), 1, NOW());

-- ============================================================
-- 2. 模型配置（语音合成列表中出现两条新记录，可在页面可视化编辑）
-- ============================================================
DELETE FROM `ai_model_config` WHERE `id` = 'TTS_QwenTTS';
INSERT INTO `ai_model_config` VALUES ('TTS_QwenTTS', 'TTS', 'QwenTTS', 'Qwen3-TTS语音合成', 0, 1,
'{\"type\": \"qwen_tts\", \"api_key\": \"\", \"model\": \"qwen3-tts-flash\", \"voice\": \"Cherry\", \"output_dir\": \"tmp/\"}',
NULL, NULL, 21, NULL, NULL, NULL, NULL);

DELETE FROM `ai_model_config` WHERE `id` = 'TTS_QwenAudioTTS';
INSERT INTO `ai_model_config` VALUES ('TTS_QwenAudioTTS', 'TTS', 'QwenAudioTTS', 'Qwen-Audio-TTS语音合成', 0, 1,
'{\"type\": \"qwen_audio_tts\", \"api_key\": \"\", \"model\": \"qwen-audio-3.1-tts-flash\", \"voice\": \"longanhuan_v3.1\", \"output_dir\": \"tmp/\"}',
NULL, NULL, 22, NULL, NULL, NULL, NULL);

-- 配置说明
UPDATE `ai_model_config` SET
`doc_link` = 'https://help.aliyun.com/zh/model-studio/qwen-tts-api',
`remark` = 'Qwen3-TTS语音合成（非实时 + SSE流式输出）配置说明：
1. 阿里云百炼的上一代 TTS，首包快、成本低（0.8元/万字符），适合追求吞吐和成本的批量播报场景
2. 音频固定 PCM 24000Hz/16bit/单声道，设备采样率非 24kHz 时服务端自动重采样，无需配置
3. 单次请求文本上限 600 字符，超长文本会自动按句切分串行合成
4. model 可选：qwen3-tts-flash
5. voice 音色列表：https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list
6. language_type 可选：Auto/Chinese/English/German/Italian/Portuguese/Spanish/Japanese/Korean/French/Russian，默认 Auto
7. 如追求更高拟人度、情感表达与方言能力，建议选用 Qwen-Audio-TTS语音合成
API Key 获取：https://bailian.console.aliyun.com/?tab=model#/api-key'
WHERE `id` = 'TTS_QwenTTS';

UPDATE `ai_model_config` SET
`doc_link` = 'https://help.aliyun.com/zh/model-studio/qwen-audio-tts-http-python-sdk',
`remark` = 'Qwen-Audio-TTS语音合成（非实时 + SSE流式输出）配置说明：
1. 阿里云百炼新一代 TTS，拟人度和表达力全面优于 Qwen3-TTS，Flash 档面向低延迟交互场景调优（官方口径首包约 300ms）
2. 支持 instruction 自然语言指令（如“请用温柔亲切的语气说话”）、情感/拟声标签、多种中文方言
3. 支持语速 rate(0.5~2.0)、音调 pitch(0.5~2.0)、音量 volume(0~100) 调节，均可留空使用默认值
4. 采样率支持 8000/16000/22050/24000/44100/48000，直接对齐设备采样率，无需重采样
5. 单次请求文本上限 600 字符，超长文本会自动按句切分串行合成
6. model 可选：qwen-audio-3.1-tts-flash（推荐，低延迟）、qwen-audio-3.x-tts-plus（音质最好但吞吐仅约16字/秒，交互场景不建议）
7. voice 音色列表：https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list，注意音色必须属于当前 model，不能跨模型混用
API Key 获取：https://bailian.console.aliyun.com/?tab=model#/api-key'
WHERE `id` = 'TTS_QwenAudioTTS';

-- ============================================================
-- 3. 音色列表（模型配置页“音色管理”可视化展示）
-- ============================================================
DELETE FROM `ai_tts_voice` WHERE `tts_model_id` = 'TTS_QwenTTS';
-- Qwen3-TTS：主流音色
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0001', 'TTS_QwenTTS', '芊悦', 'Cherry', '中文普通话及多语种', NULL, NULL, NULL, NULL, 1, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0002', 'TTS_QwenTTS', '苏瑶', 'Serena', '中文普通话及多语种', NULL, NULL, NULL, NULL, 2, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0003', 'TTS_QwenTTS', '晨煦', 'Ethan', '中文普通话及多语种', NULL, NULL, NULL, NULL, 3, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0004', 'TTS_QwenTTS', '千雪', 'Chelsie', '中文普通话及多语种', NULL, NULL, NULL, NULL, 4, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0005', 'TTS_QwenTTS', '茉兔', 'Momo', '中文普通话及多语种', NULL, NULL, NULL, NULL, 5, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0006', 'TTS_QwenTTS', '十三', 'Vivian', '中文普通话及多语种', NULL, NULL, NULL, NULL, 6, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0007', 'TTS_QwenTTS', '月白', 'Moon', '中文普通话及多语种', NULL, NULL, NULL, NULL, 7, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0008', 'TTS_QwenTTS', '四月', 'Maia', '中文普通话及多语种', NULL, NULL, NULL, NULL, 8, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0009', 'TTS_QwenTTS', '凯', 'Kai', '中文普通话及多语种', NULL, NULL, NULL, NULL, 9, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0010', 'TTS_QwenTTS', '不吃鱼', 'Nofish', '中文普通话及多语种', NULL, NULL, NULL, NULL, 10, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0011', 'TTS_QwenTTS', '萌宝', 'Bella', '中文普通话及多语种', NULL, NULL, NULL, NULL, 11, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0012', 'TTS_QwenTTS', '詹妮弗', 'Jennifer', '中文普通话及多语种', NULL, NULL, NULL, NULL, 12, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0013', 'TTS_QwenTTS', '甜茶', 'Ryan', '中文普通话及多语种', NULL, NULL, NULL, NULL, 13, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0014', 'TTS_QwenTTS', '卡捷琳娜', 'Katerina', '中文普通话及多语种', NULL, NULL, NULL, NULL, 14, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0015', 'TTS_QwenTTS', '艾登', 'Aiden', '中文普通话及多语种', NULL, NULL, NULL, NULL, 15, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0016', 'TTS_QwenTTS', '乖小妹', 'Mia', '中文普通话及多语种', NULL, NULL, NULL, NULL, 16, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0017', 'TTS_QwenTTS', '沙小弥', 'Mochi', '中文普通话及多语种', NULL, NULL, NULL, NULL, 17, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0018', 'TTS_QwenTTS', '阿闻', 'Neil', '中文普通话及多语种', NULL, NULL, NULL, NULL, 18, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0019', 'TTS_QwenTTS', '邻家妹妹', 'Nini', '中文普通话及多语种', NULL, NULL, NULL, NULL, 19, NULL, NULL, NULL, NULL);
-- Qwen3-TTS：方言音色
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0020', 'TTS_QwenTTS', '上海阿珍', 'Jada', '中文方言及多语种', NULL, NULL, NULL, NULL, 20, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0021', 'TTS_QwenTTS', '北京晓东', 'Dylan', '中文方言及多语种', NULL, NULL, NULL, NULL, 21, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0022', 'TTS_QwenTTS', '陕西秦川', 'Marcus', '中文方言及多语种', NULL, NULL, NULL, NULL, 22, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0023', 'TTS_QwenTTS', '天津李彼得', 'Peter', '中文方言及多语种', NULL, NULL, NULL, NULL, 23, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0024', 'TTS_QwenTTS', '四川晴儿', 'Sunny', '中文方言及多语种', NULL, NULL, NULL, NULL, 24, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0025', 'TTS_QwenTTS', '粤语阿强', 'Rocky', '中文方言及多语种', NULL, NULL, NULL, NULL, 25, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenTTS_0026', 'TTS_QwenTTS', '粤语阿清', 'Kiki', '中文方言及多语种', NULL, NULL, NULL, NULL, 26, NULL, NULL, NULL, NULL);

DELETE FROM `ai_tts_voice` WHERE `tts_model_id` = 'TTS_QwenAudioTTS';
-- Qwen-Audio-TTS：多语种与方言音色（支持 instruction 方言指令）
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0001', 'TTS_QwenAudioTTS', '龙安欢', 'longanhuan_v3.1', '中文普通话及方言/多语种', NULL, NULL, NULL, NULL, 1, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0002', 'TTS_QwenAudioTTS', '龙安灵心', 'longanlingxin_v3.1', '中文普通话及方言/多语种', NULL, NULL, NULL, NULL, 2, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0003', 'TTS_QwenAudioTTS', '龙安风悦', 'longanfengyue_v3.1', '中文普通话及方言/多语种', NULL, NULL, NULL, NULL, 3, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0004', 'TTS_QwenAudioTTS', '许南川', 'xunanchuan_v3.1', '中文普通话及方言/多语种', NULL, NULL, NULL, NULL, 4, NULL, NULL, NULL, NULL);
-- Qwen-Audio-TTS：精品中文音色
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0005', 'TTS_QwenAudioTTS', '于小云', 'yuxiaoyun_v3.1', '中文普通话', NULL, NULL, NULL, NULL, 5, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0006', 'TTS_QwenAudioTTS', '乔小娇', 'qiaoxiaojiao_v3.1', '中文普通话', NULL, NULL, NULL, NULL, 6, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0007', 'TTS_QwenAudioTTS', '夏小晨', 'xiaxiaochen_v3.1', '中文普通话', NULL, NULL, NULL, NULL, 7, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0008', 'TTS_QwenAudioTTS', '安明远', 'anmingyuan_v3.1', '中文普通话', NULL, NULL, NULL, NULL, 8, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0009', 'TTS_QwenAudioTTS', '谢舒柔', 'xieshurou_v3.1', '中文普通话', NULL, NULL, NULL, NULL, 9, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0010', 'TTS_QwenAudioTTS', '安若柔', 'anruorou_v3.1', '中文普通话', NULL, NULL, NULL, NULL, 10, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0011', 'TTS_QwenAudioTTS', '白清岚', 'baiqinglan_v3.1', '中文普通话', NULL, NULL, NULL, NULL, 11, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0012', 'TTS_QwenAudioTTS', '叶清禾', 'yeqinghe_v3.1', '中文普通话', NULL, NULL, NULL, NULL, 12, NULL, NULL, NULL, NULL);
-- Qwen-Audio-TTS：特色音色
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0013', 'TTS_QwenAudioTTS', '龙老铁', 'longlaotie_v3.1', '中文东北话', NULL, NULL, NULL, NULL, 13, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0014', 'TTS_QwenAudioTTS', '龙可', 'longke_v3.1', '童声', NULL, NULL, NULL, NULL, 14, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0015', 'TTS_QwenAudioTTS', '安迪', 'andi_v3.1', '英文ABC口音', NULL, NULL, NULL, NULL, 15, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0016', 'TTS_QwenAudioTTS', '安语晴', 'anyuqing_v3.1', '中文普通话', NULL, NULL, NULL, NULL, 16, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0017', 'TTS_QwenAudioTTS', '龙颜', 'longyan_v3.1', '中文普通话', NULL, NULL, NULL, NULL, 17, NULL, NULL, NULL, NULL);
INSERT INTO `ai_tts_voice` VALUES ('TTS_QwenAudioTTS_0018', 'TTS_QwenAudioTTS', '龙书', 'longshu_v3.1', '中文普通话', NULL, NULL, NULL, NULL, 18, NULL, NULL, NULL, NULL);
