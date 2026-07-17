from __future__ import annotations

import asyncio
from typing import Any

from mini_agent.tools.builtin.common import resolve_workspace_path
from mini_agent.tools.types import ToolExecutionContext


async def write_file(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    path = resolve_workspace_path(context.workspace_root, str(arguments["path"]))
    content = str(arguments["content"])
    overwrite = bool(arguments.get("overwrite", False))

    def write() -> str:
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"File already exists; set overwrite=true to replace it: {path}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        return f"Wrote {len(content)} characters to {path.relative_to(context.workspace_root)}"

    return await asyncio.to_thread(write)
