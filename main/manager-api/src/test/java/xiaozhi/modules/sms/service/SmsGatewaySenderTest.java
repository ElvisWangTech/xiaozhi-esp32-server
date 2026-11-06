package xiaozhi.modules.sms.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.HashMap;
import java.util.Map;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;

import com.fasterxml.jackson.databind.ObjectMapper;

import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.common.utils.TestMessages;
import xiaozhi.modules.sys.service.SysParamsService;

class SmsGatewaySenderTest {

    private static final String BASE_URL = "https://sms.example.com";
    private static final String CLIENT_ID = "xiaozhi-manager";
    private static final String DEFAULT_MESSAGE = "您的验证码为4321，五分钟内有效，请勿泄露给他人";

    private final RestTemplate restTemplate = mock(RestTemplate.class);
    private final Map<String, String> params = new HashMap<>();

    @BeforeAll
    static void installMessages() {
        TestMessages.install();
    }

    @AfterAll
    static void restoreMessages() {
        TestMessages.uninstall();
    }

    private SmsGatewaySender sender() {
        SysParamsService sysParamsService = mock(SysParamsService.class);
        when(sysParamsService.getValue(anyString(), any()))
                .thenAnswer(invocation -> params.get(invocation.getArgument(0, String.class)));
        return new SmsGatewaySender(sysParamsService, restTemplate, new ObjectMapper());
    }

    @Test
    void postsTemplateTypePhoneAndRenderedMessage() {
        params.put(SmsGatewaySender.BASE_URL, BASE_URL + "/");
        params.put(SmsGatewaySender.CLIENT_ID, CLIENT_ID);
        params.put(SmsGatewaySender.TEMPLATE_TYPE, "2");
        params.put(SmsGatewaySender.MESSAGE_TEMPLATE, "您的验证码是{{code}}，五分钟内有效");
        ResponseEntity<String> ok = ResponseEntity.ok("{\"success\":true}");
        when(restTemplate.exchange(eq(BASE_URL + "/api/notification/send/sms"), eq(HttpMethod.POST),
                any(HttpEntity.class), eq(String.class))).thenReturn(ok);

        sender().sendVerifyCode("13800138000", "1234");

        ArgumentCaptor<HttpEntity> entity = ArgumentCaptor.forClass(HttpEntity.class);
        org.mockito.Mockito.verify(restTemplate).exchange(eq(BASE_URL + "/api/notification/send/sms"),
                eq(HttpMethod.POST), entity.capture(), eq(String.class));
        @SuppressWarnings("unchecked")
        Map<String, Object> body = (Map<String, Object>) entity.getValue().getBody();
        assertThat(body).containsEntry("template_type", 2)
                .containsEntry("phone_number", "13800138000")
                .containsEntry("message", "您的验证码是1234，五分钟内有效");
        assertThat(entity.getValue().getHeaders().getFirst("Client-Id")).isEqualTo(CLIENT_ID);
    }

    @Test
    void messageTemplateDefaultsToVerificationCode() {
        assertThat(sender().renderMessage("4321")).isEqualTo(DEFAULT_MESSAGE);
    }

    @Test
    void templateWithoutPlaceholderFallsBackSoTheUserStillGetsTheCode() {
        params.put(SmsGatewaySender.MESSAGE_TEMPLATE, "登录验证码");
        assertThat(sender().renderMessage("4321")).isEqualTo(DEFAULT_MESSAGE);
    }

    @Test
    void templateTypeDefaultsToVerificationCode() {
        assertEquals(2, sender().templateType());
        params.put(SmsGatewaySender.TEMPLATE_TYPE, "1");
        assertEquals(1, sender().templateType());
        params.put(SmsGatewaySender.TEMPLATE_TYPE, "9");
        assertEquals(2, sender().templateType());
        params.put(SmsGatewaySender.TEMPLATE_TYPE, "abc");
        assertEquals(2, sender().templateType());
    }

    @Test
    void missingBaseUrlIsReportedAsUnavailable() {
        RenException error = assertThrows(RenException.class,
                () -> sender().sendVerifyCode("13800138000", "1234"));
        assertThat(error.getMsg()).contains("暂不可用");
    }

    @Test
    void missingClientIdIsReportedAsUnavailable() {
        params.put(SmsGatewaySender.BASE_URL, BASE_URL);
        RenException error = assertThrows(RenException.class,
                () -> sender().sendVerifyCode("13800138000", "1234"));
        assertThat(error.getMsg()).contains("暂不可用");
        org.mockito.Mockito.verifyNoInteractions(restTemplate);
    }

    @Test
    void httpErrorBecomesSendFailed() {
        params.put(SmsGatewaySender.BASE_URL, BASE_URL);
        params.put(SmsGatewaySender.CLIENT_ID, CLIENT_ID);
        when(restTemplate.exchange(anyString(), eq(HttpMethod.POST), any(HttpEntity.class), eq(String.class)))
                .thenThrow(new HttpClientErrorException(HttpStatus.BAD_REQUEST));

        RenException error = assertThrows(RenException.class,
                () -> sender().sendVerifyCode("13800138000", "1234"));
        assertEquals(ErrorCode.SMS_SEND_FAILED, error.getCode());
    }

    @Test
    void bodyDeclaringFailureIsNotTreatedAsSent() {
        params.put(SmsGatewaySender.BASE_URL, BASE_URL);
        params.put(SmsGatewaySender.CLIENT_ID, CLIENT_ID);
        when(restTemplate.exchange(anyString(), eq(HttpMethod.POST), any(HttpEntity.class), eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"success\":false,\"message\":\"balance\"}"));

        RenException error = assertThrows(RenException.class,
                () -> sender().sendVerifyCode("13800138000", "1234"));
        assertEquals(ErrorCode.SMS_SEND_FAILED, error.getCode());
    }

    @Test
    void mockFlagFollowsTheLegacyKeyWhenTheNewOneIsEmpty() {
        params.put("aliyun.dypns.mock", "true");
        assertThat(sender().isMock()).isTrue();
    }

    @Test
    void mockFlagPrefersTheGatewayKey() {
        params.put("aliyun.dypns.mock", "true");
        params.put(SmsGatewaySender.MOCK, "false");
        assertThat(sender().isMock()).isFalse();
    }
}
