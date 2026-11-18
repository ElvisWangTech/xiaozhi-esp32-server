-- -------------------------------------------------------
-- 联网搜索默认开启（2026-11-18）
-- 已执行过 202611181600 的库补上总开关，默认 true。
-- -------------------------------------------------------

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 661, 'plugins.web_search.enabled', 'true', 'boolean', 1, '联网搜索总开关，默认开启。设为 false 后，未单独配置密钥的角色不再加载联网搜索'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'plugins.web_search.enabled' OR id = 661);

UPDATE `sys_params`
SET `remark` = '联网搜索 API Key。秘塔为 mk- 前缀，Tavily 为 tvly- 前缀，Serply 为控制台密钥。默认开启，请换成可用密钥'
WHERE `param_code` = 'plugins.web_search.api_key'
  AND `remark` = '联网搜索 API Key。秘塔为 mk- 前缀，Tavily 为 tvly- 前缀，Serply 为控制台密钥。保持 mk-XXXX 时不启用';
