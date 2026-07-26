# -*- coding: utf-8 -*-
"""
tia_blocks.py
程序块管理：导入 SCL 程序块、生成并导入全局 DB、导入 LAD XML 块。

关于 LAD 块导入：
  TIA Portal Openness 不支持直接从文本创建梯形图，LAD 块必须以 SimaticML/XML
  格式（通过博图导出获得模板）再导入。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .tia_core import safe_filename, write_text_file

# TIA Portal SCL 变量名规则：以字母或下划线开头，只含字母/数字/下划线
_VAR_NAME_RE = re.compile(r'^[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*$')


def _validate_var_name(name: str) -> None:
    """校验 TIA Portal 变量名，不合法时抛出 ValueError。"""
    if not name:
        raise ValueError("变量名不能为空")
    if name[0].isdigit():
        raise ValueError(
            f"变量名 '{name}' 以数字开头，TIA Portal 不允许此格式。"
            f"建议改为下划线或字母开头，如 '_{name}' 或将数字移至末尾。"
        )
    if not _VAR_NAME_RE.match(name):
        invalid_chars = {c for c in name if not re.match(r'[A-Za-z0-9_\u4e00-\u9fff]', c)}
        raise ValueError(
            f"变量名 '{name}' 含非法字符 {invalid_chars}（不允许空格、连字符等）。"
            f"请只使用字母、数字、下划线或中文字符。"
        )


# ── 数据结构 ──────────────────────────────────────────────────────────────────
@dataclass
class DBVariable:
    """全局 DB 变量规格。"""

    name: str
    data_type: str
    initial_value: str = ""
    comment: str = ""
    offset: str = ""   # 逻辑偏移量，如 "0.0"、"2"，仅用于 SCL 注释中记录，不影响 TIA 编译器分配地址


# ── 内部辅助 ──────────────────────────────────────────────────────────────────
def _find_external_source(plc_software, name: str):
    """在外部源码组中按名称查找，未找到返回 None。"""
    try:
        for src in plc_software.ExternalSourceGroup.ExternalSources:
            if str(src.Name) == name:
                return src
    except Exception:
        pass
    return None


def _delete_if_exists(obj) -> None:
    try:
        if obj is not None:
            obj.Delete()
    except Exception:
        pass


# ── SCL 块导入 ────────────────────────────────────────────────────────────────
def import_scl_block(
    plc_software,
    temp_dir: str,
    block_name: str,
    scl_content: str,
) -> str:
    """
    将 SCL 源码以外部源文件方式导入 PLC 并生成程序块。

    若存在同名外部源，先删除再重新导入（覆盖语义）。

    Args:
        plc_software: PLC Software 对象
        temp_dir: 临时目录，用于存放 .scl 文件
        block_name: 块的逻辑名称（也是外部源名称），如 "OB1"、"FC_Fan"
        scl_content: SCL 源码文本

    Returns:
        写入的 .scl 文件完整路径
    """
    from Siemens.Engineering.SW.ExternalSources import GenerateBlockOption  # type: ignore

    _delete_if_exists(_find_external_source(plc_software, block_name))

    filename = safe_filename(block_name, ".scl")
    scl_path = write_text_file(temp_dir, filename, scl_content, encoding="utf-8-sig")

    ext_src = plc_software.ExternalSourceGroup.ExternalSources.CreateFromFile(
        block_name, scl_path
    )
    option_none = getattr(GenerateBlockOption, "None")
    ext_src.GenerateBlocksFromSource(option_none)
    return scl_path


# ── 全局 DB ───────────────────────────────────────────────────────────────────

# TIA Portal SCL 中不需要加引号的基础类型（小写）
_S7_PRIMITIVE_TYPES: frozenset = frozenset({
    "bool", "byte", "word", "dword", "lword",
    "int", "uint", "sint", "usint", "dint", "udint", "lint", "ulint",
    "real", "lreal",
    "time", "ltime", "date", "time_of_day", "tod", "ltime_of_day", "ltod",
    "date_and_time", "dt", "ldt",
    "char", "wchar", "string", "wstring",
    "s5time",
})


def _scl_type(data_type: str) -> tuple[str, bool]:
    """
    规范化 SCL 类型名，返回 (scl_type_str, is_primitive)。

    - 基础类型原样返回，is_primitive=True
    - Array / String[n] / WString[n]：原样返回，is_primitive=True（允许初始值）
    - 已有双引号：原样返回，is_primitive=False
    - 其他（UDT、系统结构体如 IEC_TIMER、FB 实例）：加双引号，is_primitive=False
      → 复合类型在 DATA_BLOCK 中不允许 := 初始值
    """
    s = data_type.strip()
    lower = s.lower()

    if s.startswith('"'):
        return s, False

    # Array 类型（Array[lo..hi] of T）
    if lower.startswith("array"):
        return s, True

    # String / WString 含长度参数
    if lower.startswith("string[") or lower.startswith("wstring["):
        return s, True

    # 纯基础类型（可能携带长度，如 String 不含括号时）
    base = lower.split("[")[0].strip()
    if base in _S7_PRIMITIVE_TYPES:
        return s, True

    # 其余为复合/UDT/系统类型 → 加双引号，不允许初始值
    return f'"{s}"', False


def build_global_db_scl(
    db_name: str,
    db_number: int,
    variables: List[DBVariable],
) -> str:
    """
    根据变量列表生成全局 DB 的 SCL 源码文本。

    Args:
        db_name: DB 名称，如 "Fan_DB"
        db_number: DB 编号，如 10
        variables: DBVariable 列表

    Returns:
        SCL 源码字符串
    """
    lines = [
        f'DATA_BLOCK "{db_name}"',
        "{ S7_Optimized_Access := 'TRUE' }",
        "VERSION : 0.1",
        "NON_RETAIN",
        "VAR",
    ]
    for var in variables:
        _validate_var_name(var.name)
        scl_dtype, is_primitive = _scl_type(var.data_type)
        comment_part = f"  // {var.comment}" if var.comment else ""
        # 复合类型（IEC_TIMER 等系统结构体/UDT）不允许 := 初始值
        init_part = f" := {var.initial_value}" if (var.initial_value and is_primitive) else ""
        lines.append(f"    {var.name} : {scl_dtype}{init_part};{comment_part}")
    lines += ["END_VAR", "", "BEGIN", "END_DATA_BLOCK"]
    return "\n".join(lines)


def create_global_db(
    plc_software,
    temp_dir: str,
    db_name: str,
    db_number: int,
    variables: List[DBVariable],
) -> str:
    """
    生成全局 DB 的 SCL 文本并导入到 PLC。

    Args:
        plc_software: PLC Software 对象
        temp_dir: 临时目录
        db_name: DB 名称
        db_number: DB 编号
        variables: DBVariable 列表

    Returns:
        写入的 .scl 文件完整路径
    """
    scl_content = build_global_db_scl(db_name, db_number, variables)
    return import_scl_block(plc_software, temp_dir, db_name, scl_content)


# ── FlgNet Part 排序自动修复 ──────────────────────────────────────────────────
def _fix_flgnet_part_order(xml_content: str) -> str:
    """
    预处理：修复 FlgNet 网络中 <Part> 元素顺序不符合功率流方向的问题。

    TIA Portal 要求同一功率分支的 Part 元素必须连续排列，整体按分支顺序从上到下排列，
    不能把所有 Contact 堆在前面、所有 Coil/SCoil/RCoil 堆在后面。

    错误表现：'UId 为 X 的元素产生以下错误消息：这些元素必须根据电流进行排序'
    """
    import re as _re2
    import xml.etree.ElementTree as _ET
    import bisect as _bisect

    _NS = 'http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4'
    _POWER_IN = {'in', 'pre', 'IN', 'en'}
    _POWER_OUT = {'out', 'Q', 'eno', 'out1', 'out2', 'out3'}

    def _fix_one(flgnet_str: str) -> str:
        try:
            root = _ET.fromstring(flgnet_str)
        except _ET.ParseError:
            return flgnet_str

        parts_elem = root.find(f'{{{_NS}}}Parts')
        wires_elem = root.find(f'{{{_NS}}}Wires')
        if parts_elem is None or wires_elem is None:
            return flgnet_str

        # 按出现顺序收集所有 Part 的 UId
        orig_order = []
        for child in parts_elem:
            if child.tag == f'{{{_NS}}}Part':
                try:
                    orig_order.append(int(child.get('UId')))
                except (TypeError, ValueError):
                    pass
        if len(orig_order) <= 1:
            return flgnet_str

        uid_set = set(orig_order)

        # 构建功率流前驱关系：preds[B] = {A, ...} 表示 A 必须排在 B 之前
        preds = {u: set() for u in uid_set}
        for wire in wires_elem:
            ins, outs = [], []
            for nc in wire:
                if nc.tag != f'{{{_NS}}}NameCon':
                    continue
                try:
                    uid = int(nc.get('UId'))
                except (TypeError, ValueError):
                    continue
                if uid not in uid_set:
                    continue
                pin = nc.get('Name', '')
                if pin in _POWER_IN:
                    ins.append(uid)
                elif pin in _POWER_OUT:
                    outs.append(uid)
            for src in outs:
                for dst in ins:
                    if src != dst:
                        preds[dst].add(src)

        # Kahn 拓扑排序，同层按原始 UId 从小到大（保持分支相对顺序）
        in_deg = {u: len(p) for u, p in preds.items()}
        ready = sorted(u for u, d in in_deg.items() if d == 0)
        sorted_uids = []
        remaining = {u: set(p) for u, p in preds.items()}

        while ready:
            uid = ready.pop(0)
            sorted_uids.append(uid)
            for other in uid_set - set(sorted_uids):
                if uid in remaining[other]:
                    remaining[other].discard(uid)
                    in_deg[other] -= 1
                    if in_deg[other] == 0:
                        _bisect.insort(ready, other)

        if len(sorted_uids) != len(orig_order):
            return flgnet_str  # 存在环或其他异常，不修改
        if sorted_uids == orig_order:
            return flgnet_str  # 已经正确，无需改动

        # 从原始字符串中提取 Part 子串（保留原始格式/缩进）
        parts_sec = _re2.search(r'(<Parts>)(.*?)(</Parts>)', flgnet_str, _re2.DOTALL)
        if not parts_sec:
            return flgnet_str
        parts_body = parts_sec.group(2)

        # 匹配自闭合或带内容的 Part 元素（含尾部空白）
        part_re = _re2.compile(
            r'<Part\b[^>]*/>\s*|<Part\b[^>]*>.*?</Part>\s*',
            _re2.DOTALL,
        )
        uid_to_str = {}
        for m in part_re.finditer(parts_body):
            s = m.group(0)
            um = _re2.search(r'\bUId="(\d+)"', s)
            if um:
                uid_to_str[int(um.group(1))] = s

        if len(uid_to_str) != len(uid_set):
            return flgnet_str  # 提取数量不符，放弃

        # 非 Part 内容（Access / Call 元素）保留在前，Part 按新顺序追加
        non_part = part_re.sub('', parts_body)
        new_body = non_part + ''.join(uid_to_str[u] for u in sorted_uids)

        return (
            flgnet_str[: parts_sec.start()]
            + parts_sec.group(1) + new_body + parts_sec.group(3)
            + flgnet_str[parts_sec.end():]
        )

    return _re2.compile(r'<FlgNet\b[^>]*>.*?</FlgNet>', _re2.DOTALL).sub(
        lambda m: _fix_one(m.group(0)), xml_content
    )


# ── FlgNet 多条 Powerrail 检测 ────────────────────────────────────────────────
def _check_multiple_powerrails(xml_content: str) -> None:
    """
    检测同一 FlgNet 网络中是否出现多条 <Powerrail />。
    LAD 中每个程序段（CompileUnit）只能有一条 Powerrail。
    多个并联支路必须从同一条 Powerrail 的 Wire 分叉，不能写多条 Wire+Powerrail。
    """
    import re as _re3
    import xml.etree.ElementTree as _ET3

    _NS = 'http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4'

    for m in _re3.finditer(r'<FlgNet\b[^>]*>.*?</FlgNet>', xml_content, _re3.DOTALL):
        try:
            root = _ET3.fromstring(m.group(0))
        except _ET3.ParseError:
            continue
        wires = root.find(f'{{{_NS}}}Wires')
        if wires is None:
            continue
        powerrail_count = sum(
            1 for w in wires if w.find(f'{{{_NS}}}Powerrail') is not None
        )
        if powerrail_count > 1:
            raise ValueError(
                f"FlgNet 中有 {powerrail_count} 条 Powerrail，LAD 每个程序段只能有 1 条。\n"
                "并联支路的正确写法是在同一条 Powerrail Wire 中连接多个 NameCon：\n"
                "  <Wire UId=\"N\">\n"
                "    <Powerrail />\n"
                "    <NameCon UId=\"支路1首元素\" Name=\"in\" />\n"
                "    <NameCon UId=\"支路2首元素\" Name=\"in\" />\n"
                "    <NameCon UId=\"支路3首元素\" Name=\"in\" />\n"
                "  </Wire>\n"
                "不要为每条并联支路单独写一条 <Wire><Powerrail />...</Wire>。"
            )


# ── FlgNet 重复 IdentCon 自动修复 ────────────────────────────────────────────
def _fix_duplicate_identcon(xml_content: str) -> str:
    """
    修复同一个 IdentCon UId 在多条 Wire 中重复出现的问题。

    FlgNet 规则：每个 Access 元素的 UId 只能在一条 Wire 中作为 IdentCon 引用。
    如果同一个变量需要连接到多处，必须用不同 UId 的 Access 元素各引用一次。

    修复方式：保留第一次出现不变，从第二次起复制原 Access 元素并分配新 UId。
    """
    import re as _re4
    import xml.etree.ElementTree as _ET4

    _NS = 'http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4'

    def _fix_one(flgnet_str: str) -> str:
        try:
            root = _ET4.fromstring(flgnet_str)
        except _ET4.ParseError:
            return flgnet_str

        wires_elem = root.find(f'{{{_NS}}}Wires')
        if wires_elem is None:
            return flgnet_str

        # 收集所有已用 UId（取全局最大值，用于分配新 UId）
        all_uids = set()
        for el in root.iter():
            try:
                all_uids.add(int(el.get('UId')))
            except (TypeError, ValueError):
                pass
        max_uid = max(all_uids) if all_uids else 1000

        # 统计 IdentCon UId 在各条 Wire 中的出现次数（按 Wire 索引记录）
        ident_occ: dict = {}
        for wi, wire in enumerate(wires_elem):
            for ic in wire:
                if ic.tag != f'{{{_NS}}}IdentCon':
                    continue
                try:
                    uid = int(ic.get('UId'))
                except (TypeError, ValueError):
                    continue
                ident_occ.setdefault(uid, []).append(wi)

        dup_uids = {u: idxs for u, idxs in ident_occ.items() if len(idxs) > 1}
        if not dup_uids:
            return flgnet_str

        # 构建替换映射：{wire_index: {old_uid: new_uid}}
        replace_map: dict = {}
        new_access: dict = {}  # {new_uid: original_uid}

        for old_uid, wire_indices in dup_uids.items():
            for wi in wire_indices[1:]:
                max_uid += 1
                replace_map.setdefault(wi, {})[old_uid] = max_uid
                new_access[max_uid] = old_uid

        if not new_access:
            return flgnet_str

        # 提取原始 Access 元素字符串
        parts_match = _re4.search(r'<Parts>(.*?)</Parts>', flgnet_str, _re4.DOTALL)
        if not parts_match:
            return flgnet_str
        parts_body = parts_match.group(1)

        access_re = _re4.compile(
            r'<Access\b[^>]*UId="(\d+)"[^>]*>.*?</Access>\s*|'
            r'<Access\b[^>]*UId="(\d+)"[^>]*/>\s*',
            _re4.DOTALL,
        )
        uid_to_access_str: dict = {}
        for m2 in access_re.finditer(parts_body):
            raw_uid = m2.group(1) or m2.group(2)
            if raw_uid:
                uid_to_access_str[int(raw_uid)] = m2.group(0)

        # 构造新增 Access 字符串（复制原始 Access，替换 UId）
        new_access_strs = []
        for new_uid, orig_uid in new_access.items():
            if orig_uid not in uid_to_access_str:
                continue
            orig_str = uid_to_access_str[orig_uid]
            new_str = _re4.sub(
                r'(\bUId=")' + str(orig_uid) + r'"',
                r'\g<1>' + str(new_uid) + '"',
                orig_str,
                count=1,
            )
            new_access_strs.append(new_str)

        if not new_access_strs:
            return flgnet_str

        # 在 </Parts> 前插入新 Access 元素
        new_flgnet = (
            flgnet_str[: parts_match.end(1)]
            + ''.join(new_access_strs)
            + flgnet_str[parts_match.end(1):]
        )

        # 替换 Wires 中重复 IdentCon 的 UId（从后往前，保持偏移正确）
        wire_re = _re4.compile(r'<Wire\b[^>]*>.*?</Wire>', _re4.DOTALL)
        wire_list = list(wire_re.finditer(new_flgnet))

        patches = []
        for wi, uid_map in replace_map.items():
            if wi >= len(wire_list):
                continue
            m3 = wire_list[wi]
            wire_str = m3.group(0)
            for old_uid, new_uid in uid_map.items():
                wire_str = _re4.sub(
                    r'(<IdentCon\s+UId=")' + str(old_uid) + r'"',
                    r'\g<1>' + str(new_uid) + '"',
                    wire_str,
                    count=1,
                )
            patches.append((m3.start(), m3.end(), wire_str))

        for start, end, repl in sorted(patches, reverse=True):
            new_flgnet = new_flgnet[:start] + repl + new_flgnet[end:]

        return new_flgnet

    return _re4.compile(r'<FlgNet\b[^>]*>.*?</FlgNet>', _re4.DOTALL).sub(
        lambda m: _fix_one(m.group(0)), xml_content
    )


# ── LAD XML 块导入 ────────────────────────────────────────────────────────────
def import_lad_xml_block(
    plc_software,
    temp_dir: str,
    block_name: str,
    xml_content: str,
) -> str:
    """
    导入 SimaticML/XML 格式的程序块（LAD/FBD 等）。

    XML 内容须由博图导出后修改，无法从纯文本生成梯形图。

    Args:
        plc_software: PLC Software 对象
        temp_dir: 临时目录
        block_name: 块名称（仅用于生成临时文件名）
        xml_content: SimaticML XML 文本

    Returns:
        写入的 .xml 文件完整路径
    """
    import re as _re
    from Siemens.Engineering import ImportOptions  # type: ignore
    from System.IO import FileInfo  # type: ignore

    # ── 兼容性预处理 ──────────────────────────────────────────────────────────
    # 1. 将 DisabledENO="true" 改为 DisabledENO="false"。
    #    TIA Portal 要求该属性必须显式声明——有 "true" 或 "false" 都能接受，
    #    但如果完全缺失则部分指令（Move/GATHER/SCATTER/Calc 等）会导入失败。
    #    从 S7-1200 导出的 XML 带有 DisabledENO="true" 是合法的；
    #    只需保持属性存在，将 true 改为 false 即可（ENO 默认启用，对逻辑无影响）。
    xml_content = _re.sub(r'DisabledENO="true"', 'DisabledENO="false"', xml_content)

    # 2. 检测多条 Powerrail：每个 CompileUnit（程序段）只能有 1 条 Powerrail。
    #    并联支路必须从同一条 Powerrail Wire 分叉（多个 NameCon 挂在同一条 Wire 上），
    #    不能每条支路各写一个 <Wire><Powerrail />...</Wire>。
    _check_multiple_powerrails(xml_content)

    # 3. 修复重复 IdentCon：每个 Access UId 只能在一条 Wire 中引用，
    #    同一变量连接多处时需为每处创建独立 Access 元素（不同 UId）。
    #    自动检测并复制 Access，分配新 UId，无法修复时原样保留。
    xml_content = _fix_duplicate_identcon(xml_content)

    # 4. 修复 FlgNet Part 排序：TIA Portal 要求同一分支的 Part 元素必须连续，
    #    不能将所有 Contact 排在前面、Coil/SCoil/RCoil 排在后面。
    #    自动拓扑排序，无法修复时原样保留（不影响正常 XML）。
    xml_content = _fix_flgnet_part_order(xml_content)

    # 5. 检测 S7-1500 专用直接 I/O 访问指令（S7-1200 不存在）：
    #    GETIO/SETIO/GETIO_PART/SETIO_PART 直接访问过程映像，S7-1200 不支持。
    #    注意：GATHER/SCATTER 在 S7-1200 V4.0+ 中完全支持，不在禁止列表内。
    _S1500_ONLY = ('GETIO', 'SETIO', 'GETIO_PART', 'SETIO_PART')
    _found = _re.findall(
        r'<Part Name="(' + '|'.join(_S1500_ONLY) + r')"',
        xml_content,
    )
    if _found:
        raise ValueError(
            f"XML 中包含 S7-1500 专用指令：{list(set(_found))}，S7-1200/S7-1214 不支持这些指令。\n"
            "请用等效的 S7-1200 指令替换：\n"
            "  GETIO/SETIO → 直接使用 PLC 输入/输出过程映像变量，无需直接 I/O 访问指令\n"
            "修改后重新调用 import_lad_xml。"
        )

    filename = safe_filename(block_name, ".xml")
    xml_path = write_text_file(temp_dir, filename, xml_content)

    file_info = FileInfo(xml_path)
    plc_software.BlockGroup.Blocks.Import(file_info, ImportOptions.Override)
    return xml_path


def delete_block(plc_software, block_name: str) -> bool:
    """
    删除 PLC 中的指定程序块。

    Args:
        plc_software: PLC Software 对象
        block_name: 块名称

    Returns:
        True=删除成功, False=未找到
    """
    for block in plc_software.BlockGroup.Blocks:
        if str(block.Name) == block_name:
            block.Delete()
            return True
    return False
