from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.context.compression import COMPRESSION_VERSION, FixedCompressor
from mini_agent.context.system_rules import SystemRuleLoader
from mini_agent.context.token_budget import TokenBudget
from mini_agent.core.types import Message, ModelResponseCompleted, TextDelta, ToolCall
from mini_agent.session import SqliteSessionStore
from mini_agent.session.models import SessionState


class SummaryModel:
    async def stream(self, messages, tools=()):
        assert "Previous checkpoint" in (messages[-1].content or "") or "New events" not in (messages[-1].content or "")
        yield TextDelta("Goal\ncontinue\nPending Work\ntest")
        yield ModelResponseCompleted({"total_tokens": 10}, "stop")


@pytest.mark.asyncio
async def test_session_lifecycle_messages_and_checkpoint(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "data" / "agent.db")
    await store.initialize()
    session = await store.create(tmp_path, system_rules="be concise", session_id="s1")
    assert session.state == SessionState.ACTIVE
    seq = await store.append_message("s1", Message(role="user", content="hello"))
    checkpoint = await store.save_checkpoint(
        "s1", through_seq=seq, summary="summary", version=COMPRESSION_VERSION
    )
    assert (await store.latest_checkpoint("s1")) == checkpoint
    assert (await store.load_messages("s1"))[0].message.content == "hello"
    assert (await store.archive("s1")).state == SessionState.ARCHIVED
    assert (await store.resume("s1")).state == SessionState.ACTIVE
    await store.delete("s1")
    with pytest.raises(KeyError):
        await store.get("s1")


def test_compression_selects_turn_boundary_and_keeps_tool_pair() -> None:
    budget = TokenBudget(400, trigger_ratio=0.75, target_ratio=0.5)
    compressor = FixedCompressor(SummaryModel(), budget)
    messages = [
        Message(role="user", content="first " * 30),
        Message(
            role="assistant",
            tool_calls=(ToolCall("call1", "read_file", {"path": "a"}),),
        ),
        Message(role="tool", content="result " * 30, tool_call_id="call1", name="read_file"),
        Message(role="assistant", content="done " * 20),
        Message(role="user", content="second " * 30),
        Message(role="assistant", content="answer " * 30),
    ]
    selected = compressor.select_prefix(messages, target_remaining_tokens=80)
    assert selected
    assert selected[-1].role == "assistant"
    assert any(message.tool_call_id == "call1" for message in selected)
    assert messages[len(selected)].role == "user"


def test_system_rule_precedence_and_hashes(tmp_path: Path) -> None:
    data = tmp_path / "global"
    project = tmp_path / "project"
    data.mkdir()
    project.mkdir()
    (data / "system.md").write_text("global", encoding="utf-8")
    (project / "AGENTS.md").write_text("project", encoding="utf-8")
    local = project / ".mini-agent"
    local.mkdir()
    (local / "system.md").write_text("project-local", encoding="utf-8")
    blocks = SystemRuleLoader(global_data_dir=data).load(project, "session")
    assert [block.scope for block in blocks] == ["global", "global", "project", "project", "session"]
    assert blocks[0].source == "builtin"
    assert all(len(block.content_hash) == 64 for block in blocks)
