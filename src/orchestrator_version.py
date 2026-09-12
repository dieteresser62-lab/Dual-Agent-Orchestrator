"""Deterministic identity of the code that composes provider requests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import threading


_cache_lock = threading.Lock()
_cache_process_id = os.getpid()
_cached_versions: dict[Path, str] = {}


def orchestrator_code_version(repository_root: Path | None = None) -> str:
    """Hash shipped code once per installation root and process."""

    root = (
        Path(repository_root).resolve()
        if repository_root is not None
        else Path(__file__).resolve().parents[1]
    )
    global _cache_process_id
    process_id = os.getpid()
    with _cache_lock:
        if _cache_process_id != process_id:
            _cached_versions.clear()
            _cache_process_id = process_id
        cached = _cached_versions.get(root)
        if cached is None:
            cached = _compute_orchestrator_code_version(root)
            _cached_versions[root] = cached
        return cached


def _compute_orchestrator_code_version(root: Path) -> str:
    """Compute one uncached installation digest for the process-local cache."""

    required_roots = (root / "src", root / "schemas")
    if any(not path.is_dir() for path in required_roots):
        raise RuntimeError("orchestrator code version source roots are incomplete")
    paths = tuple(
        sorted(
            (
                *(path for path in (root / "src").rglob("*.py") if path.is_file()),
                *(path for path in (root / "schemas").rglob("*.json") if path.is_file()),
                *(
                    path
                    for path in (
                        root / "orchestrator.toml",
                        root / "run_task",
                        root / "AGENTS.md",
                        root / "CLAUDE.md",  # allowlist:provider -- role policy
                        root / "CODEX.md",  # allowlist:provider -- role policy
                    )
                    if path.is_file()
                ),
            ),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )
    if not paths:
        raise RuntimeError("orchestrator code version has no source components")
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


__all__ = ["orchestrator_code_version"]
