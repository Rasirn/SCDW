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

from .models import GENERATION_STATES, KnowledgeGapError, LadGenerationPlan, PlanValidationError
from .planner import LadPlanner


_PLAN_ID = re.compile(r"^ladplan_[a-f0-9]{16,64}$")


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LadPlanService:
    """Atomic JSON persistence for progress in one long LLM conversation."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or LAD_GENERATION_PLANS_DIR).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

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
        all_ids = [str(item["id"]) for item in library.catalog()["items"]]
        items = library.get_many(all_ids) if all_ids else []
        catalog_text = json.dumps(
            [item["metadata"] for item in items], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        summary = {
            "captured_at": _stamp(),
            "catalog_sha256": hashlib.sha256(catalog_text.encode("utf-8")).hexdigest(),
            "items": {
                item["id"]: hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
                for item in items if item["id"] in selected
            },
        }
        value = {**summary, "items": [
            {
                "id": item["id"],
                "metadata": item["metadata"],
                "content": item["content"],
                "content_sha256": hashlib.sha256(item["content"].encode("utf-8")).hexdigest(),
            }
            for item in items
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
        for network in plan.networks:
            selected = list(dict.fromkeys(network.selected_knowledge_ids))
            network.selected_knowledge_ids = selected
            network.knowledge_ids = list(selected)
            unknown = [item_id for item_id in selected if item_id not in entries]
            if unknown:
                network.uncovered_capabilities = [f"knowledge_id:{item_id}" for item_id in unknown]
                issues.append({"network_key": network.network_key, "code": "UNKNOWN_KNOWLEDGE_ID", "message": f"unknown knowledge IDs: {', '.join(unknown)}", "uncovered_capabilities": network.uncovered_capabilities})
                continue
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
            network.uncovered_capabilities = [item for item in required if item not in provided]
            if network.uncovered_capabilities:
                issues.append({"network_key": network.network_key, "code": "UNCOVERED_CAPABILITIES", "message": f"uncovered capabilities: {', '.join(network.uncovered_capabilities)}", "uncovered_capabilities": list(network.uncovered_capabilities)})
            topology_kind = str(network.topology.get("kind", "")).strip().lower()
            if topology_kind in {"parallel", "parallel_merge"} and len([branch for branch in network.parallel_branches if branch]) < 2:
                issues.append({"network_key": network.network_key, "code": "INVALID_PARALLEL_BRANCHES", "message": "parallel topology requires at least two explicit parallel_branches", "uncovered_capabilities": ["topology.parallel_branches"]})
        return issues

    def _validate_knowledge_coverage(self, plan: LadGenerationPlan) -> None:
        issues = self._collect_knowledge_issues(plan)
        if issues:
            raise PlanValidationError(issues)

    def save(self, plan: LadGenerationPlan) -> LadGenerationPlan:
        plan.validate()
        self._validate_knowledge_coverage(plan)
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
            self._validate_knowledge_coverage(plan)
            self._write_knowledge_snapshot(plan)
            self._replace_active_plans(plan)
            return self.save(plan)

    def create_from_requirements(self, requirements: str, *, conversation_id: str, target_device: str, main_fc_name: str = "FC_MainControl") -> LadGenerationPlan:
        return self._save_new_active(LadPlanner().plan(requirements, conversation_id=conversation_id, target_device=target_device, main_fc_name=main_fc_name))

    def create_from_planning(self, planning: dict[str, Any], *, requirements: str, conversation_id: str, target_device: str) -> LadGenerationPlan:
        """Persist the complete plan already formed by the current LLM conversation."""
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
            "status": "active",
            "replaced_by": None,
            "closed_at": None,
            "created_at": stamp,
            "updated_at": stamp,
        }
        plan = LadGenerationPlan.from_dict(value)
        if plan.current_block is None and plan.block_dependency_order:
            plan.current_block = plan.block_dependency_order[0]
        if plan.current_network is None and plan.networks:
            plan.current_network = plan.networks[0].network_key
        issues = self._collect_knowledge_issues(plan)
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
            plan.step_status["planning"] = "needs_revision"
            plan.verification_history.append({
                "operation": "semantic_change_requires_replan",
                "network_key": network_key,
                "reason": reason,
                "recorded_at": _stamp(),
            })
            return self.save(plan)

    def revise_network_plan(self, plan_id: str, network_key: str, revision: dict[str, Any], reason: str) -> LadGenerationPlan:
        """Revise knowledge/topology on the same active plan and keep Artifact linkage."""
        allowed = {
            "title", "comment", "purpose", "main_branch", "parallel_branches", "instructions", "variables",
            "required_capabilities", "selected_knowledge_ids", "instruction_chain", "topology", "depends_on", "split_reason",
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
            for name, value in revision.items():
                setattr(network, name, value)
            network.status = "needs_revision"
            network.import_result = None
            network.compile_result = None
            network.verified_version = None
            plan.step_status["planning"] = "planned"
            plan.verification_history.append({
                "operation": "revise_network_plan",
                "network_key": network_key,
                "reason": reason,
                "changed_fields": sorted(revision),
                "recorded_at": _stamp(),
            })
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
            plan.verification_history.append({"operation": "import", **result})
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
            plan.verification_history.append({"operation": "compile", "artifact_id": artifact_id, "version": version, "network_key": network_key, **result})
            return self.save(plan)

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
            plan.verification_history.append({"operation": "create_instance_db", **result})
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
            plan.interface_change_log.append({
                "recorded_at": _stamp(),
                "block_name": block_name,
                "affected_networks": affected_networks,
                "description": description,
            })
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
                plan.verification_history.append({"operation": "reconcile", "recorded_at": _stamp(), "repairs": repairs})
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
