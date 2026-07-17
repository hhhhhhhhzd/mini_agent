from __future__ import annotations

import asyncio
import fnmatch
import re
from pathlib import Path
from typing import Any

from mini_agent.tools.builtin.common import resolve_workspace_path, walk_workspace
from mini_agent.tools.types import ToolExecutionContext


async def search_text(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    root = resolve_workspace_path(
        context.workspace_root, str(arguments.get("path", ".")), must_exist=True
    )
    query = str(arguments["query"])
    glob_pattern = str(arguments.get("glob", "*"))
    use_regex = bool(arguments.get("regex", False))
    case_sensitive = bool(arguments.get("case_sensitive", False))
    max_results = int(arguments.get("max_results", 200))

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(query if use_regex else re.escape(query), flags)

    def search() -> str:
        files = (
            [root]
            if root.is_file()
            else walk_workspace(root, context.workspace_root, recursive=True)
        )
        results: list[str] = []
        for file in files:
            if not file.is_file() or not fnmatch.fnmatch(file.name, glob_pattern):
                continue
            try:
                text = file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    relative = file.relative_to(context.workspace_root).as_posix()
                    results.append(f"{relative}:{line_number}: {line[:500]}")
                    if len(results) >= max_results:
                        results.append("...[result limit reached]")
                        return "\n".join(results)
        return "\n".join(results) or "[no matches]"

    return await asyncio.to_thread(search)
