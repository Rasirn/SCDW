"""MACtrl 运行级诊断日志。

日志以一次 GUI 启动为边界；所有记录均为 UTF-8 JSONL，方便人工查看和程序检索。
本模块不依赖 TIA 或 Web 框架，因此可被 GUI、MCP 子进程和单元测试安全使用。
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import platform
import re
import subprocess
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from scdw.common.paths import LOGS_DIR

_context: dict[str, contextvars.ContextVar[str | None]] = {
    key: contextvars.ContextVar(f"mactrl_{key}", default=None)
    for key in ("run_id", "conversation_id", "turn_id", "round", "tool_call_id", "operation_id")
}
_secret = re.compile(r"(?i)(sk-[a-z0-9_-]{8,}|(?:api[_-]?key|authorization|token)\s*[:=]\s*)([^\s,;\"']+)")
_manager: "RunLogManager | None" = None


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _secret.sub(lambda match: match.group(1) + "***REDACTED***", value)
    if isinstance(value, dict):
        return {str(k): ("***REDACTED***" if re.search(r"(?i)(api[_-]?key|authorization|token|secret|password)", str(k)) else _redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class RunLogManager:
    """线程安全的单次运行日志管理器。"""
    def __init__(self, run_dir: Path, run_id: str | None = None, *, create_manifest: bool = False) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = run_id or self.run_dir.name
        self.run_dir.mkdir(parents=True, exist_ok=True)
        for name in ("payloads", "mcp", "tia", "crash"):
            (self.run_dir / name).mkdir(exist_ok=True)
        (self.run_dir / "conversation.md").touch(exist_ok=True)
        self._start = time.perf_counter()
        self._lock = threading.Lock()
        self._sequence = 0
        self._session = (self.run_dir / "session.jsonl").open("a", encoding="utf-8", buffering=1)
        self._runtime = (self.run_dir / "runtime.log").open("a", encoding="utf-8", buffering=1)
        if create_manifest:
            self._write_manifest()

    @classmethod
    def create_run(cls, *, frontend_port: int | None = None) -> "RunLogManager":
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        run_id = f"{stamp}_{uuid.uuid4().hex[:6]}"
        manager = cls(LOGS_DIR / run_id, run_id, create_manifest=True)
        os.environ.update({"MACTRL_RUN_ID": run_id, "MACTRL_RUN_DIR": str(manager.run_dir), "MACTRL_PARENT_PID": str(os.getpid())})
        manager.log_event("run_started", component="gui", frontend_port=frontend_port, pid=os.getpid())
        return manager

    def _write_manifest(self) -> None:
        try:
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.run_dir.parents[2], capture_output=True, text=True, timeout=2).stdout.strip() or None
        except Exception:
            revision = None
        manifest = {"run_id": self.run_id, "created_at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(), "parent_pid": os.environ.get("MACTRL_PARENT_PID"), "python": platform.python_version(), "platform": platform.platform(), "git_revision": revision}
        (self.run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _record(self, event: str, fields: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            record = {"sequence": self._sequence, "event": event, "time_local": datetime.now().astimezone().isoformat(), "time_utc": datetime.now(timezone.utc).isoformat(), "elapsed_ms": round((time.perf_counter() - self._start) * 1000, 3), "pid": os.getpid(), "thread": threading.current_thread().name, "run_id": self.run_id}
            record.update({key: var.get() for key, var in _context.items() if var.get() is not None})
            record.update(_redact(fields))
            line = json.dumps(record, ensure_ascii=False, default=str)
            self._session.write(line + "\n")
            self._runtime.write(f"{record['time_local']} [{event}] {line}\n")
            if event in {"turn_started", "turn_completed", "turn_failed", "tool_result_received", "deepseek_stream_finished"}:
                with (self.run_dir / "conversation.md").open("a", encoding="utf-8") as conversation:
                    conversation.write(f"- {record['time_local']} `{event}`：{json.dumps(_redact(fields), ensure_ascii=False, default=str)}\n")
            if record.get("component") == "tia":
                with (self.run_dir / "tia" / "tia_operations.jsonl").open("a", encoding="utf-8") as tia_file:
                    tia_file.write(line + "\n")
            if str(record.get("component", "")).startswith("mcp"):
                with (self.run_dir / "mcp" / "mcp.jsonl").open("a", encoding="utf-8") as mcp_file:
                    mcp_file.write(line + "\n")
            return record

    def log_event(self, event: str, **fields: Any) -> dict[str, Any]:
        return self._record(event, fields)

    def log_exception(self, event: str, exc: BaseException | None = None, **fields: Any) -> dict[str, Any]:
        exc = exc or Exception("unknown exception")
        fields.update({"exception_type": type(exc).__name__, "exception": str(exc), "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))})
        return self._record(event, fields)

    def save_payload(self, name: str, payload: Any, *, category: str = "payloads") -> dict[str, Any]:
        safe = _redact(payload)
        text = json.dumps(safe, ensure_ascii=False, indent=2, default=str)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if len(text) <= 4000:
            return {"inline": safe, "sha256": digest, "length": len(text)}
        folder = self.run_dir / category
        folder.mkdir(exist_ok=True)
        path = folder / f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}_{re.sub(r'[^a-zA-Z0-9_.-]', '_', name)}.json"
        path.write_text(text, encoding="utf-8")
        return {"payload_ref": str(path.relative_to(self.run_dir)).replace("\\", "/"), "sha256": digest, "length": len(text), "preview": text[:500]}

    @contextmanager
    def bind_context(self, **values: Any) -> Iterator[None]:
        tokens = [_context[key].set(str(value)) for key, value in values.items() if key in _context and value is not None]
        try:
            yield
        finally:
            for token in reversed(tokens): token.var.reset(token)

    def flush(self) -> None:
        with self._lock:
            self._session.flush(); self._runtime.flush()

    def close(self) -> None:
        self.log_event("run_closed")
        with self._lock:
            self._session.close(); self._runtime.close()


def get_run_logger() -> RunLogManager:
    global _manager
    if _manager is None:
        run_dir = os.environ.get("MACTRL_RUN_DIR")
        run_id = os.environ.get("MACTRL_RUN_ID")
        _manager = RunLogManager(Path(run_dir), run_id) if run_dir else RunLogManager.create_run()
    return _manager


def set_run_logger(manager: RunLogManager) -> None:
    global _manager
    _manager = manager
