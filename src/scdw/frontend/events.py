"""Utilities for the JSON event protocol shared by the chat bridge and UI."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
