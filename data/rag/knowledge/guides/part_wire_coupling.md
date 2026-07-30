# Part、Call 与 Wire 成组规则

- `verified_v17_sample`：Access、Part/Call 与 Wire 共同构成可理解的 LAD 网络；不得只复制 Part。
- `verified_v17_sample`：分叉可在一条 Wire 中由一个上游端口连接多个下游端口；并联起始支路共享同一 Powerrail Wire。
- `project_debug_experience`：LAD 元素存在代码生成顺序约束，但不要把导出 XML 中偶然的 Wire 文本顺序解释成动作优先级。
