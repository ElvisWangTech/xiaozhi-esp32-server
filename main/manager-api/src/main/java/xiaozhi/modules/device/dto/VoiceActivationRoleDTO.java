package xiaozhi.modules.device.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
@Schema(description = "语音激活保存主人称呼和角色性格")
public class VoiceActivationRoleDTO {
    @NotBlank(message = "设备MAC不能为空")
    private String macAddress;

    @NotBlank(message = "称呼不能为空")
    private String ownerTitle;

    @NotBlank(message = "性格模板不能为空")
    private String templateName;

    @Schema(description = "用户补充的角色设定，可空")
    private String supplement;
}
