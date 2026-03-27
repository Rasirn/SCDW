# -*- coding: utf-8 -*-
"""
tia_blocks.py
程序块管理：导入 SCL 程序块、生成并导入全局 DB、导入 LAD XML 块。

关于 LAD 块导入：
  TIA Portal Openness 不支持直接从文本创建梯形图，LAD 块必须以 SimaticML/XML
  格式（通过博图导出获得模板）再导入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .tia_core import safe_filename, write_text_file


# ── 数据结构 ──────────────────────────────────────────────────────────────────
@dataclass
class DBVariable:
    """全局 DB 变量规格。"""

    name: str
    data_type: str
    initial_value: str = ""
    comment: str = ""


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
    scl_path = write_text_file(temp_dir, filename, scl_content)

    ext_src = plc_software.ExternalSourceGroup.ExternalSources.CreateFromFile(
        block_name, scl_path
    )
    option_none = getattr(GenerateBlockOption, "None")
    ext_src.GenerateBlocksFromSource(option_none)
    return scl_path


# ── 全局 DB ───────────────────────────────────────────────────────────────────
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
        "VAR",
    ]
    for var in variables:
        comment_part = f"  // {var.comment}" if var.comment else ""
        init_part = f" := {var.initial_value}" if var.initial_value else ""
        lines.append(f"    {var.name} : {var.data_type}{init_part};{comment_part}")
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
