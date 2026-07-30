# -*- coding: utf-8 -*-
"""
core/tools.py

分两部分：
  1. ToolManager  ── 客户端侧辅助类，负责从 MCP 客户端获取工具列表并执行工具调用。
  2. TIA 工具注册 ── register_mcp_tools(mcp) 向 FastMCP 服务器注册所有 TIA Portal 工具。
"""
import json
import os
import traceback
from pathlib import Path
from typing import Dict, List, Literal, Optional

from scdw.mcp.client import MCPClient
from scdw.mcp.tool_manager import ToolManager
from mcp.types import CallToolResult, Tool, TextContent
import openai.types.chat.chat_completion as Message

from pydantic import BaseModel, Field
from scdw.common.paths import GENERATED_DIR, PROJECT_ROOT, RAG_GENERATED_DIR
from scdw.openness.session import TiaSessionManager
from scdw.rag.retriever import (
    TemplateLibrary,
    get_knowledge_catalog,
    get_knowledge_items,
    get_template_xml,
    list_categories,
    list_template_catalog,
    list_templates,
    save_generated_xml,
    search_templates,
)


class GlobalDbVariableInput(BaseModel):
    name: str
    data_type: str
    initial_value: str | int | float | bool | None = None
    comment: str = ""
    address: str | None = None
    offset: str | None = None

class _LegacyToolManager:
    @classmethod
    async def get_all_tools(cls, clients: dict[str, MCPClient]) -> list[dict]:
        """Gets all tools from the provided clients in OpenAI/DeepSeek format."""
        tools = []
        for client in clients.values():
            tool_models = await client.list_tools()
            for t in tool_models:
                # 转换为 DeepSeek/OpenAI 格式
                tool = {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.inputSchema,
                    }
                }
                tools.append(tool)
        return tools

    @classmethod
    async def _find_client_with_tool(
        cls, clients: list[MCPClient], tool_name: str
    ) -> Optional[MCPClient]:
        """Finds the first client that has the specified tool."""
        for client in clients:
            tools = await client.list_tools()
            tool = next((t for t in tools if t.name == tool_name), None)
            if tool:
                return client
        return None

    @classmethod
    def _build_tool_result_part(
        cls,
        tool_use_id: str,
        text: str,
        status: str,
    ) -> dict:
        """Builds a tool result part in OpenAI/DeepSeek format."""
        # OpenAI/DeepSeek 格式的 tool result
        return {
            "role": "tool",
            "tool_call_id": tool_use_id,
            "content": text,
        }

    @classmethod
    async def execute_tool_requests(
        cls, clients: dict[str, MCPClient], message: Message
    ) -> List[dict]:
        """
        执行工具调用请求
        
        工作流程：
        1. 从消息中提取所有工具调用请求
        2. 对每个工具调用，找到对应的 MCP 客户端
        3. 执行工具调用
        4. 将结果格式化为 DeepSeek/OpenAI 要求的格式
        
        Args:
            clients: 所有可用的 MCP 客户端字典
            message: DeepSeek 返回的消息对象，包含工具调用信息
            
        Returns:
            List[dict]: 工具执行结果列表，每个结果格式为：
                {
                    "role": "tool",
                    "tool_call_id": "调用ID",
                    "content": "执行结果文本"
                }
        """
        # 从消息中提取工具调用列表
        # DeepSeek/OpenAI 格式中，工具调用存储在 message.tool_calls 中
        tool_requests = message.message.tool_calls if hasattr(message.message, 'tool_calls') else []
        tool_result_blocks: list[dict] = []
        
        # 遍历每个工具调用请求
        for tool_request in tool_requests:
            # 提取工具调用的关键信息
            tool_use_id = tool_request.id                    # 工具调用的唯一ID
            tool_name = tool_request.function.name           # 要调用的工具名称
            tool_input = json.loads(tool_request.function.arguments)  # 工具参数（从JSON字符串解析）
            
            # 步骤1：查找拥有该工具的客户端
            client = await cls._find_client_with_tool(
                list(clients.values()), tool_name
            )

            # 如果找不到对应的客户端，返回错误信息
            if not client:
                error_msg = f"Error: Tool '{tool_name}' not found"
                tool_result_blocks.append(
                    cls._build_tool_result_part(
                        tool_use_id, 
                        error_msg, 
                        "error"
                    )
                )
                continue

            # 步骤2：执行工具调用
            try:
                # 调用工具（注意：client.call_tool 是异步方法，需要 await）
                tool_output = await client.call_tool(tool_name, tool_input)
                
                # 步骤3：提取工具返回的文本内容
                content_texts = []
                
                if tool_output:
                    # 检查工具输出是否包含 content 属性（MCP 标准格式）
                    if hasattr(tool_output, 'content'):
                        # 遍历 content 列表中的每个项目
                        for item in tool_output.content:
                            # 如果是文本内容，提取 text 属性
                            if hasattr(item, 'text'):
                                content_texts.append(item.text)
                            else:
                                # 如果不是文本内容，转换为字符串
                                content_texts.append(str(item))
                    else:
                        # 如果不是标准格式，直接将整个输出转为字符串
                        content_texts.append(str(tool_output))
                else:
                    content_texts.append("Tool returned None")
                
                # 将所有内容片段合并为单一文本
                result_text = "\n".join(content_texts) if content_texts else "Tool executed successfully but returned no content"
                
                # 步骤4：将结果格式化为 DeepSeek/OpenAI 要求的格式
                tool_result_blocks.append(
                    cls._build_tool_result_part(
                        tool_use_id,
                        result_text,
                        "success",  # 状态参数保留但不在结果中使用
                    )
                )
                
            except Exception as e:
                # 工具执行出错，返回错误信息
                error_message = f"Error executing tool '{tool_name}': {str(e)}"
                tool_result_blocks.append(
                    cls._build_tool_result_part(
                        tool_use_id,
                        error_message,
                        "error",
                    )
                )

        return tool_result_blocks


# ══════════════════════════════════════════════════════════════════════════════
# TIA Portal 会话管理
# ══════════════════════════════════════════════════════════════════════════════

# 模块级会话状态（MCP server 在单进程内运行，用 dict 保存状态即可）
_session = TiaSessionManager()


def _check_session() -> None:
    """检查 TIA 会话是否已初始化，否则抛出 RuntimeError。"""
    _session.ensure_current_context()
    _session.require_project()


def _get_plc_software(device_name: str):
    """从会话中获取指定设备的 plc_software 对象。"""
    return _session.get_plc_software(device_name)


def _ensure_temp_dir() -> str:
    """获取或创建临时目录。"""
    return _session.get_temp_dir()


def _cleanup_session() -> None:
    """释放 TIA 资源；项目保存只能由 save_verified_project 显式执行。"""
    _session.close(save=False)


def _tool_error(code: str, message: str, *, retryable: bool = False, needs_user_action: bool = False, data: dict | None = None) -> str:
    """返回给模型的安全结构化错误；完整异常仅写入运行日志。"""
    return json.dumps({"success": False, "code": code, "message": message, "retryable": retryable,
                       "needs_user_action": needs_user_action, "data": data or {}}, ensure_ascii=False)


def _list_workspace_directory(directory: str, recursive: bool, max_entries: int) -> dict:
    """List a project-local directory without exposing paths outside this workspace."""
    root = PROJECT_ROOT.resolve()
    requested = (Path(directory).expanduser() if directory else GENERATED_DIR).resolve()
    try:
        relative = requested.relative_to(root)
    except ValueError:
        return {"success": False, "code": "PATH_NOT_ALLOWED", "message": "directory must be inside the project workspace"}
    if not requested.exists():
        return {"success": False, "code": "DIRECTORY_NOT_FOUND", "message": "directory does not exist", "directory": str(relative).replace("\\", "/")}
    if not requested.is_dir():
        return {"success": False, "code": "NOT_A_DIRECTORY", "message": "path is not a directory", "directory": str(relative).replace("\\", "/")}
    limit = min(max(1, max_entries), 500)
    iterator = requested.rglob("*") if recursive else requested.iterdir()
    entries = []
    for child in sorted(iterator, key=lambda value: (not value.is_dir(), value.name.lower())):
        if len(entries) >= limit:
            break
        try:
            entries.append({"path": str(child.relative_to(requested)).replace("\\", "/"), "type": "directory" if child.is_dir() else "file", "size": None if child.is_dir() else child.stat().st_size})
        except OSError:
            continue
    return {"success": True, "directory": str(relative).replace("\\", "/"), "entries": entries, "truncated": len(entries) == limit}


# ══════════════════════════════════════════════════════════════════════════════
# MCP 工具注册
# ══════════════════════════════════════════════════════════════════════════════

def register_mcp_tools(mcp) -> None:
    """
    向 FastMCP 实例注册所有 TIA Portal 工具。
    在 mcp_server.py 中调用：register_mcp_tools(mcp)

    工具列表
    ────────
    会话管理：
      init_tia_project       创建新 TIA 项目并初始化会话
      close_tia_session      保存并关闭 TIA 会话

    硬件配置：
      add_plc_to_project     向项目添加 PLC 设备
      add_hardware_module    向 PLC 机架添加 I/O 或通讯模块

    软件配置：
      create_plc_tag_table   创建 PLC 变量表并批量添加变量
      create_global_db       创建全局数据块
      import_scl_block       导入 SCL 程序块
      import_lad_xml         导入 AI 生成的 LAD XML 程序块（同时保存文件）
      save_lad_xml           仅保存 LAD XML 到文件（不导入）
      import_lad_xml_from_file  从已保存的文件路径导入（配合 save_lad_xml 使用）

    编译下载：
      compile_and_save       编译并保存当前项目
      compile_check          仅编译不保存，用于检查错误
      delete_plc_block       删除指定程序块（编译失败后重建用）

    自动化工具：
      create_project_from_xlsx  从 xlsx 文件一键创建完整 TIA 项目
    """
    from scdw.openness import (
        start_tia_portal,
        stop_tia_portal,
        create_project,
        save_project,
        add_plc_device,
        add_module_to_rack,
        create_tag_table_with_tags,
        TagSpec,
        create_global_db as _openness_create_global_db,
        import_scl_block,
        import_lad_xml_block,
        build_global_db_scl,
        DBVariable,
        compile_plc,
        delete_block,
    )
    from scdw.xlsx.reader import read_plc_project_xlsx
    from scdw.mcp.lad_plan_tools import register_lad_plan_tools
    register_lad_plan_tools(mcp)

    @mcp.tool(name="list_tia_processes", description="列出运行中的 TIA Portal，不会附着或修改任何工程。")
    def list_tia_processes() -> str:
        try:
            return json.dumps(_session.list_processes(), ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"获取 TIA 进程失败：{exc}"

    @mcp.tool(name="connect_to_open_tia", description="附着到用户已打开的 TIA，自动发现当前工程和 PLC；多实例时必须指定进程 ID。")
    def connect_to_open_tia(process_id: int | None = None, project_path: str = "", auto_select: bool = True) -> str:
        try:
            if not auto_select and process_id is None:
                return "已禁止自动选择，请提供 process_id。"
            context = _session.attach(process_id=process_id, project_path=project_path or None)
            return "已连接到 TIA Portal\n" + json.dumps(context, ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"连接已打开 TIA 失败：{exc}。首次附着时请确认 TIA 弹出的 Openness 访问授权窗口。"

    @mcp.tool(name="refresh_tia_context", description="重新扫描 TIA、工程和 PLC。用户在 TIA 中打开或切换工程后调用。")
    def refresh_tia_context() -> str:
        try:
            if not _session.is_alive():
                return _tool_error("TIA_SESSION_LOST", "当前 TIA 会话无效；请由用户明确选择重新连接或新建工程。", needs_user_action=True)
                processes = _session.list_processes()
                if len(processes) == 1:
                    _session.attach(process_id=processes[0]["process_id"])
                else:
                    return "当前没有有效 TIA 会话；请使用 connect_to_open_tia 明确连接。"
            return json.dumps(_session.refresh_context(), ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"刷新 TIA 上下文失败：{exc}"

    @mcp.tool(name="get_tia_context", description="返回当前 TIA 连接、工程、PLC 和上下文版本摘要，不修改工程。")
    def get_tia_context() -> str:
        return json.dumps(_session.get_context_summary(), ensure_ascii=False, indent=2)

    @mcp.tool(name="select_tia_project", description="当一个 TIA 中有多个工程时，按工程名称或完整路径明确选择目标工程。")
    def select_tia_project(project_name: str = "", project_path: str = "") -> str:
        try:
            return json.dumps(_session.select_project(project_name or None, project_path or None), ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"选择 TIA 工程失败：{exc}"

    @mcp.tool(name="detach_tia_session", description="仅断开 AI 的 Openness 连接；不会关闭或保存用户打开的 TIA 和工程。")
    def detach_tia_session() -> str:
        try:
            attached = _session.context.connection_mode == "attached"
            _session.detach(save=False)
            return "已断开 Openness 连接。" + ("用户的 TIA 和工程保持打开。" if attached else "")
        except Exception as exc:
            return f"断开 TIA 会话失败：{exc}"

    # ── 1. init_tia_project ───────────────────────────────────────────────────
    @mcp.tool(
        name="init_tia_project",
        description=(
            "启动 TIA Portal 并新建项目，初始化会话。"
            "调用此工具后才可使用其他工具进行硬件和软件配置。"
            "参数 overwrite=true 时若项目目录已存在则覆盖。"
        ),
    )
    def init_tia_project(
        project_name: str,
        project_root: str = Field(
            default=str(GENERATED_DIR),
            description="新项目保存根目录；未指定时默认 E:\\PlcProject\\Code\\PLC\\SCDW\\data\\generated。创建前请用 list_workspace_files 检查项目名冲突。",
        ),
        api_dir: str = r"E:\PlcProject\SoftWares\Siemens\Automation\Portal V17\PublicAPI\V17",
        with_ui: bool = True,
        overwrite: bool = False,
    ) -> str:
        """创建新 TIA 项目，初始化工具会话。"""
        try:
            from scdw.openness.tia_core import load_tia_api, set_default_api_dir
            set_default_api_dir(api_dir)
            load_tia_api(api_dir)
            preflight = _session.preflight_create_project(project_root, project_name, overwrite)
            if not preflight["success"]:
                return json.dumps(preflight, ensure_ascii=False)
            _cleanup_session()

            _session.start(with_ui=with_ui)
            _session.create_project(project_root, project_name, overwrite=overwrite)

            # ── 清理上轮生成的临时 XML ──────────────────────────────────────
            try:
                import glob as _glob
                _gen_dir = str(RAG_GENERATED_DIR)
                _removed  = 0
                for _f in _glob.glob(os.path.join(_gen_dir, "*.xml")):
                    try:
                        os.remove(_f)
                        _removed += 1
                    except Exception:
                        pass
            except Exception:
                _removed = 0

            project_dir = os.path.join(project_root, project_name)
            _gen_note = f"（已清理 {_removed} 个上轮生成文件）" if _removed else ""
            return (
                f"✅ TIA 项目已创建 {_gen_note}\n"
                f"  项目名称：{project_name}\n"
                f"  项目路径：{project_dir}\n"
                f"  TIA UI：{'有界面' if with_ui else '无界面'}\n"
                f"会话已就绪，可继续添加设备和软件配置。"
            )
        except Exception as exc:
            _cleanup_session()
            return _tool_error("PROJECT_CREATE_FAILED", "创建 TIA 工程失败。", data={"detail": str(exc)})
            return f"❌ 创建项目失败：{exc}\n{traceback.format_exc()}"

    @mcp.tool(
        name="list_workspace_files",
        description="列出项目工作区内指定目录的文件和子目录，用于检查项目名或输出文件是否冲突。directory 默认为 data/generated；可选 recursive 和 max_entries。只读，不访问工作区外路径。",
    )
    def list_workspace_files(directory: str = "", recursive: bool = False, max_entries: int = 200) -> str:
        """Return a bounded, project-local directory listing."""
        try:
            return json.dumps(_list_workspace_directory(directory, recursive, max_entries), ensure_ascii=False, sort_keys=True)
        except Exception as exc:
            traceback.print_exc()
            return _tool_error("INTERNAL_ERROR", "无法列出目录")

    # ── 2. close_tia_session ──────────────────────────────────────────────────
    @mcp.tool(
        name="close_tia_session",
        description="关闭当前TIA会话并释放资源，不保存项目。已验证项目应先调用save_verified_project。",
    )
    def close_tia_session() -> str:
        """关闭 TIA 会话但不隐式保存。"""
        if not _session.context.connected:
            return "ℹ️ 当前没有活动的 TIA 会话。"
        try:
            _cleanup_session()
            return "✅ TIA 会话已关闭且未执行隐式保存。"
        except Exception as exc:
            _cleanup_session()
            return f"⚠️ 关闭会话时出错（资源已释放）：{exc}"

    # ── 3. add_plc_to_project ─────────────────────────────────────────────────
    @mcp.tool(
        name="add_plc_to_project",
        description=(
            "向当前 TIA 项目添加 PLC 设备（CPU）。"
            "order_number 格式：OrderNumber:6ES7 XXX-XXXXX-XXXX/VX.X，"
            "可从博图硬件目录或物理设备铭牌获取。"
            "device_name 为项目树中显示的设备名，如 PLC_1。"
        ),
    )
    def add_plc_to_project(
        order_number: str = Field(description="PLC 订货号，如 OrderNumber:6ES7 214-1BG40-0XB0/V4.5。需要加上 OrderNumber: 前缀。"),
        device_name: str = "PLC_1",
        device_item_name: str = "",
    ) -> str:
        """向项目添加 PLC CPU 设备。"""
        # 手动修正order_number可能没有前缀的情况
        if not order_number.startswith("OrderNumber:"):
            order_number = "OrderNumber:" + order_number.strip()
        # 如果缺少版本号，提醒用户补全
        if "/V" not in order_number:
            return "❌ CPU订货号缺少版本信息，请补全版本号，如 /V4.5。完整格式示例：OrderNumber:6ES7 214-1BG40-0XB0/V4.5"
        try:
            item_name = device_item_name or device_name
            _session.add_plc(order_number, device_name, item_name)
            return (
                f"✅ 已添加 PLC 设备\n"
                f"  名称：{device_name}\n"
                f"  订货号：{order_number}"
            )
        except Exception as exc:
            return f"❌ 添加 PLC 设备失败：{exc}"

    # ── 4. add_hardware_module ────────────────────────────────────────────────
    @mcp.tool(
        name="add_hardware_module",
        description=(
            "向已有 PLC 设备的机架中插入 I/O 模块或通讯模块（调用 PlugNew 接口）。\n"
            "module_type_id 格式：OrderNumber:6ES7 XXXXXX-XXXX-XXXX，版本号可省略（会自动探测）。\n"
            "【S7-1200 槽位规则 - 必须严格遵守】\n"
            "  - CPU 固定占用槽位 1\n"
            "  - CM 通信模块（CM 1241 等）：左侧扩展槽，槽位必须为 101、102 或 103\n"
            "  - SM 信号模块（数字量/模拟量 I/O）：右侧扩展槽，槽位为 2、3、4 ... 最多到 9\n"
            "rack_item_path 可选，用于多级机架定位（JSON 数组，如 [0,1]），留空则自动定位。"
        ),
    )
    def add_hardware_module(
        device_name: str,
        module_type_id: str,
        slot_number: int,
        module_name: str = "",
        rack_item_path = "",
    ) -> str:
        """向 PLC 机架插入硬件模块。"""
        try:
            _check_session()
            if isinstance(rack_item_path, list):
                item_path = rack_item_path
            else:
                item_path = json.loads(rack_item_path) if str(rack_item_path).strip() else None
            name = module_name or f"Module_{slot_number}"
            result = _session.run_project_operation(
                "add_module", lambda project: add_module_to_rack(
                    project, device_name, module_type_id, slot_number, name, rack_item_path=item_path
                )
            )
            return (
                f"✅ 已在 {device_name} 的槽位 {slot_number} 插入模块\n"
                f"  模块信息：{result}"
            )
        except Exception as exc:
            return f"❌ 插入模块失败：{exc}"

    # ── 5. create_plc_tag_table ───────────────────────────────────────────────
    @mcp.tool(
        name="create_plc_tag_table",
        description=(
            "在指定 PLC 设备下创建变量表并批量添加变量。"
            "tags_json 为 JSON 数组，每项格式：\n"
            '  {"name":"变量名","data_type":"Bool","address":"%I0.0","comment":"注释"}\n'
            "data_type 支持：Bool、Int、Word、DWord、Real 等 S7 数据类型。"
            "address 格式：%I/Q/M + 字节.位 或 %IW/QW/MW + 字节号。"
        ),
    )
    def create_plc_tag_table(
        device_name: str,
        table_name: str,
        tags_json,
    ) -> str:
        """创建变量表并批量写入变量。"""
        try:
            _check_session()
            tags_data: list = tags_json if isinstance(tags_json, list) else json.loads(tags_json)
            if not isinstance(tags_data, list):
                return "❌ tags_json 必须是 JSON 数组。"

            tag_specs = [
                TagSpec(
                    name=t["name"],
                    data_type=t.get("data_type", "Bool"),
                    logical_address=t["address"],
                    comment=t.get("comment", ""),
                )
                for t in tags_data
                if t.get("name") and t.get("address")
            ]

            _session.run_plc_operation("create_tag_table", device_name,
                                       lambda _project, plc_sw: create_tag_table_with_tags(plc_sw, table_name, tag_specs))

            return (
                f"✅ 变量表 '{table_name}' 已创建，成功添加 {len(tag_specs)} 个变量。"
            )
        except Exception as exc:
            return f"❌ 创建变量表失败：{exc}"

    # ── 6. create_global_db ───────────────────────────────────────────────────
    @mcp.tool(
        name="create_global_db",
        description="Create a Global DB with an explicit address_mode. symbolic creates an optimized DB; absolute requires every requested address and currently returns ABSOLUTE_DB_ADDRESS_UNSUPPORTED without creating anything. Never omit an address or rename a variable to bypass failure. Returns requested-to-actual mappings; does not create Instance DBs.",
    )
    def create_global_db(
        device_name: str,
        db_name: str,
        db_number: int,
        variables_json: List[GlobalDbVariableInput],
        address_mode: Literal["symbolic", "absolute"],
    ) -> str:
        """创建全局 DB 并写入变量定义。"""
        requested_mappings = []
        try:
            vars_data = [
                item if isinstance(item, dict) else (
                    item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item.dict(exclude_none=True)
                )
                for item in variables_json
            ]
            if not isinstance(vars_data, list):
                return json.dumps({"success": False, "stage": "create_global_db", "code": "INVALID_VARIABLES", "message": "variables_json must be an array"}, ensure_ascii=False)

            requested_mappings = [
                {
                    "requested_name": str(v.get("name", "")),
                    "tia_actual_name": None,
                    "requested_address": str(v.get("address") or v.get("offset") or "") or None,
                    "tia_actual_address": None,
                }
                for v in vars_data
            ]
            if address_mode not in {"symbolic", "absolute"}:
                return json.dumps({
                    "success": False, "stage": "create_global_db", "code": "ADDRESS_MODE_REQUIRED",
                    "message": "address_mode must explicitly be symbolic or absolute; do not infer it by dropping requested addresses.",
                    "variable_mappings": requested_mappings,
                }, ensure_ascii=False, sort_keys=True)
            fixed = [item for item in requested_mappings if item["requested_address"]]
            if address_mode == "symbolic" and fixed:
                return json.dumps({
                    "success": False, "stage": "create_global_db", "code": "ADDRESS_MODE_CONFLICT",
                    "message": "symbolic mode cannot accept fixed addresses; preserve the user's absolute request and use address_mode=absolute.",
                    "variable_mappings": requested_mappings,
                }, ensure_ascii=False, sort_keys=True)
            if address_mode == "absolute" and len(fixed) != len(requested_mappings):
                return json.dumps({
                    "success": False, "stage": "create_global_db", "code": "ABSOLUTE_ADDRESS_REQUIRED",
                    "message": "absolute mode requires address or offset for every variable; omitted addresses are not allowed.",
                    "variable_mappings": requested_mappings,
                }, ensure_ascii=False, sort_keys=True)
            if address_mode == "absolute":
                return json.dumps({
                    "success": False,
                    "stage": "create_global_db",
                    "code": "ABSOLUTE_DB_ADDRESS_UNSUPPORTED",
                    "message": "Fixed %DBx.DBXy.z/offset layout cannot be verified by the current Openness implementation; no DB was created.",
                    "device_name": device_name,
                    "db_name": db_name,
                    "db_number": db_number,
                    "optimized_access": None,
                    "variable_mappings": requested_mappings,
                }, ensure_ascii=False, sort_keys=True)

            _check_session()

            db_vars = [
                DBVariable(
                    name=v["name"],
                    data_type=v.get("data_type", "Bool"),
                    initial_value="" if v.get("initial_value") is None else str(v.get("initial_value")),
                    comment=v.get("comment", ""),
                    offset="",
                )
                for v in vars_data
                if v.get("name") and v.get("data_type")
            ]

            temp_dir = _ensure_temp_dir()
            _session.run_plc_operation("create_global_db", device_name,
                                       lambda _project, plc_sw: _openness_create_global_db(plc_sw, temp_dir, db_name, db_number, db_vars))

            mappings = [{
                "requested_name": item.name,
                "tia_actual_name": item.name,
                "requested_address": None,
                "tia_actual_address": None,
            } for item in db_vars]
            return json.dumps({
                "success": True, "stage": "create_global_db", "code": "GLOBAL_DB_CREATED",
                "device_name": device_name, "db_name": db_name, "db_number": db_number,
                "optimized_access": True, "variable_mappings": mappings,
            }, ensure_ascii=False, sort_keys=True)
        except Exception as exc:
            return json.dumps({
                "success": False, "stage": "create_global_db",
                "code": getattr(exc, "code", type(exc).__name__), "message": str(exc),
                "device_name": device_name, "db_name": db_name, "db_number": db_number,
                "variable_mappings": requested_mappings,
            }, ensure_ascii=False, sort_keys=True)

    # ── 7. import_scl_block ───────────────────────────────────────────────────
    @mcp.tool(
        name="import_scl_block",
        description=(
            "向指定 PLC 设备导入 SCL（结构化文本）程序块。"
            "scl_code 为完整的 SCL 源码，包含块声明和 BEGIN/END 标记。"
            "支持的块类型：OB（组织块）、FB（功能块）、FC（功能）、DB（数据块）。"
            "示例（OB1）：\n"
            "  ORGANIZATION_BLOCK \"Main\"\n"
            "  { S7_Optimized_Access := 'TRUE' }\n"
            "  VERSION : 0.1\n"
            "  BEGIN\n"
            "    // 程序逻辑\n"
            "  END_ORGANIZATION_BLOCK"
        ),
    )
    def import_scl_block(
        device_name: str,
        block_name: str,
        scl_code: str,
    ) -> str:
        """导入 SCL 程序块到 PLC。"""
        try:
            _check_session()
            temp_dir = _ensure_temp_dir()
            from scdw.openness.tia_blocks import import_scl_block as _import_scl
            _session.run_plc_operation("import_scl", device_name,
                                       lambda _project, plc_sw: _import_scl(plc_sw, temp_dir, block_name, scl_code))
            return f"✅ SCL 程序块 '{block_name}' 已成功导入到 {device_name}。"
        except Exception as exc:
            return f"❌ 导入 SCL 块失败：{exc}"

    # ── 8. import_lad_xml ────────────────────────────────────────────────────
    @mcp.tool(
        name="import_lad_xml",
        description=(
            "将 AI 直接生成的 SimaticML XML 导入到指定 PLC 设备。\n"
            "这是新版 LAD 生成主路径：AI 以真实工程模板为参考，直接生成合法 XML。\n\n"
            "═══ 标准工作流 ═══\n"
            "  1. search_plc_templates(query) → 找相关模板\n"
            "  2. get_plc_template(name, full=True) → 获取完整 XML 作参考\n"
            "  3. 在模板 XML 基础上修改，生成目标块 XML\n"
            "  4. import_lad_xml(device_name, block_name, xml_content) → 导入\n"
            "  5. compile_check → 验证，有报错则修正 XML 后重新调用本工具\n\n"
            "═══ XML 必须满足的格式要求 ═══\n"
            "① 根元素：<Document>\n"
            "② 必须含 <Engineering version=\"V17\" />\n"
            "③ 块元素：<SW.Blocks.FC>、<SW.Blocks.FB> 或 <SW.Blocks.OB>\n"
            "   - 属性 ID=\"0\"\n"
            "   - <AttributeList> 中 <Name> 必须与 block_name 一致\n"
            "   - <AutoNumber>true</AutoNumber> → 博途自动分配编号\n"
            "   - <ProgrammingLanguage>LAD</ProgrammingLanguage>\n"
            "④ 每个网络用 <SW.Blocks.CompileUnit> 包裹，含 <NetworkSource>\n"
            "⑤ NetworkSource 内用 FlgNet：\n"
            "   <FlgNet xmlns=\"http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4\">\n"
            "     <Parts>  ← 所有元件（Contact, Coil, Call, Part 等）\n"
            "     <Wires>  ← 所有连线（Powerrail → 触点 → 线圈）\n"
            "   </FlgNet>\n\n"
            "═══ 常用 Parts 元素 ═══\n"
            "⚠️ 核心规则：Access 元素必须在 <Parts> 顶层，不能嵌套在 Contact/Coil/Part 内！\n"
            "   通过 Wires 中的 IdentCon 将 Access（变量）与 Part（指令）的 operand 引脚连接。\n\n"
            "触点（常开 NO）— Parts 中写法：\n"
            "  <Access Scope=\"GlobalVariable\" UId=\"22\">\n"
            "    <Symbol><Component Name=\"DB1\" /><Component Name=\"Start\" /></Symbol>\n"
            "  </Access>\n"
            "  <Part Name=\"Contact\" UId=\"21\" />\n\n"
            "触点（常闭 NC）— 加 <Negated> 子元素：\n"
            "  <Access Scope=\"GlobalVariable\" UId=\"22\">\n"
            "    <Symbol><Component Name=\"DB1\" /><Component Name=\"Stop\" /></Symbol>\n"
            "  </Access>\n"
            "  <Part Name=\"Contact\" UId=\"21\">\n"
            "    <Negated Name=\"operand\" />\n"
            "  </Part>\n\n"
            "输出线圈（Coil=普通, SCoil=置位, RCoil=复位）— Parts 中写法：\n"
            "  <Access Scope=\"GlobalVariable\" UId=\"31\">\n"
            "    <Symbol><Component Name=\"DB1\" /><Component Name=\"MotorRun\" /></Symbol>\n"
            "  </Access>\n"
            "  <Part Name=\"Coil\" UId=\"30\" />\n\n"
            "调用 FC/FB（Call 指令）：\n"
            "  <Call UId=\"21\">\n"
            "    <CallInfo Name=\"FC名称\" BlockType=\"FC\" />\n"
            "  </Call>\n\n"
            "TON 定时器（需要实例 DB）：\n"
            "  <Part Name=\"TON\" UId=\"40\">\n"
            "    <Instance UId=\"41\" Scope=\"GlobalVariable\">\n"
            "      <Symbol><Component Name=\"DB_Motor\" /><Component Name=\"Timer1\" /></Symbol>\n"
            "    </Instance>\n"
            "  </Part>\n\n"
            "═══ Wires 连线规则 ═══\n"
            "引脚分类（极其重要）：\n"
            "  • 功率流引脚 NameCon↔NameCon：Contact 的 in/out，Coil/FunctionBox 的 in，FunctionBox 的 en/eno\n"
            "  • 数据引脚 IdentCon↔NameCon：Contact/Coil 的 operand，FunctionBox 的 in/out1/PT/Q 等\n\n"
            "串联（左母线 → 触点 → 线圈）：\n"
            "  <Wire UId=\"50\"><Powerrail /><NameCon UId=\"21\" Name=\"in\" /></Wire>              ← 左母线→触点 in 引脚\n"
            "  <Wire UId=\"51\"><IdentCon UId=\"22\" /><NameCon UId=\"21\" Name=\"operand\" /></Wire>  ← 变量→触点 operand\n"
            "  <Wire UId=\"52\"><NameCon UId=\"21\" Name=\"out\" /><NameCon UId=\"30\" Name=\"in\" /></Wire>  ← 触点 out→线圈 in\n"
            "  <Wire UId=\"53\"><IdentCon UId=\"31\" /><NameCon UId=\"30\" Name=\"operand\" /></Wire>  ← 变量→线圈 operand\n\n"
            "Function Box 连线（EN/ENO）：\n"
            "  <Wire UId=\"60\"><Powerrail /><NameCon UId=\"40\" Name=\"en\" /></Wire>      ← 左母线→FunctionBox en\n"
            "  <Wire UId=\"61\"><NameCon UId=\"40\" Name=\"eno\" /><NameCon UId=\"30\" Name=\"in\" /></Wire>  ← ENO→线圈 in（注意：是 in 不是 operand）\n"
            "  数据引脚方向：输入=IdentCon在前NameCon在后；输出=NameCon在前IdentCon在后：\n"
            "  <Wire UId=\"62\"><IdentCon UId=\"42\" /><NameCon UId=\"40\" Name=\"PT\" /></Wire>  ← 变量/常量→PT 输入\n"
            "  <Wire UId=\"63\"><NameCon UId=\"40\" Name=\"out1\" /><IdentCon UId=\"43\" /></Wire>  ← Move out1→变量 输出\n\n"
            "  Access（变量连接，定义在 Parts 顶层）：\n"
            "  <Access UId=\"42\" Scope=\"GlobalVariable\">\n"
            "    <Symbol><Component Name=\"DB1\" /><Component Name=\"Timeout\" /></Symbol>\n"
            "  </Access>\n\n"
            "═══ UId 规则 ═══\n"
            "- 整个块内所有 UId 必须是唯一正整数，从 1 开始递增\n"
            "- 不同网络（CompileUnit）的 UId 也不能重复\n"
            "- 每次新建元素都分配一个新的递增 UId\n\n"
            "═══ 全局变量访问格式 ═══\n"
            "  DB成员：<Component Name=\"DB名\" /><Component Name=\"变量名\" />\n"
            "  全局标签：<Component Name=\"标签名\" />（只有一个 Component）\n"
            "  Scope：GlobalVariable（变量）| LocalVariable（局部）| LiteralConstant（常量）\n\n"
            "═══ 常量访问格式 ═══\n"
            "  <Access UId=\"n\" Scope=\"LiteralConstant\">\n"
            "    <Constant><ConstantType>Time</ConstantType><ConstantValue>T#5s</ConstantValue></Constant>\n"
            "  </Access>\n"
            "  ConstantType: Time | Int | Real | Bool | DInt | Word 等\n\n"
            "═══ 关键提示 ═══\n"
            "- 参考模板时：修改 <Name> 的值、变量名称（Component Name），保持其余结构不变\n"
            "- 导入失败后：用 save_lad_xml 保存 XML 到文件检查，或直接查看 compile_check 错误信息修正\n\n"
            "═══ 梯级设计原则（最重要！）═══\n"
            "- 一个 CompileUnit = 一个梯级 = 一句逻辑描述\n"
            "- 每个梯级只能有一个连接到左母线（Powerrail）的起始触点/条件\n"
            "  （即 Powerrail Wire 中只能有一个 NameCon 引脚，或多条并联起始支路共用同一个 Powerrail Wire）\n"
            "- 起始条件之后可以有任意分叉输出（fan-out：一个 out → 多个 in）\n\n"
            "═══ NameCon 引脚唯一性规则（违反会导致'连接被多次使用'或'电源线无效连接'错误）═══\n"
            "- 任何 NameCon 引脚（包括 in/out/en/eno/operand）只能在整个 FlgNet 中出现 1 次\n"
            "- fan-out（一出多入）合法：同一条 Wire 中，一个 out 接多个 in\n"
            "    <Wire UId=\"22\"><NameCon UId=\"A\" Name=\"out\"/><NameCon UId=\"B\" Name=\"in\"/><NameCon UId=\"C\" Name=\"in\"/></Wire>\n"
            "- fan-in（多出一入）非法：多条功率流汇聚到同一个 in 引脚\n"
            "    ⛔ Wire1: out1 → in(C)    Wire2: out2 → in(C)   ← in(C) 被引用2次，报错\n"
            "- OR 逻辑（多条件任意满足→输出）的正确实现方式：\n"
            "  方法1（推荐）：拆成两个独立梯级，各自从 Powerrail 出发\n"
            "    梯级A：条件1 → SCoil\n"
            "    梯级B：条件2 → SCoil（同一个线圈可在多个梯级中被驱动）\n"
            "  方法2：使用 O（OR）功能块合并多路功率流，再接到后续元素\n"
            "    <Part Name=\"O\" UId=\"N\"><TemplateValue Name=\"Card\" Type=\"Cardinality\">2</TemplateValue></Part>\n"
            "    引脚：in1/in2（功率流输入），out（功率流输出）\n"
            "    <Wire UId=\"A\"><NameCon UId=\"Contact1\" Name=\"out\"/><NameCon UId=\"N\" Name=\"in1\"/></Wire>\n"
            "    <Wire UId=\"B\"><NameCon UId=\"Contact2\" Name=\"out\"/><NameCon UId=\"N\" Name=\"in2\"/></Wire>\n"
            "    <Wire UId=\"C\"><NameCon UId=\"N\" Name=\"out\"/><NameCon UId=\"Coil\" Name=\"in\"/></Wire>\n\n"
            "串联后分叉写法（公共条件后多路输出）：\n"
            "  场景：故障信号出现时，同时复位运行标志、复位自动输出、置位报警\n"
            "  关键：Contact(故障).out 引脚连接多个下游线圈的 in 引脚（fan-out）\n"
            "  <Wire UId=\"22\">                 ← 一条 Wire 实现 fan-out\n"
            "    <NameCon UId=\"10\" Name=\"out\" />  ← 公共条件的 out\n"
            "    <NameCon UId=\"11\" Name=\"in\" />   ← 输出1 in\n"
            "    <NameCon UId=\"12\" Name=\"in\" />   ← 输出2 in\n"
            "    <NameCon UId=\"13\" Name=\"in\" />   ← 输出3 in\n"
            "  </Wire>\n"
            "  参考模板：basic/05_串联后分叉.xml\n\n"
            "不要混用：并联起始支路（各自从 Powerrail 出发）≠ 串联后分叉（共用一个先行条件）\n"
            "  使用时机：\n"
            "  - 并联起始支路（从 Powerrail 分叉）：多个完全独立的动作，无共同前提条件\n"
            "  - 串联后分叉（从 Contact.out 分叉）：多个动作共享同一前提条件\n\n"
            "═══ 并联支路规则（违反会导致'程序段中只能包含一个电源线'错误）═══\n"
            "- 每个 CompileUnit（程序段）只能有 1 条 Powerrail\n"
            "- 并联起始支路：在同一条 Powerrail Wire 中连接多个独立支路首元素：\n"
            "    <Wire UId=\"N\">\n"
            "      <Powerrail />\n"
            "      <NameCon UId=\"支路1首元素\" Name=\"in\" />\n"
            "      <NameCon UId=\"支路2首元素\" Name=\"in\" />\n"
            "      <NameCon UId=\"支路3首元素\" Name=\"in\" />\n"
            "    </Wire>\n"
            "- 错误写法：每条并联支路单独写一个 <Wire><Powerrail />...</Wire>（多 Powerrail 错误）\n"
            "- 设计原则：逻辑上相关的并联分支尽量放在同一 CompileUnit，共享一条 Powerrail\n\n"
            "═══ IdentCon 唯一性规则（违反会导致'连接被多次使用'错误）═══\n"
            "- 每个 Access 元素的 UId 只能在 1 条 Wire 中作为 IdentCon 引用\n"
            "- 同一变量连接到多处时，必须为每次引用创建独立的 Access 元素（各用不同 UId）\n"
            "- 错误写法（UId=301 的 Access 在 2 条 Wire 中都被引用）：\n"
            "    <Access UId=\"301\">..SensorA..</Access>\n"
            "    <Wire UId=\"309\"><IdentCon UId=\"301\" /><NameCon UId=\"304\" Name=\"operand\" /></Wire>\n"
            "    <Wire UId=\"311\"><IdentCon UId=\"301\" /><NameCon UId=\"305\" Name=\"operand\" /></Wire>  ← 错误！\n"
            "- 正确写法（两个 Access 指向同一变量，各自在独立 Wire 中引用）：\n"
            "    <Access UId=\"301\">..SensorA..</Access>\n"
            "    <Access UId=\"302\">..SensorA..</Access>  ← 内容相同但 UId 不同\n"
            "    <Wire UId=\"309\"><IdentCon UId=\"301\" /><NameCon UId=\"304\" Name=\"operand\" /></Wire>\n"
            "    <Wire UId=\"311\"><IdentCon UId=\"302\" /><NameCon UId=\"305\" Name=\"operand\" /></Wire>\n"
            "- 系统会尝试自动修复此问题，但建议生成时直接写正确\n\n"
            "═══ S7-1500 专用指令（S7-1200 不支持，禁止出现）═══\n"
            "- GETIO / SETIO / GETIO_PART / SETIO_PART：S7-1500 专用直接 I/O 访问指令\n"
            "  替代方案：在 S7-1200 中直接使用 I/Q 过程映像变量，无需专用指令\n"
            "- GATHER / SCATTER：S7-1200 V4.0+ 支持，可以正常使用（应用模板中有示例）\n"
            "- DisabledENO=\"true\" 属性：系统会自动改为 DisabledENO=\"false\"（属性必须显式存在，完全缺失会导致 Move/GATHER/Calc 等导入失败）\n"
            "  生成 XML 时建议不加此属性；若加则写 DisabledENO=\"false\"，禁止写 DisabledENO=\"true\"\n\n"
            "═══ TemplateValue 语法（违反会导致枚举约束错误，导入失败）═══\n"
            "Type 属性只有两个合法枚举值（大小写严格区分）：\n"
            "  • Type=\"Cardinality\" → 用于 Card 参数，指定输入/输出数量，值为正整数\n"
            "  • Type=\"Type\"        → 用于数据类型参数，值为 TIA 类型名（Real/Int/DInt/Bool/Word 等）\n"
            "  ⛔ 非法值（会直接导致导入失败）：Type=\"DataType\"  Type=\"String\"  Type=\"type\"\n\n"
            "各指令的正确写法（均来自真实工程导出验证）：\n"
            "  Move（1个输出）：\n"
            "    <Part Name=\"Move\" UId=\"N\" DisabledENO=\"false\">\n"
            "      <TemplateValue Name=\"Card\" Type=\"Cardinality\">1</TemplateValue>\n"
            "    </Part>\n"
            "  Mul（2个输入的乘法）：\n"
            "    <Part Name=\"Mul\" UId=\"N\" DisabledENO=\"false\">\n"
            "      <TemplateValue Name=\"Card\" Type=\"Cardinality\">2</TemplateValue>\n"
            "      <AutomaticTyped Name=\"SrcType\" />   ← 不要用 TemplateValue 指定类型，用 AutomaticTyped！\n"
            "    </Part>\n"
            "    引脚：en/eno 功率流，in1/in2（数据输入），out（数据输出）\n"
            "  Calc（公式计算，3个输入 IN1/IN2/IN3）：\n"
            "    <Part Name=\"Calc\" UId=\"N\" DisabledENO=\"false\">\n"
            "      <Equation>(IN1/IN2)*IN3</Equation>   ← 公式用 <Equation> 子元素，变量用 IN1/IN2/IN3（大写）\n"
            "      <TemplateValue Name=\"Card\" Type=\"Cardinality\">3</TemplateValue>\n"
            "      <TemplateValue Name=\"SrcType\" Type=\"Type\">Real</TemplateValue>\n"
            "    </Part>\n"
            "    ⛔ 禁止写法：<TemplateValue Name=\"Expression\" Type=\"String\">...</TemplateValue>（Expression/String 均无效）\n"
            "    引脚：en/eno 功率流，in1/in2/in3...（数据输入，对应 IN1/IN2/IN3），out（数据输出）\n"
            "  Convert（类型转换，Real→Int）：\n"
            "    <Part Name=\"Convert\" UId=\"N\" DisabledENO=\"false\">\n"
            "      <TemplateValue Name=\"SrcType\" Type=\"Type\">Real</TemplateValue>\n"
            "      <TemplateValue Name=\"DestType\" Type=\"Type\">Int</TemplateValue>\n"
            "    </Part>\n"
            "    ⛔ 禁止写法：Name=\"src_type\" / Name=\"dest_type\"（下划线小写无效，必须驼峰 SrcType/DestType）\n"
            "    引脚：en/eno 功率流，in（数据输入），out（数据输出）\n"
            "  O（OR函数块）：\n"
            "    <Part Name=\"O\" UId=\"N\">\n"
            "      <TemplateValue Name=\"Card\" Type=\"Cardinality\">2</TemplateValue>\n"
            "    </Part>\n"
            "    引脚：in1/in2（功率流输入），out（功率流输出）\n\n"
            "═══ 数组元素访问格式（只能接数据引脚，不能接 Contact/Coil operand）═══\n"
            "  <Access Scope=\"GlobalVariable\" UId=\"N\">\n"
            "    <Symbol>\n"
            "      <Component Name=\"DB名\" />\n"
            "      <Component Name=\"数组变量名\" AccessModifier=\"Array\">\n"
            "        <Access Scope=\"LiteralConstant\">\n"
            "          <Constant><ConstantType>DInt</ConstantType><ConstantValue>1</ConstantValue></Constant>\n"
            "        </Access>\n"
            "      </Component>\n"
            "    </Symbol>\n"
            "  </Access>\n"
            "  ⛔ 不能将 Array[0..15] of Bool 的元素直接连接 operand，须先用 Move 赋值到中间 Bool 变量\n\n"
            "═══ Part 排序规则（重要！违反会导致'元素必须根据电流排序'错误）═══\n"
            "- <Parts> 内的 Part 元素必须按功率流方向排列，且同一分支的元素必须连续\n"
            "- 错误写法（所有 Contact 在前，所有 Coil 在后）：\n"
            "    <Part Name=\"Contact\" UId=\"25\" />  ← StartBtn 分支\n"
            "    <Part Name=\"Contact\" UId=\"26\" />  ← StopBtn 分支（错！插入在两个分支之间）\n"
            "    <Part Name=\"SCoil\"   UId=\"27\" />  ← StartBtn 分支的 Set\n"
            "    <Part Name=\"RCoil\"   UId=\"28\" />  ← StopBtn 分支的 Reset\n"
            "- 正确写法（分支1全部元素→分支2全部元素→...）：\n"
            "    <Part Name=\"Contact\" UId=\"25\" />  ← 分支1 起点\n"
            "    <Part Name=\"SCoil\"   UId=\"26\" />  ← 分支1 终点（紧跟上面）\n"
            "    <Part Name=\"Contact\" UId=\"27\" />  ← 分支2 起点\n"
            "    <Part Name=\"RCoil\"   UId=\"28\" />  ← 分支2 终点（紧跟上面）\n"
            "- UId 分配规则：同一分支内的 UId 必须连续递增（25→26 为一支，27→28 为另一支）\n"
            "- 系统会尝试自动修复此排序问题，但建议生成时直接写正确"
        ),
    )
    def import_lad_xml(
        device_name: str,
        block_name: str,
        xml_content: str,
    ) -> str:
        """导入 AI 生成的 LAD XML 到 TIA Portal，并持久保存 XML。"""
        import xml.etree.ElementTree as _ET
        try:
            _check_session()

            # ── 持久保存 XML（无论成功失败都保存） ───────────────────────────
            saved_path = save_generated_xml(block_name, xml_content)

            # ── 基础 XML 语法验证 ────────────────────────────────────────────
            try:
                _ET.fromstring(xml_content.encode("utf-8"))
            except _ET.ParseError as parse_err:
                return (
                    f"❌ XML 语法错误（未导入）：{parse_err}\n"
                    f"已保存至：{saved_path}\n"
                    f"请直接修改该文件中的 XML 后重新调用 import_lad_xml。"
                )

            # ── 导入 TIA Portal ──────────────────────────────────────────────
            temp_dir = _ensure_temp_dir()
            _session.run_plc_operation("import_lad", device_name,
                                       lambda _project, plc_sw: import_lad_xml_block(plc_sw, temp_dir, block_name, xml_content))

            net_count = xml_content.count("SW.Blocks.CompileUnit") // 2
            return (
                f"✅ LAD 程序块 '{block_name}' 已导入到 {device_name}，共 {net_count} 个网络。\n"
                f"📁 XML 已保存：{saved_path}\n"
                f"💡 调用 compile_check(device_name=\"{device_name}\") 验证编译。"
            )
        except Exception as exc:
            err_msg = str(exc)
            try:
                debug = f"\n📁 XML 已保存（可修改后重试）：{saved_path}"
            except Exception:
                debug = ""
            return f"❌ 导入 LAD 块失败：{err_msg}{debug}\n{traceback.format_exc()}"

    # ── 8b. save_lad_xml ─────────────────────────────────────────────────────
    @mcp.tool(
        name="save_lad_xml",
        description=(
            "【调试工具】将 AI 生成的 LAD XML 持久保存到 data/generated/rag/ 目录，不导入 TIA Portal。\n"
            "用途：\n"
            "  - 在导入前检查 XML 语法是否正确\n"
            "  - 保存生成的 XML 供人工检查或用编辑器修改后再导入\n"
            "  - 文件名格式：{block_name}_{时间戳}.xml\n"
            "保存后导入流程（重要）：\n"
            "  1. 调用本工具获取 saved_path\n"
            "  2. 直接调用 import_lad_xml_from_file(device_name, saved_path) 导入\n"
            "     ← 不要重新生成 xml_content，直接用路径！\n"
            "返回：XML 语法验证结果 + 文件保存路径。\n"
            "注意：此工具不会连接 TIA Portal，无需活动会话。"
        ),
    )
    def save_lad_xml(
        block_name: str,
        xml_content: str,
    ) -> str:
        """保存 LAD XML 到生成目录并验证语法（不导入）。"""
        import xml.etree.ElementTree as _ET
        try:
            # 语法验证
            try:
                _ET.fromstring(xml_content.encode("utf-8"))
                syntax_ok = True
                syntax_msg = "✅ XML 语法验证通过"
            except _ET.ParseError as e:
                syntax_ok = False
                syntax_msg = f"⚠️ XML 语法错误：{e}"

            # 持久化保存到统一生成目录
            saved_path = save_generated_xml(block_name, xml_content)

            net_count = xml_content.count("SW.Blocks.CompileUnit") // 2
            lines = [
                f"LAD XML 保存 - {block_name}",
                f"XML 大小：{len(xml_content)} 字节，约 {net_count} 个网络",
                syntax_msg,
                f"\n📁 已持久保存至：{saved_path}",
            ]
            if syntax_ok:
                lines.append(
                    f"💡 语法正常，调用 import_lad_xml_from_file(device_name=..., file_path=\"{saved_path}\") 导入。"
                )
            else:
                lines.append("请修正 XML 语法错误后再调用 import_lad_xml_from_file 导入。")
            return "\n".join(lines)
        except Exception as exc:
            return f"❌ 保存失败：{exc}"

    # ── 8c. import_lad_xml_from_file ─────────────────────────────────────────
    @mcp.tool(
        name="import_lad_xml_from_file",
        description=(
            "从已保存的 XML 文件路径导入 LAD 程序块到 TIA Portal（不需要重新传入 xml_content）。\n"
            "【何时使用】：\n"
            "  - 调用 save_lad_xml 保存文件后，直接用返回的路径调用本工具导入，无需重新生成 XML\n"
            "  - 手动编辑了 data/generated/rag/ 下的 XML 文件后，用本工具重新导入\n"
            "  - 导入失败修改文件后重试\n"
            "【参数】：\n"
            "  device_name: PLC 设备名称（同 import_lad_xml）\n"
            "  file_path:   XML 文件的完整绝对路径（save_lad_xml 的返回值中包含此路径）\n"
            "【注意】：文件必须是合法的 SimaticML XML 格式。"
        ),
    )
    def import_lad_xml_from_file(device_name: str, file_path: str) -> str:
        """从磁盘文件路径直接导入 LAD XML，不重新生成内容。"""
        try:
            _check_session()
            if not os.path.isfile(file_path):
                return f"❌ 文件不存在：{file_path}"
            with open(file_path, encoding="utf-8") as _fh:
                xml_content = _fh.read()
            block_name = os.path.splitext(os.path.basename(file_path))[0]
            # 去掉时间戳后缀（格式：块名_YYYYMMDD_HHMMSS）
            import re as _re2
            block_name = _re2.sub(r'_\d{8}_\d{6}$', '', block_name)
            temp_dir = _ensure_temp_dir()
            _session.run_plc_operation("import_lad_file", device_name,
                                       lambda _project, plc_sw: import_lad_xml_block(plc_sw, temp_dir, block_name, xml_content))
            net_count = xml_content.count("SW.Blocks.CompileUnit") // 2
            return (
                f"✅ LAD 程序块 '{block_name}' 已从文件导入到 {device_name}，共 {net_count} 个网络。\n"
                f"📁 源文件：{file_path}\n"
                f"💡 调用 compile_check(device_name=\"{device_name}\") 验证编译。"
            )
        except Exception as exc:
            return f"❌ 从文件导入失败：{exc}\n{traceback.format_exc()}"

    # ── 9. compile_and_save ───────────────────────────────────────────────────
    @mcp.tool(
        name="compile_and_save",
        description=(
            "编译指定 PLC 设备的软件并保存项目。"
            "编译前请确保所有程序块和变量表已配置完毕。"
            "返回编译状态和消息（警告/错误）。"
        ),
    )
    def compile_and_save(device_name: str) -> str:
        """编译 PLC 软件并保存项目。"""
        try:
            _check_session()
            result = _session.run_plc_operation("compile_and_save", device_name,
                                                lambda project, plc_sw: (compile_plc(plc_sw), save_project(project))[0])

            status = "✅ 编译成功" if result.success else "⚠️ 编译有错误"
            lines = [f"{status}，项目已保存。", result.summary()]
            return "\n".join(lines)
        except Exception as exc:
            return f"❌ 编译/保存失败：{exc}"

    # ── 10. compile_check ─────────────────────────────────────────────────────
    @mcp.tool(
        name="compile_check",
        description=(
            "仅编译指定 PLC 设备的软件（不保存项目），用于检查是否有编译错误。\n"
            "返回编译状态和详细错误/警告消息列表。\n"
            "【工作流程】：\n"
            "  1. 先导入所有块 → 调用 compile_check 检查错误\n"
            "  2. 若有类型不匹配等错误 → 修正 networks_json → 重新调用 import_lad_xml（自动覆盖）\n"
            "  3. 再次 compile_check 确认无误 → 最后调用 compile_and_save 保存\n"
            "【常见编译错误及修复方法】：\n"
            "  - 'Operand type mismatch' / 类型不匹配 → 使用 Convert 指令做类型转换\n"
            "  - Real→Int: 用 Convert(src_type=Real, dest_type=Int)\n"
            "  - Int→Bool: 用 Ne 比较指令 (in1=IntVar, in2=0) 代替直接赋值\n"
            "  - 数学运算类型不一致: 先 Convert 统一类型再运算"
        ),
    )
    def compile_check(device_name: str) -> str:
        """仅编译 PLC 软件，返回编译结果（不保存）。"""
        try:
            _check_session()
            result = _session.run_plc_operation("compile_check", device_name,
                                                lambda _project, plc_sw: compile_plc(plc_sw))

            status = "✅ 编译通过，无错误" if result.success else "⚠️ 编译有错误，需要修正"
            lines = [status, result.summary()]
            if not result.success:
                lines.append(
                    "\n💡 提示：修正 XML 后重新调用 import_lad_xml 覆盖导入，"
                    "修正后再次 compile_check 验证。"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"❌ 编译检查失败：{exc}"

    # ── 11. delete_plc_block ──────────────────────────────────────────────────
    @mcp.tool(
        name="delete_plc_block",
        description="删除目标PLC中的指定程序块并返回是否找到该块。该工具不生成替代块。",
    )
    def delete_plc_block(device_name: str, block_name: str) -> str:
        """删除 PLC 中的指定程序块。"""
        try:
            _check_session()
            found = _session.run_plc_operation("delete_block", device_name,
                                               lambda _project, plc_sw: delete_block(plc_sw, block_name))
            if found:
                return f"✅ 程序块 '{block_name}' 已从 {device_name} 中删除。"
            else:
                return f"ℹ️ 未找到程序块 '{block_name}'，无需删除。"
        except Exception as exc:
            return f"❌ 删除程序块失败：{exc}"

    # ── 12. read_project_spec_from_xlsx ───────────────────────────────────────
    @mcp.tool(
        name="read_project_spec_from_xlsx",
        description="解析xlsx并返回硬件、I/O、Global DB定义和LAD功能需求。只返回工程需求事实，不决定FC、FB、Instance DB或Network组织。",
    )
    def read_project_spec_from_xlsx(xlsx_path: str) -> str:
        """解析 xlsx 文件，返回结构化的 PLC 项目规格信息。"""
        try:
            spec = read_plc_project_xlsx(xlsx_path)
        except Exception as exc:
            return f"❌ 解析 xlsx 失败：{exc}\n{traceback.format_exc()}"

        lines: List[str] = []

        # ── 硬件设备清单 ──────────────────────────────────────────────────────
        lines.append(f"=== 硬件设备清单（共 {len(spec.hardware)} 项）===")
        for hw in spec.hardware:
            lines.append(
                f"  [{hw.category}] {hw.model_full}"
                + (f"  订货号：{hw.order_number}" if hw.order_number else "")
                + (f"  数量：{hw.quantity}" if hw.quantity and hw.quantity != 1 else "")
            )

        # 提取 CPU 订货号供后续参考
        cpu_hw = next((hw for hw in spec.hardware if "cpu" in hw.category.lower()), None)
        if cpu_hw and cpu_hw.order_number:
            lines.append(f"\n>>> CPU 订货号（调用 add_plc_to_project 时使用）：OrderNumber:{cpu_hw.order_number}")
        else:
            lines.append("\n⚠️  未在硬件清单中找到 CPU 订货号，请手动确认。")

        # ── I/O 点表（按模块组） ──────────────────────────────────────────────
        from collections import defaultdict
        groups: dict = defaultdict(list)
        for tag in spec.io_tags:
            groups[tag.module_group or "Default_Tags"].append(tag)

        lines.append(f"\n=== I/O 点表（共 {len(spec.io_tags)} 个变量，{len(groups)} 个模块组）===")
        for group_name, tags in groups.items():
            lines.append(f"\n  [变量表] {group_name}（{len(tags)} 个变量）")
            for t in tags:
                lines.append(
                    f"    {t.name:<30} {t.data_type:<10} {t.address:<14}"
                    + (f"  // {t.comment}" if t.comment else "")
                )

        # ── DB 块 ─────────────────────────────────────────────────────────────
        lines.append(f"\n=== DB 块（共 {len(spec.db_blocks)} 个）===")
        for i, db in enumerate(spec.db_blocks):
            db_number = 100 + i
            lines.append(f"\n  [DB{db_number}] {db.function_name}")
            valid_vars = [v for v in db.variables if v.name and v.data_type]
            lines.append(f"  变量数：{len(valid_vars)}")
            for v in valid_vars:
                offset_str = str(v.offset).strip() if v.offset is not None else ""
                lines.append(
                    f"    {v.name:<30} {v.data_type:<20} offset={offset_str:<8}"
                    + (f"  // {v.comment}" if v.comment else "")
                )

        # ── LAD 功能需求（仅返回需求事实，块与Network由规划流程决定）─────────
        lines.append(
            f"\n=== LAD 功能需求清单（共 {len(spec.logic_functions)} 项，待整体规划）==="
        )
        if not spec.logic_functions:
            lines.append("  （xlsx 中未找到功能描述，无需生成 LAD 块）")
        for fn in spec.logic_functions:
            lines.append(f"\n  [需求{fn.block_index}] 功能名称：{fn.function_name}")
            lines.append(f"  关联 DB 块：{fn.db_block_name}")
            lines.append(f"  >>> 逻辑描述：")
            lines.append(f"  {fn.description}")

        lines.append(
            f"\n=== 汇总 ===\n"
            f"  硬件：{len(spec.hardware)} 项\n"
            f"  I/O 变量：{len(spec.io_tags)} 个（{len(groups)} 个变量表）\n"
            f"  DB 块：{len(spec.db_blocks)} 个\n"
            f"  LAD 功能需求：{len(spec.logic_functions)} 项（块与Network尚未规划）"
        )

        return "\n".join(lines)

    # ── 13. list_plc_templates ────────────────────────────────────────────────
    @mcp.tool(
        name="get_plc_knowledge_catalog",
        description=(
            "返回全部精简TIA V17 LAD知识metadata，不返回正文。先按provides、topology及not_for选择ID；带generation_mode=knowledge_renderer_required的拓扑必须通过write_lad_network_from_knowledge生成，不能自行拼Wire。随后调用get_plc_knowledge_items读取所选正文。"
        ),
    )
    def get_plc_knowledge_catalog() -> str:
        return json.dumps(get_knowledge_catalog(), ensure_ascii=False)

    @mcp.tool(
        name="get_plc_knowledge_items",
        description=(
            "按显式知识项ID批量返回精简XML片段或规则文档。item_ids可包含多个ID，返回顺序与请求一致；raw/application永不通过此接口读取。"
        ),
    )
    def get_plc_knowledge_items(item_ids: List[str]) -> str:
        try:
            return json.dumps({"items": get_knowledge_items(item_ids)}, ensure_ascii=False)
        except (KeyError, ValueError) as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

    @mcp.tool(
        name="list_plc_templates",
        description=(
            "列出 RAG 模板库中所有可用的 PLC 程序模板。\n"
            "模板来自真实博途工程导出的 SimaticML XML 文件，语法合法可直接导入。\n"
            "每条记录含：name、category（分类）、block_type（FC/FB/OB）、description。\n"
            "category 可选值：application（完整工程模板）、basic（基础单指令模板，待扩充）等。\n"
            "返回结果可用于判断是否有匹配的现成模板，有则优先调用 import_template_block 直接导入，"
            "无合适模板时再调用 import_lad_xml 生成。\n"
            "可传 category=<分类名> 只列出该分类的模板。"
        ),
    )
    def list_plc_templates(category: str = "") -> str:
        """列出所有可用的 PLC 程序模板。"""
        try:
            cat_arg = category if category else None
            templates = list_templates(category=cat_arg)
            cats = list_categories()
            if not templates:
                return "ℹ️ 模板库为空，请检查 data/rag/templates/ 目录。"
            lines = [f"📚 共找到 {len(templates)} 个程序模板（分类：{'全部' if not cat_arg else cat_arg}）：\n"]
            last_cat = None
            for t in templates:
                if t["category"] != last_cat:
                    last_cat = t["category"]
                    cat_desc = cats.get(last_cat, "")
                    lines.append(f"\n  【{last_cat}】{cat_desc}")
                lines.append(
                    f"    [{t['block_type']:2s}] {t['name']:<20}  {t['description']}"
                )
            lines.append(
                "\n💡 使用 search_plc_templates(query=<关键词>) 精确检索，"
                "或 import_template_block(template_name=<name>, ...) 直接导入。"
            )
            return "\n".join(lines)
        except Exception as exc:
            return f"❌ 获取模板列表失败：{exc}"

    # ── 14. search_plc_templates ──────────────────────────────────────────────
    @mcp.tool(
        name="search_plc_templates",
        description=(
            "按关键词检索最相关的 PLC 程序模板（RAG 检索）。\n"
            "query 支持中文描述或功能关键词，如「烧嘴控制」「风机启停」「定时报警」等。\n"
            "返回最多 top_k 条匹配结果，含相关度得分（0~1，越高越相关）和所属 category。\n"
            "category 可选，限定只在某分类中检索，如 category=\"application\"。\n"
            "【工作流程】\n"
            "  1. 先调用此工具检索是否有现成模板\n"
            "  2. score >= 0.5 时，用 get_plc_template(name=<name>, full=True) 获取完整 XML\n"
            "  3. 在模板 XML 基础上修改生成目标块 → import_lad_xml 导入\n"
            "  4. 无合适模板时，从任意相关模板的 XML 学习语法后自行生成"
        ),
    )
    def search_plc_templates(query: str, top_k: int = 5, category: str = "") -> str:
        """检索最相关的 PLC 程序模板。"""
        try:
            cat_arg = category if category else None
            results = search_templates(query, top_k=top_k, category=cat_arg)
            if not results:
                return f"ℹ️ 未找到与「{query}」相关的模板，建议使用 import_lad_xml 基于参考模板自行生成 XML。"
            lines = [f"🔍 检索「{query}」，共找到 {len(results)} 个相关模板：\n"]
            for r in results:
                bar = "█" * int(r["score"] * 10) + "░" * (10 - int(r["score"] * 10))
                lines.append(
                    f"  [{r['block_type']:2s}] {r['name']:<20}  [{r['category']}]  相关度: {r['score']:.2f} {bar}"
                )
                lines.append(f"       {r['description']}")
            lines.append(
                "\n💡 对 score >= 0.5 的模板，可调用 get_plc_template(name=<name>, full=True) 获取完整 XML 作参考，"
                "再决定是修改导入还是参考语法自行生成。"
            )
            return "\n".join(lines)
        except Exception as exc:
            return f"❌ 检索模板失败：{exc}"

    # ── 15. get_plc_template ──────────────────────────────────────────────────
    @mcp.tool(
        name="get_plc_template",
        description=(
            "获取指定模板的 XML 内容，用于作为生成新块的参考或直接复用。\n"
            "template_name: 模板名（不含 .xml，如「烧嘴控制」）\n"
            "full: false=返回截断摘要（约4000字符，含接口声明+首个网络，节省上下文）；\n"
            "      true=返回完整 XML（【推荐】生成新块时必须传 full=True 以获取完整语法参考）。\n"
            "用途：\n"
            "  - 获取完整模板 XML 作为 AI 生成新块的参考蓝本\n"
            "  - 了解梯形图网络结构、变量访问格式、UId 编排方式\n"
            "  - 在此基础上修改变量名/网络内容生成目标块的 XML\n"
            "注意：若要直接导入模板（不修改）请使用 import_template_block。"
        ),
    )
    def get_plc_template(template_name: str, full: bool = False) -> str:
        """获取模板 XML 内容。"""
        try:
            xml = get_template_xml(template_name, full=full)
            if xml is None:
                all_names = [t["name"] for t in list_templates()]
                candidates = [n for n in all_names if template_name.lower() in n.lower()][:3]
                hint = ("  候选：" + "、".join(candidates)) if candidates else "  （无匹配，请调用 list_plc_templates 查看全部）"
                return f"❌ 未找到模板「{template_name}」。\n{hint}"
            mode_hint = "（完整内容）" if full else "（截断摘要，调用时传 full=true 获取完整内容）"
            return f"📄 模板「{template_name}」{mode_hint}：\n\n{xml}"
        except Exception as exc:
            return f"❌ 获取模板内容失败：{exc}"

    # ── 16. import_template_block ─────────────────────────────────────────────
    @mcp.tool(
        name="import_template_block",
        description=(
            "将 RAG 模板库中的程序块 XML 直接导入到指定 PLC 设备（最快路径）。\n"
            "适用场景：模板与需求高度匹配，无需修改，直接复用真实工程导出的块。\n"
            "template_name: 模板名（不含 .xml，如「烧嘴控制」「风机燃气」）\n"
            "device_name:   目标 PLC 设备名（已通过 add_plc_to_project 添加）\n"
            "【工作流程】\n"
            "  search_plc_templates → get_plc_template（确认接口）→ import_template_block\n"
            "注意：\n"
            "  - 模板 XML 中的块名称和编号保持原样（由博途自动处理 AutoNumber=true）\n"
            "  - 若需要修改变量名/逻辑，请使用 import_lad_xml 从 JSON 重新生成\n"
            "  - 导入后可调用 compile_check 确认无编译错误"
        ),
    )
    def import_template_block(template_name: str, device_name: str) -> str:
        """将模板 XML 直接导入到指定 PLC 设备。"""
        try:
            _check_session()
            info = TemplateLibrary.instance().get(template_name)
            if info is None:
                return f"❌ 未找到模板「{template_name}」，请先调用 list_plc_templates 确认模板名称。"
            xml_content = info.file_path.read_text(encoding="utf-8")
            temp_dir = _ensure_temp_dir()
            _session.run_plc_operation("import_template", device_name,
                                       lambda _project, plc_sw: import_lad_xml_block(plc_sw, temp_dir, info.block_name or template_name, xml_content))
            return (
                f"✅ 模板「{template_name}」（{info.block_type} {info.block_name}）已成功导入到 {device_name}。\n"
                f"💡 如需验证，调用 compile_check(device_name=\"{device_name}\") 检查编译结果。"
            )
        except Exception as exc:
            return f"❌ 导入模板失败：{exc}\n{traceback.format_exc()}"

    # 历史raw XML/文件导入、直接保存和模板整块导入不再属于LLM公开工具。
    for legacy_name in (
        "import_lad_xml", "import_lad_xml_from_file", "save_lad_xml",
        "compile_check", "compile_and_save",
        "list_plc_templates", "search_plc_templates", "get_plc_template", "import_template_block",
    ):
        mcp._tool_manager._tools.pop(legacy_name, None)

    # Artifact编辑与TIA运行闭环在移除历史入口后注册，确保公开名称唯一。
    from scdw.mcp.xml_artifact_tools import register_xml_artifact_tools
    from scdw.mcp.lad_runtime_tools import register_lad_runtime_tools
    register_xml_artifact_tools(mcp, _session)
    register_lad_runtime_tools(mcp, _session)
