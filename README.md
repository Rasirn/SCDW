# SCDW：TIA Portal PLC 智能编程研究项目

SCDW 是实验室与四川电网合作的研究项目，探索将自然语言需求、XLSX 工程规格和真实 SimaticML 模板结合，由 AI 辅助生成西门子 TIA Portal PLC 程序，并通过 Openness 导入与编译验证。

> 本项目仍处于实验研究阶段。AI 生成的 LAD/SCL/XML 必须在非生产工程中完成 TIA Portal 编译、仿真和人工复核后方可使用。

## 当前功能范围

- TIA Portal Openness：创建工程、添加 PLC/模块、变量表、DB、SCL/LAD XML 导入、编译检查。
- MCP：以 stdio MCP Server 暴露工程操作工具。
- AI 对话：DeepSeek/OpenAI 兼容工具调用循环；保留 Claude 客户端封装。
- PLC 知识库：LLM 一次读取完整精简 metadata，显式选择能力 ID，再批量读取对应 SimaticML 片段或规则文档；不使用关键词评分或向量库。
- XLSX：读取设备、I/O、DB 和功能描述。
- UI：FastAPI + WebSocket 流式界面，可选 PyWebView 桌面窗口。

## 目录结构

```text
src/scdw/              正式 Python 代码
data/rag/raw/          仅离线蒸馏使用的原始 XML
data/rag/knowledge/    运行时精简知识目录、XML 片段和规则
data/xlsx/             XLSX 样例
data/generated/        AI 生成 XML
assets/tia_projects/   历史 TIA Portal 工程资产
archive/               历史实验、备份与待确认资产
tests/                 单元测试和 TIA 集成测试
docs/                  中文文档
```

完整约定见 [目录结构说明](docs/目录结构说明.md)。

## 环境要求与安装

- Python 3.10+，建议使用 `uv`。
- TIA Portal V17 与 Openness PublicAPI（执行 TIA 工具时需要）。
- Windows 与 pythonnet（TIA Openness 需要）。
- DeepSeek API Key（使用 AI 对话时需要）。

```powershell
uv sync
uv sync --extra ui --extra tia --extra llm --extra dev
```

配置环境变量（可写入本地 `.env`，不要提交凭据）：

```text
DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL=deepseek-chat
```

历史变量 `DEEPEEK_MODEL` 仍兼容，但新配置应使用 `DEEPSEEK_MODEL`。

## 启动方式

| 用途 | 命令 |
| --- | --- |
| CLI | `uv run python main.py` |
| MCP Server | `uv run python mcp_server.py` |
| MCP Client 工具列表 | `uv run python mcp_client.py` |
| Web 服务 | `uv run uvicorn scdw.frontend.app:app --app-dir src --host 127.0.0.1 --port 17788` |
| 桌面 UI | `uv run scdw-gui` |

根目录脚本保留为兼容入口；安装项目后也可使用 `scdw-cli`、`scdw-mcp`、`scdw-mcp-client` 和 `scdw-gui`。

## TIA Portal 与数据

- 默认 PublicAPI 路径为旧开发环境路径；调用 `init_tia_project` 时可通过 `api_dir` 显式指定本机安装目录。
- 原始 application XML 位于 `data/rag/raw/application/`，不会通过运行时工具提供给 LLM；发布知识位于 `data/rag/knowledge/`，AI 生成 XML 位于 `data/generated/rag/`。
- XLSX 样例位于 `data/xlsx/`。
- 历史 `.ap17` 工程位于 `assets/tia_projects/`，说明见 [TIA工程资产说明](docs/TIA工程资产说明.md)。

## 已知限制与排查

- 未安装 TIA Portal/pythonnet 时，调用 Openness 功能会失败；这是预期限制。
- 未安装 UI 可选依赖时，不要启动 Web/桌面入口。
- MCP Client 的 prompts/resources 历史接口尚未完整实现，CLI 补全相关功能可能不可用。
- LAD XML 应按 `get_plc_knowledge_catalog` → `get_plc_knowledge_items` 读取参考知识，并最终执行 `compile_check`；不应直接用于生产控制逻辑。

详细开发流程见 [开发指南](docs/开发指南.md)，当前调用关系见 [当前代码基线](docs/当前代码基线.md)。
# 已打开 TIA 的连接

可先启动 TIA 并打开工程，再使用 MCP 工具 `list_tia_processes` 与 `connect_to_open_tia` 附着。多实例时必须指定 PID；附着后的 `detach_tia_session` 不会关闭用户 TIA 或工程。真实诊断命令和限制见 [连接已有 TIA 测试报告](docs/连接已有TIA测试报告.md)。
