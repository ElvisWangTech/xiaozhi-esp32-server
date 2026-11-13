package xiaozhi.modules.skill.dao;

import org.apache.ibatis.annotations.Mapper;

import xiaozhi.common.dao.BaseDao;
import xiaozhi.modules.skill.entity.SkillEntity;

@Mapper
public interface SkillDao extends BaseDao<SkillEntity> {
}
