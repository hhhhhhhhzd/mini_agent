from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mini_agent.tools.builtin.common import resolve_workspace_path, walk_workspace
from mini_agent.tools.types import ToolExecutionContext


async def list_directory(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    path = resolve_workspace_path(
        context.workspace_root, str(arguments.get("path", ".")), must_exist=True
    )
    recursive = bool(arguments.get("recursive", False))
    max_entries = int(arguments.get("max_entries", 1000))

    def collect() -> str:
        if not path.is_dir():
            raise NotADirectoryError(path)
        iterator = walk_workspace(
            path, context.workspace_root, recursive=recursive
        )
        entries: list[str] = []
        for item in iterator:
            if len(entries) >= max_entries:
                entries.append("...[entry limit reached]")
                break
            relative = item.relative_to(context.workspace_root)
            suffix = "/" if item.is_dir() else ""
            entries.append(f"{relative.as_posix()}{suffix}")
        return "\n".join(sorted(entries)) or "[empty directory]"

    return await asyncio.to_thread(collect)
