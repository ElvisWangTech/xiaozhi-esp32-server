-- -------------------------------------------------------
-- 通用短信平台补充：请求头 Client-Id（2026-11-06）
-- 平台发送接口要求每个请求携带 Client-Id 请求头，值由平台分配。
-- 参数 sms.gateway.client_id 为必填项，为空时接口报「短信服务暂不可用」。
-- -------------------------------------------------------

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 634, 'sms.gateway.client_id', '', 'string', 1, '短信平台客户端标识，必填，请求时以 Client-Id 请求头携带'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'sms.gateway.client_id');
