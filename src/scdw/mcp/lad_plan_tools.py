"""Concise MCP surface for persisted, single-conversation LAD plans."""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from scdw.lad_generation import LadPlanService
from scdw.xml_workspace import ArtifactError, XmlArtifactService


class BlockPlanningInput(BaseModel):
    block_name: str
    block_type: Literal["FC", "FB"]
    responsibility: str
    logic_scope: list[str] = Field(default_factory=list)
    interface: dict[str, Any] = Field(default_factory=dict)


class AuxiliaryFbPlanningInput(BlockPlanningInput):
    state_features: list[str] = Field(default_factory=list)


class InstanceDbPlanningInput(BaseModel):
    db_name: str
    fb_name: str
    instance_name: str
    responsibility: str


class NetworkTopologyInput(BaseModel):
    kind: Literal["series", "parallel", "fan_out", "merge", "parallel_merge"]
    description: str


class NetworkPlanningInput(BaseModel):
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


class LadPlanningInput(BaseModel):
    main_fc: BlockPlanningInput
    main_fc_reason: str
    auxiliary_fbs: list[AuxiliaryFbPlanningInput] = Field(default_factory=list)
    instance_dbs: list[InstanceDbPlanningInput] = Field(default_factory=list)
    block_dependency_order: list[str]
    interface_plan: dict[str, Any]
    networks: list[NetworkPlanningInput]


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
        return json.dumps({
            "success": False,
            "stage": "lad_plan",
            "code": getattr(exc, "code", type(exc).__name__),
            "message": str(exc),
            "network_key": getattr(exc, "network_key", None),
            "uncovered_capabilities": getattr(exc, "uncovered", []),
            "retryable": False,
        }, ensure_ascii=False, sort_keys=True)

    @mcp.tool(name="create_lad_generation_plan", description="Plan a main FC, any necessary stateful helper FBs and instance DBs, merged Networks, interfaces, dependencies and explicit knowledge IDs; persist the result as JSON.")
    def create_lad_generation_plan(requirements: str, conversation_id: str, target_device: str, main_fc_name: str = "FC_MainControl") -> str:
        try:
            return ok(plan=service.create_from_requirements(requirements, conversation_id=conversation_id, target_device=target_device, main_fc_name=main_fc_name).to_dict())
        except (KeyError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="save_lad_generation_plan", description="Persist the complete overall LAD plan already formed in this conversation, including explicit Network knowledge IDs, merge decisions and split reasons.")
    def save_lad_generation_plan(planning: LadPlanningInput, requirements: str, conversation_id: str, target_device: str) -> str:
        try:
            return ok(plan=service.create_from_planning(_model_dict(planning), requirements=requirements, conversation_id=conversation_id, target_device=target_device).to_dict())
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="get_lad_generation_plan", description="Restore one persisted LAD generation plan, including cursor, step states and linked Artifact versions.")
    def get_lad_generation_plan(plan_id: str) -> str:
        try:
            return ok(plan=service.get(plan_id).to_dict())
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

    @mcp.tool(name="set_lad_generation_cursor", description="Persist the block and Network currently being processed in this conversation.")
    def set_lad_generation_cursor(plan_id: str, block_name: str | None = None, network_key: str | None = None) -> str:
        try:
            return ok(plan=service.set_cursor(plan_id, block_name, network_key).to_dict())
        except (KeyError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="set_lad_network_plan_status", description="Persist one planned Network generation status without importing or compiling it.")
    def set_lad_network_plan_status(plan_id: str, network_key: str, status: str) -> str:
        try:
            return ok(plan=service.set_network_status(plan_id, network_key, status).to_dict())
        except (KeyError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="revise_lad_network_plan", description="Revise one Network's capabilities, selected knowledge IDs, instruction chain or topology on the same active plan. Use this after a knowledge-backed structural diagnosis; it preserves the linked Artifact and marks only that Network needs_revision.")
    def revise_lad_network_plan(plan_id: str, network_key: str, revision: dict, reason: str) -> str:
        try:
            return ok(plan=service.revise_network_plan(plan_id, network_key, revision, reason).to_dict())
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="link_lad_plan_artifact", description="Link an FC or FB Artifact and immutable version to its block in one LAD plan.")
    def link_lad_plan_artifact(plan_id: str, block_name: str, artifact_id: str, version: int) -> str:
        try:
            plan = service.get(plan_id)
            block = next((item for item in [plan.main_fc, *plan.auxiliary_fbs] if item.block_name == block_name), None)
            if block is None:
                raise KeyError("block not found in plan")
            linked = service.link_artifact(plan_id, block_name, artifact_id, version)
            artifact = artifact_service.relink_plan(artifact_id, plan_id, block_name, block.block_type)
            return ok(plan=linked.to_dict(), artifact=artifact.to_dict())
        except (ArtifactError, KeyError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="record_lad_artifact_version", description="Record a new Artifact version for a plan block and move the edited Network to import_pending.")
    def record_lad_artifact_version(plan_id: str, block_name: str, version: int, network_key: str | None = None) -> str:
        try:
            return ok(plan=service.record_artifact_version(plan_id, block_name, version, network_key).to_dict())
        except (KeyError, ValueError, OSError, StopIteration) as exc:
            return fail(exc)

    @mcp.tool(name="record_lad_interface_change", description="Record an Interface change and the block and Network keys it may affect.")
    def record_lad_interface_change(plan_id: str, block_name: str, affected_networks: list[str], description: str) -> str:
        try:
            return ok(plan=service.record_interface_change(plan_id, block_name, affected_networks, description).to_dict())
        except (KeyError, ValueError, OSError) as exc:
            return fail(exc)
