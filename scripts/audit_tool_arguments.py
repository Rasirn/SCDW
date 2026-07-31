"""Audit recorded MCP requests without invoking a backend tool.

Usage: python scripts/audit_tool_arguments.py data/logs/<run-id>
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from pydantic import ValidationError
from scdw.mcp.lad_plan_tools import LadPlanningInput


def _payload(run: Path, value: dict) -> object:
    if "inline" in value:
        return value["inline"]
    ref = value.get("payload_ref")
    if ref:
        return json.loads((run / ref).read_text(encoding="utf-8"))
    return None


def audit(run: Path) -> dict:
    rows, retries = [], Counter()
    session = run / "session.jsonl"
    for line in session.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event") != "mcp_tool_call_started":
            continue
        name, raw = event.get("tool_name", ""), _payload(run, event.get("payload", {}))
        valid_json = isinstance(raw, dict)
        row = {"tool_name": name, "valid_json": valid_json, "schema_valid": None,
               "missing_fields": [], "extra_fields": [], "invalid_enum": [],
               "backend_failure_due_to_parameters": False, "could_schema_prevent": False,
               "retry_count": retries[name]}
        if not valid_json:
            row.update(schema_valid=False, backend_failure_due_to_parameters=True, could_schema_prevent=True)
        elif name == "save_lad_generation_plan":
            try:
                LadPlanningInput.model_validate(raw.get("planning"))
                row["schema_valid"] = True
            except ValidationError as exc:
                row["schema_valid"] = False
                for error in exc.errors():
                    path = ".".join(str(part) for part in error["loc"])
                    (row["extra_fields"] if error["type"] == "extra_forbidden" else row["missing_fields"]).append(path)
                row["backend_failure_due_to_parameters"] = True
                row["could_schema_prevent"] = True
        rows.append(row); retries[name] += 1
    return {"run_directory": str(run), "requests": rows,
            "summary": {"request_count": len(rows), "invalid_json_count": sum(not row["valid_json"] for row in rows),
                        "tool_call_counts": dict(retries)}}


if __name__ == "__main__":
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/logs")
    output = audit(directory)
    (directory / "tool_argument_audit.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(directory / "tool_argument_audit.json")
