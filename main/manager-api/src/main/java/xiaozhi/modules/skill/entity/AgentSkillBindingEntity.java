package xiaozhi.modules.skill.entity;

import java.util.Date;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import lombok.Data;

@Data
@TableName("ai_agent_skill")
public class AgentSkillBindingEntity {
    @TableId(type = IdType.ASSIGN_UUID)
    private String id;
    private String agentId;
    private String skillId;
    private Long creator;
    private Date createdAt;
}
