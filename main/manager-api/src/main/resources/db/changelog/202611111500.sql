-- 技能广场首版：管理员发布共享 MCP 技能，用户按智能体开通/解绑。
CREATE TABLE IF NOT EXISTS `ai_skill` (
  `id` varchar(32) NOT NULL,
  `skill_code` varchar(32) NOT NULL,
  `name` varchar(128) NOT NULL,
  `description` varchar(1000) DEFAULT NULL,
  `category` varchar(64) DEFAULT NULL,
  `skill_type` varchar(16) NOT NULL DEFAULT 'mcp',
  `endpoint_url` varchar(2048) NOT NULL,
  `status` tinyint NOT NULL DEFAULT 0 COMMENT '0=草稿/下架，1=已发布',
  `creator` bigint DEFAULT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updater` bigint DEFAULT NULL,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ai_skill_code` (`skill_code`),
  KEY `idx_ai_skill_status_category` (`status`, `category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='技能广场目录';

CREATE TABLE IF NOT EXISTS `ai_agent_skill` (
  `id` varchar(32) NOT NULL,
  `agent_id` varchar(32) NOT NULL,
  `skill_id` varchar(32) NOT NULL,
  `creator` bigint DEFAULT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ai_agent_skill` (`agent_id`, `skill_id`),
  KEY `idx_ai_agent_skill_skill` (`skill_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体开通技能关系';
