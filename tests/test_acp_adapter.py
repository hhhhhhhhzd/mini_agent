from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from acp.schema import (
    AllowedOutcome,
    RequestPermissionResponse,
    TextContentBlock,
)

from mini_agent.core.types import (
    Message,
    TextDelta,
    ToolCall,
    ToolFinished,
    ToolResult,
    ToolStarted,
    TurnCompleted,
)
from mini_agent.protocols.acp.agent import AcpAgent
from mini_agent.protocols.acp.permissions import AcpPermissionBroker
from mini_agent.session.models import Session, SessionState
from mini_agent.tools.permissions import GrantScope, PermissionDecision, PermissionRequest
from mini_agent.core.types import ToolSpec


def make_session(root: Path, session_id: str = "s1") -> Session:
    now = datetime.now(timezone.utc)
    return Session(session_id, SessionState.ACTIVE, root, None, (), now, now)


class FakeApplication:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.session = make_session(root)
        self.archived = False
        self.deleted = False

    async def create_session(self, root: Path):
        self.session = make_session(root)
        return self.session

    async def resume_session(self, session_id: str): return self.session
    async def list_sessions(self): return [self.session]
    async def archive_session(self, session_id: str):
        self.archived = True
        return self.session
    async def delete_session(self, session_id: str): self.deleted = True
    async def session_history(self, session_id: str):
        return [Message(role="user", content="old"), Message(role="assistant", content="reply")]
    async def run_turn(self, session_id: str, prompt: str):
        assert prompt == "hello"
        yield TextDelta("answer")
        call = ToolCall("c1", "read_file", {"path": "x"})
        yield ToolStarted(call)
        yield ToolFinished(ToolResult("c1", "read_file", "ok"))
        yield TurnCompleted({"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})


class FakeClient:
    def __init__(self) -> None:
        self.updates = []
        self.permission_requests = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append((session_id, update))

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.permission_requests.append((session_id, tool_call, options))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", optionId="allow_project")
        )


@pytest.mark.asyncio
async def test_acp_lifecycle_replay_and_event_mapping(tmp_path: Path) -> None:
    app = FakeApplication(tmp_path)
    broker = AcpPermissionBroker()
    agent = AcpAgent(app, broker)  # type: ignore[arg-type]
    client = FakeClient()
    agent.on_connect(client)  # type: ignore[arg-type]

    initialized = await agent.initialize(1)
    assert initialized.agent_capabilities.load_session
    created = await agent.new_session(str(tmp_path))
    assert created.session_id == "s1"
    await agent.load_session(str(tmp_path), "s1")
    assert len(client.updates) == 2
    response = await agent.prompt(
        "s1", [TextContentBlock(type="text", text="hello")]
    )
    assert response.stop_reason == "end_turn"
    assert response.usage and response.usage.total_tokens == 5
    update_types = [getattr(item, "session_update", None) for _, item in client.updates]
    assert "agent_message_chunk" in update_types
    assert "tool_call" in update_types
    assert "tool_call_update" in update_types
    assert "usage_update" in update_types
    await agent.close_session("s1")
    assert app.archived
    await agent.ext_method("mini_agent/session/delete", {"sessionId": "s1"})
    assert app.deleted


@pytest.mark.asyncio
async def test_acp_permission_broker_maps_project_scope(tmp_path: Path) -> None:
    broker = AcpPermissionBroker()
    client = FakeClient()
    broker.bind(client)  # type: ignore[arg-type]
    response = await broker.request(
        PermissionRequest(
            session_id="s1",
            workspace_root=tmp_path,
            tool=ToolSpec(
                id="tool-id",
                name="write_file",
                description="write",
                input_schema={"type": "object"},
                source="builtin",
                risk="write",
            ),
            arguments={"path": "x"},
            warning="warning",
        )
    )
    assert response.decision == PermissionDecision.ALLOW
    assert response.scope == GrantScope.PROJECT
