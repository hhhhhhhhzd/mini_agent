from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from mini_agent.skills.models import LoadedSkill, SkillMetadata


class SkillLoadError(RuntimeError):
    pass


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [item.strip().strip('"\'') for item in value[1:-1].split(",") if item.strip()]
    return value


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise SkillLoadError("SKILL.md frontmatter is not closed") from exc
    metadata: dict[str, Any] = {}
    current_list: str | None = None
    for raw in lines[1:end]:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") and current_list:
            metadata.setdefault(current_list, []).append(stripped[1:].strip().strip('"\''))
            continue
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            metadata[key] = _parse_scalar(value)
            current_list = None
        else:
            metadata[key] = []
            current_list = key
    body = "\n".join(lines[end + 1 :]).strip()
    return metadata, body


class SkillLoader:
    def inspect(self, skill_dir: Path) -> SkillMetadata:
        path = skill_dir / "SKILL.md"
        if not path.is_file():
            raise SkillLoadError(f"Missing SKILL.md: {skill_dir}")
        metadata, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
        name = str(metadata.get("name") or skill_dir.name)
        required = metadata.get("required_tools") or []
        if not isinstance(required, list):
            raise SkillLoadError("required_tools must be a YAML list")
        return SkillMetadata(
            name=name,
            description=str(metadata.get("description") or ""),
            path=skill_dir.resolve(),
            required_tools=tuple(str(item) for item in required),
            version=str(metadata["version"]) if metadata.get("version") else None,
        )

    def load(self, skill_dir: Path) -> LoadedSkill:
        metadata = self.inspect(skill_dir)
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        _, body = _parse_frontmatter(text)
        if not body:
            raise SkillLoadError(f"Skill has no instructions: {metadata.name}")
        return LoadedSkill(
            metadata=metadata,
            instructions=body,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

    def read_resource(self, skill: LoadedSkill, relative_path: str) -> str:
        root = skill.metadata.path.resolve(strict=True)
        try:
            path = (root / relative_path).resolve(strict=False)
        except OSError as exc:
            raise SkillLoadError(f"Invalid skill resource: {relative_path}") from exc
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SkillLoadError("Skill resource path escapes the skill directory") from exc
        if path.name == "SKILL.md" or not path.is_file():
            raise SkillLoadError(f"Invalid skill resource: {relative_path}")
        return path.read_text(encoding="utf-8")
