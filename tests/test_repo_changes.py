from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from repo_changes import (
    NotGitRepositoryError,
    RepositoryChangeError,
    collect_repository_changes,
    merge_reported_paths,
    resolve_merge_base,
)


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit_all(repository: Path, message: str) -> str:
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _new_repository(tmp_path: Path, *, initial_branch: str = "master") -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", initial_branch)
    _git(repository, "config", "user.name", "Slice Tests")
    _git(repository, "config", "user.email", "slice-tests@example.invalid")
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    (repository / "delete-me.txt").write_text("delete\n", encoding="utf-8")
    (repository / "tracked.log").write_text("tracked\n", encoding="utf-8")
    base_commit = _commit_all(repository, "base")
    return repository, base_commit


def test_collect_changes_combines_committed_worktree_and_untracked_states(
    tmp_path: Path,
) -> None:
    repository, base_commit = _new_repository(tmp_path)
    _git(repository, "switch", "-c", "feature/example")
    (repository / "committed.txt").write_text("committed slice\n", encoding="utf-8")
    _commit_all(repository, "committed slice")
    (repository / "base.txt").write_text("staged\n", encoding="utf-8")
    _git(repository, "add", "base.txt")
    (repository / "base.txt").write_text("staged and unstaged\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("new file\n", encoding="utf-8")

    resolved = resolve_merge_base(repository)
    first = collect_repository_changes(repository, resolved.commit)
    second = collect_repository_changes(repository, resolved.commit)

    assert resolved.base_ref == "master"
    assert resolved.commit == base_commit
    assert first.paths == ("base.txt", "committed.txt", "untracked.txt")
    assert first.review_paths == ("base.txt", "untracked.txt", "committed.txt")
    assert {entry.path: entry.kind for entry in first.entries} == {
        "base.txt": "modified",
        "committed.txt": "added",
        "untracked.txt": "untracked",
    }
    committed_entry = next(
        entry for entry in first.entries if entry.path == "committed.txt"
    )
    assert committed_entry.working_tree is False
    assert "+staged and unstaged" in first.diff_text
    assert "+new file" in first.diff_text
    assert first.fingerprint == second.fingerprint
    assert first.diff_text == second.diff_text

    _commit_all(repository, "commit working state")
    committed_state = collect_repository_changes(repository, resolved.commit)
    assert committed_state.paths == first.paths
    assert committed_state.fingerprint == first.fingerprint


def test_collect_changes_preserves_rename_and_deletion_metadata(tmp_path: Path) -> None:
    repository, base_commit = _new_repository(tmp_path)
    _git(repository, "switch", "-c", "feature/rename")
    before = collect_repository_changes(repository, base_commit)
    _git(repository, "mv", "base.txt", "renamed.txt")
    (repository / "delete-me.txt").unlink()

    changes = collect_repository_changes(repository, base_commit)
    by_path = {entry.path: entry for entry in changes.entries}

    assert by_path["renamed.txt"].kind == "renamed"
    assert by_path["renamed.txt"].old_path == "base.txt"
    assert by_path["renamed.txt"].raw_status.startswith("R")
    assert by_path["renamed.txt"].working_tree is True
    assert by_path["delete-me.txt"].kind == "deleted"
    assert changes.fingerprint != before.fingerprint


def test_collect_changes_excludes_ignored_untracked_but_keeps_tracked_ignored_file(
    tmp_path: Path,
) -> None:
    repository, base_commit = _new_repository(tmp_path)
    _git(repository, "switch", "-c", "feature/ignore")
    (repository / ".gitignore").write_text("*.log\nignored.tmp\n", encoding="utf-8")
    (repository / "tracked.log").write_text("still tracked\n", encoding="utf-8")
    (repository / "ignored.log").write_text("ignored\n", encoding="utf-8")
    (repository / "ignored.tmp").write_text("ignored\n", encoding="utf-8")

    changes = collect_repository_changes(repository, base_commit)

    assert ".gitignore" in changes.paths
    assert "tracked.log" in changes.paths
    assert "ignored.log" not in changes.paths
    assert "ignored.tmp" not in changes.paths
    assert "still tracked" in changes.diff_text


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_untracked_binary_symlink_and_content_changes_affect_fingerprint(
    tmp_path: Path,
) -> None:
    repository, base_commit = _new_repository(tmp_path)
    _git(repository, "switch", "-c", "feature/untracked")
    binary = repository / "payload.bin"
    binary.write_bytes(b"\x00\x01secret-binary")
    outside = tmp_path / "outside.txt"
    outside.write_text("FOREIGN CONTENT MUST NOT BE READ\n", encoding="utf-8")
    (repository / "external-link").symlink_to(outside)

    first = collect_repository_changes(repository, base_commit)
    binary.write_bytes(b"\x00\x02secret-binary")
    second = collect_repository_changes(repository, base_commit)

    assert "Binary file;" in first.diff_text
    assert "external-link" in first.paths
    assert "FOREIGN CONTENT MUST NOT BE READ" not in first.diff_text
    assert first.fingerprint != second.fingerprint


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation requires POSIX")
def test_git_excludes_untracked_special_file_without_reading_it(tmp_path: Path) -> None:
    repository, base_commit = _new_repository(tmp_path)
    _git(repository, "switch", "-c", "feature/fifo")
    os.mkfifo(repository / "review-pipe")

    changes = collect_repository_changes(repository, base_commit)

    assert "review-pipe" not in changes.paths
    assert "review-pipe" not in changes.diff_text


def test_resolve_merge_base_supports_explicit_nonstandard_branch_and_reports_errors(
    tmp_path: Path,
) -> None:
    repository, base_commit = _new_repository(tmp_path, initial_branch="develop")
    _git(repository, "switch", "-c", "feature/custom")

    explicit = resolve_merge_base(repository, "develop")

    assert explicit.base_ref == "develop"
    assert explicit.commit == base_commit
    with pytest.raises(RepositoryChangeError, match="could not resolve merge-base"):
        resolve_merge_base(repository)
    with pytest.raises(RepositoryChangeError, match="could not resolve merge-base"):
        resolve_merge_base(repository, "missing-base")
    with pytest.raises(RepositoryChangeError, match="rev-parse"):
        collect_repository_changes(repository, "missing-commit")


def test_non_repository_is_distinguished_from_broken_git_metadata(
    tmp_path: Path,
) -> None:
    non_repository = tmp_path / "not-a-repository"
    non_repository.mkdir()

    with pytest.raises(NotGitRepositoryError, match="not inside a Git worktree"):
        resolve_merge_base(non_repository)

    broken_repository = tmp_path / "broken-repository"
    broken_repository.mkdir()
    (broken_repository / ".git").mkdir()
    with pytest.raises(RepositoryChangeError) as caught:
        resolve_merge_base(broken_repository)
    assert not isinstance(caught.value, NotGitRepositoryError)


def test_resolve_merge_base_prefers_remote_default_branch(tmp_path: Path) -> None:
    repository, base_commit = _new_repository(tmp_path)
    _git(repository, "update-ref", "refs/remotes/origin/main", base_commit)
    _git(
        repository,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/main",
    )
    _git(repository, "switch", "-c", "feature/remote-default")

    resolved = resolve_merge_base(repository)

    assert resolved.base_ref == "origin/main"
    assert resolved.commit == base_commit


def test_agent_report_is_additive_and_cannot_restrict_or_reorder_git_paths(
    tmp_path: Path,
) -> None:
    repository, base_commit = _new_repository(tmp_path)
    _git(repository, "switch", "-c", "feature/report")
    (repository / "zeta.txt").write_text("z\n", encoding="utf-8")
    (repository / "alpha.txt").write_text("a\n", encoding="utf-8")
    changes = collect_repository_changes(repository, base_commit)

    merged = merge_reported_paths(
        changes,
        ["zeta.txt", "reported-only.txt", "alpha.txt", "reported-only.txt"],
    )

    assert tuple(merged[: len(changes.review_paths)]) == changes.review_paths
    assert merged[-1] == "reported-only.txt"
    assert merged.count("reported-only.txt") == 1


def test_render_snapshot_is_bounded_and_contains_status_and_fingerprint(
    tmp_path: Path,
) -> None:
    repository, base_commit = _new_repository(tmp_path)
    _git(repository, "switch", "-c", "feature/render")
    (repository / "large.txt").write_text("x" * 1000, encoding="utf-8")
    changes = collect_repository_changes(repository, base_commit)

    snapshot = changes.render_snapshot(max_diff_chars=80)

    assert f"since {base_commit}" in snapshot
    assert "?? large.txt" in snapshot
    assert changes.fingerprint in snapshot
    assert "...[truncated]" in snapshot


def test_large_untracked_preview_is_bounded_but_fingerprint_covers_full_content(
    tmp_path: Path,
) -> None:
    repository, base_commit = _new_repository(tmp_path)
    _git(repository, "switch", "-c", "feature/large")
    large_file = repository / "large.txt"
    large_file.write_bytes(b"x" * 300_000 + b"a")

    first = collect_repository_changes(repository, base_commit)
    large_file.write_bytes(b"x" * 300_000 + b"b")
    second = collect_repository_changes(repository, base_commit)

    assert "untracked preview truncated" in first.diff_text
    assert len(first.diff_text) < 270_000
    assert first.fingerprint != second.fingerprint
