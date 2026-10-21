package xiaozhi.modules.device.vo;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "语音激活绑定结果")
public class VoiceActivationBindVO {
    private String deviceId;
    private String agentId;
}
