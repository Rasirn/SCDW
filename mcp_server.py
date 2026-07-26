"""SCDW MCP Server 兼容入口。"""
import argparse
import sys
from pathlib import Path


def main() -> None:
    """显示帮助或启动 stdio MCP 服务。"""
    parser = argparse.ArgumentParser(description="启动 SCDW TIA Portal MCP Server")
    parser.parse_args()
    src_dir = Path(__file__).resolve().parent / "src"
    sys.path.insert(0, str(src_dir))
    from scdw.mcp.server import main as run_server
    run_server()


if __name__ == "__main__":
    main()
