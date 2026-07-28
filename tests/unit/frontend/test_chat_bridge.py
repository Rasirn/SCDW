import asyncio
import json

import pytest

from scdw.frontend.chat_bridge import StreamingChat
from scdw.llm.providers.deepseek import LlmStreamResult, LlmUsage


class _DocClient:
    async def list_prompts(self):
        return []

    async def read_resource(self, _):
        return []


class _Provider:
    async def stream_chat(self, *_, **__):
        yield {"type": "answer_start"}
        yield {"type": "answer_delta", "content": "hello"}
        yield {"type": "usage", "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        yield {"type": "answer_end"}
        yield {"type": "stream_end", "result": LlmStreamResult("hello", "", [], "stop", "test", LlmUsage(10, 20, 30))}


@pytest.mark.unit
def test_normal_stream_keeps_usage_json_safe_and_emits_turn_end(monkeypatch):
    async def no_context(_):
        return ""

    monkeypatch.setattr("scdw.frontend.chat_bridge.ToolManager.get_all_tools", no_context)
    chat = StreamingChat(doc_client=_DocClient(), clients={}, deepseek_service=_Provider())

    async def collect():
        return [event async for event in chat.run_stream("hello", "fast")]

    events = asyncio.run(collect())
    assert [event["type"] for event in events] == ["turn_start", "turn_status", "turn_status", "answer_start", "answer_delta", "usage", "answer_end", "turn_status", "turn_end"]
    assert events[1] == {"type": "turn_status", "stage": "analyzing", "message": "正在分析需求"}
    for event in events:
        json.dumps(event)
    assert events[-1]["usage"] == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}


class _ToolProvider:
    def __init__(self):
        self.round = 0

    async def stream_chat(self, *_, **__):
        if self.round == 0:
            result = LlmStreamResult(
                "", "", [{"id": "call-1", "function": {"name": "save_lad_xml", "arguments": json.dumps({"block_name": "Motor", "xml_content": "<x>" + "a" * 1000 + "</x>"})}}],
                "tool_calls", "test", LlmUsage(),
            )
        else:
            result = LlmStreamResult("done", "", [], "stop", "test", LlmUsage())
        self.round += 1
        yield {"type": "stream_end", "result": result}


@pytest.mark.unit
def test_tool_has_independent_heartbeat_elapsed_and_stops_after_result(monkeypatch):
    async def no_tools(_):
        return []

    async def slow_tool(_clients, request):
        await asyncio.sleep(1.05)
        return {"role": "tool", "tool_call_id": request.id, "content": "保存路径：data/Motor.xml\nXML 大小：1007 字节", "success": True}

    monkeypatch.setattr("scdw.frontend.chat_bridge.ToolManager.get_all_tools", no_tools)
    monkeypatch.setattr("scdw.frontend.chat_bridge.ToolManager.execute_tool_request", slow_tool)
    chat = StreamingChat(doc_client=_DocClient(), clients={}, deepseek_service=_ToolProvider())

    async def collect():
        return [event async for event in chat.run_stream("save", "fast")]

    events = asyncio.run(collect())
    start = next(event for event in events if event["type"] == "tool_call_start")
    progress = [event for event in events if event["type"] == "tool_progress"]
    result = next(event for event in events if event["type"] == "tool_result")
    assert "<x>" not in json.dumps(start, ensure_ascii=False)
    assert progress and progress[0]["id"] == "call-1" and progress[0]["elapsed_ms"] >= 900
    assert result["success"] is True and result["elapsed_ms"] >= progress[-1]["elapsed_ms"]
    assert not any(event["type"] == "tool_progress" for event in events[events.index(result) + 1:])


@pytest.mark.unit
def test_cancel_during_tool_waits_for_safe_completion_then_allows_terminal_cancel(monkeypatch):
    async def no_tools(_):
        return []

    cancel = asyncio.Event()

    async def slow_tool(_clients, request):
        await asyncio.sleep(1.05)
        return {"role": "tool", "tool_call_id": request.id, "content": "ok", "success": True}

    monkeypatch.setattr("scdw.frontend.chat_bridge.ToolManager.get_all_tools", no_tools)
    monkeypatch.setattr("scdw.frontend.chat_bridge.ToolManager.execute_tool_request", slow_tool)
    chat = StreamingChat(doc_client=_DocClient(), clients={}, deepseek_service=_ToolProvider())

    async def collect():
        async def request_cancel():
            await asyncio.sleep(.1)
            cancel.set()
        cancel_task = asyncio.create_task(request_cancel())
        values = [event async for event in chat.run_stream("save", "fast", cancel)]
        await cancel_task
        return values

    events = asyncio.run(collect())
    waiting = [event for event in events if event["type"] == "tool_progress" and event["stage"] == "waiting_safe_stop"]
    assert waiting and waiting[0]["message"] == "正在等待当前安全步骤结束后停止"
    assert events[-1]["type"] == "cancelled"
    result_index = next(index for index, event in enumerate(events) if event["type"] == "tool_result")
    assert result_index < len(events) - 1
