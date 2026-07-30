"""Concise MCP tools for versioned XML artifacts and Network-local edits."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scdw.common.run_logging import get_run_logger
from scdw.lad_generation import LadPlanService
from scdw.rag import KnowledgeLibrary
from scdw.xml_workspace import ArtifactError, PatchOperation, XmlArtifactService, render_contact_or_network


class PatchOperationInput(BaseModel):
    """Strict public Patch schema validated before any Artifact write."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["replace_exact", "insert_before", "insert_after", "delete_exact"]
    old: str = Field(min_length=1)
    new: str | None = None
    expected_occurrences: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_operation(self):
        if self.op in {"replace_exact", "insert_before", "insert_after"} and self.new is None:
            raise ValueError(f"{self.op} requires new")
        if self.op == "delete_exact" and self.new is not None:
            raise ValueError("delete_exact does not accept new")
        if self.op == "replace_exact" and self.new == self.old:
            raise ValueError("replace_exact old and new must differ")
        return self


class KnowledgeNetworkBindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contacts: list[list[str]] = Field(min_length=1)
    output: list[str] = Field(min_length=1)
    edge_memory: list[str] | None = None


def register_xml_artifact_tools(mcp, session, service: XmlArtifactService | None = None, plan_service: LadPlanService | None = None) -> None:
    service = service or XmlArtifactService()
    plan_service = plan_service or LadPlanService()

    def ok(**data): return json.dumps({"success": True, **data}, ensure_ascii=False, sort_keys=True)
    def fail(exc: ArtifactError): return json.dumps({"success": False, "stage": "artifact", "code": exc.code, "message": str(exc), "retryable": exc.retryable}, ensure_ascii=False, sort_keys=True)

    def ensure_network_editable(artifact_id: str, network_keys: list[str]) -> None:
        metadata = service.get_artifact(artifact_id)
        if metadata.plan_id and metadata.block_name:
            try:
                for key in network_keys:
                    plan_service.validate_network_generation_order(metadata.plan_id, metadata.block_name, key)
            except (KeyError, ValueError, OSError) as exc:
                raise ArtifactError(getattr(exc, "code", "PLAN_PRECONDITION_FAILED"), str(exc)) from exc

    def reject_semantic_repair(artifact_id: str, network_key: str | None, reason: str) -> None:
        metadata = service.get_artifact(artifact_id)
        if metadata.plan_id and network_key:
            plan_service.mark_network_for_replan(metadata.plan_id, network_key, reason)
        raise ArtifactError("SEMANTIC_CHANGE_REQUIRES_REPLAN", reason)

    def validate_structural_knowledge(artifact_id: str, network_key: str, knowledge_ids: list[str] | None) -> None:
        metadata = service.get_artifact(artifact_id)
        if not metadata.plan_id:
            return
        plan = plan_service.get(metadata.plan_id)
        network = next((item for item in plan.networks if item.network_key == network_key), None)
        if network is None:
            raise ArtifactError("PLAN_PRECONDITION_FAILED", "Network is not present in the linked plan")
        selected = set(knowledge_ids or [])
        if not selected:
            raise ArtifactError("KNOWLEDGE_GAP", "structural subgraph replacement requires explicit knowledge_ids")
        unexpected = sorted(selected - set(network.selected_knowledge_ids))
        if unexpected:
            raise ArtifactError("KNOWLEDGE_GAP", f"knowledge IDs are not selected by the Network plan: {', '.join(unexpected)}")

    def planned_network(artifact_id: str, network_key: str):
        metadata = service.get_artifact(artifact_id)
        if not metadata.plan_id:
            return metadata, None
        plan = plan_service.get(metadata.plan_id)
        network = next((item for item in plan.networks if item.network_key == network_key), None)
        if network is None:
            raise ArtifactError("PLAN_PRECONDITION_FAILED", "Network is not present in the linked plan")
        return metadata, network

    def require_knowledge_renderer_when_published(artifact_id: str, network_key: str) -> None:
        _, network = planned_network(artifact_id, network_key)
        if network is None:
            return
        catalog = {item["id"]: item for item in KnowledgeLibrary.instance().catalog()["items"]}
        required = [
            item_id for item_id in network.selected_knowledge_ids
            if catalog.get(item_id, {}).get("generation_mode") == "knowledge_renderer_required"
        ]
        if required:
            raise ArtifactError(
                "KNOWLEDGE_RENDERER_REQUIRED",
                "this Network selected a reviewed renderable topology; use write_lad_network_from_knowledge with one of: " + ", ".join(required),
            )

    def workflow_data(artifact_id: str, network_key: str | None = None) -> dict:
        metadata = service.get_artifact(artifact_id)
        data = {
            "artifact_id": artifact_id,
            "version": metadata.current_version,
            "network_key": network_key,
            "network_status": metadata.network_states.get(network_key) if network_key else None,
            "plan_id": metadata.plan_id,
            "block_name": metadata.block_name,
            "device_name": metadata.device_name,
        }
        if metadata.plan_id:
            data["next"] = plan_service.next_step(metadata.plan_id)
        return data

    @mcp.tool(name="create_xml_artifact", description="Save parseable XML as immutable version 1. Returns artifact metadata. TIA Portal, not this tool, validates LAD semantics.")
    def create_xml_artifact(xml_content: str, block_name: str | None = None, device_name: str | None = None, conversation_id: str | None = None, plan_id: str | None = None, block_type: str | None = None, network_keys: list[str] | None = None) -> str:
        try:
            if plan_id and not block_name:
                raise ArtifactError("PLAN_PRECONDITION_FAILED", "block_name is required when linking an Artifact to a plan")
            if plan_id and block_name:
                plan_service.validate_artifact_order(plan_id, block_name)
            value = service.create_artifact(xml_content, block_name, device_name, conversation_id, plan_id=plan_id, block_type=block_type, network_keys=network_keys)
            if plan_id and block_name:
                plan_service.link_artifact(plan_id, block_name, value.artifact_id, value.current_version)
            return ok(artifact=value.to_dict())
        except ArtifactError as exc: return fail(exc)
        except (KeyError, ValueError, OSError) as exc:
            return json.dumps({"success": False, "stage": "artifact_write", "code": getattr(exc, "code", type(exc).__name__), "message": str(exc), "uncovered_capabilities": getattr(exc, "uncovered", []), "retryable": False}, ensure_ascii=False)
        except Exception as exc:
            get_run_logger().log_exception("xml_artifact_create_failed", exc, component="mcp.xml_artifact")
            return json.dumps({"success": False, "stage": "artifact_write", "code": "INTERNAL_ERROR", "message": str(exc), "retryable": False}, ensure_ascii=False)

    @mcp.tool(name="create_lad_block_artifact", description="Create an empty FC or FB SimaticML framework with Interface and immutable Artifact version 1, then link it to a persisted LAD plan.")
    def create_lad_block_artifact(plan_id: str, block_name: str, block_type: str = "FC", interface_xml: str | None = None, device_name: str | None = None, conversation_id: str | None = None) -> str:
        try:
            plan_service.validate_artifact_order(plan_id, block_name)
            value = service.create_block_artifact(block_name, block_type, interface_xml=interface_xml, device_name=device_name, conversation_id=conversation_id, plan_id=plan_id)
            plan_service.link_artifact(plan_id, block_name, value.artifact_id, value.current_version)
            return ok(artifact=value.to_dict())
        except ArtifactError as exc:
            return fail(exc)
        except (KeyError, ValueError, OSError) as exc:
            return json.dumps({"success": False, "stage": "artifact_write", "code": getattr(exc, "code", type(exc).__name__), "message": str(exc), "uncovered_capabilities": getattr(exc, "uncovered", []), "retryable": False}, ensure_ascii=False)

    @mcp.tool(name="get_xml_artifact_status", description="Return artifact version, import diagnostics and Network states. Does not return XML content.")
    def get_xml_artifact_status(artifact_id: str) -> str:
        try: return ok(artifact=service.get_artifact(artifact_id).to_dict())
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="get_lad_block_info", description="Read FC/FB type, block name, language, Interface section names, plan link and Network count without returning the full XML.")
    def get_lad_block_info(artifact_id: str, version: int | None = None) -> str:
        try: return ok(block=service.get_block_info(artifact_id, version))
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="read_xml_fragment", description="Read a bounded fragment from an artifact version by search text or line range.")
    def read_xml_fragment(artifact_id: str, version: int | None = None, search: str | None = None, start_line: int | None = None, end_line: int | None = None, context_lines: int = 10, max_chars: int = 12000) -> str:
        try: return ok(fragment=service.read_fragment(artifact_id, version, search, start_line, end_line, context_lines, min(max_chars, 12000)).to_dict())
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="patch_xml_artifact", description="Apply strict replace_exact/insert/delete text or document-ID edits and create at most one immutable version. Structural subgraphs require replace_network_and_prepare_import; semantic changes require replanning.")
    def patch_xml_artifact(artifact_id: str, expected_version: int, operations: list[PatchOperationInput], change_source: str = "patch", affected_networks: list[str] | None = None, repair_kind: str = "text") -> str:
        try:
            operation_values = [item.dict(exclude_none=True) if isinstance(item, PatchOperationInput) else dict(item) for item in operations]
            if repair_kind == "semantic":
                reject_semantic_repair(artifact_id, (affected_networks or [None])[0], "semantic changes to trigger, coil, block type or state behavior require replanning")
            if repair_kind not in {"text", "identifier"}:
                raise ArtifactError("STRUCTURAL_REPAIR_REQUIRES_SUBGRAPH", "Patch only supports text or identifier repairs; replace the complete knowledge-backed Network subgraph")
            protected = ("<Part", "<Wire", "<Access", "<CallInfo", "<Instance", "<Parameter")
            if any(any(token in str(value) for token in protected) for operation in operation_values for value in operation.values()):
                raise ArtifactError("STRUCTURAL_REPAIR_REQUIRES_SUBGRAPH", "Part/Access/Wire/instance structures cannot be repaired with a text Patch")
            ensure_network_editable(artifact_id, affected_networks or [])
            result = service.apply_patch(artifact_id, expected_version, [PatchOperation.from_dict(x) for x in operation_values], change_source=change_source, affected_networks=affected_networks)
            metadata = service.get_artifact(artifact_id)
            if metadata.plan_id and metadata.block_name:
                plan_service.record_artifact_version(metadata.plan_id, metadata.block_name, result.new_version)
                if change_source == "interface_change":
                    plan_service.record_interface_change(metadata.plan_id, metadata.block_name, affected_networks or [], "Interface updated through deterministic XML patch")
            return ok(result=result.to_dict())
        except ArtifactError as exc: return fail(exc)
        except (TypeError, ValueError) as exc: return json.dumps({"success": False, "stage": "artifact_patch", "code": "PATCH_PRECONDITION_FAILED", "message": str(exc), "retryable": False}, ensure_ascii=False)

    @mcp.tool(name="validate_xml_artifact", description="Run only safety checks: artifact exists, content is non-empty and XML is parseable. TIA performs structural and semantic validation.")
    def validate_xml_artifact(artifact_id: str, version: int | None = None) -> str:
        try: return ok(validation=service.validate_artifact(artifact_id, version).to_dict())
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="list_xml_networks", description="List Network indexes, stable network_key values and workflow states for an artifact version.")
    def list_xml_networks(artifact_id: str, version: int | None = None) -> str:
        try: return ok(networks=service.list_networks(artifact_id, version))
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="get_xml_network", description="Return one complete CompileUnit selected by stable network_key.")
    def get_xml_network(artifact_id: str, network_key: str, version: int | None = None) -> str:
        try: return ok(network_key=network_key, xml=service.get_network(artifact_id, network_key, version))
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="append_network_and_prepare_import", description="Append one complete CompileUnit under a stable network_key, create one immutable version, update the linked Plan, and return the exact import/compile resume state.")
    def append_xml_network(artifact_id: str, expected_version: int, network_key: str, compile_unit_xml: str, before_key: str | None = None, position: int | None = None) -> str:
        try:
            metadata = service.get_artifact(artifact_id)
            if metadata.plan_id and metadata.block_name:
                try:
                    plan_service.set_cursor(metadata.plan_id, metadata.block_name, network_key)
                except (KeyError, ValueError, OSError) as exc:
                    raise ArtifactError(getattr(exc, "code", "PLAN_PRECONDITION_FAILED"), str(exc)) from exc
            ensure_network_editable(artifact_id, [network_key])
            require_knowledge_renderer_when_published(artifact_id, network_key)
            version = service.append_network(artifact_id, expected_version, network_key, compile_unit_xml, before_key=before_key, position=position)
            if version == expected_version:
                return json.dumps({"success": True, "stage": "artifact", "code": "NO_CHANGES", "message": "CompileUnit is unchanged; no version was created", **workflow_data(artifact_id, network_key)}, ensure_ascii=False)
            metadata = service.get_artifact(artifact_id)
            if metadata.plan_id and metadata.block_name:
                plan_service.record_artifact_version(metadata.plan_id, metadata.block_name, version, network_key)
            return ok(**workflow_data(artifact_id, network_key))
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="write_lad_network_from_knowledge", description="Render one Network from an explicitly selected reviewed recipe and bindings, update the Artifact and Plan, and return the exact import/compile resume state.")
    def write_lad_network_from_knowledge(artifact_id: str, expected_version: int, network_key: str, knowledge_id: str, bindings: KnowledgeNetworkBindings, title: str, comment: str, replace_existing: bool = False) -> str:
        try:
            metadata, network = planned_network(artifact_id, network_key)
            if not metadata.plan_id or metadata.block_name is None or network is None:
                raise ArtifactError("PLAN_PRECONDITION_FAILED", "knowledge rendering requires an Artifact linked to an active plan Network")
            if knowledge_id not in network.selected_knowledge_ids:
                raise ArtifactError("KNOWLEDGE_GAP", "knowledge_id is not selected by the Network plan")
            try:
                entry = KnowledgeLibrary.instance().get_many([knowledge_id])[0]["metadata"]
            except KeyError as exc:
                raise ArtifactError("KNOWLEDGE_GAP", f"unknown knowledge ID: {knowledge_id}") from exc
            renderer = entry.get("renderer") or {}
            if entry.get("generation_mode") != "knowledge_renderer_required" or not renderer.get("kind"):
                raise ArtifactError("KNOWLEDGE_RENDERER_UNSUPPORTED", "selected knowledge item has no reviewed renderer")
            value = bindings if isinstance(bindings, KnowledgeNetworkBindings) else KnowledgeNetworkBindings(**bindings)
            plan_service.set_cursor(metadata.plan_id, metadata.block_name, network_key)
            ensure_network_editable(artifact_id, [network_key])
            compile_unit = render_contact_or_network(
                str(renderer["kind"]), contacts=value.contacts, output=value.output,
                edge_memory=value.edge_memory, title=title, comment=comment,
            )
            keys = [item["network_key"] for item in service.list_networks(artifact_id)]
            if replace_existing:
                if network_key not in keys:
                    raise ArtifactError("NETWORK_NOT_FOUND", "replace_existing requires an existing network_key")
                version = service.replace_network(artifact_id, expected_version, network_key, compile_unit, source="knowledge_renderer")
            else:
                if network_key in keys:
                    raise ArtifactError("NETWORK_KEY_CONFLICT", "Network exists; set replace_existing=true to replace it")
                version = service.append_network(artifact_id, expected_version, network_key, compile_unit, source="knowledge_renderer")
            if version == expected_version:
                return json.dumps({"success": True, "stage": "artifact", "code": "NO_CHANGES", "message": "Rendered Network is unchanged; no version was created", **workflow_data(artifact_id, network_key)}, ensure_ascii=False)
            plan_service.record_artifact_version(metadata.plan_id, metadata.block_name, version, network_key)
            return ok(**workflow_data(artifact_id, network_key), knowledge_id=knowledge_id, renderer=renderer["kind"])
        except ArtifactError as exc:
            return fail(exc)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return json.dumps({"success": False, "stage": "artifact", "code": getattr(exc, "code", type(exc).__name__), "message": str(exc), "retryable": False}, ensure_ascii=False)

    @mcp.tool(name="replace_network_and_prepare_import", description="Replace one complete knowledge-backed CompileUnit, invalidate prior verification, create one immutable version, update the Plan, and return the import/compile resume state.")
    def replace_xml_network(artifact_id: str, expected_version: int, network_key: str, compile_unit_xml: str, repair_kind: str = "structural_subgraph", knowledge_ids: list[str] | None = None) -> str:
        try:
            if repair_kind == "semantic":
                reject_semantic_repair(artifact_id, network_key, "semantic changes to trigger, coil, FC/FB or state behavior require replanning")
            if repair_kind != "structural_subgraph":
                raise ArtifactError("PATCH_PRECONDITION_FAILED", "replace_xml_network repair_kind must be structural_subgraph or semantic")
            validate_structural_knowledge(artifact_id, network_key, knowledge_ids)
            ensure_network_editable(artifact_id, [network_key])
            require_knowledge_renderer_when_published(artifact_id, network_key)
            version = service.replace_network(artifact_id, expected_version, network_key, compile_unit_xml)
            if version == expected_version:
                return json.dumps({"success": True, "stage": "artifact", "code": "NO_CHANGES", "message": "Network content is unchanged; no version was created", **workflow_data(artifact_id, network_key)}, ensure_ascii=False)
            metadata = service.get_artifact(artifact_id)
            if metadata.plan_id and metadata.block_name:
                plan_service.record_artifact_version(metadata.plan_id, metadata.block_name, version, network_key)
            return ok(**workflow_data(artifact_id, network_key))
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="delete_xml_network", description="Delete only the CompileUnit selected by network_key and create a new artifact version.")
    def delete_xml_network(artifact_id: str, expected_version: int, network_key: str) -> str:
        try:
            ensure_network_editable(artifact_id, [network_key])
            version = service.delete_network(artifact_id, expected_version, network_key)
            metadata = service.get_artifact(artifact_id)
            if metadata.plan_id and metadata.block_name:
                plan_service.record_artifact_version(metadata.plan_id, metadata.block_name, version)
                plan_service.set_network_status(metadata.plan_id, network_key, "needs_revision")
            return ok(artifact_id=artifact_id, version=version, network_key=network_key)
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="update_xml_network_text", description="Atomically update the title and/or functional comment of one Network, create at most one immutable version, and update the linked Plan.")
    def update_xml_network_text(artifact_id: str, expected_version: int, network_key: str, title: str | None = None, comment: str | None = None) -> str:
        try:
            ensure_network_editable(artifact_id, [network_key])
            version = service.update_network_text(artifact_id, expected_version, network_key, title=title, comment=comment)
            if version == expected_version:
                return json.dumps({"success": True, "stage": "artifact", "code": "NO_CHANGES", "message": "Network text is unchanged; no version was created", **workflow_data(artifact_id, network_key)}, ensure_ascii=False)
            metadata = service.get_artifact(artifact_id)
            if metadata.plan_id and metadata.block_name: plan_service.record_artifact_version(metadata.plan_id, metadata.block_name, version, network_key)
            return ok(**workflow_data(artifact_id, network_key))
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="list_xml_artifacts", description="List artifact IDs, block names, current versions, statuses and update times.")
    def list_xml_artifacts(include_expired: bool = False, plan_id: str | None = None) -> str:
        return ok(artifacts=[{"artifact_id": x.artifact_id, "plan_id": x.plan_id, "block_name": x.block_name, "block_type": x.block_type, "current_version": x.current_version, "status": x.status, "updated_at": x.updated_at} for x in service.list_artifacts(include_expired, plan_id)])
