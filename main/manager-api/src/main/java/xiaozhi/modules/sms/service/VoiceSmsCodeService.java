package xiaozhi.modules.sms.service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDate;
import java.util.Map;
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
 * 语音激活短信验证码。
 *
 * <p>通用短信平台只有发送能力，没有校验接口，所以验证码在本地生成与校验：
 * 4 位数字、5 分钟有效、最多试 5 次；重发间隔 60 秒，单号码单日最多 10 条。</p>
 *
 * <p>验证码只保存 SHA-256 摘要，日志里只在 mock 模式打印明文。</p>
 */
@Slf4j
@Service
@AllArgsConstructor
public class VoiceSmsCodeService {

    static final Pattern PHONE = Pattern.compile("^1\\d{10}$");
    /** 验证码位数，需与 {@link #randomCode()} 和 {@link #verifyOrThrow} 的正则保持一致 */
    private static final int CODE_LENGTH = 4;
    private static final int CODE_TTL_SECONDS = 300;
    private static final int RESEND_INTERVAL_SECONDS = 60;
    private static final int MAX_VERIFY_ATTEMPTS = 5;
    private static final int MAX_DAILY_SEND_COUNT = 10;
    private static final long DAY_SECONDS = 60L * 60 * 26;

    private final SmsGatewaySender sender;
    private final RedisUtils redisUtils;

    public void send(String phone) {
        String normalized = normalizePhone(phone);
        assertResendInterval(normalized);
        assertDailyLimit(normalized);
        String code = randomCode();
        boolean mock = sender.isMock();
        if (!mock) {
            // 平台失败直接抛出：此时不写缓存、不计数，用户可以立刻重试
            sender.sendVerifyCode(normalized, code);
        }
        redisUtils.set(RedisKeys.getVoiceSmsCodeKey(normalized), hash(code), CODE_TTL_SECONDS);
        redisUtils.delete(RedisKeys.getVoiceSmsAttemptKey(normalized));
        redisUtils.set(RedisKeys.getVoiceSmsLastSendKey(normalized),
                System.currentTimeMillis(), RESEND_INTERVAL_SECONDS);
        redisUtils.increment(
                RedisKeys.getVoiceSmsDailyKey(normalized, LocalDate.now().toString()), DAY_SECONDS);
        if (mock) {
            log.info("语音激活短信 mock phone={} code={}", SmsGatewaySender.maskPhone(normalized), code);
        }
    }

    public void verifyOrThrow(String phone, String code) {
        String normalized = normalizePhone(phone);
        String trimmed = StringUtils.trimToEmpty(code);
        if (!trimmed.matches("\\d{" + CODE_LENGTH + "}")) {
            throw new RenException(ErrorCode.SMS_CODE_ERROR);
        }
        String codeKey = RedisKeys.getVoiceSmsCodeKey(normalized);
        String attemptKey = RedisKeys.getVoiceSmsAttemptKey(normalized);
        Object attempts = redisUtils.get(attemptKey);
        int used = attempts instanceof Number ? ((Number) attempts).intValue() : 0;
        if (used >= MAX_VERIFY_ATTEMPTS) {
            throw new RenException(ErrorCode.VOICE_SMS_ATTEMPTS_EXHAUSTED, "验证码错误次数过多，请重新获取", null);
        }
        Object stored = redisUtils.get(codeKey);
        if (stored == null) {
            throw new RenException(ErrorCode.VOICE_SMS_EXPIRED, "验证码已过期，请重新获取", null);
        }
        if (!MessageDigest.isEqual(
                hash(trimmed).getBytes(StandardCharsets.UTF_8),
                String.valueOf(stored).getBytes(StandardCharsets.UTF_8))) {
            redisUtils.increment(attemptKey, CODE_TTL_SECONDS);
            throw new RenException(ErrorCode.SMS_CODE_ERROR);
        }
        // 一次性使用：校验通过立刻失效
        redisUtils.delete(codeKey);
        redisUtils.delete(attemptKey);
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
            long retryAfterSeconds = RESEND_INTERVAL_SECONDS - elapsed;
            throw new RenException(ErrorCode.VOICE_SMS_COOLDOWN,
                    "请" + retryAfterSeconds + "秒后再获取验证码",
                    Map.of("retryAfterSeconds", retryAfterSeconds));
        }
    }

    private void assertDailyLimit(String phone) {
        Object count = redisUtils.get(RedisKeys.getVoiceSmsDailyKey(phone, LocalDate.now().toString()));
        int sent = count instanceof Number ? ((Number) count).intValue() : 0;
        if (sent >= MAX_DAILY_SEND_COUNT) {
            throw new RenException(ErrorCode.TODAY_SMS_LIMIT_REACHED);
        }
    }

    private static String randomCode() {
        int bound = (int) Math.pow(10, CODE_LENGTH);
        return String.format("%0" + CODE_LENGTH + "d", ThreadLocalRandom.current().nextInt(bound));
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
