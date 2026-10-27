package xiaozhi.modules.device.vo;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "语音激活可选性格模板")
public class VoiceActivationTemplateVO {
    @Schema(description = "模板名称")
    private String name;

    @Schema(description = "对应 TTS 音色编码，选中后立即切换")
    private String voice;
}
