from __future__ import annotations

from collections.abc import Sequence

from mini_agent.core.types import ToolSpec
from mini_agent.tools.types import ToolHandler


class ToolNotFoundError(LookupError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[ToolSpec, ToolHandler]] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._entries:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._entries[spec.name] = (spec, handler)

    def unregister_source(self, source: str) -> None:
        names = [name for name, (spec, _) in self._entries.items() if spec.source == source]
        for name in names:
            del self._entries[name]

    def specs(self) -> list[ToolSpec]:
        return [entry[0] for entry in self._entries.values()]

    def get(self, name: str) -> tuple[ToolSpec, ToolHandler]:
        try:
            return self._entries[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Unknown tool: {name}") from exc

    def ensure_available(self, names: Sequence[str]) -> None:
        missing = [name for name in names if name not in self._entries]
        if missing:
            raise ToolNotFoundError(f"Required tools are unavailable: {', '.join(missing)}")
