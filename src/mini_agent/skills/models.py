from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    path: Path
    required_tools: tuple[str, ...] = ()
    version: str | None = None


@dataclass(frozen=True)
class LoadedSkill:
    metadata: SkillMetadata
    instructions: str
    content_hash: str
