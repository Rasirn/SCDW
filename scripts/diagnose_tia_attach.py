"""独立诊断 TIA Portal Openness 附着能力。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scdw.openness.session import TiaSessionManager


def main() -> int:
    """列出进程，附着指定或唯一实例，并打印工程及 PLC 发现结果。"""
    parser = argparse.ArgumentParser(description="诊断已打开 TIA Portal 的 Openness 附着")
    parser.add_argument("--process-id", type=int, help="目标 TIA Portal 进程 ID；多实例时必填")
    parser.add_argument("--project-path", default="", help="需要选择的完整工程路径")
    parser.add_argument("--refresh", action="store_true", help="附着后立即再次刷新，用于验证用户后续打开工程")
    args = parser.parse_args()

    session = TiaSessionManager()
    try:
        processes = session.list_processes()
        print("TIA 进程：")
        print(json.dumps(processes, ensure_ascii=False, indent=2))
        if not processes:
            print("未发现运行中的 TIA Portal。")
            return 2
        if len(processes) > 1 and args.process_id is None:
            print("发现多个 TIA Portal。请使用 --process-id 明确选择，程序不会默认选择第一项。")
            return 3
        print("首次附着时，请在 TIA Portal 中确认 Openness 访问授权窗口。")
        context = session.attach(args.process_id, args.project_path or None)
        if args.refresh:
            context = session.refresh_context()
        print("附着与发现结果：")
        print(json.dumps(context, ensure_ascii=False, indent=2))
        if not context.get("project_name"):
            print("已连接 TIA，但当前没有打开工程。可在 TIA 中打开工程后再次执行 --refresh。")
        return 0
    except Exception as exc:
        print(f"诊断失败：{exc}", file=sys.stderr)
        return 1
    finally:
        # 绝不关闭用户手动打开的 TIA 或工程。
        session.detach(save=False)


if __name__ == "__main__":
    raise SystemExit(main())
