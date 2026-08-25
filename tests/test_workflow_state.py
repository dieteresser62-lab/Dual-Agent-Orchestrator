from __future__ import annotations

from dataclasses import replace

import pytest

from contracts import CodexStepContract, PlannedSlice, ReadinessMarker
from native_codex_contract import NativeCodexRequestKind
from workflow import WorkflowChanges, WorkflowContext, WorkflowEngine, WorkflowHistory

from workflow_state import (
    AgentFailureKind,
    DEFAULT_MAX_CODEX_RETURNS,
    GateRecord,
    GateDecisionRecord,
    GateReason,
    GateStatus,
    InvocationFailureRecord,
    ProtocolBinding,
    ProtocolMode,
    Reviewer,
    SliceStatus,
    WorkflowState,
    WorkflowStateValidationError,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
    BootstrapCheckFact,
)


def make_state():
    return init_workflow_state(
        run_id="run-1",
        task_file="/repo/task.md",
        branch="feature/state-v3",
        branch_base="a" * 40,
        slice_count=3,
        timestamp="2026-08-11T10:00:00+00:00",
    )


def test_init_workflow_state_uses_v3_and_one_based_ids() -> None:
    state = make_state()

    assert state.version == 3
    assert tuple(item.slice_id for item in state.slices) == (1, 2, 3)
    assert tuple(item.work_unit_id for item in state.work_units) == (1,)
    assert state.current_slice_id == 1
    assert state.current_work_unit_id == 1
    assert state.current_step is WorkflowStep.CODEX_PLAN
    assert state.current_work_unit.round_number == 1
    assert state.current_work_unit.codex_return_count == 0
    assert state.current_work_unit.max_codex_returns == DEFAULT_MAX_CODEX_RETURNS
    assert state.current_work_unit.gate.status is GateStatus.CLEAR
    assert state.branch_base == "a" * 40
    assert state.current_slice.start_commit == "a" * 40
    assert state.current_slice.commit_ref is None
    assert state.slices[1].start_commit is None


def test_bootstrap_facts_roundtrip_idempotently_and_use_a_resume_gate() -> None:
    fact = BootstrapCheckFact(
        "provider_input_measurement", "a" * 64, "codex", "codex",
        "codex_plan", 1, "b" * 64, "allowed",
    )
    state = make_state().with_bootstrap_check(fact).with_bootstrap_check(fact)
    halted = state.await_bootstrap_resume(
        detail="PROVIDER-INPUT-BUDGET | chars exceeded",
        fingerprint="c" * 64,
        paths=("src/external.py",),
    )

    assert WorkflowState.from_dict(state.to_dict()).bootstrap_checks == (fact,)
    assert halted.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    assert halted.current_work_unit.gate.reason is GateReason.BOOTSTRAP_CHECK
    assert halted.current_work_unit.gate.paths == ("src/external.py",)
    assert halted.resume_after_invocation_halt().current_step is WorkflowStep.CODEX_PLAN


def test_stop_request_resume_starts_a_new_semantic_round() -> None:
    state = make_state().with_current_step(WorkflowStep.CODEX_PLAN_REVISION)
    halted = state.await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail="UNEXPECTED-PATH | exact authorization is required",
    )

    resumed = halted.resume_after_user_decision()

    assert resumed.current_step is WorkflowStep.CODEX_PLAN_REVISION
    assert resumed.current_work_unit.round_number == 2
    assert resumed.current_work_unit.gate.status is GateStatus.CLEAR


def test_codex_scope_uses_only_matching_fingerprint_bound_resume_approval() -> None:
    path = "src/runtime-hotfix.py"
    decision = GateDecisionRecord(
        approved=True,
        reason=GateReason.UNEXPECTED_FILE,
        fingerprint="b" * 64,
        paths=(path,),
        decided_by="architect",
        decided_at="2026-08-23T13:00:00+00:00",
        rationale="Reviewed bootstrap hotfix.",
        resume_step=WorkflowStep.CODEX_PLAN_REVISION,
    )
    state = make_state().with_current_step(WorkflowStep.CODEX_PLAN_REVISION)
    state = state._replace_current_unit(
        replace(state.current_work_unit, gate_decisions=(decision,))
    )

    class Driver:
        @staticmethod
        def collect_changes(_start_commit: str):  # type: ignore[no-untyped-def]
            return type("Changes", (), {"fingerprint": "b" * 64})()

    engine = WorkflowEngine(Driver())  # type: ignore[arg-type]

    assert engine._fingerprint_bound_codex_scope_paths(state) == (path,)

    class DriftedDriver:
        @staticmethod
        def collect_changes(_start_commit: str):  # type: ignore[no-untyped-def]
            return type("Changes", (), {"fingerprint": "c" * 64})()

    drifted = WorkflowEngine(DriftedDriver())  # type: ignore[arg-type]
    assert drifted._fingerprint_bound_codex_scope_paths(state) == ()


def test_native_codex_request_projects_fingerprint_bound_paths_and_explanation() -> None:
    state = init_workflow_state(
        run_id="native-scope",
        task_file="/repo/task.md",
        branch="feature/native-scope",
        branch_base="a" * 40,
        slice_count=1,
        task_digest="b" * 64,
        task_scope_patterns=("docs/internal/plan.md",),
        target_branch="feature/native-scope",
        timestamp="2026-08-23T13:00:00+00:00",
    )
    context = WorkflowContext(
        assignment="Create the plan.",
        distilled_plan="Use the persisted task contract.",
        slice_summary="Plan the work.",
    )
    contract = CodexStepContract(
        "native-plan",
        ReadinessMarker.PLAN,
        "01",
        1,
        require_slice_plan=True,
        plan_artifact_path="docs/internal/plan.md",
    )

    bundle = WorkflowEngine._native_codex_request(
        state=state,
        context=context,
        history=WorkflowHistory(state.current_work_unit_id),
        contract=contract,
        prompt="Create the bound plan.",
        request_kind=NativeCodexRequestKind.PLAN,
        additional_authorized_paths=("src/runtime-hotfix.py",),
    )

    assert bundle.document["authorized_paths"] == [
        "docs/internal/plan.md",
        "src/runtime-hotfix.py",
    ]
    assert "FINGERPRINT-BOUND ORCHESTRATOR PATH AUTHORIZATION" in bundle.document[
        "work_context"
    ]
    assert "not an UNEXPECTED-PATH condition" in bundle.document["work_context"]


def test_legacy_unexpected_path_stop_becomes_fingerprint_bound_user_gate() -> None:
    state = (
        make_state()
        .complete_current_work_unit()
        .start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_CORRECTION,
        )
        .bind_current_slice_git_boundary(
            start_commit="a" * 40,
            scope_paths=("src/allowed.py",),
            start_fingerprint="0" * 64,
        )
        .await_policy_gate(
            reason=GateReason.STOP_REQUEST,
            detail="UNEXPECTED-PATH | Codex requested exact scope authorization.",
        )
    )

    class Driver:
        @staticmethod
        def collect_changes(_start_commit: str) -> WorkflowChanges:
            return WorkflowChanges(
                start_commit="a" * 40,
                fingerprint="b" * 64,
                paths=("src/allowed.py", "src/runtime-hotfix.py"),
                full_diff="diff --git a/src/runtime-hotfix.py b/src/runtime-hotfix.py",
            )

    reframed = WorkflowEngine(Driver()).reframe_unexpected_path_stop_gate(  # type: ignore[arg-type]
        state
    )

    assert reframed.current_work_unit.round_number == 2
    assert reframed.current_work_unit.gate.reason is GateReason.UNEXPECTED_FILE
    assert reframed.current_work_unit.gate.fingerprint == "b" * 64
    assert reframed.current_work_unit.gate.paths == ("src/runtime-hotfix.py",)
    assert reframed.current_work_unit.gate.resume_step is WorkflowStep.CODEX_CORRECTION


def test_plan_scope_stop_becomes_post_revision_fingerprint_gate() -> None:
    plan_path = "docs/internal/work-plan.md"
    hotfix_path = "src/runtime-hotfix.py"
    state = init_workflow_state(
        run_id="run-plan-scope-reframe",
        task_file="/repo/inbox/plan.md",
        branch="feature/plan-scope-reframe",
        branch_base="a" * 40,
        slice_count=1,
        task_scope_patterns=(plan_path,),
    ).with_current_step(WorkflowStep.CODEX_PLAN_REVISION).await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail=(
            "PLAN-CONTRACT-INVALID | The work plan is not safe to hand to "
            "reviewers or implementation: internal plan validation found "
            f"out-of-scope planning changes: {hotfix_path}"
        ),
    )

    class Driver:
        @staticmethod
        def collect_changes(_start_commit: str) -> WorkflowChanges:
            return WorkflowChanges(
                start_commit="a" * 40,
                fingerprint="c" * 64,
                paths=(plan_path, hotfix_path),
                full_diff="diff --git a/src/runtime-hotfix.py b/src/runtime-hotfix.py",
            )

    reframed = WorkflowEngine(Driver()).reframe_unexpected_path_stop_gate(  # type: ignore[arg-type]
        state
    )

    assert reframed.current_work_unit.gate.reason is GateReason.UNEXPECTED_FILE
    assert reframed.current_work_unit.gate.fingerprint == "c" * 64
    assert reframed.current_work_unit.gate.paths == (hotfix_path,)
    assert reframed.current_step is WorkflowStep.CLAUDE_PLAN_REVIEW
    assert (
        reframed.current_work_unit.gate.resume_step
        is WorkflowStep.CLAUDE_PLAN_REVIEW
    )


def test_plan_pre_review_scope_drift_gates_without_reinvoking_codex() -> None:
    plan_path = "docs/internal/work-plan.md"
    hotfix_path = "src/runtime-hotfix.py"
    state = init_workflow_state(
        run_id="run-plan-pre-review-gate",
        task_file="/repo/inbox/plan.md",
        branch="feature/plan-pre-review-gate",
        branch_base="a" * 40,
        slice_count=1,
        task_scope_patterns=(plan_path,),
    ).with_current_step(WorkflowStep.CODEX_PLAN_REVISION)
    checkpoints: list[WorkflowState] = []

    class Driver:
        @staticmethod
        def collect_changes(_start_commit: str) -> WorkflowChanges:
            return WorkflowChanges(
                start_commit="a" * 40,
                fingerprint="d" * 64,
                paths=(plan_path, hotfix_path),
                full_diff="diff --git a/src/runtime-hotfix.py b/src/runtime-hotfix.py",
            )

        @staticmethod
        def validate_plan(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("validation must wait for the exact path gate")

        @staticmethod
        def checkpoint(
            checkpoint_state: WorkflowState, _history: WorkflowHistory
        ) -> None:
            checkpoints.append(checkpoint_state)

    context = WorkflowContext(
        assignment="Create the work plan.",
        distilled_plan="Keep the task scope exact.",
        slice_summary="Revise the plan after review.",
        plan_only=True,
        task_scope_patterns=(plan_path,),
        work_plan_path=plan_path,
    )
    engine = WorkflowEngine(Driver())  # type: ignore[arg-type]

    gated, history, halted = engine._validate_plan_before_review(
        state,
        context,
        WorkflowHistory(state.current_work_unit_id),
    )

    assert halted
    assert history == WorkflowHistory(state.current_work_unit_id)
    assert checkpoints == [gated]
    assert gated.current_step is WorkflowStep.CLAUDE_PLAN_REVIEW
    assert gated.current_work_unit.gate.reason is GateReason.UNEXPECTED_FILE
    assert gated.current_work_unit.gate.paths == (hotfix_path,)
    assert (
        gated.current_work_unit.gate.resume_step
        is WorkflowStep.CLAUDE_PLAN_REVIEW
    )


def test_hardened_task_contract_roundtrips_in_state() -> None:
    state = init_workflow_state(
        run_id="run-contract",
        task_file="/repo/task.md",
        branch="feature/plan",
        branch_base="a" * 40,
        slice_count=1,
        task_digest="b" * 64,
        execution_mode="PLAN_ONLY",
        task_scope_patterns=("docs/internal/plan.md",),
        work_plan_path="docs/internal/plan.md",
        approved_plan_commit="c" * 40,
        target_branch="feature/plan",
        timestamp="2026-08-12T10:00:00+00:00",
    )

    restored = WorkflowState.from_dict(state.to_dict())

    assert restored == state
    assert restored.execution_mode == "PLAN_ONLY"
    assert restored.task_digest == "b" * 64
    assert restored.approved_plan_commit == "c" * 40


def test_protocol_binding_roundtrips_and_missing_binding_is_legacy() -> None:
    historical = make_state()
    assert historical.protocol_binding is None
    assert historical.effective_protocol_mode is ProtocolMode.LEGACY_STATE_V3
    old_document = historical.to_dict()
    old_document.pop("protocol_binding")
    restored_historical = WorkflowState.from_dict(old_document)
    assert restored_historical.protocol_binding is None
    assert restored_historical.protocol_mode is ProtocolMode.LEGACY_STATE_V3

    structured = replace(
        historical,
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    )
    assert WorkflowState.from_dict(structured.to_dict()) == structured
    assert structured.effective_protocol_mode is ProtocolMode.STRUCTURED_V2

    native = replace(
        historical,
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            "native-claude-review-v2",
        ),
    )
    assert WorkflowState.from_dict(native.to_dict()) == native


def test_protocol_binding_rejects_native_transport_outside_structured_v1() -> None:
    with pytest.raises(
        WorkflowStateValidationError,
        match="requires structured-v2",
    ):
        ProtocolBinding(
            ProtocolMode.LEGACY_STATE_V3,
            "3",
            "native-claude-review-v2",
        )

    with pytest.raises(
        WorkflowStateValidationError,
        match="requires structured-v2",
    ):
        ProtocolBinding(
            ProtocolMode.LEGACY_STATE_V3,
            "3",
            codex_result_transport="native-codex-v2",
        )


def test_protocol_binding_roundtrips_native_codex_result_transport() -> None:
    binding = ProtocolBinding(
        ProtocolMode.STRUCTURED_V2,
        "2",
        codex_result_transport="native-codex-v2",
    )

    assert ProtocolBinding.from_dict(binding.to_dict()) == binding


@pytest.mark.parametrize(
    ("mode", "schema_version"),
    [(ProtocolMode.STRUCTURED_V2, "3"), (ProtocolMode.LEGACY_STATE_V3, "1")],
)
def test_protocol_binding_rejects_mode_schema_mismatch(
    mode: ProtocolMode, schema_version: str
) -> None:
    with pytest.raises(WorkflowStateValidationError, match="requires schema_version"):
        ProtocolBinding(mode, schema_version)


@pytest.mark.parametrize("slice_count", [0, -1, True])
def test_init_rejects_non_one_based_slice_count(slice_count: int) -> None:
    with pytest.raises(WorkflowStateValidationError, match="slice_count"):
        init_workflow_state(
            run_id="run-1",
            task_file="/repo/task.md",
            branch="feature/state-v3",
            branch_base="abc123",
            slice_count=slice_count,
        )


def test_state_rejects_non_contiguous_or_mismatched_ids() -> None:
    state = make_state()
    bad_slices = (replace(state.slices[0], slice_id=2), *state.slices[1:])
    with pytest.raises(WorkflowStateValidationError, match="slice ids"):
        replace(state, slices=bad_slices)

    with pytest.raises(WorkflowStateValidationError, match="current_step"):
        replace(state, current_step=WorkflowStep.CLAUDE_PLAN_REVIEW)


@pytest.mark.parametrize("step", list(WorkflowStep))
def test_resume_cursor_preserves_every_persistable_step(step: WorkflowStep) -> None:
    state = make_state()
    unit = replace(state.current_work_unit, current_step=step)
    state_at_step = replace(state, current_step=step, work_units=(unit,))

    loaded = WorkflowState.from_dict(state_at_step.to_dict())
    cursor = loaded.resume_cursor()

    assert cursor.work_unit_id == 1
    assert cursor.slice_id == 1
    assert cursor.step is step
    assert cursor.round_number == 1


def test_completed_side_effect_is_persisted_and_idempotent() -> None:
    state = make_state()
    updated = state.mark_side_effect_completed("review:claude:round-1", updated_at="later")
    repeated = updated.mark_side_effect_completed("review:claude:round-1", updated_at="latest")
    loaded = WorkflowState.from_dict(updated.to_dict())

    assert repeated is updated
    assert loaded.updated_at == "later"
    assert loaded.current_work_unit.completed_side_effects == ("review:claude:round-1",)
    assert loaded.resume_cursor().should_execute("review:claude:round-1") is False
    assert loaded.resume_cursor().should_execute("review:antigravity:round-1") is True


def test_fourth_review_denial_enters_user_gate_without_reset() -> None:
    state = make_state()
    for expected_count in range(1, DEFAULT_MAX_CODEX_RETURNS + 1):
        state = state.record_review_denial(
            reviewer=Reviewer.CLAUDE,
            open_findings=("C-01",),
            return_step=WorkflowStep.CODEX_PLAN_REVISION,
            updated_at=f"round-{expected_count}",
        )
        assert state.current_work_unit.codex_return_count == expected_count

    unit = state.current_work_unit
    assert unit.round_number == DEFAULT_MAX_CODEX_RETURNS
    assert unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert unit.gate.status is GateStatus.AWAITING_USER_DECISION
    assert unit.gate.reason is GateReason.ITERATION_LIMIT
    assert unit.reviewer is Reviewer.CLAUDE
    assert unit.open_findings == ("C-01",)
    assert state.current_step is WorkflowStep.CODEX_PLAN_REVISION
    assert state.current_slice.status is SliceStatus.AWAITING_USER_DECISION
    assert state.current_slice.commit_ref is None


def test_review_denial_can_advance_past_return_limit_number_after_stop_rounds() -> None:
    base = make_state()
    state = replace(
        base,
        current_step=WorkflowStep.CLAUDE_PLAN_REVIEW,
        work_units=(
            replace(
                base.current_work_unit,
                current_step=WorkflowStep.CLAUDE_PLAN_REVIEW,
                round_number=DEFAULT_MAX_CODEX_RETURNS,
                codex_return_count=2,
            ),
        ),
    )

    denied = state.record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=("C-01",),
        return_step=WorkflowStep.CODEX_PLAN_REVISION,
    )

    assert denied.current_work_unit.round_number == DEFAULT_MAX_CODEX_RETURNS + 1
    assert denied.current_work_unit.codex_return_count == 3
    assert denied.current_work_unit.max_codex_returns == DEFAULT_MAX_CODEX_RETURNS
    assert denied.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert denied.current_step is WorkflowStep.CODEX_PLAN_REVISION


def test_quota_failure_roundtrips_and_resumes_exact_failed_step() -> None:
    state = make_state()
    failure = InvocationFailureRecord(
        invocation_id="inv-quota-1",
        idempotency_key="run-1:1:codex_plan:codex",
        role="codex",
        failure_kind=AgentFailureKind.QUOTA,
        provider_text="usage cap reached; retry in 60 seconds",
        received_at="2026-08-12T10:00:00+00:00",
        step=WorkflowStep.CODEX_PLAN,
        slice_id=1,
        work_unit_id=1,
        diagnostic_exit_code=2,
        parse_path="codex:text:relative",
        source_timezone="UTC",
        reset_at_utc="2026-08-12T10:01:00+00:00",
        resume_at_utc="2026-08-12T10:01:30+00:00",
        safety_margin_seconds=30,
        auto_resume_count=1,
        automatic_resume=True,
    )

    waiting = state.record_invocation_failure(failure, wait_automatically=True)
    loaded = WorkflowState.from_dict(waiting.to_dict())

    assert loaded.current_work_unit.status is WorkUnitStatus.WAITING_FOR_QUOTA
    assert loaded.current_slice.status is SliceStatus.WAITING_FOR_QUOTA
    assert loaded.current_work_unit.gate.status is GateStatus.WAITING_FOR_QUOTA
    assert loaded.current_work_unit.invocation_failures == (failure,)
    resumed = loaded.resume_after_invocation_halt(updated_at="2026-08-12T10:01:30+00:00")
    assert resumed.current_step is WorkflowStep.CODEX_PLAN
    assert resumed.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert resumed.current_work_unit.invocation_failures == (failure,)


def test_legacy_quota_resume_diff_gate_reopens_for_fingerprint_revalidation() -> None:
    failure = InvocationFailureRecord(
        invocation_id="inv-quota-diff",
        idempotency_key="run-1:1:codex_plan:codex",
        role="codex",
        failure_kind=AgentFailureKind.QUOTA,
        provider_text="usage cap reached",
        received_at="2026-08-12T10:00:00+00:00",
        step=WorkflowStep.CODEX_PLAN,
        slice_id=1,
        work_unit_id=1,
        diagnostic_exit_code=2,
        automatic_resume=False,
        diff_fingerprint="1" * 64,
    )
    halted = make_state().record_invocation_failure(
        failure, wait_automatically=False
    )
    active = halted.resume_after_invocation_halt()
    detail = (
        "QUOTA-RESUME-DIFF | repository changed while the role was waiting; "
        f"expected {'1' * 64}, got {'2' * 64}"
    )
    first_gate = active.await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail=detail,
        paths=("src/runtime.py",),
    )
    repeated_gate = first_gate.resume_after_user_decision().await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail=detail,
        paths=("src/runtime.py",),
    )

    reopened = repeated_gate.reopen_legacy_quota_resume_diff_gate()

    assert reopened.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    assert reopened.current_work_unit.gate.reason is GateReason.QUOTA
    assert reopened.current_work_unit.gate.resume_step is WorkflowStep.CODEX_PLAN
    assert not any(
        item.startswith("quota-resume-diff:inv-quota-diff:")
        for item in reopened.current_work_unit.completed_side_effects
    )
    revalidating = reopened.resume_after_invocation_halt()
    assert revalidating.current_step is WorkflowStep.CODEX_PLAN

    rebound = revalidating.await_user_gate(
        reason=GateReason.QUOTA_RESUME_DIFF,
        detail=detail,
        fingerprint="3" * 64,
        paths=("src/runtime.py",),
        resume_step=WorkflowStep.CODEX_PLAN,
    )
    with pytest.raises(WorkflowStateValidationError, match="explicit recorded"):
        rebound.resume_after_user_decision()
    approved = rebound.record_user_gate_decision(
        approved=True,
        fingerprint="3" * 64,
        paths=("src/runtime.py",),
        decided_by="operator",
        decided_at="2026-08-12T12:00:00+00:00",
        rationale="reviewed exact changed fingerprint and path",
    )
    loaded = WorkflowState.from_dict(approved.to_dict())

    assert loaded.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert loaded.current_work_unit.gate.reason is GateReason.NONE
    assert loaded.current_work_unit.gate_decisions[-1].reason is GateReason.QUOTA_RESUME_DIFF
    assert (
        "quota-resume-diff:inv-quota-diff:" + "3" * 64
        in loaded.current_work_unit.completed_side_effects
    )


def test_network_failure_roundtrips_as_bounded_retry_wait() -> None:
    state = make_state()
    failure = InvocationFailureRecord(
        invocation_id="inv-network-1",
        idempotency_key="run-1:1:codex_plan:codex",
        role="codex",
        failure_kind=AgentFailureKind.NETWORK,
        provider_text="connection reset by peer",
        received_at="2026-08-12T10:00:00+00:00",
        step=WorkflowStep.CODEX_PLAN,
        slice_id=1,
        work_unit_id=1,
        diagnostic_exit_code=3,
        resume_at_utc="2026-08-12T10:00:05+00:00",
        auto_resume_count=1,
        automatic_resume=True,
    )

    waiting = state.record_invocation_failure(failure, wait_automatically=True)
    loaded = WorkflowState.from_dict(waiting.to_dict())

    assert loaded.current_work_unit.status is WorkUnitStatus.WAITING_FOR_RETRY
    assert loaded.current_slice.status is SliceStatus.WAITING_FOR_RETRY
    assert loaded.current_work_unit.gate.status is GateStatus.WAITING_FOR_RETRY
    assert loaded.current_work_unit.gate.reason is GateReason.INSTANCE_FAILURE
    assert loaded.resume_after_invocation_halt().current_step is WorkflowStep.CODEX_PLAN


def test_antigravity_tool_schema_failure_roundtrips_as_retry_wait() -> None:
    state = make_state()
    failure = InvocationFailureRecord(
        invocation_id="inv-schema-1",
        idempotency_key="run-1:1:codex_plan:antigravity",
        role="antigravity",
        failure_kind=AgentFailureKind.ANTIGRAVITY_TOOL_SCHEMA,
        provider_text="additional properties 'LineNumber' not allowed",
        received_at="2026-08-12T10:00:00+00:00",
        step=WorkflowStep.CODEX_PLAN,
        slice_id=1,
        work_unit_id=1,
        diagnostic_exit_code=3,
        resume_at_utc="2026-08-12T10:00:05+00:00",
        auto_resume_count=1,
        automatic_resume=True,
    )

    waiting = state.record_invocation_failure(failure, wait_automatically=True)
    loaded = WorkflowState.from_dict(waiting.to_dict())

    assert loaded.current_work_unit.status is WorkUnitStatus.WAITING_FOR_RETRY
    assert loaded.current_work_unit.invocation_failures[-1] == failure


def test_resume_after_user_decision_keeps_saved_step_and_review_context() -> None:
    state = make_state()
    for _ in range(DEFAULT_MAX_CODEX_RETURNS):
        state = state.record_review_denial(
            reviewer=Reviewer.ANTIGRAVITY,
            open_findings=("A-01",),
            return_step=WorkflowStep.CODEX_CORRECTION,
        )

    resumed = state.resume_after_user_decision(updated_at="resumed")

    assert resumed.current_step is WorkflowStep.CODEX_CORRECTION
    assert resumed.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert resumed.current_work_unit.gate.status is GateStatus.CLEAR
    assert resumed.current_work_unit.codex_return_count == DEFAULT_MAX_CODEX_RETURNS
    assert resumed.current_work_unit.round_number == DEFAULT_MAX_CODEX_RETURNS + 1
    assert resumed.current_work_unit.max_codex_returns == DEFAULT_MAX_CODEX_RETURNS * 2
    assert resumed.current_work_unit.reviewer is Reviewer.ANTIGRAVITY
    assert resumed.current_work_unit.open_findings == ("A-01",)
    assert resumed.current_slice.status is SliceStatus.IN_PROGRESS

    for expected_count in range(
        DEFAULT_MAX_CODEX_RETURNS + 1,
        DEFAULT_MAX_CODEX_RETURNS * 2 + 1,
    ):
        resumed = resumed.record_review_denial(
            reviewer=Reviewer.ANTIGRAVITY,
            open_findings=("A-01",),
            return_step=WorkflowStep.CODEX_CORRECTION,
        )
        assert resumed.current_work_unit.codex_return_count == expected_count

    assert resumed.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert resumed.current_work_unit.gate.reason is GateReason.ITERATION_LIMIT
    assert resumed.current_work_unit.round_number == DEFAULT_MAX_CODEX_RETURNS * 2


def test_review_denial_recovers_state_cleared_by_legacy_iteration_resume() -> None:
    state = make_state()
    for _ in range(DEFAULT_MAX_CODEX_RETURNS):
        state = state.record_review_denial(
            reviewer=Reviewer.CLAUDE,
            open_findings=("C-01",),
            return_step=WorkflowStep.CODEX_CORRECTION,
        )

    legacy_resumed = replace(
        state,
        work_units=tuple(
            replace(
                unit,
                status=WorkUnitStatus.IN_PROGRESS,
                gate=GateRecord(),
            )
            if unit.work_unit_id == state.current_work_unit_id
            else unit
            for unit in state.work_units
        ),
        slices=tuple(
            replace(item, status=SliceStatus.IN_PROGRESS)
            if item.slice_id == state.current_slice_id
            else item
            for item in state.slices
        ),
    )

    recovered = legacy_resumed.record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=("C-02",),
        return_step=WorkflowStep.CODEX_CORRECTION,
    )

    assert recovered.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert recovered.current_work_unit.gate.status is GateStatus.CLEAR
    assert recovered.current_work_unit.codex_return_count == DEFAULT_MAX_CODEX_RETURNS
    assert recovered.current_work_unit.round_number == DEFAULT_MAX_CODEX_RETURNS + 1
    assert recovered.current_work_unit.max_codex_returns == DEFAULT_MAX_CODEX_RETURNS * 2
    assert recovered.current_work_unit.open_findings == ("C-02",)


def test_review_denial_requires_findings_and_unique_records() -> None:
    state = make_state()
    with pytest.raises(WorkflowStateValidationError, match="requires open findings"):
        state.record_review_denial(
            reviewer=Reviewer.CLAUDE,
            open_findings=(),
            return_step=WorkflowStep.CODEX_CORRECTION,
        )
    with pytest.raises(WorkflowStateValidationError, match="unique"):
        state.record_review_denial(
            reviewer=Reviewer.CLAUDE,
            open_findings=("C-01", "C-01"),
            return_step=WorkflowStep.CODEX_CORRECTION,
        )


def test_state_parser_rejects_unknown_or_missing_fields() -> None:
    raw = make_state().to_dict()
    raw["surprise"] = True
    with pytest.raises(WorkflowStateValidationError, match="unexpected"):
        WorkflowState.from_dict(raw)

    raw = make_state().to_dict()
    del raw["branch_base"]
    with pytest.raises(WorkflowStateValidationError, match="missing"):
        WorkflowState.from_dict(raw)


def test_state_roundtrip_preserves_full_structure() -> None:
    state = make_state().mark_side_effect_completed("plan:written", updated_at="later")
    assert WorkflowState.from_dict(state.to_dict()) == state


def test_multi_slice_transition_persists_start_and_commit_references() -> None:
    state = make_state()
    plan_done = state.complete_current_work_unit(updated_at="plan-done")
    slice_one = plan_done.start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        updated_at="slice-one",
    )
    slice_one = slice_one.bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=("src/one.py",),
        start_fingerprint="1" * 64,
        updated_at="slice-one-boundary",
    )
    slice_one_done = slice_one.complete_current_slice(
        commit_ref="b" * 40,
        updated_at="slice-one-done",
    )
    slice_two = slice_one_done.start_work_unit(
        slice_id=2,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit="b" * 40,
        updated_at="slice-two",
    )
    resumed = WorkflowState.from_dict(slice_two.to_dict()).resume_cursor()

    assert slice_two.slices[0].status is SliceStatus.COMPLETED
    assert slice_two.slices[0].commit_ref == "b" * 40
    assert slice_two.current_slice.start_commit == "b" * 40
    assert slice_two.current_work_unit_id == 3
    assert resumed.slice_id == 2
    assert resumed.work_unit_id == 3
    assert resumed.step is WorkflowStep.CODEX_IMPLEMENTATION


def test_final_review_references_committed_slice_and_appends_bounded_correction() -> None:
    state = init_workflow_state(
        run_id="run-final",
        task_file="/repo/task.md",
        branch="feature/state-v3",
        branch_base="a" * 40,
        slice_count=1,
        timestamp="2026-08-11T10:00:00+00:00",
    ).bind_slice_plan(
        (PlannedSlice(1, "initial implementation", ("src/one.py",)),),
        first_start_commit="a" * 40,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=("src/one.py",),
        start_fingerprint="1" * 64,
    ).complete_current_slice(commit_ref="b" * 40)

    final = state.start_final_review_work_unit()
    halted = final.await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail="scripted final-review pause",
    )
    resumed = halted.resume_after_user_decision().complete_current_work_unit()
    correction = resumed.start_correction_work_unit(
        start_commit="b" * 40,
        scope_paths=("src/fix.py",),
        start_fingerprint="2" * 64,
        finding_ids=("C-01",),
    )
    loaded = WorkflowState.from_dict(correction.to_dict())
    repeated_final = loaded.complete_current_slice(
        commit_ref="c" * 40,
    ).start_final_review_work_unit()
    repeated_final_loaded = WorkflowState.from_dict(repeated_final.to_dict())

    assert final.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
    assert final.current_step is WorkflowStep.CODEX_FINAL_REVIEW
    assert halted.current_slice.status is SliceStatus.COMPLETED
    assert halted.current_slice.commit_ref == "b" * 40
    assert loaded.current_work_unit.kind is WorkUnitKind.CORRECTION
    assert loaded.current_step is WorkflowStep.CODEX_FINAL_CORRECTION
    assert loaded.current_slice.slice_id == 2
    assert loaded.current_slice.status is SliceStatus.IN_PROGRESS
    assert loaded.current_slice.scope_paths == ("src/fix.py",)
    assert loaded.current_work_unit.open_findings == ("C-01",)
    assert repeated_final_loaded.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
    assert repeated_final_loaded.current_slice.slice_id == 2
    assert repeated_final_loaded.current_slice.status is SliceStatus.COMPLETED
    assert repeated_final_loaded.current_slice.commit_ref == "c" * 40


def test_correction_slice_remediation_scope_includes_current_slice_report_path() -> None:
    audit_path = "docs/internal/strukturierte-agentenkommunikation-implement-review-542ccc72.md"
    stale_report = (
        "docs/internal/slice-strukturierte-agentenkommunikation-implement-"
        "01-abschlusskorrektur.md"
    )
    state = init_workflow_state(
        run_id="run-correction-scope",
        task_file="/repo/task.md",
        branch="feature/state-v3",
        branch_base="a" * 40,
        slice_count=1,
        audit_report_path=audit_path,
        timestamp="2026-08-11T10:00:00+00:00",
    ).bind_slice_plan(
        (PlannedSlice(1, "initial implementation", ("src/one.py",)),),
        first_start_commit="a" * 40,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=("src/one.py",),
        start_fingerprint="1" * 64,
    ).complete_current_slice(
        commit_ref="b" * 40,
    ).start_final_review_work_unit().complete_current_work_unit()

    correction = state.start_correction_work_unit(
        start_commit="b" * 40,
        scope_paths=("src/one.py", audit_path, stale_report),
        start_fingerprint="2" * 64,
        finding_ids=("C-25",),
    )

    expected_report = (
        "docs/internal/slice-strukturierte-agentenkommunikation-implement-"
        "02-abschlusskorrektur.md"
    )
    assert expected_report in correction.current_slice.scope_paths
    assert stale_report not in correction.current_slice.scope_paths
    assert WorkflowState.from_dict(correction.to_dict()) == correction


def test_final_review_requires_all_slices_committed() -> None:
    with pytest.raises(WorkflowStateValidationError, match="every current slice"):
        make_state().complete_current_work_unit().start_final_review_work_unit()


def test_slice_git_boundary_is_canonical_persisted_and_immutable() -> None:
    state = make_state().bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=("tests/test_one.py", "src/one.py", "src/one.py"),
        start_fingerprint="1" * 64,
        updated_at="bound",
    )
    loaded = WorkflowState.from_dict(state.to_dict())

    assert loaded.current_slice.scope_paths == ("src/one.py", "tests/test_one.py")
    assert loaded.current_slice.scope_change_groups == (
        ("src/one.py",),
        ("tests/test_one.py",),
    )
    assert loaded.current_slice.start_fingerprint == "1" * 64
    assert loaded.updated_at == "bound"
    assert loaded.bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=("src/one.py", "tests/test_one.py"),
        start_fingerprint="1" * 64,
    ) is loaded

    with pytest.raises(WorkflowStateValidationError, match="cannot change"):
        loaded.bind_current_slice_git_boundary(
            start_commit="a" * 40,
            scope_paths=("src/two.py",),
            start_fingerprint="2" * 64,
        )


def test_legacy_v3_slice_record_loads_and_serializes_explicit_empty_git_boundary() -> None:
    raw = make_state().to_dict()
    for slice_record in raw["slices"]:
        del slice_record["scope_paths"]
        del slice_record["scope_change_groups"]
        del slice_record["start_fingerprint"]

    loaded = WorkflowState.from_dict(raw)
    migrated = loaded.to_dict()

    assert loaded.current_slice.scope_paths == ()
    assert loaded.current_slice.start_fingerprint is None
    assert migrated["slices"][0]["scope_paths"] == []
    assert migrated["slices"][0]["scope_change_groups"] == []
    assert migrated["slices"][0]["start_fingerprint"] is None


def test_slice_git_boundary_persists_rename_group_and_loads_older_flat_shape() -> None:
    state = make_state().bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=("src/new.py", "src/old.py"),
        scope_change_groups=(("src/new.py", "src/old.py"),),
        start_fingerprint="1" * 64,
    )
    loaded = WorkflowState.from_dict(state.to_dict())
    assert loaded.current_slice.scope_change_groups == (
        ("src/new.py", "src/old.py"),
    )

    early_slice12 = state.to_dict()
    for slice_record in early_slice12["slices"]:
        del slice_record["scope_change_groups"]
    migrated = WorkflowState.from_dict(early_slice12)
    assert migrated.current_slice.scope_change_groups == (
        ("src/new.py",),
        ("src/old.py",),
    )


def test_slice_cannot_complete_before_git_boundary_is_persisted() -> None:
    with pytest.raises(WorkflowStateValidationError, match="Git boundary"):
        make_state().complete_current_slice(commit_ref="b" * 40)


@pytest.mark.parametrize("scope", [("../outside",), ("src\\one.py",), ("/tmp/a",)])
def test_slice_git_boundary_rejects_non_repository_scope(scope: tuple[str, ...]) -> None:
    with pytest.raises(WorkflowStateValidationError, match="scope_paths"):
        make_state().bind_current_slice_git_boundary(
            start_commit="a" * 40,
            scope_paths=scope,
            start_fingerprint="1" * 64,
        )


def test_new_slice_cannot_start_without_persisted_start_commit() -> None:
    state = make_state().complete_current_work_unit()
    with pytest.raises(WorkflowStateValidationError, match="slice_start_commit"):
        state.start_work_unit(
            slice_id=2,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,
        )


def test_fingerprint_bound_gate_rejects_mismatch_and_persists_denial_then_approval() -> None:
    state = make_state().await_user_gate(
        reason=GateReason.TEST_CHANGE,
        detail="test approval required",
        fingerprint="1" * 64,
        paths=("tests/test_one.py",),
        gate_step=WorkflowStep.CLAUDE_SLICE_REVIEW,
        updated_at="halted",
    )

    assert state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert state.current_slice.status is SliceStatus.AWAITING_USER_DECISION
    assert state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    with pytest.raises(WorkflowStateValidationError, match="does not match"):
        state.record_user_gate_decision(
            approved=True,
            fingerprint="2" * 64,
            paths=("tests/test_one.py",),
            decided_by="user",
            decided_at="2026-08-12T12:00:00+00:00",
            rationale="wrong fingerprint",
        )
    with pytest.raises(WorkflowStateValidationError, match="explicit recorded"):
        state.resume_after_user_decision()

    rejected = state.record_user_gate_decision(
        approved=False,
        fingerprint="1" * 64,
        paths=("tests/test_one.py",),
        decided_by="user",
        decided_at="2026-08-12T12:00:00+00:00",
        rationale="needs another look",
    )
    approved = rejected.record_user_gate_decision(
        approved=True,
        fingerprint="1" * 64,
        paths=("tests/test_one.py",),
        decided_by="user",
        decided_at="2026-08-12T12:01:00+00:00",
        rationale="reviewed",
    )
    active = approved.record_active_test_approval(
        "1" * 64, ("tests/test_one.py",)
    )
    loaded = WorkflowState.from_dict(active.to_dict())

    assert rejected.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert loaded.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert loaded.current_work_unit.gate.status is GateStatus.CLEAR
    assert loaded.current_work_unit.has_gate_approval(
        GateReason.TEST_CHANGE, "1" * 64, ("tests/test_one.py",)
    )
    assert [item.approved for item in loaded.current_work_unit.gate_decisions] == [
        False,
        True,
    ]
    assert loaded.current_work_unit.active_test_fingerprint == "1" * 64
    assert loaded.current_work_unit.active_test_paths == ("tests/test_one.py",)


def test_anchor_gate_persists_reset_and_resume_steps() -> None:
    state = make_state().await_user_gate(
        reason=GateReason.ANCHOR_CHANGE,
        detail="anchor changed",
        fingerprint="3" * 64,
        paths=("RATE",),
        gate_step=WorkflowStep.CODEX_PLAN_REVISION,
        resume_step=WorkflowStep.CODEX_IMPLEMENTATION,
    )
    approved = state.record_user_gate_decision(
        approved=True,
        fingerprint="3" * 64,
        paths=("RATE",),
        decided_by="domain-owner",
        decided_at="2026-08-12T12:00:00+00:00",
        rationale="review the new anchor",
    )
    decision = approved.current_work_unit.gate_decisions[-1]

    assert approved.current_step is WorkflowStep.CODEX_PLAN_REVISION
    assert decision.reason is GateReason.ANCHOR_CHANGE
    assert decision.resume_step is WorkflowStep.CODEX_IMPLEMENTATION


def test_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields() -> None:
    raw = make_state().to_dict()
    for unit in raw["work_units"]:
        del unit["gate_decisions"]
        del unit["active_test_fingerprint"]
        del unit["active_test_paths"]
        del unit["gate"]["fingerprint"]
        del unit["gate"]["paths"]
        del unit["gate"]["resume_step"]

    loaded = WorkflowState.from_dict(raw)

    assert loaded.current_work_unit.gate_decisions == ()
    assert loaded.current_work_unit.gate.fingerprint is None


def test_early_slice11_work_unit_shape_loads_without_active_test_evidence() -> None:
    raw = make_state().to_dict()
    for unit in raw["work_units"]:
        del unit["active_test_fingerprint"]
        del unit["active_test_paths"]

    loaded = WorkflowState.from_dict(raw)

    assert loaded.current_work_unit.gate_decisions == ()
    assert loaded.current_work_unit.active_test_fingerprint is None
    assert loaded.current_work_unit.active_test_paths == ()


def test_policy_gate_roundtrips_and_resumes_at_same_step() -> None:
    state = make_state().await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail="PRODUCTIVE-FILE-LIMIT | eleven productive files",
        paths=("src/one.py", "src/two.py"),
        updated_at="halted",
    )

    loaded = WorkflowState.from_dict(state.to_dict())
    resumed = loaded.resume_after_user_decision(updated_at="resumed")

    assert loaded.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert loaded.current_work_unit.gate.fingerprint is None
    assert loaded.current_work_unit.gate.paths == ("src/one.py", "src/two.py")
    assert resumed.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert resumed.current_step is state.current_step
    assert resumed.current_work_unit.gate.status is GateStatus.CLEAR


def test_managed_audit_path_roundtrips_and_rejects_unsafe_locations() -> None:
    state = replace(
        make_state(),
        audit_report_path="docs/internal/bug-review-12345678.md",
    )

    assert WorkflowState.from_dict(state.to_dict()).audit_report_path == (
        "docs/internal/bug-review-12345678.md"
    )
    with pytest.raises(WorkflowStateValidationError, match="audit_report_path"):
        replace(state, audit_report_path="../audit.md")


def test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path() -> None:
    with pytest.raises(WorkflowStateValidationError, match="policy gate reason"):
        make_state().await_policy_gate(
            reason=GateReason.TEST_CHANGE,
            detail="wrong helper",
        )
    with pytest.raises(WorkflowStateValidationError, match="repository-relative"):
        make_state().await_policy_gate(
            reason=GateReason.UNEXPECTED_FILE,
            detail="unsafe path",
            paths=("../outside",),
        )


def test_unexpected_file_user_gate_decision_roundtrips() -> None:
    state = make_state().await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="UNEXPECTED-PATH | reviewed external changes",
        fingerprint="4" * 64,
        paths=("src/external.py",),
    )

    approved = state.record_user_gate_decision(
        approved=True,
        fingerprint="4" * 64,
        paths=("src/external.py",),
        decided_by="operator",
        decided_at="2026-08-20T10:00:00+00:00",
        rationale="exact external change reviewed",
    )
    loaded = WorkflowState.from_dict(approved.to_dict())

    assert loaded.current_work_unit.has_gate_approval(
        GateReason.UNEXPECTED_FILE,
        "4" * 64,
        ("src/external.py",),
    )
    assert loaded.current_work_unit.gate.status is GateStatus.CLEAR


def test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume() -> None:
    original_head = "a" * 40
    advanced_head = "b" * 40
    state = init_workflow_state(
        run_id="resume-boundary",
        task_file="/repo/task.md",
        branch="feature/resume-boundary",
        branch_base="0" * 40,
        first_slice_start_commit=original_head,
        slice_count=1,
    ).bind_slice_plan(
        (PlannedSlice(1, "bounded slice", ("src/one.py",)),),
        first_start_commit=original_head,
    ).complete_current_work_unit()
    state = state.start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    )

    with pytest.raises(WorkflowStateValidationError, match="must match"):
        state.bind_current_slice_git_boundary(
            start_commit=advanced_head,
            scope_paths=("src/one.py",),
            start_fingerprint="1" * 64,
        )


def test_in_progress_slice_can_extend_exact_remediation_scope() -> None:
    state = make_state().complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=("src/current.py", "tests/current.test.py"),
        start_fingerprint="1" * 64,
    )

    expanded = state.extend_current_slice_scope(
        ("src/prior.py", "tests/prior.test.py")
    )

    assert expanded.current_slice.scope_paths == (
        "src/current.py",
        "src/prior.py",
        "tests/current.test.py",
        "tests/prior.test.py",
    )
    assert expanded.current_slice.start_commit == state.current_slice.start_commit
    assert expanded.current_slice.start_fingerprint == state.current_slice.start_fingerprint
    assert WorkflowState.from_dict(expanded.to_dict()) == expanded


def test_state_scope_rejects_orchestrator_internal_paths() -> None:
    state = make_state().complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    )
    with pytest.raises(WorkflowStateValidationError, match="outside .orchestrator"):
        state.bind_current_slice_git_boundary(
            start_commit="a" * 40,
            scope_paths=(".orchestrator/state.json",),
            start_fingerprint="1" * 64,
        )
