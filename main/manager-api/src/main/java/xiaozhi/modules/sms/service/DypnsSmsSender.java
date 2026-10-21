package xiaozhi.modules.sms.service;

import java.time.Duration;
import java.util.concurrent.TimeUnit;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Component;

import com.aliyun.auth.credentials.Credential;
import com.aliyun.auth.credentials.provider.StaticCredentialProvider;
import com.aliyun.sdk.service.dypnsapi20170525.AsyncClient;
import com.aliyun.sdk.service.dypnsapi20170525.models.CheckSmsVerifyCodeRequest;
import com.aliyun.sdk.service.dypnsapi20170525.models.CheckSmsVerifyCodeResponse;
import com.aliyun.sdk.service.dypnsapi20170525.models.CheckSmsVerifyCodeResponseBody;
import com.aliyun.sdk.service.dypnsapi20170525.models.SendSmsVerifyCodeRequest;
import com.aliyun.sdk.service.dypnsapi20170525.models.SendSmsVerifyCodeResponse;
import com.aliyun.sdk.service.dypnsapi20170525.models.SendSmsVerifyCodeResponseBody;

import darabonba.core.client.ClientOverrideConfiguration;
import jakarta.annotation.PreDestroy;
import lombok.extern.slf4j.Slf4j;
import xiaozhi.common.constant.Constant;
import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.modules.sys.service.SysParamsService;

/**
 * 阿里云号码认证短信验证码。登录注册仍走 dysmsapi。
 */
@Slf4j
@Component
public class DypnsSmsSender {

    private static final long REQUEST_TIMEOUT_SECONDS = 15;
    private static final String ACCESS_KEY_ID = "aliyun.dypns.access_key_id";
    private static final String ACCESS_KEY_SECRET = "aliyun.dypns.access_key_secret";
    private static final String SIGN_NAME = "aliyun.dypns.sign_name";
    private static final String TEMPLATE_CODE = "aliyun.dypns.template_code";
    private static final String SCHEME_NAME = "aliyun.dypns.scheme_name";

    private final SysParamsService sysParamsService;
    private final Object clientLock = new Object();
    private volatile AsyncClient client;

    public DypnsSmsSender(SysParamsService sysParamsService) {
        this.sysParamsService = sysParamsService;
    }

    public void sendVerifyCode(String phone) {
        assertConfigured();
        try {
            SendSmsVerifyCodeRequest.Builder request = SendSmsVerifyCodeRequest.builder()
                    .phoneNumber(phone)
                    .signName(param(SIGN_NAME))
                    .templateCode(param(TEMPLATE_CODE))
                    .templateParam("{\"code\":\"##code##\",\"min\":\"5\"}")
                    .codeLength(6L)
                    .codeType(1L)
                    .validTime(300L)
                    .interval(60L)
                    .duplicatePolicy(1L)
                    .returnVerifyCode(false);
            String schemeName = param(SCHEME_NAME);
            if (StringUtils.isNotBlank(schemeName)) {
                request.schemeName(schemeName);
            }
            SendSmsVerifyCodeResponse response = client()
                    .sendSmsVerifyCode(request.build())
                    .get(REQUEST_TIMEOUT_SECONDS, TimeUnit.SECONDS);
            SendSmsVerifyCodeResponseBody body = response == null ? null : response.getBody();
            if (body == null || !isOk(body.getCode(), body.getSuccess())) {
                String aliyunCode = body == null ? null : body.getCode();
                log.warn("Dypnsapi send rejected phone={} code={} message={}",
                        maskPhone(phone), aliyunCode, body == null ? null : body.getMessage());
                throw sendFailure(aliyunCode);
            }
            log.info("Dypnsapi SMS sent phone={} requestId={}", maskPhone(phone), body.getRequestId());
        } catch (RenException e) {
            throw e;
        } catch (Exception e) {
            log.warn("Dypnsapi send failed phone={}: {}", maskPhone(phone), e.getMessage());
            throw sendFailure(aliyunErrorCode(e));
        }
    }

    public void checkVerifyCode(String phone, String code) {
        assertConfigured();
        try {
            CheckSmsVerifyCodeRequest.Builder request = CheckSmsVerifyCodeRequest.builder()
                    .phoneNumber(phone)
                    .verifyCode(code);
            String schemeName = param(SCHEME_NAME);
            if (StringUtils.isNotBlank(schemeName)) {
                request.schemeName(schemeName);
            }
            CheckSmsVerifyCodeResponse response = client()
                    .checkSmsVerifyCode(request.build())
                    .get(REQUEST_TIMEOUT_SECONDS, TimeUnit.SECONDS);
            CheckSmsVerifyCodeResponseBody body = response == null ? null : response.getBody();
            if (body == null || !isOk(body.getCode(), body.getSuccess())) {
                log.warn("Dypnsapi check rejected phone={} code={} message={}",
                        maskPhone(phone),
                        body == null ? null : body.getCode(),
                        body == null ? null : body.getMessage());
                throw new RenException("验证码校验失败，请稍后重试");
            }
            String verifyResult = body.getModel() == null ? null : body.getModel().getVerifyResult();
            if (!"PASS".equalsIgnoreCase(verifyResult)) {
                throw new RenException(ErrorCode.SMS_CODE_ERROR);
            }
        } catch (RenException e) {
            throw e;
        } catch (Exception e) {
            log.warn("Dypnsapi check failed phone={}: {}", maskPhone(phone), e.getMessage());
            throw new RenException("验证码校验失败，请稍后重试");
        }
    }

    public boolean isMock() {
        return "true".equalsIgnoreCase(param("aliyun.dypns.mock"));
    }

    @PreDestroy
    public void close() {
        AsyncClient toClose;
        synchronized (clientLock) {
            toClose = client;
            client = null;
        }
        if (toClose != null) {
            toClose.close();
        }
    }

    private AsyncClient client() {
        AsyncClient existing = client;
        if (existing != null) {
            return existing;
        }
        synchronized (clientLock) {
            if (client == null) {
                StaticCredentialProvider provider = StaticCredentialProvider.create(Credential.builder()
                        .accessKeyId(accessKeyId())
                        .accessKeySecret(accessKeySecret())
                        .build());
                client = AsyncClient.builder()
                        .region("cn-hangzhou")
                        .credentialsProvider(provider)
                        .overrideConfiguration(ClientOverrideConfiguration.create()
                                .setEndpointOverride("dypnsapi.aliyuncs.com")
                                .setConnectTimeout(Duration.ofSeconds(10)))
                        .build();
            }
            return client;
        }
    }

    private void assertConfigured() {
        if (StringUtils.isAnyBlank(accessKeyId(), accessKeySecret(), param(SIGN_NAME), param(TEMPLATE_CODE))) {
            log.error("Aliyun Dypnsapi is not configured");
            throw new RenException("短信服务暂不可用");
        }
    }

    private String accessKeyId() {
        String value = param(ACCESS_KEY_ID);
        if (StringUtils.isBlank(value)) {
            value = param(Constant.SysMSMParam.ALIYUN_SMS_ACCESS_KEY_ID.getValue());
        }
        return value;
    }

    private String accessKeySecret() {
        String value = param(ACCESS_KEY_SECRET);
        if (StringUtils.isBlank(value)) {
            value = param(Constant.SysMSMParam.ALIYUN_SMS_ACCESS_KEY_SECRET.getValue());
        }
        return value;
    }

    private String param(String code) {
        String value = sysParamsService.getValue(code, true);
        if (value == null || value.isBlank() || "null".equalsIgnoreCase(value)) {
            return "";
        }
        return value.trim();
    }

    private static RenException sendFailure(String aliyunCode) {
        if ("FREQUENCY_FAIL".equalsIgnoreCase(aliyunCode)) {
            return new RenException("请稍后再获取验证码");
        }
        if ("BUSINESS_LIMIT_CONTROL".equalsIgnoreCase(aliyunCode)) {
            return new RenException(ErrorCode.TODAY_SMS_LIMIT_REACHED);
        }
        if ("MOBILE_NUMBER_ILLEGAL".equalsIgnoreCase(aliyunCode)) {
            return new RenException(ErrorCode.PHONE_FORMAT_ERROR);
        }
        return new RenException(ErrorCode.SMS_SEND_FAILED);
    }

    private static boolean isOk(String code, Boolean success) {
        return "OK".equalsIgnoreCase(code) || Boolean.TRUE.equals(success);
    }

    private static String aliyunErrorCode(Throwable error) {
        Throwable current = error;
        while (current != null) {
            try {
                Object value = current.getClass().getMethod("getCode").invoke(current);
                if (value != null && !value.toString().isBlank()) {
                    return value.toString();
                }
            } catch (ReflectiveOperationException ignored) {
            }
            String message = current.getMessage();
            if (message != null) {
                for (String code : new String[] {
                        "FREQUENCY_FAIL", "BUSINESS_LIMIT_CONTROL", "MOBILE_NUMBER_ILLEGAL"
                }) {
                    if (message.contains(code)) {
                        return code;
                    }
                }
            }
            current = current.getCause();
        }
        return null;
    }

    static String maskPhone(String phone) {
        if (phone == null || phone.length() < 7) {
            return "***";
        }
        return phone.substring(0, 3) + "****" + phone.substring(phone.length() - 4);
    }
}
