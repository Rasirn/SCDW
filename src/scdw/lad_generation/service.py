from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scdw.common.paths import LAD_GENERATION_PLANS_DIR
from scdw.rag import KnowledgeLibrary

from .capabilities import CapabilityCatalogError, LadCapabilityCatalog
from .models import GENERATION_STATES, KnowledgeGapError, LadGenerationPlan, PlanValidationError
from .planner import LadPlanner
from .semantics import blueprint_tree_lines, network_semantic_sha256, plan_semantic_sha256


_PLAN_ID = re.compile(r"^ladplan_[a-f0-9]{16,64}$")
_NUMBERED_NETWORK = re.compile(r"程序段\s*(\d+)\s*[：:]")
_COUNTED_NETWORK = re.compile(r"(?:共包含|包含|共)\s*([一二三四五六七八九十\d]+)\s*个?程序段")
_CHINESE_COUNTS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _workflow_event(operation: str, **data: Any) -> dict[str, Any]:
    return {
        "operation_id": "op_" + secrets.token_hex(8),
        "operation": operation,
        "recorded_at": data.pop("recorded_at", None) or _stamp(),
        **data,
    }


def _explicit_network_count(requirements: str) -> int | None:
    labels = [int(value) for value in _NUMBERED_NETWORK.findall(requirements)]
    if labels:
        return max(labels)
    match = _COUNTED_NETWORK.search(requirements)
    if not match:
        return None
    value = match.group(1)
    return int(value) if value.isdigit() else _CHINESE_COUNTS.get(value)


class LadPlanService:
    """Atomic JSON persistence for progress in one long LLM conversation."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or LAD_GENERATION_PLANS_DIR).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.draft_root = self.root / "drafts"
        self.draft_root.mkdir(exist_ok=True)
        self._lock = threading.RLock()

    def _draft_path(self, draft_id: str) -> Path:
        if not re.fullmatch(r"draft_[a-f0-9]{16,64}", draft_id):
            raise KeyError("invalid draft id")
        return self.draft_root / f"{draft_id}.json"

    def create_or_get_draft(self, requirements: str, *, conversation_id: str, target_device: str, main_fc_name: str = "FC_MainControl") -> tuple[str, LadGenerationPlan, bool]:
        digest = hashlib.sha256(f"{conversation_id}\0{requirements}\0{target_device}\0{main_fc_name}".encode("utf-8")).hexdigest()
        draft_id = f"draft_{digest[:16]}"
        path = self._draft_path(draft_id)
        if path.exists():
            return draft_id, LadGenerationPlan.from_dict(json.loads(path.read_text(encoding="utf-8"))), True
        plan = LadPlanner().plan(requirements, conversation_id=conversation_id, target_device=target_device, main_fc_name=main_fc_name)
        self._atomic(path, json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        return draft_id, plan, False

    def get_draft(self, draft_id: str) -> LadGenerationPlan:
        path = self._draft_path(draft_id)
        if not path.exists(): raise KeyError("draft not found")
        return LadGenerationPlan.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def find_active_draft(self, conversation_id: str) -> tuple[str, LadGenerationPlan] | None:
        candidates: list[tuple[float, str, LadGenerationPlan]] = []
        for path in self.draft_root.glob("draft_*.json"):
            try:
                plan = LadGenerationPlan.from_dict(json.loads(path.read_text(encoding="utf-8")))
                if plan.conversation_id == conversation_id:
                    candidates.append((path.stat().st_mtime, path.stem, plan))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        if not candidates: return None
        _, draft_id, plan = max(candidates)
        return draft_id, plan

    def update_draft_network(self, draft_id: str, network_key: str, revision: dict[str, Any]) -> LadGenerationPlan:
        plan = self.get_draft(draft_id)
        network = self._network(plan, network_key)
        allowed = {"title", "comment", "purpose", "main_branch", "parallel_branches", "instructions", "variables", "required_capabilities", "selected_knowledge_ids", "instruction_chain", "topology", "depends_on", "split_reason", "blueprint", "renderer_id"}
        unexpected = set(revision) - allowed
        if unexpected: raise ValueError("unsupported draft fields: " + ", ".join(sorted(unexpected)))
        for name, value in revision.items():
            if name == "blueprint" and isinstance(value, dict):
                from .models import BlueprintNode
                value = BlueprintNode.from_dict(value)
            setattr(network, name, value)
        self._atomic(self._draft_path(draft_id), json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        return plan

    def _path(self, plan_id: str) -> Path:
        if not _PLAN_ID.fullmatch(plan_id):
            raise KeyError("invalid plan id")
        return self.root / f"{plan_id}.json"

    @staticmethod
    def _atomic(path: Path, content: str) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".tmp-plan-", dir=path.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _snapshot_path(self, plan_id: str) -> Path:
        path = self.root / "knowledge_snapshots" / f"{plan_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _catalog_entries(self, plan: LadGenerationPlan) -> dict[str, dict[str, Any]]:
        snapshot = self._snapshot_path(plan.plan_id)
        if snapshot.is_file():
            value = json.loads(snapshot.read_text(encoding="utf-8"))
            return {str(item["id"]): item["metadata"] for item in value.get("items", [])}
        return {str(item["id"]): item for item in KnowledgeLibrary.instance().catalog()["items"]}

    def _write_knowledge_snapshot(self, plan: LadGenerationPlan) -> None:
        selected = list(dict.fromkeys(
            item_id for network in plan.networks for item_id in network.selected_knowledge_ids
        ))
        library = KnowledgeLibrary.instance()
        catalog_items = list(library.catalog()["items"])
        items = library.get_many(selected) if selected else []
        catalog_text = json.dumps(
            catalog_items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        summary = {
            "captured_at": _stamp(),
            "catalog_sha256": hashlib.sha256(catalog_text.encode("utf-8")).hexdigest(),
            "items": {
                item["id"]: hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
                for item in items if item["id"] in selected
            },
        }
        body_by_id = {item["id"]: item for item in items}
        value = {**summary, "items": [
            {
                "id": metadata["id"],
                "metadata": metadata,
                **({
                    "content": body_by_id[metadata["id"]]["content"],
                    "content_sha256": hashlib.sha256(body_by_id[metadata["id"]]["content"].encode("utf-8")).hexdigest(),
                } if metadata["id"] in body_by_id else {}),
            }
            for metadata in catalog_items
        ]}
        self._atomic(self._snapshot_path(plan.plan_id), json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        plan.knowledge_snapshot = summary

    def _collect_knowledge_issues(self, plan: LadGenerationPlan) -> list[dict[str, Any]]:
        entries = self._catalog_entries(plan)
        topology_capabilities = {
            "series": ["topology.series"],
            "parallel": ["topology.parallel"],
            "fan_out": ["topology.fan_out"],
            "merge": ["topology.merge"],
            "parallel_merge": ["topology.parallel", "topology.merge"],
        }
        issues: list[dict[str, Any]] = []
        from scdw.xml_workspace.knowledge_networks import RENDERABLE_KINDS
        for network in plan.networks:
            catalog = LadCapabilityCatalog.instance()
            selected = list(dict.fromkeys(network.selected_knowledge_ids))
            network.selected_knowledge_ids = selected
            network.knowledge_ids = list(selected)
            unknown = [item_id for item_id in selected if item_id not in entries]
            if unknown:
                network.uncovered_capabilities = [f"knowledge_id:{item_id}" for item_id in unknown]
                issues.append({"network_key": network.network_key, "code": "UNKNOWN_KNOWLEDGE_ID", "message": f"unknown knowledge IDs: {', '.join(unknown)}", "uncovered_capabilities": network.uncovered_capabilities})
                continue
            missing_renderers = [
                item_id for item_id in selected
                if entries[item_id].get("generation_mode") == "knowledge_renderer_required"
                and str((entries[item_id].get("renderer") or {}).get("kind", "")) not in RENDERABLE_KINDS
            ]
            if missing_renderers:
                issues.append({
                    "network_key": network.network_key,
                    "code": "KNOWLEDGE_RENDERER_MISSING",
                    "message": "selected knowledge items require unavailable renderers: " + ", ".join(missing_renderers),
                    "uncovered_capabilities": [f"renderer:{item_id}" for item_id in missing_renderers],
                })
            provided = {
                str(capability)
                for item_id in selected
                for capability in entries[item_id].get("provides", [])
            }
            required = list(dict.fromkeys(network.required_capabilities))
            topology_kind = str(network.topology.get("kind", "")).strip().lower()
            for topology_capability in topology_capabilities.get(topology_kind, []):
                if topology_capability not in required:
                    required.append(topology_capability)
            network.required_capabilities = required
            # Catalog mappings cover a capability even where legacy knowledge
            # metadata predates its `provides` alias.  `provides` remains part
            # of coverage for non-catalog capabilities.
            mapped = {
                capability_id for capability_id in required
                if capability_id in catalog._items
                and set(catalog.get(capability_id).knowledge_ids).issubset(set(selected))
            }
            network.uncovered_capabilities = [item for item in required if item not in provided and item not in mapped]
            if network.uncovered_capabilities:
                issues.append({"network_key": network.network_key, "code": "UNCOVERED_CAPABILITIES", "message": f"uncovered capabilities: {', '.join(network.uncovered_capabilities)}", "uncovered_capabilities": list(network.uncovered_capabilities)})
            topology_kind = str(network.topology.get("kind", "")).strip().lower()
            if topology_kind in {"parallel", "parallel_merge"} and len([branch for branch in network.parallel_branches if branch]) < 2:
                issues.append({"network_key": network.network_key, "code": "INVALID_PARALLEL_BRANCHES", "message": "parallel topology requires at least two explicit parallel_branches", "uncovered_capabilities": ["topology.parallel_branches"]})
        return issues

    def _collect_blueprint_issues(self, plan: LadGenerationPlan) -> list[dict[str, Any]]:
        catalog = LadCapabilityCatalog.instance()
        issues: list[dict[str, Any]] = []
        from scdw.xml_workspace.knowledge_networks import RENDERABLE_KINDS
        for network in plan.networks:
            if network.blueprint is None:
                issues.append({
                    "network_key": network.network_key,
                    "code": "BLUEPRINT_TREE_MISSING",
                    "message": "Network has no structured LAD blueprint tree",
                    "uncovered_capabilities": ["blueprint.tree"],
                })
                continue
            for issue in catalog.validate_node(network.blueprint):
                issues.append({"network_key": network.network_key, **issue})
            renderer_id = network.renderer_id
            if not renderer_id:
                issues.append({
                    "network_key": network.network_key,
                    "code": "KNOWLEDGE_RENDERER_MISSING",
                    "message": "frozen Network has no deterministic renderer",
                    "uncovered_capabilities": ["renderer:network"],
                })
            elif renderer_id not in RENDERABLE_KINDS:
                issues.append({
                    "network_key": network.network_key,
                    "code": "KNOWLEDGE_RENDERER_MISSING",
                    "message": f"frozen Network renderer is unavailable: {renderer_id}",
                    "uncovered_capabilities": [f"renderer:{renderer_id}"],
                })
            selected = set(network.selected_knowledge_ids)

            def inspect(node) -> None:
                missing = sorted(set(node.knowledge_ids) - selected)
                if missing:
                    issues.append({
                        "network_key": network.network_key,
                        "code": "BLUEPRINT_KNOWLEDGE_NOT_SELECTED",
                        "message": f"blueprint node {node.node_id} uses unselected knowledge: {', '.join(missing)}",
                        "uncovered_capabilities": [f"knowledge_id:{item}" for item in missing],
                    })
                for child in node.children:
                    inspect(child)

            inspect(network.blueprint)
        return issues

    def _freeze_blueprint(self, plan: LadGenerationPlan) -> LadGenerationPlan:
        issues = [*self._collect_knowledge_issues(plan), *self._collect_blueprint_issues(plan)]
        if issues:
            plan.uncovered_capabilities = sorted({
                item for issue in issues for item in issue.get("uncovered_capabilities", [])
            })
            raise PlanValidationError(issues)
        catalog = LadCapabilityCatalog.instance().compact()
        plan.capability_catalog_sha256 = catalog["catalog_sha256"]
        plan.uncovered_capabilities = []
        for network in plan.networks:
            network.frozen_semantic_sha256 = network_semantic_sha256(network)
        plan.blueprint_sha256 = plan_semantic_sha256(plan)
        plan.blueprint_status = "approved_for_generation"
        plan.frozen_at = _stamp()
        plan.step_status["planning"] = "planned"
        return plan

    def _require_frozen_blueprint(self, plan: LadGenerationPlan) -> None:
        if plan.blueprint_status != "approved_for_generation" or not plan.blueprint_sha256:
            raise KnowledgeGapError(
                "LAD blueprint must pass preflight and be frozen before XML generation",
                uncovered=["blueprint.approved_for_generation"],
            )
        current = plan_semantic_sha256(plan)
        if current != plan.blueprint_sha256:
            raise KnowledgeGapError(
                "frozen LAD blueprint semantics changed; return it to needs_revision and freeze again",
                uncovered=["blueprint.semantic_fingerprint"],
            )
        changed = [
            item.network_key for item in plan.networks
            if item.frozen_semantic_sha256 != network_semantic_sha256(item)
        ]
        if changed:
            raise KnowledgeGapError(
                "Network semantics differ from the frozen blueprint: " + ", ".join(changed),
                uncovered=[f"frozen_network:{item}" for item in changed],
            )

    def freeze_blueprint(self, plan_id: str) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            self._freeze_blueprint(plan)
            plan.verification_history.append(_workflow_event(
                "freeze_blueprint", blueprint_sha256=plan.blueprint_sha256,
                capability_catalog_sha256=plan.capability_catalog_sha256,
            ))
            return self.save(plan)

    @staticmethod
    def render_blueprint_tree(plan: LadGenerationPlan) -> list[str]:
        return blueprint_tree_lines(plan)

    def _validate_knowledge_coverage(self, plan: LadGenerationPlan) -> None:
        issues = self._collect_knowledge_issues(plan)
        if issues:
            raise PlanValidationError(issues)

    def save(self, plan: LadGenerationPlan) -> LadGenerationPlan:
        plan.validate()
        self._validate_knowledge_coverage(plan)
        if plan.blueprint_status == "approved_for_generation":
            self._require_frozen_blueprint(plan)
        plan.updated_at = _stamp()
        if not plan.created_at:
            plan.created_at = plan.updated_at
        with self._lock:
            self._atomic(self._path(plan.plan_id), json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        return plan

    def _replace_active_plans(self, new_plan: LadGenerationPlan) -> None:
        for old in self.list(new_plan.conversation_id):
            if old.plan_id == new_plan.plan_id or old.status != "active":
                continue
            old.status = "replaced"
            old.replaced_by = new_plan.plan_id
            old.closed_at = _stamp()
            old.updated_at = old.closed_at
            # Lifecycle migration must also close legacy plans created before
            # capability fields existed; those plans are never generation-ready.
            self._atomic(self._path(old.plan_id), json.dumps(old.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    def _save_new_active(self, plan: LadGenerationPlan) -> LadGenerationPlan:
        with self._lock:
            plan.validate()
            self._freeze_blueprint(plan)
            self._write_knowledge_snapshot(plan)
            self._replace_active_plans(plan)
            return self.save(plan)

    def create_from_requirements(self, requirements: str, *, conversation_id: str, target_device: str, main_fc_name: str = "FC_MainControl") -> LadGenerationPlan:
        return self._save_new_active(LadPlanner().plan(requirements, conversation_id=conversation_id, target_device=target_device, main_fc_name=main_fc_name))

    def normalize_planning(self, planning: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Synchronise redundant planning fields without changing their format.

        Only missing/empty derived values are filled.  A supplied value that
        disagrees with the frozen blueprint is a planning error, never a
        silent semantic rewrite.
        """
        value = json.loads(json.dumps(planning))
        changed: list[str] = []
        warnings: list[str] = []
        catalog = LadCapabilityCatalog.instance()
        for network in value.get("networks", []):
            root = network.get("blueprint") or {}
            capabilities: list[str] = []
            knowledge: list[str] = []
            leaves: list[dict[str, Any]] = []
            parallel_branches: list[list[str]] = []

            def walk(node: dict[str, Any]) -> None:
                capability = node.get("capability_id")
                if capability:
                    capabilities.append(str(capability))
                    try:
                        knowledge.extend(catalog.get(str(capability)).knowledge_ids)
                    except CapabilityCatalogError:
                        # Preflight reports the precise unknown-capability error.
                        pass
                knowledge.extend(str(item) for item in node.get("knowledge_ids", []))
                children = node.get("children") or []
                if not children and node.get("label"):
                    leaves.append(node)
                for child in children:
                    walk(child)
            walk(root)
            derived_caps = list(dict.fromkeys(capabilities))
            derived_knowledge = list(dict.fromkeys(knowledge))
            for field, derived in (("required_capabilities", derived_caps), ("selected_knowledge_ids", derived_knowledge)):
                supplied = list(network.get(field) or [])
                missing = [item for item in derived if item not in supplied]
                if missing:
                    network[field] = supplied + missing
                    changed.append(f"networks.{network.get('network_key', '?')}.{field}")
            kind = str((network.get("topology") or {}).get("kind", "")).lower()
            root_kind = str(root.get("kind", "")).lower()
            if kind and root_kind in {"parallel", "fan_out", "merge", "parallel_merge"} and kind != root_kind:
                raise PlanValidationError([{"network_key": network.get("network_key"), "code": "TOPOLOGY_BLUEPRINT_CONFLICT", "message": f"topology kind {kind} conflicts with blueprint root {root_kind}", "uncovered_capabilities": []}])
            if root_kind == "parallel":
                parallel_branches = [[str(child.get("node_id", child.get("label", "branch")))] for child in root.get("children", [])]
                supplied = network.get("parallel_branches") or []
                if not supplied:
                    network["parallel_branches"] = parallel_branches
                    changed.append(f"networks.{network.get('network_key', '?')}.parallel_branches")
            if leaves:
                labels = [str(node["label"]) for node in leaves]
                if not network.get("instructions"):
                    network["instructions"] = labels
                    changed.append(f"networks.{network.get('network_key', '?')}.instructions")
                if not network.get("variables"):
                    operands = [str(item) for node in leaves for item in (node.get("operands") or {}).values() if isinstance(item, (str, int, float))]
                    network["variables"] = list(dict.fromkeys(operands or labels))
                    changed.append(f"networks.{network.get('network_key', '?')}.variables")
        actual_count = len(value.get("networks", []))
        if value.get("planned_network_count") != actual_count:
            value["planned_network_count"] = actual_count
            changed.append("planned_network_count")
        return value, {"normalized_fields": changed, "warnings": warnings}

    def create_from_planning(self, planning: dict[str, Any], *, requirements: str, conversation_id: str, target_device: str) -> LadGenerationPlan:
        """Persist the complete plan already formed by the current LLM conversation."""
        planning, _ = self.normalize_planning(planning)
        stamp = _stamp()
        value = {
            **planning,
            "plan_id": "ladplan_" + secrets.token_hex(8),
            "conversation_id": conversation_id,
            "target_device": target_device,
            "requirements": requirements,
            "current_block": None,
            "current_network": None,
            "step_status": {"planning": "planned", "artifact_creation": "planned", "network_generation": "planned"},
            "artifacts": {},
            "interface_change_log": [],
            "verification_history": [],
            "knowledge_snapshot": {},
            "blueprint_schema_version": 1,
            "blueprint_status": "draft",
            "capability_catalog_sha256": "",
            "blueprint_sha256": None,
            "frozen_at": None,
            "uncovered_capabilities": [],
            "status": "active",
            "replaced_by": None,
            "closed_at": None,
            "created_at": stamp,
            "updated_at": stamp,
        }
        plan = LadGenerationPlan.from_dict(value)
        detected_count = _explicit_network_count(requirements)
        if detected_count is not None:
            plan.requested_network_count = detected_count
        plan.planned_network_count = len(plan.networks)
        if not plan.instruction_pipeline:
            plan.instruction_pipeline = [item.network_key for item in plan.networks]
        if plan.current_block is None and plan.block_dependency_order:
            plan.current_block = plan.block_dependency_order[0]
        if plan.current_network is None and plan.networks:
            plan.current_network = plan.networks[0].network_key
        issues = [*self._collect_knowledge_issues(plan), *self._collect_blueprint_issues(plan)]
        if issues:
            raise PlanValidationError(issues)
        return self._save_new_active(plan)

    def get(self, plan_id: str) -> LadGenerationPlan:
        path = self._path(plan_id)
        if not path.is_file():
            raise KeyError("plan not found")
        return LadGenerationPlan.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def set_cursor(self, plan_id: str, block_name: str | None, network_key: str | None) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            blocks = {plan.main_fc.block_name, *(item.block_name for item in plan.auxiliary_fbs)}
            if block_name is not None and block_name not in blocks:
                raise ValueError("current block is not part of the plan")
            if network_key is not None and network_key not in {item.network_key for item in plan.networks}:
                raise ValueError("current Network is not part of the plan")
            plan.current_block, plan.current_network = block_name, network_key
            return self.save(plan)

    def set_network_status(self, plan_id: str, network_key: str, status: str) -> LadGenerationPlan:
        if status not in GENERATION_STATES:
            raise ValueError("unsupported Network status")
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            network = next((item for item in plan.networks if item.network_key == network_key), None)
            if network is None:
                raise KeyError("Network not found")
            network.status = status
            return self.save(plan)

    @staticmethod
    def _advance_cursor(plan: LadGenerationPlan) -> None:
        for block_name in plan.block_dependency_order:
            candidate = next(
                (item for item in plan.networks if item.block_name == block_name and item.status != "verified"),
                None,
            )
            if candidate is not None:
                plan.current_block = block_name
                plan.current_network = candidate.network_key
                return
        plan.current_block = None
        plan.current_network = None

    def next_step(self, plan_id: str) -> dict[str, Any]:
        plan = self.get(plan_id)
        self._advance_cursor(plan)
        if plan.current_network:
            network = self._network(plan, plan.current_network)
            artifact = plan.artifacts.get(network.block_name, {})
            return {
                "action": "generate_network" if network.status in {"planned", "needs_revision", "compile_failed", "import_failed"} else "resume_network",
                "plan_id": plan.plan_id,
                "block_name": network.block_name,
                "network_key": network.network_key,
                "network_status": network.status,
                "artifact_id": artifact.get("artifact_id"),
                "version": artifact.get("version"),
                "device_name": plan.target_device,
            }
        unverified_blocks = [item.block_name for item in [plan.main_fc, *plan.auxiliary_fbs] if item.status != "verified"]
        if unverified_blocks:
            return {"action": "finalize_block", "plan_id": plan.plan_id, "block_name": unverified_blocks[0], "device_name": plan.target_device}
        if plan.step_status.get("plc_compile") != "verified":
            return {"action": "compile_plc", "plan_id": plan.plan_id, "device_name": plan.target_device}
        return {"action": "save_project", "plan_id": plan.plan_id, "device_name": plan.target_device}

    def set_runtime_status(self, plan_id: str, status: str, *, block_name: str | None = None, network_key: str | None = None, instance_db_name: str | None = None) -> LadGenerationPlan:
        if status not in GENERATION_STATES:
            raise ValueError("unsupported runtime status")
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            if block_name:
                self._block(plan, block_name).status = status
            if network_key:
                self._network(plan, network_key).status = status
            if instance_db_name:
                target = next((item for item in plan.instance_dbs if item.db_name == instance_db_name), None)
                if target is None:
                    raise KeyError("instance DB dependency not found in plan")
                target.status = status
            return self.save(plan)

    @staticmethod
    def _block(plan: LadGenerationPlan, block_name: str):
        block = next((item for item in [plan.main_fc, *plan.auxiliary_fbs] if item.block_name == block_name), None)
        if block is None:
            raise KeyError("block not found in plan")
        return block

    @staticmethod
    def _network(plan: LadGenerationPlan, network_key: str):
        network = next((item for item in plan.networks if item.network_key == network_key), None)
        if network is None:
            raise KeyError("Network not found")
        return network

    @staticmethod
    def _require_active(plan: LadGenerationPlan) -> None:
        if plan.status != "active":
            raise ValueError(f"plan is not active: {plan.status}")

    def close(self, plan_id: str) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
            if plan.status == "active":
                plan.status = "closed"
                plan.closed_at = _stamp()
            return self.save(plan)

    def mark_network_for_replan(self, plan_id: str, network_key: str, reason: str) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            network = self._network(plan, network_key)
            network.status = "needs_revision"
            plan.blueprint_status = "needs_revision"
            plan.blueprint_sha256 = None
            plan.frozen_at = None
            network.frozen_semantic_sha256 = None
            plan.step_status["planning"] = "needs_revision"
            plan.verification_history.append(_workflow_event(
                "semantic_change_requires_replan",
                network_key=network_key,
                reason=reason,
            ))
            return self.save(plan)

    def revise_network_plan(self, plan_id: str, network_key: str, revision: dict[str, Any], reason: str) -> LadGenerationPlan:
        """Revise knowledge/topology on the same active plan and keep Artifact linkage."""
        allowed = {
            "title", "comment", "purpose", "main_branch", "parallel_branches", "instructions", "variables",
            "required_capabilities", "selected_knowledge_ids", "instruction_chain", "topology", "depends_on", "split_reason",
            "blueprint", "renderer_id",
        }
        unexpected = sorted(set(revision) - allowed)
        if unexpected:
            raise ValueError(f"unsupported Network planning fields: {', '.join(unexpected)}")
        if not revision:
            raise ValueError("revision must not be empty")
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            network = self._network(plan, network_key)
            plan.blueprint_status = "needs_revision"
            plan.blueprint_sha256 = None
            plan.frozen_at = None
            network.frozen_semantic_sha256 = None
            for name, value in revision.items():
                if name == "blueprint" and isinstance(value, dict):
                    from .models import BlueprintNode
                    value = BlueprintNode.from_dict(value)
                setattr(network, name, value)
            network.status = "needs_revision"
            network.import_result = None
            network.compile_result = None
            network.verified_version = None
            self._freeze_blueprint(plan)
            plan.verification_history.append(_workflow_event(
                "revise_network_plan",
                network_key=network_key,
                reason=reason,
                changed_fields=sorted(revision),
            ))
            return self.save(plan)

    def record_import_result(self, plan_id: str, block_name: str, artifact_id: str, version: int, result: dict[str, Any], network_key: str | None = None) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            block = self._block(plan, block_name)
            status = "imported" if result.get("success") else "import_failed"
            block.status = status
            block.artifact_id, block.artifact_version = artifact_id, version
            block.imported_at = result.get("recorded_at")
            block.import_result = result
            if network_key:
                network = self._network(plan, network_key)
                if network.block_name != block_name:
                    raise ValueError("Network does not belong to imported block")
                network.status = status
                network.artifact_id, network.artifact_version = artifact_id, version
                network.imported_at = result.get("recorded_at")
                network.import_result = result
            plan.verification_history.append(_workflow_event("import", **result))
            return self.save(plan)

    def record_compile_result(self, plan_id: str, result: dict[str, Any], *, block_name: str | None = None, artifact_id: str | None = None, version: int | None = None, network_key: str | None = None) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            success = bool(result.get("success"))
            status = "verified" if success else "compile_failed"
            if block_name:
                block = self._block(plan, block_name)
                block.compile_result = result
                block.compiled_at = result.get("recorded_at")
                if network_key:
                    network = self._network(plan, network_key)
                    if network.block_name != block_name:
                        raise ValueError("Network does not belong to compiled block")
                    network.status = status
                    network.compile_result = result
                    network.compiled_at = result.get("recorded_at")
                    if success and version is not None:
                        network.verified_version = version
                    block.status = "compiling" if success else "compile_failed"
                    if success:
                        self._advance_cursor(plan)
                else:
                    block.status = status
                    if success and version is not None:
                        block.verified_version = version
            else:
                plan.step_status["plc_compile"] = status
            event_data = {"artifact_id": artifact_id, "version": version, "network_key": network_key, **result}
            plan.verification_history.append(_workflow_event("compile", **event_data))
            return self.save(plan)

    def stop_repeated_diagnostic(self, plan_id: str, network_key: str, result: dict[str, Any]) -> bool:
        """Stop repeated expression guesses without unfreezing LAD semantics."""
        fingerprint_source = {
            "stage": result.get("stage"),
            "code": result.get("code"),
            "messages": result.get("messages", []),
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_source, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            attempts = []
            for event in reversed(plan.verification_history):
                if event.get("network_key") != network_key or event.get("operation") not in {"import", "compile"}:
                    continue
                if event.get("success") is True:
                    break
                candidate = hashlib.sha256(json.dumps({
                    "stage": event.get("stage"), "code": event.get("code"), "messages": event.get("messages", []),
                }, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
                if candidate != fingerprint:
                    break
                attempts.append(event)
            if len(attempts) < 2:
                return False
            network = self._network(plan, network_key)
            # This is still an XML-expression failure. The reviewed design
            # remains frozen and must not be revised merely to escape a TIA
            # diagnostic.
            network.status = "import_failed" if result.get("stage") == "tia_import" else "compile_failed"
            plan.verification_history.append(_workflow_event(
                "repeated_diagnostic_breaker", network_key=network_key,
                diagnostic_fingerprint=fingerprint, attempts=len(attempts),
                blueprint_sha256=plan.blueprint_sha256,
            ))
            self._advance_cursor(plan)
            self.save(plan)
            return True

    def record_instance_db_result(self, plan_id: str, fb_name: str, instance_db_name: str, result: dict[str, Any]) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            target = next((item for item in plan.instance_dbs if item.fb_name == fb_name and item.db_name == instance_db_name), None)
            if target is None:
                raise KeyError("instance DB dependency not found in plan")
            target.status = "imported" if result.get("success") else "import_failed"
            target.created_at = result.get("recorded_at")
            target.create_result = result
            plan.verification_history.append(_workflow_event("create_instance_db", **result))
            return self.save(plan)

    def validate_instance_db_order(self, plan_id: str, fb_name: str, instance_db_name: str) -> LadGenerationPlan:
        plan = self.get(plan_id)
        self._require_active(plan)
        fb = next((item for item in plan.auxiliary_fbs if item.block_name == fb_name), None)
        if fb is None:
            raise KeyError("auxiliary FB not found in plan")
        if fb.status != "verified":
            raise ValueError("auxiliary FB must be imported and pass final block compilation before creating its instance DB")
        if not any(item.fb_name == fb_name and item.db_name == instance_db_name for item in plan.instance_dbs):
            raise KeyError("instance DB dependency not found in plan")
        return plan

    def validate_network_generation_order(self, plan_id: str, block_name: str, network_key: str) -> None:
        plan = self.get(plan_id)
        self._require_active(plan)
        self._require_frozen_blueprint(plan)
        self._validate_knowledge_coverage(plan)
        network = self._network(plan, network_key)
        if network.block_name != block_name:
            raise ValueError("Network does not belong to block")
        if network.status == "verified":
            raise ValueError("verified Network cannot be regenerated without first marking it needs_revision")
        block_networks = [item for item in plan.networks if item.block_name == block_name]
        position = block_networks.index(network)
        unfinished = [item.network_key for item in block_networks[:position] if item.status != "verified"]
        if unfinished and network.status != "needs_revision":
            raise ValueError(f"previous Networks must be verified first: {', '.join(unfinished)}")
        dependency_failures = [key for key in network.depends_on if self._network(plan, key).status != "verified"]
        if dependency_failures:
            raise ValueError(f"dependent Networks must be verified first: {', '.join(dependency_failures)}")
        if block_name == plan.main_fc.block_name:
            missing = [item.db_name for item in plan.instance_dbs if item.status != "imported"]
            if missing:
                raise ValueError(f"instance DBs must be created before main FC Network generation: {', '.join(missing)}")

    def validate_final_block_compile(self, plan_id: str, block_name: str) -> None:
        plan = self.get(plan_id)
        self._require_active(plan)
        networks = [item for item in plan.networks if item.block_name == block_name]
        missing = [item.network_key for item in networks if item.status != "verified"]
        if missing:
            raise ValueError(f"all block Networks must be verified before final block compilation: {', '.join(missing)}")

    def validate_network_compile(self, plan_id: str, block_name: str, network_key: str, artifact_id: str | None, version: int | None) -> None:
        plan = self.get(plan_id)
        self._require_active(plan)
        network = self._network(plan, network_key)
        if network.block_name != block_name:
            raise ValueError("Network does not belong to compiled block")
        if network.status != "imported":
            raise ValueError("current Network must be imported successfully before compilation")
        if artifact_id and network.artifact_id != artifact_id:
            raise ValueError("compiled Artifact does not match the imported Network Artifact")
        if version is not None and network.artifact_version != version:
            raise ValueError("compiled Artifact version does not match the imported Network version")

    def validate_final_plc_compile(self, plan_id: str) -> None:
        plan = self.get(plan_id)
        self._require_active(plan)
        blocks = [plan.main_fc, *plan.auxiliary_fbs]
        missing_blocks = [item.block_name for item in blocks if item.status != "verified"]
        missing_dbs = [item.db_name for item in plan.instance_dbs if item.status != "imported"]
        if missing_blocks or missing_dbs:
            raise ValueError(f"blocks and instance DBs must be verified before PLC compilation: blocks={missing_blocks}, instance_dbs={missing_dbs}")

    def validate_ready_to_save(self, plan_id: str) -> LadGenerationPlan:
        plan = self.get(plan_id)
        self.validate_final_plc_compile(plan_id)
        if plan.step_status.get("plc_compile") != "verified":
            raise ValueError("a successful final PLC compilation is required before saving")
        return plan

    def link_artifact(self, plan_id: str, block_name: str, artifact_id: str, version: int) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            self._validate_artifact_order(plan, block_name)
            blocks = [plan.main_fc, *plan.auxiliary_fbs]
            block = next((item for item in blocks if item.block_name == block_name), None)
            if block is None:
                raise KeyError("block not found in plan")
            block.artifact_id, block.artifact_version, block.status = artifact_id, version, "artifact_created"
            plan.artifacts[block_name] = {"artifact_id": artifact_id, "version": version, "block_type": block.block_type}
            plan.step_status["artifact_creation"] = "artifact_created"
            return self.save(plan)

    @staticmethod
    def _validate_artifact_order(plan: LadGenerationPlan, block_name: str) -> None:
        blocks = {item.block_name: item for item in [plan.main_fc, *plan.auxiliary_fbs]}
        if block_name not in blocks:
            raise KeyError("block not found in plan")
        try:
            position = plan.block_dependency_order.index(block_name)
        except ValueError as exc:
            raise ValueError("block is missing from dependency order") from exc
        missing = [
            name for name in plan.block_dependency_order[:position]
            if name in blocks and blocks[name].artifact_id is None
        ]
        if missing:
            raise ValueError(f"dependency Artifacts must be created first: {', '.join(missing)}")

    def validate_artifact_order(self, plan_id: str, block_name: str) -> None:
        plan = self.get(plan_id)
        self._require_active(plan)
        self._require_frozen_blueprint(plan)
        self._validate_knowledge_coverage(plan)
        self._validate_artifact_order(plan, block_name)

    def record_artifact_version(self, plan_id: str, block_name: str, version: int, network_key: str | None = None) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            if block_name not in plan.artifacts:
                raise KeyError("artifact is not linked to plan")
            plan.artifacts[block_name]["version"] = version
            block = next(item for item in [plan.main_fc, *plan.auxiliary_fbs] if item.block_name == block_name)
            block.artifact_version = version
            if network_key:
                network = next(item for item in plan.networks if item.network_key == network_key)
                network.artifact_id = block.artifact_id
                network.artifact_version = version
                network.status = "import_pending"
            return self.save(plan)

    def record_interface_change(self, plan_id: str, block_name: str, affected_networks: list[str], description: str) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            plan.interface_change_log.append(_workflow_event(
                "interface_change",
                block_name=block_name,
                affected_networks=affected_networks,
                description=description,
            ))
            for network_key in affected_networks:
                network = self._network(plan, network_key)
                network.status = "needs_revision"
                network.verified_version = None
                network.compile_result = None
            if affected_networks:
                self._block(plan, block_name).status = "needs_revision"
                plan.step_status["planning"] = "needs_revision"
                self._advance_cursor(plan)
            return self.save(plan)

    def reconcile(self, plan_id: str, artifact_service: Any) -> tuple[LadGenerationPlan, list[dict[str, Any]]]:
        """Repair recoverable Plan/Artifact drift without changing LAD semantics."""
        with self._lock:
            plan = self.get(plan_id)
            self._require_active(plan)
            repairs: list[dict[str, Any]] = []
            for block_name, link in plan.artifacts.items():
                metadata = artifact_service.get_artifact(link["artifact_id"])
                if link.get("version") != metadata.current_version:
                    repairs.append({"kind": "artifact_version", "block_name": block_name, "from": link.get("version"), "to": metadata.current_version})
                    link["version"] = metadata.current_version
                    self._block(plan, block_name).artifact_version = metadata.current_version
                for network in (item for item in plan.networks if item.block_name == block_name):
                    artifact_state = metadata.network_states.get(network.network_key)
                    if artifact_state == "verified" and metadata.verified_versions.get(network.network_key) == metadata.current_version and network.status != "verified":
                        repairs.append({"kind": "network_verified", "network_key": network.network_key, "from": network.status, "to": "verified"})
                        network.status = "verified"
                        network.verified_version = metadata.current_version
                        network.artifact_id = metadata.artifact_id
                        network.artifact_version = metadata.current_version
                        network.compile_result = metadata.last_compile
                    elif artifact_state in {"imported", "import_failed", "compile_failed", "needs_revision", "import_pending"} and network.status != artifact_state:
                        repairs.append({"kind": "network_state", "network_key": network.network_key, "from": network.status, "to": artifact_state})
                        network.status = artifact_state
                        network.artifact_id = metadata.artifact_id
                        network.artifact_version = metadata.current_version
            self._advance_cursor(plan)
            if repairs:
                plan.verification_history.append(_workflow_event("reconcile", repairs=repairs))
                plan = self.save(plan)
            return plan, repairs

    def list(self, conversation_id: str | None = None) -> list[LadGenerationPlan]:
        plans: list[LadGenerationPlan] = []
        for path in self.root.glob("ladplan_*.json"):
            try:
                plan = LadGenerationPlan.from_dict(json.loads(path.read_text(encoding="utf-8")))
                if conversation_id is None or plan.conversation_id == conversation_id:
                    plans.append(plan)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sorted(plans, key=lambda item: item.updated_at, reverse=True)
