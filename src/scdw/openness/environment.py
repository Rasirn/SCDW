"""TIA Portal Openness 环境检查。"""
import os
import platform
from pathlib import Path

from scdw.common.exceptions import TiaEnvironmentError
from .tia_core import get_default_api_dir, load_tia_api


def check_tia_environment(api_dir: str | None = None, load_api: bool = False) -> Path:
    """检查 Windows、PublicAPI 和 pythonnet；可选加载 Openness 程序集。"""
    if platform.system() != "Windows":
        raise TiaEnvironmentError("TIA Portal Openness 测试只能在 Windows 上执行。")
    target = Path(api_dir or get_default_api_dir())
    dll = target / "Siemens.Engineering.dll"
    if not dll.is_file():
        raise TiaEnvironmentError(f"未找到 TIA Portal PublicAPI DLL：{dll}")
    try:
        import clr  # noqa: F401
    except ImportError as exc:
        raise TiaEnvironmentError("未安装 pythonnet，无法加载 TIA Portal Openness。") from exc
    if load_api:
        try:
            load_tia_api(str(target))
        except Exception as exc:
            raise TiaEnvironmentError(f"加载 TIA Portal Openness 失败：{exc}") from exc
    return target
