from __future__ import annotations

import asyncio
from typing import Any

from mini_agent.tools.builtin.common import resolve_workspace_path
from mini_agent.tools.types import ToolExecutionContext


async def apply_patch(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    path = resolve_workspace_path(
        context.workspace_root, str(arguments["path"]), must_exist=True
    )
    old_text = str(arguments["old_text"])
    new_text = str(arguments["new_text"])
    replace_all = bool(arguments.get("replace_all", False))

    def patch() -> str:
        text = path.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count == 0:
            raise ValueError("old_text was not found; no file changes were made")
        if count > 1 and not replace_all:
            raise ValueError(
                f"old_text occurs {count} times; set replace_all=true or provide more context"
            )
        updated = text.replace(old_text, new_text, -1 if replace_all else 1)
        path.write_text(updated, encoding="utf-8", newline="")
        replaced = count if replace_all else 1
        return f"Applied {replaced} replacement(s) to {path.relative_to(context.workspace_root)}"

    return await asyncio.to_thread(patch)
