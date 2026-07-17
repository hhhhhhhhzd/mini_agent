from __future__ import annotations

from pathlib import Path
from collections.abc import Iterator


class WorkspacePathError(PermissionError):
    pass


def resolve_workspace_path(
    workspace_root: Path,
    requested: str,
    *,
    must_exist: bool = False,
) -> Path:
    root = workspace_root.resolve(strict=True)
    candidate = Path(requested)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise WorkspacePathError(str(exc)) from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise WorkspacePathError(
            f"Path is outside the workspace: {requested}"
        ) from exc
    return resolved


def walk_workspace(
    root: Path,
    workspace_root: Path,
    *,
    recursive: bool,
) -> Iterator[Path]:
    """Walk resolved paths without following links or junctions outside the workspace."""
    stack = [root]
    visited: set[Path] = set()
    while stack:
        directory = stack.pop()
        if directory in visited:
            continue
        visited.add(directory)
        try:
            children = list(directory.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                safe = resolve_workspace_path(
                    workspace_root, str(child), must_exist=True
                )
            except WorkspacePathError:
                continue
            yield safe
            if recursive and safe.is_dir() and safe not in visited:
                stack.append(safe)
