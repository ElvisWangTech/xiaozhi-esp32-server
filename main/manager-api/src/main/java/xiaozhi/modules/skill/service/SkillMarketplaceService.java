package xiaozhi.modules.skill.service;

import java.util.List;
import java.util.Map;

import xiaozhi.modules.skill.dto.SkillSaveDTO;
import xiaozhi.modules.skill.entity.SkillEntity;
import xiaozhi.modules.skill.vo.SkillMarketplaceVO;
import xiaozhi.modules.skill.vo.SkillRecommendationVO;

public interface SkillMarketplaceService {
    List<SkillMarketplaceVO> listMarketplace(String agentId, Long userId);

    List<SkillEntity> listAdminSkills();

    List<SkillRecommendationVO> listRecommendedSkillPackages();

    SkillEntity installRecommendedSkillPackage(String recommendationKey, Long userId);

    SkillEntity createSkill(SkillSaveDTO dto, Long userId);

    SkillEntity updateSkill(String skillId, SkillSaveDTO dto, Long userId);

    void disableSkill(String skillId, Long userId);

    void bindSkill(String agentId, String skillId, Long userId);

    void unbindSkill(String agentId, String skillId, Long userId);

    List<Map<String, Object>> getRuntimeMcpSkills(String agentId);

    List<Map<String, Object>> getRuntimeSkillPackages(String agentId);
}
