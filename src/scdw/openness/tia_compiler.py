# -*- coding: utf-8 -*-
"""
tia_compiler.py
编译 PLC 软件、下载到硬件。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
from .diagnostics import CompileDiagnostic, diagnostics_from_messages


@dataclass
class CompileResult:
    """编译操作的结果摘要。"""

    success: bool
    state: Optional[str]
    messages: List[str] = field(default_factory=list)

    @property
    def diagnostics(self) -> List[CompileDiagnostic]:
        """以结构化形式提供诊断，同时保持原有 messages 接口兼容。"""
        return diagnostics_from_messages(self.messages)

    @property
    def error_count(self) -> int:
        """返回错误诊断数量。"""
        return sum(d.severity == "Error" for d in self.diagnostics)

    @property
    def warning_count(self) -> int:
        """返回警告诊断数量。"""
        return sum(d.severity == "Warning" for d in self.diagnostics)

    def summary(self) -> str:
        lines = [f"编译状态：{self.state or '未知'}"]
        if self.messages:
            lines.append("编译消息：")
            lines.extend(f"  {m}" for m in self.messages)
        else:
            lines.append("（无编译消息）")
        return "\n".join(lines)


def _net_val_to_str(val) -> str:
    """
    将 .NET 对象（尤其是枚举）安全转换为有意义的字符串。

    pythonnet 中 str(enum) 有时会返回完整命名空间（如
    Siemens.Engineering.Compiler.CompilerResultState.Error），
    此函数提取最后一个点之后的部分（即 "Error"）。
    """
    if val is None:
        return ""
    s = str(val)
    # 如果看起来像 .NET 命名空间路径（无空格、含多个点），取最后段
    if s.count(".") >= 2 and " " not in s.strip():
        return s.rsplit(".", 1)[-1]
    return s


def _collect_messages(msg_collection, out: List[str], prefix: str = "") -> None:
    """
    递归收集 TIA Portal 编译消息树。

    TIA Portal 编译结果是多级嵌套树：
      顶层 Messages → 块/组容器（通常只有 Description/Path，无 Category）
        └─ 子 Messages → 具体错误行（含 Category/Description/Path）
    必须递归才能拿到详细报错，只读顶层会漏掉所有具体信息。

    注意：每个属性单独 try，避免一个属性失败导致整行被丢弃。
    """
    try:
        for msg in msg_collection:
            category = ""
            description = ""
            path = ""

            # 逐个属性独立 try，互不干扰
            # Category / State：两种命名都尝试
            for attr in ("Category", "State"):
                try:
                    v = getattr(msg, attr, None)
                    if v is not None:
                        category = _net_val_to_str(v)
                        break
                except Exception:
                    pass

            try:
                v = getattr(msg, "Description", None)
                if v is not None:
                    description = str(v)
            except Exception:
                pass

            try:
                v = getattr(msg, "Path", None)
                if v is not None:
                    path = str(v)
            except Exception:
                pass

            # 输出条件：description 或 category 有实质内容
            # 过滤掉纯 .NET 类型名（无意义的 fallback 内容）
            has_content = bool(description.strip() or category.strip())
            if has_content:
                parts = []
                if category:
                    parts.append(f"[{category}]")
                if path and path not in ("", "None", "0"):
                    parts.append(path)
                if description:
                    parts.append(description)
                out.append(prefix + " ".join(parts))

            # 递归子消息（无论本层是否有内容都递归）
            for sub_attr in ("Messages", "SubMessages"):
                try:
                    sub = getattr(msg, sub_attr, None)
                    if sub is not None:
                        _collect_messages(sub, out, prefix=prefix + "  ")
                    break
                except Exception:
                    pass
    except Exception:
        pass


def compile_plc(plc_software) -> CompileResult:
    """
    编译 PLC 软件，返回 CompileResult（递归收集所有嵌套编译消息）。

    Args:
        plc_software: PLC Software 对象

    Returns:
        CompileResult 包含成功标志、状态字符串和详细消息列表
    """
    from Siemens.Engineering.Compiler import ICompilable  # type: ignore

    compiler = plc_software.GetService[ICompilable]()
    result = compiler.Compile()

    state: Optional[str] = None
    messages: List[str] = []

    try:
        state = str(result.State)
    except Exception:
        pass

    _collect_messages(result.Messages, messages)

    # 认为没有 Error 级别消息即编译成功
    has_error = any(
        "[Error]" in m or "[InternalError]" in m for m in messages
    )
    if state and "error" in state.lower():
        has_error = True

    return CompileResult(success=not has_error, state=state, messages=messages)
