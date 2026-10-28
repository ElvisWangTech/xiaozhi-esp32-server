package xiaozhi.common.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.web.client.RestTemplate;

import com.sun.net.httpserver.HttpServer;

class RestTemplateConfigTest {

    @Test
    void postJsonIncludesContentLength() throws Exception {
        RestTemplate restTemplate = new RestTemplateConfig().restTemplate();
        AtomicReference<String> contentLength = new AtomicReference<>();
        AtomicReference<String> body = new AtomicReference<>();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/device/bind", exchange -> {
            contentLength.set(exchange.getRequestHeaders().getFirst("Content-Length"));
            body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] response = "{\"ok\":true}".getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.start();
        try {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("device_name", "小智");
            payload.put("mac_address", "14:c1:9f:d0:87:e4");
            payload.put("agent_name", "小智");
            payload.put("endpoint_url", "ws://127.0.0.1/mcp");
            payload.put("phone", "13800138000");
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            restTemplate.exchange(
                    "http://127.0.0.1:" + server.getAddress().getPort() + "/api/device/bind",
                    HttpMethod.POST,
                    new HttpEntity<>(payload, headers),
                    String.class);

            assertTrue(body.get().contains("\"device_name\":\"小智\""));
            assertEquals(String.valueOf(body.get().getBytes(StandardCharsets.UTF_8).length), contentLength.get());
        } finally {
            server.stop(0);
        }
    }
}
