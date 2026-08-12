from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Iterable

from contracts import AnchorChanges, AnchorRecord, compare_anchors
from repo_changes import RepositoryChanges, fingerprint_change_subset


STOP_RULE_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]*$")
PRODUCTIVE_FILE_LIMIT_RULE_ID = "PRODUCTIVE-FILE-LIMIT"
BRANCH_MISMATCH_RULE_ID = "BRANCH-MISMATCH"
VALIDATION_UNAVAILABLE_RULE_ID = "VALIDATION-UNAVAILABLE"
UNEXPECTED_PATH_RULE_ID = "UNEXPECTED-PATH"
CONTRACT_UNCLEAR_RULE_ID = "CONTRACT-UNCLEAR"


class PathClass(str, Enum):
    PRODUCTIVE = "productive"
    TEST = "test"
    DOCUMENTATION = "documentation"
    GENERATED = "generated"


@dataclass(frozen=True)
class PathClasses:
    productive: tuple[str, ...] = ("src/**/*.py", "run_task", "*.toml")
    tests: tuple[str, ...] = ("tests/**",)
    documentation: tuple[str, ...] = ("docs/**", "*.md")
    generated: tuple[str, ...] = (
        ".orchestrator/**",
        "**/__pycache__/**",
        ".pytest_cache/**",
    )

    def __post_init__(self) -> None:
        for label, patterns in (
            ("productive", self.productive),
            ("tests", self.tests),
            ("documentation", self.documentation),
            ("generated", self.generated),
        ):
            normalized = _normalize_patterns(patterns, f"{label} path")
            if normalized != patterns:
                raise ValueError(f"{label} path patterns must be normalized and unique")
        if not self.productive:
            raise ValueError("at least one productive path pattern is required")


@dataclass(frozen=True)
class StopRule:
    id: str
    description: str

    def __post_init__(self) -> None:
        if not STOP_RULE_ID_PATTERN.fullmatch(self.id):
            raise ValueError(
                f"stop rule id must match {STOP_RULE_ID_PATTERN.pattern!r}"
            )
        if not self.description.strip():
            raise ValueError("stop rule description must not be empty")


BUILTIN_STOP_RULES = (
    StopRule(
        PRODUCTIVE_FILE_LIMIT_RULE_ID,
        "More than the configured maximum number of productive change units is in scope.",
    ),
    StopRule(
        BRANCH_MISMATCH_RULE_ID,
        "The active branch differs from the feature branch persisted for this run.",
    ),
    StopRule(
        VALIDATION_UNAVAILABLE_RULE_ID,
        "Required validation cannot be executed and has no approved replacement.",
    ),
    StopRule(
        UNEXPECTED_PATH_RULE_ID,
        "The canonical diff contains a path outside the persisted Slice scope.",
    ),
    StopRule(
        CONTRACT_UNCLEAR_RULE_ID,
        "The current step contract is technically ambiguous and requires user direction.",
    ),
)


@dataclass(frozen=True)
class PathClassification:
    path: str
    path_class: PathClass
    matched_pattern: str | None


@dataclass(frozen=True)
class ChangeGroupClassification:
    paths: tuple[str, ...]
    classifications: tuple[PathClassification, ...]

    @property
    def productive(self) -> bool:
        return any(
            item.path_class is PathClass.PRODUCTIVE for item in self.classifications
        )


@dataclass(frozen=True)
class FileLimitEvidence:
    maximum: int
    classified_groups: tuple[ChangeGroupClassification, ...]

    @property
    def rule_id(self) -> str:
        return PRODUCTIVE_FILE_LIMIT_RULE_ID

    @property
    def count(self) -> int:
        return len(self.productive_groups)

    @property
    def productive_groups(self) -> tuple[ChangeGroupClassification, ...]:
        return tuple(group for group in self.classified_groups if group.productive)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    path
                    for group in self.productive_groups
                    for path in group.paths
                }
            )
        )

    @property
    def detail(self) -> str:
        classifications = "; ".join(
            ", ".join(
                f"{item.path}={item.path_class.value}"
                + (
                    f"[{item.matched_pattern}]"
                    if item.matched_pattern is not None
                    else "[unknown]"
                )
                for item in group.classifications
            )
            for group in self.classified_groups
        )
        return (
            f"{self.rule_id} | {self.count} productive change units exceed "
            f"the configured maximum of {self.maximum}: {', '.join(self.paths)} | "
            f"classifications: {classifications}"
        )


@dataclass(frozen=True)
class TestChangeEvidence:
    paths: tuple[str, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        normalized = tuple(sorted(set(self.paths)))
        if not normalized or normalized != self.paths:
            raise ValueError("test-change paths must be sorted, unique, and non-empty")
        if re.fullmatch(r"[0-9a-f]{64}", self.fingerprint) is None:
            raise ValueError("test-change evidence requires a SHA-256 fingerprint")


@dataclass(frozen=True)
class AnchorChangeEvidence:
    changes: AnchorChanges
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.changes.has_changes:
            raise ValueError("anchor-change evidence requires at least one changed anchor")
        if re.fullmatch(r"[0-9a-f]{64}", self.fingerprint) is None:
            raise ValueError("anchor-change evidence requires a SHA-256 fingerprint")


def detect_test_changes(
    changes: RepositoryChanges,
    patterns: Iterable[str],
) -> TestChangeEvidence | None:
    """Return canonical evidence for every changed path classified as a test.

    Both sides of a rename participate in classification. The subset fingerprint includes
    the complete selected change entry, but unrelated production/documentation changes do
    not invalidate an existing test approval.
    """
    normalized_patterns = _normalize_patterns(patterns)
    if not normalized_patterns:
        raise ValueError("at least one test path pattern is required")
    test_paths: set[str] = set()
    for entry in changes.entries:
        candidates = tuple(
            candidate for candidate in (entry.old_path, entry.path) if candidate is not None
        )
        if any(_matches_any(candidate, normalized_patterns) for candidate in candidates):
            test_paths.update(candidates)
    if not test_paths:
        return None
    paths = tuple(sorted(test_paths))
    return TestChangeEvidence(
        paths=paths,
        fingerprint=fingerprint_change_subset(changes, paths),
    )


def detect_anchor_changes(
    approved: Iterable[AnchorRecord],
    current: Iterable[AnchorRecord],
) -> AnchorChangeEvidence | None:
    approved_tuple = tuple(approved)
    current_tuple = tuple(current)
    changes = compare_anchors(approved_tuple, current_tuple)
    if not changes.has_changes:
        return None
    payload = {
        "version": 1,
        "anchors": [
            {
                "id": anchor.anchor_id,
                "origin": anchor.origin,
                "input": anchor.input_fixture,
                "expected": anchor.expected,
                "tolerance": anchor.tolerance,
            }
            for anchor in sorted(current_tuple, key=lambda item: item.anchor_id)
        ],
    }
    serialized = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return AnchorChangeEvidence(changes, hashlib.sha256(serialized).hexdigest())


def classify_path(path: str, classes: PathClasses) -> PathClassification:
    """Classify one canonical path, treating unknown paths as productive."""
    normalized_path = _normalize_repository_path(path)
    for path_class, patterns in (
        (PathClass.GENERATED, classes.generated),
        (PathClass.TEST, classes.tests),
        (PathClass.DOCUMENTATION, classes.documentation),
        (PathClass.PRODUCTIVE, classes.productive),
    ):
        for pattern in patterns:
            if _matches_any(normalized_path, (pattern,)):
                return PathClassification(normalized_path, path_class, pattern)
    return PathClassification(normalized_path, PathClass.PRODUCTIVE, None)


def classify_change_groups(
    change_groups: Iterable[Iterable[str]],
    classes: PathClasses,
) -> tuple[ChangeGroupClassification, ...]:
    groups: list[ChangeGroupClassification] = []
    seen: set[tuple[str, ...]] = set()
    for raw_group in change_groups:
        paths = tuple(sorted({_normalize_repository_path(path) for path in raw_group}))
        if not paths:
            raise ValueError("change groups must not be empty")
        if paths in seen:
            raise ValueError("change groups must be unique")
        seen.add(paths)
        groups.append(
            ChangeGroupClassification(
                paths,
                tuple(classify_path(path, classes) for path in paths),
            )
        )
    return tuple(groups)


def evaluate_productive_file_limit(
    change_groups: Iterable[Iterable[str]],
    classes: PathClasses,
    *,
    maximum: int = 10,
) -> FileLimitEvidence | None:
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ValueError("productive file maximum must be a positive integer")
    classified = classify_change_groups(change_groups, classes)
    productive = tuple(group for group in classified if group.productive)
    if len(productive) <= maximum:
        return None
    return FileLimitEvidence(maximum, classified)


def _normalize_patterns(
    patterns: Iterable[str], label: str = "test path"
) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw_pattern in patterns:
        if (
            not isinstance(raw_pattern, str)
            or not raw_pattern.strip()
            or "\\" in raw_pattern
        ):
            raise ValueError(f"{label} patterns must be non-empty POSIX globs")
        pattern = raw_pattern.strip()
        path = PurePosixPath(pattern)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{label} patterns must be repository-relative")
        normalized.append(pattern)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} patterns must be unique")
    return tuple(dict.fromkeys(normalized))


def _normalize_repository_path(raw_path: str) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip() or "\\" in raw_path:
        raise ValueError("changed paths must be non-empty repository-relative POSIX paths")
    path = PurePosixPath(raw_path.strip())
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("changed paths must be repository-relative without traversal")
    return path.as_posix()


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(
        fnmatch.fnmatchcase(path, pattern)
        or (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]))
        for pattern in patterns
    )
