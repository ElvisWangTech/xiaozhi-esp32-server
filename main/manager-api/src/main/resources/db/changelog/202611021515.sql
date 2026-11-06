-- Qwen TTS api_url 二次清理：
-- 202611021410 的清理生效后，用户又在可视化页面重新保存了非原生端点
-- （百炼专属实例 Base URL，如 https://ws-xxx.cn-beijing.maas.aliyuncs.com/api/v1）。
-- 该地址不含 DashScope 原生路径特征，直接请求会 404；运行时虽会自动回退默认端点，
-- 但配置里留一个无效值易造成误解，这里再次移除（条件同上轮：仅移除非法值，合法自定义端点不受影响）。

UPDATE `ai_model_config`
SET `config_json` = JSON_REMOVE(`config_json`, '$.api_url')
WHERE `id` IN ('TTS_QwenTTS', 'TTS_QwenAudioTTS')
  AND JSON_EXTRACT(`config_json`, '$.api_url') IS NOT NULL
  AND JSON_UNQUOTE(JSON_EXTRACT(`config_json`, '$.api_url')) NOT LIKE '%/api/v1/services/%';
