# -*- coding: utf-8 -*-
"""
openness 包 —— TIA Portal Openness 接口封装。

子模块职责：
  tia_core      : TIA 连接、项目 CRUD、通用文件工具
  tia_hardware  : 设备与硬件模块管理
  tia_tags      : PLC 变量表管理
  tia_blocks    : 程序块管理（SCL / 全局 DB / LAD XML）
  tia_compiler  : 编译与下载
"""

from .tia_core import (
    load_tia_api,
    start_tia_portal,
    stop_tia_portal,
    create_project,
    save_project,
    make_temp_dir,
    write_text_file,
    safe_filename,
    ensure_dir,
    net_to_python,
    set_default_api_dir,
    get_default_api_dir,
)

from .tia_hardware import (
    add_plc_device,
    add_module_to_rack,
    get_plc_software,
    find_device,
    resolve_device_item,
    list_device_items_flat,
    describe_item,
)

from .tia_tags import (
    TagSpec,
    create_tag_table,
    add_tag,
    create_tag_table_with_tags,
)

from .tia_blocks import (
    DBVariable,
    import_scl_block,
    create_global_db,
    import_lad_xml_block,
    build_global_db_scl,
    delete_block,
)

from .tia_lad_builder import (
    LadContact,
    LadBox,
    LadOutput,
    LadBranch,
    LadNetwork,
    build_lad_xml,
    lad_networks_from_json,
)

from .tia_compiler import (
    CompileResult,
    compile_plc,
)

__all__ = [
    # tia_core
    "load_tia_api",
    "start_tia_portal",
    "stop_tia_portal",
    "create_project",
    "save_project",
    "make_temp_dir",
    "write_text_file",
    "safe_filename",
    "ensure_dir",
    "net_to_python",
    "set_default_api_dir",
    "get_default_api_dir",
    # tia_hardware
    "add_plc_device",
    "add_module_to_rack",
    "get_plc_software",
    "find_device",
    "resolve_device_item",
    "list_device_items_flat",
    "describe_item",
    # tia_tags
    "TagSpec",
    "create_tag_table",
    "add_tag",
    "create_tag_table_with_tags",
    # tia_blocks
    "DBVariable",
    "import_scl_block",
    "create_global_db",
    "import_lad_xml_block",
    "build_global_db_scl",
    # tia_lad_builder
    "LadContact",
    "LadBox",
    "LadOutput",
    "LadBranch",
    "LadNetwork",
    "build_lad_xml",
    "lad_networks_from_json",
    # tia_compiler
    "CompileResult",
    "compile_plc",
]
