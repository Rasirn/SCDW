# -*- coding: utf-8 -*-
"""
tia_project_builder.py

面向 TIA Portal Openness V17 的一个小型封装：
1. 创建新项目
2. 新增 PLC 设备
3. 导入/生成 SCL 代码块
4. 导入 LAD 代码块（注意：这里的 LAD 内容必须是 SimaticML/XML，而不是“纯文本梯形图”）

为什么 LAD 这里要求 XML：
- 官方文档里，外部源文件直接生成块的典型场景主要是 SCL/STL。
- 对于块级导入/导出，Openness 支持 XML（SimaticML）方式。
- LAD 代码块通常应先在 TIA Portal 中导出为 XML 模板，再做变量替换后重新导入。
"""

from __future__ import annotations

import os
import sys
import time
import shutil
import tempfile
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union


@dataclass
class BlockSpec:
    """
    通用块定义

    对于 language == "SCL"：
        name: 建议传块名，例如 "OB1" / "FB10"
        content: SCL 源码文本
        file_name: 可选，默认自动按 name + ".scl"

    对于 language == "LAD"：
        content: SimaticML/XML 文本
        name/file_name: 仅用于生成临时文件名，不参与块名定义
    """
    language: str
    content: str
    name: Optional[str] = None
    file_name: Optional[str] = None


@dataclass
class ProjectBuildConfig:
    public_api_dir: str
    project_root: str
    project_name: str
    cpu_order_number: str
    device_name: str = "PLC_1"
    device_item_name: str = "PLC_1"
    with_user_interface: bool = True
    overwrite_existing_project_dir: bool = False
    compile_after_import: bool = True
    auto_save: bool = True
    cleanup_temp_dir: bool = True


@dataclass
class BuildResult:
    project_path: str
    temp_dir: str
    imported_scl_files: List[str] = field(default_factory=list)
    imported_lad_xml_files: List[str] = field(default_factory=list)
    compile_state: Optional[str] = None
    compile_messages: List[str] = field(default_factory=list)


PathSegment = Union[str, int]


def add_publicapi_reference(public_api_dir: str):
    """加载 Siemens.Engineering 相关程序集"""
    if public_api_dir not in sys.path:
        sys.path.append(public_api_dir)

    import clr  # type: ignore
    clr.AddReference("Siemens.Engineering")
    for asm_name in ("Siemens.Engineering.HW", "Siemens.Engineering.SW"):
        try:
            clr.AddReference(asm_name)
        except Exception:
            pass


def ensure_dir(path: str):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def write_text_file(folder: str, file_name: str, content: str, encoding: str = "utf-8") -> str:
    ensure_dir(folder)
    file_path = os.path.join(folder, file_name)
    with open(file_path, "w", encoding=encoding) as f:
        f.write(content)
    return file_path


def get_plc_software(device):
    """递归遍历 DeviceItems，找到 PLC SoftwareContainer -> Software"""
    from Siemens.Engineering.HW.Features import SoftwareContainer  # type: ignore

    stack = []
    for item in device.DeviceItems:
        stack.append(item)

    while stack:
        item = stack.pop()

        try:
            sw_container = item.GetService[SoftwareContainer]()
            if sw_container is not None and sw_container.Software is not None:
                return sw_container.Software
        except Exception:
            pass

        try:
            for sub_item in item.DeviceItems:
                stack.append(sub_item)
        except Exception:
            pass

    raise RuntimeError("未找到 PLC Software，请确认设备是否为可编程 PLC。")


def find_external_source_by_name(plc_software, name: str):
    """查找同名外部源码"""
    try:
        for src in plc_software.ExternalSourceGroup.ExternalSources:
            try:
                if str(src.Name) == name:
                    return src
            except Exception:
                pass
    except Exception:
        pass
    return None


def delete_if_exists(obj) -> bool:
    try:
        if obj is not None:
            obj.Delete()
            return True
    except Exception:
        pass
    return False


def safe_filename(name: str, default_suffix: str) -> str:
    text = (name or "block").strip()
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        text = text.replace(ch, "_")
    if not text.lower().endswith(default_suffix.lower()):
        text += default_suffix
    return text


def remove_project_dir_if_needed(project_dir: str, overwrite: bool):
    if not os.path.exists(project_dir):
        return

    if not overwrite:
        raise RuntimeError(
            f"工程目录已存在：{project_dir}\n"
            f"如需覆盖，请将 overwrite_existing_project_dir=True。"
        )

    shutil.rmtree(project_dir, ignore_errors=False)


def create_tia_portal(with_user_interface: bool = True):
    from Siemens.Engineering import TiaPortal, TiaPortalMode  # type: ignore
    mode = TiaPortalMode.WithUserInterface if with_user_interface else TiaPortalMode.WithoutUserInterface
    return TiaPortal(mode)


def create_project_and_device(config: ProjectBuildConfig):
    from System.IO import DirectoryInfo  # type: ignore

    ensure_dir(config.project_root)
    project_dir = os.path.join(config.project_root, config.project_name)
    remove_project_dir_if_needed(project_dir, config.overwrite_existing_project_dir)

    tia = create_tia_portal(config.with_user_interface)
    project = tia.Projects.Create(DirectoryInfo(config.project_root), config.project_name)
    device = project.Devices.CreateWithItem(
        config.cpu_order_number,
        config.device_name,
        config.device_item_name
    )
    plc_software = get_plc_software(device)
    return tia, project, device, plc_software, project_dir


def _list_device_items(parent) -> List[Any]:
    """将 .NET 的 DeviceItems 集合转成 Python 列表。"""
    try:
        return [item for item in parent.DeviceItems]
    except Exception:
        return []


def _normalize_path_segments(
    device_item_path: Optional[Sequence[PathSegment] | PathSegment],
) -> List[PathSegment]:
    if device_item_path is None:
        return []

    if isinstance(device_item_path, (str, int)):
        raw_segments: List[PathSegment] = [device_item_path]
    else:
        raw_segments = list(device_item_path)

    normalized: List[PathSegment] = []
    for segment in raw_segments:
        if isinstance(segment, str):
            stripped = segment.strip()
            if not stripped:
                continue
            normalized.append(int(stripped) if stripped.isdigit() else stripped)
        else:
            normalized.append(segment)
    return normalized


def _safe_member_value(obj, member_name: str, default: Any = None) -> Any:
    try:
        value = getattr(obj, member_name)
        return value if value is not None else default
    except Exception:
        return default


def _net_to_python(value: Any) -> Any:
    """尽量把 .NET 对象转为便于打印/序列化的 Python 值。"""
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")

    try:
        if hasattr(value, "ToString"):
            text = value.ToString()
            if text is not None:
                return str(text)
    except Exception:
        pass

    try:
        return str(value)
    except Exception:
        return value


def describe_hardware_object(obj) -> Dict[str, Any]:
    """提取设备/设备项的常用信息，便于日志和 demo 输出。"""
    info = {
        "name": _net_to_python(_safe_member_value(obj, "Name")),
        "type_identifier": _net_to_python(_safe_member_value(obj, "TypeIdentifier")),
        "type_name": _net_to_python(_safe_member_value(obj, "TypeName")),
        "position_number": _net_to_python(_safe_member_value(obj, "PositionNumber")),
        "classification": _net_to_python(_safe_member_value(obj, "Classification")),
        "python_type": type(obj).__name__,
    }
    return {key: value for key, value in info.items() if value not in (None, "")}


def list_device_items(project, device_name: str) -> List[Dict[str, Any]]:
    """
    递归列出某个设备下的所有 DeviceItem，返回名字和路径。

    路径使用从 0 开始的索引列表，可直接作为 get/fill/update 的
    device_item_path 参数使用。
    """
    device = find_device(project, device_name)
    result: List[Dict[str, Any]] = []

    def walk(parent, path_prefix: List[int]):
        children = _list_device_items(parent)
        for index, item in enumerate(children):
            current_path = path_prefix + [index]
            item_info = describe_hardware_object(item)
            item_info["path"] = current_path
            result.append(item_info)
            walk(item, current_path)

    walk(device, [])
    return result


def find_device(project, device_name: str):
    """按设备名称查找项目中的顶层设备。"""
    try:
        for device in project.Devices:
            if str(device.Name) == device_name:
                return device
    except Exception:
        pass

    raise RuntimeError(f"未找到设备：{device_name}")


def _match_device_item(item, segment: str) -> bool:
    candidate = segment.strip().lower()
    for attr_name in ("Name", "TypeIdentifier", "TypeName"):
        attr_value = _net_to_python(_safe_member_value(item, attr_name))
        if isinstance(attr_value, str) and attr_value.strip().lower() == candidate:
            return True
    return False


def resolve_hardware_object(
    project,
    device_name: str,
    device_item_path: Optional[Sequence[PathSegment] | PathSegment] = None,
):
    """
    根据设备名 + 设备项路径定位硬件对象。

    - 当 device_item_path 为空时，返回顶层 Device
    - 当 device_item_path 为 [0, 1] 这类索引路径时，逐层进入 DeviceItems
    - 当 device_item_path 为 ["Rack_1", "DI_16x24VDC"] 这类名称路径时，按名称匹配
    """
    current = find_device(project, device_name)
    normalized_path = _normalize_path_segments(device_item_path)

    for segment in normalized_path:
        children = _list_device_items(current)
        if not children:
            raise RuntimeError(
                f"对象 {describe_hardware_object(current)} 下不存在 DeviceItems，"
                f"无法继续解析路径段 {segment!r}"
            )

        next_item = None
        if isinstance(segment, int):
            if segment < 0 or segment >= len(children):
                raise IndexError(
                    f"路径段索引越界：{segment}，当前层只有 {len(children)} 个 DeviceItem"
                )
            next_item = children[segment]
        else:
            next_item = next(
                (item for item in children if _match_device_item(item, segment)),
                None,
            )
            if next_item is None:
                available_names = [
                    _net_to_python(_safe_member_value(item, "Name")) for item in children
                ]
                raise RuntimeError(
                    f"在对象 {describe_hardware_object(current)} 下未找到路径段 {segment!r}。"
                    f" 当前可用子项：{available_names}"
                )

        current = next_item

    return current


def _get_attribute_info_map(engineering_object) -> Dict[str, Dict[str, Any]]:
    info_map: Dict[str, Dict[str, Any]] = {}

    try:
        attribute_infos = engineering_object.GetAttributeInfos()
    except Exception:
        attribute_infos = []

    for info in attribute_infos:
        name = _net_to_python(_safe_member_value(info, "Name"))
        if not name:
            continue

        writable = None
        read_only = _safe_member_value(info, "IsReadOnly", None)
        if read_only is not None:
            writable = not bool(read_only)
        else:
            for member_name in ("IsWriteable", "IsWritable", "CanWrite", "Writable"):
                value = _safe_member_value(info, member_name, None)
                if value is not None:
                    writable = bool(value)
                    break

        access_mode = _net_to_python(_safe_member_value(info, "AccessMode"))
        if writable is None and isinstance(access_mode, str):
            writable = "write" in access_mode.lower()

        info_map[str(name)] = {
            "name": str(name),
            "data_type": _net_to_python(_safe_member_value(info, "DataType")),
            "access_mode": access_mode,
            "writable": writable,
        }

    return info_map


def _read_attribute_value(engineering_object, attribute_name: str) -> Any:
    return _net_to_python(engineering_object.GetAttribute(attribute_name))


def _coerce_parameter_value(new_value: Any, current_value: Any) -> Any:
    """
    按当前属性值的 Python 类型做一个轻量转换，便于处理布尔/整数类属性。
    未识别类型保持原值透传给 Openness。
    """
    if current_value is None:
        return new_value

    if isinstance(current_value, bool):
        if isinstance(new_value, str):
            normalized = new_value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(new_value)

    if isinstance(current_value, int) and not isinstance(current_value, bool):
        try:
            return int(new_value)
        except Exception:
            return new_value

    if isinstance(current_value, float):
        try:
            return float(new_value)
        except Exception:
            return new_value

    return new_value


def get_device_parameter(
    project,
    device_name: str,
    parameter_names: Optional[Sequence[str] | str] = None,
    device_item_path: Optional[Sequence[PathSegment] | PathSegment] = None,
) -> Dict[str, Any]:
    """
    读取设备或设备项参数。

    Args:
        project: TIA Project 对象
        device_name: 顶层设备名称
        parameter_names: 要读取的参数名；为 None 时读取当前对象全部可见属性
        device_item_path:
            可选，设备项路径，为空时读取顶层 Device 的属性。
            用于在顶层设备(Device)下面继续定位某个具体的设备项(DeviceItem)
            如果你想读取整台设备的属性，不要传这个参数。
            如果你想读取 CPU、机架、IO 模块等某个子模块的属性，需要传这个参数。
            支持两种写法:
            - 索引路径，例如 [0, 1]，表示先进入第 0 个，再进入它下面的第 1 个
            - 名称路径，例如 ["Rack_1", "Slot_3"]，表示按名字逐层匹配 DeviceItem

    返回值：
        返回一个字典，整体结构示例如下：
            {
                "target": {...},
                "parameters": {
                    "Name": {...},
                    "Comment": {...},
                }
            }
    """
    target = resolve_hardware_object(project, device_name, device_item_path)
    attribute_infos = _get_attribute_info_map(target)

    if parameter_names is None:
        names = list(attribute_infos.keys())
        if not names:
            raise RuntimeError(
                f"对象 {describe_hardware_object(target)} 未返回可枚举属性，"
                "请显式传入 parameter_names。"
            )
    elif isinstance(parameter_names, str):
        names = [parameter_names]
    else:
        names = list(parameter_names)

    parameters: Dict[str, Dict[str, Any]] = {}
    for name in names:
        parameter_info = dict(attribute_infos.get(name, {"name": name}))
        try:
            parameter_info["value"] = _read_attribute_value(target, name)
        except Exception as exc:
            parameter_info["error"] = str(exc)
        parameters[name] = parameter_info

    return {
        "target": describe_hardware_object(target),
        "parameters": parameters,
    }


def fill_device_parameter(
    project,
    device_name: str,
    parameter_values: Mapping[str, Any],
    device_item_path: Optional[Sequence[PathSegment] | PathSegment] = None,
    strict: bool = True,
) -> Dict[str, Any]:
    """
    批量写入设备或设备项的属性参数。

    参数说明：
        project: 已经通过 Openness 打开的 TIA Project 对象。
        device_name: 顶层设备名称，也就是项目树里类似 "PLC_1"、"PLC_2" 这样的站名。
        parameter_values:
            要批量写入的属性字典，格式示例为:
                {
                    "Name": "PLC_2_FINAL",
                    "Comment": "由脚本写入",
                }
            key 是属性名，value 是要写入的新值。
        device_item_path:
            可选，表示从顶层 Device 继续向下定位某个 DeviceItem 的路径。
            - 传 None:
                直接操作顶层 Device 本身
            - 传索引路径，例如 [0, 1]:
                表示先进入第 1 层的第 0 个 DeviceItem，再进入其下第 1 个 DeviceItem
            - 传名称路径，例如 ["PLC_1", "CPU 1510SP-1 PN"] 或 ["Rack_1", "DI_16x24VDC"]:
                表示按名称逐层匹配 DeviceItem
        strict:
            控制写入失败时的处理方式。
            - True:
                只要有任意一个属性写入失败，函数就抛出异常
            - False:
                不抛异常，而是在返回结果里给出 updated / failed 明细

    返回值：
        返回一个字典，包含：
        - target: 实际被操作的对象信息
        - updated: 成功写入并回读成功的属性
        - failed: 写入失败的属性及错误信息
    """
    if not parameter_values:
        raise ValueError("parameter_values 不能为空。")

    target = resolve_hardware_object(project, device_name, device_item_path)
    attribute_infos = _get_attribute_info_map(target)
    updated: Dict[str, Dict[str, Any]] = {}
    failed: Dict[str, str] = {}

    for parameter_name, new_value in parameter_values.items():
        try:
            current_value = _read_attribute_value(target, parameter_name)
        except Exception:
            current_value = None

        coerced_value = _coerce_parameter_value(new_value, current_value)

        try:
            target.SetAttribute(parameter_name, coerced_value)
        except Exception:
            # 类型推断失败时，尝试直接把原始值交给 Openness 处理
            try:
                target.SetAttribute(parameter_name, new_value)
            except Exception as exc:
                failed[parameter_name] = str(exc)
                continue

        parameter_info = dict(attribute_infos.get(parameter_name, {"name": parameter_name}))
        try:
            parameter_info["value"] = _read_attribute_value(target, parameter_name)
        except Exception as exc:
            parameter_info["error"] = str(exc)
        updated[parameter_name] = parameter_info

    result = {
        "target": describe_hardware_object(target),
        "updated": updated,
        "failed": failed,
    }

    if strict and failed:
        raise RuntimeError(
            f"批量写入设备参数时存在失败项：{failed}\n"
            f"目标对象：{describe_hardware_object(target)}"
        )

    return result


def updata_device_parameter(
    project,
    device_name: str,
    parameter_name: str,
    parameter_value: Any,
    device_item_path: Optional[Sequence[PathSegment] | PathSegment] = None,
    strict: bool = True,
) -> Dict[str, Any]:
    """单个设备参数更新接口，保留 updata 命名以匹配当前需求。"""
    return fill_device_parameter(
        project=project,
        device_name=device_name,
        parameter_values={parameter_name: parameter_value},
        device_item_path=device_item_path,
        strict=strict,
    )



def add_device(
    project,
    type_identifier: str,
    device_name: str,
    device_item_name: Optional[str] = None,
    container_device_name: Optional[str] = None,
    container_device_item_path: Optional[Sequence[PathSegment] | PathSegment] = None,
    position_number: Optional[int] = None,
) -> Dict[str, Any]:
    """
    添加设备或模块的通用接口。
    这个接口兼容两种典型场景：
    1. 新增顶层设备
       例如在项目树根节点下新增一台 PLC、HMI 或其他硬件站。
       这种情况下，函数内部会调用:
           project.Devices.CreateWithItem(...)
    2. 在现有设备下插入模块
       例如在某个机架、底座、站点或模块容器下插入一个新的 DeviceItem。
       这种情况下，函数内部会定位到目标容器对象，然后调用:
           container.PlugNew(...)

    参数说明：
        project: 已经通过 Openness 打开的 TIA Project 对象。
        type_identifier:
            要创建的设备/模块的类型标识。
            它通常来自博图硬件目录或从已存在对象上读取到的 TypeIdentifier。
            例如某个 CPU、ET200SP 站、IO 模块的类型字符串。
        device_name:
            当新增顶层设备时，它表示项目树里显示的设备名称。
            当通过 PlugNew 新增模块时，它通常作为逻辑名称使用，用于帮助识别新插入的对象。
        device_item_name:
            创建设备项时使用的内部项名称。
            如果不传，则默认与 device_name 相同。
            对于 CreateWithItem(...) 来说，这个值通常对应“首个 DeviceItem”的名称。
        container_device_name: 可选，目标容器所属的顶层设备名称。
            - 不传:
                表示新增的是项目根级的顶层 Device
            - 传值:
                表示要往某个已有设备内部插模块，此时函数会先找到这个顶层设备
        container_device_item_path:
            可选，在 container_device_name 传值时，即在在现有设备下插入模块时，该参数才有意义。
            用于在 container_device_name 对应的顶层设备下面继续定位具体容器。
            支持两种形式: 索引路径和名称路径。
            例如：
            - [0]: 进入第一层的第 0 个 DeviceItem
            - [0, 1]: 先进入第 0 个，再进入它下面的第 1 个
            - ["Rack_1", "Slot_3"]: 按名字逐层匹配 DeviceItem
            如果只传 container_device_name、不传这个参数，则默认把顶层 Device 本身作为容器。
        position_number: 插槽号/安装位置编号，仅在 PlugNew 场景下必须提供。
            - 新增顶层设备时可以不传
            - 往已有容器里插模块时必须传

    返回值：
        - 顶层设备创建场景:
            返回 mode="project_device"，以及新建的 device 和首个 first_device_item 信息
        - 模块插入场景:
            返回 mode="plug_new"，以及目标容器对象和新建模块对象信息
    """
    actual_item_name = device_item_name or device_name

    if not container_device_name:
        created_device = project.Devices.CreateWithItem(
            type_identifier,
            device_name,
            actual_item_name,
        )
        root_items = _list_device_items(created_device)
        return {
            "mode": "project_device",
            "device": describe_hardware_object(created_device),
            "first_device_item": (
                describe_hardware_object(root_items[0]) if root_items else None
            ),
        }

    container = resolve_hardware_object(
        project,
        container_device_name,
        container_device_item_path,
    )

    if position_number is None:
        raise ValueError(
            "当使用 PlugNew 添加模块时，position_number 不能为空。"
        )

    try:
        can_plug = container.CanPlugNew(type_identifier, actual_item_name, position_number)
        if can_plug is False:
            raise RuntimeError(
                f"容器 {describe_hardware_object(container)} 不允许在槽位 {position_number} "
                f"插入设备 {type_identifier}"
            )
    except AttributeError:
        pass

    created_item = container.PlugNew(
        type_identifier,
        actual_item_name,
        position_number,
    )

    return {
        "mode": "plug_new",
        "container": describe_hardware_object(container),
        "created_item": describe_hardware_object(created_item),
    }


def import_scl_block(plc_software, temp_dir: str, block: BlockSpec) -> str:
    """用外部源文件导入 SCL 并生成块"""
    from Siemens.Engineering.SW.ExternalSources import GenerateBlockOption  # type: ignore

    if not block.name:
        raise ValueError("SCL 块导入时，BlockSpec.name 不能为空。")

    source_name = block.name
    old_src = find_external_source_by_name(plc_software, source_name)
    delete_if_exists(old_src)

    file_name = block.file_name or safe_filename(block.name, ".scl")
    scl_path = write_text_file(temp_dir, file_name, block.content)

    ext_src = plc_software.ExternalSourceGroup.ExternalSources.CreateFromFile(
        source_name,
        scl_path
    )

    option_none = getattr(GenerateBlockOption, "None")
    ext_src.GenerateBlocksFromSource(option_none)
    return scl_path


def _try_import_xml_on_target(target, file_info, override_option):
    """尝试不同 Import 重载"""
    last_error = None

    try:
        return target.Import(file_info, override_option)
    except Exception as e:
        last_error = e

    try:
        return target.Import(file_info)
    except Exception as e:
        last_error = e

    raise last_error


def import_lad_block_xml(plc_software, temp_dir: str, block):

    from Siemens.Engineering import ImportOptions
    from System.IO import FileInfo

    file_name = block.file_name or f"{block.name}.xml"
    xml_path = write_text_file(temp_dir, file_name, block.content)

    blocks = plc_software.BlockGroup.Blocks

    file_info = FileInfo(xml_path)

    blocks.Import(file_info, ImportOptions.Override)

    print("LAD block imported:", xml_path)

    return xml_path





def compile_plc_software(plc_software):
    from Siemens.Engineering.Compiler import ICompilable  # type: ignore

    compiler = plc_software.GetService[ICompilable]()
    result = compiler.Compile()

    compile_state = None
    messages: List[str] = []

    try:
        compile_state = str(result.State)
    except Exception:
        compile_state = None

    try:
        for message in result.Messages:
            try:
                messages.append(f"[{message.Category}] {message.Description}")
            except Exception:
                messages.append(str(message))
    except Exception:
        pass

    return compile_state, messages


def build_tia_project(
    config: ProjectBuildConfig,
    blocks: Sequence[BlockSpec],
) -> BuildResult:
    """
    主入口函数：
    - 创建项目
    - 增加 PLC
    - 导入多个块（SCL / LAD-XML）
    - 编译、保存

    返回 BuildResult，方便调用方拿到工程路径、临时文件路径和编译消息
    """
    add_publicapi_reference(config.public_api_dir)

    tia = None
    project = None
    temp_dir = tempfile.mkdtemp(prefix="tia_py_", dir=config.project_root)
    result_obj = BuildResult(
        project_path=os.path.join(config.project_root, config.project_name),
        temp_dir=temp_dir,
    )

    try:
        tia, project, device, plc_software, project_dir = create_project_and_device(config)
        result_obj.project_path = project_dir

        for block in blocks:
            lang = (block.language or "").strip().upper()

            if lang == "SCL":
                path = import_scl_block(plc_software, temp_dir, block)
                result_obj.imported_scl_files.append(path)

            elif lang == "LAD":
                path = import_lad_block_xml(plc_software, temp_dir, block)
                result_obj.imported_lad_xml_files.append(path)

            else:
                raise ValueError(f"不支持的 language: {block.language}，目前只支持 SCL / LAD")

        if config.compile_after_import:
            state, messages = compile_plc_software(plc_software)
            result_obj.compile_state = state
            result_obj.compile_messages.extend(messages)

        if config.auto_save and project is not None:
            project.Save()

        return result_obj

    except Exception:
        raise RuntimeError(traceback.format_exc())

    finally:
        time.sleep(2)

        try:
            if project is not None and config.auto_save:
                project.Save()
        except Exception:
            pass

        try:
            if tia is not None:
                tia.Dispose()
        except Exception:
            pass

        if config.cleanup_temp_dir:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


DEFAULT_OB1_SCL = r'''
ORGANIZATION_BLOCK "Main"
{ S7_Optimized_Access := 'TRUE' }
VERSION : 0.1
BEGIN
    IF %I0.0 AND NOT %I0.1 THEN
        %Q0.0 := TRUE;
    END_IF;

    IF %I0.1 THEN
        %Q0.0 := FALSE;
    END_IF;
END_ORGANIZATION_BLOCK
'''.strip()


def build_demo_project():
    """一个最小示例：只创建工程并导入一个 SCL 的 OB1"""
    cfg = ProjectBuildConfig(
        public_api_dir=r"E:\PlcProject\SoftWares\Siemens\Automation\Portal V17\PublicAPI\V17",
        project_root=r"E:\PlcProject\Code\PLC\tia_python_demo_output",
        project_name="PyOpennessDemo",
        cpu_order_number="OrderNumber:6ES7 510-1DJ01-0AB0/V2.0",
        overwrite_existing_project_dir=True,
    )

    blocks = [
        BlockSpec(
            language="SCL",
            name="OB1",
            content=DEFAULT_OB1_SCL,
        ),
    ]

    return build_tia_project(cfg, blocks)


if __name__ == "__main__":
    demo_result = build_demo_project()
    print("工程路径:", demo_result.project_path)
    print("编译状态:", demo_result.compile_state)
    if demo_result.compile_messages:
        print("编译消息:")
        for msg in demo_result.compile_messages:
            print(" -", msg)
