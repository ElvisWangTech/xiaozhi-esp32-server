package xiaozhi.modules.skill.entity;

import java.util.Date;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import lombok.Data;

@Data
@TableName("ai_skill")
public class SkillEntity {
    @TableId(type = IdType.ASSIGN_UUID)
    private String id;
    private String skillCode;
    private String name;
    private String description;
    private String category;
    private String skillType;
    private String endpointUrl;
    private String skillContent;
    private String referenceContent;
    private String sourceUrl;
    private String license;
    /** 1=published, 0=disabled/draft */
    private Integer status;
    private Long creator;
    private Date createdAt;
    private Long updater;
    private Date updatedAt;
}
