from __future__ import annotations

from types import SimpleNamespace

import pytest

from scdw.lad_generation import LadPlanService, LadPlanner
from scdw.mcp.tool_manager import ToolManager


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
