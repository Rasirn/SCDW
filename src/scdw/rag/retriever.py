"""TIA Portal V17 LAD SimaticML 精简知识库。

运行时只读取 ``data/rag/knowledge/catalog.json`` 明确发布的条目。原始
application XML 位于 ``data/rag/raw``，不会被目录扫描或通过读取接口暴露。
"""
from __future__ import annotations

import json
import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from scdw.common.paths import (
    PROJECT_ROOT,
    RAG_GENERATED_DIR,
    RAG_KNOWLEDGE_DIR,
    ensure_generated_dir,
)

KNOWLEDGE_DIR = RAG_KNOWLEDGE_DIR
CATALOG_FILE = "catalog.json"
ALLOWED_STATUSES = {"golden", "verified", "draft", "deprecated"}
ALLOWED_CONTENT_TYPES = {"xml_fragment", "rule_document"}
REQUIRED_FIELDS = {
    "id", "title", "description", "role", "tia_version", "content_type",
    "content_path", "intent", "not_for", "provides", "requires", "replace",
    "preserve", "source_refs", "status",
}


class KnowledgeCatalogError(ValueError):
    """知识目录或条目校验失败。"""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass(frozen=True)
class KnowledgeItem:
    metadata: dict[str, Any]
    content_path: Path

    @property
    def id(self) -> str:
        return str(self.metadata["id"])

    def catalog_entry(self) -> dict[str, Any]:
        return dict(self.metadata)


class KnowledgeLibrary:
    """从单一 catalog 加载并严格校验显式发布的知识项。"""

    _instance: Optional["KnowledgeLibrary"] = None

    def __init__(self, knowledge_dir: Path = KNOWLEDGE_DIR) -> None:
        self._dir = Path(knowledge_dir).resolve()
        self._catalog_path = self._dir / CATALOG_FILE
        self._items: dict[str, KnowledgeItem] = {}
        self._catalog_meta: dict[str, Any] = {}
        self._content_cache: dict[str, str] = {}
        self._load()

    @classmethod
    def instance(cls, knowledge_dir: Path = KNOWLEDGE_DIR) -> "KnowledgeLibrary":
        if cls._instance is None:
            cls._instance = cls(knowledge_dir)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def reload(self) -> None:
        self._load()

    def _load(self) -> None:
        if not self._catalog_path.is_file():
            raise KnowledgeCatalogError(f"知识目录文件不存在: {self._catalog_path}")
        try:
            document = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeCatalogError(f"知识目录无法读取: {exc}") from exc
        entries = document.get("items") if isinstance(document, dict) else None
        if not isinstance(entries, list):
            raise KnowledgeCatalogError("catalog.json 的 items 必须是数组")

        loaded: dict[str, KnowledgeItem] = {}
        content_cache: dict[str, str] = {}
        for index, metadata in enumerate(entries):
            if not isinstance(metadata, dict):
                raise KnowledgeCatalogError(f"items[{index}] 必须是对象")
            missing = sorted(REQUIRED_FIELDS - metadata.keys())
            if missing:
                raise KnowledgeCatalogError(f"items[{index}] 缺少字段: {', '.join(missing)}")
            item_id = str(metadata["id"])
            if item_id in loaded:
                raise KnowledgeCatalogError(f"知识项 ID 重复: {item_id}")
            self._validate_metadata(item_id, metadata)
            content_path = self._resolve_published_path(item_id, str(metadata["content_path"]))
            if not content_path.is_file():
                raise KnowledgeCatalogError(f"知识项 {item_id} 的内容文件不存在: {metadata['content_path']}")
            content = content_path.read_text(encoding="utf-8")
            if metadata["content_type"] == "xml_fragment":
                root = self._parse_fragment(item_id, content)
                self._validate_declared_xml(item_id, metadata.get("contains", {}), root)
            self._validate_sources(item_id, metadata["source_refs"])
            loaded[item_id] = KnowledgeItem(dict(metadata), content_path)
            content_cache[item_id] = content

        self._catalog_meta = {key: value for key, value in document.items() if key != "items"}
        self._items = loaded
        self._content_cache = content_cache

    def _resolve_published_path(self, item_id: str, relative: str) -> Path:
        candidate = (self._dir / relative).resolve()
        try:
            candidate.relative_to(self._dir)
        except ValueError as exc:
            raise KnowledgeCatalogError(f"知识项 {item_id} 的内容路径越出 knowledge: {relative}") from exc
        lowered = {part.casefold() for part in candidate.parts}
        if "raw" in lowered or "application" in lowered:
            raise KnowledgeCatalogError(f"知识项 {item_id} 错误发布了 raw/application 内容")
        return candidate

    @staticmethod
    def _validate_metadata(item_id: str, metadata: dict[str, Any]) -> None:
        if metadata["status"] not in ALLOWED_STATUSES:
            raise KnowledgeCatalogError(f"知识项 {item_id} 的 status 非法: {metadata['status']}")
        if metadata["content_type"] not in ALLOWED_CONTENT_TYPES:
            raise KnowledgeCatalogError(f"知识项 {item_id} 的 content_type 非法")
        if metadata["tia_version"] != "V17":
            raise KnowledgeCatalogError(f"知识项 {item_id} 不是目标 TIA V17")
        for field in ("intent", "not_for", "provides", "requires", "replace", "preserve", "source_refs"):
            if not isinstance(metadata[field], list):
                raise KnowledgeCatalogError(f"知识项 {item_id} 的 {field} 必须是数组")

    @staticmethod
    def _parse_fragment(item_id: str, content: str) -> ET.Element:
        try:
            return ET.fromstring(content)
        except ET.ParseError:
            try:
                return ET.fromstring(f"<KnowledgeFragment>{content}</KnowledgeFragment>")
            except ET.ParseError as exc:
                raise KnowledgeCatalogError(f"知识项 {item_id} 的 XML 片段无法解析: {exc}") from exc

    @staticmethod
    def _validate_declared_xml(item_id: str, contains: Any, root: ET.Element) -> None:
        if not isinstance(contains, dict):
            raise KnowledgeCatalogError(f"知识项 {item_id} 的 contains 必须是对象")
        actual_parts = {e.attrib.get("Name", "") for e in root.iter() if _local(e.tag) == "Part"}
        actual_calls = {e.attrib.get("BlockType", "") for e in root.iter() if _local(e.tag) == "CallInfo"}
        actual_scopes = {e.attrib.get("Scope", "") for e in root.iter() if _local(e.tag) == "Access"}
        checks = (("parts", actual_parts), ("calls", actual_calls), ("access_scopes", actual_scopes))
        for field, actual in checks:
            declared = set(contains.get(field, []))
            missing = sorted(declared - actual)
            if missing:
                raise KnowledgeCatalogError(
                    f"知识项 {item_id} 的 metadata 与 XML 不一致: {field} 缺少 {missing}"
                )

    @staticmethod
    def _validate_sources(item_id: str, refs: Iterable[str]) -> None:
        for ref in refs:
            source = (PROJECT_ROOT / str(ref)).resolve()
            try:
                source.relative_to(PROJECT_ROOT.resolve())
            except ValueError as exc:
                raise KnowledgeCatalogError(f"知识项 {item_id} 的 source_ref 越出项目: {ref}") from exc
            if not source.is_file():
                raise KnowledgeCatalogError(f"知识项 {item_id} 的 source_ref 不存在: {ref}")

    def catalog(self) -> dict[str, Any]:
        return {**self._catalog_meta, "items": [item.catalog_entry() for item in self._items.values()]}

    @staticmethod
    def _verification_states(metadata: dict[str, Any]) -> list[str]:
        states = ["source_exported"] if metadata.get("source_refs") else []
        if metadata.get("status") in {"golden", "verified"}:
            states.extend(["import_verified", "compile_verified"])
        if metadata.get("generation_mode") == "knowledge_renderer_required":
            states.append("generated_case_verified")
        if metadata.get("status") == "draft":
            states.append("experimental")
        return states

    def compact_catalog(self, *, roles: Iterable[str] | None = None, capabilities: Iterable[str] | None = None) -> dict[str, Any]:
        """Return deterministic, non-scored catalog filtering for model selection."""
        role_set = {str(value) for value in roles or []}
        capability_set = {str(value) for value in capabilities or []}
        items = []
        for item in self._items.values():
            metadata = item.metadata
            if role_set and str(metadata.get("role")) not in role_set:
                continue
            provides = {str(value) for value in metadata.get("provides", [])}
            if capability_set and not capability_set <= provides:
                continue
            items.append({
                "id": item.id,
                "title": metadata.get("title"),
                "role": metadata.get("role"),
                "provides": sorted(provides),
                "status": metadata.get("status"),
                "verification_states": self._verification_states(metadata),
                "limitations": list(metadata.get("not_for", []))[:4],
                "generation_mode": metadata.get("generation_mode"),
            })
        encoded = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "schema_version": self._catalog_meta.get("schema_version", 2),
            "tia_version": self._catalog_meta.get("tia_version", "V17"),
            "selection_mode": "explicit_ids",
            "catalog_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "filters": {"roles": sorted(role_set), "capabilities": sorted(capability_set)},
            "items": items,
        }

    def get_many(self, item_ids: Iterable[str]) -> list[dict[str, Any]]:
        requested = list(item_ids)
        if not requested:
            raise KnowledgeCatalogError("item_ids 至少包含一个知识项 ID")
        unknown = [item_id for item_id in requested if item_id not in self._items]
        if unknown:
            raise KeyError(f"未知知识项 ID: {', '.join(unknown)}")
        return [
            {"id": item_id, "metadata": self._items[item_id].catalog_entry(),
             "content": self._content_cache[item_id],
             "content_sha256": hashlib.sha256(self._content_cache[item_id].encode("utf-8")).hexdigest()}
            for item_id in requested
        ]


def get_knowledge_catalog() -> dict[str, Any]:
    return KnowledgeLibrary.instance().catalog()


def get_knowledge_items(item_ids: Iterable[str]) -> list[dict[str, Any]]:
    return KnowledgeLibrary.instance().get_many(item_ids)


# 内部兼容层：不再公开为 MCP 工具，也不进行搜索、评分或截断。
TemplateLibrary = KnowledgeLibrary


def list_template_catalog(category: str | None = None) -> list[dict[str, Any]]:
    items = get_knowledge_catalog()["items"]
    return [item for item in items if not category or item["content_path"].split("/", 1)[0] == category]


def list_templates(category: str | None = None) -> list[dict[str, Any]]:
    return list_template_catalog(category)


def list_categories() -> dict[str, str]:
    categories = {item["content_path"].split("/", 1)[0] for item in list_template_catalog()}
    return {name: name for name in sorted(categories)}


def get_template_xml(name: str, full: bool = True, max_chars: int = 4000, category: str | None = None) -> str | None:
    try:
        return get_knowledge_items([name])[0]["content"]
    except KeyError:
        return None


def search_templates(query: str, top_k: int = 5, category: str | None = None) -> list[dict[str, Any]]:
    """已废弃的内部兼容入口；不执行关键词检索或相关度评分。"""
    raise RuntimeError("关键词模板检索已废弃；请读取完整知识目录并显式选择知识项 ID")


def get_generated_dir() -> Path:
    return ensure_generated_dir()


def save_generated_xml(block_name: str, xml_content: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", block_name)
    path = get_generated_dir() / f"{safe_name}_{timestamp}.xml"
    path.write_text(xml_content, encoding="utf-8")
    return path
