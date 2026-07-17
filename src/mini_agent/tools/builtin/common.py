from __future__ import annotations

from pathlib import Path
from collections.abc import Iterator
import hashlib
import os
import tempfile


class WorkspacePathError(PermissionError):
    pass


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_expected_hash(path: Path, expected_sha256: str | None) -> None:
    if expected_sha256 is None:
        return
    expected = expected_sha256.strip().casefold()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise ValueError("expected_sha256 must be a 64-character hexadecimal SHA-256")
    if not path.exists():
        raise FileNotFoundError(
            f"Cannot verify expected_sha256 because the file does not exist: {path}"
        )
    actual = file_sha256(path)
    if actual != expected:
        raise ValueError(
            f"File changed since it was read: expected sha256 {expected}, actual {actual}"
        )


def atomic_write_text(
    path: Path,
    content: str,
    *,
    expected_sha256: str | None = None,
) -> str:
    """Write UTF-8 content through a same-directory temporary file and os.replace."""

    verify_expected_hash(path, expected_sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return file_sha256(path)


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
