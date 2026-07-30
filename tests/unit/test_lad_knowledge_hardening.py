from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import pytest

from scdw.lad_generation import KnowledgeGapError, LadPlanService, LadPlanner
from scdw.openness.tia_blocks import AbsoluteDbAddressUnsupportedError, DBVariable, build_global_db_scl
from scdw.xml_workspace import PatchOperation, XmlArtifactService


def compile_unit(unit_id: int, label: str, uid: int = 1) -> str:
    return f'''<SW.Blocks.CompileUnit ID="{unit_id}" CompositionName="CompileUnits">
  <AttributeList><NetworkSource><FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4"><Parts><Access Scope="GlobalVariable" UId="{uid}"><Symbol><Component Name="{label}" /></Symbol></Access></Parts><Wires /></FlgNet></NetworkSource><ProgrammingLanguage>LAD</ProgrammingLanguage></AttributeList>
  <ObjectList><MultilingualText ID="{unit_id}" CompositionName="Comment"><ObjectList><MultilingualTextItem ID="{unit_id}" CompositionName="Items"><AttributeList><Culture>zh-CN</Culture><Text>{label} comment</Text></AttributeList></MultilingualTextItem></ObjectList></MultilingualText><MultilingualText ID="{unit_id}" CompositionName="Title"><ObjectList><MultilingualTextItem ID="{unit_id}" CompositionName="Items"><AttributeList><Culture>zh-CN</Culture><Text>{label} title</Text></AttributeList></MultilingualTextItem></ObjectList></MultilingualText></ObjectList>
</SW.Blocks.CompileUnit>'''


@pytest.mark.unit
def test_application_report_finds_pbox_and_all_raw_parts_are_catalog_covered():
    report = json.loads(open("data/rag/knowledge/application_coverage.json", encoding="utf-8").read())
    pbox = next(item for item in report["aggregate"]["part_types"] if item["name"] == "PBox")
    assert pbox["ports"] == ["bit", "in", "out"]
    assert "报警.xml#network-1" in pbox["sources"]
    assert report["catalog_comparison"]["uncovered_part_types"] == []
    assert report["catalog_comparison"]["coverage_ratio"] == 1.0

    catalog = json.loads(open("data/rag/knowledge/catalog.json", encoding="utf-8").read())
    item = next(item for item in catalog["items"] if item["id"] == "instruction.pbox_edge_memory.v17")
    assert item["status"] == "golden"
    assert {"pbox", "edge.memory_bit", "edge.ports.in_bit_out"} <= set(item["provides"])


@pytest.mark.unit
def test_missing_knowledge_and_parallel_series_mismatch_block_plan_save(tmp_path):
    planner = LadPlanner()
    service = LadPlanService(tmp_path / "plans")
    missing = planner.plan("条件有效时输出", conversation_id="missing", target_device="PLC")
    missing.networks[0].selected_knowledge_ids = ["instruction.does_not_exist.v17"]
    with pytest.raises(KnowledgeGapError) as error:
        service.save(missing)
    assert error.value.code == "KNOWLEDGE_GAP"

    parallel = planner.plan("条件A或条件B时输出", conversation_id="topology", target_device="PLC")
    parallel.networks[0].selected_knowledge_ids = [
        "shell.fc_block.v17", "topology.series_contact_coil.v17", "network_text.title_comment.v17"
    ]
    with pytest.raises(KnowledgeGapError) as error:
        service.save(parallel)
    assert "topology.parallel" in error.value.uncovered


@pytest.mark.unit
def test_one_active_plan_per_conversation(tmp_path):
    service = LadPlanService(tmp_path / "plans")
    first = service.create_from_requirements("条件A时输出", conversation_id="same-task", target_device="PLC")
    second = service.create_from_requirements("条件B时输出", conversation_id="same-task", target_device="PLC")
    assert service.get(first.plan_id).status == "replaced"
    assert service.get(first.plan_id).replaced_by == second.plan_id
    assert [item.plan_id for item in service.list("same-task") if item.status == "active"] == [second.plan_id]


@pytest.mark.unit
def test_internal_patch_aliases_normalize_but_public_schema_is_strict(tmp_path):
    canonical = PatchOperation.from_dict({"op": "replace_exact", "old": "A", "new": "B", "expected_occurrences": 1})
    op_alias = PatchOperation.from_dict({"op": "replace", "old": "A", "new": "B"})
    field_alias = PatchOperation.from_dict({"search": "A", "replace": "B"})
    assert canonical == op_alias == field_alias == PatchOperation("replace_exact", "A", "B", 1)
    artifacts = XmlArtifactService(tmp_path / "artifacts")
    artifact = artifacts.create_artifact("<Document><Name>A</Name></Document>")
    result = artifacts.apply_patch(artifact.artifact_id, 1, [field_alias])
    assert "<Name>B</Name>" in artifacts.store.version_path(artifact.artifact_id, result.new_version).read_text(encoding="utf-8")


@pytest.mark.unit
def test_public_patch_schema_documents_canonical_and_alias_fields():
    from mcp.server.fastmcp import FastMCP
    from scdw.mcp.xml_artifact_tools import register_xml_artifact_tools

    mcp = FastMCP("patch-schema")
    register_xml_artifact_tools(mcp, object())
    tool = mcp._tool_manager._tools["patch_xml_artifact"]
    properties = tool.parameters["$defs"]["PatchOperationInput"]["properties"]
    assert set(properties) == {"op", "old", "new", "expected_occurrences"}
    assert "replace_exact" in tool.description


@pytest.mark.unit
def test_semantic_repair_returns_to_planning(tmp_path):
    from mcp.server.fastmcp import FastMCP
    from scdw.mcp.xml_artifact_tools import register_xml_artifact_tools

    plans = LadPlanService(tmp_path / "plans")
    plan = plans.create_from_requirements("条件有效时输出", conversation_id="semantic", target_device="PLC")
    artifacts = XmlArtifactService(tmp_path / "artifacts")
    artifact = artifacts.create_block_artifact(plan.main_fc.block_name, "FC", plan_id=plan.plan_id)
    plans.link_artifact(plan.plan_id, plan.main_fc.block_name, artifact.artifact_id, 1)
    mcp = FastMCP("semantic-repair")
    register_xml_artifact_tools(mcp, object(), artifacts, plans)
    call = mcp._tool_manager._tools["replace_network_and_prepare_import"].fn
    result = json.loads(call(artifact.artifact_id, 1, plan.networks[0].network_key, compile_unit(1, "changed"), "semantic", None))
    assert result["code"] == "SEMANTIC_CHANGE_REQUIRES_REPLAN"
    restored = plans.get(plan.plan_id)
    assert restored.step_status["planning"] == "needs_revision"
    assert restored.networks[0].status == "needs_revision"


@pytest.mark.unit
def test_appended_networks_receive_unique_document_ids_but_keep_local_uids(tmp_path):
    artifacts = XmlArtifactService(tmp_path / "artifacts")
    artifact = artifacts.create_block_artifact("FC_Main", "FC")
    version = artifacts.append_network(artifact.artifact_id, 1, "one", compile_unit(1, "one", uid=7))
    version = artifacts.append_network(artifact.artifact_id, version, "two", compile_unit(1, "two", uid=7))
    content = artifacts.store.version_path(artifact.artifact_id, version).read_text(encoding="utf-8")
    document_ids = re.findall(r'(?<!U)\bID="(\d+)"', content)
    assert len(document_ids) == len(set(document_ids))
    assert content.count('UId="7"') == 2


@pytest.mark.unit
def test_absolute_db_offset_is_rejected_before_optimized_db_creation():
    with pytest.raises(AbsoluteDbAddressUnsupportedError):
        build_global_db_scl("AlarmDB", 4, [DBVariable("Alarm", "Bool", offset="0.0")])

    from mcp.server.fastmcp import FastMCP
    from scdw.mcp.tools import register_mcp_tools

    mcp = FastMCP("absolute-db")
    register_mcp_tools(mcp)
    call = mcp._tool_manager._tools["create_global_db"].fn
    result = json.loads(call("PLC_1", "AlarmDB", 4, [{"name": "Alarm", "data_type": "Bool", "address": "%DB4.DBX0.0"}], "absolute"))
    assert result["success"] is False
    assert result["code"] == "ABSOLUTE_DB_ADDRESS_UNSUPPORTED"
    assert result["optimized_access"] is None
    assert result["variable_mappings"][0]["requested_address"] == "%DB4.DBX0.0"
    assert result["variable_mappings"][0]["tia_actual_address"] is None


@pytest.mark.unit
def test_latest_burner_alarm_golden_topologies_are_published_from_raw_v17():
    catalog = json.loads(open("data/rag/knowledge/catalog.json", encoding="utf-8").read())
    entries = {item["id"]: item for item in catalog["items"]}
    edge = entries["topology.contact_or_pbox_scoil.v17"]
    coil = entries["topology.contact_or_coil.v17"]
    assert edge["status"] == coil["status"] == "golden"
    assert edge["source_refs"] == ["data/rag/raw/application/烧嘴控制块.xml"]
    assert edge["generation_mode"] == "knowledge_renderer_required"
    assert {"or.cardinality", "topology.parallel", "topology.merge", "edge.ports.in_bit_out"} <= set(edge["provides"])
    assert "多个Contact.out共用一条Wire" in edge["not_for"]


@pytest.mark.unit
def test_planner_selects_exact_reviewed_or_topologies_instead_of_comparison_template():
    plain = LadPlanner().plan("故障A或故障B时驱动报警", conversation_id="plain-or", target_device="PLC")
    edge = LadPlanner().plan("故障A或故障B经P_TRIG上升沿后置位报警", conversation_id="edge-or", target_device="PLC")
    assert "topology.contact_or_coil.v17" in plain.networks[0].selected_knowledge_ids
    assert "topology.contact_or_pbox_scoil.v17" in edge.networks[0].selected_knowledge_ids
    assert plain.networks[0].topology["kind"] == edge.networks[0].topology["kind"] == "parallel_merge"


@pytest.mark.unit
def test_planner_keeps_distinct_additional_output_out_of_first_or_network():
    plan = LadPlanner().plan(
        "故障A或故障B经P_TRIG上升沿后置位蜂鸣器。另外，超温A或超温B时驱动超温标志。",
        conversation_id="two-actions",
        target_device="PLC",
    )
    assert len(plan.networks) == 2
    assert plan.networks[0].depends_on == plan.networks[1].depends_on == []
    assert "topology.contact_or_pbox_scoil.v17" in plan.networks[0].selected_knowledge_ids
    assert "topology.contact_or_coil.v17" in plan.networks[1].selected_knowledge_ids
    assert all(network.split_reason for network in plan.networks)


@pytest.mark.unit
def test_parallel_plan_requires_known_kind_and_explicit_branches(tmp_path):
    service = LadPlanService(tmp_path / "plans")
    plan = LadPlanner().plan("条件A或条件B时输出", conversation_id="strict-topology", target_device="PLC")
    plan.networks[0].topology["kind"] = "parallel_powerrail_magic"
    with pytest.raises(KnowledgeGapError, match="unsupported topology kind"):
        service.save(plan)
    plan.networks[0].topology["kind"] = "parallel"
    plan.networks[0].parallel_branches = []
    with pytest.raises(KnowledgeGapError, match="parallel_branches"):
        service.save(plan)


@pytest.mark.unit
def test_knowledge_renderer_prevents_latest_invalid_multi_output_wire(tmp_path):
    from mcp.server.fastmcp import FastMCP
    from scdw.mcp.xml_artifact_tools import register_xml_artifact_tools

    plans = LadPlanService(tmp_path / "plans")
    plan = plans.create_from_requirements("故障A或故障B时输出", conversation_id="renderer", target_device="PLC", main_fc_name="FC_BurnerAlarm")
    network = plan.networks[0]
    selected = ["topology.contact_or_pbox_scoil.v17", "shell.fc_block.v17", "network_text.title_comment.v17"]
    plan = plans.revise_network_plan(plan.plan_id, network.network_key, {
        "purpose": "故障OR上升沿置位",
        "main_branch": ["Contacts -> O -> PBox -> SCoil"],
        "parallel_branches": [["故障A"], ["故障B"]],
        "instructions": ["Contact", "O", "PBox", "SCoil"],
        "variables": ["故障A", "故障B", "内部数据.报警脉冲", "蜂鸣器"],
        "required_capabilities": ["multi_contact_or_pbox_scoil", "fc_shell", "network_title", "network_comment"],
        "selected_knowledge_ids": selected,
        "instruction_chain": ["Contact branches", "O merge", "PBox rising edge", "SCoil"],
        "topology": {"kind": "parallel_merge", "description": "Each Contact.out has an independent Wire to O.inN."},
    }, "use exact burner alarm golden topology")
    artifacts = XmlArtifactService(tmp_path / "artifacts")
    artifact = artifacts.create_block_artifact("FC_BurnerAlarm", "FC", plan_id=plan.plan_id)
    plans.link_artifact(plan.plan_id, "FC_BurnerAlarm", artifact.artifact_id, 1)
    mcp = FastMCP("knowledge-renderer")
    register_xml_artifact_tools(mcp, object(), artifacts, plans)

    manual = mcp._tool_manager._tools["append_network_and_prepare_import"].fn
    rejected = json.loads(manual(artifact.artifact_id, 1, network.network_key, compile_unit(1, "guessed"), None, None))
    assert rejected["code"] == "KNOWLEDGE_RENDERER_REQUIRED"

    write = mcp._tool_manager._tools["write_lad_network_from_knowledge"].fn
    contacts = [[f"{index}#故障反馈"] for index in range(1, 8)]
    result = json.loads(write(
        artifact.artifact_id, 1, network.network_key, "topology.contact_or_pbox_scoil.v17",
        {"contacts": contacts, "edge_memory": ["内部数据", "报警脉冲"], "output": ["蜂鸣器"]},
        "故障报警", "7个故障反馈经O汇合和PBox上升沿后置位蜂鸣器。", False,
    ))
    assert result["success"] is True
    xml = artifacts.get_network(artifact.artifact_id, network.network_key, result["version"])
    root = ET.fromstring(xml)
    local = lambda tag: tag.rsplit("}", 1)[-1]
    part_by_uid = {item.attrib["UId"]: item.attrib.get("Name") for item in root.iter() if local(item.tag) == "Part"}
    card = next(item for item in root.iter() if local(item.tag) == "TemplateValue" and item.attrib.get("Name") == "Card")
    assert card.text == "7"
    contact_uids = {uid for uid, name in part_by_uid.items() if name == "Contact"}
    seen_inputs = set()
    for wire in (item for item in root.iter() if local(item.tag) == "Wire"):
        contact_outputs = [item for item in wire if local(item.tag) == "NameCon" and item.attrib.get("UId") in contact_uids and item.attrib.get("Name") == "out"]
        assert len(contact_outputs) <= 1
        for endpoint in wire:
            if local(endpoint.tag) == "NameCon" and part_by_uid.get(endpoint.attrib.get("UId")) == "O" and endpoint.attrib.get("Name", "").startswith("in"):
                seen_inputs.add(endpoint.attrib["Name"])
    assert seen_inputs == {f"in{index}" for index in range(1, 8)}
    assert {item.attrib.get("Name") for item in root.iter() if local(item.tag) == "Part"} >= {"O", "PBox", "SCoil"}


@pytest.mark.unit
def test_block_artifact_creation_links_plan_without_second_tool_call(tmp_path):
    from mcp.server.fastmcp import FastMCP
    from scdw.mcp.lad_plan_tools import register_lad_plan_tools

    plans = LadPlanService(tmp_path / "plans")
    artifacts = XmlArtifactService(tmp_path / "artifacts")
    plan = plans.create_from_requirements("条件A时输出", conversation_id="relink", target_device="PLC")
    mcp = FastMCP("plan-relink")
    from scdw.mcp.xml_artifact_tools import register_xml_artifact_tools
    register_xml_artifact_tools(mcp, object(), artifacts, plans)
    call = mcp._tool_manager._tools["create_lad_block_artifact"].fn
    result = json.loads(call(plan.plan_id, plan.main_fc.block_name, "FC", None, "PLC", "relink"))
    assert result["success"] is True
    artifact_id = result["artifact"]["artifact_id"]
    assert artifacts.get_artifact(artifact_id).plan_id == plan.plan_id
    assert plans.get(plan.plan_id).artifacts[plan.main_fc.block_name]["artifact_id"] == artifact_id
    assert "link_lad_plan_artifact" not in mcp._tool_manager._tools
