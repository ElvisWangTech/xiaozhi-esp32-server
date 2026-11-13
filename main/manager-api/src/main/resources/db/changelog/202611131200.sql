INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 635, 'voice_activation.role_supplement_enabled', 'false', 'boolean', 1, '首次语音配置：确认角色后是否询问补充性格'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'voice_activation.role_supplement_enabled');
