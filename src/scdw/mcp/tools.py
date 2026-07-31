# -*- coding: utf-8 -*-
"""Register the public project, hardware, tag, DB, and knowledge MCP tools."""
import json
import functools
import os
import traceback
from pathlib import Path
from typing import List, Literal

from pydantic import BaseModel, Field
from scdw.common.paths import GENERATED_DIR, PROJECT_ROOT
from scdw.openness.session import TiaSessionManager
from scdw.rag.retriever import (
    TemplateLibrary,
    get_knowledge_items,
)


class GlobalDbVariableInput(BaseModel):
    name: str
    data_type: str
    initial_value: str | int | float | bool | None = None
    comment: str = ""
    address: str | None = None
    offset: str | None = None



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


def _normalise_public_result(tool_name: str, value) -> str:
    """Adapt every server-facing tool to the shared result envelope."""
    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
    required = {
        "success", "stage", "code", "message", "data", "retryable", "needs_user_action",
        "recommended_action", "fallback_arguments",
    }
    if isinstance(decoded, dict) and required <= set(decoded):
        return json.dumps(decoded, ensure_ascii=False, sort_keys=True)
    if isinstance(decoded, dict):
        success = bool(decoded.get("success", True))
        stage = str(decoded.get("stage") or tool_name)
        code = str(decoded.get("code") or ("OK" if success else "TOOL_FAILED"))
        message = str(decoded.get("message") or ("completed" if success else "tool failed"))
        data = {key: item for key, item in decoded.items() if key not in {
            "success", "stage", "code", "message", "retryable", "needs_user_action",
            "recommended_action", "fallback_arguments",
        }}
        payload = {
            "success": success, "stage": stage, "code": code, "message": message,
            "data": data, "retryable": bool(decoded.get("retryable", False)),
            "needs_user_action": bool(decoded.get("needs_user_action", False)),
            "recommended_action": str(decoded.get("recommended_action") or ("continue" if success else "inspect_failure")),
            "fallback_arguments": decoded.get("fallback_arguments") or {},
        }
        if tool_name in {"create_global_db", "create_plc_tag_table", "add_plc_to_project", "add_hardware_module"}:
            payload.update(data)
    else:
        text = str(value or "")
        success = not text.lstrip().startswith(("❌", "失败", "错误", "Error", "error"))
        payload = {
            "success": success, "stage": tool_name, "code": "OK" if success else "TOOL_FAILED",
            "message": text.splitlines()[0] if text else ("completed" if success else "tool failed"),
            "data": {"text": text}, "retryable": False, "needs_user_action": False,
            "recommended_action": "continue" if success else "inspect_failure", "fallback_arguments": {},
        }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _wrap_public_tool_results(mcp) -> None:
    for tool in mcp._tool_manager.list_tools():
        original = tool.fn
        if getattr(original, "_scdw_result_wrapped", False):
            continue

        @functools.wraps(original)
        def wrapped(*args, __original=original, __name=tool.name, **kwargs):
            return _normalise_public_result(__name, __original(*args, **kwargs))

        wrapped._scdw_result_wrapped = True
        tool.fn = wrapped


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
    """Register the supported MCP surface on ``mcp``."""
    from scdw.openness import (
        start_tia_portal,
        stop_tia_portal,
        create_project,
        add_plc_device,
        add_module_to_rack,
        create_tag_table_with_tags,
        TagSpec,
        create_global_db as _openness_create_global_db,
        import_scl_block,
        build_global_db_scl,
        DBVariable,
        delete_block,
    )
    from scdw.xlsx.reader import read_plc_project_xlsx
    from scdw.lad_generation import LadPlanService
    from scdw.mcp.lad_plan_tools import register_lad_plan_tools
    from scdw.xml_workspace import XmlArtifactService
    _lad_plans = LadPlanService()
    _xml_artifacts = XmlArtifactService()
    register_lad_plan_tools(mcp, _lad_plans, _xml_artifacts)

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

            project_dir = os.path.join(project_root, project_name)
            return (
                "✅ TIA 项目已创建\n"
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
        description="Insert one I/O or communication module into an existing PLC rack slot. Requires a module type ID; optional rack_item_path selects a nested rack.",
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

            result = _session.run_plc_operation("create_tag_table", device_name,
                                                lambda _project, plc_sw: create_tag_table_with_tags(plc_sw, table_name, tag_specs))

            return json.dumps({
                "success": True,
                "stage": "create_plc_tag_table",
                "code": "ALREADY_EXISTS" if result.get("idempotent") else "TAG_TABLE_CREATED",
                "message": "所有变量已存在，未重复创建" if result.get("idempotent") else "变量表已创建",
                "device_name": device_name,
                **result,
            }, ensure_ascii=False, sort_keys=True)
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
            symbolic_fallback = address_mode == "absolute"

            _check_session()

            from scdw.openness.tia_blocks import normalise_tia_member_name
            used_names: set[str] = set()
            actual_names: dict[str, str] = {}
            for item in vars_data:
                requested = str(item.get("name", ""))
                actual = normalise_tia_member_name(requested, used_names)
                used_names.add(actual)
                actual_names[requested] = actual
            db_vars = [
                DBVariable(
                    name=actual_names[str(v["name"])],
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
                "requested_name": next((requested for requested, actual in actual_names.items() if actual == item.name), item.name),
                "tia_actual_name": item.name,
                "requested_address": next(
                    (mapping["requested_address"] for mapping in requested_mappings if mapping["requested_name"] == item.name),
                    None,
                ),
                "tia_actual_address": None,
            } for item in db_vars]
            return json.dumps({
                "success": True, "stage": "create_global_db",
                "code": "SYMBOLIC_DB_FALLBACK" if symbolic_fallback else "GLOBAL_DB_CREATED",
                "message": (
                    "Absolute DB layout is not supported; a symbolic optimized DB was created automatically without changing control semantics."
                    if symbolic_fallback else "Global DB created"
                ),
                "device_name": device_name, "db_name": db_name, "db_number": db_number,
                "optimized_access": True, "variable_mappings": mappings,
                "original_address_mode": address_mode,
                "effective_address_mode": "symbolic",
                "address_layout_preserved": not symbolic_fallback,
                "fallback_applied": symbolic_fallback,
                "recommended_action": "continue_generation",
                "fallback_arguments": {},
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

    # ── Knowledge snapshot source ─────────────────────────────────────────────
    @mcp.tool(
        name="get_plc_knowledge_catalog",
        description=(
            "兼容性知识metadata目录；LAD规划应先调用get_lad_capability_catalog。该工具不返回XML正文。"
        ),
    )
    def get_plc_knowledge_catalog() -> str:
        return json.dumps(TemplateLibrary.instance().compact_catalog(), ensure_ascii=False)

    @mcp.tool(
        name="get_plc_knowledge_items",
        description=(
            "仅在蓝图approved_for_generation后，按当前Plan已选择的ID批量返回XML片段或规则正文；任务级正文由知识库缓存，raw/application不暴露。"
        ),
    )
    def get_plc_knowledge_items(plan_id: str, item_ids: List[str]) -> str:
        try:
            plan = _lad_plans.get(plan_id)
            _lad_plans._require_frozen_blueprint(plan)
            selected = {item for network in plan.networks for item in network.selected_knowledge_ids}
            unexpected = sorted(set(item_ids) - selected)
            if unexpected:
                raise ValueError("knowledge IDs are not selected by the frozen blueprint: " + ", ".join(unexpected))
            return json.dumps({
                "success": True, "plan_id": plan_id, "blueprint_sha256": plan.blueprint_sha256,
                "items": get_knowledge_items(item_ids), "cache_scope": "task_process",
            }, ensure_ascii=False)
        except (KeyError, ValueError) as exc:
            return json.dumps({
                "success": False, "code": getattr(exc, "code", "KNOWLEDGE_GAP"),
                "message": str(exc), "needs_user_action": False,
            }, ensure_ascii=False)
    # Artifact editing and the TIA closed loop are registered exactly once.
    from scdw.mcp.xml_artifact_tools import register_xml_artifact_tools
    from scdw.mcp.lad_runtime_tools import register_lad_runtime_tools
    register_xml_artifact_tools(mcp, _session, _xml_artifacts, _lad_plans)
    register_lad_runtime_tools(mcp, _session, _xml_artifacts, _lad_plans)
    _wrap_public_tool_results(mcp)
