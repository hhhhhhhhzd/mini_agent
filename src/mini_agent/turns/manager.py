from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mini_agent.core.types import (
    AgentError,
    AgentEvent,
    ToolFinished,
    ToolStarted,
    TurnCompleted,
)
from mini_agent.session.store import SqliteSessionStore
from mini_agent.turns.cancellation import CancellationToken
from mini_agent.turns.models import TurnStatus


@dataclass
class _ActiveTurn:
    turn_id: str
    task: asyncio.Task[object]
    token: CancellationToken


class TurnManager:
    """Serialize Turns per Session and own their cancellation/lifecycle state."""

    def __init__(self, store: SqliteSessionStore) -> None:
        self._store = store
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: dict[str, _ActiveTurn] = {}

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def recover(self) -> int:
        return await self._store.recover_interrupted_turns()

    @asynccontextmanager
    async def session_gate(self, session_id: str) -> AsyncIterator[None]:
        async with self._lock(session_id):
            yield

    async def cancel(self, session_id: str) -> bool:
        active = self._active.get(session_id)
        if active is None or active.task.done():
            return False
        active.token.cancel()
        active.task.cancel()
        return True

    async def run(
        self,
        *,
        session_id: str,
        runner: Callable[[str], AsyncIterator[AgentEvent]],
    ) -> AsyncIterator[AgentEvent]:
        turn_id = uuid.uuid4().hex
        async with self._lock(session_id):
            await self._store.create_turn(session_id, turn_id=turn_id)
            await self._store.update_turn(turn_id, TurnStatus.RUNNING)
            task = asyncio.current_task()
            if task is None:
                raise RuntimeError("TurnManager requires an asyncio Task")
            token = CancellationToken()
            self._active[session_id] = _ActiveTurn(turn_id, task, token)
            terminal = False
            failed = False
            try:
                async for event in runner(turn_id):
                    if isinstance(event, ToolStarted):
                        await self._store.update_turn(turn_id, TurnStatus.WAITING_TOOL)
                    elif isinstance(event, ToolFinished):
                        await self._store.update_turn(turn_id, TurnStatus.RUNNING)
                    elif isinstance(event, AgentError):
                        failed = True
                    elif isinstance(event, TurnCompleted):
                        state = TurnStatus.FAILED if failed else TurnStatus.COMPLETED
                        await self._store.update_turn(
                            turn_id,
                            state,
                            error_kind="agent_error" if failed else None,
                            error_message=(
                                "Turn completed after an AgentError" if failed else None
                            ),
                        )
                        terminal = True
                    yield event
                if not terminal:
                    state = TurnStatus.FAILED if failed else TurnStatus.INTERRUPTED
                    await self._store.update_turn(
                        turn_id,
                        state,
                        error_kind=(
                            "agent_error" if failed else "incomplete_event_stream"
                        ),
                        error_message=(
                            "Agent ended without TurnCompleted"
                            if not failed
                            else "Agent emitted an error without TurnCompleted"
                        ),
                    )
            except asyncio.CancelledError:
                await self._store.update_turn(
                    turn_id,
                    TurnStatus.CANCELLED,
                    error_kind="cancelled",
                    error_message="Turn cancelled",
                )
                terminal = True
                raise
            except Exception as exc:
                await self._store.update_turn(
                    turn_id,
                    TurnStatus.FAILED,
                    error_kind=type(exc).__name__,
                    error_message=str(exc),
                )
                terminal = True
                yield AgentError(str(exc), fatal=True)
            finally:
                active = self._active.get(session_id)
                if active is not None and active.turn_id == turn_id:
                    self._active.pop(session_id, None)
