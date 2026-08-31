from __future__ import annotations

from dataclasses import replace
from datetime import datetime
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
    GateTransitionPayload,
    InvocationFailurePayload,
    PlanPayload,
    RecordType,
    ReviewEvidencePayload,
    ReviewPayload,
    Role,
    RunIdentityPayload,
    RunProfilePayload,
    SliceBoundaryPayload,
    SliceSpec,
    TaskPayload,
    TransientRetryPayload,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
    WorkflowCompletionPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    provider_text_evidence,
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
from content_authority_support import (
    append_provider_decision_authority,
    append_validation_authority,
    validation_history_mirror,
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


def _append_invocation_failure(
    repository: Path, state: WorkflowState, failure: InvocationFailureRecord
) -> None:
    marker, digest, byte_count = provider_text_evidence(failure.provider_text)
    decision = datetime.fromisoformat(failure.received_at.replace("Z", "+00:00"))
    retry_delay = 0
    if failure.failure_kind is AgentFailureKind.QUOTA and failure.reset_at_utc:
        retry_delay = failure.safety_margin_seconds
    elif failure.failure_kind is AgentFailureKind.NETWORK and failure.automatic_resume:
        assert failure.resume_at_utc is not None
        retry_delay = int(
            (
                datetime.fromisoformat(failure.resume_at_utc.replace("Z", "+00:00"))
                - decision
            ).total_seconds()
        )
    payload = InvocationFailurePayload(
        invocation_id=failure.invocation_id,
        idempotency_key=failure.idempotency_key,
        role=Role(failure.role),
        failure_kind=failure.failure_kind.value,
        failure_class="transient",
        diagnostic_code="AGENT-INVOCATION",
        provider_text=marker,
        provider_text_sha256=digest,
        provider_text_bytes=byte_count,
        received_at=failure.received_at,
        decision_at_utc=failure.received_at,
        step=failure.step.value,
        slice_id=str(failure.slice_id),
        work_unit_id=str(failure.work_unit_id),
        diagnostic_exit_code=failure.diagnostic_exit_code,
        parse_path=failure.parse_path,
        source_timezone=failure.source_timezone,
        reset_at_utc=failure.reset_at_utc,
        resume_at_utc=failure.resume_at_utc,
        safety_margin_seconds=failure.safety_margin_seconds,
        retry_delay_seconds=retry_delay,
        auto_resume_count=failure.auto_resume_count,
        automatic_resume=failure.automatic_resume,
        diff_fingerprint=failure.diff_fingerprint,
    )
    ArtifactBridge(ArtifactStore(repository, state.run_id)).append(
        payload,
        logical_id=f"invocation-failure-{failure.invocation_id}",
        idempotency_key=f"invocation-failure:{failure.invocation_id}",
        fingerprint_sha256=failure.diff_fingerprint or state.task_digest,
        fingerprint_kind=(
            FingerprintKind.IMPLEMENTATION
            if failure.diff_fingerprint is not None
            else FingerprintKind.CONTRACT
        ),
    )


def _records(
    repository: Path,
    state,
    *,
    include_gate_records: bool = True,
) -> None:
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
    _status_records(bridge, state, include_gate_records=include_gate_records)


def test_resume_projects_authoritative_side_effect_result_ahead_of_mirror(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    operation = ("result.json", "d" * 64)
    intent, _ = bridge.record_side_effect_intent(
        effect_class="file_write",
        work_unit_id=state.current_work_unit_id,
        operation=operation,
        fingerprint_sha256="d" * 64,
    )
    result = bridge.record_side_effect_result(
        effect_class="file_write",
        work_unit_id=state.current_work_unit_id,
        operation=operation,
        result="d" * 64,
        fingerprint_sha256="d" * 64,
    )

    resolved = resolve_resume_state(tmp_path, state).state

    assert resolved.current_work_unit.completed_side_effects == (
        result.payload.effect_key,
    )
    assert intent.payload.effect_key == result.payload.effect_key


def test_resume_rejects_side_effect_mirror_ahead_of_ledger(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    state = state.mark_side_effect_completed("unrecorded-effect")

    with pytest.raises(ArtifactResumeError, match="completed side effects differ"):
        resolve_resume_state(tmp_path, state)


def _run_records(
    repository: Path,
    state: WorkflowState,
    *,
    identity: RunIdentityPayload | None = None,
    profile: RunProfilePayload | None = None,
) -> None:
    binding = state.protocol_binding
    assert binding is not None
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    bridge.append(
        identity
        or RunIdentityPayload(
            state.task_file,
            state.branch,
            state.branch_base,
            state.execution_mode,
            state.audit_report_path,
        ),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        profile
        or RunProfilePayload(
            binding.codex_profile.model,
            binding.codex_profile.effort,
            binding.claude_profile.model,
            binding.claude_profile.effort,
        ),
        logical_id="run-profile",
        idempotency_key="run-profile",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )


def _status_records(
    bridge: ArtifactBridge,
    state: WorkflowState,
    *,
    include_slice_boundaries: bool = True,
    include_gate_records: bool = True,
) -> None:
    chain = bridge.store.load_chain()
    denied_units = {
        record.payload.work_unit_id
        for record in chain
        if isinstance(record.payload, ReviewPayload)
        and record.payload.verdict == "denied"
    }
    latest_attestation = next(
        (
            record
            for record in reversed(chain)
            if isinstance(record.payload, ValidationAttestationPayload)
        ),
        None,
    )
    for unit in state.work_units:
        work_unit_id = str(unit.work_unit_id)
        if unit.reviewer is None or work_unit_id in denied_units:
            continue
        if latest_attestation is None:
            raise AssertionError("fixture reviewer projection requires validation")
        append_provider_decision_authority(
            bridge,
            ReviewPayload(
                reviewer=Role.CLAUDE,
                work_unit_id=work_unit_id,
                verdict="denied",
                finding_ids=unit.open_findings,
                evidence=None,
                transport_schema="native-claude-review-v2",
                request_id=(
                    "native-review-request-" + f"{unit.work_unit_id:064x}"
                ),
                response_sha256=f"{unit.work_unit_id + 100:064x}",
                review_evidence=ReviewEvidencePayload(
                    "fixture reviewer projection",
                    "fixture residual risk",
                    "fixture break condition",
                ),
            ),
            logical_id=f"review-fixture-{work_unit_id}",
            idempotency_key=f"review-fixture:{work_unit_id}",
            fingerprint_sha256=latest_attestation.fingerprint.sha256,
            operation="claude_slice_review",
        )

    units_by_slice = {str(unit.slice_id) for unit in state.work_units}
    revision = max(
        (
            record.revision for record in bridge.store.load_chain()
            if record.record_type is RecordType.WORKFLOW_TRANSITION
        ),
        default=0,
    )
    for item in state.slices:
        slice_id = str(item.slice_id)
        if slice_id in units_by_slice:
            continue
        revision += 1
        bridge.append(
            WorkflowTransitionPayload(
                slice_id, item.status.value, None, None, None
            ),
            logical_id="workflow-transition",
            idempotency_key=f"workflow-transition:{revision}",
            fingerprint_sha256="a" * 64,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
    operation = ("structured-v2-side-effect-ledger",)
    bridge.record_side_effect_intent(
        effect_class="ledger",
        work_unit_id="run",
        operation=operation,
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.record_side_effect_result(
        effect_class="ledger",
        work_unit_id="run",
        operation=operation,
        result="initialized",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    ordered_units = (
        *(unit for unit in state.work_units if unit.work_unit_id != state.current_work_unit_id),
        state.current_work_unit,
    )
    for unit in ordered_units:
        revision += 1
        slice_status = next(
            item.status.value for item in state.slices if item.slice_id == unit.slice_id
        )
        bridge.append(
            WorkflowTransitionPayload(
                str(unit.slice_id),
                slice_status,
                str(unit.work_unit_id),
                unit.current_step.value,
                unit.status.value,
            ),
            logical_id="workflow-transition",
            idempotency_key=f"workflow-transition:{revision}",
            fingerprint_sha256="a" * 64,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
        logical_id = f"workflow-policy-{unit.work_unit_id}"
        policy_revision = 1 + max(
            (
                record.revision for record in bridge.store.load_chain()
                if record.record_type is RecordType.WORKFLOW_POLICY
                and record.logical_id == logical_id
            ),
            default=0,
        )
        bridge.append(
            WorkflowPolicyPayload(
                str(unit.work_unit_id),
                unit.codex_return_count,
                unit.max_codex_returns,
            ),
            logical_id=logical_id,
            idempotency_key=f"workflow-policy:{unit.work_unit_id}:{policy_revision}",
            fingerprint_sha256="a" * 64,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
        if include_gate_records:
            bridge.append(
                GateTransitionPayload(
                    work_unit_id=str(unit.work_unit_id),
                    gate_status=unit.gate.status.value,
                    reason=unit.gate.reason.value,
                    detail=unit.gate.detail,
                    fingerprint=unit.gate.fingerprint,
                    paths=unit.gate.paths,
                    resume_step=(
                        None
                        if unit.gate.resume_step is None
                        else unit.gate.resume_step.value
                    ),
                    active_test_fingerprint=unit.active_test_fingerprint,
                    active_test_paths=unit.active_test_paths,
                ),
                logical_id=f"gate-transition-{unit.work_unit_id}",
                idempotency_key=f"gate-transition:{unit.work_unit_id}:1",
                fingerprint_sha256="a" * 64,
                fingerprint_kind=FingerprintKind.CONTRACT,
            )
    if not include_slice_boundaries:
        return
    for item in state.slices:
        if item.start_commit is None or item.start_fingerprint is None:
            continue
        bridge.append(
            SliceBoundaryPayload(
                str(item.slice_id),
                item.start_commit,
                item.scope_change_groups,
                item.start_fingerprint,
            ),
            logical_id=f"slice-boundary-{item.slice_id}",
            idempotency_key=f"slice-boundary:{item.slice_id}:1",
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
    attestation = append_validation_authority(
        bridge,
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
            output_digest="0" * 64,
            content_record_id="ar1-" + "0" * 64,
        ),
        logical_id="validation-resume",
        idempotency_key=f"validation-resume:{attestation_fingerprint}",
        fingerprint_sha256=attestation_fingerprint,
    )
    review = append_provider_decision_authority(
        bridge,
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="2",
            verdict=review_verdict,
            finding_ids=(),
            evidence=None,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
            review_evidence=ReviewEvidencePayload(
                "resume authorization reviewed",
                "fixture residual risk",
                "fixture break condition",
            ),
        ),
        logical_id="review-resume",
        idempotency_key=f"review-resume:{review_fingerprint}:{review_verdict}",
        fingerprint_sha256=review_fingerprint,
        operation="claude_slice_review",
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
    append_validation_authority(
        source,
        ValidationAttestationPayload(
            (
                ValidationResult(
                    CommandSpec("pytest", ("python3", "-m", "pytest")),
                    "pass",
                    0,
                    "0" * 64,
                ),
            ),
            Role.ORCHESTRATOR,
            "0" * 64,
            "ar1-" + "0" * 64,
        ),
        logical_id="validation-plan-review",
        idempotency_key="validation-plan-review",
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
    review = append_provider_decision_authority(
        source,
        ReviewPayload(
            Role.CLAUDE, "plan-review", "approved", ("C-01",), None,
            "native-claude-review-v2", "native-review-request-" + "c" * 64,
            "d" * 64,
        ),
        logical_id="plan-review",
        idempotency_key="plan-review",
        fingerprint_sha256="b" * 64,
        operation="claude_plan_review",
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
    _status_records(local, state)
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
    attestation = append_validation_authority(
        local,
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
            output_digest="0" * 64,
            content_record_id="ar1-" + "0" * 64,
        ),
        logical_id="validation-slice-round-1",
        idempotency_key="validation-slice-round-1",
        fingerprint_sha256="d" * 64,
    )
    review = append_provider_decision_authority(
        local,
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
        operation="claude_slice_review",
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
                "attestations": [validation_history_mirror(local, attestation)],
            },
            "archive": [],
        },
    )
    _status_records(local, state)

    resolution = resolve_resume_state(tmp_path, state)

    assert next_round.record_id in {
        record.record_id for record in resolution.replay_result.records
    }
    assert resolution.replay_result.records[-1].record_type is RecordType.WORKFLOW_POLICY
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
                "attestations": [validation_history_mirror(bridge, attestation)],
            },
            "archive": [],
        },
    )
    _status_records(bridge, state)

    resolution = resolve_resume_state(tmp_path, state)

    assert latest.record_id in {
        record.record_id for record in resolution.replay_result.records
    }
    assert resolution.replay_result.records[-1].record_type is RecordType.SLICE_BOUNDARY
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
    assert resolved.replay_result.run_identity is None
    assert resolved.replay_result.run_profile is None


@pytest.mark.parametrize(
    ("drift", "message"),
    (
        ("identity", "run identity differs from state-v3"),
        ("profile", "run profile differs from state-v3"),
    ),
)
def test_structured_resume_rejects_typed_run_binding_mismatch(
    tmp_path: Path, drift: str, message: str
) -> None:
    state = _state(tmp_path)
    if drift == "identity":
        _run_records(
            tmp_path,
            state,
            identity=RunIdentityPayload(
                state.task_file,
                "feature/foreign",
                state.branch_base,
                state.execution_mode,
                state.audit_report_path,
            ),
        )
    else:
        _run_records(
            tmp_path,
            state,
            profile=RunProfilePayload(
                "foreign-codex",
                "medium",
                "foreign-claude",
                "high",
            ),
        )
    _records(tmp_path, state)

    with pytest.raises(ArtifactResumeError, match=message) as caught:
        resolve_resume_state(tmp_path, state)

    assert caught.value.code is ReplayDiagnosticCode.MIRROR_AMBIGUOUS


def test_structured_state_without_records_halts_with_repair_hint(tmp_path: Path) -> None:
    with pytest.raises(ArtifactResumeError, match="restore its record directory"):
        resolve_resume_state(tmp_path, _state(tmp_path))


@pytest.mark.parametrize("prefix", ("neither", "transition_only"))
def test_pre_r2_chain_without_complete_status_prefix_is_rejected(
    tmp_path: Path,
    prefix: str,
) -> None:
    state = _state(tmp_path)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    bridge.append(
        TaskPayload("feature/resume", ("src/resume.py",), "a" * 64),
        logical_id="task-contract",
        idempotency_key="task-contract",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    if prefix == "transition_only":
        bridge.append(
            WorkflowTransitionPayload(
                "1", "in_progress", "2", "codex_implementation", "in_progress"
            ),
            logical_id="workflow-transition",
            idempotency_key="workflow-transition:1",
            fingerprint_sha256="a" * 64,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
    with pytest.raises(ArtifactResumeError, match="workflow .* prefix") as caught:
        resolve_resume_state(tmp_path, state)

    assert caught.value.code is ReplayDiagnosticCode.RECORD_MISSING


def test_pre_r3_chain_without_slice_boundary_prefix_is_rejected(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
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
    _status_records(bridge, state, include_slice_boundaries=False)

    with pytest.raises(ArtifactResumeError, match="slice boundary prefix") as caught:
        resolve_resume_state(tmp_path, state)

    assert caught.value.code is ReplayDiagnosticCode.RECORD_MISSING


def test_pre_r5_chain_without_gate_transition_prefix_is_rejected(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state, include_gate_records=False)

    with pytest.raises(ArtifactResumeError, match="gate transition prefix") as caught:
        resolve_resume_state(tmp_path, state)

    assert caught.value.code is ReplayDiagnosticCode.RECORD_MISSING


@pytest.mark.parametrize("field", ("start_commit", "start_fingerprint", "groups"))
def test_structured_resume_rejects_slice_boundary_mirror_drift(
    tmp_path: Path,
    field: str,
) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    current = state.current_slice
    if field == "start_commit":
        changed_slice = replace(current, start_commit="d" * 40)
    elif field == "start_fingerprint":
        changed_slice = replace(current, start_fingerprint="e" * 64)
    else:
        changed_slice = replace(
            current,
            scope_paths=("src/extra.py", "src/resume.py"),
            scope_change_groups=(("src/extra.py",), ("src/resume.py",)),
        )
    changed = replace(
        state,
        slices=tuple(
            changed_slice if item.slice_id == current.slice_id else item
            for item in state.slices
        ),
    )

    with pytest.raises(ArtifactResumeError, match="slice boundaries differ"):
        resolve_resume_state(tmp_path, changed)


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
        rationale="approve changed resume test",
    )

    with pytest.raises(ArtifactResumeError, match="gate decision bindings differ from state-v3") as error:
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
        auto_resume_count=1,
        automatic_resume=True,
        diff_fingerprint="d" * 64,
    )
    state = state.record_invocation_failure(
        failure,
        wait_automatically=True,
    ).resume_after_invocation_halt(updated_at="2026-08-18T12:05:30+00:00")
    _append_invocation_failure(tmp_path, state, failure)

    with pytest.raises(ArtifactResumeError, match="quota pauses differ from state-v3"):
        resolve_resume_state(tmp_path, state)


def test_structured_resume_rejects_pre_r6_failure_mirror_without_record(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    failure = InvocationFailureRecord(
        invocation_id="pre-r6-process-failure",
        idempotency_key="resume-run:2:codex_implementation:codex",
        role="codex",
        failure_kind=AgentFailureKind.PROCESS,
        provider_text="provider process failed",
        received_at="2026-08-31T10:00:00+00:00",
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_id=1,
        work_unit_id=2,
        diagnostic_exit_code=3,
    )
    state = state.record_invocation_failure(
        failure, wait_automatically=False
    )

    with pytest.raises(
        ArtifactResumeError,
        match="structured-v2 run has invocation failures but no R6 failure records",
    ) as caught:
        resolve_resume_state(tmp_path, state)

    assert caught.value.code is ReplayDiagnosticCode.RECORD_MISSING


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
    _append_invocation_failure(tmp_path, state, failure)

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
            work_unit_id="4",
            summary="Correction round finding.",
            acceptance_test="The correction resolves C-07.",
            origin_slice_id="2",
            origin_round_number=2,
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
    attestation = append_validation_authority(
        bridge,
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
            output_digest="0" * 64,
            content_record_id="ar1-" + "0" * 64,
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
    review = append_provider_decision_authority(
        bridge,
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
        operation="claude_slice_review",
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
    attestation = append_validation_authority(
        bridge,
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
            output_digest="0" * 64,
            content_record_id="ar1-" + "0" * 64,
        ),
        logical_id="validation-slice-round-1",
        idempotency_key="validation-slice-round-1",
        fingerprint_sha256="d" * 64,
    )
    review = append_provider_decision_authority(
        bridge,
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
        operation="claude_slice_review",
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
        item.finding_id: item
        for item in artifact_migration.project_latest_recorded_statuses(
            chain, imported=False, bound_only=False
        )
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
        {
            item.finding_id: item
            for item in artifact_migration.project_latest_recorded_statuses(
                chain, imported=False, bound_only=False
            )
        },
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
        {
            item.finding_id: item
            for item in artifact_migration.project_latest_recorded_statuses(
                chain, imported=False, bound_only=False
            )
        },
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
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
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
    return replace(
        state,
        runtime_history={
            "attestations": [validation_history_mirror(bridge, attestation)]
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
    _status_records(bridge, second_round)

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
    _status_records(bridge, second_round)

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
    _append_invocation_failure(tmp_path, state, failure)
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
            "attestations": [validation_history_mirror(bridge, attestation)]
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
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    state = replace(
        state,
        runtime_history={
            "attestations": [validation_history_mirror(bridge, attestation)]
        },
    )
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
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    state = replace(
        state,
        runtime_history={
            "attestations": [validation_history_mirror(bridge, attestation)]
        },
    )
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
