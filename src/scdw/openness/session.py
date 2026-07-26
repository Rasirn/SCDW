"""TIA Portal 会话、附着和上下文刷新管理。"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from scdw.common.exceptions import TiaSessionError
from scdw.common.run_logging import get_run_logger

from .context import TiaConnectionMode, TiaContext
from .discovery import attach_tia_process, discover_plc_devices, list_open_projects, list_running_tia_processes
from .executor import TiaOpennessExecutor
from .tia_core import create_project, save_project, start_tia_portal


@dataclass
class TiaSessionManager:
    """管理一个 TIA 会话，并确保所有 .NET 对象只在专用线程中使用。"""

    tia: Any = None
    project: Any = None
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    temp_dir: Path | None = None
    cleanup_paths: list[Path] = field(default_factory=list)
    executor: TiaOpennessExecutor = field(default_factory=TiaOpennessExecutor)
    context: TiaContext = field(default_factory=TiaContext)

    def run(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """在 Openness 专用线程执行操作。不要将返回的 .NET 对象传到线程外。"""
        get_run_logger().log_event("tia_operation_requested", component="tia", operation=getattr(operation, "__name__", type(operation).__name__), context_before=self.context.serialise())
        return self.executor.run(operation, *args, **kwargs)

    def list_processes(self) -> list[dict[str, Any]]:
        """重新枚举运行中的 TIA 进程。"""
        return self.run(list_running_tia_processes)

    def is_alive(self) -> bool:
        """验证当前 PID 仍存在；已失效时清理会话引用。"""
        if self.tia is None or self.context.process_id is None:
            return False
        try:
            alive = any(p.get("process_id") == self.context.process_id for p in self.list_processes())
        except Exception:
            alive = False
        if not alive:
            self._invalidate("TIA Portal 连接已失效，可能已被用户关闭。")
        return alive

    @staticmethod
    def _same_path(left: str | None, right: str | None) -> bool:
        if not left or not right:
            return False
        try:
            return Path(left).resolve() == Path(right).resolve()
        except Exception:
            return left.casefold() == right.casefold()

    def _choose_process(self, processes: list[dict[str, Any]], process_id: int | None, project_path: str | None) -> dict[str, Any]:
        if process_id is not None:
            match = next((item for item in processes if item.get("process_id") == process_id), None)
            if match is None:
                raise TiaSessionError(f"未找到进程 ID 为 {process_id} 的 TIA Portal。")
            return match
        if project_path:
            candidates = [item for item in processes if self._same_path(item.get("project_path"), project_path)]
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise TiaSessionError("存在多个打开目标工程的 TIA Portal，请使用 process_id 明确选择。")
        if len(processes) == 1:
            return processes[0]
        if not processes:
            raise TiaSessionError("未发现运行中的 TIA Portal。请先启动 TIA，或使用 init_tia_project 新建工程。")
        raise TiaSessionError("发现多个 TIA Portal，无法安全自动选择；请使用 process_id 明确指定。")

    def attach(self, process_id: int | None = None, project_path: str | None = None) -> dict[str, Any]:
        """附着到用户已启动的 TIA；多实例时绝不默认选择第一项。"""
        def operation() -> dict[str, Any]:
            processes = list_running_tia_processes()
            choice = self._choose_process(processes, process_id, project_path)
            self._dispose_current_for_replace()
            self.tia = attach_tia_process(int(choice["process_id"]))
            self.project = None
            self.devices.clear()
            self.context = TiaContext(connected=True, process_id=int(choice["process_id"]),
                                      connection_mode=TiaConnectionMode.ATTACHED.value,
                                      owns_tia_process=False, owns_project=False,
                                      executor_thread_id=self.executor.thread_id)
            return self._refresh_context_on_thread(project_path=project_path)
        try:
            result = self.run(operation)
            get_run_logger().log_event("tia_attach_succeeded", component="tia", process_id=process_id, project_path=project_path, context_after=result)
            return result
        except Exception as exc:
            get_run_logger().log_exception("tia_attach_failed", exc, component="tia", process_id=process_id, project_path=project_path)
            raise

    def start(self, with_ui: bool = False) -> Any:
        """启动由本程序拥有的 TIA 实例。"""
        def operation() -> Any:
            if self.tia is None:
                self.tia = start_tia_portal(with_ui=with_ui)
                self.context = TiaContext(connected=True, connection_mode=TiaConnectionMode.OWNED.value,
                                          owns_tia_process=True, executor_thread_id=self.executor.thread_id)
            return self.tia
        get_run_logger().log_event("tia_start_requested", component="tia", with_ui=with_ui)
        return self.run(operation)

    def create_project(self, project_root: str | Path, project_name: str, overwrite: bool = False) -> Any:
        """创建本程序拥有的工程。"""
        def operation() -> Any:
            self.project = create_project(self.tia, str(project_root), project_name, overwrite)
            self.context.owns_project = True
            self._refresh_context_on_thread(project_name=project_name)
            return self.project
        get_run_logger().log_event("tia_create_project_requested", component="tia", project_root=str(project_root), project_name=project_name, overwrite=overwrite)
        self.start()
        return self.run(operation)

    def _select_project_on_thread(self, projects: list[dict[str, Any]], project_name: str | None, project_path: str | None) -> dict[str, Any] | None:
        if project_path:
            matches = [p for p in projects if self._same_path(p.get("path"), project_path)]
        elif project_name:
            matches = [p for p in projects if p.get("name") == project_name]
        else:
            primary = [p for p in projects if p.get("is_primary")]
            matches = primary if len(primary) == 1 else (projects if len(projects) == 1 else [])
        if not matches:
            if not projects:
                return None
            raise TiaSessionError("当前 TIA 中存在多个工程，请使用 project_name 或 project_path 明确选择。")
        if len(matches) != 1:
            raise TiaSessionError("工程选择不唯一，请提供完整工程路径。")
        return matches[0]

    def _refresh_context_on_thread(self, project_name: str | None = None, project_path: str | None = None) -> dict[str, Any]:
        if self.tia is None:
            raise TiaSessionError("当前未连接 TIA Portal。")
        projects = list_open_projects(self.tia)
        selected = self._select_project_on_thread(projects, project_name, project_path)
        old_identity = self.context.project_identity
        if selected is None:
            self.project = None
            self.devices.clear()
            self.context.project_name = self.context.project_path = self.context.project_identity = None
            self.context.project_is_primary = None
            self.context.plc_devices = []
        else:
            # 仅在确认唯一目标后获取对应 .NET 工程对象。
            match_project = next(p for p in self.tia.Projects if str(getattr(p, "Name", "")) == selected["name"])
            self.project = match_project
            identity = selected.get("path") or selected.get("name")
            if identity != old_identity:
                self.devices.clear()
            self.context.project_name = selected.get("name")
            self.context.project_path = selected.get("path")
            self.context.project_is_primary = selected.get("is_primary")
            self.context.project_identity = identity
            summary, registry = discover_plc_devices(self.project)
            self.devices = registry
            self.context.plc_devices = summary
        self.context.context_version += 1
        from datetime import datetime, timezone
        self.context.last_refresh_time = datetime.now(timezone.utc).isoformat()
        self.context.last_connection_error = None
        self.context.executor_thread_id = self.executor.thread_id
        return self.context.serialise()

    def refresh_context(self) -> dict[str, Any]:
        """重新枚举工程和 PLC；连接失效时清理缓存并返回清晰错误。"""
        try:
            if not self.is_alive():
                raise TiaSessionError("TIA Portal 连接已失效。")
            result = self.run(self._refresh_context_on_thread)
            get_run_logger().log_event("tia_context_refreshed", component="tia", context_after=result)
            return result
        except Exception as exc:
            get_run_logger().log_exception("tia_context_refresh_failed", exc, component="tia")
            self._invalidate(str(exc))
            raise TiaSessionError(f"刷新 TIA 上下文失败：{exc}") from exc

    def select_project(self, project_name: str | None = None, project_path: str | None = None) -> dict[str, Any]:
        """在多工程场景中显式选择工程。"""
        if not project_name and not project_path:
            raise TiaSessionError("请选择 project_name 或 project_path。")
        return self.run(self._refresh_context_on_thread, project_name, project_path)

    def discover_plc_devices(self) -> list[dict[str, Any]]:
        """重新扫描当前工程 PLC Software。"""
        return self.refresh_context()["plc_devices"]

    def get_context_summary(self) -> dict[str, Any]:
        """返回缓存摘要，不触碰工程对象。"""
        return self.context.serialise()

    def ensure_current_context(self) -> dict[str, Any]:
        """写入操作前刷新上下文，防止将旧工程操作写入新工程。"""
        return self.refresh_context()

    def require_project(self) -> Any:
        """返回当前工程；仅能由 Openness 线程中的操作使用。"""
        if self.project is None:
            raise TiaSessionError("已连接 TIA，但当前没有打开工程。")
        return self.project

    def register_device(self, name: str, device: Any, plc_software: Any) -> None:
        """登记新建 PLC，并保持名称兼容别名。"""
        key = name
        self.devices[key] = {"device": device, "plc_software": plc_software, "device_name": name}

    def get_plc_software(self, device_name: str) -> Any:
        """按完整设备路径或唯一显示名称获取 PLC Software；重名时拒绝猜测。"""
        entry = self.devices.get(device_name)
        if entry:
            return entry["plc_software"]
        matches = [(key, item) for key, item in self.devices.items() if item.get("device_name") == device_name]
        if len(matches) == 1:
            return matches[0][1]["plc_software"]
        if len(matches) > 1:
            paths = "、".join(key for key, _ in matches)
            raise TiaSessionError(f"设备名称“{device_name}”不唯一，请使用完整设备路径：{paths}")
        raise TiaSessionError(f"未发现 PLC 设备“{device_name}”，请先刷新 TIA 上下文。")

    def get_temp_dir(self) -> str:
        """取得导入用临时目录。"""
        if self.temp_dir is None or not self.temp_dir.is_dir():
            self.temp_dir = Path(tempfile.mkdtemp(prefix="scdw_tia_"))
        return str(self.temp_dir)

    def _dispose_current_for_replace(self) -> None:
        if self.tia is not None:
            try:
                self.tia.Dispose()
            except Exception:
                pass
        self.tia = self.project = None
        self.devices.clear()

    def _invalidate(self, message: str) -> None:
        self.tia = self.project = None
        self.devices.clear()
        self.context = TiaContext(last_connection_error=message, executor_thread_id=self.executor.thread_id)

    def detach(self, save: bool = False) -> None:
        """断开连接；附着模式绝不保存、关闭用户工程或关闭用户 TIA。"""
        def operation() -> None:
            if self.context.connection_mode == TiaConnectionMode.OWNED.value:
                self.close_owned_session(save=save)
                return
            try:
                if self.tia is not None:
                    self.tia.Dispose()
            finally:
                self._invalidate("")
        get_run_logger().log_event("tia_detach_requested", component="tia", save=save, context_before=self.context.serialise())
        self.run(operation)
        get_run_logger().log_event("tia_detached", component="tia", context_after=self.context.serialise())

    def close_owned_session(self, save: bool = True) -> None:
        """保存并关闭仅由本程序创建的工程及 TIA。"""
        if self.context.connection_mode != TiaConnectionMode.OWNED.value:
            self.detach(save=False)
            return
        try:
            if save and self.project is not None:
                save_project(self.project)
            if self.project is not None and hasattr(self.project, "Close"):
                self.project.Close()
            if self.tia is not None:
                self.tia.Dispose()
        finally:
            self._invalidate("")

    def close(self, save: bool = True) -> None:
        """兼容旧入口：按所有权关闭或断开。"""
        self.detach(save=save)
