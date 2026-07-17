from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class TurnStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class Turn:
    id: str
    session_id: str
    state: TurnStatus
    prompt_event_seq: int | None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_kind: str | None = None
    error_message: str | None = None
