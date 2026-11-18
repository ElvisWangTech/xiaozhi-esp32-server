package xiaozhi.modules.config.service.impl;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNotSame;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import xiaozhi.common.redis.RedisKeys;
import xiaozhi.common.redis.RedisUtils;
import xiaozhi.common.utils.JsonUtils;
import xiaozhi.modules.agent.dao.AgentVoicePrintDao;
import xiaozhi.modules.agent.service.AgentContextProviderService;
import xiaozhi.modules.agent.service.AgentMcpAccessPointService;
import xiaozhi.modules.agent.service.AgentPluginMappingService;
import xiaozhi.modules.agent.service.AgentService;
import xiaozhi.modules.agent.service.AgentTemplateService;
import xiaozhi.modules.correctword.service.CorrectWordFileService;
import xiaozhi.modules.device.service.DeviceService;
import xiaozhi.modules.model.service.ModelConfigService;
import xiaozhi.modules.sys.dto.SysParamsDTO;
import xiaozhi.modules.sys.service.SysParamsService;
import xiaozhi.modules.timbre.service.TimbreService;
import xiaozhi.modules.voiceclone.service.VoiceCloneService;

class ConfigServiceImplTest {

    @Test
    void cachedServerConfigIsCheckedAsAStringKeyedMapWithoutChangingNestedValues() {
        RedisUtils redisUtils = mock(RedisUtils.class);
        Map<String, Object> nested = new HashMap<>();
        nested.put("enabled", true);
        Map<String, Object> cached = new HashMap<>();
        cached.put("features", nested);
        when(redisUtils.get(RedisKeys.getServerConfigKey())).thenReturn(cached);

        ConfigServiceImpl service = newService(mock(SysParamsService.class), redisUtils);

        Map<String, Object> result = service.getConfig(true);

        assertNotSame(cached, result);
        assertSame(nested, result.get("features"));
        assertEquals(true, ((Map<?, ?>) result.get("features")).get("enabled"));
    }

    @Test
    void nestedSystemParametersStillShareAndPopulateTheSameConfigBranch() {
        SysParamsService sysParamsService = mock(SysParamsService.class);
        SysParamsDTO enabled = parameter("server.features.enabled", "true", "boolean");
        SysParamsDTO labels = parameter("server.features.labels", "first;second", "array");
        when(sysParamsService.list(anyMap())).thenReturn(List.of(enabled, labels));
        ConfigServiceImpl service = newService(sysParamsService, mock(RedisUtils.class));
        Map<String, Object> config = new HashMap<>();

        Object returned = ReflectionTestUtils.invokeMethod(service, "buildConfig", config);

        assertSame(config, returned);
        Map<?, ?> server = assertInstanceOf(Map.class, config.get("server"));
        Map<?, ?> features = assertInstanceOf(Map.class, server.get("features"));
        assertEquals(true, features.get("enabled"));
        assertEquals(List.of("first", "second"), features.get("labels"));
    }

    @Test
    void webSearchSystemDefaultIsAddedWhenRoleHasNoOwnKey() {
        ConfigServiceImpl service = webSearchService("Tavily", "tvly-real", "", "");
        Map<String, Object> plugins = new HashMap<>();

        ReflectionTestUtils.invokeMethod(service, "applyWebSearchDefault", plugins);

        Map<String, Object> config = JsonUtils.toStringObjectMap(plugins.get("web_search"));
        assertEquals("tavily", config.get("provider"));
        assertEquals("tvly-real", config.get("api_key"));
        assertEquals(5, ((Number) config.get("max_results")).intValue());
        assertEquals("联网搜索工具。当用户明确需要联网搜索问题时使用此工具。", config.get("description"));
    }

    @Test
    void webSearchRoleKeyOverridesSystemDefault() {
        ConfigServiceImpl service = webSearchService("metaso", "mk-system", "5", "系统描述");
        String roleConfig = "{\"provider\":\"serply\",\"api_key\":\"role-key\",\"max_results\":\"2\"}";
        Map<String, Object> plugins = new HashMap<>();
        plugins.put("web_search", roleConfig);

        ReflectionTestUtils.invokeMethod(service, "applyWebSearchDefault", plugins);

        assertSame(roleConfig, plugins.get("web_search"));
    }

    @Test
    void webSearchPlaceholderOnRoleFallsBackToSystemDefault() {
        ConfigServiceImpl service = webSearchService("serply", "serply-key", "3", "查一下");
        Map<String, Object> plugins = new HashMap<>();
        plugins.put("web_search", "{\"provider\":\"metaso\",\"api_key\":\"mk-XXXX\",\"max_results\":\"5\"}");

        ReflectionTestUtils.invokeMethod(service, "applyWebSearchDefault", plugins);

        Map<String, Object> config = JsonUtils.toStringObjectMap(plugins.get("web_search"));
        assertEquals("serply", config.get("provider"));
        assertEquals("serply-key", config.get("api_key"));
        assertEquals(3, ((Number) config.get("max_results")).intValue());
        assertEquals("查一下", config.get("description"));
    }

    @Test
    void webSearchIsEnabledByDefaultEvenWhenApiKeyIsStillPlaceholder() {
        Map<String, Object> plugins = new HashMap<>();
        ReflectionTestUtils.invokeMethod(
                webSearchService("metaso", "mk-XXXX", "5", "描述"), "applyWebSearchDefault", plugins);

        Map<String, Object> config = JsonUtils.toStringObjectMap(plugins.get("web_search"));
        assertEquals("metaso", config.get("provider"));
        assertEquals("mk-XXXX", config.get("api_key"));
    }

    @Test
    void webSearchStaysOffWhenDisabledOrProviderIsUnknown() {
        SysParamsService disabledParams = mock(SysParamsService.class);
        when(disabledParams.getValue(eq("plugins.web_search.enabled"), eq(true))).thenReturn("false");
        when(disabledParams.getValue(eq("plugins.web_search.provider"), eq(true))).thenReturn("metaso");
        when(disabledParams.getValue(eq("plugins.web_search.api_key"), eq(true))).thenReturn("mk-real");
        when(disabledParams.getValue(eq("plugins.web_search.max_results"), eq(true))).thenReturn("5");
        when(disabledParams.getValue(eq("plugins.web_search.description"), eq(true))).thenReturn("描述");
        Map<String, Object> disabled = new HashMap<>();
        ReflectionTestUtils.invokeMethod(
                newService(disabledParams, mock(RedisUtils.class)), "applyWebSearchDefault", disabled);
        assertFalse(disabled.containsKey("web_search"));

        Map<String, Object> unknownProvider = new HashMap<>();
        ReflectionTestUtils.invokeMethod(
                webSearchService("bing", "real-key", "5", "描述"), "applyWebSearchDefault", unknownProvider);
        assertFalse(unknownProvider.containsKey("web_search"));
    }

    @Test
    void weatherSystemDefaultIsAddedWhenRoleHasNoPlugin() {
        ConfigServiceImpl service = weatherService("true", "host.example", "system-key", "北京");
        Map<String, Object> plugins = new HashMap<>();

        ReflectionTestUtils.invokeMethod(service, "applyWeatherDefault", plugins);

        Map<String, Object> config = JsonUtils.toStringObjectMap(plugins.get("get_weather"));
        assertEquals("host.example", config.get("api_host"));
        assertEquals("system-key", config.get("api_key"));
        assertEquals("北京", config.get("default_location"));
    }

    @Test
    void weatherSharedKeyOnRoleUsesSystemDefault() {
        ConfigServiceImpl service = weatherService("true", "km67cdhc9g.re.qweatherapi.com",
                "e8c3b5eb33e54c698e7a05012948f7ce", "上海");
        Map<String, Object> plugins = new HashMap<>();
        plugins.put("get_weather",
                "{\"api_host\":\"old.example\",\"api_key\":\"a861d0d5e7bf4ee1a83d9a9e4f96d4da\",\"default_location\":\"广州\"}");

        ReflectionTestUtils.invokeMethod(service, "applyWeatherDefault", plugins);

        Map<String, Object> config = JsonUtils.toStringObjectMap(plugins.get("get_weather"));
        assertEquals("km67cdhc9g.re.qweatherapi.com", config.get("api_host"));
        assertEquals("e8c3b5eb33e54c698e7a05012948f7ce", config.get("api_key"));
        assertEquals("上海", config.get("default_location"));
    }

    @Test
    void weatherCustomRoleKeyOverridesSystemDefault() {
        ConfigServiceImpl service = weatherService("true", "host.example", "system-key", "上海");
        String roleConfig = "{\"api_host\":\"role.example\",\"api_key\":\"role-key\",\"default_location\":\"杭州\"}";
        Map<String, Object> plugins = new HashMap<>();
        plugins.put("get_weather", roleConfig);

        ReflectionTestUtils.invokeMethod(service, "applyWeatherDefault", plugins);

        assertSame(roleConfig, plugins.get("get_weather"));
    }

    @Test
    void weatherSwitchOffRemovesSharedConfigAndKeepsCustomKey() {
        ConfigServiceImpl disabled = weatherService("false", "host.example", "system-key", "上海");
        Map<String, Object> shared = new HashMap<>();
        shared.put("get_weather", "{\"api_key\":\"e8c3b5eb33e54c698e7a05012948f7ce\"}");
        ReflectionTestUtils.invokeMethod(disabled, "applyWeatherDefault", shared);
        assertFalse(shared.containsKey("get_weather"));

        Map<String, Object> missing = new HashMap<>();
        ReflectionTestUtils.invokeMethod(disabled, "applyWeatherDefault", missing);
        assertFalse(missing.containsKey("get_weather"));

        String roleConfig = "{\"api_key\":\"role-key\"}";
        Map<String, Object> custom = new HashMap<>();
        custom.put("get_weather", roleConfig);
        ReflectionTestUtils.invokeMethod(disabled, "applyWeatherDefault", custom);
        assertSame(roleConfig, custom.get("get_weather"));
    }

    private static ConfigServiceImpl weatherService(String enabled, String apiHost, String apiKey, String location) {
        SysParamsService sysParamsService = mock(SysParamsService.class);
        when(sysParamsService.getValue(eq("plugins.get_weather.enabled"), eq(true))).thenReturn(enabled);
        when(sysParamsService.getValue(eq("plugins.get_weather.api_host"), eq(true))).thenReturn(apiHost);
        when(sysParamsService.getValue(eq("plugins.get_weather.api_key"), eq(true))).thenReturn(apiKey);
        when(sysParamsService.getValue(eq("plugins.get_weather.default_location"), eq(true))).thenReturn(location);
        return newService(sysParamsService, mock(RedisUtils.class));
    }

    private static ConfigServiceImpl webSearchService(String provider, String apiKey, String maxResults,
            String description) {
        SysParamsService sysParamsService = mock(SysParamsService.class);
        when(sysParamsService.getValue(eq("plugins.web_search.provider"), eq(true))).thenReturn(provider);
        when(sysParamsService.getValue(eq("plugins.web_search.api_key"), eq(true))).thenReturn(apiKey);
        when(sysParamsService.getValue(eq("plugins.web_search.max_results"), eq(true))).thenReturn(maxResults);
        when(sysParamsService.getValue(eq("plugins.web_search.description"), eq(true))).thenReturn(description);
        return newService(sysParamsService, mock(RedisUtils.class));
    }

    private static SysParamsDTO parameter(String code, String value, String type) {
        SysParamsDTO parameter = new SysParamsDTO();
        parameter.setParamCode(code);
        parameter.setParamValue(value);
        parameter.setValueType(type);
        return parameter;
    }

    private static ConfigServiceImpl newService(SysParamsService sysParamsService, RedisUtils redisUtils) {
        return new ConfigServiceImpl(
                sysParamsService,
                mock(DeviceService.class),
                mock(ModelConfigService.class),
                mock(AgentService.class),
                mock(AgentTemplateService.class),
                redisUtils,
                mock(TimbreService.class),
                mock(AgentPluginMappingService.class),
                mock(AgentMcpAccessPointService.class),
                mock(AgentContextProviderService.class),
                mock(VoiceCloneService.class),
                mock(AgentVoicePrintDao.class),
                mock(CorrectWordFileService.class));
    }
}
