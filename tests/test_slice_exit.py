from __future__ import annotations

import pytest
import native_finding_decisions

from artifact_models import (
    ArtifactRecord,
    BindingPayload,
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
from finding_responsibility import (
    BranchPlanningResponsibility,
    SliceResponsibility,
)
from native_finding_decisions import (
    NativeClosureKind,
    NativeFindingClosure,
    NativeRejectionReason,
    NativeResponsibilityProposal,
    NativeResponsibilityRoute,
)
from native_review_contract import (
    NativeReviewResult,
    NativeStatusChange,
)
from slice_exit import (
    CONDITION_4_ACCEPTANCE_UNDECIDABLE,
    SliceExitStatus,
    evaluate_slice_exit,
)


RUN_ID = "slice-exit-run"
PLAN_COMMIT = "a" * 40
FINGERPRINT = "b" * 64


@pytest.fixture
def decisions_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )


def test_reviewer_route_projects_to_open_routed_record_unchanged(
    decisions_enabled: None,
) -> None:
    target = SliceResponsibility(RUN_ID, PLAN_COMMIT, "7")
    response = _review(
        routes=(NativeResponsibilityRoute("C-01", target, "Slice 7 owns it"),)
    )

    payloads = project_native_review_decision_payloads(
        response, (_finding(),), work_unit_id="6"
    )

    assert payloads == (
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="routed",
            severity=FindingSeverity.OBSERVATION,
            finding_status="open",
            rationale="Slice 7 owns it",
            work_unit_id="6",
            responsibility=target,
        ),
    )


def test_rejected_closure_projects_reason_and_named_evidence(
    decisions_enabled: None,
) -> None:
    closure = NativeFindingClosure(
        NativeClosureKind.REJECTED,
        NativeRejectionReason.OUT_OF_SCOPE,
        "Task and SliceBoundary both exclude src/foreign.py",
    )
    response = _review(
        status_changes=(
            NativeStatusChange(
                "C-01",
                FindingStatus.CLOSED,
                "The finding is outside this task",
                closure,
            ),
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


def test_codex_responsibility_proposal_cannot_enter_reviewer_projection(
    decisions_enabled: None,
) -> None:
    proposal = NativeResponsibilityProposal(
        "C-01",
        SliceResponsibility(RUN_ID, PLAN_COMMIT, "7"),
        "Codex suggests a later owner",
    )

    with pytest.raises(TypeError, match="parsed NativeReviewResult"):
        project_native_review_decision_payloads(  # type: ignore[arg-type]
            proposal, (_finding(),), work_unit_id="6"
        )


def test_decision_projection_is_dormant_under_the_single_cutover_switch() -> None:
    assert native_finding_decisions.JOINT_67_68_NATIVE_CONTRACT_CUTOVER is False
    response = _review(
        routes=(
            NativeResponsibilityRoute(
                "C-01",
                SliceResponsibility(RUN_ID, PLAN_COMMIT, "7"),
                "Later Slice",
            ),
        )
    )

    assert project_native_review_decision_payloads(
        response, (_finding(),), work_unit_id="6"
    ) == ()


def test_a_s_keeps_ever_routed_finding_after_slice_s_routes_it_onward() -> None:
    records = _base_records()
    records.extend(
        (
            _record(
                len(records) + 1,
                _opening(
                    "C-01", "3", SliceResponsibility(RUN_ID, PLAN_COMMIT, "3")
                ),
                records,
            ),
            _record(
                len(records) + 2,
                _route("C-01", SliceResponsibility(RUN_ID, PLAN_COMMIT, "6"), "3"),
                records,
            ),
            _record(
                len(records) + 3,
                WorkUnitPayload("6", 1, ("src/fix.py",), ("C-01",)),
                records,
                logical_id="work-unit-6",
            ),
            _record(
                len(records) + 4,
                _route("C-01", BranchPlanningResponsibility("family-1", 1), "6"),
                records,
            ),
        )
    )

    result = evaluate_slice_exit(records, run_id=RUN_ID, slice_id="6")

    assert result.responsibility_finding_ids == ("C-01",)
    assert result.condition(5).status is SliceExitStatus.VIOLATED
    assert "run-bound family identity" in result.condition(5).reasons[0]


def test_open_blocker_cannot_be_routed_out_of_its_slice() -> None:
    records = _slice_with_opening(
        severity=FindingSeverity.BLOCKER,
        responsibility=SliceResponsibility(RUN_ID, PLAN_COMMIT, "6"),
    )
    records.append(
            _record(
                len(records) + 1,
                _route(
                    "C-01",
                    BranchPlanningResponsibility("family-1", 1),
                    "6",
                    severity=FindingSeverity.BLOCKER,
                ),
            records,
        )
    )

    result = evaluate_slice_exit(records, run_id=RUN_ID, slice_id="6")

    assert result.condition(1).status is SliceExitStatus.VIOLATED
    assert "open BLOCKER C-01" in result.condition(1).reasons[0]
    assert not result.commit_eligible


def test_blocker_downgrade_without_record_evidence_is_rejected() -> None:
    records = _slice_with_opening(
        severity=FindingSeverity.BLOCKER,
        responsibility=SliceResponsibility(RUN_ID, PLAN_COMMIT, "6"),
    )
    records.extend(
        (
            _record(len(records) + 1, _reclassification("C-01"), records),
            _record(
                len(records) + 2,
                _route("C-01", BranchPlanningResponsibility("family-1", 1), "6"),
                records,
            ),
        )
    )

    result = evaluate_slice_exit(records, run_id=RUN_ID, slice_id="6")

    assert result.condition(1).status is SliceExitStatus.VIOLATED
    assert "without earlier record-bound evidence" in result.condition(1).reasons[0]


def test_current_slice_responsibility_violates_condition_3() -> None:
    records = _slice_with_opening(
        responsibility=SliceResponsibility(RUN_ID, PLAN_COMMIT, "6")
    )

    result = evaluate_slice_exit(records, run_id=RUN_ID, slice_id="6")

    assert result.condition(3).status is SliceExitStatus.VIOLATED
    assert "slice:6" in result.condition(3).reasons[0]


def test_recorded_fixed_closure_clears_local_responsibility_and_satisfies_exit() -> None:
    records = _slice_with_opening(
        responsibility=SliceResponsibility(RUN_ID, PLAN_COMMIT, "6")
    )
    records.append(
        _record(
            len(records) + 1,
            FindingTransitionPayload(
                finding_id="C-01",
                reporter=Role.CLAUDE,
                actor=Role.CLAUDE,
                action="status_changed",
                severity=FindingSeverity.OBSERVATION,
                finding_status="closed",
                rationale="The fingerprint-bound repair passes its acceptance test",
                work_unit_id="6",
                closure_kind="fixed",
            ),
            records,
        )
    )

    result = evaluate_slice_exit(records, run_id=RUN_ID, slice_id="6")

    assert result.status is SliceExitStatus.SATISFIED
    assert result.condition(3).status is SliceExitStatus.SATISFIED
    assert result.condition(6).status is SliceExitStatus.SATISFIED


def test_route_to_completed_later_plan_slice_violates_condition_4() -> None:
    records = _slice_with_opening(
        responsibility=SliceResponsibility(RUN_ID, PLAN_COMMIT, "6")
    )
    records.extend(
        (
            _record(
                len(records) + 1,
                _route("C-01", SliceResponsibility(RUN_ID, PLAN_COMMIT, "7"), "6"),
                records,
            ),
            _record(
                len(records) + 2,
                BindingPayload("commit", "c" * 40, "attestation", ("approval",)),
                records,
                logical_id="commit-7-cccccccccccc",
            ),
        )
    )

    result = evaluate_slice_exit(records, run_id=RUN_ID, slice_id="6")

    assert result.condition(4).status is SliceExitStatus.VIOLATED
    assert "already completed Slice 7" in result.condition(4).reasons[0]


def test_scope_valid_later_slice_route_is_explicitly_indeterminate() -> None:
    records = _slice_with_opening(
        responsibility=SliceResponsibility(RUN_ID, PLAN_COMMIT, "6")
    )
    records.append(
        _record(
            len(records) + 1,
            _route("C-01", SliceResponsibility(RUN_ID, PLAN_COMMIT, "7"), "6"),
            records,
        )
    )

    result = evaluate_slice_exit(records, run_id=RUN_ID, slice_id="6")

    assert result.condition(4).status is SliceExitStatus.INDETERMINATE
    assert CONDITION_4_ACCEPTANCE_UNDECIDABLE in result.condition(4).reasons[0]
    assert result.status is SliceExitStatus.INDETERMINATE
    assert not result.commit_eligible


def test_projection_or_markdown_only_decision_cannot_satisfy_condition_6() -> None:
    records = _slice_with_opening(responsibility=None)
    non_authoritative_projection = {"C-01": {"status": "closed"}}
    non_authoritative_markdown = "C-01 was fixed"

    result = evaluate_slice_exit(records, run_id=RUN_ID, slice_id="6")

    assert non_authoritative_projection["C-01"]["status"] == "closed"
    assert "fixed" in non_authoritative_markdown
    assert result.condition(6).status is SliceExitStatus.VIOLATED
    assert "no transition record" in result.condition(6).reasons[0]


def _review(
    *,
    status_changes: tuple[NativeStatusChange, ...] = (),
    routes: tuple[NativeResponsibilityRoute, ...] = (),
) -> NativeReviewResult:
    return NativeReviewResult(
        request_id="native-review-request-" + "d" * 64,
        reviewer=AgentRole.CLAUDE,
        approved=False,
        new_findings=(),
        status_changes=status_changes,
        reclassifications=(),
        responsibility_routes=routes,
        anchors=(),
        evidence=None,
        pre_mortem=None,
    )


def _finding() -> FindingRecord:
    return FindingRecord(
        "C-01",
        FindingClass.OBSERVATION,
        FindingStatus.OPEN,
        "Repair src/fix.py",
        "src/fix.py passes its regression test",
        FindingOrigin("6", 1, AgentRole.CLAUDE),
    )


def _base_records() -> list[ArtifactRecord]:
    records: list[ArtifactRecord] = []
    records.append(
        _record(
            1,
            PlanPayload(
                "docs/internal/plan.md",
                PLAN_COMMIT,
                (
                    SliceSpec("3", "Origin", ("src/origin.py",)),
                    SliceSpec("6", "Current", ("src/fix.py",)),
                    SliceSpec("7", "Follow-up", ("src/fix.py",)),
                ),
            ),
            records,
        )
    )
    return records


def _slice_with_opening(
    *,
    severity: FindingSeverity = FindingSeverity.OBSERVATION,
    responsibility,
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
            _opening("C-01", "6", responsibility, severity=severity),
            records,
        )
    )
    return records


def _opening(
    finding_id: str,
    slice_id: str,
    responsibility,
    *,
    severity: FindingSeverity = FindingSeverity.OBSERVATION,
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
        responsibility=responsibility,
    )


def _route(
    finding_id: str,
    responsibility,
    work_unit_id: str,
    *,
    severity: FindingSeverity = FindingSeverity.OBSERVATION,
) -> FindingTransitionPayload:
    return FindingTransitionPayload(
        finding_id=finding_id,
        reporter=Role.CLAUDE,
        actor=Role.CLAUDE,
        action="routed",
        severity=severity,
        finding_status="open",
        rationale="Transfer to the recorded owner",
        work_unit_id=work_unit_id,
        responsibility=responsibility,
    )


def _reclassification(finding_id: str) -> FindingTransitionPayload:
    return FindingTransitionPayload(
        finding_id=finding_id,
        reporter=Role.CLAUDE,
        actor=Role.CLAUDE,
        action="reclassified",
        severity=FindingSeverity.OBSERVATION,
        finding_status="open",
        rationale="Downgrade without a prior evidence record",
        work_unit_id="6",
    )


def _record(
    sequence: int,
    payload,
    records: list[ArtifactRecord] | None = None,
    *,
    logical_id: str | None = None,
) -> ArtifactRecord:
    return ArtifactRecord.create(
        run_id=RUN_ID,
        logical_id=logical_id or f"slice-exit-{sequence}",
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, FINGERPRINT),
        predecessor_ids=() if not records else (records[-1].record_id,),
        created_at=f"2026-09-18T12:00:{sequence:02d}+00:00",
        idempotency_key=f"slice-exit:{sequence}",
        payload=payload,
    )
