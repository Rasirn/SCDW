"""TIA 编译诊断的结构化表示。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CompileDiagnostic:
    """一条保留原始文本的编译诊断。"""

    severity: str
    message: str
    path: str = ""
    source: str = "TIA 编译"


def diagnostics_from_messages(messages: list[str]) -> list[CompileDiagnostic]:
    """将现有文本消息转换为兼容的结构化诊断，不丢弃原始内容。"""
    result = []
    for message in messages:
        severity = "Error" if "[Error]" in message or "[InternalError]" in message else "Warning" if "[Warning]" in message else "Info"
        result.append(CompileDiagnostic(severity=severity, message=message))
    return result
