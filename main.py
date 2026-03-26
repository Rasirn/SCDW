import asyncio
import sys
import os
from dotenv import load_dotenv
from contextlib import AsyncExitStack

from mcp_client import MCPClient
from core.deepseek import Deepseek

from core.cli_chat import CliChat
from core.cli import CliApp

load_dotenv()

# DeepSeek Config
deepseek_model = os.getenv("DEEPEEK_MODEL", "deepseek-chat")
deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")

assert deepseek_model, "Error: DEEPSEEK_MODEL cannot be empty. Update .env"
assert deepseek_api_key, (
    "Error: DEEPSEEK_API_KEY cannot be empty. Update .env"
)


async def main():
    # 使用 DeepSeek 创建服务实例
    deepseek_service = Deepseek(api_key=deepseek_api_key,model=deepseek_model)

    server_scripts = sys.argv[1:]
    clients = {}

    command, args = (
        ("uv", ["run", "mcp_server.py"])
        if os.getenv("USE_UV", "0") == "1"
        else ("python", ["mcp_server.py"])
    )

    async with AsyncExitStack() as stack:
        doc_client = await stack.enter_async_context(
            MCPClient(command=command, args=args)
        )
        clients["doc_client"] = doc_client

        for i, server_script in enumerate(server_scripts):
            client_id = f"client_{i}_{server_script}"
            client = await stack.enter_async_context(
                MCPClient(command="uv", args=["run", server_script])
            )
            clients[client_id] = client

        # 使用 DeepSeek 服务初始化 CliChat
        chat = CliChat(
            doc_client=doc_client,
            clients=clients,
            deepseek_service=deepseek_service,  # 使用 DeepSeek 服务
        )

        cli = CliApp(chat)
        await cli.initialize()
        await cli.run()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
