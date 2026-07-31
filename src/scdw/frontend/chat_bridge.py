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
from scdw.common.config import get_tool_budget
from scdw.common.tool_results import tool_result
from scdw.llm.providers.deepseek import LlmStreamResult
from scdw.common.run_logging import get_run_logger
from scdw.frontend.events import summarize_tool_arguments
from scdw.lad_generation import LadPlanService

TOOL_DISPLAY_NAMES = {"refresh_tia_context": "刷新 TIA 上下文", "get_tia_context": "读取 TIA 工程信息",
                      "connect_to_open_tia": "连接已打开的 TIA", "import_and_compile_artifact": "导入并编译 Artifact",
                      "import_scl_block": "导入 SCL 程序块",
                      "create_instance_db": "创建背景 DB", "save_verified_project": "保存已验证项目",
                      "create_global_db": "创建全局 DB", "create_plc_tag_table": "创建 PLC 变量表",
                      "get_plc_knowledge_catalog": "读取 PLC 知识目录",
                      "get_plc_knowledge_items": "读取 PLC 知识项",
                      "get_lad_capability_catalog": "读取 LAD 能力目录",
                      "check_and_freeze_lad_blueprint": "检查并冻结 LAD 蓝图",
                      "write_lad_network_from_blueprint": "按蓝图生成 Network",
                      "repair_lad_xml_expression": "修复 XML 表达"}
TOOL_ACTIVITY_MESSAGES = {
    "import_and_compile_artifact": "正在导入 Artifact 并执行块级编译",
    "create_instance_db": "正在创建并绑定背景 DB",
}

_READ_CACHE_TOOLS = {
    "get_lad_capability_catalog", "get_plc_knowledge_catalog", "get_plc_knowledge_items",
    "get_lad_generation_plan", "list_lad_generation_plans",
    "get_xml_artifact_status", "get_lad_block_info", "list_xml_networks",
    "get_xml_network", "read_xml_fragment", "list_xml_artifacts",
    "get_tia_context", "list_tia_processes", "list_workspace_files",
}
_SINGLE_READ_TOOLS = {"get_lad_capability_catalog", "get_plc_knowledge_catalog", "get_plc_knowledge_items"}
_MUTATING_TOOLS = {
    "init_tia_project", "close_tia_session", "detach_tia_session",
    "add_plc_to_project", "add_hardware_module", "create_plc_tag_table",
    "create_global_db", "import_scl_block", "delete_plc_block",
    "save_lad_generation_plan", "revise_lad_network_plan",
    "create_xml_artifact", "create_lad_block_artifact", "patch_xml_artifact",
    "append_network_and_prepare_import", "write_lad_network_from_knowledge",
    "replace_network_and_prepare_import", "delete_xml_network", "update_xml_network_text",
    "import_and_compile_artifact", "create_instance_db", "save_verified_project",
    "check_and_freeze_lad_blueprint", "write_lad_network_from_blueprint", "repair_lad_xml_expression",
    "reconcile_lad_workflow",
}


def _merge_workflow_context(target: dict[str, Any], value: Any) -> None:
    """Collect stable recovery identifiers from compact or legacy tool results."""
    if isinstance(value, dict):
        if value.get("needs_user_action") is True:
            target["needs_user_action"] = True
        if value.get("success") is True and value.get("stage") == "tia_save":
            target["project_saved"] = True
        if value.get("action"):
            target["next_action"] = value["action"]
        for key in ("plan_id", "artifact_id", "version", "network_key", "device_name", "block_name"):
            item = value.get(key)
            if item not in (None, ""):
                target[key] = item
        for child in value.values():
            _merge_workflow_context(target, child)
    elif isinstance(value, list):
        for child in value:
            _merge_workflow_context(target, child)


def _execution_requested(query: str) -> bool:
    lowered = query.lower()
    if any(token in lowered for token in ("只回答", "不做修改", "不要修改", "仅解释", "why", "为什么")):
        return False
    return any(token in lowered for token in (
        "创建", "新建", "添加", "生成", "编写", "导入", "编译", "保存",
        "构建", "实现", "完成", "配置", "implement", "fix", "修改",
    ))


def _requires_verified_save(query: str) -> bool:
    lowered = query.lower()
    return any(token in lowered for token in ("梯形图", "lad", "程序", "导入", "编译", "保存项目"))


def _must_continue_workflow(
    query: str,
    workflow_context: dict[str, Any],
    tool_trace: list[dict[str, Any]],
    successful_mutations: set[tuple[str, str]],
) -> bool:
    """Code-level completion gate for autonomous, recoverable workflows."""
    if not _execution_requested(query) or workflow_context.get("needs_user_action") is True:
        return False
    if workflow_context.get("project_saved") is True:
        return False
    next_action = workflow_context.get("next_action")
    if next_action and next_action not in {"complete", "done", "none"}:
        return True
    if not successful_mutations:
        return True
    if tool_trace and tool_trace[-1].get("success") is False:
        return True
    return _requires_verified_save(query)


class StreamingChat(CliChat):
    """把 Provider 的真实 chunk 转换为前端可渲染事件。"""
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def reset_conversation(self) -> None:
        """清除用户、模型和工具历史，仅保留系统身份。"""
        self.messages[:] = self.messages[:1]

    def _resume_context(self, query: str) -> dict[str, Any] | None:
        if query.strip().lower() not in {"继续", "接着", "恢复", "continue", "resume"}:
            return {}
        conversation_id = getattr(self, "conversation_id", None)
        if not conversation_id:
            return None
        service = LadPlanService()
        plans = [item for item in service.list(conversation_id) if item.status == "active"]
        draft = service.find_active_draft(conversation_id)
        if not plans and draft is None:
            return None
        plan = plans[0] if plans else None
        return {
            "conversation_id": conversation_id,
            "active_plan_id": plan.plan_id if plan else None,
            "active_draft_id": draft[0] if draft else None,
            "current_network": plan.current_network if plan else draft[1].current_network,
            "next_action": service.next_step(plan.plan_id) if plan else "validate_and_save_lad_plan",
        }

    async def run_stream(self, query: str, mode: str = "thinking", cancel_event: Any = None) -> AsyncGenerator[dict[str, Any], None]:
        run_logger = get_run_logger()
        run_logger.log_event("chat_bridge_query", component="chat", mode=mode, query=run_logger.save_payload("chat_query", query), history_size=len(self.messages))
        # Emit immediately; query/context preparation can itself take noticeable time.
        yield {"type": "turn_start", "mode": mode, "round": 0}
        yield {"type": "turn_status", "stage": "analyzing", "message": "正在分析需求"}
        resume = self._resume_context(query)
        if resume is None:
            failure = {"success": False, "stage": "workflow_resume", "code": "NO_ACTIVE_WORKFLOW",
                       "message": "没有可恢复的 active draft 或 Plan。", "retryable": False, "needs_user_action": False}
            yield {"type": "stream_error", "message": json.dumps(failure, ensure_ascii=False)}
            return
        if resume:
            query = "恢复已中断工作流。先调用 reconcile_lad_workflow（如有 Plan），再按 next_action 完成一个短增量步骤：" + json.dumps(resume, ensure_ascii=False)
            run_logger.log_event("workflow_resume_requested", component="chat", recovery=resume)
        await self._process_query(query)
        tia_prompt = await self._tia_context_prompt()
        soft_limit, hard_limit = get_tool_budget()
        tool_schemas = await ToolManager.get_all_tools(self.clients)
        successful_mutations: set[tuple[str, str]] = set()
        read_cache: dict[tuple[str, str], dict[str, Any]] = {}
        workflow_context: dict[str, Any] = {}
        tool_trace: list[dict[str, Any]] = []
        tool_call_count = 0
        soft_warning_sent = False
        completion_nudges = 0
        truncation_retries = 0
        try:
            for round_index in range(hard_limit + 2):
                yield {"type": "turn_status", "stage": "generating", "message": "正在生成回复", "round": round_index}
                run_logger.log_event("llm_round_started", component="chat", round=round_index, history_size=len(self.messages))
                messages = ([{"role": "system", "content": tia_prompt}] if tia_prompt else []) + list(self.messages)
                result: LlmStreamResult | None = None
                async for event in self.deepseek_service.stream_chat(messages, tools=tool_schemas, mode=mode, cancel_event=cancel_event):
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
                # A length-limited response can contain only the first half of
                # a tool call.  Never persist or execute that partial call.
                if (result.finish_reason or "").lower() in {"length", "max_tokens"}:
                    truncation_retries += 1
                    recovery = {
                        "success": False, "stage": "model_output", "code": "MODEL_OUTPUT_TRUNCATED",
                        "message": "模型输出被长度限制截断；未执行或写入任何不完整工具调用。",
                        "finish_reason": result.finish_reason, "retryable": True,
                        "needs_user_action": False, "recovery": workflow_context,
                    }
                    run_logger.log_event("model_output_truncated", component="chat", round=round_index,
                                         finish_reason=result.finish_reason, tool_call_count=len(result.tool_calls), recovery=recovery)
                    yield {"type": "turn_status", "stage": "model_output_truncated", "message": recovery["message"], "round": round_index}
                    if truncation_retries > 2:
                        yield {"type": "stream_error", "message": json.dumps(recovery, ensure_ascii=False)}
                        return
                    self.messages.append({
                        "role": "system",
                        "content": (
                            "上一轮模型输出因长度限制被丢弃，不能复用其中任何 tool_calls。"
                            "请从已保存的 Plan/Artifact 恢复，仅执行一个短的增量下一步；"
                            "不要重新传递完整 Plan 或长 JSON。恢复信息：" + json.dumps(workflow_context, ensure_ascii=False)
                        ),
                    })
                    continue
                if (result.finish_reason or "stop").lower() not in {"tool_calls", "stop"}:
                    failure = {"success": False, "stage": "model_output", "code": "MODEL_OUTPUT_FINISH_REASON_UNSUPPORTED",
                               "message": "模型以未支持的结束原因终止，未执行工具调用。", "finish_reason": result.finish_reason,
                               "retryable": True, "needs_user_action": False}
                    run_logger.log_event("model_output_finish_reason_unsupported", component="chat", round=round_index, failure=failure)
                    yield {"type": "stream_error", "message": json.dumps(failure, ensure_ascii=False)}
                    return
                assistant = {"role": "assistant", "content": result.content or None}
                if result.reasoning_content:
                    assistant["reasoning_content"] = result.reasoning_content
                if result.tool_calls:
                    assistant["tool_calls"] = result.tool_calls
                self.messages.append(assistant)
                if result.finish_reason != "tool_calls":
                    if _must_continue_workflow(query, workflow_context, tool_trace, successful_mutations):
                        completion_nudges += 1
                        self.messages.append({
                            "role": "system",
                            "content": (
                                "代码级完成检查：任务尚未完成，且最近工具未要求用户操作。"
                                "不得结束或询问用户；请立即按当前Plan/Artifact和next动作继续调用工具。"
                                "恢复上下文：" + json.dumps(workflow_context, ensure_ascii=False)
                            ),
                        })
                        run_logger.log_event(
                            "workflow_completion_blocked", component="chat", round=round_index,
                            nudge=completion_nudges, workflow_context=workflow_context,
                        )
                        yield {
                            "type": "turn_status", "stage": "continuing_workflow",
                            "message": "任务尚未验证完成，正在自动继续", "round": round_index,
                        }
                        continue
                    yield {"type": "turn_status", "stage": "completed", "message": "已完成"}
                    yield {"type": "turn_end", "usage": asdict(result.usage), "tool_calls": tool_call_count}
                    return

                yield {"type": "turn_status", "stage": "preparing_tool", "message": "正在准备工具调用", "round": round_index}
                hard_exhausted = False
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
                    read_fingerprint = (
                        function.name,
                        "__workflow_single_read__" if function.name in _SINGLE_READ_TOOLS else canonical,
                    )
                    if tool_call_count >= hard_limit:
                        hard_exhausted = True
                        payload = tool_result(
                            False, stage="tool_budget", code="TOOL_BUDGET_EXHAUSTED",
                            message="本回合工具预算已用尽；请从持久化 Plan 的恢复位置继续。",
                            data={"soft_limit": soft_limit, "hard_limit": hard_limit, "recovery": workflow_context},
                        )
                        item = {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(payload, ensure_ascii=False), "success": False}
                    elif function.name in _MUTATING_TOOLS and fingerprint in successful_mutations:
                        payload = tool_result(
                            True, stage="deduplication", code="NO_CHANGES",
                            message="相同参数的写操作已成功完成；未重复执行。",
                            data={"tool_name": function.name},
                        )
                        item = {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(payload, ensure_ascii=False), "success": True}
                        tool_call_count += 1
                    elif function.name in _READ_CACHE_TOOLS and read_fingerprint in read_cache:
                        cached = read_cache[read_fingerprint]
                        item = {**cached, "tool_call_id": call["id"]}
                        tool_call_count += 1
                    elif soft_warning_sent and function.name in _READ_CACHE_TOOLS:
                        payload = tool_result(
                            False, stage="tool_budget", code="SOFT_BUDGET_READ_BLOCKED",
                            message="软预算后禁止新的非必要只读调用；请使用当前工作流缓存并完成当前 Network。",
                            data={"tool_name": function.name, "recovery": workflow_context},
                        )
                        item = {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(payload, ensure_ascii=False), "success": False}
                        tool_call_count += 1
                    else:
                        item = None

                    if item is not None:
                        try:
                            cached_decoded = json.loads(item.get("content", ""))
                        except (TypeError, json.JSONDecodeError):
                            cached_decoded = None
                        _merge_workflow_context(workflow_context, cached_decoded)
                        self.messages.append({key: value for key, value in item.items() if key != "success"})
                        yield {"type": "tool_result", "id": call["id"], "content": item["content"], "round": round_index, "success": bool(item.get("success")), "elapsed_ms": 0}
                        tool_trace.append({"name": function.name, "success": bool(item.get("success")), "elapsed_ms": 0, "code": "cached_or_blocked"})
                        continue

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
                    tool_call_count += 1
                    text = item.get("content", "")
                    success = bool(item.get("success", True))
                    if success and function.name in _MUTATING_TOOLS:
                        successful_mutations.add(fingerprint)
                        if function.name in {
                            "init_tia_project", "connect_to_open_tia", "add_plc_to_project", "add_hardware_module",
                            "create_plc_tag_table", "create_global_db", "import_scl_block", "import_and_compile_artifact",
                            "create_instance_db", "save_verified_project",
                        }:
                            self.invalidate_tia_context()
                    if success and function.name in _READ_CACHE_TOOLS:
                        read_cache[read_fingerprint] = dict(item)
                    try:
                        decoded = json.loads(text)
                    except (TypeError, json.JSONDecodeError):
                        decoded = None
                    _merge_workflow_context(workflow_context, decoded)
                    tool_trace.append({"name": function.name, "success": success, "elapsed_ms": elapsed_ms})
                    yield {"type": "tool_result", "id": item.get("tool_call_id", ""), "content": text, "round": round_index,
                           "success": success, "elapsed_ms": elapsed_ms}
                    self.messages.append({key: value for key, value in item.items() if key != "success"})
                    run_logger.log_event("tool_result_received", component="chat", round=round_index, tool_call_id=item.get("tool_call_id"), result=run_logger.save_payload("tool_result", item))
                    if cancel_event is not None and cancel_event.is_set():
                        yield {"type": "turn_status", "stage": "cancelled", "message": "已取消"}
                        yield {"type": "cancelled"}
                        return
                if hard_exhausted:
                    summary = {
                        "success": False,
                        "stage": "tool_budget",
                        "code": "TOOL_BUDGET_EXHAUSTED",
                        "message": "本回合已安全暂停，可从 Plan 恢复，不会从头开始。",
                        "data": {
                            "tool_calls": tool_call_count,
                            "soft_limit": soft_limit,
                            "hard_limit": hard_limit,
                            "recovery": workflow_context,
                            "unfinished_steps": ["恢复 active Plan", "完成当前 Network 导入与编译", "继续后续 Network"],
                        },
                        "retryable": True,
                        "needs_user_action": False,
                    }
                    self.messages.append({"role": "assistant", "content": json.dumps(summary, ensure_ascii=False)})
                    run_logger.log_event("tool_budget_exhausted", component="chat", summary=summary, tool_trace=tool_trace)
                    yield {"type": "turn_status", "stage": "paused", "message": "工具预算已用尽，已保存恢复位置"}
                    yield {"type": "turn_end", "usage": asdict(result.usage), "tool_calls": tool_call_count, "paused": True, "recovery": summary["data"]}
                    return
                if tool_call_count >= soft_limit and not soft_warning_sent:
                    soft_warning_sent = True
                    counts: dict[str, int] = {}
                    for trace in tool_trace:
                        counts[trace["name"]] = counts.get(trace["name"], 0) + 1
                    budget_summary = {
                        "tool_calls": tool_call_count,
                        "soft_limit": soft_limit,
                        "hard_limit": hard_limit,
                        "by_tool": counts,
                        "recovery": workflow_context,
                    }
                    self.messages.append({
                        "role": "system",
                        "content": "工具软预算已达到。禁止重复读取状态或知识；合并后续操作，优先使用 import_and_compile_artifact 完成当前 Network，然后直接继续未验证 Network。当前摘要：" + json.dumps(budget_summary, ensure_ascii=False),
                    })
                    run_logger.log_event("tool_budget_soft_warning", component="chat", summary=budget_summary)
                    yield {"type": "tool_budget_warning", **budget_summary}
                yield {"type": "turn_status", "stage": "summarizing", "message": "正在整理执行结果", "round": round_index}
            yield {"type": "turn_status", "stage": "paused", "message": "已保存恢复位置"}
            yield {
                "type": "turn_end", "tool_calls": tool_call_count, "paused": True,
                "recovery": {"reason": "completion_round_limit", **workflow_context},
            }
        except Exception as exc:
            run_logger.log_exception("chat_bridge_failed", exc, component="chat")
            yield {"type": "turn_status", "stage": "failed", "message": "执行失败"}
            yield {"type": "stream_error", "message": str(exc)}
