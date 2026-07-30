# PLC 知识数据

- `raw/application/`：原有真实工程大 XML，只用于离线分析与来源追踪，运行时绝不发布。
- `raw/basic/`：旧基础样本的离线留档。
- `knowledge/catalog.json`：唯一运行时发布清单；不含关键词或相关度分数。
- `knowledge/distillation.json`：application 原始文件到知识项 ID 的蒸馏追踪。
- `knowledge/*/`：人工精简的 XML 片段和分级规则文档。

运行时先调用 `get_plc_knowledge_catalog`，由 LLM 显式选择 ID，再一次调用 `get_plc_knowledge_items` 批量读取正文。
