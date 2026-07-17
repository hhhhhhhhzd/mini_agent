from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from mini_agent.tools.providers.mcp.adapter import render_result, to_tool_spec
from mini_agent.tools.providers.mcp.config import McpServerConfig
from mini_agent.tools.registry import ToolRegistry
from mini_agent.tools.types import ToolExecutionContext


class McpManager:
    """Own long-lived MCP stdio processes and expose their tools in one registry."""

    def __init__(
        self,
        registry: ToolRegistry,
        configs: list[McpServerConfig],
        *,
        call_timeout_seconds: float = 120.0,
    ) -> None:
        self._registry = registry
        self._configs = configs
        self._call_timeout = timedelta(seconds=call_timeout_seconds)
        self._stack: AsyncExitStack | None = None
        self._sessions: dict[str, ClientSession] = {}

    @property
    def configured(self) -> bool:
        return bool(self._configs)

    async def connect_all(self) -> None:
        if self._stack is not None:
            return
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            for config in self._configs:
                transport = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=config.command,
                            args=list(config.args),
                            env=config.env,
                            cwd=config.cwd,
                        )
                    )
                )
                session = await stack.enter_async_context(ClientSession(*transport))
                await session.initialize()
                self._sessions[config.name] = session
                await self._register_server_tools(config.name, session)
        except BaseException:
            await stack.aclose()
            self._sessions.clear()
            for config in self._configs:
                self._registry.unregister_source(f"mcp:{config.name}")
            raise
        self._stack = stack

    async def _register_server_tools(self, server: str, session: ClientSession) -> None:
        cursor: str | None = None
        while True:
            page = await session.list_tools(cursor)
            for tool in page.tools:
                spec = to_tool_spec(server, tool)
                original_name = tool.name

                async def handler(
                    arguments: dict[str, Any],
                    _context: ToolExecutionContext,
                    *,
                    client: ClientSession = session,
                    name: str = original_name,
                ) -> str:
                    result = await client.call_tool(
                        name,
                        arguments,
                        read_timeout_seconds=self._call_timeout,
                    )
                    content = render_result(result)
                    if result.isError:
                        raise RuntimeError(content or f"MCP tool {name} failed")
                    return content

                self._registry.register(spec, handler)
            cursor = page.nextCursor
            if not cursor:
                break

    async def close(self) -> None:
        for config in self._configs:
            self._registry.unregister_source(f"mcp:{config.name}")
        self._sessions.clear()
        if self._stack is not None:
            stack, self._stack = self._stack, None
            await stack.aclose()
