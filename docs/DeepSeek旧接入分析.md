# DeepSeek 旧接入分析

快照日期：2026-07-26。

旧实现位于 `src/scdw/llm/deepseek.py`，直接使用 OpenAI Chat Completions SDK。默认模型为 `deepseek-chat`，并兼容拼写错误的 `DEEPEEK_MODEL`。系统提示词通过顶层 `system` 参数传递，不符合 Chat Completions 的 messages 格式。

旧实现没有统一响应对象、超时与有限重试、`finish_reason` 截断拒绝、JSON Output 或本地 Schema 校验。工具调用仅保存 `content` 和 `tool_calls`，未保存思考模式返回的 `reasoning_content`；因此连续工具调用不能可靠地恢复完整历史。

旧 Prompt 的主要来源是 `frontend/chat_bridge.py` 中的 `_SYSTEM_PROMPT`；CLI 未注入系统提示词。RAG 通过 `search_plc_templates` / `get_plc_template` MCP 工具提供，XML 修复逻辑在 `openness/tia_blocks.py` 的导入函数内部执行。
