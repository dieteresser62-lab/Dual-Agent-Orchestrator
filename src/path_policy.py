from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable


class PathPolicyError(ValueError):
    """Raised when an untrusted path cannot be confined to an allowed root."""


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

    windows_path = PureWindowsPath(path_text)
    if windows_path.drive or path_text.startswith(("\\\\", "//")):
        raise PathPolicyError("drive-qualified and UNC paths are not supported")

    normalized = path_text.replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if ".." in parsed.parts:
        raise PathPolicyError("parent traversal is not allowed")

    candidate = Path(normalized)
    if not candidate.is_absolute():
        candidate = repository_root.resolve() / candidate
    return resolve_path_within_roots(candidate, (repository_root,))
