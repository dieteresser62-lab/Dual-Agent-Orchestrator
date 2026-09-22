from __future__ import annotations

import pytest

from artifact_models import (
    ArtifactRecord,
    Fingerprint,
    FingerprintKind,
    FindingSeverity,
    FindingTransitionPayload,
    PlanPayload,
    Role,
    SliceSpec,
    WorkUnitPayload,
)
from contracts import (
    AgentRole,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
)
from finding_decision_records import project_native_review_decision_payloads
from native_finding_decisions import (
    NativeClosureKind,
    NativeFindingClosure,
    NativeRejectionReason,
)
from native_review_contract import NativeReviewResult, NativeStatusChange
from slice_exit import (
    SliceExitStatus,
    evaluate_slice_exit,
    workflow_completion_blocking_finding_ids,
)


RUN_ID = "slice-exit-run"
PLAN_COMMIT = "a" * 40
FINGERPRINT = "b" * 64


def test_rejected_closure_projects_reason_and_named_evidence() -> None:
    closure = NativeFindingClosure(
        NativeClosureKind.REJECTED,
        NativeRejectionReason.OUT_OF_SCOPE,
        "Task and SliceBoundary both exclude src/foreign.py",
    )
    response = _review(
        NativeStatusChange(
            "C-01",
            FindingStatus.CLOSED,
            "The finding is outside this task",
            closure,
        )
    )

    payload = project_native_review_decision_payloads(
        response, (_finding(),), work_unit_id="6"
    )[0]
    restored = ArtifactRecord.from_dict(_record(1, payload).to_dict())

    assert payload.action == "status_changed"
    assert payload.closure_kind == "rejected"
    assert payload.rejection_reason == "out_of_scope"
    assert payload.closure_evidence == (
        "Task and SliceBoundary both exclude src/foreign.py"
    )
    assert restored.payload == payload


def test_recorded_fixed_closure_satisfies_slice_exit() -> None:
    records = _slice_with_opening()
    records.append(_record(len(records) + 1, _closure("C-01", "fixed"), records))

    result = evaluate_slice_exit(records, run_id=RUN_ID, slice_id="6")

    assert result.cohort_finding_ids == ("C-01",)
    assert result.status is SliceExitStatus.SATISFIED
    assert result.commit_eligible


def test_origin_slice_is_immutable_cohort_fact() -> None:
    records = _base_records()
    records.extend(
        (
            _record(
                len(records) + 1,
                WorkUnitPayload("3", 1, ("src/origin.py",)),
                records,
                logical_id="work-unit-3",
            ),
            _record(
                len(records) + 2,
                _opening("C-01", "3", FindingSeverity.BLOCKER),
                records,
            ),
            _record(
                len(records) + 3,
                WorkUnitPayload("6", 1, ("src/fix.py",)),
                records,
                logical_id="work-unit-6",
            ),
        )
    )

    origin = evaluate_slice_exit(records, run_id=RUN_ID, slice_id="3")
    current = evaluate_slice_exit(records, run_id=RUN_ID, slice_id="6")

    assert origin.cohort_finding_ids == ("C-01",)
    assert origin.condition(1).status is SliceExitStatus.VIOLATED
    assert current.cohort_finding_ids == ()
    assert current.status is SliceExitStatus.SATISFIED


def test_completion_blocks_open_finding_and_accepts_recorded_closure() -> None:
    records = _slice_with_opening()

    assert workflow_completion_blocking_finding_ids(
        records, run_id=RUN_ID
    ) == ("C-01",)

    records.append(
        _record(len(records) + 1, _closure("C-01", "rejected"), records)
    )
    assert workflow_completion_blocking_finding_ids(records, run_id=RUN_ID) == ()


def _review(*status_changes: NativeStatusChange) -> NativeReviewResult:
    return NativeReviewResult(
        request_id="native-review-request-" + "d" * 64,
        reviewer=AgentRole.CLAUDE,
        approved=False,
        new_findings=(),
        status_changes=status_changes,
        anchors=(),
        evidence=None,
        pre_mortem="A partial repair could leave the blocker reproducible.",
    )


def _finding() -> FindingRecord:
    return FindingRecord(
        "C-01",
        FindingClass.FINDING,
        FindingStatus.OPEN,
        "Repair src/fix.py",
        "src/fix.py passes its regression test",
        FindingOrigin("6", 1, AgentRole.CLAUDE),
        affected_paths=("src/fix.py",),
    )


def _base_records() -> list[ArtifactRecord]:
    return [
        _record(
            1,
            PlanPayload(
                "docs/internal/plan.md",
                PLAN_COMMIT,
                (
                    SliceSpec("3", "Origin", ("src/origin.py",)),
                    SliceSpec("6", "Current", ("src/fix.py",)),
                ),
            ),
        )
    ]


def _slice_with_opening(
    *, severity: FindingSeverity = FindingSeverity.BLOCKER
) -> list[ArtifactRecord]:
    records = _base_records()
    records.append(
        _record(
            len(records) + 1,
            WorkUnitPayload("6", 1, ("src/fix.py",)),
            records,
            logical_id="work-unit-6",
        )
    )
    records.append(
        _record(
            len(records) + 1,
            _opening("C-01", "6", severity),
            records,
        )
    )
    return records


def _opening(
    finding_id: str, slice_id: str, severity: FindingSeverity
) -> FindingTransitionPayload:
    return FindingTransitionPayload(
        finding_id=finding_id,
        reporter=Role.CLAUDE,
        actor=Role.CLAUDE,
        action="opened",
        severity=severity,
        finding_status="open",
        rationale="Repair src/fix.py",
        work_unit_id=slice_id,
        summary="Repair src/fix.py",
        acceptance_test="src/fix.py passes its regression test",
        origin_slice_id=slice_id,
        origin_round_number=1,
        affected_paths=("src/fix.py",),
    )


def _closure(finding_id: str, kind: str) -> FindingTransitionPayload:
    return FindingTransitionPayload(
        finding_id=finding_id,
        reporter=Role.CLAUDE,
        actor=Role.CLAUDE,
        action="status_changed",
        severity=FindingSeverity.BLOCKER,
        finding_status="closed",
        rationale="The reviewer records the final decision",
        work_unit_id="6",
        closure_kind=kind,
        rejection_reason="no_defect" if kind == "rejected" else None,
        closure_evidence=(
            "The named evidence disproves the finding"
            if kind == "rejected"
            else None
        ),
    )


def _record(
    sequence: int,
    payload: object,
    records: list[ArtifactRecord] | None = None,
    *,
    logical_id: str | None = None,
) -> ArtifactRecord:
    chain = records or []
    return ArtifactRecord.create(
        run_id=RUN_ID,
        logical_id=logical_id or f"record-{sequence}",
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, FINGERPRINT),
        predecessor_ids=() if not chain else (chain[-1].record_id,),
        created_at=f"2026-09-20T12:00:{sequence:02d}+00:00",
        idempotency_key=f"slice-exit:{logical_id or sequence}",
        payload=payload,
    )
