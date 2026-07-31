from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from scdw.lad_generation import LadCapabilityCatalog, LadPlanService, LadPlanner
from scdw.lad_generation.models import PlanValidationError
from scdw.lad_generation.semantics import blueprint_tree_lines, validate_compile_unit_semantics
from scdw.mcp.xml_artifact_tools import register_xml_artifact_tools
from scdw.xml_workspace import XmlArtifactService
from scdw.xml_workspace.knowledge_networks import render_knowledge_network


LATEST_FAILED_REQUIREMENT = (
    Path(__file__).parents[1] / "fixtures" / "latest_failed_fan_gas.txt"
).read_text(encoding="utf-8").strip()


def _make_linked_pipeline(tmp_path):
    plans = LadPlanService(tmp_path / "plans")
    plan = plans.create_from_requirements(
        "条件A常开时驱动输出A", conversation_id="frozen", target_device="PLC",
    )
    artifacts = XmlArtifactService(tmp_path / "artifacts")
    artifact = artifacts.create_block_artifact(
        plan.main_fc.block_name, "FC", plan_id=plan.plan_id,
    )
    plans.link_artifact(plan.plan_id, plan.main_fc.block_name, artifact.artifact_id, 1)
    mcp = FastMCP("blueprint-pipeline")
    register_xml_artifact_tools(mcp, object(), artifacts, plans)
    return plans, plan, artifacts, artifact, mcp


@pytest.mark.unit
def test_latest_failed_case_freezes_three_complete_networks(tmp_path):
    service = LadPlanService(tmp_path / "plans")
    plan = service.create_from_requirements(
        LATEST_FAILED_REQUIREMENT, conversation_id="latest-failure", target_device="PLC_1",
    )

    assert plan.blueprint_status == "approved_for_generation"
    assert plan.requested_network_count == plan.planned_network_count == len(plan.networks) == 3
    assert plan.uncovered_capabilities == []
    assert plan.blueprint_sha256 and plan.capability_catalog_sha256
    tree = "\n".join(blueprint_tree_lines(plan))
    assert "程序段1" in tree and "程序段2" in tree and "程序段3" in tree
    assert "常闭触点" in tree and "CALCULATE" in tree and "数值比较 (Ge)" in tree

    for network in plan.networks:
        xml = render_knowledge_network(
            network.renderer_id,
            blueprint=network.blueprint.to_dict(),
            title=network.title,
            comment=network.comment,
        )
        assert validate_compile_unit_semantics(network, xml) == []


@pytest.mark.unit
def test_unavailable_capability_is_reported_before_xml_generation(tmp_path):
    plan = LadPlanner().plan("条件A时输出A", conversation_id="gap", target_device="PLC")
    contact = plan.networks[0].blueprint.children[0]
    contact.kind = "falling_edge"
    contact.capability_id = "logic.falling_edge_external_bit"
    contact.operands = {"memory": ["内部数据", "下降沿记忆"]}

    with pytest.raises(PlanValidationError) as raised:
        LadPlanService(tmp_path / "plans")._save_new_active(plan)

    assert any(
        issue["code"] == "CAPABILITY_UNAVAILABLE"
        for issue in raised.value.issues
    )
    assert not list((tmp_path / "artifacts").glob("**/*.xml"))


@pytest.mark.unit
def test_capability_catalog_is_compact_and_declares_missing_renderers():
    catalog = LadCapabilityCatalog.instance().compact()
    by_id = {item["capability_id"]: item for item in catalog["capabilities"]}

    assert by_id["logic.contact_no"]["available"] is True
    assert by_id["math.numeric"]["renderer_id"] == "blueprint_network_v17"
    assert by_id["timer.tof"]["available"] is False
    assert by_id["call.fb_instance_db"]["renderer_id"] == "block_call_v17"
    assert "content" not in json.dumps(catalog, ensure_ascii=False)


@pytest.mark.unit
def test_xml_repair_cannot_change_frozen_coil_semantics_and_noop_is_idempotent(tmp_path):
    plans, plan, artifacts, artifact, mcp = _make_linked_pipeline(tmp_path)
    network = plan.networks[0]
    write = mcp._tool_manager._tools["write_lad_network_from_blueprint"].fn
    written = json.loads(write(artifact.artifact_id, 1, network.network_key, False))
    assert written["success"] is True
    version = written["version"]
    original = artifacts.get_network(artifact.artifact_id, network.network_key, version)

    repair = mcp._tool_manager._tools["repair_lad_xml_expression"].fn
    semantic_change = original.replace('Part Name="Coil"', 'Part Name="SCoil"', 1)
    rejected = json.loads(repair(
        artifact.artifact_id, version, network.network_key, semantic_change, "TEST_DIAGNOSTIC",
    ))
    assert rejected["success"] is False
    assert rejected["code"] == "BLUEPRINT_SEMANTIC_MISMATCH"
    assert artifacts.get_artifact(artifact.artifact_id).current_version == version
    assert plans.get(plan.plan_id).blueprint_sha256 == plan.blueprint_sha256

    noop = json.loads(repair(
        artifact.artifact_id, version, network.network_key, original, "TEST_DIAGNOSTIC",
    ))
    assert noop["success"] is False
    assert noop["code"] == "NO_XML_CHANGES"
    assert artifacts.get_artifact(artifact.artifact_id).current_version == version


@pytest.mark.unit
def test_verified_network_cannot_be_regenerated(tmp_path):
    plans, plan, artifacts, artifact, mcp = _make_linked_pipeline(tmp_path)
    network = plan.networks[0]
    write = mcp._tool_manager._tools["write_lad_network_from_blueprint"].fn
    written = json.loads(write(artifact.artifact_id, 1, network.network_key, False))
    plans.set_network_status(plan.plan_id, network.network_key, "verified")

    blocked = json.loads(write(
        artifact.artifact_id, written["version"], network.network_key, True,
    ))
    assert blocked["success"] is False
    assert blocked["code"] == "VERIFIED_NETWORK_IMMUTABLE"
    assert "verified Network cannot be regenerated" in blocked["message"]
    assert artifacts.get_artifact(artifact.artifact_id).current_version == written["version"]
