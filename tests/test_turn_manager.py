from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mini_agent.core.types import TextDelta, ToolCall, ToolFinished, ToolResult, ToolStarted, TurnCompleted
from mini_agent.session import SqliteSessionStore
from mini_agent.turns.manager import TurnManager
from mini_agent.turns.models import TurnStatus


async def collect(stream):
    return [event async for event in stream]


@pytest.mark.asyncio
async def test_same_session_turns_are_fifo(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "agent.db")
    await store.initialize()
    await store.create(tmp_path, session_id="s1")
    manager = TurnManager(store)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def first(_turn_id: str):
        order.append("first-start")
        first_started.set()
        await release_first.wait()
        order.append("first-end")
        yield TurnCompleted()

    async def second(_turn_id: str):
        order.append("second-start")
        yield TurnCompleted()

    first_task = asyncio.create_task(collect(manager.run(session_id="s1", runner=first)))
    await first_started.wait()
    second_task = asyncio.create_task(collect(manager.run(session_id="s1", runner=second)))
    await asyncio.sleep(0)
    assert order == ["first-start"]
    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert order == ["first-start", "first-end", "second-start"]
    assert [turn.state for turn in await store.list_turns("s1")] == [
        TurnStatus.COMPLETED,
        TurnStatus.COMPLETED,
    ]


@pytest.mark.asyncio
async def test_different_sessions_run_concurrently(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "agent.db")
    await store.initialize()
    await store.create(tmp_path, session_id="s1")
    await store.create(tmp_path, session_id="s2")
    manager = TurnManager(store)
    both_started = asyncio.Event()
    release = asyncio.Event()
    started: set[str] = set()

    def runner(label: str):
        async def run(_turn_id: str):
            started.add(label)
            if len(started) == 2:
                both_started.set()
            await release.wait()
            yield TurnCompleted()

        return run

    tasks = [
        asyncio.create_task(collect(manager.run(session_id="s1", runner=runner("s1")))),
        asyncio.create_task(collect(manager.run(session_id="s2", runner=runner("s2")))),
    ]
    await asyncio.wait_for(both_started.wait(), timeout=2)
    assert started == {"s1", "s2"}
    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_cancel_updates_turn_and_tool_waiting_state(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "agent.db")
    await store.initialize()
    await store.create(tmp_path, session_id="s1")
    manager = TurnManager(store)
    tool_started = asyncio.Event()

    async def runner(_turn_id: str):
        call = ToolCall("c1", "shell_exec", {"command": "wait"})
        yield ToolStarted(call)
        tool_started.set()
        await asyncio.Event().wait()
        yield ToolFinished(ToolResult("c1", "shell_exec", "never"))
        yield TurnCompleted()

    task = asyncio.create_task(collect(manager.run(session_id="s1", runner=runner)))
    await tool_started.wait()
    turns = await store.list_turns("s1")
    assert turns[0].state == TurnStatus.WAITING_TOOL
    assert await manager.cancel("s1")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await store.list_turns("s1"))[0].state == TurnStatus.CANCELLED
    assert not await manager.cancel("s1")


@pytest.mark.asyncio
async def test_session_gate_waits_for_active_turn(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "agent.db")
    await store.initialize()
    await store.create(tmp_path, session_id="s1")
    manager = TurnManager(store)
    started = asyncio.Event()
    release = asyncio.Event()
    gate_entered = asyncio.Event()

    async def runner(_turn_id: str):
        started.set()
        await release.wait()
        yield TextDelta("done")
        yield TurnCompleted()

    turn_task = asyncio.create_task(collect(manager.run(session_id="s1", runner=runner)))
    await started.wait()

    async def enter_gate() -> None:
        async with manager.session_gate("s1"):
            gate_entered.set()

    gate_task = asyncio.create_task(enter_gate())
    await asyncio.sleep(0)
    assert not gate_entered.is_set()
    release.set()
    await asyncio.gather(turn_task, gate_task)
    assert gate_entered.is_set()
