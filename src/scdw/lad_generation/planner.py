from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from .models import AuxiliaryFbPlan, BlockPlan, InstanceDbPlan, LadGenerationPlan, NetworkPlan


_STATE_FEATURES = {
    "timer": ("定时", "延时", "TON", "TOF", "TP", "timer"),
    "memory": ("记忆", "保持", "锁存", "跨扫描", "Static", "memory", "latch"),
    "counter": ("累计", "计数", "counter"),
    "runtime": ("累计运行时间", "运行时间累计", "runtime"),
    "sequence": ("状态机", "顺序步骤", "步序", "sequence", "state machine"),
}
_DEPENDENCY_MARKERS = ("先", "然后", "再", "之后", "依赖", "结果用于", "previous result")


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LadPlanner:
    """Small deterministic planner; it does not generate or validate LAD semantics."""

    def plan(
        self,
        requirements: str,
        *,
        conversation_id: str,
        target_device: str,
        main_fc_name: str = "FC_MainControl",
    ) -> LadGenerationPlan:
        if not requirements.strip():
            raise ValueError("requirements must not be empty")
        sentences = [part.strip(" -\t") for part in re.split(r"[。；;\n]+", requirements) if part.strip(" -\t")]
        features = [name for name, words in _STATE_FEATURES.items() if any(word.lower() in requirements.lower() for word in words)]
        state_sentences = [item for item in sentences if any(word.lower() in item.lower() for words in _STATE_FEATURES.values() for word in words)]
        ordinary = [item for item in sentences if item not in state_sentences]
        stamp = _stamp()

        main_fc = BlockPlan(
            block_name=main_fc_name,
            block_type="FC",
            responsibility="组织无状态组合逻辑并直接调用需要跨扫描状态的辅助FB",
            logic_scope=ordinary or ["调用辅助状态控制FB"],
            interface={"Input": [], "Output": [], "InOut": [], "Temp": [], "Constant": [], "Return": ["Ret_Val:Void"]},
        )
        auxiliary_fbs: list[AuxiliaryFbPlan] = []
        instance_dbs: list[InstanceDbPlan] = []
        networks: list[NetworkPlan] = []

        if features:
            fb = AuxiliaryFbPlan(
                block_name="FB_StateControl",
                block_type="FB",
                responsibility="集中处理同一需求中的定时、记忆、累计、计数和顺序状态",
                logic_scope=state_sentences,
                interface={"Input": [], "Output": [], "InOut": [], "Static": features, "Temp": [], "Constant": []},
                state_features=features,
            )
            auxiliary_fbs.append(fb)
            instance_dbs.append(InstanceDbPlan(
                db_name="DB_StateControl",
                fb_name=fb.block_name,
                instance_name="StateControl",
                responsibility=f"保存 {fb.block_name} 的跨扫描周期状态",
            ))
            networks.append(NetworkPlan(
                network_key="state_control",
                block_name=fb.block_name,
                title="状态控制、定时与累计",
                comment="在一个职责清晰的辅助FB中集中处理相关跨扫描状态，避免按定时器或状态位拆分FB。",
                purpose="跨扫描周期状态处理",
                main_branch=state_sentences,
                instructions=[feature.upper() for feature in features],
                knowledge_ids=["interface.complex_sections.v17", "instruction.ton_static_instance.v17", "network_text.title_comment.v17"],
            ))

        explicit_dependency = len(ordinary) > 1 and any(marker.lower() in requirements.lower() for marker in _DEPENDENCY_MARKERS)
        if ordinary and explicit_dependency:
            previous: str | None = None
            for index, sentence in enumerate(ordinary, 1):
                key = f"main_sequence_{index}"
                networks.append(NetworkPlan(
                    network_key=key,
                    block_name=main_fc_name,
                    title=f"顺序逻辑 {index}",
                    comment=sentence,
                    purpose=sentence,
                    main_branch=[sentence],
                    knowledge_ids=["shell.fc_block.v17", "topology.series_contact_coil.v17", "network_text.title_comment.v17"],
                    depends_on=[previous] if previous else [],
                    split_reason="存在明确扫描顺序或前一Network结果依赖",
                ))
                previous = key
        elif ordinary:
            has_or = any(token in requirements for token in ("或", "任一", "OR", "or"))
            networks.append(NetworkPlan(
                network_key="main_combined_logic",
                block_name=main_fc_name,
                title="组合条件与业务动作",
                comment="将无明确扫描依赖且可合并的条件、判断和动作集中表达；OR条件使用同一Network的并联支路。",
                purpose="普通组合逻辑、条件判断、计算、转换和无状态控制",
                main_branch=ordinary,
                parallel_branches=[[item] for item in ordinary] if has_or else [],
                knowledge_ids=["shell.fc_block.v17", "topology.parallel_set_reset.v17" if has_or else "topology.series_contact_coil.v17", "network_text.title_comment.v17"],
            ))

        if auxiliary_fbs:
            networks.append(NetworkPlan(
                network_key="call_state_control",
                block_name=main_fc_name,
                title="调用辅助状态控制FB",
                comment="主FC直接调用FB_StateControl，并使用DB_StateControl作为其独立背景DB。",
                purpose="从主FC调度跨扫描状态逻辑",
                main_branch=["CALL FB_StateControl, DB_StateControl"],
                instructions=["Call"],
                knowledge_ids=["call.fb_global_instance.v17", "network_text.title_comment.v17"],
                depends_on=["state_control"],
                split_reason="FB调用依赖辅助FB及其背景DB先创建",
            ))

        order = [block.block_name for block in auxiliary_fbs]
        order.extend(db.db_name for db in instance_dbs)
        order.append(main_fc_name)
        interface_plan = {main_fc.block_name: main_fc.interface, **{block.block_name: block.interface for block in auxiliary_fbs}}
        return LadGenerationPlan(
            plan_id="ladplan_" + secrets.token_hex(8),
            conversation_id=conversation_id,
            target_device=target_device,
            requirements=requirements,
            main_fc=main_fc,
            main_fc_reason="主块选择FC，因为组合逻辑和调度本身不需要跨扫描Static状态；仅将确有状态的职责提取到辅助FB。",
            auxiliary_fbs=auxiliary_fbs,
            instance_dbs=instance_dbs,
            block_dependency_order=order,
            interface_plan=interface_plan,
            networks=networks,
            current_block=order[0] if order else main_fc_name,
            current_network=networks[0].network_key if networks else None,
            step_status={"planning": "planned", "artifact_creation": "planned", "network_generation": "planned"},
            created_at=stamp,
            updated_at=stamp,
        )
