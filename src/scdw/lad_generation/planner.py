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
_SEPARATE_ACTION_MARKERS = ("另外", "另有", "同时还", "此外")


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _combinational_recipe(text: str) -> dict:
    lowered = text.lower()
    has_or = any(token in text for token in ("或", "任一", "OR", "or"))
    has_edge = any(token.lower() in lowered for token in ("p_trig", "pbox", "上升沿", "正跳变"))
    has_comparison = any(token in text for token in (">", "<", "大于", "小于", "不大于", "不小于", "比较"))
    if has_or and has_edge:
        topology_id = "topology.contact_or_pbox_scoil.v17"
    elif has_or and not has_comparison:
        topology_id = "topology.contact_or_coil.v17"
    elif has_or:
        topology_id = "instruction.comparison_or_chain.v17"
    else:
        topology_id = "topology.series_contact_coil.v17"

    branches = [item.strip() for item in re.split(r"(?:或|\bOR\b)", text, flags=re.IGNORECASE) if item.strip()] if has_or else []
    instructions = ["OR" if has_or else "Contact", "Coil"]
    required = ["fc_shell", "coil", "network_title", "network_comment"]
    instruction_chain = ["evaluate input conditions", "merge parallel branches" if has_or else "evaluate series path", "drive result coil"]
    if topology_id == "topology.contact_or_coil.v17":
        instructions = ["Contact", "O", "Coil"]
        required.append("multi_contact_or_coil")
        instruction_chain = ["evaluate Contact branches", "merge each Contact.out through O.inN", "drive Coil"]
    elif topology_id == "topology.contact_or_pbox_scoil.v17":
        instructions = ["Contact", "O", "PBox", "SCoil"]
        required = ["fc_shell", "multi_contact_or_pbox_scoil", "network_title", "network_comment"]
        instruction_chain = ["evaluate Contact branches", "merge each Contact.out through O.inN", "detect rising edge with PBox bit", "drive SCoil"]
    topology_kind = "parallel_merge" if has_or else "series"
    required.extend(["topology.parallel", "topology.merge"] if has_or else ["topology.series"])
    return {
        "selected": ["shell.fc_block.v17", topology_id, "network_text.title_comment.v17"],
        "parallel_branches": [[item] for item in branches],
        "instructions": instructions,
        "required": required,
        "instruction_chain": instruction_chain,
        "topology": {
            "kind": topology_kind,
            "description": "Alternative conditions merge before one action." if has_or else "Conditions form one series path to the action.",
        },
    }


class LadPlanner:
    """Small deterministic planner; it never invents SimaticML structures."""

    def plan(self, requirements: str, *, conversation_id: str, target_device: str, main_fc_name: str = "FC_MainControl") -> LadGenerationPlan:
        if not requirements.strip():
            raise ValueError("requirements must not be empty")
        sentences = [part.strip(" -\t") for part in re.split(r"[。；;\n]+", requirements) if part.strip(" -\t")]
        lowered = requirements.lower()
        features = [name for name, words in _STATE_FEATURES.items() if any(word.lower() in lowered for word in words)]
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
                db_name="DB_StateControl", fb_name=fb.block_name, instance_name="StateControl", responsibility=f"保存 {fb.block_name} 的跨扫描周期状态"
            ))
            selected = ["interface.complex_sections.v17", "network_text.title_comment.v17", "topology.series_contact_coil.v17"]
            required = ["fb_interface", "network_title", "network_comment", "topology.series"]
            if "timer" in features:
                selected.append("instruction.ton_static_instance.v17")
                required.extend(["ton", "static_timer_instance"])
            if any(item in features for item in ("counter", "runtime")):
                selected.append("instruction.increment_counter.v17")
                required.append("counter.accumulate")
            if any(item in features for item in ("memory", "sequence")):
                selected.append("instruction.pbox_edge_memory.v17")
                required.append("edge.memory_bit")
            selected = list(dict.fromkeys(selected))
            networks.append(NetworkPlan(
                network_key="state_control", block_name=fb.block_name, title="状态控制、定时与累计",
                comment="在一个职责清晰的辅助FB中集中处理相关跨扫描状态，避免按定时器或状态位拆分FB。",
                purpose="跨扫描周期状态处理", main_branch=state_sentences or ["处理跨扫描状态"],
                instructions=[feature.upper() for feature in features], variables=[f"state_{feature}" for feature in features],
                knowledge_ids=selected, selected_knowledge_ids=selected, required_capabilities=required,
                instruction_chain=[f"evaluate {feature}" for feature in features] + ["update state outputs"],
                topology={"kind": "series", "description": "State conditions flow through selected state instructions to outputs."},
            ))

        explicit_dependency = len(ordinary) > 1 and any(marker.lower() in lowered for marker in _DEPENDENCY_MARKERS)
        separate_business_actions = len(ordinary) > 1 and any(
            sentence.lstrip().startswith(_SEPARATE_ACTION_MARKERS) for sentence in ordinary[1:]
        )
        if ordinary and (explicit_dependency or separate_business_actions):
            previous: str | None = None
            for index, sentence in enumerate(ordinary, 1):
                recipe = _combinational_recipe(sentence)
                key = f"main_sequence_{index}" if explicit_dependency else f"main_action_{index}"
                networks.append(NetworkPlan(
                    network_key=key, block_name=main_fc_name,
                    title=f"顺序逻辑 {index}" if explicit_dependency else f"独立业务动作 {index}",
                    comment=sentence, purpose=sentence, main_branch=[sentence],
                    parallel_branches=recipe["parallel_branches"], instructions=recipe["instructions"],
                    variables=[f"action_{index}_condition", f"action_{index}_result"],
                    knowledge_ids=recipe["selected"], selected_knowledge_ids=recipe["selected"],
                    required_capabilities=recipe["required"], instruction_chain=recipe["instruction_chain"],
                    topology=recipe["topology"],
                    depends_on=[previous] if explicit_dependency and previous else [],
                    split_reason=(
                        "存在明确扫描顺序或前一Network结果依赖"
                        if explicit_dependency else "“另外/此外”引出不同输出或独立业务动作，不应合并成同一OR结果"
                    ),
                ))
                if explicit_dependency:
                    previous = key
        elif ordinary:
            recipe = _combinational_recipe("；".join(ordinary))
            networks.append(NetworkPlan(
                network_key="main_combined_logic", block_name=main_fc_name, title="组合条件与业务动作",
                comment="将无明确扫描依赖且可合并的条件、判断和动作集中表达；OR条件使用同一Network的并联支路。",
                purpose="普通组合逻辑、条件判断、计算、转换和无状态控制", main_branch=ordinary,
                parallel_branches=recipe["parallel_branches"], instructions=recipe["instructions"],
                variables=[f"condition_{index}" for index, _ in enumerate(ordinary, 1)] + ["result"],
                knowledge_ids=recipe["selected"], selected_knowledge_ids=recipe["selected"],
                required_capabilities=recipe["required"], instruction_chain=recipe["instruction_chain"],
                topology=recipe["topology"],
            ))

        if auxiliary_fbs:
            selected = ["call.fb_global_instance.v17", "network_text.title_comment.v17", "topology.series_contact_coil.v17"]
            networks.append(NetworkPlan(
                network_key="call_state_control", block_name=main_fc_name, title="调用辅助状态控制FB",
                comment="主FC直接调用FB_StateControl，并使用DB_StateControl作为其独立背景DB。",
                purpose="从主FC调度跨扫描状态逻辑", main_branch=["CALL FB_StateControl, DB_StateControl"],
                instructions=["Call"], variables=["FB_StateControl", "DB_StateControl"], knowledge_ids=selected,
                selected_knowledge_ids=selected,
                required_capabilities=["fb_call", "global_instance_db", "parameter_binding", "network_title", "network_comment", "topology.series"],
                instruction_chain=["enable call", "call FB_StateControl with DB_StateControl", "map outputs"],
                topology={"kind": "series", "description": "Power flow enables the FB call bound to its global instance DB."},
                depends_on=["state_control"], split_reason="FB调用依赖辅助FB及其背景DB先创建",
            ))

        order = [block.block_name for block in auxiliary_fbs]
        order.extend(db.db_name for db in instance_dbs)
        order.append(main_fc_name)
        interface_plan = {main_fc.block_name: main_fc.interface, **{block.block_name: block.interface for block in auxiliary_fbs}}
        return LadGenerationPlan(
            plan_id="ladplan_" + secrets.token_hex(8), conversation_id=conversation_id, target_device=target_device,
            requirements=requirements, main_fc=main_fc,
            main_fc_reason="主块选择FC，因为组合逻辑和调度本身不需要跨扫描Static状态；仅将确有状态的职责提取到辅助FB。",
            auxiliary_fbs=auxiliary_fbs, instance_dbs=instance_dbs, block_dependency_order=order,
            interface_plan=interface_plan, networks=networks, current_block=order[0] if order else main_fc_name,
            current_network=networks[0].network_key if networks else None,
            step_status={"planning": "planned", "artifact_creation": "planned", "network_generation": "planned"},
            created_at=stamp, updated_at=stamp,
        )
