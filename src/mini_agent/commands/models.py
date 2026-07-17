from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CommandName = Literal["new", "list", "resume", "zip"]


@dataclass(frozen=True)
class ParsedCommand:
    name: CommandName
    argument: str | None = None


@dataclass(frozen=True)
class CommandResult:
    handled: bool
    session_id: str | None = None
    output: str | None = None
    error: str | None = None
