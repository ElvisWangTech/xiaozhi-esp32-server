package xiaozhi.modules.sms.service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDate;
import java.util.HexFormat;
import java.util.concurrent.ThreadLocalRandom;
import java.util.regex.Pattern;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;

import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.common.redis.RedisKeys;
import xiaozhi.common.redis.RedisUtils;

/**
 * 语音激活短信：频控，以及 mock 模式下的本地验证码。
 * 真实发送时验证码由阿里云号码认证生成并校验。
 */
@Slf4j
@Service
@AllArgsConstructor
public class DypnsSmsCodeService {

    static final Pattern PHONE = Pattern.compile("^1\\d{10}$");
    private static final int RESEND_INTERVAL_SECONDS = 60;
    private static final int CODE_TTL_SECONDS = 300;
    private static final int MAX_VERIFY_ATTEMPTS = 5;
    private static final int MAX_DAILY_SEND_COUNT = 10;
    private static final long DAY_SECONDS = 60L * 60 * 26;

    private final DypnsSmsSender sender;
    private final RedisUtils redisUtils;

    public void send(String phone) {
        String normalized = normalizePhone(phone);
        assertResendInterval(normalized);
        assertDailyLimit(normalized);
        if (sender.isMock()) {
            String mockCode = String.format("%06d", ThreadLocalRandom.current().nextInt(1_000_000));
            redisUtils.set(RedisKeys.getVoiceSmsCodeKey(normalized), hash(mockCode), CODE_TTL_SECONDS);
            log.info("Voice activation SMS mock phone={} code={}", DypnsSmsSender.maskPhone(normalized), mockCode);
        } else {
            redisUtils.delete(RedisKeys.getVoiceSmsCodeKey(normalized));
            sender.sendVerifyCode(normalized);
        }
        redisUtils.set(RedisKeys.getVoiceSmsLastSendKey(normalized), System.currentTimeMillis(), RESEND_INTERVAL_SECONDS);
        redisUtils.delete(RedisKeys.getVoiceSmsAttemptKey(normalized));
        String dailyKey = RedisKeys.getVoiceSmsDailyKey(normalized, LocalDate.now().toString());
        redisUtils.increment(dailyKey, DAY_SECONDS);
    }

    public void verifyOrThrow(String phone, String code) {
        String normalized = normalizePhone(phone);
        if (StringUtils.isBlank(code) || !code.trim().matches("\\d{4,8}")) {
            throw new RenException(ErrorCode.SMS_CODE_ERROR);
        }
        String attemptKey = RedisKeys.getVoiceSmsAttemptKey(normalized);
        Object attempts = redisUtils.get(attemptKey);
        int used = attempts instanceof Number ? ((Number) attempts).intValue() : 0;
        if (used >= MAX_VERIFY_ATTEMPTS) {
            throw new RenException("验证码错误次数过多，请重新获取");
        }
        String trimmed = code.trim();
        try {
            if (sender.isMock()) {
                Object stored = redisUtils.get(RedisKeys.getVoiceSmsCodeKey(normalized));
                if (stored == null) {
                    throw new RenException("请先获取手机验证码");
                }
                if (!MessageDigest.isEqual(
                        hash(trimmed).getBytes(StandardCharsets.UTF_8),
                        String.valueOf(stored).getBytes(StandardCharsets.UTF_8))) {
                    throw new RenException(ErrorCode.SMS_CODE_ERROR);
                }
            } else {
                sender.checkVerifyCode(normalized, trimmed);
            }
            redisUtils.delete(RedisKeys.getVoiceSmsCodeKey(normalized));
            redisUtils.delete(attemptKey);
        } catch (RenException e) {
            if (e.getCode() == ErrorCode.SMS_CODE_ERROR) {
                redisUtils.increment(attemptKey, CODE_TTL_SECONDS);
            }
            throw e;
        }
    }

    public static String normalizePhone(String phone) {
        if (StringUtils.isBlank(phone) || !PHONE.matcher(phone.trim()).matches()) {
            throw new RenException(ErrorCode.PHONE_FORMAT_ERROR);
        }
        return phone.trim();
    }

    private void assertResendInterval(String phone) {
        Object last = redisUtils.get(RedisKeys.getVoiceSmsLastSendKey(phone));
        if (!(last instanceof Number)) {
            return;
        }
        long elapsed = (System.currentTimeMillis() - ((Number) last).longValue()) / 1000;
        if (elapsed < RESEND_INTERVAL_SECONDS) {
            throw new RenException("请" + Math.max(1, RESEND_INTERVAL_SECONDS - elapsed) + "秒后再获取验证码");
        }
    }

    private void assertDailyLimit(String phone) {
        Object count = redisUtils.get(RedisKeys.getVoiceSmsDailyKey(phone, LocalDate.now().toString()));
        int sent = count instanceof Number ? ((Number) count).intValue() : 0;
        if (sent >= MAX_DAILY_SEND_COUNT) {
            throw new RenException(ErrorCode.TODAY_SMS_LIMIT_REACHED);
        }
    }

    private static String hash(String code) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(code.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
