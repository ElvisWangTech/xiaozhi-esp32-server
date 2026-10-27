package xiaozhi.modules.device.vo;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "语音激活会话")
public class VoiceActivationSessionVO {
    @Schema(description = "设备是否已绑定")
    private boolean bound;

    @Schema(description = "未绑定时的激活方式，voice 或 code")
    private String activationType;

    @Schema(description = "已绑定时的引导状态：nickname 待设昵称，role 待认主和性格，skills 待技能绑定")
    private String onboardingStatus;
}
