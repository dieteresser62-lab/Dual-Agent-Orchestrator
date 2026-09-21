from __future__ import annotations

import os
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable


class PathPolicyError(ValueError):
    """Raised when an untrusted path cannot be confined to an allowed root."""


class PathClass(str, Enum):
    """Closed repository path categories shared by classification and records."""

    PRODUCTIVE = "productive"
    TEST = "test"
    DOCUMENTATION = "documentation"
    GENERATED = "generated"


def resolve_path_within_roots(
    raw_path: str | os.PathLike[str],
    allowed_roots: Iterable[Path],
) -> Path:
    """Resolve a path and require its canonical target to stay within a trusted root."""
    roots = tuple(Path(root).resolve() for root in allowed_roots)
    if not roots:
        raise PathPolicyError("no allowed path roots were configured")

    try:
        resolved = Path(raw_path).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathPolicyError("path could not be resolved safely") from exc

    if not any(resolved.is_relative_to(root) for root in roots):
        raise PathPolicyError("path resolves outside allowed roots")
    return resolved


def resolve_repository_path(
    raw_path: str | os.PathLike[str],
    repository_root: Path,
) -> Path:
    """Resolve an agent-reported POSIX/WSL path inside a repository root.

    Relative backslashes are treated as separators because reports may originate
    from Windows-oriented tools. Drive-qualified and UNC paths are not portable
    to the supported POSIX/WSL runtime and therefore fail closed.
    """
    path_text = os.fspath(raw_path)
    if not path_text or "\x00" in path_text:
        raise PathPolicyError("path is empty or contains a NUL byte")

    if path_text.startswith(("\\\\", "//")):
        raise PathPolicyError("drive-qualified and UNC paths are not supported")

    normalized = path_text.replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if ".." in parsed.parts:
        raise PathPolicyError("parent traversal is not allowed")

    candidate = Path(path_text)
    if candidate.is_absolute():
        repo_resolved = repository_root.resolve()
        try:
            cand_resolved = candidate.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise PathPolicyError("path could not be resolved safely") from exc
        if not cand_resolved.is_relative_to(repo_resolved):
            raise PathPolicyError("path resolves outside allowed roots")
        return cand_resolved

    windows_path = PureWindowsPath(path_text)
    if windows_path.drive:
        raise PathPolicyError("drive-qualified and UNC paths are not supported")

    candidate = repository_root.resolve() / PurePosixPath(normalized)
    return resolve_path_within_roots(candidate, (repository_root,))
