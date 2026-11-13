-- 技能广场扩展：支持纯文本 SKILL.md 技能包。
ALTER TABLE `ai_skill`
  MODIFY COLUMN `endpoint_url` varchar(2048) DEFAULT NULL,
  ADD COLUMN `skill_content` mediumtext DEFAULT NULL COMMENT '纯文本 SKILL.md 内容，不执行包内脚本' AFTER `endpoint_url`,
  ADD COLUMN `reference_content` mediumtext DEFAULT NULL COMMENT '纯文本 Markdown 参考资料' AFTER `skill_content`,
  ADD COLUMN `source_url` varchar(2048) DEFAULT NULL AFTER `reference_content`,
  ADD COLUMN `license` varchar(64) DEFAULT NULL AFTER `source_url`;
