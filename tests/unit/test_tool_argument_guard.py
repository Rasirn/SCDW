from __future__ import annotations

from types import SimpleNamespace

import pytest

from scdw.lad_generation import LadPlanService, LadPlanner
from scdw.mcp.tool_manager import ToolManager
from scdw.openness.tia_blocks import normalise_tia_member_name


@pytest.mark.unit
def test_trailing_json_is_structured_parse_error():
    with pytest.raises(Exception) as raised:
        ToolManager._parse_arguments('{"plan_id":"x"} trailing')
    assert raised.value.code == "TRAILING_TOOL_ARGUMENT_CONTENT"
    assert raised.value.position == 15


@pytest.mark.unit
def test_schema_error_reports_nested_path():
    schema = {"type": "object", "properties": {"planning": {"type": "object", "properties": {"networks": {"type": "array", "items": {"type": "object", "properties": {"kind": {"enum": ["series", "parallel"]}}, "required": ["kind"]}}}, "required": ["networks"]}}, "required": ["planning"]}
    errors = ToolManager._validate_schema({"planning": {"networks": [{"kind": "bad"}]}}, schema)
    assert errors == [{"path": "planning.networks[0].kind", "message": "allowed values: series, parallel"}]


@pytest.mark.unit
def test_normalize_backfills_blueprint_capability_mapping(tmp_path):
    source = LadPlanner().plan("条件A常开时驱动输出B", conversation_id="normal", target_device="PLC")
    planning = source.to_dict()
    for network in planning["networks"]:
        network["required_capabilities"] = []
        network["selected_knowledge_ids"] = []
    value, report = LadPlanService(tmp_path / "plans").normalize_planning(planning)
    assert value["planned_network_count"] == len(value["networks"])
    assert "logic.contact_no" in value["networks"][0]["required_capabilities"]
    assert value["networks"][0]["selected_knowledge_ids"]
    assert any(name.endswith("required_capabilities") for name in report["normalized_fields"])


@pytest.mark.unit
def test_draft_is_persisted_and_reused_by_conversation_and_requirements(tmp_path):
    service = LadPlanService(tmp_path / "plans")
    first_id, first, reused = service.create_or_get_draft("条件A时输出B", conversation_id="c1", target_device="PLC")
    second_id, second, reused_second = service.create_or_get_draft("条件A时输出B", conversation_id="c1", target_device="PLC")
    assert not reused and reused_second and first_id == second_id
    assert first.networks[0].network_key == second.networks[0].network_key
    updated = service.update_draft_network(first_id, first.networks[0].network_key, {"title": "增量标题"})
    assert updated.networks[0].title == "增量标题"


@pytest.mark.unit
def test_numeric_leading_db_member_is_normalised_deterministically():
    assert normalise_tia_member_name("1区超温标志") == "超温标志_1区"
    assert normalise_tia_member_name("1区超温标志", {"超温标志_1区"}) == "超温标志_1区_2"
