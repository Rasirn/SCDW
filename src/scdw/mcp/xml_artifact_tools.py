"""Concise MCP tools for versioned XML artifacts and Network-local edits."""
import json

from scdw.common.run_logging import get_run_logger
from scdw.lad_generation import LadPlanService
from scdw.xml_workspace import ArtifactError, PatchOperation, XmlArtifactService


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
                raise ArtifactError("PLAN_PRECONDITION_FAILED", str(exc)) from exc

    @mcp.tool(name="create_xml_artifact", description="Save parseable XML as immutable version 1. Returns artifact metadata. TIA Portal, not this tool, validates LAD semantics.")
    def create_xml_artifact(xml_content: str, block_name: str | None = None, device_name: str | None = None, conversation_id: str | None = None, plan_id: str | None = None, block_type: str | None = None, network_keys: list[str] | None = None) -> str:
        try:
            if plan_id and block_name:
                plan_service.validate_artifact_order(plan_id, block_name)
            value = service.create_artifact(xml_content, block_name, device_name, conversation_id, plan_id=plan_id, block_type=block_type, network_keys=network_keys)
            if plan_id and block_name:
                plan_service.link_artifact(plan_id, block_name, value.artifact_id, value.current_version)
            return ok(artifact=value.to_dict())
        except ArtifactError as exc: return fail(exc)
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
            return json.dumps({"success": False, "stage": "artifact_write", "code": type(exc).__name__, "message": str(exc), "retryable": False}, ensure_ascii=False)

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

    @mcp.tool(name="patch_xml_artifact", description="Apply deterministic text edits to one expected artifact version and create a new version. Supply affected Network keys when known.")
    def patch_xml_artifact(artifact_id: str, expected_version: int, operations: list[dict], change_source: str = "patch", affected_networks: list[str] | None = None) -> str:
        try:
            ensure_network_editable(artifact_id, affected_networks or [])
            result = service.apply_patch(artifact_id, expected_version, [PatchOperation.from_dict(x) for x in operations], change_source=change_source, affected_networks=affected_networks)
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

    @mcp.tool(name="append_xml_network", description="Append or insert one complete CompileUnit under a stable network_key and create a new artifact version.")
    def append_xml_network(artifact_id: str, expected_version: int, network_key: str, compile_unit_xml: str, before_key: str | None = None, position: int | None = None) -> str:
        try:
            metadata = service.get_artifact(artifact_id)
            if metadata.plan_id and metadata.block_name:
                try:
                    plan_service.set_cursor(metadata.plan_id, metadata.block_name, network_key)
                except (KeyError, ValueError, OSError) as exc:
                    raise ArtifactError("PLAN_PRECONDITION_FAILED", str(exc)) from exc
            ensure_network_editable(artifact_id, [network_key])
            version = service.append_network(artifact_id, expected_version, network_key, compile_unit_xml, before_key=before_key, position=position)
            metadata = service.get_artifact(artifact_id)
            if metadata.plan_id and metadata.block_name:
                plan_service.record_artifact_version(metadata.plan_id, metadata.block_name, version, network_key)
            return ok(artifact_id=artifact_id, version=version, network_key=network_key)
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="replace_xml_network", description="Replace only the CompileUnit selected by network_key and create a new artifact version.")
    def replace_xml_network(artifact_id: str, expected_version: int, network_key: str, compile_unit_xml: str) -> str:
        try:
            ensure_network_editable(artifact_id, [network_key])
            version = service.replace_network(artifact_id, expected_version, network_key, compile_unit_xml)
            metadata = service.get_artifact(artifact_id)
            if metadata.plan_id and metadata.block_name:
                plan_service.record_artifact_version(metadata.plan_id, metadata.block_name, version, network_key)
            return ok(artifact_id=artifact_id, version=version, network_key=network_key)
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

    @mcp.tool(name="update_xml_network_title", description="Change only one Network title selected by stable network_key and create a new immutable Artifact version.")
    def update_xml_network_title(artifact_id: str, expected_version: int, network_key: str, title: str) -> str:
        try:
            ensure_network_editable(artifact_id, [network_key])
            version = service.update_network_text(artifact_id, expected_version, network_key, title=title)
            metadata = service.get_artifact(artifact_id)
            if metadata.plan_id and metadata.block_name: plan_service.record_artifact_version(metadata.plan_id, metadata.block_name, version, network_key)
            return ok(artifact_id=artifact_id, version=version, network_key=network_key)
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="update_xml_network_comment", description="Change only one Network functional comment selected by stable network_key and create a new immutable Artifact version.")
    def update_xml_network_comment(artifact_id: str, expected_version: int, network_key: str, comment: str) -> str:
        try:
            ensure_network_editable(artifact_id, [network_key])
            version = service.update_network_text(artifact_id, expected_version, network_key, comment=comment)
            metadata = service.get_artifact(artifact_id)
            if metadata.plan_id and metadata.block_name: plan_service.record_artifact_version(metadata.plan_id, metadata.block_name, version, network_key)
            return ok(artifact_id=artifact_id, version=version, network_key=network_key)
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="set_xml_network_state", description="Set a Network workflow state such as generated, importing, compiling, verified or needs_revision.")
    def set_xml_network_state(artifact_id: str, network_key: str, state: str) -> str:
        try: return ok(artifact=service.set_network_state(artifact_id, network_key, state).to_dict())
        except ArtifactError as exc: return fail(exc)

    @mcp.tool(name="list_xml_artifacts", description="List artifact IDs, block names, current versions, statuses and update times.")
    def list_xml_artifacts(include_expired: bool = False, plan_id: str | None = None) -> str:
        return ok(artifacts=[{"artifact_id": x.artifact_id, "plan_id": x.plan_id, "block_name": x.block_name, "block_type": x.block_type, "current_version": x.current_version, "status": x.status, "updated_at": x.updated_at} for x in service.list_artifacts(include_expired, plan_id)])
