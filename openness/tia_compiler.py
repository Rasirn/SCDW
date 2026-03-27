# -*- coding: utf-8 -*-
"""
tia_compiler.py
编译 PLC 软件、下载到硬件。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CompileResult:
    """编译操作的结果摘要。"""

    success: bool
    state: Optional[str]
    messages: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"编译状态：{self.state or '未知'}"]
        if self.messages:
            lines.append("编译消息：")
            lines.extend(f"  {m}" for m in self.messages)
        return "\n".join(lines)


def compile_plc(plc_software) -> CompileResult:
    """
    编译 PLC 软件，返回 CompileResult。

    Args:
        plc_software: PLC Software 对象

    Returns:
        CompileResult 包含成功标志、状态字符串和消息列表
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

    try:
        for msg in result.Messages:
            try:
                messages.append(f"[{msg.Category}] {msg.Description}")
            except Exception:
                messages.append(str(msg))
    except Exception:
        pass

    # 认为没有 Error 级别消息即编译成功
    has_error = any(
        "[Error]" in m or "[error]" in m.lower() for m in messages
    )
    if state and "error" in state.lower():
        has_error = True

    return CompileResult(success=not has_error, state=state, messages=messages)
