"""技能广场共享 MCP 工具执行器。

首版只支持管理员配置的远程 MCP endpoint，不导入或执行市场上传的 Python 代码。
"""

import hashlib
import json
from typing import Any, Dict

from config.logger import setup_logging
from core.providers.tools.base import ToolDefinition, ToolExecutor, ToolType
from core.providers.tools.mcp_endpoint.mcp_endpoint_handler import (
    call_mcp_endpoint_tool,
    connect_mcp_endpoint,
)
from core.utils.util import sanitize_tool_name
from plugins_func.register import Action, ActionResponse


class MarketplaceMCPExecutor(ToolExecutor):
    def __init__(self, conn):
        self.conn = conn
        self.logger = setup_logging()
        self.clients: Dict[str, Any] = {}
        self.tool_targets: Dict[str, tuple] = {}

    async def initialize(self, skills: list[dict]) -> None:
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            skill_code = str(skill.get("skill_code", "")).strip().lower()
            endpoint_url = str(skill.get("endpoint_url", "")).strip()
            if not skill_code or not endpoint_url:
                continue
            try:
                client = await connect_mcp_endpoint(
                    endpoint_url, self.conn, redact_errors=True
                )
            except Exception as exc:
                self.logger.warning(
                    f"技能广场 MCP 初始化失败 skill={skill_code} error={type(exc).__name__}"
                )
                continue
            if client is None:
                self.logger.warning(f"技能广场 MCP 初始化失败 skill={skill_code}")
                continue
            self.clients[skill_code] = client
            self.logger.info(f"技能广场 MCP 已连接 skill={skill_code}")

    def get_tools(self) -> Dict[str, ToolDefinition]:
        tools: Dict[str, ToolDefinition] = {}
        self.tool_targets = {}
        for skill_code, client in self.clients.items():
            for tool in client.get_available_tools():
                function = tool.get("function", {})
                original_tool_name = str(function.get("name", ""))
                if not original_tool_name:
                    continue
                public_name = self._public_tool_name(skill_code, original_tool_name)
                if public_name in tools:
                    self.logger.warning(
                        f"技能广场工具名冲突，忽略重复工具: {public_name}"
                    )
                    continue
                namespaced_function = dict(function)
                namespaced_function["name"] = public_name
                description = dict(tool)
                description["function"] = namespaced_function
                tools[public_name] = ToolDefinition(
                    name=public_name,
                    description=description,
                    tool_type=ToolType.MARKETPLACE_MCP,
                )
                self.tool_targets[public_name] = (client, original_tool_name)
        return tools

    async def execute(
        self, conn, tool_name: str, arguments: Dict[str, Any]
    ) -> ActionResponse:
        target = self.tool_targets.get(tool_name)
        if target is None:
            # ToolManager may have loaded the description cache before the endpoint
            # finished returning tools; rebuild the lookup once before failing.
            self.get_tools()
            target = self.tool_targets.get(tool_name)
        if target is None:
            return ActionResponse(
                action=Action.NOTFOUND,
                response=f"技能工具 {tool_name} 不存在或尚未准备就绪",
            )

        client, endpoint_tool_name = target
        try:
            result = await call_mcp_endpoint_tool(
                client,
                endpoint_tool_name,
                json.dumps(arguments or {}, ensure_ascii=False),
            )
            return ActionResponse(action=Action.REQLLM, result=str(result))
        except Exception as exc:
            self.logger.warning(
                f"技能工具调用失败 tool={tool_name} error={type(exc).__name__}"
            )
            return ActionResponse(action=Action.ERROR, response="技能工具调用失败")

    def has_tool(self, tool_name: str) -> bool:
        if not self.tool_targets:
            self.get_tools()
        return tool_name in self.tool_targets

    async def cleanup(self) -> None:
        clients = list(self.clients.values())
        self.clients.clear()
        self.tool_targets.clear()
        for client in clients:
            try:
                await client.close()
            except Exception as exc:
                self.logger.debug(
                    f"关闭技能广场 MCP 连接失败: {type(exc).__name__}"
                )

    @staticmethod
    def _public_tool_name(skill_code: str, endpoint_tool_name: str) -> str:
        prefix = sanitize_tool_name(f"skill_{skill_code}_")
        suffix = sanitize_tool_name(endpoint_tool_name)
        full_name = f"{prefix}{suffix}"
        if len(full_name) <= 64:
            return full_name
        digest = hashlib.sha1(full_name.encode("utf-8")).hexdigest()[:8]
        return f"{full_name[:55]}_{digest}"
