from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mini_agent.session import CURRENT_SCHEMA_VERSION, SqliteSessionStore
from mini_agent.session.migrations import SCHEMA_V1
from mini_agent.turns import TurnStatus


def database_version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


@pytest.mark.asyncio
async def test_new_database_uses_current_schema_without_backup(tmp_path: Path) -> None:
    path = tmp_path / "agent.db"
    store = SqliteSessionStore(path)
    await store.initialize()
    assert database_version(path) == CURRENT_SCHEMA_VERSION
    assert not path.with_name("agent.db.v1.bak").exists()


@pytest.mark.asyncio
async def test_legacy_v01_database_is_backed_up_and_migrated(tmp_path: Path) -> None:
    path = tmp_path / "agent.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_V1)
        connection.execute(
            """
            INSERT INTO sessions(
                id, state, project_root, system_rules, active_skills_json,
                created_at, updated_at, archived_at
            ) VALUES ('legacy', 'active', ?, NULL, '[]', ?, ?, NULL)
            """,
            (str(tmp_path.resolve()), "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )

    store = SqliteSessionStore(path)
    await store.initialize()

    assert database_version(path) == CURRENT_SCHEMA_VERSION
    backup = path.with_name("agent.db.v1.bak")
    assert backup.is_file()
    assert database_version(backup) == 0
    assert (await store.get("legacy")).name is None


@pytest.mark.asyncio
async def test_failed_migration_preserves_legacy_database(tmp_path: Path) -> None:
    path = tmp_path / "agent.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                project_root TEXT NOT NULL,
                system_rules TEXT,
                active_skills_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT
            );
            """
        )

    with pytest.raises(sqlite3.OperationalError):
        await SqliteSessionStore(path).initialize()

    assert path.with_name("agent.db.v1.bak").is_file()
    assert database_version(path) == 0
    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
        }
    assert "name" not in columns


@pytest.mark.asyncio
async def test_session_names_turn_recovery_and_bindings(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "agent.db")
    await store.initialize()
    first = await store.create(tmp_path, session_id="s1", name="Alpha Session")
    assert first.name == "Alpha Session"
    with pytest.raises(ValueError, match="already exists"):
        await store.create(tmp_path, session_id="s2", name="alpha session")

    assert (await store.resolve("Alpha Session", tmp_path)).id == "s1"
    assert (await store.resolve("s1", tmp_path)).id == "s1"

    turn = await store.create_turn("s1", turn_id="t1")
    assert turn.state == TurnStatus.QUEUED
    await store.update_turn("t1", TurnStatus.RUNNING, prompt_event_seq=3)
    await store.update_turn("t1", TurnStatus.WAITING_TOOL)
    assert await store.recover_interrupted_turns() == 1
    recovered = await store.get_turn("t1")
    assert recovered.state == TurnStatus.INTERRUPTED
    assert recovered.prompt_event_seq == 3
    assert recovered.error_kind == "process_restart"

    await store.bind_session(
        channel="acp", external_session_id="external", internal_session_id="s1"
    )
    assert (
        await store.resolve_binding(channel="acp", external_session_id="external")
        == "s1"
    )

    checkpoint = await store.save_checkpoint(
        "s1",
        through_seq=3,
        summary="summary",
        version="v2",
        trigger="manual",
        input_tokens=10,
        output_tokens=4,
        details={"turns": 2},
    )
    loaded = await store.latest_checkpoint("s1")
    assert loaded == checkpoint

    await store.delete("s1")
    assert (
        await store.resolve_binding(channel="acp", external_session_id="external")
        is None
    )
