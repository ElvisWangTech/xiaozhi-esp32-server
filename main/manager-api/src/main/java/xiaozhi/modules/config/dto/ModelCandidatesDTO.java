package xiaozhi.modules.config.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
@Schema(description = "获取同类型可用模型候选DTO")
public class ModelCandidatesDTO {

    @NotBlank(message = "模型类型不能为空")
    @Schema(description = "模型类型(Memory/ASR/VAD/LLM/TTS)")
    private String modelType;
}
