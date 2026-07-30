"""Concise MCP surface for persisted, single-conversation LAD plans."""

import json

from scdw.lad_generation import LadPlanService


def register_lad_plan_tools(mcp, service: LadPlanService | None = None) -> None:
    service = service or LadPlanService()

    def ok(**data):
        return json.dumps({"success": True, **data}, ensure_ascii=False, sort_keys=True)

    def fail(exc: Exception):
        return json.dumps({"success": False, "stage": "lad_plan", "code": type(exc).__name__, "message": str(exc), "retryable": False}, ensure_ascii=False, sort_keys=True)

    @mcp.tool(name="create_lad_generation_plan", description="Plan a main FC, any necessary stateful helper FBs and instance DBs, merged Networks, interfaces, dependencies and explicit knowledge IDs; persist the result as JSON.")
    def create_lad_generation_plan(requirements: str, conversation_id: str, target_device: str, main_fc_name: str = "FC_MainControl") -> str:
        try:
            return ok(plan=service.create_from_requirements(requirements, conversation_id=conversation_id, target_device=target_device, main_fc_name=main_fc_name).to_dict())
        except (KeyError, ValueError, OSError) as exc:
            return fail(exc)

    @mcp.tool(name="save_lad_generation_plan", description="Persist the complete overall LAD plan already formed in this conversation, including explicit Network knowledge IDs, merge decisions and split reasons.")
    def save_lad_generation_plan(planning: dict, requirements: str, conversation_id: str, target_device: str) -> str:
        try:
            return ok(plan=service.create_from_planning(planning, requirements=requirements, conversation_id=conversation_id, target_device=target_device).to_dict())
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

    @mcp.tool(name="link_lad_plan_artifact", description="Link an FC or FB Artifact and immutable version to its block in one LAD plan.")
    def link_lad_plan_artifact(plan_id: str, block_name: str, artifact_id: str, version: int) -> str:
        try:
            return ok(plan=service.link_artifact(plan_id, block_name, artifact_id, version).to_dict())
        except (KeyError, ValueError, OSError) as exc:
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
