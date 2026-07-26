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
    assert [event["type"] for event in events] == ["turn_start", "answer_start", "answer_delta", "usage", "answer_end", "turn_end"]
    for event in events:
        json.dumps(event)
    assert events[-1]["usage"] == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
