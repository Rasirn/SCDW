"""SCDW MCP Client 兼容入口与调试工具。"""
import argparse
import asyncio
import sys
from pathlib import Path


def main() -> None:
    """连接本仓库 MCP Server 并打印可用工具。"""
    parser = argparse.ArgumentParser(description="列出 SCDW MCP Server 的工具")
    parser.parse_args()
    src_dir = Path(__file__).resolve().parent / "src"
    sys.path.insert(0, str(src_dir))
    from scdw.mcp.client import main as run_client
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run_client())


if __name__ == "__main__":
    main()
