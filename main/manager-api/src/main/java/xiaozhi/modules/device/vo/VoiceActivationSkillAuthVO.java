package xiaozhi.modules.device.vo;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "授权技能绑定结果")
public class VoiceActivationSkillAuthVO {
    @Schema(description = "waiting_code / done / skipped / failed")
    private String status;

    @Schema(description = "服务：didi / luckin")
    private String service;

    @Schema(description = "自动化任务 ID，waiting_code 时有值")
    private String taskId;

    @Schema(description = "可读说明")
    private String message;
}
