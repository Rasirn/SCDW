from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


GENERATION_STATES = {
    "planned",
    "artifact_created",
    "generating",
    "generated",
    "import_pending",
    "importing",
    "imported",
    "import_failed",
    "compile_pending",
    "compiling",
    "compile_failed",
    "verified",
    "needs_revision",
}


@dataclass
class BlockPlan:
    block_name: str
    block_type: str
    responsibility: str
    logic_scope: list[str] = field(default_factory=list)
    interface: dict[str, Any] = field(default_factory=dict)
    status: str = "planned"
    artifact_id: str | None = None
    artifact_version: int | None = None
    imported_at: str | None = None
    compiled_at: str | None = None
    import_result: dict[str, Any] | None = None
    compile_result: dict[str, Any] | None = None
    verified_version: int | None = None


@dataclass
class AuxiliaryFbPlan(BlockPlan):
    state_features: list[str] = field(default_factory=list)


@dataclass
class InstanceDbPlan:
    db_name: str
    fb_name: str
    instance_name: str
    responsibility: str
    status: str = "planned"
    created_at: str | None = None
    create_result: dict[str, Any] | None = None


@dataclass
class NetworkPlan:
    network_key: str
    block_name: str
    title: str
    comment: str
    purpose: str
    main_branch: list[str] = field(default_factory=list)
    parallel_branches: list[list[str]] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    knowledge_ids: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    split_reason: str | None = None
    status: str = "planned"
    artifact_id: str | None = None
    artifact_version: int | None = None
    imported_at: str | None = None
    compiled_at: str | None = None
    import_result: dict[str, Any] | None = None
    compile_result: dict[str, Any] | None = None
    verified_version: int | None = None


@dataclass
class LadGenerationPlan:
    plan_id: str
    conversation_id: str
    target_device: str
    requirements: str
    main_fc: BlockPlan
    main_fc_reason: str
    auxiliary_fbs: list[AuxiliaryFbPlan]
    instance_dbs: list[InstanceDbPlan]
    block_dependency_order: list[str]
    interface_plan: dict[str, Any]
    networks: list[NetworkPlan]
    current_block: str | None = None
    current_network: str | None = None
    step_status: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    interface_change_log: list[dict[str, Any]] = field(default_factory=list)
    verification_history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LadGenerationPlan":
        data = dict(value)
        data["main_fc"] = BlockPlan(**data["main_fc"])
        data["auxiliary_fbs"] = [AuxiliaryFbPlan(**item) for item in data.get("auxiliary_fbs", [])]
        data["instance_dbs"] = [InstanceDbPlan(**item) for item in data.get("instance_dbs", [])]
        data["networks"] = [NetworkPlan(**item) for item in data.get("networks", [])]
        return cls(**data)

    def validate(self) -> None:
        if self.main_fc.block_type != "FC":
            raise ValueError("main_fc must remain an FC")
        if any(block.block_type != "FB" for block in self.auxiliary_fbs):
            raise ValueError("auxiliary blocks must be FBs")
        if any(block.status not in GENERATION_STATES for block in [self.main_fc, *self.auxiliary_fbs]):
            raise ValueError("unsupported block status")
        if any(db.status not in GENERATION_STATES for db in self.instance_dbs):
            raise ValueError("unsupported instance DB status")
        keys: set[str] = set()
        block_names = {self.main_fc.block_name, *(block.block_name for block in self.auxiliary_fbs)}
        for network in self.networks:
            if not network.network_key or network.network_key in keys:
                raise ValueError("network_key values must be non-empty and unique")
            if not network.title.strip() or not network.comment.strip():
                raise ValueError(f"Network {network.network_key} requires title and comment")
            if network.block_name not in block_names:
                raise ValueError(f"Network {network.network_key} references an unknown block")
            if network.status not in GENERATION_STATES:
                raise ValueError(f"unsupported Network status: {network.status}")
            keys.add(network.network_key)
        for status in self.step_status.values():
            if status not in GENERATION_STATES:
                raise ValueError(f"unsupported step status: {status}")
