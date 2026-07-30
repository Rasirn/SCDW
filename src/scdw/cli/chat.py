import json

from scdw.mcp.client import MCPClient
from scdw.mcp.tool_manager import ToolManager
from scdw.llm.providers.deepseek import DeepSeekProvider
from scdw.common.config import get_tool_budget
from scdw.llm.prompts import SYSTEM_PROMPT

class Chat:
    def __init__(self, deepseek_service: DeepSeekProvider, clients: dict[str, MCPClient]):
        self.deepseek_service: DeepSeekProvider = deepseek_service
        self.clients: dict[str, MCPClient] = clients
        self.messages: list = [{"role": "system", "content": SYSTEM_PROMPT}]

    async def _process_query(self, query: str):
        self.messages.append({"role": "user", "content": query})

    async def _tia_context_prompt(self) -> str | None:
        """获取本轮临时 TIA 摘要；失败不影响普通对话。"""
        for client in self.clients.values():
            try:
                result = await client.call_tool("refresh_tia_context", {})
                texts = [item.text for item in getattr(result, "content", []) if hasattr(item, "text")]
                if texts:
                    return "当前 TIA 状态（仅本轮有效）：\n" + "\n".join(texts)
            except Exception:
                continue
        return None

    async def run(
        self,
        query: str,
    ) -> str:
        final_text_response = ""

        # 1. 添加用户消息
        await self._process_query(query)
        tia_prompt = await self._tia_context_prompt()

        soft_limit, hard_limit = get_tool_budget()
        tool_count = 0
        tools = await ToolManager.get_all_tools(self.clients)
        soft_warned = False
        for tool_round in range(hard_limit + 2):
            # 2. 获取 AI 响应
            response = self.deepseek_service.chat(
                messages=([{"role": "system", "content": tia_prompt}] if tia_prompt else []) + self.messages,
                tools=tools,
            )

            # 3. 添加 assistant 消息到历史
            self.messages.append(self.deepseek_service.serialise_assistant_message(response.message))

            if response.finish_reason == "tool_calls":
                requested = list(getattr(response.message, "tool_calls", None) or [])
                if tool_count + len(requested) > hard_limit:
                    return json.dumps({"success": False, "stage": "tool_budget", "code": "TOOL_BUDGET_EXHAUSTED", "message": "本回合已安全暂停；请从active Plan恢复。", "data": {"tool_calls": tool_count, "soft_limit": soft_limit, "hard_limit": hard_limit}, "retryable": True, "needs_user_action": False}, ensure_ascii=False)
                # 打印 assistant 的文本响应（如果有）
                if response.message.content:
                    print(response.message.content)

                # 4. 执行工具调用
                tool_result_parts = await ToolManager.execute_tool_requests(
                    self.clients, response
                )
                tool_count += len(requested)

                # 5. 添加工具结果到历史（作为单独的 tool 消息）
                if tool_result_parts:
                    # 确保每个 tool result 都添加为单独的消息
                    for tool_result in tool_result_parts:
                        self.messages.append(tool_result)
                if tool_count >= soft_limit and not soft_warned:
                    soft_warned = True
                    self.messages.append({"role": "system", "content": "工具软预算已达到：禁止重复读取，优先使用组合工具完成当前Network。"})
                
                # 继续循环，让 AI 处理工具结果
            else:
                # 6. 获取最终文本响应
                final_text_response = response.content or ""
                break

        return final_text_response
