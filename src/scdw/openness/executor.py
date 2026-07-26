"""TIA Portal Openness 单线程执行器。"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import ctypes
from threading import Lock, get_ident
from typing import Any, Callable
from scdw.common.run_logging import get_run_logger


class TiaOpennessExecutor:
    """将所有 Openness 调用串行安排到同一个专用线程。"""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scdw-tia")
        self._thread_id: int | None = None
        self._lock = Lock()

    @property
    def thread_id(self) -> int | None:
        """返回 Openness 专用线程标识。"""
        return self._thread_id

    def run(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """同步执行任务；异常会原样返回给调用方。"""
        def invoke() -> Any:
            with self._lock:
                run_logger = get_run_logger()
                operation = getattr(function, "__name__", type(function).__name__)
                run_logger.log_event("tia_executor_started", component="tia", operation=operation)
                # Openness 通过 COM 与 Portal 交互；工作线程必须初始化为 STA。
                try:
                    ctypes.windll.ole32.CoInitializeEx(None, 0x2)
                except Exception:
                    pass
                self._thread_id = get_ident()
                try:
                    result = function(*args, **kwargs)
                    run_logger.log_event("tia_executor_succeeded", component="tia", operation=operation, executor_thread_id=self._thread_id)
                    return result
                except Exception as exc:
                    run_logger.log_exception("tia_executor_failed", exc, component="tia", operation=operation, executor_thread_id=self._thread_id)
                    raise

        future: Future[Any] = self._pool.submit(invoke)
        return future.result()

    def shutdown(self) -> None:
        """停止执行器。通常不需要显式调用。"""
        self._pool.shutdown(wait=True, cancel_futures=False)
