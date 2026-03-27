# -*- coding: utf-8 -*-
"""
测试新版 tia_lad_builder 的各种复杂逻辑场景。
运行：python -m pytest tests/test_lad_builder.py -v
或：  python tests/test_lad_builder.py
"""
import json
import sys
import os
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from openness.tia_lad_builder import (
    build_lad_xml,
    lad_networks_from_json,
    LadNetwork,
    LadContact,
    LadBox,
    LadBranch,
    LadOutput,
)


def _validate_xml(xml_str: str) -> ET.Element:
    """验证 XML 可以正确解析，返回根元素。"""
    root = ET.fromstring(xml_str)
    assert root.tag == "Document"
    return root


def _count_parts(xml_str: str, part_name: str) -> int:
    """统计 XML 中某类 Part 的个数。"""
    root = ET.fromstring(xml_str)
    ns = {"flg": "http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4"}
    count = 0
    for part in root.iter("{http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4}Part"):
        if part.get("Name") == part_name:
            count += 1
    # 也检查非命名空间版本
    for part in root.iter("Part"):
        if part.get("Name") == part_name:
            count += 1
    return count


def test_basic_series():
    """测试基本串联：两个触点 → 一个线圈。"""
    networks_json = [
        {
            "title": "电机启动",
            "contacts": [
                {"var": "DB1.Start", "nc": False},
                {"var": "DB1.Stop", "nc": True},
            ],
            "outputs": [{"var": "DB1.Motor", "type": "Coil"}],
        }
    ]
    networks = lad_networks_from_json(networks_json)
    xml = build_lad_xml("FC_Test", "FC", 1, networks)
    _validate_xml(xml)
    assert "Contact" in xml
    assert "Coil" in xml
    assert "DB1" in xml
    assert "Start" in xml
    assert '<Negated Name="operand" />' in xml  # NC contact
    print("  ✓ test_basic_series")


def test_parallel_or():
    """测试并联 OR 逻辑。"""
    networks_json = [
        {
            "title": "多条件启动",
            "branches": [
                {"contacts": [{"var": "DB1.Remote", "nc": False}]},
                {"contacts": [{"var": "DB1.Local", "nc": False}]},
            ],
            "outputs": [{"var": "DB1.Motor", "type": "SCoil"}],
        }
    ]
    networks = lad_networks_from_json(networks_json)
    xml = build_lad_xml("FC_Test", "FC", 2, networks)
    _validate_xml(xml)
    assert "Or" in xml
    assert "SCoil" in xml
    assert "in1" in xml
    assert "in2" in xml
    print("  ✓ test_parallel_or")


def test_old_branch_format():
    """测试旧版并联格式（纯列表）的兼容性。"""
    networks_json = [
        {
            "title": "兼容测试",
            "branches": [
                [{"var": "DB1.A", "nc": False}],
                [{"var": "DB1.B", "nc": False}],
            ],
            "outputs": [{"var": "DB1.Out", "type": "Coil"}],
        }
    ]
    networks = lad_networks_from_json(networks_json)
    assert len(networks) == 1
    assert len(networks[0].branches) == 2
    xml = build_lad_xml("FC_Compat", "FC", 3, networks)
    _validate_xml(xml)
    print("  ✓ test_old_branch_format")


def test_timer_ton():
    """测试 TON 定时器。"""
    networks_json = [
        {
            "title": "延时启动",
            "contacts": [{"var": "DB1.Start", "nc": False}],
            "boxes": [
                {
                    "type": "TON",
                    "instance_db": "DB_Motor.Timer1",
                    "params": {"PT": "T#5s"},
                    "outputs_from": {"Q": "DB1.DelayDone"},
                }
            ],
            "outputs": [{"var": "DB1.Motor", "type": "Coil"}],
        }
    ]
    networks = lad_networks_from_json(networks_json)
    xml = build_lad_xml("FC_Timer", "FC", 10, networks)
    _validate_xml(xml)
    assert "TON" in xml
    assert "Instance" in xml
    assert "DB_Motor" in xml
    assert "Timer1" in xml
    assert "LiteralConstant" in xml  # T#5s 是常量
    assert "T#5s" in xml
    print("  ✓ test_timer_ton")


def test_compare_gt():
    """测试比较指令 Gt。"""
    networks_json = [
        {
            "title": "温度报警",
            "contacts": [{"var": "DB1.Enable", "nc": False}],
            "boxes": [
                {
                    "type": "Gt",
                    "params": {"in1": "DB1.Temperature", "in2": "80.0"},
                }
            ],
            "outputs": [{"var": "DB1.Alarm", "type": "SCoil"}],
        }
    ]
    networks = lad_networks_from_json(networks_json)
    xml = build_lad_xml("FC_Cmp", "FC", 11, networks)
    _validate_xml(xml)
    assert "Gt" in xml
    assert "80.0" in xml
    assert "LiteralConstant" in xml
    assert "Real" in xml  # 80.0 应推断为 Real
    print("  ✓ test_compare_gt")


def test_move():
    """测试 MOVE 赋值（无线圈输出）。"""
    networks_json = [
        {
            "title": "参数传递",
            "contacts": [{"var": "DB1.Enable", "nc": False}],
            "boxes": [
                {
                    "type": "Move",
                    "params": {"in": "DB1.SetPoint"},
                    "outputs_from": {"out1": "DB1.ActualValue"},
                }
            ],
            "outputs": [],
        }
    ]
    networks = lad_networks_from_json(networks_json)
    xml = build_lad_xml("FC_Move", "FC", 12, networks)
    _validate_xml(xml)
    assert "Move" in xml
    assert "DB1" in xml
    assert "SetPoint" in xml
    assert "ActualValue" in xml
    print("  ✓ test_move")


def test_math_add():
    """测试数学运算 Add。"""
    networks_json = [
        {
            "title": "加法",
            "contacts": [{"var": "DB1.Enable", "nc": False}],
            "boxes": [
                {
                    "type": "Add",
                    "params": {"in1": "DB1.Value1", "in2": "DB1.Value2"},
                    "outputs_from": {"out": "DB1.Sum"},
                }
            ],
            "outputs": [],
        }
    ]
    networks = lad_networks_from_json(networks_json)
    xml = build_lad_xml("FC_Add", "FC", 13, networks)
    _validate_xml(xml)
    assert "Add" in xml
    print("  ✓ test_math_add")


def test_edge_contact():
    """测试边沿触点。"""
    networks_json = [
        {
            "title": "上升沿",
            "contacts": [{"var": "DB1.Start", "nc": False, "edge": "P"}],
            "outputs": [{"var": "DB1.Trigger", "type": "SCoil"}],
        }
    ]
    networks = lad_networks_from_json(networks_json)
    xml = build_lad_xml("FC_Edge", "FC", 14, networks)
    _validate_xml(xml)
    assert "PContact" in xml
    print("  ✓ test_edge_contact")


def test_branch_with_box():
    """测试并联支路内带 Box。"""
    networks_json = [
        {
            "title": "复合逻辑",
            "branches": [
                {
                    "contacts": [{"var": "DB1.Manual", "nc": False}],
                    "boxes": [
                        {"type": "Gt", "params": {"in1": "DB1.Temp", "in2": "80.0"}}
                    ],
                },
                {"contacts": [{"var": "DB1.Auto", "nc": False}]},
            ],
            "outputs": [{"var": "DB1.Alarm", "type": "Coil"}],
        }
    ]
    networks = lad_networks_from_json(networks_json)
    xml = build_lad_xml("FC_Complex", "FC", 15, networks)
    _validate_xml(xml)
    assert "Or" in xml
    assert "Gt" in xml
    print("  ✓ test_branch_with_box")


def test_multiple_outputs():
    """测试多个输出线圈。"""
    networks_json = [
        {
            "title": "多输出",
            "contacts": [{"var": "DB1.Start", "nc": False}],
            "outputs": [
                {"var": "DB1.Motor1", "type": "Coil"},
                {"var": "DB1.Motor2", "type": "SCoil"},
            ],
        }
    ]
    networks = lad_networks_from_json(networks_json)
    xml = build_lad_xml("FC_Multi", "FC", 16, networks)
    _validate_xml(xml)
    assert "Motor1" in xml
    assert "Motor2" in xml
    print("  ✓ test_multiple_outputs")


def test_counter_ctu():
    """测试计数器 CTU。"""
    networks_json = [
        {
            "title": "计数",
            "contacts": [{"var": "DB1.CountPulse", "nc": False}],
            "boxes": [
                {
                    "type": "CTU",
                    "instance_db": "DB_Counter.CTU1",
                    "params": {"PV": "100"},
                    "outputs_from": {"Q": "DB1.CountDone", "CV": "DB1.CurrentCount"},
                }
            ],
            "outputs": [],
        }
    ]
    networks = lad_networks_from_json(networks_json)
    xml = build_lad_xml("FC_Counter", "FC", 17, networks)
    _validate_xml(xml)
    assert "CTU" in xml
    assert "DB_Counter" in xml
    assert "100" in xml
    print("  ✓ test_counter_ctu")


def test_complex_multi_network():
    """测试复杂多网络块（模拟真实工业场景）。"""
    networks_json = [
        # 网络1：启动条件 + 延时
        {
            "title": "启动延时",
            "contacts": [
                {"var": "DB_Motor.StartCmd", "nc": False},
                {"var": "DB_Motor.SafetyOK", "nc": False},
            ],
            "boxes": [
                {
                    "type": "TON",
                    "instance_db": "DB_Motor.StartDelay",
                    "params": {"PT": "T#3s"},
                }
            ],
            "outputs": [{"var": "DB_Motor.Running", "type": "SCoil"}],
        },
        # 网络2：停止条件
        {
            "title": "停止",
            "branches": [
                {"contacts": [{"var": "DB_Motor.StopCmd", "nc": False}]},
                {"contacts": [{"var": "DB_Motor.Fault", "nc": False}]},
            ],
            "outputs": [{"var": "DB_Motor.Running", "type": "RCoil"}],
        },
        # 网络3：速度设定
        {
            "title": "速度传递",
            "contacts": [{"var": "DB_Motor.Running", "nc": False}],
            "boxes": [
                {
                    "type": "Move",
                    "params": {"in": "DB_Motor.SpeedRef"},
                    "outputs_from": {"out1": "DB_Motor.SpeedOut"},
                }
            ],
            "outputs": [],
        },
        # 网络4：过温报警
        {
            "title": "过温报警",
            "contacts": [{"var": "DB_Motor.Running", "nc": False}],
            "boxes": [
                {
                    "type": "Gt",
                    "params": {"in1": "DB_Motor.Temp", "in2": "85.0"},
                }
            ],
            "outputs": [{"var": "DB_Motor.TempAlarm", "type": "SCoil"}],
        },
    ]
    networks = lad_networks_from_json(networks_json)
    assert len(networks) == 4

    xml = build_lad_xml("FC_MotorControl", "FC", 100, networks)
    root = _validate_xml(xml)

    # 验证 4 个 CompileUnit（每个有开启标签+关闭标签+CompositionName引用=3次）
    assert xml.count("SW.Blocks.CompileUnit") == 4 * 2  # 开始+结束标签

    # 验证各功能块存在
    assert "TON" in xml
    assert "Move" in xml
    assert "Gt" in xml
    assert "Or" in xml
    assert "SCoil" in xml
    assert "RCoil" in xml

    print("  ✓ test_complex_multi_network")


def test_constant_types():
    """验证各种常量类型的推断。"""
    from openness.tia_lad_builder import _is_constant_value, _access_constant

    # 时间常量
    assert _is_constant_value("T#5s")
    assert _is_constant_value("T#1m30s")
    assert _is_constant_value("LT#10ms")
    assert _is_constant_value("S5T#2s")

    # 数字常量
    assert _is_constant_value("100")
    assert _is_constant_value("80.0")
    assert _is_constant_value("-3.14")

    # 布尔常量
    assert _is_constant_value("TRUE")
    assert _is_constant_value("FALSE")

    # 十六进制
    assert _is_constant_value("16#FF")

    # 非常量（变量路径）
    assert not _is_constant_value("DB1.Value")
    assert not _is_constant_value("StartButton")
    assert not _is_constant_value("")

    # 数据类型推断
    xml = _access_constant(1, "T#5s")
    assert "Time" in xml
    xml = _access_constant(2, "80.0")
    assert "Real" in xml
    xml = _access_constant(3, "100")
    assert "Int" in xml
    xml = _access_constant(4, "TRUE")
    assert "Bool" in xml

    print("  ✓ test_constant_types")


def test_uid_uniqueness():
    """验证所有定义处 UId 的唯一性（Access/Part/Wire 的 UId 属性）。"""
    networks_json = [
        {
            "title": "N1",
            "contacts": [{"var": "A.B", "nc": False}],
            "boxes": [{"type": "TON", "instance_db": "X.T1", "params": {"PT": "T#1s"}}],
            "outputs": [{"var": "A.C", "type": "Coil"}],
        },
        {
            "title": "N2",
            "branches": [
                {"contacts": [{"var": "A.D", "nc": False}]},
                {"contacts": [{"var": "A.E", "nc": True}]},
            ],
            "outputs": [{"var": "A.F", "type": "SCoil"}],
        },
    ]
    networks = lad_networks_from_json(networks_json)
    xml = build_lad_xml("FC_UID", "FC", 99, networks)

    import re
    from collections import Counter
    # 只检查定义处的 UId（Access/Part/Wire/Instance 的直接 UId 属性）
    def_uids = re.findall(r'<(?:Access|Part|Wire|Instance)[^>]* UId="(\d+)"', xml)
    def_counts = Counter(def_uids)
    dupes = {k: v for k, v in def_counts.items() if v > 1}

    assert not dupes, f"定义处有重复 UId：{dupes}"
    print(f"  ✓ test_uid_uniqueness ({len(def_uids)} definition UIDs, all unique)")


if __name__ == "__main__":
    print("Testing tia_lad_builder (enhanced version)...")
    test_basic_series()
    test_parallel_or()
    test_old_branch_format()
    test_timer_ton()
    test_compare_gt()
    test_move()
    test_math_add()
    test_edge_contact()
    test_branch_with_box()
    test_multiple_outputs()
    test_counter_ctu()
    test_complex_multi_network()
    test_constant_types()
    test_uid_uniqueness()
    print("\n✅ All 14 tests passed!")
