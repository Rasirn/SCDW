"""Small, catalog-backed renderers for reviewed V17 topology recipes.

This intentionally supports only explicitly published recipes.  It is not a
general LAD rules engine and never guesses Part names, ports, or versions.
"""
from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from .models import ArtifactError


RENDERABLE_KINDS = {"contact_or_coil", "contact_or_pbox_scoil"}


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
