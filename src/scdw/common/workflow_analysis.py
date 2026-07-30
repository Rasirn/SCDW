"""Replay a run directory into a machine-readable LAD workflow summary."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _payload(run_dir: Path, value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "inline" in value:
        return value["inline"]
    ref = value.get("payload_ref")
    if ref:
        try:
            return json.loads((run_dir / str(ref)).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"payload_ref": ref, "unavailable": True}
    return value


def _content_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("role") == "tool":
        value = value.get("content")
    if isinstance(value, str):
        marker = "text='"
        if value.startswith("meta=") and marker in value:
            # The raw MCP log is diagnostic only; the corresponding chat payload
            # carries clean JSON and is preferred when available.
            return {"raw": value[:500]}
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {"text": value[:500]}
    return value if isinstance(value, dict) else {}


def _brief(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 300:
        return {"chars": len(value), "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}
    if isinstance(value, dict):
        return {key: _brief(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_brief(item) for item in value]
    return value


def analyze_run(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    mcp = _load_jsonl(run_dir / "mcp" / "mcp.jsonl")
    session = _load_jsonl(run_dir / "session.jsonl")
    starts = [row for row in mcp if row.get("event") == "mcp_tool_call_started"]
    finishes = [row for row in mcp if row.get("event") == "mcp_tool_call_finished"]
    chat_results = [row for row in session if row.get("event") == "tool_result_received"]
    result_by_time = list(chat_results)
    calls: list[dict[str, Any]] = []
    fingerprints: dict[str, int] = {}
    by_network: dict[str, Counter] = defaultdict(Counter)

    for index, start in enumerate(starts):
        finish = finishes[index] if index < len(finishes) else None
        arguments = _payload(run_dir, start.get("payload", {}))
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        fingerprint = hashlib.sha256((str(start.get("tool_name")) + "\0" + canonical).encode("utf-8")).hexdigest()
        finish_time = (finish or start).get("time_utc", "")
        next_start_time = starts[index + 1].get("time_utc", "") if index + 1 < len(starts) else "9999"
        matching_chat = next(
            (row for row in result_by_time if finish_time <= row.get("time_utc", "") < next_start_time),
            None,
        )
        if matching_chat is not None:
            result_by_time.remove(matching_chat)
        clean_result = _content_dict(_payload(run_dir, matching_chat.get("result", {}))) if matching_chat else {}
        raw_result = str((finish or {}).get("result", {}).get("inline", ""))
        success = clean_result.get("success")
        if success is None:
            success = not any(token in raw_result for token in ("success=False", '"success": false', "isError=True"))
        duration_ms = None
        if finish:
            try:
                duration_ms = round((datetime.fromisoformat(finish["time_utc"]) - datetime.fromisoformat(start["time_utc"])).total_seconds() * 1000, 3)
            except (KeyError, ValueError):
                pass
        network_key = arguments.get("network_key") if isinstance(arguments, dict) else None
        tool_name = str(start.get("tool_name"))
        if network_key:
            operation = "compile" if tool_name in {"compile_check", "import_and_compile_artifact"} else "import" if tool_name == "import_lad_xml" else "replace" if "replace" in tool_name else "patch" if "patch" in tool_name else "generate" if tool_name in {"append_network_and_prepare_import", "append_xml_network", "write_lad_network_from_knowledge"} else tool_name
            by_network[str(network_key)][operation] += 1
            if not success:
                by_network[str(network_key)]["failures"] += 1
        calls.append({
            "index": index + 1,
            "tool": tool_name,
            "started_at": start.get("time_local"),
            "duration_ms": duration_ms,
            "success": bool(success),
            "stage": clean_result.get("stage"),
            "code": clean_result.get("code"),
            "arguments_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "arguments": _brief(arguments),
            "result_chars": int((matching_chat or {}).get("result", {}).get("length", 0) or len(json.dumps(clean_result, ensure_ascii=False))),
            "duplicate_of": fingerprints.get(fingerprint),
        })
        fingerprints.setdefault(fingerprint, index + 1)

    counts = Counter(call["tool"] for call in calls)
    duplicate_calls = [call for call in calls if call["duplicate_of"] is not None]
    failures = [call for call in calls if not call["success"]]
    total_duration = None
    turn_starts = [row for row in session if row.get("event") == "turn_started"]
    terminals = [row for row in session if row.get("event") in {"turn_completed", "turn_failed", "chat_bridge_failed"}]
    if turn_starts and terminals:
        try:
            total_duration = round((datetime.fromisoformat(terminals[-1]["time_utc"]) - datetime.fromisoformat(turn_starts[0]["time_utc"])).total_seconds() * 1000, 3)
        except (KeyError, ValueError):
            pass
    hard_failure = next((row for row in reversed(session) if row.get("event") == "chat_bridge_failed"), None)
    longest = sorted(calls, key=lambda item: item["duration_ms"] or -1, reverse=True)[:10]
    summary = {
        "total_tool_calls": len(calls),
        "knowledge_calls": sum(counts[name] for name in ("get_plc_knowledge_catalog", "get_plc_knowledge_items")),
        "catalog_reads": counts["get_plc_knowledge_catalog"],
        "knowledge_body_reads": counts["get_plc_knowledge_items"],
        "plan_calls": sum(count for name, count in counts.items() if "lad_generation_plan" in name or "lad_network_plan" in name),
        "artifact_reads": sum(counts[name] for name in ("get_xml_artifact_status", "get_lad_block_info", "read_xml_fragment", "list_xml_networks", "get_xml_network")),
        "artifact_writes": sum(count for name, count in counts.items() if any(token in name for token in ("append", "replace", "patch", "update_xml", "create_lad_block_artifact", "write_lad_network"))),
        "tia_imports": counts["import_lad_xml"] + counts["import_and_compile_artifact"],
        "tia_compiles": counts["compile_check"] + counts["import_and_compile_artifact"],
        "failed_calls": len(failures),
        "duplicate_calls": len(duplicate_calls),
        "tool_duration_ms": round(sum(call["duration_ms"] or 0 for call in calls), 3),
        "run_duration_ms": total_duration,
    }
    return {
        "schema_version": 1,
        "run_id": run_dir.name,
        "summary": summary,
        "by_tool": dict(sorted(counts.items())),
        "by_network": {key: dict(value) for key, value in sorted(by_network.items())},
        "calls": calls,
        "duplicate_calls": duplicate_calls,
        "failures": failures,
        "longest_calls": longest,
        "termination": {
            "success": hard_failure is None,
            "event": hard_failure.get("event") if hard_failure else (terminals[-1].get("event") if terminals else None),
            "exception": hard_failure.get("exception") if hard_failure else None,
            "recovery_available": any(row.get("event") == "tool_budget_exhausted" for row in session),
        },
    }


def write_workflow_analysis(run_dir: Path, output: Path | None = None) -> Path:
    target = Path(output) if output else Path(run_dir) / "workflow_analysis.json"
    value = analyze_run(Path(run_dir))
    target.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return target
