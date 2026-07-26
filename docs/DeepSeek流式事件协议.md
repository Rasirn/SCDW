# DeepSeek 流式事件协议

请求：`{"type":"query","content":"…","mode":"thinking|fast","turn_id":"…"}`；取消：`{"type":"cancel","turn_id":"…"}`。

服务端事件都携带 `turn_id`：`turn_start`、`reasoning_start`、`reasoning_delta`、`reasoning_end`、`answer_start`、`answer_delta`、`answer_end`、`tool_call_start`、`tool_result`、`usage`、`turn_end`、`cancelled` 与 `stream_error`。工具调用只在完整聚合 JSON 后执行。取消关闭模型流但不强制中断已经启动的 TIA 工具。
