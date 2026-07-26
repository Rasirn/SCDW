# 连接已有 TIA 测试报告

独立验证命令：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe scripts\diagnose_tia_attach.py --process-id <PID>
```

本次代码提交环境已实际加载 Openness 并枚举到运行中的 TIA：PID `40584`，模式 `WithUserInterface`，工程为 `E:\temp\方炉子\10烧嘴\10烧嘴.ap17`。以该 PID 调用完整诊断脚本时，`Attach()` 等待 TIA 首次 Openness 授权而超时；没有把该状态记为通过，也没有关闭用户 TIA。请在 TIA 授权窗口确认后重试，再按以下场景复测：已打开工程、空 TIA 后打开工程、多实例指定 PID、切换工程、手动关闭 TIA 和断开附着会话。

测试工程必须为正式工程的副本。诊断脚本只附着并读取信息，退出时只断开 Openness 连接。
