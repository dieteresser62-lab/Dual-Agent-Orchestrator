from __future__ import annotations

from collections import Counter
import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import orchestrator
from agent_runtime import NativeAgentCodexOutput
from audit_trail import ReviewAuditEvent, ValidationAuditEvent
from artifact_bridge import (
    ArtifactBridge,
    attestation_payload,
    finding_payload,
    review_payload,
)
from artifact_migration import ArtifactResumeError, resolve_resume_state
from artifact_models import (
    ArtifactRecord,
    BindingPayload,
    CorrectionWorkUnitPayload,
    FingerprintKind,
    InvocationFailurePayload,
    ProviderInputMeasurementPayload,
    ProviderInputComponentPayload,
    ProviderAttemptPayload,
    ProviderContentPayload,
    QuotaPausePayload,
    RecordType,
    ReviewEvidencePayload,
    ReviewPayload,
    ReviewPacketPayload,
    ReviewStopRequestPayload,
    Role,
    TransientRetryPayload,
    WorkUnitPayload,
    provider_text_evidence,
)
from artifact_replay import ReplayDiagnosticCode, replay_artifacts
from artifact_store import ArtifactStore
from artifact_projection import ArtifactAuditProjection
from content_authority import ValidationCapture, validation_output_digest
from content_authority_support import (
    append_provider_decision_authority,
    append_validation_authority,
)
from contracts import (
    AgentRole,
    CodexContractResult,
    ContractResult,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ReviewEvidence,
    PlannedSlice,
    StopRequest,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from orchestrator import ProductionWorkflowDriver
from review_packets import build_review_packet
from final_review_preflight import (
    FinalReviewPreflightDenied,
    FinalReviewPreflightResult,
)
from provider_input_budget import (
    PreparedProviderInput,
    ProviderInputBudgetExceeded,
    ProviderInputBudgetPolicy,
    ProviderInputBudgetRule,
    ProviderInputComponent,
    default_provider_input_budget_policy,
    measure_provider_input,
)
from workflow import (
    WorkflowContext,
    WorkflowEngine,
    WorkflowExecutionError,
    WorkflowHistory,
    WorkflowChanges,
)
from workflow_state import (
    AgentProfileBinding,
    AgentFailureKind,
    GateReason,
    InvocationFailureRecord,
    ProtocolBinding,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path, branch: str) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", branch)
    _git(repository, "config", "user.name", "Structured Regression")
    _git(repository, "config", "user.email", "structured@example.invalid")
    (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repository, "add", "seed.txt")
    _git(repository, "commit", "-m", "seed")
    return repository


def _state(repository: Path, run_id: str = "structured-regression"):
    head = _git(repository, "rev-parse", "HEAD")
    task = repository / "task.md"
    task.write_text("structured regression task\n", encoding="utf-8")
    return init_workflow_state(
        run_id=run_id,
        task_file=str(task),
        branch="feature/structured-regression",
        branch_base=head,
        slice_count=1,
        task_digest=hashlib.sha256(task.read_bytes()).hexdigest(),
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/structured-regression",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    )


def _driver(repository: Path) -> ProductionWorkflowDriver:
    return ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )


def test_first_checkpoint_bootstraps_authoritative_chain_idempotently(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository)
    driver = _driver(repository)

    driver.checkpoint(state, WorkflowHistory(1))
    persisted = driver.active_state
    assert persisted is not None
    driver.checkpoint(persisted, WorkflowHistory(1))
    driver.assert_structured_decision_context()

    chain = ArtifactStore(repository, state.run_id).load_chain()
    assert tuple(record.record_type for record in chain[:8]) == (
        RecordType.RUN_IDENTITY,
        RecordType.RUN_PROFILE,
        RecordType.SIDE_EFFECT,
        RecordType.SIDE_EFFECT,
        RecordType.WORKFLOW_TRANSITION,
        RecordType.WORKFLOW_POLICY,
        RecordType.GATE_TRANSITION,
        RecordType.TASK,
    )
    assert all(
        record.record_type is RecordType.SIDE_EFFECT for record in chain[8:]
    )


def test_external_side_effect_guard_loads_and_replays_the_chain_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-single-replay")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    original_load_chain = ArtifactStore.load_chain
    calls = 0

    def counted_load_chain(store: ArtifactStore):
        nonlocal calls
        calls += 1
        return original_load_chain(store)

    monkeypatch.setattr(ArtifactStore, "load_chain", counted_load_chain)

    driver.assert_structured_decision_context()

    assert calls == 1


def test_not_ready_final_report_resumes_without_a_final_report_mirror(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    head = _git(repository, "rev-parse", "HEAD")
    state = (
        _state(repository, "not-ready-final-report")
        .bind_slice_plan(
            (PlannedSlice(1, "implementation", ("src/runtime.py",)),),
            first_start_commit=head,
        )
        .complete_current_work_unit()
        .start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,
        )
        .bind_current_slice_git_boundary(
            start_commit=head,
            scope_paths=("src/runtime.py",),
            start_fingerprint="b" * 64,
        )
    )
    final_unit = replace(
        state.current_work_unit,
        kind=WorkUnitKind.FINAL_REVIEW,
        current_step=WorkflowStep.CODEX_FINAL_REVIEW,
    )
    state = replace(
        state,
        current_step=WorkflowStep.CODEX_FINAL_REVIEW,
        work_units=(*state.work_units[:-1], final_unit),
    )
    driver = _driver(repository)
    history = WorkflowHistory(state.current_work_unit_id)
    driver.checkpoint(state, history)
    request_id = "native-codex-request-" + "b" * 64
    canonical = json.dumps(
        {"request_id": request_id, "ready": False},
        sort_keys=True,
        separators=(",", ":"),
    )
    driver.persist_native_codex_contract(
        NativeAgentCodexOutput(
            result=CodexContractResult(
                ready=False,
                stopped=False,
                stop_request=None,
                validation=None,
                test_files=(),
                findings=(),
                slice_plan=(),
            ),
            canonical_json=canonical,
            request_id=request_id,
            response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        ),
        (),
    )
    halted = state.await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail="CODEX-FINAL-REPORT-NOT-READY | retry the same final report step",
    )
    driver.checkpoint(halted, history)
    persisted = driver.active_state
    assert persisted is not None

    resolution = resolve_resume_state(repository, persisted)
    contents = tuple(
        record.payload
        for record in resolution.replay_result.records
        if isinstance(record.payload, ProviderContentPayload)
    )

    assert contents[-1].content_kind == "agent_result"
    assert history.codex_final_report is None
    assert resolution.state.current_step is WorkflowStep.CODEX_FINAL_REVIEW


def test_resume_compares_only_latest_review_packet_per_work_unit(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    head = _git(repository, "rev-parse", "HEAD")
    state = (
        _state(repository, "review-packet-rounds")
        .bind_slice_plan(
            (PlannedSlice(1, "implementation", ("src/runtime.py",)),),
            first_start_commit=head,
        )
        .complete_current_work_unit()
        .start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,
        )
        .bind_current_slice_git_boundary(
            start_commit=head,
            scope_paths=("src/runtime.py",),
            start_fingerprint="0" * 64,
        )
        .with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    )
    driver = _driver(repository)
    history = WorkflowHistory(state.current_work_unit_id)
    driver.checkpoint(state, history)
    plan_text = (
        "### Slice 1 - Runtime\n\n"
        "**Ziel**\n\nBind review evidence.\n\n"
        "**Exakter Änderungspfad**\n\n- `src/runtime.py`\n\n"
        "#### " + "Akzep" + "tanzkriterien\n\n"
        "- Resume uses the latest packet.\n"
    )
    packets = []
    attestations = []
    for index, fingerprint in enumerate(("c" * 64, "d" * 64), start=1):
        command = ValidationCommandSpec(argv=("pytest",))
        capture = ValidationCapture(
            "pytest", "pass", 0, f"round {index}", "", f"round {index}"
        )
        attestation = ValidationAttestation(
            attestation_id=f"validation-{fingerprint[:12]}",
            diff_fingerprint=fingerprint,
            expected_commands=("pytest",),
            records=(
                ValidationRecord(
                    ValidationStatus.PASS,
                    "pytest",
                    0,
                    f"round {index}",
                ),
            ),
            output_digest=validation_output_digest((capture,)),
            summary=f"round {index} passed",
            command_specs=(command,),
            content_captures=(capture,),
        )
        driver.persist_validation_attestation(attestation)
        packet = build_review_packet(
            purpose="slice",
            fingerprint=fingerprint,
            start_fingerprint="0" * 64,
            paths=("src/runtime.py",),
            review_diff=(
                "diff --git a/src/runtime.py b/src/runtime.py\n"
                "index 1111111..2222222 100644\n"
                "--- a/src/runtime.py\n"
                "+++ b/src/runtime.py\n"
                "@@ -1 +1 @@\n"
                f"-old {index}\n+new {index}\n"
            ),
            plan_text=plan_text,
            slice_id=1,
            attestation=attestation,
            findings=(),
        )
        driver.persist_review_packet(packet)
        attestations.append(attestation)
        packets.append(packet)
        history = replace(
            history,
            attestations=tuple(attestations),
            active_review_packet=packet,
        )
        driver.checkpoint(state, history)
        state = driver.active_state or state

    resolution = resolve_resume_state(repository, state)
    packet_records = tuple(
        record
        for record in resolution.replay_result.records
        if isinstance(record.payload, ReviewPacketPayload)
    )

    assert len(packet_records) == 2
    assert history.active_review_packet is packets[-1]
    assert packet_records[-1].payload.fingerprint == packets[-1].fingerprint


def test_external_side_effect_guard_rejects_mirror_ahead_of_records(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-mirror-drift")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    assert driver.active_state is not None
    driver.active_state = replace(
        driver.active_state, task_scope_patterns=("src/foreign.py",)
    )

    with pytest.raises(WorkflowExecutionError, match="decision context is not resumable"):
        driver.assert_structured_decision_context()


def _invocation_failure_payload(
    failure: InvocationFailureRecord,
) -> InvocationFailurePayload:
    marker, digest, byte_count = provider_text_evidence(failure.provider_text)
    decision_at = failure.received_at
    retry_delay = (
        failure.safety_margin_seconds
        if failure.failure_kind is AgentFailureKind.QUOTA
        else 5
        if failure.failure_kind is AgentFailureKind.NETWORK
        and failure.automatic_resume
        else 0
    )
    return InvocationFailurePayload(
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
        decision_at_utc=decision_at,
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


@pytest.mark.parametrize(
    "drifted",
    (
        {
            "branch": "feature/foreign-branch",
            "target_branch": "feature/foreign-branch",
        },
        {
            "protocol_binding": ProtocolBinding(
                ProtocolMode.STRUCTURED_V2,
                "2",
                codex_profile=AgentProfileBinding("foreign-codex", "medium"),
            )
        },
    ),
)
def test_baseline_rebind_rejects_run_record_mirror_drift_before_append(
    tmp_path: Path, drifted: dict[str, object]
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-run-binding-drift")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))

    with pytest.raises(ArtifactResumeError) as caught:
        driver.bind_work_unit(replace(state, **drifted))

    assert caught.value.code is ReplayDiagnosticCode.MIRROR_AMBIGUOUS


def test_external_side_effect_guard_rejects_review_record_ahead_of_mirror(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-review-drift")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    validation_output = "pass:0"
    attestation = ValidationAttestation(
        attestation_id="validation-review-drift",
        diff_fingerprint="b" * 64,
        expected_commands=("python3 -m pytest tests/ -v",),
        records=(
            ValidationRecord(
                ValidationStatus.PASS,
                "python3 -m pytest tests/ -v",
                0,
                validation_output,
            ),
        ),
        output_digest=validation_output_digest(
            (
                ValidationCapture(
                    "python3 -m pytest tests/ -v",
                    "pass",
                    0,
                    validation_output,
                    "",
                    validation_output,
                ),
            )
        ),
        summary="test validation authority",
        command_specs=(
            ValidationCommandSpec(
                argv=("python3", "-m", "pytest", "tests/", "-v")
            ),
        ),
    )
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    append_validation_authority(
        bridge,
        attestation_payload(attestation, "ar1-" + "0" * 64),
        logical_id=attestation.attestation_id,
        idempotency_key="attestation:validation-review-drift",
        fingerprint_sha256=attestation.diff_fingerprint,
    )
    assert driver.active_state is not None
    driver.checkpoint(
        driver.active_state,
        WorkflowHistory(1, attestations=(attestation,)),
    )
    append_provider_decision_authority(
        bridge,
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="1",
            verdict="approved",
            finding_ids=(),
            evidence=None,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
            review_evidence=ReviewEvidencePayload(
                "complete evidence",
                "fixture residual risk",
                "fixture break condition",
            ),
        ),
        logical_id="review-claude-1-1",
        idempotency_key="review-drift",
        fingerprint_sha256="b" * 64,
        operation=state.current_step.value,
    )

    with pytest.raises(WorkflowExecutionError, match="reviewer decisions differ"):
        driver.assert_structured_decision_context()


def _legacy_final_denial_recovery_case(
    repository: Path,
    *,
    correction_finding_ids: tuple[str, ...] = ("C-01",),
) -> tuple[WorkflowState, tuple[object, ...], tuple[ArtifactRecord, ...]]:
    head = _git(repository, "rev-parse", "HEAD")
    state = _state(repository, "structured-final-denial-transition").bind_slice_plan(
        (PlannedSlice(1, "implementation", ("src/runtime.py",)),),
        first_start_commit=head,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="b" * 64,
    ).complete_current_slice(
        commit_ref=head,
    ).start_final_review_work_unit()
    attestation = ValidationAttestation(
        attestation_id="validation-final-denial",
        diff_fingerprint="c" * 64,
        expected_commands=("python3 -m pytest tests/ -v",),
        records=(
            ValidationRecord(
                ValidationStatus.PASS,
                "python3 -m pytest tests/ -v",
                0,
            ),
        ),
        output_digest="d" * 64,
        summary="validation completed before final denial",
        command_specs=(
            ValidationCommandSpec(
                argv=("python3", "-m", "pytest", "tests/", "-v")
            ),
        ),
    )
    final_history = WorkflowHistory(
        state.current_work_unit_id,
        events=(ValidationAuditEvent(1, 1, attestation),),
        attestations=(attestation,),
    )
    state = replace(
        state,
        runtime_history={"current": final_history.to_dict(), "archive": []},
    )
    correction = state.complete_current_work_unit().start_correction_work_unit(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="e" * 64,
        finding_ids=("C-01",),
    )
    signature = (
        str(state.current_work_unit_id),
        "claude",
        attestation.diff_fingerprint,
        "denied",
        ("C-01",),
    )

    bridge = ArtifactBridge(ArtifactStore(repository, correction.run_id))
    bridge.append(
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id=signature[0],
            verdict="denied",
            finding_ids=signature[4],
            evidence="legacy final denial",
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
        ),
        logical_id=f"review-claude-{signature[0]}-1",
        idempotency_key="legacy-final-denial-review",
        fingerprint_sha256=signature[2],
    )
    bridge.append(
        CorrectionWorkUnitPayload(
            slice_id=str(correction.current_slice_id),
            round_number=1,
            paths=correction.current_slice.scope_paths,
            finding_ids=correction_finding_ids,
        ),
        logical_id=f"work-unit-{correction.current_work_unit_id}",
        idempotency_key="legacy-final-denial-correction",
        fingerprint_sha256=correction.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )

    return correction, signature, bridge.store.load_chain()


def test_external_side_effect_guard_accepts_legacy_final_denial_correction_transition(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    correction, signature, chain = _legacy_final_denial_recovery_case(repository)

    assert orchestrator._recoverable_final_denial_mirror_gap(
        correction,
        Counter({signature: 1}),
        Counter(),
        chain=chain,
    ) == Counter({signature: 1})


@pytest.mark.parametrize(
    "failure_mode",
    (
        "approved-verdict",
        "state-open-findings-not-subset",
        "record-open-findings-not-subset",
        "attestation-fingerprint",
        "reviewed-unit-kind",
        "correction-unit-kind",
        "multiple-missing-signatures",
        "mirror-ahead",
    ),
)
def test_external_side_effect_guard_rejects_near_miss_final_denial_recovery(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    correction_ids = (
        ("C-99",)
        if failure_mode == "record-open-findings-not-subset"
        else ("C-01",)
    )
    correction, signature, chain = _legacy_final_denial_recovery_case(
        repository,
        correction_finding_ids=correction_ids,
    )
    record_reviews = Counter({signature: 1})
    mirror_reviews: Counter[tuple[object, ...]] = Counter()

    if failure_mode == "approved-verdict":
        changed = (*signature[:3], "approved", signature[4])
        record_reviews = Counter({changed: 1})
    elif failure_mode == "state-open-findings-not-subset":
        current_id = correction.current_work_unit_id
        correction = replace(
            correction,
            work_units=tuple(
                replace(unit, open_findings=("C-99",))
                if unit.work_unit_id == current_id
                else unit
                for unit in correction.work_units
            ),
        )
    elif failure_mode == "attestation-fingerprint":
        changed = (signature[0], signature[1], "f" * 64, *signature[3:])
        record_reviews = Counter({changed: 1})
    elif failure_mode == "reviewed-unit-kind":
        reviewed_id = int(str(signature[0]))
        correction = replace(
            correction,
            work_units=tuple(
                replace(unit, kind=WorkUnitKind.SLICE)
                if unit.work_unit_id == reviewed_id
                else unit
                for unit in correction.work_units
            ),
        )
    elif failure_mode == "correction-unit-kind":
        object.__setattr__(
            correction.current_work_unit,
            "kind",
            WorkUnitKind.SLICE,
        )
    elif failure_mode == "multiple-missing-signatures":
        record_reviews = Counter({signature: 2})
    elif failure_mode == "mirror-ahead":
        mirror_reviews[("999", "claude", "e" * 64, "denied", ("C-99",))] = 1

    assert orchestrator._recoverable_final_denial_mirror_gap(
        correction,
        record_reviews,
        mirror_reviews,
        chain=(
            ()
            if failure_mode == "state-open-findings-not-subset"
            else chain
        ),
    ) == Counter()


def _pending_reviewer_recovery_case(
    repository: Path,
    *,
    reviewer: Role = Role.CLAUDE,
    verdict: str = "denied",
    output_verdict: str = "denied",
    logical_round: int = 1,
    malformed_key: bool = False,
    mirrored_partial_review: bool = False,
    attestation_fingerprint: str | None = None,
) -> tuple[ProductionWorkflowDriver, WorkflowState, str]:
    head = _git(repository, "rev-parse", "HEAD")
    state = (
        _state(repository, "structured-pending-review-near-miss")
        .bind_slice_plan(
            (PlannedSlice(1, "implementation", ("src/runtime.py",)),),
            first_start_commit=head,
        )
        .complete_current_work_unit()
        .start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,
        )
        .bind_current_slice_git_boundary(
            start_commit=head,
            scope_paths=("src/runtime.py",),
            start_fingerprint="b" * 64,
        )
        .complete_current_slice(commit_ref=head)
        .start_final_review_work_unit()
        .complete_current_work_unit()
        .start_correction_work_unit(
            start_commit=head,
            scope_paths=("src/runtime.py",),
            start_fingerprint="c" * 64,
            finding_ids=("C-07",),
        )
        .with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    )
    fingerprint = "d" * 64
    attestation = ValidationAttestation(
        attestation_id="validation-pending-review",
        diff_fingerprint=fingerprint,
        expected_commands=("python3 -m pytest tests/ -v",),
        records=(
            ValidationRecord(
                ValidationStatus.PASS,
                "python3 -m pytest tests/ -v",
                0,
            ),
        ),
        output_digest="e" * 64,
        summary="validation before interrupted reviewer checkpoint",
        command_specs=(
            ValidationCommandSpec(argv=("python3", "-m", "pytest", "tests/", "-v")),
        ),
    )
    prior_finding = FindingRecord(
        finding_id="C-07",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="pre-existing observation",
        acceptance_test="Carry the observation through review.",
        origin=FindingOrigin("1", 1, AgentRole.CLAUDE),
    )
    events: tuple[object, ...] = (
        ValidationAuditEvent(1, state.current_slice_id, attestation),
    )
    if mirrored_partial_review:
        partial_finding = FindingRecord(
            finding_id="C-99",
            finding_class=FindingClass.BLOCKER,
            status=FindingStatus.OPEN,
            summary="partially mirrored reviewer result",
            acceptance_test="Do not replay across a partial mirror.",
            origin=FindingOrigin(
                f"{state.current_slice_id:02d}",
                1,
                AgentRole.CLAUDE,
            ),
        )
        partial = ContractResult(
            reviewer=AgentRole.CLAUDE,
            approval=False,
            stopped=False,
            stop_request=None,
            validation=attestation,
            test_files=(),
            pre_mortem=None,
            evidence=None,
            findings=(prior_finding, partial_finding),
            anchors=(),
        )
        events += (
            ReviewAuditEvent(
                2,
                state.current_slice_id,
                1,
                partial,
                allowed_finding_origins=("1",),
            ),
        )
    history = WorkflowHistory(
        state.current_work_unit_id,
        events=events,
        attestations=(attestation,),
        findings=(prior_finding,),
    )
    state = replace(state, runtime_history={"current": history.to_dict(), "archive": []})
    if output_verdict == "approved":
        output = "\n".join(
            (
                "REVIEWER: claude",
                "REVIEW_EVIDENCE: replay identity and binding | stale durable "
                "verdict | the replay record differs from its provider log",
                "FINDING_STATUS: C-07 | CLOSED | The correction resolves the prior finding.",
                "PRE_MORTEM: A future adapter change could weaken replay identity.",
                f"SLICE_APPROVAL: {state.current_slice_id:02d} | YES",
                "STATUS: DONE",
            )
        )
    else:
        output = "\n".join(
            (
                "REVIEWER: claude",
                "NEW_FINDING: C-01 | BLOCKER | replay near miss | Keep replay fail closed.",
                "FINDING_STATUS: C-07 | OPEN | Preserve the prior finding for correction.",
                f"SLICE_APPROVAL: {state.current_slice_id:02d} | NO",
                "STATUS: DONE",
            )
        )
    logical_id = (
        f"review-claude-{state.current_work_unit_id}-{logical_round}"
    )
    digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    bridge.append(
        attestation_payload(attestation, "ar1-" + "0" * 64),
        logical_id=attestation.attestation_id,
        idempotency_key=f"attestation:{attestation.attestation_id}",
        fingerprint_sha256=attestation_fingerprint or fingerprint,
    )
    bridge.append(
        finding_payload(prior_finding),
        logical_id="finding-C-07",
        idempotency_key="finding:C-07:opened:1:claude",
        fingerprint_sha256=fingerprint,
    )
    bridge.append(
        ReviewPayload(
            reviewer=reviewer,
            work_unit_id=str(state.current_work_unit_id),
            verdict=verdict,
            finding_ids=(
                ("C-07",)
                if output_verdict == "approved" and verdict == "approved"
                else ("C-01", "C-07")
            ),
            evidence=(
                "replay identity and binding | stale durable verdict | "
                "the replay record differs from its provider log"
                if verdict == "approved" and output_verdict == "approved"
                else "near-miss evidence"
                if verdict == "approved"
                else None
            ),
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
        ),
        logical_id=logical_id,
        idempotency_key=(
            f"malformed:{digest}"
            if malformed_key
            else f"parsed:{logical_id}:{fingerprint}:{digest}"
        ),
        fingerprint_sha256=fingerprint,
    )
    driver = _driver(repository)
    driver.active_state = state
    driver._artifact_bridge = bridge
    driver.log_dir.mkdir(parents=True, exist_ok=True)
    (
        driver.log_dir
        / f"work-unit-{state.current_work_unit_id:04d}-claude_slice_review.attempt-1.log"
    ).write_text(output + "\n", encoding="utf-8")
    return driver, state, output


def test_budget_denial_persists_gate_checkpoint_and_resumes_idempotently(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    (repository / ".gitignore").write_text(
        ".orchestrator/\ntask.md\n", encoding="utf-8"
    )
    _git(repository, "add", ".gitignore")
    _git(repository, "commit", "-m", "ignore runtime control files")
    task = repository / "task.md"
    task.write_text("bootstrap task\n", encoding="utf-8")
    state = replace(
        _state(repository, "structured-bootstrap-resume"),
        task_digest=hashlib.sha256(task.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
    )
    head = _git(repository, "rev-parse", "HEAD")
    state = state.complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="b" * 64,
    )
    defaults = default_provider_input_budget_policy()
    policy = ProviderInputBudgetPolicy(
        tuple(
            ProviderInputBudgetRule(
                rule.provider,
                rule.role,
                rule.operation,
                3 if rule.key == ("codex", "codex", "codex_implementation") else rule.max_chars,
                3 if rule.key == ("codex", "codex", "codex_implementation") else rule.max_bytes,
            )
            for rule in defaults.rules
        )
    )
    measurement = measure_provider_input(
        PreparedProviderInput(
            command=("codex",),
            stdin_text="oversized",
            components=(ProviderInputComponent("stdin_prompt", "oversized"),),
        ),
        provider="codex",
        role="codex",
        operation="codex_implementation",
        binding_fingerprint="b" * 64,
        policy=policy,
    )
    assert not measurement.allowed
    driver = _driver(repository)
    history = WorkflowHistory(state.current_work_unit_id)
    driver.checkpoint(state, history)
    state = driver.active_state or state
    engine = WorkflowEngine(driver)
    context = WorkflowContext("assignment", "plan", "slice")

    def denied_provider_start() -> str:
        driver._persist_provider_bootstrap(measurement)
        raise ProviderInputBudgetExceeded(measurement)

    halted, output = engine._invoke_role(
        state, history, context, AgentRole.CODEX, denied_provider_start
    )

    assert output is None
    assert halted.current_work_unit.gate.reason.value == "bootstrap_check"
    assert halted.current_work_unit.gate.resume_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert driver.state_file.exists()
    checkpoint_path = next((driver.checkpoint_dir / halted.run_id).iterdir())
    checkpoint_state = WorkflowState.from_dict(
        json.loads(checkpoint_path.read_text(encoding="utf-8"))
    )
    assert checkpoint_state.current_work_unit.gate.reason.value == "bootstrap_check"
    measurement_records = tuple(
        record
        for record in ArtifactStore(repository, halted.run_id).load_chain()
        if isinstance(record.payload, ProviderInputMeasurementPayload)
    )
    assert len(measurement_records) == 1
    assert not any(
        isinstance(record.payload, ProviderAttemptPayload)
        for record in ArtifactStore(repository, halted.run_id).load_chain()
    )
    audit = ArtifactAuditProjection(
        ArtifactStore(repository, halted.run_id).load_chain()
    ).render_sections()["validation-attestation"]
    assert "Verletzung `chars,bytes`" in audit
    assert "Überhang `6/6`" in audit
    assert "local_input_largest_component `stdin_prompt`" in audit
    assert "local_input_component_count `1`" in audit
    assert "Komponenten `stdin_prompt=9/9`" in audit
    assert "oversized" not in audit

    resumed = halted.resume_after_invocation_halt()
    driver.checkpoint(resumed, history)
    resumed = driver.active_state or resumed
    halted_again, output = engine._invoke_role(
        resumed, history, context, AgentRole.CODEX, denied_provider_start
    )

    assert output is None
    assert halted_again.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert halted_again.current_work_unit.gate == halted.current_work_unit.gate
    measurements = tuple(
        record
        for record in ArtifactStore(repository, halted.run_id).load_chain()
        if isinstance(record.payload, ProviderInputMeasurementPayload)
    )
    assert len(measurements) == 1


def test_legacy_final_review_keeps_budget_fact_without_structured_preflight(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/legacy-final-review")
    task = repository / "task.md"
    task.write_text("legacy final review\n", encoding="utf-8")
    head = _git(repository, "rev-parse", "HEAD")
    (repository / "src").mkdir()
    (repository / "src/runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    state = init_workflow_state(
        run_id="legacy-final-review-bootstrap",
        task_file=str(task),
        branch="feature/legacy-final-review",
        branch_base=head,
        slice_count=1,
        task_scope_patterns=("src/runtime.py",),
        protocol_binding=None,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="a" * 64,
    ).complete_current_slice(
        commit_ref=head,
    ).start_final_review_work_unit()
    driver = _driver(repository)
    driver.bind_work_unit(state)
    measurement = measure_provider_input(
        PreparedProviderInput(
            command=("codex",),
            stdin_text="final review",
            components=(ProviderInputComponent("stdin_prompt", "final review"),),
        ),
        provider="codex",
        role="codex",
        operation="codex_final_review",
        binding_fingerprint="b" * 64,
        policy=default_provider_input_budget_policy(),
    )

    driver._persist_provider_bootstrap(measurement)

    persisted = WorkflowState.from_dict(
        json.loads(driver.state_file.read_text(encoding="utf-8"))
    )
    assert persisted.effective_protocol_mode is ProtocolMode.LEGACY_STATE_V3
    assert tuple(item.check_kind for item in persisted.bootstrap_checks) == (
        RecordType.PROVIDER_INPUT_MEASUREMENT.value,
    )
    assert persisted.bootstrap_checks[0].decision == "allowed"
    assert not (repository / ".orchestrator" / "artifacts" / state.run_id).exists()


def test_final_preflight_denial_exposes_affected_paths_on_resume_gate(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/preflight-paths")
    state = init_workflow_state(
        run_id="preflight-paths",
        task_file=str(repository / "task.md"),
        branch="feature/preflight-paths",
        branch_base=_git(repository, "rev-parse", "HEAD"),
        slice_count=1,
    )
    driver = _driver(repository)
    history = WorkflowHistory(state.current_work_unit_id)
    driver.checkpoint(state, history)
    state = driver.active_state or state
    denial = FinalReviewPreflightDenied(
        FinalReviewPreflightResult(
            "denied",
            "correction_required",
            "UNAUTHORIZED-PATH",
            (),
            ("src/external.py", "tests/test_external.py"),
            "move the changes into an authorized Slice or revert them",
        ),
        fingerprint="d" * 64,
    )

    def denied_provider_start() -> str:
        raise denial

    halted, output = WorkflowEngine(driver)._invoke_role(
        state,
        history,
        WorkflowContext("assignment", "plan", "slice"),
        AgentRole.CODEX,
        denied_provider_start,
    )

    assert output is None
    assert halted.current_work_unit.gate.reason.value == "unexpected_file"
    assert halted.current_work_unit.gate.fingerprint == "d" * 64
    assert halted.current_work_unit.gate.resume_step is WorkflowStep.CODEX_PLAN
    assert halted.current_work_unit.gate.paths == (
        "src/external.py",
        "tests/test_external.py",
    )


@pytest.mark.parametrize(
    ("current_step", "error_code", "rewind_step"),
    [
        (
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            "CODEX-FINAL-RESULT-MISSING",
            WorkflowStep.CODEX_FINAL_REVIEW,
        ),
    ],
)
def test_final_preflight_missing_prerequisite_rewinds_without_manual_resume(
    tmp_path: Path,
    current_step: WorkflowStep,
    error_code: str,
    rewind_step: WorkflowStep,
) -> None:
    repository = _repository(tmp_path, "feature/preflight-rewind")
    head = _git(repository, "rev-parse", "HEAD")
    state = _state(repository, f"preflight-rewind-{current_step.value}").bind_slice_plan(
        (PlannedSlice(1, "implementation", ("src/runtime.py",)),),
        first_start_commit=head,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="b" * 64,
    ).complete_current_slice(
        commit_ref=head,
    ).start_final_review_work_unit().with_current_step(current_step)

    class RewindDriver:
        def __init__(self) -> None:
            self.active_state: WorkflowState | None = None

        def checkpoint(
            self,
            checkpoint_state: WorkflowState,
            _history: WorkflowHistory,
        ) -> None:
            self.active_state = checkpoint_state

    driver = RewindDriver()
    history = WorkflowHistory(state.current_work_unit_id)
    denial = FinalReviewPreflightDenied(
        FinalReviewPreflightResult(
            "denied",
            "technical",
            error_code,
            (),
            (),
            "restore the missing prerequisite",
        )
    )

    def denied_provider_start() -> str:
        raise denial

    rewound, output = WorkflowEngine(driver)._invoke_role(
        state,
        history,
        WorkflowContext("assignment", "plan", "slice"),
        AgentRole.CLAUDE,
        denied_provider_start,
    )

    assert output is None
    assert rewound.current_step is rewind_step
    assert rewound.current_work_unit.status.value == "in_progress"
    assert rewound.current_work_unit.gate.reason.value == "none"


def test_automatic_quota_pause_persists_matching_chain_record_and_resumes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-quota-resume")
    head = _git(repository, "rev-parse", "HEAD")
    state = state.complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="b" * 64,
    )
    failure = InvocationFailureRecord(
        invocation_id="quota-pause-1",
        idempotency_key=(
            f"{state.run_id}:{state.current_work_unit_id}:"
            f"{state.current_step.value}:codex"
        ),
        role="codex",
        failure_kind=AgentFailureKind.QUOTA,
        provider_text="usage cap reached",
        received_at=datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc).isoformat(),
        step=state.current_step,
        slice_id=state.current_slice_id,
        work_unit_id=state.current_work_unit_id,
        diagnostic_exit_code=2,
        parse_path="codex:text:relative",
        source_timezone="UTC",
        reset_at_utc=datetime(2026, 8, 18, 10, 1, tzinfo=timezone.utc).isoformat(),
        resume_at_utc=datetime(2026, 8, 18, 10, 1, 5, tzinfo=timezone.utc).isoformat(),
        safety_margin_seconds=5,
        auto_resume_count=1,
        automatic_resume=True,
        diff_fingerprint="c" * 64,
    )
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(state.current_work_unit_id))
    driver.persist_invocation_failure(_invocation_failure_payload(failure))
    paused = state.record_invocation_failure(failure, wait_automatically=True)

    driver.checkpoint(paused, WorkflowHistory(paused.current_work_unit_id))

    chain = ArtifactStore(repository, paused.run_id).load_chain()
    quota_records = tuple(
        record for record in chain if isinstance(record.payload, QuotaPausePayload)
    )
    assert len(quota_records) == 1
    failure_record = next(
        record
        for record in chain
        if isinstance(record.payload, InvocationFailurePayload)
    )
    assert chain.index(failure_record) < chain.index(quota_records[0])
    assert quota_records[0].payload == QuotaPausePayload(
        role=Role.CODEX,
        repository_fingerprint="c" * 64,
        retry_at=failure.resume_at_utc,
    )

    assert driver.active_state is not None
    resumed_state = driver.active_state.resume_after_invocation_halt()
    driver.checkpoint(
        resumed_state, WorkflowHistory(resumed_state.current_work_unit_id)
    )
    driver.assert_structured_decision_context()
    assert len(
        tuple(
            record
            for record in ArtifactStore(repository, paused.run_id).load_chain()
            if isinstance(record.payload, QuotaPausePayload)
        )
    ) == 1


def test_record_ahead_failure_resume_is_idempotent_and_does_not_restart_provider(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-record-ahead-failure")
    head = _git(repository, "rev-parse", "HEAD")
    state = state.complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CLAUDE_SLICE_REVIEW,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="b" * 64,
    )
    failure = InvocationFailureRecord(
        invocation_id="record-ahead-network-1",
        idempotency_key=(
            f"{state.run_id}:{state.current_work_unit_id}:"
            f"{state.current_step.value}:claude"
        ),
        role="claude",
        failure_kind=AgentFailureKind.NETWORK,
        provider_text="HTTP 529 overloaded",
        received_at="2026-08-31T10:00:00+00:00",
        step=state.current_step,
        slice_id=state.current_slice_id,
        work_unit_id=state.current_work_unit_id,
        diagnostic_exit_code=3,
        resume_at_utc="2026-08-31T10:00:05+00:00",
        auto_resume_count=1,
        automatic_resume=True,
        diff_fingerprint="c" * 64,
    )
    payload = _invocation_failure_payload(failure)
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(state.current_work_unit_id))

    driver.persist_invocation_failure(payload)
    driver.persist_invocation_failure(payload)

    chain = ArtifactStore(repository, state.run_id).load_chain()
    assert sum(
        isinstance(record.payload, InvocationFailurePayload) for record in chain
    ) == 1
    resolved = resolve_resume_state(repository, state)
    assert resolved.replay_result is not None
    assert resolved.replay_result.invocation_failures == (payload,)
    assert resolved.state.current_work_unit.status is WorkUnitStatus.WAITING_FOR_RETRY
    assert resolved.state.current_work_unit.invocation_failures == (
        replace(failure, provider_text=payload.provider_text),
    )

    result = WorkflowEngine(driver).run_current_work_unit(
        resolved.state,
        WorkflowContext("assignment", "plan", "slice"),
    )

    assert result.state.current_work_unit.status is WorkUnitStatus.WAITING_FOR_RETRY
    assert len(result.state.current_work_unit.invocation_failures) == 1
    assert not any(
        isinstance(record.payload, ProviderAttemptPayload)
        for record in ArtifactStore(repository, state.run_id).load_chain()
    )


def test_automatic_network_retry_uses_its_own_chain_record_idempotently(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-network-retry")
    head = _git(repository, "rev-parse", "HEAD")
    state = state.complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CLAUDE_SLICE_REVIEW,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="b" * 64,
    )
    failure = InvocationFailureRecord(
        invocation_id="network-retry-1",
        idempotency_key=(
            f"{state.run_id}:{state.current_work_unit_id}:"
            f"{state.current_step.value}:claude"
        ),
        role="claude",
        failure_kind=AgentFailureKind.NETWORK,
        provider_text="HTTP 529 overloaded",
        received_at=datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc).isoformat(),
        step=state.current_step,
        slice_id=state.current_slice_id,
        work_unit_id=state.current_work_unit_id,
        diagnostic_exit_code=3,
        resume_at_utc=datetime(2026, 8, 18, 10, 0, 5, tzinfo=timezone.utc).isoformat(),
        auto_resume_count=1,
        automatic_resume=True,
        diff_fingerprint="c" * 64,
    )
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(state.current_work_unit_id))
    driver.persist_invocation_failure(_invocation_failure_payload(failure))
    waiting = state.record_invocation_failure(failure, wait_automatically=True)

    driver.checkpoint(waiting, WorkflowHistory(waiting.current_work_unit_id))

    chain = ArtifactStore(repository, waiting.run_id).load_chain()
    retry_records = tuple(
        record for record in chain if isinstance(record.payload, TransientRetryPayload)
    )
    assert len(retry_records) == 1
    failure_record = next(
        record
        for record in chain
        if isinstance(record.payload, InvocationFailurePayload)
    )
    assert chain.index(failure_record) < chain.index(retry_records[0])
    assert retry_records[0].payload == TransientRetryPayload(
        role=Role.CLAUDE,
        repository_fingerprint="c" * 64,
        retry_at=failure.resume_at_utc,
        attempt=1,
    )
    assert not any(isinstance(record.payload, QuotaPausePayload) for record in chain)

    assert driver.active_state is not None
    resumed_state = driver.active_state.resume_after_invocation_halt()
    driver.checkpoint(
        resumed_state, WorkflowHistory(resumed_state.current_work_unit_id)
    )
    driver.assert_structured_decision_context()
    assert len(
        tuple(
            record
            for record in ArtifactStore(repository, waiting.run_id).load_chain()
            if isinstance(record.payload, TransientRetryPayload)
        )
    ) == 1


def test_structured_resume_accepts_mirrored_stopped_review(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-stopped-review")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    validation_output = "pass:0"
    attestation = ValidationAttestation(
        attestation_id="validation-stop",
        diff_fingerprint="b" * 64,
        expected_commands=("python3 -m pytest tests/ -v",),
        records=(
            ValidationRecord(
                ValidationStatus.PASS,
                "python3 -m pytest tests/ -v",
                0,
                validation_output,
            ),
        ),
        output_digest=validation_output_digest(
            (
                ValidationCapture(
                    "python3 -m pytest tests/ -v",
                    "pass",
                    0,
                    validation_output,
                    "",
                    validation_output,
                ),
            )
        ),
        summary="test validation authority",
        command_specs=(
            ValidationCommandSpec(
                argv=("python3", "-m", "pytest", "tests/", "-v")
            ),
        ),
    )
    stopped = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=None,
        stopped=True,
        stop_request=StopRequest("CONTRACT-UNCLEAR", "owner decision required"),
        validation=attestation,
        test_files=(),
        pre_mortem=None,
        evidence=None,
        findings=(),
        anchors=(),
    )
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    append_validation_authority(
        bridge,
        attestation_payload(attestation, "ar1-" + "0" * 64),
        logical_id=attestation.attestation_id,
        idempotency_key="attestation:validation-stop",
        fingerprint_sha256=attestation.diff_fingerprint,
    )
    append_provider_decision_authority(
        bridge,
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="1",
            verdict="stop",
            finding_ids=(),
            evidence=None,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
            stop_request=ReviewStopRequestPayload(
                "CONTRACT-UNCLEAR",
                "owner decision required",
                (),
            ),
        ),
        logical_id="review-claude-1-1",
        idempotency_key="review-stop",
        fingerprint_sha256=attestation.diff_fingerprint,
        operation=state.current_step.value,
    )
    history = WorkflowHistory(
        1,
        events=(
            ValidationAuditEvent(1, 1, attestation),
            ReviewAuditEvent(2, 1, 1, stopped),
        ),
        attestations=(attestation,),
    )
    assert driver.active_state is not None
    driver.checkpoint(driver.active_state, history)

    wrong_latest = replace(
        stopped,
        stop_request=StopRequest(
            "CONTRACT-UNCLEAR",
            "a different latest-review mirror",
        ),
    )
    with pytest.raises(WorkflowExecutionError, match="latest review differs"):
        driver.checkpoint(
            driver.active_state,
            replace(history, latest_claude_review=wrong_latest),
        )

    resumed = _driver(repository)
    assert driver.active_state is not None
    resumed.bind_work_unit(driver.active_state)
    resumed.assert_structured_decision_context()


def test_unbound_historical_state_keeps_legacy_resume_mode(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    historical = replace(_state(repository, "legacy-resume"), protocol_binding=None)
    driver = _driver(repository)

    driver.bind_work_unit(historical)
    driver.assert_structured_decision_context()

    assert historical.effective_protocol_mode is ProtocolMode.LEGACY_STATE_V3
    assert ArtifactStore(repository, historical.run_id).load_chain() == ()


def test_pre_schema_failure_artifact_chain_replays_without_synthesized_facts(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, "pre-schema-failure-chain")
    bridge = ArtifactBridge(store)
    bridge.append(
        WorkUnitPayload("1", 1, ("src/runtime.py",)),
        logical_id="work-unit-1",
        idempotency_key="work-unit:1",
        fingerprint_sha256="a" * 64,
    )
    measurement = bridge.append(
        ProviderInputMeasurementPayload(
            Role.CLAUDE, Role.CLAUDE, "claude_slice_review", "1",
            "a" * 64, "b" * 64, "c" * 64, "d" * 64,
            (ProviderInputComponentPayload("prompt_file", 3, 3),),
            3, 3, 10, 10, None, None, None, 10, 10, True, (), 0, 0,
            "prompt_file",
        ),
        logical_id="measurement-legacy",
        idempotency_key="measurement:legacy",
        fingerprint_sha256="a" * 64,
    )
    started = bridge.start_provider_attempt(
        measurement_record=measurement,
        binding_fingerprint="a" * 64,
        work_unit_id="1",
    )
    bridge.finish_provider_attempt(
        started, duration_seconds=1.0, failure_kind="network", usage=None
    )
    before = store.load_chain()

    first_replay = replay_artifacts(before, store.run_id)
    after = store.load_chain()
    second_replay = replay_artifacts(after, store.run_id)

    assert after == before
    assert first_replay.records == second_replay.records
    assert Counter(record.record_type for record in after) == Counter(
        {
            RecordType.WORK_UNIT: 1,
            RecordType.PROVIDER_INPUT_MEASUREMENT: 1,
            RecordType.PROVIDER_ATTEMPT: 2,
        }
    )
    assert not any(
        record.record_type in {RecordType.REVIEW, RecordType.DIAGNOSTIC}
        for record in after
    )
