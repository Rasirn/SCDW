"""Inventory every LAD construct in raw application SimaticML exports.

This is an offline distillation aid. Runtime generation must continue to read
only the published knowledge catalog, never ``raw/application``.
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def node_shape(node: ET.Element) -> dict[str, Any]:
    value: dict[str, Any] = {"tag": local_name(node.tag)}
    if node.attrib:
        value["attributes"] = dict(sorted(node.attrib.items()))
    text = (node.text or "").strip()
    if text:
        value["text"] = text
    children = [node_shape(child) for child in list(node)]
    if children:
        value["children"] = children
    return value


def components(node: ET.Element) -> list[str]:
    return [str(item.attrib.get("Name", "")) for item in node.iter() if local_name(item.tag) == "Component"]


def multilingual_text(unit: ET.Element, composition: str) -> str:
    for item in unit.iter():
        if local_name(item.tag) == "MultilingualText" and item.attrib.get("CompositionName") == composition:
            text = next((child.text for child in item.iter() if local_name(child.tag) == "Text"), "")
            return str(text or "")
    return ""


def classify_topology(wires: list[dict[str, Any]]) -> list[str]:
    result: set[str] = set()
    for wire in wires:
        endpoints = wire["endpoints"]
        names = [item.get("name", "") for item in endpoints if item["kind"] == "NameCon"]
        if any(item["kind"] == "Powerrail" for item in endpoints) and len(names) > 1:
            result.add("parallel_powerrail")
        if len(endpoints) > 2:
            result.add("fan_out_or_merge")
        output_count = sum(name.lower() in {"out", "q", "eno"} for name in names)
        if output_count > 1:
            result.add("multi_source_merge")
        if output_count == 1 and len(names) > 1:
            result.add("series_or_fan_out")
    return sorted(result or {"point_to_point"})


def analyze_application(raw_dir: Path, catalog_path: Path) -> dict[str, Any]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    items = catalog.get("items", [])
    catalog_part_coverage: dict[str, list[str]] = defaultdict(list)
    catalog_access_coverage: dict[str, list[str]] = defaultdict(list)
    catalog_call_coverage: dict[str, list[str]] = defaultdict(list)
    catalog_capability_coverage: dict[str, list[str]] = defaultdict(list)
    catalog_structure_coverage: dict[str, list[str]] = defaultdict(list)
    for item in items:
        for part in item.get("contains", {}).get("parts", []):
            catalog_part_coverage[str(part)].append(str(item["id"]))
        for scope in item.get("contains", {}).get("access_scopes", []):
            catalog_access_coverage[str(scope)].append(str(item["id"]))
        for block_type in item.get("contains", {}).get("calls", []):
            catalog_call_coverage[str(block_type)].append(str(item["id"]))
        for capability in item.get("provides", []):
            catalog_capability_coverage[str(capability)].append(str(item["id"]))
        if item.get("content_type") == "xml_fragment":
            content = catalog_path.parent / str(item.get("content_path", ""))
            try:
                xml_text = content.read_text(encoding="utf-8")
                try:
                    fragment = ET.fromstring(xml_text)
                except ET.ParseError:
                    fragment = ET.fromstring(f"<KnowledgeFragment>{xml_text}</KnowledgeFragment>")
                for tag in {local_name(node.tag) for node in fragment.iter()}:
                    catalog_structure_coverage[tag].append(str(item["id"]))
            except (OSError, ET.ParseError):
                pass

    part_stats: dict[tuple[str, str], dict[str, Any]] = {}
    access_stats: Counter[tuple[str, tuple[str, ...]]] = Counter()
    call_stats: Counter[tuple[str, str, str, tuple[tuple[str, str, str], ...]]] = Counter()
    template_stats: Counter[tuple[str, str, str, str]] = Counter()
    automatic_stats: Counter[tuple[str, str]] = Counter()
    equations: Counter[str] = Counter()
    topology_stats: Counter[str] = Counter()
    topology_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_rows: list[dict[str, Any]] = []
    network_rows: list[dict[str, Any]] = []

    for path in sorted(raw_dir.glob("*.xml"), key=lambda item: item.name.casefold()):
        root = ET.parse(path).getroot()
        interface: dict[str, str] = {}
        for section in (item for item in root.iter() if local_name(item.tag) == "Section"):
            section_name = str(section.attrib.get("Name", ""))
            for member in list(section):
                if local_name(member.tag) == "Member" and member.attrib.get("Name"):
                    interface[str(member.attrib["Name"])] = section_name

        source_parts: Counter[str] = Counter()
        source_scopes: Counter[str] = Counter()
        source_calls: Counter[str] = Counter()
        units = [item for item in root.iter() if local_name(item.tag) == "SW.Blocks.CompileUnit"]
        for network_index, unit in enumerate(units, 1):
            flgnet = next((item for item in unit.iter() if local_name(item.tag) == "FlgNet"), None)
            if flgnet is None:
                continue
            direct_parts = next((item for item in list(flgnet) if local_name(item.tag) == "Parts"), None)
            direct_wires = next((item for item in list(flgnet) if local_name(item.tag) == "Wires"), None)
            uid_ports: dict[str, set[str]] = defaultdict(set)
            wires: list[dict[str, Any]] = []
            if direct_wires is not None:
                for wire in list(direct_wires):
                    endpoints = []
                    for endpoint in list(wire):
                        kind = local_name(endpoint.tag)
                        row = {"kind": kind}
                        if endpoint.attrib.get("UId") is not None:
                            row["uid"] = str(endpoint.attrib["UId"])
                        if endpoint.attrib.get("Name") is not None:
                            row["name"] = str(endpoint.attrib["Name"])
                        endpoints.append(row)
                        if kind == "NameCon" and "uid" in row:
                            uid_ports[row["uid"]].add(row.get("name", ""))
                    wires.append({"uid": str(wire.attrib.get("UId", "")), "endpoints": endpoints})

            instructions: list[str] = []
            variables: list[str] = []
            local_dependencies: list[dict[str, str]] = []
            if direct_parts is not None:
                for element in list(direct_parts):
                    kind = local_name(element.tag)
                    if kind == "Part":
                        name = str(element.attrib.get("Name", ""))
                        version = str(element.attrib.get("Version", ""))
                        key = (name, version)
                        row = part_stats.setdefault(key, {
                            "name": name, "version": version or None, "count": 0,
                            "ports": set(), "child_structures": {}, "sources": set(),
                        })
                        row["count"] += 1
                        row["ports"].update(uid_ports.get(str(element.attrib.get("UId", "")), set()))
                        shape = node_shape(element)
                        shape_key = json.dumps(shape, ensure_ascii=False, sort_keys=True)
                        row["child_structures"][shape_key] = shape
                        row["sources"].add(f"{path.name}#network-{network_index}")
                        source_parts[name] += 1
                        instructions.append(name)
                        for child in element.iter():
                            child_kind = local_name(child.tag)
                            if child_kind == "TemplateValue":
                                template_stats[(name, str(child.attrib.get("Name", "")), str(child.attrib.get("Type", "")), str(child.text or ""))] += 1
                            elif child_kind == "AutomaticTyped":
                                automatic_stats[(name, str(child.attrib.get("Name", "")))] += 1
                            elif child_kind == "Equation":
                                equations[str(child.text or "")] += 1
                    elif kind == "Access":
                        scope = str(element.attrib.get("Scope", ""))
                        path_parts = tuple(components(element))
                        access_stats[(scope, path_parts)] += 1
                        source_scopes[scope] += 1
                        variables.append(".".join(path_parts) or f"<{scope}>")
                        if scope == "LocalVariable" and path_parts:
                            local_dependencies.append({"name": path_parts[0], "section": interface.get(path_parts[0], "UNDECLARED")})
                    elif kind == "Call":
                        info = next((item for item in element.iter() if local_name(item.tag) == "CallInfo"), None)
                        if info is not None:
                            instance = next((item for item in info.iter() if local_name(item.tag) == "Instance"), None)
                            parameters = tuple(
                                (str(item.attrib.get("Name", "")), str(item.attrib.get("Section", "")), str(item.attrib.get("Type", "")))
                                for item in info.iter() if local_name(item.tag) == "Parameter"
                            )
                            name = str(info.attrib.get("Name", ""))
                            block_type = str(info.attrib.get("BlockType", ""))
                            instance_scope = str(instance.attrib.get("Scope", "")) if instance is not None else ""
                            call_stats[(name, block_type, instance_scope, parameters)] += 1
                            source_calls[block_type] += 1
                            instructions.append(f"Call:{block_type}:{name}")

            for access in (item for item in flgnet.iter() if local_name(item.tag) == "Access"):
                if direct_parts is not None and access in list(direct_parts):
                    continue
                scope = str(access.attrib.get("Scope", ""))
                access_stats[(scope, tuple(components(access)))] += 1
                source_scopes[scope] += 1

            topologies = classify_topology(wires)
            for topology in topologies:
                topology_stats[topology] += 1
                if len(topology_examples[topology]) < 10:
                    topology_examples[topology].append({"source": path.name, "network": network_index, "wires": wires})
            network_rows.append({
                "source": path.name,
                "network": network_index,
                "document_id": unit.attrib.get("ID"),
                "title": multilingual_text(unit, "Title"),
                "comment": multilingual_text(unit, "Comment"),
                "instructions": instructions,
                "variables": variables,
                "interface_dependencies": local_dependencies,
                "topology": topologies,
                "wires": wires,
            })

        source_rows.append({
            "source": path.name,
            "network_count": len(units),
            "part_counts": dict(sorted(source_parts.items())),
            "access_scope_counts": dict(sorted(source_scopes.items())),
            "call_counts": dict(sorted(source_calls.items())),
        })

    part_rows = []
    observed_part_names: set[str] = set()
    for row in sorted(part_stats.values(), key=lambda item: (item["name"], item["version"] or "")):
        observed_part_names.add(row["name"])
        part_rows.append({
            **row,
            "ports": sorted(row["ports"]),
            "child_structures": list(row["child_structures"].values()),
            "sources": sorted(row["sources"]),
            "catalog_items": sorted(catalog_part_coverage.get(row["name"], [])),
        })

    uncovered = sorted(name for name in observed_part_names if not catalog_part_coverage.get(name))
    observed_scopes = sorted({scope for scope, _ in access_stats})
    observed_calls = sorted({block_type for _, block_type, _, _ in call_stats})
    observed_structures = {
        "TemplateValue": sum(template_stats.values()),
        "AutomaticTyped": sum(automatic_stats.values()),
        "Equation": sum(equations.values()),
        "CallInfo": sum(call_stats.values()),
        "Instance": sum(count for (_, _, scope, _), count in call_stats.items() if scope),
        "Parameter": sum(len(parameters) * count for (_, _, _, parameters), count in call_stats.items()),
        "Access": sum(access_stats.values()),
        "Wire": sum(len(network["wires"]) for network in network_rows),
    }
    topology_capability_options = {
        "point_to_point": ["topology.series"],
        "series_or_fan_out": ["topology.series", "topology.fan_out"],
        "parallel_powerrail": ["topology.parallel"],
        "fan_out_or_merge": ["topology.fan_out", "topology.merge"],
        "multi_source_merge": ["topology.merge"],
    }
    topology_coverage = []
    for topology, count in sorted(topology_stats.items()):
        options = topology_capability_options.get(topology, [f"topology.{topology}"])
        matching = sorted({item for capability in options for item in catalog_capability_coverage.get(capability, [])})
        topology_coverage.append({
            "topology": topology,
            "count": count,
            "acceptable_capabilities": options,
            "catalog_items": matching,
            "covered": bool(matching),
        })
    missing_raw_samples = [
        name for name in ("RBox", "TOF", "TP", "CTU", "CTD", "CTUD")
        if name not in observed_part_names
    ] + ["用户FB多重实例调用", "动态数组下标"]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "raw_directory": str(raw_dir),
        "catalog": str(catalog_path),
        "sources": source_rows,
        "aggregate": {
            "part_types": part_rows,
            "accesses": [
                {"scope": scope, "path": list(path_parts), "count": count}
                for (scope, path_parts), count in sorted(access_stats.items())
            ],
            "calls": [
                {"name": name, "block_type": block_type, "instance_scope": scope,
                 "parameters": [{"name": p[0], "section": p[1], "type": p[2]} for p in parameters], "count": count}
                for (name, block_type, scope, parameters), count in sorted(call_stats.items())
            ],
            "template_values": [
                {"part": part, "name": name, "type": value_type, "value": value, "count": count}
                for (part, name, value_type, value), count in sorted(template_stats.items())
            ],
            "automatic_typed": [
                {"part": part, "name": name, "count": count}
                for (part, name), count in sorted(automatic_stats.items())
            ],
            "equations": [{"value": value, "count": count} for value, count in sorted(equations.items())],
            "topology_counts": dict(sorted(topology_stats.items())),
            "topology_examples": dict(sorted(topology_examples.items())),
            "networks": network_rows,
        },
        "catalog_comparison": {
            "observed_part_types": sorted(observed_part_names),
            "covered_part_types": {
                name: sorted(catalog_part_coverage[name])
                for name in sorted(observed_part_names) if catalog_part_coverage.get(name)
            },
            "uncovered_part_types": uncovered,
            "coverage_ratio": 1.0 if not observed_part_names else round((len(observed_part_names) - len(uncovered)) / len(observed_part_names), 6),
            "access_scope_coverage": [
                {"scope": scope, "catalog_items": sorted(catalog_access_coverage.get(scope, [])), "covered": bool(catalog_access_coverage.get(scope))}
                for scope in observed_scopes
            ],
            "call_type_coverage": [
                {"block_type": block_type, "catalog_items": sorted(catalog_call_coverage.get(block_type, [])), "covered": bool(catalog_call_coverage.get(block_type))}
                for block_type in observed_calls
            ],
            "structure_coverage": [
                {"structure": structure, "count": count, "catalog_items": sorted(set(catalog_structure_coverage.get(structure, []))), "covered": bool(catalog_structure_coverage.get(structure))}
                for structure, count in observed_structures.items() if count
            ],
            "topology_coverage": topology_coverage,
            "knowledge_gaps": {
                "uncovered_part_types": uncovered,
                "uncovered_access_scopes": [scope for scope in observed_scopes if not catalog_access_coverage.get(scope)],
                "uncovered_call_types": [block_type for block_type in observed_calls if not catalog_call_coverage.get(block_type)],
                "uncovered_structures": [structure for structure, count in observed_structures.items() if count and not catalog_structure_coverage.get(structure)],
                "uncovered_topologies": [item["topology"] for item in topology_coverage if not item["covered"]],
                "missing_raw_samples": missing_raw_samples,
            },
        },
    }


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, default=project_root / "data/rag/raw/application")
    parser.add_argument("--catalog", type=Path, default=project_root / "data/rag/knowledge/catalog.json")
    parser.add_argument("--output", type=Path, default=project_root / "data/rag/knowledge/application_coverage.json")
    args = parser.parse_args()
    report = analyze_application(args.raw_dir.resolve(), args.catalog.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["catalog_comparison"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
