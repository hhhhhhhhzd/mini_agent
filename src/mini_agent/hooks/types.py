from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HookHandler:
    event: str
    command: tuple[str, ...]
    matcher: str | None = None
    timeout_seconds: float = 30.0
    required: bool = False
    cwd: Path | None = None
