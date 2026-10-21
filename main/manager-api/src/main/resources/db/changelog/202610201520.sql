-- 语音激活：设备绑定手机号与引导状态。
-- MySQL 的 DDL 会自动提交。上次这条变更在插入参数时失败后，列可能已经存在，这里按列和参数编码补齐。

SET @add_phone = (
    SELECT IF(
        COUNT(*) = 0,
        'ALTER TABLE `ai_device` ADD COLUMN `phone` varchar(20) NULL COMMENT ''绑定手机号'' AFTER `mac_address`',
        'SELECT 1'
    )
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ai_device'
      AND COLUMN_NAME = 'phone'
);
PREPARE stmt FROM @add_phone;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @add_onboarding = (
    SELECT IF(
        COUNT(*) = 0,
        'ALTER TABLE `ai_device` ADD COLUMN `onboarding_status` varchar(32) NULL COMMENT ''语音激活引导：nickname待设置昵称，done已完成'' AFTER `phone`',
        'SELECT 1'
    )
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ai_device'
      AND COLUMN_NAME = 'onboarding_status'
);
PREPARE stmt FROM @add_onboarding;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 620, 'aliyun.dypns.access_key_id', '', 'string', 1, '号码认证AccessKey，空则回退aliyun.sms.access_key_id'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'aliyun.dypns.access_key_id');

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 621, 'aliyun.dypns.access_key_secret', '', 'string', 1, '号码认证AccessKeySecret，空则回退aliyun.sms.access_key_secret'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'aliyun.dypns.access_key_secret');

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 622, 'aliyun.dypns.sign_name', '', 'string', 1, '号码认证短信签名'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'aliyun.dypns.sign_name');

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 623, 'aliyun.dypns.template_code', '', 'string', 1, '号码认证短信模板，变量使用##code##'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'aliyun.dypns.template_code');

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 624, 'aliyun.dypns.scheme_name', '', 'string', 1, '号码认证方案名称，可空'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'aliyun.dypns.scheme_name');

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 625, 'aliyun.dypns.mock', 'false', 'boolean', 1, '语音激活短信是否使用本地模拟验证码'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'aliyun.dypns.mock');
