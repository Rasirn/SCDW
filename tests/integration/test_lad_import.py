# -*- coding: utf-8 -*-
"""
test_lad_import.py
测试将指定 LAD XML 文件导入 TIA Portal S7-1200，验证是否报错。

运行前提：以管理员权限运行，TIA Portal V17 已安装。
用法：
    python tests/integration/test_lad_import.py
    python tests/integration/test_lad_import.py <xml路径>
"""
from __future__ import annotations

import sys
import os

# ── 路径初始化 ────────────────────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# ── 配置项（按需修改）────────────────────────────────────────────────────────
PROJECT_ROOT = r'E:\PlcProject\TestProjects'
PROJECT_NAME = 'LAD_XML_Test'
CPU_ORDER    = 'OrderNumber:6ES7 214-1BG40-0XB0/V4.4'   # 按本地 TIA 版本调整 /V 号

# 默认测试的 XML 文件路径
DEFAULT_XML = os.path.join(ROOT, 'data', 'rag', 'templates', 'application', '报警.xml')


def main(xml_path: str) -> None:
    from scdw.openness.tia_core import start_tia_portal, create_project, save_project
    from scdw.openness.tia_hardware import add_plc_device

    xml_path = os.path.abspath(xml_path)
    if not os.path.isfile(xml_path):
        print(f'❌ XML 文件不存在: {xml_path}')
        sys.exit(1)

    print(f'测试文件 : {xml_path}')

    # 1. 启动 TIA Portal（内部会加载 Siemens.Engineering DLL）
    print('\n[1/3] 启动 TIA Portal...')
    tia = start_tia_portal(with_ui=True)
    print('      已启动')

    # DLL 加载完成后才能导入 .NET 命名空间
    from Siemens.Engineering import ImportOptions  # type: ignore
    from System.IO import FileInfo                 # type: ignore

    # 2. 新建项目 + 添加 CPU
    print(f'\n[2/3] 新建项目 {PROJECT_NAME}...')
    project = create_project(tia, PROJECT_ROOT, PROJECT_NAME, overwrite=True)
    print(f'      项目已创建: {project.Name}')
    device, plc_sw = add_plc_device(project, CPU_ORDER, 'PLC_1', 'PLC_1')
    print(f'      PLC 已添加: {device.Name}')
    save_project(project)

    # 3. 直接调用 Openness API 导入，不做任何预处理
    print(f'\n[3/3] 导入 XML...')
    try:
        plc_sw.BlockGroup.Blocks.Import(FileInfo(xml_path), ImportOptions.Override)
        save_project(project)
        print(f'\n✅ 导入成功')
    except Exception as exc:
        print(f'\n❌ 导入失败：\n{exc}')

    print('\n（TIA Portal 保持开启，可手动检查结果）')


if __name__ == '__main__':
    xml = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XML
    main(xml)
