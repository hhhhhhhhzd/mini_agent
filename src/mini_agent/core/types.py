from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias


Role: TypeAlias = Literal["system", "developer", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        import json

        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass(frozen=True)
class Message:
    role: Role
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    def to_openai(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            data["name"] = self.name
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            data["tool_calls"] = [call.to_openai() for call in self.tool_calls]
        return data


@dataclass(frozen=True)
class ToolSpec:
    id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    source: str
    capabilities: frozenset[str] = frozenset()
    risk: Literal["read", "write", "execute", "network", "destructive"] = "read"

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    tool_name: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class ModelResponseCompleted:
    usage: dict[str, int] | None = None
    finish_reason: str | None = None


@dataclass(frozen=True)
class ToolStarted:
    call: ToolCall


@dataclass(frozen=True)
class ToolFinished:
    result: ToolResult


@dataclass(frozen=True)
class MessageCommitted:
    message: Message


@dataclass(frozen=True)
class HookNotice:
    event: str
    message: str


@dataclass(frozen=True)
class AgentError:
    message: str
    fatal: bool = False


@dataclass(frozen=True)
class TurnCompleted:
    usage: dict[str, int] | None = None


ModelEvent: TypeAlias = (
    TextDelta | ReasoningDelta | ToolCall | ModelResponseCompleted
)
AgentEvent: TypeAlias = (
    TextDelta
    | ReasoningDelta
    | ToolCall
    | ToolStarted
    | ToolFinished
    | MessageCommitted
    | HookNotice
    | AgentError
    | TurnCompleted
)


@dataclass(frozen=True)
class HookOutcome:
    blocked: bool = False
    reason: str | None = None
    additional_context: tuple[str, ...] = ()
    rewritten_arguments: dict[str, Any] | None = None
    warnings: tuple[str, ...] = ()


@dataclass
class TurnState:
    messages: list[Message] = field(default_factory=list)
    usage: dict[str, int] | None = None
