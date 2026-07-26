"""pytest 公共夹具：仅在显式标记的测试中启动 TIA Portal。"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from scdw.common.paths import TEST_PROJECTS_DIR
from scdw.openness.environment import check_tia_environment
from scdw.openness.session import TiaSessionManager


def pytest_configure(config):
    """注册自定义标记，便于直接从源码树运行 pytest。"""
    for marker in ("unit", "integration", "tia", "slow", "requires_tia", "requires_network", "requires_model"):
        config.addinivalue_line("markers", marker)


@pytest.fixture(scope="session")
def tia_environment() -> Path:
    """验证并加载本机 TIA Portal Openness 环境。"""
    try:
        return check_tia_environment(load_api=True)
    except Exception as exc:
        pytest.skip(f"TIA 测试环境不可用：{exc}")


@pytest.fixture
def tia_session(tia_environment):
    """启动独立的无界面 TIA 会话，并在测试结束后释放。"""
    session = TiaSessionManager()
    try:
        session.start(with_ui=False)
    except Exception as exc:
        pytest.fail(f"启动 TIA Portal 失败：{exc}")
    try:
        yield session
    finally:
        session.close(save=True)


@pytest.fixture
def temporary_tia_project(tia_session):
    """为每个测试创建独立临时工程，不触碰人工维护工程。"""
    TEST_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    project_name = f"SCDW_TEST_{uuid.uuid4().hex[:8]}"
    project_dir = TEST_PROJECTS_DIR / project_name
    try:
        project = tia_session.create_project(TEST_PROJECTS_DIR, project_name)
    except Exception as exc:
        pytest.fail(f"创建临时 TIA 工程失败：{exc}")
    try:
        yield tia_session, project, project_dir
    finally:
        keep = os.getenv("SCDW_KEEP_TIA_TEST_PROJECTS", "0") == "1"
        if not keep:
            # 工程仍由 tia_session 持有，必须在其关闭后再删除目录。
            tia_session.cleanup_paths.append(project_dir)


@pytest.fixture
def test_plc_software(temporary_tia_project):
    """向临时工程添加测试 CPU 并返回 PLC Software。"""
    from scdw.openness.tia_hardware import add_plc_device

    session, project, _ = temporary_tia_project
    order_number = os.getenv("SCDW_TEST_CPU", "OrderNumber:6ES7 214-1BG40-0XB0/V4.4")
    device_name = "SCDW_TEST_PLC"
    try:
        device, plc_software = add_plc_device(project, order_number, device_name, device_name)
    except Exception as exc:
        pytest.fail(
            "无法向临时工程添加测试 CPU。可通过 SCDW_TEST_CPU 指定本机硬件目录中的完整订货号。"
            f" 原始错误：{exc}"
        )
    session.register_device(device_name, device, plc_software)
    return session, project, plc_software
