from scdw.mcp.client import MCPClient
from scdw.mcp.tool_manager import ToolManager
from scdw.llm.providers.deepseek import DeepSeekProvider
from scdw.common.exceptions import LlmToolCallError

MAX_TOOL_ROUNDS = 20

class Chat:
    def __init__(self, deepseek_service: DeepSeekProvider, clients: dict[str, MCPClient]):
        self.deepseek_service: DeepSeekProvider = deepseek_service
        self.clients: dict[str, MCPClient] = clients
        self.messages: list = []

    async def _process_query(self, query: str):
        self.messages.append({"role": "user", "content": query})

    async def run(
        self,
        query: str,
    ) -> str:
        final_text_response = ""

        # 1. 添加用户消息
        await self._process_query(query)

        for tool_round in range(MAX_TOOL_ROUNDS + 1):
            # 2. 获取 AI 响应
            response = self.deepseek_service.chat(
                messages=self.messages,
                tools=await ToolManager.get_all_tools(self.clients),
            )

            # 3. 添加 assistant 消息到历史
            self.messages.append(self.deepseek_service.serialise_assistant_message(response.message))

            if response.finish_reason == "tool_calls":
                if tool_round == MAX_TOOL_ROUNDS:
                    raise LlmToolCallError(f"工具调用已达到上限 {MAX_TOOL_ROUNDS} 轮，已停止执行。")
                # 打印 assistant 的文本响应（如果有）
                if response.message.content:
                    print(response.message.content)

                # 4. 执行工具调用
                tool_result_parts = await ToolManager.execute_tool_requests(
                    self.clients, response
                )

                # 5. 添加工具结果到历史（作为单独的 tool 消息）
                if tool_result_parts:
                    # 确保每个 tool result 都添加为单独的消息
                    for tool_result in tool_result_parts:
                        self.messages.append(tool_result)
                
                # 继续循环，让 AI 处理工具结果
            else:
                # 6. 获取最终文本响应
                final_text_response = response.content or ""
                break

        return final_text_response
