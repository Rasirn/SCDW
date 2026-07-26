# tools 模块拆分设计

## 原始职责

原 `core/tools.py`（现 `scdw.mcp.tools`）混合了模型侧工具调度、MCP 注册、模块级 TIA 会话、工程/硬件/标签/块/编译操作、RAG、XLSX 和返回文本格式化。

## 本阶段实际拆分

| 职责 | 新模块 | 兼容方式 |
| --- | --- | --- |
| 模型侧 MCP 工具发现与执行 | `scdw.mcp.tool_manager` | `scdw.mcp.tools.ToolManager` 仍可导入新实现 |
| TIA 会话状态和资源释放 | `scdw.openness.session.TiaSessionManager` | `scdw.mcp.tools` 内部 `_session` 改为该类实例 |
| TIA 环境检查 | `scdw.openness.environment` | 测试 fixture 直接使用 |
| 统一异常 | `scdw.common.exceptions` | 新代码采用；旧 MCP 返回文本保持不变 |
| 通用结果类型 | `scdw.common.result` | 为后续服务层预留，不改变旧接口 |
| 编译结构化诊断 | `scdw.openness.diagnostics` | `CompileResult.messages` 保留，新增 diagnostics/error_count/warning_count |

## 暂未拆分

`scdw.mcp.tools.register_mcp_tools` 仍包含 18 个 MCP 装饰器。其嵌套函数闭包共享会话与 Openness 导入对象，直接一次性移动会提高回归风险。本阶段已先抽离跨入口组件并建立真实 TIA 回归测试；下一阶段可按 session、hardware、tag、block、compile、knowledge、xlsx 分批拆出注册函数。

## 风险与测试

会话替换风险由 TIA 工程创建、标签、SCL 导入、LAD XML 导入和编译测试覆盖；MCP 注册风险由工具列表和 schema 测试覆盖。
