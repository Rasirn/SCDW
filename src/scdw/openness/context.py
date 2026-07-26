"""TIA 会话的可序列化上下文模型。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TiaConnectionMode(Enum):
    """TIA 连接的所有权状态。"""
    DETACHED = "detached"
    ATTACHED = "attached"
    OWNED = "owned"


@dataclass
class TiaContext:
    """只包含可安全传出 Openness 线程的上下文信息。"""
    connected: bool = False
    process_id: int | None = None
    owned_process_id: int | None = None
    connection_mode: str = TiaConnectionMode.DETACHED.value
    owns_tia_process: bool = False
    owns_project: bool = False
    project_name: str | None = None
    project_path: str | None = None
    project_is_primary: bool | None = None
    project_identity: str | None = None
    plc_devices: list[dict[str, Any]] = field(default_factory=list)
    context_version: int = 0
    last_refresh_time: str | None = None
    last_connection_error: str | None = None
    executor_thread_id: int | None = None

    def serialise(self) -> dict[str, Any]:
        """导出 JSON 友好的字典。"""
        result = asdict(self)
        result["last_refresh_time"] = self.last_refresh_time or datetime.now(timezone.utc).isoformat()
        return result
