package xiaozhi.modules.device.vo;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "授权技能绑定结果")
public class VoiceActivationSkillAuthVO {
    @Schema(description = "waiting_code / waiting_oauth / pending / done / skipped / failed / already_bound")
    private String status;

    @Schema(description = "服务：didi / luckin / tesla")
    private String service;

    @Schema(description = "自动化任务 ID，waiting_code 时有值")
    private String taskId;

    @Schema(description = "Tesla OAuth 授权页地址，waiting_oauth 时可选")
    private String authUrl;

    @Schema(description = "可读说明")
    private String message;

    @Schema(description = "授权服务返回的机器可读错误码")
    private String errorCode;
}
