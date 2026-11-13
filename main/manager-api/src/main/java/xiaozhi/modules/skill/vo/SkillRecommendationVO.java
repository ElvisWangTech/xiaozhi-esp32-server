package xiaozhi.modules.skill.vo;

import lombok.Data;

@Data
public class SkillRecommendationVO {
    private String key;
    private String skillCode;
    private String name;
    private String description;
    private String category;
    private String sourceUrl;
    private String license;
    private boolean installed;
}
