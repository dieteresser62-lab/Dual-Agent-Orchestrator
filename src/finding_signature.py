"""Deterministic signatures for semantically repeatable reviewer findings.

The signature is deliberately derived from existing Finding content.  It is
transport metadata, not persisted Finding state, so the structured record
schema and reducer semantics remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re
import unicodedata

from contracts import FindingRecord


_PATH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_./:-])"
    r"(?:[A-Za-z0-9_.@+-]+/)+[A-Za-z0-9_.@+()-]+/?"
)
_EXECUTABLE_MODE_FAMILY = "repository-executable-file-mode"
_DOCUMENTATION_EVIDENCE_FAMILY = "missing-reviewable-slice-documentation"


def mentioned_repository_paths(*texts: str) -> tuple[str, ...]:
    """Extract sorted canonical repository-relative paths mentioned in text.

    Findings predate a dedicated path field.  Reviewer prose consistently
    names repository paths either as Markdown code spans or as bare POSIX
    tokens, so a deliberately narrow lexical extractor is preferable to
    guessing from arbitrary words.  URLs, absolute paths, traversal and diff
    ``a/``/``b/`` prefixes are excluded or normalized.
    """

    paths: set[str] = set()
    for text in texts:
        normalized = unicodedata.normalize("NFKC", text)
        for match in _PATH_TOKEN.finditer(normalized):
            raw = match.group(0).rstrip("/.,;:!?")
            if match.start() >= 3 and normalized[match.start() - 3 : match.start()] == "://":
                continue
            if raw.startswith(("a/", "b/")):
                raw = raw[2:]
            path = PurePosixPath(raw)
            if (
                path.is_absolute()
                or len(path.parts) < 2
                or ".." in path.parts
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                continue
            paths.add(path.as_posix())
    return tuple(sorted(paths))


def finding_signature(
    acceptance_test: str,
    repository_paths: tuple[str, ...],
) -> str:
    """Hash a normalized acceptance test and a sorted repository-path set."""

    normalized_acceptance = " ".join(
        unicodedata.normalize("NFKC", acceptance_test).casefold().split()
    )
    family = _conservative_acceptance_family(normalized_acceptance)
    normalized_paths = tuple(
        sorted(
            {
                unicodedata.normalize("NFKC", PurePosixPath(path).as_posix())
                for path in repository_paths
            }
        )
    )
    canonical = json.dumps(
        (
            {
                "acceptance_family": family,
                "repository_paths": ["<occurrence-paths>"],
            }
            if family is not None
            else {
                "acceptance_test": normalized_acceptance,
                "repository_paths": normalized_paths,
            }
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _conservative_acceptance_family(normalized_acceptance: str) -> str | None:
    """Collapse only the two independently measured repetition families.

    Their concrete paths and Slice numbers describe additional occurrences,
    not different remediation.  The predicates intentionally require several
    co-occurring phrases.  A near miss falls back to the full acceptance/path
    signature, preserving a real finding at the cost of a possible duplicate.
    """

    mode_wording = (
        "file mode" in normalized_acceptance
        or "files' modes" in normalized_acceptance
    )
    explicit_executable_mode = (
        "executable" in normalized_acceptance
        and (
            "100644" in normalized_acceptance
            or "non-executable" in normalized_acceptance
        )
        and (
            "normaliz" in normalized_acceptance
            or "documents" in normalized_acceptance
        )
    )
    commit_hygiene_mode = all(
        phrase in normalized_acceptance
        for phrase in (
            "normalize the file mode",
            "100644",
            "git show --summary",
            "mode change",
        )
    )
    if mode_wording and (explicit_executable_mode or commit_hygiene_mode):
        return _EXECUTABLE_MODE_FAMILY
    if (
        normalized_acceptance.startswith(
            "a follow-up either updates docs/internal/"
        )
        and "includes that change in a reviewable diff" in normalized_acceptance
        and "documentation" in normalized_acceptance
    ):
        return _DOCUMENTATION_EVIDENCE_FAMILY
    return None


def finding_record_signature(finding: FindingRecord) -> str:
    """Derive one signature solely from fields already in a FindingRecord."""

    paths = mentioned_repository_paths(finding.summary, finding.acceptance_test)
    return finding_signature(finding.acceptance_test, paths)


__all__ = [
    "finding_record_signature",
    "finding_signature",
    "mentioned_repository_paths",
]
