from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from contracts import AgentRole
from workflow_state import WorkUnitKind, WorkflowStep


def run_default_dry_run(task_file: Path, *, run_id: str | None = None):
    """Exercise planning and two Slice commits without external effects."""
    from dry_run_scenarios import (
        DryRunScenario,
        ScriptedAgentEvent,
        ScriptedChange,
        ScriptedCommit,
        ScriptedInitialState,
        ScriptedValidation,
        ScriptedWorkflowSession,
        build_scenario_context,
        build_scenario_state,
    )

    base = "a" * 40
    commit1 = "b" * 40
    commit2 = "c" * 40
    plan_fp, first_fp, second_fp = (value * 64 for value in "123")

    def review() -> dict[str, object]:
        return {
            "schema_version": "native-agent-review-result-v2",
            "result_type": "review_result",
            "request_id": "$BOUND_REQUEST_ID",
            "reviewer": "claude",
            "decision": "approved",
            "new_findings": [],
            "status_changes": [],
            "reclassifications": [],
            "responsibility_routes": [],
            "plan_treatment_decisions": [],
            "anchors": [],
            "review_evidence": {
                "dimensions": "correctness, contracts, failure paths, security, resume",
                "largest_residual_risk": "runtime drift",
                "break_condition": "a provider changes its output envelope",
            },
            "pre_mortem": "A resumed invocation uses stale evidence.",
        }

    def codex_result(result_type: str, **fields: object) -> dict[str, object]:
        return {
            "schema_version": "native-agent-codex-result-v2",
            "request_id": "$BOUND_REQUEST_ID",
            "result_type": result_type,
            "ready": True,
            "finding_dispositions": [],
            **fields,
        }

    scenario = DryRunScenario(
        name="default-v3-cutover",
        initial=ScriptedInitialState(
            kind=WorkUnitKind.PLAN,
            slice_count=2,
            scope_paths=("docs/internal/plan.md", "src/first.py", "src/second.py"),
        ),
        agent_events=(
            ScriptedAgentEvent(AgentRole.CODEX, 1, 1, WorkflowStep.CODEX_PLAN,
                               codex_result("plan_result", slice_plan=[
                                   {
                                       "slice_id": 1,
                                       "summary": "Execute the first native Slice.",
                                       "scope_paths": ["src/first.py"],
                                       "acceptance_criteria": ["The first Slice is complete."],
                                   },
                                   {
                                       "slice_id": 2,
                                       "summary": "Execute the second native Slice.",
                                       "scope_paths": ["src/second.py"],
                                       "acceptance_criteria": ["The second Slice is complete."],
                                   },
                               ], plan_treatments=[],
                               plan_completion="IMPLEMENTATION_REQUIRED")),
            ScriptedAgentEvent(AgentRole.CLAUDE, 1, 1, WorkflowStep.CLAUDE_PLAN_REVIEW,
                               review()),
            ScriptedAgentEvent(AgentRole.CODEX, 2, 1, WorkflowStep.CODEX_IMPLEMENTATION,
                               codex_result("implementation_result", test_files=[])),
            ScriptedAgentEvent(AgentRole.CLAUDE, 2, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                               review()),
            ScriptedAgentEvent(AgentRole.CODEX, 3, 1, WorkflowStep.CODEX_IMPLEMENTATION,
                               codex_result("implementation_result", test_files=[])),
            ScriptedAgentEvent(AgentRole.CLAUDE, 3, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                               review()),
        ),
        changes=(
            ScriptedChange(1, 1, base, plan_fp, ("docs/internal/plan.md",), "plan diff"),
            ScriptedChange(2, 1, base, first_fp, ("src/first.py",), "first slice diff"),
            ScriptedChange(3, 1, commit1, second_fp, ("src/second.py",), "second slice diff"),
        ),
        validations=tuple(
            ScriptedValidation(fingerprint, "pass")
            for fingerprint in (plan_fp, first_fp, second_fp)
        ),
        commits=(
            ScriptedCommit(1, first_fp, commit1),
            ScriptedCommit(2, second_fp, commit2),
        ),
    )
    session = ScriptedWorkflowSession(scenario)
    context = build_scenario_context(scenario)
    state = build_scenario_state(scenario, task_file=task_file)
    if run_id is not None:
        state = replace(state, run_id=run_id)
    plan = session.run(state, context)
    state = plan.result.state.start_work_unit(
        slice_id=1, kind=WorkUnitKind.SLICE, step=WorkflowStep.CODEX_IMPLEMENTATION
    ).bind_current_slice_git_boundary(
        start_commit=base, scope_paths=("src/first.py",), start_fingerprint="0" * 64
    )
    first = session.run(state, context)
    state = first.result.state.start_work_unit(
        slice_id=2, kind=WorkUnitKind.SLICE, step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit=commit1,
    ).bind_current_slice_git_boundary(
        start_commit=commit1, scope_paths=("src/second.py",), start_fingerprint="0" * 64
    )
    second = session.run(state, context)
    return second.result, tuple(session.driver.calls), dict(session.driver.validation_counts)
