"""模型侧 MCP 工具发现与调用调度。"""
import json
from typing import Optional

from scdw.mcp.client import MCPClient


class ToolManager:
    """将 MCP 工具描述与调用结果转换为 OpenAI/DeepSeek 兼容格式。"""

    @classmethod
    async def get_all_tools(cls, clients: dict[str, MCPClient]) -> list[dict]:
        """获取全部客户端工具的函数调用 Schema。"""
        tools = []
        for client in clients.values():
            for tool in await client.list_tools():
                tools.append({"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.inputSchema}})
        return tools

    @classmethod
    async def _find_client_with_tool(cls, clients: list[MCPClient], tool_name: str) -> Optional[MCPClient]:
        for client in clients:
            if any(tool.name == tool_name for tool in await client.list_tools()):
                return client
        return None

    @classmethod
    async def execute_tool_requests(cls, clients: dict[str, MCPClient], message) -> list[dict]:
        """执行模型返回的工具调用，并返回标准 tool 消息列表。"""
        requests = message.message.tool_calls if hasattr(message.message, "tool_calls") else []
        results: list[dict] = []
        for request in requests:
            tool_id = request.id
            name = request.function.name
            try:
                arguments = json.loads(request.function.arguments)
                client = await cls._find_client_with_tool(list(clients.values()), name)
                if client is None:
                    raise RuntimeError(f"未找到 MCP 工具：{name}")
                output = await client.call_tool(name, arguments)
                texts = [item.text if hasattr(item, "text") else str(item) for item in (output.content if output else [])]
                content = "\n".join(texts) or "工具执行成功，但未返回文本。"
            except Exception as exc:
                content = f"调用工具“{name}”失败：{exc}"
            results.append({"role": "tool", "tool_call_id": tool_id, "content": content})
        return results
