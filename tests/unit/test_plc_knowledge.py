from __future__ import annotations

import json
from pathlib import Path

import pytest

from scdw.common.paths import PROJECT_ROOT, RAG_APPLICATION_RAW_DIR, RAG_KNOWLEDGE_DIR
from scdw.rag.retriever import KnowledgeCatalogError, KnowledgeLibrary, search_templates


def _metadata(item_id: str, content_path: str, **extra):
    value = {
        "id": item_id, "title": item_id, "description": "test", "role": "test",
        "tia_version": "V17", "content_type": "xml_fragment", "content_path": content_path,
        "intent": [], "not_for": [], "provides": [], "requires": [], "replace": [],
        "preserve": [], "source_refs": ["pyproject.toml"], "status": "golden",
        "contains": {"parts": [], "calls": [], "access_scopes": []},
    }
    value.update(extra)
    return value


def _write_catalog(root: Path, items):
    root.mkdir()
    (root / "catalog.json").write_text(json.dumps({"items": items}), encoding="utf-8")


@pytest.mark.unit
def test_complete_catalog_loads_and_every_content_exists():
    library = KnowledgeLibrary(RAG_KNOWLEDGE_DIR)
    catalog = library.catalog()
    assert len(catalog["items"]) >= 20
    assert all((RAG_KNOWLEDGE_DIR / item["content_path"]).is_file() for item in catalog["items"])


@pytest.mark.unit
def test_raw_application_is_never_published_or_scanned():
    catalog = KnowledgeLibrary(RAG_KNOWLEDGE_DIR).catalog()
    assert RAG_APPLICATION_RAW_DIR.is_dir()
    assert all("raw" not in Path(item["content_path"]).parts for item in catalog["items"])
    assert all(Path(item["content_path"]).suffix in {".xml", ".md"} for item in catalog["items"])


@pytest.mark.unit
def test_legacy_search_does_not_score_or_return_results():
    with pytest.raises(RuntimeError, match="关键词模板检索已废弃"):
        search_templates("TON", top_k=5)


@pytest.mark.unit
def test_raw_path_cannot_be_published(tmp_path):
    root = tmp_path / "knowledge"
    _write_catalog(root, [_metadata("raw", "../raw/application/source.xml")])
    with pytest.raises(KnowledgeCatalogError, match="越出 knowledge"):
        KnowledgeLibrary(root)


@pytest.mark.unit
def test_duplicate_id_is_rejected(tmp_path):
    root = tmp_path / "knowledge"
    _write_catalog(root, [_metadata("same", "one.xml"), _metadata("same", "two.xml")])
    (root / "one.xml").write_text("<x />", encoding="utf-8")
    (root / "two.xml").write_text("<x />", encoding="utf-8")
    with pytest.raises(KnowledgeCatalogError, match="ID 重复"):
        KnowledgeLibrary(root)


@pytest.mark.unit
def test_missing_content_is_rejected(tmp_path):
    root = tmp_path / "knowledge"
    _write_catalog(root, [_metadata("missing", "missing.xml")])
    with pytest.raises(KnowledgeCatalogError, match="内容文件不存在"):
        KnowledgeLibrary(root)


@pytest.mark.unit
def test_obvious_metadata_xml_mismatch_is_rejected(tmp_path):
    root = tmp_path / "knowledge"
    item = _metadata("bad", "bad.xml", contains={"parts": ["TON"], "calls": [], "access_scopes": []})
    _write_catalog(root, [item])
    (root / "bad.xml").write_text("<FlgNet><Parts><Part Name='Move'/></Parts><Wires/></FlgNet>", encoding="utf-8")
    with pytest.raises(KnowledgeCatalogError, match="metadata 与 XML 不一致"):
        KnowledgeLibrary(root)


@pytest.mark.unit
def test_all_application_distillations_are_traceable():
    manifest = json.loads((RAG_KNOWLEDGE_DIR / "distillation.json").read_text(encoding="utf-8"))
    catalog_ids = {item["id"] for item in KnowledgeLibrary(RAG_KNOWLEDGE_DIR).catalog()["items"]}
    catalog = {item["id"]: item for item in KnowledgeLibrary(RAG_KNOWLEDGE_DIR).catalog()["items"]}
    raw_files = {path.resolve() for path in RAG_APPLICATION_RAW_DIR.glob("*.xml")}
    traced_files = {(PROJECT_ROOT / row["source"]).resolve() for row in manifest["sources"]}
    assert raw_files == traced_files
    assert all(row["extracted_items"] for row in manifest["sources"])
    assert all(set(row["extracted_items"]) <= catalog_ids for row in manifest["sources"])
    assert all(row["source"] in catalog[item_id]["source_refs"] for row in manifest["sources"] for item_id in row["extracted_items"])
