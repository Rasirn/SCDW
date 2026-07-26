# Phase 4：旧方案基线报告

## 基线能力

`scripts/run_phase4_legacy_baseline.py` 固定 15 个案例，覆盖简单、中等、复杂、XLSX 和脱敏场景；保存每例 Prompt、RAG 结果、原始模型输出、提取的 XML 和 `results.jsonl`。支持模型、思考模式、案例、类别、重复次数、RAG、输出目录和最大输出 token 参数。

运行示例：

```powershell
uv run python scripts/run_phase4_legacy_baseline.py --model deepseek-v4-pro --thinking enabled --repeat 2
```

## 当前结果

尚无有效基线批次。真实 API 在最小冒烟请求时返回余额不足；因此 XML 提取率、解析率、TIA 导入/编译率、RAG 贡献、预处理器贡献和稳定性均为“未测”，不能用零值替代。

正式批次仅能在临时 TIA 工程上执行，且应在脚本完成 XML 初检后连接现有 Openness 导入与编译封装；不得人工改写模型生成 XML。每个失败须以 `MODEL_OUTPUT_TRUNCATED`、`XML_NOT_WELL_FORMED`、`TIA_IMPORT_FAILED` 等分类记录。
