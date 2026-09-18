from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from dataclasses import replace

from artifact_models import (
    ArtifactRecord,
    Fingerprint,
    FingerprintKind,
    FindingSeverity,
    FindingTransitionPayload,
    Role,
    WorkUnitPayload,
)
from contracts import (
    AgentRole,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    PlannedSlice,
)
from dry_run_scenarios import (
    DryRunScenario,
    ScriptedAgentEvent,
    ScriptedChange,
    ScriptedCommit,
    ScriptedInitialState,
    ScriptedFailure,
    ScriptedValidation,
    ScriptedWorkflowSession,
    build_scenario_context,
    build_scenario_state,
    run_scripted_workflow,
)
from finding_cleanup import (
    FINDING_CLEANUP_BACKTEST_THRESHOLD,
    FINDING_CLEANUP_THRESHOLD,
    derive_slice_finding_balances,
    finding_cleanup_scope_paths,
    is_finding_cleanup_work_unit,
    plan_finding_cleanup,
    positive_balance_streak,
)
from finding_responsibility import (
    BranchPlanningResponsibility,
    SliceResponsibility,
)
from finding_signature import finding_record_signature, mentioned_repository_paths
from workflow import WorkflowHistory
from workflow_audit_projection import _audit_projection
from workflow_state import (
    AgentFailureKind,
    GateStatus,
    SliceStatus,
    WorkUnitKind,
    WorkUnitStatus,
    WorkflowStep,
)


def _finding(index: int, *, path: str | None = None) -> FindingRecord:
    target = path or f"src/finding-{index:02d}.py"
    return FindingRecord(
        finding_id=f"C-{index:02d}",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary=f"The behavior in `{target}` remains reviewable for case {index}.",
        acceptance_test=f"Verify case {index} in `{target}` against the current branch.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )


def _transition_record(
    index: int,
    *,
    work_unit_id: str,
    action: str,
    finding_status: str,
    closure_kind: str | None = None,
    rejection_reason: str | None = None,
    closure_evidence: str | None = None,
    responsibility=None,
) -> ArtifactRecord:
    payload = FindingTransitionPayload(
        finding_id=f"C-{index:02d}",
        reporter=Role.CLAUDE,
        actor=Role.CLAUDE,
        action=action,
        severity=FindingSeverity.OBSERVATION,
        finding_status=finding_status,
        rationale=f"transition {index}",
        work_unit_id=work_unit_id,
        closure_kind=closure_kind,
        rejection_reason=rejection_reason,
        closure_evidence=closure_evidence,
        responsibility=responsibility,
        **(
            {
                "summary": f"Finding {index} affects `src/finding-{index:02d}.py`.",
                "acceptance_test": f"Verify `src/finding-{index:02d}.py`.",
                "origin_slice_id": work_unit_id,
                "origin_round_number": 1,
            }
            if action == "opened"
            else {}
        ),
    )
    return ArtifactRecord.create(
        run_id="finding-cleanup-balance",
        logical_id=f"finding-{index:02d}-{action}-{work_unit_id}",
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64),
        predecessor_ids=(),
        created_at=f"2026-09-12T10:{index:02d}:00+00:00",
        idempotency_key=f"finding-cleanup-{index}-{action}-{work_unit_id}",
        payload=payload,
    )


def _work_unit_record(work_unit_id: str, slice_id: str) -> ArtifactRecord:
    return ArtifactRecord.create(
        run_id="finding-cleanup-balance",
        logical_id=f"work-unit-{work_unit_id}",
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64),
        predecessor_ids=(),
        created_at="2026-09-12T10:00:00+00:00",
        idempotency_key=f"work-unit:{work_unit_id}",
        payload=WorkUnitPayload(slice_id, 1, ("src/finding.py",)),
    )


def test_slice_finding_balance_is_derived_from_slice_work_units_only() -> None:
    records = (
        _work_unit_record("2", "1"),
        _work_unit_record("4", "2"),
        _transition_record(1, work_unit_id="2", action="opened", finding_status="open"),
        _transition_record(2, work_unit_id="2", action="opened", finding_status="open"),
        _transition_record(1, work_unit_id="2", action="status_changed", finding_status="closed"),
        _transition_record(3, work_unit_id="3", action="opened", finding_status="open"),
        _transition_record(4, work_unit_id="4", action="opened", finding_status="open"),
    )

    balances = derive_slice_finding_balances(records)

    assert [(item.slice_id, item.opened, item.closed, item.net) for item in balances] == [
        (1, 2, 1, 1),
        (2, 1, 0, 1),
    ]
    assert positive_balance_streak(balances) == 2


def test_slice_finding_balance_distinguishes_all_typed_e6_outcomes() -> None:
    records = (
        _work_unit_record("2", "1"),
        _transition_record(
            1,
            work_unit_id="2",
            action="status_changed",
            finding_status="closed",
            closure_kind="fixed",
        ),
        _transition_record(
            2,
            work_unit_id="2",
            action="status_changed",
            finding_status="closed",
            closure_kind="rejected",
            rejection_reason="no_defect",
            closure_evidence="The named counterexample passes.",
        ),
        _transition_record(
            3,
            work_unit_id="2",
            action="routed",
            finding_status="open",
            responsibility=SliceResponsibility(
                "finding-cleanup-balance", "b" * 40, "2"
            ),
        ),
        _transition_record(
            4,
            work_unit_id="2",
            action="routed",
            finding_status="open",
            responsibility=BranchPlanningResponsibility("family-1", 1),
        ),
    )

    balance = derive_slice_finding_balances(records)[0]

    assert balance.closed == 2
    assert balance.locally_fixed == 1
    assert balance.rejected == 1
    assert balance.routed_to_later_slice == 1
    assert balance.routed_to_branch_planning == 1


def _materialize_paths(root: Path, paths: tuple[str, ...]) -> None:
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture\n", encoding="utf-8")


def test_cleanup_threshold_triggers_only_at_the_record_derived_limit(
    tmp_path: Path,
) -> None:
    assert FINDING_CLEANUP_THRESHOLD is None
    assert FINDING_CLEANUP_BACKTEST_THRESHOLD == 18
    below = tuple(
        _finding(index) for index in range(1, FINDING_CLEANUP_BACKTEST_THRESHOLD)
    )
    at_limit = (*below, _finding(FINDING_CLEANUP_BACKTEST_THRESHOLD))
    _materialize_paths(
        tmp_path,
        tuple(f"src/finding-{index:02d}.py" for index in range(1, 19)),
    )

    assert plan_finding_cleanup(
        below,
        repository_root=tmp_path,
        threshold=FINDING_CLEANUP_BACKTEST_THRESHOLD,
    ) is None
    oversized_backlog = tuple(_finding(index) for index in range(1, 1_001))
    assert plan_finding_cleanup(oversized_backlog, repository_root=tmp_path) is None
    plan = plan_finding_cleanup(
        at_limit,
        repository_root=tmp_path,
        threshold=FINDING_CLEANUP_BACKTEST_THRESHOLD,
    )
    assert plan is not None
    assert len(plan.finding_ids) == FINDING_CLEANUP_BACKTEST_THRESHOLD
    assert plan_finding_cleanup(
        at_limit,
        repository_root=tmp_path,
        previously_addressed_ids=plan.finding_ids,
        threshold=FINDING_CLEANUP_BACKTEST_THRESHOLD,
    ) is None


def test_cleanup_scope_is_exact_union_of_paths_named_by_selected_findings(
    tmp_path: Path,
) -> None:
    findings = (
        _finding(1, path="src/shared.py"),
        _finding(2, path="tests/test_shared.py"),
        _finding(3, path="src/outside.py"),
    )

    _materialize_paths(tmp_path, ("src/shared.py", "tests/test_shared.py"))
    assert finding_cleanup_scope_paths(
        findings,
        ("C-01", "C-02"),
        repository_root=tmp_path,
    ) == (
        "src/shared.py",
        "tests/test_shared.py",
    )


def test_cleanup_scope_retains_paths_after_an_offered_finding_closes(
    tmp_path: Path,
) -> None:
    offered = (_finding(1, path="src/closed.py"), _finding(2, path="src/open.py"))
    after_review = (
        FindingRecord(
            finding_id=offered[0].finding_id,
            finding_class=offered[0].finding_class,
            status=FindingStatus.CLOSED,
            summary=offered[0].summary,
            acceptance_test=offered[0].acceptance_test,
            origin=offered[0].origin,
            status_rationale="The cleanup review closed this offered finding.",
        ),
        offered[1],
    )

    _materialize_paths(tmp_path, ("src/closed.py", "src/open.py"))
    assert finding_cleanup_scope_paths(
        after_review,
        ("C-01", "C-02"),
        repository_root=tmp_path,
    ) == (
        "src/closed.py",
        "src/open.py",
    )


def test_cleanup_scope_includes_paths_from_later_occurrence_rationales(
    tmp_path: Path,
) -> None:
    finding = replace(
        _finding(1, path="src/original.py"),
        status_rationale=(
            "Additional occurrence merged into C-01 at "
            "app/public/assets/data.json."
        ),
    )
    _materialize_paths(
        tmp_path, ("src/original.py", "app/public/assets/data.json")
    )

    assert finding_cleanup_scope_paths(
        (finding,), ("C-01",), repository_root=tmp_path
    ) == ("app/public/assets/data.json", "src/original.py")


def _codex_result(result_type: str, *, finding_ids: tuple[str, ...] = ()) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": "native-agent-codex-result-v2",
        "request_id": "$BOUND_REQUEST_ID",
        "result_type": result_type,
        "ready": True,
        "finding_dispositions": [
            {
                "finding_id": finding_id,
                "decision": "accepted",
                "rationale": f"The current branch was checked for {finding_id}.",
            }
            for finding_id in finding_ids
        ],
    }
    if result_type in {"implementation_result", "correction_result"}:
        result["test_files"] = []
    if result_type == "final_report_result":
        result["self_check"] = "The complete finding ledger was checked."
    return result


def _review_result(
    *,
    approved: bool,
    new_observations: tuple[FindingRecord, ...] = (),
    closed_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": "$BOUND_REQUEST_ID",
        "reviewer": "claude",
        "decision": "approved" if approved else "denied",
        "new_findings": [
            {
                "finding_id": finding.finding_id,
                "finding_class": "OBSERVATION",
                "summary": finding.summary,
                "acceptance_test": {
                    "kind": "prose",
                    "text": finding.acceptance_test,
                },
            }
            for finding in new_observations
        ],
        "status_changes": [
            {
                "finding_id": finding_id,
                "status": "CLOSED",
                "rationale": f"The current branch makes {finding_id} obsolete.",
            }
            for finding_id in closed_ids
        ],
        "reclassifications": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "correctness, contracts, failure paths, security, resume",
            "largest_residual_risk": "a later change could invalidate a disposition",
            "break_condition": "a selected finding escapes the exact cleanup scope",
        },
        "pre_mortem": "A future transition could accidentally turn cleanup into a gate.",
    }


def _diff(paths: tuple[str, ...], label: str) -> str:
    return "".join(
        f"diff --git a/{path} b/{path}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-before {label}\n"
        f"+after {label}\n"
        for path in paths
    )


def _cleanup_findings() -> tuple[FindingRecord, ...]:
    """Return exactly enough open observations to reach the threshold."""

    return tuple(
        _finding(index)
        for index in range(1, FINDING_CLEANUP_BACKTEST_THRESHOLD + 1)
    )


def _cleanup_finding_ids() -> tuple[str, ...]:
    return tuple(item.finding_id for item in _cleanup_findings())


def _cleanup_scope_paths() -> tuple[str, ...]:
    return tuple(
        f"src/finding-{index:02d}.py"
        for index in range(1, FINDING_CLEANUP_BACKTEST_THRESHOLD + 1)
    )


def _real_cleanup_scenario() -> DryRunScenario:
    """Build the scenario whose backlog forces one real sliceless cleanup."""

    findings = tuple(
        _finding(index)
        for index in range(1, FINDING_CLEANUP_BACKTEST_THRESHOLD + 1)
    )
    finding_ids = tuple(item.finding_id for item in findings)
    cleanup_scope = tuple(
        f"src/finding-{index:02d}.py"
        for index in range(1, FINDING_CLEANUP_BACKTEST_THRESHOLD + 1)
    )
    slice_one_scope = tuple(sorted((*cleanup_scope, "src/slice-one.py")))
    base, first_commit, second_commit = "a" * 40, "b" * 40, "c" * 40
    fingerprints = tuple(str(index) * 64 for index in range(1, 5))
    scenario = DryRunScenario(
        name="real-sliceless-finding-cleanup",
        initial=ScriptedInitialState(
            kind=WorkUnitKind.SLICE,
            slice_count=2,
            scope_paths=slice_one_scope,
            work_plan_path="docs/internal/finding-cleanup-test-plan.md",
            approved_plan_commit=base,
            planned_slices=(
                PlannedSlice(1, "Open the measured cross-cutting observations.", slice_one_scope),
                PlannedSlice(2, "Continue with the next planned Slice.", ("src/slice-two.py",)),
            ),
        ),
        agent_events=(
            ScriptedAgentEvent(
                AgentRole.CODEX, 2, 1, WorkflowStep.CODEX_IMPLEMENTATION,
                _codex_result("implementation_result"),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 2, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                _review_result(approved=True, new_observations=findings),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 3, 1, WorkflowStep.CLAUDE_FINAL_REVIEW,
                _review_result(approved=False),
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX, 4, 1, WorkflowStep.CODEX_IMPLEMENTATION,
                _codex_result("implementation_result"),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 4, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                _review_result(approved=True),
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX, 5, 1, WorkflowStep.CODEX_FINAL_REVIEW,
                _codex_result("final_report_result", finding_ids=finding_ids),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 5, 1, WorkflowStep.CLAUDE_FINAL_REVIEW,
                _review_result(approved=True, closed_ids=finding_ids),
            ),
        ),
        changes=(
            ScriptedChange(2, 1, base, fingerprints[0], ("src/slice-one.py",), _diff(("src/slice-one.py",), "slice one")),
            ScriptedChange(3, 1, base, fingerprints[1], slice_one_scope, _diff(slice_one_scope, "cleanup view")),
            ScriptedChange(4, 1, first_commit, fingerprints[2], ("src/slice-two.py",), _diff(("src/slice-two.py",), "slice two")),
            ScriptedChange(5, 1, base, fingerprints[3], ("src/slice-one.py", "src/slice-two.py"), _diff(("src/slice-one.py", "src/slice-two.py"), "final branch")),
        ),
        validations=tuple(ScriptedValidation(item) for item in fingerprints),
        commits=(
            ScriptedCommit(1, fingerprints[0], first_commit),
            ScriptedCommit(2, fingerprints[2], second_commit),
        ),
    )
    return scenario


def _cleanup_task(tmp_path: Path) -> Path:
    _materialize_paths(tmp_path, _cleanup_scope_paths())
    task = tmp_path / "task.md"
    task.write_text("exercise the real cleanup work unit", encoding="utf-8")
    return task


def test_cleanup_scope_filters_slash_prose_and_repairs_real_trailing_parenthesis(
    tmp_path: Path,
) -> None:
    _materialize_paths(tmp_path, ("app/src/core/models.ts",))
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary=(
            "Ratios `1/2`, API names `assign/replace/move`, and variables "
            "`--a/--b` are prose. Top-level `src/)` and `tests/)` references "
            "must not authorize whole trees. The model lives at "
            "`app/src/core/models.ts)`."
        ),
        acceptance_test="Verify (`app/src/core/models.ts`) without slash prose.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )

    assert finding_cleanup_scope_paths(
        (finding,),
        ("C-01",),
        repository_root=tmp_path,
    ) == ("app/src/core/models.ts",)


def test_cleanup_authorization_filter_does_not_change_b113_signature(
    tmp_path: Path,
) -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="Check `app/src/core/models.ts` and the ratio `1/2`.",
        acceptance_test="Keep `assign/replace/move` distinct.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    before_paths = mentioned_repository_paths(
        finding.summary, finding.acceptance_test
    )
    before_signature = finding_record_signature(finding)
    _materialize_paths(tmp_path, ("app/src/core/models.ts",))

    assert finding_cleanup_scope_paths(
        (finding,),
        ("C-01",),
        repository_root=tmp_path,
    ) == ("app/src/core/models.ts",)
    assert mentioned_repository_paths(
        finding.summary, finding.acceptance_test
    ) == before_paths
    assert finding_record_signature(finding) == before_signature


def test_real_sliceless_cleanup_no_progress_continues_with_next_slice(
    tmp_path: Path,
) -> None:
    report = run_scripted_workflow(
        scenario=_real_cleanup_scenario(), task_file=_cleanup_task(tmp_path)
    )

    finding_ids = _cleanup_finding_ids()
    cleanup_scope = _cleanup_scope_paths()
    state = report.result.state
    cleanup = state.work_units[2]
    assert report.result.workflow_completed
    assert len(state.slices) == len(state.planned_slices) == 2
    assert cleanup.kind is WorkUnitKind.CORRECTION
    assert cleanup.slice_id == 1
    assert cleanup.status is WorkUnitStatus.COMPLETED
    assert cleanup.open_findings == finding_ids
    assert is_finding_cleanup_work_unit(state, cleanup)
    assert state.work_units[3].kind is WorkUnitKind.SLICE
    assert state.work_units[3].slice_id == 2
    assert all(unit.kind is not WorkUnitKind.CORRECTION for unit in state.work_units[3:])
    cleanup_request = next(
        item for item in report.reviewer_invocations if item.work_unit_id == 3
    )
    assert cleanup_request.paths == cleanup_scope
    assert cleanup_request.native_request is not None
    assert cleanup_request.native_request.document["authorized_paths"] == list(cleanup_scope)
    assert cleanup_request.review_packet is None
    assert not any(
        checkpoint.current_work_unit.gate.status is not GateStatus.CLEAR
        for checkpoint in report.checkpoints
    )


def test_cleanup_output_failure_retries_with_the_same_authorized_scope(
    tmp_path: Path,
) -> None:
    scenario = _real_cleanup_scenario()
    events = list(scenario.agent_events)
    cleanup_review = next(
        index
        for index, event in enumerate(events)
        if event.work_unit_id == 3
        and event.step is WorkflowStep.CLAUDE_FINAL_REVIEW
    )
    events.insert(
        cleanup_review,
        ScriptedAgentEvent(
            AgentRole.CLAUDE,
            3,
            1,
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            failure=ScriptedFailure(
                failure_kind=AgentFailureKind.OUTPUT,
                provider_text="native Claude error",
                received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                provider_data={
                    "type": "result",
                    "subtype": "error_max_structured_output_retries",
                },
                process_exit_code=1,
            ),
        ),
    )
    scenario = replace(
        scenario,
        name="cleanup-output-retry-scope",
        agent_events=tuple(events),
    )

    task = _cleanup_task(tmp_path)
    session = ScriptedWorkflowSession(scenario)
    context = build_scenario_context(scenario)
    first = session.run(
        build_scenario_state(scenario, task_file=task),
        context,
    )
    findings = session.driver.carry_forward_native_findings(
        first.result.state, first.result.history.findings
    )
    cleanup_plan = plan_finding_cleanup(
        findings,
        repository_root=tmp_path,
        threshold=FINDING_CLEANUP_BACKTEST_THRESHOLD,
    )
    assert cleanup_plan is not None
    cleanup_state = first.result.state.start_finding_cleanup_work_unit(
        finding_ids=cleanup_plan.finding_ids
    )
    session.driver._cleanup_scope_by_unit[
        cleanup_state.current_work_unit_id
    ] = cleanup_plan.scope_paths
    cleanup_history = WorkflowHistory(
        cleanup_state.current_work_unit_id,
        findings=findings,
        attestations=first.result.history.attestations[-1:],
    )
    session.driver.bind_work_unit(cleanup_state)
    session.driver.checkpoint(cleanup_state, cleanup_history)
    report = session.run(cleanup_state, context, cleanup_history)

    assert report.result.completed
    cleanup_calls = tuple(
        item for item in report.reviewer_invocations if item.work_unit_id == 3
    )
    assert len(cleanup_calls) == 2
    assert cleanup_calls[0].paths == cleanup_calls[1].paths == _cleanup_scope_paths()
    assert cleanup_calls[0].native_request is not None
    assert cleanup_calls[1].native_request is not None
    assert (
        cleanup_calls[0].native_request.document["authorized_paths"]
        == cleanup_calls[1].native_request.document["authorized_paths"]
        == list(_cleanup_scope_paths())
    )


def test_cleanup_over_a_completed_slice_never_claims_commit_authorization(
    tmp_path: Path,
) -> None:
    """A cleanup reuses a finished Slice id and must not inherit its commit proof.

    The Slice record proves a commit for the unit that owns it.  Because the
    cleanup deliberately reuses that id, the audit projection would otherwise
    authorize a commit before the cleanup has any review of its own, and the
    audit guard would halt the run.
    """

    report = run_scripted_workflow(
        scenario=_real_cleanup_scenario(), task_file=_cleanup_task(tmp_path)
    )
    state = report.result.state
    cleanup = state.work_units[2]
    assert is_finding_cleanup_work_unit(state, cleanup)
    assert next(
        item for item in state.slices if item.slice_id == cleanup.slice_id
    ).status is SliceStatus.COMPLETED

    fresh = _audit_projection(state, cleanup, WorkflowHistory(cleanup.work_unit_id))
    assert fresh.commit_authorized is False

    # Every checkpoint the driver writes projects its own work unit, so the
    # cleanup checkpoints are exactly the ones that halted the Cookbook run.
    projected = [
        (
            checkpoint.current_work_unit,
            _audit_projection(checkpoint, checkpoint.current_work_unit, history),
        )
        for checkpoint, history in zip(
            report.checkpoints, report.checkpoint_histories, strict=True
        )
    ]
    assert any(
        is_finding_cleanup_work_unit(state, unit) for unit, _ in projected
    )
    assert not any(
        projection.commit_authorized
        for unit, projection in projected
        if is_finding_cleanup_work_unit(state, unit)
    )
    # The unit that owns the Slice record keeps its unchanged commit proof.
    assert any(
        projection.commit_authorized
        for unit, projection in projected
        if unit.kind is WorkUnitKind.SLICE and unit.slice_id == cleanup.slice_id
    )


def test_workflow_without_finding_backlog_has_no_cleanup_unit(tmp_path: Path) -> None:
    base, commit = "a" * 40, "b" * 40
    scenario = DryRunScenario(
        name="finding-cleanup-no-backlog",
        initial=ScriptedInitialState(
            kind=WorkUnitKind.SLICE,
            slice_count=1,
            scope_paths=("src/ordinary.py",),
        ),
        agent_events=(
            ScriptedAgentEvent(
                AgentRole.CODEX, 2, 1, WorkflowStep.CODEX_IMPLEMENTATION,
                _codex_result("implementation_result"),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 2, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                _review_result(approved=True),
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX, 3, 1, WorkflowStep.CODEX_FINAL_REVIEW,
                _codex_result("final_report_result"),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 3, 1, WorkflowStep.CLAUDE_FINAL_REVIEW,
                _review_result(approved=True),
            ),
        ),
        changes=(
            ScriptedChange(
                2, 1, base, "1" * 64, ("src/ordinary.py",),
                _diff(("src/ordinary.py",), "ordinary slice"),
            ),
            ScriptedChange(
                3, 1, base, "2" * 64, ("src/ordinary.py",),
                _diff(("src/ordinary.py",), "ordinary final"),
            ),
        ),
        validations=(ScriptedValidation("1" * 64), ScriptedValidation("2" * 64)),
        commits=(ScriptedCommit(1, "1" * 64, commit),),
    )
    task = tmp_path / "task.md"
    task.write_text("ordinary run without findings", encoding="utf-8")

    report = run_scripted_workflow(scenario=scenario, task_file=task)

    assert report.result.workflow_completed
    assert tuple(unit.kind for unit in report.result.state.work_units) == (
        WorkUnitKind.PLAN,
        WorkUnitKind.SLICE,
        WorkUnitKind.FINAL_REVIEW,
    )
