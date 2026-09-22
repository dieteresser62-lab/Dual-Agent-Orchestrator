from __future__ import annotations

from collections import Counter
import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import artifact_store as artifact_store_module
import orchestrator
from agent_adapters import AgentOutputError
from agent_runtime import (
    AgentProcessError,
    NativeAgentCodexOutput,
    classify_agent_failure,
)
from audit_trail import ReviewAuditEvent, ValidationAuditEvent
from artifact_bridge import (
    ArtifactBridge,
    ArtifactBridgeError,
    attestation_payload,
    finding_payload,
    review_payload,
)
from artifact_resume import resolve_resume_state
from artifact_models import (
    ArtifactRecord,
    BindingPayload,
    CorrectionWorkUnitPayload,
    DiagnosticPayload,
    FindingSeverity,
    FindingTransitionPayload,
    FingerprintKind,
    GateTransitionPayload,
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
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    TransientRetryPayload,
    WorkUnitPayload,
    provider_text_evidence,
    technical_text_evidence,
)
from artifact_replay import ArtifactReplayError, replay_artifacts
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
from finding_order import finding_id_sort_key
from orchestrator import ProductionWorkflowDriver
from native_review_contract import NativeReviewContractError, NativeReviewErrorCode
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
    WorkflowRunResult,
    WorkflowChanges,
)
from workflow_state import (
    AgentProfileBinding,
    AgentFailureKind,
    GateReason,
    GateStatus,
    InvocationFailureRecord,
    ProtocolBinding,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
)


SYNTHETIC_TECHNICAL_TEXT = technical_text_evidence(
    "synthetic invocation failure"
)[0]


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
        first_slice_start_commit=head,
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
    assert tuple(record.record_type for record in chain[:10]) == (
        RecordType.RUN_IDENTITY,
        RecordType.WORKFLOW_EVENT,
        RecordType.RUN_PROFILE,
        RecordType.SIDE_EFFECT,
        RecordType.SIDE_EFFECT,
        RecordType.WORKFLOW_TRANSITION,
        RecordType.WORKFLOW_EVENT,
        RecordType.WORKFLOW_POLICY,
        RecordType.GATE_TRANSITION,
        RecordType.TASK,
    )
    assert all(
        record.record_type is RecordType.SIDE_EFFECT for record in chain[10:]
    )


def test_external_side_effect_guard_reuses_the_process_local_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-single-replay")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    original_read = artifact_store_module._read_record
    reads = 0

    def counted_read(path: Path):  # type: ignore[no-untyped-def]
        nonlocal reads
        reads += 1
        return original_read(path)

    monkeypatch.setattr(artifact_store_module, "_read_record", counted_read)

    driver.assert_structured_decision_context()

    assert reads == 0


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
    resumed_history = orchestrator._history(resolution.state, repository)
    assert resumed_history.active_review_packet == packets[-1]
    assert tuple(
        item.attestation_id for item in resumed_history.attestations
    ) == tuple(item.attestation_id for item in attestations)


def test_external_side_effect_guard_reprojects_mirror_ahead_from_records(
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

    driver.assert_structured_decision_context()

    assert driver.active_state is not None
    assert driver.active_state.task_scope_patterns == ("src/runtime.py",)


def _invocation_failure_payload(
    failure: InvocationFailureRecord,
) -> InvocationFailurePayload:
    marker, digest, byte_count = provider_text_evidence(failure.provider_text)
    technical_marker, technical_digest, technical_byte_count = (
        technical_text_evidence(failure.technical_text)
    )
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
        technical_text=technical_marker,
        technical_text_sha256=technical_digest,
        technical_text_bytes=technical_byte_count,
        received_at=failure.received_at,
        decision_at_utc=decision_at,
        step=failure.step.value,
        slice_id=str(failure.slice_id),
        work_unit_id=str(failure.work_unit_id),
        diagnostic_exit_code=failure.diagnostic_exit_code,
        process_exit_code=failure.process_exit_code,
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


def test_process_failure_exit_and_redacted_technical_evidence_reach_authoritative_chain(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "provider-failure-diagnostics")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    active = driver.active_state
    assert active is not None
    now = datetime(2026, 9, 1, 18, 30, tzinfo=timezone.utc)
    raw_technical_text = "stderr sentinel: provider worker was killed"
    raw_provider_text = "provider process rejected the request"
    error = classify_agent_failure(
        AgentRole.CLAUDE.value,
        AgentOutputError(
            "review invocation failed",
            provider_text=raw_provider_text,
            technical_text=raw_technical_text,
            exit_code=137,
        ),
        invocation_id="canary-review-process-failure",
        received_at=now,
    )

    WorkflowEngine(driver, now_fn=lambda: now)._persist_invocation_failure(
        active,
        WorkflowHistory(active.current_work_unit_id),
        WorkflowContext("assignment", "plan", "slice"),
        AgentRole.CLAUDE,
        error,
    )

    chain = ArtifactStore(repository, state.run_id).load_chain()
    failure_records = tuple(
        record for record in chain
        if isinstance(record.payload, InvocationFailurePayload)
    )
    assert len(failure_records) == 1
    payload = failure_records[0].payload
    assert payload.failure_kind == "process"
    assert payload.process_exit_code == 137
    assert payload.diagnostic_exit_code == 3
    assert payload.provider_text_sha256 != payload.technical_text_sha256
    assert payload.technical_text.startswith("[technical text redacted; sha256=")
    assert payload.technical_text_bytes == len(raw_technical_text.encode("utf-8"))
    assert raw_technical_text.encode("utf-8") not in failure_records[0].canonical_json()
    diagnostic_path = (
        repository
        / ".orchestrator"
        / "logs"
        / (
                f"{state.run_id}.work-unit-{active.current_work_unit_id}."
                "request-0001.attempt-0001.failure.json"
            )
        )
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["run_id"] == state.run_id
    assert diagnostic["work_unit_id"] == str(active.current_work_unit_id)
    assert diagnostic["round_number"] == diagnostic["request_sequence"] == 1
    assert diagnostic["attempt_number"] == 1
    assert diagnostic["provider_text_sha256"] != diagnostic["technical_text_sha256"]


def test_structured_output_subtype_reaches_safe_halt_diagnostic(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-output-diagnostic")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    active = driver.active_state
    assert active is not None
    now = datetime(2026, 9, 9, 2, 22, tzinfo=timezone.utc)
    provider_text = "native Claude error"
    error = classify_agent_failure(
        AgentRole.CLAUDE.value,
        AgentOutputError(
            provider_text,
            provider_text=provider_text,
            technical_text=provider_text,
            exit_code=1,
            provider_data={
                "type": "result",
                "subtype": "error_max_structured_output_retries",
            },
        ),
        invocation_id="structured-output-diagnostic-1",
        received_at=now,
    )

    caplog.set_level("INFO", logger="workflow")
    WorkflowEngine(driver, now_fn=lambda: now)._persist_invocation_failure(
        active,
        WorkflowHistory(active.current_work_unit_id),
        WorkflowContext("assignment", "plan", "slice"),
        AgentRole.CLAUDE,
        error,
    )

    payload = next(
        record.payload
        for record in ArtifactStore(repository, state.run_id).load_chain()
        if isinstance(record.payload, InvocationFailurePayload)
    )
    assert payload.failure_kind == "output"
    assert payload.diagnostic_code == "PROVIDER-STRUCTURED-OUTPUT"
    assert payload.provider_text_sha256 != payload.technical_text_sha256
    diagnostic_path = next((repository / ".orchestrator" / "logs").glob("*.failure.json"))
    diagnostic_text = diagnostic_path.read_text(encoding="utf-8")
    diagnostic = json.loads(diagnostic_text)
    assert (
        diagnostic["provider_diagnostic_subtype"]
        == "error_max_structured_output_retries"
    )
    assert provider_text not in diagnostic_text
    assert "error_max_structured_output_retries" in caplog.text
    assert provider_text not in caplog.text


def test_code_version_change_is_warned_and_recorded_before_provider_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "code-version-resume")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    active = driver.active_state
    assert active is not None
    chain_before = ArtifactStore(repository, state.run_id).load_chain()
    profile = next(
        record.payload
        for record in chain_before
        if isinstance(record.payload, RunProfilePayload)
    )
    changed_version = (
        "f" * 64
        if profile.orchestrator_code_version != "f" * 64
        else "e" * 64
    )
    monkeypatch.setattr(orchestrator, "orchestrator_code_version", lambda: changed_version)
    caplog.set_level("WARNING")

    driver.checkpoint(active, WorkflowHistory(active.current_work_unit_id))

    chain = ArtifactStore(repository, state.run_id).load_chain()
    diagnostic = next(
        record
        for record in chain
        if isinstance(record.payload, DiagnosticPayload)
        and record.logical_id == "orchestrator-code-version-diff"
    )
    assert profile.orchestrator_code_version in diagnostic.payload.reason
    assert changed_version in diagnostic.payload.reason
    assert not any(isinstance(record.payload, ProviderAttemptPayload) for record in chain)
    assert "changed before provider start" in caplog.text


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
def test_baseline_rebind_rejects_untrusted_in_memory_drift_before_append(
    tmp_path: Path, drifted: dict[str, object]
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-run-binding-drift")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))

    before = ArtifactStore(repository, state.run_id).load_chain()
    with pytest.raises(ArtifactBridgeError, match="existing record"):
        driver.bind_work_unit(replace(state, **drifted))
    after = ArtifactStore(repository, state.run_id).load_chain()

    assert tuple(item.record_id for item in after) == tuple(
        item.record_id for item in before
    )


def test_external_side_effect_guard_preserves_review_record_ahead_of_transition(
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

    driver.assert_structured_decision_context()

    replay = replay_artifacts(
        ArtifactStore(repository, state.run_id).load_chain(),
        state.run_id,
        allow_incomplete_review_tail=True,
    )
    assert replay.pending_review_record_id is None
    assert any(
        isinstance(record.payload, ReviewPayload)
        and record.payload.work_unit_id == "1"
        for record in replay.records
    )


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
    binding = correction.protocol_binding
    assert binding is not None
    bridge.append(
        RunIdentityPayload(
            correction.task_file,
            correction.branch,
            correction.branch_base,
            correction.slices[0].start_commit,
            correction.execution_mode,
            correction.audit_report_path,
        ),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256=correction.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        RunProfilePayload(
            RoleProfilePayload(
                binding.codex_profile.model, binding.codex_profile.effort
            ),
            RoleProfilePayload(
                binding.claude_profile.model, binding.claude_profile.effort
            ),
        ),
        logical_id="run-profile",
        idempotency_key="run-profile",
        fingerprint_sha256=correction.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        WorkUnitPayload(
            str(state.current_slice_id),
            1,
            state.current_slice.scope_paths,
        ),
        logical_id=f"work-unit-{state.current_work_unit_id}",
        idempotency_key="legacy-final-denial-work-unit",
        fingerprint_sha256=correction.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    for finding_id in sorted(
        {*signature[4], *correction_finding_ids}, key=finding_id_sort_key
    ):
        bridge.append(
            FindingTransitionPayload(
                finding_id=finding_id,
                reporter=Role.CLAUDE,
                actor=Role.CLAUDE,
                action="opened",
                severity=FindingSeverity.BLOCKER,
                finding_status="open",
                rationale=f"Historical final denial opened {finding_id}.",
                work_unit_id=signature[0],
                summary=f"Historical final denial finding {finding_id}.",
                acceptance_test=f"Correction closes {finding_id}.",
                origin_slice_id="final",
                origin_round_number=1,
            ),
            logical_id=f"finding-{finding_id}",
            idempotency_key=f"legacy-final-denial:{finding_id}:opened",
            fingerprint_sha256=signature[2],
        )
    append_validation_authority(
        bridge,
        attestation_payload(attestation, "ar1-" + "0" * 64),
        logical_id=attestation.attestation_id,
        idempotency_key="legacy-final-denial-attestation",
        fingerprint_sha256=signature[2],
    )
    append_provider_decision_authority(
        bridge,
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
        operation=state.current_step.value,
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


def test_external_side_effect_guard_accepts_bound_final_denial_transition(
    tmp_path: Path,
) -> None:
    assert not hasattr(orchestrator, "_recoverable_final_denial_mirror_gap")


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
    assert failure_mode
    assert not hasattr(orchestrator, "_recoverable_final_denial_mirror_gap")


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


def test_budget_denial_persists_terminal_checkpoint_without_provider_start(
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
    assert halted.current_work_unit.status is WorkUnitStatus.COMPLETED
    assert halted.current_work_unit.gate.status is GateStatus.CLEAR
    assert halted.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert WorkflowRunResult(halted, history).rejection_code == "PROVIDER-INPUT-BUDGET"
    assert driver.state_file.exists()
    checkpoint_path = next((driver.checkpoint_dir / halted.run_id).iterdir())
    checkpoint_state = WorkflowState.from_dict(
        json.loads(checkpoint_path.read_text(encoding="utf-8"))["state"]
    )
    assert checkpoint_state.current_work_unit.status is WorkUnitStatus.COMPLETED
    assert checkpoint_state.current_work_unit.gate.status is GateStatus.CLEAR
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

    measurements = tuple(
        record
        for record in ArtifactStore(repository, halted.run_id).load_chain()
        if isinstance(record.payload, ProviderInputMeasurementPayload)
    )
    assert len(measurements) == 1


def test_final_review_preflight_missing_prerequisite_halts_for_resume(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/preflight-rewind")
    head = _git(repository, "rev-parse", "HEAD")
    state = (
        _state(repository, "preflight-resume-final-review")
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
    )

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
            "ATTESTATION-MISSING",
            (),
            (),
            "restore the missing prerequisite",
        )
    )

    def denied_provider_start() -> str:
        raise denial

    halted, output = WorkflowEngine(driver)._invoke_role(
        state,
        history,
        WorkflowContext("assignment", "plan", "slice"),
        AgentRole.CLAUDE,
        denied_provider_start,
    )

    assert output is None
    assert halted.current_step is WorkflowStep.CLAUDE_FINAL_REVIEW
    assert halted.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    assert halted.current_work_unit.gate.reason is GateReason.BOOTSTRAP_CHECK
    assert halted.current_work_unit.gate.detail is not None
    assert halted.current_work_unit.gate.detail.startswith("ATTESTATION-MISSING | ")


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
        process_exit_code=None,
        technical_text=SYNTHETIC_TECHNICAL_TEXT,
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
        process_exit_code=None,
        technical_text=SYNTHETIC_TECHNICAL_TEXT,
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


def test_gate_transition_after_invocation_failure_supersedes_failure_projection(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-failure-then-gate")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(state.current_work_unit_id))
    failure = InvocationFailureRecord(
        invocation_id="failure-before-policy-gate",
        idempotency_key=f"{state.run_id}:1:codex_plan:codex",
        role="codex",
        failure_kind=AgentFailureKind.NETWORK,
        provider_text="HTTP 529 overloaded",
        received_at="2026-08-31T10:00:00+00:00",
        step=state.current_step,
        slice_id=state.current_slice_id,
        work_unit_id=state.current_work_unit_id,
        diagnostic_exit_code=3,
        process_exit_code=None,
        technical_text=SYNTHETIC_TECHNICAL_TEXT,
        automatic_resume=False,
        diff_fingerprint="c" * 64,
    )
    driver.persist_invocation_failure(_invocation_failure_payload(failure))
    bridge = driver._artifact_bridge
    assert bridge is not None
    bridge.append(
        GateTransitionPayload(
            work_unit_id=str(state.current_work_unit_id),
            gate_status="awaiting_user_decision",
            reason="stop_request",
            detail="PLAN-CONTRACT-INVALID",
            fingerprint=None,
            paths=(),
            resume_step=None,
            active_test_fingerprint=None,
            active_test_paths=(),
        ),
        logical_id=f"gate-transition-{state.current_work_unit_id}",
        idempotency_key="gate-transition:failure-superseded:2",
        fingerprint_sha256=state.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )

    resolved = resolve_resume_state(repository, state)

    assert resolved.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert resolved.state.current_work_unit.gate.detail == "PLAN-CONTRACT-INVALID"


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
        process_exit_code=None,
        technical_text=SYNTHETIC_TECHNICAL_TEXT,
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


def test_schema_invalid_review_records_retryable_failure_with_typed_feedback(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-review-form-retry")
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
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(state.current_work_unit_id))
    active = driver.active_state
    assert active is not None
    driver.collect_changes = lambda _start: WorkflowChanges(  # type: ignore[method-assign]
        start_commit=head,
        fingerprint="c" * 64,
        paths=("src/runtime.py",),
        full_diff="diff --git a/src/runtime.py b/src/runtime.py\n",
    )
    now = datetime(2026, 9, 7, 20, 24, tzinfo=timezone.utc)
    contract_error = NativeReviewContractError(
        NativeReviewErrorCode.SCHEMA_INVALID,
        "variant 'bound_slice_initial_approved' failed at status_changes",
    )
    output_error = AgentOutputError(
        "native review result violates its bound contract",
        technical_text=f"{contract_error.code.value}: {contract_error.detail}",
    )
    output_error.__cause__ = contract_error
    failure_error = classify_agent_failure(
        AgentRole.CLAUDE.value,
        output_error,
        invocation_id="review-form-retry-1",
        received_at=now,
    )
    failure_error.__cause__ = output_error

    waiting, failure = WorkflowEngine(
        driver, now_fn=lambda: now
    )._persist_invocation_failure(
        active,
        WorkflowHistory(active.current_work_unit_id),
        WorkflowContext("assignment", "plan", "slice"),
        AgentRole.CLAUDE,
        failure_error,
    )
    driver.checkpoint(waiting, WorkflowHistory(waiting.current_work_unit_id))

    chain = ArtifactStore(repository, state.run_id).load_chain()
    failure_record = next(
        record for record in chain
        if isinstance(record.payload, InvocationFailurePayload)
        and record.payload.invocation_id == failure.invocation_id
    )
    assert failure_record.payload.failure_kind == "output"
    assert failure_record.payload.failure_class == "transient"
    assert failure_record.payload.diagnostic_code == "NATIVE-REVIEW-FORM"
    assert failure_record.payload.automatic_resume is True
    assert failure_record.payload.native_review_rejection == "schema-invalid"
    assert failure_record.payload.native_review_retry_round == 2
    assert any(
        isinstance(record.payload, TransientRetryPayload) for record in chain
    )


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
        last_claude_fingerprint=attestation.diff_fingerprint,
        latest_claude_review=stopped,
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


def test_pre_r1_failure_artifact_chain_is_rejected_without_synthesized_facts(
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
    before = store.load_chain()

    with pytest.raises(ArtifactReplayError, match="RECORD-MISSING"):
        bridge.start_provider_attempt(
            measurement_record=measurement,
            binding_fingerprint="a" * 64,
            work_unit_id="1",
        )

    after = store.load_chain()
    assert after == before
    assert Counter(record.record_type for record in after) == Counter(
        {
            RecordType.WORK_UNIT: 1,
            RecordType.PROVIDER_INPUT_MEASUREMENT: 1,
        }
    )
    assert not any(
        record.record_type in {RecordType.REVIEW, RecordType.DIAGNOSTIC}
        for record in after
    )
