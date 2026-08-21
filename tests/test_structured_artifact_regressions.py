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
from audit_trail import ReviewAuditEvent, ValidationAuditEvent
from artifact_bridge import (
    ArtifactBridge,
    attestation_payload,
    finding_payload,
    review_payload,
)
from artifact_migration import resolve_resume_state
from artifact_models import (
    ArtifactRecord,
    BindingPayload,
    CorrectionWorkUnitPayload,
    FingerprintKind,
    ProviderInputMeasurementPayload,
    QuotaPausePayload,
    RecordType,
    ReviewPayload,
    Role,
    TransientRetryPayload,
    WorkUnitPayload,
)
from artifact_store import ArtifactStore
from artifact_projection import ArtifactAuditProjection
from contracts import (
    AgentRole,
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


def test_hash_bound_denied_review_replays_after_checkpoint_failure_without_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    head = _git(repository, "rev-parse", "HEAD")
    state = (
        _state(repository, "structured-review-checkpoint-replay")
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
            finding_ids=("A-01",),
        )
        .with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    )
    fingerprint = "d" * 64
    prior_finding = FindingRecord(
        finding_id="A-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="pre-existing Antigravity observation",
        acceptance_test="Carry the observation through the Claude review replay.",
        origin=FindingOrigin(
            str(state.current_slice_id),
            1,
            AgentRole.ANTIGRAVITY,
        ),
    )
    attestation = ValidationAttestation(
        attestation_id="validation-checkpoint-replay",
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
        summary="validation completed before the interrupted review checkpoint",
        command_specs=(
            ValidationCommandSpec(
                argv=("python3", "-m", "pytest", "tests/", "-v")
            ),
        ),
    )
    approvals = tuple(
        ContractResult(
            reviewer=reviewer,
            approval=True,
            stopped=False,
            stop_request=None,
            validation=attestation,
            test_files=(),
            pre_mortem="A future transition could invalidate the replay binding.",
            evidence=ReviewEvidence(
                "commit binding",
                "stale approval identity",
                "the commit fingerprint changes after approval",
            ),
            findings=(),
            anchors=(),
        )
        for reviewer in (AgentRole.CLAUDE, AgentRole.ANTIGRAVITY)
    )
    final_history = WorkflowHistory(
        state.current_work_unit_id - 1,
        events=(
            ValidationAuditEvent(1, 1, attestation),
            ReviewAuditEvent(2, 1, 1, approvals[0]),
            ReviewAuditEvent(3, 1, 1, approvals[1]),
        ),
        attestations=(attestation,),
    )
    history = WorkflowHistory(
        state.current_work_unit_id,
        events=(ValidationAuditEvent(1, state.current_slice_id, attestation),),
        attestations=(attestation,),
        findings=(prior_finding,),
    )
    state = replace(
        state,
        runtime_history={
            "archive": [final_history.to_dict()],
            "current": history.to_dict(),
        },
    )
    driver = _driver(repository)
    driver.bind_work_unit(state)
    driver.persist_validation_attestation(attestation)
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    final_unit = next(
        item
        for item in state.work_units
        if item.work_unit_id == state.current_work_unit_id - 1
    )
    final_slice = next(
        item for item in state.slices if item.slice_id == final_unit.slice_id
    )
    bridge.append(
        WorkUnitPayload(
            slice_id=str(final_unit.slice_id),
            round_number=final_unit.round_number,
            paths=final_slice.scope_paths,
        ),
        logical_id=f"work-unit-{state.current_work_unit_id - 1}",
        idempotency_key=(
            f"work-unit:{state.current_work_unit_id - 1}:round:{final_unit.round_number}"
        ),
        fingerprint_sha256=state.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        finding_payload(prior_finding),
        logical_id="finding-A-01",
        idempotency_key="finding:A-01:opened:1:antigravity",
        fingerprint_sha256=fingerprint,
    )
    approval_records = tuple(
        bridge.append(
            review_payload(result, work_unit_id=state.current_work_unit_id - 1),
            logical_id=(
                f"review-{result.reviewer.value}-{state.current_work_unit_id - 1}-1"
            ),
            idempotency_key=(
                f"review:{result.reviewer.value}:"
                f"{state.current_work_unit_id - 1}:1"
            ),
            fingerprint_sha256=fingerprint,
        )
        for result in approvals
    )
    bridge.append(
        BindingPayload(
            binding_kind="commit",
            target=head,
            attestation_id=next(
                item.record_id
                for item in bridge.store.load_chain()
                if item.logical_id == attestation.attestation_id
            ),
            approval_ids=tuple(item.record_id for item in approval_records),
        ),
        logical_id="commit-1",
        idempotency_key="commit:1",
        fingerprint_sha256=fingerprint,
    )
    driver.checkpoint(state, history)
    output = "\n".join(
        (
            "REVIEWER: claude",
            "NEW_FINDING: C-01 | BLOCKER | checkpoint replay gap | "
            "Add a deterministic replay regression test.",
            f"SLICE_APPROVAL: {state.current_slice_id:02d} | NO",
            "STATUS: DONE",
        )
    )
    logical_id = f"review-claude-{state.current_work_unit_id}-1"
    bridge.append(
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id=str(state.current_work_unit_id),
            verdict="denied",
            finding_ids=("A-01", "C-01"),
            evidence=None,
        ),
        logical_id=logical_id,
        idempotency_key=(
            f"parsed:{logical_id}:{fingerprint}:"
            f"{hashlib.sha256(output.encode('utf-8')).hexdigest()}"
        ),
        fingerprint_sha256=fingerprint,
    )
    new_finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="checkpoint replay gap",
        acceptance_test="Add a deterministic replay regression test.",
        origin=FindingOrigin(
            str(state.current_slice_id),
            1,
            AgentRole.CLAUDE,
        ),
    )
    bridge.append(
        finding_payload(new_finding),
        logical_id="finding-C-01",
        idempotency_key="finding:C-01:opened:1:claude",
        fingerprint_sha256=fingerprint,
    )
    bridge.append(
        CorrectionWorkUnitPayload(
            slice_id=str(state.current_slice_id),
            round_number=2,
            paths=state.current_slice.scope_paths,
            finding_ids=("C-01",),
        ),
        logical_id=f"work-unit-{state.current_work_unit_id}",
        idempotency_key=(
            f"correction-work-unit:{state.current_work_unit_id}:round:2"
        ),
        fingerprint_sha256=state.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    driver.log_dir.mkdir(parents=True, exist_ok=True)
    (
        driver.log_dir
        / (
            f"work-unit-{state.current_work_unit_id:04d}-"
            "claude_slice_review.attempt-1.log"
        )
    ).write_text(output + "\n", encoding="utf-8")

    resolution = resolve_resume_state(repository, state)
    assert resolution.record_head_id is not None

    provider_roles: list[AgentRole] = []

    def provider_must_not_review(role, *_args, **_kwargs):
        provider_roles.append(role)
        raise AssertionError("the next Codex correction was intentionally not executed")

    monkeypatch.setattr(driver, "_agent", provider_must_not_review)
    with pytest.raises(
        AssertionError,
        match="next Codex correction was intentionally not executed",
    ):
        WorkflowEngine(driver).run_current_work_unit(
            state,
            WorkflowContext(
                assignment="Recover the interrupted correction review.",
                distilled_plan="Replay only an exactly hash-bound reviewer decision.",
                slice_summary="Correction review",
            ),
            history,
        )

    assert provider_roles == [AgentRole.CODEX]
    assert driver.active_state is not None
    assert driver.active_state.current_step is WorkflowStep.CODEX_FINAL_CORRECTION
    assert driver.active_state.current_work_unit.round_number == 2
    assert driver.active_state.current_work_unit.open_findings == ("C-01",)
    driver.assert_structured_decision_context()


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
            finding_ids=("A-01",),
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
        finding_id="A-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="pre-existing observation",
        acceptance_test="Carry the observation through review.",
        origin=FindingOrigin("1", 1, AgentRole.ANTIGRAVITY),
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
        attestation_payload(attestation),
        logical_id=attestation.attestation_id,
        idempotency_key=f"attestation:{attestation.attestation_id}",
        fingerprint_sha256=attestation_fingerprint or fingerprint,
    )
    bridge.append(
        finding_payload(prior_finding),
        logical_id="finding-A-01",
        idempotency_key="finding:A-01:opened:1:antigravity",
        fingerprint_sha256=fingerprint,
    )
    bridge.append(
        ReviewPayload(
            reviewer=reviewer,
            work_unit_id=str(state.current_work_unit_id),
            verdict=verdict,
            finding_ids=(
                ("A-01",)
                if output_verdict == "approved" and verdict == "approved"
                else ("A-01", "C-01")
            ),
            evidence=(
                "replay identity and binding | stale durable verdict | "
                "the replay record differs from its provider log"
                if verdict == "approved" and output_verdict == "approved"
                else "near-miss evidence"
                if verdict == "approved"
                else None
            ),
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


@pytest.mark.parametrize(
    ("failure_mode", "kwargs"),
    (
        ("reviewer-identity", {"reviewer": Role.ANTIGRAVITY}),
        ("round-number", {"logical_round": 2}),
        ("verdict", {"verdict": "approved"}),
        ("idempotency-prefix", {"malformed_key": True}),
        ("partial-state-mirror", {"mirrored_partial_review": True}),
        ("attestation-fingerprint", {"attestation_fingerprint": "f" * 64}),
    ),
)
def test_pending_reviewer_recovery_rejects_record_and_mirror_near_misses(
    tmp_path: Path,
    failure_mode: str,
    kwargs: dict[str, object],
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    driver, state, _output = _pending_reviewer_recovery_case(repository, **kwargs)

    recovered = driver.recover_pending_reviewer(
        reviewer=AgentRole.CLAUDE,
        work_unit_id=state.current_work_unit_id,
        step=WorkflowStep.CLAUDE_SLICE_REVIEW,
        round_number=1,
    )

    assert recovered is None, failure_mode


def test_pending_reviewer_recovery_rejects_non_unique_hash_bound_logs(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    driver, state, output = _pending_reviewer_recovery_case(repository)
    (
        driver.log_dir
        / f"work-unit-{state.current_work_unit_id:04d}-claude_slice_review.attempt-2.log"
    ).write_text(output + "\n", encoding="utf-8")

    with pytest.raises(WorkflowExecutionError, match="no unique hash-bound provider log"):
        driver.recover_pending_reviewer(
            reviewer=AgentRole.CLAUDE,
            work_unit_id=state.current_work_unit_id,
            step=WorkflowStep.CLAUDE_SLICE_REVIEW,
            round_number=1,
        )


def test_hash_bound_approved_review_replays_to_antigravity_without_claude_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    driver, state, _output = _pending_reviewer_recovery_case(
        repository,
        verdict="approved",
        output_verdict="approved",
    )
    history = WorkflowHistory.from_dict(state.runtime_history["current"])
    provider_roles: list[AgentRole] = []
    checkpoints: list[tuple[WorkflowState, WorkflowHistory]] = []

    def provider_must_not_run(role, *_args, **_kwargs):
        provider_roles.append(role)
        raise AssertionError("Claude provider must not run during approved replay")

    monkeypatch.setattr(driver, "_agent", provider_must_not_run)
    monkeypatch.setattr(
        driver,
        "checkpoint",
        lambda checkpoint_state, checkpoint_history: checkpoints.append(
            (checkpoint_state, checkpoint_history)
        ),
    )
    recovered_state, recovered_history = WorkflowEngine(driver)._run_review(
        state,
        WorkflowContext(
            assignment="Recover the interrupted approved review.",
            distilled_plan="Replay only an exactly hash-bound reviewer decision.",
            slice_summary="Correction review",
        ),
        history,
        AgentRole.CLAUDE,
    )

    assert provider_roles == []
    assert len(checkpoints) == 1
    assert recovered_state.current_step is WorkflowStep.ANTIGRAVITY_SLICE_REVIEW
    assert recovered_history.latest_claude_review is not None
    assert recovered_history.latest_claude_review.approval is True

    changed_fingerprint = "f" * 64
    monkeypatch.setattr(
        driver,
        "collect_changes",
        lambda _start_commit: WorkflowChanges(
            start_commit=recovered_state.current_slice.start_commit,
            fingerprint=changed_fingerprint,
            paths=recovered_state.current_slice.scope_paths,
            full_diff="diff --git a/src/runtime.py b/src/runtime.py\n+changed",
        ),
    )
    rewound_state, _ = WorkflowEngine(driver)._run_review(
        recovered_state,
        WorkflowContext(
            assignment="Review the changed fingerprint.",
            distilled_plan="Require a current Claude approval before Antigravity.",
            slice_summary="Correction review",
        ),
        recovered_history,
        AgentRole.ANTIGRAVITY,
    )
    assert len(checkpoints) == 2
    assert rewound_state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW


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
