-- -------------------------------------------------------
-- 语音激活短信：阿里云号码认证 -> 通用短信平台（2026-11-06）
-- 平台只提供发送接口 POST {base_url}/api/notification/send/sms，
-- 请求体为 template_type、phone_number、message，不支持验证码校验。
-- 因此验证码改由 manager-api 本地生成（4 位）、本地校验，5 分钟内有效。
-- 配置在 系统设置 -> 参数管理 里维护，参数编码以 sms.gateway. 开头。
-- 旧的 aliyun.dypns.* 参数已不再读取，确认无回滚需求后可自行删除。
-- -------------------------------------------------------

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 630, 'sms.gateway.base_url', '', 'string', 1, '通用短信平台地址，例如 https://sms.example.com，不带路径'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'sms.gateway.base_url');

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 631, 'sms.gateway.template_type', '2', 'number', 1, '短信类型：0通知，1营销，2验证码；非法值按2处理'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'sms.gateway.template_type');

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 632, 'sms.gateway.message_template', '验证码：{{code}}', 'string', 1, '短信文案模板，{{code}} 替换为4位验证码'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'sms.gateway.message_template');

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 633, 'sms.gateway.mock', 'false', 'boolean', 1, '语音激活短信本地模拟：true 只生成验证码写日志，不发真实短信'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'sms.gateway.mock');
