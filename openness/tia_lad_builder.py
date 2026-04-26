# -*- coding: utf-8 -*-
"""
tia_lad_builder.py
将结构化的梯形图 JSON 描述转换为 TIA Portal V17 SimaticML/XML。

设计哲学
--------
LLM / MCP Agent 不直接生成 XML，而是提供简单的 JSON 梯级描述。
本模块负责将 JSON → 正确的 V17 FlgNet XML，确保版本兼容性和结构正确性。

支持的 LAD 元素
--------------
触点类型：
  - Contact      常开触点（NO）
  - NegContact   常闭触点（NC）
  - PContact     上升沿检测触点（P）
  - NContact     下降沿检测触点（N）

线圈类型：
  - Coil         输出线圈
  - SCoil        SET 置位线圈
  - RCoil        RESET 复位线圈

功能块类型（Box 指令）：
  - TON          接通延时定时器
  - TOF          断开延时定时器
  - TP           脉冲定时器
  - CTU          递增计数器
  - CTD          递减计数器
  - CTUD         递增/递减计数器
  - Move         赋值（MOVE）
  - Add / Sub / Mul / Div   数学运算
  - Eq / Ne / Gt / Lt / Ge / Le  比较运算
  - SR / RS      触发器
  - TON_TIME / TOF_TIME / TP_TIME   IEC 定时器变体

梯级拓扑
-------
1. 串联（AND）：contacts + boxes 按顺序串联 → outputs
2. 并联（OR）：branches 列表，每条支路内可含触点和 box → Or 节点 → outputs
3. Box 指令：放在触点与输出之间，有多个输入/输出引脚

Agent 使用的 JSON 格式
--------------------

基本串联（AND）：
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

带定时器的串联：
{
  "title": "延时启动",
  "contacts": [{"var": "DB1.Start", "nc": false}],
  "boxes": [
    {
      "type": "TON",
      "instance_db": "DB_Motor.StartDelay",
      "params": {"PT": "T#5s"},
      "outputs_from": {"Q": "DB1.DelayDone"}
    }
  ],
  "outputs": [{"var": "DB1.MotorRun", "type": "Coil"}]
}

带比较器的串联：
{
  "title": "温度超限报警",
  "contacts": [{"var": "DB1.Enable", "nc": false}],
  "boxes": [
    {
      "type": "Gt",
      "params": {"in1": "DB1.Temperature", "in2": "DB1.HighLimit"}
    }
  ],
  "outputs": [{"var": "DB1.TempAlarm", "type": "SCoil"}]
}

带 MOVE 的赋值：
{
  "title": "参数传递",
  "contacts": [{"var": "DB1.Enable", "nc": false}],
  "boxes": [
    {
      "type": "Move",
      "params": {"in": "DB1.SetPoint"},
      "outputs_from": {"out1": "DB1.ActualValue"}
    }
  ],
  "outputs": []
}

并联（OR）格式：
{
  "title": "多条件启动",
  "branches": [
    {"contacts": [{"var": "DB1.RemoteStart", "nc": false}]},
    {"contacts": [{"var": "DB1.LocalStart", "nc": false}, {"var": "DB1.SafeOK", "nc": false}]}
  ],
  "outputs": [{"var": "DB1.MotorRun", "type": "SCoil"}]
}

并联支路内带 box：
{
  "title": "复合逻辑",
  "branches": [
    {
      "contacts": [{"var": "DB1.ManualMode", "nc": false}],
      "boxes": [{"type": "Gt", "params": {"in1": "DB1.Temp", "in2": "80.0"}}]
    },
    {
      "contacts": [{"var": "DB1.AutoMode", "nc": false}]
    }
  ],
  "outputs": [{"var": "DB1.Alarm", "type": "Coil"}]
}

边沿触点：
{
  "title": "上升沿启动",
  "contacts": [
    {"var": "DB1.Start", "nc": false, "edge": "P"}
  ],
  "outputs": [{"var": "DB1.Trigger", "type": "SCoil"}]
}

var 路径规则
----------
- 全局标签（I/O点）：直接写变量名，如 "StartButton"
- DB 成员：用点分隔，如 "DB_Motor.SpeedRef"
- 多级成员：如 "DB_Motor.Status.Running"
- 常量值：数字、T#时间格式，如 "T#5s"、"80.0"、"100"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class LadContact:
    """LAD 触点。"""
    var_path: str          # 变量路径，如 "DB1.Start" 或 "StartButton"
    negated: bool = False  # True → 常闭 NegContact；False → 常开 Contact
    edge: str = ""         # "P" → 上升沿, "N" → 下降沿, "" → 普通触点


@dataclass
class LadBox:
    """
    功能块 / 系统指令调用（Box 元素）。

    在 FlgNet 中表现为 Part + 多个 Access + Wire。
    信号流：EN(in) → Box → ENO(out)
    数据流：各 pin 通过 IdentCon 连接到 Access。
    """
    box_type: str                     # "TON" | "TOF" | "Move" | "Gt" | "Add" | "Convert" 等
    instance_db: str = ""             # 实例 DB（定时器/计数器需要）
    params: Dict[str, str] = field(default_factory=dict)       # pin_name → var_path
    outputs_from: Dict[str, str] = field(default_factory=dict) # pin_name → var_path
    src_type: str = ""                # 显式数据类型（比较/数学运算需要，如 "Int", "Real"）
    dest_type: str = ""               # 目标数据类型（类型转换指令需要，如 Convert: Real→Int）


@dataclass
class LadOutput:
    """LAD 输出线圈。"""
    var_path: str
    coil_type: str = "Coil"  # Coil | SCoil | RCoil


@dataclass
class LadBranch:
    """并联支路（支路内触点串联 + 可选的 box 指令）。"""
    contacts: List[LadContact] = field(default_factory=list)
    boxes: List[LadBox] = field(default_factory=list)


@dataclass
class LadNetwork:
    """一个梯级（Rung / CompileUnit）。"""
    title: str = ""
    comment: str = ""
    branches: List[LadBranch] = field(default_factory=list)   # 并联支路
    boxes: List[LadBox] = field(default_factory=list)         # 顶层 box（串联在触点之后）
    outputs: List[LadOutput] = field(default_factory=list)


# ── UId / ID 生成器 ───────────────────────────────────────────────────────────

class _Counter:
    """全局递增 ID 生成器。"""

    def __init__(self, start: int = 1):
        self._v = start - 1

    def next(self) -> int:
        self._v += 1
        return self._v


# ── Box 元数据定义 ────────────────────────────────────────────────────────────

_BOX_META = {
    # ── 定时器 ──
    "TON":  {"input_pins": ["PT"], "output_pins": ["Q", "ET"], "needs_instance": True},
    "TOF":  {"input_pins": ["PT"], "output_pins": ["Q", "ET"], "needs_instance": True},
    "TP":   {"input_pins": ["PT"], "output_pins": ["Q", "ET"], "needs_instance": True},
    "TON_TIME":  {"input_pins": ["PT"], "output_pins": ["Q", "ET"], "needs_instance": True},
    "TOF_TIME":  {"input_pins": ["PT"], "output_pins": ["Q", "ET"], "needs_instance": True},
    "TP_TIME":   {"input_pins": ["PT"], "output_pins": ["Q", "ET"], "needs_instance": True},
    # ── 计数器 ──
    "CTU":  {"input_pins": ["PV"], "output_pins": ["Q", "CV"],
             "signal_in": "cu", "needs_instance": True},
    "CTD":  {"input_pins": ["PV"], "output_pins": ["Q", "CV"],
             "signal_in": "cd", "needs_instance": True},
    "CTUD": {"input_pins": ["PV"], "output_pins": ["QU", "QD", "CV"],
             "signal_in": "cu", "needs_instance": True},
    # ── 赋值 ──
    "Move": {"input_pins": ["in"], "output_pins": ["out1"],
             "signal_in": "en", "signal_out": "eno"},
    # ── 数学运算 ──
    # Add/Mul 支持可变数量输入 (in1..inN)，需要 Card
    # Sub/Div 固定 2 个输入，不接受 Card
    "Add":  {"input_pins": ["in1", "in2"], "output_pins": ["out"],
             "signal_in": "en", "signal_out": "eno", "needs_card": True},
    "Sub":  {"input_pins": ["in1", "in2"], "output_pins": ["out"],
             "signal_in": "en", "signal_out": "eno"},
    "Mul":  {"input_pins": ["in1", "in2"], "output_pins": ["out"],
             "signal_in": "en", "signal_out": "eno", "needs_card": True},
    "Div":  {"input_pins": ["in1", "in2"], "output_pins": ["out"],
             "signal_in": "en", "signal_out": "eno"},
    # ── 比较运算（信号 pre→out，无 EN/ENO）──
    "Eq":   {"input_pins": ["in1", "in2"], "output_pins": [],
             "signal_in": "pre", "signal_out": "out", "is_compare": True},
    "Ne":   {"input_pins": ["in1", "in2"], "output_pins": [],
             "signal_in": "pre", "signal_out": "out", "is_compare": True},
    "Gt":   {"input_pins": ["in1", "in2"], "output_pins": [],
             "signal_in": "pre", "signal_out": "out", "is_compare": True},
    "Lt":   {"input_pins": ["in1", "in2"], "output_pins": [],
             "signal_in": "pre", "signal_out": "out", "is_compare": True},
    "Ge":   {"input_pins": ["in1", "in2"], "output_pins": [],
             "signal_in": "pre", "signal_out": "out", "is_compare": True},
    "Le":   {"input_pins": ["in1", "in2"], "output_pins": [],
             "signal_in": "pre", "signal_out": "out", "is_compare": True},
    # ── 触发器 ──
    "SR":   {"input_pins": ["S1", "R"], "output_pins": ["Q"],
             "signal_in": "S1", "needs_instance": False},
    "RS":   {"input_pins": ["S", "R1"], "output_pins": ["Q"],
             "signal_in": "R1", "needs_instance": False},
    # ── 类型转换 ──
    "Convert": {"input_pins": ["in"], "output_pins": ["out"],
                "signal_in": "en", "signal_out": "eno", "needs_dest_type": True},
    "Round":   {"input_pins": ["in"], "output_pins": ["out"],
                "signal_in": "en", "signal_out": "eno", "needs_dest_type": True},
    "Trunc":   {"input_pins": ["in"], "output_pins": ["out"],
                "signal_in": "en", "signal_out": "eno", "needs_dest_type": True},
    "Ceil":    {"input_pins": ["in"], "output_pins": ["out"],
                "signal_in": "en", "signal_out": "eno", "needs_dest_type": True},
    "Floor":   {"input_pins": ["in"], "output_pins": ["out"],
                "signal_in": "en", "signal_out": "eno", "needs_dest_type": True},
}


def _get_box_meta(box_type: str) -> dict:
    """获取 Box 类型的元数据。"""
    meta = _BOX_META.get(box_type)
    if meta is None:
        for k, v in _BOX_META.items():
            if k.lower() == box_type.lower():
                meta = v
                break
    if meta is None:
        meta = {"input_pins": [], "output_pins": [], "needs_instance": False}
    return meta


# ── 内部 XML 生成辅助 ─────────────────────────────────────────────────────────

def _components(var_path: str) -> List[str]:
    """拆分变量路径：'DB1.Start' → ['DB1', 'Start']。"""
    return [p.strip() for p in var_path.split(".") if p.strip()]


def _is_constant_value(val: str) -> bool:
    """判断值是否是常量。"""
    s = val.strip()
    if not s:
        return False
    if s.upper().startswith(("T#", "LT#", "S5T#", "16#")):
        return True
    try:
        float(s)
        return True
    except ValueError:
        pass
    if s.upper() in ("TRUE", "FALSE"):
        return True
    return False


def _access_variable(uid: int, var_path: str) -> str:
    # 处理 # 前缀的局部变量（Temp / Static）
    stripped = var_path.lstrip("#")
    is_local = var_path.startswith("#")
    scope = "LocalVariable" if is_local else "GlobalVariable"
    comps = _components(stripped)
    inner = "".join(f'              <Component Name="{c}" />\n' for c in comps)
    return (
        f'            <Access Scope="{scope}" UId="{uid}">\n'
        f'              <Symbol>\n'
        f'{inner}'
        f'              </Symbol>\n'
        f'            </Access>\n'
    )


def _access_constant(uid: int, value: str, datatype: str = "") -> str:
    if not datatype:
        s = value.strip().upper()
        if s.startswith(("T#", "LT#")):
            datatype = "Time"
        elif s.startswith("S5T#"):
            datatype = "S5Time"
        elif s in ("TRUE", "FALSE"):
            datatype = "Bool"
        elif "." in value:
            datatype = "Real"
        else:
            datatype = "Int"
    return (
        f'            <Access Scope="LiteralConstant" UId="{uid}">\n'
        f'              <Constant>\n'
        f'                <ConstantType>{datatype}</ConstantType>\n'
        f'                <ConstantValue>{value}</ConstantValue>\n'
        f'              </Constant>\n'
        f'            </Access>\n'
    )


def _access_auto(uid: int, var_path: str) -> str:
    if _is_constant_value(var_path):
        return _access_constant(uid, var_path)
    return _access_variable(uid, var_path)


def _part(name: str, uid: int) -> str:
    return f'            <Part Name="{name}" UId="{uid}" />\n'


def _part_contact(uid: int, negated: bool = False, edge: str = "") -> str:
    if edge == "P":
        return f'            <Part Name="PContact" UId="{uid}" />\n'
    elif edge == "N":
        return f'            <Part Name="NContact" UId="{uid}" />\n'
    elif negated:
        return (
            f'            <Part Name="Contact" UId="{uid}">\n'
            f'              <Negated Name="operand" />\n'
            f'            </Part>\n'
        )
    else:
        return f'            <Part Name="Contact" UId="{uid}" />\n'


def _infer_type_from_params(params: Dict[str, str]) -> str:
    """从参数值推断数据类型（用于比较和数学运算的 SrcType）。"""
    for pin, val in params.items():
        if _is_constant_value(val):
            s = val.strip().upper()
            if s.startswith(("T#", "LT#")):
                return "Time"
            if s.startswith("S5T#"):
                return "S5Time"
            if s in ("TRUE", "FALSE"):
                return "Bool"
            if "." in val:
                return "Real"
            try:
                int(val)
                return "Int"
            except ValueError:
                pass
    return "Int"


def _part_box_with_instance(box_type: str, uid: int, instance_db: str, cnt: _Counter,
                            box: Optional[LadBox] = None) -> tuple:
    meta = _get_box_meta(box_type)
    canonical = box_type
    for k in _BOX_META:
        if k.lower() == box_type.lower():
            canonical = k
            break

    # 构建 TemplateValue 节点
    template_values = ""

    # Move 指令: Card = 输出目标数量 (out1, out2, ...)
    if canonical == "Move":
        card = len(box.outputs_from) if (box and box.outputs_from) else 1
        template_values += f'              <TemplateValue Name="Card" Type="Cardinality">{card}</TemplateValue>\n'

    # Add/Mul 支持可变输入数 (in1..inN)，需要 Card
    # Sub/Div 固定 2 输入，不接受 Card
    if meta.get("needs_card"):
        signal_in_name = meta.get("signal_in", "en")
        data_pins = [p for p in (box.params if box else {}) if p.lower() != signal_in_name.lower()]
        card = max(len(data_pins), 2)  # 至少 2 个输入
        template_values += f'              <TemplateValue Name="Card" Type="Cardinality">{card}</TemplateValue>\n'

    # 比较运算需要 SrcType
    if meta.get("is_compare"):
        resolved_type = (box.src_type if box and box.src_type
                         else _infer_type_from_params(box.params if box else {}))
        template_values += f'              <TemplateValue Name="SrcType" Type="Type">{resolved_type}</TemplateValue>\n'

    # 数学运算需要 SrcType
    if canonical in ("Add", "Sub", "Mul", "Div"):
        resolved_type = (box.src_type if box and box.src_type
                         else _infer_type_from_params(box.params if box else {}))
        template_values += f'              <TemplateValue Name="SrcType" Type="Type">{resolved_type}</TemplateValue>\n'

    # 类型转换指令需要 SrcType + DestType
    if meta.get("needs_dest_type"):
        resolved_src = (box.src_type if box and box.src_type
                        else _infer_type_from_params(box.params if box else {}))
        resolved_dest = (box.dest_type if box and box.dest_type else "Int")
        template_values += f'              <TemplateValue Name="SrcType" Type="Type">{resolved_src}</TemplateValue>\n'
        template_values += f'              <TemplateValue Name="DestType" Type="Type">{resolved_dest}</TemplateValue>\n'

    if meta.get("needs_instance", False) and instance_db:
        comps = _components(instance_db)
        inner_comps = "".join(f'                  <Component Name="{c}" />\n' for c in comps)
        xml = (
            f'            <Part Name="{canonical}" UId="{uid}">\n'
            f'{template_values}'
            f'              <Instance Scope="GlobalVariable" UId="{cnt.next()}">\n'
            f'                <Symbol>\n'
            f'{inner_comps}'
            f'                </Symbol>\n'
            f'              </Instance>\n'
            f'            </Part>\n'
        )
        return xml, 0
    elif template_values:
        xml = (
            f'            <Part Name="{canonical}" UId="{uid}">\n'
            f'{template_values}'
            f'            </Part>\n'
        )
        return xml, 0
    else:
        return f'            <Part Name="{canonical}" UId="{uid}" />\n', 0


def _part_or(uid: int, card: int) -> str:
    return (
        f'            <Part Name="Or" UId="{uid}">\n'
        f'              <TemplateValue Name="Card" Type="Cardinality">{card}</TemplateValue>\n'
        f'            </Part>\n'
    )


# ── Wire 辅助 ─────────────────────────────────────────────────────────────────

def _wire(uid: int, from_xml: str, to_xml: str) -> str:
    return (
        f'            <Wire UId="{uid}">\n'
        f'{from_xml}'
        f'{to_xml}'
        f'            </Wire>\n'
    )

def _powerrail() -> str:
    return '              <Powerrail />\n'

def _namecon(uid: int, name: str) -> str:
    return f'              <NameCon UId="{uid}" Name="{name}" />\n'

def _identcon(uid: int) -> str:
    return f'              <IdentCon UId="{uid}" />\n'

def _wire_rail_to_in(w: int, to: int) -> str:
    return _wire(w, _powerrail(), _namecon(to, "in"))

def _wire_ident_to_namecon(w: int, access: int, part: int, pin: str) -> str:
    return _wire(w, _identcon(access), _namecon(part, pin))

def _wire_out_to_in(w: int, frm: int, to: int) -> str:
    return _wire(w, _namecon(frm, "out"), _namecon(to, "in"))

def _wire_namecon_to_namecon(w: int, frm: int, frm_name: str, to: int, to_name: str) -> str:
    return _wire(w, _namecon(frm, frm_name), _namecon(to, to_name))

def _wire_namecon_to_ident(w: int, part: int, pin_name: str, access: int) -> str:
    return _wire(w, _namecon(part, pin_name), _identcon(access))


# ── Box 指令 XML 生成 ─────────────────────────────────────────────────────────

def _normalize_pin_name(box_type: str, pin_name: str, is_output: bool, meta: dict) -> str:
    """
    校正 LLM 常见的引脚名错误。

    典型错误：Mul.out1 → Mul.out，Move.out → Move.out1
    """
    valid_pins = meta.get("output_pins" if is_output else "input_pins", [])
    signal_in = meta.get("signal_in", "en")
    signal_out = meta.get("signal_out", "eno")
    all_valid = set(valid_pins + [signal_in, signal_out])

    # 如果引脚名已经正确，直接返回
    if pin_name in all_valid:
        return pin_name

    # 常见纠正规则
    if is_output:
        # out1 → out（适用于数学运算，Mul/Add/Sub/Div 的输出叫 out 不叫 out1）
        if pin_name == "out1" and "out" in valid_pins:
            return "out"
        # out → out1（适用于 Move，Move 的输出叫 out1 不叫 out）
        if pin_name == "out" and "out1" in valid_pins:
            return "out1"

    return pin_name


def _build_box(box, cnt, prev_uid, prev_out_name="out"):
    """
    生成一个 Box 指令的完整 XML。

    Returns: (box_uid, signal_out_name, parts_xml, wires_xml)
    """
    meta = _get_box_meta(box.box_type)
    signal_in = meta.get("signal_in", "en")
    signal_out = meta.get("signal_out", "eno")

    box_uid = cnt.next()
    parts_xml = ""
    wires_xml = ""

    box_part_xml, _ = _part_box_with_instance(box.box_type, box_uid, box.instance_db, cnt, box=box)
    parts_xml += box_part_xml

    # 信号连线：prev → Box.signal_in
    if prev_uid is not None:
        wires_xml += _wire_namecon_to_namecon(
            cnt.next(), prev_uid, prev_out_name, box_uid, signal_in
        )
    else:
        # 直接从 Powerrail 连接，使用正确的信号引脚名（如比较指令用 "pre"）
        wires_xml += _wire(cnt.next(), _powerrail(), _namecon(box_uid, signal_in))

    # 输入参数连线
    for pin_name, var_path in box.params.items():
        if pin_name.lower() == signal_in.lower():
            continue
        resolved_pin = _normalize_pin_name(box.box_type, pin_name, False, meta)
        a_uid = cnt.next()
        parts_xml += _access_auto(a_uid, var_path)
        wires_xml += _wire_ident_to_namecon(cnt.next(), a_uid, box_uid, resolved_pin)

    # 输出参数连线（比较指令无数据输出引脚，跳过其 outputs_from）
    if not meta.get("is_compare", False):
        for pin_name, var_path in box.outputs_from.items():
            resolved_pin = _normalize_pin_name(box.box_type, pin_name, True, meta)
            a_uid = cnt.next()
            parts_xml += _access_auto(a_uid, var_path)
            wires_xml += _wire_namecon_to_ident(cnt.next(), box_uid, resolved_pin, a_uid)

    return box_uid, signal_out, parts_xml, wires_xml


# ── 支路逻辑生成 ──────────────────────────────────────────────────────────────

def _build_series_chain(contacts, boxes, cnt, connect_powerrail=True):
    """
    生成一条串联链（触点 + boxes）。

    Returns: (last_uid, last_out_name, parts_xml, wires_xml)
    """
    parts_xml = ""
    wires_xml = ""
    access_uids = []
    contact_uids = []

    for c in contacts:
        a_uid = cnt.next()
        p_uid = cnt.next()
        access_uids.append(a_uid)
        contact_uids.append(p_uid)
        parts_xml += _access_variable(a_uid, c.var_path)
        parts_xml += _part_contact(p_uid, negated=c.negated, edge=c.edge)

    if contact_uids:
        if connect_powerrail:
            wires_xml += _wire_rail_to_in(cnt.next(), contact_uids[0])
        for a, p in zip(access_uids, contact_uids):
            wires_xml += _wire_ident_to_namecon(cnt.next(), a, p, "operand")
        for i in range(len(contact_uids) - 1):
            wires_xml += _wire_out_to_in(cnt.next(), contact_uids[i], contact_uids[i + 1])

    last_uid = contact_uids[-1] if contact_uids else None
    last_out_name = "out"

    for box in boxes:
        box_uid, sig_out, bp, bw = _build_box(
            box, cnt, prev_uid=last_uid, prev_out_name=last_out_name,
        )
        parts_xml += bp
        wires_xml += bw
        last_uid = box_uid
        last_out_name = sig_out

    return last_uid, last_out_name, parts_xml, wires_xml


def _connect_to_outputs(last_uid, outputs, cnt, from_name="out"):
    parts_xml = ""
    wires_xml = ""
    prev_coil_uid = None

    for out in outputs:
        a_uid = cnt.next()
        c_uid = cnt.next()
        parts_xml += _access_variable(a_uid, out.var_path)
        parts_xml += _part(out.coil_type, c_uid)

        if last_uid is not None:
            if prev_coil_uid is None:
                wires_xml += _wire_namecon_to_namecon(cnt.next(), last_uid, from_name, c_uid, "in")
            else:
                wires_xml += _wire_namecon_to_namecon(cnt.next(), prev_coil_uid, "out", c_uid, "in")
        else:
            wires_xml += _wire_rail_to_in(cnt.next(), c_uid)

        wires_xml += _wire_ident_to_namecon(cnt.next(), a_uid, c_uid, "operand")
        prev_coil_uid = c_uid

    return parts_xml, wires_xml


# ── 网络（CompileUnit）XML ────────────────────────────────────────────────────

def _build_compile_unit(network, cnt):
    cu_id = cnt.next()
    parts_xml = ""
    wires_xml = ""

    branches = [b for b in network.branches if b.contacts or b.boxes]
    outputs = network.outputs
    top_boxes = network.boxes

    if not branches:
        if not top_boxes:
            op_p, op_w = _connect_to_outputs(None, outputs, cnt)
            parts_xml += op_p
            wires_xml += op_w
        else:
            last_uid = None
            last_out = "out"
            for box in top_boxes:
                box_uid, sig_out, bp, bw = _build_box(box, cnt, last_uid, last_out)
                parts_xml += bp; wires_xml += bw
                last_uid = box_uid; last_out = sig_out
            op_p, op_w = _connect_to_outputs(last_uid, outputs, cnt, from_name=last_out)
            parts_xml += op_p; wires_xml += op_w

    elif len(branches) == 1:
        branch = branches[0]
        last, last_out, bp, bw = _build_series_chain(branch.contacts, branch.boxes, cnt)
        parts_xml += bp; wires_xml += bw

        for box in top_boxes:
            box_uid, sig_out, tbp, tbw = _build_box(box, cnt, last, last_out)
            parts_xml += tbp; wires_xml += tbw
            last = box_uid; last_out = sig_out

        op_p, op_w = _connect_to_outputs(last, outputs, cnt, from_name=last_out)
        parts_xml += op_p; wires_xml += op_w

    else:
        or_uid = cnt.next()
        parts_xml += _part_or(or_uid, len(branches))

        for b_idx, branch in enumerate(branches):
            in_name = f"in{b_idx + 1}"
            last, last_out, bp, bw = _build_series_chain(branch.contacts, branch.boxes, cnt)
            parts_xml += bp; wires_xml += bw

            if last is not None:
                wires_xml += _wire_namecon_to_namecon(cnt.next(), last, last_out, or_uid, in_name)
            else:
                wires_xml += _wire(cnt.next(), _powerrail(), _namecon(or_uid, in_name))

        last_uid = or_uid
        last_out = "out"

        for box in top_boxes:
            box_uid, sig_out, tbp, tbw = _build_box(box, cnt, last_uid, last_out)
            parts_xml += tbp; wires_xml += tbw
            last_uid = box_uid; last_out = sig_out

        op_p, op_w = _connect_to_outputs(last_uid, outputs, cnt, from_name=last_out)
        parts_xml += op_p; wires_xml += op_w

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


# ── OB 块 SecondaryType 映射 ──────────────────────────────────────────────────

def _get_ob_secondary_type(block_number: int) -> str:
    """根据 OB 编号返回 SecondaryType。"""
    if block_number == 1:
        return "ProgramCycle"
    if block_number == 100:
        return "Startup"
    if 10 <= block_number <= 17:
        return "TimeOfDay"
    if 20 <= block_number <= 23:
        return "TimeDelay"
    if 30 <= block_number <= 38:
        return "CyclicInterrupt"
    if 40 <= block_number <= 47:
        return "HardwareInterrupt"
    if block_number == 80:
        return "TimeError"
    if block_number == 82:
        return "DiagnosticError"
    if block_number == 83:
        return "PullOrPlugOfModules"
    if block_number == 86:
        return "RackOrStationFailure"
    if block_number == 121:
        return "ProgrammingError"
    if block_number == 122:
        return "IOAccessError"
    # 默认为程序循环 OB
    return "ProgramCycle"


def build_lad_xml(
    block_name: str,
    block_type: str,
    block_number: int,
    networks: List[LadNetwork],
    tia_version: str = "V17",
    secondary_type: str = "",
) -> str:
    """
    生成完整的 TIA Portal LAD 块 SimaticML/XML。

    Args:
        block_name:     块名称，如 "FC_Motor"
        block_type:     "FC" | "FB" | "OB"
        block_number:   块编号
        networks:       LadNetwork 列表
        tia_version:    TIA Portal 版本字符串
        secondary_type: OB 的 SecondaryType（仅 OB 需要，留空则自动推断）

    Returns:
        完整 XML 字符串
    """
    cnt = _Counter(start=1)
    block_tag = f"SW.Blocks.{block_type.upper()}"
    interface_xml = _INTERFACES.get(block_type.upper(), _FC_INTERFACE)

    # OB 块需要 SecondaryType
    secondary_type_xml = ""
    if block_type.upper() == "OB":
        st = secondary_type or _get_ob_secondary_type(block_number)
        secondary_type_xml = f'      <SecondaryType>{st}</SecondaryType>\n'

    blk_comment_id = cnt.next()
    blk_comment_item_id = cnt.next()

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
        f'{secondary_type_xml}'
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

    支持的格式（可混用）：

    基本串联格式（contacts 为顶层列表）：
    {
        "title": "电机启动",
        "contacts": [{"var": "DB1.Start", "nc": false}],
        "outputs": [{"var": "DB1.Motor", "type": "Coil"}]
    }

    带 Box 指令的串联格式：
    {
        "title": "延时启动",
        "contacts": [{"var": "DB1.Start", "nc": false}],
        "boxes": [{"type": "TON", "instance_db": "DB1.Timer", "params": {"PT": "T#5s"}}],
        "outputs": [{"var": "DB1.Motor", "type": "Coil"}]
    }

    并联格式（branches 为顶层列表）：
    {
        "title": "多条件启动",
        "branches": [
            {"contacts": [{"var": "DB1.Remote", "nc": false}]},
            {"contacts": [{"var": "DB1.Local", "nc": false}]}
        ],
        "outputs": [{"var": "DB1.Motor", "type": "SCoil"}]
    }

    branches 也兼容旧格式（纯列表形式）：
    {
        "branches": [
            [{"var": "DB1.Remote", "nc": false}],
            [{"var": "DB1.Local", "nc": false}]
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

        top_boxes = _parse_boxes(nd.get("boxes", []))

        if "branches" in nd:
            branches = []
            for branch_data in nd["branches"]:
                if isinstance(branch_data, list):
                    # 旧格式兼容：纯触点列表
                    contacts = [_parse_contact(c) for c in branch_data]
                    branches.append(LadBranch(contacts=contacts, boxes=[]))
                elif isinstance(branch_data, dict):
                    contacts = [_parse_contact(c) for c in branch_data.get("contacts", [])]
                    branch_boxes = _parse_boxes(branch_data.get("boxes", []))
                    branches.append(LadBranch(contacts=contacts, boxes=branch_boxes))
        else:
            contacts = [_parse_contact(c) for c in nd.get("contacts", [])]
            branches = [LadBranch(contacts=contacts, boxes=[])] if contacts else []

        result.append(LadNetwork(
            title=nd.get("title", ""),
            comment=nd.get("comment", ""),
            branches=branches,
            boxes=top_boxes,
            outputs=outputs,
        ))
    return result


def _parse_contact(c: dict) -> LadContact:
    return LadContact(
        var_path=c["var"],
        negated=bool(c.get("nc", False)),
        edge=c.get("edge", ""),
    )


def _parse_boxes(boxes_json: list) -> List[LadBox]:
    result = []
    for b in boxes_json:
        result.append(LadBox(
            box_type=b["type"],
            instance_db=b.get("instance_db", ""),
            params=dict(b.get("params", {})),
            outputs_from=dict(b.get("outputs_from", {})),
            src_type=b.get("src_type", ""),
            dest_type=b.get("dest_type", ""),
        ))
    return result


# ── XML 验证 ──────────────────────────────────────────────────────────────────

# 各 Part 类型允许的引脚名
_VALID_PINS: Dict[str, set] = {
    "Contact":  {"in", "out", "operand"},
    "PContact": {"in", "out", "operand"},
    "NContact": {"in", "out", "operand"},
    "Coil":     {"in", "out", "operand"},
    "SCoil":    {"in", "out", "operand"},
    "RCoil":    {"in", "out", "operand"},
    "Or":       {f"in{i}" for i in range(1, 33)} | {"out"},
    "Move":     {"en", "eno", "in"} | {f"out{i}" for i in range(1, 33)},
    "Add":      {"en", "eno", "out"} | {f"in{i}" for i in range(1, 33)},
    "Sub":      {"en", "eno", "in1", "in2", "out"},
    "Mul":      {"en", "eno", "out"} | {f"in{i}" for i in range(1, 33)},
    "Div":      {"en", "eno", "in1", "in2", "out"},
    "Convert":  {"en", "eno", "in", "out"},
    "Round":    {"en", "eno", "in", "out"},
    "Trunc":    {"en", "eno", "in", "out"},
    "Ceil":     {"en", "eno", "in", "out"},
    "Floor":    {"en", "eno", "in", "out"},
    "Eq":       {"pre", "out", "in1", "in2"},
    "Ne":       {"pre", "out", "in1", "in2"},
    "Gt":       {"pre", "out", "in1", "in2"},
    "Lt":       {"pre", "out", "in1", "in2"},
    "Ge":       {"pre", "out", "in1", "in2"},
    "Le":       {"pre", "out", "in1", "in2"},
    "TON":      {"in", "Q", "PT", "ET"},
    "TOF":      {"in", "Q", "PT", "ET"},
    "TP":       {"in", "Q", "PT", "ET"},
    "TON_TIME": {"in", "Q", "PT", "ET"},
    "TOF_TIME": {"in", "Q", "PT", "ET"},
    "TP_TIME":  {"in", "Q", "PT", "ET"},
    "CTU":      {"cu", "r", "PV", "Q", "CV"},
    "CTD":      {"cd", "ld", "PV", "Q", "CV"},
    "CTUD":     {"cu", "cd", "r", "ld", "PV", "QU", "QD", "CV"},
    "SR":       {"S1", "R", "Q"},
    "RS":       {"S", "R1", "Q"},
}


def validate_lad_xml(xml: str) -> List[str]:
    """
    验证生成的 LAD XML 中 UID 引用和引脚名是否正确。

    Returns:
        错误消息列表（空列表表示无错误）
    """
    import re as _re
    errors: List[str] = []

    # 收集所有 Part（有 Name 连接的元素）
    parts = {uid: name for name, uid in _re.findall(
        r'<Part Name="(\w+)" UId="(\d+)"', xml)}

    # 收集所有 Access（只能用 IdentCon 引用）
    accesses = set(_re.findall(r'<Access[^>]*UId="(\d+)"', xml))

    # 检查所有 NameCon 引用
    for uid, pin in _re.findall(r'<NameCon UId="(\d+)" Name="(\w+)"', xml):
        if uid in accesses:
            errors.append(
                f"Wire 错误引用了 Access 元素（UID={uid}）的引脚 '{pin}'，"
                f"Access 只能用 IdentCon 引用")
        elif uid not in parts:
            errors.append(
                f"Wire 引用了不存在的 UID={uid}（引脚 '{pin}'）")
        else:
            part_name = parts[uid]
            valid = _VALID_PINS.get(part_name)
            if valid and pin not in valid:
                errors.append(
                    f"{part_name}(UID={uid}) 没有引脚 '{pin}'，"
                    f"有效引脚: {sorted(valid)}")

    # 检查所有 IdentCon 引用（应指向 Access 或带 Instance 的 Part 子元素）
    for uid in _re.findall(r'<IdentCon UId="(\d+)"', xml):
        if uid in parts and uid not in accesses:
            errors.append(
                f"IdentCon 错误引用了 Part 元素 {parts[uid]}（UID={uid}），"
                f"Part 应该用 NameCon 引用")

    return errors
