package xiaozhi.common.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.BufferingClientHttpRequestFactory;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;
import java.time.Duration;

/**
 * RestTemplate配置。
 * JDK HttpClient 在不知道正文长度时使用分块传输。portal_lite 只按 Content-Length 读 body，
 * 分块请求会被当成空 JSON，技能绑定就会报缺少全部字段。先缓冲再发出，补上 Content-Length。
 */
@Configuration
public class RestTemplateConfig {

    @Bean
    public RestTemplate restTemplate() {
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory();
        factory.setReadTimeout(Duration.ofSeconds(30));
        return new RestTemplate(new BufferingClientHttpRequestFactory(factory));
    }
}