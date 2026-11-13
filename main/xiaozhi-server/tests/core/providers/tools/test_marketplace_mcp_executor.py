import json
import unittest
from unittest.mock import AsyncMock, patch

from core.providers.tools.marketplace_mcp_executor import MarketplaceMCPExecutor
from plugins_func.register import Action


class FakeMCPClient:
    def __init__(self, tool_name):
        self.tool_name = tool_name

    def get_available_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": self.tool_name,
                    "description": "A shared MCP tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]


class MarketplaceMCPExecutorTests(unittest.IsolatedAsyncioTestCase):
    def test_same_endpoint_tool_names_are_namespaced_by_skill(self):
        executor = MarketplaceMCPExecutor(conn=None)
        calendar = FakeMCPClient("list_events")
        notes = FakeMCPClient("list_events")
        executor.clients = {"calendar": calendar, "notes": notes}

        tools = executor.get_tools()

        self.assertIn("skill_calendar_list_events", tools)
        self.assertIn("skill_notes_list_events", tools)
        self.assertEqual(len(tools), 2)

    async def test_call_routes_to_the_bound_skill_endpoint(self):
        executor = MarketplaceMCPExecutor(conn=None)
        calendar = FakeMCPClient("list_events")
        executor.clients = {"calendar": calendar}
        executor.get_tools()

        with patch(
            "core.providers.tools.marketplace_mcp_executor.call_mcp_endpoint_tool",
            new_callable=AsyncMock,
            return_value="events",
        ) as call_tool:
            response = await executor.execute(
                None,
                "skill_calendar_list_events",
                {"limit": 3},
            )

        call_tool.assert_awaited_once_with(
            calendar,
            "list_events",
            json.dumps({"limit": 3}, ensure_ascii=False),
        )
        self.assertEqual(response.action, Action.REQLLM)
        self.assertEqual(response.result, "events")


if __name__ == "__main__":
    unittest.main()
