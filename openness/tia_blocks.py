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
        "{ S7_Optimized_Access := 'FALSE' }",
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
    from Siemens.Engineering import ImportOptions  # type: ignore
    from System.IO import FileInfo  # type: ignore

    filename = safe_filename(block_name, ".xml")
    xml_path = write_text_file(temp_dir, filename, xml_content)

    file_info = FileInfo(xml_path)
    plc_software.BlockGroup.Blocks.Import(file_info, ImportOptions.Override)
    return xml_path
