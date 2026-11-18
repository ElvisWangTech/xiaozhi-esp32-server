-- -------------------------------------------------------
-- 天气查询统一默认配置（2026-11-18）
-- 配置入口：智控台 -> 参数管理 -> 插件 -> 天气查询，
-- 参数编码以 plugins.get_weather. 开头，param_type=1 才在页面展示。
-- 默认开启。所有未单独配置密钥的角色在下次对话时自动继承，
-- 不必再到每个角色的「编辑功能」里单独配置。
-- 角色自己填了不同于共用密钥的 API Key 时，仍使用角色配置。
-- 将 plugins.get_weather.enabled 设为 false 可关闭共用配置。
-- -------------------------------------------------------

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 662, 'plugins.get_weather.enabled', 'true', 'boolean', 1, '天气查询总开关，默认开启。设为 false 后，未单独配置密钥的角色不再加载天气查询'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'plugins.get_weather.enabled' OR id = 662);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 663, 'plugins.get_weather.api_host', 'km67cdhc9g.re.qweatherapi.com', 'string', 1, '和风天气 API Host。保存后所有未单独配置密钥的角色自动继承'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'plugins.get_weather.api_host' OR id = 663);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 664, 'plugins.get_weather.api_key', 'e8c3b5eb33e54c698e7a05012948f7ce', 'string', 1, '和风天气 API Key。这是项目共用密钥，保存后所有未单独配置密钥的角色自动继承'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'plugins.get_weather.api_key' OR id = 664);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 665, 'plugins.get_weather.default_location', '上海', 'string', 1, '未解析出设备所在城市时使用的默认查询城市'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'plugins.get_weather.default_location' OR id = 665);
