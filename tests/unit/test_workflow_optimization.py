from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

from scdw.frontend.chat_bridge import StreamingChat
from scdw.lad_generation import LadPlanService, LadPlanner, PlanValidationError
from scdw.llm.providers.deepseek import LlmStreamResult, LlmUsage
from scdw.mcp.lad_runtime_tools import register_lad_runtime_tools
from scdw.openness.tia_compiler import CompileResult
from scdw.openness.tia_tags import TagSpec, create_tag_table_with_tags
from scdw.xml_workspace import PatchOperation, XmlArtifactService


def _unit(identifier: str = "A", label: str = "one") -> str:
    return f'''<SW.Blocks.CompileUnit ID="{identifier}" CompositionName="CompileUnits">
<AttributeList><NetworkSource><FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4"><Parts><Access Scope="GlobalVariable" UId="7"><Symbol><Component Name="{label}" /></Symbol></Access></Parts><Wires /></FlgNet></NetworkSource><ProgrammingLanguage>LAD</ProgrammingLanguage></AttributeList>
<ObjectList><MultilingualText ID="{identifier}1" CompositionName="Title"><ObjectList><MultilingualTextItem ID="{identifier}2" CompositionName="Items"><AttributeList><Culture>zh-CN</Culture><Text>{label}</Text></AttributeList></MultilingualTextItem></ObjectList></MultilingualText></ObjectList>
</SW.Blocks.CompileUnit>'''


@pytest.mark.unit
def test_plan_validation_returns_all_independent_issues(tmp_path):
    value = LadPlanner().plan("先读取输入。然后计算输出", conversation_id="one", target_device="PLC").to_dict()
    planning = {key: item for key, item in value.items() if key not in {
        "plan_id", "conversation_id", "target_device", "requirements", "created_at", "updated_at",
    }}
    for network in planning["networks"]:
        network["required_capabilities"].append("missing.capability")
    with pytest.raises(PlanValidationError) as error:
        LadPlanService(tmp_path / "plans").create_from_planning(
            planning, requirements="same", conversation_id="one", target_device="PLC"
        )
    assert len(error.value.issues) == 2
    assert {item["network_key"] for item in error.value.issues} == {item["network_key"] for item in planning["networks"]}


@pytest.mark.unit
def test_no_change_patch_and_replace_do_not_create_versions(tmp_path):
    artifacts = XmlArtifactService(tmp_path / "artifacts")
    artifact = artifacts.create_artifact("<Document><Name>A</Name></Document>")
    patched = artifacts.apply_patch(artifact.artifact_id, 1, [PatchOperation("replace_exact", "A", "A", 1)])
    assert patched.old_version == patched.new_version == 1
    assert artifacts.get_artifact(artifact.artifact_id).current_version == 1

    block = artifacts.create_block_artifact("FC_Main")
    version = artifacts.append_network(block.artifact_id, 1, "one", _unit("A", "same"))
    original = artifacts.get_network(block.artifact_id, "one", version)
    same = artifacts.replace_network(block.artifact_id, version, "one", original)
    assert same == version


@pytest.mark.unit
def test_alpha_and_hex_document_ids_are_reallocated_without_touching_uid(tmp_path):
    artifacts = XmlArtifactService(tmp_path / "artifacts")
    block = artifacts.create_block_artifact("FC_Main")
    version = artifacts.append_network(block.artifact_id, 1, "one", _unit("A", "one"))
    version = artifacts.append_network(block.artifact_id, version, "two", _unit("0F", "two"))
    content = artifacts.store.version_path(block.artifact_id, version).read_text(encoding="utf-8")
    import re
    identifiers = re.findall(r'(?<!U)\bID="([^"]+)"', content)
    assert len(identifiers) == len(set(identifiers))
    assert content.count('UId="7"') == 2


class _Session:
    def __init__(self):
        self.context = SimpleNamespace(project_name="Project")
        self.calls = []

    def ensure_current_context(self): return {}
    def require_project(self): return object()
    def get_temp_dir(self): return "."
    def run_plc_operation(self, name, _device, operation):
        self.calls.append(name)
        return operation(object(), object())
    def run_project_operation(self, name, operation):
        self.calls.append(name)
        return operation(object())


@pytest.mark.unit
def test_import_and_compile_is_atomic_and_idempotent(tmp_path, monkeypatch):
    plans = LadPlanService(tmp_path / "plans")
    artifacts = XmlArtifactService(tmp_path / "artifacts")
    plan = plans.create_from_requirements("条件有效时输出", conversation_id="one", target_device="PLC_1")
    artifact = artifacts.create_block_artifact(plan.main_fc.block_name, "FC", plan_id=plan.plan_id)
    plans.link_artifact(plan.plan_id, plan.main_fc.block_name, artifact.artifact_id, 1)
    network = plan.networks[0]
    version = artifacts.append_network(artifact.artifact_id, 1, network.network_key, _unit("A", "network"))
    plans.record_artifact_version(plan.plan_id, plan.main_fc.block_name, version, network.network_key)
    session = _Session()
    mcp = FastMCP("runtime")
    register_lad_runtime_tools(mcp, session, artifacts, plans)
    call = mcp._tool_manager._tools["import_and_compile_artifact"].fn

    import scdw.openness.tia_blocks as blocks
    import scdw.openness.tia_compiler as compiler
    monkeypatch.setattr(blocks, "import_lad_xml_block", lambda *_: None)
    monkeypatch.setattr(compiler, "compile_block", lambda _plc, name: CompileResult(True, "Success", scope="block", target_name=name))
    monkeypatch.setattr(compiler, "compile_plc", lambda _plc: CompileResult(True, "Success", scope="plc"))

    first = json.loads(call(artifact.artifact_id, "PLC_1", version, network.network_key, True))
    assert first["success"] and first["next"]["action"] == "save_project"
    first_calls = list(session.calls)
    second = json.loads(call(artifact.artifact_id, "PLC_1", version, network.network_key, True))
    assert second["success"] and session.calls == first_calls


class _Doc:
    async def list_prompts(self): return []
    async def read_resource(self, _): return []


class _LoopProvider:
    def __init__(self): self.index = 0
    async def stream_chat(self, *_, **__):
        call = {"id": f"call-{self.index}", "function": {"name": "noop", "arguments": "{}"}}
        self.index += 1
        yield {"type": "stream_end", "result": LlmStreamResult("", "", [call], "tool_calls", "test", LlmUsage())}


@pytest.mark.unit
def test_soft_and_hard_tool_budgets_pause_with_recovery(monkeypatch):
    executed = []
    async def schemas(_): return []
    async def execute(_clients, request):
        executed.append(request.id)
        return {"role": "tool", "tool_call_id": request.id, "content": '{"success":true}', "success": True}
    monkeypatch.setattr("scdw.frontend.chat_bridge.get_tool_budget", lambda: (1, 2))
    monkeypatch.setattr("scdw.frontend.chat_bridge.ToolManager.get_all_tools", schemas)
    monkeypatch.setattr("scdw.frontend.chat_bridge.ToolManager.execute_tool_request", execute)
    chat = StreamingChat(doc_client=_Doc(), clients={}, deepseek_service=_LoopProvider())
    events = asyncio.run(_collect(chat))
    assert len(executed) == 2
    assert any(event["type"] == "tool_budget_warning" for event in events)
    assert events[-1]["type"] == "turn_end" and events[-1]["paused"] is True
    assert events[-1]["recovery"]["hard_limit"] == 2


async def _collect(chat):
    return [event async for event in chat.run_stream("loop", "fast")]


@pytest.mark.unit
def test_public_surface_removes_low_level_legacy_tools_and_uses_envelope():
    from scdw.mcp.tools import register_mcp_tools
    mcp = FastMCP("surface")
    register_mcp_tools(mcp)
    names = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert {
        "search_plc_templates", "list_plc_templates", "get_plc_template", "import_template_block",
        "import_lad_xml_from_file", "save_lad_xml", "set_lad_generation_cursor",
        "set_lad_network_plan_status", "link_lad_plan_artifact", "record_lad_artifact_version",
        "set_xml_network_state", "append_xml_network", "replace_xml_network",
        "import_lad_xml", "compile_check",
    }.isdisjoint(names)
    assert {"append_network_and_prepare_import", "replace_network_and_prepare_import", "import_and_compile_artifact", "reconcile_lad_workflow"} <= names
    value = json.loads(mcp._tool_manager._tools["get_tia_context"].fn())
    assert set(value) >= {"success", "stage", "code", "message", "data", "retryable", "needs_user_action"}


@pytest.mark.unit
def test_tag_replay_skips_device_global_names_without_creating_empty_table():
    existing_tag = SimpleNamespace(Name="风机运行")
    existing_table = SimpleNamespace(Name="Existing", Tags=[existing_tag])

    class Tables(list):
        def Create(self, name):
            raise AssertionError(f"unexpected empty table creation: {name}")

    software = SimpleNamespace(TagTableGroup=SimpleNamespace(TagTables=Tables([existing_table])))
    result = create_tag_table_with_tags(
        software,
        "Replay",
        [TagSpec("风机运行", "Bool", "%Q4.4")],
    )
    assert result == {"created": 0, "skipped": 1, "table_name": "Replay", "idempotent": True}
