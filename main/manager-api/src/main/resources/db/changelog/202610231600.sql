-- 语音激活第三阶段：portal_lite 技能绑定参数

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 626, 'portal_lite.base_url', '', 'string', 1, 'portal_lite 服务地址，例如 http://127.0.0.1:5005'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'portal_lite.base_url');

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 627, 'portal_lite.bind_token', '', 'string', 1, 'portal_lite 设备绑定口令，对应 X-Bind-Token / LITE_BIND_TOKEN'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'portal_lite.bind_token');
