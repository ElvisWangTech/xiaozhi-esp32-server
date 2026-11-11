package xiaozhi.modules.device.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.HashMap;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import xiaozhi.modules.device.config.CellLocationProperties;
import xiaozhi.modules.sys.service.SysParamsService;

@DisplayName("基站定位参数管理配置：覆盖、校验与兜底")
class CellLocationPropertiesTest {

    @Test
    @DisplayName("未接入参数管理时回退 application.yml 默认值")
    void fallsBackToYamlDefaults() {
        CellLocationProperties.Settings s = new CellLocationProperties().snapshot();

        assertTrue(s.enabled());
        assertEquals("http://api.cellocation.com:84/cell/", s.url());
        assertEquals(2000, s.connectTimeoutMs());
        assertEquals(3000, s.readTimeoutMs());
        assertEquals(3, s.cacheHours());
        assertEquals(30, s.inflightSeconds());
        assertEquals(3 * 3600L, s.cacheTtlSeconds());
    }

    @Test
    @DisplayName("参数管理的值优先于 yml 默认值，且修改即时生效")
    void paramManagementOverridesDefaults() {
        Map<String, String> values = new HashMap<>();
        values.put(CellLocationProperties.PARAM_ENABLED, "false");
        values.put(CellLocationProperties.PARAM_URL, "https://cell.example.com/cell/");
        values.put(CellLocationProperties.PARAM_CONNECT_TIMEOUT_MS, "1200");
        values.put(CellLocationProperties.PARAM_READ_TIMEOUT_MS, "2500");
        values.put(CellLocationProperties.PARAM_CACHE_HOURS, "6");
        values.put(CellLocationProperties.PARAM_INFLIGHT_SECONDS, "15");

        CellLocationProperties.Settings s = propsWith(values).snapshot();

        assertFalse(s.enabled());
        assertEquals("https://cell.example.com/cell/", s.url());
        assertEquals(1200, s.connectTimeoutMs());
        assertEquals(2500, s.readTimeoutMs());
        assertEquals(6, s.cacheHours());
        assertEquals(6 * 3600L, s.cacheTtlSeconds());
        assertEquals(15, s.inflightSeconds());
    }

    @Test
    @DisplayName("空值、字符串 null、非法类型一律回退默认值，不影响链路")
    void invalidValuesFallBackToDefaults() {
        Map<String, String> values = new HashMap<>();
        values.put(CellLocationProperties.PARAM_ENABLED, "yes");
        values.put(CellLocationProperties.PARAM_URL, "ftp://cell.example.com");
        values.put(CellLocationProperties.PARAM_CONNECT_TIMEOUT_MS, "abc");
        values.put(CellLocationProperties.PARAM_CACHE_HOURS, "null");
        values.put(CellLocationProperties.PARAM_INFLIGHT_SECONDS, "   ");

        CellLocationProperties.Settings s = propsWith(values).snapshot();

        assertTrue(s.enabled());
        assertEquals("http://api.cellocation.com:84/cell/", s.url());
        assertEquals(2000, s.connectTimeoutMs());
        assertEquals(3, s.cacheHours());
        assertEquals(30, s.inflightSeconds());
    }

    @Test
    @DisplayName("越界数值被裁剪到安全范围，避免缓存永久有效或零超时")
    void outOfRangeValuesAreClamped() {
        Map<String, String> values = new HashMap<>();
        values.put(CellLocationProperties.PARAM_CONNECT_TIMEOUT_MS, "0");
        values.put(CellLocationProperties.PARAM_READ_TIMEOUT_MS, "999999");
        values.put(CellLocationProperties.PARAM_CACHE_HOURS, "9999");
        values.put(CellLocationProperties.PARAM_INFLIGHT_SECONDS, "-1");

        CellLocationProperties.Settings s = propsWith(values).snapshot();

        assertEquals(200, s.connectTimeoutMs());
        assertEquals(30000, s.readTimeoutMs());
        assertEquals(168, s.cacheHours());
        assertEquals(1, s.inflightSeconds());
    }

    @Test
    @DisplayName("参数服务异常时静默回退默认值")
    void paramServiceFailureNeverBreaksLocation() {
        SysParamsService params = mock(SysParamsService.class);
        when(params.getValue(anyString(), eq(true))).thenThrow(new RuntimeException("Redis 不可用"));
        CellLocationProperties props = new CellLocationProperties();
        props.setSysParamsService(params);

        CellLocationProperties.Settings s = props.snapshot();

        assertTrue(s.enabled());
        assertEquals(3 * 3600L, s.cacheTtlSeconds());
    }

    private CellLocationProperties propsWith(Map<String, String> values) {
        SysParamsService params = mock(SysParamsService.class);
        when(params.getValue(anyString(), eq(true)))
                .thenAnswer(invocation -> values.get(invocation.getArgument(0, String.class)));
        CellLocationProperties props = new CellLocationProperties();
        props.setSysParamsService(params);
        return props;
    }
}
