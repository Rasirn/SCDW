import json
from typing import Optional, Literal, List
from mcp.types import CallToolResult, Tool, TextContent
from mcp_client import MCPClient
import openai.types.chat.chat_completion as Message

class ToolManager:
    @classmethod
    async def get_all_tools(cls, clients: dict[str, MCPClient]) -> list[dict]:
        """Gets all tools from the provided clients in OpenAI/DeepSeek format."""
        tools = []
        for client in clients.values():
            tool_models = await client.list_tools()
            for t in tool_models:
                # 转换为 DeepSeek/OpenAI 格式
                tool = {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.inputSchema,
                    }
                }
                tools.append(tool)
        return tools

    @classmethod
    async def _find_client_with_tool(
        cls, clients: list[MCPClient], tool_name: str
    ) -> Optional[MCPClient]:
        """Finds the first client that has the specified tool."""
        for client in clients:
            tools = await client.list_tools()
            tool = next((t for t in tools if t.name == tool_name), None)
            if tool:
                return client
        return None

    @classmethod
    def _build_tool_result_part(
        cls,
        tool_use_id: str,
        text: str,
        status: Literal["success"] | Literal["error"],
    ) -> dict:
        """Builds a tool result part in OpenAI/DeepSeek format."""
        # OpenAI/DeepSeek 格式的 tool result
        return {
            "role": "tool",
            "tool_call_id": tool_use_id,
            "content": text,
        }

    @classmethod
    async def execute_tool_requests(
        cls, clients: dict[str, MCPClient], message: Message
    ) -> List[dict]:
        """
        执行工具调用请求
        
        工作流程：
        1. 从消息中提取所有工具调用请求
        2. 对每个工具调用，找到对应的 MCP 客户端
        3. 执行工具调用
        4. 将结果格式化为 DeepSeek/OpenAI 要求的格式
        
        Args:
            clients: 所有可用的 MCP 客户端字典
            message: DeepSeek 返回的消息对象，包含工具调用信息
            
        Returns:
            List[dict]: 工具执行结果列表，每个结果格式为：
                {
                    "role": "tool",
                    "tool_call_id": "调用ID",
                    "content": "执行结果文本"
                }
        """
        # 从消息中提取工具调用列表
        # DeepSeek/OpenAI 格式中，工具调用存储在 message.tool_calls 中
        tool_requests = message.message.tool_calls if hasattr(message.message, 'tool_calls') else []
        tool_result_blocks: list[dict] = []
        
        # 遍历每个工具调用请求
        for tool_request in tool_requests:
            # 提取工具调用的关键信息
            tool_use_id = tool_request.id                    # 工具调用的唯一ID
            tool_name = tool_request.function.name           # 要调用的工具名称
            tool_input = json.loads(tool_request.function.arguments)  # 工具参数（从JSON字符串解析）
            
            # 步骤1：查找拥有该工具的客户端
            client = await cls._find_client_with_tool(
                list(clients.values()), tool_name
            )

            # 如果找不到对应的客户端，返回错误信息
            if not client:
                error_msg = f"Error: Tool '{tool_name}' not found"
                tool_result_blocks.append(
                    cls._build_tool_result_part(
                        tool_use_id, 
                        error_msg, 
                        "error"
                    )
                )
                continue

            # 步骤2：执行工具调用
            try:
                # 调用工具（注意：client.call_tool 是异步方法，需要 await）
                tool_output = await client.call_tool(tool_name, tool_input)
                
                # 步骤3：提取工具返回的文本内容
                content_texts = []
                
                if tool_output:
                    # 检查工具输出是否包含 content 属性（MCP 标准格式）
                    if hasattr(tool_output, 'content'):
                        # 遍历 content 列表中的每个项目
                        for item in tool_output.content:
                            # 如果是文本内容，提取 text 属性
                            if hasattr(item, 'text'):
                                content_texts.append(item.text)
                            else:
                                # 如果不是文本内容，转换为字符串
                                content_texts.append(str(item))
                    else:
                        # 如果不是标准格式，直接将整个输出转为字符串
                        content_texts.append(str(tool_output))
                else:
                    content_texts.append("Tool returned None")
                
                # 将所有内容片段合并为单一文本
                result_text = "\n".join(content_texts) if content_texts else "Tool executed successfully but returned no content"
                
                # 步骤4：将结果格式化为 DeepSeek/OpenAI 要求的格式
                tool_result_blocks.append(
                    cls._build_tool_result_part(
                        tool_use_id,
                        result_text,
                        "success",  # 状态参数保留但不在结果中使用
                    )
                )
                
            except Exception as e:
                # 工具执行出错，返回错误信息
                error_message = f"Error executing tool '{tool_name}': {str(e)}"
                tool_result_blocks.append(
                    cls._build_tool_result_part(
                        tool_use_id,
                        error_message,
                        "error",
                    )
                )

        return tool_result_blocks