package xiaozhi.modules.sms.service;

import java.util.LinkedHashMap;
import java.util.Map;

import org.apache.commons.lang3.StringUtils;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.client.RestTemplate;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import lombok.extern.slf4j.Slf4j;
import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.modules.sys.service.SysParamsService;

/**
 * 通用短信平台。
 *
 * <p>平台只提供发送接口 {@code POST {base_url}/api/notification/send/sms}，
 * 请求体为 {@code template_type}、{@code phone_number}、{@code message}，
 * 请求头必须携带 {@code Client-Id}（参数 {@code sms.gateway.client_id}，必填）。
 * 验证码的生成、有效期与校验由 {@link VoiceSmsCodeService} 在本地完成，
 * 平台不参与校验。</p>
 */
@Slf4j
@Component
public class SmsGatewaySender {

    /** 短信平台根地址，例如 https://sms.example.com */
    static final String BASE_URL = "sms.gateway.base_url";
    /** 请求头 Client-Id，平台分配的客户端标识，必填 */
    static final String CLIENT_ID = "sms.gateway.client_id";
    /** 短信类型：0 通知，1 营销，2 验证码 */
    static final String TEMPLATE_TYPE = "sms.gateway.template_type";
    /** 短信文案模板，{{code}} 会替换成验证码 */
    static final String MESSAGE_TEMPLATE = "sms.gateway.message_template";
    /** 特斯拉授权链接短信模板，{{url}} 会替换成授权页地址 */
    static final String TESLA_AUTH_TEMPLATE = "sms.gateway.tesla_auth_template";
    /** 本地模拟开关：只生成验证码并写入日志，不调用平台 */
    static final String MOCK = "sms.gateway.mock";
    /** 迁移期兜底：旧号码认证的模拟开关 */
    private static final String LEGACY_MOCK = "aliyun.dypns.mock";

    private static final String SEND_PATH = "/api/notification/send/sms";
    private static final String CLIENT_ID_HEADER = "Client-Id";
    private static final String CODE_PLACEHOLDER = "{{code}}";
    private static final String URL_PLACEHOLDER = "{{url}}";
    private static final String DEFAULT_MESSAGE_TEMPLATE =
            "您的验证码为" + CODE_PLACEHOLDER + "，五分钟内有效，请勿泄露给他人";
    private static final String DEFAULT_TESLA_AUTH_TEMPLATE =
            "请打开以下链接完成特斯拉授权：" + URL_PLACEHOLDER;
    private static final int DEFAULT_TEMPLATE_TYPE = 2;
    private static final int NOTIFY_TEMPLATE_TYPE = 0;
    private static final int MIN_TEMPLATE_TYPE = 0;
    private static final int MAX_TEMPLATE_TYPE = 2;

    private final SysParamsService sysParamsService;
    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    public SmsGatewaySender(SysParamsService sysParamsService, RestTemplate restTemplate,
            ObjectMapper objectMapper) {
        this.sysParamsService = sysParamsService;
        this.restTemplate = restTemplate;
        this.objectMapper = objectMapper;
    }

    /**
     * 发送一条带验证码的短信。失败抛出 {@link RenException}，调用方据此保留重试机会。
     */
    public void sendVerifyCode(String phone, String code) {
        sendSms(phone, renderMessage(code), templateType());
    }

    /**
     * 把 Tesla 授权页链接发到用户手机。走通知短信，不占用验证码模板和日限额。
     * mock 模式只写日志，不调平台。
     */
    public void sendTeslaAuthLink(String phone, String authUrl) {
        if (StringUtils.isBlank(phone)) {
            throw new RenException("设备未记录手机号，无法发送特斯拉授权短信");
        }
        if (StringUtils.isBlank(authUrl)) {
            throw new RenException(ErrorCode.SMS_SEND_FAILED);
        }
        String message = renderTeslaAuthMessage(authUrl);
        if (isMock()) {
            log.info("特斯拉授权短信 mock phone={} url={}", maskPhone(phone), authUrl);
            return;
        }
        sendSms(phone, message, NOTIFY_TEMPLATE_TYPE);
    }

    private void sendSms(String phone, String message, int templateType) {
        String baseUrl = param(BASE_URL);
        if (StringUtils.isBlank(baseUrl)) {
            log.error("通用短信平台未配置 {}", BASE_URL);
            throw new RenException("短信服务暂不可用");
        }
        String clientId = param(CLIENT_ID);
        if (StringUtils.isBlank(clientId)) {
            log.error("通用短信平台未配置 {}", CLIENT_ID);
            throw new RenException("短信服务暂不可用");
        }
        String url = StringUtils.removeEnd(baseUrl, "/") + SEND_PATH;
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("template_type", templateType);
        body.put("phone_number", phone);
        body.put("message", message);
        try {
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set(CLIENT_ID_HEADER, clientId);
            ResponseEntity<String> response = restTemplate.exchange(
                    url, HttpMethod.POST, new HttpEntity<>(body, headers), String.class);
            String payload = response.getBody();
            if (!response.getStatusCode().is2xxSuccessful() || rejected(payload)) {
                log.warn("短信平台拒绝发送 phone={} status={} body={}",
                        maskPhone(phone), response.getStatusCode().value(), abbreviate(payload));
                throw new RenException(ErrorCode.SMS_SEND_FAILED);
            }
            log.info("短信平台已发送 phone={}", maskPhone(phone));
        } catch (RenException e) {
            throw e;
        } catch (RestClientResponseException e) {
            log.warn("短信平台拒绝发送 phone={} status={} body={}",
                    maskPhone(phone), e.getStatusCode().value(), abbreviate(e.getResponseBodyAsString()));
            throw new RenException(ErrorCode.SMS_SEND_FAILED);
        } catch (Exception e) {
            log.warn("短信平台调用失败 phone={}: {}", maskPhone(phone), e.getMessage());
            throw new RenException(ErrorCode.SMS_SEND_FAILED);
        }
    }

    public boolean isMock() {
        String mock = param(MOCK);
        if (StringUtils.isBlank(mock)) {
            mock = param(LEGACY_MOCK);
        }
        return "true".equalsIgnoreCase(mock);
    }

    /** 短信类型，配置非法时回落到验证码。 */
    int templateType() {
        String raw = param(TEMPLATE_TYPE);
        if (StringUtils.isBlank(raw)) {
            return DEFAULT_TEMPLATE_TYPE;
        }
        try {
            int value = Integer.parseInt(raw);
            if (value < MIN_TEMPLATE_TYPE || value > MAX_TEMPLATE_TYPE) {
                log.warn("短信类型配置越界 {}，按验证码发送", raw);
                return DEFAULT_TEMPLATE_TYPE;
            }
            return value;
        } catch (NumberFormatException e) {
            log.warn("短信类型配置非法 {}，按验证码发送", raw);
            return DEFAULT_TEMPLATE_TYPE;
        }
    }

    /**
     * 渲染短信文案。模板里没有占位符时用户会收到一条没有验证码的短信，
     * 这种情况退回默认文案，避免把登录流程锁死。
     */
    String renderMessage(String code) {
        String template = param(MESSAGE_TEMPLATE);
        if (StringUtils.isBlank(template)) {
            template = DEFAULT_MESSAGE_TEMPLATE;
        }
        if (!template.contains(CODE_PLACEHOLDER)) {
            log.warn("短信模板缺少 {} 占位符，改用默认模板", CODE_PLACEHOLDER);
            template = DEFAULT_MESSAGE_TEMPLATE;
        }
        return template.replace(CODE_PLACEHOLDER, code);
    }

    /**
     * 渲染特斯拉授权短信。模板缺少 {@code {{url}}} 时回落默认文案，避免发出没有链接的短信。
     */
    String renderTeslaAuthMessage(String authUrl) {
        String template = param(TESLA_AUTH_TEMPLATE);
        if (StringUtils.isBlank(template)) {
            template = DEFAULT_TESLA_AUTH_TEMPLATE;
        }
        if (!template.contains(URL_PLACEHOLDER)) {
            log.warn("特斯拉授权短信模板缺少 {} 占位符，改用默认模板", URL_PLACEHOLDER);
            template = DEFAULT_TESLA_AUTH_TEMPLATE;
        }
        return template.replace(URL_PLACEHOLDER, authUrl);
    }

    /**
     * 平台可能用 HTTP 200 携带业务失败。只有明确写着 success/ok 为 false，或带非空 error，
     * 才判失败，不去猜各家的 code 取值。
     */
    private boolean rejected(String payload) {
        if (StringUtils.isBlank(payload)) {
            return false;
        }
        try {
            JsonNode root = objectMapper.readTree(payload);
            if (root == null || !root.isObject()) {
                return false;
            }
            if (root.has("success") && root.get("success").isBoolean() && !root.get("success").asBoolean()) {
                return true;
            }
            if (root.has("ok") && root.get("ok").isBoolean() && !root.get("ok").asBoolean()) {
                return true;
            }
            return root.hasNonNull("error") && StringUtils.isNotBlank(root.get("error").asText());
        } catch (Exception ignored) {
            // 非 JSON 响应（例如纯文本 ok）以 HTTP 状态码为准
            return false;
        }
    }

    private String param(String code) {
        String value = sysParamsService.getValue(code, true);
        if (value == null || value.isBlank() || "null".equalsIgnoreCase(value)) {
            return "";
        }
        return value.trim();
    }

    public static String maskPhone(String phone) {
        if (phone == null || phone.length() < 7) {
            return "***";
        }
        return phone.substring(0, 3) + "****" + phone.substring(phone.length() - 4);
    }

    private static String abbreviate(String text) {
        if (text == null) {
            return "";
        }
        String oneLine = text.replace('\n', ' ').replace('\r', ' ').trim();
        return oneLine.length() <= 200 ? oneLine : oneLine.substring(0, 200);
    }
}
