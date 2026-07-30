# 导入失败模式

- `project_debug_experience`：常见导入失败来自 XML 不可解析、枚举值/属性不受 V17 接受、引用端口名错误、一个 Network 出现不合法的 Powerrail 或连接复用。
- `project_debug_experience`：诊断时保留 TIA 原始错误消息，并回到成组的 Access、Part/Call、Wire 与 Interface 上核对；不要仅修改单个 Part。
- `verified_v17_sample`：V17 样本本身是结构证据，但脱离其依赖后不保证可直接导入。
