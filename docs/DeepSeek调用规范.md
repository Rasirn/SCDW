# DeepSeek 调用规范

默认模型为 `deepseek-v4-pro`，快速对照模型为 `deepseek-v4-flash`。新业务代码必须使用 `scdw.llm.providers.deepseek.DeepSeekProvider`；旧 `Deepseek` 类只作为 CLI 和 Web 的兼容入口。

系统提示词必须作为第一条 `role=system` 消息传递。工具调用后的 assistant 消息必须保留 `content`、`tool_calls` 和 `reasoning_content`（如存在），再追加 tool 消息后发送完整历史。单次会话最多执行 20 轮工具调用。

思考模式使用 `thinking={"type":"enabled"}`，关闭时使用 `{"type":"disabled"}`。思考模式不以 temperature 控制；非思考的 XML/代码基线使用 `temperature=0`。XML 任务必须配置足够的 `max_tokens` 并检查 `finish_reason`；`length` 或 `max_tokens` 视为 `MODEL_OUTPUT_TRUNCATED`，不得导入 TIA。

结构化结果使用 `generate_json`，它请求 `json_object` 格式、检查空结果、执行 `json.loads` 和调用方提供的本地 Schema 校验，并把校验错误反馈给有限次重试。认证与参数错误不可重试；超时、限流和 5xx 使用指数退避有限重试。日志不得输出 API Key 或完整 reasoning_content。
