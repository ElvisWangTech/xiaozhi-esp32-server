package xiaozhi.modules.device.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
@Schema(description = "语音激活授权技能绑定")
public class VoiceActivationSkillAuthDTO {
    @NotBlank(message = "设备MAC不能为空")
    @Schema(description = "设备MAC")
    private String macAddress;

    @NotBlank(message = "授权服务不能为空")
    @Schema(description = "授权服务：didi / luckin")
    private String service;

    @Schema(description = "用户明确要求重新绑定时为 true，否则已开通的技能直接跳过")
    private Boolean rebind;
}
