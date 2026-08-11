from __future__ import annotations

from pathlib import Path

import pytest

from path_policy import (
    PathPolicyError,
    resolve_path_within_roots,
    resolve_repository_path,
)


def test_resolve_repository_path_accepts_relative_mixed_and_absolute_paths(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository with spaces"
    source_dir = repository / "src"
    source_dir.mkdir(parents=True)
    source_file = source_dir / "module.py"
    source_file.write_text("pass\n", encoding="utf-8")

    assert resolve_repository_path("src/module.py", repository) == source_file.resolve()
    assert resolve_repository_path(r"src\module.py", repository) == source_file.resolve()
    assert resolve_repository_path(source_file, repository) == source_file.resolve()


@pytest.mark.parametrize(
    "reported_path",
    [
        "../outside.txt",
        "src/../module.py",
        r"src\..\module.py",
        r"src/..\module.py",
    ],
)
def test_resolve_repository_path_rejects_parent_traversal(
    reported_path: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(PathPolicyError, match="parent traversal"):
        resolve_repository_path(reported_path, tmp_path)


@pytest.mark.parametrize(
    "reported_path",
    [
        "/etc/passwd",
        r"C:\Windows\system.ini",
        "C:/Windows/system.ini",
        r"\\server\share\secret.txt",
        "//server/share/secret.txt",
    ],
)
def test_resolve_repository_path_rejects_foreign_absolute_paths(
    reported_path: str,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(PathPolicyError):
        resolve_repository_path(reported_path, repository)


def test_resolve_repository_path_rejects_existing_and_broken_symlink_escapes(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    existing_escape = repository / "existing-link"
    broken_escape = repository / "broken-link"
    existing_escape.symlink_to(outside)
    broken_escape.symlink_to(tmp_path / "missing-outside.txt")

    with pytest.raises(PathPolicyError, match="outside allowed roots"):
        resolve_repository_path(existing_escape.name, repository)
    with pytest.raises(PathPolicyError, match="outside allowed roots"):
        resolve_repository_path(broken_escape.name, repository)


def test_resolve_path_within_roots_accepts_any_allowed_root_and_rejects_other_paths(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    allowed = second_root / "state.json"
    allowed.write_text("{}\n", encoding="utf-8")

    assert resolve_path_within_roots(allowed, (first_root, second_root)) == allowed.resolve()
    with pytest.raises(PathPolicyError, match="outside allowed roots"):
        resolve_path_within_roots(tmp_path / "outside.json", (first_root, second_root))


def test_resolve_path_within_roots_rejects_missing_roots(tmp_path: Path) -> None:
    with pytest.raises(PathPolicyError, match="no allowed path roots"):
        resolve_path_within_roots(tmp_path / "anything", ())
