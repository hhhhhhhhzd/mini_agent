from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from mcp import types as mcp_types

from mini_agent.core.types import ToolSpec


def mcp_tool_name(server: str, tool: str) -> str:
    raw = f"mcp__{server}__{tool}"
    normalized = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
    if len(normalized) <= 64:
        return normalized
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{normalized[:53]}_{suffix}"


def infer_risk(tool: mcp_types.Tool) -> str:
    annotations = tool.annotations
    if annotations and annotations.destructiveHint:
        return "destructive"
    if annotations and annotations.readOnlyHint and not annotations.openWorldHint:
        return "read"
    if annotations and annotations.openWorldHint:
        return "network"
    return "write"


def to_tool_spec(server: str, tool: mcp_types.Tool) -> ToolSpec:
    exposed_name = mcp_tool_name(server, tool.name)
    return ToolSpec(
        id=f"mcp:{server}:{tool.name}",
        name=exposed_name,
        description=tool.description or tool.title or f"MCP tool {tool.name} from {server}",
        input_schema=tool.inputSchema,
        source=f"mcp:{server}",
        capabilities=frozenset({"mcp", infer_risk(tool)}),
        risk=infer_risk(tool),  # type: ignore[arg-type]
    )


def render_result(result: mcp_types.CallToolResult) -> str:
    parts: list[str] = []
    for item in result.content:
        if isinstance(item, mcp_types.TextContent):
            parts.append(item.text)
        elif isinstance(item, mcp_types.EmbeddedResource):
            resource = item.resource
            text = getattr(resource, "text", None)
            parts.append(text if text is not None else resource.model_dump_json(by_alias=True))
        else:
            parts.append(item.model_dump_json(by_alias=True, exclude_none=True))
    if result.structuredContent is not None:
        parts.append(json.dumps(result.structuredContent, ensure_ascii=False, indent=2))
    return "\n".join(part for part in parts if part)
