# Phase 0～Phase 1 代码整理报告

## 整理前主要问题

- 正式代码、数据、模板、生成 XML、TIA 工程和 Notebook 混放在根目录。
- `RAG/`、`core/`、`openness/`、`frontend/` 以历史目录名组织，包路径和运行目录耦合。
- 根目录入口包含业务逻辑，`pyproject.toml` 的项目名和依赖不完整。
- README 仅有一句说明；旧项目说明与实际实现存在差异。

## 实际调整

- 正式代码迁移至 `src/scdw/`，分为 `cli`、`llm`、`mcp`、`openness`、`rag`、`xlsx`、`frontend` 和 `common`。
- 添加 `common.paths` 与 `common.config`，RAG 模板/生成物、XLSX、TIA 资源不再通过当前工作目录定位。
- 模板迁至 `data/rag/`，生成 XML 迁至 `data/generated/rag/`，XLSX 迁至 `data/xlsx/`。
- TIA 工程集中到 `assets/tia_projects/`；历史副本和压缩备份归档到 `archive/legacy_assets/`；Notebook 归档到 `archive/experiments/`。
- 根目录 `main.py`、`mcp_server.py`、`mcp_client.py` 改为兼容入口。
- 新增基础单元测试及完整中文文档。

## 删除与归档

- 删除：仅删除 Python 缓存目录；未删除任何 TIA 工程、XML 模板、XLSX 或压缩备份。
- 归档：Notebook、旧 RAG 说明、`test429.rar`、`data/SCDW_Project` 工程副本。
- 保留待确认：全部 `assets/tia_projects/` 工程，以及被外部进程锁定而未移动的 `data/PLC程序完整.xlsx`。

## 入口、导入与依赖变化

- 包内导入统一为 `scdw.*`；RAG 工具不再动态修改 `sys.path` 以导入旧 `RAG` 目录。
- 根目录入口保留原命令兼容性，并支持 `--help`。
- `pyproject.toml` 配置 `src` 包布局、CLI scripts 和可选依赖组；新增源码实际导入的 `openai`、`openpyxl`、`pydantic`，并已执行 `uv lock` 更新锁文件。

## 本轮未处理

- 未变更 API Key、模型选择、RAG 算法、LAD XML 生成逻辑、PLC 控制逻辑或 TIA 工程内部内容。
- 未重构 `scdw.mcp.tools` 的大文件；只迁移并修正其路径依赖。
- 未在缺少 TIA Portal、MCP、OpenAI、FastAPI 等依赖的当前环境中伪造端到端验证。
- `uv sync --extra dev` 在当前执行窗口超时，故 MCP Server 与前端模块导入仍需在完成 `uv sync` 的环境复验。

## 下一阶段建议

1. 在具备网络与 TIA V17 的 Windows 环境执行 `uv lock`、`uv sync --all-extras` 和 MCP/TIA 集成测试。
2. 确认唯一工程基线和模板来源。
3. 为 XML 修复、XLSX 解析和 MCP 参数增加更多无 TIA 单元测试。
4. 再逐步拆分 `mcp.tools` 的会话、块导入和工程创建职责。
