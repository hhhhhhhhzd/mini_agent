from __future__ import annotations

from datetime import datetime, timezone
from contextlib import asynccontextmanager
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
        self.sessions = {self.session.id: self.session}
        self.bindings: dict[str, str] = {}
        self.created = 0
        self.commands = []
        self.run_sessions = []

    async def create_session(self, root: Path, *, name: str | None = None):
        self.created += 1
        session_id = "s1" if self.created == 1 else f"s{self.created}"
        self.session = make_session(root, session_id)
        if name:
            object.__setattr__(self.session, "name", name)
        self.sessions[session_id] = self.session
        return self.session

    async def get_session(self, session_id: str): return self.sessions[session_id]
    async def resume_session(self, session_id: str): return self.sessions[session_id]
    async def list_sessions(self, *, include_archived=True, project_root=None):
        sessions = list(self.sessions.values())
        return sessions if project_root is None else [s for s in sessions if s.project_root == project_root.resolve()]
    async def resolve_session(self, reference: str, project_root: Path):
        matches = [s for s in self.sessions.values() if s.project_root == project_root.resolve() and (s.id.startswith(reference) or s.name == reference)]
        if len(matches) != 1: raise KeyError(reference)
        return matches[0]
    async def archive_session(self, session_id: str):
        self.archived = True
        return self.sessions[session_id]
    async def delete_session(self, session_id: str): self.deleted = True
    async def session_history(self, session_id: str):
        return [Message(role="user", content="old"), Message(role="assistant", content="reply")]
    async def run_turn(self, session_id: str, prompt: str):
        assert prompt == "hello"
        self.run_sessions.append(session_id)
        yield TextDelta("answer")
        call = ToolCall("c1", "read_file", {"path": "x"})
        yield ToolStarted(call)
        yield ToolFinished(ToolResult("c1", "read_file", "ok"))
        yield TurnCompleted({"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})
    async def bind_session(self, *, channel: str, external_session_id: str, internal_session_id: str):
        self.bindings[f"{channel}:{external_session_id}"] = internal_session_id
    async def resolve_binding(self, *, channel: str, external_session_id: str):
        return self.bindings.get(f"{channel}:{external_session_id}")
    async def cancel_turn(self, session_id: str): return True
    async def compact_session(self, session_id: str, *, trigger: str = "manual"): return None
    async def record_command(self, session_id: str, *, name: str, argument: str | None, result_session_id: str | None = None):
        self.commands.append((session_id, name, argument, result_session_id))
    @asynccontextmanager
    async def session_gate(self, session_id: str):
        yield


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
    replay_types = [getattr(item, "session_update", None) for _, item in client.updates]
    assert replay_types.count("available_commands_update") == 2
    assert replay_types.count("user_message_chunk") == 1
    assert replay_types.count("agent_message_chunk") == 1
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
async def test_acp_commands_share_dispatcher_and_switch_binding(tmp_path: Path) -> None:
    app = FakeApplication(tmp_path)
    broker = AcpPermissionBroker()
    agent = AcpAgent(app, broker)  # type: ignore[arg-type]
    client = FakeClient()
    agent.on_connect(client)  # type: ignore[arg-type]
    created = await agent.new_session(str(tmp_path))

    response = await agent.prompt(
        created.session_id,
        [TextContentBlock(type="text", text="/new named")],
    )
    assert response.stop_reason == "end_turn"
    assert app.bindings["acp:s1"] == "s2"
    assert app.sessions["s2"].name == "named"
    assert any(
        getattr(update, "session_update", None) == "agent_message_chunk"
        and "Created session" in update.content.text
        for _, update in client.updates
    )

    restarted = AcpAgent(app, AcpPermissionBroker())  # type: ignore[arg-type]
    restarted_client = FakeClient()
    restarted.on_connect(restarted_client)  # type: ignore[arg-type]
    await restarted.resume_session("s1", str(tmp_path))
    await restarted.prompt(
        "s1",
        [TextContentBlock(type="text", text="hello")],
    )
    assert app.run_sessions[-1] == "s2"

    response = await agent.prompt(
        created.session_id,
        [TextContentBlock(type="text", text="/resume s1")],
    )
    assert response.stop_reason == "end_turn"
    assert app.bindings["acp:s1"] == "s1"


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
