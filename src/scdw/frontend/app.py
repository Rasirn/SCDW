"""MACtrl 的 FastAPI 静态页和单连接流式 WebSocket 服务。"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT / "src") not in sys.path: sys.path.insert(0, str(PROJECT_ROOT / "src"))
from scdw.frontend.chat_bridge import StreamingChat
from scdw.llm.providers.deepseek import DeepSeekProvider
from scdw.mcp.client import MCPClient

PORT = int(os.environ.get("FRONTEND_PORT", "17788")); STATIC_DIR = Path(__file__).parent / "static"
_template: StreamingChat | None = None; _init_error: str | None = None; _tool_lock = asyncio.Lock()

@asynccontextmanager
async def lifespan(_: FastAPI):
    global _template, _init_error
    try:
        async with AsyncExitStack() as stack:
            client = await stack.enter_async_context(MCPClient(command=sys.executable, args=[str(PROJECT_ROOT / "mcp_server.py")]))
            _template = StreamingChat(doc_client=client, clients={"doc_client": client}, deepseek_service=DeepSeekProvider())
            yield
    except Exception as exc:
        _init_error = str(exc); yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("</head>", f"<script>window.BACKEND_PORT={PORT}</script></head>", 1))

@app.get("/static/{asset_path:path}")
async def asset(asset_path: str):
    path = (STATIC_DIR / asset_path).resolve()
    if STATIC_DIR.resolve() not in path.parents or not path.is_file(): raise HTTPException(404)
    return FileResponse(path)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    if _init_error or _template is None:
        await websocket.send_json({"type": "init_error", "message": _init_error or "MACtrl 尚未就绪"}); return
    chat = StreamingChat(doc_client=_template.doc_client, clients=_template.clients, deepseek_service=_template.deepseek_service)
    active: asyncio.Task | None = None; cancel = asyncio.Event(); current_turn = ""
    send_lock = asyncio.Lock()
    async def send_event(payload: dict) -> None:
        async with send_lock:
            await websocket.send_json(payload)
    async def run_turn(query: str, mode: str, turn_id: str) -> None:
        async with _tool_lock:
            async for event in chat.run_stream(query, mode, cancel):
                event["turn_id"] = turn_id; await send_event(event)
    await send_event({"type": "ready"})
    try:
        while True:
            msg = await websocket.receive_json(); kind = msg.get("type")
            if kind == "cancel": cancel.set(); await websocket.send_json({"type":"cancel_requested", "turn_id":current_turn}); continue
            if kind == "clear":
                cancel.set()
                if active and not active.done():
                    try: await active
                    except asyncio.CancelledError: pass
                chat.reset_conversation(); current_turn = ""; await send_event({"type":"cleared"}); continue
            if kind != "query": continue
            if active and not active.done():
                await websocket.send_json({"type":"error", "message":"当前回合仍在处理。", "turn_id":msg.get("turn_id")}); continue
            mode = msg.get("mode", "thinking")
            if mode not in {"thinking", "fast"}:
                await websocket.send_json({"type":"error", "message":"无效模式。", "turn_id":msg.get("turn_id")}); continue
            query = str(msg.get("content", "")).strip()
            if not query: continue
            current_turn = str(msg.get("turn_id") or f"turn-{asyncio.get_running_loop().time():.6f}"); cancel = asyncio.Event()
            active = asyncio.create_task(run_turn(query, mode, current_turn))
    except WebSocketDisconnect:
        cancel.set()
