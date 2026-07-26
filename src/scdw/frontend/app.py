"""
frontend/app.py

FastAPI application that:
  - Serves the frontend HTML (with injected port)
  - Provides a /ws WebSocket endpoint that streams chat events
  - Manages the MCP client lifecycle via FastAPI lifespan
"""
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── 路径初始化 ────────────────────────────────────────────────────────────────
# 文件路径：<项目根>/src/scdw/frontend/app.py。
# 保留项目根常量供 .env、根目录兼容 MCP 入口和静态资源定位使用。
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from scdw.mcp.client import MCPClient
from scdw.llm.providers.deepseek import DeepSeekProvider
from scdw.frontend.chat_bridge import StreamingChat

# ── config ────────────────────────────────────────────────────────────────────
PORT: int = int(os.environ.get("FRONTEND_PORT", "17788"))
STATIC_DIR = Path(__file__).parent / "static"

# ── shared state ──────────────────────────────────────────────────────────────
_chat: Optional[StreamingChat] = None
_init_error: Optional[str] = None
_query_lock: Optional[asyncio.Lock] = None


# ── lifespan: start MCP clients, build chat instance ─────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    global _chat, _init_error, _query_lock

    _query_lock = asyncio.Lock()

    from scdw.common.config import get_deepseek_model
    deepseek_model = get_deepseek_model()

    mcp_server_path = str(PROJECT_ROOT / "mcp_server.py")

    try:
        async with AsyncExitStack() as stack:
            doc_client: MCPClient = await stack.enter_async_context(
                # Use sys.executable so the subprocess uses the same interpreter
                # (important inside Conda / venv environments)
                MCPClient(command=sys.executable, args=[mcp_server_path])
            )
            clients = {"doc_client": doc_client}

            deepseek_service = DeepSeekProvider(model=deepseek_model)

            _chat = StreamingChat(
                doc_client=doc_client,
                clients=clients,
                deepseek_service=deepseek_service,
            )

            yield   # server is running

    except Exception as exc:
        import traceback
        _init_error = traceback.format_exc()
        print(f"[MAC-TIACompleter] Lifespan error:\n{_init_error}", flush=True)
        yield


# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root() -> HTMLResponse:
    """Serve the frontend HTML with the backend port injected."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    inject = f'<script>window.BACKEND_PORT = {PORT};</script>'
    html = html.replace("</head>", f"{inject}\n</head>", 1)
    return HTMLResponse(html)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    # ── guard: initialization failed ─────────────────────────────────────────
    if _init_error:
        await websocket.send_json({
            "type": "init_error",
            "message": f"Agent 初始化失败:\n{_init_error}",
        })
        # Keep WS open so the frontend can display the error persistently
        try:
            while True:
                await asyncio.sleep(30)
        except (WebSocketDisconnect, Exception):
            pass
        return

    if _chat is None:
        await websocket.send_json({
            "type": "init_error",
            "message": "Agent 尚未就绪，请稍候几秒后刷新页面。",
        })
        await websocket.close()
        return

    # ── notify client that backend is ready ──────────────────────────────────
    await websocket.send_json({"type": "ready"})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            # ── clear conversation history ────────────────────────────────────
            if msg_type == "clear":
                # Keep only the system prompt (first message) to preserve identity
                if _chat.messages and _chat.messages[0].get("role") == "system":
                    _chat.messages[1:] = []
                else:
                    _chat.messages.clear()
                await websocket.send_json({"type": "cleared"})
                continue

            if msg_type != "query":
                continue

            query = msg.get("content", "").strip()
            if not query:
                continue

            # ── run query (one at a time via lock) ────────────────────────────
            if _query_lock.locked():
                await websocket.send_json({
                    "type": "error",
                    "message": "正在处理上一条消息，请稍候。",
                })
                continue

            async with _query_lock:
                async for event in _chat.run_stream(query):
                    await websocket.send_json(event)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
