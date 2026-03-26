from mcp_client import MCPClient
from core.tools import ToolManager
from core.deepseek import Deepseek

class Chat:
    def __init__(self, deepseek_service: Deepseek, clients: dict[str, MCPClient]):
        self.deepseek_service: Deepseek = deepseek_service
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

        while True:
            # 2. 获取 AI 响应
            response = self.deepseek_service.chat(
                messages=self.messages,
                tools=await ToolManager.get_all_tools(self.clients),
            )

            # 3. 添加 assistant 消息到历史
            self.deepseek_service.add_assistant_message(self.messages, response)

            if response.finish_reason == "tool_calls":
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
                final_text_response = self.deepseek_service.text_from_message(
                    response
                )
                break

        return final_text_response
