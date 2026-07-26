# -*- coding: utf-8 -*-
"""
tia_hardware.py
设备与硬件模块管理：添加 PLC 顶层设备、在机架中插入 I/O 模块、枚举设备项。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

from .tia_core import net_to_python

PathSegment = Union[str, int]


# ── DeviceItem 辅助工具 ───────────────────────────────────────────────────────
def _list_items(parent) -> List[Any]:
    """将 .NET DeviceItems 集合转为 Python 列表，出错返回空列表。"""
    try:
        return list(parent.DeviceItems)
    except Exception:
        return []


def _safe_attr(obj, name: str, default: Any = None) -> Any:
    """安全读取对象属性，出错返回 default。"""
    try:
        v = getattr(obj, name)
        return v if v is not None else default
    except Exception:
        return default


def describe_item(item) -> Dict[str, Any]:
    """提取设备/模块的常用信息字典（过滤 None 和空字符串）。"""
    return {
        k: v
        for k, v in {
            "name": net_to_python(_safe_attr(item, "Name")),
            "type_identifier": net_to_python(_safe_attr(item, "TypeIdentifier")),
            "type_name": net_to_python(_safe_attr(item, "TypeName")),
            "position_number": net_to_python(_safe_attr(item, "PositionNumber")),
            "classification": net_to_python(_safe_attr(item, "Classification")),
        }.items()
        if v not in (None, "")
    }


def _normalize_path(path) -> List[PathSegment]:
    """将设备项路径规范化为 List[str | int]。"""
    if path is None:
        return []
    if isinstance(path, (str, int)):
        raw = [path]
    else:
        raw = list(path)
    result: List[PathSegment] = []
    for seg in raw:
        if isinstance(seg, str):
            s = seg.strip()
            result.append(int(s) if s.isdigit() else s)
        else:
            result.append(seg)
    return result


# ── 设备查找 / 定位 ───────────────────────────────────────────────────────────
def _find_rack(device) -> Any:
    """
    在设备的直接子 DeviceItem 中查找 Rack 容器（TypeIdentifier 含 'Rack'）。
    找不到时回退返回第一个子项；子项为空时返回 device 本身。
    """
    children = _list_items(device)
    for child in children:
        ti = net_to_python(_safe_attr(child, "TypeIdentifier", ""))
        if ti and "rack" in ti.lower():
            return child
    # 回退：没有明确 Rack 标识时用第一个子项
    if children:
        return children[0]
    return device


def find_device(project, device_name: str):
    """按名称在项目顶层 Devices 中查找设备，未找到则抛出 RuntimeError。"""
    try:
        for dev in project.Devices:
            if str(dev.Name) == device_name:
                return dev
    except Exception:
        pass
    raise RuntimeError(f"未找到设备：{device_name}")


def resolve_device_item(project, device_name: str, item_path=None):
    """
    从 device_name 出发，沿 item_path 逐级定位 DeviceItem。
    item_path 为 None 时返回顶层 Device 本身。
    item_path 支持索引列表（如 [0, 1]）或名称列表（如 ["PLC_1", "CPU"]）。
    """
    current = find_device(project, device_name)
    for seg in _normalize_path(item_path):
        children = _list_items(current)
        if not children:
            raise RuntimeError(
                f"对象 {describe_item(current)} 下没有子 DeviceItem，无法解析路径段 {seg!r}"
            )
        if isinstance(seg, int):
            if not (0 <= seg < len(children)):
                raise IndexError(
                    f"路径索引越界：{seg}，当前层共 {len(children)} 个子项"
                )
            current = children[seg]
        else:
            match = next(
                (
                    c
                    for c in children
                    if net_to_python(_safe_attr(c, "Name", "")).strip().lower()
                    == seg.strip().lower()
                ),
                None,
            )
            if match is None:
                avail = [net_to_python(_safe_attr(c, "Name")) for c in children]
                raise RuntimeError(
                    f"在 {describe_item(current)} 下未找到名称 {seg!r}，可用子项：{avail}"
                )
            current = match
    return current


# ── PLC Software ──────────────────────────────────────────────────────────────
def get_plc_software(device):
    """
    深度搜索设备的 DeviceItems，找到 SoftwareContainer 并返回其 Software 对象。
    用于 PLC 程序块、变量表等所有软件操作的入口。
    """
    from Siemens.Engineering.HW.Features import SoftwareContainer  # type: ignore

    stack = list(_list_items(device))
    while stack:
        item = stack.pop()
        try:
            sw_container = item.GetService[SoftwareContainer]()
            if sw_container is not None and sw_container.Software is not None:
                return sw_container.Software
        except Exception:
            pass
        stack.extend(_list_items(item))
    raise RuntimeError("未找到 PLC Software，请确认该设备为可编程 PLC。")


# ── 设备 / 模块 添加 ──────────────────────────────────────────────────────────
def add_plc_device(
    project,
    order_number: str,
    device_name: str,
    device_item_name: Optional[str] = None,
):
    """
    在项目根节点下新建 PLC 顶层设备。

    Args:
        project: TIA Project 对象
        order_number: 设备订货号，格式如 "OrderNumber:6ES7 214-1BG40-0XB0/V4.4"
        device_name: 项目树中显示的设备名称，如 "PLC_1"
        device_item_name: DeviceItem 名称，默认与 device_name 相同

    Returns:
        (device, plc_software) 元组
    """
    item_name = device_item_name or device_name
    device = project.Devices.CreateWithItem(order_number, device_name, item_name)
    plc_software = get_plc_software(device)
    return device, plc_software


# 已知模块版本候选表：订货号前缀 → 常见版本列表（按优先级排序）
_KNOWN_VERSIONS: Dict[str, List[str]] = {
    "6ES7 241-1CH32-0XB0": ["V2.1", "V2.2", "V2.0"],
    "6ES7 223-1PL30-0XB0": ["V1.0", "V2.0"],
    "6ES7 222-1BF32-0XB0": ["V1.0", "V2.0"],
    "6ES7 221-1BF32-0XB0": ["V1.0", "V2.0"],
    "6ES7 231-4HF32-0XB0": ["V2.0", "V2.1"],
    "6ES7 234-4HE32-0XB0": ["V2.0", "V2.1"],
}


def _probe_order_number(container, order_number: str, slot_number: int, module_name: str) -> str:
    """
    若订货号不含版本号，自动尝试已知版本候选，返回第一个 CanPlugNew=True 的完整订货号。
    所有候选均失败时返回原始订货号（由 PlugNew 最终报错）。
    """
    # 已含版本号则直接返回
    if "/V" in order_number:
        return order_number

    # 提取裸订货号部分（去掉 OrderNumber: 前缀）
    bare = order_number.replace("OrderNumber:", "").strip()
    versions = _KNOWN_VERSIONS.get(bare, [])

    for ver in versions:
        candidate = f"OrderNumber:{bare}/{ver}"
        try:
            result = container.CanPlugNew(candidate, module_name, slot_number)
            if result:
                return candidate
        except Exception:
            continue

    return order_number


def add_module_to_rack(
    project,
    device_name: str,
    module_type_id: str,
    slot_number: int,
    module_name: str,
    rack_item_path=None,
) -> Dict[str, Any]:
    """
    在已有设备的机架/容器中插入硬件模块（调用 PlugNew）。

    Args:
        project: TIA Project 对象
        device_name: 顶层设备名称
        module_type_id: 模块订货号 / TypeIdentifier，可不带版本号（会自动探测）
        slot_number: 目标槽位编号。
            S7-1200 右侧扩展 SM：2~9；左侧通信 CM：101/102/103
        module_name: 模块的显示名称
        rack_item_path: 容器路径（索引或名称列表）。
            默认为 None，此时自动定位设备下的 Rack 容器（推荐）。
            显式传入 [] 可强制使用顶层 Device。

    Returns:
        新创建模块的描述字典
    """
    if rack_item_path is None:
        container = _find_rack(find_device(project, device_name))
    else:
        container = resolve_device_item(project, device_name, rack_item_path)

    # 自动补全版本号
    resolved_type_id = _probe_order_number(container, module_type_id, slot_number, module_name)

    try:
        can_plug = container.CanPlugNew(resolved_type_id, module_name, slot_number)
        if can_plug is False:
            raise RuntimeError(
                f"容器 {describe_item(container)} 不允许在槽位 {slot_number} 插入 {resolved_type_id}"
            )
    except AttributeError:
        pass
    created = container.PlugNew(resolved_type_id, module_name, slot_number)
    return describe_item(created)


def list_device_items_flat(project, device_name: str) -> List[Dict[str, Any]]:
    """
    递归枚举设备的所有 DeviceItem，返回扁平列表，每项包含路径信息。
    适用于调试、查看硬件树结构。
    """
    device = find_device(project, device_name)
    result: List[Dict[str, Any]] = []

    def walk(parent, prefix: List[int]) -> None:
        for idx, item in enumerate(_list_items(parent)):
            path = prefix + [idx]
            info = describe_item(item)
            info["path"] = path
            result.append(info)
            walk(item, path)

    walk(device, [])
    return result
