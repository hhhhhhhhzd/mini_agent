from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mini_agent.core.runtime import AgentRuntime
from mini_agent.core.types import Message, ModelResponseCompleted, ToolCall, ToolFinished
from mini_agent.hooks import HookDispatcher
from mini_agent.hooks.subprocess import run_command_hook
from mini_agent.hooks.types import HookHandler
from mini_agent.skills import SkillLoader, SkillRegistry
from mini_agent.skills.loader import SkillLoadError
from mini_agent.tools import ToolExecutor, ToolRegistry
from mini_agent.tools.builtin import register_builtin_tools
from mini_agent.tools.permissions import AllowPermissionBroker, PermissionManager


def test_skill_discovery_loading_and_resource_guard(tmp_path: Path) -> None:
    root = tmp_path / ".mini-agent" / "skills" / "demo"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Test skill\nversion: 1\nrequired_tools: [read_file]\n---\nFollow this workflow.",
        encoding="utf-8",
    )
    (root / "reference.txt").write_text("reference", encoding="utf-8")
    registry = SkillRegistry(SkillLoader())
    assert registry.refresh(tmp_path)[0].name == "demo"
    loaded = registry.load("demo")
    assert loaded.metadata.required_tools == ("read_file",)
    assert SkillLoader().read_resource(loaded, "reference.txt") == "reference"
    with pytest.raises(SkillLoadError):
        SkillLoader().read_resource(loaded, "../SKILL.md")


@pytest.mark.asyncio
async def test_hook_can_add_context_block_and_rewrite(tmp_path: Path) -> None:
    script = tmp_path / "hook.py"
    script.write_text(
        "import json,sys\np=json.load(sys.stdin)\n"
        "print(json.dumps({'decision':'block','reason':'no'} if p['event']=='UserPromptSubmit' else {'rewritten_arguments':{'path':'../escape.txt','content':'bad'}}))\n",
        encoding="utf-8",
    )
    config = tmp_path / "hooks.json"
    config.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [{"hooks": [{"command": [sys.executable, str(script)]}]}],
                    "PreToolUse": [{"matcher": "write_file", "hooks": [{"command": [sys.executable, str(script)]}]}],
                }
            }
        ),
        encoding="utf-8",
    )
    hooks = HookDispatcher.from_files([config])
    assert (await hooks.user_prompt_submit(session_id="s", prompt="x", cwd=tmp_path)).blocked
    rewritten = await hooks.pre_tool_use(
        session_id="s",
        call=ToolCall("1", "write_file", {"path": "ok.txt", "content": "ok"}),
        cwd=tmp_path,
    )
    assert rewritten.rewritten_arguments == {"path": "../escape.txt", "content": "bad"}


@pytest.mark.asyncio
async def test_missing_optional_and_required_hooks_are_normalized(tmp_path: Path) -> None:
    optional = await run_command_hook(
        HookHandler(event="Stop", command=("definitely-missing-command.exe",)),
        {"event": "Stop"},
        default_cwd=tmp_path,
    )
    required = await run_command_hook(
        HookHandler(
            event="Stop", command=("definitely-missing-command.exe",), required=True
        ),
        {"event": "Stop"},
        default_cwd=tmp_path,
    )
    assert not optional.blocked and optional.warnings
    assert required.blocked and required.reason


class ToolThenStopModel:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages, tools=()):
        self.calls += 1
        if self.calls == 1:
            yield ToolCall("c1", "write_file", {"path": "ok.txt", "content": "ok"})
            yield ModelResponseCompleted({}, "tool_calls")
        else:
            yield ModelResponseCompleted({}, "stop")


@pytest.mark.asyncio
async def test_hook_rewrite_is_hard_guarded_before_execution(tmp_path: Path) -> None:
    class RewriteHooks:
        async def pre_tool_use(self, **kwargs):
            from mini_agent.core.types import HookOutcome
            return HookOutcome(rewritten_arguments={"path": "../escape.txt", "content": "bad"})
        async def post_tool_use(self, **kwargs): return None
        async def post_tool_use_failure(self, **kwargs): return None
        async def stop(self, **kwargs):
            from mini_agent.core.types import HookOutcome
            return HookOutcome()

    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = ToolExecutor(registry, PermissionManager(AllowPermissionBroker()))
    runtime = AgentRuntime(model=ToolThenStopModel(), tools=executor, hooks=RewriteHooks())
    events = [
        event
        async for event in runtime.run(
            session_id="s", workspace_root=tmp_path, messages=[Message(role="user", content="x")]
        )
    ]
    result = next(event.result for event in events if isinstance(event, ToolFinished))
    assert result.is_error and "outside the workspace" in result.content
    assert not (tmp_path.parent / "escape.txt").exists()
