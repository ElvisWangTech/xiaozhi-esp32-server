package xiaozhi.modules.skill.service.impl;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import org.apache.commons.lang3.StringUtils;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;

import lombok.RequiredArgsConstructor;
import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.modules.agent.service.AgentService;
import xiaozhi.modules.skill.dao.AgentSkillBindingDao;
import xiaozhi.modules.skill.dao.SkillDao;
import xiaozhi.modules.skill.dto.SkillSaveDTO;
import xiaozhi.modules.skill.entity.AgentSkillBindingEntity;
import xiaozhi.modules.skill.entity.SkillEntity;
import xiaozhi.modules.skill.service.SkillMarketplaceService;
import xiaozhi.modules.skill.vo.SkillMarketplaceVO;
import xiaozhi.modules.skill.vo.SkillRecommendationVO;

@Service
@RequiredArgsConstructor
public class SkillMarketplaceServiceImpl implements SkillMarketplaceService {
    private static final Pattern SKILL_CODE = Pattern.compile("^[a-z][a-z0-9_]{1,31}$");
    private static final int PUBLISHED = 1;
    private static final String TYPE_MCP = "mcp";
    private static final String TYPE_SKILL_PACKAGE = "skill_package";
    private static final int MAX_RECOMMENDED_SKILL_BYTES = 128 * 1024;
    private static final HttpClient RECOMMENDATION_HTTP_CLIENT = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(4))
            .followRedirects(HttpClient.Redirect.NEVER)
            .build();
    private static final List<RecommendedPackage> RECOMMENDED_PACKAGES = List.of(
            new RecommendedPackage("astronomy-guide", "astronomy_guide", "星空导览",
                    "观星、月相和天体可见性指引；需要配套 Astronomy MCP 工具。", "天文",
                    "https://github.com/cyanheads/astronomy-mcp-server/tree/main/skills/astronomy-guide",
                    "https://raw.githubusercontent.com/cyanheads/astronomy-mcp-server/main/skills/astronomy-guide/SKILL.md",
                    "Apache-2.0"),
            new RecommendedPackage("travel-planner", "travel_planner", "中国旅行规划",
                    "面向中国大陆行程的规划和行前复核指引；脚本及仓库参考文件不会导入或执行。", "出行",
                    "https://github.com/SquirrelSong5/travel-planner-skill",
                    "https://raw.githubusercontent.com/SquirrelSong5/travel-planner-skill/main/SKILL.md",
                    "MIT"),
            new RecommendedPackage("cooklang-recipe-search", "cooklang_recipe_search", "菜谱搜索",
                    "按食材、标签和菜系搜索个人 Cooklang 菜谱；需要 Cook MCP 和用户自己的菜谱库。", "烹饪",
                    "https://github.com/cooklang/cooklang-skills/tree/main/skills/recipe-search",
                    "https://raw.githubusercontent.com/cooklang/cooklang-skills/main/skills/recipe-search/SKILL.md",
                    "MIT"),
            new RecommendedPackage("home-assistant-best-practices", "ha_best_practices", "智能家居配置指南",
                    "Home Assistant 配置最佳实践指引；本身不直接控制设备。", "智能家居",
                    "https://github.com/homeassistant-ai/skills/tree/main/skills/home-assistant-best-practices",
                    "https://raw.githubusercontent.com/homeassistant-ai/skills/main/skills/home-assistant-best-practices/SKILL.md",
                    "MIT"),
            new RecommendedPackage("daily-planner", "daily_planner", "日程规划",
                    "将目标拆解成时间块；写入日历或任务仍需对应工具和用户确认。", "效率",
                    "https://github.com/frankjdwu/ai-daily-planner/tree/main/daily-planner",
                    "https://raw.githubusercontent.com/frankjdwu/ai-daily-planner/main/daily-planner/SKILL.md",
                    "MIT"));

    private final SkillDao skillDao;
    private final AgentSkillBindingDao bindingDao;
    private final AgentService agentService;

    @Override
    public List<SkillMarketplaceVO> listMarketplace(String agentId, Long userId) {
        requireAgentPermission(agentId, userId);
        List<SkillEntity> skills = skillDao.selectList(new QueryWrapper<SkillEntity>()
                .eq("status", PUBLISHED)
                .orderByAsc("category", "name"));
        Set<String> boundIds = bindingDao.selectList(new QueryWrapper<AgentSkillBindingEntity>()
                .eq("agent_id", agentId)).stream()
                .map(AgentSkillBindingEntity::getSkillId)
                .collect(Collectors.toSet());

        List<SkillMarketplaceVO> result = new ArrayList<>(skills.size());
        for (SkillEntity skill : skills) {
            SkillMarketplaceVO item = new SkillMarketplaceVO();
            item.setId(skill.getId());
            item.setSkillCode(skill.getSkillCode());
            item.setName(skill.getName());
            item.setDescription(skill.getDescription());
            item.setCategory(skill.getCategory());
            item.setSkillType(skill.getSkillType());
            item.setSourceUrl(skill.getSourceUrl());
            item.setLicense(skill.getLicense());
            item.setBound(boundIds.contains(skill.getId()));
            result.add(item);
        }
        return result;
    }

    @Override
    public List<SkillEntity> listAdminSkills() {
        return skillDao.selectList(new QueryWrapper<SkillEntity>().orderByDesc("updated_at", "created_at"));
    }

    @Override
    public List<SkillRecommendationVO> listRecommendedSkillPackages() {
        List<String> skillCodes = RECOMMENDED_PACKAGES.stream()
                .map(RecommendedPackage::skillCode)
                .toList();
        Set<String> installedCodes = skillDao.selectList(new QueryWrapper<SkillEntity>()
                .in("skill_code", skillCodes))
                .stream()
                .map(SkillEntity::getSkillCode)
                .collect(Collectors.toSet());

        return RECOMMENDED_PACKAGES.stream().map(recommended -> {
            SkillRecommendationVO item = new SkillRecommendationVO();
            item.setKey(recommended.key());
            item.setSkillCode(recommended.skillCode());
            item.setName(recommended.name());
            item.setDescription(recommended.description());
            item.setCategory(recommended.category());
            item.setSourceUrl(recommended.sourceUrl());
            item.setLicense(recommended.license());
            item.setInstalled(installedCodes.contains(recommended.skillCode()));
            return item;
        }).toList();
    }

    @Override
    public SkillEntity installRecommendedSkillPackage(String recommendationKey, Long userId) {
        RecommendedPackage recommended = RECOMMENDED_PACKAGES.stream()
                .filter(item -> item.key().equals(recommendationKey))
                .findFirst()
                .orElseThrow(() -> new RenException("推荐技能不存在"));
        String skillContent = fetchRecommendedSkill(recommended.contentUrl());

        SkillSaveDTO dto = new SkillSaveDTO();
        dto.setSkillCode(recommended.skillCode());
        dto.setName(recommended.name());
        dto.setDescription(recommended.description());
        dto.setCategory(recommended.category());
        dto.setSkillType(TYPE_SKILL_PACKAGE);
        dto.setSkillContent(skillContent);
        dto.setSourceUrl(recommended.sourceUrl());
        dto.setLicense(recommended.license());
        dto.setStatus(0); // Import as a draft so an administrator can review before publishing.
        return createSkill(dto, userId);
    }

    private String fetchRecommendedSkill(String contentUrl) {
        try {
            HttpRequest request = HttpRequest.newBuilder(URI.create(contentUrl))
                    .timeout(Duration.ofSeconds(8))
                    .header("Accept", "text/plain")
                    .GET()
                    .build();
            HttpResponse<InputStream> response = RECOMMENDATION_HTTP_CLIENT.send(
                    request, HttpResponse.BodyHandlers.ofInputStream());
            try (InputStream body = response.body()) {
                if (response.statusCode() != 200) {
                    throw new RenException("下载推荐技能失败，请稍后重试");
                }
                String content = readUtf8Bounded(body, MAX_RECOMMENDED_SKILL_BYTES).trim();
                if (content.isEmpty() || !content.startsWith("---")) {
                    throw new RenException("推荐地址未返回有效的 SKILL.md 文件");
                }
                return content;
            }
        } catch (RenException e) {
            throw e;
        } catch (Exception e) {
            throw new RenException("下载推荐技能失败，请检查服务端网络后重试");
        }
    }

    private String readUtf8Bounded(InputStream input, int maxBytes) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int total = 0;
        int read;
        while ((read = input.read(buffer)) != -1) {
            total += read;
            if (total > maxBytes) {
                throw new RenException("推荐技能文件超过大小限制");
            }
            output.write(buffer, 0, read);
        }
        return output.toString(StandardCharsets.UTF_8);
    }

    private record RecommendedPackage(
            String key,
            String skillCode,
            String name,
            String description,
            String category,
            String sourceUrl,
            String contentUrl,
            String license) {
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public SkillEntity createSkill(SkillSaveDTO dto, Long userId) {
        String skillType = validateSkill(dto, null);
        SkillEntity skill = new SkillEntity();
        copyFromDto(skill, dto);
        skill.setSkillType(skillType);
        skill.setStatus(dto.getStatus() == null ? 0 : (dto.getStatus() == PUBLISHED ? 1 : 0));
        skill.setCreator(userId);
        skill.setCreatedAt(new Date());
        skill.setUpdater(userId);
        skill.setUpdatedAt(new Date());
        try {
            if (skillDao.insert(skill) != 1) {
                throw new RenException("创建技能失败");
            }
        } catch (DuplicateKeyException e) {
            throw new RenException("技能编码已存在");
        }
        return skill;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public SkillEntity updateSkill(String skillId, SkillSaveDTO dto, Long userId) {
        String skillType = validateSkill(dto, skillId);
        SkillEntity skill = skillDao.selectById(skillId);
        if (skill == null) {
            throw new RenException("技能不存在");
        }
        copyFromDto(skill, dto);
        skill.setSkillType(skillType);
        skill.setStatus(dto.getStatus() == null
                ? skill.getStatus()
                : (dto.getStatus() == PUBLISHED ? 1 : 0));
        skill.setUpdater(userId);
        skill.setUpdatedAt(new Date());
        try {
            if (skillDao.updateById(skill) != 1) {
                throw new RenException("更新技能失败");
            }
        } catch (DuplicateKeyException e) {
            throw new RenException("技能编码已存在");
        }
        return skill;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void disableSkill(String skillId, Long userId) {
        SkillEntity skill = skillDao.selectById(skillId);
        if (skill == null) {
            throw new RenException("技能不存在");
        }
        skill.setStatus(0);
        skill.setUpdater(userId);
        skill.setUpdatedAt(new Date());
        skillDao.updateById(skill);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void bindSkill(String agentId, String skillId, Long userId) {
        requireAgentPermission(agentId, userId);
        SkillEntity skill = skillDao.selectById(skillId);
        if (skill == null || !Integer.valueOf(PUBLISHED).equals(skill.getStatus())) {
            throw new RenException("技能不存在或尚未发布");
        }
        AgentSkillBindingEntity existing = bindingDao.selectOne(new QueryWrapper<AgentSkillBindingEntity>()
                .eq("agent_id", agentId).eq("skill_id", skillId).last("LIMIT 1"));
        if (existing != null) {
            return;
        }
        AgentSkillBindingEntity binding = new AgentSkillBindingEntity();
        binding.setAgentId(agentId);
        binding.setSkillId(skillId);
        binding.setCreator(userId);
        binding.setCreatedAt(new Date());
        try {
            bindingDao.insert(binding);
        } catch (DuplicateKeyException ignored) {
            // Idempotent bind: a concurrent request may have inserted the unique pair.
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void unbindSkill(String agentId, String skillId, Long userId) {
        requireAgentPermission(agentId, userId);
        bindingDao.delete(new QueryWrapper<AgentSkillBindingEntity>()
                .eq("agent_id", agentId).eq("skill_id", skillId));
    }

    @Override
    public List<Map<String, Object>> getRuntimeMcpSkills(String agentId) {
        List<Map<String, Object>> runtimeSkills = new ArrayList<>();
        for (SkillEntity skill : getBoundPublishedSkills(agentId, TYPE_MCP)) {
            Map<String, Object> item = new HashMap<>();
            item.put("id", skill.getId());
            item.put("skill_code", skill.getSkillCode());
            item.put("name", skill.getName());
            item.put("endpoint_url", skill.getEndpointUrl().replace("/mcp/", "/call/"));
            runtimeSkills.add(item);
        }
        return runtimeSkills;
    }

    @Override
    public List<Map<String, Object>> getRuntimeSkillPackages(String agentId) {
        List<Map<String, Object>> runtimePackages = new ArrayList<>();
        for (SkillEntity skill : getBoundPublishedSkills(agentId, TYPE_SKILL_PACKAGE)) {
            Map<String, Object> item = new HashMap<>();
            item.put("id", skill.getId());
            item.put("skill_code", skill.getSkillCode());
            item.put("name", skill.getName());
            item.put("skill_content", skill.getSkillContent());
            item.put("reference_content", skill.getReferenceContent());
            runtimePackages.add(item);
        }
        return runtimePackages;
    }

    private List<SkillEntity> getBoundPublishedSkills(String agentId, String skillType) {
        List<AgentSkillBindingEntity> bindings = bindingDao.selectList(new QueryWrapper<AgentSkillBindingEntity>()
                .eq("agent_id", agentId));
        if (bindings.isEmpty()) {
            return List.of();
        }
        Set<String> skillIds = bindings.stream().map(AgentSkillBindingEntity::getSkillId).collect(Collectors.toSet());
        return skillDao.selectList(new QueryWrapper<SkillEntity>()
                .in("id", skillIds)
                .eq("status", PUBLISHED)
                .eq("skill_type", skillType)
                .orderByAsc("skill_code"));
    }

    private void requireAgentPermission(String agentId, Long userId) {
        if (StringUtils.isBlank(agentId) || !agentService.checkAgentPermission(agentId, userId)) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
    }

    private String validateSkill(SkillSaveDTO dto, String excludeSkillId) {
        if (dto == null || !SKILL_CODE.matcher(StringUtils.trimToEmpty(dto.getSkillCode())).matches()) {
            throw new RenException("技能编码只能包含小写字母、数字和下划线，且必须以字母开头");
        }
        String skillType = StringUtils.defaultIfBlank(StringUtils.trimToNull(dto.getSkillType()), TYPE_MCP);
        if (TYPE_MCP.equals(skillType)) {
            String endpoint = StringUtils.trimToEmpty(dto.getEndpointUrl());
            try {
                URI uri = URI.create(endpoint);
                String scheme = StringUtils.lowerCase(uri.getScheme());
                if (!("ws".equals(scheme) || "wss".equals(scheme)) || StringUtils.isBlank(uri.getHost())) {
                    throw new IllegalArgumentException("unsupported endpoint");
                }
            } catch (Exception e) {
                throw new RenException("MCP 地址必须是有效的 ws:// 或 wss:// 地址");
            }
        } else if (TYPE_SKILL_PACKAGE.equals(skillType)) {
            String content = StringUtils.trimToEmpty(dto.getSkillContent());
            if (content.isEmpty()) {
                throw new RenException("技能包必须包含 SKILL.md 文本内容");
            }
            if (content.length() > 32768) {
                throw new RenException("SKILL.md 内容不能超过 32,768 个字符");
            }
            if (content.indexOf('\0') >= 0) {
                throw new RenException("技能包内容必须是纯文本 Markdown");
            }
            String referenceContent = StringUtils.defaultString(dto.getReferenceContent());
            if (referenceContent.length() > 32768) {
                throw new RenException("参考文档不能超过 32,768 个字符");
            }
            if (referenceContent.indexOf('\0') >= 0) {
                throw new RenException("参考文档必须是纯文本 Markdown");
            }
            String sourceUrl = StringUtils.trimToEmpty(dto.getSourceUrl());
            if (StringUtils.isNotBlank(sourceUrl)) {
                try {
                    URI uri = URI.create(sourceUrl);
                    String scheme = StringUtils.lowerCase(uri.getScheme());
                    if (!("http".equals(scheme) || "https".equals(scheme))
                            || StringUtils.isBlank(uri.getHost())) {
                        throw new IllegalArgumentException("unsupported source URL");
                    }
                } catch (Exception e) {
                    throw new RenException("技能来源必须是有效的 http:// 或 https:// 链接");
                }
            }
        } else {
            throw new RenException("不支持的技能类型");
        }
        QueryWrapper<SkillEntity> duplicateQuery = new QueryWrapper<SkillEntity>()
                .eq("skill_code", dto.getSkillCode());
        if (StringUtils.isNotBlank(excludeSkillId)) {
            duplicateQuery.ne("id", excludeSkillId);
        }
        if (skillDao.selectCount(duplicateQuery) > 0) {
            throw new RenException("技能编码已存在");
        }
        return skillType;
    }

    private void copyFromDto(SkillEntity target, SkillSaveDTO dto) {
        target.setSkillCode(StringUtils.trim(dto.getSkillCode()));
        target.setName(StringUtils.trim(dto.getName()));
        target.setDescription(StringUtils.trimToNull(dto.getDescription()));
        target.setCategory(StringUtils.trimToNull(dto.getCategory()));
        String skillType = StringUtils.defaultIfBlank(StringUtils.trimToNull(dto.getSkillType()), TYPE_MCP);
        if (TYPE_MCP.equals(skillType)) {
            target.setEndpointUrl(StringUtils.trim(dto.getEndpointUrl()));
            target.setSkillContent(null);
            target.setReferenceContent(null);
            target.setSourceUrl(null);
            target.setLicense(null);
        } else {
            target.setEndpointUrl(null);
            target.setSkillContent(StringUtils.trim(dto.getSkillContent()));
            target.setReferenceContent(StringUtils.trimToNull(dto.getReferenceContent()));
            target.setSourceUrl(StringUtils.trimToNull(dto.getSourceUrl()));
            target.setLicense(StringUtils.trimToNull(dto.getLicense()));
        }
    }

}
