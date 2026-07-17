from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


BUILTIN_SYSTEM_RULES = """You are a concise, careful coding agent operating on a local workspace.
Use tools only when needed. Never claim a tool succeeded when it returned an error.
Respect the workspace and permission boundaries enforced by the host application.
Prefer targeted edits and verify material changes when practical.
Do not expose secrets in output, logs, tool arguments, hooks, or session data."""


@dataclass(frozen=True)
class SystemRuleBlock:
    source: str
    scope: str
    content: str
    content_hash: str


class SystemRuleLoader:
    def __init__(self, *, global_data_dir: Path | None = None) -> None:
        default = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "MiniAgent"
        self._global_data_dir = global_data_dir or default

    def load(self, project_root: Path, session_rules: str | None) -> list[SystemRuleBlock]:
        blocks = [self._block("builtin", "global", BUILTIN_SYSTEM_RULES)]
        global_rules = self._global_data_dir / "system.md"
        if global_rules.is_file():
            blocks.append(self._file_block(global_rules, "global"))
        for path in (
            project_root / "AGENTS.md",
            project_root / ".mini-agent" / "system.md",
        ):
            if path.is_file():
                blocks.append(self._file_block(path, "project"))
        if session_rules and session_rules.strip():
            blocks.append(self._block("session", "session", session_rules.strip()))
        return blocks

    @staticmethod
    def render(blocks: list[SystemRuleBlock]) -> str:
        return "\n\n".join(
            f"## {block.scope}: {block.source}\n{block.content}" for block in blocks
        )

    def _file_block(self, path: Path, scope: str) -> SystemRuleBlock:
        return self._block(str(path.resolve()), scope, path.read_text(encoding="utf-8"))

    @staticmethod
    def _block(source: str, scope: str, content: str) -> SystemRuleBlock:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return SystemRuleBlock(source, scope, content, digest)
