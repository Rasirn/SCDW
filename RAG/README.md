# RAG 模板检索系统

本目录实现了 TIA Portal PLC 程序的 RAG（检索增强生成）模板库。AI 智能体通过检索真实博途工程导出的 SimaticML XML 文件，学习 XML 语法和代码风格，然后生成或修改 PLC 程序块 XML。

---

## 目录结构

```
RAG/
├── rag_retriever.py        # 核心检索模块（TemplateLibrary 单例）
├── generated/              # AI 生成 XML 的持久化存储目录
│   └── README.md           # 说明文件
└── templates/              # 模板库根目录（多分类结构）
    ├── application/        # 完整工程级模板（来自真实博途工程导出）
    │   ├── meta.json       # 模板元数据（描述、关键词）
    │   ├── Main.xml
    │   ├── 烧嘴控制.xml
    │   └── ...             # 共 10 个模板
    └── basic/              # 基础单指令模板（待扩充）
        └── meta.json       # 分类元数据（含扩充说明）
```

---

## 模板分类说明

| 分类 | 说明 | 模板数量 |
|------|------|--------|
| `application` | 完整工程应用模板，来自真实博途 V17 工程导出，结构复杂，包含完整 DB/FB/FC 引用 | 10 |
| `basic` | 基础单指令/单网络模板，展示最小可运行的 XML 结构（待扩充） | 0 |

---

## 拓展模板库

### 方式一：向已有分类添加模板

1. 将新的博途 XML 导出文件放入对应分类目录，如 `templates/application/新模板.xml`
2. 编辑该目录的 `meta.json`，在根对象中添加条目：
   ```json
   "新模板": {
     "description": "模板用途的简短描述（20字以内）",
     "keywords": ["关键词1", "关键词2", "功能描述词"]
   }
   ```
3. `TemplateLibrary` 单例会在下次调用时自动重新扫描（无需重启服务）。

### 方式二：新建模板分类

1. 在 `templates/` 目录下新建子目录，如 `templates/motion/`
2. 在新目录下创建 `meta.json`（可以复制 `basic/meta.json` 为模板）：
   ```json
   {
     "_category_desc": "运动控制相关模板（S7-1500T Motion）",
     "模板文件名（不含.xml）": {
       "description": "模板描述",
       "keywords": ["关键词"]
     }
   }
   ```
3. 将 XML 文件放入该目录
4. 系统自动发现新分类，无需修改代码

### 方式三：从博途工程导出 XML

1. 在博途 V17/V18 中选择程序块 → 右键 → 导出
2. 选择 SimaticML 格式导出为 `.xml`
3. 按方式一/二添加到对应分类

---

## API 使用

### Python API（`rag_retriever.py`）

```python
import sys
sys.path.insert(0, 'RAG')
from rag_retriever import (
    list_templates,       # 列出所有/指定分类的模板
    list_categories,      # 列出所有分类及描述
    search_templates,     # 关键词检索
    get_template_xml,     # 获取模板 XML 内容
    save_generated_xml,   # 保存 AI 生成的 XML
    get_generated_dir,    # 获取 generated/ 目录路径
)

# 列出所有分类
cats = list_categories()
# {'application': '完整工程应用模板...', 'basic': '...'}

# 列出 application 分类的所有模板
tpls = list_templates(category='application')

# 关键词检索（返回 score 排序的列表）
results = search_templates('烧嘴控制', top_k=3)

# 获取模板 XML（full=True 返回完整内容，否则截断到 max_chars 字符）
xml = get_template_xml('烧嘴控制', full=True)

# 保存 AI 生成的 XML
saved_path = save_generated_xml('MyBlock', xml_content)
# → 保存到 RAG/generated/MyBlock_20241215_143022.xml
```

### MCP 工具（供 AI 智能体调用）

| 工具名 | 说明 |
|--------|------|
| `list_plc_templates(category="")` | 列出模板，可按分类过滤 |
| `search_plc_templates(query, top_k, category="")` | 关键词检索，可限定分类 |
| `get_plc_template(name, full=False)` | 获取模板 XML |
| `import_lad_xml(device_name, block_name, xml_content)` | 导入 XML 到 TIA Portal，自动持久保存 |
| `save_lad_xml(block_name, xml_content)` | 保存并验证 XML（不导入） |

---

## AI 生成 XML 的局部修改流程

当 `import_lad_xml` 失败时，可按以下步骤局部修改：

1. 从返回消息中找到保存路径（如 `RAG/generated/MyBlock_20241215_143022.xml`）
2. 用文本编辑器打开，定位出错的 `<SW.Blocks.CompileUnit>` 网络段
3. 参考 `templates/application/` 中的模板，修正对应 XML 节点
4. 复制修改后的完整 XML 内容，再次调用 `import_lad_xml`

---

## meta.json 格式参考

```json
{
  "_category_desc": "分类描述（显示在列表工具的分组标题旁）",
  "_how_to_add": "可选，说明字段，不影响检索",
  "模板文件名（不含.xml后缀）": {
    "description": "模板功能描述",
    "keywords": ["关键词1", "中文关键词", "功能域"]
  }
}
```

所有以 `_` 开头的键被忽略（不作为模板条目处理）。
