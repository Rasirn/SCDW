# -*- coding: utf-8 -*-
"""
core/tools.py

分两部分：
  1. ToolManager  ── 客户端侧辅助类，负责从 MCP 客户端获取工具列表并执行工具调用。
  2. TIA 工具注册 ── register_mcp_tools(mcp) 向 FastMCP 服务器注册所有 TIA Portal 工具。
"""
import json
import os
import shutil
import traceback
from typing import Dict, List, Optional

from mcp_client import MCPClient
from mcp.types import CallToolResult, Tool, TextContent
import openai.types.chat.chat_completion as Message

from pydantic import Field

class ToolManager:
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
_session: Dict = {
    "tia": None,           # TiaPortal 实例
    "project": None,       # TIA Project 对象
    "devices": {},         # {device_name: {"device": ..., "plc_software": ...}}
    "temp_dir": None,      # 临时文件目录
}


def _check_session() -> None:
    """检查 TIA 会话是否已初始化，否则抛出 RuntimeError。"""
    if _session["project"] is None:
        raise RuntimeError(
            "TIA 会话尚未初始化，请先调用 init_tia_project 工具创建或打开项目。"
        )


def _get_plc_software(device_name: str):
    """从会话中获取指定设备的 plc_software 对象。"""
    entry = _session["devices"].get(device_name)
    if entry is None:
        raise RuntimeError(
            f"设备 '{device_name}' 不在当前会话中，请先调用 add_plc_to_project 添加该设备。"
        )
    return entry["plc_software"]


def _ensure_temp_dir() -> str:
    """获取或创建临时目录。"""
    if not _session["temp_dir"] or not os.path.isdir(_session["temp_dir"]):
        import tempfile
        _session["temp_dir"] = tempfile.mkdtemp(prefix="tia_mcp_")
    return _session["temp_dir"]


def _cleanup_session() -> None:
    """释放 TIA 资源并清理会话状态。"""
    try:
        if _session["project"] is not None:
            _session["project"].Save()
    except Exception:
        pass
    try:
        if _session["tia"] is not None:
            _session["tia"].Dispose()
    except Exception:
        pass
    try:
        if _session["temp_dir"] and os.path.isdir(_session["temp_dir"]):
            shutil.rmtree(_session["temp_dir"], ignore_errors=True)
    except Exception:
        pass
    _session["tia"] = None
    _session["project"] = None
    _session["devices"] = {}
    _session["temp_dir"] = None


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

    编译下载：
      compile_and_save       编译并保存当前项目

    自动化工具：
      create_project_from_xlsx  从 xlsx 文件一键创建完整 TIA 项目
    """
    from openness import (
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
        build_lad_xml,
        lad_networks_from_json,
        compile_plc,
    )
    from data.xlsx_reader import read_plc_project_xlsx

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
        project_root: str,
        api_dir: str = r"E:\PlcProject\SoftWares\Siemens\Automation\Portal V17\PublicAPI\V17",
        with_ui: bool = True,
        overwrite: bool = False,
    ) -> str:
        """创建新 TIA 项目，初始化工具会话。"""
        _cleanup_session()
        try:
            from openness.tia_core import load_tia_api, set_default_api_dir
            set_default_api_dir(api_dir)
            load_tia_api(api_dir)

            tia = start_tia_portal(with_ui=with_ui)
            project = create_project(tia, project_root, project_name, overwrite=overwrite)

            _session["tia"] = tia
            _session["project"] = project

            project_dir = os.path.join(project_root, project_name)
            return (
                f"✅ TIA 项目已创建\n"
                f"  项目名称：{project_name}\n"
                f"  项目路径：{project_dir}\n"
                f"  TIA UI：{'有界面' if with_ui else '无界面'}\n"
                f"会话已就绪，可继续添加设备和软件配置。"
            )
        except Exception as exc:
            _cleanup_session()
            return f"❌ 创建项目失败：{exc}\n{traceback.format_exc()}"

    # ── 2. close_tia_session ──────────────────────────────────────────────────
    @mcp.tool(
        name="close_tia_session",
        description="保存当前 TIA 项目并关闭 TIA Portal，释放会话资源。",
    )
    def close_tia_session() -> str:
        """保存并关闭 TIA 会话。"""
        if _session["project"] is None:
            return "ℹ️ 当前没有活动的 TIA 会话。"
        try:
            save_project(_session["project"])
            _cleanup_session()
            return "✅ TIA 项目已保存并关闭，会话已释放。"
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
            _check_session()
            item_name = device_item_name or device_name
            device, plc_software = add_plc_device(
                _session["project"], order_number, device_name, item_name
            )
            _session["devices"][device_name] = {
                "device": device,
                "plc_software": plc_software,
            }
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
            result = add_module_to_rack(
                _session["project"],
                device_name,
                module_type_id,
                slot_number,
                name,
                rack_item_path=item_path,
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

            plc_sw = _get_plc_software(device_name)
            create_tag_table_with_tags(plc_sw, table_name, tag_specs)

            return (
                f"✅ 变量表 '{table_name}' 已创建，成功添加 {len(tag_specs)} 个变量。"
            )
        except Exception as exc:
            return f"❌ 创建变量表失败：{exc}"

    # ── 6. create_global_db ───────────────────────────────────────────────────
    @mcp.tool(
        name="create_global_db",
        description=(
            "在指定 PLC 设备下创建全局数据块（Global DB，非优化访问，偏移量可见）。\n"
            "variables_json 为 JSON 数组，每项格式：\n"
            '  {"name":"变量名","data_type":"Bool","offset":"0.0","initial_value":"","comment":"注释"}\n'
            "offset 为 xlsx 中的字节偏移量（如 0.0、2.0），仅写入 SCL 注释，不影响编译器分配地址。\n"
            "data_type 支持 S7 所有基础类型及复合类型，如 IEC_TIMER、Array[0..15] of Bool 等。\n"
            "复合类型（IEC_TIMER 等）会自动加双引号，且不允许设置初始值。"
        ),
    )
    def create_global_db(
        device_name: str,
        db_name: str,
        db_number: int,
        variables_json,
    ) -> str:
        """创建全局 DB 并写入变量定义。"""
        try:
            _check_session()
            vars_data: list = variables_json if isinstance(variables_json, list) else json.loads(variables_json)
            if not isinstance(vars_data, list):
                return "❌ variables_json 必须是 JSON 数组。"

            db_vars = [
                DBVariable(
                    name=v["name"],
                    data_type=v.get("data_type", "Bool"),
                    initial_value=str(v.get("initial_value", "")),
                    comment=v.get("comment", ""),
                    offset=str(v.get("offset", "")),
                )
                for v in vars_data
                if v.get("name") and v.get("data_type")
            ]

            plc_sw = _get_plc_software(device_name)
            temp_dir = _ensure_temp_dir()
            _openness_create_global_db(plc_sw, temp_dir, db_name, db_number, db_vars)

            return (
                f"✅ 全局 DB '{db_name}' (DB{db_number}) 已创建，"
                f"包含 {len(db_vars)} 个变量。"
            )
        except Exception as exc:
            return f"❌ 创建全局 DB 失败：{exc}"

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
            plc_sw = _get_plc_software(device_name)
            temp_dir = _ensure_temp_dir()
            from openness.tia_blocks import import_scl_block as _import_scl
            _import_scl(plc_sw, temp_dir, block_name, scl_code)
            return f"✅ SCL 程序块 '{block_name}' 已成功导入到 {device_name}。"
        except Exception as exc:
            return f"❌ 导入 SCL 块失败：{exc}"

    # ── 8. add_lad_block ─────────────────────────────────────────────────────
    @mcp.tool(
        name="add_lad_block",
        description=(
            "向指定 PLC 设备导入梯形图（LAD）程序块。\n"
            "本工具采用「JSON 逻辑描述 → Python 自动生成 XML」方案，\n"
            "你只需描述梯级逻辑，无需手写 XML。\n\n"
            "【block_type】：FC | FB | OB\n"
            "【block_number】：块编号（整数）\n"
            "【networks_json】：JSON 数组，每项描述一个梯级（网络），格式如下：\n\n"
            "串联（AND）格式：\n"
            "  {\n"
            "    \"title\": \"电机启动\",\n"
            "    \"contacts\": [\n"
            "      {\"var\": \"DB1.Start\", \"nc\": false},\n"
            "      {\"var\": \"DB1.Stop\",  \"nc\": true}\n"
            "    ],\n"
            "    \"outputs\": [{\"var\": \"DB1.Motor\", \"type\": \"Coil\"}]\n"
            "  }\n\n"
            "并联（OR）格式：\n"
            "  {\n"
            "    \"title\": \"多条件启动\",\n"
            "    \"branches\": [\n"
            "      [{\"var\": \"DB1.Remote\", \"nc\": false}],\n"
            "      [{\"var\": \"DB1.Local\", \"nc\": false}]\n"
            "    ],\n"
            "    \"outputs\": [{\"var\": \"DB1.Motor\", \"type\": \"SCoil\"}]\n"
            "  }\n\n"
            "【字段说明】\n"
            "  var：变量路径。全局标签直接写名称，DB 成员用点分隔（如 DB_Motor.Run）\n"
            "  nc：触点类型，false=常开(NO)，true=常闭(NC)\n"
            "  type：线圈类型，Coil=普通输出，SCoil=SET置位，RCoil=RESET复位\n\n"
            "【OR 逻辑替代方案】\n"
            "  若逻辑复杂，可拆成多个梯级使用 SCoil/RCoil 实现，避免 Or 节点：\n"
            "  梯级1: 条件A → SCoil(Flag)\n"
            "  梯级2: 条件B → SCoil(Flag)\n"
            "  梯级3: 停止条件 → RCoil(Flag)"
        ),
    )
    def add_lad_block(
        device_name: str,
        block_name: str,
        block_type: str,
        block_number: int,
        networks_json,
    ) -> str:
        """向 PLC 导入 LAD 程序块。"""
        try:
            _check_session()
            nets_data: list = (
                networks_json if isinstance(networks_json, list)
                else json.loads(networks_json)
            )
            if not isinstance(nets_data, list):
                return "❌ networks_json 必须是 JSON 数组。"

            bt = block_type.upper()
            if bt not in ("FC", "FB", "OB"):
                return "❌ block_type 必须是 FC、FB 或 OB。"

            networks = lad_networks_from_json(nets_data)
            xml_content = build_lad_xml(block_name, bt, block_number, networks)

            plc_sw = _get_plc_software(device_name)
            temp_dir = _ensure_temp_dir()
            import_lad_xml_block(plc_sw, temp_dir, block_name, xml_content)

            return (
                f"✅ LAD 程序块 '{block_name}' ({bt}{block_number}) 已导入到 {device_name}，"
                f"共 {len(networks)} 个网络。"
            )
        except Exception as exc:
            return f"❌ 导入 LAD 块失败：{exc}\n{traceback.format_exc()}"

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
            plc_sw = _get_plc_software(device_name)
            result = compile_plc(plc_sw)
            save_project(_session["project"])

            status = "✅ 编译成功" if result.success else "⚠️ 编译有错误"
            lines = [f"{status}，项目已保存。", result.summary()]
            return "\n".join(lines)
        except Exception as exc:
            return f"❌ 编译/保存失败：{exc}"

    # ── 9. read_project_spec_from_xlsx ────────────────────────────────────────
    @mcp.tool(
        name="read_project_spec_from_xlsx",
        description=(
            "解析 xlsx 配置文件，读取 PLC 项目规格并以结构化文本返回，供后续逐步调用其他工具创建项目使用。\n"
            "返回内容包括：\n"
            "  - 硬件设备清单（CPU 订货号、I/O 模块等）\n"
            "  - I/O 点表（按模块组分组，含变量名/数据类型/地址/注释）\n"
            "  - DB 块列表（含变量定义，不含逻辑描述）\n"
            "  - 【LAD 功能逻辑清单】每条功能的逻辑描述文本，这是生成 LAD 程序块的依据\n\n"
            "读取结果后，按以下顺序调用工具完成项目创建：\n"
            "  1. init_tia_project       — 创建 TIA 项目\n"
            "  2. add_plc_to_project     — 添加 CPU（使用此工具返回的订货号）\n"
            "  3. create_plc_tag_table   — 按模块组创建变量表\n"
            "  4. create_global_db       — 创建各 DB 块\n"
            "  5. add_lad_block          — 【重要】根据 LAD 功能逻辑清单为每个功能生成 LAD 程序块\n"
            "  6. compile_and_save       — 编译并保存\n\n"
            "注意：LAD 功能逻辑清单中的每条描述都需要生成对应的 LAD 块（FC），\n"
            "描述中包含的条件/动作信息应转化为 contacts/outputs 梯级逻辑。"
        ),
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

        # ── LAD 功能逻辑清单（核心：每条描述对应一个需要生成的 LAD 程序块）────
        lines.append(
            f"\n=== LAD 功能逻辑清单（共 {len(spec.logic_functions)} 个功能，每个功能需调用 add_lad_block 生成 LAD 块）==="
        )
        if not spec.logic_functions:
            lines.append("  （xlsx 中未找到功能描述，无需生成 LAD 块）")
        for fn in spec.logic_functions:
            lines.append(f"\n  [FC{fn.block_index}] 功能名称：{fn.function_name}")
            lines.append(f"  关联 DB 块：{fn.db_block_name}")
            lines.append(f"  >>> 逻辑描述（根据此描述生成 LAD 梯级）：")
            lines.append(f"  {fn.description}")
            lines.append(
                f"  >>> 调用方式：add_lad_block(block_name=\"FC_{fn.block_index}\", "
                f"block_type=\"FC\", block_number={fn.block_index}, "
                f"networks_json=<根据上述描述生成的梯级 JSON>)"
            )

        lines.append(
            f"\n=== 汇总 ===\n"
            f"  硬件：{len(spec.hardware)} 项\n"
            f"  I/O 变量：{len(spec.io_tags)} 个（{len(groups)} 个变量表）\n"
            f"  DB 块：{len(spec.db_blocks)} 个\n"
            f"  LAD 逻辑功能：{len(spec.logic_functions)} 个（需调用 add_lad_block 生成）"
        )

        return "\n".join(lines)
