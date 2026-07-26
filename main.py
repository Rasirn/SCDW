"""SCDW 命令行兼容入口。"""
import argparse
import sys
from pathlib import Path


def _bootstrap() -> None:
    src_dir = Path(__file__).resolve().parent / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def main() -> None:
    """显示帮助或启动交互式 CLI。"""
    parser = argparse.ArgumentParser(description="SCDW PLC 智能编程助手命令行入口")
    parser.parse_args()
    _bootstrap()
    from scdw.cli.entry import main as run_cli
    run_cli()


if __name__ == "__main__":
    main()
