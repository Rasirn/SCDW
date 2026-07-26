# Phase 5 界面与流式调用设计

当前界面将 CSS、DOM、WebSocket 与渲染逻辑集中在单个 HTML，使用 CDN Markdown 依赖，并且后端等待完整模型响应后才发送事件。窗口固定为窄尺寸，未提供主题、模式、取消和可恢复的回合标识。

本阶段改造采用静态 HTML 加本地 CSS/JavaScript 模块：主题令牌、布局、组件、渲染、WebSocket 和设置分离。浏览器只接收带 `turn_id` 的结构化事件，不直接把模型文本写入 `innerHTML`。

Provider 使用 DeepSeek 的 `stream=True` 读取真实 chunk，实时区分 `reasoning_content`、`content`、工具调用分片和 usage。工具调用按 choice/tool-call 索引聚合，完整后才执行 MCP。取消只终止模型流，不中断已启动的 TIA 工具。
