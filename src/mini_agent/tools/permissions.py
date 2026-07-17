from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from mini_agent.core.types import ToolSpec


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class GrantScope(str, Enum):
    ONCE = "once"
    SESSION = "session"
    PROJECT = "project"


class PermissionMode(str, Enum):
    STANDARD = "standard"
    TRUSTED = "trusted"
    LOCKED = "locked"


@dataclass(frozen=True)
class PermissionRequest:
    session_id: str
    workspace_root: Path
    tool: ToolSpec
    arguments: dict[str, Any]
    warning: str
    allow_persistent: bool = True
    tool_call_id: str | None = None


@dataclass(frozen=True)
class PermissionResponse:
    decision: PermissionDecision
    scope: GrantScope = GrantScope.ONCE


class PermissionBroker(Protocol):
    async def request(self, request: PermissionRequest) -> PermissionResponse: ...


class DenyPermissionBroker:
    async def request(self, request: PermissionRequest) -> PermissionResponse:
        return PermissionResponse(PermissionDecision.DENY)


class AllowPermissionBroker:
    async def request(self, request: PermissionRequest) -> PermissionResponse:
        return PermissionResponse(PermissionDecision.ALLOW)


class CliPermissionBroker:
    async def request(self, request: PermissionRequest) -> PermissionResponse:
        import asyncio

        choices = (
            "[y] once / [s] session / [p] project / [N] deny"
            if request.allow_persistent
            else "[y] once / [N] deny"
        )
        prompt = (
            f"\nPermission required for {request.tool.name}\n"
            f"Risk: {request.tool.risk}\n"
            f"Arguments: {request.arguments}\n"
            f"Warning: {request.warning}\n"
            f"Allow? {choices}: "
        )
        answer = (await asyncio.to_thread(input, prompt)).strip().lower()
        if answer == "s" and request.allow_persistent:
            return PermissionResponse(PermissionDecision.ALLOW, GrantScope.SESSION)
        if answer == "p" and request.allow_persistent:
            return PermissionResponse(PermissionDecision.ALLOW, GrantScope.PROJECT)
        if answer in {"y", "yes"}:
            return PermissionResponse(PermissionDecision.ALLOW, GrantScope.ONCE)
        return PermissionResponse(PermissionDecision.DENY)


class PermissionManager:
    def __init__(
        self,
        broker: PermissionBroker,
        *,
        mode: PermissionMode = PermissionMode.STANDARD,
    ) -> None:
        self._broker = broker
        self._mode = mode
        self._session_grants: set[tuple[str, str]] = set()
        self._project_grants: set[tuple[str, str]] = set()

    def policy(self, spec: ToolSpec) -> PermissionDecision:
        if self._mode == PermissionMode.TRUSTED:
            return PermissionDecision.ALLOW
        if self._mode == PermissionMode.LOCKED:
            return (
                PermissionDecision.ALLOW
                if spec.risk == "read" and spec.source == "builtin"
                else PermissionDecision.DENY
            )
        if spec.risk == "read" and spec.source == "builtin":
            return PermissionDecision.ALLOW
        return PermissionDecision.ASK

    async def authorize(
        self,
        *,
        session_id: str,
        workspace_root: Path,
        spec: ToolSpec,
        arguments: dict[str, Any],
        tool_call_id: str | None = None,
    ) -> PermissionDecision:
        policy = self.policy(spec)
        if policy != PermissionDecision.ASK:
            return policy
        if (session_id, spec.id) in self._session_grants:
            return PermissionDecision.ALLOW
        project_key = (str(workspace_root.resolve()).lower(), spec.id)
        if project_key in self._project_grants:
            return PermissionDecision.ALLOW

        warning = (
            "This operation runs with the current Windows user permissions; no OS sandbox is active."
            if spec.risk == "execute"
            else "This operation can modify data."
        )
        allow_persistent = not (
            spec.risk in {"execute", "destructive"}
            or (spec.source.startswith("mcp:") and spec.risk != "read")
        )
        response = await self._broker.request(
            PermissionRequest(
                session_id=session_id,
                workspace_root=workspace_root,
                tool=spec,
                arguments=arguments,
                warning=warning,
                allow_persistent=allow_persistent,
                tool_call_id=tool_call_id,
            )
        )
        if response.decision == PermissionDecision.ALLOW and allow_persistent:
            if response.scope == GrantScope.SESSION:
                self._session_grants.add((session_id, spec.id))
            elif response.scope == GrantScope.PROJECT:
                self._project_grants.add(project_key)
        return response.decision
