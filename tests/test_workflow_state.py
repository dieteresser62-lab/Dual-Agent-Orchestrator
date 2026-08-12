from __future__ import annotations

from dataclasses import replace

import pytest

from workflow_state import (
    AgentFailureKind,
    DEFAULT_MAX_CODEX_RETURNS,
    GateReason,
    GateStatus,
    InvocationFailureRecord,
    Reviewer,
    SliceStatus,
    WorkflowState,
    WorkflowStateValidationError,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
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
    assert resumed.current_work_unit.reviewer is Reviewer.ANTIGRAVITY
    assert resumed.current_work_unit.open_findings == ("A-01",)
    assert resumed.current_slice.status is SliceStatus.IN_PROGRESS


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
    )
    loaded = WorkflowState.from_dict(correction.to_dict())

    assert final.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
    assert final.current_step is WorkflowStep.CODEX_FINAL_REVIEW
    assert halted.current_slice.status is SliceStatus.COMPLETED
    assert halted.current_slice.commit_ref == "b" * 40
    assert loaded.current_work_unit.kind is WorkUnitKind.CORRECTION
    assert loaded.current_step is WorkflowStep.CODEX_FINAL_CORRECTION
    assert loaded.current_slice.slice_id == 2
    assert loaded.current_slice.status is SliceStatus.IN_PROGRESS
    assert loaded.current_slice.scope_paths == ("src/fix.py",)


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
