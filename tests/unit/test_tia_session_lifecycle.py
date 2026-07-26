"""基于故障日志的 TIA 会话回归测试。"""
from pathlib import Path

import pytest

from scdw.openness.context import TiaConnectionMode
from scdw.openness.session import TiaSessionManager


class InlineExecutor:
    """用同一测试线程模拟专用 Openness 线程。"""
    thread_id = 1
    def run(self, operation, *args, **kwargs):
        return operation(*args, **kwargs)


class FakeTia:
    Projects = []


def test_owned_session_without_pid_is_not_invalidated(monkeypatch):
    session = TiaSessionManager(executor=InlineExecutor())
    session.tia = FakeTia()
    session.context.connection_mode = TiaConnectionMode.OWNED.value
    session.context.owns_tia_process = True
    monkeypatch.setattr("scdw.openness.session.list_open_projects", lambda tia: [])
    assert session.is_alive() is True
    assert session.context.connection_mode == TiaConnectionMode.OWNED.value
    assert session.context.owns_tia_process is True


def test_project_directory_conflict_is_detected_before_start(tmp_path):
    target = tmp_path / "DemoProject"
    target.mkdir()
    assert TiaSessionManager.project_preflight(tmp_path, "DemoProject", overwrite=False)["code"] == "PROJECT_DIRECTORY_EXISTS"


def test_open_project_cannot_be_overwritten(tmp_path, monkeypatch):
    target = tmp_path / "DemoProject"
    target.mkdir()
    session = TiaSessionManager(executor=InlineExecutor())
    monkeypatch.setattr(session, "list_processes", lambda: [{"process_id": 99, "project_path": str(target / "DemoProject.ap17")}])
    result = session.preflight_create_project(tmp_path, "DemoProject", overwrite=True)
    assert result["code"] == "PROJECT_OPEN_IN_TIA"
    assert result["data"]["process_id"] == 99


def test_session_operation_runs_inside_executor():
    session = TiaSessionManager(executor=InlineExecutor())
    session.project = object()
    session.context.connection_mode = TiaConnectionMode.OWNED.value
    assert session.run_project_operation("测试操作", lambda project: {"ok": project is session.project}) == {"ok": True}
