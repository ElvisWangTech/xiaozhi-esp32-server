package xiaozhi.modules.device.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
@Schema(description = "语音激活授权技能提交验证码")
public class VoiceActivationSkillCodeDTO {
    @NotBlank(message = "设备MAC不能为空")
    private String macAddress;

    @NotBlank(message = "任务ID不能为空")
    private String taskId;

    @NotBlank(message = "验证码不能为空")
    private String code;
}
