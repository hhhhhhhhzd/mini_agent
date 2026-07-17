from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mini_agent.commands import CommandDispatcher, parse_command
from mini_agent.session.models import Session, SessionCheckpoint, SessionState


def _session(session_id: str, root: Path, name: str | None = None) -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        id=session_id,
        state=SessionState.ACTIVE,
        project_root=root.resolve(),
        system_rules=None,
        active_skills=(),
        created_at=now,
        updated_at=now,
        name=name,
    )


class FakeApplication:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.sessions = [_session("first-session", root, "first")]
        self.gated: list[str] = []
        self.compacted: list[str] = []
        self.commands: list[tuple[str, str, str | None, str | None]] = []

    async def create_session(self, project_root: Path, *, name: str | None = None):
        session = _session(f"session-{len(self.sessions) + 1}", project_root, name)
        self.sessions.append(session)
        return session

    async def list_sessions(self, *, include_archived=True, project_root=None):
        return [item for item in self.sessions if item.project_root == project_root.resolve()]

    async def resolve_session(self, reference: str, project_root: Path):
        matches = [
            item
            for item in self.sessions
            if item.project_root == project_root.resolve()
            and (item.id.startswith(reference) or item.name == reference)
        ]
        if len(matches) != 1:
            raise KeyError(reference)
        return matches[0]

    async def resume_session(self, session_id: str):
        return next(item for item in self.sessions if item.id == session_id)

    async def compact_session(self, session_id: str, *, trigger: str = "manual"):
        self.compacted.append(session_id)
        now = datetime.now(timezone.utc)
        return SessionCheckpoint(session_id, 7, "summary", "v1", now, trigger)

    @asynccontextmanager
    async def session_gate(self, session_id: str):
        self.gated.append(session_id)
        yield

    async def record_command(
        self, session_id: str, *, name: str, argument: str | None,
        result_session_id: str | None = None,
    ) -> None:
        self.commands.append((session_id, name, argument, result_session_id))


def test_parse_only_shared_commands() -> None:
    assert parse_command("hello") is None
    assert parse_command("/skill x") is None
    assert parse_command(" /new named session ").argument == "named session"
    assert parse_command("/LIST").name == "list"


@pytest.mark.asyncio
async def test_shared_commands_switch_and_list_sessions(tmp_path: Path) -> None:
    app = FakeApplication(tmp_path)
    dispatcher = CommandDispatcher(app, tmp_path)

    created = await dispatcher.dispatch("/new second", session_id="first-session")
    assert created.handled and created.session_id == "session-2"
    assert app.gated == ["first-session"]
    assert app.commands[0] == ("first-session", "new", "second", "session-2")

    listed = await dispatcher.dispatch("/list", session_id="session-2")
    assert "first (first-se)" in (listed.output or "")
    assert "* second (session-)" in (listed.output or "")
    assert app.gated == ["first-session"]
    assert app.commands[-1][1] == "list"

    resumed = await dispatcher.dispatch("/resume first", session_id="session-2")
    assert resumed.session_id == "first-session"
    assert app.gated[-1] == "session-2"


@pytest.mark.asyncio
async def test_zip_uses_gate_and_does_not_become_prompt(tmp_path: Path) -> None:
    app = FakeApplication(tmp_path)
    dispatcher = CommandDispatcher(app, tmp_path)
    result = await dispatcher.dispatch("/zip", session_id="first-session")
    assert result.handled
    assert result.output == "Compressed session through event 7 (v1)."
    assert app.compacted == ["first-session"]
    assert app.gated == ["first-session"]


@pytest.mark.asyncio
async def test_command_validation_error_is_returned(tmp_path: Path) -> None:
    dispatcher = CommandDispatcher(FakeApplication(tmp_path), tmp_path)
    result = await dispatcher.dispatch("/resume", session_id="first-session")
    assert result.handled
    assert result.error == "Usage: /resume <session-id|name>"
