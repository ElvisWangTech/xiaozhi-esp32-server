package xiaozhi.modules.device.service;

import java.io.IOException;
import java.math.BigInteger;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import org.apache.commons.lang3.StringUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.util.UriComponentsBuilder;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import lombok.extern.slf4j.Slf4j;
import xiaozhi.common.redis.RedisKeys;
import xiaozhi.common.redis.RedisUtils;
import xiaozhi.modules.device.config.CellLocationProperties;
import xiaozhi.modules.device.dto.DeviceReportReqDTO;

/**
 * 基站定位：设备 OTA 上报 cereg 时，在后台异步查询单基站接口并缓存结果。
 *
 * <p>设计约束：</p>
 * <ol>
 * <li>外部接口只在专用后台线程池中调用，专用短超时 RestTemplate；队列满直接丢弃任务，
 * 任何失败/超时都只记日志，绝不阻塞 OTA 响应，也绝不影响 public_ip 定位链路。</li>
 * <li>结果按基站标识缓存（默认 3 小时，可配置），命中即复用；设备侧另存一份带同样 TTL 的结果，
 * 保证只有连 4G 时才上报 cereg 的设备，在切回 Wi-Fi 后仍能在有效期内取到位置。</li>
 * <li>基站定位是城市级粗略结果（返回带 approximate=true 与 radius），与 IP 定位一样
 * 不可当作精确定位使用；下游只消费解析出的城市名。</li>
 * </ol>
 */
@Slf4j
@Service
public class CellLocationService {

    private final RedisUtils redisUtils;
    private final CellLocationProperties properties;
    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final ExecutorService executor;
    /** 构造时按 yml 默认值创建客户端所用的超时；参数管理改了超时才另建客户端 */
    private final int defaultConnectTimeoutMs;
    private final int defaultReadTimeoutMs;
    private volatile RestTemplate customRestTemplate;
    private volatile String customTemplateKey;

    /**
     * Spring 注入入口。本类还有一个供单测使用的 4 参构造，存在多个构造时 Spring
     * 不会自动推断注入构造，会回退去找无参构造并报 "No default constructor found"，
     * 因此这里必须显式标注 @Autowired。
     */
    @Autowired
    public CellLocationService(RedisUtils redisUtils, CellLocationProperties properties) {
        this(redisUtils, properties,
                buildRestTemplate(properties.getConnectTimeoutMs(), properties.getReadTimeoutMs()),
                createExecutor());
    }

    /** 包可见构造函数，供单元测试注入可控的 HTTP 客户端与执行器。 */
    CellLocationService(RedisUtils redisUtils, CellLocationProperties properties,
            RestTemplate restTemplate, ExecutorService executor) {
        this.redisUtils = redisUtils;
        this.properties = properties;
        this.restTemplate = restTemplate;
        this.executor = executor;
        this.defaultConnectTimeoutMs = properties.getConnectTimeoutMs();
        this.defaultReadTimeoutMs = properties.getReadTimeoutMs();
    }

    /** 启动时打印生效配置：线上排查「为什么没有基站定位」时首先要确认配置来源。 */
    @PostConstruct
    public void logEffectiveConfig() {
        CellLocationProperties.Settings s = properties.snapshot();
        log.info("基站定位生效配置（参数管理 server.cell_location.* 优先于 application.yml）: "
                        + "enabled={}, url={}, connectTimeoutMs={}, readTimeoutMs={}, "
                        + "cacheHours={} (ttl={}s), inflightSeconds={}",
                s.enabled(), s.url(), s.connectTimeoutMs(), s.readTimeoutMs(),
                s.cacheHours(), s.cacheTtlSeconds(), s.inflightSeconds());
    }

    @PreDestroy
    public void shutdown() {
        executor.shutdownNow();
    }

    /**
     * OTA 上报入口。只做参数解析与任务提交，不执行任何网络调用。
     * 没有 cereg（设备未连 4G）或参数不全时直接返回，位置获取保持走 public_ip。
     */
    public void scheduleRefresh(String macAddress, DeviceReportReqDTO deviceReport) {
        try {
            // 参数管理可即时改配置：每次 OTA 上报取一次快照，并在整条后台链路里复用
            CellLocationProperties.Settings settings = properties.snapshot();
            DeviceReportReqDTO.CeregInfo cereg = ceregOf(deviceReport);
            if (!settings.enabled() || StringUtils.isBlank(macAddress)) {
                if (cereg != null) {
                    log.info("基站定位未启用，跳过查询（回退 IP 定位）: mac={}, enabled={}, hasMac={}",
                            macAddress, settings.enabled(), StringUtils.isNotBlank(macAddress));
                }
                return;
            }
            CellKey cell = CellKey.from(deviceReport);
            if (cell == null) {
                // 未上报 cereg 是 Wi-Fi 设备的常态，不打印日志；上报了却无效才需要线索
                if (cereg != null) {
                    log.info("基站参数无效，跳过查询（回退 IP 定位）: mac={}, mcc={}, mnc={}, lac={}, ci={}",
                            macAddress, cereg.getMcc(), cereg.getMnc(), cereg.getLac(), cereg.getCi());
                }
                return;
            }
            executor.execute(() -> refreshQuietly(macAddress, cell, settings));
            log.debug("已提交基站定位后台任务: mac={}, cell={}, cacheHours={}",
                    macAddress, cell.id(), settings.cacheHours());
        } catch (Exception e) {
            // 线程池饱和/已关闭等情况一律忽略，不能反向影响 OTA 主流程
            log.warn("提交基站定位任务失败，不影响 OTA 与 IP 定位: mac={}, type={}, err={}",
                    macAddress, e.getClass().getSimpleName(), e.toString());
        }
    }

    /** 只取 cereg 四个定位参数用于诊断，绝不打印 board 里的 imei/iccid/本地 IP。 */
    private static DeviceReportReqDTO.CeregInfo ceregOf(DeviceReportReqDTO report) {
        return report == null || report.getBoard() == null ? null : report.getBoard().getCereg();
    }

    /** 读取设备最近一次基站定位结果；没有或缓存过期时返回 null，调用方回退 IP 定位。 */
    public Map<String, Object> getDeviceCellLocation(String macAddress) {
        if (StringUtils.isBlank(macAddress)) {
            return null;
        }
        try {
            Object value = redisUtils.get(RedisKeys.getDeviceCellLocationKey(normalizeMac(macAddress)));
            if (!(value instanceof String json) || StringUtils.isBlank(json)) {
                // 未上报 cereg、查询失败或 3 小时缓存已过期：下游回退 IP 定位
                log.debug("设备无基站定位结果，回退 IP 定位: mac={}", macAddress);
                return null;
            }
            JsonNode node = objectMapper.readTree(json);
            if (!node.isObject()) {
                log.debug("设备基站定位结果格式异常，回退 IP 定位: mac={}", macAddress);
                return null;
            }
            Map<String, Object> result = objectMapper.convertValue(node,
                    new TypeReference<Map<String, Object>>() {
                    });
            log.debug("读取设备基站定位结果（粗略）: mac={}, city={}, approximate={}, radius={}, updated_at={}",
                    macAddress, result.get("city"), result.get("approximate"),
                    result.get("radius"), result.get("updated_at"));
            return result;
        } catch (Exception e) {
            log.warn("读取设备基站定位结果失败，回退 IP 定位: mac={}, type={}, err={}",
                    macAddress, e.getClass().getSimpleName(), e.toString());
            return null;
        }
    }

    /** 兼容单测与直接调用：取当前参数管理快照。 */
    void refreshQuietly(String macAddress, CellKey cell) {
        refreshQuietly(macAddress, cell, properties.snapshot());
    }

    /** 后台任务主体：缓存命中直接复用，未命中才调用外部接口；全流程异常自愈。 */
    void refreshQuietly(String macAddress, CellKey cell, CellLocationProperties.Settings settings) {
        String cellId = cell.id();
        String cacheKey = RedisKeys.getCellLocationCacheKey(cellId);
        String inflightKey = RedisKeys.getCellLocationInflightKey(cellId);
        String deviceKey = RedisKeys.getDeviceCellLocationKey(normalizeMac(macAddress));
        long ttl = settings.cacheTtlSeconds();
        long startedAt = System.currentTimeMillis();
        try {
            Object cached = redisUtils.get(cacheKey);
            if (cached instanceof String json && StringUtils.isNotBlank(json)) {
                // 命中缓存：只刷新设备侧结果的有效期，不再打外部接口
                redisUtils.set(deviceKey, json, ttl);
                log.info("基站定位命中缓存，本次不调用外部接口: mac={}, cell={}, ttl={}s",
                        macAddress, cellId, ttl);
                return;
            }
            if (!redisUtils.setIfAbsent(inflightKey, "1", settings.inflightSeconds())) {
                log.debug("同一基站查询已在进行中，跳过本次（并发去重）: mac={}, cell={}", macAddress, cellId);
                return;
            }
            try {
                log.info("开始查询基站定位（后台异步，不阻塞 OTA）: mac={}, cell={}", macAddress, cellId);
                Map<String, Object> result = queryCell(cell, settings);
                if (result == null) {
                    // 失败/超时/无结果都不写缓存，设备侧旧值继续有效；没有旧值时下游回退 IP
                    log.info("本次未取到基站位置，位置获取回退 IP 定位: mac={}, cell={}, elapsed={}ms",
                            macAddress, cellId, System.currentTimeMillis() - startedAt);
                    return;
                }
                String json = objectMapper.writeValueAsString(result);
                redisUtils.set(cacheKey, json, ttl);
                redisUtils.set(deviceKey, json, ttl);
                log.info("基站定位成功（粗略/城市级）: mac={}, cell={}, city={}, radius={}, "
                                + "ttl={}s, elapsed={}ms",
                        macAddress, cellId, result.get("city"), result.get("radius"), ttl,
                        System.currentTimeMillis() - startedAt);
            } finally {
                redisUtils.delete(inflightKey);
            }
        } catch (Exception e) {
            log.warn("基站定位刷新失败，不影响 OTA 与 IP 定位: mac={}, cell={}, type={}, "
                            + "elapsed={}ms, err={}",
                    macAddress, cellId, e.getClass().getSimpleName(),
                    System.currentTimeMillis() - startedAt, e.toString());
        }
    }

    /** 兼容单测与真实接口测试：取当前参数管理快照。 */
    Map<String, Object> queryCell(CellKey cell) {
        return queryCell(cell, properties.snapshot());
    }

    /** 调用单基站查询接口；任何失败都返回 null。 */
    Map<String, Object> queryCell(CellKey cell, CellLocationProperties.Settings settings) {
        String url = UriComponentsBuilder.fromUriString(settings.url())
                .queryParam("mcc", cell.mcc())
                .queryParam("mnc", cell.mnc())
                .queryParam("lac", cell.lac())
                .queryParam("ci", cell.ci())
                .queryParam("output", "json")
                .build()
                .encode()
                .toUriString();
        // 用 byte[] 自行交给 Jackson 解析，避免 StringHttpMessageConverter 默认 ISO-8859-1 让中文地址乱码
        long startedAt = System.currentTimeMillis();
        ResponseEntity<byte[]> response;
        try {
            response = restTemplateFor(settings).getForEntity(url, byte[].class);
        } catch (Exception e) {
            // 连接/读取超时是最常见的降级原因，单独记录耗时与异常类型
            log.warn("基站定位接口调用失败（超时/连接错误，回退 IP 定位）: cell={}, type={}, "
                            + "elapsed={}ms, err={}",
                    cell.id(), e.getClass().getSimpleName(),
                    System.currentTimeMillis() - startedAt, e.toString());
            return null;
        }
        long elapsed = System.currentTimeMillis() - startedAt;
        if (response == null) {
            log.info("基站定位接口无响应，忽略: cell={}, elapsed={}ms", cell.id(), elapsed);
            return null;
        }
        byte[] body = response.getBody();
        if (body == null || body.length == 0) {
            log.info("基站定位响应为空，忽略: cell={}, status={}, elapsed={}ms",
                    cell.id(), response.getStatusCode().value(), elapsed);
            return null;
        }
        log.debug("基站定位接口响应: cell={}, status={}, bytes={}, elapsed={}ms",
                cell.id(), response.getStatusCode().value(), body.length, elapsed);
        JsonNode root;
        try {
            root = objectMapper.readTree(body);
        } catch (IOException e) {
            log.warn("基站定位响应解析失败，忽略: cell={}, bytes={}, elapsed={}ms, err={}",
                    cell.id(), body.length, elapsed, e.toString());
            return null;
        }
        int errcode = root.path("errcode").asInt(-1);
        if (errcode != 0) {
            // 10000=参数错误，10001=无查询结果
            log.info("基站定位无结果，回退 IP 定位: cell={}, errcode={}, elapsed={}ms",
                    cell.id(), errcode, elapsed);
            return null;
        }
        String lat = root.path("lat").asText("").trim();
        String lon = root.path("lon").asText("").trim();
        if (lat.isEmpty() || lon.isEmpty()) {
            log.info("基站定位结果缺少坐标，忽略: cell={}, elapsed={}ms", cell.id(), elapsed);
            return null;
        }
        String address = root.path("address").asText("").trim();
        String city = extractCity(address);
        if (city == null) {
            // 日志只记地址长度，不打印详细地址、经纬度
            log.info("基站地址未解析出城市，下游将回退 IP 定位: cell={}, address_length={}",
                    cell.id(), address.length());
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("source", "cell");
        result.put("approximate", true);
        result.put("city", city);
        result.put("address", address);
        result.put("lat", lat);
        result.put("lon", lon);
        result.put("radius", root.path("radius").asText("").trim());
        result.put("cell", cell.toMap());
        result.put("updated_at", Instant.now().toString());
        return result;
    }

    /**
     * 从地址描述中取城市级名称，与 IP 定位返回的 city 对齐，供天气与提示词使用。
     * 只取「省/自治区 + 市」前缀，不把街道级描述或经纬度当成精确位置。
     */
    static String extractCity(String address) {
        if (StringUtils.isBlank(address)) {
            return null;
        }
        String head = address.split(";", 2)[0].trim();
        int cityEnd = head.indexOf('市');
        if (cityEnd < 0) {
            return null;
        }
        int start = 0;
        int provinceEnd = head.lastIndexOf('省', cityEnd);
        if (provinceEnd >= 0) {
            start = provinceEnd + 1;
        }
        int autonomousEnd = head.lastIndexOf("自治区", cityEnd);
        if (autonomousEnd >= 0) {
            start = Math.max(start, autonomousEnd + "自治区".length());
        }
        String city = head.substring(start, cityEnd + 1).trim();
        return city.length() >= 2 && city.length() <= 12 ? city : null;
    }

    private static String normalizeMac(String macAddress) {
        return macAddress.trim().toLowerCase();
    }

    /**
     * 参数管理里的超时与构造时（yml 默认值）一致时沿用注入的客户端，
     * 单测因此仍可注入 mock；管理员改了超时才按新值懒加载另一个客户端。
     */
    private RestTemplate restTemplateFor(CellLocationProperties.Settings settings) {
        if (settings.connectTimeoutMs() == defaultConnectTimeoutMs
                && settings.readTimeoutMs() == defaultReadTimeoutMs) {
            return restTemplate;
        }
        String key = settings.connectTimeoutMs() + ":" + settings.readTimeoutMs();
        RestTemplate current = customRestTemplate;
        if (current == null || !key.equals(customTemplateKey)) {
            synchronized (this) {
                if (customRestTemplate == null || !key.equals(customTemplateKey)) {
                    customRestTemplate = buildRestTemplate(
                            settings.connectTimeoutMs(), settings.readTimeoutMs());
                    customTemplateKey = key;
                }
                current = customRestTemplate;
            }
        }
        return current;
    }

    private static RestTemplate buildRestTemplate(int connectTimeoutMs, int readTimeoutMs) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofMillis(Math.max(200, connectTimeoutMs)));
        factory.setReadTimeout(Duration.ofMillis(Math.max(200, readTimeoutMs)));
        return new RestTemplate(factory);
    }

    private static ExecutorService createExecutor() {
        AtomicInteger seq = new AtomicInteger();
        ThreadPoolExecutor pool = new ThreadPoolExecutor(
                1, 2, 60L, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(64),
                runnable -> {
                    Thread thread = new Thread(runnable, "cell-location-" + seq.incrementAndGet());
                    thread.setDaemon(true);
                    return thread;
                },
                // 队列满静默丢弃：定位是增强能力，绝不反向拖慢 OTA 请求线程
                new ThreadPoolExecutor.DiscardPolicy());
        pool.allowCoreThreadTimeOut(true);
        return pool;
    }

    /** 单基站标识；cellocation 要求 mnc/lac/ci 均为十进制整数。 */
    record CellKey(String mcc, String mnc, String lac, String ci) {

        String id() {
            return mcc + ":" + mnc + ":" + lac + ":" + ci;
        }

        Map<String, Object> toMap() {
            Map<String, Object> map = new LinkedHashMap<>();
            map.put("mcc", mcc);
            map.put("mnc", mnc);
            map.put("lac", lac);
            map.put("ci", ci);
            return map;
        }

        static CellKey from(DeviceReportReqDTO report) {
            if (report == null || report.getBoard() == null) {
                return null;
            }
            DeviceReportReqDTO.CeregInfo cereg = report.getBoard().getCereg();
            if (cereg == null) {
                return null;
            }
            String mcc = decimal(cereg.getMcc());
            String mnc = decimal(cereg.getMnc());
            String lac = decimal(cereg.getLac());
            String ci = decimal(cereg.getCi());
            if (mcc == null || mnc == null || lac == null || ci == null) {
                return null;
            }
            // 未注册/占位值不查，避免无意义的外部请求（mnc=0 是中国移动，合法）
            if ("0".equals(mcc) || "0".equals(lac) || "0".equals(ci)) {
                return null;
            }
            return new CellKey(mcc, mnc, lac, ci);
        }

        /** 只接受十进制数字字符串，去掉前导零（mnc "00" -> "0"）。 */
        static String decimal(String raw) {
            if (StringUtils.isBlank(raw)) {
                return null;
            }
            String value = raw.trim();
            if (!value.matches("\\d{1,20}")) {
                return null;
            }
            return new BigInteger(value).toString();
        }
    }
}
