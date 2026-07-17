from __future__ import annotations

from pathlib import Path
from typing import AsyncContextManager, Protocol

from mini_agent.commands.models import CommandResult, ParsedCommand
from mini_agent.commands.parser import parse_command
from mini_agent.session.models import Session, SessionCheckpoint


class CommandApplication(Protocol):
    async def create_session(self, project_root: Path, *, name: str | None = None) -> Session: ...

    async def list_sessions(
        self, *, include_archived: bool = True, project_root: Path | None = None
    ) -> list[Session]: ...

    async def resolve_session(self, reference: str, project_root: Path) -> Session: ...

    async def resume_session(self, session_id: str) -> Session: ...

    async def compact_session(
        self, session_id: str, *, trigger: str = "manual"
    ) -> SessionCheckpoint | None: ...

    def session_gate(self, session_id: str) -> AsyncContextManager[None]: ...

    async def record_command(
        self,
        session_id: str,
        *,
        name: str,
        argument: str | None,
        result_session_id: str | None = None,
    ) -> None: ...


class CommandDispatcher:
    """Shared command semantics used by CLI and protocol adapters."""

    def __init__(self, application: CommandApplication, project_root: Path) -> None:
        self._application = application
        self._project_root = project_root.resolve()

    async def dispatch(self, text: str, *, session_id: str) -> CommandResult:
        command = parse_command(text)
        if command is None:
            return CommandResult(handled=False, session_id=session_id)
        try:
            return await self._execute(command, session_id)
        except (KeyError, ValueError, RuntimeError) as exc:
            return CommandResult(
                handled=True,
                session_id=session_id,
                error=str(exc),
            )

    async def _execute(
        self, command: ParsedCommand, current_session_id: str
    ) -> CommandResult:
        if command.name == "list":
            if command.argument:
                raise ValueError("Usage: /list")
            sessions = await self._application.list_sessions(
                project_root=self._project_root
            )
            lines = [self._format_session(item, current_session_id) for item in sessions]
            await self._application.record_command(
                current_session_id,
                name="list",
                argument=None,
            )
            return CommandResult(
                handled=True,
                session_id=current_session_id,
                output="\n".join(lines) if lines else "No sessions in this workspace.",
            )

        async with self._application.session_gate(current_session_id):
            if command.name == "new":
                session = await self._application.create_session(
                    self._project_root,
                    name=command.argument,
                )
                await self._application.record_command(
                    current_session_id,
                    name="new",
                    argument=command.argument,
                    result_session_id=session.id,
                )
                return CommandResult(
                    handled=True,
                    session_id=session.id,
                    output=f"Created session {self._label(session)}.",
                )
            if command.name == "resume":
                if not command.argument:
                    raise ValueError("Usage: /resume <session-id|name>")
                session = await self._application.resolve_session(
                    command.argument,
                    self._project_root,
                )
                session = await self._application.resume_session(session.id)
                await self._application.record_command(
                    current_session_id,
                    name="resume",
                    argument=command.argument,
                    result_session_id=session.id,
                )
                return CommandResult(
                    handled=True,
                    session_id=session.id,
                    output=f"Resumed session {self._label(session)}.",
                )
            if command.name == "zip":
                if command.argument:
                    raise ValueError("Usage: /zip")
                checkpoint = await self._application.compact_session(
                    current_session_id,
                    trigger="manual",
                )
                await self._application.record_command(
                    current_session_id,
                    name="zip",
                    argument=None,
                )
                if checkpoint is None:
                    output = "Nothing to compress: no complete new turn is available."
                else:
                    output = (
                        "Compressed session through event "
                        f"{checkpoint.through_seq} ({checkpoint.version})."
                    )
                return CommandResult(
                    handled=True,
                    session_id=current_session_id,
                    output=output,
                )
        raise RuntimeError(f"Unsupported command: /{command.name}")

    @staticmethod
    def _label(session: Session) -> str:
        if session.name:
            return f"{session.name} ({session.id[:8]})"
        return session.id

    @classmethod
    def _format_session(cls, session: Session, current_session_id: str) -> str:
        marker = "*" if session.id == current_session_id else " "
        updated = session.updated_at.astimezone().isoformat(timespec="seconds")
        return f"{marker} {cls._label(session)}\t{session.state.value}\t{updated}"
