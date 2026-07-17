from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

from mini_agent.app.factory import build_application
from mini_agent.config import AgentConfig
from mini_agent.core.types import (
    AgentError,
    HookNotice,
    ReasoningDelta,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnCompleted,
)
from mini_agent.session import SqliteSessionStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mini-agent")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--enable-shell", action="store_true")
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="Start an interactive chat")
    chat.add_argument("--session")

    sessions = sub.add_parser("session", help="Manage sessions")
    session_sub = sessions.add_subparsers(dest="session_command", required=True)
    create = session_sub.add_parser("create")
    create.add_argument("--rules")
    session_sub.add_parser("list")
    for name in ("resume", "archive", "delete"):
        command = session_sub.add_parser(name)
        command.add_argument("session_id")

    skills = sub.add_parser("skill", help="Manage skills")
    skill_sub = skills.add_subparsers(dest="skill_command", required=True)
    skill_sub.add_parser("list")
    for name in ("activate", "deactivate"):
        command = skill_sub.add_parser(name)
        command.add_argument("session_id")
        command.add_argument("name")
    return parser


async def _session_command(args: argparse.Namespace, data_dir: Path) -> int:
    store = SqliteSessionStore(data_dir / "agent.db")
    await store.initialize()
    action = args.session_command
    if action == "create":
        session = await store.create(args.workspace, system_rules=args.rules)
        print(session.id)
    elif action == "list":
        for session in await store.list():
            print(f"{session.id}\t{session.state.value}\t{session.project_root}")
    elif action == "resume":
        print((await store.resume(args.session_id)).id)
    elif action == "archive":
        print((await store.archive(args.session_id)).id)
    elif action == "delete":
        await store.delete(args.session_id)
        print(args.session_id)
    return 0


async def _run_chat(args: argparse.Namespace, config: AgentConfig) -> int:
    app, model = build_application(config)
    await app.initialize()
    try:
        if args.session:
            session = await app.resume_session(args.session)
        else:
            session = await app.create_session(config.workspace_root)
        print(f"Session: {session.id}")
        print("Commands: /exit, /archive, /skill <name>, /unskill <name>")
        while True:
            try:
                prompt = (await asyncio.to_thread(input, "You> ")).strip()
            except EOFError:
                break
            if not prompt:
                continue
            if prompt in {"/exit", "/quit"}:
                break
            if prompt == "/archive":
                await app.archive_session(session.id)
                print("Session archived")
                break
            if prompt.startswith("/skill "):
                skill = await app.activate_skill(session.id, prompt.split(maxsplit=1)[1])
                print(f"Activated skill: {skill.metadata.name}")
                continue
            if prompt.startswith("/unskill "):
                name = prompt.split(maxsplit=1)[1]
                await app.deactivate_skill(session.id, name)
                print(f"Deactivated skill: {name}")
                continue

            print("Agent> ", end="", flush=True)
            async for event in app.run_turn(session.id, prompt):
                if isinstance(event, TextDelta):
                    print(event.text, end="", flush=True)
                elif isinstance(event, ReasoningDelta):
                    # Reasoning is intentionally not printed or persisted as assistant text.
                    pass
                elif isinstance(event, ToolStarted):
                    print(f"\n[tool {event.call.name} started]", flush=True)
                elif isinstance(event, ToolFinished):
                    status = "error" if event.result.is_error else "ok"
                    print(f"\n[tool {event.result.tool_name}: {status}]", flush=True)
                elif isinstance(event, HookNotice):
                    print(f"\n[hook {event.event}: {event.message}]", flush=True)
                elif isinstance(event, AgentError):
                    print(f"\n[error: {event.message}]", flush=True)
                elif isinstance(event, TurnCompleted) and event.usage:
                    print(f"\n[usage: {event.usage}]", flush=True)
            print()
    finally:
        await app.close()
    return 0


async def async_main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = args.workspace.resolve()
    data_dir = args.data_dir.resolve() if args.data_dir else None
    if args.command == "session":
        default_data = AgentConfig.resolve_data_dir(data_dir)
        return await _session_command(args, default_data)

    config = AgentConfig.from_env(workspace_root=workspace, data_dir=data_dir)
    if args.enable_shell and not config.shell_enabled:
        config = replace(config, shell_enabled=True)
    if args.command == "skill":
        app, model = build_application(config)
        await app.initialize()
        try:
            if args.skill_command == "list":
                for skill in app.list_skills(workspace):
                    print(f"{skill.name}\t{skill.description}")
            elif args.skill_command == "activate":
                print((await app.activate_skill(args.session_id, args.name)).metadata.name)
            elif args.skill_command == "deactivate":
                await app.deactivate_skill(args.session_id, args.name)
                print(args.name)
        finally:
            await app.close()
        return 0
    return await _run_chat(args, config)


def run() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        print("\nCancelled", file=sys.stderr)
        raise SystemExit(130)
