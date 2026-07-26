# MCP 工具兼容清单

本阶段所有工具名称、必填参数、默认值和文字返回格式保持不变；仅将内部会话状态改为 `TiaSessionManager`。

| 分类 | 工具 |
| --- | --- |
| 会话 | `init_tia_project`、`close_tia_session` |
| 硬件 | `add_plc_to_project`、`add_hardware_module` |
| 标签/DB | `create_plc_tag_table`、`create_global_db` |
| 块 | `import_scl_block`、`import_lad_xml`、`save_lad_xml`、`import_lad_xml_from_file`、`delete_plc_block`、`import_template_block` |
| 编译 | `compile_and_save`、`compile_check` |
| XLSX | `read_project_spec_from_xlsx` |
| RAG | `list_plc_templates`、`search_plc_templates`、`get_plc_template` |

实际 MCP Client 已通过 stdio 列出上述 18 个工具。不存在接口破坏或新增废弃包装。
