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
from pathlib import Path

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


# ── server thread ──────────────────────────────────────────────────────────────
def _run_server() -> None:
    """Run uvicorn in its own thread with a fresh event loop."""
    import asyncio
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    import uvicorn
    from scdw.frontend.app import app  # 在设置端口后导入应用

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PORT,
        log_level="warning",
        # Disable reload; we manage lifecycle via lifespan
        reload=False,
    )


def _wait_for_server(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    # Start backend server
    server_thread = threading.Thread(target=_run_server, daemon=True, name="uvicorn")
    server_thread.start()

    if not _wait_for_server():
        print("[MACtrl] 服务未能在 30 秒内启动。", file=sys.stderr)
        sys.exit(1)

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

    except ImportError:
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


if __name__ == "__main__":
    main()
