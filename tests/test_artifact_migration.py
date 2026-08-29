from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import artifact_migration
from artifact_bridge import (
    ArtifactBridge,
    finding_handoff_export_payload,
    finding_handoff_import_payload,
)
from artifact_migration import ArtifactResumeError, resolve_resume_state
from artifact_models import (
    ArtifactValidationError,
    BindingPayload,
    CommandSpec,
    CorrectionWorkUnitPayload,
    FindingSeverity,
    FindingHandoffImportPayload,
    FindingTransitionPayload,
    FingerprintKind,
    PlanPayload,
    ReviewPayload,
    Role,
    SliceSpec,
    TaskPayload,
    TransientRetryPayload,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
    WorkflowCompletionPayload,
)
from artifact_store import ArtifactStore
from artifact_replay import ReplayDiagnosticCode, replay_artifacts
from contracts import PlannedSlice
from workflow_state import (
    AgentFailureKind,
    GateReason,
    InvocationFailureRecord,
    ProtocolBinding,
    ProtocolMode,
    Reviewer,
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
            ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2") if structured else None
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
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
        ),
        logical_id="review-resume",
        idempotency_key=f"review-resume:{review_fingerprint}:{review_verdict}",
        fingerprint_sha256=review_fingerprint,
    )
    return attestation, review


def _finding_handoff_resume_fixture(repository: Path):
    state = replace(
        _state(repository),
        approved_plan_commit="b" * 40,
        work_plan_path="docs/internal/plan.md",
    )
    source = ArtifactBridge(ArtifactStore(repository, "source-plan-run"))
    source.append(
        PlanPayload(
            "docs/internal/plan.md",
            "b" * 40,
            (SliceSpec("1", "implementation", ("src/resume.py",)),),
        ),
        logical_id="approved-plan",
        idempotency_key="approved-plan",
        fingerprint_sha256="b" * 64,
    )
    source.append(
        FindingTransitionPayload(
            "C-01", Role.CLAUDE, Role.CLAUDE, "opened",
            FindingSeverity.BLOCKER, "open", "Carry it.", "plan-review",
            "Carry it.", "It must remain open.", "plan", 1,
        ),
        logical_id="finding-C-01",
        idempotency_key="finding-C-01",
        fingerprint_sha256="b" * 64,
    )
    review = source.append(
        ReviewPayload(
            Role.CLAUDE, "plan-review", "approved", ("C-01",), None,
            "native-claude-review-v2", "native-review-request-" + "c" * 64,
            "d" * 64,
        ),
        logical_id="plan-review",
        idempotency_key="plan-review",
        fingerprint_sha256="b" * 64,
    )
    source_replay = replay_artifacts(source.store.load_chain(), "source-plan-run")
    export = source.append(
        finding_handoff_export_payload(
            source_replay,
            approved_plan_commit="b" * 40,
            approval_review_record_id=review.record_id,
            target_task_path="task.md",
            target_task_bytes=Path(state.task_file).read_bytes(),
        ),
        logical_id="finding-handoff-export-bbbbbbbbbbbb",
        idempotency_key="finding-handoff-export:" + "b" * 40,
        fingerprint_sha256="b" * 64,
    )
    state = replace(
        state,
        finding_handoff_source_run_id="source-plan-run",
        finding_handoff_export_record_id=export.record_id,
        work_units=tuple(
            replace(unit, open_findings=("C-01",))
            if unit.work_unit_id == state.current_work_unit_id else unit
            for unit in state.work_units
        ),
    )
    source_replay = replay_artifacts(source.store.load_chain(), "source-plan-run")
    local = ArtifactBridge(ArtifactStore(repository, state.run_id))
    local.append(
        TaskPayload("feature/resume", ("src/resume.py",), "a" * 64),
        logical_id="task-contract",
        idempotency_key="task-contract",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    imported = local.append(
        finding_handoff_import_payload(
            source_replay,
            export,
            target_run_id=state.run_id,
            target_task_bytes=Path(state.task_file).read_bytes(),
        ),
        logical_id="finding-handoff-import",
        idempotency_key=f"finding-handoff-import:{export.record_id}",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    local.append(
        WorkUnitPayload(
            "1", 1, ("src/resume.py",), ("C-01",), imported.record_id
        ),
        logical_id="work-unit-2",
        idempotency_key="work-unit:2:round:1",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    return state, source, local, export, imported


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("incomplete-mirror", "mirror is incomplete"),
        ("unbound-import", "unbound finding import"),
        ("missing-source", "source is no longer valid"),
        ("missing-export", "source is no longer valid"),
        ("wrong-record-type", "source is no longer valid"),
        ("wrong-plan-commit", "source is no longer valid"),
        ("divergent-import", "differs from its revalidated source"),
        ("work-unit-binding", "finding import binding differs"),
        ("status-mirror", "imported finding status differs"),
    ),
)
def test_finding_handoff_resume_rejects_tampered_source_import_or_mirror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    state, source, local, export, imported = _finding_handoff_resume_fixture(tmp_path)
    if mutation == "incomplete-mirror":
        object.__setattr__(state, "finding_handoff_export_record_id", None)
    elif mutation == "unbound-import":
        state = replace(
            state,
            finding_handoff_source_run_id=None,
            finding_handoff_export_record_id=None,
        )
    elif mutation == "missing-source":
        state = replace(state, finding_handoff_source_run_id="missing-source-run")
    elif mutation == "missing-export":
        state = replace(state, finding_handoff_export_record_id="ar1-" + "f" * 64)
    elif mutation == "wrong-record-type":
        state = replace(
            state,
            finding_handoff_export_record_id=source.store.load_chain()[0].record_id,
        )
    elif mutation == "wrong-plan-commit":
        state = replace(state, approved_plan_commit="c" * 40)
    elif mutation == "divergent-import":
        original = finding_handoff_import_payload

        def divergent_payload(*args, **kwargs):
            return replace(
                original(*args, **kwargs), target_task_sha256="e" * 64
            )

        monkeypatch.setattr(
            "artifact_bridge.finding_handoff_import_payload", divergent_payload
        )
    elif mutation == "work-unit-binding":
        local.append(
            WorkUnitPayload("1", 1, ("src/resume.py",), (), None),
            logical_id="work-unit-2",
            idempotency_key="work-unit:2:round:1:tampered",
            fingerprint_sha256="a" * 64,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
    elif mutation == "status-mirror":
        state = replace(
            state,
            runtime_history={
                "current": {
                    "findings": [{"finding_id": "C-01", "status": "closed"}]
                },
                "archive": [],
            },
        )

    with pytest.raises(ArtifactResumeError, match=message):
        resolve_resume_state(tmp_path, state)


def test_finding_handoff_resume_rejects_duplicate_import_before_mirror_use(
    tmp_path: Path,
) -> None:
    state, _source, local, _export, imported = _finding_handoff_resume_fixture(tmp_path)
    payload = imported.payload
    assert isinstance(payload, FindingHandoffImportPayload)
    local.append(
        payload,
        logical_id="finding-handoff-import-duplicate",
        idempotency_key="finding-handoff-import:duplicate",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )

    with pytest.raises(ArtifactResumeError) as error:
        resolve_resume_state(tmp_path, state)

    assert error.value.code is ReplayDiagnosticCode.RECORD_DUPLICATE


def test_finding_import_denial_round_converges_from_record_ahead_state(
    tmp_path: Path,
) -> None:
    state, _source, local, _export, imported = _finding_handoff_resume_fixture(
        tmp_path
    )
    state = state.with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    attestation = local.append(
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
        logical_id="validation-slice-round-1",
        idempotency_key="validation-slice-round-1",
        fingerprint_sha256="d" * 64,
    )
    review = local.append(
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id=str(state.current_work_unit_id),
            verdict="denied",
            finding_ids=("C-01", "C-02"),
            evidence=None,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
        ),
        logical_id=f"review-claude-{state.current_work_unit_id}-1",
        idempotency_key="slice-review-round-1",
        fingerprint_sha256="d" * 64,
    )
    local.append(
        FindingTransitionPayload(
            "C-01", Role.CLAUDE, Role.CLAUDE, "status_changed",
            FindingSeverity.BLOCKER, "closed", "Imported finding verified.",
            str(state.current_work_unit_id),
        ),
        logical_id="finding-C-01",
        idempotency_key="finding-C-01-closed",
        fingerprint_sha256="d" * 64,
    )
    local.append(
        FindingTransitionPayload(
            "C-02", Role.CLAUDE, Role.CLAUDE, "opened",
            FindingSeverity.BLOCKER, "open", "Correction required.",
            str(state.current_work_unit_id), "New Slice blocker.",
            "The correction remains resumable.", "1", 1,
        ),
        logical_id="finding-C-02",
        idempotency_key="finding-C-02-opened",
        fingerprint_sha256="d" * 64,
    )
    next_round = local.append(
        WorkUnitPayload(
            "1", 2, ("src/resume.py",), ("C-02",), imported.record_id
        ),
        logical_id="work-unit-2",
        idempotency_key="work-unit:2:round:2",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    state = replace(
        state,
        runtime_history={
            "current": {
                "findings": [{"finding_id": "C-01", "status": "open"}],
                "attestations": [
                    {
                        "attestation_id": attestation.logical_id,
                        "diff_fingerprint": attestation.fingerprint.sha256,
                    }
                ],
            },
            "archive": [],
        },
    )

    resolution = resolve_resume_state(tmp_path, state)

    assert resolution.record_head_id == next_round.record_id
    assert review.record_id in {
        record.record_id for record in resolution.replay_result.records
    }


def test_later_work_unit_can_carry_open_finding_without_import_snapshot(
    tmp_path: Path,
) -> None:
    task = tmp_path / "task.md"
    task.write_text("task", encoding="utf-8")
    state = (
        init_workflow_state(
            run_id="carried-finding-run",
            task_file=str(task),
            branch="feature/carried-finding",
            branch_base="b" * 40,
            slice_count=2,
            task_digest="a" * 64,
            task_scope_patterns=("src/one.py", "src/two.py"),
            target_branch="feature/carried-finding",
            protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
        )
        .complete_current_work_unit()
        .start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,
        )
        .bind_current_slice_git_boundary(
            start_commit="b" * 40,
            scope_paths=("src/one.py",),
            start_fingerprint="c" * 64,
        )
        .complete_current_slice(commit_ref="d" * 40)
        .start_work_unit(
            slice_id=2,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,
            slice_start_commit="d" * 40,
        )
        .bind_current_slice_git_boundary(
            start_commit="d" * 40,
            scope_paths=("src/two.py",),
            start_fingerprint="f" * 64,
        )
    )
    carried_unit = replace(state.current_work_unit, open_findings=("C-04",))
    state = replace(
        state,
        work_units=(*state.work_units[:-1], carried_unit),
    )
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    bridge.append(
        TaskPayload(
            "feature/carried-finding",
            ("src/one.py", "src/two.py"),
            "a" * 64,
        ),
        logical_id="task-contract",
        idempotency_key="task-contract",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        WorkUnitPayload("1", 1, ("src/one.py",)),
        logical_id="work-unit-2",
        idempotency_key="work-unit:2:round:1",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    attestation, review = _authorization_records(
        tmp_path,
        state,
        attestation_fingerprint="d" * 64,
        review_fingerprint="d" * 64,
    )
    bridge.append(
        FindingTransitionPayload(
            "C-04", Role.CLAUDE, Role.CLAUDE, "opened",
            FindingSeverity.OBSERVATION, "open", "Carry to the next Slice.",
            "2", "Cross-Slice observation.", "It remains in the ledger.", "1", 1,
        ),
        logical_id="finding-C-04",
        idempotency_key="finding-C-04-opened",
        fingerprint_sha256="d" * 64,
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
    latest = bridge.append(
        WorkUnitPayload("2", 1, ("src/two.py",)),
        logical_id="work-unit-3",
        idempotency_key="work-unit:3:round:1",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    state = replace(
        state,
        runtime_history={
            "current": {
                "findings": [{"finding_id": "C-04", "status": "open"}],
                "attestations": [
                    {
                        "attestation_id": attestation.logical_id,
                        "diff_fingerprint": attestation.fingerprint.sha256,
                    }
                ],
            },
            "archive": [],
        },
    )

    resolution = resolve_resume_state(tmp_path, state)

    assert resolution.record_head_id == latest.record_id
    assert state.current_work_unit.open_findings == ("C-04",)
    assert latest.payload.finding_import_record_id is None
    assert latest.payload.open_finding_ids == ()


def test_import_bound_latest_work_unit_still_rejects_mirror_open_set_drift(
    tmp_path: Path,
) -> None:
    state, _source, _local, _export, _imported = _finding_handoff_resume_fixture(
        tmp_path
    )
    drifted_unit = replace(state.current_work_unit, open_findings=("C-02",))
    state = replace(
        state,
        work_units=(*state.work_units[:-1], drifted_unit),
    )

    with pytest.raises(
        ArtifactResumeError,
        match="latest work-unit finding state differs",
    ):
        resolve_resume_state(tmp_path, state)


def test_legacy_state_without_records_is_rejected_without_store_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, structured=False)
    monkeypatch.setattr(
        artifact_migration,
        "ArtifactStore",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy resume must not open the artifact store")
        ),
    )

    with pytest.raises(ArtifactResumeError, match="UNSUPPORTED-PROTOCOL"):
        resolve_resume_state(tmp_path, state)
    assert not (tmp_path / ".orchestrator" / "artifacts").exists()


def test_structured_state_rehydrates_from_matching_complete_chain(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)

    resolved = resolve_resume_state(tmp_path, state)

    assert resolved.state == state
    assert resolved.mode is ProtocolMode.STRUCTURED_V2
    assert resolved.record_head_id is not None
    assert resolved.replay_result is not None
    assert resolved.replay_result.head_record_id == resolved.record_head_id


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

    with pytest.raises(ArtifactResumeError) as error:
        resolve_resume_state(tmp_path, legacy_state)

    assert error.value.code is ReplayDiagnosticCode.RECORD_DUPLICATE


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

    with pytest.raises(ArtifactResumeError, match="gate decisions differ from state-v3") as error:
        resolve_resume_state(tmp_path, state)

    assert error.value.code is ReplayDiagnosticCode.MIRROR_AHEAD


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


def test_runtime_finding_closure_overrides_correction_work_unit_attribution(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path).record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=("C-05",),
        return_step=WorkflowStep.CODEX_CORRECTION,
    )
    state = replace(
        state,
        runtime_history={
            "findings": [
                {
                    "finding_id": "C-05",
                    "status": "CLOSED",
                }
            ]
        },
    )

    assert state.current_work_unit.open_findings == ("C-05",)
    assert artifact_migration._finding_statuses(state) == {"C-05": "closed"}


def test_structured_resume_halts_when_mirror_transient_retry_has_no_chain_record(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    failure = InvocationFailureRecord(
        invocation_id="inv-network-mirror-only",
        idempotency_key="resume-run:2:codex_implementation:claude",
        role="claude",
        failure_kind=AgentFailureKind.NETWORK,
        provider_text="HTTP 529 overloaded",
        received_at="2026-08-18T12:00:00+00:00",
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_id=1,
        work_unit_id=2,
        diagnostic_exit_code=3,
        resume_at_utc="2026-08-18T12:00:05+00:00",
        auto_resume_count=1,
        automatic_resume=True,
        diff_fingerprint="d" * 64,
    )
    state = state.record_invocation_failure(
        failure,
        wait_automatically=True,
    ).resume_after_invocation_halt(updated_at="2026-08-18T12:00:05+00:00")

    with pytest.raises(
        ArtifactResumeError,
        match="transient retries differ from state-v3",
    ):
        resolve_resume_state(tmp_path, state)


def _correction_round_state(repository: Path) -> WorkflowState:
    state = _state(repository)
    return (
        state.complete_current_slice(commit_ref="d" * 40)
        .start_final_review_work_unit()
        .complete_current_work_unit()
        .start_correction_work_unit(
            start_commit="d" * 40,
            scope_paths=("src/resume.py",),
            start_fingerprint="e" * 64,
            finding_ids=("C-01",),
        )
    )


def _append_correction_round(
    bridge: ArtifactBridge,
    state: WorkflowState,
    *,
    round_number: int,
    finding_ids: tuple[str, ...],
) -> None:
    bridge.append(
        CorrectionWorkUnitPayload(
            str(state.current_slice_id),
            round_number,
            state.current_slice.scope_paths,
            finding_ids,
        ),
        logical_id=f"work-unit-{state.current_work_unit_id}",
        idempotency_key=(
            f"correction-work-unit:{state.current_work_unit_id}:round:{round_number}"
        ),
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )


def _append_open_finding(
    bridge: ArtifactBridge,
    *,
    finding_id: str,
) -> None:
    bridge.append(
        FindingTransitionPayload(
            finding_id=finding_id,
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="correction round finding",
        ),
        logical_id=f"finding-{finding_id}",
        idempotency_key=f"finding-{finding_id}-opened",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )


def _pending_review_chain(
    repository: Path,
) -> tuple[
    WorkflowState,
    tuple[object, ...],
    object,
    object,
    object,
    object,
    object,
]:
    state = _correction_round_state(repository).with_current_step(
        WorkflowStep.CLAUDE_SLICE_REVIEW
    )
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
        logical_id="validation-pending-review",
        idempotency_key="validation-pending-review",
        fingerprint_sha256="d" * 64,
    )
    prior = bridge.append(
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="prior correction finding",
        ),
        logical_id="finding-C-01",
        idempotency_key="finding-C-01-opened",
        fingerprint_sha256="d" * 64,
    )
    review = bridge.append(
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id=str(state.current_work_unit_id),
            verdict="denied",
            finding_ids=("C-01", "C-07"),
            evidence=None,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
        ),
        logical_id=f"review-claude-{state.current_work_unit_id}-1",
        idempotency_key="pending-review",
        fingerprint_sha256="d" * 64,
    )
    current = bridge.append(
        FindingTransitionPayload(
            finding_id="C-07",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="new correction finding",
        ),
        logical_id="finding-C-07",
        idempotency_key="finding-C-07-opened",
        fingerprint_sha256="d" * 64,
    )
    correction = bridge.append(
        CorrectionWorkUnitPayload(
            slice_id=str(state.current_slice_id),
            round_number=2,
            paths=state.current_slice.scope_paths,
            finding_ids=("C-07",),
        ),
        logical_id=f"work-unit-{state.current_work_unit_id}",
        idempotency_key="correction-round-2",
        fingerprint_sha256=state.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    return state, bridge.store.load_chain(), attestation, prior, review, current, correction


@pytest.mark.parametrize(
    "failure_mode",
    (
        "wrong-round",
        "approved-verdict",
        "finding-outside-review",
        "correction-before-review",
        "duplicate-review",
    ),
)
def test_pending_correction_resume_exception_rejects_near_misses(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    state, chain, _attestation, _prior, review, _current, correction = (
        _pending_review_chain(repository)
    )
    if failure_mode == "wrong-round":
        correction = replace(
            correction,
            payload=replace(correction.payload, round_number=3),
        )
    elif failure_mode == "approved-verdict":
        review = replace(
            review,
            payload=replace(
                review.payload,
                verdict="approved",
                evidence="approval evidence",
            ),
        )
    elif failure_mode == "finding-outside-review":
        correction = replace(
            correction,
            payload=replace(correction.payload, finding_ids=("C-08",)),
        )
    chain = tuple(
        correction if item.record_id == correction.record_id else
        review if item.record_id == review.record_id else item
        for item in chain
    )
    if failure_mode == "correction-before-review":
        items = list(chain)
        review_index = items.index(review)
        correction_index = items.index(correction)
        items[review_index], items[correction_index] = (
            items[correction_index],
            items[review_index],
        )
        chain = tuple(items)
    elif failure_mode == "duplicate-review":
        chain = (*chain, review)

    assert not artifact_migration._recoverable_pending_correction_record(
        state, chain, correction
    ), failure_mode


def _pending_slice_denial_chain(
    repository: Path,
) -> tuple[WorkflowState, tuple[object, ...], object, object, object]:
    state = _state(repository).with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    bridge.append(
        WorkUnitPayload(
            str(state.current_slice_id),
            1,
            state.current_slice.scope_paths,
        ),
        logical_id=f"work-unit-{state.current_work_unit_id}",
        idempotency_key="slice-round-1",
        fingerprint_sha256=state.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
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
        logical_id="validation-slice-round-1",
        idempotency_key="validation-slice-round-1",
        fingerprint_sha256="d" * 64,
    )
    review = bridge.append(
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id=str(state.current_work_unit_id),
            verdict="denied",
            finding_ids=("C-02",),
            evidence=None,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
        ),
        logical_id=f"review-claude-{state.current_work_unit_id}-1",
        idempotency_key="slice-review-round-1",
        fingerprint_sha256="d" * 64,
    )
    next_round = bridge.append(
        WorkUnitPayload(
            str(state.current_slice_id),
            2,
            state.current_slice.scope_paths,
            ("C-02",),
        ),
        logical_id=f"work-unit-{state.current_work_unit_id}",
        idempotency_key="slice-round-2",
        fingerprint_sha256=state.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    return state, bridge.store.load_chain(), attestation, review, next_round


def test_pending_slice_denial_round_is_admitted_for_record_ahead_resume(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    state, chain, _attestation, _review, next_round = (
        _pending_slice_denial_chain(repository)
    )

    assert artifact_migration._recoverable_pending_slice_denial_record(
        state, chain, next_round
    )


@pytest.mark.parametrize(
    "failure_mode",
    (
        "wrong-round",
        "approved-verdict",
        "finding-outside-review",
        "record-before-review",
        "missing-attestation",
    ),
)
def test_pending_slice_denial_round_rejects_near_misses(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    state, chain, attestation, review, next_round = _pending_slice_denial_chain(
        repository
    )
    if failure_mode == "wrong-round":
        next_round = replace(
            next_round,
            payload=replace(next_round.payload, round_number=3),
        )
    elif failure_mode == "approved-verdict":
        review = replace(
            review,
            payload=replace(
                review.payload,
                verdict="approved",
                evidence="review evidence | residual risk | break condition",
            ),
        )
    elif failure_mode == "finding-outside-review":
        next_round = replace(
            next_round,
            payload=replace(next_round.payload, open_finding_ids=("C-03",)),
        )
    chain = tuple(
        review if item.record_id == review.record_id else
        next_round if item.record_id == next_round.record_id else item
        for item in chain
    )
    if failure_mode == "record-before-review":
        items = list(chain)
        review_index = items.index(review)
        record_index = items.index(next_round)
        items[review_index], items[record_index] = items[record_index], items[review_index]
        chain = tuple(items)
    elif failure_mode == "missing-attestation":
        chain = tuple(item for item in chain if item.record_id != attestation.record_id)

    assert not artifact_migration._recoverable_pending_slice_denial_record(
        state, chain, next_round
    ), failure_mode


def test_wrong_finding_prefix_is_rejected_before_correction_migration() -> None:
    with pytest.raises(
        ArtifactValidationError,
        match=r"canonical C-\* finding ID",
    ):
        CorrectionWorkUnitPayload(
            slice_id="01",
            round_number=2,
            paths=("src/core.py",),
            finding_ids=("F-07",),
        )


@pytest.mark.parametrize(
    "failure_mode",
    (
        "approved-verdict",
        "missing-attestation",
        "finding-outside-review",
        "transition-before-review",
        "duplicate-review",
    ),
)
def test_pending_review_finding_resume_exception_rejects_near_misses(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    state, chain, attestation, prior, review, current, _correction = (
        _pending_review_chain(repository)
    )
    if failure_mode == "approved-verdict":
        review = replace(
            review,
            payload=replace(
                review.payload,
                verdict="approved",
                evidence="approval evidence",
            ),
        )
    elif failure_mode == "finding-outside-review":
        current = replace(
            current,
            payload=replace(current.payload, finding_id="C-08"),
        )
    chain = tuple(
        review if item.record_id == review.record_id else
        current if item.record_id == current.record_id else item
        for item in chain
    )
    if failure_mode == "missing-attestation":
        chain = tuple(item for item in chain if item.record_id != attestation.record_id)
    elif failure_mode == "transition-before-review":
        items = list(chain)
        review_index = items.index(review)
        current_index = items.index(current)
        items[review_index], items[current_index] = items[current_index], items[review_index]
        chain = tuple(items)
    elif failure_mode == "duplicate-review":
        chain = (*chain, review)
    latest = {
        "C-01": prior,
        current.payload.finding_id: current,
    }

    assert not artifact_migration._recoverable_pending_review_finding_gap(
        state,
        chain,
        {"C-01": "open"},
        latest,
    ), failure_mode


def test_pending_approved_review_closure_is_admitted_for_exact_local_replay(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    state, chain, _attestation, prior, review, transition, correction = (
        _pending_review_chain(repository)
    )
    review = replace(
        review,
        payload=replace(
            review.payload,
            verdict="approved",
            finding_ids=("C-01",),
            evidence="review identity | residual risk | break condition",
        ),
    )
    transition = replace(
        transition,
        payload=replace(
            transition.payload,
            finding_id="C-01",
            action="status_changed",
            finding_status="closed",
            rationale="the correction is verified",
        ),
    )
    chain = tuple(
        review
        if item.record_id == review.record_id
        else transition
        if item.record_id == transition.record_id
        else item
        for item in chain
        if item.record_id != correction.record_id
    )

    assert artifact_migration._recoverable_pending_review_finding_gap(
        state,
        chain,
        {"C-01": "open"},
        {"C-01": transition},
    )


def test_pending_slice_review_finding_is_admitted_after_gate_advanced_rounds(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    state, chain, _attestation, prior, _review, current, _correction = (
        _pending_review_chain(repository)
    )
    unit = replace(
        state.current_work_unit,
        kind=WorkUnitKind.SLICE,
        round_number=4,
        codex_return_count=2,
    )
    state = replace(
        state,
        work_units=(*state.work_units[:-1], unit),
    )

    assert artifact_migration._recoverable_pending_review_finding_gap(
        state,
        chain,
        {"C-01": "open"},
        {"C-01": prior, "C-07": current},
    )


def _append_completed_slice_binding(
    repository: Path,
    state: WorkflowState,
) -> WorkflowState:
    attestation, review = _authorization_records(
        repository,
        state,
        attestation_fingerprint="d" * 64,
        review_fingerprint="d" * 64,
    )
    ArtifactBridge(ArtifactStore(repository, state.run_id)).append(
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
    return replace(
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


def test_structured_resume_compares_correction_findings_with_their_own_round(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    first_round = _append_completed_slice_binding(
        repository, _correction_round_state(repository)
    )
    second_round = first_round.record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=("C-07",),
        return_step=WorkflowStep.CODEX_FINAL_CORRECTION,
    )
    bridge = ArtifactBridge(ArtifactStore(repository, second_round.run_id))
    bridge.append(
        TaskPayload("feature/resume", ("src/resume.py",), "a" * 64),
        logical_id="task-contract",
        idempotency_key="task-contract",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    _append_correction_round(
        bridge, first_round, round_number=1, finding_ids=("C-01",)
    )
    _append_correction_round(
        bridge, second_round, round_number=2, finding_ids=("C-07",)
    )
    _append_open_finding(bridge, finding_id="C-07")

    resolution = resolve_resume_state(repository, second_round)

    assert resolution.record_head_id is not None


@pytest.mark.parametrize(
    "record_mode",
    ("missing-latest", "wrong-latest-findings", "future-round"),
)
def test_structured_resume_rejects_invalid_latest_correction_round(
    tmp_path: Path,
    record_mode: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    first_round = _append_completed_slice_binding(
        repository, _correction_round_state(repository)
    )
    second_round = first_round.record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=("C-07",),
        return_step=WorkflowStep.CODEX_FINAL_CORRECTION,
    )
    bridge = ArtifactBridge(ArtifactStore(repository, second_round.run_id))
    bridge.append(
        TaskPayload("feature/resume", ("src/resume.py",), "a" * 64),
        logical_id="task-contract",
        idempotency_key="task-contract",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    _append_correction_round(
        bridge, first_round, round_number=1, finding_ids=("C-01",)
    )
    if record_mode == "wrong-latest-findings":
        _append_correction_round(
            bridge, second_round, round_number=2, finding_ids=("C-99",)
        )
    elif record_mode == "future-round":
        _append_correction_round(
            bridge, second_round, round_number=3, finding_ids=("C-07",)
        )
    _append_open_finding(bridge, finding_id="C-07")

    with pytest.raises(ArtifactResumeError, match="round|finding attribution"):
        resolve_resume_state(repository, second_round)


def test_structured_resume_accepts_matching_transient_retry_record(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    failure = InvocationFailureRecord(
        invocation_id="inv-network-mirrored",
        idempotency_key="resume-run:2:codex_implementation:claude",
        role="claude",
        failure_kind=AgentFailureKind.NETWORK,
        provider_text="HTTP 529 overloaded",
        received_at="2026-08-18T12:00:00+00:00",
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_id=1,
        work_unit_id=2,
        diagnostic_exit_code=3,
        resume_at_utc="2026-08-18T12:00:05+00:00",
        auto_resume_count=1,
        automatic_resume=True,
        diff_fingerprint="d" * 64,
    )
    state = state.record_invocation_failure(
        failure,
        wait_automatically=True,
    ).resume_after_invocation_halt(updated_at="2026-08-18T12:00:05+00:00")
    ArtifactBridge(ArtifactStore(tmp_path, state.run_id)).append(
        TransientRetryPayload(
            role=Role.CLAUDE,
            repository_fingerprint="d" * 64,
            retry_at="2026-08-18T12:00:05+00:00",
            attempt=1,
        ),
        logical_id="transient-retry-inv-network-mirrored",
        idempotency_key="transient-retry:inv-network-mirrored",
        fingerprint_sha256="d" * 64,
        fingerprint_kind=FingerprintKind.IMPLEMENTATION,
    )

    resolved = resolve_resume_state(tmp_path, state)

    assert resolved.state == state


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


@pytest.mark.parametrize("binding_mode", ("unknown", "mismatched"))
def test_structured_resume_halts_when_completion_final_binding_id_is_unknown_or_mismatched(
    tmp_path: Path,
    binding_mode: str,
) -> None:
    state = (
        _state(tmp_path)
        .complete_current_slice(commit_ref="d" * 40)
        .start_final_review_work_unit()
        .complete_current_work_unit()
    )
    _records(tmp_path, state)
    binding_fingerprint = "e" * 64 if binding_mode == "mismatched" else "d" * 64
    attestation, review = _authorization_records(
        tmp_path,
        state,
        attestation_fingerprint=binding_fingerprint,
        review_fingerprint=binding_fingerprint,
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
        WorkUnitPayload("1", 1, ("src/resume.py",)),
        logical_id="work-unit-3",
        idempotency_key="work-unit:3:round:1",
        fingerprint_sha256="d" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    binding = bridge.append(
        BindingPayload(
            binding_kind="commit",
            target="d" * 40,
            attestation_id=attestation.record_id,
            approval_ids=(review.record_id,),
        ),
        logical_id="commit-1",
        idempotency_key="commit:1",
        fingerprint_sha256=binding_fingerprint,
    )
    bridge.append(
        WorkflowCompletionPayload(
            outcome="completed",
            final_binding_id=(
                "ar1-" + "f" * 64 if binding_mode == "unknown" else binding.record_id
            ),
        ),
        logical_id="workflow-completion",
        idempotency_key="workflow-completion:completed",
        fingerprint_sha256="d" * 64,
    )

    with pytest.raises(ArtifactResumeError) as error:
        resolve_resume_state(tmp_path, state)

    assert error.value.code in {
        ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
        ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
    }


@pytest.mark.parametrize(
    ("attestation_mode", "approval_mode", "review_verdict", "expected_code"),
    (
        ("unknown", "valid", "approved", ReplayDiagnosticCode.RECORD_REFERENCE_MISSING),
        ("mismatched", "valid", "approved", ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH),
        ("valid", "unknown", "approved", ReplayDiagnosticCode.RECORD_REFERENCE_MISSING),
        ("valid", "mismatched", "approved", ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH),
        ("valid", "valid", "denied", ReplayDiagnosticCode.RECORD_REFERENCE_MISSING),
    ),
)
def test_structured_resume_halts_when_commit_binding_references_unknown_or_mismatched_attestation_or_approval(
    tmp_path: Path,
    attestation_mode: str,
    approval_mode: str,
    review_verdict: str,
    expected_code: ReplayDiagnosticCode,
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

    with pytest.raises(ArtifactResumeError) as error:
        resolve_resume_state(tmp_path, state)

    assert error.value.code is expected_code
