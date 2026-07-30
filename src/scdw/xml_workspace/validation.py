"""Crash-prevention checks only; TIA Portal owns SimaticML/LAD semantics."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .models import ValidationResult

MAX_XML_BYTES = 5_000_000


def validate_xml(content: str) -> ValidationResult:
    if not content or not content.strip():
        return ValidationResult(False, [{"code": "XML_EMPTY", "message": "XML content is empty"}])
    try:
        encoded = content.encode("utf-8")
    except UnicodeError as exc:
        return ValidationResult(False, [{"code": "XML_ENCODING_ERROR", "message": str(exc)}])
    if len(encoded) > MAX_XML_BYTES:
        return ValidationResult(False, [{"code": "XML_TOO_LARGE", "message": "XML exceeds safety size limit"}], summary={"bytes": len(encoded)})
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, UnicodeError) as exc:
        line, column = getattr(exc, "position", (None, None))
        return ValidationResult(False, [{"code": "XML_PARSE_ERROR", "message": str(exc), "line": line, "column": column}])
    return ValidationResult(True, summary={"root": root.tag.rsplit("}", 1)[-1], "bytes": len(encoded)})
