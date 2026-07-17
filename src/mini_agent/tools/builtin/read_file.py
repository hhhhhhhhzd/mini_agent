from __future__ import annotations

import asyncio
from typing import Any

from mini_agent.tools.builtin.common import file_sha256, resolve_workspace_path
from mini_agent.tools.types import ToolExecutionContext


async def read_file(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    path = resolve_workspace_path(
        context.workspace_root, str(arguments["path"]), must_exist=True
    )
    start = int(arguments.get("start_line", 1))
    end_value = arguments.get("end_line")
    end = int(end_value) if end_value is not None else None
    max_chars = int(arguments.get("max_chars", 100_000))
    if end is not None and end < start:
        raise ValueError("end_line must be greater than or equal to start_line")

    def read() -> str:
        if not path.is_file():
            raise FileNotFoundError(path)
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        selected = "".join(lines[start - 1 : end])
        header = f"[sha256: {file_sha256(path)}]\n"
        if len(selected) > max_chars:
            return header + selected[:max_chars] + "\n...[content truncated]"
        return header + selected

    return await asyncio.to_thread(read)
