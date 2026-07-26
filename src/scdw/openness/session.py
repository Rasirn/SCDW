"""单实例 TIA Portal 会话管理。"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scdw.common.exceptions import TiaSessionError
from .tia_core import create_project, save_project, start_tia_portal


@dataclass
class TiaSessionManager:
    """管理一个 TIA Portal 实例、当前工程和已注册 PLC Software。"""

    tia: Any = None
    project: Any = None
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    temp_dir: Path | None = None
    cleanup_paths: list[Path] = field(default_factory=list)

    def start(self, with_ui: bool = False) -> Any:
        """启动 TIA Portal；重复调用返回当前实例。"""
        if self.tia is None:
            self.tia = start_tia_portal(with_ui=with_ui)
        return self.tia

    def create_project(self, project_root: str | Path, project_name: str, overwrite: bool = False) -> Any:
        """创建工程并登记为当前工程。"""
        self.project = create_project(self.start(), str(project_root), project_name, overwrite)
        return self.project

    def require_project(self) -> Any:
        """返回当前工程；未初始化时给出中文异常。"""
        if self.project is None:
            raise TiaSessionError("TIA 会话尚未初始化，请先创建或打开测试工程。")
        return self.project

    def register_device(self, name: str, device: Any, plc_software: Any) -> None:
        """登记设备及其 PLC Software。"""
        self.devices[name] = {"device": device, "plc_software": plc_software}

    def get_plc_software(self, device_name: str) -> Any:
        """取得已登记设备的 PLC Software。"""
        entry = self.devices.get(device_name)
        if entry is None:
            raise TiaSessionError(f"设备“{device_name}”不在当前会话中。")
        return entry["plc_software"]

    def get_temp_dir(self) -> str:
        """取得测试/导入临时目录。"""
        if self.temp_dir is None or not self.temp_dir.is_dir():
            self.temp_dir = Path(tempfile.mkdtemp(prefix="scdw_tia_"))
        return str(self.temp_dir)

    def close(self, save: bool = True) -> None:
        """保存（可选）并释放工程、TIA 实例和临时文件。"""
        try:
            if save and self.project is not None:
                save_project(self.project)
        finally:
            try:
                if self.project is not None and hasattr(self.project, "Close"):
                    self.project.Close()
            except Exception:
                pass
            try:
                if self.tia is not None:
                    self.tia.Dispose()
            except Exception:
                pass
            if self.temp_dir is not None:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            for path in self.cleanup_paths:
                shutil.rmtree(path, ignore_errors=True)
            self.tia = None
            self.project = None
            self.devices.clear()
            self.temp_dir = None
            self.cleanup_paths.clear()
