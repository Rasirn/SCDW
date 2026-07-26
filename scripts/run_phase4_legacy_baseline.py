"""运行 Phase 4 旧 XML 方案基线（不会修改正式 TIA 工程）。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from scdw.common.exceptions import LlmError, LlmOutputTruncatedError
from scdw.common.paths import GENERATED_DIR, PROJECT_ROOT
from scdw.llm.providers.deepseek import DEFAULT_MODEL, DeepSeekProvider
from scdw.rag.retriever import search_templates

CASES = [
    ("basic_series", "simple", "生成一个常开触点驱动线圈的 LAD 块"),
    ("basic_parallel", "simple", "生成两个并联触点驱动线圈的 LAD 块"),
    ("basic_move", "simple", "生成使用 MOVE 的 LAD 块"),
    ("motor_start_stop", "medium", "生成电机启停自保持 LAD 块"),
    ("alarm", "medium", "生成报警置位与复位 LAD 块"),
    ("fan_control", "medium", "生成风机联锁控制 LAD 块"),
    ("burner_control", "complex", "生成烧嘴控制 LAD 块"),
    ("pid_parameter", "complex", "生成 PID 参数控制 LAD 块"),
    ("signal_conversion", "medium", "生成输入信号转换 LAD 块"),
    ("setpoint_transfer", "medium", "生成设定值传递 LAD 块"),
    ("array_access", "complex", "生成数组访问 LAD 块"),
    ("math_calculation", "medium", "生成数学计算 LAD 块"),
    ("multi_network", "complex", "生成多网络 LAD 块"),
    ("xlsx_complete", "xlsx", "依据完整 XLSX 需求生成对应 LAD XML"),
    ("desensitized_fan", "desensitized", "生成脱敏风机燃气控制 LAD 块"),
]


def extract_xml(text: str) -> str | None:
    match = re.search(r"<Document\\b[\\s\\S]*?</Document>", text)
    return match.group(0) if match else None


def write(path: Path, value: str | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, dict) else value, encoding="utf-8")


def run_case(provider: DeepSeekProvider, item: tuple[str, str, str], output: Path, args: argparse.Namespace) -> dict:
    case_id, category, requirement = item
    started = time.perf_counter()
    case_dir = output / "cases" / case_id
    retrieved = search_templates(requirement, top_k=3) if args.rag else []
    references = "\n".join(f"- {r['name']}: {r.get('summary', '')}" for r in retrieved)
    prompt = ("按当前旧方案直接输出完整 SimaticML LAD XML。不要解释、不要 Markdown。\n"
              f"需求：{requirement}\nRAG 检索结果：\n{references}")
    write(case_dir / "prompt.txt", prompt)
    result = {"run_id": args.run_id, "case_id": case_id, "category": category,
              "timestamp": datetime.now(timezone.utc).isoformat(), "model": args.model,
              "thinking_enabled": args.thinking == "enabled", "reasoning_effort": args.reasoning_effort,
              "temperature": 0 if args.thinking == "disabled" else None, "max_output_tokens": args.max_output_tokens,
              "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(), "retrieved_cases": retrieved,
              "api_request_count": 0, "raw_xml_well_formed": False, "xml_extracted": False,
              "preprocessor_applied": False, "manual_intervention": False, "failure_stage": None,
              "failure_code": None, "raw_tia_import_success": None, "raw_compile_success": None,
              "processed_tia_import_success": None, "processed_compile_success": None}
    try:
        response = provider.chat([{"role": "user", "content": prompt}], thinking=args.thinking == "enabled",
                                 reasoning_effort=args.reasoning_effort, temperature=0, max_tokens=args.max_output_tokens, model=args.model)
        result.update({"api_request_count": 1, "prompt_tokens": response.usage.prompt_tokens,
                       "completion_tokens": response.usage.completion_tokens, "total_tokens": response.usage.total_tokens,
                       "finish_reason": response.finish_reason, "output_characters": len(response.content or ""), "output_truncated": False})
        write(case_dir / "raw_model_response.txt", response.content or "")
        xml = extract_xml(response.content or "")
        if not xml:
            result.update(failure_stage="xml_extract", failure_code="MODEL_NO_XML_FOUND")
        else:
            result["xml_extracted"] = True
            write(case_dir / "raw.xml", xml)
            try:
                ET.fromstring(xml); result["raw_xml_well_formed"] = True
            except ET.ParseError as exc:
                result.update(failure_stage="xml_parse", failure_code="XML_NOT_WELL_FORMED", notes=str(exc))
    except LlmOutputTruncatedError as exc:
        result.update(api_request_count=1, output_truncated=True, failure_stage="model", failure_code="MODEL_OUTPUT_TRUNCATED", notes=str(exc))
    except LlmError as exc:
        result.update(failure_stage="api", failure_code=type(exc).__name__, notes=str(exc))
    finally:
        result["latency_seconds"] = round(time.perf_counter() - started, 3)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 4 旧 XML 方案基线")
    parser.add_argument("--model", default=DEFAULT_MODEL); parser.add_argument("--thinking", choices=["enabled", "disabled"], default="enabled")
    parser.add_argument("--case"); parser.add_argument("--category"); parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--rag", action=argparse.BooleanOptionalAction, default=True); parser.add_argument("--output-dir")
    parser.add_argument("--max-output-tokens", type=int, default=16384); parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--resume", action="store_true"); parser.add_argument("--keep-failed-project", action="store_true")
    args = parser.parse_args(); args.run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    output = Path(args.output_dir) if args.output_dir else GENERATED_DIR / "benchmarks" / "phase4" / args.run_id
    selected = [x for x in CASES if (not args.case or x[0] == args.case) and (not args.category or x[1] == args.category)]
    if not selected: parser.error("未找到匹配的基线案例")
    write(output / "run_config.json", {"run_id": args.run_id, "model": args.model, "thinking": args.thinking, "case_count": len(selected), "repeat": args.repeat})
    provider = DeepSeekProvider(model=args.model)
    with (output / "results.jsonl").open("a", encoding="utf-8") as stream:
        for _ in range(args.repeat):
            for item in selected:
                record = run_case(provider, item, output, args)
                stream.write(json.dumps(record, ensure_ascii=False) + "\n"); stream.flush()
                print(f"{record['case_id']}: {record.get('failure_code') or '已完成 XML 初检'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
