"""路径、配置、RAG 和 XML 基础处理回归测试。"""
import os
import xml.etree.ElementTree as ET

import pytest

from scdw.common.config import get_deepseek_model
from scdw.common.paths import RAG_GENERATED_DIR, RAG_KNOWLEDGE_DIR, ensure_generated_dir
from scdw.rag.retriever import KnowledgeLibrary, get_knowledge_items
from scdw.openness.tia_blocks import _check_multiple_powerrails, _fix_duplicate_identcon


@pytest.mark.unit
def test_rag_catalog_is_stable_and_has_no_scores():
    KnowledgeLibrary.reset()
    first = KnowledgeLibrary.instance().catalog()
    KnowledgeLibrary.reset()
    second = KnowledgeLibrary.instance().catalog()
    assert first == second
    assert first["selection_mode"] == "explicit_ids"
    assert all("score" not in item and "keywords" not in item for item in first["items"])


@pytest.mark.unit
def test_rag_items_are_read_in_requested_order():
    ids = ["call.fc.v17", "topology.series_contact_coil.v17"]
    items = get_knowledge_items(ids)
    assert [item["id"] for item in items] == ids
    assert ET.fromstring(items[1]["content"]).tag.endswith("FlgNet")


@pytest.mark.unit
def test_generated_directory_is_not_source_directory():
    assert ensure_generated_dir() == RAG_GENERATED_DIR
    assert RAG_GENERATED_DIR.is_dir()
    assert RAG_KNOWLEDGE_DIR.is_dir()


@pytest.mark.unit
def test_legacy_model_variable_is_migrated_to_v4(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setenv("DEEPEEK_MODEL", "deepseek-chat")
    assert get_deepseek_model() == "deepseek-v4-pro"


@pytest.mark.unit
def test_multiple_powerrails_are_rejected():
    xml = '''<FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4"><Parts/><Wires><Wire><Powerrail/></Wire><Wire><Powerrail/></Wire></Wires></FlgNet>'''
    with pytest.raises(ValueError, match="Powerrail"):
        _check_multiple_powerrails(xml)


@pytest.mark.unit
def test_duplicate_identcon_without_access_is_left_unchanged():
    xml = '''<FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4"><Parts/><Wires><Wire><IdentCon UId="1"/></Wire><Wire><IdentCon UId="1"/></Wire></Wires></FlgNet>'''
    assert _fix_duplicate_identcon(xml) == xml
