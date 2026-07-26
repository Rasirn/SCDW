# generated 目录

此目录存放 MCP Server 中 **AI 生成的 SimaticML XML 文件**（通过 `import_lad_xml` 或 `save_lad_xml` 工具产生）。

## 用途

- 每次调用 `import_lad_xml` / `save_lad_xml` 时，生成的 XML 自动保存到此处
- 如果导入 TIA Portal 失败，可在此找到对应的 `.xml` 文件进行**局部修改**后重新导入
- 文件名格式：`{block_name}_{YYYYMMDD_HHMMSS}.xml`

## 局部修改流程

1. 找到 `generated/{block_name}_{时间戳}.xml`
2. 用文本编辑器打开，定位错误所在的 `<SW.Blocks.CompileUnit>` 网络段
3. 修改对应的 `<NameCon>`、`<Access>` 或 `<Wire>` 元素
4. 复制修改后的 XML 内容，再次调用 `import_lad_xml` 导入

## 注意

- 此目录不纳入 git 版本控制（已在 .gitignore 中排除，如有）
- 确认导入成功后可删除过期文件，保持目录整洁
