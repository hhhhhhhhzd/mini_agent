from __future__ import annotations

from pathlib import Path

from mini_agent.config import AgentConfig
from mini_agent.context import ContextBuilder, FixedCompressor, SystemRuleLoader, TokenBudget
from mini_agent.core.application import AgentApplication
from mini_agent.core.runtime import AgentRuntime
from mini_agent.hooks import HookDispatcher
from mini_agent.model_api import FixedModelClient
from mini_agent.session import SqliteSessionStore
from mini_agent.skills import SkillLoader, SkillRegistry
from mini_agent.tools import ToolExecutor, ToolRegistry
from mini_agent.tools.builtin import register_builtin_tools
from mini_agent.tools.permissions import CliPermissionBroker, PermissionBroker, PermissionManager
from mini_agent.tools.providers.mcp import McpManager, load_mcp_config
from mini_agent.turns.manager import TurnManager


def build_application(
    config: AgentConfig,
    *,
    permission_broker: PermissionBroker | None = None,
) -> tuple[AgentApplication, FixedModelClient]:
    model = FixedModelClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model_id=config.model_id,
        max_output_tokens=config.max_output_tokens,
        timeout_seconds=config.request_timeout_seconds,
    )
    registry = ToolRegistry()
    register_builtin_tools(registry, include_shell=config.shell_enabled)
    mcp_configs = load_mcp_config(
        [
            config.data_dir / "mcp.json",
            config.workspace_root / ".mini-agent" / "mcp.json",
        ]
    )
    mcp = McpManager(registry, mcp_configs)
    permissions = PermissionManager(
        permission_broker or CliPermissionBroker(),
        mode=config.permission_mode,
    )
    executor = ToolExecutor(registry, permissions)

    hook_paths = [
        config.data_dir / "hooks.json",
        config.workspace_root / ".mini-agent" / "hooks.json",
    ]
    hooks = HookDispatcher.from_files(hook_paths)
    budget = TokenBudget(config.context_window)
    compressor = FixedCompressor(model, budget)
    context = ContextBuilder(
        rules=SystemRuleLoader(global_data_dir=config.data_dir),
        budget=budget,
        compressor=compressor,
    )
    skills = SkillRegistry(SkillLoader(), [config.data_dir / "skills"])
    sessions = SqliteSessionStore(config.data_dir / "agent.db")
    turns = TurnManager(sessions)
    runtime = AgentRuntime(model=model, tools=executor, hooks=hooks)
    app = AgentApplication(
        runtime=runtime,
        sessions=sessions,
        context=context,
        skills=skills,
        tools=executor,
        hooks=hooks,
        turns=turns,
        startup=(mcp.connect_all,),
        shutdown=(model.close, mcp.close),
    )
    return app, model
