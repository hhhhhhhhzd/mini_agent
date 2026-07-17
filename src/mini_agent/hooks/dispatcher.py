from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from mini_agent.core.types import HookOutcome, ToolCall, ToolResult
from mini_agent.hooks.subprocess import run_command_hook
from mini_agent.hooks.types import HookHandler


class HookConfigurationError(RuntimeError):
    pass


def _split_command(value: str | list[str]) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    parts = shlex.split(value, posix=False)
    return tuple(part.strip('"') for part in parts)


class HookDispatcher:
    def __init__(self, handlers: list[HookHandler] | None = None) -> None:
        self._handlers = list(handlers or [])

    @classmethod
    def from_files(cls, paths: list[Path]) -> "HookDispatcher":
        handlers: list[HookHandler] = []
        for path in paths:
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise HookConfigurationError(f"Cannot load {path}: {exc}") from exc
            for event, groups in (data.get("hooks") or {}).items():
                for group in groups or []:
                    matcher = group.get("matcher")
                    for item in group.get("hooks") or []:
                        command_value = item.get("command_windows") or item.get("command")
                        if not command_value:
                            continue
                        handlers.append(
                            HookHandler(
                                event=str(event),
                                matcher=str(matcher) if matcher else None,
                                command=_split_command(command_value),
                                timeout_seconds=float(item.get("timeout", 30)),
                                required=bool(item.get("required", False)),
                                cwd=path.parent,
                            )
                        )
        return cls(handlers)

    def _matching(self, event: str, target: str | None = None) -> list[HookHandler]:
        matched: list[HookHandler] = []
        for handler in self._handlers:
            if handler.event != event:
                continue
            if handler.matcher and not re.search(handler.matcher, target or ""):
                continue
            matched.append(handler)
        return matched

    async def _dispatch(
        self,
        event: str,
        envelope: dict[str, Any],
        *,
        cwd: Path,
        target: str | None = None,
    ) -> HookOutcome:
        outcomes = [
            await run_command_hook(handler, envelope, default_cwd=cwd)
            for handler in self._matching(event, target)
        ]
        blocked = next((item for item in outcomes if item.blocked), None)
        rewritten = next(
            (item.rewritten_arguments for item in outcomes if item.rewritten_arguments is not None),
            None,
        )
        return HookOutcome(
            blocked=blocked is not None,
            reason=blocked.reason if blocked else None,
            additional_context=tuple(
                value for item in outcomes for value in item.additional_context
            ),
            rewritten_arguments=rewritten,
            warnings=tuple(value for item in outcomes for value in item.warnings),
        )

    async def user_prompt_submit(
        self, *, session_id: str, prompt: str, cwd: Path
    ) -> HookOutcome:
        return await self._dispatch(
            "UserPromptSubmit",
            {"event": "UserPromptSubmit", "session_id": session_id, "cwd": str(cwd), "prompt": prompt},
            cwd=cwd,
        )

    async def pre_tool_use(
        self, *, session_id: str, call: ToolCall, cwd: Path
    ) -> HookOutcome:
        return await self._dispatch(
            "PreToolUse",
            {
                "event": "PreToolUse",
                "session_id": session_id,
                "cwd": str(cwd),
                "tool_name": call.name,
                "tool_call_id": call.id,
                "arguments": call.arguments,
            },
            cwd=cwd,
            target=call.name,
        )

    async def post_tool_use(
        self, *, session_id: str, call: ToolCall, result: ToolResult, cwd: Path
    ) -> None:
        await self._dispatch(
            "PostToolUse",
            {
                "event": "PostToolUse",
                "session_id": session_id,
                "cwd": str(cwd),
                "tool_name": call.name,
                "tool_call_id": call.id,
                "result": result.content,
                "is_error": result.is_error,
            },
            cwd=cwd,
            target=call.name,
        )

    async def post_tool_use_failure(
        self, *, session_id: str, call: ToolCall, result: ToolResult, cwd: Path
    ) -> None:
        await self._dispatch(
            "PostToolUseFailure",
            {
                "event": "PostToolUseFailure",
                "session_id": session_id,
                "cwd": str(cwd),
                "tool_name": call.name,
                "tool_call_id": call.id,
                "error": result.content,
            },
            cwd=cwd,
            target=call.name,
        )

    async def stop(
        self, *, session_id: str, cwd: Path, stop_hook_active: bool
    ) -> HookOutcome:
        return await self._dispatch(
            "Stop",
            {
                "event": "Stop",
                "session_id": session_id,
                "cwd": str(cwd),
                "stop_hook_active": stop_hook_active,
            },
            cwd=cwd,
        )


class NoopHookDispatcher(HookDispatcher):
    def __init__(self) -> None:
        super().__init__([])
