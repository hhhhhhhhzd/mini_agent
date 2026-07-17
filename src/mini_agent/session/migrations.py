from __future__ import annotations

import os
import sqlite3
from pathlib import Path


CURRENT_SCHEMA_VERSION = 2


SCHEMA_V1 = """
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

CREATE INDEX IF NOT EXISTS idx_events_session_kind
ON events(session_id, kind, seq);

CREATE INDEX IF NOT EXISTS idx_sessions_state_updated
ON sessions(state, updated_at DESC);
"""


SCHEMA_V2 = """
ALTER TABLE sessions ADD COLUMN name TEXT;

ALTER TABLE checkpoints
ADD COLUMN trigger TEXT NOT NULL DEFAULT 'automatic';

ALTER TABLE checkpoints ADD COLUMN input_tokens INTEGER;
ALTER TABLE checkpoints ADD COLUMN output_tokens INTEGER;

ALTER TABLE checkpoints
ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}';

CREATE UNIQUE INDEX idx_sessions_root_name
ON sessions(project_root, name COLLATE NOCASE)
WHERE name IS NOT NULL;

CREATE TABLE turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    state TEXT NOT NULL,
    prompt_event_seq INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_kind TEXT,
    error_message TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX idx_turns_session_created
ON turns(session_id, created_at);

CREATE INDEX idx_turns_state
ON turns(state, created_at);

CREATE TABLE session_bindings (
    channel TEXT NOT NULL,
    external_session_id TEXT NOT NULL,
    internal_session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (channel, external_session_id),
    FOREIGN KEY (internal_session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX idx_session_bindings_internal
ON session_bindings(internal_session_id);
"""


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _run_migration(
    connection: sqlite3.Connection,
    *,
    sql: str,
    target_version: int,
) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + sql
            + f"\nPRAGMA user_version = {target_version};\nCOMMIT;"
        )
    except BaseException:
        connection.rollback()
        raise


def _backup_database(connection: sqlite3.Connection, path: Path) -> Path:
    backup_path = path.with_name(f"{path.name}.v1.bak")
    if backup_path.exists():
        return backup_path
    temporary = backup_path.with_name(f"{backup_path.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        destination = sqlite3.connect(temporary)
        try:
            connection.backup(destination)
        finally:
            destination.close()
        os.replace(temporary, backup_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return backup_path


def migrate(connection: sqlite3.Connection, path: Path) -> int:
    """Migrate a new or legacy v0.1 database to the current schema."""
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema {version} is newer than supported {CURRENT_SCHEMA_VERSION}"
        )

    legacy = version == 0 and _table_exists(connection, "sessions")
    new_database = version == 0 and not legacy
    if new_database:
        _run_migration(connection, sql=SCHEMA_V1, target_version=1)
        version = 1
    elif legacy:
        _backup_database(connection, path)
        version = 1

    if version < 2:
        if not new_database:
            _backup_database(connection, path)
        _run_migration(connection, sql=SCHEMA_V2, target_version=2)
        version = 2
    return version
