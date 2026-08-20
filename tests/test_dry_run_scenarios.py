from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_runtime import TransientRetryPolicy
from cli import ConfigError, parse_args, run_cli
from contracts import AgentRole, AnchorRecord
from dry_run_scenarios import (
    DryRunScenario,
    DryRunScenarioError,
    ScenarioExpectations,
    ScriptedAgentEvent,
    ScriptedChange,
    ScriptedCommit,
    ScriptedContext,
    ScriptedFailure,
    ScriptedInitialState,
    ScriptedTestChange,
    ScriptedValidation,
    ScriptedWorkflowSession,
    build_scenario_context,
    build_scenario_state,
    load_dry_run_scenario,
    run_dry_run_scenario_file,
    run_scripted_work_unit,
)
from workflow import EvidenceKind, WorkflowContractError, WorkflowExecutionError
from workflow_state import (
    AgentFailureKind,
    GateReason,
    WorkUnitKind,
    WorkflowStep,
    WorkUnitStatus,
)


BASE = "a" * 40
FP1 = "1" * 64
FP2 = "2" * 64
TEST_FP = "e" * 64
NOW = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
TEST_FILE = "tests/test_dry_run_scenarios.py"
PATHS = ("src/engine.py", TEST_FILE)


def codex_ready(*finding_ids: str, slice_id: str = "01") -> str:
    return "\n".join(
        (
            *(f"FINDING_RESPONSE: {item} | ACCEPTED | corrected" for item in finding_ids),
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            f"IMPLEMENTATION_READY: {slice_id} | YES",
            "STATUS: DONE",
        )
    )


def approval(
    role: AgentRole, *, finding_status: str | None = None, slice_id: str = "01"
) -> str:
    record = finding_status or (
        "REVIEW_EVIDENCE: invariants and gates | provider drift | malformed envelope"
    )
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            record,
            "PRE_MORTEM: a future provider payload bypasses automation",
            f"SLICE_APPROVAL: {slice_id} | YES",
            "STATUS: DONE",
        )
    )


def denial(role: AgentRole, finding_id: str, *, previous: bool = False) -> str:
    finding = (
        f"FINDING_STATUS: {finding_id} | OPEN | correction remains incomplete"
        if previous
        else f"NEW_FINDING: {finding_id} | BLOCKER | unsafe transition | add regression"
    )
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            finding,
            "SLICE_APPROVAL: 01 | NO",
            "STATUS: DONE",
        )
    )


def final_report() -> str:
    return "FINAL_REPORT_READY: YES\nSTATUS: DONE"


def final_approval(role: AgentRole) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            "REVIEW_EVIDENCE: architecture through R-18 | interface drift | dead transition",
            "PRE_MORTEM: a later change desynchronizes docs and runtime",
            "FINAL_APPROVAL: YES",
            "STATUS: DONE",
        )
    )


def final_denial(role: AgentRole, finding_id: str) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            f"NEW_FINDING: {finding_id} | BLOCKER | branch transition is stale | add branch regression",
            "FINAL_APPROVAL: NO",
            "STATUS: DONE",
        )
    )


def change(fingerprint: str = FP1, *, round_number: int = 1, paths=PATHS) -> ScriptedChange:
    return ScriptedChange(2, round_number, BASE, fingerprint, tuple(sorted(paths)), "diff")


def event(
    role: AgentRole,
    step: WorkflowStep,
    output: str | None = None,
    *,
    round_number: int = 1,
    failure: ScriptedFailure | None = None,
) -> ScriptedAgentEvent:
    return ScriptedAgentEvent(role, 2, round_number, step, output, failure)


def happy_scenario(*, validation_status: str = "pass", red_state: str | None = None) -> DryRunScenario:
    return DryRunScenario(
        name="happy",
        agent_events=(
            event(AgentRole.CODEX, WorkflowStep.CODEX_IMPLEMENTATION, codex_ready()),
            event(AgentRole.CLAUDE, WorkflowStep.CLAUDE_SLICE_REVIEW, approval(AgentRole.CLAUDE)),
            event(
                AgentRole.ANTIGRAVITY,
                WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
                approval(AgentRole.ANTIGRAVITY),
            ),
        ),
        changes=(change(),),
        validations=(ScriptedValidation(FP1, validation_status),),
        commits=(ScriptedCommit(1, FP1, "b" * 40),),
        clock_start=NOW,
        context=ScriptedContext(
            expected_test_files=(TEST_FILE,),
            red_state_followup_slice=red_state,
        ),
    )


def run(scenario: DryRunScenario, tmp_path: Path):
    return run_scripted_work_unit(
        scenario=scenario,
        state=build_scenario_state(scenario, task_file=tmp_path / "task.md"),
        context=build_scenario_context(scenario),
    )


def test_positive_scenario_uses_real_workflow_once_and_reports_audit(tmp_path: Path) -> None:
    report = run(happy_scenario(), tmp_path)

    assert report.result.completed
    assert report.result.exit_code == 0
    assert report.validation_counts == {FP1: 1}
    assert report.remaining_agent_events == 0
    assert sum(item.startswith("commit:") for item in report.calls) == 1
    audit = json.loads(report.audit_document)
    assert audit["exit_code"] == 0
    assert audit["state"]["work_units"][-1]["status"] == "completed"
    assert audit["validation_counts"] == {FP1: 1}


def test_positive_session_runs_plan_and_multiple_slices(tmp_path: Path) -> None:
    plan_fp = "0" * 64
    slice2_fp = "2" * 64
    commit1 = "b" * 40
    commit2 = "c" * 40
    plan_ready = "PLAN_READY: YES\nSTATUS: DONE"
    plan_approval = "\n".join(
        (
            "REVIEWER: claude",
            "TEST_FILES_TOUCHED: NONE",
            "REVIEW_EVIDENCE: plan invariants | stale scope | requirement drift",
            "PRE_MORTEM: a later slice violates the plan",
            "PLAN_APPROVAL: YES",
            "STATUS: DONE",
        )
    )
    scenario = DryRunScenario(
        name="plan-two-slices",
        agent_events=(
            ScriptedAgentEvent(
                AgentRole.CODEX, 1, 1, WorkflowStep.CODEX_PLAN, plan_ready
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE,
                1,
                1,
                WorkflowStep.CLAUDE_PLAN_REVIEW,
                plan_approval,
            ),
            ScriptedAgentEvent(
                AgentRole.ANTIGRAVITY,
                1,
                1,
                WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
                plan_approval.replace("REVIEWER: claude", "REVIEWER: antigravity"),
            ),
            event(AgentRole.CODEX, WorkflowStep.CODEX_IMPLEMENTATION, codex_ready()),
            event(
                AgentRole.CLAUDE,
                WorkflowStep.CLAUDE_SLICE_REVIEW,
                approval(AgentRole.CLAUDE),
            ),
            event(
                AgentRole.ANTIGRAVITY,
                WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
                approval(AgentRole.ANTIGRAVITY),
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX,
                3,
                1,
                WorkflowStep.CODEX_IMPLEMENTATION,
                codex_ready(slice_id="02"),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE,
                3,
                1,
                WorkflowStep.CLAUDE_SLICE_REVIEW,
                approval(AgentRole.CLAUDE, slice_id="02"),
            ),
            ScriptedAgentEvent(
                AgentRole.ANTIGRAVITY,
                3,
                1,
                WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
                approval(AgentRole.ANTIGRAVITY, slice_id="02"),
            ),
        ),
        changes=(
            ScriptedChange(1, 1, BASE, plan_fp, ("docs/internal/plan.md",), "plan diff"),
            change(),
            ScriptedChange(3, 1, commit1, slice2_fp, PATHS, "slice 2 diff"),
        ),
        validations=(
            ScriptedValidation(plan_fp, "pass"),
            ScriptedValidation(FP1, "pass"),
            ScriptedValidation(slice2_fp, "pass"),
        ),
        commits=(
            ScriptedCommit(1, FP1, commit1),
            ScriptedCommit(2, slice2_fp, commit2),
        ),
        clock_start=NOW,
        initial=ScriptedInitialState(kind=WorkUnitKind.PLAN, slice_count=2),
        context=ScriptedContext(expected_test_files=(TEST_FILE,)),
    )
    state = build_scenario_state(scenario, task_file=tmp_path / "task.md")
    context = build_scenario_context(scenario)
    session = ScriptedWorkflowSession(scenario)

    plan_result = session.run(state, context)
    assert plan_result.result.completed

    slice1 = plan_result.result.state.start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=BASE,
        scope_paths=PATHS,
        start_fingerprint="d" * 64,
    )
    slice1_result = session.run(slice1, context)
    assert slice1_result.result.commit_ref == commit1

    slice2 = slice1_result.result.state.start_work_unit(
        slice_id=2,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit=commit1,
    ).bind_current_slice_git_boundary(
        start_commit=commit1,
        scope_paths=PATHS,
        start_fingerprint="e" * 64,
    )
    slice2_result = session.run(slice2, context)

    assert slice2_result.result.commit_ref == commit2
    assert [item.commit_ref for item in slice2_result.result.state.slices] == [
        commit1,
        commit2,
    ]
    assert slice2_result.validation_counts == {plan_fp: 1, FP1: 1, slice2_fp: 1}
    assert slice2_result.remaining_agent_events == 0


def test_scripted_session_runs_plan_slices_correction_and_repeated_final_review(
    tmp_path: Path,
) -> None:
    plan_fp = "0" * 64
    slice2_fp = "2" * 64
    first_final_fp = "4" * 64
    correction_fp = "5" * 64
    second_final_fp = "6" * 64
    commit1 = "b" * 40
    commit2 = "c" * 40
    commit3 = "d" * 40
    plan_approval = "\n".join(
        (
            "REVIEWER: claude",
            "TEST_FILES_TOUCHED: NONE",
            "REVIEW_EVIDENCE: plan invariants | stale scope | requirement drift",
            "PRE_MORTEM: a later slice violates the plan",
            "PLAN_APPROVAL: YES",
            "STATUS: DONE",
        )
    )
    scenario = DryRunScenario(
        name="complete-final-correction",
        agent_events=(
            ScriptedAgentEvent(
                AgentRole.CODEX, 1, 1, WorkflowStep.CODEX_PLAN,
                "PLAN_READY: YES\nSTATUS: DONE",
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 1, 1, WorkflowStep.CLAUDE_PLAN_REVIEW,
                plan_approval,
            ),
            ScriptedAgentEvent(
                AgentRole.ANTIGRAVITY, 1, 1,
                WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
                plan_approval.replace("REVIEWER: claude", "REVIEWER: antigravity"),
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX, 2, 1, WorkflowStep.CODEX_IMPLEMENTATION,
                codex_ready(),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 2, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                approval(AgentRole.CLAUDE),
            ),
            ScriptedAgentEvent(
                AgentRole.ANTIGRAVITY, 2, 1,
                WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
                approval(AgentRole.ANTIGRAVITY),
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX, 3, 1, WorkflowStep.CODEX_IMPLEMENTATION,
                codex_ready(slice_id="02"),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 3, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                approval(AgentRole.CLAUDE, slice_id="02"),
            ),
            ScriptedAgentEvent(
                AgentRole.ANTIGRAVITY, 3, 1,
                WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
                approval(AgentRole.ANTIGRAVITY, slice_id="02"),
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX, 4, 1, WorkflowStep.CODEX_FINAL_REVIEW,
                final_report(),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 4, 1, WorkflowStep.CLAUDE_FINAL_REVIEW,
                final_denial(AgentRole.CLAUDE, "C-01"),
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX, 5, 1, WorkflowStep.CODEX_FINAL_CORRECTION,
                codex_ready("C-01", slice_id="03"),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 5, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                approval(
                    AgentRole.CLAUDE,
                    finding_status=(
                        "FINDING_STATUS: C-01 | CLOSED | branch regression proves the fix"
                    ),
                    slice_id="03",
                ),
            ),
            ScriptedAgentEvent(
                AgentRole.ANTIGRAVITY, 5, 1,
                WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
                approval(AgentRole.ANTIGRAVITY, slice_id="03"),
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX, 6, 1, WorkflowStep.CODEX_FINAL_REVIEW,
                final_report(),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 6, 1, WorkflowStep.CLAUDE_FINAL_REVIEW,
                final_approval(AgentRole.CLAUDE),
            ),
            ScriptedAgentEvent(
                AgentRole.ANTIGRAVITY, 6, 1,
                WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
                final_approval(AgentRole.ANTIGRAVITY),
            ),
        ),
        changes=(
            ScriptedChange(1, 1, BASE, plan_fp, ("docs/internal/plan.md",), "plan"),
            ScriptedChange(2, 1, BASE, FP1, PATHS, "slice one"),
            ScriptedChange(3, 1, commit1, slice2_fp, PATHS, "slice two"),
            ScriptedChange(
                4, 1, BASE, first_final_fp, PATHS,
                "slice one\nslice two",
            ),
            ScriptedChange(5, 1, commit2, correction_fp, PATHS, "correction"),
            ScriptedChange(
                6, 1, BASE, second_final_fp, PATHS,
                "slice one\nslice two\ncorrection",
            ),
        ),
        validations=tuple(
            ScriptedValidation(item, "pass")
            for item in (
                plan_fp, FP1, slice2_fp, first_final_fp,
                correction_fp, second_final_fp,
            )
        ),
        commits=(
            ScriptedCommit(1, FP1, commit1),
            ScriptedCommit(2, slice2_fp, commit2),
            ScriptedCommit(3, correction_fp, commit3),
        ),
        clock_start=NOW,
        initial=ScriptedInitialState(kind=WorkUnitKind.PLAN, slice_count=2),
        context=ScriptedContext(expected_test_files=(TEST_FILE,)),
    )
    state = build_scenario_state(scenario, task_file=tmp_path / "task.md")
    context = build_scenario_context(scenario)
    session = ScriptedWorkflowSession(scenario)

    plan = session.run(state, context)
    slice1_state = plan.result.state.start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=BASE, scope_paths=PATHS, start_fingerprint="a" * 64
    )
    slice1 = session.run(slice1_state, context)
    slice2_state = slice1.result.state.start_work_unit(
        slice_id=2,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit=commit1,
    ).bind_current_slice_git_boundary(
        start_commit=commit1, scope_paths=PATHS, start_fingerprint="b" * 64
    )
    slice2 = session.run(slice2_state, context)
    final = session.run(slice2.result.state.start_final_review_work_unit(), context)

    assert final.result.completed
    assert final.result.state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
    assert [item.kind for item in final.result.state.work_units] == [
        WorkUnitKind.PLAN,
        WorkUnitKind.SLICE,
        WorkUnitKind.SLICE,
        WorkUnitKind.FINAL_REVIEW,
        WorkUnitKind.CORRECTION,
        WorkUnitKind.FINAL_REVIEW,
    ]
    assert [item.commit_ref for item in final.result.state.slices] == [
        commit1, commit2, commit3,
    ]
    assert final.validation_counts == {
        plan_fp: 1,
        FP1: 1,
        slice2_fp: 1,
        first_final_fp: 1,
        correction_fp: 1,
        second_final_fp: 1,
    }
    final_reviews = [
        item
        for item in session.driver.reviewer_invocations
        if item.evidence_kind is EvidenceKind.FULL_BRANCH
    ]
    assert [item.fingerprint for item in final_reviews] == [
        first_final_fp,
        second_final_fp,
        second_final_fp,
    ]
    assert "slice one\nslice two\ncorrection" in final_reviews[-1].prompt
    assert final.remaining_agent_events == 0


@pytest.mark.parametrize(
    ("red_state", "completes"), ((None, False), ("slice-16", True))
)
def test_red_validation_requires_explicit_red_state_exception(
    tmp_path: Path, red_state: str | None, completes: bool
) -> None:
    scenario = happy_scenario(validation_status="fail", red_state=red_state)
    if completes:
        assert run(scenario, tmp_path).result.completed
    else:
        scenario = replace(
            scenario,
            repair_outputs=(approval(AgentRole.CLAUDE),),
        )
        report = run(scenario, tmp_path)
        assert report.result.exit_code == 3
        assert report.result.state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
        failure = report.result.state.current_work_unit.invocation_failures[-1]
        assert failure.failure_kind is AgentFailureKind.OUTPUT
        assert "complete passing validation attestation" in failure.provider_text


@pytest.mark.parametrize("status", ["missing", "incomplete"])
def test_missing_or_incomplete_validation_never_approves(
    tmp_path: Path, status: str
) -> None:
    scenario = happy_scenario(validation_status=status)
    if status == "missing":
        report = run(scenario, tmp_path)
        assert report.result.exit_code == 4
        assert report.result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
        assert not any(call.startswith("agent:claude") for call in report.calls)
    else:
        with pytest.raises(WorkflowExecutionError, match="incomplete"):
            run(scenario, tmp_path)


def test_foreign_validation_is_rejected_before_reviewer(tmp_path: Path) -> None:
    scenario = replace(
        happy_scenario(),
        validations=(ScriptedValidation(FP1, "pass", FP2),),
    )
    with pytest.raises(WorkflowExecutionError, match="foreign"):
        run(scenario, tmp_path)


@pytest.mark.parametrize(
    ("bad_review", "error"),
    (
        (
            "\n".join(
                (
                    "REVIEWER: claude",
                    f"TEST_FILES_TOUCHED: {TEST_FILE}",
                    "REVIEW_EVIDENCE: dimensions | risk | break",
                    "PRE_MORTEM: drift",
                    "STATUS: DONE",
                )
            ),
            "SLICE_APPROVAL",
        ),
        (
            "\n".join(
                (
                    "REVIEWER: claude",
                    f"TEST_FILES_TOUCHED: {TEST_FILE}",
                    "PRE_MORTEM: drift",
                    "SLICE_APPROVAL: 01 | YES",
                    "STATUS: DONE",
                )
            ),
            "finding or REVIEW_EVIDENCE",
        ),
        (
            "\n".join(
                (
                    "REVIEWER: claude",
                    f"TEST_FILES_TOUCHED: {TEST_FILE}",
                    "REVIEW_EVIDENCE: dimensions | risk | break",
                    "SLICE_APPROVAL: 01 | YES",
                    "STATUS: DONE",
                )
            ),
            "PRE_MORTEM",
        ),
        (
            "\n".join(
                (
                    "REVIEWER: claude",
                    f"TEST_FILES_TOUCHED: {TEST_FILE}",
                    "REVIEW_EVIDENCE: dimensions | risk | break",
                    "PRE_MORTEM: drift",
                    "VALIDATION_RESULT: PASS | fake | 0",
                    "SLICE_APPROVAL: 01 | YES",
                    "STATUS: DONE",
                )
            ),
            "VALIDATION_RESULT",
        ),
    ),
)
def test_contract_gates_are_negative_scenarios(
    tmp_path: Path, bad_review: str, error: str
) -> None:
    good = happy_scenario()
    scenario = replace(
        good,
        agent_events=(good.agent_events[0], replace(good.agent_events[1], output=bad_review)),
        repair_outputs=(bad_review,),
        commits=(),
    )
    report = run(scenario, tmp_path)
    assert report.result.exit_code == 3
    assert report.result.state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    failure = report.result.state.current_work_unit.invocation_failures[-1]
    assert failure.failure_kind is AgentFailureKind.OUTPUT
    assert error in failure.provider_text


def test_antigravity_contract_gate_rejects_a_missing_verdict(tmp_path: Path) -> None:
    scenario = happy_scenario()
    invalid = "\n".join(
        (
            "REVIEWER: antigravity",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            "REVIEW_EVIDENCE: dimensions | risk | break",
            "PRE_MORTEM: drift",
            "STATUS: DONE",
        )
    )
    broken = replace(
        scenario,
        agent_events=(
            scenario.agent_events[0],
            scenario.agent_events[1],
            replace(scenario.agent_events[2], output=invalid),
        ),
        repair_outputs=(invalid,),
        commits=(),
    )

    report = run(broken, tmp_path)
    assert report.result.exit_code == 3
    assert report.result.state.current_step is WorkflowStep.ANTIGRAVITY_SLICE_REVIEW
    failure = report.result.state.current_work_unit.invocation_failures[-1]
    assert failure.failure_kind is AgentFailureKind.OUTPUT
    assert "SLICE_APPROVAL" in failure.provider_text


def test_scripted_contract_repair_can_supply_the_only_valid_verdict(
    tmp_path: Path,
) -> None:
    scenario = happy_scenario()
    invalid = "REVIEWER: claude\nSTATUS: DONE"
    repaired = replace(
        scenario,
        agent_events=(
            scenario.agent_events[0],
            replace(scenario.agent_events[1], output=invalid),
            scenario.agent_events[2],
        ),
        repair_outputs=(approval(AgentRole.CLAUDE),),
    )

    report = run(repaired, tmp_path)

    assert report.result.completed
    assert "repair:claude" in report.calls


def test_missing_scripted_agent_response_fails_closed(tmp_path: Path) -> None:
    scenario = replace(happy_scenario(), agent_events=(), validations=(), commits=())

    with pytest.raises(DryRunScenarioError, match="missing scripted response"):
        run(scenario, tmp_path)


def test_stop_rule_scenario_halts_without_validation_or_reviewer(tmp_path: Path) -> None:
    stop = "STOP_REQUESTED: PRODUCTIVE-FILE-LIMIT | stop before review\nSTATUS: DONE"
    scenario = replace(
        happy_scenario(),
        agent_events=(event(AgentRole.CODEX, WorkflowStep.CODEX_IMPLEMENTATION, stop),),
        validations=(),
        commits=(),
    )
    report = run(scenario, tmp_path)

    assert report.result.exit_code == 4
    assert report.result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert sum(report.validation_counts.values()) == 0


def test_unexpected_file_scenario_halts_before_validation(tmp_path: Path) -> None:
    scenario = replace(
        happy_scenario(),
        initial=ScriptedInitialState(scope_paths=("src/engine.py",)),
        agent_events=(happy_scenario().agent_events[0],),
        validations=(),
        commits=(),
    )
    report = run(scenario, tmp_path)

    assert report.result.exit_code == 4
    assert report.result.state.current_work_unit.gate.reason is GateReason.UNEXPECTED_FILE
    assert TEST_FILE in report.result.state.current_work_unit.gate.paths


def test_test_change_gate_and_fingerprint_changed_after_approval(tmp_path: Path) -> None:
    scenario = replace(
        happy_scenario(),
        context=ScriptedContext(
            expected_test_files=(TEST_FILE,), test_changes_approved=False
        ),
        test_changes=(ScriptedTestChange(FP1, (TEST_FILE,), TEST_FP),),
    )
    state = build_scenario_state(scenario, task_file=tmp_path / "task.md")
    context = build_scenario_context(scenario)
    session = ScriptedWorkflowSession(scenario)

    halted = session.run(state, context)
    assert halted.result.exit_code == 4
    assert halted.result.state.current_work_unit.gate.reason is GateReason.TEST_CHANGE

    approved = halted.result.state.record_user_gate_decision(
        approved=True,
        fingerprint=TEST_FP,
        paths=(TEST_FILE,),
        decided_by="tester",
        decided_at="2026-08-13T10:01:00+00:00",
        rationale="scripted approval",
    )
    completed = session.run(approved, context, halted.result.history)
    assert completed.result.completed
    assert completed.validation_counts == {FP1: 1}

    changed = replace(
        scenario,
        changes=(change(FP2),),
        validations=(ScriptedValidation(FP2, "pass"),),
        commits=(ScriptedCommit(1, FP2, "c" * 40),),
        test_changes=(ScriptedTestChange(FP2, (TEST_FILE,), "f" * 64),),
    )
    changed_session = ScriptedWorkflowSession(changed)
    changed_halt = changed_session.run(approved, context, halted.result.history)
    assert changed_halt.result.state.current_work_unit.gate.reason is GateReason.TEST_CHANGE


def test_anchor_change_scenario_resets_plan_review_chain(tmp_path: Path) -> None:
    scenario = happy_scenario()
    state = build_scenario_state(scenario, task_file=tmp_path / "task.md")
    context = replace(
        build_scenario_context(scenario),
        approved_anchors=(AnchorRecord("A", "plan", "x", "1", "exact"),),
        current_anchors=(AnchorRecord("A", "plan", "x", "2", "exact"),),
    )
    report = run_scripted_work_unit(scenario=scenario, state=state, context=context)

    assert report.result.exit_code == 4
    assert report.result.state.current_work_unit.gate.reason is GateReason.ANCHOR_CHANGE
    gate = report.result.state.current_work_unit.gate
    approved = report.result.state.record_user_gate_decision(
        approved=True,
        fingerprint=gate.fingerprint or "",
        paths=gate.paths,
        decided_by="tester",
        decided_at="2026-08-13T10:01:00+00:00",
        rationale="approve new anchor",
    )
    reset_scenario = replace(
        scenario,
        agent_events=(
            event(
                AgentRole.CODEX,
                WorkflowStep.CODEX_PLAN_REVISION,
                "STOP_REQUESTED: CONTRACT-UNCLEAR | inspect anchor\nSTATUS: DONE",
            ),
        ),
        validations=(),
        commits=(),
    )
    reset = ScriptedWorkflowSession(reset_scenario).run(
        approved, context, report.result.history
    )
    assert reset.result.state.current_step is WorkflowStep.CODEX_PLAN_REVISION


def test_iteration_limit_scenario_halts_on_fourth_codex_return(tmp_path: Path) -> None:
    events: list[ScriptedAgentEvent] = []
    changes: list[ScriptedChange] = []
    validations: list[ScriptedValidation] = []
    for round_number, fingerprint in enumerate((FP1, FP2, "3" * 64, "4" * 64), 1):
        events.append(
            event(
                AgentRole.CODEX,
                WorkflowStep.CODEX_IMPLEMENTATION
                if round_number == 1
                else WorkflowStep.CODEX_CORRECTION,
                codex_ready(*(() if round_number == 1 else ("C-01",))),
                round_number=round_number,
            )
        )
        events.append(
            event(
                AgentRole.CLAUDE,
                WorkflowStep.CLAUDE_SLICE_REVIEW,
                denial(AgentRole.CLAUDE, "C-01", previous=round_number > 1),
                round_number=round_number,
            )
        )
        changes.append(change(fingerprint, round_number=round_number))
        validations.append(ScriptedValidation(fingerprint, "pass"))
    scenario = replace(
        happy_scenario(),
        agent_events=tuple(events),
        changes=tuple(changes),
        validations=tuple(validations),
        commits=(),
    )
    report = run(scenario, tmp_path)

    assert report.result.exit_code == 4
    assert report.result.state.current_work_unit.gate.reason is GateReason.ITERATION_LIMIT
    assert report.result.state.current_work_unit.codex_return_count == 4
    assert report.result.state.current_work_unit.round_number == 4


def quota_failure(role: AgentRole, text: str = "usage cap; retry in 5 seconds") -> ScriptedFailure:
    return ScriptedFailure(AgentFailureKind.QUOTA, text, NOW)


@pytest.mark.parametrize(
    ("role", "prefix_events", "step"),
    (
        (AgentRole.CODEX, (), WorkflowStep.CODEX_IMPLEMENTATION),
        (
            AgentRole.CLAUDE,
            (event(AgentRole.CODEX, WorkflowStep.CODEX_IMPLEMENTATION, codex_ready()),),
            WorkflowStep.CLAUDE_SLICE_REVIEW,
        ),
        (
            AgentRole.ANTIGRAVITY,
            (
                event(AgentRole.CODEX, WorkflowStep.CODEX_IMPLEMENTATION, codex_ready()),
                event(
                    AgentRole.CLAUDE,
                    WorkflowStep.CLAUDE_SLICE_REVIEW,
                    approval(AgentRole.CLAUDE),
                ),
            ),
            WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
        ),
    ),
)
def test_quota_is_scriptable_per_role_without_real_sleep(
    tmp_path: Path,
    role: AgentRole,
    prefix_events: tuple[ScriptedAgentEvent, ...],
    step: WorkflowStep,
) -> None:
    good = happy_scenario()
    successful = next(item for item in good.agent_events if item.role is role)
    scenario = replace(
        good,
        agent_events={
            AgentRole.CODEX: (
                event(role, step, failure=quota_failure(role)),
                *good.agent_events,
            ),
            AgentRole.CLAUDE: (
                *prefix_events,
                event(role, step, failure=quota_failure(role)),
                successful,
                good.agent_events[2],
            ),
            AgentRole.ANTIGRAVITY: (
                *prefix_events,
                event(role, step, failure=quota_failure(role)),
                successful,
            ),
        }[role],
        context=replace(
            good.context,
            quota_wait_policy=replace(
                good.context.quota_wait_policy,
                safety_margin_seconds=0,
                maximum_wait_seconds=30,
                heartbeat_interval_seconds=2,
            ),
        ),
    )
    report = run(scenario, tmp_path)

    assert report.result.completed
    assert sum(report.sleeps) == 5
    assert report.heartbeats
    role_calls = [item for item in report.calls if item.startswith(f"agent:{role.value}")]
    assert len(role_calls) == 2


@pytest.mark.parametrize(
    ("kind", "text"),
    (
        (AgentFailureKind.AUTH, "401 unauthorized"),
        (AgentFailureKind.NETWORK, "provider egress connection failed"),
        (AgentFailureKind.PERMISSION, "permission denied"),
        (AgentFailureKind.TIMEOUT, "request timeout"),
        (AgentFailureKind.BINARY, "missing CLI binary"),
        (AgentFailureKind.PROCESS, "Execution error"),
        (AgentFailureKind.OUTPUT, "invalid JSON response"),
    ),
)
def test_non_quota_instance_failure_scenarios_exit_three(
    tmp_path: Path, kind: AgentFailureKind, text: str
) -> None:
    scenario = replace(
        happy_scenario(),
        agent_events=(
            event(
                AgentRole.CODEX,
                WorkflowStep.CODEX_IMPLEMENTATION,
                failure=ScriptedFailure(kind, text, NOW, process_exit_code=7),
            ),
        ),
        validations=(),
        commits=(),
        context=replace(
            happy_scenario().context,
            transient_retry_policy=TransientRetryPolicy(automatic=False),
        ),
    )
    report = run(scenario, tmp_path)

    assert report.result.exit_code == 3
    assert report.result.state.current_work_unit.gate.reason is GateReason.INSTANCE_FAILURE
    assert len([item for item in report.calls if item.startswith("agent:")]) == 1


def test_quota_without_reset_second_quota_changed_fingerprint_and_interrupt(
    tmp_path: Path,
) -> None:
    no_reset = replace(
        happy_scenario(),
        agent_events=(
            event(
                AgentRole.CODEX,
                WorkflowStep.CODEX_IMPLEMENTATION,
                failure=quota_failure(AgentRole.CODEX, "usage cap; try later"),
            ),
        ),
        validations=(),
        commits=(),
    )
    assert run(no_reset, tmp_path).result.exit_code == 2

    second = replace(
        happy_scenario(),
        agent_events=(
            event(
                AgentRole.CODEX,
                WorkflowStep.CODEX_IMPLEMENTATION,
                failure=quota_failure(AgentRole.CODEX),
            ),
            event(
                AgentRole.CODEX,
                WorkflowStep.CODEX_IMPLEMENTATION,
                failure=quota_failure(AgentRole.CODEX),
            ),
        ),
        validations=(),
        commits=(),
        context=replace(
            happy_scenario().context,
            quota_wait_policy=replace(
                happy_scenario().context.quota_wait_policy,
                safety_margin_seconds=0,
                maximum_wait_seconds=30,
                heartbeat_interval_seconds=2,
            ),
        ),
    )
    assert run(second, tmp_path).result.exit_code == 2

    mutated = replace(second, agent_events=second.agent_events[:1], changes=(change(), change(FP2)))
    mutated_report = run(mutated, tmp_path)
    assert mutated_report.result.exit_code == 4
    assert (
        mutated_report.result.state.current_work_unit.gate.reason
        is GateReason.QUOTA_RESUME_DIFF
    )

    interrupted = replace(second, agent_events=second.agent_events[:1], interrupt_on_sleep=1)
    interrupted_report = run(interrupted, tmp_path)
    assert interrupted_report.result.exit_code == 2
    assert interrupted_report.result.state.current_work_unit.status is WorkUnitStatus.WAITING_FOR_QUOTA
    assert "interrupt:quota-wait" in interrupted_report.calls


@pytest.mark.parametrize(
    ("provider_text", "provider_data", "margin", "maximum", "expected_exit", "wait"),
    (
        (
            "usage cap; resets at 2026-08-13T12:00:05+02:00",
            None,
            0,
            30,
            0,
            5,
        ),
        (
            "usage cap reached",
            {"status": "rate_limit", "retry_after_seconds": 4},
            3,
            30,
            0,
            7,
        ),
        (
            "usage cap; resets at 2026-08-13T09:59:59Z",
            None,
            0,
            30,
            0,
            0,
        ),
        (
            "usage cap; retry in 5 seconds or retry in 8 seconds",
            None,
            0,
            30,
            2,
            0,
        ),
        (
            "usage cap; retry in 40 seconds",
            None,
            0,
            30,
            2,
            0,
        ),
    ),
)
def test_quota_time_evidence_and_wait_policy_are_fully_scriptable(
    tmp_path: Path,
    provider_text: str,
    provider_data: dict[str, object] | None,
    margin: int,
    maximum: int,
    expected_exit: int,
    wait: int,
) -> None:
    good = happy_scenario()
    failure = ScriptedFailure(
        AgentFailureKind.QUOTA, provider_text, NOW, provider_data
    )
    tail = good.agent_events if expected_exit == 0 else ()
    scenario = replace(
        good,
        agent_events=(
            event(
                AgentRole.CODEX,
                WorkflowStep.CODEX_IMPLEMENTATION,
                failure=failure,
            ),
            *tail,
        ),
        validations=good.validations if expected_exit == 0 else (),
        commits=good.commits if expected_exit == 0 else (),
        context=replace(
            good.context,
            quota_wait_policy=replace(
                good.context.quota_wait_policy,
                safety_margin_seconds=margin,
                maximum_wait_seconds=maximum,
                heartbeat_interval_seconds=2,
            ),
        ),
    )
    report = run(scenario, tmp_path)

    assert report.result.exit_code == expected_exit
    assert sum(report.sleeps) == wait
    if wait:
        assert report.heartbeats


@pytest.mark.parametrize(
    ("role", "prefix_events", "step"),
    (
        (AgentRole.CODEX, (), WorkflowStep.CODEX_IMPLEMENTATION),
        (
            AgentRole.CLAUDE,
            (event(AgentRole.CODEX, WorkflowStep.CODEX_IMPLEMENTATION, codex_ready()),),
            WorkflowStep.CLAUDE_SLICE_REVIEW,
        ),
        (
            AgentRole.ANTIGRAVITY,
            (
                event(AgentRole.CODEX, WorkflowStep.CODEX_IMPLEMENTATION, codex_ready()),
                event(
                    AgentRole.CLAUDE,
                    WorkflowStep.CLAUDE_SLICE_REVIEW,
                    approval(AgentRole.CLAUDE),
                ),
            ),
            WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
        ),
    ),
)
def test_process_failure_is_scriptable_for_every_role(
    tmp_path: Path,
    role: AgentRole,
    prefix_events: tuple[ScriptedAgentEvent, ...],
    step: WorkflowStep,
) -> None:
    scenario = replace(
        happy_scenario(),
        agent_events=(
            *prefix_events,
            event(
                role,
                step,
                failure=ScriptedFailure(
                    AgentFailureKind.PROCESS,
                    "Execution error",
                    NOW,
                    process_exit_code=7,
                ),
            ),
        ),
        validations=(ScriptedValidation(FP1, "pass"),) if prefix_events else (),
        commits=(),
    )
    report = run(scenario, tmp_path)

    assert report.result.exit_code == 3
    assert report.result.state.current_work_unit.gate.reason is GateReason.INSTANCE_FAILURE
    assert not any(call.startswith("commit:") for call in report.calls)


def test_correction_fingerprint_runs_exactly_one_new_validation(tmp_path: Path) -> None:
    corrected = "\n".join(
        (
            "REVIEWER: claude",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            "FINDING_STATUS: C-01 | CLOSED | corrected",
            "PRE_MORTEM: provider drift",
            "SLICE_APPROVAL: 01 | YES",
            "STATUS: DONE",
        )
    )
    scenario = replace(
        happy_scenario(),
        agent_events=(
            event(AgentRole.CODEX, WorkflowStep.CODEX_IMPLEMENTATION, codex_ready()),
            event(
                AgentRole.CLAUDE,
                WorkflowStep.CLAUDE_SLICE_REVIEW,
                denial(AgentRole.CLAUDE, "C-01"),
            ),
            event(
                AgentRole.CODEX,
                WorkflowStep.CODEX_CORRECTION,
                codex_ready("C-01"),
                round_number=2,
            ),
            event(
                AgentRole.CLAUDE,
                WorkflowStep.CLAUDE_SLICE_REVIEW,
                corrected,
                round_number=2,
            ),
            event(
                AgentRole.ANTIGRAVITY,
                WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
                approval(AgentRole.ANTIGRAVITY),
            ),
        ),
        changes=(change(), change(FP2, round_number=2)),
        validations=(ScriptedValidation(FP1, "pass"), ScriptedValidation(FP2, "pass")),
        commits=(ScriptedCommit(1, FP2, "c" * 40),),
    )
    report = run(scenario, tmp_path)

    assert report.result.completed
    assert report.validation_counts == {FP1: 1, FP2: 1}
    assert [item for item in report.calls if item.startswith("validation:")] == [
        f"validation:{FP1[:8]}:pass",
        f"validation:{FP2[:8]}:pass",
    ]


@pytest.mark.parametrize(
    ("branch", "paths", "maximum", "reason"),
    (
        ("wrong-branch", PATHS, 10, GateReason.STOP_REQUEST),
        (
            "feature/dry-run",
            tuple(f"src/file-{index}.py" for index in range(11)),
            10,
            GateReason.STOP_REQUEST,
        ),
    ),
)
def test_machine_policy_gates_need_no_agent_event(
    tmp_path: Path,
    branch: str,
    paths: tuple[str, ...],
    maximum: int,
    reason: GateReason,
) -> None:
    scenario = DryRunScenario(
        name="machine-gate",
        agent_events=(),
        changes=(change(paths=tuple(sorted(paths))),),
        clock_start=NOW,
        initial=ScriptedInitialState(scope_paths=tuple(sorted(paths))),
        context=ScriptedContext(
            expected_test_files=(),
            current_branch=branch,
            max_productive_files=maximum,
        ),
    )
    report = run(scenario, tmp_path)

    assert report.result.exit_code == 4
    assert report.result.state.current_work_unit.gate.reason is reason
    assert not any(call.startswith("agent:") for call in report.calls)


def test_json_loader_is_strict_and_cli_routes_without_v2_pipeline(
    tmp_path: Path,
) -> None:
    task = tmp_path / "task.md"
    task.write_text("# task\n", encoding="utf-8")
    scenario_path = tmp_path / "scenario.json"
    report_path = tmp_path / "report.json"
    scenario_path.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "cli-stop",
                "agent_events": [
                    {
                        "role": "codex",
                        "work_unit_id": 2,
                        "round_number": 1,
                        "step": "codex_implementation",
                        "output": "STOP_REQUESTED: PRODUCTIVE-FILE-LIMIT | stop\nSTATUS: DONE",
                    }
                ],
                "changes": [
                    {
                        "work_unit_id": 2,
                        "round_number": 1,
                        "start_commit": BASE,
                        "fingerprint": FP1,
                        "paths": ["src/engine.py"],
                        "full_diff": "diff",
                    }
                ],
                "test_changes": [
                    {
                        "diff_fingerprint": FP1,
                        "paths": [TEST_FILE],
                        "test_fingerprint": TEST_FP,
                    }
                ],
                "expect": {
                    "exit_code": 4,
                    "status": "awaiting_user_decision",
                    "step": "codex_implementation",
                    "gate_reason": "stop_request",
                    "commit_count": 0,
                    "audit_contains": ["PRODUCTIVE-FILE-LIMIT"],
                    "agent_order": [
                        "agent:codex:work-unit-2:round-1:codex_implementation"
                    ],
                    "validation_counts": {},
                },
            }
        ),
        encoding="utf-8",
    )
    args = parse_args(
        [
            "--dry-run",
            "--dry-run-scenario",
            str(scenario_path),
            "--dry-run-report",
            str(report_path),
            "--task-file",
            str(task),
        ],
        cwd=tmp_path,
        environ={},
    )
    called = {"pipeline": False}
    rc = run_cli(
        args,
        run_pipeline_fn=lambda *_args, **_kwargs: called.__setitem__("pipeline", True) or 0,
        watch_inbox_fn=lambda **_kwargs: 0,
        find_task_file_fn=lambda path: Path(path or task),
    )

    assert rc == 4
    assert called["pipeline"] is False
    assert json.loads(report_path.read_text(encoding="utf-8"))["exit_code"] == 4
    loaded = load_dry_run_scenario(scenario_path)
    assert loaded.test_changes == (
        ScriptedTestChange(FP1, (TEST_FILE,), TEST_FP),
    )

    raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    raw["unexpected"] = True
    scenario_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DryRunScenarioError, match="unknown key"):
        load_dry_run_scenario(scenario_path)


def test_cli_reports_engine_contract_errors_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dry_run_scenarios

    task = tmp_path / "task.md"
    task.write_text("# task\n", encoding="utf-8")
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text("{}\n", encoding="utf-8")
    args = parse_args(
        [
            "--dry-run",
            "--dry-run-scenario",
            str(scenario_path),
            "--task-file",
            str(task),
        ],
        cwd=tmp_path,
        environ={},
    )

    def raise_contract_error(*_args, **_kwargs) -> None:
        raise WorkflowContractError("scripted invalid verdict")

    monkeypatch.setattr(
        dry_run_scenarios,
        "run_dry_run_scenario_file",
        raise_contract_error,
    )

    rc = run_cli(
        args,
        run_pipeline_fn=lambda *_args, **_kwargs: 99,
        watch_inbox_fn=lambda **_kwargs: 99,
        find_task_file_fn=lambda path: Path(path or task),
    )

    assert rc == 1


@pytest.mark.parametrize(
    "argv",
    (
        ("--development-mode", "--dry-run-scenario", "scenario.json"),
        ("--dry-run-report", "report.json"),
    ),
)
def test_cli_rejects_incomplete_scenario_activation(
    tmp_path: Path, argv: tuple[str, ...]
) -> None:
    with pytest.raises(SystemExit):
        parse_args(list(argv), cwd=tmp_path, environ={})
