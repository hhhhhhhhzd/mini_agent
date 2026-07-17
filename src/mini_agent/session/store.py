from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mini_agent.core.types import Message, ToolCall
from mini_agent.session.models import (
    Session,
    SessionCheckpoint,
    SessionState,
    StoredMessage,
)


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    project_root TEXT NOT NULL,
    system_rules TEXT,
    active_skills_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, seq),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS checkpoints (
    session_id TEXT NOT NULL,
    through_seq INTEGER NOT NULL,
    summary TEXT NOT NULL,
    version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, through_seq),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_session_kind ON events(session_id, kind, seq);
CREATE INDEX IF NOT EXISTS idx_sessions_state_updated ON sessions(state, updated_at DESC);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _message_to_payload(message: Message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "name": message.name,
        "tool_call_id": message.tool_call_id,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ],
    }


def _message_from_payload(payload: dict[str, Any]) -> Message:
    return Message(
        role=payload["role"],
        content=payload.get("content"),
        name=payload.get("name"),
        tool_call_id=payload.get("tool_call_id"),
        tool_calls=tuple(
            ToolCall(
                id=item["id"],
                name=item["name"],
                arguments=dict(item.get("arguments") or {}),
            )
            for item in payload.get("tool_calls") or []
        ),
    )


class SqliteSessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    async def initialize(self) -> None:
        def run() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(SCHEMA)

        await asyncio.to_thread(run)

    async def create(
        self,
        project_root: Path,
        *,
        system_rules: str | None = None,
        session_id: str | None = None,
    ) -> Session:
        session_id = session_id or uuid.uuid4().hex
        now = _now()

        def run() -> None:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO sessions(
                        id, state, project_root, system_rules, active_skills_json,
                        created_at, updated_at, archived_at
                    ) VALUES (?, ?, ?, ?, '[]', ?, ?, NULL)
                    """,
                    (
                        session_id,
                        SessionState.ACTIVE.value,
                        str(project_root.resolve()),
                        system_rules,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )

        await asyncio.to_thread(run)
        return await self.get(session_id)

    async def get(self, session_id: str) -> Session:
        def run() -> sqlite3.Row:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"Session not found: {session_id}")
                return row

        return self._row_to_session(await asyncio.to_thread(run))

    async def list(self, *, include_archived: bool = True) -> list[Session]:
        def run() -> list[sqlite3.Row]:
            with self._connect() as connection:
                if include_archived:
                    rows = connection.execute(
                        "SELECT * FROM sessions ORDER BY updated_at DESC"
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT * FROM sessions WHERE state = ? ORDER BY updated_at DESC",
                        (SessionState.ACTIVE.value,),
                    ).fetchall()
                return list(rows)

        return [self._row_to_session(row) for row in await asyncio.to_thread(run)]

    async def resume(self, session_id: str) -> Session:
        now = _now().isoformat()

        def run() -> None:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE sessions
                    SET state = ?, archived_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (SessionState.ACTIVE.value, now, session_id),
                )
                if cursor.rowcount == 0:
                    raise KeyError(f"Session not found: {session_id}")

        await asyncio.to_thread(run)
        return await self.get(session_id)

    async def archive(self, session_id: str) -> Session:
        now = _now().isoformat()

        def run() -> None:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE sessions
                    SET state = ?, archived_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (SessionState.ARCHIVED.value, now, now, session_id),
                )
                if cursor.rowcount == 0:
                    raise KeyError(f"Session not found: {session_id}")

        await asyncio.to_thread(run)
        return await self.get(session_id)

    async def delete(self, session_id: str) -> None:
        def run() -> None:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM sessions WHERE id = ?", (session_id,)
                )
                if cursor.rowcount == 0:
                    raise KeyError(f"Session not found: {session_id}")

        await asyncio.to_thread(run)

    async def append_message(self, session_id: str, message: Message) -> int:
        return await self.append_event(session_id, "message", _message_to_payload(message))

    async def append_event(
        self, session_id: str, kind: str, payload: dict[str, Any]
    ) -> int:
        now = _now().isoformat()

        def run() -> int:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                exists = connection.execute(
                    "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if exists is None:
                    raise KeyError(f"Session not found: {session_id}")
                seq = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO events(session_id, seq, kind, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        seq,
                        kind,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                    ),
                )
                connection.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
                return seq

        return await asyncio.to_thread(run)

    async def load_messages(
        self, session_id: str, *, after_seq: int = 0
    ) -> list[StoredMessage]:
        def run() -> list[sqlite3.Row]:
            with self._connect() as connection:
                return list(
                    connection.execute(
                        """
                        SELECT seq, payload_json FROM events
                        WHERE session_id = ? AND kind = 'message' AND seq > ?
                        ORDER BY seq
                        """,
                        (session_id, after_seq),
                    ).fetchall()
                )

        rows = await asyncio.to_thread(run)
        return [
            StoredMessage(
                seq=int(row["seq"]),
                message=_message_from_payload(json.loads(row["payload_json"])),
            )
            for row in rows
        ]

    async def set_active_skills(self, session_id: str, names: list[str]) -> None:
        now = _now().isoformat()

        def run() -> None:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE sessions
                    SET active_skills_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (json.dumps(sorted(set(names))), now, session_id),
                )
                if cursor.rowcount == 0:
                    raise KeyError(f"Session not found: {session_id}")

        await asyncio.to_thread(run)

    async def save_checkpoint(
        self,
        session_id: str,
        *,
        through_seq: int,
        summary: str,
        version: str,
    ) -> SessionCheckpoint:
        now = _now()

        def run() -> None:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO checkpoints(
                        session_id, through_seq, summary, version, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, through_seq, summary, version, now.isoformat()),
                )

        await asyncio.to_thread(run)
        return SessionCheckpoint(session_id, through_seq, summary, version, now)

    async def latest_checkpoint(self, session_id: str) -> SessionCheckpoint | None:
        def run() -> sqlite3.Row | None:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT * FROM checkpoints
                    WHERE session_id = ?
                    ORDER BY through_seq DESC LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()

        row = await asyncio.to_thread(run)
        if row is None:
            return None
        return SessionCheckpoint(
            session_id=row["session_id"],
            through_seq=int(row["through_seq"]),
            summary=row["summary"],
            version=row["version"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            state=SessionState(row["state"]),
            project_root=Path(row["project_root"]),
            system_rules=row["system_rules"],
            active_skills=tuple(json.loads(row["active_skills_json"])),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            archived_at=(
                datetime.fromisoformat(row["archived_at"])
                if row["archived_at"]
                else None
            ),
        )
