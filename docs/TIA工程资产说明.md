# TIA 工程资产说明

本轮仅移动目录，不修改任何 `.ap17`、数据库、索引或 PLC 控制逻辑。Python 源码未发现对这些目录的硬编码引用，因此统一存入 `assets/tia_projects/`。

| 工程目录 | 工程文件 | 目录体积（约） | 最近修改 | 判断 | 本轮处理 |
| --- | --- | ---: | --- | --- | --- |
| `test429` | `SCDW_PLC程序.ap17` | 878 KB | 2026-04-29 | 名称带测试编号，可能为导入验证工程 | 保留在 `assets/tia_projects/test429/` |
| `SCDW_风机控制` | 同名 `.ap17` | 1.0 MB | 2026-04-27 | 风机控制工程，资产基线候选 | 保留待确认 |
| `SCDW_PlcProject` | 同名 `.ap17` | 757 KB | 2026-04-27 | 通用 PLC 工程候选 | 保留待确认 |
| `SCDW_FanCtrl` | 同名 `.ap17` | 419 KB | 2026-04-27 | 风机控制历史版本候选 | 保留待确认 |
| `SCDW_FanControl` | 同名 `.ap17` | 428 KB | 2026-04-29 | 风机控制历史版本候选 | 保留待确认 |
| `SCDW_Project` | 同名 `.ap17` | 417 KB | 2026-06-22 | 修改时间最新，可能为通用工程 | 保留待确认 |
| `data/SCDW_Project` | 同名 `.ap17` | 868 KB | 2026-06-22 | 主 `.ap17` 哈希与上一项相同，附属索引不同 | 归档为 `archive/legacy_assets/tia_projects/SCDW_Project_data_copy/` |

`test429.rar` 已移至 `archive/legacy_assets/backups/`。由于压缩包未在本轮解压比对，不能断言其与目录 `test429/` 完全重复，故未删除。

后续应由熟悉业务的人员在 TIA Portal 中确认每个工程的 CPU、块、导出模板来源和最终基线，再决定是否精简历史工程。
