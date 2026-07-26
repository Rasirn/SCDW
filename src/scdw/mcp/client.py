import sys
import asyncio
import os
from pathlib import Path
from typing import Optional, Any
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from scdw.common.run_logging import get_run_logger


class MCPClient:
    def __init__(
        self,
        command: str,
        args: list[str],
        env: Optional[dict] = None,
    ):
        self._command = command
        self._args = args
        self._env = env
        self._session: Optional[ClientSession] = None
        self._exit_stack: AsyncExitStack = AsyncExitStack()

    async def connect(self):
        run_logger = get_run_logger()
        run_logger.log_event("mcp_connect_started", component="mcp_client", command=self._command, args=self._args)
        child_env = dict(os.environ)
        if self._env:
            child_env.update(self._env)
        server_params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=child_env,
        )
        stdio_transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        _stdio, _write = stdio_transport
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(_stdio, _write)
        )
        await self._session.initialize()
        run_logger.log_event("mcp_connect_succeeded", component="mcp_client")

    def session(self) -> ClientSession:
        if self._session is None:
            raise ConnectionError(
                "Client session not initialized or cache not populated. Call connect_to_server first."
            )
        return self._session

    async def list_tools(self) -> list[types.Tool]:
        result = await self.session().list_tools()
        return result.tools

    async def call_tool(
        self, tool_name: str, tool_input: dict
    ) -> types.CallToolResult | None:
        run_logger = get_run_logger()
        payload = run_logger.save_payload(f"tool_request_{tool_name}", tool_input)
        run_logger.log_event("mcp_tool_call_started", component="mcp_client", tool_name=tool_name, payload=payload)
        try:
            result = await self.session().call_tool(tool_name, tool_input)
            run_logger.log_event("mcp_tool_call_finished", component="mcp_client", tool_name=tool_name, result=run_logger.save_payload(f"tool_result_{tool_name}", str(result)))
            return result
        except Exception as exc:
            run_logger.log_exception("mcp_tool_call_failed", exc, component="mcp_client", tool_name=tool_name)
            raise

    async def list_prompts(self) -> list[types.Prompt]:
        # TODO: Return a list of prompts defined by the MCP server
        return []

    async def get_prompt(self, prompt_name, args: dict[str, str]):
        # TODO: Get a particular prompt defined by the MCP server
        return []

    async def read_resource(self, uri: str) -> Any:
        # TODO: Read a resource, parse the contents and return it
        return []

    async def cleanup(self):
        get_run_logger().log_event("mcp_cleanup_started", component="mcp_client")
        await self._exit_stack.aclose()
        self._session = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()


# For testing
async def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    project_root = Path(__file__).resolve().parents[3]
    async with MCPClient(
        # If using Python without UV, update command to 'python' and remove "run" from args.
        command=sys.executable,
        args=[str(project_root / "mcp_server.py")],
    ) as _client:
        result = await _client.list_tools()
        print(result)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
