"""MACtrl FastAPI static page and single-connection streaming WebSocket server."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from scdw.frontend.chat_bridge import StreamingChat
from scdw.frontend.events import to_json_safe, validate_event_payload
from scdw.llm.providers.deepseek import DeepSeekProvider
from scdw.mcp.client import MCPClient
from scdw.common.run_logging import get_run_logger
from scdw.common.resources import mac_logo_path

PORT = int(os.environ.get("FRONTEND_PORT", "17788"))
STATIC_DIR = Path(__file__).parent / "static"
_template: StreamingChat | None = None
_init_error: str | None = None
_tool_lock = asyncio.Lock()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _template, _init_error
    _template = None
    _init_error = None
    async with AsyncExitStack() as stack:
        run_logger = get_run_logger()
        run_logger.log_event("fastapi_lifespan_started", component="frontend")
        try:
            client = await stack.enter_async_context(
                MCPClient(command=sys.executable, args=[str(PROJECT_ROOT / "mcp_server.py")])
            )
            _template = StreamingChat(
                doc_client=client, clients={"doc_client": client}, deepseek_service=DeepSeekProvider()
            )
            run_logger.log_event("frontend_initialized", component="frontend")
        except Exception as exc:
            logger.exception("MACtrl initialization failed")
            _init_error = str(exc)
            run_logger.log_exception("frontend_initialization_failed", exc, component="frontend")
        yield
        run_logger.log_event("fastapi_lifespan_stopped", component="frontend")


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root() -> HTMLResponse:
    get_run_logger().log_event("http_root_requested", component="frontend")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(
        html.replace("</head>", f"<script>window.BACKEND_PORT={PORT}</script></head>", 1),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/health")
async def health() -> dict:
    get_run_logger().log_event("http_health_requested", component="frontend", ready=_template is not None and _init_error is None)
    return {"server": "running", "ready": _template is not None and _init_error is None, "init_error": _init_error}


@app.get("/static/{asset_path:path}")
async def asset(asset_path: str):
    path = (STATIC_DIR / asset_path).resolve()
    if STATIC_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@app.get("/assets/logo/mac_logo.png")
async def mac_logo() -> FileResponse:
    """提供 GUI 主 Logo，路径由统一资源模块解析。"""
    path = mac_logo_path()
    if not path.is_file():
        raise HTTPException(404, "MACtrl Logo 资源不存在")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    run_logger = get_run_logger()
    conversation_id = uuid.uuid4().hex
    run_logger.log_event("websocket_connected", component="frontend", conversation_id=conversation_id, client=str(websocket.client))
    if _init_error or _template is None:
        await websocket.send_json({"type": "init_error", "message": _init_error or "MACtrl 尚未就绪"})
        return

    chat = StreamingChat(
        doc_client=_template.doc_client, clients=_template.clients, deepseek_service=_template.deepseek_service
    )
    active: asyncio.Task | None = None
    cancel = asyncio.Event()
    current_turn = ""
    send_lock = asyncio.Lock()

    async def send_event(payload: dict) -> None:
        safe_payload = to_json_safe(payload)
        validate_event_payload(safe_payload)
        async with send_lock:
            await websocket.send_json(safe_payload)
        run_logger.log_event("websocket_event_sent", component="frontend", conversation_id=conversation_id, event_type=safe_payload.get("type"), turn_id=safe_payload.get("turn_id"))

    async def safe_send_terminal(kind: str, turn_id: str, message: str | None = None) -> None:
        payload: dict[str, str] = {"type": kind, "turn_id": turn_id}
        if message:
            payload["message"] = message
        try:
            async with send_lock:
                await websocket.send_json(payload)
        except Exception:
            logger.exception("Unable to send terminal WebSocket event")

    async def run_turn(query: str, mode: str, turn_id: str, cancel_event: asyncio.Event) -> None:
        terminal_sent = False
        run_logger.log_event("turn_started", component="frontend", conversation_id=conversation_id, turn_id=turn_id, mode=mode, query=run_logger.save_payload("user_query", query))
        try:
            async with _tool_lock:
                async for event in chat.run_stream(query, mode, cancel_event):
                    payload = {**event, "turn_id": turn_id}
                    await send_event(payload)
                    if payload.get("type") in {"turn_end", "cancelled", "stream_error"}:
                        terminal_sent = True
            run_logger.log_event("turn_completed", component="frontend", conversation_id=conversation_id, turn_id=turn_id, terminal_sent=terminal_sent)
        except asyncio.CancelledError:
            run_logger.log_event("turn_cancelled", component="frontend", conversation_id=conversation_id, turn_id=turn_id)
            if not terminal_sent:
                await safe_send_terminal("cancelled", turn_id, "生成任务已取消。")
            raise
        except Exception as exc:
            logger.exception("MACtrl turn failed: turn_id=%s", turn_id)
            run_logger.log_exception("turn_failed", exc, component="frontend", conversation_id=conversation_id, turn_id=turn_id)
            if not terminal_sent:
                # Do not expose upstream exception text; it can contain request details.
                await safe_send_terminal("stream_error", turn_id, "生成过程发生异常，请重试。")

    def on_turn_done(task: asyncio.Task) -> None:
        nonlocal active
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("MACtrl turn task ended unexpectedly")
        finally:
            if active is task:
                active = None

    await send_event({"type": "ready"})
    try:
        while True:
            msg = await websocket.receive_json()
            kind = msg.get("type")
            run_logger.log_event("websocket_message_received", component="frontend", conversation_id=conversation_id, event_type=kind, payload=run_logger.save_payload("websocket_message", msg))
            if kind == "client_log":
                run_logger.log_event("browser_client_log", component="browser", conversation_id=conversation_id, level=msg.get("level"), message=msg.get("message"), details=msg.get("details"))
                continue
            if kind == "cancel":
                if active and not active.done() and current_turn:
                    cancel.set()
                    await send_event({"type": "cancel_requested", "turn_id": current_turn})
                continue
            if kind == "clear":
                cancel.set()
                if active and not active.done():
                    try:
                        await active
                    except asyncio.CancelledError:
                        pass
                chat.reset_conversation()
                current_turn = ""
                await send_event({"type": "cleared"})
                continue
            if kind != "query":
                continue
            if active and not active.done():
                await send_event({"type": "error", "message": "当前回合仍在处理中。", "turn_id": msg.get("turn_id")})
                continue
            mode = msg.get("mode", "thinking")
            if mode not in {"thinking", "fast"}:
                await send_event({"type": "error", "message": "无效模式。", "turn_id": msg.get("turn_id")})
                continue
            query = str(msg.get("content", "")).strip()
            if not query:
                continue
            current_turn = str(msg.get("turn_id") or f"turn-{asyncio.get_running_loop().time():.6f}")
            cancel = asyncio.Event()
            active = asyncio.create_task(run_turn(query, mode, current_turn, cancel))
            active.add_done_callback(on_turn_done)
    except WebSocketDisconnect:
        cancel.set()
        run_logger.log_event("websocket_disconnected", component="frontend", conversation_id=conversation_id)
