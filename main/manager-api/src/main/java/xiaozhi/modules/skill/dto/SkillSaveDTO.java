package xiaozhi.modules.skill.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Data;

@Data
public class SkillSaveDTO {
    @NotBlank
    @Size(min = 2, max = 32)
    private String skillCode;

    @NotBlank
    @Size(max = 128)
    private String name;

    @Size(max = 1000)
    private String description;

    @Size(max = 64)
    private String category;

    @Size(max = 2048)
    private String endpointUrl;

    /** mcp | skill_package; omitted values remain compatible with old MCP clients. */
    @Size(max = 16)
    private String skillType;

    /** Plain-text SKILL.md body; scripts and archives are not accepted or executed. */
    @Size(max = 32768)
    private String skillContent;

    /** Optional plain-text Markdown reference documents. */
    @Size(max = 32768)
    private String referenceContent;

    @Size(max = 2048)
    private String sourceUrl;

    @Size(max = 64)
    private String license;

    /** 1=published, 0=disabled/draft */
    private Integer status;
}
