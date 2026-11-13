-- -------------------------------------------------------
-- 特斯拉授权链接短信模板（2026-11-12）
-- 语音绑定 Tesla 时，授权页 URL 不能念给用户，改发通知短信。
-- template_type 固定为 0（通知），不占用验证码模板和日限额。
-- -------------------------------------------------------

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 656, 'sms.gateway.tesla_auth_template',
       '请打开以下链接完成特斯拉授权：{{url}}',
       'string', 1,
       '特斯拉授权链接短信模板，{{url}} 替换为授权页地址；template_type 固定为 0 通知'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'sms.gateway.tesla_auth_template' OR id = 656);
