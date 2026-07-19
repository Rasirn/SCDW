# SCDW 项目说明

> MAC Lab × 四川电网 — TIA Portal 智能编程助手
> 
> **用途：在 AI 对话中引入本文件作为上下文，以便 AI 了解项目全貌并持续开发。**

---

## 项目概述

本项目实现了一套「AI 驱动的 TIA Portal PLC 程序自动生成系统」。用户通过自然语言描述需要的 PLC 程序功能，AI 智能体自动查询模板库学习 SimaticML XML 语法，生成完整的 LAD 程序块 XML，并通过 TIA Portal Openness API 直接导入 TIA Portal，完成编译验证。

### 核心理念

- **AI 直接生成 XML**：AI 参考真实工程模板学习 SimaticML 语法，直接生成 XML（不再经由 JSON 中间描述）
- **RAG 增强质量**：关键词检索模板库，保证 AI 见过合法结构再生成
- **持久化调试**：每次生成/导入都自动保存 XML 到 `RAG/generated/`，出错后可局部修改再导入

---

## 架构

```
用户自然语言需求
        ↓
  [Chat UI / Claude API]   core/chat.py, core/claude.py
        ↓  MCP 工具调用
  [MCP Server]             mcp_server.py  (FastMCP)
        ↓  注册工具
  [tools.py]               core/tools.py  (17 个工具)
    ├── TIA Portal 操作 ────→ openness/tia_*.py (Openness API)
    └── RAG 检索 ───────────→ RAG/rag_retriever.py
                                    ↓
                             RAG/templates/*/  模板库
```

---

## 目录结构

```
SCDW/
├── main.py                 # 启动入口（CLI / MCP Server 模式）
├── mcp_server.py           # FastMCP 服务，暴露工具给 Claude
├── mcp_client.py           # MCP 客户端（调试/测试用）
├── PROJECT.md              # 本文件（项目总览）
│
├── core/                   # 核心逻辑
│   ├── chat.py             # 通用对话管理
│   ├── claude.py           # Claude API 封装（MCP 工具调用）
│   ├── cli.py / cli_chat.py# 命令行对话入口
│   ├── deepseek.py         # DeepSeek API 封装
│   └── tools.py            # 所有 MCP 工具注册（17 个工具）
│
├── openness/               # TIA Portal Openness API 封装
│   ├── tia_core.py         # 连接/会话管理
│   ├── tia_hardware.py     # 硬件配置（设备/CPU/模块）
│   ├── tia_tags.py         # PLC 变量表管理
│   ├── tia_blocks.py       # 程序块操作（含 import_lad_xml_block）
│   ├── tia_compiler.py     # 编译检查
│   └── tia_project_builder*.py  # 高层工程构建器
│
├── RAG/                    # 检索增强生成系统
│   ├── rag_retriever.py    # TemplateLibrary 单例 + save_generated_xml
│   ├── README.md           # 模板库使用说明（含扩充方法）
│   ├── generated/          # AI 生成 XML 持久化目录
│   └── templates/          # 模板库（多分类）
│       ├── application/    # 完整工程级模板（10 个 V17 导出 XML）
│       │   └── meta.json   # 描述 + 关键词
│       └── basic/          # 基础单指令模板（待扩充）
│
├── frontend/               # Web 前端（Flask + 原生 JS）
│   ├── app.py              # Flask 服务
│   ├── chat_bridge.py      # AI 对话桥接（含系统提示词）
│   ├── main_gui.py         # 桌面 GUI（tkinter）
│   └── static/index.html   # 聊天界面
│
├── data/                   # 数据读取
│   ├── xlsx_reader.py      # Excel 读取（设备清单/IO表等）
│   └── create_template.py  # 模板创建辅助工具
│
└── tests/                  # 测试
```

---

## MCP 工具清单

| # | 工具名 | 功能 | 是否需要 TIA 连接 |
|---|--------|------|-----------------|
| 1 | `connect_tia` | 连接 TIA Portal 实例 | 否（建立连接用） |
| 2 | `disconnect_tia` | 断开连接 | 是 |
| 3 | `list_devices` | 列出项目中所有设备 | 是 |
| 4 | `add_device` | 添加 PLC 设备 | 是 |
| 5 | `add_io_module` | 添加 IO 模块 | 是 |
| 6 | `add_plc_block` | 添加程序块（空块） | 是 |
| 7 | `list_blocks` | 列出程序块 | 是 |
| 8a | `import_lad_xml` | 导入 AI 生成 XML + 自动持久保存 | 是 |
| 8b | `save_lad_xml` | 保存并验证 XML（不导入） | 否 |
| 9 | `compile_check` | 编译验证 | 是 |
| 10 | `add_plc_tag` | 添加 PLC 变量 | 是 |
| 11 | `list_plc_tags` | 列出变量表 | 是 |
| 12 | `read_excel_data` | 读取 Excel 数据 | 否 |
| 13 | `list_plc_templates` | 列出模板库（支持 category 过滤） | 否 |
| 14 | `search_plc_templates` | 关键词检索模板（支持 category 过滤） | 否 |
| 15 | `get_plc_template` | 获取模板完整 XML | 否 |
| 16 | `import_template_block` | 直接导入现成模板 | 是 |
| 17 | `get_session_status` | 获取当前会话状态 | 否 |

---

## AI 生成 PLC 程序的工作流

```
1. search_plc_templates(query=<功能描述>)
        ↓ 找到相关模板
2. get_plc_template(name=<模板名>, full=True)
        ↓ 读取完整 XML，学习结构
3. AI 基于模板修改/生成目标块 XML
        ↓
4. import_lad_xml(device_name, block_name, xml_content)
        ↓ 自动保存到 RAG/generated/
5. compile_check(device_name)
        ↓ 返回编译结果
6. 如有错误：参考 RAG/generated/ 中的保存文件局部修改
```

---

## 当前进度

### 已完成

- [x] **基础 TIA 操作**：连接/断开、设备/模块添加、程序块操作、编译检查、变量表管理
- [x] **Excel 数据读取**：从 IO 表/设备清单读取数据供 AI 生成程序用
- [x] **AI 直接生成 XML**：完全基于模板参考 + AI 直接生成 SimaticML XML（已删除旧的 JSON→XML 路径）
- [x] **RAG 模板库（多分类）**：
  - `templates/application/`：10 个真实工程导出模板
  - `templates/basic/`：占位，待扩充基础指令模板
  - `meta.json` 元数据支持（描述 + 关键词）
  - 支持按 category 过滤检索
- [x] **XML 持久化**：`import_lad_xml` 和 `save_lad_xml` 均保存到 `RAG/generated/`，重启不丢失
- [x] **Web 前端**：Flask 聊天界面 + 系统提示词包含完整 AI 生成工作流说明
- [x] **MCP Server**：FastMCP 暴露 17 个工具供 Claude 调用

### 待完成 / 后续方向

- [ ] **basic 分类扩充**：添加基础单指令模板（定时器、计数器、比较、数学运算等），让 AI 学到最小 XML 结构
- [ ] **import_template_block 的 category 支持**：工具第 16 号目前未传 category 参数到 get_template_xml
- [ ] **向量检索**：当前为关键词频率检索，可升级为 sentence-transformers 语义检索（需求量大时）
- [ ] **Excel → 程序自动生成**：读取 IO 表后一键生成对应程序块（用 data/ 配合 RAG）
- [ ] **硬件配置自动化**：从设备清单自动添加 CPU + IO 模块
- [ ] **多轮修改**：AI 生成 → 编译报错 → AI 读取保存的 XML 局部修改 → 再导入（自动化修复循环）

---

## 依赖与运行

### 环境要求
- Python 3.10+
- TIA Portal V17（用于 Openness 功能）
- `siemens.engineering` Python 包（随 TIA Openness 安装）
- `anthropic` / `openai`（AI API）
- `fastmcp`（MCP Server）
- `flask`（Web 前端）

### 启动方式
```bash
# MCP Server 模式（供 Claude Desktop 使用）
python mcp_server.py

# CLI 对话模式
python main.py

# Web 前端
python frontend/app.py
```

### MCP Server 配置（Claude Desktop）
在 `claude_desktop_config.json` 中：
```json
{
  "mcpServers": {
    "tia-portal": {
      "command": "python",
      "args": ["e:\\PlcProject\\Code\\PLC\\SCDW\\mcp_server.py"]
    }
  }
}
```

---

## 关键设计决策记录

1. **SimaticML XML 只能 AI 直接生成**：格式复杂（嵌套 UID、GID、多层属性），无法通过简单规则从 JSON 生成。现在 AI 参考真实模板学语法后直接输出 XML，成功率大幅提升。
2. **RAG 模板用关键词检索**：项目规模较小（< 100 个模板），关键词 TF-IDF 式匹配足够，不引入向量数据库依赖。
3. **XML 持久化**：TIA 导入操作的 XML 用完即抛会导致出错时无法调试，全部改为保存到 `RAG/generated/` 目录。
4. **多分类模板库**：将 `application`（完整工程）与 `basic`（单指令片段）分开，便于 AI 在不同场景选择合适粒度的参考。

---

## 维护说明

每次对项目有较大修改时，请更新本文件的「当前进度」章节，说明已完成什么、新增了什么结构、还有哪些待做。在新的 AI 对话开始时引入本文件，AI 可以快速理解项目状态并继续开发。
