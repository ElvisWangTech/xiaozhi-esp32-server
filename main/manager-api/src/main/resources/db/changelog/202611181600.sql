-- -------------------------------------------------------
-- 联网搜索统一默认配置（2026-11-18）
-- 配置入口：智控台 -> 参数管理 -> 插件 -> 联网搜索，
-- 参数编码以 plugins.web_search. 开头，param_type=1 才在页面展示。
-- 填入可用 API Key 后，所有角色在下次对话时自动继承，
-- 不必再到每个角色的「编辑功能」里单独配置。
-- 角色自己填了可用 API Key 时，仍使用角色配置。
-- api_key 保持占位符 mk-XXXX 时不启用联网搜索。
-- -------------------------------------------------------

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 657, 'plugins.web_search.provider', 'metaso', 'string', 1, '联网搜索源，可选 metaso、tavily、serply。保存后所有未单独配置的角色自动继承'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'plugins.web_search.provider' OR id = 657);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 658, 'plugins.web_search.api_key', 'mk-XXXX', 'string', 1, '联网搜索 API Key。秘塔为 mk- 前缀，Tavily 为 tvly- 前缀，Serply 为控制台密钥。保持 mk-XXXX 时不启用'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'plugins.web_search.api_key' OR id = 658);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 659, 'plugins.web_search.max_results', '5', 'number', 1, '联网搜索返回条数，小于 1 时按 5 处理'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'plugins.web_search.max_results' OR id = 659);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 660, 'plugins.web_search.description', '联网搜索工具。当用户明确需要联网搜索问题时使用此工具。', 'string', 1, '联网搜索工具描述，用于告诉模型何时调用。留空时使用这句默认描述'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'plugins.web_search.description' OR id = 660);
