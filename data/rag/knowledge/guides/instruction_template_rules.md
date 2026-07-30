# 指令模板规则

- `verified_v17_sample`：指令 Version 必须沿用适用于目标 PLC 的 V17 样本；原本没有 Version 的元素不要随意添加。
- `verified_v17_sample`：`TemplateValue Type="Cardinality"` 表达端口数量，`Type="Type"` 表达类型；名称和值随指令而异。
- `verified_v17_sample`：数学指令样本包含 `AutomaticTyped`；Calc 的公式使用 `Equation`，变量以 IN1、IN2 等端口名出现。
- `project_debug_experience`：不要把一种指令的 TemplateValue、AutomaticTyped 或 Version 机械套用到另一种指令。
