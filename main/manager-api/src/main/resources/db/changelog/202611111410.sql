-- -------------------------------------------------------
-- 基站定位可视化配置（2026-11-11）
-- 配置入口：智控台 -> 系统设置 -> 参数管理 -> 服务端配置 -> 基站定位，
-- 参数编码以 server.cell_location. 开头，param_type=1 才在页面展示。
-- 这些值优先于 application.yml 的 xiaozhi.cell-location.* 默认值，
-- 修改后下一次设备 OTA 上报即生效，无需重启 manager-api。
-- 基站定位只能得到城市级粗略位置，下游只使用解析出的城市名。
-- -------------------------------------------------------

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 650, 'server.cell_location.enabled', 'true', 'boolean', 1, '基站定位总开关，关闭后位置获取回退 public_ip（IP 定位）'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'server.cell_location.enabled' OR id = 650);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 651, 'server.cell_location.url', 'http://api.cellocation.com:84/cell/', 'string', 1, '单基站查询接口地址，仅支持 http 或 https，非法值回退默认地址'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'server.cell_location.url' OR id = 651);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 652, 'server.cell_location.connect_timeout_ms', '2000', 'number', 1, '接口连接超时毫秒，范围 200~30000，超时不影响 OTA 与 IP 定位'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'server.cell_location.connect_timeout_ms' OR id = 652);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 653, 'server.cell_location.read_timeout_ms', '3000', 'number', 1, '接口读取超时毫秒，范围 200~30000，超时不影响 OTA 与 IP 定位'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'server.cell_location.read_timeout_ms' OR id = 653);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 654, 'server.cell_location.cache_hours', '3', 'number', 1, '结果缓存有效期小时，范围 1~168，有效期内复用上次接口返回，不重复调用接口'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'server.cell_location.cache_hours' OR id = 654);

INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
SELECT 655, 'server.cell_location.inflight_seconds', '30', 'number', 1, '同一基站并发查询去重锁有效期秒，范围 1~300'
WHERE NOT EXISTS (SELECT 1 FROM `sys_params` WHERE param_code = 'server.cell_location.inflight_seconds' OR id = 655);
