from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import asdict
from typing import Any

from .models import BlueprintNode, LadGenerationPlan, NetworkPlan


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def network_semantic_payload(network: NetworkPlan) -> dict[str, Any]:
    return {
        "network_key": network.network_key,
        "block_name": network.block_name,
        "title": network.title,
        "comment": network.comment,
        "purpose": network.purpose,
        "instructions": list(network.instructions),
        "variables": list(network.variables),
        "required_capabilities": list(network.required_capabilities),
        "selected_knowledge_ids": list(network.selected_knowledge_ids),
        "instruction_chain": list(network.instruction_chain),
        "topology": network.topology,
        "depends_on": list(network.depends_on),
        "blueprint": asdict(network.blueprint) if network.blueprint else None,
        "renderer_id": network.renderer_id,
    }


def network_semantic_sha256(network: NetworkPlan) -> str:
    return _stable_hash(network_semantic_payload(network))


def plan_semantic_payload(plan: LadGenerationPlan) -> dict[str, Any]:
    return {
        "blueprint_schema_version": plan.blueprint_schema_version,
        "requested_network_count": plan.requested_network_count,
        "planned_network_count": plan.planned_network_count,
        "main_fc": {
            "block_name": plan.main_fc.block_name,
            "block_type": plan.main_fc.block_type,
            "responsibility": plan.main_fc.responsibility,
            "logic_scope": plan.main_fc.logic_scope,
            "interface": plan.main_fc.interface,
        },
        "auxiliary_fbs": [
            {
                "block_name": item.block_name,
                "block_type": item.block_type,
                "responsibility": item.responsibility,
                "logic_scope": item.logic_scope,
                "interface": item.interface,
                "state_features": item.state_features,
            }
            for item in plan.auxiliary_fbs
        ],
        "instance_dbs": [
            {
                "db_name": item.db_name,
                "fb_name": item.fb_name,
                "instance_name": item.instance_name,
                "responsibility": item.responsibility,
            }
            for item in plan.instance_dbs
        ],
        "block_dependency_order": list(plan.block_dependency_order),
        "interface_plan": plan.interface_plan,
        "instruction_pipeline": list(plan.instruction_pipeline),
        "networks": [network_semantic_payload(item) for item in plan.networks],
    }


def plan_semantic_sha256(plan: LadGenerationPlan) -> str:
    return _stable_hash(plan_semantic_payload(plan))


def blueprint_tree_lines(plan: LadGenerationPlan) -> list[str]:
    lines = [
        f"主程序：{plan.main_fc.block_name} ({plan.main_fc.block_type})",
        f"蓝图状态：{plan.blueprint_status}",
    ]
    for index, network in enumerate(plan.networks, 1):
        lines.append(f"程序段{index}：{network.title}")
        if network.comment:
            lines.append(f"└─ 注释：{network.comment}")
        if network.blueprint:
            _append_node(lines, network.blueprint, "", True)
    return lines


def _append_node(lines: list[str], node: BlueprintNode, prefix: str, last: bool) -> None:
    marker = "└─ " if last else "├─ "
    detail = f"：{node.label}" if node.label else ""
    lines.append(f"{prefix}{marker}{_node_display_name(node)}{detail}")
    child_prefix = prefix + ("   " if last else "│  ")
    for index, child in enumerate(node.children):
        _append_node(lines, child, child_prefix, index == len(node.children) - 1)
    for index, (name, value) in enumerate(node.operands.items()):
        operand_last = index == len(node.operands) - 1 and not node.children
        operand_marker = "└─ " if operand_last else "├─ "
        if isinstance(value, dict):
            rendered = ".".join(str(item) for item in value.get("path", [])) or str(value.get("value", ""))
        elif isinstance(value, list):
            rendered = ".".join(str(item) for item in value)
        else:
            rendered = str(value)
        lines.append(f"{child_prefix}{operand_marker}{_OPERAND_LABELS.get(name, name)}：{rendered}")


_NODE_LABELS = {
    "series": "串联指令链",
    "branch": "分支",
    "parallel": "并联条件",
    "fan_out": "并行执行分支",
    "parallel_merge": "并联汇合",
    "coil": "普通线圈",
    "set_coil": "置位线圈",
    "reset_coil": "复位线圈",
    "rising_edge": "上升沿检测",
    "falling_edge": "下降沿检测",
    "move": "MOVE",
    "math": "数值计算",
    "calc": "CALCULATE",
    "convert": "类型转换",
    "compare": "数值比较",
    "ton": "TON接通延时",
    "tof": "TOF断开延时",
    "tp": "TP脉冲定时",
    "composite": "复合控制结构",
}

_OPERAND_LABELS = {
    "operand": "操作数",
    "memory": "记忆位",
    "in": "输入",
    "out": "输出",
    "in1": "输入1",
    "in2": "输入2",
    "in3": "输入3",
    "instance": "实例",
    "pt": "预置时间",
    "timer_instance": "定时器实例",
}


def _node_display_name(node: BlueprintNode) -> str:
    if node.kind == "contact":
        return "常闭触点" if node.attributes.get("negated") else "常开触点"
    if node.kind == "compare" and node.attributes.get("operation"):
        return f"数值比较 ({node.attributes['operation']})"
    if node.kind in {"math", "calc"} and node.attributes.get("operation"):
        return f"{_NODE_LABELS[node.kind]} ({node.attributes['operation']})"
    return _NODE_LABELS.get(node.kind, node.kind)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _branches(root: BlueprintNode) -> list[list[BlueprintNode]]:
    if root.kind in {"parallel", "fan_out"}:
        return [list(child.children) if child.kind in {"series", "branch"} else [child] for child in root.children]
    if root.kind in {"series", "branch"}:
        return [list(root.children)]
    return [[root]]


def _semantic_layout(root: BlueprintNode) -> tuple[list[BlueprintNode], set[int], set[tuple[int, int]]]:
    if root.kind == "composite" and root.capability_id == "control.pbox_set_reset_ton_alarm":
        values = root.operands
        timer = values["timer_instance"]
        timer_path = list(timer.get("path", [])) if isinstance(timer, dict) else list(timer)
        make = lambda suffix, kind, capability, operands: BlueprintNode(
            node_id=f"{root.node_id}_{suffix}", kind=kind, label=suffix,
            capability_id=capability, operands=operands,
        )
        leaves = [
            make("failure_contact", "contact", "logic.contact_no", {"operand": values["failure_input"]}),
            make("failure_edge", "rising_edge", "logic.rising_edge_external_bit", {"memory": values["failure_memory"]}),
            make("fault_set", "set_coil", "output.set_coil", {"operand": values["fault_flag"]}),
            make("recovery_contact", "contact", "logic.contact_no", {"operand": values["recovery_input"]}),
            make("recovery_edge", "rising_edge", "logic.rising_edge_external_bit", {"memory": values["recovery_memory"]}),
            make("fault_reset", "reset_coil", "output.reset_coil", {"operand": values["fault_flag"]}),
            make("fault_contact", "contact", "logic.contact_no", {"operand": values["fault_flag"]}),
            make("delay", "ton", "timer.ton", {"pt": values["preset_time"], "instance": values["timer_instance"]}),
            make("timer_q", "contact", "logic.contact_no", {"operand": {"kind": "variable", "scope": "GlobalVariable", "path": [*timer_path, "Q"], "data_type": "Bool"}}),
            make("alarm", "coil", "output.coil", {"operand": values["output"]}),
        ]
        return leaves, {0, 3, 6, 8}, {(0, 1), (1, 2), (3, 4), (4, 5), (6, 7), (8, 9)}
    if root.kind == "series":
        merge_position = next((index for index, item in enumerate(root.children) if item.kind == "parallel_merge"), None)
        if merge_position is not None:
            if merge_position != 0:
                raise ValueError("parallel_merge is currently supported only as the first series element")
            merge = root.children[merge_position]
            branch_values = [list(item.children) if item.kind in {"series", "branch"} else [item] for item in merge.children]
            leaves: list[BlueprintNode] = []
            rail: set[int] = set()
            edges: set[tuple[int, int]] = set()
            endings: list[int] = []
            for branch in branch_values:
                start = len(leaves)
                if branch:
                    rail.add(start)
                    edges.update((start + index, start + index + 1) for index in range(len(branch) - 1))
                    endings.append(start + len(branch) - 1)
                    leaves.extend(branch)
            merge_index = len(leaves)
            leaves.append(merge)
            edges.update((ending, merge_index) for ending in endings)
            suffix = root.children[merge_position + 1:]
            for item in suffix:
                edges.add((len(leaves) - 1, len(leaves)))
                leaves.append(item)
            return leaves, rail, edges
    branches = _branches(root)
    leaves = [node for branch in branches for node in branch]
    rail: set[int] = set()
    edges: set[tuple[int, int]] = set()
    cursor = 0
    for branch in branches:
        if branch:
            rail.add(cursor)
            edges.update((cursor + index, cursor + index + 1) for index in range(len(branch) - 1))
        cursor += len(branch)
    return leaves, rail, edges


def _expected_part(node: BlueprintNode) -> str:
    if node.kind == "parallel_merge":
        return "O"
    if node.kind == "contact":
        return "Contact"
    if node.kind == "coil":
        return "Coil"
    if node.kind == "set_coil":
        return "SCoil"
    if node.kind == "reset_coil":
        return "RCoil"
    if node.kind == "rising_edge":
        return "PBox"
    if node.kind == "falling_edge":
        return "NBox"
    if node.kind == "move":
        return "Move"
    if node.kind == "convert":
        return "Convert"
    if node.kind == "compare":
        return str(node.attributes.get("operation") or "Ge")
    if node.kind in {"math", "calc"}:
        return str(node.attributes.get("operation") or ("Calc" if node.kind == "calc" else "Mul"))
    if node.kind in {"ton", "tof", "tp"}:
        return node.kind.upper()
    if node.kind in {"fc_call", "fb_call"}:
        return "Call"
    raise ValueError(f"unsupported blueprint node kind: {node.kind}")


def _operand_signature(value: Any, default_type: str = "Bool") -> tuple:
    if isinstance(value, list):
        return ("variable", "GlobalVariable", tuple(str(item) for item in value))
    if isinstance(value, dict):
        if value.get("kind", "variable") == "constant":
            return ("constant", str(value.get("data_type") or default_type), str(value.get("value")))
        return (
            "variable", str(value.get("scope") or "GlobalVariable"),
            tuple(str(item) for item in value.get("path", [])),
        )
    return ("constant", default_type, str(value))


def _expected_bindings(leaves: list[BlueprintNode]) -> dict[tuple[int, str], tuple]:
    result: dict[tuple[int, str], tuple] = {}
    for index, node in enumerate(leaves):
        values = node.operands
        if node.kind in {"contact", "coil", "set_coil", "reset_coil"}:
            result[(index, "operand")] = _operand_signature(values["operand"], "Bool")
        elif node.kind in {"rising_edge", "falling_edge"}:
            result[(index, "bit")] = _operand_signature(values["memory"], "Bool")
        elif node.kind == "move":
            data_type = str(node.attributes.get("data_type") or "Real")
            result[(index, "in")] = _operand_signature(values["in"], data_type)
            result[(index, "out1")] = _operand_signature(values["out"], data_type)
        elif node.kind in {"math", "calc"}:
            data_type = str(node.attributes.get("data_type") or "Real")
            for name, value in values.items():
                result[(index, "out" if name == "out" else name)] = _operand_signature(value, data_type)
        elif node.kind == "convert":
            result[(index, "in")] = _operand_signature(values["in"], str(node.attributes.get("source_type") or "Real"))
            result[(index, "out")] = _operand_signature(values["out"], str(node.attributes.get("target_type") or "Int"))
        elif node.kind == "compare":
            data_type = str(node.attributes.get("data_type") or "Real")
            result[(index, "in1")] = _operand_signature(values["in1"], data_type)
            result[(index, "in2")] = _operand_signature(values["in2"], data_type)
        elif node.kind == "ton":
            result[(index, "PT")] = ("constant", "", str(values["pt"]))
        elif node.kind in {"fc_call", "fb_call"}:
            for name, parameter in (values.get("parameters") or {}).items():
                result[(index, str(name))] = _operand_signature(
                    parameter["operand"], str(parameter.get("data_type") or parameter.get("type") or "Bool")
                )
    return result


def validate_compile_unit_semantics(network: NetworkPlan, xml: str) -> list[dict[str, Any]]:
    """Compare generated XML with the frozen design, ignoring expression-only IDs."""
    if network.blueprint is None:
        return [{"code": "BLUEPRINT_MISSING", "message": "Network has no structured frozen blueprint"}]
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [{"code": "XML_VALIDATION_FAILED", "message": str(exc)}]
    try:
        leaves, expected_rail, expected_edges = _semantic_layout(network.blueprint)
    except ValueError as exc:
        return [{"code": "BLUEPRINT_TOPOLOGY_UNSUPPORTED", "message": str(exc)}]
    expected_parts = [_expected_part(node) for node in leaves]
    part_elements = [item for item in root.iter() if _local(item.tag) in {"Part", "Call"}]
    actual_parts = ["Call" if _local(item.tag) == "Call" else str(item.get("Name", "")) for item in part_elements]
    issues: list[dict[str, Any]] = []
    if actual_parts != expected_parts:
        issues.append({
            "code": "FROZEN_INSTRUCTION_MISMATCH",
            "message": f"XML instructions {actual_parts} do not match frozen blueprint {expected_parts}",
        })
        return issues
    expected_negated = [bool(node.attributes.get("negated")) for node in leaves if node.kind == "contact"]
    actual_negated = [
        any(_local(child.tag) == "Negated" and child.get("Name") == "operand" for child in list(item))
        for item in part_elements if item.get("Name") == "Contact"
    ]
    if actual_negated != expected_negated:
        issues.append({
            "code": "FROZEN_CONTACT_SEMANTICS_MISMATCH",
            "message": f"XML normally-closed flags {actual_negated} do not match blueprint {expected_negated}",
        })

    part_index = {str(item.get("UId")): index for index, item in enumerate(part_elements)}
    access_values: dict[str, tuple] = {}
    for access in (item for item in root.iter() if _local(item.tag) == "Access" and item.get("UId")):
        uid = str(access.get("UId"))
        scope = str(access.get("Scope", ""))
        if scope in {"GlobalVariable", "LocalVariable"}:
            path = tuple(str(item.get("Name", "")) for item in access.iter() if _local(item.tag) == "Component")
            access_values[uid] = ("variable", scope, path)
        elif scope in {"LiteralConstant", "TypedConstant"}:
            data_type = next((item.text or "" for item in access.iter() if _local(item.tag) == "ConstantType"), "")
            value = next((item.text or "" for item in access.iter() if _local(item.tag) == "ConstantValue"), "")
            access_values[uid] = ("constant", data_type, value)
    input_ports = {
        "Contact": {"in"}, "Coil": {"in"}, "SCoil": {"in"}, "RCoil": {"in"},
        "PBox": {"in"}, "NBox": {"in"}, "Move": {"en"}, "Mul": {"en"},
        "Div": {"en"}, "Calc": {"en"}, "Convert": {"en"},
        "Ge": {"pre"}, "Gt": {"pre"}, "Le": {"pre"}, "Lt": {"pre"}, "Eq": {"pre"}, "Ne": {"pre"},
        "O": {f"in{index}" for index in range(1, 65)},
        "TON": {"IN"},
        "Call": {"en"},
    }
    output_ports = {
        "Contact": {"out"}, "PBox": {"out"}, "NBox": {"out"},
        "Move": {"eno"}, "Mul": {"eno"}, "Div": {"eno"}, "Calc": {"eno"}, "Convert": {"eno"},
        "Ge": {"out"}, "Gt": {"out"}, "Le": {"out"}, "Lt": {"out"}, "Eq": {"out"}, "Ne": {"out"},
        "O": {"out"},
        "Call": {"eno"},
    }
    for item in part_elements:
        if _local(item.tag) != "Call":
            continue
        for parameter in (child for child in item.iter() if _local(child.tag) == "Parameter"):
            name, section = str(parameter.get("Name", "")), str(parameter.get("Section", ""))
            if section in {"Input", "InOut"}:
                input_ports["Call"].add(name)
            if section in {"Output", "InOut"}:
                output_ports["Call"].add(name)
    rail: set[int] = set()
    edges: set[tuple[int, int]] = set()
    actual_bindings: dict[tuple[int, str], tuple] = {}
    for wire in (item for item in root.iter() if _local(item.tag) == "Wire"):
        has_rail = any(_local(child.tag) == "Powerrail" for child in list(wire))
        connectors = [child for child in list(wire) if _local(child.tag) == "NameCon" and str(child.get("UId")) in part_index]
        identifiers = [str(child.get("UId")) for child in list(wire) if _local(child.tag) == "IdentCon"]
        if len(identifiers) == 1 and identifiers[0] in access_values:
            for connector in connectors:
                actual_bindings[(part_index[str(connector.get("UId"))], str(connector.get("Name", "")))] = access_values[identifiers[0]]
        inputs, outputs = [], []
        for connector in connectors:
            index = part_index[str(connector.get("UId"))]
            part_name = actual_parts[index]
            port = str(connector.get("Name", ""))
            if port in input_ports.get(part_name, set()):
                inputs.append(index)
            if port in output_ports.get(part_name, set()):
                outputs.append(index)
        if has_rail:
            rail.update(inputs)
        for source in outputs:
            for target in inputs:
                if source != target:
                    edges.add((source, target))
    if rail != expected_rail or edges != expected_edges:
        issues.append({
            "code": "FROZEN_TOPOLOGY_MISMATCH",
            "message": f"XML power topology rail={sorted(rail)}, edges={sorted(edges)} does not match blueprint rail={sorted(expected_rail)}, edges={sorted(expected_edges)}",
        })
    expected_bindings = _expected_bindings(leaves)
    if actual_bindings != expected_bindings:
        issues.append({
            "code": "FROZEN_OPERAND_MISMATCH",
            "message": f"XML operand bindings do not match frozen business operands; expected={expected_bindings}, actual={actual_bindings}",
        })
    if network.blueprint.kind == "composite" and network.blueprint.capability_id == "control.pbox_set_reset_ton_alarm":
        timer_value = network.blueprint.operands["timer_instance"]
        expected_timer = tuple(timer_value.get("path", [])) if isinstance(timer_value, dict) else tuple(timer_value)
        timer_part = next((item for item in part_elements if item.get("Name") == "TON"), None)
        instance = next((item for item in timer_part.iter() if _local(item.tag) == "Instance"), None) if timer_part is not None else None
        actual_timer = tuple(str(item.get("Name", "")) for item in instance.iter() if _local(item.tag) == "Component") if instance is not None else ()
        if actual_timer != expected_timer:
            issues.append({
                "code": "FROZEN_STATE_STORAGE_MISMATCH",
                "message": f"TON instance {actual_timer} does not match frozen state storage {expected_timer}",
            })
    if network.blueprint.kind in {"fc_call", "fb_call"}:
        values = network.blueprint.operands
        call = next((item for item in part_elements if _local(item.tag) == "Call"), None)
        info = next((item for item in call.iter() if _local(item.tag) == "CallInfo"), None) if call is not None else None
        actual_block = str(info.get("Name", "")) if info is not None else ""
        actual_type = str(info.get("BlockType", "")) if info is not None else ""
        expected_type = "FB" if network.blueprint.kind == "fb_call" else "FC"
        if actual_block != str(values.get("block", "")) or actual_type != expected_type:
            issues.append({
                "code": "FROZEN_CALL_TARGET_MISMATCH",
                "message": f"Call target {actual_type}:{actual_block} does not match frozen {expected_type}:{values.get('block')}",
            })
        if expected_type == "FB":
            instance = next((item for item in info.iter() if _local(item.tag) == "Instance"), None) if info is not None else None
            actual_instance = tuple(str(item.get("Name", "")) for item in instance.iter() if _local(item.tag) == "Component") if instance is not None else ()
            if actual_instance != (str(values.get("instance", "")),):
                issues.append({
                    "code": "FROZEN_STATE_STORAGE_MISMATCH",
                    "message": f"Call instance {actual_instance} does not match frozen background DB {values.get('instance')}",
                })
    return issues
