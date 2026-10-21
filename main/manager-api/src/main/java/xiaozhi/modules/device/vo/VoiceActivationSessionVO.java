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

    @Schema(description = "已绑定时的引导状态，nickname 表示还要设置昵称")
    private String onboardingStatus;
}
