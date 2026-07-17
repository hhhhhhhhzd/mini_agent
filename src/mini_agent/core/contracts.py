from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Protocol

from mini_agent.core.types import (
    AgentEvent,
    HookOutcome,
    Message,
    ModelEvent,
    ToolCall,
    ToolResult,
    ToolSpec,
)


class ModelClient(Protocol):
    def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
    ) -> AsyncIterator[ModelEvent]: ...


class ToolRuntime(Protocol):
    def specs(self) -> list[ToolSpec]: ...

    def ensure_available(self, names: Sequence[str]) -> None: ...

    def validate(self, call: ToolCall, workspace_root: Path) -> ToolCall: ...

    async def execute(
        self,
        call: ToolCall,
        *,
        session_id: str,
        workspace_root: Path,
    ) -> ToolResult: ...


class HookRuntime(Protocol):
    async def user_prompt_submit(
        self, *, session_id: str, prompt: str, cwd: Path
    ) -> HookOutcome: ...

    async def pre_tool_use(
        self, *, session_id: str, call: ToolCall, cwd: Path
    ) -> HookOutcome: ...

    async def post_tool_use(
        self, *, session_id: str, call: ToolCall, result: ToolResult, cwd: Path
    ) -> None: ...

    async def post_tool_use_failure(
        self, *, session_id: str, call: ToolCall, result: ToolResult, cwd: Path
    ) -> None: ...

    async def stop(
        self, *, session_id: str, cwd: Path, stop_hook_active: bool
    ) -> HookOutcome: ...


class ContextRuntime(Protocol):
    async def build(
        self,
        *,
        session_id: str,
        project_root: Path,
        messages: Sequence[Message],
        session_rules: str | None,
        existing_summary: str | None,
        skill_instructions: Sequence[str],
        additional_context: Sequence[str],
        tools: Sequence[ToolSpec],
    ) -> tuple[list[Message], list[ToolSpec], Any | None]: ...


class AgentService(Protocol):
    def run_turn(self, session_id: str, prompt: str) -> AsyncIterator[AgentEvent]: ...
