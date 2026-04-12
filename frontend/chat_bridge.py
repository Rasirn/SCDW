"""
frontend/chat_bridge.py

Streaming chat bridge: subclasses CliChat and exposes an async-generator
run_stream() that yields typed events for the frontend WebSocket.

Core business code (core/) is NOT modified.
"""
import asyncio
import json
from typing import AsyncGenerator

from core.cli_chat import CliChat
from core.tools import ToolManager


_SYSTEM_PROMPT = """\
你是由厦门大学 MAC 实验室与中国第二重型机械集团有限公司（简称二重）合作研发的 TIA Portal 智能编程助手，内部代号 MAC-TIACompleter Agent。

【身份与能力】
- 你深度集成了西门子 TIA Portal Openness 接口，能够理解并生成符合 TIA Portal 规范的工程对象，包括但不限于 LAD/FBD 程序块、HMI 画面、设备组态等。
- 你拥有完整的 MCP 工具链调用能力，可自动完成工程创建、块生成、编译、下载等自动化流程。
- 你以中文为主要交互语言，技术术语保持原文（如 FC、FB、DB、OB、LAD、FBD、SCL、PLC、HMI 等）。

【行为准则】
- 回答简洁、准确，优先给出可直接使用的代码或操作步骤。
- 在调用工具前，简要说明意图；工具执行完成后，给出结果摘要。
- 对于超出 TIA Portal 与 PLC 编程范畴的问题，礼貌说明并引导回主题。
- 严禁捏造工具调用结果或工程数据。
"""

# _SYSTEM_PROMPT = """\
# 你是Potato Agent, TIA Portal 智能编程助手。

# 【身份与能力】
# - 你深度集成了西门子 TIA Portal Openness 接口，能够理解并生成符合 TIA Portal 规范的工程对象，包括但不限于 LAD/FBD 程序块、HMI 画面、设备组态等。
# - 你拥有完整的 MCP 工具链调用能力，可自动完成工程创建、块生成、编译、下载等自动化流程。
# - 你以中文为主要交互语言，技术术语保持原文（如 FC、FB、DB、OB、LAD、FBD、SCL、PLC、HMI 等）。

# 【行为准则】
# - 回答简洁、准确，优先给出可直接使用的代码或操作步骤。
# - 在调用工具前，简要说明意图；工具执行完成后，给出结果摘要。
# - 对于超出 TIA Portal 与 PLC 编程范畴的问题，礼貌说明并引导回主题。
# - 严禁捏造工具调用结果或工程数据。
# """


class StreamingChat(CliChat):
    """
    Extends CliChat with per-step event streaming for the GUI frontend.

    Yields dicts with the following 'type' values:
      thinking     – LLM API call has started
      llm_text     – assistant produced intermediate text before tool calls
      tool_call    – a tool is about to be executed
                     fields: id, name, args (dict)
      tool_result  – tool execution complete
                     fields: id, name, content (str)
      final        – final assistant response; fields: content (str)
      error        – unrecoverable error; fields: message (str)
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Prepend system prompt; kept at index 0 so it is never cleared by
        # the "new conversation" action (which only clears user/assistant turns
        # added later).
        self.messages.insert(0, {"role": "system", "content": _SYSTEM_PROMPT})

    async def run_stream(self, query: str) -> AsyncGenerator[dict, None]:
        # Process query: handles @resource mentions, /commands, builds message
        await self._process_query(query)

        loop = asyncio.get_event_loop()

        try:
            while True:
                # Signal to frontend that we are about to call the LLM
                yield {"type": "thinking"}

                # Gather tools and build a snapshot of current messages
                tools = await ToolManager.get_all_tools(self.clients)
                messages_snapshot = list(self.messages)

                # Blocking OpenAI/DeepSeek call – run in thread pool so the
                # event loop (and WebSocket writes) remain responsive
                response = await loop.run_in_executor(
                    None,
                    lambda: self.deepseek_service.chat(
                        messages=messages_snapshot,
                        tools=tools,
                    ),
                )

                # Persist assistant turn in the conversation history
                self.deepseek_service.add_assistant_message(self.messages, response)

                if response.finish_reason == "tool_calls":
                    # Emit any textual preamble the LLM produced before calling tools
                    if response.message.content:
                        yield {"type": "llm_text", "content": response.message.content}

                    tool_calls = response.message.tool_calls or []
                    tool_name_map: dict[str, str] = {}

                    # Emit each tool-call event so the frontend can show cards
                    for tc in tool_calls:
                        tool_name_map[tc.id] = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments)
                        except Exception:
                            args = tc.function.arguments
                        yield {
                            "type": "tool_call",
                            "id": tc.id,
                            "name": tc.function.name,
                            "args": args,
                        }

                    # Execute the tools (async, uses MCP client)
                    tool_results = await ToolManager.execute_tool_requests(
                        self.clients, response
                    )

                    # Emit results and add them to conversation history
                    for res in tool_results:
                        tool_id = res.get("tool_call_id", "")
                        yield {
                            "type": "tool_result",
                            "id": tool_id,
                            "name": tool_name_map.get(tool_id, ""),
                            "content": res.get("content", ""),
                        }
                        self.messages.append(res)

                else:
                    # No more tool calls – emit final response and stop
                    final_text = self.deepseek_service.text_from_message(response)
                    yield {"type": "final", "content": final_text}
                    break

        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
