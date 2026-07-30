"""Reproducible TIA V17 test for the Step 3 Artifact verification loop."""
from __future__ import annotations

import json
import os

import pytest
from mcp.server.fastmcp import FastMCP

from scdw.lad_generation import LadPlanService
from scdw.mcp.lad_runtime_tools import register_lad_runtime_tools
from scdw.mcp.xml_artifact_tools import register_xml_artifact_tools
from scdw.xml_workspace import XmlArtifactService


pytestmark = [pytest.mark.integration, pytest.mark.tia, pytest.mark.requires_tia, pytest.mark.slow]


def _unit(unit_id: int, title: str, comment: str, flgnet: str | None = None) -> str:
    network = flgnet or '<FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4"><Parts /><Wires /></FlgNet>'
    return f'''<SW.Blocks.CompileUnit ID="{unit_id}" CompositionName="CompileUnits">
  <AttributeList><NetworkSource>{network}</NetworkSource><ProgrammingLanguage>LAD</ProgrammingLanguage></AttributeList>
  <ObjectList>
    <MultilingualText ID="{unit_id + 1}" CompositionName="Comment"><ObjectList><MultilingualTextItem ID="{unit_id + 2}" CompositionName="Items"><AttributeList><Culture>zh-CN</Culture><Text>{comment}</Text></AttributeList></MultilingualTextItem></ObjectList></MultilingualText>
    <MultilingualText ID="{unit_id + 3}" CompositionName="Title"><ObjectList><MultilingualTextItem ID="{unit_id + 4}" CompositionName="Items"><AttributeList><Culture>zh-CN</Culture><Text>{title}</Text></AttributeList></MultilingualTextItem></ObjectList></MultilingualText>
  </ObjectList>
</SW.Blocks.CompileUnit>'''


def _call_flgnet(fb_name: str, db_name: str) -> str:
    return f'''<FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4">
  <Parts><Call UId="1"><CallInfo Name="{fb_name}" BlockType="FB"><Instance Scope="GlobalVariable" UId="2"><Component Name="{db_name}" /></Instance></CallInfo></Call></Parts>
  <Wires><Wire UId="3"><Powerrail /><NameCon UId="1" Name="en" /></Wire></Wires>
</FlgNet>'''


def _coil_flgnet(variable_name: str) -> str:
    return f'''<FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4">
  <Parts><Access Scope="LocalVariable" UId="1"><Symbol><Component Name="{variable_name}" /></Symbol></Access><Part Name="Coil" UId="10" /></Parts>
  <Wires><Wire UId="20"><Powerrail /><NameCon UId="10" Name="in" /></Wire><Wire UId="21"><IdentCon UId="1" /><NameCon UId="10" Name="operand" /></Wire></Wires>
</FlgNet>'''


def _invoke(mcp: FastMCP, name: str, *args):
    value = json.loads(mcp._tool_manager._tools[name].fn(*args))
    assert value.get("success") is True, value
    return value


def test_tia_artifact_fb_instance_db_and_incremental_main_fc(temporary_tia_project, tmp_path):
    session, _, _ = temporary_tia_project
    device = "SCDW_TEST_PLC"
    order_number = os.getenv("SCDW_TEST_CPU", "OrderNumber:6ES7 214-1BG40-0XB0/V4.4")
    session.add_plc(order_number, device, device)
    fb_name = "SCDW_STEP3_FB"
    db_name = "SCDW_STEP3_IDB"
    fc_name = "SCDW_STEP3_FC"

    plans = LadPlanService(tmp_path / "plans")
    artifacts = XmlArtifactService(tmp_path / "artifacts")
    planning = {
        "main_fc": {"block_name": fc_name, "block_type": "FC", "responsibility": "调用状态FB并执行无状态逻辑"},
        "main_fc_reason": "主程序无Static状态，因此使用FC。",
        "auxiliary_fbs": [{"block_name": fb_name, "block_type": "FB", "responsibility": "保存跨扫描状态", "state_features": ["memory"]}],
        "instance_dbs": [{"db_name": db_name, "fb_name": fb_name, "instance_name": "State", "responsibility": "保存FB状态"}],
        "block_dependency_order": [fb_name, db_name, fc_name],
        "interface_plan": {fb_name: {"Static": ["State:Bool"]}, fc_name: {}},
        "networks": [
            {"network_key": "fb_state", "block_name": fb_name, "title": "状态保持", "comment": "辅助FB状态程序段。", "purpose": "状态", "knowledge_ids": ["network_text.title_comment.v17"]},
            {"network_key": "call_fb", "block_name": fc_name, "title": "调用辅助FB", "comment": "使用真实背景DB调用辅助FB。", "purpose": "FB调用", "knowledge_ids": ["call.fb_global_instance.v17"], "depends_on": ["fb_state"]},
            {"network_key": "main_logic", "block_name": fc_name, "title": "主FC组合逻辑", "comment": "第二个逐步追加的主FC程序段。", "purpose": "组合逻辑", "knowledge_ids": ["network_text.title_comment.v17"], "depends_on": ["call_fb"]},
        ],
    }
    plan = plans.create_from_planning(planning, requirements="真实Step 3最小闭环", conversation_id="tia-integration", target_device=device)
    mcp = FastMCP("tia-step3")
    register_xml_artifact_tools(mcp, session, artifacts, plans)
    register_lad_runtime_tools(mcp, session, artifacts, plans)

    fb_interface = '<Interface><Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/v5"><Section Name="Input" /><Section Name="Output" /><Section Name="InOut" /><Section Name="Static"><Member Name="State" Datatype="Bool" Remanence="SetInIDB" Accessibility="Public" /></Section><Section Name="Temp" /><Section Name="Constant" /></Sections></Interface>'
    fb = _invoke(mcp, "create_lad_block_artifact", plan.plan_id, fb_name, "FB", fb_interface, device, "tia-integration")["artifact"]
    fb_v2 = _invoke(mcp, "append_xml_network", fb["artifact_id"], 1, "fb_state", _unit(1, "状态保持", "辅助FB状态程序段。", _coil_flgnet("State")))["version"]
    _invoke(mcp, "import_lad_xml", fb["artifact_id"], device, fb_v2)
    _invoke(mcp, "compile_check", device, fb_name, fb["artifact_id"], fb_v2, "fb_state", plan.plan_id)
    _invoke(mcp, "compile_check", device, fb_name, fb["artifact_id"], fb_v2, None, plan.plan_id)

    _invoke(mcp, "create_instance_db", device, fb_name, db_name, None, plan.plan_id)

    main_interface = '<Interface><Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/v5"><Section Name="Input" /><Section Name="Output" /><Section Name="InOut" /><Section Name="Temp"><Member Name="Dummy" Datatype="Bool" /></Section><Section Name="Constant" /><Section Name="Return"><Member Name="Ret_Val" Datatype="Void" Accessibility="Public" /></Section></Sections></Interface>'
    main = _invoke(mcp, "create_lad_block_artifact", plan.plan_id, fc_name, "FC", main_interface, device, "tia-integration")["artifact"]
    main_v2 = _invoke(mcp, "append_xml_network", main["artifact_id"], 1, "call_fb", _unit(1, "调用辅助FB", "使用真实背景DB调用辅助FB。", _call_flgnet(fb_name, db_name)))["version"]
    _invoke(mcp, "import_lad_xml", main["artifact_id"], device, main_v2)
    _invoke(mcp, "compile_check", device, fc_name, main["artifact_id"], main_v2, "call_fb", plan.plan_id)

    main_v3 = _invoke(mcp, "append_xml_network", main["artifact_id"], main_v2, "main_logic", _unit(10, "主FC组合逻辑", "第二个逐步追加的主FC程序段。", _coil_flgnet("Dummy")))["version"]
    _invoke(mcp, "import_lad_xml", main["artifact_id"], device, main_v3)
    _invoke(mcp, "compile_check", device, fc_name, main["artifact_id"], main_v3, "main_logic", plan.plan_id)
    _invoke(mcp, "compile_check", device, fc_name, main["artifact_id"], main_v3, None, plan.plan_id)
    _invoke(mcp, "compile_check", device, None, None, None, None, plan.plan_id)
    _invoke(mcp, "save_verified_project", device, plan.plan_id)

    networks = artifacts.list_networks(main["artifact_id"], main_v3)
    assert [item["network_key"] for item in networks] == ["call_fb", "main_logic"]
    assert all(item["status"] == "verified" for item in networks)
