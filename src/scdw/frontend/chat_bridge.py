"""MACtrl 的真实 DeepSeek 流式聊天桥接。"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, AsyncGenerator

from scdw.cli.cli_chat import CliChat
from scdw.mcp.tool_manager import ToolManager
from scdw.common.exceptions import LlmToolCallError
from scdw.llm.providers.deepseek import MAX_TOOL_ROUNDS, LlmStreamResult

_SYSTEM_PROMPT = """你是 MACtrl，由厦门大学 MAC 实验室与四川电网合作研发的 TIA Portal 智控助手。
你协助 PLC 工程师完成需求分析、工程检查、程序生成、程序块导入、编译诊断和修改建议。可按规则调用 MCP 工具，但不得编造工具结果。执行任何 TIA 写入操作前必须使用当前工程上下文。"""

TOOL_DISPLAY_NAMES = {"refresh_tia_context": "刷新 TIA 上下文", "get_tia_context": "读取 TIA 工程信息",
                      "connect_to_open_tia": "连接已打开的 TIA", "compile_check": "编译检查",
                      "import_lad_xml": "导入 LAD 程序块", "import_scl_block": "导入 SCL 程序块",
                      "create_global_db": "创建全局 DB", "create_plc_tag_table": "创建 PLC 变量表",
                      "search_plc_templates": "检索 PLC 模板"}


class StreamingChat(CliChat):
    """把 Provider 的真实 chunk 转换为前端可渲染事件。"""
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.messages.insert(0, {"role": "system", "content": _SYSTEM_PROMPT})

    def reset_conversation(self) -> None:
        """清除用户、模型和工具历史，仅保留系统身份。"""
        self.messages[:] = self.messages[:1]

    async def run_stream(self, query: str, mode: str = "thinking", cancel_event: Any = None) -> AsyncGenerator[dict[str, Any], None]:
        await self._process_query(query)
        tia_prompt = await self._tia_context_prompt()
        yield {"type": "turn_start", "mode": mode, "round": 0}
        try:
            for round_index in range(MAX_TOOL_ROUNDS + 1):
                tools = await ToolManager.get_all_tools(self.clients)
                messages = ([{"role": "system", "content": tia_prompt}] if tia_prompt else []) + list(self.messages)
                result: LlmStreamResult | None = None
                async for event in self.deepseek_service.stream_chat(messages, tools=tools, mode=mode, cancel_event=cancel_event):
                    if event["type"] == "stream_end": result = event["result"]
                    elif event["type"] == "stream_cancelled":
                        yield {"type": "cancelled"}; return
                    else:
                        event["round"] = round_index
                        yield event
                if result is None:
                    yield {"type": "stream_error", "message": "模型流异常结束，未返回 stream_end。"}
                    return
                assistant = {"role": "assistant", "content": result.content or None}
                if result.reasoning_content: assistant["reasoning_content"] = result.reasoning_content
                if result.tool_calls: assistant["tool_calls"] = result.tool_calls
                self.messages.append(assistant)
                if result.finish_reason != "tool_calls":
                    yield {"type": "turn_end", "usage": result.usage.__dict__}; return
                if round_index == MAX_TOOL_ROUNDS:
                    raise LlmToolCallError(f"工具调用达到上限 {MAX_TOOL_ROUNDS}。")
                calls = []
                for call in result.tool_calls:
                    function = SimpleNamespace(name=call["function"]["name"], arguments=call["function"]["arguments"])
                    calls.append(SimpleNamespace(id=call["id"], function=function))
                    try: arguments = json.loads(function.arguments)
                    except Exception: arguments = function.arguments
                    yield {"type": "tool_call_start", "id": call["id"], "name": function.name, "round": round_index,
                           "display_name": TOOL_DISPLAY_NAMES.get(function.name, function.name), "arguments": arguments}
                started = time.monotonic()
                response = SimpleNamespace(message=SimpleNamespace(tool_calls=calls))
                tool_results = await ToolManager.execute_tool_requests(self.clients, response)
                for item in tool_results:
                    text = item.get("content", "")
                    yield {"type": "tool_result", "id": item.get("tool_call_id", ""), "content": text, "round": round_index,
                           "success": not text.lstrip().lower().startswith(("error", "错误", "失败")),
                           "elapsed_ms": round((time.monotonic() - started) * 1000)}
                    self.messages.append(item)
                if cancel_event is not None and cancel_event.is_set():
                    yield {"type": "cancelled"}; return
        except Exception as exc:
            yield {"type": "stream_error", "message": str(exc)}
