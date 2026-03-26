from __future__ import annotations

from pprint import pprint
from typing import Any, Dict, Tuple

try:
    from .tia_project_builder_2 import (
        ProjectBuildConfig,
        add_device,
        create_project_and_device,
        fill_device_parameter,
        get_device_parameter,
        list_device_items,
        updata_device_parameter,
        add_publicapi_reference
    )
except ImportError:
    # 兼容直接执行：python openness/create_demo_2.py
    from tia_project_builder_2 import (  # type: ignore
        ProjectBuildConfig,
        add_device,
        create_project_and_device,
        fill_device_parameter,
        get_device_parameter,
        list_device_items,
        updata_device_parameter,
        add_publicapi_reference
    )


PUBLIC_API_DIR = r"E:\PlcProject\SoftWares\Siemens\Automation\Portal V17\PublicAPI\V17"
PROJECT_ROOT = r"E:\PlcProject\Code\PLC\tia_python_demo_output"
PROJECT_NAME = "DeviceParameterDemoProject"
CPU_ORDER_NUMBER = "OrderNumber:6ES7 510-1DJ01-0AB0/V2.0"

BASE_DEVICE_NAME = "PLC_1"
ADDED_DEVICE_NAME = "PLC_2"
RENAMED_DEVICE_NAME = "PLC_2_BATCH"
FINAL_DEVICE_NAME = "PLC_2_FINAL"


def print_title(title: str):
    print("\n" + "=" * 18 + f" {title} " + "=" * 18)


def _is_writable(parameter_info: Dict[str, Any] | None) -> bool:
    if not parameter_info:
        return False
    writable = parameter_info.get("writable")
    return writable is not False


def _select_batch_payload(parameter_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    parameters = parameter_snapshot["parameters"]
    payload: Dict[str, Any] = {}

    preferred_values = [
        ("Name", RENAMED_DEVICE_NAME),
        ("Comment", "由 create_demo_2.py 批量写入"),
        ("Author", "create_demo_2.py"),
    ]

    for parameter_name, target_value in preferred_values:
        parameter_info = parameters.get(parameter_name)
        if _is_writable(parameter_info):
            payload[parameter_name] = target_value

    if not payload:
        raise RuntimeError(
            "当前设备上没有找到适合演示 fill_device_parameter 的可写属性。"
        )

    return payload


def _select_single_update(
    parameter_snapshot: Dict[str, Any],
) -> Tuple[str, Any]:
    parameters = parameter_snapshot["parameters"]

    preferred_values = [
        ("Name", FINAL_DEVICE_NAME),
        ("Comment", "由 updata_device_parameter 单独写入"),
        ("Author", "create_demo_2.py single update"),
    ]

    for parameter_name, target_value in preferred_values:
        parameter_info = parameters.get(parameter_name)
        if _is_writable(parameter_info):
            return parameter_name, target_value

    for parameter_name, parameter_info in parameters.items():
        if not _is_writable(parameter_info):
            continue

        current_value = parameter_info.get("value")
        if isinstance(current_value, bool):
            return parameter_name, not current_value
        if isinstance(current_value, int) and not isinstance(current_value, bool):
            return parameter_name, current_value
        if current_value in (None, ""):
            return parameter_name, "updated_by_create_demo_2"
        return parameter_name, f"{current_value}_updated"

    raise RuntimeError("当前设备上没有找到适合演示 updata_device_parameter 的可写属性。")


def run_demo() -> Dict[str, Any]:
    cfg = ProjectBuildConfig(
        public_api_dir=PUBLIC_API_DIR,
        project_root=PROJECT_ROOT,
        project_name=PROJECT_NAME,
        cpu_order_number=CPU_ORDER_NUMBER,
        device_name=BASE_DEVICE_NAME,
        device_item_name=BASE_DEVICE_NAME,
        overwrite_existing_project_dir=True,
    )

    # 加载 DLL
    add_publicapi_reference(PUBLIC_API_DIR)

    tia = None
    project = None

    summary: Dict[str, Any] = {
        "project_name": PROJECT_NAME,
        "project_root": PROJECT_ROOT,
    }

    try:
        tia, project, device, _plc_software, project_dir = create_project_and_device(cfg)
        summary["project_path"] = project_dir

        print_title("1. 新建项目")
        print(f"项目路径: {project_dir}")
        print("默认 PLC 设备:")
        pprint({"name": str(device.Name), "type_identifier": CPU_ORDER_NUMBER})

        print_title("2. 查看默认 PLC 的 DeviceItems")
        base_items = list_device_items(project, BASE_DEVICE_NAME)
        pprint(base_items)
        summary["base_device_items"] = base_items

        print_title("3. 读取默认 PLC 的参数")
        base_parameters = get_device_parameter(
            project,
            BASE_DEVICE_NAME,
            parameter_names=["Name", "TypeIdentifier"],
        )
        pprint(base_parameters)
        summary["base_parameters"] = base_parameters

        print_title("4. 新增一个项目级设备")
        add_result = add_device(
            project=project,
            type_identifier=CPU_ORDER_NUMBER,
            device_name=ADDED_DEVICE_NAME,
            device_item_name=ADDED_DEVICE_NAME,
        )
        pprint(add_result)
        summary["add_device_result"] = add_result

        print_title("5. 读取新设备的全部属性快照")
        added_snapshot = get_device_parameter(project, ADDED_DEVICE_NAME)
        pprint(
            {
                "target": added_snapshot["target"],
                "parameter_count": len(added_snapshot["parameters"]),
            }
        )
        summary["added_snapshot_before"] = added_snapshot

        print_title("6. 批量填充设备参数")
        batch_payload = _select_batch_payload(added_snapshot)
        print("准备写入的参数:")
        pprint(batch_payload)
        fill_result = fill_device_parameter(
            project=project,
            device_name=ADDED_DEVICE_NAME,
            parameter_values=batch_payload,
            strict=False,
        )
        pprint(fill_result)
        summary["fill_result"] = fill_result

        current_device_name = ADDED_DEVICE_NAME
        if "Name" in fill_result["updated"]:
            current_device_name = str(fill_result["updated"]["Name"].get("value"))

        print_title("7. 单个更新设备参数")
        snapshot_after_fill = get_device_parameter(project, current_device_name)
        single_parameter_name, single_parameter_value = _select_single_update(
            snapshot_after_fill
        )
        print("准备单独更新的参数:")
        pprint({single_parameter_name: single_parameter_value})
        single_update_result = updata_device_parameter(
            project=project,
            device_name=current_device_name,
            parameter_name=single_parameter_name,
            parameter_value=single_parameter_value,
            strict=False,
        )
        pprint(single_update_result)
        summary["single_update_result"] = single_update_result

        final_device_name = current_device_name
        if (
            single_parameter_name == "Name"
            and "Name" in single_update_result["updated"]
        ):
            final_device_name = str(
                single_update_result["updated"]["Name"].get("value")
            )

        print_title("8. 回读最终参数")
        final_readback_names = ["Name", "TypeIdentifier"]
        if "Comment" in batch_payload:
            final_readback_names.append("Comment")
        if "Author" in batch_payload:
            final_readback_names.append("Author")

        final_parameters = get_device_parameter(
            project,
            final_device_name,
            parameter_names=final_readback_names,
        )
        pprint(final_parameters)
        summary["final_parameters"] = final_parameters

        project.Save()
        print_title("9. Demo 完成")
        print("工程已保存。")
        print(f"最终用于回读的设备名: {final_device_name}")

        return summary

    finally:
        try:
            if project is not None:
                project.Save()
        except Exception:
            pass

        try:
            if tia is not None:
                tia.Dispose()
        except Exception:
            pass


if __name__ == "__main__":
    demo_summary = run_demo()
    print_title("Demo 摘要")
    pprint(demo_summary)
