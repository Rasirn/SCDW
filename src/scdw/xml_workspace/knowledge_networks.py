"""Small, catalog-backed renderers for reviewed V17 topology recipes.

This intentionally supports only explicitly published recipes.  It is not a
general LAD rules engine and never guesses Part names, ports, or versions.
"""
from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from .models import ArtifactError


RENDERABLE_KINDS = {
    "blueprint_network_v17",
    "block_call_v17",
    "contact_or_coil",
    "contact_or_pbox_scoil",
    "compare_ge_real_coil",
    "pbox_set_reset_ton_coil",
}


def _component_path(path: list[str]) -> str:
    if not path or any(not str(item).strip() for item in path):
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "each symbol path must contain one or more non-empty Component names")
    return "".join(f"<Component Name={quoteattr(str(item))} />" for item in path)


def render_contact_or_network(
    renderer_kind: str,
    *,
    contacts: list[list[str]],
    output: list[str],
    edge_memory: list[str] | None,
    title: str,
    comment: str,
) -> str:
    if renderer_kind not in RENDERABLE_KINDS:
        raise ArtifactError("KNOWLEDGE_RENDERER_UNSUPPORTED", f"unsupported knowledge renderer: {renderer_kind}")
    if not 2 <= len(contacts) <= 16:
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "reviewed O/Card recipe supports 2 to 16 Contact inputs")
    if not title.strip() or not comment.strip():
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "title and comment are required")
    needs_edge = renderer_kind == "contact_or_pbox_scoil"
    if needs_edge and not edge_memory:
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "contact_or_pbox_scoil requires edge_memory")
    if not needs_edge and edge_memory:
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "contact_or_coil does not accept edge_memory")

    access_paths = [*contacts]
    if edge_memory:
        access_paths.append(edge_memory)
    access_paths.append(output)
    access_uids = list(range(1, len(access_paths) + 1))
    next_uid = len(access_paths) + 1
    contact_uids = list(range(next_uid, next_uid + len(contacts)))
    next_uid += len(contacts)
    or_uid = next_uid
    next_uid += 1
    pbox_uid = next_uid if needs_edge else None
    next_uid += 1 if needs_edge else 0
    coil_uid = next_uid
    next_uid += 1

    parts: list[str] = []
    for uid, path in zip(access_uids, access_paths):
        parts.append(f'<Access Scope="GlobalVariable" UId="{uid}"><Symbol>{_component_path(path)}</Symbol></Access>')
    parts.extend(f'<Part Name="Contact" UId="{uid}" />' for uid in contact_uids)
    parts.append(
        f'<Part Name="O" UId="{or_uid}"><TemplateValue Name="Card" Type="Cardinality">{len(contacts)}</TemplateValue></Part>'
    )
    if needs_edge:
        parts.append(f'<Part Name="PBox" UId="{pbox_uid}" />')
        parts.append(f'<Part Name="SCoil" UId="{coil_uid}" />')
    else:
        parts.append(f'<Part Name="Coil" UId="{coil_uid}" />')

    wires: list[str] = []

    def add_wire(endpoints: str) -> None:
        nonlocal next_uid
        wires.append(f'<Wire UId="{next_uid}">{endpoints}</Wire>')
        next_uid += 1

    add_wire("<Powerrail />" + "".join(f'<NameCon UId="{uid}" Name="in" />' for uid in contact_uids))
    for index, (access_uid, contact_uid) in enumerate(zip(access_uids[:len(contacts)], contact_uids), 1):
        add_wire(f'<IdentCon UId="{access_uid}" /><NameCon UId="{contact_uid}" Name="operand" />')
        # V17 golden topology: every source output has its own Wire to one O.inN.
        add_wire(f'<NameCon UId="{contact_uid}" Name="out" /><NameCon UId="{or_uid}" Name="in{index}" />')
    if needs_edge:
        assert pbox_uid is not None and edge_memory is not None
        edge_access_uid = access_uids[len(contacts)]
        output_access_uid = access_uids[len(contacts) + 1]
        add_wire(f'<NameCon UId="{or_uid}" Name="out" /><NameCon UId="{pbox_uid}" Name="in" />')
        add_wire(f'<IdentCon UId="{edge_access_uid}" /><NameCon UId="{pbox_uid}" Name="bit" />')
        add_wire(f'<NameCon UId="{pbox_uid}" Name="out" /><NameCon UId="{coil_uid}" Name="in" />')
    else:
        output_access_uid = access_uids[len(contacts)]
        add_wire(f'<NameCon UId="{or_uid}" Name="out" /><NameCon UId="{coil_uid}" Name="in" />')
    add_wire(f'<IdentCon UId="{output_access_uid}" /><NameCon UId="{coil_uid}" Name="operand" />')

    return (
        '<SW.Blocks.CompileUnit ID="1" CompositionName="CompileUnits">'
        '<AttributeList><NetworkSource>'
        '<FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4">'
        f'<Parts>{"".join(parts)}</Parts><Wires>{"".join(wires)}</Wires>'
        '</FlgNet></NetworkSource><ProgrammingLanguage>LAD</ProgrammingLanguage></AttributeList>'
        '<ObjectList>'
        '<MultilingualText ID="2" CompositionName="Comment"><ObjectList>'
        '<MultilingualTextItem ID="3" CompositionName="Items"><AttributeList>'
        f'<Culture>zh-CN</Culture><Text>{escape(comment)}</Text>'
        '</AttributeList></MultilingualTextItem></ObjectList></MultilingualText>'
        '<MultilingualText ID="4" CompositionName="Title"><ObjectList>'
        '<MultilingualTextItem ID="5" CompositionName="Items"><AttributeList>'
        f'<Culture>zh-CN</Culture><Text>{escape(title)}</Text>'
        '</AttributeList></MultilingualTextItem></ObjectList></MultilingualText>'
        '</ObjectList></SW.Blocks.CompileUnit>'
    )


def _compile_unit(parts: str, wires: str, title: str, comment: str) -> str:
    if not title.strip() or not comment.strip():
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "title and comment are required")
    return (
        '<SW.Blocks.CompileUnit ID="1" CompositionName="CompileUnits">'
        '<AttributeList><NetworkSource>'
        '<FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4">'
        f'<Parts>{parts}</Parts><Wires>{wires}</Wires>'
        '</FlgNet></NetworkSource><ProgrammingLanguage>LAD</ProgrammingLanguage></AttributeList>'
        '<ObjectList>'
        '<MultilingualText ID="2" CompositionName="Comment"><ObjectList>'
        '<MultilingualTextItem ID="3" CompositionName="Items"><AttributeList>'
        f'<Culture>zh-CN</Culture><Text>{escape(comment)}</Text>'
        '</AttributeList></MultilingualTextItem></ObjectList></MultilingualText>'
        '<MultilingualText ID="4" CompositionName="Title"><ObjectList>'
        '<MultilingualTextItem ID="5" CompositionName="Items"><AttributeList>'
        f'<Culture>zh-CN</Culture><Text>{escape(title)}</Text>'
        '</AttributeList></MultilingualTextItem></ObjectList></MultilingualText>'
        '</ObjectList></SW.Blocks.CompileUnit>'
    )


def _normalize_operand(value, *, default_type: str = "Bool") -> dict:
    if isinstance(value, list):
        return {"kind": "variable", "scope": "GlobalVariable", "path": value, "data_type": default_type}
    if isinstance(value, (str, int, float, bool)):
        return {"kind": "constant", "value": value, "data_type": default_type}
    if not isinstance(value, dict):
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "blueprint operand must be an object, path list or scalar constant")
    return value


class _BlueprintFlgNet:
    def __init__(self) -> None:
        self.next_uid = 1
        self.parts: list[str] = []
        self.wires: list[str] = []

    def uid(self) -> int:
        value = self.next_uid
        self.next_uid += 1
        return value

    def wire(self, content: str) -> None:
        self.wires.append(f'<Wire UId="{self.uid()}">{content}</Wire>')

    def access(self, raw, *, default_type: str = "Bool") -> int:
        value = _normalize_operand(raw, default_type=default_type)
        uid = self.uid()
        kind = str(value.get("kind", "variable"))
        if kind == "constant":
            data_type = escape(str(value.get("data_type") or default_type))
            constant = escape(str(value.get("value")))
            self.parts.append(
                f'<Access Scope="LiteralConstant" UId="{uid}"><Constant>'
                f'<ConstantType>{data_type}</ConstantType><ConstantValue>{constant}</ConstantValue>'
                '</Constant></Access>'
            )
            return uid
        path = value.get("path")
        if not isinstance(path, list) or not path or not all(str(item).strip() for item in path):
            raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "variable operand requires a non-empty path array")
        scope = str(value.get("scope") or "GlobalVariable")
        if scope not in {"GlobalVariable", "LocalVariable"}:
            raise ArtifactError("KNOWLEDGE_BINDING_INVALID", f"unsupported blueprint access scope: {scope}")
        self.parts.append(f'<Access Scope="{scope}" UId="{uid}"><Symbol>{_component_path([str(item) for item in path])}</Symbol></Access>')
        return uid

    def _data(self, part_uid: int, port: str, operand, *, default_type: str = "Bool", output: bool = False) -> None:
        access_uid = self.access(operand, default_type=default_type)
        connectors = (
            f'<NameCon UId="{part_uid}" Name="{escape(port)}" /><IdentCon UId="{access_uid}" />'
            if output else
            f'<IdentCon UId="{access_uid}" /><NameCon UId="{part_uid}" Name="{escape(port)}" />'
        )
        self.wire(connectors)

    def leaf(self, node: dict) -> dict:
        kind = str(node.get("kind", ""))
        operands = dict(node.get("operands") or {})
        attributes = dict(node.get("attributes") or {})
        uid = self.uid()
        power_in = "in"
        power_out: str | None = "out"
        if kind == "contact":
            negated = bool(attributes.get("negated"))
            body = '<Negated Name="operand" />' if negated else ''
            self.parts.append(f'<Part Name="Contact" UId="{uid}">{body}</Part>')
            self._data(uid, "operand", operands["operand"])
        elif kind in {"coil", "set_coil", "reset_coil"}:
            part_name = {"coil": "Coil", "set_coil": "SCoil", "reset_coil": "RCoil"}[kind]
            self.parts.append(f'<Part Name="{part_name}" UId="{uid}" />')
            self._data(uid, "operand", operands["operand"])
            power_out = None
        elif kind in {"rising_edge", "falling_edge"}:
            part_name = "PBox" if kind == "rising_edge" else "NBox"
            self.parts.append(f'<Part Name="{part_name}" UId="{uid}" />')
            self._data(uid, "bit", operands["memory"])
        elif kind == "move":
            power_in, power_out = "en", "eno"
            self.parts.append(
                f'<Part Name="Move" UId="{uid}" DisabledENO="true">'
                '<TemplateValue Name="Card" Type="Cardinality">1</TemplateValue></Part>'
            )
            self._data(uid, "in", operands["in"], default_type=str(attributes.get("data_type", "Real")))
            self._data(uid, "out1", operands["out"], default_type=str(attributes.get("data_type", "Real")), output=True)
        elif kind in {"math", "calc"}:
            operation = str(attributes.get("operation") or ("Calc" if kind == "calc" else "Mul"))
            data_type = str(attributes.get("data_type") or "Real")
            power_in, power_out = "en", "eno"
            if operation == "Calc":
                equation = escape(str(attributes.get("equation") or "IN1"))
                input_names = sorted((name for name in operands if name.startswith("in")), key=lambda name: int(name[2:]) if name[2:].isdigit() else 99)
                body = (
                    f'<Equation>{equation}</Equation>'
                    f'<TemplateValue Name="Card" Type="Cardinality">{len(input_names)}</TemplateValue>'
                    f'<TemplateValue Name="SrcType" Type="Type">{escape(data_type)}</TemplateValue>'
                )
            else:
                input_names = sorted((name for name in operands if name.startswith("in")), key=lambda name: int(name[2:]) if name[2:].isdigit() else 99)
                body = (
                    f'<TemplateValue Name="Card" Type="Cardinality">{len(input_names)}</TemplateValue>'
                    '<AutomaticTyped Name="SrcType" />'
                )
            self.parts.append(f'<Part Name="{escape(operation)}" UId="{uid}" DisabledENO="true">{body}</Part>')
            for name in input_names:
                self._data(uid, name, operands[name], default_type=data_type)
            self._data(uid, "out", operands["out"], default_type=data_type, output=True)
        elif kind == "convert":
            source_type = str(attributes.get("source_type") or "Real")
            target_type = str(attributes.get("target_type") or "Int")
            power_in, power_out = "en", "eno"
            self.parts.append(
                f'<Part Name="Convert" UId="{uid}" DisabledENO="true">'
                f'<TemplateValue Name="SrcType" Type="Type">{escape(source_type)}</TemplateValue>'
                f'<TemplateValue Name="DestType" Type="Type">{escape(target_type)}</TemplateValue></Part>'
            )
            self._data(uid, "in", operands["in"], default_type=source_type)
            self._data(uid, "out", operands["out"], default_type=target_type, output=True)
        elif kind == "compare":
            operation = str(attributes.get("operation") or "Ge")
            data_type = str(attributes.get("data_type") or "Real")
            power_in, power_out = "pre", "out"
            self.parts.append(
                f'<Part Name="{escape(operation)}" UId="{uid}">'
                f'<TemplateValue Name="SrcType" Type="Type">{escape(data_type)}</TemplateValue></Part>'
            )
            self._data(uid, "in1", operands["in1"], default_type=data_type)
            self._data(uid, "in2", operands["in2"], default_type=data_type)
        else:
            raise ArtifactError("KNOWLEDGE_RENDERER_UNSUPPORTED", f"blueprint renderer does not support node kind: {kind}")
        return {"uid": uid, "kind": kind, "power_in": power_in, "power_out": power_out}

    @staticmethod
    def branches(root: dict) -> list[list[dict]]:
        kind = str(root.get("kind", ""))
        children = list(root.get("children") or [])
        if kind in {"parallel", "fan_out"}:
            result = []
            for child in children:
                result.append(list(child.get("children") or []) if child.get("kind") in {"series", "branch"} else [child])
            return result
        if kind in {"series", "branch"}:
            return [children]
        return [[root]]

    def render(self, root: dict) -> tuple[str, str]:
        rendered: list[list[dict]] = []
        for branch in self.branches(root):
            if not branch:
                raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "blueprint branch must not be empty")
            rendered.append([self.leaf(node) for node in branch])
        rail = []
        for branch in rendered:
            rail.append(f'<NameCon UId="{branch[0]["uid"]}" Name="{branch[0]["power_in"]}" />')
            for previous, current in zip(branch, branch[1:]):
                if previous["power_out"] is None:
                    raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "a terminal blueprint instruction cannot precede another instruction")
                self.wire(
                    f'<NameCon UId="{previous["uid"]}" Name="{previous["power_out"]}" />'
                    f'<NameCon UId="{current["uid"]}" Name="{current["power_in"]}" />'
                )
        self.wire('<Powerrail />' + ''.join(rail))
        return ''.join(self.parts), ''.join(self.wires)


def render_blueprint_network_v17(*, blueprint: dict | None, title: str, comment: str) -> str:
    if not isinstance(blueprint, dict):
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "blueprint_network_v17 requires a structured blueprint object")
    builder = _BlueprintFlgNet()
    parts, wires = builder.render(blueprint)
    return _compile_unit(parts, wires, title, comment)


def render_block_call_v17(*, blueprint: dict | None, title: str, comment: str) -> str:
    """Render a frozen no-guess FC/FB CallInfo, including an FB global instance."""
    if not isinstance(blueprint, dict) or blueprint.get("kind") not in {"fc_call", "fb_call"}:
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "block_call_v17 requires an fc_call or fb_call blueprint")
    values = dict(blueprint.get("operands") or {})
    block_name = str(values.get("block") or "").strip()
    if not block_name:
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "block_call_v17 requires block")
    block_type = "FB" if blueprint["kind"] == "fb_call" else "FC"
    call_uid = 1
    instance = ""
    if block_type == "FB":
        instance_name = str(values.get("instance") or "").strip()
        if not instance_name:
            raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "FB call requires a global instance DB")
        instance = f'<Instance Scope="GlobalVariable" UId="2">{_component_path([instance_name])}</Instance>'
    raw_parameters = values.get("parameters") or {}
    if not isinstance(raw_parameters, dict):
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "call parameters must be an object keyed by parameter name")
    parameter_xml: list[str] = []
    access_xml: list[str] = []
    wire_xml: list[str] = []
    next_uid = 3
    for name, raw in raw_parameters.items():
        if not isinstance(raw, dict):
            raise ArtifactError("KNOWLEDGE_BINDING_INVALID", f"call parameter {name} must be an object")
        section = str(raw.get("section") or "Input")
        data_type = str(raw.get("data_type") or raw.get("type") or "Bool")
        operand = raw.get("operand")
        if section not in {"Input", "Output", "InOut"} or operand is None:
            raise ArtifactError("KNOWLEDGE_BINDING_INVALID", f"call parameter {name} requires section and operand")
        parameter_xml.append(
            f'<Parameter Name={quoteattr(str(name))} Section={quoteattr(section)} Type={quoteattr(data_type)} />'
        )
        value = _normalize_operand(operand, default_type=data_type)
        if value.get("kind", "variable") != "variable":
            raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "block_call_v17 currently requires symbolic parameter operands")
        scope = str(value.get("scope") or "GlobalVariable")
        path = value.get("path")
        if scope not in {"GlobalVariable", "LocalVariable"} or not isinstance(path, list):
            raise ArtifactError("KNOWLEDGE_BINDING_INVALID", f"invalid operand for call parameter {name}")
        access_uid, wire_uid = next_uid, next_uid + 1
        next_uid += 2
        access_xml.append(f'<Access Scope="{scope}" UId="{access_uid}"><Symbol>{_component_path(path)}</Symbol></Access>')
        connectors = (
            f'<NameCon UId="{call_uid}" Name={quoteattr(str(name))} /><IdentCon UId="{access_uid}" />'
            if section == "Output" else
            f'<IdentCon UId="{access_uid}" /><NameCon UId="{call_uid}" Name={quoteattr(str(name))} />'
        )
        wire_xml.append(f'<Wire UId="{wire_uid}">{connectors}</Wire>')
    parts = (
        ''.join(access_xml)
        + f'<Call UId="{call_uid}"><CallInfo Name={quoteattr(block_name)} BlockType="{block_type}">'
        + instance + ''.join(parameter_xml) + '</CallInfo></Call>'
    )
    wires = f'<Wire UId="{next_uid}"><Powerrail /><NameCon UId="{call_uid}" Name="en" /></Wire>' + ''.join(wire_xml)
    return _compile_unit(parts, wires, title, comment)


def render_compare_ge_real_coil(
    *, compare_input: list[str] | None, compare_constant: str | int | float | None,
    output: list[str], title: str, comment: str,
) -> str:
    if not compare_input or compare_constant is None:
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "compare_ge_real_coil requires compare_input and compare_constant")
    constant = str(compare_constant)
    try:
        float(constant)
    except ValueError as exc:
        raise ArtifactError("KNOWLEDGE_BINDING_INVALID", "compare_constant must be a Real literal") from exc
    parts = (
        f'<Access Scope="GlobalVariable" UId="1"><Symbol>{_component_path(compare_input)}</Symbol></Access>'
        f'<Access Scope="LiteralConstant" UId="2"><Constant><ConstantType>Real</ConstantType><ConstantValue>{escape(constant)}</ConstantValue></Constant></Access>'
        f'<Access Scope="GlobalVariable" UId="3"><Symbol>{_component_path(output)}</Symbol></Access>'
        '<Part Name="Ge" UId="10"><TemplateValue Name="SrcType" Type="Type">Real</TemplateValue></Part>'
        '<Part Name="Coil" UId="11" />'
    )
    wires = (
        '<Wire UId="20"><Powerrail /><NameCon UId="10" Name="pre" /></Wire>'
        '<Wire UId="21"><IdentCon UId="1" /><NameCon UId="10" Name="in1" /></Wire>'
        '<Wire UId="22"><IdentCon UId="2" /><NameCon UId="10" Name="in2" /></Wire>'
        '<Wire UId="23"><NameCon UId="10" Name="out" /><NameCon UId="11" Name="in" /></Wire>'
        '<Wire UId="24"><IdentCon UId="3" /><NameCon UId="11" Name="operand" /></Wire>'
    )
    return _compile_unit(parts, wires, title, comment)


def render_pbox_set_reset_ton_coil(
    *, failure_input: list[str] | None, failure_memory: list[str] | None,
    recovery_input: list[str] | None, recovery_memory: list[str] | None,
    fault_flag: list[str] | None, timer_instance: list[str] | None,
    preset_time: str | None, output: list[str], title: str, comment: str,
) -> str:
    bindings = {
        "failure_input": failure_input, "failure_memory": failure_memory,
        "recovery_input": recovery_input, "recovery_memory": recovery_memory,
        "fault_flag": fault_flag, "timer_instance": timer_instance,
    }
    missing = [name for name, value in bindings.items() if not value]
    if missing or not preset_time:
        raise ArtifactError(
            "KNOWLEDGE_BINDING_INVALID",
            "pbox_set_reset_ton_coil missing bindings: " + ", ".join(missing + ([] if preset_time else ["preset_time"])),
        )
    paths = [failure_input, failure_memory, fault_flag, recovery_input, recovery_memory, fault_flag, fault_flag, [*timer_instance, "Q"], output]
    accesses = "".join(
        f'<Access Scope="GlobalVariable" UId="{uid}"><Symbol>{_component_path(path or [])}</Symbol></Access>'
        for uid, path in enumerate(paths, 1)
    )
    accesses += f'<Access Scope="TypedConstant" UId="10"><Constant><ConstantValue>{escape(str(preset_time))}</ConstantValue></Constant></Access>'
    parts = accesses + (
        '<Part Name="Contact" UId="20" /><Part Name="PBox" UId="21" /><Part Name="SCoil" UId="22" />'
        '<Part Name="Contact" UId="23" /><Part Name="PBox" UId="24" /><Part Name="RCoil" UId="25" />'
        '<Part Name="Contact" UId="26" />'
        f'<Part Name="TON" Version="1.0" UId="27"><Instance Scope="GlobalVariable" UId="28">{_component_path(timer_instance or [])}</Instance><TemplateValue Name="time_type" Type="Type">Time</TemplateValue></Part>'
        '<Part Name="Contact" UId="29" /><Part Name="Coil" UId="30" />'
    )
    wire_values = [
        '<Powerrail /><NameCon UId="20" Name="in" /><NameCon UId="23" Name="in" /><NameCon UId="26" Name="in" /><NameCon UId="29" Name="in" />',
        '<IdentCon UId="1" /><NameCon UId="20" Name="operand" />',
        '<NameCon UId="20" Name="out" /><NameCon UId="21" Name="in" />',
        '<IdentCon UId="2" /><NameCon UId="21" Name="bit" />',
        '<NameCon UId="21" Name="out" /><NameCon UId="22" Name="in" />',
        '<IdentCon UId="3" /><NameCon UId="22" Name="operand" />',
        '<IdentCon UId="4" /><NameCon UId="23" Name="operand" />',
        '<NameCon UId="23" Name="out" /><NameCon UId="24" Name="in" />',
        '<IdentCon UId="5" /><NameCon UId="24" Name="bit" />',
        '<NameCon UId="24" Name="out" /><NameCon UId="25" Name="in" />',
        '<IdentCon UId="6" /><NameCon UId="25" Name="operand" />',
        '<IdentCon UId="7" /><NameCon UId="26" Name="operand" />',
        '<NameCon UId="26" Name="out" /><NameCon UId="27" Name="IN" />',
        '<IdentCon UId="10" /><NameCon UId="27" Name="PT" />',
        '<IdentCon UId="8" /><NameCon UId="29" Name="operand" />',
        '<NameCon UId="29" Name="out" /><NameCon UId="30" Name="in" />',
        '<IdentCon UId="9" /><NameCon UId="30" Name="operand" />',
    ]
    wires = "".join(f'<Wire UId="{uid}">{value}</Wire>' for uid, value in enumerate(wire_values, 40))
    return _compile_unit(parts, wires, title, comment)


def render_knowledge_network(renderer_kind: str, **bindings) -> str:
    if renderer_kind == "blueprint_network_v17":
        return render_blueprint_network_v17(
            blueprint=bindings.get("blueprint"), title=bindings["title"], comment=bindings["comment"],
        )
    if renderer_kind == "block_call_v17":
        return render_block_call_v17(
            blueprint=bindings.get("blueprint"), title=bindings["title"], comment=bindings["comment"],
        )
    if renderer_kind in {"contact_or_coil", "contact_or_pbox_scoil"}:
        return render_contact_or_network(
            renderer_kind,
            contacts=bindings.get("contacts") or [], output=bindings.get("output") or [],
            edge_memory=bindings.get("edge_memory"), title=bindings["title"], comment=bindings["comment"],
        )
    if renderer_kind == "compare_ge_real_coil":
        return render_compare_ge_real_coil(
            compare_input=bindings.get("compare_input"), compare_constant=bindings.get("compare_constant"),
            output=bindings.get("output") or [], title=bindings["title"], comment=bindings["comment"],
        )
    if renderer_kind == "pbox_set_reset_ton_coil":
        blueprint = bindings.get("blueprint")
        if isinstance(blueprint, dict):
            values = blueprint.get("operands") or {}

            def path(name: str) -> list[str] | None:
                value = values.get(name)
                if isinstance(value, dict):
                    return [str(item) for item in value.get("path", [])]
                if isinstance(value, list):
                    return [str(item) for item in value]
                return None

            bindings = {
                **bindings,
                "failure_input": path("failure_input"),
                "failure_memory": path("failure_memory"),
                "recovery_input": path("recovery_input"),
                "recovery_memory": path("recovery_memory"),
                "fault_flag": path("fault_flag"),
                "timer_instance": path("timer_instance"),
                "preset_time": values.get("preset_time"),
                "output": path("output") or [],
            }
        return render_pbox_set_reset_ton_coil(
            failure_input=bindings.get("failure_input"), failure_memory=bindings.get("failure_memory"),
            recovery_input=bindings.get("recovery_input"), recovery_memory=bindings.get("recovery_memory"),
            fault_flag=bindings.get("fault_flag"), timer_instance=bindings.get("timer_instance"),
            preset_time=bindings.get("preset_time"), output=bindings.get("output") or [],
            title=bindings["title"], comment=bindings["comment"],
        )
    raise ArtifactError("KNOWLEDGE_RENDERER_UNSUPPORTED", f"unsupported knowledge renderer: {renderer_kind}")
