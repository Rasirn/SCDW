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

STATE_TRANSITIONS = {
    "planned": {"artifact_created", "generating", "needs_revision"},
    "artifact_created": {"generating", "generated", "import_pending", "needs_revision"},
    "generating": {"generated", "needs_revision"},
    "generated": {"import_pending", "needs_revision"},
    "import_pending": {"importing", "needs_revision"},
    "importing": {"imported", "import_failed"},
    "imported": {"compiling", "importing", "needs_revision"},
    "compile_pending": {"compiling", "needs_revision"},
    "compiling": {"verified", "compile_failed"},
    "import_failed": {"import_pending", "importing", "needs_revision"},
    "compile_failed": {"import_pending", "compiling", "needs_revision"},
    "verified": {"needs_revision", "import_pending"},
    "needs_revision": {"generating", "generated", "import_pending"},
}


def require_state_transition(current: str, target: str) -> None:
    if current == target:
        return
    if target not in STATE_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid workflow state transition: {current} -> {target}")

PLAN_STATES = {"active", "replaced", "closed"}
TOPOLOGY_KINDS = {"series", "parallel", "fan_out", "merge", "parallel_merge"}


class KnowledgeGapError(ValueError):
    """The selected catalog entries cannot support a planned Network."""

    code = "KNOWLEDGE_GAP"

    def __init__(self, message: str, *, network_key: str | None = None, uncovered: list[str] | None = None) -> None:
        super().__init__(message)
        self.network_key = network_key
        self.uncovered = list(uncovered or [])


class PlanValidationError(KnowledgeGapError):
    """All independently detectable plan issues returned in one response."""

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues
        uncovered = sorted({item for issue in issues for item in issue.get("uncovered_capabilities", [])})
        super().__init__(
            f"plan has {len(issues)} validation issue(s); fix them in one revision",
            network_key=issues[0].get("network_key") if len(issues) == 1 else None,
            uncovered=uncovered,
        )


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
    required_capabilities: list[str] = field(default_factory=list)
    selected_knowledge_ids: list[str] = field(default_factory=list)
    uncovered_capabilities: list[str] = field(default_factory=list)
    instruction_chain: list[str] = field(default_factory=list)
    topology: dict[str, Any] = field(default_factory=dict)
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
    knowledge_snapshot: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    replaced_by: str | None = None
    closed_at: str | None = None
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
        networks = []
        for raw in data.get("networks", []):
            item = dict(raw)
            selected = list(item.get("selected_knowledge_ids") or item.get("knowledge_ids") or [])
            item["selected_knowledge_ids"] = selected
            item["knowledge_ids"] = list(item.get("knowledge_ids") or selected)
            item.setdefault("required_capabilities", [])
            item.setdefault("uncovered_capabilities", [])
            item.setdefault("instruction_chain", list(item.get("instructions") or []))
            item.setdefault("topology", {})
            networks.append(NetworkPlan(**item))
        data["networks"] = networks
        data.setdefault("status", "active")
        data.setdefault("replaced_by", None)
        data.setdefault("closed_at", None)
        data.setdefault("knowledge_snapshot", {})
        return cls(**data)

    def validate(self) -> None:
        if self.status not in PLAN_STATES:
            raise ValueError(f"unsupported plan status: {self.status}")
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
            if not network.instructions:
                raise KnowledgeGapError(f"Network {network.network_key} requires explicit instructions", network_key=network.network_key)
            if not network.variables:
                raise KnowledgeGapError(f"Network {network.network_key} requires explicit variables", network_key=network.network_key)
            if not network.main_branch:
                raise KnowledgeGapError(f"Network {network.network_key} requires a non-empty main_branch", network_key=network.network_key)
            if not network.instruction_chain:
                raise KnowledgeGapError(f"Network {network.network_key} requires an explicit instruction_chain", network_key=network.network_key)
            if not network.required_capabilities:
                raise KnowledgeGapError(f"Network {network.network_key} requires required_capabilities", network_key=network.network_key)
            if not network.selected_knowledge_ids:
                raise KnowledgeGapError(f"Network {network.network_key} requires selected_knowledge_ids", network_key=network.network_key)
            topology_kind = str(network.topology.get("kind", "")).strip().lower()
            if not topology_kind or not network.topology.get("description"):
                raise KnowledgeGapError(f"Network {network.network_key} requires explicit topology kind and description", network_key=network.network_key)
            if topology_kind not in TOPOLOGY_KINDS:
                raise KnowledgeGapError(
                    f"Network {network.network_key} uses unsupported topology kind: {topology_kind}",
                    network_key=network.network_key,
                    uncovered=[f"topology.kind:{topology_kind}"],
                )
            if topology_kind in {"parallel", "parallel_merge"}:
                branches = [branch for branch in network.parallel_branches if branch]
                if len(branches) < 2:
                    raise KnowledgeGapError(
                        f"Network {network.network_key} parallel topology requires at least two explicit parallel_branches",
                        network_key=network.network_key,
                        uncovered=["topology.parallel_branches"],
                    )
            if network.block_name not in block_names:
                raise ValueError(f"Network {network.network_key} references an unknown block")
            if network.status not in GENERATION_STATES:
                raise ValueError(f"unsupported Network status: {network.status}")
            keys.add(network.network_key)
        for status in self.step_status.values():
            if status not in GENERATION_STATES:
                raise ValueError(f"unsupported step status: {status}")
