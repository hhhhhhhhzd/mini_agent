from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from mini_agent.core.contracts import HookRuntime, ModelClient, ToolRuntime
from mini_agent.core.types import (
    AgentError,
    AgentEvent,
    HookNotice,
    Message,
    MessageCommitted,
    ModelAttemptFailed,
    ModelAttemptStarted,
    ModelResponseCompleted,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    ToolFinished,
    ToolResult,
    ToolStarted,
    TurnCompleted,
)


class AgentRuntime:
    def __init__(
        self,
        *,
        model: ModelClient,
        tools: ToolRuntime,
        hooks: HookRuntime,
        max_tool_rounds: int = 32,
        max_stop_retries: int = 3,
    ) -> None:
        self._model = model
        self._tools = tools
        self._hooks = hooks
        self._max_tool_rounds = max_tool_rounds
        self._max_stop_retries = max_stop_retries

    async def run(
        self,
        *,
        session_id: str,
        workspace_root: Path,
        messages: Sequence[Message],
    ) -> AsyncIterator[AgentEvent]:
        working = list(messages)
        stop_retries = 0
        total_usage: dict[str, int] = {}

        for _round in range(self._max_tool_rounds + 1):
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            response_usage: dict[str, int] | None = None
            try:
                async for event in self._model.stream(working, self._tools.specs()):
                    if isinstance(event, (ModelAttemptStarted, ModelAttemptFailed)):
                        yield event
                    elif isinstance(event, TextDelta):
                        text_parts.append(event.text)
                        yield event
                    elif isinstance(event, ReasoningDelta):
                        yield event
                    elif isinstance(event, ToolCall):
                        tool_calls.append(event)
                        yield event
                    elif isinstance(event, ModelResponseCompleted):
                        response_usage = event.usage
            except Exception as exc:
                yield AgentError(str(exc), fatal=True)
                return

            if response_usage:
                for key, value in response_usage.items():
                    total_usage[key] = total_usage.get(key, 0) + value

            assistant = Message(
                role="assistant",
                content="".join(text_parts) or None,
                tool_calls=tuple(tool_calls),
            )
            working.append(assistant)
            yield MessageCommitted(assistant)

            if tool_calls:
                for original_call in tool_calls:
                    try:
                        original_call = self._tools.validate(original_call, workspace_root)
                    except Exception as exc:
                        result = ToolResult(
                            original_call.id,
                            original_call.name,
                            str(exc),
                            is_error=True,
                        )
                        yield ToolFinished(result)
                        tool_message = Message(
                            role="tool",
                            content=result.content,
                            name=result.tool_name,
                            tool_call_id=result.call_id,
                        )
                        working.append(tool_message)
                        yield MessageCommitted(tool_message)
                        await self._hooks.post_tool_use_failure(
                            session_id=session_id,
                            call=original_call,
                            result=result,
                            cwd=workspace_root,
                        )
                        continue

                    hook = await self._hooks.pre_tool_use(
                        session_id=session_id,
                        call=original_call,
                        cwd=workspace_root,
                    )
                    for warning in hook.warnings:
                        yield HookNotice("PreToolUse", warning)
                    if hook.blocked:
                        result = ToolResult(
                            original_call.id,
                            original_call.name,
                            hook.reason or "Blocked by PreToolUse hook",
                            is_error=True,
                        )
                        effective_call = original_call
                    else:
                        effective_call = (
                            ToolCall(
                                id=original_call.id,
                                name=original_call.name,
                                arguments=hook.rewritten_arguments,
                            )
                            if hook.rewritten_arguments is not None
                            else original_call
                        )
                        yield ToolStarted(effective_call)
                        result = await self._tools.execute(
                            effective_call,
                            session_id=session_id,
                            workspace_root=workspace_root,
                        )

                    yield ToolFinished(result)
                    tool_message = Message(
                        role="tool",
                        content=result.content,
                        name=result.tool_name,
                        tool_call_id=result.call_id,
                    )
                    working.append(tool_message)
                    yield MessageCommitted(tool_message)
                    if result.is_error:
                        await self._hooks.post_tool_use_failure(
                            session_id=session_id,
                            call=effective_call,
                            result=result,
                            cwd=workspace_root,
                        )
                    else:
                        await self._hooks.post_tool_use(
                            session_id=session_id,
                            call=effective_call,
                            result=result,
                            cwd=workspace_root,
                        )
                continue

            stop = await self._hooks.stop(
                session_id=session_id,
                cwd=workspace_root,
                stop_hook_active=stop_retries > 0,
            )
            for warning in stop.warnings:
                yield HookNotice("Stop", warning)
            if stop.blocked and stop_retries < self._max_stop_retries:
                stop_retries += 1
                reason = stop.reason or "Stop hook requested continuation"
                yield HookNotice("Stop", reason)
                continuation = Message(
                    role="system",
                    content=f"Stop hook requires the agent to continue: {reason}",
                )
                working.append(continuation)
                yield MessageCommitted(continuation)
                continue
            if stop.blocked:
                yield HookNotice(
                    "Stop",
                    "Maximum Stop hook continuation count reached; releasing the turn.",
                )
            yield TurnCompleted(total_usage or response_usage)
            return

        yield AgentError(
            f"Maximum tool rounds exceeded ({self._max_tool_rounds})",
            fatal=True,
        )
