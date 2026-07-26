# -*- coding: utf-8 -*-
"""
tia_core.py
TIA Portal 连接管理、项目 CRUD 以及通用工具函数。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from typing import Any, Optional

# ── 默认 Public API 目录 ──────────────────────────────────────────────────────
_DEFAULT_API_DIR = r"E:\PlcProject\SoftWares\Siemens\Automation\Portal V17\PublicAPI\V17"
_api_loaded = False


def get_default_api_dir() -> str:
    return _DEFAULT_API_DIR


def set_default_api_dir(path: str) -> None:
    global _DEFAULT_API_DIR
    _DEFAULT_API_DIR = path


# ── DLL 加载（幂等） ──────────────────────────────────────────────────────────
def load_tia_api(api_dir: Optional[str] = None) -> None:
    """加载 TIA Portal Openness .NET 程序集（可多次调用，只加载一次）。

    除 PublicAPI/V17 目录外，还需将 TIA Portal 安装根目录加入 sys.path 和
    PATH 环境变量，否则 .NET 运行时无法解析 Siemens.Engineering.Contract
    等间接依赖程序集，会抛出 FileNotFoundException。
    """
    global _api_loaded
    if _api_loaded:
        return

    target = api_dir or _DEFAULT_API_DIR

    # PublicAPI/V17 → PublicAPI → Portal V17（安装根目录）
    tia_install_dir = os.path.dirname(os.path.dirname(target))

    for path in (target, tia_install_dir):
        if path and path not in sys.path:
            sys.path.insert(0, path)

    # 将安装目录加入 PATH，确保 .NET 程序集探测器能找到依赖 DLL
    env_path = os.environ.get("PATH", "")
    for path in (target, tia_install_dir):
        if path and path not in env_path:
            os.environ["PATH"] = path + os.pathsep + env_path
            env_path = os.environ["PATH"]

    import clr  # type: ignore
    clr.AddReference("Siemens.Engineering")
    for asm in ("Siemens.Engineering.HW", "Siemens.Engineering.SW"):
        try:
            clr.AddReference(asm)
        except Exception:
            pass

    _api_loaded = True


# ── TIA Portal 实例 ───────────────────────────────────────────────────────────
def start_tia_portal(with_ui: bool = True):
    """启动 TIA Portal，返回 TiaPortal 实例。"""
    load_tia_api()
    from Siemens.Engineering import TiaPortal, TiaPortalMode  # type: ignore

    mode = (
        TiaPortalMode.WithUserInterface
        if with_ui
        else TiaPortalMode.WithoutUserInterface
    )
    return TiaPortal(mode)


def stop_tia_portal(tia) -> None:
    """安全释放 TIA Portal 实例。"""
    try:
        if tia is not None:
            tia.Dispose()
    except Exception:
        pass


# ── 项目管理 ──────────────────────────────────────────────────────────────────
def create_project(tia, project_root: str, project_name: str, overwrite: bool = False):
    """
    在 project_root 下新建名为 project_name 的 TIA 项目。
    若目录已存在且 overwrite=True，先删除旧目录再创建。
    返回 project 对象。
    """
    from System.IO import DirectoryInfo  # type: ignore

    project_dir = os.path.join(project_root, project_name)
    if os.path.exists(project_dir):
        if not overwrite:
            raise RuntimeError(
                f"项目目录已存在：{project_dir}。请将 overwrite=True 以覆盖。"
            )
        _delete_project_directory_after_unlock(project_dir)

    os.makedirs(project_root, exist_ok=True)
    return tia.Projects.Create(DirectoryInfo(project_root), project_name)


def _delete_project_directory_after_unlock(project_dir: str, timeout: float = 8.0) -> None:
    """有界等待项目文件解锁后删除目录，统一转换占用错误。"""
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            shutil.rmtree(project_dir)
            return
        except (PermissionError, OSError) as exc:
            last_error = exc
            time.sleep(0.15)
    raise RuntimeError("PROJECT_FILES_LOCKED") from last_error


def save_project(project) -> None:
    """保存 TIA 项目。"""
    try:
        project.Save()
    except Exception as exc:
        raise RuntimeError(f"项目保存失败：{exc}") from exc


# ── 文件 / 目录辅助 ──────────────────────────────────────────────────────────
def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def make_temp_dir(base_dir: Optional[str] = None) -> str:
    """创建临时目录，返回路径。"""
    if base_dir:
        ensure_dir(base_dir)
    return tempfile.mkdtemp(prefix="tia_py_", dir=base_dir)


def write_text_file(
    folder: str, filename: str, content: str, encoding: str = "utf-8"
) -> str:
    """将文本写入 folder/filename，返回完整路径。"""
    ensure_dir(folder)
    path = os.path.join(folder, filename)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(content)
    return path


def safe_filename(name: str, suffix: str = "") -> str:
    """将字符串净化为合法文件名（去除非法字符），可选附加后缀。"""
    invalid = r'<>:"/\|?*'
    result = (name or "block").strip()
    for ch in invalid:
        result = result.replace(ch, "_")
    if suffix and not result.lower().endswith(suffix.lower()):
        result += suffix
    return result


# ── .NET 对象辅助 ─────────────────────────────────────────────────────────────
def net_to_python(value: Any) -> Any:
    """将 .NET 对象转换为基础 Python 类型，便于打印和序列化。"""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        if hasattr(value, "ToString"):
            text = value.ToString()
            return str(text) if text is not None else None
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return value
