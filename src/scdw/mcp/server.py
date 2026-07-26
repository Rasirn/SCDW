# -*- coding: utf-8 -*-
"""
mcp_server.py
TIA Portal MCP 服务器入口。

职责：
  1. 创建 FastMCP 实例
  2. 从 core/tools.py 注册所有 TIA 工具
  3. 启动 MCP 服务

工具实现逻辑位于 core/tools.py（register_mcp_tools 函数），
openness 接口位于 openness/ 目录，
数据解析位于 data/xlsx_reader.py。
"""
from mcp.server.fastmcp import FastMCP

from scdw.mcp.tools import register_mcp_tools

# ── MCP 服务器实例 ─────────────────────────────────────────────────────────────
mcp = FastMCP("TIA_MCP", log_level="INFO")

# ── 注册所有工具 ───────────────────────────────────────────────────────────────
register_mcp_tools(mcp)


def main() -> None:
    """启动 stdio MCP 服务。"""
    mcp.run()

# ── 启动 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()

