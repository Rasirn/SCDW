"""Uniform JSON envelopes returned by public MCP tools."""
from __future__ import annotations

import json
from typing import Any


def tool_result(
    success: bool,
    *,
    stage: str,
    code: str = "OK",
    message: str = "",
    data: dict[str, Any] | None = None,
    retryable: bool = False,
    needs_user_action: bool = False,
) -> dict[str, Any]:
    return {
        "success": bool(success),
        "stage": stage,
        "code": code,
        "message": message,
        "data": data or {},
        "retryable": bool(retryable),
        "needs_user_action": bool(needs_user_action),
    }


def tool_json(
    success: bool,
    *,
    stage: str,
    code: str = "OK",
    message: str = "",
    data: dict[str, Any] | None = None,
    retryable: bool = False,
    needs_user_action: bool = False,
) -> str:
    return json.dumps(
        tool_result(
            success,
            stage=stage,
            code=code,
            message=message,
            data=data,
            retryable=retryable,
            needs_user_action=needs_user_action,
        ),
        ensure_ascii=False,
        sort_keys=True,
    )
