from __future__ import annotations

import asyncio
from pathlib import Path

from acp import run_agent

from mini_agent.app.factory import build_application
from mini_agent.config import AgentConfig
from mini_agent.protocols.acp.agent import AcpAgent
from mini_agent.protocols.acp.permissions import AcpPermissionBroker


async def async_main() -> None:
    config = AgentConfig.from_env(workspace_root=Path.cwd())
    broker = AcpPermissionBroker()
    app, _model = build_application(config, permission_broker=broker)
    await app.initialize()
    try:
        await run_agent(AcpAgent(app, broker), use_unstable_protocol=True)
    finally:
        await app.close()


def run() -> None:
    asyncio.run(async_main())
