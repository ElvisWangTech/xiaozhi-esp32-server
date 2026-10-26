-- 语音激活：portal_lite 管理员口令（签发邀请令牌、跑授权自动化）

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 628, 'portal_lite.admin_token', '', 'string', 1, 'portal_lite 管理员口令，对应 X-Admin-Token / LITE_ADMIN_TOKEN；为空时回退 portal_lite.bind_token'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'portal_lite.admin_token');
