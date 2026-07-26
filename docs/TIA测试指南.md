# TIA 测试指南

## 环境

- Windows、TIA Portal V17、PublicAPI V17、pythonnet。
- 默认 PublicAPI：`E:\PlcProject\SoftWares\Siemens\Automation\Portal V17\PublicAPI\V17`。
- 默认测试 CPU：`OrderNumber:6ES7 214-1BG40-0XB0/V4.4`；可通过 `SCDW_TEST_CPU` 覆盖。

## 命令

```powershell
python -m pytest tests/integration/tia -m tia -vv --timeout=55
```

测试在 `data/generated/test_projects/` 创建 `SCDW_TEST_*` 独立工程，不会修改 `assets/tia_projects/`。设置 `SCDW_KEEP_TIA_TEST_PROJECTS=1` 可保留失败现场；否则 fixture 在关闭 TIA 会话后清理目录。若工程被锁定，先关闭 TIA Portal 与残留测试 Python 进程后再删除临时目录。
# 已有工程附着回归

覆盖：已打开工程、空 TIA 后打开工程、切换工程、两个 TIA 实例、手动关闭 TIA 和 `detach_tia_session`。多实例必须传 PID，退出附着会话后确认 TIA UI 与工程仍保持打开。
