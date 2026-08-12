from __future__ import annotations

import subprocess
from pathlib import Path

from contracts import AnchorRecord
from gates import detect_anchor_changes, detect_test_changes
from repo_changes import collect_repository_changes


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


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    (repository / "tests" / "unit").mkdir(parents=True)
    (repository / "src").mkdir()
    _git(repository, "init", "-b", "master")
    _git(repository, "config", "user.name", "Gate Tests")
    _git(repository, "config", "user.email", "gate-tests@example.invalid")
    (repository / "tests" / "existing.py").write_text("old\n", encoding="utf-8")
    (repository / "tests" / "move.py").write_text("move\n", encoding="utf-8")
    (repository / "src" / "product.py").write_text("product\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "base")
    return repository, _git(repository, "rev-parse", "HEAD")


def test_test_gate_detects_modified_untracked_and_both_sides_of_rename(
    tmp_path: Path,
) -> None:
    repository, base = _repository(tmp_path)
    (repository / "tests" / "existing.py").write_text("changed\n", encoding="utf-8")
    (repository / "tests" / "unit" / "new.py").write_text("new\n", encoding="utf-8")
    (repository / "tests" / "move.py").rename(repository / "src" / "moved_test.py")
    _git(repository, "add", "-A", "tests/move.py", "src/moved_test.py")

    evidence = detect_test_changes(
        collect_repository_changes(repository, base), ("tests/**",)
    )

    assert evidence is not None
    assert evidence.paths == (
        "src/moved_test.py",
        "tests/existing.py",
        "tests/move.py",
        "tests/unit/new.py",
    )


def test_test_subset_fingerprint_ignores_production_only_changes_and_expires_on_test_change(
    tmp_path: Path,
) -> None:
    repository, base = _repository(tmp_path)
    test_file = repository / "tests" / "existing.py"
    product_file = repository / "src" / "product.py"
    test_file.write_text("approved test delta\n", encoding="utf-8")
    first = detect_test_changes(collect_repository_changes(repository, base), ("tests/**",))
    assert first is not None

    product_file.write_text("unrelated production delta\n", encoding="utf-8")
    production_changed = detect_test_changes(
        collect_repository_changes(repository, base), ("tests/**",)
    )
    assert production_changed is not None
    assert production_changed.fingerprint == first.fingerprint

    test_file.write_text("new test delta\n", encoding="utf-8")
    test_changed = detect_test_changes(
        collect_repository_changes(repository, base), ("tests/**",)
    )
    assert test_changed is not None
    assert test_changed.fingerprint != first.fingerprint


def test_test_subset_fingerprint_uses_the_captured_repository_snapshot(
    tmp_path: Path,
) -> None:
    repository, base = _repository(tmp_path)
    test_file = repository / "tests" / "existing.py"
    test_file.write_text("captured\n", encoding="utf-8")
    snapshot = collect_repository_changes(repository, base)
    captured = detect_test_changes(snapshot, ("tests/**",))
    assert captured is not None

    test_file.write_text("mutated after collection\n", encoding="utf-8")
    repeated = detect_test_changes(snapshot, ("tests/**",))

    assert repeated is not None
    assert repeated.fingerprint == captured.fingerprint


def test_test_gate_returns_none_without_matching_paths(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    (repository / "src" / "product.py").write_text("changed\n", encoding="utf-8")

    assert (
        detect_test_changes(
            collect_repository_changes(repository, base), ("tests/**",)
        )
        is None
    )


def test_anchor_gate_reports_added_removed_changed_and_stable_current_fingerprint() -> None:
    approved = (
        AnchorRecord("A", "plan", "1", "2", "exact"),
        AnchorRecord("B", "plan", "2", "4", "exact"),
    )
    current = (
        AnchorRecord("A", "plan", "1", "3", "exact"),
        AnchorRecord("C", "plan", "3", "6", "exact"),
    )

    first = detect_anchor_changes(approved, current)
    reordered = detect_anchor_changes(approved, tuple(reversed(current)))

    assert first is not None
    assert reordered is not None
    assert first.changes.added == ("C",)
    assert first.changes.removed == ("B",)
    assert first.changes.changed == ("A",)
    assert reordered.fingerprint == first.fingerprint
    assert detect_anchor_changes(approved, approved) is None
