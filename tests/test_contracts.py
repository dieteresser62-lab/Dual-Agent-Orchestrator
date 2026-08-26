from __future__ import annotations

from dataclasses import replace

import pytest

from contracts import (
    AgentRole,
    AnchorRecord,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingResponseDecision,
    FindingStatus,
    apply_finding_response,
    apply_reviewer_finding_update,
    compare_anchors,
)


def _finding(*, status: FindingStatus = FindingStatus.OPEN) -> FindingRecord:
    return FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=status,
        summary="Native contract gap",
        acceptance_test="The native result is request-bound.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )


def test_codex_can_answer_only_an_open_finding() -> None:
    answered = apply_finding_response(
        _finding(), FindingResponseDecision.ACCEPTED, "Implemented and covered."
    )
    assert answered.responses[-1].decision is FindingResponseDecision.ACCEPTED
    with pytest.raises(ValueError, match="closed"):
        apply_finding_response(
            _finding(status=FindingStatus.CLOSED),
            FindingResponseDecision.REJECTED,
            "Already resolved.",
        )


def test_only_reporting_reviewer_can_update_a_finding() -> None:
    closed = apply_reviewer_finding_update(
        _finding(),
        reviewer=AgentRole.CLAUDE,
        status=FindingStatus.CLOSED,
        rationale="Verified against the bound JSON result.",
    )
    assert closed.status is FindingStatus.CLOSED
    with pytest.raises(ValueError, match="reporting reviewer"):
        apply_reviewer_finding_update(
            _finding(),
            reviewer=AgentRole.CODEX,
            status=FindingStatus.CLOSED,
            rationale="Foreign closure.",
        )


def test_reclassification_preserves_class_history() -> None:
    updated = apply_reviewer_finding_update(
        _finding(),
        reviewer=AgentRole.CLAUDE,
        status=FindingStatus.OPEN,
        rationale="Reduced to follow-up risk.",
        finding_class=FindingClass.OBSERVATION,
    )
    assert updated.finding_class is FindingClass.OBSERVATION
    assert updated.class_history == (FindingClass.BLOCKER,)


def test_anchor_comparison_is_typed_and_deterministic() -> None:
    approved = (AnchorRecord("a", "plan", "input", "expected", "exact"),)
    current = (
        replace(approved[0], expected="changed"),
        AnchorRecord("b", "plan", "input", "expected", "exact"),
    )
    assert compare_anchors(approved, current).added == ("b",)
    assert compare_anchors(approved, current).changed == ("a",)


def test_duplicate_anchor_ids_fail_closed() -> None:
    anchor = AnchorRecord("a", "plan", "input", "expected", "exact")
    with pytest.raises(ValueError, match="duplicate anchor"):
        compare_anchors((anchor, anchor), ())
