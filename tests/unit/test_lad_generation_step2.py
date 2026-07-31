from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from scdw.lad_generation import LadPlanService, LadPlanner
from scdw.xml_workspace import ArtifactError, XmlArtifactService
from scdw.xml_workspace.validation import validate_xml


def compile_unit(unit_id: int, label: str, uid: int = 1) -> str:
    return f'''<SW.Blocks.CompileUnit ID="{unit_id}" CompositionName="CompileUnits">
  <AttributeList>
    <NetworkSource><FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4"><Parts><Access Scope="GlobalVariable" UId="{uid}"><Symbol><Component Name="{label}" /></Symbol></Access></Parts><Wires /></FlgNet></NetworkSource>
    <ProgrammingLanguage>LAD</ProgrammingLanguage>
  </AttributeList>
  <ObjectList>
    <MultilingualText ID="{unit_id + 100}" CompositionName="Comment"><ObjectList><MultilingualTextItem ID="{unit_id + 101}" CompositionName="Items"><AttributeList><Culture>zh-CN</Culture><Text>{label} comment</Text></AttributeList></MultilingualTextItem></ObjectList></MultilingualText>
    <MultilingualText ID="{unit_id + 102}" CompositionName="Title"><ObjectList><MultilingualTextItem ID="{unit_id + 103}" CompositionName="Items"><AttributeList><Culture>zh-CN</Culture><Text>{label} title</Text></AttributeList></MultilingualTextItem></ObjectList></MultilingualText>
  </ObjectList>
</SW.Blocks.CompileUnit>'''


def document(*units: str) -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<Document><Engineering version="V17" /><SW.Blocks.FC ID="0"><AttributeList><Interface><Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/v5"><Section Name="Input" /><Section Name="Output" /><Section Name="InOut" /><Section Name="Temp" /><Section Name="Constant" /><Section Name="Return" /></Sections></Interface><Name>FC_MainControl</Name><ProgrammingLanguage>LAD</ProgrammingLanguage></AttributeList><ObjectList>{''.join(units)}</ObjectList></SW.Blocks.FC></Document>'''


@pytest.fixture
def planner() -> LadPlanner:
    return LadPlanner()


def make_plan(planner: LadPlanner, requirement: str):
    return planner.plan(requirement, conversation_id="conversation-1", target_device="PLC_1")


@pytest.mark.unit
def test_default_main_block_is_fc_and_reason_is_recorded(planner):
    plan = make_plan(planner, "安全条件满足时允许风机运行")
    assert plan.main_fc.block_type == "FC"
    assert "FC" in plan.main_fc_reason
    assert plan.block_dependency_order[-1] == plan.main_fc.block_name


@pytest.mark.unit
def test_plain_combinational_logic_does_not_create_fb(planner):
    plan = make_plan(planner, "入口允许且联锁正常时驱动输出")
    assert plan.auxiliary_fbs == []
    assert plan.instance_dbs == []


@pytest.mark.unit
@pytest.mark.parametrize("requirement", ["启动后延时5秒输出", "累计产品数量", "记忆启停状态"])
def test_stateful_requirement_creates_helper_fb_and_instance_db(planner, requirement):
    plan = make_plan(planner, requirement)
    assert len(plan.auxiliary_fbs) == 1
    assert len(plan.instance_dbs) == 1
    assert plan.instance_dbs[0].fb_name == plan.auxiliary_fbs[0].block_name


@pytest.mark.unit
def test_related_timers_memory_and_counter_share_one_fb(planner):
    plan = make_plan(planner, "启动记忆；TON启动延时；TOF停机延时；累计启动次数；报警锁存")
    assert len(plan.auxiliary_fbs) == 1
    assert {"timer", "memory", "counter"} <= set(plan.auxiliary_fbs[0].state_features)
    assert len(plan.instance_dbs) == 1


@pytest.mark.unit
def test_or_conditions_are_parallel_branches_in_one_network(planner):
    plan = make_plan(planner, "就地按钮或远程命令任一有效时驱动风机")
    main = [item for item in plan.networks if item.block_name == plan.main_fc.block_name]
    assert len(main) == 1
    assert main[0].parallel_branches


@pytest.mark.unit
def test_explicit_scan_dependency_can_split_networks(planner):
    plan = make_plan(planner, "先读取并换算模拟量。再使用换算结果计算输出")
    main = [item for item in plan.networks if item.block_name == plan.main_fc.block_name]
    assert len(main) == 2
    assert main[1].depends_on == [main[0].network_key]
    assert all(item.split_reason for item in main)


@pytest.mark.unit
def test_explicit_numbered_networks_are_a_hard_boundary(planner):
    plan = make_plan(
        planner,
        "程序共包含三个程序段。程序段1：风机输出。程序段2：风机输出计算。程序段3：REAL大于等于1.2时驱动烧嘴电源。",
    )
    main = [item for item in plan.networks if item.block_name == plan.main_fc.block_name]
    assert plan.requested_network_count == plan.planned_network_count == len(main) == 3
    assert plan.instruction_pipeline == [item.network_key for item in plan.networks]
    assert all(item.split_reason == "用户明确指定程序段边界（硬约束）" for item in main)
    assert "instruction.compare_ge_real_coil.v17" in main[-1].selected_knowledge_ids


@pytest.mark.unit
def test_llm_plan_cannot_override_explicit_network_count(tmp_path, planner):
    requirement = "程序共包含三个程序段。程序段1：输出A。程序段2：输出B。程序段3：输出C。"
    value = planner.plan(requirement, conversation_id="hard-count", target_device="PLC").to_dict()
    planning = {key: item for key, item in value.items() if key not in {
        "plan_id", "conversation_id", "target_device", "requirements", "created_at", "updated_at",
    }}
    extra = dict(planning["networks"][-1])
    extra["network_key"] = "illegal_extra_network"
    extra["title"] = "额外程序段"
    planning["networks"].append(extra)
    planning["planned_network_count"] = 4
    planning["instruction_pipeline"].append("illegal_extra_network")
    with pytest.raises(ValueError, match="hard constraint"):
        LadPlanService(tmp_path / "plans").create_from_planning(
            planning, requirements=requirement, conversation_id="hard-count", target_device="PLC",
        )


@pytest.mark.unit
def test_latest_vfd_case_uses_global_state_in_fc_and_composite_renderer(planner):
    plan = make_plan(
        planner,
        "变频器读取失败1上升沿由PBox检测并使用存储变量，置位通讯故障标志；读取成功1上升沿复位故障标志；"
        "故障标志接入TON，定时器变量使用内部数据.通讯延时定时器，PT=T#3m；TON.Q驱动通讯故障线圈。",
    )
    assert plan.auxiliary_fbs == [] and plan.instance_dbs == []
    assert len(plan.networks) == 1
    network = plan.networks[0]
    assert "instruction.pbox_set_reset_ton_coil.v17" in network.selected_knowledge_ids
    assert "pbox_set_reset_ton_coil" in network.required_capabilities


@pytest.mark.unit
def test_every_planned_network_has_title_comment_and_knowledge_ids(planner):
    plan = make_plan(planner, "入口允许时输出；TON延时并记忆报警")
    assert plan.networks
    assert all(item.title.strip() and item.comment.strip() and item.knowledge_ids for item in plan.networks)


@pytest.mark.unit
def test_single_conversation_plan_is_persisted_and_recovered(tmp_path):
    root = tmp_path / "plans"
    created = LadPlanService(root).create_from_requirements("条件A或条件B时输出", conversation_id="only-one", target_device="PLC")
    restored = LadPlanService(root).get(created.plan_id)
    assert restored.to_dict() == created.to_dict()
    assert restored.conversation_id == "only-one"


@pytest.mark.unit
def test_llm_formed_plan_can_be_persisted_without_replanning(tmp_path, planner):
    planned = make_plan(planner, "条件A或条件B时输出").to_dict()
    planning = {key: value for key, value in planned.items() if key not in {
        "plan_id", "conversation_id", "target_device", "requirements", "created_at", "updated_at",
    }}
    saved = LadPlanService(tmp_path / "plans").create_from_planning(
        planning,
        requirements="原始需求",
        conversation_id="same-dialog",
        target_device="PLC",
    )
    assert saved.conversation_id == "same-dialog"
    assert saved.networks[0].knowledge_ids == planned["networks"][0]["knowledge_ids"]


@pytest.mark.unit
def test_multiple_artifacts_belong_to_one_plan(tmp_path):
    plans = LadPlanService(tmp_path / "plans")
    plan = plans.create_from_requirements("TON延时后保持运行", conversation_id="one", target_device="PLC")
    plans.link_artifact(plan.plan_id, "FB_StateControl", "xml_111111111111", 1)
    linked = plans.link_artifact(plan.plan_id, "FC_MainControl", "xml_222222222222", 1)
    assert set(linked.artifacts) == {"FB_StateControl", "FC_MainControl"}
    assert {item["artifact_id"] for item in linked.artifacts.values()} == {"xml_111111111111", "xml_222222222222"}


@pytest.mark.unit
def test_main_fc_artifact_waits_for_helper_fb_artifact(tmp_path):
    plans = LadPlanService(tmp_path / "plans")
    plan = plans.create_from_requirements("TON延时后保持运行", conversation_id="one", target_device="PLC")
    with pytest.raises(ValueError, match="created first"):
        plans.link_artifact(plan.plan_id, "FC_MainControl", "xml_222222222222", 1)


@pytest.mark.unit
def test_append_network_creates_new_version_and_sidecar_only(tmp_path):
    service = XmlArtifactService(tmp_path / "artifacts")
    artifact = service.create_block_artifact("FC_MainControl", "FC")
    version = service.append_network(artifact.artifact_id, 1, "motor_logic", compile_unit(1, "Motor"))
    assert version == 2
    assert service.store.version_path(artifact.artifact_id, 1).read_text(encoding="utf-8") != service.store.version_path(artifact.artifact_id, 2).read_text(encoding="utf-8")
    assert "network_key" not in service.store.version_path(artifact.artifact_id, 2).read_text(encoding="utf-8")
    sidecar = service.store.network_index(artifact.artifact_id, 2)
    assert sidecar[0]["network_key"] == "motor_logic"


@pytest.mark.unit
def test_replacing_one_network_preserves_other_network_and_stable_keys(tmp_path):
    service = XmlArtifactService(tmp_path / "artifacts")
    artifact = service.create_artifact(document(compile_unit(1, "A"), compile_unit(2, "B")), network_keys=["a", "b"])
    untouched = service.get_network(artifact.artifact_id, "b")
    version = service.replace_network(artifact.artifact_id, 1, "a", compile_unit(3, "A2", uid=77))
    assert service.get_network(artifact.artifact_id, "b", version) == untouched
    assert [item["network_key"] for item in service.list_networks(artifact.artifact_id, version)] == ["a", "b"]
    assert "A title" in service.get_network(artifact.artifact_id, "a", 1)


@pytest.mark.unit
def test_insert_delete_and_text_edits_keep_stable_key_and_uids(tmp_path):
    service = XmlArtifactService(tmp_path / "artifacts")
    artifact = service.create_artifact(document(compile_unit(1, "A", uid=15), compile_unit(2, "B", uid=15)), network_keys=["a", "b"])
    version = service.append_network(artifact.artifact_id, 1, "middle", compile_unit(3, "M", uid=15), position=1)
    assert [item["network_key"] for item in service.list_networks(artifact.artifact_id, version)] == ["a", "middle", "b"]
    version = service.update_network_text(artifact.artifact_id, version, "middle", title="新标题", comment="新注释")
    edited = service.get_network(artifact.artifact_id, "middle", version)
    assert "新标题" in edited and "新注释" in edited and 'UId="15"' in edited
    version = service.delete_network(artifact.artifact_id, version, "a")
    assert [item["network_key"] for item in service.list_networks(artifact.artifact_id, version)] == ["middle", "b"]


@pytest.mark.unit
def test_reused_uid_in_different_compile_units_is_not_a_local_error():
    result = validate_xml(document(compile_unit(1, "A", uid=10), compile_unit(2, "B", uid=10)))
    assert result.valid
    assert result.errors == []


@pytest.mark.unit
def test_invalid_xml_is_still_rejected(tmp_path):
    service = XmlArtifactService(tmp_path / "artifacts")
    with pytest.raises(ArtifactError) as created:
        service.create_artifact("<Document>")
    assert created.value.code == "XML_VALIDATION_FAILED"
    artifact = service.create_block_artifact("FC_MainControl")
    with pytest.raises(ArtifactError):
        service.append_network(artifact.artifact_id, 1, "bad", "<SW.Blocks.CompileUnit>")


@pytest.mark.unit
def test_generation_planner_never_reads_application_raw(monkeypatch, planner):
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        if "application" in {part.lower() for part in path.parts}:
            raise AssertionError("application raw data was read")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    assert make_plan(planner, "TON延时后输出").networks


@pytest.mark.unit
def test_lad_generation_modules_contain_no_multi_agent_dispatch_code():
    import scdw.lad_generation.models as models
    import scdw.lad_generation.planner as planning
    import scdw.lad_generation.service as persistence

    source = "\n".join(inspect.getsource(module).lower() for module in (models, planning, persistence))
    forbidden = ("spawn_agent", "subagent", "agent_pool", "multi_agent", "parallel_agent")
    assert not any(token in source for token in forbidden)
