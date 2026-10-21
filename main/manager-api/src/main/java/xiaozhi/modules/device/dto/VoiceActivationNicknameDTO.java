package xiaozhi.modules.device.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
@Schema(description = "语音激活设置助手昵称")
public class VoiceActivationNicknameDTO {
    @NotBlank(message = "设备MAC不能为空")
    private String macAddress;

    @NotBlank(message = "昵称不能为空")
    private String nickname;
}
