# Phase 4：DeepSeek V4 升级报告

## 已完成的代码升级

新增统一 Provider：默认 `deepseek-v4-pro`，可选 `deepseek-v4-flash`，Base URL 为 `https://api.deepseek.com`。旧 `deepseek-chat`、`deepseek-reasoner` 和拼写错误的模型变量会映射到 V4-Pro。API Key 保持原项目值，且只保存在 Provider 中；本文不记录其值。

Provider 将 system prompt 转为 system message，提供统一响应与 usage 数据，保留 reasoning_content，支持工具调用、JSON 本地校验、截断检测和有限重试。CLI 与 Web 均保留兼容入口，并增加 20 轮工具调用上限。新增离线 Provider 单元测试与 `requires_model` 真实 API 冒烟测试。

## 实测状态

2026-07-26 对 `deepseek-v4-flash` 的最小真实 HTTP 调用返回 HTTP 402（余额不足）。因此没有伪造 V4 模型成功、工具调用、MCP 或 TIA 联动结果。

升级前回归也无法运行：当前解释器为 Python 3.14.3，虚拟环境没有 pytest/openai/pydantic；`uv run pytest` 尝试编译 pydantic-core 时因缺少 MSVC `link.exe` 失败。该环境失败不能归因于 V4。

## 未完成的实测项

真实 API、MCP→TIA、15 案例完整基线，以及原始/预处理 XML 的 TIA 导入和编译统计，均受 API 余额与测试依赖环境阻塞。基线脚本已经建立案例集和 JSONL 落盘，待环境恢复后运行。
