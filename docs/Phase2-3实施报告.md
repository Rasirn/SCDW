# Phase 2–3 实施报告

## 完成内容

- 配置 pytest 标记、超时和测试目录；`uv.lock` 已更新，Conda `plc` 环境已以 editable 方式安装基础、开发和 UI 依赖。
- 新增真实 TIA fixture，所有工程均为临时 `SCDW_TEST_*` 工程。
- 建立路径、配置、RAG、XML 预处理、MCP 注册、TIA 工程/标签/块/编译基线。
- 抽离工具调度、会话管理、环境检查、异常、结果和编译诊断类型。

## 已知遗留

- 巨型 `scdw.mcp.tools.register_mcp_tools` 仍是兼容注册层，尚未拆为多个 decorator 注册模块。
- `Main.xml` 依赖多个应用块，空工程中编译失败；已记录为基线，不修改业务模板。
- 真实模型调用和 WebSocket 全链路未调用，避免消耗 API 或触发人工交互。
- 首轮 fixture 清理顺序遗留了 5 个历史 `SCDW_TEST_*` 临时目录；已修复为会话关闭后清理。后续复跑不再新增目录，既有目录可在确认无 TIA 进程后人工删除。
