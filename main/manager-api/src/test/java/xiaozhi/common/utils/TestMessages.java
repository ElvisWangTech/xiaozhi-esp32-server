package xiaozhi.common.utils;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.lang.reflect.Field;
import java.util.Locale;

import org.springframework.context.ApplicationContext;
import org.springframework.context.i18n.LocaleContextHolder;
import org.springframework.context.support.ResourceBundleMessageSource;

/**
 * 单元测试里补齐 i18n：{@code new RenException(ErrorCode.X)} 会经 MessageUtils 取 Spring 容器里的
 * messageSource，没有容器时直接 NPE。这里挂上项目自带的 i18n 资源，行为与运行时一致。
 *
 * <p>MessageUtils 的 messageSource 是静态缓存，用完必须 {@link #uninstall()} 还原，
 * 否则会影响同一个 JVM 里其他测试类。</p>
 */
public final class TestMessages {

    private static boolean installed;
    private static ApplicationContext previousContext;

    private TestMessages() {
    }

    public static synchronized void install() {
        if (installed) {
            return;
        }
        previousContext = SpringContextUtils.applicationContext;
        LocaleContextHolder.setLocale(Locale.SIMPLIFIED_CHINESE);
        ResourceBundleMessageSource source = new ResourceBundleMessageSource();
        source.setBasename("i18n/messages");
        source.setDefaultEncoding("UTF-8");
        source.setFallbackToSystemLocale(true);
        ApplicationContext context = mock(ApplicationContext.class);
        when(context.getBean("messageSource")).thenReturn(source);
        SpringContextUtils.applicationContext = context;
        installed = true;
    }

    public static synchronized void uninstall() {
        if (!installed) {
            return;
        }
        SpringContextUtils.applicationContext = previousContext;
        previousContext = null;
        installed = false;
        try {
            Field field = MessageUtils.class.getDeclaredField("messageSource");
            field.setAccessible(true);
            field.set(null, null);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }
}
