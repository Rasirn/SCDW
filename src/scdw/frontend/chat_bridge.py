"""MACtrl 的真实 DeepSeek 流式聊天桥接。"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any, AsyncGenerator

from scdw.cli.cli_chat import CliChat
from scdw.mcp.tool_manager import ToolManager
from scdw.common.exceptions import LlmToolCallError
from scdw.llm.providers.deepseek import MAX_TOOL_ROUNDS, LlmStreamResult
from scdw.common.run_logging import get_run_logger
from scdw.frontend.events import summarize_tool_arguments

TOOL_DISPLAY_NAMES = {"refresh_tia_context": "刷新 TIA 上下文", "get_tia_context": "读取 TIA 工程信息",
                      "connect_to_open_tia": "连接已打开的 TIA", "compile_check": "编译检查",
                      "import_lad_xml": "导入 LAD 程序块", "import_scl_block": "导入 SCL 程序块",
                      "create_instance_db": "创建背景 DB", "save_verified_project": "保存已验证项目",
                      "create_global_db": "创建全局 DB", "create_plc_tag_table": "创建 PLC 变量表",
                      "get_plc_knowledge_catalog": "读取 PLC 知识目录",
                      "get_plc_knowledge_items": "读取 PLC 知识项"}
TOOL_ACTIVITY_MESSAGES = {
    "import_lad_xml": "正在导入 Artifact XML 到 TIA Portal",
    "create_instance_db": "正在创建并绑定背景 DB",
    "compile_check": "正在执行编译检查",
}


class StreamingChat(CliChat):
    """把 Provider 的真实 chunk 转换为前端可渲染事件。"""
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def reset_conversation(self) -> None:
        """清除用户、模型和工具历史，仅保留系统身份。"""
        self.messages[:] = self.messages[:1]

    async def run_stream(self, query: str, mode: str = "thinking", cancel_event: Any = None) -> AsyncGenerator[dict[str, Any], None]:
        run_logger = get_run_logger()
        run_logger.log_event("chat_bridge_query", component="chat", mode=mode, query=run_logger.save_payload("chat_query", query), history_size=len(self.messages))
        # Emit immediately; query/context preparation can itself take noticeable time.
        yield {"type": "turn_start", "mode": mode, "round": 0}
        yield {"type": "turn_status", "stage": "analyzing", "message": "正在分析需求"}
        await self._process_query(query)
        tia_prompt = await self._tia_context_prompt()
        mutation_ledger: set[tuple[str, str]] = set()
        mutating_tools = {"init_tia_project", "close_tia_session", "detach_tia_session", "add_plc_to_project"}
        try:
            for round_index in range(MAX_TOOL_ROUNDS + 1):
                yield {"type": "turn_status", "stage": "generating", "message": "正在生成回复", "round": round_index}
                run_logger.log_event("llm_round_started", component="chat", round=round_index, history_size=len(self.messages))
                tools = await ToolManager.get_all_tools(self.clients)
                messages = ([{"role": "system", "content": tia_prompt}] if tia_prompt else []) + list(self.messages)
                result: LlmStreamResult | None = None
                async for event in self.deepseek_service.stream_chat(messages, tools=tools, mode=mode, cancel_event=cancel_event):
                    if event["type"] == "stream_end":
                        result = event["result"]
                    elif event["type"] == "stream_cancelled":
                        yield {"type": "turn_status", "stage": "cancelled", "message": "已取消"}
                        yield {"type": "cancelled"}
                        return
                    else:
                        event["round"] = round_index
                        run_logger.log_event("llm_stream_event", component="chat", round=round_index, event_type=event.get("type"), payload=run_logger.save_payload("llm_event", event))
                        yield event
                if result is None:
                    yield {"type": "turn_status", "stage": "failed", "message": "执行失败"}
                    yield {"type": "stream_error", "message": "模型流异常结束，未返回 stream_end。"}
                    return
                assistant = {"role": "assistant", "content": result.content or None}
                if result.reasoning_content:
                    assistant["reasoning_content"] = result.reasoning_content
                if result.tool_calls:
                    assistant["tool_calls"] = result.tool_calls
                self.messages.append(assistant)
                if result.finish_reason != "tool_calls":
                    yield {"type": "turn_status", "stage": "completed", "message": "已完成"}
                    yield {"type": "turn_end", "usage": asdict(result.usage)}
                    return
                if round_index == MAX_TOOL_ROUNDS:
                    raise LlmToolCallError(f"工具调用达到上限 {MAX_TOOL_ROUNDS}。")

                yield {"type": "turn_status", "stage": "preparing_tool", "message": "正在准备工具调用", "round": round_index}
                for call in result.tool_calls:
                    function = SimpleNamespace(name=call["function"]["name"], arguments=call["function"]["arguments"])
                    try:
                        arguments = json.loads(function.arguments)
                        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
                    except Exception:
                        arguments = function.arguments
                        canonical = function.arguments or "{}"
                    display_name = TOOL_DISPLAY_NAMES.get(function.name, function.name)
                    activity_message = TOOL_ACTIVITY_MESSAGES.get(function.name, f"正在执行 {display_name}")
                    start_event = {
                        "type": "tool_call_start", "id": call["id"], "name": function.name,
                        "round": round_index, "display_name": display_name,
                        "message": activity_message, "arguments": summarize_tool_arguments(arguments),
                    }
                    yield start_event
                    fingerprint = (function.name, canonical)
                    if function.name in mutating_tools and fingerprint in mutation_ledger:
                        item = {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"success": False, "code": "DUPLICATE_MUTATING_TOOL_CALL", "message": "同一回合已执行相同的破坏性工具调用。", "retryable": False, "needs_user_action": False}, ensure_ascii=False), "success": False}
                        self.messages.append({key: value for key, value in item.items() if key != "success"})
                        yield {"type": "tool_result", "id": call["id"], "content": item["content"], "round": round_index, "success": False, "elapsed_ms": 0}
                        continue
                    if function.name in mutating_tools:
                        mutation_ledger.add(fingerprint)

                    request = SimpleNamespace(id=call["id"], function=function)
                    started = time.monotonic()
                    task = asyncio.create_task(ToolManager.execute_tool_request(self.clients, request))
                    try:
                        while not task.done():
                            done, _ = await asyncio.wait({task}, timeout=1.0)
                            if done:
                                break
                            elapsed_ms = round((time.monotonic() - started) * 1000)
                            stopping = cancel_event is not None and cancel_event.is_set()
                            yield {
                                "type": "tool_progress", "id": call["id"], "name": function.name,
                                "display_name": display_name, "round": round_index,
                                "stage": "waiting_safe_stop" if stopping else "running",
                                "message": "正在等待当前安全步骤结束后停止" if stopping else activity_message,
                                "elapsed_ms": elapsed_ms,
                            }
                        item = await task
                    finally:
                        if not task.done():
                            task.cancel()
                            with suppress(asyncio.CancelledError):
                                await task
                    elapsed_ms = round((time.monotonic() - started) * 1000)
                    text = item.get("content", "")
                    success = bool(item.get("success", True))
                    yield {"type": "tool_result", "id": item.get("tool_call_id", ""), "content": text, "round": round_index,
                           "success": success, "elapsed_ms": elapsed_ms}
                    self.messages.append({key: value for key, value in item.items() if key != "success"})
                    run_logger.log_event("tool_result_received", component="chat", round=round_index, tool_call_id=item.get("tool_call_id"), result=run_logger.save_payload("tool_result", item))
                    if cancel_event is not None and cancel_event.is_set():
                        yield {"type": "turn_status", "stage": "cancelled", "message": "已取消"}
                        yield {"type": "cancelled"}
                        return
                yield {"type": "turn_status", "stage": "summarizing", "message": "正在整理执行结果", "round": round_index}
        except Exception as exc:
            run_logger.log_exception("chat_bridge_failed", exc, component="chat")
            yield {"type": "turn_status", "stage": "failed", "message": "执行失败"}
            yield {"type": "stream_error", "message": str(exc)}
