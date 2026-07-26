"""路径、配置、RAG 和 XML 基础处理回归测试。"""
import os
import xml.etree.ElementTree as ET

import pytest

from scdw.common.config import get_deepseek_model
from scdw.common.paths import RAG_GENERATED_DIR, RAG_TEMPLATES_DIR, ensure_generated_dir
from scdw.rag.retriever import TemplateLibrary, get_template_xml, search_templates
from scdw.openness.tia_blocks import _check_multiple_powerrails, _fix_duplicate_identcon


@pytest.mark.unit
def test_rag_query_is_stable():
    TemplateLibrary.reset()
    first = search_templates("烧嘴控制", top_k=3)
    TemplateLibrary.reset()
    second = search_templates("烧嘴控制", top_k=3)
    assert first == second
    assert first and first[0]["name"] == "烧嘴控制"


@pytest.mark.unit
def test_rag_xml_can_be_parsed():
    xml = get_template_xml("01_串联_触点线圈", full=True)
    assert xml is not None
    assert ET.fromstring(xml).tag == "Document"


@pytest.mark.unit
def test_generated_directory_is_not_source_directory():
    assert ensure_generated_dir() == RAG_GENERATED_DIR
    assert RAG_GENERATED_DIR.is_dir()
    assert RAG_TEMPLATES_DIR.is_dir()


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
