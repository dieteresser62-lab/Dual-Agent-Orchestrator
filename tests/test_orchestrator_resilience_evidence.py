from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

import orchestrator
import agent_runtime
from agent_runtime import classify_agent_failure
from artifact_models import technical_text_evidence
from cli import parse_args
from contracts import FindingStatus
from dry_run_scenarios import (
    DryRunScenarioError,
    ResilienceEvidenceRow,
    ScenarioExpectations,
    ScenarioGateExpectation,
    ScriptedRunReport,
    build_progressive_correction_scenario,
    render_resilience_evidence,
    run_scripted_workflow,
    verify_scenario_expectations,
)
from inbox_watcher import (
    WatchTaskDisposition,
    WatchTaskIdentity,
    WatchTaskResult,
    save_watch_identity,
    success_marker_path,
    watch_identity_path,
)
from workflow import WorkflowHistory, WorkflowRunResult
from workflow_state import (
    AgentFailureKind,
    GateRecord,
    GateReason,
    GateStatus,
    InvocationFailureRecord,
    ProtocolBinding,
    ProtocolMode,
    Reviewer,
    SliceStatus,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "tests/fixtures/orchestrator_resilience/manifest-v1.json"
)
EXPECTED_SCENARIOS = {
    "commit-head-drift",
    "direct-resume-queue-finalization",
    "exact-structured-output-retry",
    "happy-path",
    "record-ahead-resume",
    "scope-violation",
    "slice-boundary-drift",
    "slice-correction",
    "structured-output-near-miss",
}
SYNTHETIC_TECHNICAL_TEXT = technical_text_evidence(
    "synthetic invocation failure"
)[0]


def _evidence_node(scenario_id: str) -> str:
    return (
        "tests/test_orchestrator_resilience_evidence.py::"
        f"test_provider_free_resilience_scenario[{scenario_id}]"
    )


EVIDENCE_ORACLE = {
    "happy-path": (
        "autonomy",
        "Plan und zwei Slices terminieren ohne Gate; drei Validierungen und zwei Commits laufen genau einmal; die Branchentdeckung ist ein verknüpfter Folgelauf.",
    ),
    "slice-correction": (
        "autonomy",
        "Slice-Denial führt über Codex-Korrektur und Reviewrunde zwei zur gebundenen Folgekante.",
    ),
    "exact-structured-output-retry": (
        "availability",
        "Der Strukturfehler-Subtype wird als Output typisiert und bleibt ohne verworfenen Modellinhalt transient wiederholbar.",
    ),
    "structured-output-near-miss": (
        "security",
        "Nicht passende oder fehlende Strukturfehler-Subtypes bleiben fail-closed.",
    ),
    "record-ahead-resume": (
        "availability",
        "Ein persistiertes natives Review wird vor Policy und Provider gespiegelt; Claude wird beim Resume nicht erneut aufgerufen.",
    ),
    "commit-head-drift": (
        "security",
        "Commit-HEAD-Drift erzeugt ein fingerprint- und pfadgebundenes Nutzergate mit HEAD-DRIFT und Resume bei slice_commit.",
    ),
    "slice-boundary-drift": (
        "security",
        "Slice-Boundary-Drift erzeugt ein pfadloses Policygate mit SLICE-HEAD-DRIFT und ohne Resume-Schritt.",
    ),
    "scope-violation": (
        "security",
        "Eine Scopeverletzung erzeugt ein fingerprintgebundenes Nutzergate mit UNEXPECTED-PATH und exakt dem betroffenen Pfadsatz.",
    ),
    "direct-resume-queue-finalization": (
        "autonomy",
        "Ein direkter Watch-Origin-Resume führt den terminalen Workflow und die Outbox-Finalisierung genau einmal aus.",
    ),
}


def _manifest_rows() -> tuple[ResilienceEvidenceRow, ...]:
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert set(document) == {"schema_version", "scenarios"}
    assert document["schema_version"] == "orchestrator-resilience-evidence-v1"
    return tuple(ResilienceEvidenceRow(**item) for item in document["scenarios"])


def _active_slice_state():
    state = init_workflow_state(
        run_id="dry-resilience-gate",
        task_file="/repo/task.md",
        branch="feature/dry-resilience",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        task_digest="b" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/dry-resilience",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
        timestamp="2026-08-27T12:00:00+00:00",
    ).complete_current_work_unit(updated_at="2026-08-27T12:00:01+00:00")
    state = state.start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.IMPLEMENTER_IMPLEMENTATION,
        updated_at="2026-08-27T12:00:02+00:00",
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=("src/runtime.py",),
        start_fingerprint="c" * 64,
        updated_at="2026-08-27T12:00:03+00:00",
    )
    return state


def _state_report(state) -> ScriptedRunReport:
    result = WorkflowRunResult(state, WorkflowHistory(state.current_work_unit_id))
    return ScriptedRunReport(result, (), {}, "{}", (), (), 0)


def _gate_report(*, kind: str) -> ScriptedRunReport:
    state = _active_slice_state()
    if kind == "boundary":
        state = state.await_policy_gate(
            reason=GateReason.UNEXPECTED_FILE,
            detail="SLICE-HEAD-DRIFT | persisted start HEAD differs",
        )
    elif kind == "commit":
        state = state.with_current_step(WorkflowStep.SLICE_COMMIT).await_user_gate(
            reason=GateReason.UNEXPECTED_FILE,
            detail="HEAD-DRIFT | reviewed descendant HEAD differs",
            fingerprint="d" * 64,
            paths=("src/runtime.py",),
            resume_step=WorkflowStep.SLICE_COMMIT,
        )
    elif kind == "scope":
        state = state.await_user_gate(
            reason=GateReason.UNEXPECTED_FILE,
            detail="UNEXPECTED-PATH | canonical changes contain unauthorized paths",
            fingerprint="e" * 64,
            paths=("src/outside.py",),
        )
    else:
        raise AssertionError(f"unknown gate fixture {kind}")
    return _state_report(state)


def test_provider_free_happy_path_completes_implementation_run(tmp_path: Path) -> None:
    task = tmp_path / "task.md"
    task.write_text("provider-free resilience", encoding="utf-8")

    result, calls, validations = orchestrator.run_default_dry_run(task)

    assert result.workflow_completed
    assert result.state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
    assert result.state.current_step is WorkflowStep.COMPLETED
    assert tuple(call for call in calls if call.startswith("agent:")) == (
        "agent:codex:work-unit-1:request-1:codex_plan",
        "agent:claude:work-unit-1:request-1:claude_plan_review",
        "agent:codex:work-unit-2:request-1:codex_implementation",
        "agent:claude:work-unit-2:request-1:claude_slice_review",
        "agent:codex:work-unit-3:request-1:codex_implementation",
        "agent:claude:work-unit-3:request-1:claude_slice_review",
        "agent:claude:work-unit-4:request-1:claude_final_review",
    )
    assert sum(call.startswith("commit:") for call in calls) == 2
    assert sum(validations.values()) == 4
    assert result.history.findings == ()


def test_provider_free_correction_uses_all_six_rounds(tmp_path: Path) -> None:
    task = tmp_path / "progressive-correction.md"
    task.write_text("provider-free progressive correction", encoding="utf-8")

    report = run_scripted_workflow(
        scenario=build_progressive_correction_scenario(), task_file=task
    )

    correction = report.result.state.current_work_unit
    assert report.result.workflow_completed
    assert report.remaining_agent_events == 0
    assert correction.kind is WorkUnitKind.FINAL_REVIEW
    corrected_slice = report.result.state.work_units[-2]
    assert corrected_slice.kind is WorkUnitKind.SLICE
    assert corrected_slice.round_number == 6
    assert corrected_slice.implementer_return_count == 5
    assert corrected_slice.max_implementer_returns == 6
    assert correction.gate.status is GateStatus.CLEAR
    assert sum(call.startswith("commit:") for call in report.calls) == 1
    assert all(
        item.status is FindingStatus.CLOSED
        for item in report.result.history.findings
    )


def test_provider_free_stalled_correction_is_a_named_terminal_verdict(
    tmp_path: Path,
) -> None:
    task = tmp_path / "stalled-correction.md"
    task.write_text("provider-free stalled correction", encoding="utf-8")

    report = run_scripted_workflow(
        scenario=build_progressive_correction_scenario(stalled=True),
        task_file=task,
    )

    result = report.result
    assert result.workflow_rejected
    assert result.exit_code == 5
    assert result.state.current_work_unit.kind is WorkUnitKind.SLICE
    assert result.state.current_work_unit.status is WorkUnitStatus.COMPLETED
    assert result.state.current_work_unit.gate.status is GateStatus.CLEAR
    assert result.rejection_code == "SLICE-REVIEW-DENIED"
    assert result.rejection_detail is not None
    assert "no attested fingerprint-changing remediation" in (
        result.rejection_detail
    )
    assert "remaining open findings: C-01" in result.rejection_detail
    assert report.remaining_agent_events == 0
    assert sum(call.startswith("commit:") for call in report.calls) == 0
    watch_result = WatchTaskResult.from_workflow(result)
    assert watch_result.disposition is WatchTaskDisposition.REJECTED
    assert watch_result.status == "rejected"
    assert watch_result.gate_reason == "SLICE-REVIEW-DENIED"
    assert watch_result.resume_available is False


def _run_structured_output_probe(scenario_id: str) -> None:
    exact = {
        "type": "result",
        "subtype": "error_max_structured_output_retries",
    }
    if scenario_id == "exact-structured-output-retry":
        failure = classify_agent_failure(
            "claude",
            agent_runtime.AgentOutputError(
                "native Claude error", exit_code=1, provider_data=exact
            ),
            invocation_id=scenario_id,
        )
        assert failure.kind.value == "output"
        assert failure.provider_data == exact
        return
    near_misses = (
        ("codex", {**exact, "subtype": "error_max_structured_output_retry"}),
        ("claude", {"type": "error", "subtype": "different_error"}),
        ("claude", {**exact, "subtype": "error_max_structured_output_retry"}),
        ("claude", {"type": "result"}),
    )
    for provider, envelope in near_misses:
        failure = classify_agent_failure(
            provider,
            agent_runtime.AgentProcessError(
                "native provider error", exit_code=1, provider_data=envelope
            ),
            invocation_id=scenario_id,
        )
        assert failure.kind.value == "process"


def _run_record_ahead_probe() -> None:
    path = REPOSITORY_ROOT / "tests/test_workflow.py"
    spec = importlib.util.spec_from_file_location("resilience_record_ahead", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        module.test_native_record_ahead_review_is_mirrored_before_next_policy_or_provider()
    finally:
        sys.modules.pop(spec.name, None)


def _run_queue_resume_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "runtime-resume.md"
    task.write_text("runtime-bound resume", encoding="utf-8")
    digest = hashlib.sha256(task.read_bytes()).hexdigest()
    identity = WatchTaskIdentity("watch-runtime-resume", digest, True, "structured-v2", 2)
    save_watch_identity(task, identity)
    terminal, _, _ = orchestrator.run_default_dry_run(task, run_id=identity.run_id)
    terminal = WorkflowRunResult(replace(terminal.state, task_digest=digest), terminal.history)
    calls = {"workflow": 0}

    def completed_workflow(*_args, **_kwargs) -> WorkflowRunResult:
        calls["workflow"] += 1
        return terminal

    monkeypatch.setattr(orchestrator, "run_production_workflow", completed_workflow)
    args = parse_args(
        [
            "--resume", "--task-file", str(task),
            "--inbox-dir", str(inbox), "--outbox-dir", str(outbox),
        ],
        cwd=tmp_path,
        environ={},
    )
    assert orchestrator.run_pipeline(task, args) == 0
    assert calls == {"workflow": 1}
    assert len(list((outbox / "done").glob("*.md"))) == 1
    assert not task.exists()
    assert not success_marker_path(task).exists()
    assert not watch_identity_path(task).exists()


def _run_gate_probe(scenario_id: str) -> None:
    fixture, expected = {
        "commit-head-drift": (
            "commit",
            ScenarioGateExpectation(
                GateStatus.AWAITING_USER_DECISION,
                GateReason.UNEXPECTED_FILE,
                "HEAD-DRIFT",
                "user",
                "d" * 64,
                ("src/runtime.py",),
                WorkflowStep.SLICE_COMMIT,
            ),
        ),
        "slice-boundary-drift": (
            "boundary",
            ScenarioGateExpectation(
                GateStatus.AWAITING_USER_DECISION,
                GateReason.UNEXPECTED_FILE,
                "SLICE-HEAD-DRIFT",
                "policy",
            ),
        ),
        "scope-violation": (
            "scope",
            ScenarioGateExpectation(
                GateStatus.AWAITING_USER_DECISION,
                GateReason.UNEXPECTED_FILE,
                "UNEXPECTED-PATH",
                "user",
                "e" * 64,
                paths=("src/outside.py",),
            ),
        ),
    }[scenario_id]
    verify_scenario_expectations(
        _gate_report(kind=fixture), ScenarioExpectations(exit_code=4, gate=expected)
    )


@pytest.mark.parametrize("scenario_id", sorted(EXPECTED_SCENARIOS), ids=sorted(EXPECTED_SCENARIOS))
def test_provider_free_resilience_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario_id: str
) -> None:
    if scenario_id == "happy-path":
        test_provider_free_happy_path_completes_implementation_run(tmp_path)
    elif scenario_id == "slice-correction":
        test_provider_free_correction_uses_all_six_rounds(tmp_path)
    elif scenario_id in {"exact-structured-output-retry", "structured-output-near-miss"}:
        _run_structured_output_probe(scenario_id)
    elif scenario_id == "record-ahead-resume":
        _run_record_ahead_probe()
    elif scenario_id in {"commit-head-drift", "slice-boundary-drift", "scope-violation"}:
        _run_gate_probe(scenario_id)
    elif scenario_id == "direct-resume-queue-finalization":
        _run_queue_resume_probe(tmp_path, monkeypatch)
    else:
        raise AssertionError(f"unbound resilience scenario {scenario_id}")


def test_resilience_manifest_is_closed_and_binds_executed_literal_oracle() -> None:
    rows = _manifest_rows()
    assert {item.scenario_id for item in rows} == EXPECTED_SCENARIOS
    assert len(rows) == len(EXPECTED_SCENARIOS)
    assert {
        row.scenario_id: (row.dimension, row.expected_result, row.evidence_test)
        for row in rows
    } == {
        scenario_id: (*EVIDENCE_ORACLE[scenario_id], _evidence_node(scenario_id))
        for scenario_id in EXPECTED_SCENARIOS
    }
    assert len({row.evidence_test for row in rows}) == len(rows)


def test_resilience_report_is_byte_stable_and_dimensionally_separated() -> None:
    rows = _manifest_rows()
    first = render_resilience_evidence(rows)
    second = render_resilience_evidence(tuple(reversed(rows)))

    assert first == second
    assert first.startswith("# Providerfreier Resilienznachweis\n")
    assert first.count("## Security") == 1
    assert first.count("## Availability") == 1
    assert first.count("## Autonomy") == 1
    assert "elapsed" not in first
    assert "tokens" not in first
    assert "cost" not in first


def test_dry_run_gate_expectation_binds_full_rule_identity() -> None:
    report = _gate_report(kind="commit")
    expected = ScenarioExpectations(
        exit_code=4,
        gate=ScenarioGateExpectation(
            status=GateStatus.AWAITING_USER_DECISION,
            reason=GateReason.UNEXPECTED_FILE,
            rule_id="HEAD-DRIFT",
            kind="user",
            fingerprint="d" * 64,
            paths=("src/runtime.py",),
            resume_step=WorkflowStep.SLICE_COMMIT,
        ),
    )

    verify_scenario_expectations(report, expected)

    boundary_report = _gate_report(kind="boundary")
    with pytest.raises(DryRunScenarioError, match="gate identity expected"):
        verify_scenario_expectations(boundary_report, expected)


def test_dry_run_gate_expectation_rejects_prefix_substring_mutation() -> None:
    report = _gate_report(kind="boundary")
    exact_boundary = ScenarioExpectations(
        exit_code=4,
        gate=ScenarioGateExpectation(
            status=GateStatus.AWAITING_USER_DECISION,
            reason=GateReason.UNEXPECTED_FILE,
            rule_id="SLICE-HEAD-DRIFT",
            kind="policy",
            paths=(),
        ),
    )
    verify_scenario_expectations(report, exact_boundary)

    substring_only = ScenarioExpectations(
        exit_code=4,
        gate=ScenarioGateExpectation(
            status=GateStatus.AWAITING_USER_DECISION,
            reason=GateReason.UNEXPECTED_FILE,
            rule_id="HEAD-DRIFT",
            kind="policy",
            paths=(),
        ),
    )
    with pytest.raises(DryRunScenarioError, match="gate identity expected"):
        verify_scenario_expectations(report, substring_only)


def test_scope_gate_expectation_requires_production_user_binding() -> None:
    report = _gate_report(kind="scope")
    expected = ScenarioGateExpectation(
        GateStatus.AWAITING_USER_DECISION,
        GateReason.UNEXPECTED_FILE,
        "UNEXPECTED-PATH",
        "user",
        "e" * 64,
        paths=("src/outside.py",),
    )
    verify_scenario_expectations(
        report, ScenarioExpectations(exit_code=4, gate=expected)
    )

    for weakened in (
        replace(expected, kind="policy"),
        replace(expected, fingerprint=None),
    ):
        with pytest.raises(DryRunScenarioError, match="gate identity expected"):
            verify_scenario_expectations(
                report, ScenarioExpectations(exit_code=4, gate=weakened)
            )


def test_gate_kind_uses_iteration_limit_state_semantics_not_fingerprint() -> None:
    state = _active_slice_state()
    current = replace(
        state.current_work_unit,
        status=WorkUnitStatus.AWAITING_USER_DECISION,
        current_step=WorkflowStep.IMPLEMENTER_CORRECTION,
        implementer_return_count=1,
        max_implementer_returns=1,
        gate=GateRecord(
            status=GateStatus.AWAITING_USER_DECISION,
            reason=GateReason.ITERATION_LIMIT,
            detail="review denied by claude after 1 Codex returns",
        ),
        reviewer=Reviewer.REVIEWER,
        open_findings=("C-01",),
    )
    state = replace(
        state,
        current_step=WorkflowStep.IMPLEMENTER_CORRECTION,
        work_units=tuple(
            current if item.work_unit_id == current.work_unit_id else item
            for item in state.work_units
        ),
        slices=tuple(
            replace(item, status=SliceStatus.AWAITING_USER_DECISION)
            if item.slice_id == state.current_slice_id
            else item
            for item in state.slices
        ),
    )
    expected = ScenarioGateExpectation(
        GateStatus.AWAITING_USER_DECISION,
        GateReason.ITERATION_LIMIT,
        "PREFIXLESS:REVIEW-DENIAL",
        "user",
    )
    report = _state_report(state)
    verify_scenario_expectations(report, ScenarioExpectations(exit_code=4, gate=expected))

    with pytest.raises(DryRunScenarioError, match="gate identity expected"):
        verify_scenario_expectations(
            report,
            ScenarioExpectations(
                exit_code=4,
                gate=replace(expected, kind="policy"),
            ),
        )


@pytest.mark.parametrize(
    ("failure_kind", "gate_status"),
    (
        (AgentFailureKind.QUOTA, GateStatus.WAITING_FOR_QUOTA),
        (AgentFailureKind.NETWORK, GateStatus.WAITING_FOR_RETRY),
        (AgentFailureKind.TIMEOUT, GateStatus.WAITING_FOR_RETRY),
    ),
)
def test_gate_kind_uses_automatic_wait_status_as_resume(
    failure_kind: AgentFailureKind, gate_status: GateStatus
) -> None:
    state = _active_slice_state()
    quota = failure_kind is AgentFailureKind.QUOTA
    failure = InvocationFailureRecord(
        invocation_id=f"inv-{failure_kind.value}",
        idempotency_key=f"dry:2:codex_implementation:{failure_kind.value}",
        role="codex",
        failure_kind=failure_kind,
        provider_text="scripted provider wait",
        received_at="2026-08-27T12:01:00+00:00",
        step=WorkflowStep.IMPLEMENTER_IMPLEMENTATION,
        slice_id=1,
        work_unit_id=2,
        diagnostic_exit_code=2 if quota else 3,
        process_exit_code=None,
        technical_text=SYNTHETIC_TECHNICAL_TEXT,
        parse_path="dry:quota" if quota else None,
        source_timezone="UTC" if quota else None,
        reset_at_utc="2026-08-27T12:02:00+00:00" if quota else None,
        resume_at_utc="2026-08-27T12:02:30+00:00",
        automatic_resume=True,
    )
    state = state.record_invocation_failure(failure, wait_automatically=True)
    expectation = ScenarioGateExpectation(
        gate_status,
        GateReason.QUOTA if quota else GateReason.INSTANCE_FAILURE,
        "PREFIXLESS:INVOCATION-FAILURE",
        "resume",
        resume_step=WorkflowStep.IMPLEMENTER_IMPLEMENTATION,
    )
    verify_scenario_expectations(
        _state_report(state),
        ScenarioExpectations(exit_code=2 if quota else 3, gate=expectation),
    )


def test_gate_kind_covers_bootstrap_resume_source_semantics() -> None:
    state = _active_slice_state().await_bootstrap_resume(
        detail="PROVIDER-INPUT-BUDGET | request exceeds the bound limit",
        fingerprint="5" * 64,
        paths=("src/runtime.py",),
    )
    expectation = ScenarioGateExpectation(
        GateStatus.AWAITING_RESUME,
        GateReason.BOOTSTRAP_CHECK,
        "PROVIDER-INPUT-BUDGET",
        "resume",
        fingerprint="5" * 64,
        paths=("src/runtime.py",),
        resume_step=WorkflowStep.IMPLEMENTER_IMPLEMENTATION,
    )
    verify_scenario_expectations(
        _state_report(state), ScenarioExpectations(exit_code=1, gate=expectation)
    )


def test_gate_kind_covers_reopened_legacy_quota_revalidation() -> None:
    state = init_workflow_state(
        run_id="dry-legacy-quota",
        task_file="/repo/task.md",
        branch="feature/dry-resilience",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        timestamp="2026-08-27T12:00:00+00:00",
    )
    failure = InvocationFailureRecord(
        invocation_id="inv-quota-diff",
        idempotency_key="dry:1:codex_plan:quota",
        role="codex",
        failure_kind=AgentFailureKind.QUOTA,
        provider_text="usage cap reached",
        received_at="2026-08-27T12:01:00+00:00",
        step=WorkflowStep.IMPLEMENTER_PLAN,
        slice_id=1,
        work_unit_id=1,
        diagnostic_exit_code=2,
        process_exit_code=None,
        technical_text=SYNTHETIC_TECHNICAL_TEXT,
        automatic_resume=False,
        diff_fingerprint="1" * 64,
    )
    active = state.record_invocation_failure(
        failure, wait_automatically=False
    ).resume_after_invocation_halt()
    detail = (
        "QUOTA-RESUME-DIFF | repository changed while the role was waiting; "
        f"expected {'1' * 64}, got {'2' * 64}"
    )
    first = active.await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail=detail,
        paths=("src/runtime.py",),
    )
    reopened = first.resume_after_user_decision().await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail=detail,
        paths=("src/runtime.py",),
    ).reopen_legacy_quota_resume_diff_gate()
    expectation = ScenarioGateExpectation(
        GateStatus.AWAITING_RESUME,
        GateReason.QUOTA,
        "PREFIXLESS:LEGACY-QUOTA-REVALIDATION",
        "resume",
        fingerprint="1" * 64,
        resume_step=WorkflowStep.IMPLEMENTER_PLAN,
    )
    verify_scenario_expectations(
        _state_report(reopened),
        ScenarioExpectations(exit_code=2, gate=expectation),
    )
