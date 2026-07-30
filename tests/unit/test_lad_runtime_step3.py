from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

from scdw.lad_generation import LadPlanService
from scdw.mcp.lad_runtime_tools import register_lad_runtime_tools
from scdw.openness.tia_blocks import create_instance_db as openness_create_instance_db
from scdw.openness.tia_compiler import CompileResult, parse_compiler_result
from scdw.xml_workspace import XmlArtifactService


def compile_unit(unit_id: int, title: str) -> str:
    return f'''<SW.Blocks.CompileUnit ID="{unit_id}" CompositionName="CompileUnits">
<AttributeList><NetworkSource><FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4"><Parts /><Wires /></FlgNet></NetworkSource><ProgrammingLanguage>LAD</ProgrammingLanguage></AttributeList>
<ObjectList><MultilingualText ID="{unit_id + 100}" CompositionName="Comment"><ObjectList><MultilingualTextItem ID="{unit_id + 101}" CompositionName="Items"><AttributeList><Culture>zh-CN</Culture><Text>{title} comment</Text></AttributeList></MultilingualTextItem></ObjectList></MultilingualText><MultilingualText ID="{unit_id + 102}" CompositionName="Title"><ObjectList><MultilingualTextItem ID="{unit_id + 103}" CompositionName="Items"><AttributeList><Culture>zh-CN</Culture><Text>{title}</Text></AttributeList></MultilingualTextItem></ObjectList></MultilingualText></ObjectList>
</SW.Blocks.CompileUnit>'''


class Session:
    def __init__(self):
        self.context = SimpleNamespace(project_name="Project")
        self.plc = object()
        self.calls: list[str] = []

    def ensure_current_context(self):
        return {}

    def require_project(self):
        return object()

    def get_temp_dir(self):
        return "."

    def run_plc_operation(self, name, _device, operation):
        self.calls.append(name)
        return operation(object(), self.plc)

    def run_project_operation(self, name, operation):
        self.calls.append(name)
        return operation(object())


def registered(tmp_path):
    artifacts = XmlArtifactService(tmp_path / "artifacts")
    plans = LadPlanService(tmp_path / "plans")
    session = Session()
    mcp = FastMCP("runtime-test")
    register_lad_runtime_tools(mcp, session, artifacts, plans)
    calls = {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}
    return artifacts, plans, session, mcp, calls


def simple_plan_artifact(tmp_path, requirement="条件A或条件B任一有效时输出"):
    artifacts, plans, session, mcp, calls = registered(tmp_path)
    plan = plans.create_from_requirements(requirement, conversation_id="one-dialog", target_device="PLC_1")
    block = plan.main_fc
    artifact = artifacts.create_block_artifact(block.block_name, block.block_type, plan_id=plan.plan_id)
    plans.link_artifact(plan.plan_id, block.block_name, artifact.artifact_id, 1)
    network = next(item for item in plan.networks if item.block_name == block.block_name)
    version = artifacts.append_network(artifact.artifact_id, 1, network.network_key, compile_unit(1, network.title))
    plans.record_artifact_version(plan.plan_id, block.block_name, version, network.network_key)
    plans.set_cursor(plan.plan_id, block.block_name, network.network_key)
    return artifacts, plans, session, mcp, calls, plans.get(plan.plan_id), artifact, network, version


@pytest.mark.unit
def test_import_lad_xml_public_signature_accepts_artifact_only(tmp_path):
    _, _, _, mcp, _ = registered(tmp_path)
    tool = mcp._tool_manager._tools["import_lad_xml"]
    assert set(tool.parameters["properties"]) == {"artifact_id", "device_name", "version"}
    assert "xml_content" not in tool.parameters["properties"]
    assert all(
        not isinstance(parameter.annotation, str)
        for parameter in inspect.signature(tool.fn).parameters.values()
    )


@pytest.mark.unit
def test_runtime_tool_descriptions_have_no_historical_tutorials(tmp_path):
    _, _, _, mcp, _ = registered(tmp_path)
    descriptions = "\n".join(tool.description or "" for tool in mcp._tool_manager.list_tools())
    forbidden = ("IdentCon", "TemplateValue", "Contact", "Coil", "Real→Int", "系统会自动修复")
    assert not any(token in descriptions for token in forbidden)
    assert all(len(tool.description or "") < 300 for tool in mcp._tool_manager.list_tools())


@pytest.mark.unit
def test_import_success_and_compile_success_are_separate(tmp_path, monkeypatch):
    artifacts, _, _, _, calls, _, artifact, _, version = simple_plan_artifact(tmp_path)
    import scdw.openness.tia_blocks as blocks

    monkeypatch.setattr(blocks, "import_lad_xml_block", lambda *_: "submitted.xml")
    imported = json.loads(calls["import_lad_xml"](artifact.artifact_id, "PLC_1", version))
    assert imported["success"] is True
    metadata = artifacts.get_artifact(artifact.artifact_id)
    assert metadata.last_import["success"] is True
    assert metadata.last_compile is None


@pytest.mark.unit
def test_import_failure_diagnostic_is_written_to_requested_version(tmp_path, monkeypatch):
    artifacts, _, _, _, calls, _, artifact, _, version = simple_plan_artifact(tmp_path)
    import scdw.openness.tia_blocks as blocks

    monkeypatch.setattr(blocks, "import_lad_xml_block", lambda *_: (_ for _ in ()).throw(RuntimeError("TIA native import error")))
    result = json.loads(calls["import_lad_xml"](artifact.artifact_id, "PLC_1", version))
    path = artifacts.store.artifact_dir(artifact.artifact_id) / "diagnostics" / f"import_v{version:04d}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert result["code"] == "TIA_XML_IMPORT_FAILED"
    assert saved["version"] == version
    assert saved["messages"][0]["description"] == "TIA native import error"


class Message:
    def __init__(self, severity, description, path="", children=()):
        self.Category = severity
        self.Description = description
        self.Path = path
        self.Messages = list(children)


@pytest.mark.unit
def test_block_compiler_result_is_recursively_parsed():
    native = SimpleNamespace(
        State="Error",
        Messages=[Message("Info", "FB", "Program blocks", [Message("Error", "bad operand", "Network 1"), Message("Warning", "unused")])],
    )
    result = parse_compiler_result(native, scope="block", target_name="FB_StateControl")
    assert result.error_count == 1 and result.warning_count == 1
    assert result.message_tree[0]["messages"][0]["path"] == "Network 1"
    assert result.message_tree[0]["messages"][0]["description"] == "bad operand"


@pytest.mark.unit
def test_compile_result_is_written_to_artifact_and_marks_network_verified(tmp_path, monkeypatch):
    artifacts, plans, _, _, calls, plan, artifact, network, version = simple_plan_artifact(tmp_path)
    import scdw.openness.tia_blocks as blocks
    import scdw.openness.tia_compiler as compiler

    monkeypatch.setattr(blocks, "import_lad_xml_block", lambda *_: None)
    monkeypatch.setattr(compiler, "compile_block", lambda _plc, name: CompileResult(True, "Success", scope="block", target_name=name))
    assert json.loads(calls["import_lad_xml"](artifact.artifact_id, "PLC_1", version))["success"]
    compiled = json.loads(calls["compile_check"]("PLC_1", plan.main_fc.block_name, artifact.artifact_id, version, network.network_key, plan.plan_id))
    metadata = artifacts.get_artifact(artifact.artifact_id)
    restored = plans.get(plan.plan_id)
    assert compiled["success"] is True
    assert metadata.last_compile["version"] == version
    assert metadata.verified_versions[network.network_key] == version
    assert next(item for item in restored.networks if item.network_key == network.network_key).status == "verified"


@pytest.mark.unit
def test_auxiliary_fb_must_be_verified_before_instance_db(tmp_path):
    plans = LadPlanService(tmp_path / "plans")
    plan = plans.create_from_requirements("TON延时并记忆状态", conversation_id="one", target_device="PLC_1")
    db = plan.instance_dbs[0]
    with pytest.raises(ValueError, match="final block compilation"):
        plans.validate_instance_db_order(plan.plan_id, db.fb_name, db.db_name)
    plans.set_runtime_status(plan.plan_id, "verified", block_name=db.fb_name)
    plans.validate_instance_db_order(plan.plan_id, db.fb_name, db.db_name)


@pytest.mark.unit
def test_instance_db_must_exist_before_main_fc_generation(tmp_path):
    plans = LadPlanService(tmp_path / "plans")
    plan = plans.create_from_requirements("TON延时并记忆状态", conversation_id="one", target_device="PLC_1")
    call = next(item for item in plan.networks if item.block_name == plan.main_fc.block_name)
    plans.set_network_status(plan.plan_id, "state_control", "verified")
    with pytest.raises(ValueError, match="instance DBs"):
        plans.validate_network_generation_order(plan.plan_id, plan.main_fc.block_name, call.network_key)
    plans.set_runtime_status(plan.plan_id, "imported", instance_db_name=plan.instance_dbs[0].db_name)
    plans.validate_network_generation_order(plan.plan_id, plan.main_fc.block_name, call.network_key)


@pytest.mark.unit
def test_stateless_main_fc_has_no_instance_db(tmp_path):
    plan = LadPlanService(tmp_path / "plans").create_from_requirements("入口允许时输出", conversation_id="one", target_device="PLC_1")
    assert plan.auxiliary_fbs == [] and plan.instance_dbs == []


@pytest.mark.unit
def test_failed_current_network_does_not_change_verified_network(tmp_path):
    plans = LadPlanService(tmp_path / "plans")
    plan = plans.create_from_requirements("先读取输入。再计算输出", conversation_id="one", target_device="PLC_1")
    first, second = plan.networks
    plans.set_network_status(plan.plan_id, first.network_key, "verified")
    plans.set_network_status(plan.plan_id, second.network_key, "compile_failed")
    restored = plans.get(plan.plan_id)
    assert restored.networks[0].status == "verified"
    assert restored.networks[1].status == "compile_failed"


@pytest.mark.unit
def test_reimport_uses_new_artifact_version(tmp_path, monkeypatch):
    artifacts, plans, _, _, calls, plan, artifact, network, version = simple_plan_artifact(tmp_path)
    import scdw.openness.tia_blocks as blocks

    monkeypatch.setattr(blocks, "import_lad_xml_block", lambda *_: None)
    newer = artifacts.replace_network(artifact.artifact_id, version, network.network_key, compile_unit(2, "revised"))
    plans.record_artifact_version(plan.plan_id, plan.main_fc.block_name, newer, network.network_key)
    result = json.loads(calls["import_lad_xml"](artifact.artifact_id, "PLC_1"))
    assert result["version"] == newer
    assert plans.get(plan.plan_id).networks[0].artifact_version == newer


@pytest.mark.unit
def test_final_block_then_plc_compile_and_verified_save(tmp_path, monkeypatch):
    _, plans, session, _, calls, plan, artifact, network, version = simple_plan_artifact(tmp_path)
    import scdw.openness.tia_blocks as blocks
    import scdw.openness.tia_compiler as compiler
    import scdw.openness.tia_core as core

    monkeypatch.setattr(blocks, "import_lad_xml_block", lambda *_: None)
    monkeypatch.setattr(compiler, "compile_block", lambda _plc, name: CompileResult(True, "Success", scope="block", target_name=name))
    monkeypatch.setattr(compiler, "compile_plc", lambda _plc: CompileResult(True, "Success", scope="plc"))
    monkeypatch.setattr(core, "save_project", lambda _project: None)
    calls["import_lad_xml"](artifact.artifact_id, "PLC_1", version)
    calls["compile_check"]("PLC_1", plan.main_fc.block_name, artifact.artifact_id, version, network.network_key, plan.plan_id)
    assert json.loads(calls["compile_check"]("PLC_1", plan.main_fc.block_name, artifact.artifact_id, version, None, plan.plan_id))["success"]
    assert json.loads(calls["compile_check"]("PLC_1", None, None, None, None, plan.plan_id))["success"]
    assert json.loads(calls["save_verified_project"]("PLC_1", plan.plan_id))["success"]
    assert session.calls.count("compile_check") == 3
    assert session.calls[-1] == "save_verified_project"


@pytest.mark.unit
def test_public_tools_have_only_one_lad_import_entry():
    from scdw.mcp.tools import register_mcp_tools

    mcp = FastMCP("all-tools")
    register_mcp_tools(mcp)
    names = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert "import_lad_xml" in names
    assert "import_xml_artifact" not in names
    assert "import_lad_xml_from_file" not in names
    assert "save_lad_xml" not in names


@pytest.mark.unit
def test_low_level_instance_db_is_bound_and_not_global_db():
    class FakeFunctionBlock:
        Name = "FB_StateControl"

    class FakeInstanceDB:
        Name = "DB_StateControl"
        Number = 20

        def __init__(self, fb):
            self.InstanceOf = fb

    class Blocks(list):
        def CreateInstanceDB(self, name, auto, number, fb_name):
            assert fb_name == "FB_StateControl"
            assert name == "DB_StateControl" and auto is False and number == 20
            value = FakeInstanceDB(fb)
            self.append(value)
            return value

    fb = FakeFunctionBlock()
    blocks = Blocks([fb])
    plc = SimpleNamespace(BlockGroup=SimpleNamespace(Blocks=blocks, Groups=[]))
    result = openness_create_instance_db(plc, "FB_StateControl", "DB_StateControl", 20)
    assert result["success"] and result["created"]
    assert result["bound_to"] == "FB_StateControl"


@pytest.mark.unit
def test_instance_db_auto_numbering_uses_valid_v17_seed():
    class FakeFunctionBlock:
        Name = "FB_Auto"

    class Blocks(list):
        def CreateInstanceDB(self, name, auto, number, fb_name):
            assert (name, auto, number, fb_name) == ("DB_Auto", True, 1, "FB_Auto")
            return SimpleNamespace(Name=name, Number=12, InstanceOf=FakeFunctionBlock())

    blocks = Blocks([FakeFunctionBlock()])
    plc = SimpleNamespace(BlockGroup=SimpleNamespace(Blocks=blocks, Groups=[]))
    result = openness_create_instance_db(plc, "FB_Auto", "DB_Auto")
    assert result["success"] and result["db_number"] == 12


@pytest.mark.unit
def test_single_conversation_runtime_has_no_agent_dispatch():
    import scdw.lad_generation.service as plan_service
    import scdw.mcp.lad_runtime_tools as runtime

    source = inspect.getsource(runtime).lower() + inspect.getsource(plan_service).lower()
    forbidden = ("spawn_agent", "subagent", "agent_pool", "multi_agent", "parallel_agent")
    assert not any(token in source for token in forbidden)
