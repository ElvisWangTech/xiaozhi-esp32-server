package xiaozhi.modules.device.service;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.ExecutorService;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.mockito.ArgumentCaptor;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestTemplate;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import xiaozhi.common.redis.RedisUtils;
import xiaozhi.modules.device.config.CellLocationProperties;
import xiaozhi.modules.device.dto.DeviceReportReqDTO;
import xiaozhi.modules.sys.service.SysParamsService;

@DisplayName("基站定位服务：异步、缓存、超时与回退")
class CellLocationServiceTest {

    private static final String MAC = "00:11:22:33:44:55";
    private static final String CELL_ID = "460:0:6334:29434881";
    private static final String CACHE_KEY = "device:cell_location:cache:" + CELL_ID;
    private static final String DEVICE_KEY = "device:cell_location:" + MAC;
    private static final String INFLIGHT_KEY = "device:cell_location:inflight:" + CELL_ID;
    private static final long TTL = 3 * 3600L;

    private RedisUtils redisUtils;
    private RestTemplate restTemplate;
    private ExecutorService executor;
    private CellLocationService service;

    @BeforeEach
    void setUp() {
        redisUtils = mock(RedisUtils.class);
        restTemplate = mock(RestTemplate.class);
        executor = mock(ExecutorService.class);
        service = new CellLocationService(redisUtils, new CellLocationProperties(), restTemplate, executor);
    }

    @Test
    @DisplayName("cereg 十进制参数规范化，mnc 00 -> 0，去掉 lac 前导零")
    void cellParamsAreNormalized() {
        CellLocationService.CellKey cell = CellLocationService.CellKey
                .from(report("460", "00", "06334", "29434881"));
        assertNotNull(cell);
        assertEquals("460", cell.mcc());
        assertEquals("0", cell.mnc());
        assertEquals("6334", cell.lac());
        assertEquals("29434881", cell.ci());
        assertEquals(CELL_ID, cell.id());
    }

    @Test
    @DisplayName("未上报 cereg、字段缺失、占位值或非十进制时不触发查询")
    void invalidCellIsIgnored() {
        assertNull(CellLocationService.CellKey.from(new DeviceReportReqDTO()));
        assertNull(CellLocationService.CellKey.from(report("460", null, "6334", "29434881")));
        assertNull(CellLocationService.CellKey.from(report("460", "00", "0", "0")));
        assertNull(CellLocationService.CellKey.from(report("460", "AB", "6334", "29434881")));
    }

    @Test
    @DisplayName("只提取城市级名称，不带街道描述")
    void extractCityReturnsCityLevelOnly() {
        assertEquals("上海市", CellLocationService.extractCity(
                "上海市浦东新区张江镇徐油车;中环路华夏高架路出口与中环路出口路口西南363米"));
        assertEquals("深圳市", CellLocationService.extractCity("广东省深圳市南山区科技园;某路口"));
        assertEquals("乌鲁木齐市", CellLocationService.extractCity(
                "新疆维吾尔自治区乌鲁木齐市天山区;某路口"));
        assertNull(CellLocationService.extractCity(""));
        assertNull(CellLocationService.extractCity("中环路华夏高架路出口"));
    }

    @Test
    @DisplayName("OTA 上报只提交后台任务，调用线程不打外部接口")
    void scheduleRefreshDoesNotBlockCaller() {
        service.scheduleRefresh(MAC, report("460", "00", "6334", "29434881"));

        verify(executor).execute(any(Runnable.class));
        verifyNoInteractions(restTemplate);
    }

    @Test
    @DisplayName("设备未连 4G 时不提交任务，保留 public_ip 定位")
    void scheduleRefreshSkipsNonCellularDevices() {
        service.scheduleRefresh(MAC, new DeviceReportReqDTO());
        verify(executor, never()).execute(any(Runnable.class));
    }

    @Test
    @DisplayName("缓存命中直接复用，不调用外部接口，并刷新设备结果有效期")
    void cacheHitSkipsExternalApi() {
        String cachedJson = "{\"city\":\"上海市\"}";
        when(redisUtils.get(CACHE_KEY)).thenReturn(cachedJson);

        service.refreshQuietly(MAC, cell());

        verify(redisUtils).set(DEVICE_KEY, cachedJson, TTL);
        verifyNoInteractions(restTemplate);
    }

    @Test
    @DisplayName("同一基站已有查询在途时跳过，避免重复调用")
    void inflightLockBlocksDuplicateQuery() {
        when(redisUtils.get(CACHE_KEY)).thenReturn(null);
        when(redisUtils.setIfAbsent(anyString(), any(), anyLong())).thenReturn(false);

        service.refreshQuietly(MAC, cell());

        verifyNoInteractions(restTemplate);
    }

    @Test
    @DisplayName("接口超时不影响流程：不抛异常、不写结果缓存")
    void timeoutDoesNotBreakFlow() {
        when(redisUtils.get(anyString())).thenReturn(null);
        when(redisUtils.setIfAbsent(anyString(), any(), anyLong())).thenReturn(true);
        when(restTemplate.getForEntity(anyString(), eq(byte[].class)))
                .thenThrow(new ResourceAccessException("Read timed out"));

        assertDoesNotThrow(() -> service.refreshQuietly(MAC, cell()));

        verify(redisUtils, never()).set(eq(CACHE_KEY), any(), anyLong());
        verify(redisUtils, never()).set(eq(DEVICE_KEY), any(), anyLong());
        verify(redisUtils).delete(INFLIGHT_KEY);
    }

    @Test
    @DisplayName("errcode 非 0（无查询结果）时不写缓存")
    void errcodeNonZeroIsIgnored() {
        when(redisUtils.get(anyString())).thenReturn(null);
        when(redisUtils.setIfAbsent(anyString(), any(), anyLong())).thenReturn(true);
        when(restTemplate.getForEntity(anyString(), eq(byte[].class)))
                .thenReturn(ResponseEntity.ok("{\"errcode\":10001}".getBytes(StandardCharsets.UTF_8)));

        assertDoesNotThrow(() -> service.refreshQuietly(MAC, cell()));

        verify(redisUtils, never()).set(eq(CACHE_KEY), any(), anyLong());
        verify(redisUtils, never()).set(eq(DEVICE_KEY), any(), anyLong());
    }

    @Test
    @DisplayName("成功时写基站缓存与设备结果，结果标记为粗略定位")
    void successStoresApproximateCityLevelResult() throws Exception {
        String apiJson = "{\"errcode\": 0, \"lat\": \"31.186959\", \"lon\": \"121.615436\", "
                + "\"radius\": \"1414\", \"address\": \"上海市浦东新区张江镇徐油车;中环路出口西南363米\"}";
        when(redisUtils.get(anyString())).thenReturn(null);
        when(redisUtils.setIfAbsent(anyString(), any(), anyLong())).thenReturn(true);
        when(restTemplate.getForEntity(anyString(), eq(byte[].class)))
                .thenReturn(ResponseEntity.ok(apiJson.getBytes(StandardCharsets.UTF_8)));

        service.refreshQuietly(MAC, cell());

        ArgumentCaptor<Object> payload = ArgumentCaptor.forClass(Object.class);
        verify(redisUtils, times(2)).set(anyString(), payload.capture(), eq(TTL));
        JsonNode stored = new ObjectMapper().readTree(String.valueOf(payload.getAllValues().get(0)));
        assertEquals("上海市", stored.path("city").asText());
        assertTrue(stored.path("approximate").asBoolean());
        assertEquals("cell", stored.path("source").asText());
        assertEquals("1414", stored.path("radius").asText());

        ArgumentCaptor<String> url = ArgumentCaptor.forClass(String.class);
        verify(restTemplate).getForEntity(url.capture(), eq(byte[].class));
        assertTrue(url.getValue().contains("mcc=460"));
        assertTrue(url.getValue().contains("mnc=0"));
        assertTrue(url.getValue().contains("lac=6334"));
        assertTrue(url.getValue().contains("ci=29434881"));
        assertTrue(url.getValue().contains("output=json"));
    }

    @Test
    @DisplayName("读取设备结果：正常解析，异常内容静默返回 null")
    void getDeviceCellLocationFallsBackToNull() {
        when(redisUtils.get(DEVICE_KEY))
                .thenReturn("{\"city\":\"上海市\",\"approximate\":true}")
                .thenReturn("not-json");

        assertEquals("上海市", service.getDeviceCellLocation(MAC).get("city"));
        assertNull(service.getDeviceCellLocation(MAC));
    }

    @Test
    @DisplayName("真实接口连通性：示例基站应解析出上海市")
    @EnabledIfEnvironmentVariable(named = "RUN_CELLOCATION_LIVE", matches = "1")
    void liveApiReturnsCityLevelLocation() {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofSeconds(2));
        factory.setReadTimeout(Duration.ofSeconds(5));
        CellLocationService live = new CellLocationService(
                mock(RedisUtils.class), new CellLocationProperties(),
                new RestTemplate(factory), mock(ExecutorService.class));

        Map<String, Object> result = live.queryCell(cell());

        assertNotNull(result, "真实接口未返回可用结果（网络不通时该项可跳过）");
        assertEquals("上海市", result.get("city"));
        assertEquals(Boolean.TRUE, result.get("approximate"));
    }

    @Test
    @DisplayName("参数管理关闭开关后不提交后台任务，位置回退 IP 定位")
    void paramManagementCanDisableCellLocation() {
        CellLocationProperties props = new CellLocationProperties();
        SysParamsService params = mock(SysParamsService.class);
        when(params.getValue(eq(CellLocationProperties.PARAM_ENABLED), eq(true))).thenReturn("false");
        props.setSysParamsService(params);
        CellLocationService disabled = new CellLocationService(
                redisUtils, props, restTemplate, executor);

        disabled.scheduleRefresh(MAC, report("460", "00", "6334", "29434881"));

        verify(executor, never()).execute(any(Runnable.class));
        verifyNoInteractions(restTemplate);
    }

    @Test
    @DisplayName("参数管理的缓存时长与接口地址即时生效，无需重启")
    void paramManagementOverridesCacheHoursAndUrl() throws Exception {
        String apiJson = "{\"errcode\": 0, \"lat\": \"31.186959\", \"lon\": \"121.615436\", "
                + "\"radius\": \"1414\", \"address\": \"上海市浦东新区张江镇;某路口\"}";
        when(redisUtils.get(anyString())).thenReturn(null);
        when(redisUtils.setIfAbsent(anyString(), any(), anyLong())).thenReturn(true);
        when(restTemplate.getForEntity(anyString(), eq(byte[].class)))
                .thenReturn(ResponseEntity.ok(apiJson.getBytes(StandardCharsets.UTF_8)));

        CellLocationProperties props = new CellLocationProperties();
        SysParamsService params = mock(SysParamsService.class);
        when(params.getValue(eq(CellLocationProperties.PARAM_CACHE_HOURS), eq(true))).thenReturn("6");
        when(params.getValue(eq(CellLocationProperties.PARAM_URL), eq(true)))
                .thenReturn("https://cell.example.com/cell/");
        props.setSysParamsService(params);
        CellLocationService customized = new CellLocationService(
                redisUtils, props, restTemplate, executor);

        customized.refreshQuietly(MAC, cell());

        verify(redisUtils, times(2)).set(anyString(), any(), eq(6 * 3600L));
        ArgumentCaptor<String> url = ArgumentCaptor.forClass(String.class);
        verify(restTemplate).getForEntity(url.capture(), eq(byte[].class));
        assertTrue(url.getValue().startsWith("https://cell.example.com/cell/"));
    }

    private CellLocationService.CellKey cell() {
        return CellLocationService.CellKey.from(report("460", "00", "6334", "29434881"));
    }

    private DeviceReportReqDTO report(String mcc, String mnc, String lac, String ci) {
        DeviceReportReqDTO.CeregInfo cereg = new DeviceReportReqDTO.CeregInfo();
        cereg.setStat(1);
        cereg.setAct(7);
        cereg.setMcc(mcc);
        cereg.setMnc(mnc);
        cereg.setLac(lac);
        cereg.setCi(ci);

        DeviceReportReqDTO.BoardInfo board = new DeviceReportReqDTO.BoardInfo();
        board.setCereg(cereg);

        DeviceReportReqDTO report = new DeviceReportReqDTO();
        report.setBoard(board);
        return report;
    }
}
