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
    assert {"init_tia_project", "import_and_compile_artifact", "get_plc_knowledge_catalog", "get_plc_knowledge_items", "draft_lad_generation_plan", "save_lad_generation_plan", "revise_lad_network_plan", "reconcile_lad_workflow", "create_lad_block_artifact", "append_network_and_prepare_import", "write_lad_network_from_knowledge", "list_tia_processes", "connect_to_open_tia", "detach_tia_session", "list_workspace_files"}.issubset(names)
    assert {"import_lad_xml", "compile_check"}.isdisjoint(names)
    assert {"search_plc_templates", "list_plc_templates", "get_plc_template", "import_template_block"}.isdisjoint(names)

    plan_schema = mcp._tool_manager._tools["save_lad_generation_plan"].parameters
    network_schema = plan_schema["$defs"]["NetworkPlanningInput"]
    assert {"purpose", "main_branch", "instructions", "variables", "required_capabilities", "selected_knowledge_ids", "instruction_chain", "topology"} <= set(network_schema["required"])
    assert network_schema["properties"]["topology"]["$ref"].endswith("NetworkTopologyInput")
