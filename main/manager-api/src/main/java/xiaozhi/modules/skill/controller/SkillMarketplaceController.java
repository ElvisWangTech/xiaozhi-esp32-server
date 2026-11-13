package xiaozhi.modules.skill.controller;

import java.util.List;

import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import xiaozhi.common.utils.Result;
import xiaozhi.modules.agent.dto.AgentDTO;
import xiaozhi.modules.agent.service.AgentService;
import xiaozhi.modules.security.user.SecurityUser;
import xiaozhi.modules.skill.dto.SkillSaveDTO;
import xiaozhi.modules.skill.entity.SkillEntity;
import xiaozhi.modules.skill.service.SkillMarketplaceService;
import xiaozhi.modules.skill.vo.SkillMarketplaceVO;
import xiaozhi.modules.skill.vo.SkillRecommendationVO;

@Tag(name = "技能广场")
@RestController
@RequiredArgsConstructor
@RequestMapping("/skills")
public class SkillMarketplaceController {
    private final SkillMarketplaceService skillMarketplaceService;
    private final AgentService agentService;

    @GetMapping("/admin/users/{userId}/agents")
    @Operation(summary = "管理员获取指定用户的智能体列表")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<List<AgentDTO>> agentsForUser(@PathVariable Long userId) {
        return new Result<List<AgentDTO>>().ok(agentService.getUserAgents(userId, null, "name"));
    }

    @GetMapping("/marketplace")
    @Operation(summary = "获取技能广场列表及当前智能体开通状态")
    @RequiresPermissions("sys:role:normal")
    public Result<List<SkillMarketplaceVO>> marketplace(@RequestParam String agentId) {
        return new Result<List<SkillMarketplaceVO>>()
                .ok(skillMarketplaceService.listMarketplace(agentId, SecurityUser.getUserId()));
    }

    @PostMapping("/{skillId}/bind")
    @Operation(summary = "为智能体开通技能")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> bind(@PathVariable String skillId, @RequestParam String agentId) {
        skillMarketplaceService.bindSkill(agentId, skillId, SecurityUser.getUserId());
        return new Result<Void>().ok(null);
    }

    @DeleteMapping("/{skillId}/bind")
    @Operation(summary = "为智能体解绑技能")
    @RequiresPermissions("sys:role:normal")
    public Result<Void> unbind(@PathVariable String skillId, @RequestParam String agentId) {
        skillMarketplaceService.unbindSkill(agentId, skillId, SecurityUser.getUserId());
        return new Result<Void>().ok(null);
    }

    @GetMapping("/admin/list")
    @Operation(summary = "管理员获取技能目录")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<List<SkillEntity>> adminList() {
        return new Result<List<SkillEntity>>().ok(skillMarketplaceService.listAdminSkills());
    }

    @GetMapping("/admin/recommended")
    @Operation(summary = "管理员获取可直接安装的推荐技能包")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<List<SkillRecommendationVO>> recommendedSkillPackages() {
        return new Result<List<SkillRecommendationVO>>().ok(
                skillMarketplaceService.listRecommendedSkillPackages());
    }

    @PostMapping("/admin/recommended/{recommendationKey}/install")
    @Operation(summary = "从可信推荐源导入 SKILL.md 技能包")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<SkillEntity> installRecommendedSkillPackage(@PathVariable String recommendationKey) {
        return new Result<SkillEntity>().ok(skillMarketplaceService.installRecommendedSkillPackage(
                recommendationKey, SecurityUser.getUserId()));
    }

    @PostMapping("/admin")
    @Operation(summary = "管理员新增 MCP 技能或 SKILL.md 技能包")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<SkillEntity> create(@Valid @RequestBody SkillSaveDTO dto) {
        return new Result<SkillEntity>().ok(skillMarketplaceService.createSkill(dto, SecurityUser.getUserId()));
    }

    @PutMapping("/admin/{skillId}")
    @Operation(summary = "管理员更新技能")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<SkillEntity> update(@PathVariable String skillId, @Valid @RequestBody SkillSaveDTO dto) {
        return new Result<SkillEntity>().ok(
                skillMarketplaceService.updateSkill(skillId, dto, SecurityUser.getUserId()));
    }

    @DeleteMapping("/admin/{skillId}")
    @Operation(summary = "管理员下架技能")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> disable(@PathVariable String skillId) {
        skillMarketplaceService.disableSkill(skillId, SecurityUser.getUserId());
        return new Result<Void>().ok(null);
    }
}
