package xiaozhi.modules.device.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
@Schema(description = "语音激活发送短信")
public class VoiceActivationSmsDTO {
    @NotBlank(message = "设备MAC不能为空")
    private String macAddress;

    @NotBlank(message = "手机号不能为空")
    private String phone;
}
