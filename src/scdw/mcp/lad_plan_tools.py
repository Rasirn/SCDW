"""Concise MCP surface for persisted, single-conversation LAD plans."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from scdw.lad_generation import LadPlanService
from scdw.lad_generation.capabilities import LadCapabilityCatalog
from scdw.xml_workspace import ArtifactError, XmlArtifactService


class BlockPlanningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_name: str
    block_type: Literal["FC", "FB"]
    responsibility: str
    logic_scope: list[str] = Field(default_factory=list)
    interface: dict[str, Any] = Field(default_factory=dict)


class AuxiliaryFbPlanningInput(BlockPlanningInput):
    state_features: list[str] = Field(default_factory=list)


class InstanceDbPlanningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    db_name: str
    fb_name: str
    instance_name: str
    responsibility: str


class NetworkTopologyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["series", "parallel", "fan_out", "merge", "parallel_merge"]
    description: str


class BlueprintNodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    label: str = Field(min_length=1)
    capability_id: str | None = None
    operands: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    knowledge_ids: list[str] = Field(default_factory=list)
    renderer_id: str | None = None
    children: list["BlueprintNodeInput"] = Field(default_factory=list)


class NetworkPlanningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network_key: str = Field(min_length=1)
    block_name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    comment: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    main_branch: list[str] = Field(min_length=1)
    parallel_branches: list[list[str]]
    instructions: list[str] = Field(min_length=1)
    variables: list[str] = Field(min_length=1)
    required_capabilities: list[str] = Field(min_length=1)
    selected_knowledge_ids: list[str] = Field(min_length=1)
    instruction_chain: list[str] = Field(min_length=1)
    topology: NetworkTopologyInput
    depends_on: list[str] = Field(default_factory=list)
    split_reason: str | None = None
    blueprint: BlueprintNodeInput
    renderer_id: str | None = None


class LadPlanningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    main_fc: BlockPlanningInput
    main_fc_reason: str
    auxiliary_fbs: list[AuxiliaryFbPlanningInput] = Field(default_factory=list)
    instance_dbs: list[InstanceDbPlanningInput] = Field(default_factory=list)
    block_dependency_order: list[str]
    interface_plan: dict[str, Any]
    networks: list[NetworkPlanningInput]
    requested_network_count: int | None = Field(default=None, ge=1)
    planned_network_count: int = Field(ge=0)
    instruction_pipeline: list[str] = Field(min_length=1)


def _model_dict(value: BaseModel | dict) -> dict:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    return dump(exclude_none=True) if dump else value.dict(exclude_none=True)


def register_lad_plan_tools(mcp, service: LadPlanService | None = None, artifact_service: XmlArtifactService | None = None) -> None:
    service = service or LadPlanService()
    artifact_service = artifact_service or XmlArtifactService()

    def ok(**data):
        return json.dumps({"success": True, **data}, ensure_ascii=False, sort_keys=True)

    def fail(exc: Exception):
        issues = getattr(exc, "issues", [])
        return json.dumps({
            "success": False,
            "stage": "lad_plan",
            "code": getattr(exc, "code", type(exc).__name__),
            "message": str(exc),
            "network_key": getattr(exc, "network_key", None),
            "uncovered_capabilities": getattr(exc, "uncovered", []),
            "issues": issues,
            "retryable": False,
            "needs_user_action": False,
            "recommended_action": "revise_plan_in_memory_and_retry_once" if issues else "inspect_plan_error",
            "fallback_arguments": {"issues": issues} if issues else {},
        }, ensure_ascii=False, sort_keys=True)

    @mcp.tool(
        name="get_lad_capability_catalog",
        description="Read the compact, XML-free LAD capability catalog before planning. It reports actual availability, state ownership, knowledge IDs, renderer IDs and allowed adjacency.",
    )
    def get_lad_capability_catalog() -> str:
        try:
            return ok(catalog=LadCapabilityCatalog.instance().compact())
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="draft_lad_generation_plan", description="Return a deterministic non-persisted LAD draft. It never creates or replaces the single active formal Plan; save_lad_generation_plan persists the reviewed plan.")
    def draft_lad_generation_plan(requirements: str, conversation_id: str, target_device: str, main_fc_name: str = "FC_MainControl") -> str:
        try:
            from scdw.lad_generation import LadPlanner
            value = LadPlanner().plan(requirements, conversation_id=conversation_id, target_device=target_device, main_fc_name=main_fc_name)
            return ok(
                draft=True, persisted=False, plan=value.to_dict(),
                blueprint_tree=service.render_blueprint_tree(value),
                required_next="save_lad_generation_plan",
            )
        except (KeyError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="save_lad_generation_plan", description="Persist the complete overall LAD plan already formed in this conversation, including explicit Network knowledge IDs, merge decisions and split reasons.")
    def save_lad_generation_plan(planning: LadPlanningInput, requirements: str, conversation_id: str, target_device: str) -> str:
        try:
            plan = service.create_from_planning(_model_dict(planning), requirements=requirements, conversation_id=conversation_id, target_device=target_device)
            return ok(
                plan=plan.to_dict(), blueprint_tree=service.render_blueprint_tree(plan),
                blueprint_status=plan.blueprint_status, blueprint_sha256=plan.blueprint_sha256,
                uncovered_capabilities=plan.uncovered_capabilities,
                next=service.next_step(plan.plan_id),
            )
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="get_lad_generation_plan", description="Restore one persisted LAD generation plan, including cursor, step states and linked Artifact versions.")
    def get_lad_generation_plan(plan_id: str) -> str:
        try:
            plan = service.get(plan_id)
            return ok(plan=plan.to_dict(), blueprint_tree=service.render_blueprint_tree(plan), next=service.next_step(plan_id))
        except (KeyError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="list_lad_generation_plans", description="List lightweight LAD plans, optionally restricted to one conversation ID.")
    def list_lad_generation_plans(conversation_id: str | None = None) -> str:
        return ok(plans=[item.to_dict() for item in service.list(conversation_id)])

    @mcp.tool(name="close_lad_generation_plan", description="Close the active LAD plan so it cannot generate or edit further Artifacts.")
    def close_lad_generation_plan(plan_id: str) -> str:
        try:
            return ok(plan=service.close(plan_id).to_dict())
        except (KeyError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(
        name="check_and_freeze_lad_blueprint",
        description="Run complete capability, knowledge, renderer, connection, operand, state, Network-boundary and text preflight; on success freeze control semantics as approved_for_generation.",
    )
    def check_and_freeze_lad_blueprint(plan_id: str) -> str:
        try:
            plan = service.freeze_blueprint(plan_id)
            return ok(
                plan_id=plan.plan_id, blueprint_status=plan.blueprint_status,
                blueprint_sha256=plan.blueprint_sha256,
                capability_catalog_sha256=plan.capability_catalog_sha256,
                uncovered_capabilities=plan.uncovered_capabilities,
                blueprint_tree=service.render_blueprint_tree(plan), next=service.next_step(plan_id),
            )
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="revise_lad_network_plan", description="Revise one Network's capabilities, selected knowledge IDs, instruction chain or topology on the same active plan. Use this after a knowledge-backed structural diagnosis; it preserves the linked Artifact and marks only that Network needs_revision.")
    def revise_lad_network_plan(plan_id: str, network_key: str, revision: dict, reason: str) -> str:
        try:
            return ok(plan=service.revise_network_plan(plan_id, network_key, revision, reason).to_dict())
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="record_lad_interface_change", description="Record an Interface change and the block and Network keys it may affect.")
    def record_lad_interface_change(plan_id: str, block_name: str, affected_networks: list[str], description: str) -> str:
        try:
            return ok(plan=service.record_interface_change(plan_id, block_name, affected_networks, description).to_dict())
        except (KeyError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="reconcile_lad_workflow", description="Reconcile recoverable Plan and Artifact version/state drift after interruption. It does not change LAD XML or semantics and returns the exact resume action.")
    def reconcile_lad_workflow(plan_id: str) -> str:
        try:
            plan, repairs = service.reconcile(plan_id, artifact_service)
            return ok(plan_id=plan.plan_id, repairs=repairs, next=service.next_step(plan_id))
        except (ArtifactError, KeyError, ValueError, OSError) as exc:
            return fail(exc)
