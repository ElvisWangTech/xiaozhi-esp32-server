package xiaozhi.modules.skill.vo;

import lombok.Data;

@Data
public class SkillMarketplaceVO {
    private String id;
    private String skillCode;
    private String name;
    private String description;
    private String category;
    private String skillType;
    private String sourceUrl;
    private String license;
    private boolean bound;
}
