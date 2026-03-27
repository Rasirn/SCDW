# -*- coding: utf-8 -*-
"""
tia_lad_builder.py
将结构化的梯形图 JSON 描述转换为 TIA Portal V17 SimaticML/XML。

设计哲学
--------
LLM / MCP Agent 不直接生成 XML，而是提供简单的 JSON 梯级描述。
本模块负责将 JSON → 正确的 V17 FlgNet XML，确保版本兼容性和结构正确性。

支持的 LAD 元素
-------------
- Contact    常开触点（NO）
- NegContact 常闭触点（NC）
- Coil       输出线圈
- SCoil      SET 线圈
- RCoil      RESET 线圈

梯级拓扑
-------
1. 串联（AND）：contacts 列表，所有触点顺序串联 → outputs
2. 并联（OR）：branches 列表，每条支路内串联，多支路并联经 Or 节点 → outputs

OR 逻辑备选方案（无需 Or 节点）
------------------------------
对于复杂 OR 条件，推荐将逻辑拆成多个梯级：
  梯级 A：条件1 → SCoil(flag)
  梯级 B：条件2 → SCoil(flag)
  梯级 C：条件3（停止） → RCoil(flag)
这样更贴近工业编程习惯，也避免嵌套 XML 复杂度。

Agent 使用的 JSON 格式
--------------------
串联形式：
{
  "title": "电机启动",
  "comment": "启停控制",
  "contacts": [
    {"var": "DB1.Start", "nc": false},
    {"var": "DB1.Stop",  "nc": true}
  ],
  "outputs": [
    {"var": "DB1.MotorRun", "type": "Coil"}
  ]
}

并联形式（OR 逻辑）：
{
  "title": "多条件启动",
  "branches": [
    [{"var": "DB1.RemoteStart", "nc": false}],
    [{"var": "DB1.LocalStart",  "nc": false}, {"var": "DB1.SafeOK", "nc": false}]
  ],
  "outputs": [{"var": "DB1.MotorRun", "type": "SCoil"}]
}

var 路径规则
----------
- 全局标签（I/O点）：直接写变量名，如 "StartButton"
- DB 成员：用点分隔，如 "DB_Motor.SpeedRef"
- 多级成员：如 "DB_Motor.Status.Running"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class LadContact:
    """LAD 触点。"""
    var_path: str          # 变量路径，如 "DB1.Start" 或 "StartButton"
    negated: bool = False  # True → 常闭 NegContact；False → 常开 Contact


@dataclass
class LadOutput:
    """LAD 输出线圈。"""
    var_path: str
    coil_type: str = "Coil"  # Coil | SCoil | RCoil


@dataclass
class LadBranch:
    """并联支路（支路内触点串联）。"""
    contacts: List[LadContact] = field(default_factory=list)


@dataclass
class LadNetwork:
    """一个梯级（Rung / CompileUnit）。"""
    title: str = ""
    comment: str = ""
    branches: List[LadBranch] = field(default_factory=list)   # 并联支路
    outputs: List[LadOutput] = field(default_factory=list)


# ── UId / ID 生成器 ───────────────────────────────────────────────────────────

class _Counter:
    """全局递增 ID 生成器（文档内 ID 和 UId 共用同一命名空间以保证唯一性）。"""

    def __init__(self, start: int = 1):
        self._v = start - 1

    def next(self) -> int:
        self._v += 1
        return self._v


# ── 内部 XML 生成辅助 ─────────────────────────────────────────────────────────

def _components(var_path: str) -> List[str]:
    """拆分变量路径为组件列表：'DB1.Start' → ['DB1', 'Start']。"""
    return [p.strip() for p in var_path.split(".") if p.strip()]


def _access(uid: int, var_path: str) -> str:
    comps = _components(var_path)
    inner = "".join(f"              <Component Name=\"{c}\" />\n" for c in comps)
    return (
        f'            <Access Scope="GlobalVariable" UId="{uid}">\n'
        f'              <Symbol>\n'
        f'{inner}'
        f'              </Symbol>\n'
        f'            </Access>\n'
    )


def _part(name: str, uid: int) -> str:
    return f'            <Part Name="{name}" UId="{uid}" />\n'


def _part_nc(uid: int) -> str:
    """TIA V17 FlgNet 常闭触点：Contact + <Negated Name="operand" />。"""
    return (
        f'            <Part Name="Contact" UId="{uid}">\n'
        f'              <Negated Name="operand" />\n'
        f'            </Part>\n'
    )


def _part_or(uid: int, card: int) -> str:
    return (
        f'            <Part Name="Or" UId="{uid}">\n'
        f'              <TemplateValue Name="Card" Type="Cardinality">{card}</TemplateValue>\n'
        f'            </Part>\n'
    )


def _wire_rail_to_in(w: int, to: int) -> str:
    return (
        f'            <Wire UId="{w}">\n'
        f'              <Powerrail />\n'
        f'              <NameCon UId="{to}" Name="in" />\n'
        f'            </Wire>\n'
    )


def _wire_ident_to_operand(w: int, access: int, part: int) -> str:
    return (
        f'            <Wire UId="{w}">\n'
        f'              <IdentCon UId="{access}" />\n'
        f'              <NameCon UId="{part}" Name="operand" />\n'
        f'            </Wire>\n'
    )


def _wire_out_to_in(w: int, frm: int, to: int) -> str:
    return (
        f'            <Wire UId="{w}">\n'
        f'              <NameCon UId="{frm}" Name="out" />\n'
        f'              <NameCon UId="{to}" Name="in" />\n'
        f'            </Wire>\n'
    )


def _wire_namecon_to_namecon(w: int, frm: int, frm_name: str, to: int, to_name: str) -> str:
    return (
        f'            <Wire UId="{w}">\n'
        f'              <NameCon UId="{frm}" Name="{frm_name}" />\n'
        f'              <NameCon UId="{to}" Name="{to_name}" />\n'
        f'            </Wire>\n'
    )


# ── 支路逻辑生成（返回 last_contact_uid 和 XML 片段） ─────────────────────────

def _build_series_chain(contacts: List[LadContact], cnt: _Counter):
    """
    生成一条串联触点链，返回 (last_contact_uid, parts_xml, wires_xml)。
    如果 contacts 为空，last_contact_uid = None。
    """
    parts_xml = ""
    wires_xml = ""
    access_uids: List[int] = []
    contact_uids: List[int] = []

    for c in contacts:
        a_uid = cnt.next()
        p_uid = cnt.next()
        access_uids.append(a_uid)
        contact_uids.append(p_uid)
        parts_xml += _access(a_uid, c.var_path)
        if c.negated:
            parts_xml += _part_nc(p_uid)
        else:
            parts_xml += _part("Contact", p_uid)

    if contact_uids:
        # Powerrail → 第一个触点 in
        wires_xml += _wire_rail_to_in(cnt.next(), contact_uids[0])
        # 每个触点的 operand
        for a, p in zip(access_uids, contact_uids):
            wires_xml += _wire_ident_to_operand(cnt.next(), a, p)
        # 串联连线
        for i in range(len(contact_uids) - 1):
            wires_xml += _wire_out_to_in(cnt.next(), contact_uids[i], contact_uids[i + 1])

    last = contact_uids[-1] if contact_uids else None
    return last, parts_xml, wires_xml


def _connect_to_outputs(
    last_uid: int | None,
    outputs: List[LadOutput],
    cnt: _Counter,
    from_name: str = "out",
) -> tuple[str, str]:
    """生成输出线圈的 parts_xml 和 wires_xml。"""
    parts_xml = ""
    wires_xml = ""

    for out in outputs:
        a_uid = cnt.next()
        c_uid = cnt.next()
        parts_xml += _access(a_uid, out.var_path)
        parts_xml += _part(out.coil_type, c_uid)

        if last_uid is not None:
            wires_xml += _wire_namecon_to_namecon(cnt.next(), last_uid, from_name, c_uid, "in")
        else:
            wires_xml += _wire_rail_to_in(cnt.next(), c_uid)

        wires_xml += _wire_ident_to_operand(cnt.next(), a_uid, c_uid)

    return parts_xml, wires_xml


# ── 网络（CompileUnit）XML 生成 ───────────────────────────────────────────────

def _build_compile_unit(network: LadNetwork, cnt: _Counter) -> str:
    cu_id = cnt.next()
    parts_xml = ""
    wires_xml = ""

    branches = [b for b in network.branches if b.contacts]  # 过滤空支路
    outputs = network.outputs

    if not branches:
        # 无条件输出（直接 Powerrail → Coil）
        op_p, op_w = _connect_to_outputs(None, outputs, cnt)
        parts_xml += op_p
        wires_xml += op_w

    elif len(branches) == 1:
        # 纯串联
        last, bp, bw = _build_series_chain(branches[0].contacts, cnt)
        parts_xml += bp
        wires_xml += bw
        op_p, op_w = _connect_to_outputs(last, outputs, cnt)
        parts_xml += op_p
        wires_xml += op_w

    else:
        # 并联：每条支路各自串联，汇入 Or 节点
        or_uid = cnt.next()
        parts_xml += _part_or(or_uid, len(branches))

        for b_idx, branch in enumerate(branches):
            in_name = f"in{b_idx + 1}"
            last, bp, bw = _build_series_chain(branch.contacts, cnt)
            parts_xml += bp
            wires_xml += bw

            if last is not None:
                wires_xml += _wire_namecon_to_namecon(cnt.next(), last, "out", or_uid, in_name)
            else:
                # 空支路：Powerrail 直接接 Or.inX
                wires_xml += (
                    f'            <Wire UId="{cnt.next()}">\n'
                    f'              <Powerrail />\n'
                    f'              <NameCon UId="{or_uid}" Name="{in_name}" />\n'
                    f'            </Wire>\n'
                )

        # Or.out → 输出线圈
        op_p, op_w = _connect_to_outputs(or_uid, outputs, cnt, from_name="out")
        parts_xml += op_p
        wires_xml += op_w

    # 网络内部的 MultilingualText
    net_comment_id = cnt.next()
    net_comment_item_id = cnt.next()
    net_title_id = cnt.next()
    net_title_item_id = cnt.next()

    title = network.title or ""
    comment = network.comment or ""

    return (
        f'      <SW.Blocks.CompileUnit ID="{cu_id}" CompositionName="CompileUnits">\n'
        f'        <AttributeList>\n'
        f'          <NetworkSource>\n'
        f'            <FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4">\n'
        f'              <Parts>\n'
        f'{parts_xml}'
        f'              </Parts>\n'
        f'              <Wires>\n'
        f'{wires_xml}'
        f'              </Wires>\n'
        f'            </FlgNet>\n'
        f'          </NetworkSource>\n'
        f'          <ProgrammingLanguage>LAD</ProgrammingLanguage>\n'
        f'        </AttributeList>\n'
        f'        <ObjectList>\n'
        f'          <MultilingualText ID="{net_comment_id}" CompositionName="Comment">\n'
        f'            <ObjectList>\n'
        f'              <MultilingualTextItem ID="{net_comment_item_id}" CompositionName="Items">\n'
        f'                <AttributeList>\n'
        f'                  <Culture>zh-CN</Culture>\n'
        f'                  <Text>{comment}</Text>\n'
        f'                </AttributeList>\n'
        f'              </MultilingualTextItem>\n'
        f'            </ObjectList>\n'
        f'          </MultilingualText>\n'
        f'          <MultilingualText ID="{net_title_id}" CompositionName="Title">\n'
        f'            <ObjectList>\n'
        f'              <MultilingualTextItem ID="{net_title_item_id}" CompositionName="Items">\n'
        f'                <AttributeList>\n'
        f'                  <Culture>zh-CN</Culture>\n'
        f'                  <Text>{title}</Text>\n'
        f'                </AttributeList>\n'
        f'              </MultilingualTextItem>\n'
        f'            </ObjectList>\n'
        f'          </MultilingualText>\n'
        f'        </ObjectList>\n'
        f'      </SW.Blocks.CompileUnit>\n'
    )


# ── 顶层接口 ──────────────────────────────────────────────────────────────────

_FC_INTERFACE = """\
      <Interface>
        <Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/v5">
          <Section Name="Input" />
          <Section Name="Output" />
          <Section Name="InOut" />
          <Section Name="Temp" />
          <Section Name="Constant" />
          <Section Name="Return">
            <Member Name="Ret_Val" Datatype="Void" Accessibility="Public" />
          </Section>
        </Sections>
      </Interface>"""

_FB_INTERFACE = """\
      <Interface>
        <Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/v5">
          <Section Name="Input" />
          <Section Name="Output" />
          <Section Name="InOut" />
          <Section Name="Static" />
          <Section Name="Temp" />
          <Section Name="Constant" />
        </Sections>
      </Interface>"""

_OB_INTERFACE = """\
      <Interface>
        <Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/v5">
          <Section Name="Temp" />
          <Section Name="Constant" />
        </Sections>
      </Interface>"""

_INTERFACES = {"FC": _FC_INTERFACE, "FB": _FB_INTERFACE, "OB": _OB_INTERFACE}


def build_lad_xml(
    block_name: str,
    block_type: str,
    block_number: int,
    networks: List[LadNetwork],
    tia_version: str = "V17",
) -> str:
    """
    生成完整的 TIA Portal LAD 块 SimaticML/XML。

    Args:
        block_name:   块名称，如 "FC_Motor"
        block_type:   "FC" | "FB" | "OB"
        block_number: 块编号
        networks:     LadNetwork 列表
        tia_version:  TIA Portal 版本字符串，默认 "V17"

    Returns:
        完整 XML 字符串（UTF-8，带 BOM 声明）
    """
    cnt = _Counter(start=1)
    block_tag = f"SW.Blocks.{block_type.upper()}"
    interface_xml = _INTERFACES.get(block_type.upper(), _FC_INTERFACE)

    # 块级 MultilingualText IDs
    blk_comment_id = cnt.next()
    blk_comment_item_id = cnt.next()

    # 生成所有网络
    networks_xml = ""
    for net in networks:
        networks_xml += _build_compile_unit(net, cnt)

    blk_title_id = cnt.next()
    blk_title_item_id = cnt.next()

    return (
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f'<Document>\n'
        f'  <Engineering version="{tia_version}" />\n'
        f'  <DocumentInfo>\n'
        f'    <Created>2026-01-01T00:00:00.0000000Z</Created>\n'
        f'    <ExportSetting>WithDefaults</ExportSetting>\n'
        f'    <InstalledProducts>\n'
        f'      <Product>\n'
        f'        <DisplayName>Totally Integrated Automation Portal</DisplayName>\n'
        f'        <DisplayVersion>{tia_version} Update 4</DisplayVersion>\n'
        f'      </Product>\n'
        f'      <OptionPackage>\n'
        f'        <DisplayName>TIA Portal Openness</DisplayName>\n'
        f'        <DisplayVersion>{tia_version} Update 4</DisplayVersion>\n'
        f'      </OptionPackage>\n'
        f'      <Product>\n'
        f'        <DisplayName>STEP 7 Professional</DisplayName>\n'
        f'        <DisplayVersion>{tia_version} Update 4</DisplayVersion>\n'
        f'      </Product>\n'
        f'    </InstalledProducts>\n'
        f'  </DocumentInfo>\n'
        f'\n'
        f'  <{block_tag} ID="0">\n'
        f'    <AttributeList>\n'
        f'      <AutoNumber>true</AutoNumber>\n'
        f'      <HeaderAuthor />\n'
        f'      <HeaderFamily />\n'
        f'      <HeaderName />\n'
        f'      <HeaderVersion>0.1</HeaderVersion>\n'
        f'{interface_xml}\n'
        f'      <IsIECCheckEnabled>false</IsIECCheckEnabled>\n'
        f'      <MemoryLayout>Optimized</MemoryLayout>\n'
        f'      <Name>{block_name}</Name>\n'
        f'      <Number>{block_number}</Number>\n'
        f'      <ProgrammingLanguage>LAD</ProgrammingLanguage>\n'
        f'      <SetENOAutomatically>false</SetENOAutomatically>\n'
        f'      <UDABlockProperties />\n'
        f'      <UDAEnableTagReadback>false</UDAEnableTagReadback>\n'
        f'    </AttributeList>\n'
        f'    <ObjectList>\n'
        f'      <MultilingualText ID="{blk_comment_id}" CompositionName="Comment">\n'
        f'        <ObjectList>\n'
        f'          <MultilingualTextItem ID="{blk_comment_item_id}" CompositionName="Items">\n'
        f'            <AttributeList>\n'
        f'              <Culture>zh-CN</Culture>\n'
        f'              <Text />\n'
        f'            </AttributeList>\n'
        f'          </MultilingualTextItem>\n'
        f'        </ObjectList>\n'
        f'      </MultilingualText>\n'
        f'{networks_xml}'
        f'      <MultilingualText ID="{blk_title_id}" CompositionName="Title">\n'
        f'        <ObjectList>\n'
        f'          <MultilingualTextItem ID="{blk_title_item_id}" CompositionName="Items">\n'
        f'            <AttributeList>\n'
        f'              <Culture>zh-CN</Culture>\n'
        f'              <Text>{block_name}</Text>\n'
        f'            </AttributeList>\n'
        f'          </MultilingualTextItem>\n'
        f'        </ObjectList>\n'
        f'      </MultilingualText>\n'
        f'    </ObjectList>\n'
        f'  </{block_tag}>\n'
        f'</Document>'
    )


def lad_networks_from_json(networks_json: list) -> List[LadNetwork]:
    """
    从 JSON 描述列表构建 LadNetwork 列表。

    两种格式均支持（可混用）：

    串联格式（contacts 为顶层列表）：
    {
        "title": "电机启动",
        "comment": "说明",
        "contacts": [
            {"var": "DB1.Start", "nc": false},
            {"var": "DB1.Stop",  "nc": true}
        ],
        "outputs": [
            {"var": "DB1.Motor", "type": "Coil"}
        ]
    }

    并联格式（branches 为顶层列表）：
    {
        "title": "多条件启动",
        "branches": [
            [{"var": "DB1.RemoteStart", "nc": false}],
            [{"var": "DB1.LocalStart", "nc": false}, {"var": "DB1.SafeOK", "nc": false}]
        ],
        "outputs": [{"var": "DB1.Motor", "type": "SCoil"}]
    }
    """
    result: List[LadNetwork] = []
    for nd in networks_json:
        outputs = [
            LadOutput(var_path=o["var"], coil_type=o.get("type", "Coil"))
            for o in nd.get("outputs", [])
        ]

        if "branches" in nd:
            branches = [
                LadBranch(contacts=[
                    LadContact(var_path=c["var"], negated=bool(c.get("nc", False)))
                    for c in branch
                ])
                for branch in nd["branches"]
            ]
        else:
            contacts = [
                LadContact(var_path=c["var"], negated=bool(c.get("nc", False)))
                for c in nd.get("contacts", [])
            ]
            branches = [LadBranch(contacts=contacts)] if contacts else []

        result.append(LadNetwork(
            title=nd.get("title", ""),
            comment=nd.get("comment", ""),
            branches=branches,
            outputs=outputs,
        ))
    return result
