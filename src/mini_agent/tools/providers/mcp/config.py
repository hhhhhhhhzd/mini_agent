from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    cwd: Path | None = None
    enabled: bool = True


def _parse_server(name: str, raw: Any, base_dir: Path) -> McpServerConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"MCP server {name!r} must be an object")
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(f"MCP server {name!r} requires a non-empty command")
    args = raw.get("args", [])
    env = raw.get("env")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError(f"MCP server {name!r} args must be a string list")
    if env is not None and (
        not isinstance(env, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
    ):
        raise ValueError(f"MCP server {name!r} env must be a string map")
    cwd_value = raw.get("cwd")
    cwd = None
    if cwd_value is not None:
        if not isinstance(cwd_value, str):
            raise ValueError(f"MCP server {name!r} cwd must be a string")
        candidate = Path(cwd_value)
        cwd = (base_dir / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    return McpServerConfig(
        name=name,
        command=command,
        args=tuple(args),
        env=dict(env) if env is not None else None,
        cwd=cwd,
        enabled=bool(raw.get("enabled", True)),
    )


def load_mcp_config(paths: list[Path]) -> list[McpServerConfig]:
    """Load and merge MCP JSON files; later files override earlier servers."""
    merged: dict[str, McpServerConfig] = {}
    for path in paths:
        if not path.is_file():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        servers = raw.get("mcpServers", raw.get("servers")) if isinstance(raw, dict) else None
        if not isinstance(servers, dict):
            raise ValueError(f"MCP config {path} requires an mcpServers object")
        for name, value in servers.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"MCP config {path} contains an invalid server name")
            merged[name] = _parse_server(name, value, path.parent)
    return [config for config in merged.values() if config.enabled]
