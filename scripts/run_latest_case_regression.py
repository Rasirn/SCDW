"""Offline replay of the latest three complete LAD generation cases.

This command is intentionally safe by default: it never starts TIA or calls an
LLM.  It rebuilds each plan from the recorded user requirement, runs the same
knowledge/renderer preflight used by production, and reports historical versus
mock-regression facts.  Real TIA verification remains a separate explicit step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scdw.common.paths import LOGS_DIR
from scdw.common.workflow_analysis import analyze_run
from scdw.lad_generation import LadCapabilityCatalog, LadPlanService, LadPlanner
from scdw.lad_generation.semantics import validate_compile_unit_semantics
from scdw.rag import KnowledgeLibrary
from scdw.xml_workspace.knowledge_networks import RENDERABLE_KINDS, render_knowledge_network


_CONTINUATIONS = {"可以", "都可以", "继续", "ok", "okay"}
_CASE_MARKERS = ("梯形图", "程序段", "lad")


def _rows(path: Path) -> list[dict[str, Any]]:
    values = []
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def _payload(run_dir: Path, value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "inline" in value:
        return value["inline"]
    if value.get("payload_ref"):
        try:
            return json.loads((run_dir / value["payload_ref"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return value


def primary_requirement(run_dir: Path) -> str | None:
    for row in _rows(run_dir / "session.jsonl"):
        if row.get("event") != "turn_started":
            continue
        query = str(_payload(run_dir, row.get("query", {})) or "").strip()
        if query.lower() in _CONTINUATIONS or "只回答" in query:
            continue
        if any(marker in query.lower() for marker in _CASE_MARKERS):
            return query
    return None


def latest_case_dirs(logs_dir: Path = LOGS_DIR, limit: int = 3) -> list[Path]:
    result = []
    for path in sorted((item for item in Path(logs_dir).iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime, reverse=True):
        if not (path / "manifest.json").is_file() or not (path / "session.jsonl").is_file():
            continue
        rows = _rows(path / "session.jsonl")
        if not primary_requirement(path) or not any(row.get("event") == "turn_completed" for row in rows):
            continue
        if not any(row.get("event") == "mcp_tool_call_started" for row in rows) and not (path / "mcp" / "mcp.jsonl").is_file():
            continue
        result.append(path)
        if len(result) == limit:
            break
    return result


def replay_case(run_dir: Path, catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    requirement = primary_requirement(run_dir)
    if not requirement:
        raise ValueError(f"no LAD requirement in {run_dir.name}")
    before = analyze_run(run_dir)
    plan = LadPlanner().plan(requirement, conversation_id=f"replay-{run_dir.name}", target_device="PLC_1")
    with tempfile.TemporaryDirectory(prefix="scdw-plan-replay-") as temporary:
        plan = LadPlanService(Path(temporary))._save_new_active(plan)
    renderer_issues = []
    rendered_networks = []
    for network in plan.networks:
        for item_id in network.selected_knowledge_ids:
            metadata = catalog[item_id]
            if metadata.get("generation_mode") == "knowledge_renderer_required":
                kind = str((metadata.get("renderer") or {}).get("kind", ""))
                if kind not in RENDERABLE_KINDS:
                    renderer_issues.append({"network_key": network.network_key, "knowledge_id": item_id, "renderer": kind})
        if network.renderer_id:
            try:
                xml = render_knowledge_network(
                    network.renderer_id,
                    blueprint=network.blueprint.to_dict() if network.blueprint else None,
                    title=network.title,
                    comment=network.comment,
                )
                semantic_issues = validate_compile_unit_semantics(network, xml)
                if semantic_issues:
                    renderer_issues.extend({"network_key": network.network_key, **item} for item in semantic_issues)
                rendered_networks.append({
                    "network_key": network.network_key,
                    "xml_chars": len(xml),
                    "semantic_preflight": "passed" if not semantic_issues else "failed",
                })
            except Exception as exc:
                renderer_issues.append({"network_key": network.network_key, "code": type(exc).__name__, "message": str(exc)})
    return {
        "run_id": run_dir.name,
        "requirement_sha256": hashlib.sha256(requirement.encode("utf-8")).hexdigest(),
        "requirement_chars": len(requirement),
        "before": before["summary"],
        "before_termination": before["termination"],
        "before_user_continuations": before.get("user_continuations", []),
        "offline_after": {
            "success": not renderer_issues,
            "requires_user_reply": False,
            "requested_network_count": plan.requested_network_count,
            "planned_network_count": plan.planned_network_count,
            "total_networks_including_auxiliary_blocks": len(plan.networks),
            "active_plan_count": 1,
            "knowledge_preflight": "passed",
            "renderer_preflight": "passed" if not renderer_issues else "failed",
            "renderer_issues": renderer_issues,
            "blueprint_status": plan.blueprint_status,
            "blueprint_sha256": plan.blueprint_sha256,
            "capability_catalog_sha256": plan.capability_catalog_sha256,
            "uncovered_capabilities": plan.uncovered_capabilities,
            "rendered_networks": rendered_networks,
            "estimated_lad_tool_calls": 3 + (2 * len(plan.networks)),
            "planned_tia_imports": len(plan.networks),
            "planned_tia_compiles": len(plan.networks),
            "tia_verified": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs-dir", type=Path, default=LOGS_DIR)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = latest_case_dirs(args.logs_dir, args.limit)
    if len(cases) != args.limit:
        raise RuntimeError(f"expected {args.limit} complete LAD runs, found {len(cases)}")
    catalog = {str(item["id"]): item for item in KnowledgeLibrary.instance().catalog()["items"]}
    value = {
        "schema_version": 2,
        "mode": "offline_log_replay_frozen_blueprint_and_renderer_preflight",
        "tia_verified": False,
        "capability_catalog": LadCapabilityCatalog.instance().compact(),
        "cases": [replay_case(path, catalog) for path in cases],
    }
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
