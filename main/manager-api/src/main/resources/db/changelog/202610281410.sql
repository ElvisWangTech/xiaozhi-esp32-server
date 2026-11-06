-- 角色模板只换性格和音色，主语言模型固定为 DeepSeek。
-- 之前只在 llm_model_id 恰好是 LLM_MimoLLM 时改写；这里再兜底一次，避免模板仍停在小米 MiMo。

UPDATE `ai_agent_template`
SET `llm_model_id` = 'LLM_DeepSeekLLM'
WHERE `agent_name` IN ('湾湾小何', '星际游子', '英语老师', '好奇男孩', '汪汪队长')
  AND (`llm_model_id` IS NULL OR `llm_model_id` <> 'LLM_DeepSeekLLM');

UPDATE `ai_agent`
SET `llm_model_id` = 'LLM_DeepSeekLLM'
WHERE `llm_model_id` = 'LLM_MimoLLM';

UPDATE `ai_agent`
SET `slm_model_id` = 'LLM_DeepSeekLLM'
WHERE `slm_model_id` = 'LLM_MimoLLM';
