"""TIA PLC and CodeBlock compilation with lossless recursive diagnostics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .diagnostics import CompileDiagnostic, diagnostics_from_messages


class CompileTargetNotFoundError(LookupError):
    """Raised when a requested TIA CodeBlock cannot be found."""


@dataclass
class CompileResult:
    success: bool
    state: Optional[str]
    messages: list[str] = field(default_factory=list)
    message_tree: list[dict[str, Any]] = field(default_factory=list)
    scope: str = "plc"
    target_name: str | None = None
    native_error_count: int | None = None
    native_warning_count: int | None = None

    @property
    def diagnostics(self) -> list[CompileDiagnostic]:
        return diagnostics_from_messages(self.messages)

    @staticmethod
    def _count(items: list[dict[str, Any]], severity: str) -> int:
        return sum(
            (1 if str(item.get("severity", "")).lower() == severity.lower() else 0)
            + CompileResult._count(item.get("messages", []), severity)
            for item in items
        )

    @property
    def error_count(self) -> int:
        if self.native_error_count is not None:
            return self.native_error_count
        return self._count(self.message_tree, "error") + self._count(self.message_tree, "internalerror")

    @property
    def warning_count(self) -> int:
        if self.native_warning_count is not None:
            return self.native_warning_count
        return self._count(self.message_tree, "warning")

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "scope": self.scope,
            "target_name": self.target_name,
            "state": self.state,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "messages": self.message_tree,
        }

    def summary(self) -> str:
        lines = [f"编译状态：{self.state or '未知'}"]
        lines.extend(f"  {message}" for message in self.messages)
        if not self.messages:
            lines.append("（无编译消息）")
        return "\n".join(lines)


def _net_val_to_str(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.count(".") >= 2 and " " not in text.strip():
        return text.rsplit(".", 1)[-1]
    return text


def _safe_attr(value: Any, name: str) -> Any:
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _message_tree(collection: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        values = list(collection)
    except Exception:
        return result
    for message in values:
        severity = ""
        for attribute in ("Category", "State"):
            raw = _safe_attr(message, attribute)
            if raw is not None:
                severity = _net_val_to_str(raw)
                break
        description = str(_safe_attr(message, "Description") or "")
        path = str(_safe_attr(message, "Path") or "")
        children: list[dict[str, Any]] = []
        for attribute in ("Messages", "SubMessages"):
            raw_children = _safe_attr(message, attribute)
            if raw_children is not None:
                children = _message_tree(raw_children)
                break
        if description.strip() or severity.strip() or path.strip() or children:
            result.append({
                "severity": severity.lower() or "info",
                "path": "" if path in {"None", "0"} else path,
                "description": description,
                "messages": children,
            })
    return result


def _flatten(items: list[dict[str, Any]], output: list[str], prefix: str = "") -> None:
    for item in items:
        parts = []
        if item.get("severity"):
            parts.append(f"[{str(item['severity']).title()}]")
        if item.get("path"):
            parts.append(str(item["path"]))
        if item.get("description"):
            parts.append(str(item["description"]))
        if parts:
            output.append(prefix + " ".join(parts))
        _flatten(item.get("messages", []), output, prefix + "  ")


def parse_compiler_result(result: Any, *, scope: str, target_name: str | None = None) -> CompileResult:
    """Convert a native CompilerResult into stable JSON-ready data."""
    state = _net_val_to_str(_safe_attr(result, "State")) or None
    tree = _message_tree(_safe_attr(result, "Messages") or [])
    flat: list[str] = []
    _flatten(tree, flat)
    native_errors = _safe_attr(result, "ErrorCount")
    native_warnings = _safe_attr(result, "WarningCount")
    parsed = CompileResult(
        success=True,
        state=state,
        messages=flat,
        message_tree=tree,
        scope=scope,
        target_name=target_name,
        native_error_count=int(native_errors) if native_errors is not None else None,
        native_warning_count=int(native_warnings) if native_warnings is not None else None,
    )
    has_error = parsed.error_count > 0 or bool(state and "error" in state.lower())
    parsed.success = not has_error
    return parsed


def _compile_target(target: Any, *, scope: str, target_name: str | None = None) -> CompileResult:
    from Siemens.Engineering.Compiler import ICompilable  # type: ignore

    compiler = target.GetService[ICompilable]()
    return parse_compiler_result(compiler.Compile(), scope=scope, target_name=target_name)


def _iter_blocks(group: Any):
    try:
        yield from list(group.Blocks)
    except Exception:
        pass
    for attribute in ("Groups", "BlockGroups"):
        try:
            children = list(getattr(group, attribute))
        except Exception:
            continue
        for child in children:
            yield from _iter_blocks(child)
        break


def find_code_block(plc_software: Any, block_name: str) -> Any:
    for block in _iter_blocks(plc_software.BlockGroup):
        try:
            if str(block.Name) == block_name:
                return block
        except Exception:
            continue
    raise CompileTargetNotFoundError(f"CodeBlock not found: {block_name}")


def compile_block(plc_software: Any, block_name: str) -> CompileResult:
    return _compile_target(find_code_block(plc_software, block_name), scope="block", target_name=block_name)


def compile_plc(plc_software: Any) -> CompileResult:
    return _compile_target(plc_software, scope="plc")
