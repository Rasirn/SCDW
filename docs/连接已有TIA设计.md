# 连接已有 TIA 设计

系统支持附着到用户已经启动的 TIA Portal V17。`list_tia_processes` 每次均重新枚举进程；`connect_to_open_tia` 按 PID 附着，并扫描已打开工程及 PLC Software。

多 TIA 实例或多工程均不会默认选择第一项。请分别传入 `process_id` 或 `project_path`，也可使用 `select_tia_project` 明确选择工程。

附着模式不拥有 TIA 及工程：`detach_tia_session` 只调用 Openness 的 `Dispose()`，不会保存、关闭工程或关闭 TIA UI。AI 新建工程使用 `init_tia_project`，属于拥有模式，可按原有语义保存和关闭。

首次附着时，TIA 可能显示 Openness 访问授权对话框，必须由用户确认。系统只能感知已打开工程和 PLC，不会感知当前选中的 LAD 网络或编辑器元素。
