package xiaozhi.modules.device.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
@Schema(description = "语音激活会话查询")
public class VoiceActivationMacDTO {
    @NotBlank(message = "设备MAC不能为空")
    @Schema(description = "设备MAC")
    private String macAddress;
}
