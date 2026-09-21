from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from contracts import AnchorRecord
from gates import (
    PathClass,
    PathClasses,
    SCOPE_EXTENSION_REQUESTED_RULE_ID,
    ScopeExtensionDetails,
    classify_change_groups,
    detect_anchor_changes,
    detect_test_changes,
    validate_builtin_stop_content,
)
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


def test_scope_extension_stop_accepts_blank_and_unlabeled_explanation_lines() -> None:
    rationale = (
        "Required paths: docs/guide.md, src/runtime.py\n\n"
        "Planning could not see the concrete adapter dependency.\n\n"
        "Why required for current Slice: both paths are needed to finish the behavior"
    )

    details = validate_builtin_stop_content(
        SCOPE_EXTENSION_REQUESTED_RULE_ID,
        rationale,
        ("docs/guide.md", "src/runtime.py"),
    )

    assert details == ScopeExtensionDetails(
        ("docs/guide.md", "src/runtime.py"),
        "both paths are needed to finish the behavior",
    )


@pytest.mark.parametrize(
    ("rationale", "message"),
    (
        (
            "Why required for current Slice: needed to finish",
            "requires required paths",
        ),
        ("Required paths: src/runtime.py", "requires reason"),
        (
            "Required paths: src/runtime.py\n"
            "Required paths: src/other.py\n"
            "Why required for current Slice: needed to finish",
            "duplicate required paths",
        ),
        (
            "Required paths: src/runtime.py\n"
            "Why required for current Slice: needed to finish",
            "requires remediation_paths",
        ),
    ),
)
def test_scope_extension_stop_rejects_incomplete_or_divergent_labels(
    rationale: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_builtin_stop_content(
            SCOPE_EXTENSION_REQUESTED_RULE_ID,
            rationale,
            () if message == "requires remediation_paths" else ("src/other.py",),
        )


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


def test_path_classification_handles_fifteen_productive_change_groups() -> None:
    classes = PathClasses(productive=("src/**",))
    fifteen = tuple((f"src/file_{index}.py",) for index in range(15))

    classified = classify_change_groups(fifteen, classes)

    assert len(classified) == 15
    assert all(group.productive for group in classified)
    assert tuple(group.paths for group in classified) == fifteen


def test_path_classes_exclude_tests_docs_generated_and_fail_closed_unknown() -> None:
    classes = PathClasses(
        productive=("app/**",),
        tests=("spec/**",),
        documentation=("notes/**",),
        generated=("build/**",),
    )
    groups = classify_change_groups(
        (
            ("spec/check.py",),
            ("notes/design.md",),
            ("build/cache.bin",),
            ("unclassified.payload",),
        ),
        classes,
    )

    assert [group.classifications[0].path_class for group in groups] == [
        PathClass.TEST,
        PathClass.DOCUMENTATION,
        PathClass.GENERATED,
        PathClass.PRODUCTIVE,
    ]
    assert groups[-1].classifications[0].matched_pattern is None
def test_rename_is_one_change_unit_and_either_side_can_make_it_productive() -> None:
    classes = PathClasses(
        productive=("src/**",),
        documentation=("docs/**",),
    )
    rename = (("docs/old.md", "src/new.py"),)
    classified = classify_change_groups(rename, classes)

    assert len(classified) == 1
    assert classified[0].productive
    assert {item.path_class for item in classified[0].classifications} == {
        PathClass.DOCUMENTATION,
        PathClass.PRODUCTIVE,
    }
