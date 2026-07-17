from __future__ import annotations

from acp.interfaces import Client
from acp.schema import PermissionOption, ToolCallUpdate

from mini_agent.tools.permissions import (
    GrantScope,
    PermissionDecision,
    PermissionRequest,
    PermissionResponse,
)


class AcpPermissionBroker:
    def __init__(self) -> None:
        self._client: Client | None = None

    def bind(self, client: Client) -> None:
        self._client = client

    async def request(self, request: PermissionRequest) -> PermissionResponse:
        if self._client is None:
            return PermissionResponse(PermissionDecision.DENY)
        tool_call = ToolCallUpdate(
            toolCallId=request.tool_call_id or request.tool.id,
            title=f"Run {request.tool.name}",
            kind=_tool_kind(request.tool.risk),
            status="pending",
            rawInput=request.arguments,
        )
        options = [
            PermissionOption(optionId="allow_once", name="Allow once", kind="allow_once"),
            PermissionOption(optionId="deny_once", name="Deny", kind="reject_once"),
        ]
        if request.allow_persistent:
            options[1:1] = [
                PermissionOption(optionId="allow_session", name="Allow for session", kind="allow_always"),
                PermissionOption(optionId="allow_project", name="Allow for project", kind="allow_always"),
            ]
        response = await self._client.request_permission(
            request.session_id,
            tool_call,
            options,
        )
        outcome = response.outcome
        if getattr(outcome, "outcome", None) != "selected":
            return PermissionResponse(PermissionDecision.DENY)
        option_id = getattr(outcome, "option_id", "deny_once")
        scope = {
            "allow_session": GrantScope.SESSION,
            "allow_project": GrantScope.PROJECT,
        }.get(option_id, GrantScope.ONCE)
        decision = (
            PermissionDecision.ALLOW
            if option_id.startswith("allow_")
            else PermissionDecision.DENY
        )
        return PermissionResponse(decision, scope)


def _tool_kind(risk: str) -> str:
    if risk == "read":
        return "read"
    if risk == "execute":
        return "execute"
    if risk in {"write", "destructive"}:
        return "edit"
    if risk == "network":
        return "fetch"
    return "other"
