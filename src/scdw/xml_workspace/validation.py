from __future__ import annotations

import xml.etree.ElementTree as ET
from .models import ValidationResult

MAX_XML_CHARS = 5_000_000


def validate_xml(content: str) -> ValidationResult:
    errors, warnings = [], []
    if not content or not content.strip(): return ValidationResult(False, [{"code":"XML_EMPTY", "message":"XML content is empty"}], warnings, {})
    if len(content) > MAX_XML_CHARS: return ValidationResult(False, [{"code":"XML_TOO_LARGE", "message":"XML exceeds size limit"}], warnings, {"chars": len(content)})
    try: root = ET.fromstring(content)
    except ET.ParseError as exc:
        line, column = getattr(exc, "position", (None, None))
        return ValidationResult(False, [{"code":"XML_PARSE_ERROR", "message":str(exc), "line":line, "column":column}], warnings, {})
    local = root.tag.rsplit("}", 1)[-1]
    if local != "Document": errors.append({"code":"ROOT_NOT_DOCUMENT", "message":"root element must be Document"})
    elements = list(root.iter())
    blocks = [e for e in elements if e.tag.rsplit("}", 1)[-1] in {"SW.Blocks.FC", "SW.Blocks.FB", "SW.Blocks.OB", "SW.Blocks.GlobalDB"}]
    if not blocks: errors.append({"code":"BLOCK_NOT_FOUND", "message":"no SimaticML block object found"})
    names = [e.text.strip() for e in elements if e.tag.rsplit("}", 1)[-1] == "Name" and e.text and e.text.strip()]
    if not names: errors.append({"code":"BLOCK_NAME_NOT_FOUND", "message":"block name could not be identified"})
    if not any(e.tag.rsplit("}", 1)[-1] == "ProgrammingLanguage" and (e.text or "").strip() for e in elements): errors.append({"code":"PROGRAMMING_LANGUAGE_MISSING", "message":"ProgrammingLanguage is missing"})
    uids = [e.attrib["UId"] for e in elements if e.get("UId")]
    duplicates = sorted({uid for uid in uids if uids.count(uid) > 1})
    if duplicates: errors.append({"code":"DUPLICATE_UID", "message":"duplicate UId values found", "uids":duplicates[:20]})
    target_uids = {e.get("UId") for e in elements if e.tag.rsplit("}", 1)[-1] != "IdentCon" and e.get("UId")}
    missing = sorted({e.get("UId") for e in elements if e.tag.rsplit("}", 1)[-1] == "IdentCon" and e.get("UId") and e.get("UId") not in target_uids})
    if missing: warnings.append({"code":"IDENTCON_REFERENCE_UNRESOLVED", "message":"some IdentCon UIds have no matching UId", "uids":missing[:20]})
    return ValidationResult(not errors, errors, warnings, {"root":local, "block_names":names[:10], "block_count":len(blocks), "uid_count":len(uids)})
