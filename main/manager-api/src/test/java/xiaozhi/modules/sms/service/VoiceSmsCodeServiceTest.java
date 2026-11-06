package xiaozhi.modules.sms.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDate;
import java.util.HexFormat;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.common.redis.RedisKeys;
import xiaozhi.common.redis.RedisUtils;
import xiaozhi.common.utils.TestMessages;

class VoiceSmsCodeServiceTest {

    private static final String PHONE = "13800138000";

    private SmsGatewaySender sender;
    private RedisUtils redisUtils;
    private VoiceSmsCodeService service;

    @BeforeAll
    static void installMessages() {
        TestMessages.install();
    }

    @AfterAll
    static void restoreMessages() {
        TestMessages.uninstall();
    }

    @BeforeEach
    void setUp() {
        sender = mock(SmsGatewaySender.class);
        redisUtils = mock(RedisUtils.class);
        service = new VoiceSmsCodeService(sender, redisUtils);
    }

    @Test
    void sendUsesFourDigitCodeAndCachesOnlyItsHash() {
        String code = sendAndCaptureCode();

        assertThat(code).matches("\\d{4}");
        ArgumentCaptor<Object> cached = ArgumentCaptor.forClass(Object.class);
        verify(redisUtils).set(eq(RedisKeys.getVoiceSmsCodeKey(PHONE)), cached.capture(), eq(300L));
        assertThat(String.valueOf(cached.getValue())).isNotEqualTo(code);
        verify(redisUtils).set(eq(RedisKeys.getVoiceSmsLastSendKey(PHONE)), any(), eq(60L));
        verify(redisUtils).increment(eq(RedisKeys.getVoiceSmsDailyKey(PHONE, LocalDate.now().toString())),
                anyLong());
    }

    @Test
    void verifyAcceptsTheCodeOnceAndClearsIt() {
        String code = sendAndCaptureCode();
        when(redisUtils.get(RedisKeys.getVoiceSmsCodeKey(PHONE))).thenReturn(hashOf(code));

        service.verifyOrThrow(PHONE, code);

        verify(redisUtils).delete(RedisKeys.getVoiceSmsCodeKey(PHONE));
        verify(redisUtils, org.mockito.Mockito.atLeastOnce())
                .delete(RedisKeys.getVoiceSmsAttemptKey(PHONE));
    }

    @Test
    void wrongCodeCountsAnAttempt() {
        String code = sendAndCaptureCode();
        when(redisUtils.get(RedisKeys.getVoiceSmsCodeKey(PHONE))).thenReturn(hashOf(code));

        RenException error = assertThrows(RenException.class,
                () -> service.verifyOrThrow(PHONE, mismatchOf(code)));
        assertEquals(ErrorCode.SMS_CODE_ERROR, error.getCode());
        verify(redisUtils).increment(RedisKeys.getVoiceSmsAttemptKey(PHONE), 300L);
    }

    @Test
    void expiredCodeAsksForANewOne() {
        RenException error = assertThrows(RenException.class,
                () -> service.verifyOrThrow(PHONE, "1234"));
        assertThat(error.getMsg()).contains("重新获取");
    }

    @Test
    void codeWithWrongLengthIsRejectedBeforeTouchingRedis() {
        RenException error = assertThrows(RenException.class,
                () -> service.verifyOrThrow(PHONE, "123456"));
        assertEquals(ErrorCode.SMS_CODE_ERROR, error.getCode());
        verify(redisUtils, never()).get(anyString());
    }

    @Test
    void attemptsAreCapped() {
        when(redisUtils.get(RedisKeys.getVoiceSmsAttemptKey(PHONE))).thenReturn(5);

        RenException error = assertThrows(RenException.class,
                () -> service.verifyOrThrow(PHONE, "1234"));
        assertThat(error.getMsg()).contains("次数过多");
    }

    @Test
    void gatewayFailureLeavesNoCodeAndNoQuota() {
        doThrow(new RenException(ErrorCode.SMS_SEND_FAILED))
                .when(sender).sendVerifyCode(anyString(), anyString());

        assertThrows(RenException.class, () -> service.send(PHONE));
        verify(redisUtils, never()).set(anyString(), any(), anyLong());
        verify(redisUtils, never()).increment(anyString(), anyLong());
    }

    @Test
    void mockModeSkipsTheGateway() {
        when(sender.isMock()).thenReturn(true);

        service.send(PHONE);

        verify(sender, never()).sendVerifyCode(anyString(), anyString());
        verify(redisUtils).set(eq(RedisKeys.getVoiceSmsCodeKey(PHONE)), any(), eq(300L));
    }

    @Test
    void resendIntervalIsEnforced() {
        when(redisUtils.get(RedisKeys.getVoiceSmsLastSendKey(PHONE)))
                .thenReturn(System.currentTimeMillis());

        RenException error = assertThrows(RenException.class, () -> service.send(PHONE));
        assertThat(error.getMsg()).contains("秒后再获取验证码");
    }

    @Test
    void dailyLimitIsEnforced() {
        when(redisUtils.get(RedisKeys.getVoiceSmsDailyKey(PHONE, LocalDate.now().toString())))
                .thenReturn(10);

        RenException error = assertThrows(RenException.class, () -> service.send(PHONE));
        assertEquals(ErrorCode.TODAY_SMS_LIMIT_REACHED, error.getCode());
    }

    private String sendAndCaptureCode() {
        service.send(PHONE);
        ArgumentCaptor<String> code = ArgumentCaptor.forClass(String.class);
        verify(sender).sendVerifyCode(eq(PHONE), code.capture());
        return code.getValue();
    }

    /** 取一个与正确验证码不同的 4 位数字。 */
    private static String mismatchOf(String code) {
        return "0000".equals(code) ? "1111" : "0000";
    }

    private static String hashOf(String code) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(code.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
