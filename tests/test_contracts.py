from __future__ import annotations

from dataclasses import replace

import pytest

import contracts
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


MISSING_ANCHOR_DETAIL = (
    "predecessor_finding_ref and evidence_anchor_sha256 must be provided together "
    "or both omitted; a valid evidence_anchor_sha256 is missing, so either provide "
    "evidence_anchor_sha256 with predecessor_finding_ref or omit "
    "predecessor_finding_ref"
)
MISSING_PREDECESSOR_DETAIL = (
    "predecessor_finding_ref and evidence_anchor_sha256 must be provided together "
    "or both omitted; predecessor_finding_ref is missing, so either provide "
    "predecessor_finding_ref with evidence_anchor_sha256 or omit "
    "evidence_anchor_sha256"
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


def _generation_finding(
    *,
    predecessor_finding_ref: str | None = None,
    evidence_anchor_sha256: str | None = None,
) -> FindingRecord:
    return FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.FINDING,
        status=FindingStatus.OPEN,
        summary="Rediscovered defect",
        acceptance_test="The generation identity is complete.",
        origin=FindingOrigin("07", 1, AgentRole.CLAUDE),
        predecessor_finding_ref=predecessor_finding_ref,
        evidence_anchor_sha256=evidence_anchor_sha256,
    )


def _assert_u14_pair_diagnostics_are_complete() -> None:
    cases = (
        ({"predecessor_finding_ref": "C-01"}, MISSING_ANCHOR_DETAIL),
        ({"evidence_anchor_sha256": "a" * 64}, MISSING_PREDECESSOR_DETAIL),
    )
    for arguments, expected in cases:
        try:
            _generation_finding(**arguments)
        except ValueError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError("an incomplete Finding generation identity was accepted")


def _assert_u14_independent_form_errors_are_complete() -> None:
    try:
        _generation_finding(
            predecessor_finding_ref="not-a-finding-id",
            evidence_anchor_sha256="not-a-digest",
        )
    except ValueError as exc:
        detail = str(exc)
        assert "predecessor finding reference is invalid" in detail
        assert "a valid evidence_anchor_sha256 is missing" in detail
    else:
        raise AssertionError("two malformed Finding generation fields were accepted")


def test_finding_generation_identity_requires_both_fields_or_neither() -> None:
    _assert_u14_pair_diagnostics_are_complete()

    assert _generation_finding().predecessor_finding_ref is None
    complete = _generation_finding(
        predecessor_finding_ref="C-01",
        evidence_anchor_sha256="a" * 64,
    )
    assert complete.predecessor_finding_ref == "C-01"
    assert complete.evidence_anchor_sha256 == "a" * 64


def test_finding_generation_identity_reports_independent_form_errors_together() -> None:
    _assert_u14_independent_form_errors_are_complete()


@pytest.mark.parametrize(
    "one_sided_detail",
    (
        "new Finding generation requires its evidence anchor digest",
        "evidence anchor digest requires a predecessor Finding reference",
    ),
)
def test_u14_proof_kills_one_sided_pair_diagnostic_mutations(
    monkeypatch: pytest.MonkeyPatch,
    one_sided_detail: str,
) -> None:
    def mutant(
        finding_id: object,
        predecessor_finding_ref: object | None,
        evidence_anchor_sha256: object | None,
    ) -> None:
        del finding_id, predecessor_finding_ref, evidence_anchor_sha256
        raise ValueError(one_sided_detail)

    monkeypatch.setattr(
        contracts,
        "_validate_finding_generation_identity",
        mutant,
    )

    with pytest.raises(AssertionError):
        _assert_u14_pair_diagnostics_are_complete()


@pytest.mark.parametrize(
    "one_error_detail",
    (
        "predecessor finding reference is invalid",
        MISSING_ANCHOR_DETAIL,
    ),
)
def test_u14_proof_kills_single_error_aggregation_mutations(
    monkeypatch: pytest.MonkeyPatch,
    one_error_detail: str,
) -> None:
    def mutant(
        finding_id: object,
        predecessor_finding_ref: object | None,
        evidence_anchor_sha256: object | None,
    ) -> None:
        del finding_id, predecessor_finding_ref, evidence_anchor_sha256
        raise ValueError(one_error_detail)

    monkeypatch.setattr(
        contracts,
        "_validate_finding_generation_identity",
        mutant,
    )

    with pytest.raises(AssertionError):
        _assert_u14_independent_form_errors_are_complete()


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
