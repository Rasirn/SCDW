# 用户块调用规则

- `verified_v17_sample`：用户块由 `CallInfo Name` 与 `BlockType` 关联；参数的 Name、Section、Type 必须与被调用块签名一致。
- `verified_v17_sample`：FB 调用包含合法 Instance；全局背景实例使用 `Scope="GlobalVariable"`。
- `verified_v17_sample`：每个参数绑定由 CallInfo Parameter、对应 Access 和 Wire 共同表达，输入与输出的连接方向不同。
- `verified_v17_sample`：当前 application 未提供用户 FB 多重实例调用样本；在取得调用方 Static 声明及 `Scope="LocalVariable"` 的 V17 样本前不发布该模式。
