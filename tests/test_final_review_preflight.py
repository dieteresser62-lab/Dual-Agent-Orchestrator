from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from artifact_bridge import ArtifactBridge
from artifact_models import (
    AgentResultPayload,
    BindingPayload,
    GatePayload,
    GateTransitionPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ReviewPayload,
    Role,
    SliceBoundaryPayload,
    ValidationAttestationPayload,
    ValidationResult,
    CommandSpec,
    WorkflowCompletionPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    technical_text_evidence,
)
from artifact_store import ArtifactStore
from contracts import PlannedSlice
from final_review_preflight import relevant_record_head, run_final_review_preflight
from workflow_state import (
    AgentFailureKind,
    GateReason,
    InvocationFailureRecord,
    WorkflowStep,
    WorkUnitKind,
    init_workflow_state,
)


FINGERPRINT = "d" * 64
EXTERNAL_FINGERPRINT = "9" * 64
SLICE_COMMIT = "b" * 40
CORRECTION_COMMIT = "c" * 40
SYNTHETIC_TECHNICAL_TEXT = technical_text_evidence(
    "synthetic invocation failure"
)[0]


def _state(step: WorkflowStep):
    state = init_workflow_state(
        run_id="preflight-run", task_file="/repo/task.md", branch="feature/preflight",
        branch_base="a" * 40, slice_count=1,
    ).bind_slice_plan(
        (PlannedSlice(1, "implementation", ("src/one.py",)),),
        first_start_commit="a" * 40,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1, kind=WorkUnitKind.SLICE, step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40, scope_paths=("src/one.py",), start_fingerprint="1" * 64,
    ).complete_current_slice(commit_ref=SLICE_COMMIT).start_final_review_work_unit()
    return state if step is WorkflowStep.CODEX_FINAL_REVIEW else state.with_current_step(step)


def _state_with_approved_external_path():  # type: ignore[no-untyped-def]
    state = init_workflow_state(
        run_id="preflight-run", task_file="/repo/task.md", branch="feature/preflight",
        branch_base="a" * 40, slice_count=1,
    ).bind_slice_plan(
        (PlannedSlice(1, "implementation", ("src/one.py",)),),
        first_start_commit="a" * 40,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1, kind=WorkUnitKind.SLICE, step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40, scope_paths=("src/one.py",), start_fingerprint="1" * 64,
    ).await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="UNEXPECTED-PATH | reviewed external path",
        fingerprint=EXTERNAL_FINGERPRINT,
        paths=("src/external.py",),
    ).record_user_gate_decision(
        approved=True,
        fingerprint=EXTERNAL_FINGERPRINT,
        paths=("src/external.py",),
        rationale="reviewed exact external path",
    ).complete_current_slice(commit_ref=SLICE_COMMIT).start_final_review_work_unit()
    return state


def _state_with_approved_correction_external_path():  # type: ignore[no-untyped-def]
    state = _state(WorkflowStep.CODEX_FINAL_REVIEW).complete_current_work_unit()
    state = state.start_correction_work_unit(
        start_commit=SLICE_COMMIT,
        scope_paths=("src/one.py",),
        start_fingerprint="3" * 64,
        finding_ids=("C-01",),
    )
    failure = InvocationFailureRecord(
        invocation_id="correction-runtime-failure",
        idempotency_key="preflight-run:correction-runtime-failure",
        role="codex",
        failure_kind=AgentFailureKind.RUNTIME,
        provider_text="correction interrupted",
        received_at="2026-08-21T17:59:00+00:00",
        step=WorkflowStep.CODEX_FINAL_CORRECTION,
        slice_id=state.current_slice_id,
        work_unit_id=state.current_work_unit_id,
        diagnostic_exit_code=3,
        process_exit_code=None,
        technical_text=SYNTHETIC_TECHNICAL_TEXT,
        automatic_resume=False,
        diff_fingerprint="8" * 64,
    )
    state = state.record_invocation_failure(
        failure,
        wait_automatically=False,
    ).resume_after_invocation_halt()
    return state.await_user_gate(
        reason=GateReason.QUOTA_RESUME_DIFF,
        detail="QUOTA-RESUME-DIFF | reviewed correction hotfix",
        fingerprint=EXTERNAL_FINGERPRINT,
        paths=("src/external.py",),
        resume_step=WorkflowStep.CODEX_FINAL_CORRECTION,
    ).record_user_gate_decision(
        approved=True,
        fingerprint=EXTERNAL_FINGERPRINT,
        paths=("src/external.py",),
        rationale="reviewed exact correction path",
    ).complete_current_slice(
        commit_ref=CORRECTION_COMMIT,
    ).start_final_review_work_unit()


def _measurement(bridge: ArtifactBridge, state, operation: str):  # type: ignore[no-untyped-def]
    head = relevant_record_head(bridge.store.load_chain())
    payload = ProviderInputMeasurementPayload(
        Role(operation.split("_", 1)[0]), Role(operation.split("_", 1)[0]), operation,
        str(state.current_work_unit_id), "e" * 64, head, "f" * 64, "0" * 64,
        (ProviderInputComponentPayload("stdin_prompt", 10, 10),), 10, 10,
        100, 100, None, None, None, 100, 100, True, (), 0, 0, "stdin_prompt",
    )
    return bridge.append(
        payload, logical_id=f"measurement-{operation}", idempotency_key=f"measurement:{operation}",
        fingerprint_sha256=FINGERPRINT,
    )


def _attest(bridge: ArtifactBridge) -> None:
    bridge.append(
        ValidationAttestationPayload(
            (ValidationResult(CommandSpec("pytest", ("pytest",)), "pass", 0, "1" * 64),),
            Role.ORCHESTRATOR,
            "1" * 64,
            "ar1-" + "0" * 64,
        ),
        logical_id="final-validation", idempotency_key="final-validation",
        fingerprint_sha256=FINGERPRINT,
    )


def _append_external_path_evidence(
    bridge: ArtifactBridge,
    *,
    include_gate: bool = True,
    binding_target: str = SLICE_COMMIT,
    gate_kind: str = "unexpected-file",
) -> None:
    attestation = bridge.append(
        ValidationAttestationPayload(
            (ValidationResult(CommandSpec("pytest", ("pytest",)), "pass", 0, "2" * 64),),
            Role.ORCHESTRATOR,
            "2" * 64,
            "ar1-" + "0" * 64,
        ),
        logical_id="slice-validation",
        idempotency_key="slice-validation",
        fingerprint_sha256=EXTERNAL_FINGERPRINT,
    )
    claude = bridge.append(
        ReviewPayload(Role.CLAUDE, "2", "approved", (), "checked", "native-claude-review-v2", "native-review-request-" + "b" * 64, "c" * 64),
        logical_id="slice-claude",
        idempotency_key="slice-claude",
        fingerprint_sha256=EXTERNAL_FINGERPRINT,
    )
    if include_gate:
        bridge.append(
            GatePayload(
                gate_kind, "approved", Role.USER,
                "reviewed exact external path",
            ),
            logical_id="slice-user-gate",
            idempotency_key="slice-user-gate",
            fingerprint_sha256=EXTERNAL_FINGERPRINT,
        )
    bridge.append(
        BindingPayload(
            "commit",
            binding_target,
            attestation.record_id,
            (claude.record_id,),
        ),
        logical_id="slice-commit-binding",
        idempotency_key="slice-commit-binding",
        fingerprint_sha256=EXTERNAL_FINGERPRINT,
    )


@pytest.mark.parametrize(
    ("step", "prior_kind"),
    [
        (WorkflowStep.CODEX_FINAL_REVIEW, None),
        (WorkflowStep.CLAUDE_FINAL_REVIEW, "codex"),
    ],
)
def test_each_final_transition_accepts_only_currently_available_facts(
    tmp_path: Path, step: WorkflowStep, prior_kind: str | None,
) -> None:
    state = _state(step)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    _attest(bridge)
    if prior_kind == "codex":
        bridge.append(
            AgentResultPayload(Role.CODEX, str(state.current_work_unit_id), "ready", (), "native-codex-v2", "native-codex-request-" + "b" * 64, "c" * 64),
            logical_id="codex-final", idempotency_key="codex-final", fingerprint_sha256=FINGERPRINT,
        )
    elif prior_kind == "claude":
        bridge.append(
            ReviewPayload(Role.CLAUDE, str(state.current_work_unit_id), "approved", (), "checked", "native-claude-review-v2", "native-review-request-" + "b" * 64, "c" * 64),
            logical_id="claude-final", idempotency_key="claude-final", fingerprint_sha256=FINGERPRINT,
        )
    measurement = _measurement(bridge, state, step.value)

    result = run_final_review_preflight(
        state=state, records=bridge.store.load_chain(), measurement_record=measurement,
        repository_paths=("src/one.py",),
    )

    assert result.passed


def test_preflight_matches_repository_paths_against_task_scope_globs(
    tmp_path: Path,
) -> None:
    state = replace(
        _state(WorkflowStep.CODEX_FINAL_REVIEW), task_scope_patterns=("src/**",)
    )
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    _attest(bridge)
    measurement = _measurement(bridge, state, state.current_step.value)

    result = run_final_review_preflight(
        state=state,
        records=bridge.store.load_chain(),
        measurement_record=measurement,
        repository_paths=("src/nested/one.py",),
    )

    assert result.passed


def test_preflight_deduplicates_overlapping_task_and_slice_scope(
    tmp_path: Path,
) -> None:
    state = replace(
        _state(WorkflowStep.CODEX_FINAL_REVIEW),
        task_scope_patterns=("src/one.py",),
    )
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    _attest(bridge)
    measurement = _measurement(bridge, state, state.current_step.value)

    result = run_final_review_preflight(
        state=state,
        records=bridge.store.load_chain(),
        measurement_record=measurement,
        repository_paths=("src/one.py",),
    )

    assert result.passed


def test_preflight_accepts_exact_current_final_review_gate_paths(
    tmp_path: Path,
) -> None:
    state = _state(WorkflowStep.CODEX_FINAL_REVIEW)
    failure = InvocationFailureRecord(
        invocation_id="final-review-runtime-failure",
        idempotency_key="preflight-run:2:codex_final_review:codex",
        role="codex",
        failure_kind=AgentFailureKind.RUNTIME,
        provider_text="validation path patterns must be unique",
        received_at="2026-08-21T09:59:00+00:00",
        step=WorkflowStep.CODEX_FINAL_REVIEW,
        slice_id=state.current_slice_id,
        work_unit_id=state.current_work_unit_id,
        diagnostic_exit_code=3,
        process_exit_code=None,
        technical_text=SYNTHETIC_TECHNICAL_TEXT,
        automatic_resume=False,
        diff_fingerprint="c" * 64,
    )
    state = state.record_invocation_failure(
        failure,
        wait_automatically=False,
    ).resume_after_invocation_halt().await_user_gate(
        reason=GateReason.QUOTA_RESUME_DIFF,
        detail="QUOTA-RESUME-DIFF | reviewed final-review hotfix",
        fingerprint=FINGERPRINT,
        paths=("src/final_review_preflight.py",),
        resume_step=WorkflowStep.CODEX_FINAL_REVIEW,
    ).record_user_gate_decision(
        approved=True,
        fingerprint=FINGERPRINT,
        paths=("src/final_review_preflight.py",),
        rationale="reviewed exact final-review hotfix",
    )
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    bridge.append(
        GatePayload(
            "quota-resume-diff",
            "approved",
            Role.USER,
            "reviewed exact final-review hotfix",
        ),
        logical_id="final-review-user-gate",
        idempotency_key="final-review-user-gate",
        fingerprint_sha256=FINGERPRINT,
    )
    _attest(bridge)
    measurement = _measurement(bridge, state, state.current_step.value)

    result = run_final_review_preflight(
        state=state,
        records=bridge.store.load_chain(),
        measurement_record=measurement,
        repository_paths=("src/one.py", "src/final_review_preflight.py"),
    )

    assert result.passed


def test_preflight_denies_missing_attestation_unexpected_path_and_premature_completion(tmp_path: Path) -> None:
    state = _state(WorkflowStep.CODEX_FINAL_REVIEW)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    measurement = _measurement(bridge, state, state.current_step.value)
    missing = run_final_review_preflight(
        state=state, records=bridge.store.load_chain(), measurement_record=measurement,
    )
    assert missing.error_code == "ATTESTATION-MISSING"

    _attest(bridge)
    unauthorized = run_final_review_preflight(
        state=state, records=bridge.store.load_chain(), measurement_record=measurement,
        repository_paths=("secrets.txt",),
    )
    assert unauthorized.error_code == "UNAUTHORIZED-PATH"

    bridge.append(
        WorkflowCompletionPayload("completed", "binding-placeholder"),
        logical_id="premature-completion", idempotency_key="premature-completion",
        fingerprint_sha256=FINGERPRINT,
    )
    premature = run_final_review_preflight(
        state=state, records=bridge.store.load_chain(), measurement_record=measurement,
    )
    assert premature.error_code == "PREMATURE-COMPLETION"


def test_preflight_accepts_user_approved_external_path_bound_to_completed_slice_commit(
    tmp_path: Path,
) -> None:
    state = _state_with_approved_external_path()
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    _append_external_path_evidence(bridge)
    _attest(bridge)
    measurement = _measurement(bridge, state, state.current_step.value)

    result = run_final_review_preflight(
        state=state,
        records=bridge.store.load_chain(),
        measurement_record=measurement,
        repository_paths=("src/one.py", "src/external.py"),
    )

    assert result.passed


def test_preflight_accepts_user_approved_external_path_bound_to_correction_commit(
    tmp_path: Path,
) -> None:
    state = _state_with_approved_correction_external_path()
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    _append_external_path_evidence(
        bridge,
        binding_target=CORRECTION_COMMIT,
        gate_kind="quota-resume-diff",
    )
    _attest(bridge)
    measurement = _measurement(bridge, state, state.current_step.value)

    result = run_final_review_preflight(
        state=state,
        records=bridge.store.load_chain(),
        measurement_record=measurement,
        repository_paths=("src/one.py", "src/external.py"),
    )

    assert result.passed


@pytest.mark.parametrize(
    ("include_gate", "binding_target"),
    [
        (False, SLICE_COMMIT),
        (True, "c" * 40),
    ],
)
def test_preflight_rejects_external_path_without_matching_gate_and_commit_binding(
    tmp_path: Path,
    include_gate: bool,
    binding_target: str,
) -> None:
    state = _state_with_approved_external_path()
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    _append_external_path_evidence(
        bridge,
        include_gate=include_gate,
        binding_target=binding_target,
    )
    _attest(bridge)
    measurement = _measurement(bridge, state, state.current_step.value)

    result = run_final_review_preflight(
        state=state,
        records=bridge.store.load_chain(),
        measurement_record=measurement,
        repository_paths=("src/one.py", "src/external.py"),
    )

    assert result.error_code == "UNAUTHORIZED-PATH"
    assert result.affected_paths == ("src/external.py",)


@pytest.mark.parametrize("wrong_reference", ["attestation", "approval"])
def test_preflight_rejects_binding_reference_with_wrong_payload_type(
    tmp_path: Path,
    wrong_reference: str,
) -> None:
    state = _state(WorkflowStep.CODEX_FINAL_REVIEW)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    attestation = bridge.append(
        ValidationAttestationPayload(
            (ValidationResult(CommandSpec("pytest", ("pytest",)), "pass", 0, "2" * 64),),
            Role.ORCHESTRATOR,
            "2" * 64,
            "ar1-" + "0" * 64,
        ),
        logical_id="binding-attestation",
        idempotency_key="binding-attestation",
        fingerprint_sha256=FINGERPRINT,
    )
    approval = bridge.append(
        ReviewPayload(Role.CLAUDE, "2", "approved", (), "checked", "native-claude-review-v2", "native-review-request-" + "b" * 64, "c" * 64),
        logical_id="binding-approval",
        idempotency_key="binding-approval",
        fingerprint_sha256=FINGERPRINT,
    )
    wrong_type = bridge.append(
        AgentResultPayload(Role.CODEX, "2", "ready", (), "native-codex-v2", "native-codex-request-" + "b" * 64, "c" * 64),
        logical_id="binding-wrong-type",
        idempotency_key="binding-wrong-type",
        fingerprint_sha256=FINGERPRINT,
    )
    binding = bridge.append(
        BindingPayload(
            "commit",
            SLICE_COMMIT,
            wrong_type.record_id if wrong_reference == "attestation" else attestation.record_id,
            (wrong_type.record_id if wrong_reference == "approval" else approval.record_id,),
        ),
        logical_id="typed-binding",
        idempotency_key="typed-binding",
        fingerprint_sha256=FINGERPRINT,
    )
    measurement = _measurement(bridge, state, state.current_step.value)

    result = run_final_review_preflight(
        state=state,
        records=bridge.store.load_chain(),
        measurement_record=measurement,
        repository_paths=("src/one.py",),
    )

    assert result.error_code == "MISSING-REFERENCE"
    assert result.affected_record_ids == (binding.record_id, wrong_type.record_id)


def test_preflight_rejects_binding_reference_that_is_not_a_predecessor(
    tmp_path: Path,
) -> None:
    state = _state(WorkflowStep.CODEX_FINAL_REVIEW)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    _append_external_path_evidence(bridge)
    _attest(bridge)
    measurement = _measurement(bridge, state, state.current_step.value)
    records = list(bridge.store.load_chain())
    binding_index = next(
        index for index, item in enumerate(records)
        if isinstance(item.payload, BindingPayload)
    )
    approval_index = next(
        index for index, item in enumerate(records)
        if isinstance(item.payload, ReviewPayload)
    )
    records[binding_index], records[approval_index] = (
        records[approval_index],
        records[binding_index],
    )

    result = run_final_review_preflight(
        state=state,
        records=records,
        measurement_record=measurement,
        repository_paths=("src/one.py",),
    )

    assert result.error_code == "MISSING-REFERENCE"
    binding = records[approval_index]
    assert isinstance(binding.payload, BindingPayload)
    assert result.affected_record_ids[0] == binding.record_id
    assert set(result.affected_record_ids[1:]) == set(binding.payload.approval_ids)


def test_relevant_record_head_excludes_r2_r5_dispatch_context_but_includes_r3_boundary(
    tmp_path: Path,
) -> None:
    state = _state(WorkflowStep.CODEX_FINAL_REVIEW)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    before = relevant_record_head(bridge.store.load_chain())
    _measurement(bridge, state, state.current_step.value)
    assert relevant_record_head(bridge.store.load_chain()) == before
    bridge.append(
        WorkflowTransitionPayload(
            str(state.current_slice_id),
            state.current_slice.status.value,
            str(state.current_work_unit_id),
            state.current_step.value,
            state.current_work_unit.status.value,
        ),
        logical_id="workflow-transition",
        idempotency_key="workflow-transition:1",
        fingerprint_sha256=FINGERPRINT,
    )
    bridge.append(
        WorkflowPolicyPayload(str(state.current_work_unit_id), 0, 4),
        logical_id=f"workflow-policy-{state.current_work_unit_id}",
        idempotency_key=f"workflow-policy:{state.current_work_unit_id}:1",
        fingerprint_sha256=FINGERPRINT,
    )
    unit = state.current_work_unit
    bridge.append(
        GateTransitionPayload(
            str(unit.work_unit_id),
            unit.gate.status.value,
            unit.gate.reason.value,
            unit.gate.detail,
            unit.gate.fingerprint,
            unit.gate.paths,
            None if unit.gate.resume_step is None else unit.gate.resume_step.value,
            unit.active_test_fingerprint,
            unit.active_test_paths,
        ),
        logical_id=f"gate-transition-{unit.work_unit_id}",
        idempotency_key=f"gate-transition:{unit.work_unit_id}:1",
        fingerprint_sha256=FINGERPRINT,
    )
    assert relevant_record_head(bridge.store.load_chain()) == before
    bridge.append(
        SliceBoundaryPayload(
            str(state.current_slice_id),
            state.current_slice.start_commit or SLICE_COMMIT,
            state.current_slice.scope_change_groups,
            state.current_slice.start_fingerprint or FINGERPRINT,
        ),
        logical_id=f"slice-boundary-{state.current_slice_id}",
        idempotency_key=f"slice-boundary:{state.current_slice_id}:1",
        fingerprint_sha256=FINGERPRINT,
    )
    assert relevant_record_head(bridge.store.load_chain()) != before
