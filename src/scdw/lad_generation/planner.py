from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from .models import AuxiliaryFbPlan, BlockPlan, BlueprintNode, InstanceDbPlan, LadGenerationPlan, NetworkPlan


_STATE_FEATURES = {
    "timer": ("定时", "延时", "TON", "TOF", "TP", "timer"),
    "memory": ("记忆", "保持", "锁存", "跨扫描", "Static", "memory", "latch"),
    "counter": ("累计", "计数", "counter"),
    "runtime": ("累计运行时间", "运行时间累计", "runtime"),
    "sequence": ("状态机", "顺序步骤", "步序", "sequence", "state machine"),
}
_DEPENDENCY_MARKERS = ("先", "然后", "再", "之后", "依赖", "结果用于", "previous result")
_SEPARATE_ACTION_MARKERS = ("另外", "另有", "同时还", "此外")
_NUMBERED_NETWORK = re.compile(r"程序段\s*(\d+)\s*[：:]")
_QUOTED_PATH = re.compile(r"[“\"]([^”\"]+)[”\"]\s*[.．]\s*[“\"]([^”\"]+)[”\"]")
_OUTPUT_TAG = re.compile(r"%Q(?:W|D|B)?[\d.]+\s*[“\"]([^”\"]+)[”\"]", re.IGNORECASE)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _variable(path: list[str], data_type: str) -> dict:
    return {"kind": "variable", "scope": "GlobalVariable", "path": path, "data_type": data_type}


def _constant(value: str | int | float, data_type: str) -> dict:
    return {"kind": "constant", "value": value, "data_type": data_type}


def _leaf(
    node_id: str,
    kind: str,
    label: str,
    capability_id: str,
    operands: dict,
    *,
    attributes: dict | None = None,
    knowledge_ids: list[str] | None = None,
) -> BlueprintNode:
    return BlueprintNode(
        node_id=node_id,
        kind=kind,
        label=label,
        capability_id=capability_id,
        operands=operands,
        attributes=attributes or {},
        knowledge_ids=knowledge_ids or [],
        renderer_id="blueprint_network_v17",
    )


def _container(node_id: str, kind: str, label: str, children: list[BlueprintNode]) -> BlueprintNode:
    capability_id = "topology.parallel" if kind in {"parallel", "fan_out", "parallel_merge"} else "topology.series"
    return BlueprintNode(
        node_id=node_id,
        kind=kind,
        label=label,
        capability_id=capability_id,
        knowledge_ids=["topology.fan_out.v17"] if capability_id == "topology.parallel" else ["topology.series_contact_coil.v17"],
        renderer_id="blueprint_network_v17",
        children=children,
    )


def _paths(text: str) -> list[list[str]]:
    return [[first, second] for first, second in _QUOTED_PATH.findall(text)]


def _path_matching(paths: list[list[str]], *tokens: str, fallback: int = 0) -> list[str]:
    for path in paths:
        if all(token in path[-1] for token in tokens):
            return path
    if paths:
        return paths[min(fallback, len(paths) - 1)]
    return ["未绑定变量"]


def _output_path(text: str, fallback: str = "输出") -> list[str]:
    match = _OUTPUT_TAG.search(text)
    return [match.group(1)] if match else [fallback]


def _blueprint_for_text(text: str, key: str) -> BlueprintNode:
    """Build an operand-complete deterministic draft for common V17 LAD idioms."""
    lowered = text.lower()
    paths = _paths(text)
    if any(token in lowered for token in ("p_trig", "pbox", "上升沿")) and "ton" in lowered and "置位" in text and "复位" in text:
        failure_input = _path_matching(paths, "读取失败", fallback=0)
        failure_memory = _path_matching(paths, "脉冲1", fallback=1)
        recovery_input = _path_matching(paths, "读取成功", fallback=3)
        recovery_memory = _path_matching(paths, "脉冲2", fallback=4)
        fault_flag = _path_matching(paths, "故障标志", fallback=2)
        timer_instance = _path_matching(paths, "定时器", fallback=max(0, len(paths) - 2))
        output = _path_matching(paths, "变频器通讯故障", fallback=max(0, len(paths) - 1))
        preset = next(iter(re.findall(r"T#[A-Za-z0-9_.]+", text)), "T#3m")
        return BlueprintNode(
            node_id=f"{key}_composite", kind="composite", label="失败沿置位、成功沿复位、TON延时报警",
            capability_id="control.pbox_set_reset_ton_alarm",
            operands={
                "failure_input": _variable(failure_input, "Bool"),
                "failure_memory": _variable(failure_memory, "Bool"),
                "recovery_input": _variable(recovery_input, "Bool"),
                "recovery_memory": _variable(recovery_memory, "Bool"),
                "fault_flag": _variable(fault_flag, "Bool"),
                "timer_instance": _variable(timer_instance, "TON_Time"),
                "preset_time": preset,
                "output": _variable(output, "Bool"),
            },
            knowledge_ids=["instruction.pbox_set_reset_ton_coil.v17"],
            renderer_id="pbox_set_reset_ton_coil",
        )
    if "calculate" in lowered and "conv" in lowered and "move" in lowered:
        enable = _path_matching(paths, "运行", fallback=0)
        source = _path_matching(paths, "控制输出", fallback=1)
        calculated = _path_matching(paths, "控制输出值", fallback=max(0, len(paths) - 2))
        converted = _path_matching(paths, "过渡", fallback=max(0, len(paths) - 1))
        output = _output_path(text, "模拟量输出")
        clear_branch = _container(f"{key}_clear", "branch", "停止时清零", [
            _leaf(f"{key}_clear_contact", "contact", "停止条件（常闭）", "logic.contact_nc", {"operand": _variable(enable, "Bool")}, attributes={"negated": True}, knowledge_ids=["topology.series_contact_coil.v17"]),
            _leaf(f"{key}_clear_move", "move", "写入0.0", "data.move", {"in": _constant("0.0", "Real"), "out": _variable(source, "Real")}, attributes={"data_type": "Real"}, knowledge_ids=["instruction.move.v17"]),
        ])
        calculate_branch = _container(f"{key}_calculate", "branch", "比例换算并输出", [
            _leaf(f"{key}_calc", "calc", "OUT=(IN1/IN2)*IN3", "math.numeric", {
                "in1": _variable(source, "Real"), "in2": _constant("100.0", "Real"),
                "in3": _constant("27648.0", "Real"), "out": _variable(calculated, "Real"),
            }, attributes={"operation": "Calc", "equation": "(IN1/IN2)*IN3", "data_type": "Real"}, knowledge_ids=["instruction.math_cascade.v17"]),
            _leaf(f"{key}_convert", "convert", "REAL转INT", "data.convert", {
                "in": _variable(calculated, "Real"), "out": _variable(converted, "Int"),
            }, attributes={"source_type": "Real", "target_type": "Int"}, knowledge_ids=["instruction.convert_divide.v17"]),
            _leaf(f"{key}_output_move", "move", "写入模拟量输出", "data.move", {
                "in": _variable(converted, "Int"), "out": _variable(output, "Int"),
            }, attributes={"data_type": "Int"}, knowledge_ids=["instruction.move.v17"]),
        ])
        return _container(f"{key}_root", "parallel", "停止清零与持续比例计算", [clear_branch, calculate_branch])
    if "move" in lowered and "mul" in lowered and "常闭" in text:
        run = _path_matching(paths, "运行", fallback=0)
        fault = _path_matching(paths, "故障", fallback=1)
        manual = _path_matching(paths, "手动", fallback=2)
        automatic = _path_matching(paths, "自动", "输出", fallback=3)
        manual_frequency = _path_matching(paths, "手动", "频率", fallback=4)
        control = _path_matching(paths, "控制输出", fallback=max(0, len(paths) - 1))
        output = _output_path(text, "运行输出")
        run_branch = _container(f"{key}_run", "branch", "运行许可", [
            _leaf(f"{key}_run_no", "contact", "运行请求（常开）", "logic.contact_no", {"operand": _variable(run, "Bool")}, knowledge_ids=["topology.series_contact_coil.v17"]),
            _leaf(f"{key}_fault_nc", "contact", "故障反馈（常闭）", "logic.contact_nc", {"operand": _variable(fault, "Bool")}, attributes={"negated": True}, knowledge_ids=["topology.series_contact_coil.v17"]),
            _leaf(f"{key}_run_coil", "coil", "风机运行", "output.coil", {"operand": _variable(output, "Bool")}, knowledge_ids=["topology.series_contact_coil.v17"]),
        ])
        auto_branch = _container(f"{key}_auto", "branch", "自动模式输出", [
            _leaf(f"{key}_manual_nc", "contact", "手动模式（常闭）", "logic.contact_nc", {"operand": _variable(manual, "Bool")}, attributes={"negated": True}, knowledge_ids=["topology.series_contact_coil.v17"]),
            _leaf(f"{key}_auto_move", "move", "自动值传送", "data.move", {"in": _variable(automatic, "Real"), "out": _variable(control, "Real")}, attributes={"data_type": "Real"}, knowledge_ids=["instruction.move.v17"]),
        ])
        manual_branch = _container(f"{key}_manual", "branch", "手动模式输出", [
            _leaf(f"{key}_manual_no", "contact", "手动模式（常开）", "logic.contact_no", {"operand": _variable(manual, "Bool")}, knowledge_ids=["topology.series_contact_coil.v17"]),
            _leaf(f"{key}_manual_mul", "math", "手动频率乘2.0", "math.numeric", {
                "in1": _variable(manual_frequency, "Real"), "in2": _constant("2.0", "Real"), "out": _variable(control, "Real"),
            }, attributes={"operation": "Mul", "data_type": "Real"}, knowledge_ids=["instruction.math_cascade.v17"]),
        ])
        return _container(f"{key}_root", "parallel", "运行、自动和手动输出分支", [run_branch, auto_branch, manual_branch])
    if any(token in text for token in (">=", "大于等于", "不小于")):
        source = paths[0] if paths else ["比较输入"]
        output = _output_path(text, "比较结果")
        constant_match = re.search(r"常数\s*([+-]?\d+(?:\.\d+)?)", text)
        if constant_match is None:
            constant_match = re.search(r"(?:>=|大于或?等于|不小于)\D{0,12}([+-]?\d+(?:\.\d+)?)", text)
        threshold = constant_match.group(1) if constant_match else "0.0"
        return _container(f"{key}_root", "series", "比较并驱动输出", [
            _leaf(f"{key}_compare", "compare", "REAL大于等于", "compare.numeric", {
                "in1": _variable(source, "Real"), "in2": _constant(threshold, "Real"),
            }, attributes={"operation": "Ge", "data_type": "Real"}, knowledge_ids=["instruction.compare_ge_real_coil.v17"]),
            _leaf(f"{key}_coil", "coil", "比较结果线圈", "output.coil", {"operand": _variable(output, "Bool")}, knowledge_ids=["topology.series_contact_coil.v17"]),
        ])
    condition = paths[0] if paths else ["条件"]
    return _container(f"{key}_root", "series", "条件到输出", [
        _leaf(f"{key}_contact", "contact", "条件", "logic.contact_no", {"operand": _variable(condition, "Bool")}, knowledge_ids=["topology.series_contact_coil.v17"]),
        _leaf(f"{key}_coil", "coil", "输出", "output.coil", {"operand": _variable(_output_path(text), "Bool")}, knowledge_ids=["topology.series_contact_coil.v17"]),
    ])


def _combinational_recipe(text: str) -> dict:
    lowered = text.lower()
    logical_text = text.replace("大于或等于", "大于等于").replace("小于或等于", "小于等于")
    has_or = any(token in logical_text for token in ("或", "任一", "OR", "or"))
    has_edge = any(token.lower() in lowered for token in ("p_trig", "pbox", "上升沿", "正跳变"))
    has_comparison = any(token in text for token in (">", "<", "大于", "小于", "不大于", "不小于", "比较"))
    has_data_fan_out = "move" in lowered and any(token in lowered for token in ("mul", "calculate", "calc", "conv"))
    has_vfd_fault = has_edge and "ton" in lowered and any(token in text for token in ("置位", "SCoil", "S线圈")) and any(
        token in text for token in ("复位", "RCoil", "R线圈")
    )
    has_ge_real = any(token in text for token in (">=", "大于等于", "不小于")) and any(
        token in lowered for token in ("real", "1.", "0.")
    )
    if has_vfd_fault:
        topology_id = "instruction.pbox_set_reset_ton_coil.v17"
    elif has_ge_real and not has_or:
        topology_id = "instruction.compare_ge_real_coil.v17"
    elif has_data_fan_out:
        topology_id = "topology.fan_out.v17"
    elif has_or and has_edge:
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
    topology_kind = "fan_out" if has_data_fan_out else ("parallel_merge" if has_or else "series")
    if topology_id == "instruction.pbox_set_reset_ton_coil.v17":
        instructions = ["Contact", "PBox", "SCoil", "RCoil", "TON", "Coil"]
        required = ["fc_shell", "pbox_set_reset_ton_coil", "network_title", "network_comment", "topology.parallel"]
        instruction_chain = ["detect failure rising edge", "set fault flag", "detect recovery rising edge", "reset fault flag", "delay with TON", "drive alarm coil"]
        topology_kind = "parallel"
        branches = ["failure edge -> set", "recovery edge -> reset", "fault flag -> TON", "TON.Q -> alarm"]
    elif topology_id == "instruction.compare_ge_real_coil.v17":
        instructions = ["Ge(Real)", "Coil"]
        required = ["fc_shell", "compare.ge", "coil", "network_title", "network_comment", "topology.series"]
        instruction_chain = ["compare Real input against Real threshold", "drive Coil"]
        topology_kind = "series"
    elif topology_id == "topology.contact_or_coil.v17":
        instructions = ["Contact", "O", "Coil"]
        required.append("multi_contact_or_coil")
        instruction_chain = ["evaluate Contact branches", "merge each Contact.out through O.inN", "drive Coil"]
    elif topology_id == "topology.contact_or_pbox_scoil.v17":
        instructions = ["Contact", "O", "PBox", "SCoil"]
        required = ["fc_shell", "multi_contact_or_pbox_scoil", "network_title", "network_comment"]
        instruction_chain = ["evaluate Contact branches", "merge each Contact.out through O.inN", "detect rising edge with PBox bit", "drive SCoil"]
    topology_requirements = {
        "series": ["topology.series"],
        "parallel": ["topology.parallel"],
        "fan_out": ["topology.fan_out"],
        "parallel_merge": ["topology.parallel", "topology.merge"],
    }[topology_kind]
    for capability in topology_requirements:
        if capability not in required:
            required.append(capability)
    selected = [
        "shell.fc_block.v17", topology_id, "topology.series_contact_coil.v17",
        "network_text.title_comment.v17",
    ]
    if "move" in lowered:
        selected.append("instruction.move.v17")
        required.append("move")
    if any(token in lowered for token in ("mul", "calculate", "calc")):
        selected.append("instruction.math_cascade.v17")
        required.extend([item for item in ("mul", "calc") if item not in required])
    if any(token in lowered for token in ("conv", "convert")):
        selected.append("instruction.convert_divide.v17")
        required.append("convert")
    if any(token in text for token in ("DB", ".”", "\".")):
        selected.append("access.multilevel_db_member.v17")
    return {
        "selected": list(dict.fromkeys(selected)),
        "parallel_branches": [[item] for item in branches],
        "instructions": instructions,
        "required": required,
        "instruction_chain": instruction_chain,
        "topology": {
            "kind": topology_kind,
            "description": {
                "series": "Conditions form one series path to the action.",
                "parallel": "Independent powerrail branches preserve separate set, reset, timer and output actions.",
                "fan_out": "The powerrail feeds multiple explicitly planned action branches.",
                "parallel_merge": "Alternative conditions merge before one action.",
            }[topology_kind],
        },
    }


class LadPlanner:
    """Small deterministic planner; it never invents SimaticML structures."""

    def plan(self, requirements: str, *, conversation_id: str, target_device: str, main_fc_name: str = "FC_MainControl") -> LadGenerationPlan:
        if not requirements.strip():
            raise ValueError("requirements must not be empty")
        numbered = list(_NUMBERED_NETWORK.finditer(requirements))
        requested_network_count = max((int(item.group(1)) for item in numbered), default=None)
        if numbered:
            numbered_sections = []
            for index, match in enumerate(numbered):
                end = numbered[index + 1].start() if index + 1 < len(numbered) else len(requirements)
                numbered_sections.append(requirements[match.end():end].strip(" -\t\n。"))
            sentences = [item for item in numbered_sections if item]
        else:
            sentences = [part.strip(" -\t") for part in re.split(r"[。；;\n]+", requirements) if part.strip(" -\t")]
        lowered = requirements.lower()
        features = [name for name, words in _STATE_FEATURES.items() if any(word.lower() in lowered for word in words)]
        external_state_binding = any(token in requirements for token in (
            "定时器变量使用", "计数器变量使用", "存储变量使用", "全局DB", "%DB",
        ))
        if external_state_binding:
            # The requested global DB already owns cross-scan state. Keep the
            # main logic in FC instead of inventing an extra helper FB/DB.
            features = []
        state_sentences = [item for item in sentences if any(word.lower() in item.lower() for words in _STATE_FEATURES.values() for word in words)]
        if external_state_binding:
            state_sentences = []
        # Explicit numbered program segments remain main-FC boundaries even if
        # their state is implemented by a helper FB behind that boundary.
        ordinary = list(sentences) if numbered else [item for item in sentences if item not in state_sentences]
        if not numbered and "instruction.pbox_set_reset_ton_coil.v17" in _combinational_recipe(requirements)["selected"]:
            # Set/reset, timer and alarm branches form one reviewed functional
            # subgraph; words such as “then” do not imply scan separation here.
            ordinary = [requirements]
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
                blueprint=_blueprint_for_text("；".join(state_sentences), "state_control"),
                renderer_id="blueprint_network_v17",
            ))

        explicit_dependency = len(ordinary) > 1 and any(marker.lower() in lowered for marker in _DEPENDENCY_MARKERS)
        separate_business_actions = len(ordinary) > 1 and any(
            sentence.lstrip().startswith(_SEPARATE_ACTION_MARKERS) for sentence in ordinary[1:]
        )
        if numbered and ordinary:
            previous: str | None = None
            for index, sentence in enumerate(ordinary, 1):
                recipe = _combinational_recipe(sentence)
                key = f"requested_network_{index}"
                networks.append(NetworkPlan(
                    network_key=key, block_name=main_fc_name, title=f"程序段 {index}",
                    comment=sentence, purpose=sentence, main_branch=[sentence],
                    parallel_branches=recipe["parallel_branches"], instructions=recipe["instructions"],
                    variables=[f"network_{index}_input", f"network_{index}_output"],
                    knowledge_ids=recipe["selected"], selected_knowledge_ids=recipe["selected"],
                    required_capabilities=recipe["required"], instruction_chain=recipe["instruction_chain"],
                    topology=recipe["topology"], depends_on=[previous] if previous else [],
                    split_reason="用户明确指定程序段边界（硬约束）",
                    blueprint=_blueprint_for_text(sentence, key), renderer_id="blueprint_network_v17",
                ))
                previous = key
        elif ordinary and (explicit_dependency or separate_business_actions):
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
                    blueprint=_blueprint_for_text(sentence, key), renderer_id="blueprint_network_v17",
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
                blueprint=_blueprint_for_text("；".join(ordinary), "main_combined_logic"), renderer_id="blueprint_network_v17",
            ))

        if auxiliary_fbs and not numbered:
            selected = ["call.fb_global_instance.v17", "network_text.title_comment.v17", "topology.series_contact_coil.v17"]
            call_blueprint = BlueprintNode(
                node_id="call_state_control_fb",
                kind="fb_call",
                label="FB_StateControl与背景DB",
                capability_id="call.fb_instance_db",
                operands={
                    "block": "FB_StateControl",
                    "instance": "DB_StateControl",
                    "parameters": {},
                },
                knowledge_ids=["call.fb_global_instance.v17"],
                renderer_id="block_call_v17",
            )
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
                blueprint=call_blueprint, renderer_id="block_call_v17",
            ))

        order = [block.block_name for block in auxiliary_fbs]
        order.extend(db.db_name for db in instance_dbs)
        order.append(main_fc_name)
        interface_plan = {main_fc.block_name: main_fc.interface, **{block.block_name: block.interface for block in auxiliary_fbs}}
        planned_network_count = sum(1 for item in networks if item.block_name == main_fc_name)
        for network in networks:
            if network.blueprint and network.blueprint.kind == "composite":
                network.renderer_id = network.blueprint.renderer_id
        return LadGenerationPlan(
            plan_id="ladplan_" + secrets.token_hex(8), conversation_id=conversation_id, target_device=target_device,
            requirements=requirements, main_fc=main_fc,
            main_fc_reason="主块选择FC，因为组合逻辑和调度本身不需要跨扫描Static状态；仅将确有状态的职责提取到辅助FB。",
            auxiliary_fbs=auxiliary_fbs, instance_dbs=instance_dbs, block_dependency_order=order,
            interface_plan=interface_plan, networks=networks, current_block=order[0] if order else main_fc_name,
            current_network=networks[0].network_key if networks else None,
            requested_network_count=requested_network_count,
            planned_network_count=planned_network_count,
            instruction_pipeline=[item.network_key for item in networks],
            step_status={"planning": "planned", "artifact_creation": "planned", "network_generation": "planned"},
            created_at=stamp, updated_at=stamp,
        )
