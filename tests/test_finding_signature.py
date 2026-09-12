from __future__ import annotations

from contracts import (
    AgentRole,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
)
from finding_signature import (
    finding_record_signature,
    finding_signature,
    mentioned_repository_paths,
)


def _finding(summary: str, acceptance_test: str) -> FindingRecord:
    return FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary=summary,
        acceptance_test=acceptance_test,
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )


def test_signature_normalizes_acceptance_and_path_order() -> None:
    first = _finding(
        "Affected: `src/alpha.py` and tests/test_alpha.py.",
        "ＦＩＸ STRASSE in tests/test_alpha.py and src/alpha.py.",
    )
    second = _finding(
        "Affected: tests/test_alpha.py, then src/alpha.py.",
        "fix strasse in tests/test_alpha.py and src/alpha.py.",
    )

    assert mentioned_repository_paths(first.summary, first.acceptance_test) == (
        "src/alpha.py",
        "tests/test_alpha.py",
    )
    assert finding_record_signature(first) == finding_record_signature(second)


def test_different_acceptance_tests_on_the_same_file_remain_distinct() -> None:
    paths = ("src/worker.py",)

    assert finding_signature("Reject stale cache entries.", paths) != finding_signature(
        "Preserve valid cache entries.", paths
    )


def test_measured_repetition_families_ignore_occurrence_paths() -> None:
    first_mode = finding_signature(
        "A follow-up change normalizes the file mode of src/first.ts to 100644 "
        "or documents why the executable bit is required.",
        ("src/first.ts",),
    )
    second_mode = finding_signature(
        "A follow-up change normalizes the file mode of tests/second.test.ts to "
        "100644 or documents why the executable bit is required.",
        ("tests/second.test.ts",),
    )
    commit_hygiene_mode = finding_signature(
        "Normalize the file mode to 100644 and confirm via `git show --summary` "
        "that no mode change remains.",
        ("src/third.ts",),
    )
    first_doc = finding_signature(
        "A follow-up either updates docs/internal/slice-one.md with documentation "
        "and includes that change in a reviewable diff.",
        ("docs/internal/slice-one.md",),
    )
    second_doc = finding_signature(
        "A follow-up either updates docs/internal/slice-two.md with documentation "
        "and includes that change in a reviewable diff.",
        ("docs/internal/slice-two.md",),
    )

    assert first_mode == second_mode
    assert first_mode == commit_hygiene_mode
    assert first_doc == second_doc
    assert first_mode != first_doc


def test_near_miss_does_not_enter_a_repetition_family() -> None:
    ordinary = finding_signature(
        "Document why src/tool.py is executable.",
        ("src/tool.py",),
    )
    mode_family = finding_signature(
        "Normalize the file mode of src/tool.py to 100644 or document why the "
        "executable bit is required.",
        ("src/tool.py",),
    )

    assert ordinary != mode_family


def test_path_extraction_rejects_urls_absolute_paths_and_traversal() -> None:
    assert mentioned_repository_paths(
        "See https://example.invalid/src/no.py, /tmp/no.py, ../no.py and src/yes.py."
    ) == ("src/yes.py",)
