# 编译失败模式

- `project_debug_experience`：XML 导入成功后仍可能因全局符号缺失、用户块不存在、背景 DB 不存在、参数签名或数据类型不一致而编译失败。
- `project_debug_experience`：以 TIA V17 编译诊断为最终验证，区分 XML 导入错误与 PLC 编译错误。
- `verified_v17_sample`：知识片段刻意不携带完整工程依赖，因此不能把片段可解析等同于 PLC 逻辑正确。
