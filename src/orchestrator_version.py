"""Deterministic identity of the code that composes provider requests."""

from __future__ import annotations

import hashlib
from pathlib import Path


def orchestrator_code_version(repository_root: Path | None = None) -> str:
    """Hash every shipped source and schema component, independent of Git state."""

    root = (
        Path(repository_root).resolve()
        if repository_root is not None
        else Path(__file__).resolve().parents[1]
    )
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
