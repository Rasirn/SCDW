"""模型侧 MCP 工具发现与调用调度。"""
import json
from typing import Optional

from scdw.mcp.client import MCPClient


class ToolManager:
    """将 MCP 工具描述与调用结果转换为 OpenAI/DeepSeek 兼容格式。"""

    _client_tool_names: dict[int, set[str]] = {}

    @classmethod
    async def get_all_tools(cls, clients: dict[str, MCPClient]) -> list[dict]:
        """获取全部客户端工具的函数调用 Schema。"""
        tools = []
        for client in clients.values():
            listed = await client.list_tools()
            cls._client_tool_names[id(client)] = {tool.name for tool in listed}
            for tool in listed:
                tools.append({"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.inputSchema}})
        return tools

    @classmethod
    async def _find_client_with_tool(cls, clients: list[MCPClient], tool_name: str) -> Optional[MCPClient]:
        for client in clients:
            names = cls._client_tool_names.get(id(client))
            if names is None:
                listed = await client.list_tools()
                names = {tool.name for tool in listed}
                cls._client_tool_names[id(client)] = names
            if tool_name in names:
                return client
        return None

    @classmethod
    async def execute_tool_requests(cls, clients: dict[str, MCPClient], message) -> list[dict]:
        """执行模型返回的工具调用，并返回标准 tool 消息列表。"""
        requests = message.message.tool_calls if hasattr(message.message, "tool_calls") else []
        results: list[dict] = []
        for request in requests:
            item = await cls.execute_tool_request(clients, request)
            results.append({key: value for key, value in item.items() if key != "success"})
        return results

    @classmethod
    async def execute_tool_request(cls, clients: dict[str, MCPClient], request) -> dict:
        """Execute one request so callers can time and report it independently."""
        tool_id = request.id
        name = request.function.name
        success = True
        try:
            arguments = json.loads(request.function.arguments)
            client = await cls._find_client_with_tool(list(clients.values()), name)
            if client is None:
                raise RuntimeError(f"未找到 MCP 工具：{name}")
            output = await client.call_tool(name, arguments)
            texts = [item.text if hasattr(item, "text") else str(item) for item in (output.content if output else [])]
            content = "\n".join(texts) or "工具执行成功，但未返回文本。"
            # MCP CallToolResult may explicitly mark a returned payload as failed.
            lowered = content.lstrip().lower()
            success = not bool(getattr(output, "isError", False)) and not lowered.startswith(("error", "错误", "失败", "调用工具"))
            if success and content.lstrip().startswith("{"):
                try:
                    decoded = json.loads(content)
                    success = decoded.get("success", True) is not False if isinstance(decoded, dict) else True
                except json.JSONDecodeError:
                    pass
        except Exception as exc:
            success = False
            content = f"调用工具“{name}”失败：{exc}"
        return {"role": "tool", "tool_call_id": tool_id, "content": content, "success": success}
