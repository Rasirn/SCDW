# Phase 2 测试基线报告

## 环境

- 操作系统：Windows 开发主机。
- Python：3.11.14（Conda `plc` 环境）。
- TIA/Openness：TIA Portal V17 PublicAPI V17，`Siemens.Engineering.dll` 文件版本 `1700.0.4302.1` 已加载。
- CPU：S7-1200 1214C，订货号 `6ES7 214-1BG40-0XB0/V4.4`。
- 工程：测试运行时创建 `data/generated/test_projects/SCDW_TEST_*`，无正式工程写入。

## 已执行结果

| 命令/测试 | 结果 | 耗时 |
| --- | --- | --- |
| `pytest tests/unit -m unit -q` | 7 通过，3 未选中 | 约 1.2 秒 |
| 创建/释放临时 TIA 工程 | 通过 | 26.6 秒 |
| 添加 CPU、标签表并编译 | 通过 | 36.5 秒 |
| 导入最小 SCL FC 并编译 | 通过 | 37.0 秒 |
| 导入 Main XML 并编译 | 真实失败，6 个缺失块诊断 | 38.8 秒 |
| 导入基础 LAD XML 并编译 | 真实失败诊断被捕获 | 37.6 秒 |
| MCP Client 列出工具 | 通过，18 工具 | 约 2 秒 |

测试 fixture 会关闭工程和 TIA Portal；全量 TIA 套件在本机需约 3 分钟，建议按单测或在 CI/人工终端运行。
