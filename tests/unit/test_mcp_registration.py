"""MCP 工具注册不应在导入阶段连接 TIA Portal。"""
import pytest


@pytest.mark.unit
def test_mcp_tools_register_without_tia_connection():
    from mcp.server.fastmcp import FastMCP
    from scdw.mcp.tools import register_mcp_tools

    mcp = FastMCP("SCDW_TEST")
    register_mcp_tools(mcp)
    tools = mcp._tool_manager.list_tools()
    names = [tool.name for tool in tools]
    assert len(names) == len(set(names))
    assert {"init_tia_project", "compile_check", "get_plc_knowledge_catalog", "get_plc_knowledge_items", "create_lad_generation_plan", "save_lad_generation_plan", "create_lad_block_artifact", "append_xml_network", "list_tia_processes", "connect_to_open_tia", "detach_tia_session", "list_workspace_files"}.issubset(names)
    assert {"search_plc_templates", "list_plc_templates", "get_plc_template", "import_template_block"}.isdisjoint(names)
