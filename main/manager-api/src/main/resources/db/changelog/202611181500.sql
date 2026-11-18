-- -------------------------------------------------------
-- 和风天气默认配置更新（2026-11-18）
-- 插件字段默认值在 202505292203 从 sys_params 拷进
-- ai_model_provider.fields 后，plugins.* 参数已被删除。
-- 只覆盖仍是旧默认值的字段，智控台里改过的配置不动。
-- -------------------------------------------------------

UPDATE `ai_model_provider`
SET `fields` = JSON_SET(`fields`, '$[0].default', 'e8c3b5eb33e54c698e7a05012948f7ce')
WHERE `id` = 'SYSTEM_PLUGIN_WEATHER'
  AND JSON_UNQUOTE(JSON_EXTRACT(`fields`, '$[0].key')) = 'api_key'
  AND JSON_UNQUOTE(JSON_EXTRACT(`fields`, '$[0].default')) = 'a861d0d5e7bf4ee1a83d9a9e4f96d4da';

UPDATE `ai_model_provider`
SET `fields` = JSON_SET(`fields`, '$[1].default', '上海')
WHERE `id` = 'SYSTEM_PLUGIN_WEATHER'
  AND JSON_UNQUOTE(JSON_EXTRACT(`fields`, '$[1].key')) = 'default_location'
  AND JSON_UNQUOTE(JSON_EXTRACT(`fields`, '$[1].default')) = '广州';

UPDATE `ai_model_provider`
SET `fields` = JSON_SET(`fields`, '$[2].default', 'km67cdhc9g.re.qweatherapi.com')
WHERE `id` = 'SYSTEM_PLUGIN_WEATHER'
  AND JSON_UNQUOTE(JSON_EXTRACT(`fields`, '$[2].key')) = 'api_host'
  AND JSON_UNQUOTE(JSON_EXTRACT(`fields`, '$[2].default')) = 'mj7p3y7naa.re.qweatherapi.com';

UPDATE `ai_agent_plugin_mapping`
SET `param_info` = JSON_SET(`param_info`, '$.api_key', 'e8c3b5eb33e54c698e7a05012948f7ce')
WHERE `plugin_id` = 'SYSTEM_PLUGIN_WEATHER'
  AND JSON_UNQUOTE(JSON_EXTRACT(`param_info`, '$.api_key')) = 'a861d0d5e7bf4ee1a83d9a9e4f96d4da';

UPDATE `ai_agent_plugin_mapping`
SET `param_info` = JSON_SET(`param_info`, '$.default_location', '上海')
WHERE `plugin_id` = 'SYSTEM_PLUGIN_WEATHER'
  AND JSON_UNQUOTE(JSON_EXTRACT(`param_info`, '$.default_location')) = '广州';

UPDATE `ai_agent_plugin_mapping`
SET `param_info` = JSON_SET(`param_info`, '$.api_host', 'km67cdhc9g.re.qweatherapi.com')
WHERE `plugin_id` = 'SYSTEM_PLUGIN_WEATHER'
  AND JSON_UNQUOTE(JSON_EXTRACT(`param_info`, '$.api_host')) = 'mj7p3y7naa.re.qweatherapi.com';
