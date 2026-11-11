package xiaozhi.modules.device.config;

import org.apache.commons.lang3.StringUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import lombok.Data;
import xiaozhi.modules.sys.service.SysParamsService;

/**
 * 基站定位配置。
 *
 * <p>配置来源优先级：智控台「参数管理」中 {@code server.cell_location.*}（修改后下一次 OTA 上报即生效）
 * &gt; application.yml 中 {@code xiaozhi.cell-location.*}（默认值、无参数管理时的兜底）。
 * 参数值都经过类型与范围校验，非法值静默回退默认值，绝不影响 OTA 与 IP 定位链路。</p>
 *
 * <p>基站定位与 IP 定位都只能得到城市级的粗略位置，两者都不可当作精确定位使用。
 * 服务端只把解析出的城市用于天气与提示词，不把经纬度用于精确判断。</p>
 */
@Data
@Component
@ConfigurationProperties(prefix = "xiaozhi.cell-location")
public class CellLocationProperties {

    public static final String PARAM_PREFIX = "server.cell_location.";
    public static final String PARAM_ENABLED = PARAM_PREFIX + "enabled";
    public static final String PARAM_URL = PARAM_PREFIX + "url";
    public static final String PARAM_CONNECT_TIMEOUT_MS = PARAM_PREFIX + "connect_timeout_ms";
    public static final String PARAM_READ_TIMEOUT_MS = PARAM_PREFIX + "read_timeout_ms";
    public static final String PARAM_CACHE_HOURS = PARAM_PREFIX + "cache_hours";
    public static final String PARAM_INFLIGHT_SECONDS = PARAM_PREFIX + "inflight_seconds";

    /** 总开关；关闭后完全不触发外部查询，位置获取回退到 public_ip */
    private boolean enabled = true;

    /** 单基站查询接口 */
    private String url = "http://api.cellocation.com:84/cell/";

    /** 连接超时（毫秒） */
    private int connectTimeoutMs = 2000;

    /** 读取超时（毫秒） */
    private int readTimeoutMs = 3000;

    /** 结果缓存有效期（小时），有效期内复用上一次接口返回的数据 */
    private int cacheHours = 3;

    /** 同一基站并发查询去重锁的有效期（秒） */
    private int inflightSeconds = 30;

    /** 参数管理服务；纯单测直接 new 时为 null，snapshot() 自动回退 yml 默认值。 */
    @Autowired(required = false)
    private SysParamsService sysParamsService;

    public long getCacheTtlSeconds() {
        return Math.max(1, cacheHours) * 3600L;
    }

    /**
     * 读取本次请求生效的配置快照。
     * SysParamsService 自身带 Redis 缓存，参数管理页保存/修改时会刷新该缓存，
     * 所以无需重启 manager-api；每次读取都在本地完成，不会引入外部网络调用。
     */
    public Settings snapshot() {
        return new Settings(
                booleanParam(PARAM_ENABLED, enabled),
                urlParam(),
                numberParam(PARAM_CONNECT_TIMEOUT_MS, connectTimeoutMs, 200, 30000),
                numberParam(PARAM_READ_TIMEOUT_MS, readTimeoutMs, 200, 30000),
                numberParam(PARAM_CACHE_HOURS, cacheHours, 1, 168),
                numberParam(PARAM_INFLIGHT_SECONDS, inflightSeconds, 1, 300));
    }

    /** 参数不存在、为字符串 "null" 或参数服务异常时返回 null，由调用方回退 yml 默认值。 */
    private String raw(String paramCode) {
        if (sysParamsService == null) {
            return null;
        }
        try {
            String value = sysParamsService.getValue(paramCode, true);
            if (StringUtils.isBlank(value) || "null".equalsIgnoreCase(value.trim())) {
                return null;
            }
            return value.trim();
        } catch (Exception e) {
            // 参数服务不可用（如 Redis 抖动）不能影响 OTA 与定位链路
            return null;
        }
    }

    private boolean booleanParam(String paramCode, boolean defaultValue) {
        String value = raw(paramCode);
        if (value == null) {
            return defaultValue;
        }
        if ("true".equalsIgnoreCase(value)) {
            return true;
        }
        if ("false".equalsIgnoreCase(value)) {
            return false;
        }
        return defaultValue;
    }

    private int numberParam(String paramCode, int defaultValue, int min, int max) {
        String value = raw(paramCode);
        if (value == null) {
            return defaultValue;
        }
        try {
            return Math.max(min, Math.min(max, Integer.parseInt(value)));
        } catch (NumberFormatException e) {
            return defaultValue;
        }
    }

    /** 只接受 http(s) 地址，避免管理员误填后外部调用持续抛异常。 */
    private String urlParam() {
        String value = raw(PARAM_URL);
        return value != null && (value.startsWith("http://") || value.startsWith("https://"))
                ? value : url;
    }

    /** 一次 OTA 上报内使用的不可变配置，避免后台任务执行到一半时配置被改动。 */
    public record Settings(boolean enabled, String url, int connectTimeoutMs, int readTimeoutMs,
                           int cacheHours, int inflightSeconds) {
        public long cacheTtlSeconds() {
            return Math.max(1, cacheHours) * 3600L;
        }
    }
}
