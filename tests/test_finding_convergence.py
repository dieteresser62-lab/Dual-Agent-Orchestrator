from __future__ import annotations

from artifact_models import (
    ArtifactRecord,
    CommandSpec,
    Fingerprint,
    FingerprintKind,
    FindingSeverity,
    FindingTransitionPayload,
    PlanPayload,
    ReviewPayload,
    ReviewValidationBindingPayload,
    Role,
    SliceSpec,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
    WorkflowEventPayload,
)
from finding_convergence import SliceReviewPhase, evaluate_slice_convergence


RUN_ID = "finding-convergence"
PLAN_COMMIT = "a" * 40
OLD_FINGERPRINT = "b" * 64
NEW_FINGERPRINT = "c" * 64
WORK_UNIT_ID = "6"
SLICE_ID = "1"


def test_discovery_round_treats_new_findings_as_progress() -> None:
    chain = _base_chain()
    _append_review_round(
        chain,
        round_number=1,
        fingerprint=OLD_FINGERPRINT,
        transitions=(_opening("C-01", round_number=1),),
        finding_ids=("C-01",),
    )

    result = _evaluate(chain, round_number=1)

    assert result.phase is SliceReviewPhase.DISCOVERY
    assert result.newly_opened_finding_ids == ("C-01",)
    assert result.progress_made


def test_discovery_round_counts_two_origin_bound_findings_from_the_round() -> None:
    """Regression for Canary 17: record openings exhaust the decision set."""

    chain = _base_chain()
    _append_review_round(
        chain,
        round_number=1,
        fingerprint=OLD_FINGERPRINT,
        transitions=(
            _opening_with_severity("C-01", FindingSeverity.BLOCKER),
            _opening_with_severity("C-02", FindingSeverity.FINDING),
        ),
        finding_ids=("C-01", "C-02"),
    )

    result = _evaluate(chain, round_number=1)

    assert result.phase is SliceReviewPhase.DISCOVERY
    assert result.cohort_finding_ids == ("C-01", "C-02")
    assert result.newly_opened_finding_ids == ("C-01", "C-02")
    assert result.progress_made


def test_convergence_round_does_not_count_a_bare_new_opening() -> None:
    chain = _chain_after_discovery()
    _append_review_round(
        chain,
        round_number=2,
        fingerprint=NEW_FINGERPRINT,
        transitions=(_opening("C-02", round_number=2),),
        finding_ids=("C-01", "C-02"),
    )

    result = _evaluate(chain, round_number=2)

    assert result.newly_opened_finding_ids == ("C-02",)
    assert result.closed_local_finding_ids == ()
    assert not result.progress_made


def test_convergence_round_counts_a_closed_previously_local_finding() -> None:
    chain = _chain_after_discovery()
    _append_review_round(
        chain,
        round_number=2,
        fingerprint=OLD_FINGERPRINT,
        transitions=(_closure("C-01", kind="rejected"),),
        finding_ids=("C-01",),
    )

    result = _evaluate(chain, round_number=2)

    assert result.closed_local_finding_ids == ("C-01",)
    assert result.progress_made


def test_attested_fixed_closure_proves_fingerprint_changing_remediation() -> None:
    chain = _chain_after_discovery()
    attestation = _append_attestation(chain, NEW_FINGERPRINT)
    _append_review_round(
        chain,
        round_number=2,
        fingerprint=NEW_FINGERPRINT,
        transitions=(_closure("C-01", kind="fixed"),),
        finding_ids=("C-01",),
        attestation=attestation,
    )

    result = _evaluate(chain, round_number=2)

    assert result.attested_remediation_finding_ids == ("C-01",)
    assert result.progress_made


def test_fingerprint_change_alone_does_not_prove_remediation() -> None:
    chain = _chain_after_discovery()
    _append_attestation(chain, NEW_FINGERPRINT)
    _append_review_round(
        chain,
        round_number=2,
        fingerprint=NEW_FINGERPRINT,
        transitions=(),
        finding_ids=("C-01",),
    )

    result = _evaluate(chain, round_number=2)

    assert result.attested_remediation_finding_ids == ()
    assert not result.progress_made


def test_equal_cardinality_still_progresses_when_old_closes_and_new_opens() -> None:
    chain = _chain_after_discovery()
    _append_review_round(
        chain,
        round_number=2,
        fingerprint=OLD_FINGERPRINT,
        transitions=(
            _closure("C-01", kind="rejected"),
            _opening("C-02", round_number=2),
        ),
        finding_ids=("C-01", "C-02"),
    )

    result = _evaluate(chain, round_number=2)

    assert result.newly_opened_finding_ids == ("C-02",)
    assert result.closed_local_finding_ids == ("C-01",)
    assert result.progress_made


def _base_chain() -> list[ArtifactRecord]:
    chain: list[ArtifactRecord] = []
    _append(
        chain,
        PlanPayload(
            "docs/internal/plan.md",
            PLAN_COMMIT,
            (
                SliceSpec("1", "Current", ("src/current.py",)),
                SliceSpec("2", "Later", ("src/current.py",)),
            ),
        ),
        "plan",
        OLD_FINGERPRINT,
    )
    _append(
        chain,
        WorkUnitPayload(SLICE_ID, 1, ("src/current.py",)),
        f"work-unit-{WORK_UNIT_ID}",
        OLD_FINGERPRINT,
    )
    return chain


def _chain_after_discovery() -> list[ArtifactRecord]:
    chain = _base_chain()
    _append_review_round(
        chain,
        round_number=1,
        fingerprint=OLD_FINGERPRINT,
        transitions=(_opening("C-01", round_number=1),),
        finding_ids=("C-01",),
    )
    return chain


def _opening(finding_id: str, *, round_number: int) -> FindingTransitionPayload:
    return FindingTransitionPayload(
        finding_id=finding_id,
        reporter=Role.CLAUDE,
        actor=Role.CLAUDE,
        action="opened",
        severity=FindingSeverity.FINDING,
        finding_status="open",
        rationale="The Slice must decide this finding.",
        work_unit_id=WORK_UNIT_ID,
        summary="Repair src/current.py",
        acceptance_test="src/current.py passes its regression test",
        origin_slice_id=SLICE_ID,
        origin_round_number=round_number,
    )


def _opening_with_severity(
    finding_id: str, severity: FindingSeverity
) -> FindingTransitionPayload:
    return FindingTransitionPayload(
        finding_id=finding_id,
        reporter=Role.CLAUDE,
        actor=Role.CLAUDE,
        action="opened",
        severity=severity,
        finding_status="open",
        rationale="The discovery round opened this origin-bound finding.",
        work_unit_id=WORK_UNIT_ID,
        summary=f"Repair {finding_id}",
        acceptance_test=f"The regression for {finding_id} passes",
        # Native review Slice IDs are display-padded while Work Units bind the
        # numeric Slice ID.  Membership must not depend on that representation.
        origin_slice_id=f"0{SLICE_ID}",
        origin_round_number=1,
    )


def _closure(finding_id: str, *, kind: str) -> FindingTransitionPayload:
    return FindingTransitionPayload(
        finding_id=finding_id,
        reporter=Role.CLAUDE,
        actor=Role.CLAUDE,
        action="status_changed",
        severity=FindingSeverity.FINDING,
        finding_status="closed",
        rationale="Claude records the typed decision.",
        work_unit_id=WORK_UNIT_ID,
        closure_kind=kind,
        rejection_reason="no_defect" if kind == "rejected" else None,
        closure_evidence=(
            "The counterexample passes under the bound review."
            if kind == "rejected"
            else None
        ),
    )


def _append_attestation(
    chain: list[ArtifactRecord], fingerprint: str
) -> ArtifactRecord:
    return _append(
        chain,
        ValidationAttestationPayload(
            (
                ValidationResult(
                    CommandSpec("validation", ("python3", "-m", "pytest")),
                    "pass",
                    0,
                    "d" * 64,
                ),
            ),
            Role.ORCHESTRATOR,
            "e" * 64,
            "ar1-" + "f" * 64,
        ),
        f"attestation-{len(chain)}",
        fingerprint,
    )


def _append_review_round(
    chain: list[ArtifactRecord],
    *,
    round_number: int,
    fingerprint: str,
    transitions: tuple[FindingTransitionPayload, ...],
    finding_ids: tuple[str, ...],
    attestation: ArtifactRecord | None = None,
) -> None:
    review = _append(
        chain,
        ReviewPayload(
            Role.CLAUDE,
            WORK_UNIT_ID,
            "denied",
            finding_ids,
            None,
            "native-claude-review-v2",
            "native-review-request-" + f"{round_number:x}" * 64,
            f"{round_number + 5:x}" * 64,
        ),
        f"review-round-{round_number}",
        fingerprint,
    )
    if attestation is not None:
        _append(
            chain,
            ReviewValidationBindingPayload(review.record_id, attestation.record_id),
            f"review-validation-{round_number}",
            fingerprint,
        )
    for index, transition in enumerate(transitions, start=1):
        _append(
            chain,
            transition,
            f"finding-{transition.finding_id}-round-{round_number}-{index}",
            fingerprint,
        )
    _append(
        chain,
        WorkflowEventPayload(
            "review",
            WORK_UNIT_ID,
            SLICE_ID,
            round_number,
            (review.record_id,),
        ),
        f"workflow-event-review-{round_number}",
        fingerprint,
    )


def _append(
    chain: list[ArtifactRecord], payload, logical_id: str, fingerprint: str
) -> ArtifactRecord:
    record = ArtifactRecord.create(
        run_id=RUN_ID,
        logical_id=logical_id,
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, fingerprint),
        predecessor_ids=() if not chain else (chain[-1].record_id,),
        created_at=f"2026-09-18T12:00:{len(chain):02d}+00:00",
        idempotency_key=f"finding-convergence:{logical_id}",
        payload=payload,
    )
    chain.append(record)
    return record


def _evaluate(
    chain: list[ArtifactRecord], *, round_number: int
):
    return evaluate_slice_convergence(
        chain,
        run_id=RUN_ID,
        slice_id=SLICE_ID,
        work_unit_id=WORK_UNIT_ID,
        round_number=round_number,
        approved_plan_commit=PLAN_COMMIT,
    )
