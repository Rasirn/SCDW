"""CLI 正式启动入口。"""
import asyncio
import sys
from contextlib import AsyncExitStack

from scdw.common.config import get_deepseek_api_key, get_deepseek_model
from scdw.common.paths import PROJECT_ROOT
from scdw.cli.cli_chat import CliChat
from scdw.cli.main import CliApp
from scdw.llm.providers.deepseek import DeepSeekProvider
from scdw.mcp.client import MCPClient


async def run_cli() -> None:
    """初始化 MCP 客户端并启动交互式命令行。"""
    api_key = get_deepseek_api_key()
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，请在环境变量或 .env 中设置。")

    command, args = (sys.executable, [str(PROJECT_ROOT / "mcp_server.py")])
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(MCPClient(command=command, args=args))
        chat = CliChat(
            doc_client=client,
            clients={"tia_client": client},
            deepseek_service=DeepSeekProvider(model=get_deepseek_model()),
        )
        app = CliApp(chat)
        await app.initialize()
        await app.run()


def main() -> None:
    """在 Windows 上配置事件循环后启动 CLI。"""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run_cli())
