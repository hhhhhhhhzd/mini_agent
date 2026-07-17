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
from mini_agent.session.migrations import migrate
from mini_agent.turns.models import Turn, TurnStatus

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_name(name: str | None) -> str | None:
    if name is None:
        return None
    normalized = " ".join(name.split()).strip()
    if not normalized:
        return None
    if len(normalized) > 128:
        raise ValueError("Session name must be 128 characters or fewer")
    return normalized


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
                migrate(connection, self.path)

        await asyncio.to_thread(run)

    async def create(
        self,
        project_root: Path,
        *,
        system_rules: str | None = None,
        session_id: str | None = None,
        name: str | None = None,
    ) -> Session:
        session_id = session_id or uuid.uuid4().hex
        name = _normalize_name(name)
        now = _now()

        def run() -> None:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO sessions(
                        id, state, project_root, system_rules, active_skills_json,
                        created_at, updated_at, archived_at, name
                    ) VALUES (?, ?, ?, ?, '[]', ?, ?, NULL, ?)
                    """,
                    (
                        session_id,
                        SessionState.ACTIVE.value,
                        str(project_root.resolve()),
                        system_rules,
                        now.isoformat(),
                        now.isoformat(),
                        name,
                    ),
                )
        try:
            await asyncio.to_thread(run)
        except sqlite3.IntegrityError as exc:
            if name is not None:
                raise ValueError(
                    f"Session name already exists in this workspace: {name}"
                ) from exc
            raise
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

    async def list(
        self,
        *,
        include_archived: bool = True,
        project_root: Path | None = None,
    ) -> list[Session]:
        root_value = str(project_root.resolve()) if project_root is not None else None

        def run() -> list[sqlite3.Row]:
            with self._connect() as connection:
                if include_archived and root_value is None:
                    rows = connection.execute(
                        "SELECT * FROM sessions ORDER BY updated_at DESC"
                    ).fetchall()
                elif include_archived:
                    rows = connection.execute(
                        "SELECT * FROM sessions WHERE project_root = ? ORDER BY updated_at DESC",
                        (root_value,),
                    ).fetchall()
                elif root_value is None:
                    rows = connection.execute(
                        "SELECT * FROM sessions WHERE state = ? ORDER BY updated_at DESC",
                        (SessionState.ACTIVE.value,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT * FROM sessions
                        WHERE state = ? AND project_root = ?
                        ORDER BY updated_at DESC
                        """,
                        (SessionState.ACTIVE.value, root_value),
                    ).fetchall()
                return list(rows)

        return [self._row_to_session(row) for row in await asyncio.to_thread(run)]

    async def resolve(self, reference: str, project_root: Path) -> Session:
        reference = reference.strip()
        if not reference:
            raise ValueError("Session reference cannot be empty")
        sessions = await self.list(project_root=project_root)
        exact_id = [session for session in sessions if session.id == reference]
        if exact_id:
            return exact_id[0]
        exact_name = [
            session
            for session in sessions
            if session.name is not None and session.name.casefold() == reference.casefold()
        ]
        if len(exact_name) == 1:
            return exact_name[0]
        prefix = [session for session in sessions if session.id.startswith(reference)]
        candidates = exact_name or prefix
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise KeyError(f"Session not found in current workspace: {reference}")
        labels = ", ".join(
            f"{session.name or '-'} ({session.id[:8]})" for session in candidates[:10]
        )
        raise ValueError(f"Session reference is ambiguous: {reference}; candidates: {labels}")

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

    async def create_turn(
        self,
        session_id: str,
        *,
        turn_id: str | None = None,
        prompt_event_seq: int | None = None,
    ) -> Turn:
        turn_id = turn_id or uuid.uuid4().hex
        now = _now()

        def run() -> None:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO turns(
                        id, session_id, state, prompt_event_seq, created_at,
                        started_at, finished_at, error_kind, error_message
                    ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
                    """,
                    (
                        turn_id,
                        session_id,
                        TurnStatus.QUEUED.value,
                        prompt_event_seq,
                        now.isoformat(),
                    ),
                )

        try:
            await asyncio.to_thread(run)
        except sqlite3.IntegrityError as exc:
            if not await self._session_exists(session_id):
                raise KeyError(f"Session not found: {session_id}") from exc
            raise
        return await self.get_turn(turn_id)

    async def get_turn(self, turn_id: str) -> Turn:
        def run() -> sqlite3.Row:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM turns WHERE id = ?", (turn_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"Turn not found: {turn_id}")
                return row

        return self._row_to_turn(await asyncio.to_thread(run))

    async def list_turns(self, session_id: str) -> list[Turn]:
        def run() -> list[sqlite3.Row]:
            with self._connect() as connection:
                return list(
                    connection.execute(
                        "SELECT * FROM turns WHERE session_id = ? ORDER BY created_at",
                        (session_id,),
                    ).fetchall()
                )

        return [self._row_to_turn(row) for row in await asyncio.to_thread(run)]

    async def update_turn(
        self,
        turn_id: str,
        state: TurnStatus,
        *,
        prompt_event_seq: int | None = None,
        error_kind: str | None = None,
        error_message: str | None = None,
    ) -> Turn:
        now = _now().isoformat()
        started_at = now if state == TurnStatus.RUNNING else None
        finished_at = (
            now
            if state
            in {
                TurnStatus.COMPLETED,
                TurnStatus.FAILED,
                TurnStatus.CANCELLED,
                TurnStatus.INTERRUPTED,
            }
            else None
        )

        def run() -> None:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE turns SET
                        state = ?,
                        prompt_event_seq = COALESCE(?, prompt_event_seq),
                        started_at = COALESCE(started_at, ?),
                        finished_at = COALESCE(?, finished_at),
                        error_kind = ?,
                        error_message = ?
                    WHERE id = ?
                    """,
                    (
                        state.value,
                        prompt_event_seq,
                        started_at,
                        finished_at,
                        error_kind,
                        error_message,
                        turn_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise KeyError(f"Turn not found: {turn_id}")

        await asyncio.to_thread(run)
        return await self.get_turn(turn_id)

    async def recover_interrupted_turns(self) -> int:
        now = _now().isoformat()

        def run() -> int:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE turns
                    SET state = ?, finished_at = ?, error_kind = ?, error_message = ?
                    WHERE state IN (?, ?, ?)
                    """,
                    (
                        TurnStatus.INTERRUPTED.value,
                        now,
                        "process_restart",
                        "Agent restarted before the Turn completed",
                        TurnStatus.QUEUED.value,
                        TurnStatus.RUNNING.value,
                        TurnStatus.WAITING_TOOL.value,
                    ),
                )
                return int(cursor.rowcount)

        return await asyncio.to_thread(run)

    async def bind_session(
        self,
        *,
        channel: str,
        external_session_id: str,
        internal_session_id: str,
    ) -> None:
        now = _now().isoformat()

        def run() -> None:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO session_bindings(
                        channel, external_session_id, internal_session_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(channel, external_session_id) DO UPDATE SET
                        internal_session_id = excluded.internal_session_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        channel,
                        external_session_id,
                        internal_session_id,
                        now,
                        now,
                    ),
                )

        try:
            await asyncio.to_thread(run)
        except sqlite3.IntegrityError as exc:
            raise KeyError(f"Session not found: {internal_session_id}") from exc

    async def resolve_binding(
        self, *, channel: str, external_session_id: str
    ) -> str | None:
        def run() -> str | None:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT internal_session_id FROM session_bindings
                    WHERE channel = ? AND external_session_id = ?
                    """,
                    (channel, external_session_id),
                ).fetchone()
                return str(row[0]) if row is not None else None

        return await asyncio.to_thread(run)

    async def delete_binding(self, *, channel: str, external_session_id: str) -> None:
        def run() -> None:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM session_bindings WHERE channel = ? AND external_session_id = ?",
                    (channel, external_session_id),
                )

        await asyncio.to_thread(run)

    async def _session_exists(self, session_id: str) -> bool:
        def run() -> bool:
            with self._connect() as connection:
                return (
                    connection.execute(
                        "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
                    ).fetchone()
                    is not None
                )

        return await asyncio.to_thread(run)

    async def save_checkpoint(
        self,
        session_id: str,
        *,
        through_seq: int,
        summary: str,
        version: str,
        trigger: str = "automatic",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        details: dict[str, object] | None = None,
    ) -> SessionCheckpoint:
        now = _now()
        details = dict(details or {})

        def run() -> None:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO checkpoints(
                        session_id, through_seq, summary, version, created_at,
                        trigger, input_tokens, output_tokens, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        through_seq,
                        summary,
                        version,
                        now.isoformat(),
                        trigger,
                        input_tokens,
                        output_tokens,
                        json.dumps(details, ensure_ascii=False),
                    ),
                )

        await asyncio.to_thread(run)
        return SessionCheckpoint(
            session_id,
            through_seq,
            summary,
            version,
            now,
            trigger,
            input_tokens,
            output_tokens,
            details,
        )

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
            trigger=row["trigger"],
            input_tokens=(
                int(row["input_tokens"]) if row["input_tokens"] is not None else None
            ),
            output_tokens=(
                int(row["output_tokens"]) if row["output_tokens"] is not None else None
            ),
            details=json.loads(row["details_json"]),
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
            name=row["name"],
        )

    @staticmethod
    def _row_to_turn(row: sqlite3.Row) -> Turn:
        return Turn(
            id=row["id"],
            session_id=row["session_id"],
            state=TurnStatus(row["state"]),
            prompt_event_seq=(
                int(row["prompt_event_seq"])
                if row["prompt_event_seq"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=(
                datetime.fromisoformat(row["started_at"])
                if row["started_at"]
                else None
            ),
            finished_at=(
                datetime.fromisoformat(row["finished_at"])
                if row["finished_at"]
                else None
            ),
            error_kind=row["error_kind"],
            error_message=row["error_message"],
        )
