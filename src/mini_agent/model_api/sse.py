from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from mini_agent.core.types import (
    ModelEvent,
    ModelResponseCompleted,
    ReasoningDelta,
    TextDelta,
    ToolCall,
)


class SSEDecodeError(RuntimeError):
    pass


@dataclass
class _ToolBuffer:
    call_id: str = ""
    name: str = ""
    arguments: list[str] = field(default_factory=list)


class OpenAIStreamDecoder:
    """Decode OpenAI-compatible chat-completion SSE chunks."""

    def __init__(self) -> None:
        self._tools: dict[int, _ToolBuffer] = {}
        self._usage: dict[str, int] | None = None
        self._finish_reason: str | None = None
        self._tools_emitted = False
        self.completed = False

    def feed(self, data: str) -> list[ModelEvent]:
        if data.strip() == "[DONE]":
            self.completed = True
            events = self._flush_tools()
            events.append(
                ModelResponseCompleted(
                    usage=self._usage,
                    finish_reason=self._finish_reason,
                )
            )
            return events

        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise SSEDecodeError(f"Invalid SSE JSON: {exc.msg}") from exc

        if "error" in payload:
            error = payload["error"]
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise SSEDecodeError(f"Model stream error: {message}")

        usage = payload.get("usage")
        if isinstance(usage, dict):
            self._usage = {
                key: int(value)
                for key, value in usage.items()
                if isinstance(value, (int, float))
            }

        events: list[ModelEvent] = []
        for choice in payload.get("choices") or []:
            if choice.get("finish_reason") is not None:
                self._finish_reason = str(choice["finish_reason"])
            delta = choice.get("delta") or {}

            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                events.append(ReasoningDelta(reasoning))

            content = delta.get("content")
            if isinstance(content, str) and content:
                events.append(TextDelta(content))

            for tool_delta in delta.get("tool_calls") or []:
                index = int(tool_delta.get("index", 0))
                buffer = self._tools.setdefault(index, _ToolBuffer())
                if tool_delta.get("id"):
                    buffer.call_id = str(tool_delta["id"])
                function = tool_delta.get("function") or {}
                if function.get("name"):
                    buffer.name += str(function["name"])
                if function.get("arguments"):
                    buffer.arguments.append(str(function["arguments"]))

        if self._finish_reason == "tool_calls":
            events.extend(self._flush_tools())
        return events

    def _flush_tools(self) -> list[ToolCall]:
        if self._tools_emitted:
            return []
        emitted: list[ToolCall] = []
        for index in sorted(self._tools):
            buffer = self._tools[index]
            raw_arguments = "".join(buffer.arguments).strip() or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise SSEDecodeError(
                    f"Invalid tool arguments for {buffer.name or index}: {exc.msg}"
                ) from exc
            if not isinstance(arguments, dict):
                raise SSEDecodeError("Tool arguments must decode to an object")
            emitted.append(
                ToolCall(
                    id=buffer.call_id or f"call_{index}",
                    name=buffer.name,
                    arguments=arguments,
                )
            )
        self._tools_emitted = True
        return emitted
