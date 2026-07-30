# MCP 工具清单：PLC 知识库 Step 1

TIA 会话、硬件、标签、块导入和编译工具本步骤不重构。知识库公开工具调整如下：

| 状态 | 工具 | 说明 |
| --- | --- | --- |
| 新增 | `get_plc_knowledge_catalog` | 一次返回全部精简 metadata，不返回正文 |
| 新增 | `get_plc_knowledge_items` | 按多个显式 ID 批量返回 XML 片段或规则文档，保持请求顺序 |
| 停止公开 | `list_plc_templates` | 旧模板枚举流程 |
| 停止公开 | `search_plc_templates` | 旧关键词、top_k、score 流程 |
| 停止公开 | `get_plc_template` | 被批量 ID 读取替代 |
| 停止公开 | `import_template_block` | 精简片段不是完整可导入块 |

`data/rag/raw/application/` 不由运行时接口扫描或读取。`import_lad_xml` 与 `compile_check` 的实现和工具说明未在本步骤重构。
