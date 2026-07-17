from __future__ import annotations

from mini_agent.core.types import ToolSpec
from mini_agent.tools.builtin.apply_patch import apply_patch
from mini_agent.tools.builtin.list_directory import list_directory
from mini_agent.tools.builtin.read_file import read_file
from mini_agent.tools.builtin.search_text import search_text
from mini_agent.tools.builtin.write_file import write_file
from mini_agent.tools.registry import ToolRegistry


def register_builtin_tools(registry: ToolRegistry, *, include_shell: bool = False) -> None:
    registry.register(
        ToolSpec(
            id="builtin.filesystem.list_directory",
            name="list_directory",
            description="List files and directories inside the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "recursive": {"type": "boolean", "default": False},
                    "max_entries": {"type": "integer", "minimum": 1, "maximum": 5000},
                },
                "additionalProperties": False,
            },
            source="builtin",
            capabilities=frozenset({"filesystem.read", "filesystem.list"}),
            risk="read",
        ),
        list_directory,
    )
    registry.register(
        ToolSpec(
            id="builtin.filesystem.read_file",
            name="read_file",
            description="Read a UTF-8 text file inside the workspace, optionally by line range.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "max_chars": {"type": "integer", "minimum": 1, "maximum": 500000},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            source="builtin",
            capabilities=frozenset({"filesystem.read"}),
            risk="read",
        ),
        read_file,
    )
    registry.register(
        ToolSpec(
            id="builtin.filesystem.search_text",
            name="search_text",
            description="Search text or a regular expression in workspace files.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "glob": {"type": "string", "default": "*"},
                    "regex": {"type": "boolean", "default": False},
                    "case_sensitive": {"type": "boolean", "default": False},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            source="builtin",
            capabilities=frozenset({"filesystem.read", "text.search"}),
            risk="read",
        ),
        search_text,
    )
    registry.register(
        ToolSpec(
            id="builtin.filesystem.write_file",
            name="write_file",
            description="Create a new UTF-8 text file, or overwrite one only when overwrite=true.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                    "expected_sha256": {
                        "type": "string",
                        "pattern": "^[0-9a-fA-F]{64}$",
                        "description": "Optional SHA-256 of the existing file; reject if it changed.",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            source="builtin",
            capabilities=frozenset({"filesystem.write"}),
            risk="write",
        ),
        write_file,
    )
    registry.register(
        ToolSpec(
            id="builtin.filesystem.apply_patch",
            name="apply_patch",
            description="Replace exact text in one workspace file without rewriting unrelated content.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "minLength": 1},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                    "expected_sha256": {
                        "type": "string",
                        "pattern": "^[0-9a-fA-F]{64}$",
                        "description": "Optional SHA-256 from read_file; reject stale edits.",
                    },
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            source="builtin",
            capabilities=frozenset({"filesystem.write", "text.patch"}),
            risk="write",
        ),
        apply_patch,
    )
    if include_shell:
        from mini_agent.tools.builtin.shell.powershell import powershell_exec

        registry.register(
            ToolSpec(
                id="builtin.process.shell_exec",
                name="shell_exec",
                description=(
                    "Execute a PowerShell command with the current Windows user permissions. "
                    "No operating-system sandbox is active."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "minLength": 1},
                        "cwd": {"type": "string", "default": "."},
                        "timeout_seconds": {
                            "type": "number",
                            "minimum": 1,
                            "maximum": 600,
                        },
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
                source="builtin",
                capabilities=frozenset({"process.execute"}),
                risk="execute",
            ),
            powershell_exec,
        )


__all__ = ["register_builtin_tools"]
