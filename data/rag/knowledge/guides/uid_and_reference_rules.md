# UId 与引用规则

- `verified_v17_sample`：statements、CallInfo 和 operands 使用 UId 作为当前 CompileUnit 内的引用键；样本在不同 CompileUnit 中重复使用相同 UId。
- `verified_v17_sample`：UId 不要求跨整个块连续，也不携带业务语义。
- `verified_v17_sample`：嵌套的数组下标 Access 没有 UId，只有最外层 Access 被 `IdentCon` 引用。
- `verified_v17_sample`：`IdentCon` 引用 Access；`NameCon` 引用 Part 或 Call，并使用该元素的实际端口名。
