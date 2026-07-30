from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
import pytest
from scdw.xml_workspace import ArtifactError, PatchOperation, XmlArtifactService

XML = '''<Document><SW.Blocks.FC><AttributeList><Name>Main</Name><ProgrammingLanguage>LAD</ProgrammingLanguage></AttributeList></SW.Blocks.FC></Document>'''

@pytest.fixture
def service(tmp_path): return XmlArtifactService(tmp_path / "xml_artifacts", ttl_hours=48)

def test_create_read_and_patch_versions(service):
    artifact = service.create_artifact(XML, "Main")
    assert artifact.current_version == 1 and (service.store.root / artifact.artifact_id / "versions" / "v0001.xml").is_file()
    piece = service.read_fragment(artifact.artifact_id, start_line=1, end_line=1, max_chars=30)
    assert piece.truncated and len(piece.content) == 30
    result = service.apply_patch(artifact.artifact_id, 1, [PatchOperation("replace_exact", "<Name>Main</Name>", "<Name>Main2</Name>")])
    assert result.new_version == 2 and "Main</Name>" in service.store.version_path(artifact.artifact_id, 1).read_text()
    assert "Main2" in service.store.version_path(artifact.artifact_id, 2).read_text()

def test_search_and_all_patch_operations(service):
    artifact = service.create_artifact(XML)
    fragment = service.read_fragment(artifact.artifact_id, search="ProgrammingLanguage", context_lines=0)
    assert "ProgrammingLanguage" in fragment.content
    result = service.apply_patch(artifact.artifact_id, 1, [
        PatchOperation("insert_before", "<Name>Main</Name>", "<x/>"),
        PatchOperation("insert_after", "<x/>", "<y/>"),
        PatchOperation("delete_exact", "<y/>")])
    assert result.new_version == 2 and "<x/>" in service.store.version_path(artifact.artifact_id, 2).read_text()

def test_failed_preconditions_and_invalid_xml_do_not_create_version(service):
    artifact = service.create_artifact(XML)
    with pytest.raises(ArtifactError, match="found 0"): service.apply_patch(artifact.artifact_id, 1, [PatchOperation("delete_exact", "missing")])
    with pytest.raises(ArtifactError) as invalid: service.apply_patch(artifact.artifact_id, 1, [PatchOperation("replace_exact", "</Document>", "")])
    assert invalid.value.code == "XML_VALIDATION_FAILED"
    assert service.get_artifact(artifact.artifact_id).current_version == 1
    with pytest.raises(ArtifactError) as conflict: service.apply_patch(artifact.artifact_id, 2, [])
    assert conflict.value.code == "VERSION_CONFLICT"

def test_safety_expiry_and_diagnostics(service):
    artifact = service.create_artifact(XML)
    metadata = service.record_import_result(artifact.artifact_id, 1, False, "TIA_XML_IMPORT_FAILED", "failed", [{"id":"x"}])
    diagnostic = service.store.artifact_dir(artifact.artifact_id) / "diagnostics" / "import_v0001.json"
    assert metadata.status == "import_failed" and json.loads(diagnostic.read_text())["code"] == "TIA_XML_IMPORT_FAILED"
    with pytest.raises(ArtifactError): service.get_artifact("xml_../../outside")
    expired = service.store.metadata(artifact.artifact_id)
    service.store.write_metadata(type(expired)(**{**expired.to_dict(), "expires_at":(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}))
    assert service.list_artifacts() == []
    assert len(service.list_artifacts(include_expired=True)) == 1

def test_mcp_registration_includes_concise_artifact_tools():
    from mcp.server.fastmcp import FastMCP
    from scdw.mcp.tools import register_mcp_tools
    mcp = FastMCP("workspace-test"); register_mcp_tools(mcp)
    tools = {tool.name: tool.description for tool in mcp._tool_manager.list_tools()}
    required = {"create_xml_artifact", "get_xml_artifact_status", "read_xml_fragment", "patch_xml_artifact", "validate_xml_artifact", "import_lad_xml", "list_xml_artifacts"}
    assert required <= tools.keys()
    assert all(len(tools[name]) < 500 and "<Document>" not in tools[name] for name in required)

def test_import_tool_records_mocked_tia_results(tmp_path, monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from scdw.mcp.lad_runtime_tools import register_lad_runtime_tools
    class Session:
        def ensure_current_context(self): pass
        def require_project(self): pass
        def get_temp_dir(self): return str(tmp_path)
        def run_plc_operation(self, _name, _device, operation): return operation(None, object())
    workspace = XmlArtifactService(tmp_path / "workspace")
    artifact = workspace.create_artifact(XML, "Main")
    import scdw.openness.tia_blocks as blocks
    monkeypatch.setattr(blocks, "import_lad_xml_block", lambda *_args: "ignored")
    mcp = FastMCP("import-test"); register_lad_runtime_tools(mcp, Session(), workspace)
    call = mcp._tool_manager._tools["import_lad_xml"].fn
    assert json.loads(call(artifact.artifact_id, "PLC"))["success"] is True
    monkeypatch.setattr(blocks, "import_lad_xml_block", lambda *_args: (_ for _ in ()).throw(RuntimeError("mock failure")))
    failed = json.loads(call(artifact.artifact_id, "PLC"))
    assert failed["code"] == "TIA_XML_IMPORT_FAILED"
    assert workspace.get_artifact(artifact.artifact_id).last_import["code"] == "TIA_XML_IMPORT_FAILED"
