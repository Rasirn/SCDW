# TIA 会话生命周期

所有 `TiaPortal`、`Project`、`Device`、`PlcSoftware` 和编译对象均应在 `TiaOpennessExecutor` 的同一专用线程创建、访问和释放。线程外只传递字典、字符串及诊断数据。

每次刷新会重新枚举 `tia.Projects` 并重新扫描 PLC；工程身份变化时清空旧缓存并增加 `context_version`。用户关闭 TIA 后，下次刷新会清理会话，后续可重新附着。

写操作前必须调用 `ensure_current_context()`，从而避免长对话中把针对旧工程的操作写入用户刚切换的新工程。
