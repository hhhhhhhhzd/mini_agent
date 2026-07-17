from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import AsyncContextManager

from mini_agent.context.builder import ContextBuildResult, ContextBuilder
from mini_agent.core.runtime import AgentRuntime
from mini_agent.core.types import (
    AgentError,
    AgentEvent,
    Message,
    MessageCommitted,
    ModelAttemptFailed,
    ModelAttemptStarted,
    ToolFinished,
    ToolStarted,
    TurnCompleted,
)
from mini_agent.hooks.dispatcher import HookDispatcher
from mini_agent.session.models import Session, SessionState
from mini_agent.session.store import SqliteSessionStore
from mini_agent.skills.models import LoadedSkill, SkillMetadata
from mini_agent.skills.registry import SkillRegistry
from mini_agent.tools.executor import ToolExecutor
from mini_agent.turns.manager import TurnManager
from mini_agent.turns.models import TurnStatus


class AgentApplication:
    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        sessions: SqliteSessionStore,
        context: ContextBuilder,
        skills: SkillRegistry,
        tools: ToolExecutor,
        hooks: HookDispatcher,
        turns: TurnManager,
        startup: Sequence[Callable[[], Awaitable[None]]] = (),
        shutdown: Sequence[Callable[[], Awaitable[None]]] = (),
    ) -> None:
        self._runtime = runtime
        self._sessions = sessions
        self._context = context
        self._skills = skills
        self._tools = tools
        self._hooks = hooks
        self._turns = turns
        self._startup = tuple(startup)
        self._shutdown = tuple(shutdown)

    async def initialize(self) -> None:
        await self._sessions.initialize()
        await self._turns.recover()
        try:
            for callback in self._startup:
                await callback()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        for callback in reversed(self._shutdown):
            await callback()

    async def create_session(
        self,
        project_root: Path,
        *,
        system_rules: str | None = None,
        session_id: str | None = None,
        name: str | None = None,
    ) -> Session:
        return await self._sessions.create(
            project_root,
            system_rules=system_rules,
            session_id=session_id,
            name=name,
        )

    async def list_sessions(
        self,
        *,
        include_archived: bool = True,
        project_root: Path | None = None,
    ) -> list[Session]:
        return await self._sessions.list(
            include_archived=include_archived,
            project_root=project_root,
        )

    async def resolve_session(self, reference: str, project_root: Path) -> Session:
        return await self._sessions.resolve(reference, project_root)

    async def resume_session(self, session_id: str) -> Session:
        return await self._sessions.resume(session_id)

    async def archive_session(self, session_id: str) -> Session:
        return await self._sessions.archive(session_id)

    async def delete_session(self, session_id: str) -> None:
        await self._sessions.delete(session_id)

    async def session_history(self, session_id: str) -> list[Message]:
        await self._sessions.get(session_id)
        return [record.message for record in await self._sessions.load_messages(session_id)]

    async def record_interruption(self, session_id: str, reason: str) -> None:
        await self._sessions.append_event(
            session_id, "interrupted", {"reason": reason}
        )

    async def cancel_turn(self, session_id: str) -> bool:
        return await self._turns.cancel(session_id)

    def session_gate(self, session_id: str) -> AsyncContextManager[None]:
        return self._turns.session_gate(session_id)

    async def bind_session(
        self,
        *,
        channel: str,
        external_session_id: str,
        internal_session_id: str,
    ) -> None:
        await self._sessions.bind_session(
            channel=channel,
            external_session_id=external_session_id,
            internal_session_id=internal_session_id,
        )

    async def resolve_binding(
        self, *, channel: str, external_session_id: str
    ) -> str | None:
        return await self._sessions.resolve_binding(
            channel=channel,
            external_session_id=external_session_id,
        )

    def list_skills(self, project_root: Path) -> list[SkillMetadata]:
        return self._skills.refresh(project_root)

    async def activate_skill(self, session_id: str, name: str) -> LoadedSkill:
        session = await self._sessions.get(session_id)
        self._skills.refresh(session.project_root)
        skill = self._skills.load(name)
        self._tools.ensure_available(list(skill.metadata.required_tools))
        names = list(session.active_skills)
        if name not in names:
            names.append(name)
            await self._sessions.set_active_skills(session_id, names)
            await self._sessions.append_event(
                session_id,
                "skill_activated",
                {
                    "name": name,
                    "version": skill.metadata.version,
                    "content_hash": skill.content_hash,
                },
            )
        return skill

    async def deactivate_skill(self, session_id: str, name: str) -> None:
        session = await self._sessions.get(session_id)
        names = [item for item in session.active_skills if item != name]
        await self._sessions.set_active_skills(session_id, names)
        await self._sessions.append_event(session_id, "skill_deactivated", {"name": name})

    async def run_turn(self, session_id: str, prompt: str) -> AsyncIterator[AgentEvent]:
        async for event in self._turns.run(
            session_id=session_id,
            runner=lambda turn_id: self._execute_turn(session_id, prompt, turn_id),
        ):
            yield event

    async def _execute_turn(
        self, session_id: str, prompt: str, turn_id: str
    ) -> AsyncIterator[AgentEvent]:
        session = await self._sessions.get(session_id)
        if session.state != SessionState.ACTIVE:
            yield AgentError("Session is archived; resume it before sending a prompt", fatal=True)
            return

        prompt_hook = await self._hooks.user_prompt_submit(
            session_id=session_id,
            prompt=prompt,
            cwd=session.project_root,
        )
        if prompt_hook.blocked:
            yield AgentError(prompt_hook.reason or "Prompt blocked by hook")
            return

        self._skills.refresh(session.project_root)
        loaded_skills = self._skills.load_many(session.active_skills)
        for skill in loaded_skills:
            self._tools.ensure_available(list(skill.metadata.required_tools))

        user_message = Message(role="user", content=prompt)
        prompt_seq = await self._sessions.append_message(session_id, user_message)
        await self._sessions.update_turn(
            turn_id,
            TurnStatus.RUNNING,
            prompt_event_seq=prompt_seq,
        )
        checkpoint = await self._sessions.latest_checkpoint(session_id)
        records = await self._sessions.load_messages(
            session_id,
            after_seq=checkpoint.through_seq if checkpoint else 0,
        )
        messages = [record.message for record in records]

        built_messages, _tool_specs, build_result = await self._context.build(
            session_id=session_id,
            project_root=session.project_root,
            messages=messages,
            session_rules=session.system_rules,
            existing_summary=checkpoint.summary if checkpoint else None,
            skill_instructions=[skill.instructions for skill in loaded_skills],
            additional_context=prompt_hook.additional_context,
            tools=self._tools.specs(),
        )
        assert isinstance(build_result, ContextBuildResult)
        await self._sessions.append_event(
            session_id,
            "context_rules",
            {
                "rules": [
                    {
                        "source": rule.source,
                        "scope": rule.scope,
                        "content_hash": rule.content_hash,
                    }
                    for rule in build_result.rules
                ]
            },
        )
        if build_result.compression:
            count = build_result.compression.through_seq
            if count > 0 and count <= len(records):
                through_seq = records[count - 1].seq
                await self._sessions.save_checkpoint(
                    session_id,
                    through_seq=through_seq,
                    summary=build_result.compression.summary,
                    version=build_result.compression.version,
                )

        async for event in self._runtime.run(
            session_id=session_id,
            workspace_root=session.project_root,
            messages=built_messages,
        ):
            if isinstance(event, ModelAttemptStarted):
                await self._sessions.append_event(
                    session_id,
                    "model_attempt_started",
                    {"attempt": event.attempt, "max_attempts": event.max_attempts},
                )
            elif isinstance(event, ModelAttemptFailed):
                await self._sessions.append_event(
                    session_id,
                    "model_attempt_failed",
                    {
                        "attempt": event.attempt,
                        "error_kind": event.error_kind,
                        "message": event.message,
                        "retryable": event.retryable,
                        "will_retry": event.will_retry,
                        "request_id": event.request_id,
                        "retry_delay_seconds": event.retry_delay_seconds,
                    },
                )
            elif isinstance(event, MessageCommitted):
                await self._sessions.append_message(session_id, event.message)
            elif isinstance(event, ToolStarted):
                await self._sessions.append_event(
                    session_id,
                    "tool_started",
                    {
                        "id": event.call.id,
                        "name": event.call.name,
                        "arguments": event.call.arguments,
                    },
                )
            elif isinstance(event, ToolFinished):
                await self._sessions.append_event(
                    session_id,
                    "tool_finished",
                    {
                        "id": event.result.call_id,
                        "name": event.result.tool_name,
                        "content": event.result.content,
                        "is_error": event.result.is_error,
                    },
                )
            elif isinstance(event, AgentError):
                await self._sessions.append_event(
                    session_id,
                    "error",
                    {"message": event.message, "fatal": event.fatal},
                )
            elif isinstance(event, TurnCompleted):
                await self._sessions.append_event(
                    session_id,
                    "turn_completed",
                    {"usage": event.usage},
                )
            yield event
