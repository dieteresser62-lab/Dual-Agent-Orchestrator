from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import orchestrator
import pytest
from agent_runtime import (
    AgentInvocationError,
    NativeAgentCodexOutput,
    NativeAgentReviewOutput,
    QuotaReset,
    QuotaWaitPolicy,
)
from audit_trail import ValidationAuditEvent
from artifact_models import (
    AgentResultPayload,
    CorrectionWorkUnitPayload,
    FindingTransitionPayload,
    PlanPayload,
    QuotaPausePayload,
    RecordType,
    ReviewPayload,
    Role,
)
from artifact_store import ArtifactStore
from cli import parse_args
from contracts import (
    AgentRole,
    ApprovalMarker,
    CodexContractResult,
    CodexStepContract,
    ContractResult,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    PlannedSlice,
    ReadinessMarker,
    StepContract,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
    ReviewEvidence,
    validate_review_response,
)
from git_service import GitTransactionError
from inbox_watcher import WatchTaskDisposition, WatchTaskResult
from orchestrator import ProductionWorkflowDriver, run_pipeline, run_production_workflow
from review_packets import ReviewPacket, ReviewPacketManifest
from workflow import (
    CodexInvocation,
    ContractRepairInvocation,
    EvidenceKind,
    ReviewerInvocation,
    WorkflowChanges,
    WorkflowCommitRequest,
    WorkflowContext,
    WorkflowEngine,
    WorkflowHistory,
    normalize_review_contract,
)
from workflow import WorkflowExecutionError
from plan_handoff import PlanHandoffError
from state_io import StateSchemaError, save_workflow_state, write_workflow_checkpoint
from task_contract import parse_task_contract
from workflow_state import (
    GateReason,
    InvocationFailureRecord,
    ProtocolBinding,
    ProtocolMode,
    Reviewer,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
)
from workflow_state import AgentFailureKind
from provider_input_budget import default_provider_input_budget_policy
from native_review_contract import (
    NativeReviewContext,
    canonical_native_review_json,
    parse_bound_native_contract_result,
)
from native_review_request import (
    NativeReviewEvidenceInput,
    NativeReviewKind,
    NativeReviewRequestSpec,
    build_native_review_request,
)
from artifact_replay import replay_artifacts
from native_codex_contract import (
    NativeCodexContext,
    NativeCodexRequestKind,
    canonical_native_codex_json,
    parse_bound_native_codex_contract_result,
)
from native_codex_request import (
    NativeCodexEvidenceInput,
    NativeCodexRequestSpec,
    build_native_codex_request,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_every_orchestrated_agent_step_has_a_provider_input_budget_rule() -> None:
    policy = default_provider_input_budget_policy()
    operations = {
        "codex": (
            WorkflowStep.CODEX_PLAN, WorkflowStep.CODEX_PLAN_REVISION,
            WorkflowStep.CODEX_IMPLEMENTATION, WorkflowStep.CODEX_CORRECTION,
            WorkflowStep.CODEX_FINAL_REVIEW, WorkflowStep.CODEX_FINAL_CORRECTION,
        ),
        "claude": (
            WorkflowStep.CLAUDE_PLAN_REVIEW, WorkflowStep.CLAUDE_SLICE_REVIEW,
            WorkflowStep.CLAUDE_FINAL_REVIEW,
        ),
        "antigravity": (
            WorkflowStep.ANTIGRAVITY_PLAN_REVIEW, WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
            WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
        ),
    }

    for provider, steps in operations.items():
        for step in steps:
            assert policy.select(provider, provider, step.value).operation == step.value


def _repository(tmp_path: Path, branch: str) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "master")
    _git(repository, "config", "user.name", "Slice Test")
    _git(repository, "config", "user.email", "slice@example.invalid")
    (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
    (repository / ".gitignore").write_text(".orchestrator/\n", encoding="utf-8")
    _git(repository, "add", "seed.txt", ".gitignore")
    _git(repository, "commit", "-m", "seed")
    _git(repository, "switch", "-c", branch)
    return repository


def _args(repository: Path, task: Path):
    return parse_args(
        [
            "--task-file", str(task),
            "--test-command", "python3 -c 'print(\"ok\")'",
            "--agent-output", "none",
            "--no-agent-live-stream",
            "--no-plan-gate",
        ],
        cwd=repository,
        environ={},
    )


def _write_task(path: Path, branch: str, *scope: str) -> None:
    path.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: IMPLEMENT",
                f"TARGET_BRANCH: {branch}",
                "TASK_SCOPE: " + ", ".join(scope),
                "",
                "Implement the bounded task.",
            )
        ),
        encoding="utf-8",
    )


def _review(role: AgentRole, marker: str) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            "TEST_FILES_TOUCHED: NONE",
            "REVIEW_EVIDENCE: correctness, contracts, failure paths, security, resume | "
            "state drift | a provider changes its output",
            "PRE_MORTEM: a resumed step consumes stale evidence",
            marker,
            "STATUS: DONE",
        )
    )


def test_new_watch_task_switches_to_existing_target_and_uses_its_head_as_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/inbox-target")
    (repository / "prior.txt").write_text("prior target work\n", encoding="utf-8")
    _git(repository, "add", "prior.txt")
    _git(repository, "commit", "-m", "prior target work")
    target_head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "switch", "master")
    task = tmp_path / "inbox-task.md"
    _write_task(task, "feature/inbox-target", "src/new.py")
    args = _args(repository, task)
    args.watch_run_id = "watch-existing-target"
    captured: dict[str, object] = {}
    real_fresh_state = orchestrator._fresh_state

    class StateCaptured(RuntimeError):
        pass

    def capture_state(**kwargs):
        state = real_fresh_state(**kwargs)
        captured["state"] = state
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    state = captured["state"]
    assert state.branch == "feature/inbox-target"
    assert state.branch_base == target_head
    assert state.current_slice.start_commit == target_head
    assert _git(repository, "branch", "--show-current") == "feature/inbox-target"


def test_fresh_workflow_is_immutably_bound_to_structured_v1(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "feature/structured-cutover")
    task = tmp_path / "structured-cutover.md"
    _write_task(task, "feature/structured-cutover", "src/new.py")

    state = orchestrator._fresh_state(
        task_file=task,
        run_id="structured-cutover",
        repository_root=repository,
        task_contract=parse_task_contract(task.read_text(encoding="utf-8")),
    )
    assert state.protocol_binding == ProtocolBinding(
        ProtocolMode.STRUCTURED_V1, "1"
    )

    native = orchestrator._fresh_state(
        task_file=task,
        run_id="structured-native-cutover",
        repository_root=repository,
        task_contract=parse_task_contract(task.read_text(encoding="utf-8")),
        native_claude_reviews=True,
    )
    assert native.protocol_binding == ProtocolBinding(
        ProtocolMode.STRUCTURED_V1,
        "1",
        "native-claude-review-v1",
    )

    native_codex = orchestrator._fresh_state(
        task_file=task,
        run_id="structured-native-codex-cutover",
        repository_root=repository,
        task_contract=parse_task_contract(task.read_text(encoding="utf-8")),
        native_codex_results=True,
    )
    assert native_codex.protocol_binding == ProtocolBinding(
        ProtocolMode.STRUCTURED_V1,
        "1",
        codex_result_transport="native-codex-v1",
    )


def test_final_review_structured_records_use_branch_wide_fingerprint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path, "feature/final-fingerprint")
    branch_base = _git(repository, "rev-parse", "HEAD")
    (repository / "prior.txt").write_text("prior slice\n", encoding="utf-8")
    _git(repository, "add", "prior.txt")
    _git(repository, "commit", "-m", "prior slice")
    slice_start = _git(repository, "rev-parse", "HEAD")
    (repository / "current.txt").write_text("current slice\n", encoding="utf-8")
    _git(repository, "add", "current.txt")
    _git(repository, "commit", "-m", "current slice")
    slice_commit = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="final-fingerprint",
        task_file=str(repository / "task.md"),
        branch="feature/final-fingerprint",
        branch_base=branch_base,
        slice_count=1,
    ).bind_slice_plan(
        (PlannedSlice(1, "implementation", ("current.txt",)),),
        first_start_commit=slice_start,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=slice_start,
        scope_paths=("current.txt",),
        start_fingerprint="1" * 64,
    ).complete_current_slice(
        commit_ref=slice_commit,
    ).start_final_review_work_unit()
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = state
    starts: list[str] = []

    def collect(start_commit: str) -> WorkflowChanges:
        starts.append(start_commit)
        return WorkflowChanges(
            start_commit,
            "b" * 64 if start_commit == branch_base else "c" * 64,
            ("current.txt",),
            "diff",
        )

    monkeypatch.setattr(driver, "collect_changes", collect)

    assert driver._artifact_fingerprint() == "b" * 64
    assert starts == [branch_base]


def test_reviewer_recovers_complete_contract_from_false_401_auth_classification(
    tmp_path: Path, monkeypatch,
) -> None:
    repository = _repository(tmp_path, "feature/reviewer-auth-recovery")
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    response = "\n".join(
        (
            "REVIEWER: antigravity",
            "REVIEW_EVIDENCE: checked src/orchestrator.py#L401-L433 | risk | break",
            "PRE_MORTEM: a future lifecycle step drifts",
            "SLICE_APPROVAL: 11 | YES",
            "STATUS: DONE",
        )
    )
    failure = AgentInvocationError(
        agent_key="antigravity",
        kind=AgentFailureKind.AUTH,
        invocation_id="false-401-link",
        provider_text=response,
        received_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    def fail_agent(*args, **kwargs):
        _ = (args, kwargs)
        raise failure

    monkeypatch.setattr(driver, "_agent", fail_agent)
    invocation = ReviewerInvocation(
        work_unit_id=16,
        step=WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
        reviewer=AgentRole.ANTIGRAVITY,
        round_number=2,
        evidence_kind=EvidenceKind.CORRECTION_DELTA,
        fingerprint="a" * 64,
        paths=("src/orchestrator.py",),
        prompt="review",
    )

    assert driver.invoke_reviewer(invocation) == response


@pytest.mark.parametrize(
    ("failure_kind", "provider_text"),
    (
        (AgentFailureKind.AUTH, "401 unauthorized"),
        (AgentFailureKind.QUOTA, "quota exceeded"),
        (
            AgentFailureKind.QUOTA,
            "REVIEWER: antigravity\n"
            "REVIEW_EVIDENCE: complete output | risk | break\n"
            "PRE_MORTEM: a quota response is mistaken for success\n"
            "SLICE_APPROVAL: 11 | YES\n"
            "STATUS: DONE",
        ),
        (AgentFailureKind.AUTH, "REVIEWER: antigravity\nSTATUS: incomplete"),
        (AgentFailureKind.AUTH, "REVIEWER: claude\nSTATUS: DONE"),
    ),
)
def test_reviewer_does_not_recover_real_incomplete_or_foreign_failure(
    tmp_path: Path,
    monkeypatch,
    failure_kind: AgentFailureKind,
    provider_text: str,
) -> None:
    repository = _repository(tmp_path, "feature/reviewer-auth-rejection")
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    failure = AgentInvocationError(
        agent_key="antigravity",
        kind=failure_kind,
        invocation_id="real-failure",
        provider_text=provider_text,
        received_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    def fail_agent(*args, **kwargs):
        _ = (args, kwargs)
        raise failure

    monkeypatch.setattr(driver, "_agent", fail_agent)
    invocation = ReviewerInvocation(
        work_unit_id=16,
        step=WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
        reviewer=AgentRole.ANTIGRAVITY,
        round_number=2,
        evidence_kind=EvidenceKind.CORRECTION_DELTA,
        fingerprint="a" * 64,
        paths=("src/orchestrator.py",),
        prompt="review",
    )

    with pytest.raises(AgentInvocationError) as caught:
        driver.invoke_reviewer(invocation)

    assert caught.value is failure


@pytest.mark.parametrize(
    ("reviewer", "expected_operation"),
    (
        (AgentRole.CLAUDE, "claude_contract_repair"),
        (AgentRole.ANTIGRAVITY, "antigravity_contract_repair"),
    ),
)
def test_contract_repair_uses_a_separate_provider_operation(
    tmp_path: Path,
    monkeypatch,
    reviewer: AgentRole,
    expected_operation: str,
) -> None:
    repository = _repository(tmp_path, "feature/repair-operation")
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = init_workflow_state(
        run_id="repair-operation",
        task_file=str(repository / "task.md"),
        branch="feature/repair-operation",
        branch_base=_git(repository, "rev-parse", "HEAD"),
        slice_count=1,
    )
    captured: dict[str, object] = {}

    def fake_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured["args"] = args
        captured.update(kwargs)
        return "STATUS: DONE"

    monkeypatch.setattr(driver, "_agent", fake_agent)
    output = driver.repair_review_contract(
        ContractRepairInvocation(
            reviewer=reviewer,
            rejected_output="REVIEWER: claude",
            validation_error="REVIEW_EVIDENCE is ambiguous",
            contract="contract",
        )
    )

    assert output == "STATUS: DONE"
    assert captured["operation"] == expected_operation
    assert captured["reviewer_repository_required"] is False


def test_review_packet_materialization_reuses_bytes_and_rejects_cache_mismatch(
    tmp_path: Path, monkeypatch,
) -> None:
    repository = _repository(tmp_path, "feature/packet-cache")
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = init_workflow_state(
        run_id="packet-run", task_file="/repo/task.md",
        branch="feature/packet-cache", branch_base="a" * 40,
        slice_count=1, timestamp="2026-08-21T10:00:00+00:00",
    )
    canonical = b'{"schema":"review-packet-v1"}'
    packet = ReviewPacket(
        purpose="slice", fingerprint="a" * 64,
        manifest=ReviewPacketManifest(("seed.txt",)),
        canonical_bytes=canonical,
        digest=hashlib.sha256(canonical).hexdigest(),
    )

    first = driver._materialize_review_packet(packet)
    second = driver._materialize_review_packet(packet)
    assert first == second
    assert first.read_bytes() == canonical

    captured: dict[str, object] = {}

    def fake_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return "STATUS: DONE"

    monkeypatch.setattr(driver, "_agent", fake_agent)
    assert driver.invoke_reviewer(
        ReviewerInvocation(
            work_unit_id=2,
            step=WorkflowStep.CLAUDE_SLICE_REVIEW,
            reviewer=AgentRole.CLAUDE,
            round_number=1,
            evidence_kind=EvidenceKind.FULL_SLICE,
            fingerprint="a" * 64,
            paths=("seed.txt",),
            prompt="review",
            review_packet=packet,
        )
    ) == "STATUS: DONE"
    assert captured["reviewer_manifest_paths"] == ("seed.txt",)

    first.write_text("collision", encoding="utf-8")
    with pytest.raises(WorkflowExecutionError, match="differs from canonical bytes"):
        driver._materialize_review_packet(packet)


def test_quota_classified_recoverable_contract_still_persists_matching_quota_pause_record(
    tmp_path: Path, monkeypatch,
) -> None:
    repository = _repository(tmp_path, "feature/reviewer-quota-pause")
    head = _git(repository, "rev-parse", "HEAD")
    (repository / "src").mkdir()
    (repository / "src" / "orchestrator.py").write_text(
        "QUOTA_RECOVERY_GUARD = True\n", encoding="utf-8"
    )
    _git(repository, "add", "src/orchestrator.py")
    task = repository / "task.md"
    task.write_text("Review quota recovery.\n", encoding="utf-8")
    state = init_workflow_state(
        run_id="reviewer-quota-pause",
        task_file=str(task),
        branch="feature/reviewer-quota-pause",
        branch_base=head,
        slice_count=1,
        task_digest=hashlib.sha256(task.read_bytes()).hexdigest(),
        task_scope_patterns=("src/orchestrator.py",),
        target_branch="feature/reviewer-quota-pause",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1"),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/orchestrator.py",),
        start_fingerprint="0" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)
    changes = driver.collect_changes(head)
    monkeypatch.setattr(driver, "collect_changes", lambda _start_commit: changes)
    response = "\n".join(
        (
            "REVIEWER: antigravity",
            "REVIEW_EVIDENCE: complete output | risk | break",
            "PRE_MORTEM: a quota response is mistaken for success",
            "SLICE_APPROVAL: 12 | YES",
            "STATUS: DONE",
        )
    )
    now = [datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)]
    quota = AgentInvocationError(
        agent_key="antigravity",
        kind=AgentFailureKind.QUOTA,
        invocation_id="quota-with-complete-contract",
        provider_text=response,
        received_at=now[0],
        quota_reset=QuotaReset(
            now[0] + timedelta(seconds=1),
            "antigravity:structured:retry_after_seconds",
            "UTC",
        ),
    )
    calls = 0

    def invoke_agent(*args, **kwargs):
        nonlocal calls
        _ = (args, kwargs)
        calls += 1
        if calls == 1:
            raise quota
        return response

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    monkeypatch.setattr(driver, "_agent", invoke_agent)
    invocation = ReviewerInvocation(
        work_unit_id=state.current_work_unit_id,
        step=WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
        reviewer=AgentRole.ANTIGRAVITY,
        round_number=state.current_work_unit.round_number,
        evidence_kind=EvidenceKind.CORRECTION_DELTA,
        fingerprint="b" * 64,
        paths=("src/orchestrator.py",),
        prompt="review",
    )
    engine = WorkflowEngine(
        driver,
        now_fn=lambda: now[0],
        sleep_fn=sleep,
        heartbeat_fn=lambda _message: None,
    )
    context = WorkflowContext(
        assignment="Review the correction",
        distilled_plan="Persist quota failures before retrying the same role.",
        slice_summary="Final correction",
        quota_wait_policy=QuotaWaitPolicy(
            safety_margin_seconds=0,
            maximum_wait_seconds=60,
            heartbeat_interval_seconds=1,
        ),
    )

    resumed, output = engine._invoke_role(
        state,
        WorkflowHistory(state.current_work_unit_id),
        context,
        AgentRole.ANTIGRAVITY,
        lambda: driver.invoke_reviewer(invocation),
    )

    assert resumed.current_work_unit.invocation_failures[-1].automatic_resume is True
    assert output == response
    assert calls == 2
    assert resumed.current_work_unit.invocation_failures[-1].failure_kind is AgentFailureKind.QUOTA
    quota_records = tuple(
        record
        for record in ArtifactStore(repository, state.run_id).load_chain()
        if isinstance(record.payload, QuotaPausePayload)
    )
    assert len(quota_records) == 1
    assert quota_records[0].payload.role is Role.ANTIGRAVITY
    assert quota_records[0].payload.retry_at == quota.quota_reset.reset_at_utc.isoformat()


def test_reviewer_reuses_diagnostic_bound_bulleted_evidence_output_without_provider(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/output-recovery")
    fingerprint = "a" * 64
    attestation = ValidationAttestation(
        "validation-output-recovery",
        fingerprint,
        ("pytest",),
        (ValidationRecord(ValidationStatus.PASS, "pytest", 0, "passed"),),
        "b" * 64,
        "passed",
    )
    finding = FindingRecord(
        finding_id="C-03",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="foreign chain accepted",
        acceptance_test="reject a foreign start record",
        origin=FindingOrigin("03", 1, AgentRole.CLAUDE),
    )
    state = init_workflow_state(
        run_id="output-recovery",
        task_file=str(repository / "task.md"),
        branch="feature/output-recovery",
        branch_base=_git(repository, "rev-parse", "HEAD"),
        slice_count=3,
    ).bind_slice_plan(
        (
            PlannedSlice(1, "first", ("src/first.py",)),
            PlannedSlice(2, "second", ("src/second.py",)),
            PlannedSlice(3, "correction", ("src/artifact_bridge.py",)),
        ),
        first_start_commit=_git(repository, "rev-parse", "HEAD"),
    ).complete_current_work_unit().start_work_unit(
        slice_id=3,
        kind=WorkUnitKind.CORRECTION,
        step=WorkflowStep.CLAUDE_SLICE_REVIEW,
        slice_start_commit=_git(repository, "rev-parse", "HEAD"),
    )
    state = replace(
        state,
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1"),
    )
    failure = InvocationFailureRecord(
        invocation_id="contract-2-claude_slice_review-1",
        idempotency_key="output-recovery:2:claude_slice_review:claude",
        role="claude",
        failure_kind=AgentFailureKind.OUTPUT,
        provider_text=(
            "invalid claude verdict is not safely repairable: "
            "unknown state-v3 contract marker EVIDENCE"
        ),
        received_at="2026-08-21T15:33:32+00:00",
        step=WorkflowStep.CLAUDE_SLICE_REVIEW,
        slice_id=3,
        work_unit_id=2,
        diagnostic_exit_code=3,
        diff_fingerprint=fingerprint,
    )
    state = state.record_invocation_failure(failure, wait_automatically=False)
    contract = StepContract(
        name="work-unit-2-claude_slice_review",
        reviewer=AgentRole.CLAUDE,
        approval_marker=ApprovalMarker.SLICE,
        slice_id="03",
        round_number=1,
        review_fingerprint=fingerprint,
        validation_attestation=attestation,
        expected_test_files=("tests/test_artifact_bridge.py",),
        test_changes_approved=True,
        existing_finding_ids=("C-03",),
        allow_new_observations=False,
    )
    raw = "\n".join(
        (
            "REVIEWER: claude",
            "EVIDENCE:",
            "- Checked the exact chain-membership fix.",
            "FINDING_STATUS: C-03 | CLOSED | guard and regression are present",
            "PRE_MORTEM: a shared cache bypasses provenance",
            "SLICE_APPROVAL: 03 | YES",
            "STATUS: DONE",
        )
    )
    legacy = normalize_review_contract(
        raw,
        contract,
        (finding,),
        provider_completed=True,
        remove_bulleted_evidence=False,
    ).output
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = state
    driver._bind_artifact_store(state)
    assert driver._artifact_bridge is not None
    driver._artifact_bridge.diagnostic(
        role=AgentRole.CLAUDE,
        work_unit_id=2,
        attempt=1,
        output=legacy,
        reason="unknown state-v3 contract marker EVIDENCE",
        fingerprint_sha256=fingerprint,
    )
    driver.log_dir.mkdir(parents=True)
    (driver.log_dir / "work-unit-0002-claude_slice_review.attempt-1.log").write_text(
        raw,
        encoding="utf-8",
    )
    invocation = ReviewerInvocation(
        work_unit_id=2,
        step=WorkflowStep.CLAUDE_SLICE_REVIEW,
        reviewer=AgentRole.CLAUDE,
        round_number=1,
        evidence_kind=EvidenceKind.CORRECTION_DELTA,
        fingerprint=fingerprint,
        paths=("src/artifact_bridge.py", "tests/test_artifact_bridge.py"),
        prompt="unused",
    )

    recovered = driver.recover_failed_reviewer_output(
        invocation, contract, (finding,)
    )

    assert recovered is not None
    assert "EVIDENCE:" not in recovered
    assert validate_review_response(
        recovered, contract, (finding,)
    ).approval is True


@pytest.mark.parametrize(
    ("reset_delay_seconds", "safety_margin_seconds", "expected_automatic"),
    (
        (604_800, 0, True),
        (604_800, 60, True),
        (604_801, 0, False),
        (604_801, 60, False),
        (None, 60, False),
    ),
)
def test_quota_auto_wait_boundary_uses_reset_span_without_safety_margin(
    reset_delay_seconds: int | None,
    safety_margin_seconds: int,
    expected_automatic: bool,
) -> None:
    class CheckpointDriver:
        def checkpoint(self, state, history) -> None:
            _ = (state, history)

    now = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)
    state = init_workflow_state(
        run_id=f"quota-boundary-{reset_delay_seconds}-{safety_margin_seconds}",
        task_file="task.md",
        branch="feature/quota-boundary",
        branch_base="a" * 40,
        slice_count=1,
    )
    reset_at = (
        (now + timedelta(seconds=reset_delay_seconds)).astimezone(
            timezone(timedelta(hours=2))
        )
        if reset_delay_seconds is not None
        else None
    )
    error = AgentInvocationError(
        agent_key="codex",
        kind=AgentFailureKind.QUOTA,
        invocation_id=f"quota-{reset_delay_seconds}-{safety_margin_seconds}",
        provider_text="quota exceeded",
        received_at=now,
        quota_reset=(
            QuotaReset(reset_at, "codex:text:absolute", "UTC+02:00")
            if reset_at is not None
            else None
        ),
    )
    context = WorkflowContext(
        assignment="Test the quota boundary.",
        distilled_plan="Use only the provider reset span for the boundary.",
        slice_summary="Quota boundary",
        quota_wait_policy=QuotaWaitPolicy(
            safety_margin_seconds=safety_margin_seconds,
            maximum_wait_seconds=604_800,
        ),
    )
    engine = WorkflowEngine(CheckpointDriver(), now_fn=lambda: now)

    persisted, failure = engine._persist_invocation_failure(
        state,
        WorkflowHistory(state.current_work_unit_id),
        context,
        AgentRole.CODEX,
        error,
    )

    assert failure.automatic_resume is expected_automatic
    assert persisted.current_work_unit.invocation_failures[-1] == failure
    expected_resume = (
        (reset_at + timedelta(seconds=safety_margin_seconds))
        .astimezone(timezone.utc)
        .isoformat()
        if reset_at is not None
        else None
    )
    assert failure.resume_at_utc == expected_resume
    assert failure.safety_margin_seconds == (
        safety_margin_seconds if expected_automatic else 0
    )


def test_commit_backstop_rejects_yes_reviews_bound_to_failed_attestation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/red-attestation-backstop")
    start_commit = _git(repository, "rev-parse", "HEAD")
    changed_path = "runtime.py"
    (repository / changed_path).write_text("VALUE = 1\n", encoding="utf-8")
    state = init_workflow_state(
        run_id="red-attestation-backstop",
        task_file=str(tmp_path / "task.md"),
        branch="feature/red-attestation-backstop",
        branch_base=start_commit,
        slice_count=1,
    ).bind_current_slice_git_boundary(
        start_commit=start_commit,
        scope_paths=(changed_path,),
        start_fingerprint="a" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = state
    changes = driver.collect_changes(start_commit)
    attestation = ValidationAttestation(
        "validation-failed",
        changes.fingerprint,
        ("pytest",),
        (ValidationRecord(ValidationStatus.FAIL, "pytest", 1, "failed"),),
        "b" * 64,
        "failed",
    )

    def approving_review(role: AgentRole) -> ContractResult:
        return ContractResult(
            reviewer=role,
            approval=True,
            stopped=False,
            stop_request=None,
            validation=attestation,
            test_files=(),
            pre_mortem="a latent gate regression permits a red commit",
            evidence=None,
            findings=(),
            anchors=(),
        )

    request = WorkflowCommitRequest(
        slice_id=1,
        fingerprint=changes.fingerprint,
        attestation=attestation,
        claude_review=approving_review(AgentRole.CLAUDE),
        antigravity_review=approving_review(AgentRole.ANTIGRAVITY),
        findings=(),
    )

    with pytest.raises(GitTransactionError, match="passing current attestation"):
        driver.commit_slice(request)

    assert _git(repository, "rev-parse", "HEAD") == start_commit
    assert _git(repository, "status", "--short") == "?? runtime.py"


def test_runtime_context_auto_authorizes_scoped_test_changes_unless_gate_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/automatic-tests")
    task = tmp_path / "task.md"
    _write_task(task, "feature/automatic-tests", "tests/regression.test.py")
    state = orchestrator.init_workflow_state(
        run_id="automatic-tests",
        task_file=str(task),
        branch="feature/automatic-tests",
        branch_base=_git(repository, "rev-parse", "HEAD"),
        slice_count=1,
    )
    monkeypatch.chdir(repository)
    automatic_args = _args(repository, task)
    gated_args = _args(repository, task)
    gated_args.test_change_gate = True

    automatic = orchestrator._context(
        args=automatic_args,
        assignment="Add regression coverage.",
        state=state,
    )
    gated = orchestrator._context(
        args=gated_args,
        assignment="Add regression coverage.",
        state=state,
    )

    assert automatic.test_changes_approved is True
    assert gated.test_changes_approved is False


def test_carry_forward_findings_migrates_reused_legacy_ids_stably() -> None:
    first = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="first slice observation",
        acceptance_test="disposition first observation",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    second = replace(
        first,
        summary="second slice observation",
        acceptance_test="disposition second observation",
        origin=FindingOrigin("02", 1, AgentRole.CLAUDE),
    )
    first_history = WorkflowHistory(1, findings=(first,))
    second_history = WorkflowHistory(2, findings=(second,))
    state = init_workflow_state(
        run_id="legacy-findings",
        task_file="task.md",
        branch="feature/findings",
        branch_base="a" * 40,
        slice_count=1,
        timestamp="2026-08-16T12:00:00+00:00",
    )
    state = replace(
        state,
        runtime_history={
            "archive": [first_history.to_dict()],
            "current": second_history.to_dict(),
        },
    )

    migrated = orchestrator._carry_forward_findings(state, second_history)

    assert [finding.finding_id for finding in migrated] == ["C-01", "C-02"]
    carried_history = WorkflowHistory(3, findings=migrated)
    state = replace(
        state,
        runtime_history={
            "archive": [first_history.to_dict(), second_history.to_dict()],
            "current": carried_history.to_dict(),
        },
    )
    assert orchestrator._carry_forward_findings(state, carried_history) == migrated


def test_final_review_recovers_latest_prior_attestation_after_transition_checkpoint() -> None:
    attestation = ValidationAttestation(
        "validation-a",
        "a" * 64,
        ("pytest",),
        (ValidationRecord(ValidationStatus.PASS, "pytest", 0, "ok"),),
        "b" * 64,
        "passed",
    )
    prior = WorkflowHistory(2, attestations=(attestation,))
    final_state = init_workflow_state(
        run_id="final-attestation-recovery",
        task_file="task.md",
        branch="feature/final-attestation",
        branch_base="a" * 40,
        slice_count=1,
        timestamp="2026-08-21T12:00:00+00:00",
    ).bind_slice_plan(
        (PlannedSlice(1, "implementation", ("src/core.py",)),),
        first_start_commit="a" * 40,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=("src/core.py",),
        start_fingerprint="0" * 64,
    ).complete_current_slice(
        commit_ref="b" * 40,
    ).start_final_review_work_unit()
    final_history = WorkflowHistory(final_state.current_work_unit_id)
    runtime = {
        "current": final_history.to_dict(),
        "archive": [prior.to_dict()],
    }
    final_state = replace(final_state, runtime_history=runtime)

    recovered = orchestrator._recover_final_review_attestation(
        final_state,
        final_history,
    )

    assert recovered.attestations == prior.attestations
    assert len(recovered.events) == 1
    assert isinstance(recovered.events[0], ValidationAuditEvent)
    assert recovered.events[0].attestation == attestation


def test_bind_work_unit_preserves_latest_driver_owned_runtime_history(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/runtime-history")
    task = repository / "inbox" / "runtime-history.md"
    task.parent.mkdir()
    _write_task(task, "feature/runtime-history", "src/runtime.py")
    base = init_workflow_state(
        run_id="runtime-history-transition",
        task_file=str(task),
        branch="feature/runtime-history",
        branch_base=_git(repository, "rev-parse", "HEAD"),
        slice_count=1,
    )
    old_history = WorkflowHistory(1).to_dict()
    latest_history = WorkflowHistory(
        1,
        codex_final_report="latest persisted review evidence",
    ).to_dict()
    persisted = replace(
        base,
        runtime_history={"archive": [], "current": latest_history},
    )
    stale_transition = replace(
        base,
        runtime_history={"archive": [], "current": old_history},
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = persisted

    driver.bind_work_unit(stale_transition)

    assert driver.active_state is not None
    assert driver.active_state.runtime_history == persisted.runtime_history


def test_final_review_compacts_generated_audit_without_weakening_fingerprint(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/final-review-evidence")
    head = _git(repository, "rev-parse", "HEAD")
    audit_path = "docs/internal/final-review-evidence-review-12345678.md"
    source_path = "src/runtime.py"
    (repository / "docs/internal").mkdir(parents=True)
    (repository / "src").mkdir()
    audit_sentinel = "FULL AUDIT BODY MUST NOT REACH THE FINAL AGENT\n"
    (repository / audit_path).write_text(
        audit_sentinel * 12_000,
        encoding="utf-8",
    )
    (repository / source_path).write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", audit_path, source_path)
    state = init_workflow_state(
        run_id="final-review-evidence",
        task_file=str(tmp_path / "task.md"),
        branch="feature/final-review-evidence",
        branch_base=head,
        slice_count=1,
        task_scope_patterns=(audit_path, source_path),
        audit_report_path=audit_path,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=(audit_path, source_path),
        start_fingerprint="a" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )

    driver.active_state = state
    ordinary = driver.collect_changes(head)
    assert audit_sentinel in ordinary.full_diff

    driver.active_state = state.complete_current_slice(
        commit_ref="b" * 40
    ).start_final_review_work_unit()
    compacted = driver.collect_changes(head)

    assert compacted.fingerprint == ordinary.fingerprint
    assert compacted.paths == ordinary.paths
    assert compacted.gate_paths == ordinary.gate_paths
    assert "VALUE = 1" in compacted.full_diff
    assert audit_sentinel not in compacted.full_diff
    assert "DETERMINISTIC AUDIT PROJECTION (COMPACT EVIDENCE)" in compacted.full_diff
    assert f"path: {audit_path}" in compacted.full_diff
    audit_entry = next(
        entry
        for entry in driver._repository_changes[compacted.fingerprint].fingerprint_entries
        if entry.path == audit_path
    )
    assert (
        f"semantic_payload_sha256: {audit_entry.payload_digest}"
        in compacted.full_diff
    )
    assert len(compacted.full_diff) < 10_000

    first_fingerprint = compacted.fingerprint
    first_summary = compacted.full_diff
    (repository / audit_path).write_text(
        audit_sentinel * 12_000 + "semantic audit change\n",
        encoding="utf-8",
    )
    _git(repository, "add", audit_path)
    changed = driver.collect_changes(head)

    assert changed.fingerprint != first_fingerprint
    assert changed.full_diff != first_summary
    assert audit_sentinel not in changed.full_diff


def test_final_review_evidence_never_silently_truncates_diff_content(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/complete-final-review-evidence")
    head = _git(repository, "rev-parse", "HEAD")
    audit_path = "docs/internal/complete-final-review-review-12345678.md"
    source_path = "src/large.py"
    (repository / "docs/internal").mkdir(parents=True)
    (repository / "src").mkdir()
    (repository / audit_path).write_text("audit row\n" * 40_000, encoding="utf-8")
    (repository / source_path).write_text(
        "FIRST_SENTINEL\n"
        + "A" * 400_000
        + "\nMIDDLE_SENTINEL\n"
        + "Z" * 400_000
        + "\nLAST_SENTINEL\n",
        encoding="utf-8",
    )
    _git(repository, "add", audit_path, source_path)
    state = init_workflow_state(
        run_id="complete-final-review-evidence",
        task_file=str(tmp_path / "task.md"),
        branch="feature/complete-final-review-evidence",
        branch_base=head,
        slice_count=1,
        task_scope_patterns=(audit_path, source_path),
        audit_report_path=audit_path,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=(audit_path, source_path),
        start_fingerprint="a" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = state.complete_current_slice(
        commit_ref="b" * 40
    ).start_final_review_work_unit()

    changes = driver.collect_changes(head)

    assert len(changes.full_diff) > 650_000
    assert "FIRST_SENTINEL" in changes.full_diff
    assert "MIDDLE_SENTINEL" in changes.full_diff
    assert "LAST_SENTINEL" in changes.full_diff
    assert "middle of final-review diff section" not in changes.full_diff
    assert "DETERMINISTIC AUDIT PROJECTION (COMPACT EVIDENCE)" in changes.full_diff


def test_structured_bind_persists_contract_and_active_work_unit_once(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-bind")
    task = repository / "task.md"
    _write_task(task, "feature/structured-bind", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="structured-bind",
        task_file=str(task),
        branch="feature/structured-bind",
        branch_base=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/structured-bind",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1"),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="b" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )

    driver.bind_work_unit(state)
    driver.bind_work_unit(state)

    chain = ArtifactStore(repository, state.run_id).load_chain()
    assert tuple(item.record_type for item in chain) == (
        RecordType.TASK,
        RecordType.WORK_UNIT,
    )


def test_native_review_record_ahead_recovery_reuses_bound_json_without_provider(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/native-record-ahead")
    task = repository / "task.md"
    _write_task(task, "feature/native-record-ahead", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    fingerprint = "b" * 64
    state = init_workflow_state(
        run_id="native-record-ahead",
        task_file=str(task),
        branch="feature/native-record-ahead",
        branch_base=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/native-record-ahead",
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V1,
            "1",
            "native-claude-review-v1",
        ),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="c" * 64,
    ).with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)
    command = "python3 -m pytest tests/ -v"
    attestation = ValidationAttestation(
        "validation-native-recovery",
        fingerprint,
        (command,),
        (ValidationRecord(ValidationStatus.PASS, command, 0, "passed"),),
        "d" * 64,
        "passed",
        command_specs=(
            ValidationCommandSpec(
                argv=("python3", "-m", "pytest", "tests/", "-v")
            ),
        ),
    )
    driver.persist_validation_attestation(attestation)
    context = NativeReviewContext(
        run_id=state.run_id,
        work_unit_id=str(state.current_work_unit_id),
        operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
        diff_fingerprint=fingerprint,
        reviewer=AgentRole.CLAUDE,
        approval_marker=ApprovalMarker.SLICE,
        slice_id="01",
        round_number=1,
        validation_attestation=attestation,
        validation_command_prefixes=(("python3", "-m", "pytest"),),
    )
    bundle = build_native_review_request(
        NativeReviewRequestSpec(
            context=context,
            review_kind=NativeReviewKind.SLICE,
            target_branch=state.branch,
            base_commit=head,
            authorized_paths=("src/runtime.py",),
            acceptance_criteria=("Native recovery does not invoke Claude twice.",),
            evidence=(
                NativeReviewEvidenceInput(
                    "review-diff", "full_slice", "diff -- src/runtime.py"
                ),
            ),
        )
    )
    response = {
        "schema_version": "native-agent-review-result-v1",
        "result_type": "review_result",
        "request_id": bundle.bound_context.request_id,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [],
        "status_changes": [],
        "reclassifications": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "persistence and recovery",
            "largest_residual_risk": "log loss",
            "break_condition": "Claude is started twice",
        },
        "pre_mortem": "A response log could be detached from its record.",
    }
    canonical = canonical_native_review_json(response)
    result = parse_bound_native_contract_result(
        response, bundle.bound_context
    )
    output = NativeAgentReviewOutput(
        result=result,
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
    )
    driver.persist_native_review_contract(output, fingerprint, 1, ())
    driver.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = (
        driver.log_dir
        / (
            f"work-unit-{state.current_work_unit_id:04d}-claude_slice_review-"
            "round-0001.attempt-1.log"
        )
    )
    log_path.write_text(canonical, encoding="utf-8")
    invocation = ReviewerInvocation(
        work_unit_id=state.current_work_unit_id,
        step=WorkflowStep.CLAUDE_SLICE_REVIEW,
        reviewer=AgentRole.CLAUDE,
        round_number=1,
        evidence_kind=EvidenceKind.FULL_SLICE,
        fingerprint=fingerprint,
        paths=("src/runtime.py",),
        prompt="",
        native_request=bundle,
    )
    contract = StepContract(
        "native-recovery",
        AgentRole.CLAUDE,
        ApprovalMarker.SLICE,
        "01",
        1,
        fingerprint,
        attestation,
    )

    recovered = driver.recover_pending_native_reviewer(
        invocation,
        contract,
        WorkflowHistory(state.current_work_unit_id),
    )

    assert recovered == output
    reviews = tuple(
        item
        for item in ArtifactStore(repository, state.run_id).load_chain()
        if isinstance(item.payload, ReviewPayload)
    )
    assert len(reviews) == 1
    assert reviews[0].payload.request_id == bundle.bound_context.request_id

    log_path.unlink()
    with pytest.raises(
        WorkflowExecutionError,
        match="no unique response-digest-bound log",
    ):
        driver.recover_pending_native_reviewer(
            invocation,
            contract,
            WorkflowHistory(state.current_work_unit_id),
        )


def test_native_codex_record_ahead_recovery_reuses_raw_json_without_provider(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/native-codex-record-ahead")
    task = repository / "task.md"
    _write_task(task, "feature/native-codex-record-ahead", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="native-codex-record-ahead",
        task_file=str(task),
        branch="feature/native-codex-record-ahead",
        branch_base=head,
        slice_count=1,
        task_digest=hashlib.sha256(
            task.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest(),
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/native-codex-record-ahead",
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V1,
            "1",
            codex_result_transport="native-codex-v1",
        ),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="c" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)
    contract = CodexStepContract(
        "native-codex-recovery",
        ReadinessMarker.IMPLEMENTATION,
        "01",
        1,
        require_test_files_record=True,
        expected_test_files=(),
        test_changes_approved=True,
    )
    native_context = NativeCodexContext(
        run_id=state.run_id,
        work_unit_id=str(state.current_work_unit_id),
        operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
        current_fingerprint="c" * 64,
        request_kind=NativeCodexRequestKind.IMPLEMENTATION,
        contract=contract,
    )
    bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=native_context,
            target_branch=state.branch,
            base_commit=head,
            authorized_paths=("src/runtime.py",),
            assignment="Implement runtime.",
            work_context="Use the bound native contract.",
            evidence=(
                NativeCodexEvidenceInput(
                    "workflow-prompt", "orchestrator_instruction", "Implement."
                ),
            ),
        )
    )
    document = {
        "schema_version": "native-agent-codex-result-v1",
        "result_type": "implementation_result",
        "request_id": bundle.bound_context.request_id,
        "ready": True,
        "test_files": [],
        "finding_dispositions": [],
    }
    canonical = canonical_native_codex_json(document)
    result = parse_bound_native_codex_contract_result(
        document, bundle.bound_context
    )
    output = NativeAgentCodexOutput(
        result=result,
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
    invocation = CodexInvocation(
        state.current_work_unit_id,
        WorkflowStep.CODEX_IMPLEMENTATION,
        1,
        "",
        native_request=bundle,
    )
    raw_path = driver._native_codex_response_path(invocation)
    driver._write_native_codex_raw_response(raw_path, canonical)

    raw_ahead_recovered = driver.recover_pending_native_codex(
        invocation,
        contract,
        WorkflowHistory(state.current_work_unit_id),
    )
    recovered = driver.recover_pending_native_codex(
        invocation,
        contract,
        WorkflowHistory(state.current_work_unit_id),
    )

    assert raw_ahead_recovered == output
    assert recovered == output
    results = tuple(
        item
        for item in ArtifactStore(repository, state.run_id).load_chain()
        if isinstance(item.payload, AgentResultPayload)
    )
    assert len(results) == 1
    assert results[0].payload.request_id == bundle.bound_context.request_id

    raw_path.write_text("{}", encoding="utf-8")
    with pytest.raises(
        WorkflowExecutionError,
        match="raw response digest differs",
    ):
        driver.recover_pending_native_codex(
            invocation,
            contract,
            WorkflowHistory(state.current_work_unit_id),
        )


def test_native_codex_record_ahead_recovery_completes_finding_responses(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/native-codex-finding-recovery")
    task = repository / "task.md"
    _write_task(task, "feature/native-codex-finding-recovery", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = (
        init_workflow_state(
            run_id="native-codex-finding-recovery",
            task_file=str(task),
            branch="feature/native-codex-finding-recovery",
            branch_base=head,
            slice_count=1,
            task_digest=hashlib.sha256(
                task.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest(),
            task_scope_patterns=("src/runtime.py",),
            target_branch="feature/native-codex-finding-recovery",
            protocol_binding=ProtocolBinding(
                ProtocolMode.STRUCTURED_V1,
                "1",
                codex_result_transport="native-codex-v1",
            ),
        )
        .complete_current_work_unit()
        .start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_CORRECTION,
        )
        .bind_current_slice_git_boundary(
            start_commit=head,
            scope_paths=("src/runtime.py",),
            start_fingerprint="c" * 64,
        )
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="Native recovery must retain the Codex disposition.",
        acceptance_test="Recover the missing response transition idempotently.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    contract = CodexStepContract(
        "native-codex-finding-recovery",
        ReadinessMarker.IMPLEMENTATION,
        "01",
        1,
        require_test_files_record=True,
        expected_test_files=(),
        test_changes_approved=True,
    )
    native_context = NativeCodexContext(
        run_id=state.run_id,
        work_unit_id=str(state.current_work_unit_id),
        operation=WorkflowStep.CODEX_CORRECTION.value,
        current_fingerprint="c" * 64,
        request_kind=NativeCodexRequestKind.CORRECTION,
        contract=contract,
        previous_findings=(finding,),
    )
    bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=native_context,
            target_branch=state.branch,
            base_commit=head,
            authorized_paths=("src/runtime.py",),
            assignment="Correct the open finding.",
            work_context="Use the bound native contract.",
            evidence=(
                NativeCodexEvidenceInput(
                    "workflow-prompt", "orchestrator_instruction", "Correct."
                ),
            ),
        )
    )
    document = {
        "schema_version": "native-agent-codex-result-v1",
        "result_type": "correction_result",
        "request_id": bundle.bound_context.request_id,
        "ready": True,
        "test_files": [],
        "finding_dispositions": [
            {
                "finding_id": "C-01",
                "decision": "accepted",
                "rationale": "The recovery path now completes durable responses.",
            }
        ],
    }
    canonical = canonical_native_codex_json(document)
    output = NativeAgentCodexOutput(
        result=parse_bound_native_codex_contract_result(
            document, bundle.bound_context
        ),
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
    invocation = CodexInvocation(
        state.current_work_unit_id,
        WorkflowStep.CODEX_CORRECTION,
        1,
        "",
        native_request=bundle,
        previous_findings=(finding,),
    )
    raw_path = driver._native_codex_response_path(invocation)
    driver._write_native_codex_raw_response(raw_path, canonical)
    bridge = driver._artifact_bridge
    assert bridge is not None
    original_append = type(bridge).append
    crash_once = {"pending": True}

    def append_with_mid_persistence_crash(self, payload, **kwargs):  # type: ignore[no-untyped-def]
        if (
            self is bridge
            and isinstance(payload, FindingTransitionPayload)
            and crash_once["pending"]
        ):
            crash_once["pending"] = False
            raise RuntimeError("crash after AgentResult persistence")
        return original_append(self, payload, **kwargs)

    monkeypatch.setattr(type(bridge), "append", append_with_mid_persistence_crash)
    with pytest.raises(RuntimeError, match="crash after AgentResult"):
        driver.persist_native_codex_contract(output, (finding,))

    incomplete = ArtifactStore(repository, state.run_id).load_chain()
    assert sum(
        isinstance(item.payload, AgentResultPayload) for item in incomplete
    ) == 1
    assert not any(
        isinstance(item.payload, FindingTransitionPayload)
        and item.payload.action == "responded"
        for item in incomplete
    )

    recovered = driver.recover_pending_native_codex(
        invocation,
        contract,
        WorkflowHistory(state.current_work_unit_id, findings=(finding,)),
    )

    assert recovered == output
    replay = replay_artifacts(
        ArtifactStore(repository, state.run_id).load_chain(), state.run_id
    )
    response = output.result.findings[0].responses[-1]
    durable_responses = tuple(
        (
            item.payload.finding_id,
            item.payload.rationale,
            item.payload.response_decision,
            item.payload.work_unit_id,
        )
        for item in replay.records
        if isinstance(item.payload, FindingTransitionPayload)
        and item.payload.action == "responded"
    )
    assert durable_responses == (
        (
            "C-01",
            response.rationale,
            response.decision.value.lower(),
            str(state.current_work_unit_id),
        ),
    )


def test_combined_native_finding_authority_rejects_state_mirror_drift(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/combined-native-authority")
    task = repository / "task.md"
    _write_task(task, "feature/combined-native-authority", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = (
        init_workflow_state(
            run_id="combined-native-authority",
            task_file=str(task),
            branch="feature/combined-native-authority",
            branch_base=head,
            slice_count=1,
            task_digest=hashlib.sha256(
                task.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest(),
            task_scope_patterns=("src/runtime.py",),
            target_branch="feature/combined-native-authority",
            protocol_binding=ProtocolBinding(
                ProtocolMode.STRUCTURED_V1,
                "1",
                claude_review_transport="native-claude-review-v1",
                codex_result_transport="native-codex-v1",
            ),
        )
        .complete_current_work_unit()
        .start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_CORRECTION,
        )
        .bind_current_slice_git_boundary(
            start_commit=head,
            scope_paths=("src/runtime.py",),
            start_fingerprint="c" * 64,
        )
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The request must use replayed findings.",
        acceptance_test="Mirror-only changes stop before provider invocation.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    bridge = driver._artifact_bridge
    assert bridge is not None
    bridge.append(
        orchestrator.finding_payload(
            finding,
            work_unit_id=state.current_work_unit_id,
        ),
        logical_id="finding-C-01",
        idempotency_key="finding:C-01:opened:1:claude",
        fingerprint_sha256="c" * 64,
    )
    second_finding = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="The projection order must not define mirror equality.",
        acceptance_test="Equivalent finding sets compare canonically by finding ID.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    bridge.append(
        orchestrator.finding_payload(
            second_finding,
            work_unit_id=state.current_work_unit_id,
        ),
        logical_id="finding-C-02",
        idempotency_key="finding:C-02:opened:1:claude",
        fingerprint_sha256="c" * 64,
    )

    # State-v3 preserves event order while replay deliberately canonicalizes by
    # finding ID. Order-only differences are not semantic mirror drift.
    assert driver.authoritative_native_findings(
        state, (second_finding, finding)
    ) == (finding, second_finding)
    with pytest.raises(
        WorkflowExecutionError,
        match="differs from the state-v3 mirror",
    ):
        driver.authoritative_native_findings(
            state,
            (
                second_finding,
                replace(finding, summary="Tampered state-only summary."),
            ),
        )


@pytest.mark.parametrize(
    ("request_kind", "step", "readiness", "result_type"),
    (
        (
            NativeCodexRequestKind.PLAN,
            WorkflowStep.CODEX_PLAN,
            ReadinessMarker.PLAN,
            "plan_result",
        ),
        (
            NativeCodexRequestKind.FINAL_REPORT,
            WorkflowStep.CODEX_FINAL_REVIEW,
            ReadinessMarker.FINAL_REPORT,
            "final_report_result",
        ),
        (
            NativeCodexRequestKind.CORRECTION,
            WorkflowStep.CODEX_CORRECTION,
            ReadinessMarker.IMPLEMENTATION,
            "correction_result",
        ),
    ),
)
def test_native_codex_plan_and_final_recovery_are_raw_and_record_ahead_safe(
    tmp_path: Path,
    request_kind: NativeCodexRequestKind,
    step: WorkflowStep,
    readiness: ReadinessMarker,
    result_type: str,
) -> None:
    branch = f"feature/native-codex-{request_kind.value}-recovery"
    repository = _repository(tmp_path, branch)
    task = repository / "task.md"
    _write_task(task, branch, "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    task_digest = hashlib.sha256(
        task.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    state = init_workflow_state(
        run_id=f"native-codex-{request_kind.value}-recovery",
        task_file=str(task),
        branch=branch,
        branch_base=head,
        slice_count=1,
        task_digest=task_digest,
        task_scope_patterns=("src/runtime.py",),
        target_branch=branch,
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V1,
            "1",
            codex_result_transport="native-codex-v1",
        ),
    )
    current_fingerprint = task_digest
    if request_kind is NativeCodexRequestKind.CORRECTION:
        state = (
            state.complete_current_work_unit()
            .start_work_unit(
                slice_id=1,
                kind=WorkUnitKind.SLICE,
                step=WorkflowStep.CODEX_IMPLEMENTATION,
            )
            .bind_current_slice_git_boundary(
                start_commit=head,
                scope_paths=("src/runtime.py",),
                start_fingerprint="c" * 64,
            )
            .with_current_step(WorkflowStep.CODEX_CORRECTION)
        )
        current_fingerprint = "c" * 64
    elif request_kind is NativeCodexRequestKind.FINAL_REPORT:
        state = (
            state.complete_current_work_unit()
            .start_work_unit(
                slice_id=1,
                kind=WorkUnitKind.SLICE,
                step=WorkflowStep.CODEX_IMPLEMENTATION,
            )
            .bind_current_slice_git_boundary(
                start_commit=head,
                scope_paths=("src/runtime.py",),
                start_fingerprint="c" * 64,
            )
            .complete_current_slice(commit_ref=head)
            .start_final_review_work_unit()
        )
        current_fingerprint = "f" * 64
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)
    contract = CodexStepContract(
        f"native-codex-{request_kind.value}-recovery",
        readiness,
        "FINAL" if request_kind is NativeCodexRequestKind.FINAL_REPORT else "01",
        1,
        review_fingerprint=(
            current_fingerprint
            if request_kind is NativeCodexRequestKind.FINAL_REPORT
            else None
        ),
        require_test_files_record=(
            request_kind is NativeCodexRequestKind.CORRECTION
        ),
        test_changes_approved=True,
        require_slice_plan=request_kind is NativeCodexRequestKind.PLAN,
        plan_artifact_path=(
            "src/runtime.py" if request_kind is NativeCodexRequestKind.PLAN else None
        ),
    )
    native_context = NativeCodexContext(
        run_id=state.run_id,
        work_unit_id=str(state.current_work_unit_id),
        operation=step.value,
        current_fingerprint=current_fingerprint,
        request_kind=request_kind,
        contract=contract,
    )
    bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=native_context,
            target_branch=state.branch,
            base_commit=head,
            authorized_paths=("src/runtime.py",),
            assignment=f"Execute native Codex {request_kind.value}.",
            work_context="Use the bound native contract.",
            evidence=(
                NativeCodexEvidenceInput(
                    "workflow-prompt", "orchestrator_instruction", "Execute."
                ),
            ),
        )
    )
    document: dict[str, object] = {
        "schema_version": "native-agent-codex-result-v1",
        "result_type": result_type,
        "request_id": bundle.bound_context.request_id,
        "ready": True,
    }
    if request_kind is NativeCodexRequestKind.PLAN:
        document["slice_plan"] = [
            {
                "slice_id": 1,
                "summary": "Implement the bound plan.",
                "scope_paths": ["src/runtime.py"],
            }
        ]
    elif request_kind is NativeCodexRequestKind.FINAL_REPORT:
        document["finding_dispositions"] = []
        document["self_check"] = "Checked branch contracts and recovery."
    else:
        document["test_files"] = []
        document["finding_dispositions"] = []
    canonical = canonical_native_codex_json(document)
    result = parse_bound_native_codex_contract_result(
        document, bundle.bound_context
    )
    output = NativeAgentCodexOutput(
        result=result,
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
    invocation = CodexInvocation(
        state.current_work_unit_id,
        step,
        1,
        "",
        native_request=bundle,
    )
    raw_path = driver._native_codex_response_path(invocation)
    driver._write_native_codex_raw_response(raw_path, canonical)

    raw_ahead_recovered = driver.recover_pending_native_codex(
        invocation,
        contract,
        WorkflowHistory(state.current_work_unit_id),
    )
    record_ahead_recovered = driver.recover_pending_native_codex(
        invocation,
        contract,
        WorkflowHistory(state.current_work_unit_id),
    )

    assert raw_ahead_recovered == output
    assert record_ahead_recovered == output
    results = tuple(
        item
        for item in ArtifactStore(repository, state.run_id).load_chain()
        if isinstance(item.payload, AgentResultPayload)
    )
    assert len(results) == 1
    assert results[0].payload.request_id == bundle.bound_context.request_id


def test_structured_bind_survives_round_number_increase_within_same_work_unit(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-round-transition")
    task = repository / "task.md"
    _write_task(task, "feature/structured-round-transition", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="structured-round-transition",
        task_file=str(task),
        branch="feature/structured-round-transition",
        branch_base=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/structured-round-transition",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1"),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="b" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )

    driver.bind_work_unit(state)
    round_two = state.record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=("C-04",),
        return_step=WorkflowStep.CODEX_IMPLEMENTATION,
    )
    driver.bind_work_unit(round_two)
    driver.bind_work_unit(round_two)

    work_units = tuple(
        item
        for item in ArtifactStore(repository, state.run_id).load_chain()
        if item.record_type is RecordType.WORK_UNIT
    )
    assert tuple(item.revision for item in work_units) == (1, 2)
    assert tuple(item.payload.round_number for item in work_units) == (1, 2)
    assert len({item.idempotency_key for item in work_units}) == 2


def test_multi_slice_plan_binding_pins_original_approved_commit_not_slice_start(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-plan-binding")
    task = repository / "task.md"
    _write_task(
        task,
        "feature/structured-plan-binding",
        "src/one.py",
        "src/two.py",
    )
    approved_plan_commit = _git(repository, "rev-parse", "HEAD")
    second_slice_start = "b" * 40
    planned_slices = (
        PlannedSlice(1, "first", ("src/one.py",)),
        PlannedSlice(2, "second", ("src/two.py",)),
    )
    state = init_workflow_state(
        run_id="structured-plan-binding",
        task_file=str(task),
        branch="feature/structured-plan-binding",
        branch_base=approved_plan_commit,
        first_slice_start_commit=approved_plan_commit,
        slice_count=2,
        task_digest="a" * 64,
        task_scope_patterns=("src/one.py", "src/two.py"),
        work_plan_path="docs/internal/approved-plan.md",
        approved_plan_commit=approved_plan_commit,
        target_branch="feature/structured-plan-binding",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1"),
    ).bind_slice_plan(
        planned_slices,
        first_start_commit=approved_plan_commit,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=approved_plan_commit,
        scope_paths=("src/one.py",),
        start_fingerprint="c" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )

    driver.bind_work_unit(state)
    slice_two = state.complete_current_slice(
        commit_ref=second_slice_start,
    ).start_work_unit(
        slice_id=2,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit=second_slice_start,
    ).bind_current_slice_git_boundary(
        start_commit=second_slice_start,
        scope_paths=("src/two.py",),
        start_fingerprint="d" * 64,
    )
    driver.bind_work_unit(slice_two)

    plan_records = tuple(
        record
        for record in ArtifactStore(repository, state.run_id).load_chain()
        if isinstance(record.payload, PlanPayload)
    )
    assert len(plan_records) == 1
    assert plan_records[0].payload.approved_plan_commit == approved_plan_commit
    assert plan_records[0].idempotency_key == f"approved-plan:{approved_plan_commit}"


def test_correction_work_unit_persists_correction_work_unit_payload_with_finding_ids(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-correction")
    task = repository / "task.md"
    _write_task(task, "feature/structured-correction", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="structured-correction",
        task_file=str(task),
        branch="feature/structured-correction",
        branch_base=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/structured-correction",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1"),
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
    ).start_final_review_work_unit().complete_current_work_unit().start_correction_work_unit(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="c" * 64,
        finding_ids=("C-14", "C-15"),
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )

    driver.bind_work_unit(state)

    correction_records = tuple(
        record
        for record in ArtifactStore(repository, state.run_id).load_chain()
        if isinstance(record.payload, CorrectionWorkUnitPayload)
    )
    assert len(correction_records) == 1
    assert correction_records[0].payload.finding_ids == ("C-14", "C-15")
    assert correction_records[0].payload.paths == ("src/runtime.py",)


def test_structured_checkpoint_projects_record_chain_into_slice_and_overall_audits(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-audit")
    task = repository / "task.md"
    _write_task(task, "feature/structured-audit", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    slice_path = "docs/internal/slice-structured-audit-01-runtime.md"
    state = init_workflow_state(
        run_id="structured-audit",
        task_file=str(task),
        branch="feature/structured-audit",
        branch_base=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=(slice_path, "src/runtime.py"),
        audit_report_path="docs/internal/structured-audit-review-12345678.md",
        target_branch="feature/structured-audit",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1"),
    ).bind_slice_plan(
        (PlannedSlice(1, "Runtime", (slice_path, "src/runtime.py")),),
        first_start_commit=head,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=(slice_path, "src/runtime.py"),
        start_fingerprint="b" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)

    driver.checkpoint(state, WorkflowHistory(state.current_work_unit_id))

    overall = repository / "docs/internal/structured-audit-review-12345678.md"
    slice_audit = repository / slice_path
    assert "<!-- artifact-records:approval-status:begin -->" in overall.read_text(
        encoding="utf-8"
    )
    rendered = slice_audit.read_text(encoding="utf-8")
    assert "Semantischer Record-Digest" in rendered
    assert "`src/runtime.py`" in rendered


def test_structured_checkpoint_stops_before_audit_on_mirror_mismatch(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-audit-mismatch")
    task = repository / "task.md"
    _write_task(task, "feature/structured-audit-mismatch", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="structured-audit-mismatch",
        task_file=str(task),
        branch="feature/structured-audit-mismatch",
        branch_base=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/structured-audit-mismatch",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1"),
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)
    mismatched = replace(state, task_scope_patterns=("src/other.py",))

    with pytest.raises(WorkflowExecutionError, match="audit dual-write mismatch"):
        driver.checkpoint(mismatched, WorkflowHistory(1))


def test_checkpoint_archives_latest_driver_history_across_work_unit_transition(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/runtime-history-checkpoint")
    task = repository / "inbox" / "runtime-history-checkpoint.md"
    task.parent.mkdir()
    _write_task(task, "feature/runtime-history-checkpoint", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    base = init_workflow_state(
        run_id="runtime-history-checkpoint-transition",
        task_file=str(task),
        branch="feature/runtime-history-checkpoint",
        branch_base=head,
        slice_count=1,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="a" * 64,
    )
    completed = base.complete_current_slice(commit_ref="b" * 40)
    latest_history = WorkflowHistory(
        1,
        codex_final_report="latest approving review evidence",
    ).to_dict()
    old_history = WorkflowHistory(
        1,
        codex_final_report="stale denying review evidence",
    ).to_dict()
    persisted = replace(
        completed,
        runtime_history={"archive": [], "current": latest_history},
    )
    stale_transition = replace(
        completed,
        runtime_history={"archive": [], "current": old_history},
    ).start_final_review_work_unit()
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = persisted

    driver.checkpoint(stale_transition, WorkflowHistory(2))

    saved = json.loads(driver.state_file.read_text(encoding="utf-8"))
    assert saved["runtime_history"]["archive"] == [latest_history]
    assert saved["runtime_history"]["current"]["work_unit_id"] == 2


def test_new_inbox_watch_task_persists_deterministic_audit_report_path(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/inbox-audit")
    inbox = repository / "inbox"
    inbox.mkdir()
    task = inbox / "RundungsDiff.md"
    _write_task(task, "feature/inbox-audit", "src/new.py")
    args = _args(repository, task)
    args.watch_run_id = "watch-inbox-audit"
    captured: dict[str, object] = {}
    real_fresh_state = orchestrator._fresh_state

    class StateCaptured(RuntimeError):
        pass

    def capture_state(**kwargs):
        state = real_fresh_state(**kwargs)
        captured["state"] = state
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    state = captured["state"]
    assert state.audit_report_path.startswith(
        "docs/internal/rundungsdiff-review-"
    )
    assert not list((repository / "docs" / "internal").glob("slice-*.md"))
    assert _git(repository, "branch", "--show-current") == "feature/inbox-audit"


def test_new_watch_task_archives_only_stale_untracked_audit_siblings(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/inbox-audit")
    internal = repository / "docs" / "internal"
    internal.mkdir(parents=True)
    tracked = internal / "fix-implement-review-11111111.md"
    tracked.write_text("tracked history\n", encoding="utf-8")
    _git(repository, "add", tracked.relative_to(repository).as_posix())
    _git(repository, "commit", "-m", "add tracked audit history")
    stale = internal / "fix-implement-review-22222222.md"
    stale.write_text("abandoned run\n", encoding="utf-8")
    current = internal / "fix-implement-review-33333333.md"
    current.write_text("current run\n", encoding="utf-8")
    unrelated = internal / "other-implement-review-44444444.md"
    unrelated.write_text("other task\n", encoding="utf-8")
    failed = repository / "outbox" / "failed"

    archived = orchestrator._archive_stale_untracked_audit_reports(
        repository,
        current_audit_path=current.relative_to(repository).as_posix(),
        outbox_failed_dir=failed,
    )

    assert len(archived) == 1
    assert archived[0].parent == failed
    assert archived[0].name.endswith(
        "fix-implement-review-22222222.md.stale-audit"
    )
    assert archived[0].read_text(encoding="utf-8") == "abandoned run\n"
    assert not stale.exists()
    assert tracked.read_text(encoding="utf-8") == "tracked history\n"
    assert current.read_text(encoding="utf-8") == "current run\n"
    assert unrelated.read_text(encoding="utf-8") == "other task\n"


def test_new_watch_task_derives_plan_contract_from_informal_idea(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    repository = _repository(tmp_path, "feature/informal-idea")
    inbox = repository / "inbox"
    inbox.mkdir()
    task = inbox / "Neue Strategie.md"
    task.write_text(
        """# Neue Strategie

TARGET_BRANCH: feature/informal-idea

Ich möchte zwei Strategien verständlich vergleichen können. Bitte untersuche
zuerst die bestehende Anwendung und frage nur bei echten Produktalternativen.
""",
        encoding="utf-8",
    )
    args = _args(repository, task)
    args.watch_run_id = "watch-informal-idea"
    captured: dict[str, object] = {}
    real_fresh_state = orchestrator._fresh_state

    class StateCaptured(RuntimeError):
        pass

    def capture_state(**kwargs):
        state = real_fresh_state(**kwargs)
        captured["state"] = state
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with caplog.at_level("INFO"), pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    state = captured["state"]
    assert state.execution_mode == "PLAN_ONLY"
    assert state.work_plan_path == "docs/internal/neue-strategie-arbeitsplan.md"
    assert state.task_scope_patterns == (
        "docs/internal/neue-strategie-arbeitsplan.md",
    )
    assert "Informal inbox intake" in caplog.text


def test_legacy_approved_inbox_plan_gets_deferred_audit_paths_before_slice_start() -> None:
    state = orchestrator.init_workflow_state(
        run_id="legacy-inbox-plan",
        task_file="/repo/inbox/RundungsDiff.md",
        branch="codex/rounding",
        branch_base="a" * 40,
        slice_count=1,
        audit_report_path="docs/internal/rundungsdiff-review-12345678.md",
    ).bind_slice_plan(
        (PlannedSlice(1, "round values", ("src/rounding.py",)),),
        first_start_commit="a" * 40,
    )

    migrated = orchestrator._attach_managed_audit_paths(state)

    assert migrated.planned_slices[0].scope_paths == (
        "docs/internal/rundungsdiff-review-12345678.md",
        "docs/internal/slice-rundungsdiff-01-round-values.md",
        "src/rounding.py",
    )
    assert not migrated.current_slice.scope_paths


def test_handoff_slice_document_is_reused_instead_of_adding_a_second_one() -> None:
    existing_slice_doc = (
        "docs/internal/slice-stress-pfad-replay-arbeitsplan-01-contracts.md"
    )
    state = orchestrator.init_workflow_state(
        run_id="handoff-inbox-plan",
        task_file="/repo/inbox/Stress_Replay-implement.md",
        branch="codex/stress-replay",
        branch_base="a" * 40,
        slice_count=1,
        audit_report_path="docs/internal/stress-replay-implement-review-12345678.md",
    ).bind_slice_plan(
        (
            PlannedSlice(
                1,
                "contracts",
                (existing_slice_doc, "src/contracts.js"),
            ),
        ),
        first_start_commit="a" * 40,
    )

    migrated = orchestrator._attach_managed_audit_paths(state)

    slice_docs = tuple(
        path
        for path in migrated.planned_slices[0].scope_paths
        if path.startswith("docs/internal/slice-")
    )
    assert slice_docs == (existing_slice_doc,)
    assert migrated.audit_report_path in migrated.planned_slices[0].scope_paths


def test_legacy_approved_inbox_plan_cannot_retrofit_after_slice_boundary() -> None:
    state = orchestrator.init_workflow_state(
        run_id="legacy-inbox-bound",
        task_file="/repo/inbox/RundungsDiff.md",
        branch="codex/rounding",
        branch_base="a" * 40,
        slice_count=1,
        audit_report_path="docs/internal/rundungsdiff-review-12345678.md",
    ).bind_slice_plan(
        (PlannedSlice(1, "round values", ("src/rounding.py",)),),
        first_start_commit="a" * 40,
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=("src/rounding.py",),
        start_fingerprint="b" * 64,
    )

    with pytest.raises(WorkflowExecutionError, match="cannot retrofit"):
        orchestrator._attach_managed_audit_paths(state)


def test_final_correction_rejects_audit_only_persisted_scope(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "feature/audit-only-correction")
    task = repository / "inbox" / "audit-only.md"
    task.parent.mkdir()
    _write_task(task, "feature/audit-only-correction", "src/rounding.py")
    audit_path = "docs/internal/audit-only-review-12345678.md"
    state = orchestrator.init_workflow_state(
        run_id="audit-only-correction",
        task_file=str(task),
        branch="feature/audit-only-correction",
        branch_base=_git(repository, "rev-parse", "HEAD"),
        slice_count=1,
        audit_report_path=audit_path,
    ).bind_slice_plan(
        (
            PlannedSlice(
                1,
                "managed records only",
                (
                    audit_path,
                    "docs/internal/slice-audit-only-01-managed-records-only.md",
                ),
            ),
        ),
        first_start_commit=_git(repository, "rev-parse", "HEAD"),
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = state

    with pytest.raises(
        WorkflowExecutionError,
        match="final correction has no persisted remediation scope",
    ):
        driver.prepare_correction(())


def test_runtime_inherits_exact_prior_test_gate_before_early_resume_return() -> None:
    test_path = "tests/rounding.test.mjs"
    fingerprint = "c" * 64
    state = orchestrator.init_workflow_state(
        run_id="resume-repeated-test-gate",
        task_file="/repo/inbox/rounding.md",
        branch="feature/rounding",
        branch_base="a" * 40,
        slice_count=1,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=(test_path,),
        start_fingerprint="b" * 64,
    ).await_user_gate(
        reason=GateReason.TEST_CHANGE,
        detail="test approval required",
        fingerprint=fingerprint,
        paths=(test_path,),
    ).record_user_gate_decision(
        approved=True,
        fingerprint=fingerprint,
        paths=(test_path,),
        decided_by="dieter",
        decided_at="2026-08-14T17:11:21+00:00",
        rationale="planned regression test reviewed",
    ).record_active_test_approval(
        fingerprint,
        (test_path,),
    ).complete_current_slice(
        commit_ref="d" * 40,
    ).start_final_review_work_unit().await_user_gate(
        reason=GateReason.TEST_CHANGE,
        detail="test changes require explicit approval before review",
        fingerprint=fingerprint,
        paths=(test_path,),
    )

    resumed = orchestrator._inherit_redundant_test_gate(state)

    assert resumed.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert resumed.current_work_unit.gate.reason is GateReason.NONE
    assert resumed.current_work_unit.active_test_fingerprint == fingerprint
    assert resumed.current_work_unit.active_test_paths == (test_path,)


def test_runtime_recognizes_exact_reopened_gate_approval_for_plain_resume() -> None:
    paths = ("src/agent_runtime.py", "tests/test_agent_runtime.py")
    fingerprint = "a" * 64
    state = orchestrator.init_workflow_state(
        run_id="resume-reopened-user-gate",
        task_file="/repo/inbox/native-review.md",
        branch="feature/native-review",
        branch_base="b" * 40,
        slice_count=1,
    ).await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="reviewed diagnostic hotfix",
        fingerprint=fingerprint,
        paths=paths,
    ).record_user_gate_decision(
        approved=True,
        fingerprint=fingerprint,
        paths=paths,
        decided_by="dieter",
        decided_at="2026-08-23T10:24:26+00:00",
        rationale="fingerprint-bound hotfix reviewed",
    ).await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="same gate rediscovered after resume",
        fingerprint=fingerprint,
        paths=paths,
    )

    approval = orchestrator._current_gate_approval(state)

    assert approval is state.current_work_unit.gate_decisions[0]
    assert approval.rationale == "fingerprint-bound hotfix reviewed"


def test_runtime_does_not_reuse_gate_approval_for_changed_fingerprint() -> None:
    paths = ("src/agent_runtime.py", "tests/test_agent_runtime.py")
    state = orchestrator.init_workflow_state(
        run_id="resume-changed-user-gate",
        task_file="/repo/inbox/native-review.md",
        branch="feature/native-review",
        branch_base="b" * 40,
        slice_count=1,
    ).await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="first hotfix fingerprint",
        fingerprint="a" * 64,
        paths=paths,
    ).record_user_gate_decision(
        approved=True,
        fingerprint="a" * 64,
        paths=paths,
        decided_by="dieter",
        decided_at="2026-08-23T10:24:26+00:00",
        rationale="first fingerprint reviewed",
    ).await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="changed repository fingerprint",
        fingerprint="c" * 64,
        paths=paths,
    )

    assert orchestrator._current_gate_approval(state) is None


def test_new_watch_task_does_not_require_a_conventional_base_branch(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "trunk-repository"
    repository.mkdir()
    _git(repository, "init", "-b", "trunk")
    _git(repository, "config", "user.name", "Slice Test")
    _git(repository, "config", "user.email", "slice@example.invalid")
    (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
    (repository / ".gitignore").write_text(".orchestrator/\n", encoding="utf-8")
    _git(repository, "add", "seed.txt", ".gitignore")
    _git(repository, "commit", "-m", "seed")
    task = tmp_path / "trunk-task.md"
    _write_task(task, "feature/from-trunk", "src/new.py")
    args = _args(repository, task)
    args.watch_run_id = "watch-from-trunk"
    captured = {}
    real_fresh_state = orchestrator._fresh_state

    class StateCaptured(RuntimeError):
        pass

    def capture_state(**kwargs):
        captured["state"] = real_fresh_state(**kwargs)
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    assert captured["state"].branch == "feature/from-trunk"
    assert captured["state"].branch_base == _git(repository, "rev-parse", "HEAD")


def test_watch_retry_before_first_state_is_treated_as_new_task(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/source")
    _git(repository, "switch", "master")
    task = tmp_path / "inbox-retry.md"
    _write_task(task, "feature/retried", "src/new.py")
    args = _args(repository, task)
    args.watch_run_id = "watch-retry-without-state"
    captured_states = []

    class StateCaptured(RuntimeError):
        pass

    real_fresh_state = orchestrator._fresh_state

    def capture_state(**kwargs):
        state = real_fresh_state(**kwargs)
        assert state.run_id == "watch-retry-without-state"
        captured_states.append(state)
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    created_head = _git(repository, "rev-parse", "HEAD")
    args.resume = True
    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=False)

    assert _git(repository, "branch", "--show-current") == "feature/retried"
    assert len(captured_states) == 2
    assert captured_states[1].branch_base == created_head


def test_new_watch_task_can_switch_with_unignored_in_repository_control_files(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/inbox-control-target")
    _git(repository, "switch", "master")
    inbox = repository / "CustomInbox"
    inbox.mkdir()
    task = inbox / "bug.md"
    _write_task(task, "feature/inbox-control-target", "src/new.py")
    (inbox / ".bug.md.watch.json").write_text("identity\n", encoding="utf-8")
    args = _args(repository, task)
    args.watch_run_id = "watch-control-files"

    class StateCaptured(RuntimeError):
        pass

    real_fresh_state = orchestrator._fresh_state

    def capture_state(**kwargs):
        real_fresh_state(**kwargs)
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    assert _git(repository, "branch", "--show-current") == (
        "feature/inbox-control-target"
    )
    assert task.is_file()
    assert (inbox / ".bug.md.watch.json").is_file()


def test_new_plan_watch_task_carries_existing_untracked_bound_plan(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/existing-plan-target")
    _git(repository, "switch", "master")
    plan = repository / "docs" / "internal" / "existing-plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Existing plan\n", encoding="utf-8")
    inbox = repository / "inbox"
    inbox.mkdir()
    task = inbox / "existing-plan.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/existing-plan.md",
                "TARGET_BRANCH: feature/existing-plan-target",
                "TASK_SCOPE: docs/internal/existing-plan.md",
            )
        ),
        encoding="utf-8",
    )
    args = _args(repository, task)
    args.watch_run_id = "watch-existing-plan"
    captured: dict[str, object] = {}
    real_fresh_state = orchestrator._fresh_state

    class StateCaptured(RuntimeError):
        pass

    def capture_state(**kwargs):
        state = real_fresh_state(**kwargs)
        captured["state"] = state
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    state = captured["state"]
    assert state.execution_mode == "PLAN_ONLY"
    assert state.work_plan_path == "docs/internal/existing-plan.md"
    assert _git(repository, "branch", "--show-current") == (
        "feature/existing-plan-target"
    )
    assert plan.read_text(encoding="utf-8") == "# Existing plan\n"


def test_watch_pipeline_failure_before_state_disables_resume(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/preflight-target")
    _git(repository, "switch", "master")
    (repository / "foreign.txt").write_text("blocks switch\n", encoding="utf-8")
    task = tmp_path / "preflight-task.md"
    _write_task(task, "feature/preflight-target", "src/new.py")
    args = _args(repository, task)
    args.watch_run_id = "watch-preflight-failure"
    monkeypatch.chdir(repository)

    result = run_pipeline(task, args, force_new=True)

    assert isinstance(result, WatchTaskResult)
    assert result.disposition is WatchTaskDisposition.TECHNICAL_FAILURE
    assert result.step == "pipeline"
    assert result.resume_available is False
    assert not (repository / ".orchestrator" / "state.json").exists()


def test_watch_resume_does_not_switch_back_after_branch_drift(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/persisted-target")
    task = tmp_path / "persisted-task.md"
    _write_task(task, "feature/persisted-target", "src/new.py")
    contract = parse_task_contract(task.read_text(encoding="utf-8"))
    state = replace(
        orchestrator._fresh_state(
            task_file=task,
            run_id="watch-persisted-run",
            repository_root=repository,
            task_contract=contract,
        ),
        protocol_binding=None,
    )
    state_file = repository / ".orchestrator" / "state.json"
    save_workflow_state(
        state_file,
        state,
        allowed_roots=(repository, task.parent.resolve()),
    )
    _git(repository, "switch", "master")
    args = _args(repository, task)
    args.watch_run_id = "watch-persisted-run"
    args.resume = True

    def unexpected_prepare(*_args, **_kwargs):
        raise AssertionError("resume must not prepare or switch branches")

    monkeypatch.setattr(
        orchestrator,
        "prepare_new_watch_task_branch",
        unexpected_prepare,
    )
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, args)

    assert result.exit_code == 4
    assert "BRANCH-MISMATCH" in result.state.current_work_unit.gate.detail
    assert _git(repository, "branch", "--show-current") == "master"


def test_prepared_watch_head_change_is_rejected_before_state_initialization(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/head-race")
    task = tmp_path / "head-race.md"
    _write_task(task, "feature/head-race", "src/new.py")
    contract = parse_task_contract(task.read_text(encoding="utf-8"))
    prepared_head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "commit", "--allow-empty", "-m", "concurrent head move")

    with pytest.raises(StateSchemaError, match="HEAD changed"):
        orchestrator._fresh_state(
            task_file=task,
            run_id="watch-head-race",
            repository_root=repository,
            task_contract=contract,
            branch_base_override=prepared_head,
        )


def test_force_new_watch_task_intentionally_replaces_unrelated_existing_state(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/old-watch-target")
    old_task = tmp_path / "old-task.md"
    _write_task(old_task, "feature/old-watch-target", "src/old.py")
    old_contract = parse_task_contract(old_task.read_text(encoding="utf-8"))
    old_state = orchestrator._fresh_state(
        task_file=old_task,
        run_id="old-watch-run",
        repository_root=repository,
        task_contract=old_contract,
    )
    save_workflow_state(
        repository / ".orchestrator" / "state.json",
        old_state,
        allowed_roots=(repository, old_task.parent.resolve()),
    )
    old_checkpoint = write_workflow_checkpoint(
        repository / ".orchestrator" / "checkpoints",
        old_state,
        allowed_roots=(repository, old_task.parent.resolve()),
    )
    _git(repository, "switch", "master")
    new_task = tmp_path / "new-task.md"
    _write_task(new_task, "feature/new-watch-target", "src/new.py")
    args = _args(repository, new_task)
    args.watch_run_id = "new-watch-run"
    captured = {}

    class StateCaptured(RuntimeError):
        pass

    def capture_state(_engine, state, _context, _history):
        captured["state"] = orchestrator.load_workflow_state(
            repository / ".orchestrator" / "state.json",
            allowed_roots=(repository, new_task.parent.resolve()),
        )
        raise StateCaptured

    monkeypatch.setattr(WorkflowEngine, "run_current_work_unit", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(new_task, args, force_new=True)

    assert captured["state"].run_id == "new-watch-run"
    assert captured["state"].branch == "feature/new-watch-target"
    assert captured["state"].protocol_binding == ProtocolBinding(
        ProtocolMode.STRUCTURED_V1, "1"
    )
    assert orchestrator.load_workflow_state(
        old_checkpoint,
        allowed_roots=(repository, new_task.parent.resolve()),
    ).run_id == "old-watch-run"
    new_checkpoint = write_workflow_checkpoint(
        repository / ".orchestrator" / "checkpoints" / "new-watch-run",
        captured["state"],
        allowed_roots=(repository, new_task.parent.resolve()),
    )
    assert new_checkpoint.parent.name == "new-watch-run"
    assert orchestrator.load_workflow_state(
        new_checkpoint,
        allowed_roots=(repository, new_task.parent.resolve()),
    ).run_id == "new-watch-run"
    assert _git(repository, "branch", "--show-current") == (
        "feature/new-watch-target"
    )


def test_watch_state_schema_error_is_a_single_non_retryable_policy_halt(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/state-error")
    task = tmp_path / "state-error.md"
    _write_task(task, "feature/state-error", "src/new.py")
    args = _args(repository, task)
    args.watch_run_id = "watch-state-error"

    def fail(*_args, **_kwargs):
        raise StateSchemaError("deterministic state conflict")

    monkeypatch.setattr(orchestrator, "run_production_workflow", fail)

    result = run_pipeline(task, args, force_new=True)

    assert isinstance(result, WatchTaskResult)
    assert result.exit_code == 4
    assert result.disposition is WatchTaskDisposition.RESUMABLE_HALT
    assert result.gate_reason == "state_contract"
    assert result.resume_available is False
    assert result.failure_detail == (
        "StateSchemaError: deterministic state conflict"
    )


def test_direct_task_still_requires_target_branch_to_be_active(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/inactive-target")
    _git(repository, "switch", "master")
    task = tmp_path / "direct-task.md"
    _write_task(task, "feature/inactive-target", "src/new.py")
    monkeypatch.chdir(repository)

    with pytest.raises(StateSchemaError, match="TARGET_BRANCH mismatch"):
        run_production_workflow(task, _args(repository, task))

    assert _git(repository, "branch", "--show-current") == "master"


def test_inbox_workflow_creates_slice_audit_at_implementation_start_and_finalizes_overall(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/inbox-lifecycle")
    inbox = repository / "inbox"
    inbox.mkdir()
    task = inbox / "AuditLifecycle.md"
    _write_task(task, "feature/inbox-lifecycle", "app/result.py")
    observations = {"plan_without_slice": False, "implementation_with_slice": False}

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        slice_docs = list((repository / "docs" / "internal").glob("slice-*.md"))
        if invocation.step is WorkflowStep.CODEX_PLAN:
            assert slice_docs == []
            output = "\n".join(
                (
                    "SLICE_PLAN: 1 | implement result | app/result.py",
                    "PLAN_READY: YES",
                    "STATUS: DONE",
                )
            )
        elif invocation.step is WorkflowStep.CODEX_IMPLEMENTATION:
            observations["implementation_with_slice"] = len(slice_docs) == 1
            (repository / "app").mkdir(exist_ok=True)
            (repository / "app" / "result.py").write_text("VALUE = 1\n", encoding="utf-8")
            output = "TEST_FILES_TOUCHED: NONE\nIMPLEMENTATION_READY: 01 | YES\nSTATUS: DONE"
        else:
            output = "FINAL_REPORT_READY: YES\nSTATUS: DONE"
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        if invocation.step in {
            WorkflowStep.CLAUDE_PLAN_REVIEW,
            WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
        }:
            observations["plan_without_slice"] = not list(
                (repository / "docs" / "internal").glob("slice-*.md")
            )
            marker = "PLAN_APPROVAL: YES"
        elif invocation.step in {
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
        }:
            marker = "FINAL_APPROVAL: YES"
        else:
            marker = "SLICE_APPROVAL: 01 | YES"
        return _review(invocation.reviewer, marker)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task), force_new=True)

    assert result.workflow_completed
    assert observations == {
        "plan_without_slice": True,
        "implementation_with_slice": True,
    }
    docs = sorted((repository / "docs" / "internal").glob("*.md"))
    assert len(docs) == 2
    overall = next(path for path in docs if "-review-" in path.name)
    slice_doc = next(path for path in docs if path.name.startswith("slice-"))
    overall_text = overall.read_text(encoding="utf-8")
    assert "Work Unit 01 – Planung" in overall_text
    assert "Work Unit 02 – Slice 01" in overall_text
    assert "Work Unit 03 – Gesamtreview" in overall_text
    assert "Review-Feedback von Claude" in slice_doc.read_text(encoding="utf-8")
    assert len(_git(repository, "log", "--format=%s", "master..HEAD").splitlines()) == 2
    assert _git(repository, "status", "--short", "--untracked-files=all") == (
        "?? inbox/AuditLifecycle.md"
    )


def test_production_session_plans_commits_two_slices_and_persists_final_state(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/runtime-test")
    task = tmp_path / "task.md"
    _write_task(task, "feature/runtime-test", "src/first.py", "src/second.py")
    interrupt_once = {"value": True}

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        if invocation.step is WorkflowStep.CODEX_PLAN:
            output = "\n".join(
                (
                    "SLICE_PLAN: 1 | add first file | src/first.py",
                    "SLICE_PLAN: 2 | add second file | src/second.py",
                    "PLAN_READY: YES",
                    "STATUS: DONE",
                )
            )
        elif invocation.step is WorkflowStep.CODEX_IMPLEMENTATION:
            source = repository / "src"
            source.mkdir(exist_ok=True)
            slice_id = driver.active_state.current_slice_id
            (source / ("first.py" if slice_id == 1 else "second.py")).write_text(
                f"VALUE = {slice_id}\n", encoding="utf-8"
            )
            finding_response = (
                "FINDING_RESPONSE: C-01 | ACCEPTED | retained for final disposition\n"
                if slice_id == 2
                else ""
            )
            output = (
                finding_response
                +
                f"TEST_FILES_TOUCHED: NONE\n"
                f"IMPLEMENTATION_READY: {slice_id:02d} | YES\nSTATUS: DONE"
            )
        else:
            output = (
                "FINDING_RESPONSE: C-01 | ACCEPTED | full branch evidence resolves it\n"
                "FINAL_REPORT_READY: YES\nSTATUS: DONE"
            )
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        if (
            interrupt_once["value"]
            and invocation.step is WorkflowStep.ANTIGRAVITY_SLICE_REVIEW
        ):
            interrupt_once["value"] = False
            raise AgentInvocationError(
                agent_key="antigravity",
                kind=AgentFailureKind.NETWORK,
                invocation_id="resume-proof",
                provider_text="temporary provider outage",
                received_at=datetime.now(timezone.utc),
            )
        if invocation.step in {
            WorkflowStep.CLAUDE_PLAN_REVIEW,
            WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
        }:
            marker = "PLAN_APPROVAL: YES"
        elif invocation.step in {
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
        }:
            marker = "FINAL_APPROVAL: YES"
            rendered = _review(invocation.reviewer, marker)
            if invocation.reviewer is AgentRole.CLAUDE:
                rendered = rendered.replace(
                    "PRE_MORTEM:",
                    "FINDING_STATUS: C-01 | CLOSED | full branch evidence resolves it\n"
                    "PRE_MORTEM:",
                )
            return rendered
        else:
            slice_id = invocation.paths[0].split("/")[-1] == "first.py" and "01" or "02"
            marker = f"SLICE_APPROVAL: {slice_id} | YES"
            rendered = _review(invocation.reviewer, marker)
            if invocation.reviewer is AgentRole.CLAUDE and slice_id == "01":
                rendered = rendered.replace(
                    "PRE_MORTEM:",
                    "NEW_FINDING: C-01 | OBSERVATION | cross-slice risk | "
                    "disposition during final review\nPRE_MORTEM:",
                )
            elif invocation.reviewer is AgentRole.CLAUDE and slice_id == "02":
                rendered = rendered.replace(
                    "PRE_MORTEM:",
                    "FINDING_STATUS: C-01 | OPEN | keep until full branch review\n"
                    "PRE_MORTEM:",
                )
            return rendered
        return _review(invocation.reviewer, marker)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)
    args = _args(repository, task)
    args.transient_retry_policy = replace(
        args.transient_retry_policy, automatic=False
    )

    halted = run_production_workflow(task, args)
    assert halted.exit_code == 3
    assert halted.state.current_step is WorkflowStep.ANTIGRAVITY_SLICE_REVIEW
    assert not _git(repository, "log", "--format=%s", "master..HEAD")

    args.resume = True
    args.auto_resume = False
    result = run_production_workflow(task, args)

    assert result.workflow_completed
    assert [item.status.value for item in result.state.slices] == ["completed", "completed"]
    assert len(_git(repository, "log", "--format=%s", "master..HEAD").splitlines()) == 2
    state = json.loads(
        (repository / ".orchestrator" / "state.json").read_text(encoding="utf-8")
    )
    assert state["version"] == 3
    assert state["work_units"][-1]["kind"] == "final_review"
    assert state["work_units"][-1]["status"] == "completed"
    assert [item["work_unit_id"] for item in state["runtime_history"]["archive"]] == [1, 2, 3]
    assert state["runtime_history"]["current"]["work_unit_id"] == 4
    assert state["runtime_history"]["current"]["events"]
    assert state["runtime_history"]["current"]["findings"][0]["status"] == "CLOSED"
    slice_two_history = state["runtime_history"]["archive"][2]
    assert slice_two_history["findings"][0]["status"] == "OPEN"


def test_head_drift_after_plan_becomes_typed_persisted_halt(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/head-drift")
    task = tmp_path / "task.md"
    _write_task(task, "feature/head-drift", "src/one.py")

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        _git(repository, "commit", "--allow-empty", "-m", "external drift")
        output = (
            "SLICE_PLAN: 1 | add file | src/one.py\n"
            "PLAN_READY: YES\nSTATUS: DONE"
        )
        driver.last_codex_output = output
        return output

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "invoke_reviewer",
        lambda _driver, invocation: _review(
            invocation.reviewer, "PLAN_APPROVAL: YES"
        ),
    )
    monkeypatch.chdir(repository)
    result = run_production_workflow(task, _args(repository, task))

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason.value == "unexpected_file"
    assert "SLICE-HEAD-DRIFT" in result.state.current_work_unit.gate.detail
    persisted = json.loads(
        (repository / ".orchestrator" / "state.json").read_text(encoding="utf-8")
    )
    assert persisted["work_units"][-1]["status"] == "awaiting_user_decision"


def test_empty_implementation_is_a_typed_halt_not_cli_crash(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/empty-slice")
    task = tmp_path / "task.md"
    _write_task(task, "feature/empty-slice", "src/one.py")

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        if invocation.step is WorkflowStep.CODEX_PLAN:
            output = (
                "SLICE_PLAN: 1 | add file | src/one.py\n"
                "PLAN_READY: YES\nSTATUS: DONE"
            )
        else:
            output = (
                "TEST_FILES_TOUCHED: NONE\n"
                "IMPLEMENTATION_READY: 01 | YES\nSTATUS: DONE"
            )
        driver.last_codex_output = output
        return output

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "invoke_reviewer",
        lambda _driver, invocation: _review(
            invocation.reviewer, "PLAN_APPROVAL: YES"
        ),
    )
    monkeypatch.chdir(repository)
    args = _args(repository, task)
    result = run_production_workflow(task, args)

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason.value == "stop_request"
    assert "NO-IMPLEMENTATION-CHANGES" in result.state.current_work_unit.gate.detail
    args.force_overwrite_state = True
    args.resume = False
    assert run_pipeline(task, args, force_new=True) == 4


def test_not_ready_gate_resumes_with_plain_resume_without_explicit_approval(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/not-ready-resume")
    task = tmp_path / "task.md"
    _write_task(task, "feature/not-ready-resume", "src/one.py")
    implementation_attempts = {"count": 0}

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        if invocation.step is WorkflowStep.CODEX_PLAN:
            output = (
                "SLICE_PLAN: 1 | add file | src/one.py\n"
                "PLAN_READY: YES\nSTATUS: DONE"
            )
        elif invocation.step is WorkflowStep.CODEX_IMPLEMENTATION:
            implementation_attempts["count"] += 1
            if implementation_attempts["count"] == 1:
                output = (
                    "TEST_FILES_TOUCHED: NONE\n"
                    "IMPLEMENTATION_READY: 01 | NO\nSTATUS: DONE"
                )
            else:
                source = repository / "src"
                source.mkdir(exist_ok=True)
                (source / "one.py").write_text("VALUE = 1\n", encoding="utf-8")
                output = (
                    "TEST_FILES_TOUCHED: NONE\n"
                    "IMPLEMENTATION_READY: 01 | YES\nSTATUS: DONE"
                )
        else:
            output = "FINAL_REPORT_READY: YES\nSTATUS: DONE"
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        if invocation.step in {
            WorkflowStep.CLAUDE_PLAN_REVIEW,
            WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
        }:
            marker = "PLAN_APPROVAL: YES"
        elif invocation.step in {
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
        }:
            marker = "FINAL_APPROVAL: YES"
        else:
            marker = "SLICE_APPROVAL: 01 | YES"
        return _review(invocation.reviewer, marker)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)
    args = _args(repository, task)

    halted = run_production_workflow(task, args)

    assert halted.exit_code == 4
    assert halted.state.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert halted.state.current_work_unit.gate.reason.value == "stop_request"
    assert halted.state.current_work_unit.gate.fingerprint is None

    args.resume = True
    args.auto_resume = False
    resumed = run_production_workflow(task, args)

    assert resumed.workflow_completed
    assert implementation_attempts["count"] == 2


def test_plan_only_uses_internal_plan_validation_and_commits_no_product_code(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/plan-only")
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/work-plan.md",
                "TARGET_BRANCH: feature/plan-only",
                "TASK_SCOPE: docs/internal/work-plan.md",
                "",
                "Create only the reviewed work plan.",
            )
        ),
        encoding="utf-8",
    )

    codex_steps: list[WorkflowStep] = []

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        codex_steps.append(invocation.step)
        if invocation.step is WorkflowStep.CODEX_PLAN:
            plan = repository / "docs" / "internal" / "work-plan.md"
            plan.parent.mkdir(parents=True)
            plan.write_text(
                "# Work plan\n\n### Slice 1 – Future implementation\n\n"
                "**Exakter Änderungspfad**\n\n- `src/future.py`\n\n"
                "#### \u0041kzeptanzkriterien\n\n- Future behavior is covered.\n",
                encoding="utf-8",
            )
            output = (
                "SLICE_PLAN: 1 | create reviewed work plan | docs/internal/work-plan.md\n"
                "PLAN_READY: YES\nSTATUS: DONE"
            )
        elif invocation.step is WorkflowStep.CODEX_IMPLEMENTATION:
            output = (
                "TEST_FILES_TOUCHED: NONE\n"
                "IMPLEMENTATION_READY: 01 | YES\nSTATUS: DONE"
            )
        else:
            output = "FINAL_REPORT_READY: YES\nSTATUS: DONE"
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        if invocation.step in {
            WorkflowStep.CLAUDE_PLAN_REVIEW,
            WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
        }:
            marker = "PLAN_APPROVAL: YES"
        elif invocation.step in {
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
        }:
            marker = "FINAL_APPROVAL: YES"
        else:
            marker = "SLICE_APPROVAL: 01 | YES"
        return _review(invocation.reviewer, marker)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.workflow_completed
    assert codex_steps == [WorkflowStep.CODEX_PLAN]
    assert [item.kind.value for item in result.state.work_units] == ["plan"]
    assert _git(repository, "show", "--pretty=", "--name-only", "HEAD") == (
        "docs/internal/work-plan.md"
    )
    handoff = task.with_name("task-implement.md")
    assert handoff.is_file()
    handoff_text = handoff.read_text(encoding="utf-8")
    assert "ORCHESTRATOR_MODE: IMPLEMENT" in handoff_text
    assert f"APPROVED_PLAN_COMMIT: {result.commit_ref}" in handoff_text
    assert "SLICE_PLAN: 1 | Future implementation |" in handoff_text
    assert "src/future.py" in handoff_text
    assert "docs/internal/slice-work-plan-01-future-implementation.md" in handoff_text
    histories = [
        result.state.runtime_history["current"],
        *result.state.runtime_history["archive"],
    ]
    attestations = [
        event["attestation"]
        for history in histories
        for event in history.get("events", [])
        if event.get("kind") == "validation"
    ]
    assert attestations
    assert all(
        item["expected_commands"] == ["internal:work-plan-contract"]
        for item in attestations
    )


def test_plan_only_retries_non_handoff_plan_once_then_halts_before_review(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/invalid-plan")
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/work-plan.md",
                "TARGET_BRANCH: feature/invalid-plan",
                "TASK_SCOPE: docs/internal/work-plan.md",
            )
        ),
        encoding="utf-8",
    )
    reviewer_steps: list[WorkflowStep] = []
    codex_steps: list[WorkflowStep] = []

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        codex_steps.append(invocation.step)
        plan = repository / "docs" / "internal" / "work-plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "# Work plan\n\n### Slice 1 - Future implementation\n\n"
            "No exact path section.\n",
            encoding="utf-8",
        )
        output = (
            "SLICE_PLAN: 1 | create reviewed work plan | docs/internal/work-plan.md\n"
            "PLAN_READY: YES\nSTATUS: DONE"
        )
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        reviewer_steps.append(invocation.step)
        return _review(invocation.reviewer, "PLAN_APPROVAL: YES")

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.exit_code == 4
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert "PLAN-CONTRACT-INVALID" in result.state.current_work_unit.gate.detail
    assert codex_steps == [WorkflowStep.CODEX_PLAN, WorkflowStep.CODEX_PLAN_REVISION]
    assert reviewer_steps == []
    assert _git(repository, "status", "--short") == "?? docs/"


def test_plan_only_repairs_handoff_contract_before_review(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/repaired-plan")
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/work-plan.md",
                "TARGET_BRANCH: feature/repaired-plan",
                "TASK_SCOPE: docs/internal/work-plan.md",
            )
        ),
        encoding="utf-8",
    )
    codex_steps: list[WorkflowStep] = []
    reviewer_steps: list[WorkflowStep] = []

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        codex_steps.append(invocation.step)
        plan = repository / "docs" / "internal" / "work-plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        if invocation.step is WorkflowStep.CODEX_PLAN:
            body = "No exact path section.\n"
        else:
            assert "AUTOMATIC PLAN CONTRACT REPAIR" in invocation.prompt
            assert "Slice 1 has no exact change-path section" in invocation.prompt
            body = (
                "**Exakter Änderungspfad**\n\n- `src/future.py`\n\n"
                "#### \u0041kzeptanzkriterien\n\n- Future behavior is covered.\n"
            )
        plan.write_text(
            "# Work plan\n\n### Slice 1 - Future implementation\n\n" + body,
            encoding="utf-8",
        )
        output = (
            "SLICE_PLAN: 1 | create reviewed work plan | docs/internal/work-plan.md\n"
            "PLAN_READY: YES\nSTATUS: DONE"
        )
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        reviewer_steps.append(invocation.step)
        return _review(invocation.reviewer, "PLAN_APPROVAL: YES")

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.workflow_completed
    assert codex_steps == [WorkflowStep.CODEX_PLAN, WorkflowStep.CODEX_PLAN_REVISION]
    assert reviewer_steps == [
        WorkflowStep.CLAUDE_PLAN_REVIEW,
        WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
    ]
    assert task.with_name("task-implement.md").is_file()


def test_generated_implementation_handoff_skips_second_plan_review(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/handoff")
    task = tmp_path / "guide-plan.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/guide.md",
                "TARGET_BRANCH: feature/handoff",
                "TASK_SCOPE: docs/internal/guide.md",
                "",
                "Create the reviewed guide plan.",
            )
        ),
        encoding="utf-8",
    )
    steps: list[WorkflowStep] = []

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        steps.append(invocation.step)
        if invocation.step is WorkflowStep.CODEX_PLAN:
            plan = repository / "docs" / "internal" / "guide.md"
            plan.parent.mkdir(parents=True)
            plan.write_text(
                "# Guide plan\n\n### Slice 1 - Rewrite guide\n\n"
                "**Ziel**\n\nRewrite the guide.\n\n"
                "**Exakter Änderungspfad**\n\n- `Guide.html`\n\n"
                "#### \u0041kzeptanzkriterien\n\n- The guide is rewritten.\n",
                encoding="utf-8",
            )
            output = (
                "SLICE_PLAN: 1 | create guide plan | docs/internal/guide.md\n"
                "PLAN_READY: YES\nSTATUS: DONE"
            )
        elif invocation.step is WorkflowStep.CODEX_IMPLEMENTATION:
            (repository / "Guide.html").write_text("<h1>Guide</h1>\n", encoding="utf-8")
            output = "TEST_FILES_TOUCHED: NONE\nIMPLEMENTATION_READY: 01 | YES\nSTATUS: DONE"
        else:
            output = "FINAL_REPORT_READY: YES\nSTATUS: DONE"
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        if invocation.step in {
            WorkflowStep.CLAUDE_PLAN_REVIEW,
            WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
        }:
            marker = "PLAN_APPROVAL: YES"
        elif invocation.step in {
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
        }:
            marker = "FINAL_APPROVAL: YES"
        else:
            marker = "SLICE_APPROVAL: 01 | YES"
        return _review(invocation.reviewer, marker)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    plan_result = run_production_workflow(task, _args(repository, task))
    assert plan_result.workflow_completed
    handoff = task.with_name("guide-implement.md")
    assert handoff.is_file()
    plan_chain = ArtifactStore(repository, plan_result.state.run_id).load_chain()
    assert any(
        item.record_type is RecordType.BINDING
        and item.payload.binding_kind == "implementation_handoff"
        and item.payload.target == str(handoff.resolve())
        for item in plan_chain
    )
    assert any(
        item.record_type is RecordType.WORKFLOW_COMPLETION
        and item.payload.outcome == "completed"
        for item in plan_chain
    )

    implementation_args = _args(repository, handoff)
    implementation_args.resume = False
    implementation_args.force_overwrite_state = True
    implementation = run_production_workflow(handoff, implementation_args)

    assert implementation.workflow_completed
    assert steps.count(WorkflowStep.CODEX_PLAN) == 1
    assert steps.count(WorkflowStep.CODEX_IMPLEMENTATION) == 1
    assert (repository / "docs/internal/slice-guide-01-rewrite-guide.md").is_file()
    assert _git(repository, "show", "--pretty=", "--name-only", "HEAD").splitlines() == [
        "Guide.html",
        "docs/internal/slice-guide-01-rewrite-guide.md",
    ]


def test_completed_plan_resume_retries_failed_handoff_without_agents(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/handoff-resume")
    task = tmp_path / "resume-plan.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/resume.md",
                "TARGET_BRANCH: feature/handoff-resume",
                "TASK_SCOPE: docs/internal/resume.md",
                "",
                "Create the reviewed resume plan.",
            )
        ),
        encoding="utf-8",
    )
    agent_steps: list[WorkflowStep] = []

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        agent_steps.append(invocation.step)
        plan = repository / "docs" / "internal" / "resume.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "# Resume plan\n\n### Slice 1 – Implement resume\n\n"
            "**Exakter Änderungspfad**\n\n- `src/resume.py`\n\n"
            "#### \u0041kzeptanzkriterien\n\n- Resume behavior is covered.\n",
            encoding="utf-8",
        )
        output = (
            "SLICE_PLAN: 1 | create resume plan | docs/internal/resume.md\n"
            "PLAN_READY: YES\nSTATUS: DONE"
        )
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        agent_steps.append(invocation.step)
        return _review(invocation.reviewer, "PLAN_APPROVAL: YES")

    real_handoff = orchestrator.write_implementation_handoff
    handoff_calls = 0

    def fail_once(**kwargs):
        nonlocal handoff_calls
        handoff_calls += 1
        if handoff_calls == 1:
            raise PlanHandoffError("simulated post-commit handoff failure")
        return real_handoff(**kwargs)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.setattr(orchestrator, "write_implementation_handoff", fail_once)
    monkeypatch.chdir(repository)

    with pytest.raises(
        WorkflowExecutionError,
        match="could not create IMPLEMENT handoff",
    ):
        run_production_workflow(task, _args(repository, task))

    steps_after_commit = tuple(agent_steps)
    resumed = run_production_workflow(task, _args(repository, task))

    assert resumed.workflow_completed
    assert tuple(agent_steps) == steps_after_commit
    assert handoff_calls == 2
    assert task.with_name("resume-implement.md").is_file()
