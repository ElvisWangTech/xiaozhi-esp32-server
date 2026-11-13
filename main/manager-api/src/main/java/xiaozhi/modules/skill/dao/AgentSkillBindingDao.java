package xiaozhi.modules.skill.dao;

import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import xiaozhi.common.dao.BaseDao;
import xiaozhi.modules.skill.entity.AgentSkillBindingEntity;

@Mapper
public interface AgentSkillBindingDao extends BaseDao<AgentSkillBindingEntity> {
    @Delete("DELETE FROM ai_agent_skill WHERE agent_id = #{agentId}")
    int deleteByAgentId(@Param("agentId") String agentId);
}
