"""真实 TIA Portal Openness 基线测试。"""
from __future__ import annotations

import pytest

from scdw.common.paths import RAG_APPLICATION_RAW_DIR, RAG_RAW_DIR
from scdw.openness.tia_blocks import import_lad_xml_block, import_scl_block
from scdw.openness.tia_compiler import compile_plc
from scdw.openness.tia_tags import TagSpec, create_tag_table_with_tags


pytestmark = [pytest.mark.integration, pytest.mark.tia, pytest.mark.requires_tia, pytest.mark.slow]


def test_tia_can_create_project_and_release(tia_session, tmp_path):
    tia_session.create_project(tmp_path, "SCDW_TEST_Create")
    assert tia_session.get_context_summary()["project_name"] == "SCDW_TEST_Create"


def test_tia_plc_tag_and_compile(test_plc_software):
    session, device = test_plc_software
    result = session.run_plc_operation(
        "test_tag_and_compile",
        device,
        lambda _project, plc: (
            create_tag_table_with_tags(
                plc,
                "SCDW_TEST_Tags",
                [TagSpec(name="SCDW_TEST_Input", data_type="Bool", logical_address="%M0.0", comment="自动化测试变量")],
            ),
            compile_plc(plc),
        )[1],
    )
    assert result.success, result.summary()
    assert isinstance(result.messages, list)


def test_tia_import_main_xml_reports_missing_dependencies(test_plc_software):
    """Main 模板可以导入，但缺少其应用块依赖时必须报告真实诊断。"""
    session, device = test_plc_software
    xml_content = (RAG_APPLICATION_RAW_DIR / "Main.xml").read_text(encoding="utf-8")
    result = session.run_plc_operation(
        "test_missing_dependencies", device,
        lambda _project, plc: (import_lad_xml_block(plc, session.get_temp_dir(), "SCDW_TEST_Main", xml_content), compile_plc(plc))[1],
    )
    assert not result.success
    assert any("风机燃气" in message for message in result.messages)


def test_tia_import_scl_block_and_compile(test_plc_software):
    """导入最小 SCL FC，验证程序块导入和编译成功路径。"""
    session, device = test_plc_software
    scl = '''FUNCTION "SCDW_TEST_FC" : Void
VERSION : 0.1
BEGIN
END_FUNCTION
'''
    result = session.run_plc_operation(
        "test_import_scl", device,
        lambda _project, plc: (import_scl_block(plc, session.get_temp_dir(), "SCDW_TEST_FC", scl), compile_plc(plc))[1],
    )
    assert result.success, result.summary()


def test_tia_import_xml_with_missing_symbol_reports_compile_error(test_plc_software):
    """引用不存在 DB 的基础模板必须产生真实编译错误。"""
    session, device = test_plc_software
    xml_content = (RAG_RAW_DIR / "basic" / "01_串联_触点线圈.xml").read_text(encoding="utf-8")
    result = session.run_plc_operation(
        "test_missing_symbol", device,
        lambda _project, plc: (import_lad_xml_block(plc, session.get_temp_dir(), "SCDW_TEST_MissingSymbol", xml_content), compile_plc(plc))[1],
    )
    assert not result.success
    assert result.messages
