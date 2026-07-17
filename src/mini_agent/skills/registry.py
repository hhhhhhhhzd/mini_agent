from __future__ import annotations

from pathlib import Path

from mini_agent.skills.loader import SkillLoader
from mini_agent.skills.models import LoadedSkill, SkillMetadata


class SkillRegistry:
    def __init__(self, loader: SkillLoader, roots: list[Path] | None = None) -> None:
        self._loader = loader
        self._roots = list(roots or [])
        self._skills: dict[str, SkillMetadata] = {}

    def refresh(self, project_root: Path | None = None) -> list[SkillMetadata]:
        roots = list(self._roots)
        if project_root:
            roots.append(project_root / ".mini-agent" / "skills")
        discovered: dict[str, SkillMetadata] = {}
        for root in roots:
            if not root.is_dir():
                continue
            for directory in sorted(item for item in root.iterdir() if item.is_dir()):
                skill_file = directory / "SKILL.md"
                if not skill_file.is_file():
                    continue
                metadata = self._loader.inspect(directory)
                discovered[metadata.name] = metadata
        self._skills = discovered
        return list(discovered.values())

    def list(self) -> list[SkillMetadata]:
        return sorted(self._skills.values(), key=lambda item: item.name)

    def load(self, name: str) -> LoadedSkill:
        try:
            metadata = self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Unknown skill: {name}") from exc
        return self._loader.load(metadata.path)

    def load_many(self, names: list[str] | tuple[str, ...]) -> list[LoadedSkill]:
        return [self.load(name) for name in names]
