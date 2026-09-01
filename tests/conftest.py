from __future__ import annotations

from collections.abc import Iterator
import hashlib
import os
from pathlib import Path
import tempfile

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

MetadataSignature = tuple[tuple[str, str, str], ...]
RunRootSignature = tuple[str, ...]


def repository_metadata_signature(
    repository_root: Path = REPOSITORY_ROOT,
) -> MetadataSignature:
    """Return a content-addressed snapshot of a repository's .orchestrator tree."""
    metadata_root = repository_root.resolve() / ".orchestrator"
    if not metadata_root.exists():
        return ()

    entries: list[tuple[str, str, str]] = []
    paths = (metadata_root, *sorted(metadata_root.rglob("*")))
    for path in paths:
        relative = path.relative_to(repository_root.resolve()).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", os.readlink(path)))
        elif path.is_dir():
            entries.append((relative, "directory", ""))
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append((relative, "file", digest))
        else:
            entries.append((relative, "other", ""))
    return tuple(entries)


def repository_run_root_signature(
    repository_root: Path = REPOSITORY_ROOT,
) -> RunRootSignature:
    """Return the direct run-root names used for fast per-test attribution."""
    artifacts_root = repository_root.resolve() / ".orchestrator" / "artifacts"
    if not artifacts_root.is_dir():
        return ()
    return tuple(sorted(path.name for path in artifacts_root.iterdir()))


def _metadata_signature_diff(
    before: MetadataSignature,
    after: MetadataSignature,
) -> str:
    before_by_path = {entry[0]: entry[1:] for entry in before}
    after_by_path = {entry[0]: entry[1:] for entry in after}
    added = sorted(after_by_path.keys() - before_by_path.keys())
    removed = sorted(before_by_path.keys() - after_by_path.keys())
    changed = sorted(
        path
        for path in before_by_path.keys() & after_by_path.keys()
        if before_by_path[path] != after_by_path[path]
    )
    lines = [
        *(f"added: {path}" for path in added),
        *(f"removed: {path}" for path in removed),
        *(f"changed: {path}" for path in changed),
    ]
    return "\n".join(lines) or "signature changed without a path-level difference"


def assert_repository_metadata_unchanged(
    before: MetadataSignature,
    after: MetadataSignature,
) -> None:
    assert after == before, (
        "Test run changed the real repository .orchestrator tree; bind its run "
        "root to tmp_path instead:\n"
        + _metadata_signature_diff(before, after)
    )


def assert_repository_run_roots_unchanged(
    before: RunRootSignature,
    after: RunRootSignature,
) -> None:
    assert after == before, (
        "Test changed the real repository .orchestrator/artifacts run roots; "
        "bind its cwd-derived run root to tmp_path instead:\n"
        + _metadata_signature_diff(
            tuple((path, "run-root", "") for path in before),
            tuple((path, "run-root", "") for path in after),
        )
    )


def assert_isolated_run_root(run_root: Path, temporary_root: Path) -> Path:
    resolved_run_root = run_root.resolve()
    resolved_temporary_root = temporary_root.resolve()
    real_metadata_root = (REPOSITORY_ROOT / ".orchestrator").resolve()
    assert not resolved_run_root.is_relative_to(real_metadata_root), (
        f"run root targets the real repository metadata: {resolved_run_root}"
    )
    assert resolved_run_root.is_relative_to(resolved_temporary_root), (
        f"run root escaped tmp_path: {resolved_run_root}"
    )
    return resolved_run_root


@pytest.fixture(scope="session", autouse=True)
def protect_repository_metadata() -> Iterator[None]:
    """Fail a test run if it changes this checkout's .orchestrator tree."""
    before = repository_metadata_signature()
    yield
    after = repository_metadata_signature()
    assert_repository_metadata_unchanged(before, after)


@pytest.fixture(autouse=True)
def protect_repository_run_roots() -> Iterator[None]:
    """Attribute a newly created real artifact run root to its exact test."""
    before = repository_run_root_signature()
    yield
    after = repository_run_root_signature()
    assert_repository_run_roots_unchanged(before, after)


@pytest.fixture
def isolated_run_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Bind cwd-derived run metadata to the current test's temporary directory."""
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    yield root


def can_symlink() -> bool:
    if not hasattr(os, "symlink"):
        return False
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            link = Path(tmpdir) / "link"
            target = Path(tmpdir) / "target"
            target.write_text("test", encoding="utf-8")
            link.symlink_to(target)
        return True
    except OSError:
        return False


requires_symlink = pytest.mark.skipif(
    not can_symlink(), reason="symlinks are unavailable or restricted by OS"
)
