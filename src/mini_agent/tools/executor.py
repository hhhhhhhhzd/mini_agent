from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import jsonschema

from mini_agent.core.types import ToolCall, ToolResult, ToolSpec
from mini_agent.tools.permissions import PermissionDecision, PermissionManager
from mini_agent.tools.registry import ToolRegistry
from mini_agent.tools.types import ToolExecutionContext
from mini_agent.tools.builtin.common import resolve_workspace_path


class ToolValidationError(RuntimeError):
    pass


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        permission_manager: PermissionManager,
        *,
        timeout_seconds: float = 120.0,
        max_output_chars: int = 100_000,
    ) -> None:
        self._registry = registry
        self._permissions = permission_manager
        self._timeout_seconds = timeout_seconds
        self._max_output_chars = max_output_chars

    def specs(self) -> list[ToolSpec]:
        return self._registry.specs()

    def ensure_available(self, names: list[str] | tuple[str, ...]) -> None:
        self._registry.ensure_available(names)

    def validate(self, call: ToolCall, workspace_root: Path) -> ToolCall:
        spec, _ = self._registry.get(call.name)
        arguments = copy.deepcopy(call.arguments)
        self._apply_defaults(arguments, spec.input_schema)
        try:
            jsonschema.validate(arguments, spec.input_schema)
        except jsonschema.ValidationError as exc:
            raise ToolValidationError(
                f"Invalid arguments for {call.name}: {exc.message}"
            ) from exc
        # Builtin filesystem/process tools are constrained before any Hook or
        # permission prompt, then validated again after a Hook rewrite.
        if spec.source == "builtin":
            for key in ("path", "cwd"):
                value = arguments.get(key)
                if isinstance(value, str):
                    resolve_workspace_path(workspace_root, value)
        return ToolCall(call.id, call.name, arguments)

    @classmethod
    def _apply_defaults(cls, value: object, schema: dict[str, object]) -> None:
        if not isinstance(value, dict) or schema.get("type") != "object":
            return
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return
        for key, child_schema in properties.items():
            if not isinstance(child_schema, dict):
                continue
            if key not in value and "default" in child_schema:
                value[key] = copy.deepcopy(child_schema["default"])
            if key in value:
                cls._apply_defaults(value[key], child_schema)

    async def execute(
        self,
        call: ToolCall,
        *,
        session_id: str,
        workspace_root: Path,
    ) -> ToolResult:
        try:
            call = self.validate(call, workspace_root)
            spec, handler = self._registry.get(call.name)
            decision = await self._permissions.authorize(
                session_id=session_id,
                workspace_root=workspace_root,
                spec=spec,
                arguments=call.arguments,
                tool_call_id=call.id,
            )
            if decision != PermissionDecision.ALLOW:
                return ToolResult(call.id, call.name, "Permission denied", is_error=True)
            context = ToolExecutionContext(session_id, workspace_root.resolve())
            content = await asyncio.wait_for(
                handler(call.arguments, context), timeout=self._timeout_seconds
            )
            if len(content) > self._max_output_chars:
                content = content[: self._max_output_chars] + "\n...[output truncated]"
            return ToolResult(call.id, call.name, content)
        except Exception as exc:
            return ToolResult(call.id, call.name, str(exc), is_error=True)
