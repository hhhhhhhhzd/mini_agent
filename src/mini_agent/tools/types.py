from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from mini_agent.core.types import ToolResult, ToolSpec


@dataclass(frozen=True)
class ToolExecutionContext:
    session_id: str
    workspace_root: Path


ToolHandler = Callable[[dict[str, Any], ToolExecutionContext], Awaitable[str]]

__all__ = ["ToolExecutionContext", "ToolHandler", "ToolResult", "ToolSpec"]
