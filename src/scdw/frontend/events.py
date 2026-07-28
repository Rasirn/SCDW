"""Utilities for the JSON event protocol shared by the chat bridge and UI."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LARGE_ARGUMENT_KEYS = {"xml_content", "content", "file_content", "source"}


def summarize_tool_arguments(arguments: Any, *, max_text_length: int = 240) -> Any:
    """Return a small, UI-safe representation of tool arguments.

    Large source/XML payloads are deliberately never placed on the frontend event
    bus.  The original arguments remain available to the tool executor.
    """
    if not isinstance(arguments, dict):
        if isinstance(arguments, str) and len(arguments) > max_text_length:
            return {"summary": f"长文本（{len(arguments)} 字符，约 {arguments.count(chr(10)) + 1} 行）"}
        return to_json_safe(arguments)

    summary: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str) and (key.lower() in _LARGE_ARGUMENT_KEYS or len(value) > max_text_length):
            details: dict[str, Any] = {
                "summary": f"已省略长文本（{len(value)} 字符，约 {value.count(chr(10)) + 1} 行）"
            }
            if key.lower() == "xml_content":
                details["type"] = "XML"
                block_name = arguments.get("block_name") or arguments.get("name")
                if block_name:
                    details["target_file"] = f"{block_name}.xml"
            summary[key] = details
        else:
            summary[key] = to_json_safe(value)
    return summary


def to_json_safe(value: Any) -> Any:
    """Recursively convert protocol values to JSON-compatible data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return to_json_safe(asdict(value))
    if isinstance(value, Enum):
        return to_json_safe(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(item) for item in value]
    logger.warning("Unexpected non-JSON value in frontend event: %s", type(value).__name__)
    return str(value)


def validate_event_payload(payload: dict[str, Any]) -> None:
    """Raise early when an event cannot be encoded as JSON."""
    json.dumps(payload, ensure_ascii=False)
