from __future__ import annotations

from pathlib import Path
import os
import hashlib

import pytest

from mini_agent.core.types import ToolCall
from mini_agent.tools import ToolExecutor, ToolRegistry
from mini_agent.tools.builtin import register_builtin_tools
from mini_agent.tools.permissions import (
    GrantScope,
    PermissionDecision,
    PermissionRequest,
    PermissionResponse,
    PermissionManager,
    PermissionMode,
)


class RecordingBroker:
    def __init__(self, scope: GrantScope = GrantScope.ONCE) -> None:
        self.requests: list[PermissionRequest] = []
        self.scope = scope

    async def request(self, request: PermissionRequest) -> PermissionResponse:
        self.requests.append(request)
        return PermissionResponse(PermissionDecision.ALLOW, self.scope)


def make_executor(broker: RecordingBroker, *, shell: bool = False) -> ToolExecutor:
    registry = ToolRegistry()
    register_builtin_tools(registry, include_shell=shell)
    return ToolExecutor(registry, PermissionManager(broker))


def test_schema_defaults_are_normalized_before_execution(tmp_path: Path) -> None:
    executor = make_executor(RecordingBroker())
    normalized = executor.validate(
        ToolCall("1", "list_directory", {}), tmp_path
    )
    assert normalized.arguments["path"] == "."
    assert normalized.arguments["recursive"] is False


@pytest.mark.asyncio
async def test_read_is_auto_allowed_and_write_is_asked(tmp_path: Path) -> None:
    (tmp_path / "input.txt").write_text("hello", encoding="utf-8")
    broker = RecordingBroker()
    executor = make_executor(broker)
    read = await executor.execute(
        ToolCall("1", "read_file", {"path": "input.txt"}),
        session_id="s1",
        workspace_root=tmp_path,
    )
    assert not read.is_error and "hello" in read.content
    assert broker.requests == []

    write = await executor.execute(
        ToolCall("2", "write_file", {"path": "output.txt", "content": "ok"}),
        session_id="s1",
        workspace_root=tmp_path,
    )
    assert not write.is_error
    assert len(broker.requests) == 1
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.asyncio
async def test_hard_guard_runs_before_permission_prompt(tmp_path: Path) -> None:
    broker = RecordingBroker()
    executor = make_executor(broker)
    result = await executor.execute(
        ToolCall("1", "write_file", {"path": "../escape.txt", "content": "bad"}),
        session_id="s1",
        workspace_root=tmp_path,
    )
    assert result.is_error and "outside the workspace" in result.content
    assert broker.requests == []
    assert not (tmp_path.parent / "escape.txt").exists()


@pytest.mark.asyncio
async def test_search_does_not_follow_file_symlink_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("unique-outside-secret", encoding="utf-8")
    link = tmp_path / "linked.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this Windows installation")
    executor = make_executor(RecordingBroker())
    result = await executor.execute(
        ToolCall("1", "search_text", {"query": "unique-outside-secret"}),
        session_id="s1",
        workspace_root=tmp_path,
    )
    assert not result.is_error
    assert result.content == "[no matches]"


@pytest.mark.asyncio
async def test_session_grant_is_reused(tmp_path: Path) -> None:
    broker = RecordingBroker(GrantScope.SESSION)
    executor = make_executor(broker)
    for index in range(2):
        result = await executor.execute(
            ToolCall(
                str(index),
                "write_file",
                {"path": f"{index}.txt", "content": "ok"},
            ),
            session_id="s1",
            workspace_root=tmp_path,
        )
        assert not result.is_error
    assert len(broker.requests) == 1


@pytest.mark.asyncio
async def test_shell_requires_permission_and_runs_in_workspace(tmp_path: Path) -> None:
    broker = RecordingBroker(GrantScope.SESSION)
    executor = make_executor(broker, shell=True)
    result = await executor.execute(
        ToolCall(
            "1",
            "shell_exec",
            {"command": "Write-Output 'hello-shell'", "cwd": ".", "timeout_seconds": 10},
        ),
        session_id="s1",
        workspace_root=tmp_path,
    )
    assert not result.is_error and "hello-shell" in result.content
    assert len(broker.requests) == 1
    second = await executor.execute(
        ToolCall("2", "shell_exec", {"command": "Write-Output 'again'"}),
        session_id="s1",
        workspace_root=tmp_path,
    )
    assert not second.is_error
    assert len(broker.requests) == 2
    assert all(not request.allow_persistent for request in broker.requests)


@pytest.mark.asyncio
async def test_shell_nonzero_exit_is_a_tool_failure(tmp_path: Path) -> None:
    executor = make_executor(RecordingBroker(), shell=True)
    result = await executor.execute(
        ToolCall("1", "shell_exec", {"command": "exit 7"}),
        session_id="s1",
        workspace_root=tmp_path,
    )
    assert result.is_error
    assert "exit_code=7" in result.content


@pytest.mark.asyncio
async def test_trusted_mode_skips_broker_for_write_and_execute(tmp_path: Path) -> None:
    broker = RecordingBroker()
    registry = ToolRegistry()
    register_builtin_tools(registry, include_shell=True)
    executor = ToolExecutor(
        registry,
        PermissionManager(broker, mode=PermissionMode.TRUSTED),
    )
    write = await executor.execute(
        ToolCall("1", "write_file", {"path": "trusted.txt", "content": "ok"}),
        session_id="s1",
        workspace_root=tmp_path,
    )
    shell = await executor.execute(
        ToolCall("2", "shell_exec", {"command": "Write-Output trusted"}),
        session_id="s1",
        workspace_root=tmp_path,
    )
    assert not write.is_error
    assert not shell.is_error
    assert broker.requests == []


@pytest.mark.asyncio
async def test_locked_mode_allows_only_builtin_reads(tmp_path: Path) -> None:
    broker = RecordingBroker()
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = ToolExecutor(
        registry,
        PermissionManager(broker, mode=PermissionMode.LOCKED),
    )
    (tmp_path / "read.txt").write_text("ok", encoding="utf-8")
    read = await executor.execute(
        ToolCall("1", "read_file", {"path": "read.txt"}),
        session_id="s1",
        workspace_root=tmp_path,
    )
    write = await executor.execute(
        ToolCall("2", "write_file", {"path": "blocked.txt", "content": "no"}),
        session_id="s1",
        workspace_root=tmp_path,
    )
    assert not read.is_error
    assert write.is_error and write.content == "Permission denied"
    assert broker.requests == []


@pytest.mark.asyncio
async def test_write_and_patch_use_expected_hash_guard(tmp_path: Path) -> None:
    broker = RecordingBroker(GrantScope.SESSION)
    executor = make_executor(broker)
    target = tmp_path / "guarded.txt"
    target.write_text("before", encoding="utf-8")
    expected = hashlib.sha256(b"before").hexdigest()

    written = await executor.execute(
        ToolCall(
            "1",
            "write_file",
            {
                "path": "guarded.txt",
                "content": "middle",
                "overwrite": True,
                "expected_sha256": expected,
            },
        ),
        session_id="s1",
        workspace_root=tmp_path,
    )
    assert not written.is_error
    assert target.read_text(encoding="utf-8") == "middle"
    assert not list(tmp_path.glob(".guarded.txt.*.tmp"))

    stale = await executor.execute(
        ToolCall(
            "2",
            "apply_patch",
            {
                "path": "guarded.txt",
                "old_text": "middle",
                "new_text": "after",
                "expected_sha256": expected,
            },
        ),
        session_id="s1",
        workspace_root=tmp_path,
    )
    assert stale.is_error
    assert "File changed since it was read" in stale.content
    assert target.read_text(encoding="utf-8") == "middle"
