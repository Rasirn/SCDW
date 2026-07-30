# SimaticML 块结构（V17）

- `verified_v17_sample`：PLC 块主体包含 `Interface` 与 `ObjectList` 中的 `SW.Blocks.CompileUnit`；每个 LAD Network 通常对应一个 CompileUnit。
- `verified_v17_sample`：CompileUnit 的 `NetworkSource/FlgNet` 由 `Parts` 和 `Wires` 组成。Network 还应在自身 `ObjectList` 中包含 Title 和 Comment 的完整 `MultilingualText` 结构。
- `project_debug_experience`：导入成功只说明 XML 被 TIA 接受，不代表符号、类型和调用关系可编译；最终必须以 TIA V17 编译结果为准。
