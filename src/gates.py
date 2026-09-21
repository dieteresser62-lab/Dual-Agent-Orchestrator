from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

from contracts import AnchorChanges, AnchorRecord, compare_anchors
from path_policy import PathClass
from repo_changes import RepositoryChanges, fingerprint_change_subset


STOP_RULE_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]*$")
BRANCH_MISMATCH_RULE_ID = "BRANCH-MISMATCH"
VALIDATION_UNAVAILABLE_RULE_ID = "VALIDATION-UNAVAILABLE"
UNEXPECTED_PATH_RULE_ID = "UNEXPECTED-PATH"
CONTRACT_UNCLEAR_RULE_ID = "CONTRACT-UNCLEAR"
OPERATOR_PREREQUISITE_MISSING_RULE_ID = "OPERATOR-PREREQUISITE-MISSING"
SCOPE_EXTENSION_REQUESTED_RULE_ID = "SCOPE-EXTENSION-REQUESTED"

MISSING_PREREQUISITE_LABEL = "Missing prerequisite:"
NON_SELF_PROVISION_REASON_LABEL = "Why it cannot be self-provided:"
OPERATOR_ACTION_LABEL = "Operator action:"
SCOPE_EXTENSION_PATHS_LABEL = "Required paths:"
SCOPE_EXTENSION_REASON_LABEL = "Why required for current Slice:"


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
    StopRule(
        OPERATOR_PREREQUISITE_MISSING_RULE_ID,
        "An operator-provided prerequisite is missing and the implementer cannot supply it. "
        "The rationale must contain one non-empty line for each of these labels: "
        f"'{MISSING_PREREQUISITE_LABEL} ...', "
        f"'{NON_SELF_PROVISION_REASON_LABEL} ...', and "
        f"'{OPERATOR_ACTION_LABEL} ...'. Additional unlabeled lines are allowed; "
        "repeated labels are invalid.",
    ),
    StopRule(
        SCOPE_EXTENSION_REQUESTED_RULE_ID,
        "The current Slice requires repository paths outside its persisted scope. "
        "The rationale must contain one non-empty line for each of these labels: "
        f"'{SCOPE_EXTENSION_PATHS_LABEL} ...' and "
        f"'{SCOPE_EXTENSION_REASON_LABEL} ...'. The structured remediation_paths "
        "carry the canonical path list. Additional unlabeled lines and blank lines "
        "are allowed; repeated labels are invalid.",
    ),
)


@dataclass(frozen=True)
class OperatorPrerequisiteDetails:
    missing_prerequisite: str
    non_self_provision_reason: str
    operator_action: str


@dataclass(frozen=True)
class ScopeExtensionDetails:
    required_paths: tuple[str, ...]
    reason: str


def validate_builtin_stop_content(
    rule_id: str,
    rationale: str,
    remediation_paths: tuple[str, ...] = (),
) -> OperatorPrerequisiteDetails | ScopeExtensionDetails | None:
    """Validate rule-specific content without inferring a rule from diagnostics."""
    if rule_id == OPERATOR_PREREQUISITE_MISSING_RULE_ID:
        return _parse_operator_prerequisite_rationale(rationale)
    if rule_id == SCOPE_EXTENSION_REQUESTED_RULE_ID:
        return _parse_scope_extension_rationale(rationale, remediation_paths)
    return None


def _parse_operator_prerequisite_rationale(
    rationale: str,
) -> OperatorPrerequisiteDetails:
    fields = (
        ("missing prerequisite", MISSING_PREREQUISITE_LABEL),
        ("reason it cannot be self-provided", NON_SELF_PROVISION_REASON_LABEL),
        ("operator action", OPERATOR_ACTION_LABEL),
    )
    values: dict[str, str] = {}
    for line in rationale.splitlines():
        matching = tuple(
            (field_name, label)
            for field_name, label in fields
            if line.startswith(label)
        )
        if not matching:
            continue
        if len(matching) != 1:
            raise ValueError(
                "operator prerequisite stop has an ambiguous labeled line"
            )
        field_name, label = matching[0]
        if field_name in values:
            raise ValueError(
                f"operator prerequisite stop has duplicate {field_name}"
            )
        values[field_name] = line[len(label) :].strip()

    for field_name, _label in fields:
        if not values.get(field_name):
            raise ValueError(
                f"operator prerequisite stop requires {field_name}"
            )

    return OperatorPrerequisiteDetails(
        missing_prerequisite=values["missing prerequisite"],
        non_self_provision_reason=values["reason it cannot be self-provided"],
        operator_action=values["operator action"],
    )


def _parse_scope_extension_rationale(
    rationale: str,
    remediation_paths: tuple[str, ...],
) -> ScopeExtensionDetails:
    fields = (
        ("required paths", SCOPE_EXTENSION_PATHS_LABEL),
        ("reason", SCOPE_EXTENSION_REASON_LABEL),
    )
    values: dict[str, str] = {}
    for line in rationale.splitlines():
        matching = tuple(
            (field_name, label)
            for field_name, label in fields
            if line.startswith(label)
        )
        if not matching:
            continue
        field_name, label = matching[0]
        if field_name in values:
            raise ValueError(f"scope extension stop has duplicate {field_name}")
        values[field_name] = line[len(label) :].strip()

    for field_name, _label in fields:
        if not values.get(field_name):
            raise ValueError(f"scope extension stop requires {field_name}")

    if not remediation_paths:
        raise ValueError("scope extension stop requires remediation_paths")
    return ScopeExtensionDetails(remediation_paths, values["reason"])


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


def normalize_path_patterns(
    patterns: Iterable[str], label: str = "validation path"
) -> tuple[str, ...]:
    """Validate and normalize repository-relative POSIX glob patterns."""
    return _normalize_patterns(patterns, label)


def matches_path_patterns(path: str, patterns: Iterable[str]) -> bool:
    """Match one canonical repository path against validated POSIX globs."""
    normalized_path = _normalize_repository_path(path)
    normalized_patterns = _normalize_patterns(patterns, "validation path")
    return _matches_any(normalized_path, normalized_patterns)


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
