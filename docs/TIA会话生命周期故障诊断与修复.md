# TIA 会话生命周期故障诊断与修复

## 日志证据

运行 `20260726_234817_284_3ac826` 显示：首次创建在目录已存在后才启动 TIA；失败清理仅释放 Openness 对象，留下空的 UI TIA。第二次创建的 owned 会话未记录 PID，刷新时被当作失效会话；随后普通附着把原本 owned 的会话降级为 attached。MCP 主线程又直接使用先前在线程内创建的 `Project`，触发 STA 跨线程异常；最终对仍打开的工程目录执行删除，得到 WinError 32。

## 修复策略

1. `init_tia_project` 在关闭旧会话或启动 TIA 前执行目录与进程预检；目录冲突返回 `PROJECT_DIRECTORY_EXISTS`，外部 TIA 打开目标工程返回 `PROJECT_OPEN_IN_TIA`。
2. 上下文分离 `process_id` 和 `owned_process_id`。owned 会话以 Openness 线程内的 `tia.Projects` 探测为主，PID 仅作附加校验，不会因为 PID 暂时为空丢失所有权。
3. 启动 owned TIA 前后枚举进程，以差集记录新 PID。关闭 owned 会话时保存并关闭工程，按 `owned_process_id` 关闭对应 TIA 进程；attached 会话仍仅释放当前 Openness 连接。
4. 提供 `recover_owned_session`，只恢复原 owned PID，不会将 owned 静默降级成 attached。
5. 项目目录删除增加有限重试与 `PROJECT_FILES_LOCKED`，不再把 WinError 32 原样暴露为工具协议。
6. 所有已注册的项目/PLC 修改、导入、编译工具通过 `run_project_operation` 或 `run_plc_operation` 在同一 Openness executor 内完成，MCP 主线程不再持有 `Project` 或 `PlcSoftware`。
7. 同一用户回合维护破坏性工具账本；相同的创建、关闭、附着或新增 PLC 调用返回 `DUPLICATE_MUTATING_TOOL_CALL`，不会再次执行。

## 已知限制

本机当前未发现可用于验收的运行中 TIA，因此真实 TIA 创建、附着和连续五次循环只能待用户在具有 Openness 授权的环境中执行。单元测试覆盖了日志中的核心状态转换与预检分支；不会伪造真实 TIA 成功结果。
