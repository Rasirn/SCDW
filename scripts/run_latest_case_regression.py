"""Replay the latest failed user case through the real single-LLM/TIA workflow."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from contextlib import AsyncExitStack
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scdw.common.config import get_deepseek_api_key, get_deepseek_model
from scdw.common.run_logging import get_run_logger
from scdw.frontend.chat_bridge import StreamingChat
from scdw.llm.providers.deepseek import DeepSeekProvider
from scdw.mcp.client import MCPClient


SOURCE_RUN = ROOT / "data" / "logs" / "20260731_003823_544_8f9c44"


def failed_case_query() -> str:
    for line in (SOURCE_RUN / "conversation.md").read_text(encoding="utf-8").splitlines():
        if "`turn_started`" not in line:
            continue
        payload = json.loads(re.split(r"`turn_started`：", line, maxsplit=1)[1])
        value = payload.get("query", {}).get("inline")
        if value and value != "都可以":
            return value
    raise RuntimeError("latest failed case query was not found")


async def drain(chat: StreamingChat, query: str, turn_label: str) -> dict:
    logger = get_run_logger()
    turn_id = f"regression-{turn_label}-{uuid.uuid4().hex[:8]}"
    logger.log_event("turn_started", component="regression", turn_id=turn_id, mode="thinking", query=logger.save_payload("user_query", query))
    terminal: dict = {}
    async for event in chat.run_stream(query, mode="thinking"):
        if event.get("type") in {"tool_call_start", "tool_call_end", "turn_end", "stream_error"}:
            print(json.dumps(event, ensure_ascii=False, default=str), flush=True)
        if event.get("type") in {"turn_end", "stream_error", "cancelled"}:
            terminal = event
    logger.log_event("turn_completed", component="regression", turn_id=turn_id, terminal=terminal)
    return terminal


async def main() -> int:
    if not get_deepseek_api_key():
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    logger = get_run_logger()
    command = sys.executable
    args = [str(ROOT / "mcp_server.py")]
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(MCPClient(command=command, args=args))
        chat = StreamingChat(
            doc_client=client,
            clients={"tia_client": client},
            deepseek_service=DeepSeekProvider(model=get_deepseek_model()),
        )
        first = await drain(chat, failed_case_query(), "initial")
        second = await drain(chat, "都可以", "continuation")
    logger.flush()
    result = {"run_id": logger.run_id, "run_dir": str(logger.run_dir), "initial": first, "continuation": second}
    print("REGRESSION_RESULT=" + json.dumps(result, ensure_ascii=False, default=str), flush=True)
    return 0 if second.get("type") == "turn_end" and not second.get("paused") else 2


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
