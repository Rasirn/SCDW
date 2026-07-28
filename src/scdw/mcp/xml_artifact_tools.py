"""Concise MCP registration for the XML Artifact workspace."""
import json
from scdw.common.run_logging import get_run_logger
from scdw.xml_workspace import ArtifactError, PatchOperation, XmlArtifactService

def register_xml_artifact_tools(mcp, session, service: XmlArtifactService | None = None) -> None:
    service = service or XmlArtifactService()
    def ok(**data): return json.dumps({"success":True, **data}, ensure_ascii=False, sort_keys=True)
    def fail(exc: ArtifactError): return json.dumps({"success":False,"code":exc.code,"message":str(exc),"retryable":exc.retryable}, ensure_ascii=False, sort_keys=True)
    @mcp.tool(name="create_xml_artifact", description="Save SimaticML XML as a versioned artifact. Parameters: xml_content and optional block_name, device_name, conversation_id. Returns ID, version, status and validation summary; creates v0001.")
    def create_xml_artifact(xml_content: str, block_name: str | None = None, device_name: str | None = None, conversation_id: str | None = None) -> str:
        try:
            value = service.create_artifact(xml_content, block_name, device_name, conversation_id); return ok(artifact_id=value.artifact_id, version=1, status=value.status, validation=value.last_validation)
        except ArtifactError as exc: return fail(exc)
        except Exception as exc: get_run_logger().log_exception("xml_artifact_create_failed", exc, component="mcp.xml_artifact"); return json.dumps({"success":False,"code":"INTERNAL_ERROR","message":"unable to create artifact","retryable":False}, ensure_ascii=False)
    @mcp.tool(name="get_xml_artifact_status", description="Return metadata and current version for a saved XML artifact. Parameters: artifact_id. No XML content is returned.")
    def get_xml_artifact_status(artifact_id: str) -> str:
        try: return ok(artifact=service.get_artifact(artifact_id).to_dict())
        except ArtifactError as exc: return fail(exc)
    @mcp.tool(name="read_xml_fragment", description="Read a bounded XML fragment. Parameters: artifact_id, optional version, search or line range, context_lines and max_chars. Returns line range, content and hash.")
    def read_xml_fragment(artifact_id: str, version: int | None = None, search: str | None = None, start_line: int | None = None, end_line: int | None = None, context_lines: int = 10, max_chars: int = 12000) -> str:
        try: return ok(fragment=service.read_fragment(artifact_id, version, search, start_line, end_line, context_lines, min(max_chars, 12000)).to_dict())
        except ArtifactError as exc: return fail(exc)
    @mcp.tool(name="patch_xml_artifact", description="Apply deterministic replace_exact, insert_before, insert_after or delete_exact operations to a saved XML artifact. Parameters: artifact_id, expected_version, operations. Creates an immutable new version on success.")
    def patch_xml_artifact(artifact_id: str, expected_version: int, operations: list[dict]) -> str:
        try: return ok(result=service.apply_patch(artifact_id, expected_version, [PatchOperation.from_dict(x) for x in operations]).to_dict())
        except ArtifactError as exc: return fail(exc)
        except (TypeError, ValueError) as exc: return json.dumps({"success":False,"code":"PATCH_PRECONDITION_FAILED","message":str(exc),"retryable":False}, ensure_ascii=False)
    @mcp.tool(name="validate_xml_artifact", description="Validate a saved XML artifact version without modifying it. Parameters: artifact_id and optional version. Returns structured validation results.")
    def validate_xml_artifact(artifact_id: str, version: int | None = None) -> str:
        try: return ok(validation=service.validate_artifact(artifact_id, version).to_dict())
        except ArtifactError as exc: return fail(exc)
    @mcp.tool(name="import_xml_artifact", description="Import a saved SimaticML XML artifact version into the target PLC device, record the result and return structured diagnostics. This tool does not generate or modify XML.")
    def import_xml_artifact(artifact_id: str, device_name: str, version: int | None = None) -> str:
        try:
            metadata = service.get_artifact(artifact_id); used = version or metadata.current_version
            xml = service.store.version_path(artifact_id, used).read_text(encoding="utf-8")
            try:
                session.ensure_current_context(); session.require_project(); temp_dir = session.get_temp_dir()
            except Exception as exc:
                service.record_import_result(artifact_id, used, False, "TIA_SESSION_UNAVAILABLE", "TIA session is unavailable", [{"message":str(exc)}]); return json.dumps({"success":False,"code":"TIA_SESSION_UNAVAILABLE","message":"TIA session is unavailable","artifact_id":artifact_id,"version":used,"retryable":True}, ensure_ascii=False)
            try:
                from scdw.openness.tia_blocks import import_lad_xml_block
                session.run_plc_operation("import_xml_artifact", device_name, lambda _project, plc: import_lad_xml_block(plc, temp_dir, metadata.block_name or "Main", xml))
            except Exception as exc:
                get_run_logger().log_exception("xml_artifact_import_failed", exc, component="mcp.xml_artifact", artifact_id=artifact_id, version=used)
                service.record_import_result(artifact_id, used, False, "TIA_XML_IMPORT_FAILED", "TIA XML import failed", [{"type":type(exc).__name__,"message":str(exc)}]); return json.dumps({"success":False,"code":"TIA_XML_IMPORT_FAILED","message":"TIA XML import failed","artifact_id":artifact_id,"version":used,"retryable":True}, ensure_ascii=False)
            service.record_import_result(artifact_id, used, True, "OK", "XML imported successfully"); return ok(artifact_id=artifact_id, version=used, status="imported")
        except ArtifactError as exc: return fail(exc)
    @mcp.tool(name="list_xml_artifacts", description="List XML artifact IDs, block names, versions, statuses and update times. Parameter: include_expired, default false. Does not read XML content.")
    def list_xml_artifacts(include_expired: bool = False) -> str:
        return ok(artifacts=[{"artifact_id":x.artifact_id,"block_name":x.block_name,"current_version":x.current_version,"status":x.status,"updated_at":x.updated_at} for x in service.list_artifacts(include_expired)])
