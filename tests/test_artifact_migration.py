from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from artifact_bridge import ArtifactBridge
from artifact_migration import ArtifactResumeError, resolve_resume_state
from artifact_models import (
    BindingPayload,
    CommandSpec,
    FingerprintKind,
    PlanPayload,
    ReviewPayload,
    Role,
    SliceSpec,
    TaskPayload,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
)
from artifact_store import ArtifactStore
from contracts import PlannedSlice
from workflow_state import (
    AgentFailureKind,
    GateReason,
    InvocationFailureRecord,
    ProtocolBinding,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    init_workflow_state,
)


def _state(repository: Path, *, structured: bool = True):
    task = repository / "task.md"
    task.write_text("task", encoding="utf-8")
    state = init_workflow_state(
        run_id="resume-run",
        task_file=str(task),
        branch="feature/resume",
        branch_base="b" * 40,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/resume.py",),
        target_branch="feature/resume",
        protocol_binding=(
            ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1") if structured else None
        ),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="b" * 40,
        scope_paths=("src/resume.py",),
        start_fingerprint="c" * 64,
    )
    return state


def _records(repository: Path, state) -> None:
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    bridge.append(
        TaskPayload("feature/resume", ("src/resume.py",), "a" * 64),
        logical_id="task-contract",
        idempotency_key="task-contract",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        WorkUnitPayload("1", 1, ("src/resume.py",)),
        logical_id="work-unit-2",
        idempotency_key="work-unit:2:round:1",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )


def _authorization_records(
    repository: Path,
    state: WorkflowState,
    *,
    attestation_fingerprint: str,
    review_fingerprint: str,
    review_verdict: str = "approved",
):
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    attestation = bridge.append(
        ValidationAttestationPayload(
            results=(
                ValidationResult(
                    command=CommandSpec("pytest", ("python3", "-m", "pytest")),
                    outcome="pass",
                    exit_code=0,
                    output_sha256="e" * 64,
                ),
            ),
            attested_by=Role.ORCHESTRATOR,
        ),
        logical_id="validation-resume",
        idempotency_key=f"validation-resume:{attestation_fingerprint}",
        fingerprint_sha256=attestation_fingerprint,
    )
    review = bridge.append(
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="2",
            verdict=review_verdict,
            finding_ids=(),
            evidence="resume authorization reviewed",
        ),
        logical_id="review-resume",
        idempotency_key=f"review-resume:{review_fingerprint}:{review_verdict}",
        fingerprint_sha256=review_fingerprint,
    )
    return attestation, review


def test_legacy_state_without_records_remains_on_legacy_path(tmp_path: Path) -> None:
    state = _state(tmp_path, structured=False)

    resolved = resolve_resume_state(tmp_path, state)

    assert resolved.state == state
    assert resolved.mode is ProtocolMode.LEGACY_STATE_V3
    assert resolved.record_head_id is None
    assert not (tmp_path / ".orchestrator" / "artifacts").exists()


def test_structured_state_rehydrates_from_matching_complete_chain(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)

    resolved = resolve_resume_state(tmp_path, state)

    assert resolved.state == state
    assert resolved.mode is ProtocolMode.STRUCTURED_V1
    assert resolved.record_head_id is not None


def test_structured_state_without_records_halts_with_repair_hint(tmp_path: Path) -> None:
    with pytest.raises(ArtifactResumeError, match="restore its record directory"):
        resolve_resume_state(tmp_path, _state(tmp_path))


def test_structured_state_rejects_a_stale_round_with_record_id(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    stale = replace(
        state,
        work_units=(state.work_units[0], replace(state.work_units[1], round_number=2)),
    )

    with pytest.raises(ArtifactResumeError, match=r"record ar1-[0-9a-f]{64}.*round"):
        resolve_resume_state(tmp_path, stale)


def test_structured_resume_halts_when_legacy_state_missing_approved_plan_commit_but_chain_has_multiple_plan_records(
    tmp_path: Path,
) -> None:
    planned_slices = (PlannedSlice(1, "resume", ("src/resume.py",)),)
    state = replace(
        _state(tmp_path),
        planned_slices=planned_slices,
        work_plan_path="docs/internal/approved-plan.md",
        approved_plan_commit="b" * 40,
    )
    legacy_document = state.to_dict()
    legacy_document.pop("approved_plan_commit")
    legacy_state = WorkflowState.from_dict(legacy_document)
    assert legacy_state.approved_plan_commit is None
    _records(tmp_path, legacy_state)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, legacy_state.run_id))
    slices = (SliceSpec("1", "resume", ("src/resume.py",)),)
    for commit in ("b" * 40, "c" * 40):
        bridge.append(
            PlanPayload("docs/internal/approved-plan.md", commit, slices),
            logical_id="approved-plan",
            idempotency_key=f"approved-plan:{commit}",
            fingerprint_sha256=commit + "0" * 24,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )

    with pytest.raises(
        ArtifactResumeError,
        match="expected exactly one immutable approved-plan record, found 2",
    ):
        resolve_resume_state(tmp_path, legacy_state)


def test_structured_resume_halts_when_mirror_gate_decision_has_no_chain_record(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    state = state.await_user_gate(
        reason=GateReason.TEST_CHANGE,
        detail="changed test requires approval",
        fingerprint="d" * 64,
        paths=("tests/test_resume.py",),
    ).record_user_gate_decision(
        approved=True,
        fingerprint="d" * 64,
        paths=("tests/test_resume.py",),
        decided_by="user",
        decided_at="2026-08-18T12:00:00+00:00",
        rationale="approve changed resume test",
    )

    with pytest.raises(ArtifactResumeError, match="gate decisions differ from state-v3"):
        resolve_resume_state(tmp_path, state)


def test_structured_resume_halts_when_mirror_finding_transition_has_no_chain_record(
    tmp_path: Path,
) -> None:
    state = replace(
        _state(tmp_path),
        runtime_history={
            "findings": [
                {
                    "finding_id": "C-05",
                    "status": "OPEN",
                }
            ]
        },
    )
    _records(tmp_path, state)

    with pytest.raises(
        ArtifactResumeError,
        match="finding transitions differ from state-v3",
    ):
        resolve_resume_state(tmp_path, state)


def test_structured_resume_halts_when_mirror_quota_pause_has_no_chain_record(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    failure = InvocationFailureRecord(
        invocation_id="inv-quota-mirror-only",
        idempotency_key="resume-run:2:codex_implementation:claude",
        role="claude",
        failure_kind=AgentFailureKind.QUOTA,
        provider_text="usage cap reached; retry later",
        received_at="2026-08-18T12:00:00+00:00",
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_id=1,
        work_unit_id=2,
        diagnostic_exit_code=2,
        parse_path="claude:text:absolute",
        source_timezone="UTC",
        reset_at_utc="2026-08-18T12:05:00+00:00",
        resume_at_utc="2026-08-18T12:05:30+00:00",
        safety_margin_seconds=30,
        automatic_resume=True,
        diff_fingerprint="d" * 64,
    )
    state = state.record_invocation_failure(
        failure,
        wait_automatically=True,
    ).resume_after_invocation_halt(updated_at="2026-08-18T12:05:30+00:00")

    with pytest.raises(ArtifactResumeError, match="quota pauses differ from state-v3"):
        resolve_resume_state(tmp_path, state)


def test_structured_resume_halts_when_state_mirror_reports_completion_without_chain_record(
    tmp_path: Path,
) -> None:
    state = (
        _state(tmp_path)
        .complete_current_slice(commit_ref="d" * 40)
        .start_final_review_work_unit()
        .complete_current_work_unit()
    )
    _records(tmp_path, state)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    attestation, review = _authorization_records(
        tmp_path,
        state,
        attestation_fingerprint="d" * 64,
        review_fingerprint="d" * 64,
    )
    state = replace(
        state,
        runtime_history={
            "attestations": [
                {
                    "attestation_id": attestation.logical_id,
                    "diff_fingerprint": attestation.fingerprint.sha256,
                }
            ]
        },
    )
    bridge.append(
        WorkUnitPayload("1", 1, ("src/resume.py",)),
        logical_id="work-unit-3",
        idempotency_key="work-unit:3:round:1",
        fingerprint_sha256="d" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        BindingPayload(
            binding_kind="commit",
            target="d" * 40,
            attestation_id=attestation.record_id,
            approval_ids=(review.record_id,),
        ),
        logical_id="commit-1",
        idempotency_key="commit:1",
        fingerprint_sha256="d" * 64,
    )

    with pytest.raises(
        ArtifactResumeError,
        match="state-v3 mirror reports workflow completion without a structured record",
    ):
        resolve_resume_state(tmp_path, state)


@pytest.mark.parametrize(
    ("attestation_mode", "approval_mode", "review_verdict", "message"),
    (
        ("unknown", "valid", "approved", "unknown, invalid, or fingerprint-mismatched"),
        ("mismatched", "valid", "approved", "unknown, invalid, or fingerprint-mismatched"),
        ("valid", "unknown", "approved", "unknown, unapproved, or fingerprint-mismatched"),
        ("valid", "mismatched", "approved", "unknown, unapproved, or fingerprint-mismatched"),
        ("valid", "valid", "denied", "unknown, unapproved, or fingerprint-mismatched"),
    ),
)
def test_structured_resume_halts_when_commit_binding_references_unknown_or_mismatched_attestation_or_approval(
    tmp_path: Path,
    attestation_mode: str,
    approval_mode: str,
    review_verdict: str,
    message: str,
) -> None:
    binding_fingerprint = "d" * 64
    attestation_fingerprint = (
        "e" * 64 if attestation_mode == "mismatched" else binding_fingerprint
    )
    review_fingerprint = (
        "e" * 64 if approval_mode == "mismatched" else binding_fingerprint
    )
    state = _state(tmp_path).complete_current_slice(commit_ref="d" * 40)
    _records(tmp_path, state)
    attestation, review = _authorization_records(
        tmp_path,
        state,
        attestation_fingerprint=attestation_fingerprint,
        review_fingerprint=review_fingerprint,
        review_verdict=review_verdict,
    )
    state = replace(
        state,
        runtime_history={
            "attestations": [
                {
                    "attestation_id": attestation.logical_id,
                    "diff_fingerprint": attestation.fingerprint.sha256,
                }
            ]
        },
    )
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    bridge.append(
        BindingPayload(
            binding_kind="commit",
            target="d" * 40,
            attestation_id=(
                "ar1-" + "f" * 64
                if attestation_mode == "unknown"
                else attestation.record_id
            ),
            approval_ids=(
                "ar1-" + "f" * 64
                if approval_mode == "unknown"
                else review.record_id,
            ),
        ),
        logical_id="commit-1",
        idempotency_key="commit:1",
        fingerprint_sha256=binding_fingerprint,
    )

    with pytest.raises(ArtifactResumeError, match=message):
        resolve_resume_state(tmp_path, state)
