"""跨层传递的轻量操作结果类型。"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OperationResult:
    """统一表示一次操作的状态、消息、数据和诊断。"""

    success: bool
    message: str = ""
    data: Any = None
    diagnostics: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
