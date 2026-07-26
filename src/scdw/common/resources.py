"""运行时资源路径解析，兼容源码运行与 PyInstaller 打包运行。"""
from __future__ import annotations

import sys
from pathlib import Path

from scdw.common.paths import PROJECT_ROOT


def resource_path(*parts: str) -> Path:
    """返回资源绝对路径；打包后优先使用解包目录。"""
    root = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return root.joinpath(*parts)


def mac_logo_path() -> Path:
    """返回 MACtrl 主 Logo 的唯一资源路径。"""
    return resource_path("assets", "logo", "mac_logo.png")
