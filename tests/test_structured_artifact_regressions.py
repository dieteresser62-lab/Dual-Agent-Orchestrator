from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import orchestrator
from audit_trail import ReviewAuditEvent, ValidationAuditEvent
from artifact_bridge import ArtifactBridge, attestation_payload
from artifact_models import (
    ProviderInputMeasurementPayload,
    QuotaPausePayload,
    RecordType,
    ReviewPayload,
    Role,
    TransientRetryPayload,
)
from artifact_store import ArtifactStore
from artifact_projection import ArtifactAuditProjection
from contracts import (
    AgentRole,
    ContractResult,
    PlannedSlice,
    StopRequest,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from orchestrator import ProductionWorkflowDriver
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
)
from workflow_state import (
    AgentFailureKind,
    InvocationFailureRecord,
    ProtocolBinding,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
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
    return init_workflow_state(
        run_id=run_id,
        task_file=str(repository / "task.md"),
        branch="feature/structured-regression",
        branch_base=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/structured-regression",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1"),
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
    assert tuple(record.record_type for record in chain) == (RecordType.TASK,)


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


def test_external_side_effect_guard_rejects_review_record_ahead_of_mirror(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-review-drift")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    ArtifactBridge(ArtifactStore(repository, state.run_id)).append(
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="1",
            verdict="approved",
            finding_ids=(),
            evidence="complete evidence",
        ),
        logical_id="review-claude-1-1",
        idempotency_key="review-drift",
        fingerprint_sha256="b" * 64,
    )

    with pytest.raises(WorkflowExecutionError, match="reviewer decisions differ"):
        driver.assert_structured_decision_context()


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
    audit = ArtifactAuditProjection(
        ArtifactStore(repository, halted.run_id).load_chain()
    ).render_sections()["validation-attestation"]
    assert "Verletzung `chars,bytes`" in audit
    assert "Überhang `6/6`" in audit
    assert "größte Komponente `stdin_prompt`" in audit
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
    assert len(
        tuple(
            record
            for record in ArtifactStore(repository, halted.run_id).load_chain()
            if isinstance(record.payload, ProviderInputMeasurementPayload)
        )
    ) == 1


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
        )
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
    assert halted.current_work_unit.gate.reason.value == "bootstrap_check"
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
        (
            WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
            "CLAUDE-FINAL-APPROVAL-MISSING",
            WorkflowStep.CLAUDE_FINAL_REVIEW,
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
    paused = state.record_invocation_failure(failure, wait_automatically=True)
    driver = _driver(repository)

    driver.checkpoint(paused, WorkflowHistory(paused.current_work_unit_id))

    chain = ArtifactStore(repository, paused.run_id).load_chain()
    quota_records = tuple(
        record for record in chain if isinstance(record.payload, QuotaPausePayload)
    )
    assert len(quota_records) == 1
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
    waiting = state.record_invocation_failure(failure, wait_automatically=True)
    driver = _driver(repository)

    driver.checkpoint(waiting, WorkflowHistory(waiting.current_work_unit_id))

    chain = ArtifactStore(repository, waiting.run_id).load_chain()
    retry_records = tuple(
        record for record in chain if isinstance(record.payload, TransientRetryPayload)
    )
    assert len(retry_records) == 1
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
    attestation = ValidationAttestation(
        attestation_id="validation-stop",
        diff_fingerprint="b" * 64,
        expected_commands=("python3 -m pytest tests/ -v",),
        records=(
            ValidationRecord(
                ValidationStatus.PASS,
                "python3 -m pytest tests/ -v",
                0,
            ),
        ),
        output_digest="c" * 64,
        summary="validation completed before reviewer stop",
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
    bridge.append(
        attestation_payload(attestation),
        logical_id=attestation.attestation_id,
        idempotency_key="attestation:validation-stop",
        fingerprint_sha256=attestation.diff_fingerprint,
    )
    bridge.append(
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="1",
            verdict="stop",
            finding_ids=(),
            evidence="owner decision required",
        ),
        logical_id="review-claude-1-1",
        idempotency_key="review-stop",
        fingerprint_sha256=attestation.diff_fingerprint,
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
