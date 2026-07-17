from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from mini_agent.core.types import Message


class SessionState(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class Session:
    id: str
    state: SessionState
    project_root: Path
    system_rules: str | None
    active_skills: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


@dataclass(frozen=True)
class StoredMessage:
    seq: int
    message: Message


@dataclass(frozen=True)
class SessionCheckpoint:
    session_id: str
    through_seq: int
    summary: str
    version: str
    created_at: datetime
