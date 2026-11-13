package xiaozhi.modules.device.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.lang.reflect.Method;

import org.junit.jupiter.api.Test;

class VoiceActivationRolePromptTest {

    @Test
    void composeRolePrompt_keepsTemplateAndAppendsOwner() {
        String prompt = VoiceActivationService.composeRolePrompt(
                "我是{{assistant_name}}。", "老板", "");
        assertEquals("我是{{assistant_name}}。\n[主人称呼]\n请称呼用户为「老板」。", prompt);
        assertFalse(prompt.contains("[补充设定]"));
    }

    @Test
    void composeRolePrompt_appendsSupplement() {
        String prompt = VoiceActivationService.composeRolePrompt(
                "我是{{assistant_name}}。", "张先生", "说话温柔一点");
        assertEquals(
                "我是{{assistant_name}}。\n[主人称呼]\n请称呼用户为「张先生」。\n[补充设定]\n说话温柔一点",
                prompt);
    }

    @Test
    void httpUrlAcceptsTeslaAuthorizeHost() throws Exception {
        Method method = VoiceActivationService.class.getDeclaredMethod("isHttpUrl", String.class);
        method.setAccessible(true);
        assertTrue((boolean) method.invoke(null, "https://auth.tesla.cn/oauth2/v3/authorize"));
        assertTrue((boolean) method.invoke(null, "https://auth.tesla.com/oauth2/v3/authorize"));
        assertTrue((boolean) method.invoke(null, "http://example.com/path"));
        assertFalse((boolean) method.invoke(null, ""));
        assertFalse((boolean) method.invoke(null, "ftp://example.com"));
    }
}
