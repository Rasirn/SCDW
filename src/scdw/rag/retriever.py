# -*- coding: utf-8 -*-
"""
PLC XML 模板库检索模块。

TIA Portal XML 模板库检索模块（多目录可扩展版本）。

功能
----
1. 递归扫描 data/rag/templates/ 的所有子目录，自动索引所有 .xml 模板文件。
2. 每个子目录为一个"分类"（category），通过 meta.json 提供元数据描述。
3. 提供基于关键词的模糊检索（无需向量数据库，纯文本匹配）。
4. 提供模板内容读取接口，供 MCP 工具层调用。
5. 提供 save_generated_xml()，将 AI 生成的 XML 持久保存到 data/generated/rag/ 目录。

目录结构
--------
data/
  templates/
    application/    ← 真实工程导出的完整应用程序模板
      meta.json     ← 该目录的元数据（可选）
      Main.xml
      烧嘴控制.xml
      ...
    basic/          ← 基础/单指令示例模板（待扩充）
      meta.json
      Contact_常开触点.xml
      ...
    custom/         ← 用户自定义模板（可自建子目录）
      meta.json
      ...
  generated/        ← AI 生成的 XML 自动保存目录（非模板）

meta.json 格式（每个子目录可选）
--------------
{
  "_category_desc": "整个分类的描述",
  "模板名（无.xml）": {
    "description": "单行描述",
    "keywords":    ["关键词1", "关键词2", ...]
  }
}

扩展方法
--------
1. 新增分类：在 templates/ 下建子目录，放入 .xml 文件和 meta.json
2. 新增模板：将 .xml 放入对应子目录，在 meta.json 中添加条目（可选）
3. 重载：重启 MCP Server，或调用 TemplateLibrary.instance().reload()
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 路径 ──────────────────────────────────────────────────────────────────────

from scdw.common.paths import RAG_GENERATED_DIR, RAG_TEMPLATES_DIR, ensure_generated_dir

TEMPLATES_DIR = RAG_TEMPLATES_DIR
GENERATED_DIR = RAG_GENERATED_DIR


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class TemplateInfo:
    """模板基础信息。"""
    name: str                    # 不含扩展名的文件名
    category: str                # 所属分类（子目录名），如 application / basic
    file_path: Path              # 绝对路径
    block_type: str = ""         # OB / FB / FC（从 XML 解析）
    block_name: str = ""         # 块名称（从 XML 解析）
    keywords: List[str] = field(default_factory=list)
    description: str = ""

    @property
    def display_name(self) -> str:
        parts = [self.name]
        if self.block_type:
            parts.append(f"[{self.block_type}]")
        parts.append(f"({self.category})")
        return " ".join(parts)

    @property
    def full_name(self) -> str:
        """唯一标识符：category/name，用于跨分类精确定位。"""
        return f"{self.category}/{self.name}"


# ── XML 解析辅助 ──────────────────────────────────────────────────────────────

def _parse_block_info(xml_path: Path) -> Tuple[str, str]:
    """
    从 SimaticML XML 文件中提取块类型和块名称。
    返回 (block_type, block_name)，解析失败时返回 ("", "")。
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for child in root:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag.startswith("SW.Blocks."):
                block_type_raw = tag.replace("SW.Blocks.", "")
                attr_list = child.find("AttributeList")
                block_name = ""
                if attr_list is not None:
                    name_elem = attr_list.find("Name")
                    if name_elem is not None and name_elem.text:
                        block_name = name_elem.text.strip()
                return block_type_raw, block_name
    except Exception:
        pass
    return "", ""


def _load_category_meta(category_dir: Path) -> Tuple[str, Dict]:
    """
    加载目录的 meta.json，返回 (category_desc, per_file_meta_dict)。
    per_file_meta_dict: {文件名（无.xml）: {"description": ..., "keywords": [...]}}
    """
    meta_file = category_dir / "meta.json"
    if not meta_file.exists():
        return "", {}
    try:
        with open(meta_file, encoding="utf-8") as f:
            data = json.load(f)
        category_desc = data.get("_category_desc", "")
        per_file: Dict = {}
        for k, v in data.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict):
                per_file[k] = {
                    "description": v.get("description", k),
                    "keywords": v.get("keywords", []),
                }
        return category_desc, per_file
    except Exception:
        return "", {}


# ── 模板库 ────────────────────────────────────────────────────────────────────

class TemplateLibrary:
    """
    模板库：多目录扫描、索引元数据、提供检索接口。

    单例模式：通过 TemplateLibrary.instance() 获取全局实例。
    """

    _instance: Optional["TemplateLibrary"] = None

    def __init__(self, templates_dir: Path = TEMPLATES_DIR):
        self._dir = templates_dir
        # key: "category/name" 唯一标识，value: TemplateInfo
        self._templates: Dict[str, TemplateInfo] = {}
        # key: category 名，value: 描述
        self._categories: Dict[str, str] = {}
        self._scan()

    @classmethod
    def instance(cls, templates_dir: Path = TEMPLATES_DIR) -> "TemplateLibrary":
        if cls._instance is None:
            cls._instance = cls(templates_dir)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（测试或重载时使用）。"""
        cls._instance = None

    # ── 扫描 ─────────────────────────────────────────────────────────────────

    def _scan(self) -> None:
        """扫描所有子目录，建立索引。"""
        self._templates.clear()
        self._categories.clear()
        if not self._dir.exists():
            return

        # 直接子目录 = 分类；同时兼容根目录下直接放 xml（legacy）
        subdirs = sorted(p for p in self._dir.iterdir() if p.is_dir())
        root_xmls = list(self._dir.glob("*.xml"))
        if root_xmls:
            subdirs = [self._dir] + list(subdirs)

        for subdir in subdirs:
            category = "default" if subdir == self._dir else subdir.name
            category_desc, per_file_meta = _load_category_meta(subdir)
            self._categories[category] = category_desc

            for xml_file in sorted(subdir.glob("*.xml")):
                stem = xml_file.stem
                block_type, block_name = _parse_block_info(xml_file)
                file_meta = per_file_meta.get(stem, {})
                desc = file_meta.get("description", stem)
                kw_from_meta: List[str] = file_meta.get("keywords", [])

                keywords: List[str] = list(kw_from_meta)
                keywords.append(stem)
                if block_name and block_name != stem:
                    keywords.append(block_name)
                if block_type:
                    keywords.append(block_type)
                keywords.append(category)

                key = f"{category}/{stem}"
                self._templates[key] = TemplateInfo(
                    name=stem,
                    category=category,
                    file_path=xml_file,
                    block_type=block_type,
                    block_name=block_name or stem,
                    keywords=keywords,
                    description=desc,
                )

    def reload(self) -> None:
        """强制重新扫描目录（新增模板后调用）。"""
        self._scan()

    # ── 查询接口 ──────────────────────────────────────────────────────────────

    def list_all(self) -> List[TemplateInfo]:
        """返回所有模板信息，按分类+名称排序。"""
        return sorted(self._templates.values(), key=lambda t: (t.category, t.name))

    def list_categories(self) -> Dict[str, str]:
        """返回所有分类及其描述。{category_name: description}"""
        return dict(self._categories)

    def list_by_category(self, category: str) -> List[TemplateInfo]:
        """返回指定分类下的所有模板。"""
        return [t for t in self._templates.values() if t.category == category]

    def get(self, name: str, category: Optional[str] = None) -> Optional[TemplateInfo]:
        """
        按名称获取模板信息（不含 .xml 后缀）。
        name 可以是 "烧嘴控制" 或 "application/烧嘴控制" 两种格式。
        """
        if "/" in name:
            return self._templates.get(name)
        if category:
            result = self._templates.get(f"{category}/{name}")
            if result:
                return result
        # 全局精确匹配
        for info in self._templates.values():
            if info.name == name:
                return info
        # 大小写不敏感匹配
        name_lower = name.lower()
        for info in self._templates.values():
            if info.name.lower() == name_lower:
                return info
        return None

    def search(
        self,
        query: str,
        top_k: int = 5,
        category: Optional[str] = None,
    ) -> List[Tuple[TemplateInfo, float]]:
        """
        关键词检索，返回 (TemplateInfo, 相关度得分) 列表，按相关度降序。

        Args:
            query:    搜索词（中文/英文均可）
            top_k:    返回数量上限
            category: 可选，限定分类范围
        """
        if not query:
            pool = self.list_by_category(category) if category else self.list_all()
            return [(t, 0.0) for t in pool][:top_k]

        tokens = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+", query.lower())
        if not tokens:
            tokens = [query.lower()]

        pool = self.list_by_category(category) if category else self.list_all()

        results: List[Tuple[TemplateInfo, float]] = []
        for info in pool:
            kw_lower = [k.lower() for k in info.keywords]
            kw_concat = " ".join(kw_lower)

            hit_count = sum(1 for t in tokens if any(t in kw for kw in kw_lower))
            score = hit_count / len(tokens) if tokens else 0.0

            if query.lower() in kw_concat:
                score += 0.3

            if score > 0:
                results.append((info, round(score, 3)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    # ── 内容读取 ──────────────────────────────────────────────────────────────

    def read_xml(self, name: str, category: Optional[str] = None) -> Optional[str]:
        """读取指定模板的完整 XML 文件内容。"""
        info = self.get(name, category=category)
        if info is None:
            return None
        return info.file_path.read_text(encoding="utf-8")

    def read_xml_excerpt(
        self,
        name: str,
        category: Optional[str] = None,
        max_chars: int = 4000,
    ) -> Optional[str]:
        """
        读取模板 XML 的精简摘要（截断到 max_chars 字符）。
        保留前半（接口声明）+ 后半（第一个网络），中间用省略标记代替。
        """
        full = self.read_xml(name, category=category)
        if full is None:
            return None
        if len(full) <= max_chars:
            return full
        half = max_chars // 2
        head = full[:half]
        tail = full[-half:]
        return head + "\n\n<!-- ... 内容已截断，显示前后各约2000字符 ... -->\n\n" + tail


# ── 生成目录工具 ──────────────────────────────────────────────────────────────

def get_generated_dir() -> Path:
    """返回 AI 生成 XML 的保存目录（确保目录存在）。"""
    return ensure_generated_dir()


def save_generated_xml(block_name: str, xml_content: str) -> Path:
    """
    将 AI 生成的 XML 内容保存到 data/generated/rag/ 目录。

    文件名格式：{block_name}_{YYYYMMDD_HHMMSS}.xml
    返回保存路径（绝对路径 Path 对象）。
    """
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", block_name)
    filename = f"{safe_name}_{timestamp}.xml"
    out_path = get_generated_dir() / filename
    out_path.write_text(xml_content, encoding="utf-8")
    return out_path


# ── 便捷函数（供 tools.py 直接调用）────────────────────────────────────────────

def list_templates(category: Optional[str] = None) -> List[Dict]:
    """返回所有（或指定分类）模板的摘要信息列表。"""
    lib = TemplateLibrary.instance()
    items = lib.list_by_category(category) if category else lib.list_all()
    return [
        {
            "name": t.name,
            "category": t.category,
            "block_type": t.block_type,
            "block_name": t.block_name,
            "description": t.description,
            "keywords": t.keywords[:8],
        }
        for t in items
    ]


def list_categories() -> Dict[str, str]:
    """返回所有分类及其描述。"""
    return TemplateLibrary.instance().list_categories()


def search_templates(query: str, top_k: int = 5, category: Optional[str] = None) -> List[Dict]:
    """
    按关键词检索模板，返回最相关的 top_k 个。
    
    按关键词检索模板，返回最相关的 top_k 个。

    格式同 list_templates()，额外含 score 和 category 字段。
    """
    lib = TemplateLibrary.instance()
    results = lib.search(query, top_k=top_k, category=category)
    return [
        {
            "name": info.name,
            "category": info.category,
            "block_type": info.block_type,
            "block_name": info.block_name,
            "description": info.description,
            "score": score,
        }
        for info, score in results
    ]


def get_template_xml(
    name: str,
    full: bool = False,
    max_chars: int = 4000,
    category: Optional[str] = None,
) -> Optional[str]:
    """
    获取模板 XML 内容。

    Args:
        name:      模板名（不含 .xml 后缀），或 "category/name" 格式
        full:      True=返回完整XML，False=返回截断摘要
        max_chars: full=False 时的最大字符数
        category:  可选，限定查找分类

    Returns:
        XML 字符串，若不存在则返回 None
    """
    lib = TemplateLibrary.instance()
    if full:
        return lib.read_xml(name, category=category)
    return lib.read_xml_excerpt(name, category=category, max_chars=max_chars)
