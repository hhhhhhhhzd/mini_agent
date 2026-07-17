from __future__ import annotations

from pathlib import Path
import sys

import mcp.types as mt
import pytest

from mini_agent.core.types import ToolCall
from mini_agent.tools import ToolExecutor, ToolRegistry
from mini_agent.tools.permissions import AllowPermissionBroker, PermissionManager
from mini_agent.tools.providers.mcp.adapter import infer_risk, mcp_tool_name
from mini_agent.tools.providers.mcp.client import McpManager
from mini_agent.tools.providers.mcp.config import McpServerConfig, load_mcp_config


def test_mcp_config_override_and_name_normalization(tmp_path: Path) -> None:
    global_config = tmp_path / "global.json"
    project_config = tmp_path / "project.json"
    global_config.write_text(
        '{"mcpServers":{"demo":{"command":"old","args":["a"]}}}', encoding="utf-8"
    )
    project_config.write_text(
        '{"mcpServers":{"demo":{"command":"new"},"off":{"command":"x","enabled":false}}}',
        encoding="utf-8",
    )
    configs = load_mcp_config([global_config, project_config])
    assert [(item.name, item.command) for item in configs] == [("demo", "new")]
    assert mcp_tool_name("my server", "read/file") == "mcp__my_server__read_file"


def test_mcp_annotation_risk() -> None:
    read = mt.Tool(
        name="read",
        inputSchema={"type": "object"},
        annotations=mt.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    destructive = mt.Tool(
        name="delete",
        inputSchema={"type": "object"},
        annotations=mt.ToolAnnotations(destructiveHint=True),
    )
    assert infer_risk(read) == "read"
    assert infer_risk(destructive) == "destructive"


@pytest.mark.asyncio
async def test_mcp_tools_are_registered_and_called_through_executor(tmp_path: Path) -> None:
    tool = mt.Tool(
        name="echo",
        description="Echo input",
        inputSchema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        annotations=mt.ToolAnnotations(readOnlyHint=True),
    )

    class Page:
        tools = [tool]
        nextCursor = None

    class FakeSession:
        async def list_tools(self, cursor=None): return Page()
        async def call_tool(self, name, arguments, read_timeout_seconds=None):
            assert name == "echo"
            return mt.CallToolResult(
                content=[mt.TextContent(type="text", text=arguments["text"])],
                isError=False,
            )

    registry = ToolRegistry()
    manager = McpManager(registry, [McpServerConfig("demo", "unused")])
    await manager._register_server_tools("demo", FakeSession())  # type: ignore[arg-type]
    executor = ToolExecutor(registry, PermissionManager(AllowPermissionBroker()))
    result = await executor.execute(
        ToolCall("1", "mcp__demo__echo", {"text": "hello"}),
        session_id="s",
        workspace_root=tmp_path,
    )
    assert not result.is_error and result.content == "hello"


@pytest.mark.asyncio
async def test_real_mcp_stdio_lifecycle(tmp_path: Path) -> None:
    registry = ToolRegistry()
    server_script = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
    manager = McpManager(
        registry,
        [McpServerConfig("real", sys.executable, (str(server_script),))],
    )
    await manager.connect_all()
    try:
        executor = ToolExecutor(registry, PermissionManager(AllowPermissionBroker()))
        result = await executor.execute(
            ToolCall("1", "mcp__real__echo", {"text": "stdio-ok"}),
            session_id="s",
            workspace_root=tmp_path,
        )
        assert not result.is_error and "stdio-ok" in result.content
    finally:
        await manager.close()
    assert registry.specs() == []
