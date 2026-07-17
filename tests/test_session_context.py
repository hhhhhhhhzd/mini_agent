from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.context.builder import ContextBuilder
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


class StagedSummaryModel:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages, tools=()):
        self.calls += 1
        yield TextDelta(f"stage-{self.calls}")
        yield ModelResponseCompleted(
            {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
            "stop",
        )


class FailingSummaryModel:
    async def stream(self, messages, tools=()):
        raise RuntimeError("summary unavailable")
        yield  # pragma: no cover


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


@pytest.mark.asyncio
async def test_oversized_compression_uses_stages_and_reports_metadata() -> None:
    model = StagedSummaryModel()
    compressor = FixedCompressor(model, TokenBudget(400))
    messages = [
        Message(role="user", content="first " * 300),
        Message(role="assistant", content="answer " * 300),
        Message(role="user", content="latest"),
        Message(role="assistant", content="reply"),
    ]
    result = await compressor.compress(messages, target_remaining_tokens=0)
    assert result is not None
    assert result.stage_count > 1
    assert model.calls == result.stage_count
    assert result.input_tokens == result.stage_count * 11
    assert result.output_tokens == result.stage_count * 3


@pytest.mark.asyncio
async def test_automatic_compression_failure_falls_back_without_checkpoint(
    tmp_path: Path,
) -> None:
    budget = TokenBudget(100, trigger_ratio=0.1, target_ratio=0.05)
    builder = ContextBuilder(
        rules=SystemRuleLoader(global_data_dir=tmp_path / "global"),
        budget=budget,
        compressor=FixedCompressor(FailingSummaryModel(), budget),
    )
    messages = [
        Message(role="user", content="old " * 100),
        Message(role="assistant", content="answer " * 100),
        Message(role="user", content="new"),
    ]
    built, _, result = await builder.build(
        session_id="s1",
        project_root=tmp_path,
        messages=messages,
        session_rules=None,
        existing_summary=None,
        skill_instructions=(),
        additional_context=(),
        tools=(),
    )
    assert result.compression is None
    assert "summary unavailable" in (result.compression_error or "")
    assert any(message.content == "old " * 100 for message in built)


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
