package xiaozhi.modules.device.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
@Schema(description = "语音激活授权技能任务")
public class VoiceActivationSkillTaskDTO {
    @NotBlank(message = "设备MAC不能为空")
    private String macAddress;

    @Schema(description = "自动化任务 ID。短信授权必填；Tesla OAuth 轮询可空，改查 auth-status")
    private String taskId;

    @Schema(description = "授权服务。Tesla 无 taskId 时必填 tesla")
    private String service;
}
