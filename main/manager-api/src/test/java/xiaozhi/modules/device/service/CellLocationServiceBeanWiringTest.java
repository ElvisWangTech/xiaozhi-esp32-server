package xiaozhi.modules.device.service;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.mockito.Mockito.mock;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.core.RedisTemplate;

import xiaozhi.common.redis.RedisUtils;
import xiaozhi.common.utils.ResourcesUtils;
import xiaozhi.modules.device.config.CellLocationProperties;

/**
 * 回归：CellLocationService 同时有 Spring 注入构造与单测用的 4 参构造。
 * 存在多个构造且未显式标注 @Autowired 时，Spring 不会推断注入构造，会去找无参构造并报
 * "No default constructor found"，导致整个 manager-api 启动失败。
 */
@DisplayName("基站定位服务：Spring 容器装配")
class CellLocationServiceBeanWiringTest {

    /** 只提供构造依赖，CellLocationService 本身交给容器按构造推断实例化。 */
    @Configuration(proxyBeanMethods = false)
    static class Dependencies {
        @Bean
        RedisUtils redisUtils() {
            return mock(RedisUtils.class);
        }

        @Bean
        @SuppressWarnings("unchecked")
        RedisTemplate<String, Object> redisTemplate() {
            return mock(RedisTemplate.class);
        }

        @Bean
        ResourcesUtils resourcesUtils() {
            return mock(ResourcesUtils.class);
        }

        @Bean
        CellLocationProperties cellLocationProperties() {
            return new CellLocationProperties();
        }
    }

    @Test
    @DisplayName("多构造函数下仍能由 Spring 完成构造注入")
    void springCanInstantiateService() {
        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext()) {
            context.register(Dependencies.class, CellLocationService.class);
            // refresh 失败会直接抛异常（正是修复前 "No default constructor found" 的表现）
            context.refresh();
            assertNotNull(context.getBean(CellLocationService.class));
        }
    }
}
