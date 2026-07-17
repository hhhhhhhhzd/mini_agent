from __future__ import annotations

import asyncio
from typing import Any

from mini_agent.tools.builtin.common import atomic_write_text, resolve_workspace_path
from mini_agent.tools.types import ToolExecutionContext


async def write_file(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    path = resolve_workspace_path(context.workspace_root, str(arguments["path"]))
    content = str(arguments["content"])
    overwrite = bool(arguments.get("overwrite", False))
    expected_sha256 = arguments.get("expected_sha256")

    def write() -> str:
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"File already exists; set overwrite=true to replace it: {path}"
            )
        digest = atomic_write_text(
            path,
            content,
            expected_sha256=(str(expected_sha256) if expected_sha256 is not None else None),
        )
        return (
            f"Wrote {len(content)} characters to "
            f"{path.relative_to(context.workspace_root)} (sha256: {digest})"
        )

    return await asyncio.to_thread(write)
