from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scdw.common.paths import LAD_GENERATION_PLANS_DIR

from .models import GENERATION_STATES, LadGenerationPlan
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

    def save(self, plan: LadGenerationPlan) -> LadGenerationPlan:
        plan.validate()
        plan.updated_at = _stamp()
        if not plan.created_at:
            plan.created_at = plan.updated_at
        with self._lock:
            self._atomic(self._path(plan.plan_id), json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        return plan

    def create_from_requirements(self, requirements: str, *, conversation_id: str, target_device: str, main_fc_name: str = "FC_MainControl") -> LadGenerationPlan:
        return self.save(LadPlanner().plan(requirements, conversation_id=conversation_id, target_device=target_device, main_fc_name=main_fc_name))

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
            "created_at": stamp,
            "updated_at": stamp,
        }
        plan = LadGenerationPlan.from_dict(value)
        if plan.current_block is None and plan.block_dependency_order:
            plan.current_block = plan.block_dependency_order[0]
        if plan.current_network is None and plan.networks:
            plan.current_network = plan.networks[0].network_key
        return self.save(plan)

    def get(self, plan_id: str) -> LadGenerationPlan:
        path = self._path(plan_id)
        if not path.is_file():
            raise KeyError("plan not found")
        return LadGenerationPlan.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def set_cursor(self, plan_id: str, block_name: str | None, network_key: str | None) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
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
            network = next((item for item in plan.networks if item.network_key == network_key), None)
            if network is None:
                raise KeyError("Network not found")
            network.status = status
            return self.save(plan)

    def set_runtime_status(self, plan_id: str, status: str, *, block_name: str | None = None, network_key: str | None = None, instance_db_name: str | None = None) -> LadGenerationPlan:
        if status not in GENERATION_STATES:
            raise ValueError("unsupported runtime status")
        with self._lock:
            plan = self.get(plan_id)
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

    def record_import_result(self, plan_id: str, block_name: str, artifact_id: str, version: int, result: dict[str, Any], network_key: str | None = None) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
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
        networks = [item for item in plan.networks if item.block_name == block_name]
        missing = [item.network_key for item in networks if item.status != "verified"]
        if missing:
            raise ValueError(f"all block Networks must be verified before final block compilation: {', '.join(missing)}")

    def validate_network_compile(self, plan_id: str, block_name: str, network_key: str, artifact_id: str | None, version: int | None) -> None:
        plan = self.get(plan_id)
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
        self._validate_artifact_order(self.get(plan_id), block_name)

    def record_artifact_version(self, plan_id: str, block_name: str, version: int, network_key: str | None = None) -> LadGenerationPlan:
        with self._lock:
            plan = self.get(plan_id)
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
            plan.interface_change_log.append({
                "recorded_at": _stamp(),
                "block_name": block_name,
                "affected_networks": affected_networks,
                "description": description,
            })
            return self.save(plan)

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
