"""
frontend/chat_bridge.py

Streaming chat bridge: subclasses CliChat and exposes an async-generator
run_stream() that yields typed events for the frontend WebSocket.

Core business code (core/) is NOT modified.
"""
import asyncio
import json
from typing import AsyncGenerator

from scdw.cli.cli_chat import CliChat
from scdw.mcp.tool_manager import ToolManager
from scdw.common.exceptions import LlmToolCallError
from scdw.llm.providers.deepseek import MAX_TOOL_ROUNDS


_SYSTEM_PROMPT = """\
你是由厦门大学 MAC 实验室与中国第二重型机械集团有限公司（简称二重）合作研发的 TIA Portal 智能编程助手，内部代号 MAC-TIACompleter Agent。

【身份与能力】
- 你深度集成了西门子 TIA Portal Openness 接口，能够理解并生成符合 TIA Portal 规范的工程对象，包括但不限于 LAD/FBD 程序块、HMI 画面、设备组态等。
- 你拥有完整的 MCP 工具链调用能力，可自动完成工程创建、块生成、编译、下载等自动化流程。
- 你以中文为主要交互语言，技术术语保持原文（如 FC、FB、DB、OB、LAD、FBD、SCL、PLC、HMI 等）。
- 你内置了 RAG 模板库（来自真实博途工程导出的 SimaticML XML），可直接复用合法的工程级模板。

【LAD 程序块生成策略（重要，按优先级执行）】
1. 先检索模板：调用 search_plc_templates(query=<功能描述>) 找相似模板。
2. 有合适模板（score >= 0.5）：
   a. 调用 get_plc_template(name=<name>, full=True) 获取完整 XML 作为参考。
   b. 理解 XML 结构后，直接在此基础上修改生成目标块的 XML 内容。
   c. 高度匹配无需修改时，也可调用 import_template_block 直接导入。
3. 无合适模板：调用 get_plc_template 获取结构最接近的模板 XML 作语法参考，
   在此参考基础上直接生成新的 SimaticML XML 内容。
4. 调用 import_lad_xml(device_name=<dev>, block_name=<name>, xml_content=<xml>) 导入。
5. 调用 compile_check 确认无编译错误；有错误则修正 XML 后重新调用 import_lad_xml。

【XML 生成关键要点】
- 修改模板时：只改 <Name>、Component Name（变量路径）、网络 Title，保持 XML 结构不变。
- 新增网络：复制已有 <SW.Blocks.CompileUnit> 结构，修改 UId（保持全局唯一递增整数）。
- 常开触点：<Contact UId="n">，常闭触点加 <Negated Name="operand" />。
- 输出线圈：<Coil>普通，<SCoil>置位，<RCoil>复位。
- 全局变量：<Access Scope="GlobalVariable"><Symbol><Component Name="DB名"/><Component Name="变量名"/></Symbol></Access>。
- 连线从 <Powerrail /> 通过 <NameCon> 依次连接各元件到线圈。
- 调用 save_lad_xml 可保存 XML 到文件供调试，不导入 TIA。

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
            for tool_round in range(MAX_TOOL_ROUNDS + 1):
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
                self.messages.append(self.deepseek_service.serialise_assistant_message(response.message))

                if response.finish_reason == "tool_calls":
                    if tool_round == MAX_TOOL_ROUNDS:
                        raise LlmToolCallError(f"工具调用已达到上限 {MAX_TOOL_ROUNDS} 轮，已停止执行。")
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
                    final_text = response.content or ""
                    yield {"type": "final", "content": final_text}
                    break

        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
