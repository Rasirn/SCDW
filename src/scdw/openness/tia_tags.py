# -*- coding: utf-8 -*-
"""
tia_tags.py
PLC 变量表管理：创建变量表、批量添加变量。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class TagSpec:
    """单个 PLC 变量的定义规格。"""

    name: str
    data_type: str
    logical_address: str
    comment: str = ""


def create_tag_table(plc_software, table_name: str):
    """
    在 PLC 软件下新建变量表。若同名变量表已存在，直接返回已有对象。

    Args:
        plc_software: PLC Software 对象
        table_name: 变量表名称

    Returns:
        TagTable 对象
    """
    tag_tables = plc_software.TagTableGroup.TagTables
    for tbl in tag_tables:
        if str(tbl.Name) == table_name:
            return tbl
    return tag_tables.Create(table_name)


def add_tag(
    tag_table,
    name: str,
    data_type: str,
    logical_address: str,
    comment: str = "",
) -> None:
    """
    向变量表中添加单个变量。

    Args:
        tag_table: TagTable 对象
        name: 变量名称
        data_type: 数据类型，如 "Bool"、"Int"、"Real"
        logical_address: 逻辑地址，如 "%I0.0"、"%MW10"
        comment: 变量注释（可选）
    """
    tag = tag_table.Tags.Create(name, data_type, logical_address)
    if comment:
        try:
            tag.Comment.Items.Item(0).Text = comment
        except Exception:
            pass


def create_tag_table_with_tags(
    plc_software,
    table_name: str,
    tags: List[TagSpec],
    skip_duplicates: bool = True,
) -> None:
    """
    创建变量表并批量写入 TagSpec 列表中的所有变量。

    Args:
        plc_software: PLC Software 对象
        table_name: 变量表名称
        tags: TagSpec 列表
        skip_duplicates: 若为 True，跳过变量表中已存在同名变量（默认 True）

    Raises:
        RuntimeError: 添加某个变量失败时抛出。
    """
    tag_table = create_tag_table(plc_software, table_name)

    existing_names: set = set()
    if skip_duplicates:
        try:
            for t in tag_table.Tags:
                existing_names.add(str(t.Name))
        except Exception:
            pass

    for spec in tags:
        if skip_duplicates and spec.name in existing_names:
            continue
        try:
            add_tag(
                tag_table,
                spec.name,
                spec.data_type,
                spec.logical_address,
                spec.comment,
            )
            existing_names.add(spec.name)
        except Exception as exc:
            raise RuntimeError(
                f"添加变量 '{spec.name}' 到变量表 '{table_name}' 失败：{exc}"
            ) from exc
