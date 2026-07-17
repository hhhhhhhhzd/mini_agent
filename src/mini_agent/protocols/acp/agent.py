from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import acp
from acp.exceptions import RequestError
from acp.interfaces import Client
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AgentThoughtChunk,
    AvailableCommand,
    AvailableCommandsUpdate,
    CloseSessionResponse,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpCapabilities,
    NewSessionResponse,
    PromptCapabilities,
    PromptResponse,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionInfo,
    SessionListCapabilities,
    SessionResumeCapabilities,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    Usage,
    UsageUpdate,
    UserMessageChunk,
)

from mini_agent.core.application import AgentApplication
from mini_agent.commands import CommandDispatcher
from mini_agent.core.types import (
    AgentError,
    ReasoningDelta,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnCompleted,
)
from mini_agent.protocols.acp.permissions import AcpPermissionBroker, _tool_kind


class AcpAgent:
    """Thin ACP input/output adapter over the shared AgentApplication."""

    def __init__(self, app: AgentApplication, permission_broker: AcpPermissionBroker) -> None:
        self._app = app
        self._permissions = permission_broker
        self._client: Client | None = None

    def on_connect(self, conn: Client) -> None:
        self._client = conn
        self._permissions.bind(conn)

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any = None,
        client_info: Any = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        del client_capabilities, client_info, kwargs
        if protocol_version != acp.PROTOCOL_VERSION:
            raise RequestError.invalid_params(
                {"message": f"Unsupported ACP protocol version: {protocol_version}"}
            )
        return InitializeResponse(
            protocolVersion=acp.PROTOCOL_VERSION,
            agentCapabilities=AgentCapabilities(
                loadSession=True,
                promptCapabilities=PromptCapabilities(
                    image=False, audio=False, embeddedContext=False
                ),
                mcpCapabilities=McpCapabilities(http=False, sse=False, acp=False),
                sessionCapabilities=SessionCapabilities(
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                    close=SessionCloseCapabilities(),
                ),
            ),
            agentInfo=Implementation(name="mini-agent", title="Mini Agent", version="0.2.0"),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        del kwargs
        self._reject_dynamic_inputs(additional_directories, mcp_servers)
        session = await self._app.create_session(Path(cwd))
        await self._app.bind_session(
            channel="acp",
            external_session_id=session.id,
            internal_session_id=session.id,
        )
        await self._advertise_commands(session.id)
        return NewSessionResponse(sessionId=session.id)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse:
        del kwargs
        self._reject_dynamic_inputs(additional_directories, mcp_servers)
        internal_session_id = await self._resolve_internal(session_id)
        session = await self._app.resume_session(internal_session_id)
        if Path(cwd).resolve() != session.project_root:
            raise RequestError.invalid_params(
                {"message": "Session cwd does not match its stored project root"}
            )
        await self._replay_history(session_id, internal_session_id)
        await self._advertise_commands(session_id)
        return LoadSessionResponse()

    async def resume_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        del kwargs
        self._reject_dynamic_inputs(additional_directories, mcp_servers)
        internal_session_id = await self._resolve_internal(session_id)
        session = await self._app.resume_session(internal_session_id)
        if Path(cwd).resolve() != session.project_root:
            raise RequestError.invalid_params(
                {"message": "Session cwd does not match its stored project root"}
            )
        await self._advertise_commands(session_id)
        return ResumeSessionResponse()

    async def list_sessions(
        self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        del kwargs
        sessions = await self._app.list_sessions()
        if cwd is not None:
            root = Path(cwd).resolve()
            sessions = [session for session in sessions if session.project_root == root]
        offset = int(cursor or "0")
        page_size = 100
        page = sessions[offset : offset + page_size]
        next_cursor = str(offset + page_size) if offset + page_size < len(sessions) else None
        return ListSessionsResponse(
            sessions=[
                SessionInfo(
                    sessionId=session.id,
                    cwd=str(session.project_root),
                    title=session.name or f"Mini Agent {session.id[:8]}",
                    updatedAt=session.updated_at.isoformat(),
                )
                for session in page
            ],
            nextCursor=next_cursor,
        )

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse:
        del kwargs
        await self._app.archive_session(await self._resolve_internal(session_id))
        return CloseSessionResponse()

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
        del kwargs
        if self._client is None:
            raise RuntimeError("ACP client is not connected")
        text_parts = [block.text for block in prompt if getattr(block, "type", None) == "text"]
        if len(text_parts) != len(prompt):
            raise RequestError.invalid_params(
                {"message": "This agent currently accepts text ACP prompt blocks only"}
            )
        internal_session_id = await self._resolve_internal(session_id)
        command_text = "\n".join(text_parts)
        current = await self._app.get_session(internal_session_id)
        command = await CommandDispatcher(
            self._app,
            current.project_root,
        ).dispatch(command_text, session_id=internal_session_id)
        if command.handled:
            if command.session_id and command.session_id != internal_session_id:
                await self._app.bind_session(
                    channel="acp",
                    external_session_id=session_id,
                    internal_session_id=command.session_id,
                )
            output = command.error or command.output
            if output:
                await self._client.session_update(
                    session_id,
                    AgentMessageChunk(
                        sessionUpdate="agent_message_chunk",
                        content=TextContentBlock(type="text", text=output),
                    ),
                )
            return PromptResponse(stopReason="refusal" if command.error else "end_turn")
        usage_data: dict[str, int] | None = None
        failed = False
        try:
            async for event in self._app.run_turn(internal_session_id, command_text):
                update = self._event_update(event)
                if update is not None:
                    await self._client.session_update(session_id, update)
                if isinstance(event, TurnCompleted):
                    usage_data = event.usage
                elif isinstance(event, AgentError):
                    failed = True
        except asyncio.CancelledError:
            await self._app.record_interruption(
                internal_session_id, "ACP prompt cancelled"
            )
            return PromptResponse(stopReason="cancelled")
        usage = self._usage(usage_data)
        return PromptResponse(
            stopReason="refusal" if failed else "end_turn",
            usage=usage,
        )

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del kwargs
        await self._app.cancel_turn(await self._resolve_internal(session_id))

    async def set_session_mode(self, session_id: str, mode_id: str, **kwargs: Any) -> None:
        del session_id, mode_id, kwargs
        return None

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **kwargs: Any
    ) -> None:
        del config_id, session_id, value, kwargs
        return None

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        del method_id, kwargs
        return None

    async def fork_session(self, **kwargs: Any) -> Any:
        del kwargs
        raise RequestError.invalid_params({"message": "Session fork is not supported"})

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "mini_agent/session/delete":
            external_session_id = str(params.get("sessionId", ""))
            await self._app.delete_session(
                await self._resolve_internal(external_session_id)
            )
            return {}
        raise RequestError.method_not_found(f"_{method}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        del method, params

    async def _resolve_internal(self, external_session_id: str) -> str:
        internal_session_id = await self._app.resolve_binding(
            channel="acp",
            external_session_id=external_session_id,
        )
        if internal_session_id is not None:
            return internal_session_id
        await self._app.get_session(external_session_id)
        await self._app.bind_session(
            channel="acp",
            external_session_id=external_session_id,
            internal_session_id=external_session_id,
        )
        return external_session_id

    async def _advertise_commands(self, external_session_id: str) -> None:
        if self._client is None:
            return
        commands = [
            AvailableCommand(name="new", description="Create a new session: /new [name]"),
            AvailableCommand(name="list", description="List sessions in this workspace"),
            AvailableCommand(
                name="resume",
                description="Resume a session: /resume <session-id|name>",
            ),
            AvailableCommand(name="zip", description="Compress completed session history"),
        ]
        await self._client.session_update(
            external_session_id,
            AvailableCommandsUpdate(
                sessionUpdate="available_commands_update",
                availableCommands=commands,
            ),
        )

    async def _replay_history(
        self, external_session_id: str, internal_session_id: str
    ) -> None:
        if self._client is None:
            return
        for message in await self._app.session_history(internal_session_id):
            if not message.content or message.role not in {"user", "assistant"}:
                continue
            content = TextContentBlock(type="text", text=message.content)
            update = (
                UserMessageChunk(sessionUpdate="user_message_chunk", content=content)
                if message.role == "user"
                else AgentMessageChunk(sessionUpdate="agent_message_chunk", content=content)
            )
            await self._client.session_update(external_session_id, update)

    @staticmethod
    def _reject_dynamic_inputs(
        additional_directories: list[str] | None, mcp_servers: list[Any] | None
    ) -> None:
        if additional_directories:
            raise RequestError.invalid_params(
                {"message": "Additional directories are not supported"}
            )
        if mcp_servers:
            raise RequestError.invalid_params(
                {"message": "Configure MCP servers with .mini-agent/mcp.json"}
            )

    @staticmethod
    def _event_update(event: Any) -> Any | None:
        if isinstance(event, TextDelta):
            return AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=TextContentBlock(type="text", text=event.text),
            )
        if isinstance(event, ReasoningDelta):
            return AgentThoughtChunk(
                sessionUpdate="agent_thought_chunk",
                content=TextContentBlock(type="text", text=event.text),
            )
        if isinstance(event, ToolStarted):
            name = event.call.name
            risk = (
                "read"
                if name in {"read_file", "list_directory", "search_text"}
                else "execute" if name == "shell_exec" else "write"
            )
            return ToolCallStart(
                sessionUpdate="tool_call",
                toolCallId=event.call.id,
                title=event.call.name,
                kind=_tool_kind(risk),
                status="in_progress",
                rawInput=event.call.arguments,
            )
        if isinstance(event, ToolFinished):
            return ToolCallProgress(
                sessionUpdate="tool_call_update",
                toolCallId=event.result.call_id,
                title=event.result.tool_name,
                status="failed" if event.result.is_error else "completed",
                rawOutput=event.result.content,
            )
        if isinstance(event, TurnCompleted) and event.usage:
            total = int(event.usage.get("total_tokens", 0))
            return UsageUpdate(sessionUpdate="usage_update", used=total, size=262_144)
        return None

    @staticmethod
    def _usage(data: dict[str, int] | None) -> Usage | None:
        if not data:
            return None
        input_tokens = int(data.get("prompt_tokens", data.get("input_tokens", 0)))
        output_tokens = int(data.get("completion_tokens", data.get("output_tokens", 0)))
        total = int(data.get("total_tokens", input_tokens + output_tokens))
        return Usage(totalTokens=total, inputTokens=input_tokens, outputTokens=output_tokens)
