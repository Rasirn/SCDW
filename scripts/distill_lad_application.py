"""Publish reviewed, compact V17 LAD subgraphs from raw application exports."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


FLGNET_NS = "http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4"
ET.register_namespace("", FLGNET_NS)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


SELECTIONS: list[dict[str, Any]] = [
    {
        "id": "topology.contact_or_coil.v17",
        "source": "烧嘴控制块.xml", "network": 16, "path": "topology/contact_or_coil.xml",
        "title": "多常开触点经O功能框汇合到普通线圈",
        "description": "TIA V17导出的完整Network；每个Contact.out使用独立Wire连接O.inN，Card等于输入数量，O.out驱动Coil。",
        "role": "topology_pattern", "status": "golden",
        "intent": ["多个常开触点OR", "O功能框", "普通线圈", "并联汇合"],
        "not_for": ["把多个Contact.out直接放入同一Wire", "SCoil置位", "PBox上升沿"],
        "provides": ["contact", "coil", "or_box", "or.cardinality", "parallel_branches", "topology.parallel", "topology.merge", "multi_contact_or_coil", "renderer.contact_or_coil"],
        "requires": ["Card必须等于Contact数量", "每个Contact.out分别连接O.in1..inN"],
        "replace": ["Contact变量Component路径", "输出Component路径", "Card", "UId"],
        "preserve": ["单一Powerrail分叉", "独立Contact.out到O.inN Wire", "O.out到Coil.in", "Access-Part-Wire完整子图"],
        "topology": ["parallel_powerrail", "merge"],
        "generation_mode": "knowledge_renderer_required",
        "renderer": {"kind": "contact_or_coil", "bindings": ["contacts", "output"]},
        "construction_rules": [
            "每个Contact.out必须在独立Wire中只连接一个O.inN",
            "禁止将多个输出端放进同一Wire",
            "O的Card必须与contacts数量相同"
        ],
    },
    {
        "id": "topology.contact_or_pbox_scoil.v17",
        "source": "烧嘴控制块.xml", "network": 17, "path": "topology/contact_or_pbox_scoil.xml",
        "title": "多常开触点经O汇合、PBox上升沿到SCoil",
        "description": "TIA V17烧嘴报警导出Network，完整展示Contact并联、O.inN汇合、PBox in/bit/out及SCoil连接。",
        "role": "topology_pattern", "status": "golden",
        "intent": ["烧嘴报警", "多个常开触点OR", "PBox上升沿", "SCoil置位"],
        "not_for": ["P_TRIG或R_TRIG猜测名称", "多个Contact.out共用一条Wire", "省略PBox.bit"],
        "provides": ["contact", "set_coil", "or_box", "or.cardinality", "parallel_branches", "pbox", "edge.rising", "edge.memory_bit", "edge.ports.in_bit_out", "topology.parallel", "topology.merge", "multi_contact_or_pbox_scoil", "renderer.contact_or_pbox_scoil"],
        "requires": ["Card必须等于Contact数量", "每个Contact.out分别连接O.in1..inN", "PBox.bit绑定Bool存储变量"],
        "replace": ["Contact变量Component路径", "PBox bit路径", "SCoil输出路径", "Card", "UId"],
        "preserve": ["单一Powerrail分叉", "独立Contact.out到O.inN Wire", "O.out到PBox.in", "PBox.bit", "PBox.out到SCoil.in", "Access-Part-Wire完整子图"],
        "topology": ["parallel_powerrail", "merge", "series"],
        "generation_mode": "knowledge_renderer_required",
        "renderer": {"kind": "contact_or_pbox_scoil", "bindings": ["contacts", "edge_memory", "output"]},
        "construction_rules": [
            "每个Contact.out必须在独立Wire中只连接一个O.inN",
            "禁止将多个输出端放进同一Wire",
            "O的Card必须与contacts数量相同",
            "PBox端口名固定为in、bit、out"
        ],
    },
    {
        "id": "instruction.pbox_edge_memory.v17",
        "source": "报警.xml", "network": 1, "path": "instructions/pbox_edge_memory.xml",
        "title": "单支路PBox上升沿检测及bit存储连接",
        "description": "从TIA V17报警Network原样提取；其中多个Contact→PBox链是共享Powerrail的独立支路，不演示多个Contact.out汇合到一个PBox。",
        "role": "instruction_pattern", "status": "golden",
        "intent": ["PBox", "上升沿", "脉冲存储", "报警置位复位"],
        "not_for": ["RBox下降沿", "把PBox改写成P_TRIG或R_TRIG", "省略bit存储连接", "多个Contact经OR汇合到一个PBox；应选topology.contact_or_pbox_scoil.v17"],
        "provides": ["pbox", "edge.rising", "edge.memory_bit", "edge.ports.in_bit_out", "topology.series", "set_coil", "reset_coil"],
        "requires": ["bit端变量为Bool", "in输入来自有效功率流"],
        "replace": ["变量Component路径", "UId"],
        "preserve": ["Part Name=PBox", "in/bit/out端口名", "Access-Part-Wire完整子图"],
        "topology": ["series", "independent_powerrail_branches"],
    },
    {
        "id": "instruction.tonr_trend_state.v17",
        "source": "烧嘴控制.xml", "network": 2, "path": "instructions/tonr_trend_state.xml",
        "title": "TONR保持计时与趋势状态链",
        "description": "TIA V17导出的TONR完整Network，包含R/IN/PT/ET、全局实例、运算、比较和状态线圈。",
        "role": "instruction_pattern", "status": "verified",
        "intent": ["TONR", "保持计时", "趋势判断", "复位输入"],
        "not_for": ["TON", "TOF", "TP", "未声明的计时器实例"],
        "provides": ["tonr", "retentive_timer", "global_timer_instance", "timer.reset_port", "add", "sub", "compare.gt", "compare.ge", "compare.lt", "pbox", "topology.series"],
        "requires": ["全局TONR实例存在", "时间及数值类型匹配"],
        "replace": ["实例路径", "变量路径", "常量", "UId"],
        "preserve": ["TONR Version=1.0", "Instance", "time_type", "R/IN/PT/ET端口", "完整Wires"],
        "topology": ["series", "parallel_powerrail", "fan_out_or_merge"],
    },
    {
        "id": "instruction.comparison_or_chain.v17",
        "source": "烧嘴控制.xml", "network": 6, "path": "instructions/comparison_or_chain.xml",
        "title": "比较条件与O功能框汇合",
        "description": "TIA V17导出的Gt、Le、Contact经O功能框汇合后驱动Coil的完整Network。",
        "role": "topology_pattern", "status": "verified",
        "intent": ["Gt", "Le", "OR汇合", "多条件输出"],
        "not_for": ["串联AND条件", "置位复位保持"],
        "provides": ["compare.gt", "compare.le", "or_box", "topology.parallel", "topology.merge", "coil"],
        "requires": ["比较输入类型一致", "O的Card与输入端口数量一致"],
        "replace": ["变量路径", "常量", "Card", "UId"],
        "preserve": ["O TemplateValue Card", "inN/out端口", "分支汇合Wires"],
        "topology": ["parallel_powerrail", "merge"],
    },
    {
        "id": "instruction.arithmetic_guard.v17",
        "source": "程序发生器.xml", "network": 8, "path": "instructions/arithmetic_guard.xml",
        "title": "Sub、Mul、Ne、Div、Eq算术保护链",
        "description": "TIA V17导出的算术级联Network，保留AutomaticTyped、比较保护和数据/功率流连接。",
        "role": "instruction_pattern", "status": "verified",
        "intent": ["Sub", "Mul", "Div", "Ne", "Eq", "除零保护"],
        "not_for": ["不检查除数的任意除法替换"],
        "provides": ["sub", "mul", "div", "compare.ne", "compare.eq", "move", "automatic_typed", "topology.series", "cascade"],
        "requires": ["数值类型兼容", "除数保护逻辑保持"],
        "replace": ["变量路径", "常量", "UId"],
        "preserve": ["AutomaticTyped名称", "Ne保护关系", "en/eno及数据端口"],
        "topology": ["series", "fan_out_or_merge"],
    },
    {
        "id": "instruction.convert_divide.v17",
        "source": "输入信号转换块.xml", "network": 3, "path": "instructions/convert_divide.xml",
        "title": "Convert与Div级联",
        "description": "TIA V17导出的Convert到Div完整功能框级联。",
        "role": "instruction_pattern", "status": "verified",
        "intent": ["Convert", "Div", "类型转换", "缩放"],
        "not_for": ["源类型和目标类型未知"],
        "provides": ["convert", "div", "automatic_typed", "topology.series", "cascade"],
        "requires": ["SrcType和DestType明确", "除数合法"],
        "replace": ["变量路径", "常量", "SrcType", "DestType", "UId"],
        "preserve": ["TemplateValue大小写", "Convert in/out", "Div in1/in2/out"],
        "topology": ["series"],
    },
    {
        "id": "instruction.coilton_pulse.v17",
        "source": "烧嘴控制.xml", "network": 8, "path": "instructions/coilton_pulse.xml",
        "title": "CoilTON脉冲线圈",
        "description": "TIA V17导出的CoilTON完整Network，保留实例、time_type以及in/value/operand端口。",
        "role": "instruction_pattern", "status": "verified",
        "intent": ["CoilTON", "定时脉冲线圈", "复位脉冲"],
        "not_for": ["TON功能框", "TOF", "TP"],
        "provides": ["coilton", "timed_coil", "global_timer_instance", "topology.series"],
        "requires": ["IEC定时实例存在", "value为Time"],
        "replace": ["实例路径", "变量路径", "时间值", "UId"],
        "preserve": ["Part Name=CoilTON", "Instance", "time_type", "in/value/operand端口"],
        "topology": ["series", "parallel_powerrail"],
    },
    {
        "id": "instruction.increment_counter.v17",
        "source": "烧嘴控制.xml", "network": 5, "path": "instructions/increment_counter.xml",
        "title": "PBox脉冲驱动Inc累计",
        "description": "TIA V17导出的PBox上升沿经Inc增加Int累计值并参与比较、O汇合和Move的完整Network。",
        "role": "instruction_pattern", "status": "verified",
        "intent": ["Inc", "累计次数", "上升沿计数"],
        "not_for": ["CTU实例", "无跨扫描存储的临时变量"],
        "provides": ["inc", "counter.accumulate", "pbox", "compare.gt", "compare.lt", "or_box", "move", "topology.series"],
        "requires": ["累计变量跨扫描保持", "DestType与累计变量一致"],
        "replace": ["变量路径", "比较阈值", "UId"],
        "preserve": ["Inc operand端口", "DestType", "PBox bit连接", "完整Wires"],
        "topology": ["series", "parallel_powerrail", "fan_out_or_merge"],
    },
    {
        "id": "instruction.gather_scatter.v17",
        "source": "风机燃气.xml", "network": 1, "path": "instructions/gather_scatter.xml",
        "title": "GATHER与SCATTER 1.2位组装拆分",
        "description": "TIA V17导出的GATHER/SCATTER完整Network，保留Version、src_type/dest_type及IN/OUT端口。",
        "role": "instruction_pattern", "status": "verified",
        "intent": ["GATHER", "SCATTER", "Bool与Word转换"],
        "not_for": ["普通Convert替代", "修改TemplateValue大小写"],
        "provides": ["gather", "scatter", "bit_word_mapping", "version.1.2", "topology.series"],
        "requires": ["IN/OUT变量类型和位宽匹配"],
        "replace": ["变量路径", "src_type", "dest_type", "UId"],
        "preserve": ["Version=1.2", "src_type/dest_type小写", "IN/OUT端口", "完整Wires"],
        "topology": ["series", "parallel_powerrail", "fan_out_or_merge"],
    },
]


def extract_flgnet(source: Path, network_number: int) -> ET.Element:
    root = ET.parse(source).getroot()
    units = [item for item in root.iter() if local_name(item.tag) == "SW.Blocks.CompileUnit"]
    if network_number < 1 or network_number > len(units):
        raise ValueError(f"network {network_number} missing from {source.name}")
    flgnet = next((item for item in units[network_number - 1].iter() if local_name(item.tag) == "FlgNet"), None)
    if flgnet is None:
        raise ValueError(f"network {network_number} has no FlgNet in {source.name}")
    return flgnet


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    raw = root / "data/rag/raw/application"
    knowledge = root / "data/rag/knowledge"
    catalog_path = knowledge / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    existing = {str(item["id"]): item for item in catalog["items"]}

    for definition in SELECTIONS:
        flgnet = extract_flgnet(raw / definition["source"], int(definition["network"]))
        ET.indent(flgnet, space="  ")
        output_path = knowledge / definition["path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        xml = ET.tostring(flgnet, encoding="unicode", short_empty_elements=True)
        output_path.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<!-- Exact FlgNet extracted from TIA V17 export: {definition["source"]}, Network {definition["network"]}. -->\n'
            + xml + "\n",
            encoding="utf-8",
        )
        parts = sorted({str(item.attrib.get("Name", "")) for item in flgnet.iter() if local_name(item.tag) == "Part"})
        calls = sorted({str(item.attrib.get("BlockType", "")) for item in flgnet.iter() if local_name(item.tag) == "CallInfo"})
        scopes = sorted({str(item.attrib.get("Scope", "")) for item in flgnet.iter() if local_name(item.tag) == "Access"})
        metadata = {
            key: value for key, value in definition.items()
            if key not in {"source", "network", "path"}
        }
        metadata.update({
            "tia_version": "V17",
            "content_type": "xml_fragment",
            "content_path": definition["path"],
            "source_refs": [f'data/rag/raw/application/{definition["source"]}'],
            "contains": {"parts": parts, "calls": calls, "access_scopes": scopes},
        })
        existing[definition["id"]] = metadata

    standard_provides = {
        "topology.series_contact_coil.v17": ["topology.series"],
        "topology.parallel_set_reset.v17": ["topology.parallel"],
        "topology.fan_out.v17": ["topology.fan_out"],
    }
    standard_topology = {
        "topology.series_contact_coil.v17": ["series"],
        "topology.parallel_set_reset.v17": ["parallel_powerrail"],
        "topology.fan_out.v17": ["fan_out"],
    }
    for item_id, capabilities in standard_provides.items():
        item = existing[item_id]
        item["provides"] = list(dict.fromkeys([*item.get("provides", []), *capabilities]))
        item["topology"] = standard_topology[item_id]

    catalog["schema_version"] = 2
    catalog["items"] = list(existing.values())
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, sort_keys=False, indent=2) + "\n", encoding="utf-8")

    raw_sources = sorted(raw.glob("*.xml"), key=lambda item: item.name.casefold())
    rows = []
    for source in raw_sources:
        ref = f"data/rag/raw/application/{source.name}"
        ids = sorted(item_id for item_id, item in existing.items() if ref in item.get("source_refs", []))
        rows.append({"source": ref, "extracted_items": ids})
    manifest = {
        "tia_version": "V17",
        "sources": rows,
        "verified_absent_from_application": ["RBox", "TOF", "TP", "CTU", "CTD", "CTUD", "用户FB多重实例调用", "动态数组下标"],
        "publication_note": "golden仅用于从TIA V17导出XML原样提取的结构；其他新条目标记verified，仍应由TIA导入和编译做最终语义验证。",
    }
    (knowledge / "distillation.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
