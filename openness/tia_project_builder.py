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
from typing import List, Optional, Sequence


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
