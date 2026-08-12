from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

from contracts import AnchorChanges, AnchorRecord, compare_anchors
from repo_changes import RepositoryChanges, fingerprint_change_subset


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


def _normalize_patterns(patterns: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw_pattern in patterns:
        if (
            not isinstance(raw_pattern, str)
            or not raw_pattern.strip()
            or "\\" in raw_pattern
        ):
            raise ValueError("test path patterns must be non-empty POSIX globs")
        pattern = raw_pattern.strip()
        path = PurePosixPath(pattern)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("test path patterns must be repository-relative")
        normalized.append(pattern)
    if not normalized:
        raise ValueError("at least one test path pattern is required")
    return tuple(dict.fromkeys(normalized))


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(
        fnmatch.fnmatchcase(path, pattern)
        or (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]))
        for pattern in patterns
    )
