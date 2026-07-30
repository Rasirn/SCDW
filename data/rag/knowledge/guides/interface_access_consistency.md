# Interface 与 Access 一致性

- `verified_v17_sample`：`Scope="LocalVariable"` 的 Access 名称必须存在于当前块 Interface 的 Input、Output、InOut、Static 或 Temp 中。
- `verified_v17_sample`：FB 的状态保持数据和 IEC 定时器实例位于 Static；Input、Output、InOut 和 Temp 的 Section 语义不可互换。
- `project_debug_experience`：复制局部变量网络时要同时复制或重建所需 Interface 声明，并保持名称和数据类型一致。
