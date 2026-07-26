"""已运行 TIA Portal、工程和 PLC Software 的发现逻辑。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .tia_core import load_tia_api, net_to_python
from scdw.common.run_logging import get_run_logger


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        try:
            value = getattr(obj, name)
            if value is not None:
                return net_to_python(value)
        except Exception:
            continue
    return default


def _normal_path(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return str(Path(str(value)).resolve())
    except Exception:
        return str(value)


def list_running_tia_processes() -> list[dict[str, Any]]:
    """每次重新调用 GetProcesses，返回可 JSON 序列化的进程快照。"""
    load_tia_api()
    from Siemens.Engineering import TiaPortal  # type: ignore

    result: list[dict[str, Any]] = []
    for process in TiaPortal.GetProcesses():
        # 单个属性读取失败不能影响其他进程。
        result.append({
            "process_id": _value(process, "Id", "ProcessId"),
            "mode": _value(process, "Mode", default="未知"),
            "executable_path": _value(process, "Path", "ExecutablePath"),
            "project_path": _normal_path(_value(process, "ProjectPath", "ProjectFilePath")),
            "acquisition_time": datetime.now(timezone.utc).isoformat(),
            "attached_session_count": _value(process, "AttachedSessionsCount", "AttachedSessionCount", default=0),
        })
    get_run_logger().log_event("tia_processes_discovered", component="tia", processes=result)
    return result


def attach_tia_process(process_id: int) -> Any:
    """按 PID 重新定位最新快照并附着到 TIA；绝不复用旧进程对象。"""
    load_tia_api()
    from Siemens.Engineering import TiaPortal  # type: ignore

    for process in TiaPortal.GetProcesses():
        current_id = _value(process, "Id", "ProcessId")
        if current_id is not None and int(current_id) == int(process_id):
            return process.Attach()
    raise RuntimeError(f"未找到进程 ID 为 {process_id} 的 TIA Portal；它可能已关闭。")


def list_open_projects(tia: Any) -> list[dict[str, Any]]:
    """枚举已附着 TIA 中的工程，且不把 .NET 对象泄漏到调用方。"""
    projects: list[dict[str, Any]] = []
    for project in tia.Projects:
        projects.append({
            "name": _value(project, "Name", default=""),
            "path": _normal_path(_value(project, "Path", "ProjectPath")),
            "is_primary": bool(_value(project, "IsPrimary", default=False)),
        })
    return projects


def discover_plc_devices(project: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """递归扫描根设备及设备组，返回 PLC 摘要与仅供会话内部使用的注册表。"""
    from Siemens.Engineering.HW.Features import SoftwareContainer  # type: ignore

    summaries: list[dict[str, Any]] = []
    registry: dict[str, dict[str, Any]] = {}

    def children(node: Any) -> list[Any]:
        try:
            return list(node.Devices)
        except Exception:
            return []

    def device_items(node: Any) -> list[Any]:
        try:
            return list(node.DeviceItems)
        except Exception:
            return []

    def visit_device(device: Any, prefix: str) -> None:
        name = str(_value(device, "Name", default="未命名设备"))
        device_path = f"{prefix}/{name}" if prefix else name
        stack = list(device_items(device))
        while stack:
            item = stack.pop()
            try:
                container = item.GetService[SoftwareContainer]()
                software = container.Software if container is not None else None
                if software is not None and "plcsoftware" in str(type(software)).lower():
                    key = device_path
                    summaries.append({"device_name": name, "device_path": key,
                                      "software_name": _value(software, "Name", default=name),
                                      "software_type": "PlcSoftware"})
                    registry[key] = {"device": device, "plc_software": software, "device_name": name}
            except Exception:
                pass
            stack.extend(device_items(item))

    def visit_group(group: Any, prefix: str = "") -> None:
        group_name = str(_value(group, "Name", default=""))
        group_path = f"{prefix}/{group_name}" if group_name else prefix
        for device in children(group):
            visit_device(device, group_path)
        try:
            groups = list(group.DeviceGroups)
        except Exception:
            groups = []
        for child in groups:
            visit_group(child, group_path)

    for root_device in list(project.Devices):
        visit_device(root_device, "")
    try:
        for root_group in project.DeviceGroups:
            visit_group(root_group)
    except Exception:
        pass
    return summaries, registry
