"""
frontend/main_gui.py

Entry point for the MAC-TIACompleter GUI.

Usage (from project root):
    python frontend/main_gui.py

Starts a FastAPI/uvicorn server in a background thread, then opens a
pywebview window (always-on-top, resizable) pointing to the local server.
Falls back to the system browser if pywebview is not installed.
"""
import os
import socket
import sys
import time
import threading
import webbrowser
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

# ── 支持直接执行本文件与包方式执行 ──────────────────────────────────────────────
# 文件路径：<项目根>/src/scdw/frontend/main_gui.py。
# 直接执行时 Python 不会自动将 <项目根>/src 加入 sys.path。
SRC_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# ── port selection ─────────────────────────────────────────────────────────────
def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _find_free_port()
os.environ["FRONTEND_PORT"] = str(PORT)
URL = f"http://127.0.0.1:{PORT}/"
_LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_BACKEND_SERVER: Any | None = None
_BACKEND_ERROR: BaseException | None = None


# ── server thread ──────────────────────────────────────────────────────────────
def _run_server() -> None:
    """Run uvicorn in its own thread with a fresh event loop."""
    global _BACKEND_SERVER, _BACKEND_ERROR
    import asyncio
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    from scdw.common.run_logging import get_run_logger
    run_logger = get_run_logger()
    run_logger.log_event("backend_thread_started", component="gui", port=PORT)
    try:
        import uvicorn
        from scdw.frontend.app import app  # 在设置端口后导入应用

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=PORT,
            log_level="warning",
            reload=False,
        )
        _BACKEND_SERVER = uvicorn.Server(config)
        _BACKEND_SERVER.run()
        run_logger.log_event(
            "backend_thread_stopped",
            component="gui",
            port=PORT,
            started=bool(getattr(_BACKEND_SERVER, "started", False)),
        )
    except BaseException as exc:
        _BACKEND_ERROR = exc
        run_logger.log_exception("backend_thread_failed", exc, component="gui", port=PORT)


def _http_health_reachable() -> bool:
    """Probe loopback directly without inheriting system HTTP proxy settings."""
    try:
        with _LOCAL_HTTP.open(f"http://127.0.0.1:{PORT}/health", timeout=0.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _wait_for_server(timeout: float = 30.0, server_thread: threading.Thread | None = None) -> bool:
    """Wait for Uvicorn's actual listen state, retaining HTTP as a fallback."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        server = _BACKEND_SERVER
        if server is not None and bool(getattr(server, "started", False)):
            return True
        if _BACKEND_ERROR is not None:
            return False
        if server_thread is not None and not server_thread.is_alive():
            return False
        if _http_health_reachable():
            return True
        time.sleep(0.25)
    return False


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    from scdw.common.run_logging import RunLogManager, set_run_logger
    run_logger = RunLogManager.create_run(frontend_port=PORT)
    set_run_logger(run_logger)
    run_logger.log_event("gui_launch_requested", url=URL)
    # Start backend server
    server_thread = threading.Thread(target=_run_server, daemon=True, name="uvicorn")
    server_thread.start()

    if not _wait_for_server(server_thread=server_thread):
        details = {
            "port": PORT,
            "thread_alive": server_thread.is_alive(),
            "server_created": _BACKEND_SERVER is not None,
            "server_started": bool(getattr(_BACKEND_SERVER, "started", False)),
            "error": str(_BACKEND_ERROR) if _BACKEND_ERROR is not None else None,
        }
        run_logger.log_event("backend_start_failed", **details)
        reason = f"：{_BACKEND_ERROR}" if _BACKEND_ERROR is not None else "，详情见最新 runtime.log"
        print(f"[MACtrl] 服务未能启动{reason}。", file=sys.stderr)
        sys.exit(1)

    run_logger.log_event(
        "backend_listening",
        component="gui",
        port=PORT,
        detection="uvicorn_started" if bool(getattr(_BACKEND_SERVER, "started", False)) else "http_health",
    )

    # Try pywebview for a floating native window
    try:
        import webview  # type: ignore[import]

        webview.create_window(
            title="MACtrl · TIA 智控助手",
            url=URL,
            width=1280,
            height=820,
            min_size=(920, 660),
            on_top=False,
            resizable=True,
            frameless=False,
            background_color="#f4f6f8",
        )
        webview.start()
        run_logger.log_event("gui_window_closed")

    except ImportError:
        run_logger.log_event("pywebview_unavailable", url=URL)
        # Fallback: open in the system browser
        print(
            f"[MACtrl] 未安装 pywebview，改用浏览器：{URL}",
            file=sys.stderr,
        )
        webbrowser.open(URL)
        # Keep the server alive until the user presses Ctrl-C
        try:
            server_thread.join()
        except KeyboardInterrupt:
            pass
    finally:
        run_logger.close()


if __name__ == "__main__":
    main()
