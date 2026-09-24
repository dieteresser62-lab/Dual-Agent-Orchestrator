from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import agent_runtime
import orchestrator
import pytest
import workflow_audit_projection
import workflow_production
import workflow_requests
from conftest import can_symlink
from agent_adapters import (
    NativeClaudeReviewAdapter,
    NativeCodexAdapter,
    NativeCodexExecutionBoundary,
)
from agent_config import AgentSettings
from agent_runtime import (
    AgentInvocationError,
    NativeAgentCodexOutput,
    NativeAgentReviewOutput,
    ProviderAttemptLifecycle,
    ProviderRequestRoundRequired,
    QuotaReset,
    QuotaWaitPolicy,
    TransientRetryPolicy,
    run_native_codex_agent_checked,
)
from audit_trail import ValidationAuditEvent
from artifact_models import (
    _IDENTIFIER_RE,
    AgentResultPayload,
    ArtifactRecord,
    BindingPayload,
    CommandSpec,
    CorrectionWorkUnitPayload,
    FinalReviewCompletedPayload,
    FindingTransitionPayload,
    FindingSeverity,
    Fingerprint,
    FingerprintKind,
    GateDecisionPayload,
    GatePayload,
    PlanPayload,
    ProviderContentPayload,
    ProviderAttemptPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    QuotaPausePayload,
    RecordType,
    ReviewAnchorPayload,
    ReviewEvidencePayload,
    ReviewPayload,
    ReviewValidationBindingPayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    SideEffectPayload,
    SliceBoundaryPayload,
    ScopeExtensionPathPayload,
    ScopeExtensionPayload,
    SliceSpec,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
    WorkflowCompletionPayload,
    WorkflowEventPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    canonical_json,
)
from artifact_store import ArtifactStore
from artifact_resume import ArtifactResumeError, resolve_resume_state
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
    FindingResponse,
    FindingResponseDecision,
    FindingStatus,
    PlannedSlice,
    ReadinessMarker,
    StepContract,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
    ReviewEvidence,
)
from error_classification import classify_exception
from finding_reducer import reduce_findings
from git_service import GitTransactionError
from gates import PathClasses
from inbox_watcher import (
    QueueFinalizationDisposition,
    WatchTaskDisposition,
    WatchTaskIdentity,
    WatchTaskResult,
    finalize_queue_success,
    save_watch_identity,
    success_marker_path,
    watch_identity_path,
)
from orchestrator import ProductionWorkflowDriver, run_pipeline, run_production_workflow
from orchestrator_diagnostics import OrchestratorDiagnostic
from review_packets import ReviewPacket, ReviewPacketManifest
from semantic_markdown import MANAGED_SECTION_HEADINGS, MANAGED_SECTION_KEYS
from workflow import (
    CodexInvocation,
    EvidenceKind,
    PLAN_CONTRACT_FAILURE_POLICIES,
    PlanContractFailureKind,
    PlanContractRepairAction,
    PlanContractRepairability,
    PlanContractSecurityClass,
    ReviewerInvocation,
    WorkflowChanges,
    WorkflowCommitRequest,
    WorkflowContext,
    WorkflowEngine,
    WorkflowHistory,
    WorkflowRunResult,
)
from workflow import WorkflowExecutionError
from plan_handoff import AcceptanceReviewLimitReached, PlanHandoffError
from state_io import StateSchemaError, save_workflow_state, write_workflow_checkpoint
from task_contract import TaskContractError, TaskMode, parse_task_contract
from workflow_state import (
    AgentProfileBinding,
    GateReason,
    InvocationFailureRecord,
    ProtocolBinding,
    ProtocolMode,
    Reviewer,
    SliceStatus,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
)
from workflow_state import AgentFailureKind
from provider_input_budget import (
    PreparedProviderInput,
    ProviderInputComponent,
    default_provider_input_budget_policy,
    measure_provider_input,
)
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
from artifact_replay import (
    ArtifactReplayError,
    ReplayDiagnosticCode,
    project_workflow_state,
    replay_artifacts,
    replay_findings,
)
from artifact_bridge import (
    ArtifactBridge,
    ArtifactBridgeError,
    attestation_payload,
    finding_payload,
    review_payload,
)
from content_authority import ValidationCapture, validation_output_digest
from content_authority_support import (
    append_provider_decision_authority,
    append_validation_authority,
)
from side_effects import SideEffectBoundaryPhase, SideEffectReconciliationError
from workflow_recovery import _require_provider_input_round


def _append_run_binding(bridge: ArtifactBridge, state: WorkflowState) -> None:
    fingerprint = state.task_digest or "a" * 64
    binding = state.protocol_binding or ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2")
    identity = bridge.append(
        RunIdentityPayload(
            state.task_file,
            state.branch,
            state.branch_base,
            state.slices[0].start_commit,
            state.execution_mode,
            state.audit_report_path,
        ),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256=fingerprint,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        WorkflowEventPayload("run", None, "1", None, (identity.record_id,)),
        logical_id=f"workflow-event-{identity.record_id}",
        idempotency_key=f"workflow-event:{identity.record_id}",
        fingerprint_sha256=identity.fingerprint.sha256,
        fingerprint_kind=identity.fingerprint.kind,
    )
    bridge.append(
        RunProfilePayload(
            RoleProfilePayload(
                binding.codex_profile.model, binding.codex_profile.effort
            ),
            RoleProfilePayload(
                binding.claude_profile.model, binding.claude_profile.effort
            ),
        ),
        logical_id="run-profile",
        idempotency_key="run-profile",
        fingerprint_sha256=fingerprint,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )


def _append_test_commit_authority(
    driver: ProductionWorkflowDriver,
    state: WorkflowState,
    *,
    commit_ref: str,
    findings: tuple[FindingRecord, ...] = (),
) -> WorkflowState:
    """Persist the review/attestation facts required by a synthetic commit.

    Runtime unit tests which jump across the real Git transaction must still
    construct the same record authority that production creates before a
    completed Slice can be projected.
    """
    review_state = state.with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    driver.bind_work_unit(review_state)
    bridge = driver._artifact_bridge
    assert bridge is not None
    fingerprint = review_state.current_slice.start_fingerprint or "e" * 64
    slice_id = review_state.current_slice_id
    work_unit_id = str(review_state.current_work_unit_id)
    attestation = append_validation_authority(
        bridge,
        ValidationAttestationPayload(
            (
                ValidationResult(
                    CommandSpec("pytest", ("python3", "-m", "pytest")),
                    "pass",
                    0,
                    "e" * 64,
                ),
            ),
            Role.ORCHESTRATOR,
            "e" * 64,
            "ar1-" + "0" * 64,
        ),
        logical_id=f"test-commit-validation-{slice_id}",
        idempotency_key=f"test-commit-validation:{slice_id}",
        fingerprint_sha256=fingerprint,
    )
    for finding in findings:
        bridge.append(
            finding_payload(finding, work_unit_id=work_unit_id),
            logical_id=f"finding-{finding.finding_id}",
            idempotency_key=f"test-finding-opened:{finding.finding_id}",
            fingerprint_sha256=fingerprint,
        )
    review = append_provider_decision_authority(
        bridge,
        ReviewPayload(
            Role.CLAUDE,
            work_unit_id,
            "approved",
            tuple(finding.finding_id for finding in findings),
            None,
            "native-claude-review-v2",
            "native-review-request-" + hashlib.sha256(
                f"{review_state.run_id}:{slice_id}".encode("utf-8")
            ).hexdigest(),
            "d" * 64,
            review_evidence=ReviewEvidencePayload(
                "synthetic commit authority",
                "the fixture skips the real Git transaction",
                "the commit binding loses its review or attestation",
            ),
            pre_mortem="The synthetic binding could target the wrong Slice.",
        ),
        logical_id=f"test-commit-review-{slice_id}",
        idempotency_key=f"test-commit-review:{slice_id}",
        fingerprint_sha256=fingerprint,
        operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
    )
    bridge.append(
        BindingPayload(
            "commit",
            commit_ref,
            attestation.record_id,
            (review.record_id,),
        ),
        logical_id=f"commit-{slice_id}-{commit_ref[:12]}",
        idempotency_key=f"test-commit:{slice_id}:{commit_ref}",
        fingerprint_sha256=fingerprint,
    )
    return review_state


def test_invoke_reviewer_dispatches_native_adapter_with_snapshot_boundary_and_provider_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path, "feature/native-review-dispatch")
    state = init_workflow_state(
        run_id="native-review-dispatch",
        task_file=str(repository / "task.md"),
        branch="feature/native-review-dispatch",
        branch_base=_git(repository, "rev-parse", "HEAD"),
        first_slice_start_commit=_git(repository, "rev-parse", "HEAD"),
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/native-review-dispatch",
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
            claude_review_transport="native-claude-review-v2",
        ),
    ).with_current_step(WorkflowStep.CLAUDE_PLAN_REVIEW)
    adapter = NativeClaudeReviewAdapter(
        AgentSettings("claude", "claude", "sonnet", 1800, "high")
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={"claude": adapter},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = state
    driver._artifact_bridge = SimpleNamespace()
    monkeypatch.setattr(driver, "assert_structured_decision_context", lambda: None)
    captured: dict[str, object] = {}
    expected = object()

    def dispatch(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(orchestrator, "run_native_review_agent_checked", dispatch)
    invocation = ReviewerInvocation(
        work_unit_id=state.current_work_unit_id,
        step=WorkflowStep.CLAUDE_PLAN_REVIEW,
        reviewer=AgentRole.CLAUDE,
        round_number=1,
        evidence_kind=EvidenceKind.FULL_SLICE,
        fingerprint="b" * 64,
        paths=(),
        prompt="review",
        native_request=object(),  # type: ignore[arg-type]
    )

    assert driver.invoke_reviewer(invocation) is expected
    assert captured["adapter"] is adapter
    assert captured["reviewer_manifest_paths"] is None
    assert captured["raw_response_path"] == driver._native_reviewer_response_path(
        invocation
    )
    assert captured["write_file"].__self__ is driver
    assert (
        captured["write_file"].__func__
        is ProductionWorkflowDriver._write_native_agent_raw_response
    )
    lifecycle = captured["provider_attempt_lifecycle"]
    assert lifecycle is not None
    assert callable(lifecycle.start)
    assert callable(lifecycle.terminal)

    assert state.work_plan_path is None
    captured.clear()
    direct_implement_slice = replace(
        invocation,
        step=WorkflowStep.CLAUDE_SLICE_REVIEW,
    )
    driver.active_state = state.with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)

    assert driver.invoke_reviewer(direct_implement_slice) is expected
    assert captured["reviewer_manifest_paths"] is None
    canonical_packet = json.dumps(
        {
            "schema": "review-packet-v1",
            "purpose": "slice",
            "fingerprint": "b" * 64,
            "manifest": {
                "paths": ["src/runtime.py"],
                "additional_dependencies": [],
            },
            "diff": "slice diff",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    packet = ReviewPacket(
        purpose="slice",
        fingerprint="b" * 64,
        manifest=ReviewPacketManifest(("src/runtime.py",)),
        canonical_bytes=canonical_packet,
        digest=hashlib.sha256(canonical_packet).hexdigest(),
    )
    monkeypatch.setattr(
        driver,
        "_materialize_review_packet",
        lambda materialized: repository / f"{materialized.digest}.json",
    )
    captured.clear()
    slice_invocation = replace(
        direct_implement_slice,
        review_packet=packet,
    )

    assert driver.invoke_reviewer(slice_invocation) is expected
    assert captured["reviewer_manifest_paths"] == ("src/runtime.py",)


def test_rejected_reviewer_response_uses_side_effect_ledger_and_exact_bytes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/native-review-response")
    task = repository / "task.md"
    task.write_text("review response persistence\n", encoding="utf-8")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="native-review-response",
        task_file=str(task),
        branch="feature/native-review-response",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest=hashlib.sha256(task.read_bytes()).hexdigest(),
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/native-review-response",
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
            claude_review_transport="native-claude-review-v2",
        ),
    ).with_current_step(WorkflowStep.CLAUDE_PLAN_REVIEW)
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.checkpoint(state, WorkflowHistory(state.current_work_unit_id))
    invocation = ReviewerInvocation(
        work_unit_id=state.current_work_unit_id,
        step=WorkflowStep.CLAUDE_PLAN_REVIEW,
        reviewer=AgentRole.CLAUDE,
        round_number=1,
        evidence_kind=EvidenceKind.FULL_SLICE,
        fingerprint=state.task_digest or "a" * 64,
        paths=(),
        prompt="review",
    )
    raw = '{"result_type":"review_result","decision":"invalid"}'
    base = driver._native_reviewer_response_path(invocation)
    target = driver._provider_attempt_response_path(base, 1)

    driver._write_native_agent_raw_response(target, raw)

    assert target.read_bytes() == raw.encode("utf-8")
    effects = tuple(
        record.payload
        for record in ArtifactStore(repository, state.run_id).load_chain()
        if isinstance(record.payload, SideEffectPayload)
        and record.payload.effect_class == "file_write"
        and record.payload.operation[0].endswith(target.name)
    )
    assert tuple(effect.phase for effect in effects) == ("intent", "result")
    assert effects[0].operation == effects[1].operation
from plan_handoff import render_implementation_task
from native_codex_contract import (
    NativeCodexContext,
    NativeCodexContractError,
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


def _native_plan_output(
    invocation: CodexInvocation,
    *,
    summary: str,
    scope_paths: tuple[str, ...],
    second_scope_paths: tuple[str, ...] | None = None,
) -> NativeAgentCodexOutput:
    bundle = invocation.native_request
    assert bundle is not None
    document = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": "plan_result",
        "request_id": bundle.bound_context.request_id,
        "ready": True,
        "finding_dispositions": [],
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": summary,
                "scope_paths": list(scope_paths),
                "acceptance_criteria": [{
                    "text": summary,
                    "measured_against": "SOURCE",
                }],
            }
        ],
    }
    if second_scope_paths is not None:
        document["slice_plan"].append({
            "slice_id": 2,
            "summary": "complete second slice",
            "scope_paths": list(second_scope_paths),
            "acceptance_criteria": [{
                "text": "complete second slice",
                "measured_against": "SOURCE",
            }],
        })
    canonical = canonical_native_codex_json(document)
    return NativeAgentCodexOutput(
        result=parse_bound_native_codex_contract_result(
            document, bundle.bound_context
        ),
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _native_review_approval(
    invocation: ReviewerInvocation,
) -> NativeAgentReviewOutput:
    bundle = invocation.native_request
    assert bundle is not None
    document = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": bundle.bound_context.request_id,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [],
        "status_changes": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "plan correctness and handoff contract",
            "largest_residual_risk": "post-commit handoff interruption",
            "break_condition": "the reviewed plan differs from the committed plan",
        },
        "pre_mortem": "A handoff failure could leave a committed plan awaiting resume.",
    }
    canonical = canonical_native_review_json(document)
    return NativeAgentReviewOutput(
        result=parse_bound_native_contract_result(document, bundle.bound_context),
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        context=bundle.bound_context.context,
    )


def _native_implementation_output(
    invocation: CodexInvocation,
) -> NativeAgentCodexOutput:
    bundle = invocation.native_request
    assert bundle is not None
    document = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": "implementation_result",
        "request_id": bundle.bound_context.request_id,
        "ready": True,
        "finding_dispositions": [
            {
                "finding_id": finding.finding_id,
                "decision": "accepted",
                "rationale": "The implementation resolves the offered finding.",
            }
            for finding in bundle.bound_context.context.previous_findings
            if finding.status is FindingStatus.OPEN
        ],
        "test_files": [],
    }
    canonical = canonical_native_codex_json(document)
    return NativeAgentCodexOutput(
        result=parse_bound_native_codex_contract_result(document, bundle.bound_context),
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _native_final_review_output(
    driver: ProductionWorkflowDriver,
    invocation: ReviewerInvocation,
    *,
    finding_id: str | None,
) -> NativeAgentReviewOutput:
    bundle = invocation.native_request
    assert bundle is not None
    finding = (
        []
        if finding_id is None
        else [
            {
                "finding_id": finding_id,
                "finding_class": "FINDING",
                "summary": "The completed branch still needs remediation.",
                "predecessor_finding_ref": None,
                "evidence_anchor_sha256": None,
                "acceptance_test": {
                    "kind": "prose",
                    "text": "A later ordinary plan must address the defect.",
                },
                "affected_paths": ["src/one.py"],
            }
        ]
    )
    document = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "final_review_completed",
        "request_id": bundle.bound_context.request_id,
        "reviewer": "claude",
        "scan_complete": True,
        "new_findings": finding,
        "occurrences": [],
        "review_evidence": {
            "dimensions": "correctness, contracts, failure paths, and resume",
            "largest_residual_risk": "The ordinary follow-up task could omit detail.",
            "break_condition": "The Inbox document is not self-contained.",
        },
        "pre_mortem": "The scan could complete without publishing its open cohort.",
    }
    canonical = canonical_native_review_json(document)
    return NativeAgentReviewOutput(
        result=parse_bound_native_contract_result(document, bundle.bound_context),
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        context=bundle.bound_context.context,
    )


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


def _final_review_test_state(state: WorkflowState) -> WorkflowState:
    """Advance a synthetic implementation state to its terminal full review."""

    scope_paths = (
        state.current_slice.scope_paths
        or state.task_scope_patterns
        or ("src/core.py",)
    )
    review = init_workflow_state(
        run_id=state.run_id,
        task_file=state.task_file,
        branch=state.branch,
        branch_base=state.branch_base,
        first_slice_start_commit=state.current_slice.start_commit or state.branch_base,
        slice_count=1,
        task_digest=state.task_digest,
        execution_mode="IMPLEMENT",
        task_scope_patterns=state.task_scope_patterns,
        audit_report_path=state.audit_report_path,
        target_branch=state.target_branch,
        protocol_binding=state.protocol_binding,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=state.current_slice.start_commit or state.branch_base,
        scope_paths=scope_paths,
        scope_change_groups=tuple((path,) for path in scope_paths),
        start_fingerprint=state.current_slice.start_fingerprint or "0" * 64,
    ).complete_current_slice(
        commit_ref=state.current_slice.commit_ref or "f" * 40,
    )
    return review.start_final_review_work_unit()


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


def _failed_codex_attempt_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    branch = "feature/attempt-bound-failure-log"
    repository = _repository(tmp_path, branch)
    task = repository / "task.md"
    _write_task(task, branch, "src/runtime.py")
    task_digest = hashlib.sha256(task.read_bytes()).hexdigest()
    state = init_workflow_state(
        run_id="attempt-bound-failure-log",
        task_file=str(task),
        branch=branch,
        branch_base=_git(repository, "rev-parse", "HEAD"),
        first_slice_start_commit=_git(repository, "rev-parse", "HEAD"),
        slice_count=1,
        task_digest=task_digest,
        task_scope_patterns=("src/runtime.py",),
        target_branch=branch,
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
        ),
    )

    class FakeAdapter:
        name = "codex"
        model = "test-model"
        effort = "high"
        metadata: dict[str, object] = {}

    adapter = FakeAdapter()
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={"codex": adapter},  # type: ignore[dict-item]
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)
    measurement = measure_provider_input(
        PreparedProviderInput(
            command=("codex", "exec"),
            stdin_text="provider request",
            components=(
                ProviderInputComponent("stdin_prompt", "provider request"),
            ),
        ),
        provider="codex",
        role="codex",
        operation=WorkflowStep.CODEX_PLAN.value,
        binding_fingerprint=task_digest,
        policy=default_provider_input_budget_policy(),
    )
    invocation = CodexInvocation(
        state.current_work_unit_id,
        WorkflowStep.CODEX_PLAN,
        1,
        "",
    )
    raw_response_path = driver._native_codex_response_path(invocation)
    lifecycle = ProviderAttemptLifecycle(
        start=lambda measured, bootstrap: driver._start_provider_attempt(
            measured,
            bootstrap,
            operation_instance="round:1",
            durable_response_path=raw_response_path,
        ),
        terminal=driver._finish_provider_attempt,
        durable_response_path=lambda handle: handle[2],
        failure_path=driver._provider_attempt_failure_path,
    )
    pending_failures: list[str] = []

    def fail_native_codex(*_args, **kwargs):  # type: ignore[no-untyped-def]
        bootstrap = kwargs["pre_start_callback"](measurement)
        kwargs["attempt_invocation"].begin(measurement, bootstrap)
        raise RuntimeError(pending_failures.pop(0))

    monkeypatch.setattr(agent_runtime, "run_native_codex_agent", fail_native_codex)

    def fail_attempt(message: str) -> None:
        pending_failures.append(message)
        with pytest.raises(AgentInvocationError, match=message):
            run_native_codex_agent_checked(
                adapter=adapter,  # type: ignore[arg-type]
                bundle=object(),  # type: ignore[arg-type]
                raw_response_path=raw_response_path,
                config=orchestrator.OrchestratorConfig(repo_root=repository),
                write_file=driver._write_native_codex_raw_response,
                shorten=lambda value, _maximum: value or "",
                operation=WorkflowStep.CODEX_PLAN.value,
                binding_fingerprint=task_digest,
                pre_start_callback=driver._persist_provider_bootstrap,
                provider_attempt_lifecycle=lifecycle,
            )

    return repository, driver, state, raw_response_path, fail_attempt


def test_codex_failure_logs_are_attempt_bound_and_leave_no_open_file_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, driver, state, raw_path, fail_attempt = (
        _failed_codex_attempt_harness(tmp_path, monkeypatch)
    )
    messages = (
        "expired provider token",
        "slice plan invalid",
        "third provider failure",
    )

    for message in messages:
        fail_attempt(message)
        assert driver._reconcile_pending_side_effects(
            driver.active_state or state
        ) is False

    expected_paths = tuple(
        driver._provider_attempt_response_path(raw_path, attempt).with_suffix(
            raw_path.suffix + ".failure.json"
        )
        for attempt in range(1, 4)
    )
    assert tuple(path.is_file() for path in expected_paths) == (True, True, True)
    assert tuple(
        json.loads(path.read_text(encoding="utf-8"))["provider_text"]
        for path in expected_paths
    ) == messages

    legacy_path = raw_path.with_suffix(raw_path.suffix + ".failure.json")
    legacy_content = '{"legacy":"unchanged"}'
    legacy_path.write_text(legacy_content, encoding="utf-8")
    fail_attempt("fourth provider failure")
    assert driver._reconcile_pending_side_effects(driver.active_state or state) is False
    assert legacy_path.read_text(encoding="utf-8") == legacy_content

    all_paths = (
        *expected_paths,
        driver._provider_attempt_response_path(raw_path, 4).with_suffix(
            raw_path.suffix + ".failure.json"
        ),
    )
    failure_targets = tuple(
        path.resolve().relative_to(repository).as_posix() for path in all_paths
    )
    file_effects = tuple(
        record.payload
        for record in ArtifactStore(repository, state.run_id).load_chain()
        if isinstance(record.payload, SideEffectPayload)
        and record.payload.effect_class == "file_write"
        and record.payload.operation[0].endswith(".failure.json")
    )
    assert tuple(effect.operation[0] for effect in file_effects[::2]) == failure_targets
    assert tuple(effect.phase for effect in file_effects) == (
        "intent",
        "result",
    ) * 4
    assert all(
        effect.operation == file_effects[index - 1].operation
        for index, effect in enumerate(file_effects)
        if effect.phase == "result"
    )


def test_removing_failure_log_attempt_suffix_recreates_the_open_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def without_attempt_suffix(response_path: Path) -> Path:
        base_stem, separator, _attempt = response_path.stem.rpartition(".attempt-")
        assert separator
        base = response_path.with_name(base_stem + response_path.suffix)
        return base.with_suffix(base.suffix + ".failure.json")

    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "_provider_attempt_failure_path",
        staticmethod(without_attempt_suffix),
    )
    _repository_path, driver, state, _raw_path, fail_attempt = (
        _failed_codex_attempt_harness(tmp_path, monkeypatch)
    )

    fail_attempt("first distinct failure")
    assert driver._reconcile_pending_side_effects(driver.active_state or state) is False
    fail_attempt("second distinct failure")
    with pytest.raises(
        SideEffectReconciliationError,
        match="has no durable identical target",
    ):
        driver._reconcile_pending_side_effects(driver.active_state or state)


def test_recomposed_request_opens_new_sequence_and_operation_but_binding_drift_stops(
    tmp_path: Path,
) -> None:
    branch = "feature/request-recomposition"
    repository = _repository(tmp_path, branch)
    task = repository / "task.md"
    _write_task(task, branch, "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = (
        init_workflow_state(
            run_id="request-recomposition",
            task_file=str(task),
            branch=branch,
            branch_base=head,
            first_slice_start_commit=head,
            slice_count=1,
            task_digest=hashlib.sha256(task.read_bytes()).hexdigest(),
            task_scope_patterns=("src/runtime.py",),
            target_branch=branch,
            protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
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
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={"codex": SimpleNamespace(model="test", effort="high")},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)
    binding = "c" * 64

    def measurement(text: str, fingerprint: str = binding):
        return measure_provider_input(
            PreparedProviderInput(
                command=("codex", "exec"),
                stdin_text=text,
                components=(ProviderInputComponent("stdin_prompt", text),),
            ),
            provider="codex",
            role="codex",
            operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
            binding_fingerprint=fingerprint,
            policy=default_provider_input_budget_policy(),
        )

    first_measurement = measurement("original request")
    first_bootstrap = driver._persist_provider_bootstrap(first_measurement)
    first = driver._start_provider_attempt(
        first_measurement,
        first_bootstrap,
        operation_instance="request:1",
        durable_response_path=repository / ".orchestrator" / "first.json",
    )
    driver._finish_provider_attempt(first, 1.0, "runtime", None)

    changed_binding_measurement = measurement("changed binding", "d" * 64)
    changed_binding_bootstrap = driver._persist_provider_bootstrap(
        changed_binding_measurement
    )
    with pytest.raises(
        WorkflowExecutionError,
        match=(
            "field=binding_fingerprint first=cccccccccccc "
            "current=dddddddddddd"
        ),
    ):
        driver._start_provider_attempt(
            changed_binding_measurement,
            changed_binding_bootstrap,
            operation_instance="request:1",
            durable_response_path=repository / ".orchestrator" / "foreign.json",
        )

    changed_request = measurement("recomposed request")
    changed_bootstrap = driver._persist_provider_bootstrap(changed_request)
    active = driver.active_state
    assert active is not None
    with pytest.raises(ProviderRequestRoundRequired):
        driver._start_provider_attempt(
            changed_request,
            changed_bootstrap,
            operation_instance="request:1",
            durable_response_path=repository / ".orchestrator" / "changed.json",
        )
    assert (
        resolve_resume_state(repository, state.run_id)
        .state.current_work_unit.round_number
        == 1
    )

    # This is the durable tail left by a crash immediately before the work-unit
    # request-sequence revision. The prerequisite policy record is itself the
    # durable request identity, so replay already exposes sequence 2 even if a
    # crash prevents the redundant work-unit projection revision.
    pending_request = active.start_recomposed_request()
    driver._persist_request_identity_prerequisites(pending_request)
    assert (
        resolve_resume_state(repository, state.run_id)
        .state.current_work_unit.request_sequence
        == 2
    )

    history = WorkflowHistory(active.current_work_unit_id)
    advanced, output = WorkflowEngine(driver)._invoke_role(
        active,
        history,
        WorkflowContext("assignment", "plan", "slice"),
        AgentRole.CODEX,
        lambda: driver._start_provider_attempt(
            changed_request,
            changed_bootstrap,
            operation_instance="request:1",
            durable_response_path=repository / ".orchestrator" / "changed.json",
        ),
    )

    assert output is None
    assert advanced.current_work_unit.round_number == 1
    assert advanced.current_work_unit.request_sequence == 2
    active = driver.active_state
    assert active is not None and active.current_work_unit.request_sequence == 2
    replayed = resolve_resume_state(repository, state.run_id).state
    assert replayed.current_work_unit.round_number == 1
    assert replayed.current_work_unit.request_sequence == 2
    chain = ArtifactStore(repository, state.run_id).load_chain()
    round_records = tuple(
        record
        for record in chain
        if isinstance(record.payload, WorkUnitPayload)
        and record.logical_id == f"work-unit-{state.current_work_unit_id}"
    )
    assert tuple(record.payload.round_number for record in round_records) == (1,)
    policy = next(
        record
        for record in chain
        if record.idempotency_key.endswith("recomposed-request:2:round:1")
    )
    identity_policy = next(
        record
        for record in chain
        if record.idempotency_key.endswith("round:1:request-sequence:2")
    )
    policy_index = chain.index(policy)
    transition, event = chain[policy_index - 2 : policy_index]
    assert isinstance(transition.payload, WorkflowTransitionPayload)
    assert transition.payload.step == WorkflowStep.CODEX_IMPLEMENTATION.value
    assert isinstance(event.payload, WorkflowEventPayload)
    assert event.payload.record_refs == (transition.record_id,)
    assert isinstance(policy.payload, WorkflowPolicyPayload)
    assert policy.idempotency_key.endswith("recomposed-request:2:round:1")
    assert isinstance(identity_policy.payload, WorkflowPolicyPayload)
    assert identity_policy.idempotency_key.endswith(
        "round:1:request-sequence:2"
    )
    assert sum(
        record.idempotency_key.endswith("recomposed-request:2:round:1")
        for record in chain
    ) == 1
    second = driver._start_provider_attempt(
        changed_request,
        changed_bootstrap,
        operation_instance="request:2",
        durable_response_path=repository / ".orchestrator" / "changed.json",
    )
    _require_provider_input_round((first[0], second[0]), changed_request)
    chain = ArtifactStore(repository, state.run_id).load_chain()
    attempts = tuple(
        record.payload
        for record in chain
        if isinstance(record.payload, ProviderAttemptPayload)
    )
    first_terminal = next(
        payload
        for payload in attempts
        if payload.logical_operation_id == first[0].payload.logical_operation_id
        and payload.phase == "failed"
    )
    assert first_terminal.failure_kind == "runtime"
    assert second[0].payload.logical_operation_id != first[0].payload.logical_operation_id
    assert second[0].payload.attempt_number == 1


def test_stored_colliding_review_response_is_superseded_by_a_recorded_new_round(
    tmp_path: Path,
) -> None:
    branch = "feature/recompose-colliding-review"
    repository = _repository(tmp_path, branch)
    task = repository / "task.md"
    _write_task(task, branch, "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = (
        init_workflow_state(
            run_id="recompose-colliding-review",
            task_file=str(task),
            branch=branch,
            branch_base=head,
            first_slice_start_commit=head,
            slice_count=1,
            task_digest=hashlib.sha256(task.read_bytes()).hexdigest(),
            task_scope_patterns=("src/runtime.py",),
            target_branch=branch,
            protocol_binding=ProtocolBinding(
                ProtocolMode.STRUCTURED_V2,
                "2",
                claude_review_transport="native-claude-review-v2",
            ),
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
        .with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={"claude": SimpleNamespace(model="sonnet", effort="high")},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)

    def measurement(request_text: str):
        return measure_provider_input(
            PreparedProviderInput(
                command=("claude", "--print"),
                stdin_text=request_text,
                components=(ProviderInputComponent("stdin_prompt", request_text),),
            ),
            provider="claude",
            role="claude",
            operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
            binding_fingerprint="c" * 64,
            policy=default_provider_input_budget_policy(),
        )

    stale = measurement('{"next_finding_id":"C-01"}')
    stale_bootstrap = driver._persist_provider_bootstrap(stale)
    response_path = (
        repository
        / ".orchestrator"
        / "artifacts"
        / state.run_id
        / "native-review-responses"
        / "work-unit-0002-claude_slice_review-request-0001.json"
    )
    started = driver._start_provider_attempt(
        stale,
        stale_bootstrap,
        operation_instance="request:1",
        durable_response_path=response_path,
    )
    stored_response_path = started[2]
    stored_response_path.parent.mkdir(parents=True, exist_ok=True)
    stored_response_path.write_text(
        '{"new_findings":[{"finding_id":"C-01"}]}',
        encoding="utf-8",
    )
    driver._finish_provider_attempt(started, 1.0, None, None)

    corrected = measurement('{"next_finding_id":"C-66"}')
    corrected_bootstrap = driver._persist_provider_bootstrap(corrected)
    advanced, output = WorkflowEngine(driver)._invoke_role(
        state,
        WorkflowHistory(state.current_work_unit_id),
        WorkflowContext("assignment", "plan", "slice"),
        AgentRole.CLAUDE,
        lambda: driver._start_provider_attempt(
            corrected,
            corrected_bootstrap,
            operation_instance="request:1",
            durable_response_path=response_path,
        ),
    )

    assert output is None
    assert advanced.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert advanced.current_work_unit.round_number == 1
    assert advanced.current_work_unit.request_sequence == 2
    assert stored_response_path.read_text(encoding="utf-8") == (
        '{"new_findings":[{"finding_id":"C-01"}]}'
    )
    chain = ArtifactStore(repository, state.run_id).load_chain()
    policies = tuple(
        record
        for record in chain
        if isinstance(record.payload, WorkflowPolicyPayload)
        and record.idempotency_key.endswith("recomposed-request:2:round:1")
    )
    assert len(policies) == 1
    work_units = tuple(
        record
        for record in chain
        if isinstance(record.payload, WorkUnitPayload)
        and record.logical_id == f"work-unit-{state.current_work_unit_id}"
    )
    assert work_units[-1].payload.round_number == 1


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


def test_new_watch_task_switches_to_existing_target_and_uses_merge_base_as_anchor(
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
    args.agent_settings["codex"] = replace(
        args.agent_settings["codex"], model="gpt-profile", effort="high"
    )
    args.agent_settings["claude"] = replace(
        args.agent_settings["claude"], model="opus", effort="medium"
    )
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
    assert state.branch_base == _git(
        repository, "merge-base", "master", "feature/inbox-target"
    )
    assert state.current_slice.start_commit == target_head
    assert state.protocol_binding.codex_profile == AgentProfileBinding(
        "gpt-profile", "high"
    )
    assert state.protocol_binding.claude_profile == AgentProfileBinding(
        "opus", "medium"
    )
    assert _git(repository, "branch", "--show-current") == "feature/inbox-target"


def test_run_base_follows_the_configured_base_branch_of_any_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "trunk")
    _git(repository, "config", "user.name", "Base Test")
    _git(repository, "config", "user.email", "base@example.invalid")
    (repository / ".gitignore").write_text(".orchestrator/\n", encoding="utf-8")
    (repository / "orchestrator.toml").write_text(
        '[repository]\nbase_branch = "trunk"\n', encoding="utf-8"
    )
    _git(repository, "add", ".gitignore", "orchestrator.toml")
    _git(repository, "commit", "-m", "seed")
    fork_point = _git(repository, "rev-parse", "HEAD")
    _git(repository, "branch", "release")
    _git(repository, "switch", "-c", "feature/any-base")
    _git(repository, "commit", "--allow-empty", "-m", "feature work")
    _git(repository, "switch", "trunk")
    _git(repository, "commit", "--allow-empty", "-m", "later trunk work")
    _git(repository, "switch", "feature/any-base")
    task = tmp_path / "any-base.md"
    _write_task(task, "feature/any-base", "src/new.py")
    captured: dict[str, object] = {}
    real_fresh_state = orchestrator._fresh_state

    class StateCaptured(RuntimeError):
        pass

    def capture_state(**kwargs):
        captured["state"] = real_fresh_state(**kwargs)
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, _args(repository, task), force_new=True)

    assert captured["state"].branch_base == fork_point


def test_new_watch_task_generates_and_persists_task_bound_target_branch(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/source")
    _git(repository, "switch", "master")
    task = tmp_path / "beitragsgrenzen.md"
    task.write_text(
        "# Beitragsgrenzen für Ärzte prüfen\n\n"
        "Die Berechnung soll nachvollziehbar werden.\n",
        encoding="utf-8",
    )
    args = _args(repository, task)
    args.watch_run_id = "watch-derived-target"
    captured: dict[str, WorkflowState] = {}
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
    reparsed = parse_task_contract(
        task.read_text(encoding="utf-8"), source_name=task.name
    )
    assert state.target_branch == reparsed.target_branch
    assert state.target_branch.startswith(
        "feature/beitragsgrenzen-fur-arzte-prufen-"
    )
    assert _git(repository, "branch", "--show-current") == state.target_branch
    assert _git(
        repository,
        "config",
        "--local",
        "--get",
        f"branch.{state.target_branch}.orchestratorTaskDigest",
    ) == reparsed.digest
    assert (
        workflow_production._validate_resumed_state(state, task.resolve(), reparsed)
        is state
    )


def test_resume_uses_persisted_profiles_and_rejects_explicit_drift_before_provider(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/profile-resume")
    task = tmp_path / "profile-resume.md"
    _write_task(task, "feature/profile-resume", "src/new.py")
    state = orchestrator._fresh_state(
        task_file=task,
        run_id="profile-resume",
        repository_root=repository,
        task_contract=parse_task_contract(task.read_text(encoding="utf-8")),
        codex_profile=AgentProfileBinding("gpt-persisted", "high"),
        claude_profile=AgentProfileBinding("opus-persisted", "medium"),
    )

    resumed = _args(repository, task)
    orchestrator._apply_resumed_agent_profiles(resumed, state)
    assert resumed.agent_settings["codex"].model == "gpt-persisted"
    assert resumed.agent_settings["codex"].effort == "high"
    assert resumed.agent_settings["claude"].model == "opus-persisted"
    assert resumed.agent_settings["claude"].effort == "medium"

    mismatched = parse_args(
        ["--task-file", str(task), "--codex-model", "terra"],
        cwd=repository,
        environ={},
    )
    with pytest.raises(StateSchemaError, match="AGENT-PROFILE-DIFF"):
        orchestrator._apply_resumed_agent_profiles(mismatched, state)


def test_run_records_exist_before_first_workflow_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path, "feature/run-binding-order")
    task = tmp_path / "run-binding-order.md"
    _write_task(task, "feature/run-binding-order", "src/new.py")
    args = _args(repository, task)
    args.agent_settings["codex"] = replace(
        args.agent_settings["codex"], model="gpt-order", effort="max"
    )
    args.agent_settings["claude"] = replace(
        args.agent_settings["claude"], model="opus-order", effort="max"
    )
    observed: dict[str, object] = {}

    class DispatchObserved(RuntimeError):
        pass

    def inspect_first_dispatch(self, state, context, history):
        chain = ArtifactStore(repository, state.run_id).load_chain()
        observed["run_id"] = state.run_id
        observed["types"] = tuple(record.record_type for record in chain)
        observed["identity"] = chain[0].payload
        observed["profile"] = chain[2].payload
        raise DispatchObserved

    monkeypatch.setattr(WorkflowEngine, "run_current_work_unit", inspect_first_dispatch)
    monkeypatch.chdir(repository)

    with pytest.raises(DispatchObserved):
        run_production_workflow(task, args, force_new=True)

    assert observed["types"][:10] == (
        RecordType.RUN_IDENTITY,
        RecordType.WORKFLOW_EVENT,
        RecordType.RUN_PROFILE,
        RecordType.SIDE_EFFECT,
        RecordType.SIDE_EFFECT,
        RecordType.WORKFLOW_TRANSITION,
        RecordType.WORKFLOW_EVENT,
        RecordType.WORKFLOW_POLICY,
        RecordType.GATE_TRANSITION,
        RecordType.TASK,
    )
    assert all(
        record_type is RecordType.SIDE_EFFECT
        for record_type in observed["types"][10:]
    )
    merge_base = _git(repository, "merge-base", "HEAD", "master")
    assert observed["identity"] == RunIdentityPayload(
        str(task.resolve()),
        "feature/run-binding-order",
        merge_base,
        _git(repository, "rev-parse", "HEAD"),
        "IMPLEMENT",
        None,
    )
    assert observed["profile"] == RunProfilePayload(
        RoleProfilePayload("gpt-order", "max"),
        RoleProfilePayload("opus-order", "max"),
        orchestrator.orchestrator_code_version(),
    )


def test_fresh_workflow_is_immutably_bound_to_complete_native_transport(
    tmp_path: Path,
) -> None:
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
        ProtocolMode.STRUCTURED_V2, "2"
    )
    assert state.protocol_binding.claude_review_transport == "native-claude-review-v2"
    assert state.protocol_binding.codex_result_transport == "native-codex-v2"


def test_final_review_structured_records_use_merge_base_fingerprint(
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
    state = _final_review_test_state(init_workflow_state(
        run_id="final-fingerprint",
        task_file=str(repository / "task.md"),
        branch="feature/final-fingerprint",
        branch_base=branch_base,
        first_slice_start_commit=slice_start,
        slice_count=1,
        task_scope_patterns=("current.txt",),
        target_branch="feature/final-fingerprint",
    ).bind_current_slice_git_boundary(
        start_commit=slice_start,
        scope_paths=("current.txt",),
        start_fingerprint="a" * 64,
    ))
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
        first_slice_start_commit="a" * 40,
        slice_count=1, timestamp="2026-08-21T10:00:00+00:00",
    )
    canonical = json.dumps(
        {
            "schema": "review-packet-v1",
            "purpose": "slice",
            "fingerprint": "a" * 64,
            "manifest": {"paths": ["seed.txt"], "additional_dependencies": []},
            "diff": "legacy compact delta",
        },
        separators=(",", ":"),
    ).encode("utf-8")
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

    first.write_text("collision", encoding="utf-8")
    with pytest.raises(WorkflowExecutionError, match="differs from canonical bytes"):
        driver._materialize_review_packet(packet)


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
    persisted_payloads = []

    class CheckpointDriver:
        def checkpoint(self, state, history) -> None:
            _ = (state, history)

        def persist_invocation_failure(self, payload) -> None:
            persisted_payloads.append(payload)

    now = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)
    state = init_workflow_state(
        run_id=f"quota-boundary-{reset_delay_seconds}-{safety_margin_seconds}",
        task_file="task.md",
        branch="feature/quota-boundary",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
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

    assert persisted_payloads

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
        safety_margin_seconds if reset_at is not None else 0
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
        first_slice_start_commit=start_commit,
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
        findings=(),
    )

    with pytest.raises(GitTransactionError, match="passing current attestation"):
        driver.commit_slice(request)

    assert _git(repository, "rev-parse", "HEAD") == start_commit
    assert _git(repository, "status", "--short") == "?? runtime.py"


def test_structured_red_state_commit_requires_exact_chain_records_before_git(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path, "feature/structured-red-state")
    start_commit = _git(repository, "rev-parse", "HEAD")
    changed_path = "runtime.py"
    (repository / changed_path).write_text("VALUE = 1\n", encoding="utf-8")
    state = init_workflow_state(
        run_id="structured-red-state",
        task_file=str(tmp_path / "task.md"),
        branch="feature/structured-red-state",
        branch_base=start_commit,
        first_slice_start_commit=start_commit,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=(changed_path,),
        target_branch="feature/structured-red-state",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
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
    driver.bind_work_unit(state)
    changes = driver.collect_changes(start_commit)
    bridge = driver._artifact_bridge
    assert bridge is not None
    monkeypatch.setattr(driver, "assert_structured_decision_context", lambda: None)
    failing = ValidationAttestation(
        "validation-red",
        changes.fingerprint,
        ("pytest",),
        (ValidationRecord(ValidationStatus.FAIL, "pytest", 1, "known red"),),
        "b" * 64,
        "red pending Slice 10",
    )
    review = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=True,
        stopped=False,
        stop_request=None,
        validation=failing,
        test_files=(),
        pre_mortem="The named correction may not restore the failing command.",
        evidence=ReviewEvidence(
            "record authority",
            "the red-state exception is stale",
            "the review record differs",
        ),
        findings=(),
        anchors=(),
        red_state_followup_slice="Slice 10",
    )
    request = WorkflowCommitRequest(
        slice_id=1,
        fingerprint=changes.fingerprint,
        attestation=failing,
        claude_review=review,
        findings=(),
        red_state_followup_slice="Slice 10",
    )
    stored_attestation = append_validation_authority(
        bridge,
        attestation_payload(failing, "ar1-" + "0" * 64),
        logical_id=failing.attestation_id,
        idempotency_key="attestation:red",
        fingerprint_sha256=changes.fingerprint,
    )

    with pytest.raises(
        WorkflowExecutionError,
        match="persisted attestation and approvals",
    ):
        driver.commit_slice(request)
    assert _git(repository, "rev-parse", "HEAD") == start_commit

    failing = replace(
        failing,
        records=(
            ValidationRecord(ValidationStatus.FAIL, "pytest", 1, "fail:1"),
        ),
        output_digest=stored_attestation.payload.output_digest,
    )
    review = replace(review, validation=failing)
    request = replace(request, attestation=failing, claude_review=review)

    append_provider_decision_authority(
        bridge,
        review_payload(
            review,
            work_unit_id=state.current_work_unit_id,
            transport_schema="native-claude-review-v2",
            request_id=f"native-review-request-{'c' * 64}",
            response_sha256="d" * 64,
        ),
        logical_id="review-claude-1-1",
        idempotency_key="review:red",
        fingerprint_sha256=changes.fingerprint,
        operation="claude_slice_review",
    )
    passing = replace(
        failing,
        records=(
            ValidationRecord(ValidationStatus.PASS, "pytest", 0, "passed"),
        ),
        summary="passed",
    )
    with pytest.raises(
        WorkflowExecutionError,
        match="attestation differs from the commit request",
    ):
        driver.commit_slice(
            replace(
                request,
                attestation=passing,
                claude_review=replace(
                    review,
                    validation=passing,
                    red_state_followup_slice=None,
                ),
                red_state_followup_slice=None,
            )
        )
    assert _git(repository, "rev-parse", "HEAD") == start_commit

    commit_hash = driver.commit_slice(request)

    assert commit_hash == _git(repository, "rev-parse", "HEAD")


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
        first_slice_start_commit=_git(repository, "rev-parse", "HEAD"),
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


def test_real_codex_canonical_request_embeds_only_configured_agents_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path, "feature/transport-boundary")
    task = tmp_path / "task.md"
    _write_task(task, "feature/transport-boundary", "docs/internal/work-plan.md")
    agents_prefix = "AGENTS-S6-TRANSPORT-SENTINEL\n"
    agents_content = (
        agents_prefix
        + "A" * (12_000 - len(agents_prefix))
        + "AGENTS-S6-OUTSIDE-TRANSPORT-LIMIT\n"
    )
    (repository / "AGENTS.md").write_text(agents_content, encoding="utf-8")
    (repository / "CLAUDE.md").write_text(
        "CLAUDE-S6-TRANSPORT-SENTINEL\n", encoding="utf-8"
    )
    (repository / "CODEX.md").write_text(
        "CODEX-S6-TRANSPORT-SENTINEL\n", encoding="utf-8"
    )
    state = init_workflow_state(
        run_id="transport-boundary",
        task_file=str(task),
        branch="feature/transport-boundary",
        branch_base=_git(repository, "rev-parse", "HEAD"),
        first_slice_start_commit=_git(repository, "rev-parse", "HEAD"),
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("docs/internal/work-plan.md",),
        target_branch="feature/transport-boundary",
        execution_mode="PLAN_ONLY",
        work_plan_path="docs/internal/work-plan.md",
    )
    args = _args(repository, task)
    args.agents_file = str(repository / "AGENTS.md")
    monkeypatch.chdir(repository)
    context = orchestrator._context(
        args=args,
        assignment="TASK-S6-TRANSPORT-SENTINEL",
        state=state,
    )
    contract = CodexStepContract(
        name="transport-boundary-plan",
        readiness_marker=ReadinessMarker.PLAN,
        slice_id="01",
        round_number=1,
        require_slice_plan=True,
        plan_artifact_path="docs/internal/work-plan.md",
    )
    bundle = workflow_requests.native_codex_request(
        execution_error=WorkflowExecutionError,
        state=state,
        context=context,
        history=WorkflowHistory(state.current_work_unit_id),
        contract=contract,
        request_kind=NativeCodexRequestKind.PLAN,
    )
    execution = tmp_path / "execution"
    assets = tmp_path / "assets"
    execution.mkdir()
    assets.mkdir()
    adapter = NativeCodexAdapter(args.agent_settings["codex"])
    prepared = adapter.prepare_native_provider_input(
        bundle,
        NativeCodexExecutionBoundary.canary(
            repository, execution_root=execution, evidence_asset_root=assets
        ),
    )

    assert prepared.stdin_text is not None
    canonical_request = json.loads(prepared.stdin_text)
    assignment = canonical_request["assignment"]
    assert assignment == context.assignment
    assert "TASK-S6-TRANSPORT-SENTINEL" in assignment
    assert "AGENTS-S6-TRANSPORT-SENTINEL" in assignment
    assert agents_content[:12_000] in assignment
    assert "AGENTS-S6-OUTSIDE-TRANSPORT-LIMIT" not in assignment
    assert "CLAUDE-S6-TRANSPORT-SENTINEL" not in assignment
    assert "CODEX-S6-TRANSPORT-SENTINEL" not in assignment
    assert prepared.stdin_text == bundle.canonical_json
    adapter.cleanup()


def test_final_review_reuses_carried_attestation_in_its_audit_history() -> None:
    attestation = ValidationAttestation(
        "validation-a",
        "a" * 64,
        ("pytest",),
        (ValidationRecord(ValidationStatus.PASS, "pytest", 0, "ok"),),
        "b" * 64,
        "passed",
    )
    final_state = _final_review_test_state(init_workflow_state(
        run_id="final-attestation-recovery",
        task_file="task.md",
        branch="feature/final-attestation",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        timestamp="2026-08-21T12:00:00+00:00",
    ))
    final_history = WorkflowHistory(
        final_state.current_work_unit_id,
        attestations=(attestation,),
    )

    recovered = orchestrator._recover_final_review_attestation(
        final_state,
        final_history,
    )

    assert recovered.attestations == final_history.attestations
    assert len(recovered.events) == 1
    assert recovered.events[0].attestation == attestation
    assert len(recovered.events) == 1
    assert isinstance(recovered.events[0], ValidationAuditEvent)
    assert recovered.events[0].attestation == attestation


def test_history_payload_does_not_archive_record_reference_projection() -> None:
    record_projection = {
        "1": {
            "workflow_event_record_refs": ("ar1-" + "a" * 64,),
            "validation_attestation_record_refs": (),
        }
    }

    payload = orchestrator._history_payload(record_projection, WorkflowHistory(2))

    assert payload == {"current": WorkflowHistory(2).to_dict(), "archive": []}


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
        first_slice_start_commit=_git(repository, "rev-parse", "HEAD"),
        slice_count=1,
    )
    old_history = WorkflowHistory(1).to_dict()
    latest_history = WorkflowHistory(
        1,
        last_claude_fingerprint="f" * 64,
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
        first_slice_start_commit=head,
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

    driver.active_state = _final_review_test_state(state)
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
        first_slice_start_commit=head,
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
    driver.active_state = _final_review_test_state(state)

    changes = driver.collect_changes(head)

    assert len(changes.full_diff) > 650_000
    assert "FIRST_SENTINEL" in changes.full_diff
    assert "MIDDLE_SENTINEL" in changes.full_diff
    assert "LAST_SENTINEL" in changes.full_diff
    assert "middle of final-review diff section" not in changes.full_diff
    assert "DETERMINISTIC AUDIT PROJECTION (COMPACT EVIDENCE)" in changes.full_diff


def _managed_final_review_audit(projected: str) -> str:
    lines = ["# Final review", "", "stable authored evidence", "", "## Orchestrator-Prüfprotokoll", ""]
    for key in MANAGED_SECTION_KEYS:
        lines.extend(
            (
                f"### {MANAGED_SECTION_HEADINGS[key]}",
                "",
                f"<!-- audit:{key}:begin -->",
                projected,
                f"<!-- audit:{key}:end -->",
                "",
            )
        )
    return "\n".join(lines)


def _final_review_evidence_driver(
    repository: Path,
    tmp_path: Path,
    *,
    run_id: str,
) -> tuple[ProductionWorkflowDriver, WorkflowState, str, str]:
    head = _git(repository, "rev-parse", "HEAD")
    audit_path = "docs/internal/final-review-cache-review-12345678.md"
    source_path = "src/runtime.py"
    (repository / "docs/internal").mkdir(parents=True, exist_ok=True)
    (repository / "src").mkdir(exist_ok=True)
    (repository / audit_path).write_text(
        _managed_final_review_audit("first projected value"), encoding="utf-8"
    )
    (repository / source_path).write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", audit_path, source_path)
    state = _final_review_test_state(init_workflow_state(
        run_id=run_id,
        task_file=str(tmp_path / "task.md"),
        branch="feature/final-review-cache",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_scope_patterns=(audit_path, source_path),
        audit_report_path=audit_path,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=(audit_path, source_path),
        start_fingerprint="a" * 64,
    ))
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = state
    driver._artifact_bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    return driver, state, head, audit_path


def test_final_review_evidence_is_collected_once_across_consumers_and_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _repository(tmp_path, "feature/final-review-cache")
    driver, state, head, _audit_path = _final_review_evidence_driver(
        repository, tmp_path, run_id="final-review-cache"
    )
    real_collect = orchestrator.collect_repository_changes
    collections: list[str] = []

    def measured_collect(*args, **kwargs):  # type: ignore[no-untyped-def]
        collections.append(str(args[1]))
        return real_collect(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "collect_repository_changes", measured_collect)
    caplog.set_level("DEBUG")

    final_report = driver.collect_changes(head)
    attestation_fingerprint = driver._artifact_fingerprint()
    provider_request = driver.collect_changes(head)
    preflight = driver.collect_changes(head)

    resumed = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    resumed.active_state = state
    resumed._artifact_bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    resumed_evidence = resumed.collect_changes(head)

    assert collections == [head]
    assert attestation_fingerprint == final_report.fingerprint
    assert (
        final_report.full_diff
        == provider_request.full_diff
        == preflight.full_diff
        == resumed_evidence.full_diff
    )
    assert final_report.paths == provider_request.paths == preflight.paths == resumed_evidence.paths
    assert sum(
        record.message.startswith("Final review evidence compacted")
        for record in caplog.records
    ) == 1
    assert any(
        record.message.startswith("Final review evidence reused")
        for record in caplog.records
    )


def test_final_review_cache_bytes_exclude_collection_runtime(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "feature/final-review-cache")
    driver, _state, head, _audit_path = _final_review_evidence_driver(
        repository, tmp_path, run_id="final-review-cache-deterministic"
    )

    driver.collect_changes(head)
    snapshot = driver._final_review_evidence_snapshot

    assert snapshot is not None
    slower_snapshot = replace(
        snapshot, collection_elapsed_ms=snapshot.collection_elapsed_ms + 60_000
    )
    first = orchestrator.render_final_review_evidence_cache(snapshot)
    second = orchestrator.render_final_review_evidence_cache(slower_snapshot)
    assert first == second
    assert "collection_elapsed_ms" not in json.loads(first)["snapshot"]


def test_final_review_semantically_empty_diff_retains_metadata_fallback(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/final-review-cache")
    semantic_path = "docs/internal/final-review-projection.md"
    (repository / "docs/internal").mkdir(parents=True, exist_ok=True)
    (repository / semantic_path).write_text(
        _managed_final_review_audit("first projected value"), encoding="utf-8"
    )
    _git(repository, "add", semantic_path)
    _git(repository, "commit", "-m", "add managed projection")
    head = _git(repository, "rev-parse", "HEAD")
    (repository / semantic_path).write_text(
        _managed_final_review_audit("second projected value"), encoding="utf-8"
    )
    state = _final_review_test_state(init_workflow_state(
        run_id="final-review-empty-diff",
        task_file=str(tmp_path / "task.md"),
        branch="feature/final-review-cache",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_scope_patterns=(semantic_path,),
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=(semantic_path,),
        start_fingerprint="a" * 64,
    ))
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = state
    driver._artifact_bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))

    changes = driver.collect_changes(head)

    assert changes.paths == (semantic_path,)
    assert changes.full_diff == "(binary or metadata-only repository change)"


def test_final_review_cache_intent_resumes_with_deterministic_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path, "feature/final-review-cache")
    driver, state, head, _audit_path = _final_review_evidence_driver(
        repository, tmp_path, run_id="final-review-cache-intent-resume"
    )
    real_collect = orchestrator.collect_repository_changes
    collections = 0
    monotonic_values = iter((0.0, 1.0, 10.0, 12.5))

    def measured_collect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal collections
        collections += 1
        return real_collect(*args, **kwargs)

    class InjectedCacheCrash(RuntimeError):
        pass

    def crash_after_cache_intent(boundary):  # type: ignore[no-untyped-def]
        if (
            boundary.effect_class == "file_write"
            and boundary.phase is SideEffectBoundaryPhase.AFTER_INTENT
        ):
            raise InjectedCacheCrash

    monkeypatch.setattr(orchestrator, "collect_repository_changes", measured_collect)
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: next(monotonic_values))
    driver._side_effect_boundary_observer = crash_after_cache_intent

    with pytest.raises(InjectedCacheCrash):
        driver.collect_changes(head)
    interrupted_snapshot = driver._final_review_evidence_snapshot
    assert interrupted_snapshot is not None
    assert interrupted_snapshot.collection_elapsed_ms == 1_000

    resumed = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    resumed.active_state = state
    resumed._artifact_bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    resumed.collect_changes(head)
    resumed_snapshot = resumed._final_review_evidence_snapshot

    assert collections == 2
    assert resumed_snapshot is not None
    assert resumed_snapshot.collection_elapsed_ms == 2_500
    assert orchestrator.render_final_review_evidence_cache(
        interrupted_snapshot
    ) == orchestrator.render_final_review_evidence_cache(resumed_snapshot)
    cache_effects = tuple(
        record.payload
        for record in resumed._artifact_bridge.store.load_chain()
        if isinstance(record.payload, SideEffectPayload)
        and record.payload.effect_class == "file_write"
        and "cache/final-review-evidence" in record.payload.operation[0]
    )
    assert tuple(payload.phase for payload in cache_effects) == ("intent", "result")
    assert cache_effects[0].operation == cache_effects[1].operation


@pytest.mark.parametrize("mutation", ("visible", "untracked", "index", "head"))
def test_final_review_evidence_cache_invalidates_every_repository_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    repository = _repository(tmp_path, "feature/final-review-cache")
    driver, _state, head, _audit_path = _final_review_evidence_driver(
        repository, tmp_path, run_id=f"final-review-cache-{mutation}"
    )
    real_collect = orchestrator.collect_repository_changes
    collections = 0

    def measured_collect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal collections
        collections += 1
        return real_collect(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "collect_repository_changes", measured_collect)
    first = driver.collect_changes(head)

    if mutation == "visible":
        (repository / "src/runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    elif mutation == "untracked":
        (repository / "outside.py").write_text("UNAUTHORIZED = True\n", encoding="utf-8")
    elif mutation == "index":
        (repository / "seed.txt").write_text("staged-only\n", encoding="utf-8")
        _git(repository, "add", "seed.txt")
        (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
    else:
        (repository / "head.txt").write_text("new head\n", encoding="utf-8")
        _git(repository, "add", "head.txt")
        _git(repository, "commit", "-m", "move head")

    second = driver.collect_changes(head)

    assert collections == 2
    if mutation == "index":
        assert second.fingerprint == first.fingerprint
        assert second.full_diff == first.full_diff
    else:
        assert second.fingerprint != first.fingerprint
    if mutation == "untracked":
        assert "outside.py" in second.paths


def test_final_review_projection_only_update_reuses_semantic_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path, "feature/final-review-cache")
    driver, _state, head, audit_path = _final_review_evidence_driver(
        repository, tmp_path, run_id="final-review-cache-projection"
    )
    real_collect = orchestrator.collect_repository_changes
    collections = 0

    def measured_collect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal collections
        collections += 1
        return real_collect(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "collect_repository_changes", measured_collect)
    first = driver.collect_changes(head)
    (repository / audit_path).write_text(
        _managed_final_review_audit("second, much longer projected value"),
        encoding="utf-8",
    )
    second = driver.collect_changes(head)

    assert collections == 1
    assert second.fingerprint == first.fingerprint
    assert second.full_diff == first.full_diff
    assert "second, much longer projected value" not in second.full_diff


@pytest.mark.parametrize("boundary", ("audit_path", "excluded_control_path"))
def test_final_review_evidence_cache_invalidates_controlling_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    repository = _repository(tmp_path, "feature/final-review-cache")
    if boundary == "excluded_control_path":
        ignore = repository / ".gitignore"
        ignore.write_text(
            ignore.read_text(encoding="utf-8") + ".task-*.md\n", encoding="utf-8"
        )
        _git(repository, "add", ".gitignore")
        _git(repository, "commit", "-m", "ignore task controls")
    driver, state, head, _audit_path = _final_review_evidence_driver(
        repository, tmp_path, run_id=f"final-review-cache-{boundary}"
    )
    if boundary == "excluded_control_path":
        first_task = repository / ".task-a.md"
        second_task = repository / ".task-b.md"
        first_task.write_text("bound task\n", encoding="utf-8")
        second_task.write_text("bound task\n", encoding="utf-8")
        task_digest = hashlib.sha256("bound task\n".encode("utf-8")).hexdigest()
        state = replace(
            state,
            task_file=str(first_task),
            task_digest=task_digest,
            target_branch=state.branch,
        )
        driver.active_state = state
    real_collect = orchestrator.collect_repository_changes
    collections = 0

    def measured_collect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal collections
        collections += 1
        return real_collect(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "collect_repository_changes", measured_collect)
    first = driver.collect_changes(head)
    if boundary == "audit_path":
        driver.active_state = replace(
            state,
            audit_report_path="docs/internal/rebound-final-review.md",
        )
    else:
        driver.active_state = replace(state, task_file=str(second_task))
    second = driver.collect_changes(head)

    assert collections == 2
    assert second.fingerprint == first.fingerprint


@pytest.mark.parametrize("cache_mutation", ("deleted", "tampered"))
def test_final_review_cache_loss_or_tampering_recollects_without_touching_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_mutation: str,
) -> None:
    repository = _repository(tmp_path, "feature/final-review-cache")
    driver, state, head, _audit_path = _final_review_evidence_driver(
        repository, tmp_path, run_id=f"final-review-cache-{cache_mutation}"
    )
    real_collect = orchestrator.collect_repository_changes
    collections = 0

    def measured_collect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal collections
        collections += 1
        return real_collect(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "collect_repository_changes", measured_collect)
    first = driver.collect_changes(head)
    source_before = (repository / "src/runtime.py").read_bytes()
    cache_path = next(
        (repository / ".orchestrator" / "cache" / "final-review-evidence").rglob(
            "*.json"
        )
    )
    if cache_mutation == "deleted":
        cache_path.unlink()
    else:
        envelope = json.loads(cache_path.read_text(encoding="utf-8"))
        envelope["snapshot"]["start_commit"] = "c" * 40
        snapshot_bytes = json.dumps(
            envelope["snapshot"],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        envelope["snapshot_sha256"] = hashlib.sha256(snapshot_bytes).hexdigest()
        cache_path.write_text(
            json.dumps(
                envelope,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )

    resumed = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    resumed.active_state = state
    resumed._artifact_bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    second = resumed.collect_changes(head)

    assert collections == 2
    assert second == first
    assert (repository / "src/runtime.py").read_bytes() == source_before


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
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/structured-bind",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
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
        RecordType.RUN_IDENTITY,
        RecordType.WORKFLOW_EVENT,
        RecordType.RUN_PROFILE,
        RecordType.SIDE_EFFECT,
        RecordType.SIDE_EFFECT,
        RecordType.WORKFLOW_TRANSITION,
        RecordType.WORKFLOW_EVENT,
        RecordType.WORKFLOW_TRANSITION,
        RecordType.WORKFLOW_EVENT,
        RecordType.WORKFLOW_POLICY,
        RecordType.WORKFLOW_POLICY,
        RecordType.SLICE_BOUNDARY,
        RecordType.GATE_TRANSITION,
        RecordType.GATE_TRANSITION,
        RecordType.TASK,
        RecordType.WORK_UNIT,
    )
    projection = project_workflow_state(replay_artifacts(chain, state.run_id))
    assert driver.active_state == projection.state


def test_r2_transition_records_precede_dispatch_guard_across_round_gate_resume_and_slice(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/r2-transition-order")
    task = repository / "task.md"
    _write_task(task, "feature/r2-transition-order", "src/one.py", "src/two.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="r2-transition-order",
        task_file=str(task),
        branch="feature/r2-transition-order",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=2,
        task_digest="a" * 64,
        task_scope_patterns=("src/one.py", "src/two.py"),
        work_plan_path="docs/internal/approved-plan.md",
        approved_plan_commit=head,
        target_branch="feature/r2-transition-order",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).bind_slice_plan(
        (
            PlannedSlice(1, "first", ("src/one.py",)),
            PlannedSlice(2, "second", ("src/two.py",)),
        ),
        first_start_commit=head,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/one.py",),
        start_fingerprint="b" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )

    def transition_records() -> tuple:
        return tuple(
            record
            for record in ArtifactStore(repository, state.run_id).load_chain()
            if isinstance(record.payload, WorkflowTransitionPayload)
        )

    def bind_before_dispatch(candidate: WorkflowState) -> int:
        driver.bind_work_unit(candidate)
        driver.assert_structured_decision_context()
        replay = replay_artifacts(
            ArtifactStore(repository, state.run_id).load_chain(), state.run_id
        )
        assert driver.active_state is not None
        assert driver.active_state == project_workflow_state(replay).state
        assert replay.workflow_cursor is not None
        assert replay.workflow_cursor.slice_id == str(candidate.current_slice_id)
        assert replay.workflow_cursor.work_unit_id == str(candidate.current_work_unit_id)
        assert replay.workflow_cursor.step == candidate.current_step.value
        return len(transition_records())

    initial_count = bind_before_dispatch(state)
    assert bind_before_dispatch(state) == initial_count

    review_round = state.with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    assert bind_before_dispatch(review_round) == initial_count + 1

    gated = review_round.await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail="R2 transition ordering fixture",
    )
    assert bind_before_dispatch(gated) == initial_count + 2

    resumed = gated.resume_after_user_decision()
    assert bind_before_dispatch(resumed) == initial_count + 3

    slice_two = resumed.complete_current_work_unit().start_work_unit(
        slice_id=2,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit=head,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/two.py",),
        start_fingerprint="c" * 64,
    )
    assert bind_before_dispatch(slice_two) == initial_count + 5
    assert tuple(record.revision for record in transition_records()) == tuple(
        range(1, initial_count + 6)
    )


def test_r9_resume_reconciles_one_durable_transition_without_its_event(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/r9-event-recovery")
    task = repository / "task.md"
    _write_task(task, "feature/r9-event-recovery", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="r9-event-recovery",
        task_file=str(task),
        branch="feature/r9-event-recovery",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/r9-event-recovery",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    )
    first = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    first.bind_work_unit(state)
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    transition = bridge.append(
        WorkflowTransitionPayload(
            "1", "in_progress", "1", "claude_plan_review", "in_progress"
        ),
        logical_id="workflow-transition",
        idempotency_key="workflow-transition:crash-tail",
        fingerprint_sha256=state.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    before = replay_artifacts(bridge.store.load_chain(), state.run_id)
    assert before.pending_workflow_event_record_id == transition.record_id

    resumed = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    resumed.bind_work_unit(state.with_current_step(WorkflowStep.CLAUDE_PLAN_REVIEW))

    replay = replay_artifacts(bridge.store.load_chain(), state.run_id)
    assert replay.pending_workflow_event_record_id is None
    matching = tuple(
        event for event in replay.workflow_events
        if event.record_refs == (transition.record_id,)
    )
    assert len(matching) == 1
    assert (
        matching[0].event_kind,
        matching[0].work_unit_id,
        matching[0].slice_id,
        matching[0].round_number,
        matching[0].record_refs,
    ) == ("transition", "1", "1", None, (transition.record_id,))
    assert resumed.active_state is not None
    assert resumed.active_state == project_workflow_state(replay).state


def test_r2_policy_records_denial_count_against_the_fixed_configured_limit(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/r2-policy")
    task = repository / "task.md"
    _write_task(task, "feature/r2-policy", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="r2-policy",
        task_file=str(task),
        branch="feature/r2-policy",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/r2-policy",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
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
    work_unit_id = str(state.current_work_unit_id)

    def policies() -> tuple[WorkflowPolicyPayload, ...]:
        return tuple(
            record.payload
            for record in ArtifactStore(repository, state.run_id).load_chain()
            if isinstance(record.payload, WorkflowPolicyPayload)
            and record.payload.work_unit_id == work_unit_id
        )

    denied = state
    for return_count in range(1, 4):
        unit = replace(denied.current_work_unit, codex_return_count=return_count)
        denied = replace(
            denied,
            work_units=(*denied.work_units[:-1], unit),
        )
        driver.bind_work_unit(denied)

    assert policies()[-1] == WorkflowPolicyPayload(work_unit_id, 3, 6)
    before_rebind = len(policies())
    driver.bind_work_unit(denied)
    assert len(policies()) == before_rebind


def test_r3_slice_boundary_precedes_reader_and_keeps_measured_start_after_tree_change(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/r3-boundary-order")
    task = repository / "task.md"
    _write_task(task, "feature/r3-boundary-order", "src/one.py", "src/old.py")
    head = _git(repository, "rev-parse", "HEAD")
    measured = orchestrator.collect_repository_changes(
        repository, head, excluded_paths=("task.md",)
    )
    state = init_workflow_state(
        run_id="r3-boundary-order",
        task_file=str(task),
        branch="feature/r3-boundary-order",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/one.py", "src/old.py"),
        target_branch="feature/r3-boundary-order",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/one.py", "src/old.py"),
        scope_change_groups=(("src/one.py", "src/old.py"),),
        start_fingerprint=measured.fingerprint,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )

    driver.bind_work_unit(state)
    driver.assert_structured_decision_context()
    chain = ArtifactStore(repository, state.run_id).load_chain()
    boundary_index = next(
        index for index, record in enumerate(chain)
        if isinstance(record.payload, SliceBoundaryPayload)
    )
    work_unit_index = next(
        index for index, record in enumerate(chain)
        if isinstance(record.payload, WorkUnitPayload)
    )
    assert boundary_index < work_unit_index
    assert sum(
        isinstance(record.payload, SliceBoundaryPayload) for record in chain
    ) == 1

    source = repository / "src" / "one.py"
    source.parent.mkdir(parents=True)
    source.write_text("changed after boundary bind\n", encoding="utf-8")
    changed = orchestrator.collect_repository_changes(
        repository, head, excluded_paths=("task.md",)
    )
    assert changed.fingerprint != measured.fingerprint
    replay = replay_artifacts(
        ArtifactStore(repository, state.run_id).load_chain(), state.run_id
    )
    assert replay.slice_boundaries == (
        SliceBoundaryPayload(
            "1",
            head,
            state.current_slice.scope_change_groups,
            measured.fingerprint,
        ),
    )
    driver.assert_structured_decision_context()


def test_r3_each_slice_start_writes_one_boundary_and_scope_extension_is_revisioned(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/r3-boundary-count")
    task = repository / "task.md"
    _write_task(task, "feature/r3-boundary-count", "src/shared.py", "tests/shared.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="r3-boundary-count",
        task_file=str(task),
        branch="feature/r3-boundary-count",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=2,
        task_digest="a" * 64,
        task_scope_patterns=("src/shared.py", "tests/shared.py"),
        target_branch="feature/r3-boundary-count",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/shared.py",),
        start_fingerprint="b" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    reviewed = _append_test_commit_authority(
        driver, state, commit_ref="d" * 40
    )
    second = reviewed.complete_current_slice(commit_ref="d" * 40).start_work_unit(
        slice_id=2,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit="d" * 40,
    ).bind_current_slice_git_boundary(
        start_commit="d" * 40,
        scope_paths=("src/shared.py", "tests/shared.py"),
        scope_change_groups=(("src/shared.py", "tests/shared.py"),),
        start_fingerprint="c" * 64,
    )
    driver.bind_work_unit(second)
    boundaries = tuple(
        record for record in ArtifactStore(repository, state.run_id).load_chain()
        if isinstance(record.payload, SliceBoundaryPayload)
    )
    assert tuple(record.payload.slice_id for record in boundaries) == ("1", "2")
    assert tuple(record.revision for record in boundaries) == (1, 1)

    expanded = second.extend_current_slice_scope(("docs/extra.md",))
    driver.bind_work_unit(expanded)
    replay = replay_artifacts(
        ArtifactStore(repository, state.run_id).load_chain(), state.run_id
    )
    assert replay.slice_boundaries[-1].start_commit == "d" * 40
    assert replay.slice_boundaries[-1].start_fingerprint == "c" * 64
    assert replay.slice_boundaries[-1].scope_change_groups == (
        ("docs/extra.md",),
        ("src/shared.py", "tests/shared.py"),
    )
    boundary_two = tuple(
        record for record in ArtifactStore(repository, state.run_id).load_chain()
        if isinstance(record.payload, SliceBoundaryPayload)
        and record.payload.slice_id == "2"
    )
    assert tuple(record.revision for record in boundary_two) == (1, 2)



def test_r3_scope_extension_checkpoint_accepts_older_subset_revision(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/r3-scope-extension-resume")
    task = repository / "task.md"
    _write_task(task, "feature/r3-scope-extension-resume", "src/runtime.py")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="r3-scope-extension-resume",
        task_file=str(task),
        branch="feature/r3-scope-extension-resume",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("docs/extra.md", "src/runtime.py"),
        target_branch="feature/r3-scope-extension-resume",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
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

    expanded = state.extend_current_slice_scope(("docs/extra.md",))
    driver.checkpoint(
        expanded,
        WorkflowHistory(expanded.current_work_unit_id),
    )

    assert driver.active_state is not None
    resolution = resolve_resume_state(repository, driver.active_state)
    assert resolution.state.current_slice.scope_paths == (
        "docs/extra.md",
        "src/runtime.py",
    )
    work_revisions = tuple(
        record
        for record in resolution.replay_result.records
        if record.logical_id == f"work-unit-{expanded.current_work_unit_id}"
    )
    assert tuple(record.revision for record in work_revisions) == (1, 2)
    assert work_revisions[0].payload.paths == ("src/runtime.py",)
    assert work_revisions[1].payload.paths == (
        "docs/extra.md",
        "src/runtime.py",
    )


def test_scope_extension_record_and_boundary_are_atomic_and_resume_authoritative(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/scope-extension-record")
    task = repository / "task.md"
    _write_task(
        task,
        "feature/scope-extension-record",
        "docs/extra.md",
        "src/runtime.py",
    )
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="scope-extension-record",
        task_file=str(task),
        branch="feature/scope-extension-record",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("docs/extra.md", "src/runtime.py"),
        target_branch="feature/scope-extension-record",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
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
    bridge = driver._artifact_bridge
    assert bridge is not None
    source_request_id = "native-codex-request-" + "c" * 64
    append_provider_decision_authority(
        bridge,
        AgentResultPayload(
            Role.CODEX,
            str(state.current_work_unit_id),
            "stopped",
            (),
            "native-codex-v2",
            source_request_id,
            "d" * 64,
        ),
        logical_id="ignored-by-helper",
        idempotency_key="scope-extension-source",
        fingerprint_sha256="b" * 64,
        operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
    )
    expanded = state.approve_current_slice_scope_extension(("docs/extra.md",))
    payload = ScopeExtensionPayload(
        str(state.current_work_unit_id),
        str(state.current_slice_id),
        source_request_id,
        "SCOPE-EXTENSION-REQUESTED",
        "Required paths: docs/extra.md\nWhy required: complete the Slice",
        (ScopeExtensionPathPayload("docs/extra.md", "documentation"),),
    )

    driver.persist_scope_extension(expanded, payload)

    chain = ArtifactStore(repository, state.run_id).load_chain()
    extension_index = next(
        index
        for index, record in enumerate(chain)
        if isinstance(record.payload, ScopeExtensionPayload)
    )
    assert isinstance(chain[extension_index + 1].payload, SliceBoundaryPayload)
    assert chain[extension_index].payload == payload
    assert chain[extension_index + 1].payload.scope_change_groups == (
        ("docs/extra.md",),
        ("src/runtime.py",),
    )
    resolution = resolve_resume_state(repository, state.run_id)
    assert resolution.state.current_slice.scope_paths == (
        "docs/extra.md",
        "src/runtime.py",
    )
    retry_key = "authorized-remediation-reprompt:" + hashlib.sha256(
        b"docs/extra.md"
    ).hexdigest()
    reprompt = expanded.mark_side_effect_completed(retry_key).start_recomposed_request()
    driver.checkpoint(reprompt, WorkflowHistory(reprompt.current_work_unit_id))
    reprompt_chain = ArtifactStore(repository, state.run_id).load_chain()
    identity_index = next(
        index
        for index, record in enumerate(reprompt_chain)
        if isinstance(record.payload, WorkflowPolicyPayload)
        and "recomposed-request:2" in record.idempotency_key
        and record.payload.work_unit_id == str(state.current_work_unit_id)
    )
    retry_index = next(
        index
        for index, record in enumerate(reprompt_chain)
        if isinstance(record.payload, SideEffectPayload)
        and record.payload.operation == (retry_key,)
        and record.payload.phase == "result"
    )
    assert identity_index < retry_index
    replayed = resolve_resume_state(repository, state.run_id)
    assert replayed.state.current_work_unit.request_sequence == 2
    assert replayed.state.current_work_unit.has_completed_side_effect(retry_key)
    (repository / ".orchestrator" / "state.json").unlink()
    assert resolve_resume_state(
        repository, state.run_id
    ).state.current_work_unit.request_sequence == 2


@pytest.mark.parametrize("approved", (True, False), ids=("approve", "reject"))
def test_scope_extension_user_gate_records_exact_decision_and_originating_request(
    tmp_path: Path,
    approved: bool,
) -> None:
    repository = _repository(tmp_path, "feature/scope-extension-user-gate")
    task = repository / "task.md"
    requested_path = "tests/existing_runtime.py"
    _write_task(
        task,
        "feature/scope-extension-user-gate",
        "src/runtime.py",
        requested_path,
    )
    head = _git(repository, "rev-parse", "HEAD")
    fingerprint = "b" * 64
    state = init_workflow_state(
        run_id="scope-extension-user-gate",
        task_file=str(task),
        branch="feature/scope-extension-user-gate",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py", requested_path),
        target_branch="feature/scope-extension-user-gate",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint=fingerprint,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)
    bridge = driver._artifact_bridge
    assert bridge is not None
    source_request_id = "native-codex-request-" + "c" * 64
    append_provider_decision_authority(
        bridge,
        AgentResultPayload(
            Role.CODEX,
            str(state.current_work_unit_id),
            "stopped",
            (),
            "native-codex-v2",
            source_request_id,
            "d" * 64,
        ),
        logical_id="ignored-by-helper",
        idempotency_key="scope-extension-user-gate-source",
        fingerprint_sha256=fingerprint,
        operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
    )
    rationale = (
        f"Required paths: {requested_path}\n"
        "Why required for current Slice: cover the existing integration seam"
    )
    pending = state.await_user_gate(
        reason=GateReason.STOP_REQUEST,
        detail=f"SCOPE-EXTENSION-REQUESTED | {rationale}",
        fingerprint=fingerprint,
        paths=(requested_path,),
        resume_step=WorkflowStep.CODEX_IMPLEMENTATION,
    )
    history = WorkflowHistory(state.current_work_unit_id)
    driver.checkpoint(pending, history)

    result = WorkflowEngine(driver).decide_current_gate(
        pending,
        history,
        approved=approved,
        rationale="reviewed existing test for the current Slice only",
        path_classes=PathClasses(
            productive=("src/**",),
            tests=("tests/**",),
            documentation=("docs/**",),
            generated=("build/**",),
        ),
    )

    chain = ArtifactStore(repository, state.run_id).load_chain()
    if approved:
        identity_index = next(
            index
            for index, record in enumerate(chain)
            if isinstance(record.payload, WorkflowPolicyPayload)
            and f"recomposed-request:2" in record.idempotency_key
            and record.payload.work_unit_id == str(state.current_work_unit_id)
        )
        retry_key = "authorized-remediation-reprompt:" + hashlib.sha256(
            requested_path.encode("utf-8")
        ).hexdigest()
        retry_index = next(
            index
            for index, record in enumerate(chain)
            if isinstance(record.payload, SideEffectPayload)
            and record.payload.operation == (retry_key,)
            and record.payload.phase == "result"
        )
        assert identity_index < retry_index
    gate_record = next(
        record
        for record in chain
        if isinstance(record.payload, GatePayload)
        and record.payload.gate_kind == "stop-request"
    )
    decision_record = next(
        record
        for record in chain
        if isinstance(record.payload, GateDecisionPayload)
        and record.payload.gate_record_id == gate_record.record_id
    )
    assert gate_record.fingerprint.sha256 == fingerprint
    assert decision_record.fingerprint.sha256 == fingerprint
    assert decision_record.payload.paths == (requested_path,)
    assert decision_record.payload.invocation_id == source_request_id
    assert datetime.fromisoformat(gate_record.created_at).tzinfo is not None
    assert datetime.fromisoformat(decision_record.created_at).tzinfo is not None
    assert (requested_path in result.state.current_slice.scope_paths) is approved
    assert result.state.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert result.state.current_work_unit.gate_decisions[-1].approved is approved
    assert result.state.current_work_unit.request_sequence == (2 if approved else 1)

    replay = replay_artifacts(chain, state.run_id)
    assert replay.gate_decisions[-1].approved is approved
    assert replay.gate_decisions[-1].fingerprint == fingerprint
    assert replay.gate_decisions[-1].paths == (requested_path,)
    assert replay.gate_decisions[-1].gate_created_at == gate_record.created_at
    resumed = resolve_resume_state(repository, state.run_id)
    assert resumed.state.current_work_unit.request_sequence == (2 if approved else 1)
    if approved:
        repeated = result.state.await_user_gate(
            reason=GateReason.STOP_REQUEST,
            detail=f"SCOPE-EXTENSION-REQUESTED | {rationale}",
            fingerprint=fingerprint,
            paths=(requested_path,),
            resume_step=WorkflowStep.CODEX_IMPLEMENTATION,
        )
        driver.checkpoint(repeated, history)
        approved_again = WorkflowEngine(driver).decide_current_gate(
            repeated,
            history,
            approved=True,
            rationale="reapprove the same Slice scope after the stale stop",
            path_classes=PathClasses(
                productive=("src/**",),
                tests=("tests/**",),
                documentation=("docs/**",),
                generated=("build/**",),
            ),
        )
        assert approved_again.state.current_work_unit.request_sequence == 3
        assert resolve_resume_state(
            repository, state.run_id
        ).state.current_work_unit.request_sequence == 3
        (repository / ".orchestrator" / "state.json").unlink()
        assert resolve_resume_state(
            repository, state.run_id
        ).state.current_work_unit.request_sequence == 3


@pytest.mark.parametrize("approval", ("operator", "automatic"))
def test_persisted_scope_stop_reprompts_new_implementer_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approval: str
) -> None:
    branch = f"feature/persisted-scope-{approval}"
    repository = _repository(tmp_path, branch)
    requested_path = (
        "tests/existing_runtime.py" if approval == "operator" else "docs/extra.md"
    )
    (repository / "src").mkdir()
    (repository / "src/runtime.py").write_text("value = 1\n", encoding="utf-8")
    if approval == "operator":
        (repository / "tests").mkdir()
        (repository / requested_path).write_text("pass\n", encoding="utf-8")
    seed_paths = ["src/runtime.py"]
    if approval == "operator":
        seed_paths.append(requested_path)
    _git(repository, "add", *seed_paths)
    _git(repository, "commit", "-m", "seed implementation scope")
    head = _git(repository, "rev-parse", "HEAD")
    task = repository / "task.md"
    _write_task(task, branch, "src/runtime.py", requested_path)
    state = init_workflow_state(
        run_id=f"persisted-scope-{approval}",
        task_file=str(task),
        branch=branch,
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest=hashlib.sha256(task.read_bytes()).hexdigest(),
        task_scope_patterns=("src/runtime.py", requested_path),
        target_branch=branch,
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).bind_slice_plan(
        (PlannedSlice(1, "implement runtime", ("src/runtime.py",)),),
        first_start_commit=head,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=head,
        scope_paths=("src/runtime.py",),
        start_fingerprint="a" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    history = WorkflowHistory(state.current_work_unit_id)
    driver.bind_work_unit(state)
    driver.checkpoint(state, history)
    context = WorkflowContext(
        "Implement the bounded task.",
        "The plan is approved.",
        "Implement runtime.",
        current_scope_paths=state.current_slice.scope_paths,
        path_classes=PathClasses(
            productive=("src/**",),
            tests=("tests/**",),
            documentation=("docs/**",),
            generated=("build/**",),
        ),
    )
    calls: list[CodexInvocation] = []

    def implement(invocation: CodexInvocation) -> NativeAgentCodexOutput:
        calls.append(invocation)
        assert len(calls) <= 2, "scope approval must not reuse the stopped response"
        bundle = invocation.native_request
        assert bundle is not None
        if len(calls) == 1:
            document = {
                "schema_version": "native-agent-codex-result-v2",
                "result_type": "stop_result",
                "request_id": bundle.bound_context.request_id,
                "rule_id": "SCOPE-EXTENSION-REQUESTED",
                "rationale": (
                    f"Required paths: {requested_path}\n"
                    "Why required for current Slice: complete the implementation"
                ),
                "remediation_paths": [requested_path],
            }
        else:
            return _native_implementation_output(invocation)
        canonical = canonical_native_codex_json(document)
        return NativeAgentCodexOutput(
            result=parse_bound_native_codex_contract_result(
                document, bundle.bound_context
            ),
            canonical_json=canonical,
            request_id=bundle.bound_context.request_id,
            response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    monkeypatch.setattr(driver, "invoke_codex", implement)
    engine = WorkflowEngine(driver)
    after_stop, history = engine._run_codex(state, context, history)
    if approval == "operator":
        assert after_stop.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
        approved = engine.decide_current_gate(
            after_stop,
            history,
            approved=True,
            rationale="approved for this Slice only",
            path_classes=context.path_classes,
        )
        after_stop, history = approved.state, approved.history
        after_stop, history = engine._run_codex(after_stop, context, history)
    assert after_stop.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert [call.request_sequence for call in calls] == [1, 2]
    assert calls[0].native_request is not None
    assert calls[1].native_request is not None
    assert calls[0].native_request.bound_context.request_id != (
        calls[1].native_request.bound_context.request_id
    )
    assert requested_path in json.loads(calls[1].native_request.canonical_json)[
        "authorized_paths"
    ]
    assert "APPROVED" in calls[1].native_request.canonical_json
    chain = ArtifactStore(repository, state.run_id).load_chain()
    assert [
        record.payload.round_number
        for record in chain
        if isinstance(record.payload, ProviderContentPayload)
        and record.payload.work_unit_id == str(state.current_work_unit_id)
    ] == [1, 2]


@pytest.mark.parametrize(
    "crash_payload_type",
    (
        ReviewAnchorPayload,
        ReviewValidationBindingPayload,
        FindingTransitionPayload,
    ),
    ids=("before-anchor", "before-validation-binding", "before-finding-transition"),
)
def test_native_review_record_ahead_recovery_reuses_bound_json_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_payload_type: type[object],
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
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/native-record-ahead",
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            "native-claude-review-v2",
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
    validation_capture = ValidationCapture(
        command, "pass", 0, "passed", "", "passed"
    )
    attestation = ValidationAttestation(
        "validation-native-recovery",
        fingerprint,
        (command,),
        (ValidationRecord(ValidationStatus.PASS, command, 0, "passed"),),
        validation_output_digest((validation_capture,)),
        "passed",
        command_specs=(
            ValidationCommandSpec(
                argv=("python3", "-m", "pytest", "tests/", "-v")
            ),
        ),
        content_captures=(validation_capture,),
    )
    driver.persist_validation_attestation(attestation)
    driver.checkpoint(
        state,
        WorkflowHistory(
            state.current_work_unit_id,
            events=(ValidationAuditEvent(1, 1, attestation),),
            attestations=(attestation,),
        ),
    )
    assert driver.active_state is not None
    state = driver.active_state
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
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": bundle.bound_context.request_id,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [
            {
                "finding_id": "C-01",
                "finding_class": "FINDING",
                "affected_paths": ["src/runtime.py"],
                "summary": "Keep recovery transaction completeness visible.",
                "acceptance_test": {
                    "kind": "prose",
                    "text": "Recovery appends the missing finding transition once.",
                },
            }
        ],
        "status_changes": [
            {
                "finding_id": "C-01",
                "status": "CLOSED",
                "rationale": "The recovery evidence decides the finding.",
                "closure": {"kind": "fixed"},
            }
        ],
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
        context=bundle.bound_context.context,
    )
    assert driver._artifact_bridge is not None
    original_append = ArtifactBridge.append
    interrupted = False

    def interrupt_child_append(self, payload, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal interrupted
        if isinstance(payload, crash_payload_type) and not interrupted:
            interrupted = True
            raise RuntimeError("simulated R7 child-record append crash")
        return original_append(self, payload, *args, **kwargs)

    with monkeypatch.context() as child_patch:
        child_patch.setattr(ArtifactBridge, "append", interrupt_child_append)
        with pytest.raises(RuntimeError, match="simulated R7 child-record append crash"):
            driver.persist_native_review_contract(output, fingerprint, 1, 1, ())

    interrupted_chain = ArtifactStore(repository, state.run_id).load_chain()
    with pytest.raises(ArtifactReplayError) as strict_error:
        replay_artifacts(interrupted_chain, state.run_id)
    assert strict_error.value.code is ReplayDiagnosticCode.RECORD_MISSING
    interrupted_replay = replay_artifacts(
        interrupted_chain,
        state.run_id,
        allow_incomplete_review_tail=True,
    )
    interrupted_reviews = tuple(
        item for item in interrupted_chain if isinstance(item.payload, ReviewPayload)
    )
    assert len(interrupted_reviews) == 1
    assert interrupted_replay.pending_review_record_id == interrupted_reviews[0].record_id
    assert (
        resolve_resume_state(repository, state).replay_result.pending_review_record_id
        == interrupted_reviews[0].record_id
    )
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
    completed_chain = ArtifactStore(repository, state.run_id).load_chain()
    reviews = tuple(
        item
        for item in completed_chain
        if isinstance(item.payload, ReviewPayload)
    )
    assert len(reviews) == 1
    assert reviews[0].payload.request_id == bundle.bound_context.request_id
    anchors = tuple(
        item
        for item in completed_chain
        if isinstance(item.payload, ReviewAnchorPayload)
        and item.payload.review_record_id == reviews[0].record_id
    )
    bindings = tuple(
        item
        for item in completed_chain
        if isinstance(item.payload, ReviewValidationBindingPayload)
        and item.payload.review_record_id == reviews[0].record_id
    )
    assert len(anchors) == len(bindings) == 1
    assert bindings[0].payload.attestation_record_id == next(
        item.record_id
        for item in completed_chain
        if isinstance(item.payload, ValidationAttestationPayload)
        and item.logical_id == attestation.attestation_id
    )
    finding_transitions = tuple(
        item
        for item in completed_chain
        if isinstance(item.payload, FindingTransitionPayload)
        and item.payload.finding_id == "C-01"
    )
    assert tuple(item.payload.action for item in finding_transitions) == (
        "opened",
        "status_changed",
    )
    assert replay_artifacts(completed_chain, state.run_id).pending_review_record_id is None

    pre_policy = driver.recover_pending_native_reviewer_before_policy(
        state,
        WorkflowContext(
                assignment="Recover the durable native decision.",
                distilled_plan="Claude reviews the bound response once.",
                slice_summary="Native reviewer record-ahead recovery.",
            ),
        WorkflowHistory(
            state.current_work_unit_id,
            attestations=(attestation,),
        ),
    )
    assert pre_policy is not None
    assert pre_policy.output == output
    assert pre_policy.fingerprint == fingerprint
    assert pre_policy.round_number == 1

    log_path.unlink()
    assert driver.recover_pending_native_reviewer(
        invocation,
        contract,
        WorkflowHistory(state.current_work_unit_id),
    ) == output


def test_native_codex_record_ahead_recovery_reuses_raw_json_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
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
        first_slice_start_commit=head,
        slice_count=1,
        task_digest=hashlib.sha256(
            task.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest(),
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/native-codex-record-ahead",
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
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
        agents={
            "codex": SimpleNamespace(model="test-model", effort="high")
        },  # type: ignore[dict-item]
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
    large_evidence = "Implement the bound recovery request. " * 1_000
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
                    "workflow-prompt", "orchestrator_instruction", large_evidence
                ),
            ),
        )
    )
    document = {
        "schema_version": "native-agent-codex-result-v2",
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
    driver._persist_native_agent_request_bundle(invocation)
    raw_path = driver._native_codex_response_path(invocation)
    measurement = measure_provider_input(
        PreparedProviderInput(
            command=("codex", "exec"),
            stdin_text=bundle.canonical_json,
            components=(
                ProviderInputComponent("stdin_prompt", bundle.canonical_json),
            ),
        ),
        provider="codex",
        role="codex",
        operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
        binding_fingerprint="c" * 64,
        policy=default_provider_input_budget_policy(),
    )
    bootstrap = driver._persist_provider_bootstrap(measurement)
    attempt = driver._start_provider_attempt(
        measurement,
        bootstrap,
        operation_instance="request:1",
        durable_response_path=raw_path,
    )
    driver._write_native_codex_raw_response(attempt[2], canonical)
    driver._finish_provider_attempt(attempt, 1.0, "runtime", None)
    rebuilt_bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=replace(native_context, current_fingerprint="d" * 64),
            target_branch=state.branch,
            base_commit=head,
            authorized_paths=("src/runtime.py",),
            assignment="Implement runtime.",
            work_context="Use the bound native contract.",
            evidence=(
                NativeCodexEvidenceInput(
                    "workflow-prompt", "orchestrator_instruction", large_evidence
                ),
            ),
        )
    )
    rebuilt_invocation = replace(invocation, native_request=rebuilt_bundle)
    with pytest.raises(
        WorkflowExecutionError,
        match=(
            "field=binding_fingerprint previous=cccccccccccc "
            "current=dddddddddddd"
        ),
    ):
        driver._persist_native_agent_request_bundle(rebuilt_invocation)
    bridge = driver._artifact_bridge
    assert bridge is not None
    assert not any(
        isinstance(item.payload, ProviderContentPayload)
        for item in bridge.store.load_chain()
    )
    request_path = driver._native_agent_request_path(invocation)
    persisted_request = request_path.read_text(encoding="utf-8")
    later_invocation = replace(rebuilt_invocation, request_sequence=2)
    recovered_request = driver._load_native_agent_request_bundle(
        later_invocation,
        rebuilt_bundle,
        bundle.bound_context.request_id,
    )
    assert recovered_request is not None
    assert recovered_request.canonical_json == bundle.canonical_json
    assert recovered_request.bound_context.request_id == bundle.bound_context.request_id
    historical_finding = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The earlier request offered this finding.",
        acceptance_test="Cross-round recovery preserves the exact offered subset.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    historical_contract = replace(
        contract, round_number=3, request_sequence=3
    )
    historical_bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=replace(
                native_context,
                contract=historical_contract,
                previous_findings=(historical_finding,),
            ),
            target_branch=state.branch,
            base_commit=head,
            authorized_paths=("src/runtime.py",),
            assignment="Implement runtime.",
            work_context="Use the bound native contract.",
            evidence=(
                NativeCodexEvidenceInput(
                    "workflow-prompt", "orchestrator_instruction", large_evidence
                ),
            ),
        )
    )
    historical_invocation = replace(
        invocation,
        round_number=3,
        request_sequence=3,
        native_request=historical_bundle,
    )
    driver._native_agent_request_path(historical_invocation).write_text(
        driver._native_agent_request_bundle_json(historical_bundle),
        encoding="utf-8",
    )
    later_contract = replace(contract, round_number=4, request_sequence=4)
    later_bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=replace(
                native_context,
                current_fingerprint="d" * 64,
                contract=later_contract,
                previous_findings=(),
            ),
            target_branch=state.branch,
            base_commit=head,
            authorized_paths=("src/runtime.py",),
            assignment="Implement runtime.",
            work_context="Use the bound native contract.",
            evidence=(
                NativeCodexEvidenceInput(
                    "workflow-prompt", "orchestrator_instruction", large_evidence
                ),
            ),
        )
    )
    restored_historical = driver._load_native_agent_request_bundle(
        replace(
            invocation,
            round_number=4,
            request_sequence=4,
            native_request=later_bundle,
        ),
        later_bundle,
        historical_bundle.bound_context.request_id,
        (historical_finding,),
    )
    assert restored_historical is not None
    assert restored_historical.bound_context.context.contract.round_number == 3
    assert restored_historical.bound_context.context.previous_findings == (
        historical_finding,
    )
    request_path.unlink()
    with pytest.raises(
        WorkflowExecutionError,
        match="raw response has no persisted request bundle",
    ):
        driver.recover_pending_native_codex(
            rebuilt_invocation,
            contract,
            WorkflowHistory(state.current_work_unit_id),
        )
    request_path.write_text(persisted_request, encoding="utf-8")
    attempt[2].write_text("{}", encoding="utf-8")
    with pytest.raises(
        WorkflowExecutionError,
        match="raw response does not match its provider ledger result",
    ):
        driver.recover_pending_native_codex(
            rebuilt_invocation,
            contract,
            WorkflowHistory(state.current_work_unit_id),
        )
    attempt[2].write_text(canonical, encoding="utf-8")

    raw_ahead_recovered = driver.recover_pending_native_codex(
        rebuilt_invocation,
        contract,
        WorkflowHistory(state.current_work_unit_id),
    )
    monkeypatch.setattr(driver, "_artifact_fingerprint", lambda: "f" * 64)
    recovered = driver.recover_pending_native_codex(
        rebuilt_invocation,
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
    recovered_chain = ArtifactStore(repository, state.run_id).load_chain()
    assert all(
        _IDENTIFIER_RE.fullmatch(item.idempotency_key)
        for item in recovered_chain
    )
    provider_contents = tuple(
        item
        for item in recovered_chain
        if isinstance(item.payload, ProviderContentPayload)
    )
    assert len(provider_contents) == 1
    assert provider_contents[0].payload.response_sha256 == output.response_sha256
    provider_attempts = tuple(
        item
        for item in recovered_chain
        if isinstance(item.payload, ProviderAttemptPayload)
    )
    assert len(provider_attempts) == 2
    assert {item.payload.phase for item in provider_attempts} == {"started", "failed"}

    next_contract = replace(contract, request_sequence=2)
    next_bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=replace(native_context, contract=next_contract),
            target_branch=state.branch,
            base_commit=head,
            authorized_paths=("docs/approved.md", "src/runtime.py"),
            assignment="Implement runtime with the approved scope.",
            work_context="The scope extension is approved for this Slice.",
            evidence=(
                NativeCodexEvidenceInput(
                    "workflow-prompt", "orchestrator_instruction", large_evidence
                ),
            ),
        )
    )
    assert driver.recover_pending_native_codex(
        replace(invocation, request_sequence=2, native_request=next_bundle),
        next_contract,
        WorkflowHistory(state.current_work_unit_id),
    ) is None

    tampered_request = json.loads(persisted_request)
    assert tampered_request["evidence_assets"]
    tampered_request["evidence_assets"][0]["content"] += "tampered"
    request_path.write_text(
        json.dumps(
            tampered_request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        WorkflowExecutionError,
        match="evidence asset.*differs from content",
    ):
        driver.recover_pending_native_codex(
            rebuilt_invocation,
            contract,
            WorkflowHistory(state.current_work_unit_id),
        )
    request_path.write_text(persisted_request, encoding="utf-8")
    request_path.unlink()
    legacy_rebuilt_bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=replace(native_context, current_fingerprint="e" * 64),
            target_branch=state.branch,
            base_commit=head,
            authorized_paths=("src/runtime.py",),
            assignment="Implement runtime.",
            work_context="Use the bound native contract.",
            evidence=(
                NativeCodexEvidenceInput(
                    "workflow-prompt", "orchestrator_instruction", large_evidence
                ),
            ),
        )
    )
    assert driver.recover_pending_native_codex(
        replace(invocation, native_request=legacy_rebuilt_bundle),
        contract,
        WorkflowHistory(state.current_work_unit_id),
    ) == output

    raw_path.write_text("{}", encoding="utf-8")
    assert driver.recover_pending_native_codex(
        rebuilt_invocation,
        contract,
        WorkflowHistory(state.current_work_unit_id),
    ) == output


def test_native_review_persists_open_status_rationale_for_authoritative_replay(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/native-open-rationale-replay")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="native-open-rationale-replay",
        task_file=str(tmp_path / "task.md"),
        branch="feature/native-open-rationale-replay",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/native-open-rationale-replay",
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
            claude_review_transport="native-claude-review-v2",
        ),
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
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The reviewer needs another correction round.",
        acceptance_test="The next review must retain its OPEN rationale.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    opened = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=False,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=(),
        pre_mortem=None,
        evidence=None,
        findings=(finding,),
        anchors=(),
    )
    driver._persist_review_finding_transitions(
        opened,
        fingerprint="d" * 64,
        round_number=1,
        previous_findings=(),
        structured=True,
    )
    reaffirmed = replace(
        finding,
        status_rationale="The first correction is incomplete; keep this blocker open.",
    )
    denied = replace(opened, findings=(reaffirmed,))
    driver._persist_review_finding_transitions(
        denied,
        fingerprint="e" * 64,
        round_number=2,
        previous_findings=(finding,),
        structured=True,
    )

    replay = replay_artifacts(
        ArtifactStore(repository, state.run_id).load_chain(), state.run_id
    )
    projected = replay_findings(replay, state.current_work_unit_id)

    assert projected == (reaffirmed,)
    transitions = tuple(
        record.payload
        for record in replay.records
        if isinstance(record.payload, FindingTransitionPayload)
    )
    assert tuple(item.action for item in transitions) == (
        "opened",
        "status_changed",
    )


def _finding_transition_driver(
    tmp_path: Path, run_id: str
) -> tuple[ProductionWorkflowDriver, object, FindingRecord]:
    repository = _repository(tmp_path, f"feature/{run_id}")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id=run_id,
        task_file=str(tmp_path / "task.md"),
        branch=f"feature/{run_id}",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch=f"feature/{run_id}",
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
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
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="Finding transition identity must be durable.",
        acceptance_test="Replay the transition without duplication.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    return driver, state, finding


def test_correction_persistence_rejects_accepted_unchanged_fingerprint(
    tmp_path: Path,
) -> None:
    driver, state, finding = _finding_transition_driver(
        tmp_path, "accepted-correction-without-change"
    )
    correction_state = state.with_current_step(WorkflowStep.CODEX_CORRECTION)
    driver.active_state = correction_state
    request_fingerprint = driver._artifact_fingerprint()
    contract = CodexStepContract(
        "accepted-correction-without-change",
        ReadinessMarker.IMPLEMENTATION,
        "01",
        1,
        require_test_files_record=True,
        expected_test_files=(),
        test_changes_approved=True,
    )
    context = NativeCodexContext(
        run_id=state.run_id,
        work_unit_id=str(state.current_work_unit_id),
        operation=WorkflowStep.CODEX_CORRECTION.value,
        current_fingerprint=request_fingerprint,
        request_kind=NativeCodexRequestKind.CORRECTION,
        contract=contract,
        previous_findings=(finding,),
    )
    canonical = '{"result_type":"correction_result"}'
    output = NativeAgentCodexOutput(
        result=CodexContractResult(
            ready=True,
            stopped=False,
            stop_request=None,
            validation=None,
            test_files=(),
            findings=(
                replace(
                    finding,
                    responses=(
                        FindingResponse(
                            FindingResponseDecision.ACCEPTED,
                            "The finding is accepted without a repository change.",
                        ),
                    ),
                ),
            ),
        ),
        canonical_json=canonical,
        request_id=f"native-codex-request-{'b' * 64}",
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        context=context,
    )

    with pytest.raises(NativeCodexContractError, match=r"accepted finding IDs C-01"):
        driver.persist_native_codex_contract(output, 1, (finding,))

    bridge = driver._artifact_bridge
    assert bridge is not None
    assert not any(
        isinstance(record.payload, AgentResultPayload)
        for record in bridge.store.load_chain()
    )


def _request_time_review_persistence_case(
    tmp_path: Path, run_id: str
) -> tuple[
    ProductionWorkflowDriver,
    WorkflowState,
    NativeAgentReviewOutput,
    str,
]:
    driver, state, _finding = _finding_transition_driver(tmp_path, run_id)
    fingerprint = "d" * 64
    review_state = state.with_current_step(WorkflowStep.CLAUDE_PLAN_REVIEW)
    driver.active_state = review_state
    command = "python3 -m pytest tests/ -v"
    capture = ValidationCapture(command, "pass", 0, "passed", "", "passed")
    attestation = ValidationAttestation(
        f"validation-{run_id}",
        fingerprint,
        (command,),
        (ValidationRecord(ValidationStatus.PASS, command, 0, "passed"),),
        validation_output_digest((capture,)),
        "passed",
        command_specs=(
            ValidationCommandSpec(argv=("python3", "-m", "pytest", "tests/", "-v")),
        ),
        content_captures=(capture,),
    )
    driver.persist_validation_attestation(attestation)
    context = NativeReviewContext(
        run_id=review_state.run_id,
        work_unit_id=str(review_state.current_work_unit_id),
        operation=WorkflowStep.CLAUDE_PLAN_REVIEW.value,
        diff_fingerprint=fingerprint,
        reviewer=AgentRole.CLAUDE,
        approval_marker=ApprovalMarker.PLAN,
        slice_id="01",
        round_number=1,
        request_sequence=1,
        validation_attestation=attestation,
    )
    result = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=True,
        stopped=False,
        stop_request=None,
        validation=attestation,
        test_files=(),
        pre_mortem="A future persistence change could reuse the live request cursor.",
        evidence=ReviewEvidence(
            "request-time persistence binding",
            "a future caller omits the captured sequence",
            "provider content is recorded under the live sequence",
        ),
        findings=(),
        anchors=(),
    )
    output = NativeAgentReviewOutput(
        result=result,
        canonical_json='{"result_type":"review_result"}',
        request_id=f"native-review-request-{'a' * 64}",
        context=context,
    )
    return driver, review_state, output, fingerprint


def test_recomposed_review_persistence_uses_request_time_sequence(
    tmp_path: Path,
) -> None:
    driver, review_state, output, fingerprint = (
        _request_time_review_persistence_case(tmp_path, "review-request-time")
    )
    driver.active_state = review_state.start_recomposed_request()

    driver.persist_native_review_contract(output, fingerprint, 1, 1, ())

    bridge = driver._artifact_bridge
    assert bridge is not None
    chain = bridge.store.load_chain()
    content = next(
        record.payload
        for record in chain
        if isinstance(record.payload, ProviderContentPayload)
        and record.payload.content_kind == "review_result"
    )
    assert content.round_number == 1
    assert driver.active_state.current_work_unit.request_sequence == 2


def test_review_context_mismatch_names_only_the_request_sequence_field(
    tmp_path: Path,
) -> None:
    driver, _review_state, output, fingerprint = (
        _request_time_review_persistence_case(tmp_path, "review-context-mismatch")
    )

    with pytest.raises(
        WorkflowExecutionError,
        match=r"exact review context: field=request_sequence$",
    ) as raised:
        driver.persist_native_review_contract(output, fingerprint, 1, 2, ())

    assert raised.value.orchestrator_diagnostic is (
        OrchestratorDiagnostic.WORKFLOW_REVIEW_CONTEXT_REQUEST_SEQUENCE
    )
    assert "native-review-request" not in raised.value.orchestrator_diagnostic.text


def test_recomposed_implementer_persistence_uses_request_time_sequence(
    tmp_path: Path,
) -> None:
    driver, state, _finding = _finding_transition_driver(
        tmp_path, "implementer-request-time"
    )
    implementation_state = state.with_current_step(
        WorkflowStep.CODEX_IMPLEMENTATION
    )
    driver.active_state = implementation_state.start_recomposed_request()
    canonical = '{"result_type":"implementation_result"}'
    output = NativeAgentCodexOutput(
        result=CodexContractResult(
            ready=True,
            stopped=False,
            stop_request=None,
            validation=None,
            test_files=(),
            findings=(),
        ),
        canonical_json=canonical,
        request_id=f"native-codex-request-{'b' * 64}",
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )

    driver.persist_native_codex_contract(output, 1, ())

    bridge = driver._artifact_bridge
    assert bridge is not None
    chain = bridge.store.load_chain()
    result_record = next(
        record for record in chain if isinstance(record.payload, AgentResultPayload)
    )
    content = next(
        record.payload
        for record in chain
        if isinstance(record.payload, ProviderContentPayload)
        and record.payload.content_kind == "agent_result"
    )
    assert result_record.logical_id.endswith("-1")
    assert content.round_number == 1
    assert driver.active_state.current_work_unit.request_sequence == 2


def _finding_review(finding: FindingRecord) -> ContractResult:
    return ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=False,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=(),
        pre_mortem=None,
        evidence=None,
        findings=(finding,),
        anchors=(),
    )


def test_invalid_review_subset_publishes_no_review_or_finding_fact(
    tmp_path: Path,
) -> None:
    driver, _state, existing = _finding_transition_driver(
        tmp_path, "invalid-review-subset"
    )
    driver._persist_review_finding_transitions(
        _finding_review(existing),
        fingerprint="d" * 64,
        round_number=1,
        previous_findings=(),
        structured=True,
    )
    colliding = replace(
        existing,
        summary="A later review silently reused the assigned identifier.",
        origin=FindingOrigin("33", 1, AgentRole.CLAUDE),
    )
    output = NativeAgentReviewOutput(
        result=_finding_review(colliding),
        canonical_json="{}",
        request_id=f"native-review-request-{'a' * 64}",
    )
    bridge = driver._artifact_bridge
    assert bridge is not None
    before = bridge.store.load_chain()

    with pytest.raises(WorkflowExecutionError, match=r"before publication.*C-01"):
        driver.persist_native_review_contract(
            output,
            "d" * 64,
            1,
            1,
            (),
        )

    after = bridge.store.load_chain()
    assert after == before
    assert not any(isinstance(record.payload, ReviewPayload) for record in after)
    assert sum(
        isinstance(record.payload, FindingTransitionPayload) for record in after
    ) == 1


@pytest.mark.parametrize(
    ("transition_identity", "action"),
    (
        ("opened", "opened"),
        ("status_changed", "status_changed"),
    ),
)
def test_structured_finding_transition_reuses_semantically_identical_old_key(
    tmp_path: Path, transition_identity: str, action: str
) -> None:
    driver, state, finding = _finding_transition_driver(
        tmp_path, f"old-key-{transition_identity}"
    )
    previous: tuple[FindingRecord, ...] = ()
    current = finding
    rationale = finding.summary
    if transition_identity == "status_changed":
        previous = (finding,)
        current = replace(
            finding,
            status=FindingStatus.CLOSED,
            status_rationale="The issue is fixed.",
        )
        rationale = current.status_rationale or current.summary
    fingerprint = "d" * 64
    old_key = f"finding:C-01:{transition_identity}:1:claude"
    bridge = driver._artifact_bridge
    assert bridge is not None
    old_record = bridge.append(
        finding_payload(
            current,
            action=action,
            rationale=rationale,
            work_unit_id=state.current_work_unit_id,
        ),
        logical_id="finding-C-01",
        idempotency_key=old_key,
        fingerprint_sha256=fingerprint,
    )

    driver._persist_review_finding_transitions(
        _finding_review(current),
        fingerprint=fingerprint,
        round_number=1,
        previous_findings=previous,
        structured=True,
    )

    records = tuple(
        item
        for item in bridge.store.load_chain()
        if isinstance(item.payload, FindingTransitionPayload)
    )
    assert records == (old_record,)


def test_structured_finding_transition_old_key_from_other_work_unit_is_rejected(
    tmp_path: Path,
) -> None:
    driver, state, finding = _finding_transition_driver(tmp_path, "old-key-other-unit")
    bridge = driver._artifact_bridge
    assert bridge is not None
    old_key = "finding:C-01:opened:1:claude"
    bridge.append(
        finding_payload(
            finding,
            rationale=finding.summary,
            work_unit_id="999",
        ),
        logical_id="finding-C-01",
        idempotency_key=old_key,
        fingerprint_sha256="d" * 64,
    )

    with pytest.raises(WorkflowExecutionError, match=r"already assigned.*C-01"):
        driver._persist_review_finding_transitions(
            _finding_review(finding),
            fingerprint="d" * 64,
            round_number=1,
            previous_findings=(),
            structured=True,
        )
    records = tuple(
        item
        for item in bridge.store.load_chain()
        if isinstance(item.payload, FindingTransitionPayload)
    )
    assert tuple(item.payload.work_unit_id for item in records) == ("999",)


def test_structured_finding_transition_old_key_conflict_in_same_work_unit_fails_closed(
    tmp_path: Path,
) -> None:
    driver, state, finding = _finding_transition_driver(tmp_path, "old-key-conflict")
    bridge = driver._artifact_bridge
    assert bridge is not None
    bridge.append(
        finding_payload(
            finding,
            rationale="Conflicting historical rationale.",
            work_unit_id=state.current_work_unit_id,
        ),
        logical_id="finding-C-01",
        idempotency_key="finding:C-01:opened:1:claude",
        fingerprint_sha256="d" * 64,
    )

    with pytest.raises(WorkflowExecutionError, match=r"already assigned.*C-01"):
        driver._persist_review_finding_transitions(
            _finding_review(finding),
            fingerprint="d" * 64,
            round_number=1,
            previous_findings=(),
            structured=True,
        )


def test_status_rationale_key_resume_boundary_keeps_existing_identity(
    tmp_path: Path,
) -> None:
    driver, state, finding = _finding_transition_driver(
        tmp_path, "status-rationale-resume"
    )
    reaffirmed = replace(finding, status_rationale="Keep the blocker open.")
    review = _finding_review(reaffirmed)

    driver._persist_review_finding_transitions(
        _finding_review(finding),
        fingerprint="d" * 64,
        round_number=1,
        previous_findings=(),
        structured=True,
    )
    driver._persist_review_finding_transitions(
        review,
        fingerprint="e" * 64,
        round_number=2,
        previous_findings=(finding,),
        structured=True,
    )
    driver._persist_review_finding_transitions(
        review,
        fingerprint="e" * 64,
        round_number=2,
        previous_findings=(finding,),
        structured=True,
    )

    bridge = driver._artifact_bridge
    assert bridge is not None
    replay = replay_artifacts(bridge.store.load_chain(), state.run_id)
    records = tuple(
        item
        for item in replay.records
        if isinstance(item.payload, FindingTransitionPayload)
    )
    assert len(records) == 2
    assert records[1].idempotency_key == (
        f"finding:C-01:status_rationale:{state.current_work_unit_id}:2:claude"
    )
    assert replay_findings(replay, state.current_work_unit_id) == (reaffirmed,)


def test_unstructured_finding_transition_key_is_unchanged(tmp_path: Path) -> None:
    driver, _, finding = _finding_transition_driver(tmp_path, "legacy-key")
    driver._persist_review_finding_transitions(
        _finding_review(finding),
        fingerprint="d" * 64,
        round_number=1,
        previous_findings=(),
        structured=False,
    )
    bridge = driver._artifact_bridge
    assert bridge is not None
    records = tuple(
        item
        for item in bridge.store.load_chain()
        if isinstance(item.payload, FindingTransitionPayload)
    )
    assert len(records) == 1
    assert records[0].idempotency_key == "finding:C-01:opened:1:claude"
    assert records[0].payload.work_unit_id is None


def test_structured_finding_transition_rejects_reused_id_in_later_work_unit(
    tmp_path: Path,
) -> None:
    driver, state, finding = _finding_transition_driver(tmp_path, "separate-units")
    review = _finding_review(finding)
    driver._persist_review_finding_transitions(
        review,
        fingerprint="d" * 64,
        round_number=1,
        previous_findings=(),
        structured=True,
    )
    next_state = state.complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=state.branch_base,
        scope_paths=("src/runtime.py",),
        start_fingerprint="c" * 64,
    )
    driver.bind_work_unit(next_state)
    with pytest.raises(WorkflowExecutionError, match=r"already assigned.*C-01"):
        driver._persist_review_finding_transitions(
            review,
            fingerprint="d" * 64,
            round_number=1,
            previous_findings=(),
            structured=True,
        )

    bridge = driver._artifact_bridge
    assert bridge is not None
    replay = replay_artifacts(bridge.store.load_chain(), state.run_id)
    records = tuple(
        item
        for item in replay.records
        if isinstance(item.payload, FindingTransitionPayload)
    )
    assert tuple(item.payload.work_unit_id for item in records) == (
        str(state.current_work_unit_id),
    )
    assert len({item.idempotency_key for item in records}) == 1
    assert replay_findings(replay, state.current_work_unit_id) == (finding,)
    assert replay_findings(replay, next_state.current_work_unit_id) == ()


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
            first_slice_start_commit=head,
            slice_count=1,
            task_digest=hashlib.sha256(
                task.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest(),
            task_scope_patterns=("src/runtime.py",),
            target_branch="feature/native-codex-finding-recovery",
            protocol_binding=ProtocolBinding(
                ProtocolMode.STRUCTURED_V2,
                "2",
                codex_result_transport="native-codex-v2",
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
        "schema_version": "native-agent-codex-result-v2",
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
        context=bundle.bound_context.context,
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
    opening_record = bridge.append(
        finding_payload(
            finding,
            actor=AgentRole.CLAUDE,
            action="opened",
            rationale="Opened for recovery coverage.",
            work_unit_id=state.current_work_unit_id,
        ),
        logical_id="finding-C-01",
        idempotency_key="finding:C-01:opened:recovery-test",
        fingerprint_sha256="c" * 64,
    )
    measurement_record = bridge.append(
        ProviderInputMeasurementPayload(
            provider=Role.CODEX,
            role=Role.CODEX,
            operation=WorkflowStep.CODEX_CORRECTION.value,
            work_unit_id=str(state.current_work_unit_id),
            transition_fingerprint="c" * 64,
            relevant_record_head="8" * 64,
            input_digest="6" * 64,
            policy_digest="7" * 64,
            components=(ProviderInputComponentPayload("stdin_prompt", 1, 1),),
            total_chars=1,
            total_bytes=1,
            safety_limit_chars=2,
            safety_limit_bytes=2,
            technical_limit_chars=None,
            technical_limit_bytes=None,
            technical_limit_source=None,
            effective_limit_chars=2,
            effective_limit_bytes=2,
            allowed=True,
            violated_dimensions=(),
            char_overage=0,
            byte_overage=0,
            largest_component="stdin_prompt",
        ),
        logical_id="provider-input-finding-recovery",
        idempotency_key="provider-input:finding-recovery",
        fingerprint_sha256="c" * 64,
    )
    bridge.append(
        ProviderAttemptPayload(
            provider=Role.CODEX,
            role=Role.CODEX,
            operation=WorkflowStep.CODEX_CORRECTION.value,
            work_unit_id=str(state.current_work_unit_id),
            logical_operation_id="provider-operation-" + "4" * 64,
            binding_fingerprint="c" * 64,
            measurement_record_id=measurement_record.record_id,
            input_digest="6" * 64,
            attempt_number=1,
            phase="started",
            started_at="2026-08-28T12:00:00+00:00",
            ended_at=None,
            duration_seconds=None,
            failure_kind=None,
            usage=None,
        ),
        logical_id="provider-operation-finding-recovery-1",
        idempotency_key="provider-attempt:finding-recovery:started",
        fingerprint_sha256="c" * 64,
    )
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
    (repository / "src").mkdir()
    (repository / "src" / "runtime.py").write_text(
        "recovery correction changed the bound implementation\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="crash after AgentResult"):
        driver.persist_native_codex_contract(output, 1, (finding,))

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
    (repository / "README.md").write_text(
        "recovered tree now has a different fingerprint\n", encoding="utf-8"
    )
    driver.persist_native_codex_contract(recovered, 1, (finding,))
    after_engine_persistence = ArtifactStore(repository, state.run_id).load_chain()
    assert sum(
        isinstance(item.payload, AgentResultPayload)
        for item in after_engine_persistence
    ) == 1
    replayed_finding = replay_findings(
        replay_artifacts(
            ArtifactStore(repository, state.run_id).load_chain(), state.run_id
        )
    )[0]
    canonical_agent_result = next(
        item
        for item in after_engine_persistence
        if isinstance(item.payload, AgentResultPayload)
    )
    with pytest.raises(
        WorkflowExecutionError,
        match="native agent recovery has divergent agent-result records",
    ):
        driver._canonical_native_agent_result(
            (
                canonical_agent_result,
                replace(
                    canonical_agent_result,
                    payload=replace(
                        canonical_agent_result.payload,
                        outcome="not_ready",
                    ),
                ),
            ),
            canonical_agent_result.logical_id,
        )
    duplicate_fingerprint = "d" * 64
    duplicate_binding_digest = hashlib.sha256(
        (
            f"{duplicate_fingerprint}:{output.request_id}:"
            f"{output.response_sha256}"
        ).encode("utf-8")
    ).hexdigest()
    existing_content = next(
        item.payload
        for item in bridge.store.load_chain()
        if isinstance(item.payload, ProviderContentPayload)
        and item.payload.request_id == output.request_id
    )
    bridge.append(
        existing_content,
        logical_id="provider-content-duplicate-fingerprint",
        idempotency_key="provider-content:duplicate-fingerprint",
        fingerprint_sha256=duplicate_fingerprint,
    )
    bridge.append(
        canonical_agent_result.payload,
        logical_id=canonical_agent_result.logical_id,
        idempotency_key=(
            f"native:{canonical_agent_result.logical_id}:"
            f"{duplicate_binding_digest}"
        ),
        fingerprint_sha256=duplicate_fingerprint,
    )
    resumed_context = replace(
        native_context,
        previous_findings=(replayed_finding,),
    )
    resumed_bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=resumed_context,
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
    assert resumed_bundle.bound_context.request_id == bundle.bound_context.request_id
    recovered_after_response = driver.recover_pending_native_codex(
        replace(
            invocation,
            native_request=resumed_bundle,
            previous_findings=(replayed_finding,),
        ),
        contract,
        WorkflowHistory(
            state.current_work_unit_id,
            findings=(replayed_finding,),
        ),
    )

    assert recovered_after_response.result.findings == (replayed_finding,)
    replay = replay_artifacts(
        ArtifactStore(repository, state.run_id).load_chain(), state.run_id
    )
    assert sum(
        isinstance(item.payload, AgentResultPayload) for item in replay.records
    ) == 2
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
            NativeCodexRequestKind.CORRECTION,
            WorkflowStep.CODEX_CORRECTION,
            ReadinessMarker.IMPLEMENTATION,
            "correction_result",
        ),
    ),
)
def test_native_codex_plan_and_correction_recovery_are_raw_and_record_ahead_safe(
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
        first_slice_start_commit=head,
        slice_count=1,
        task_digest=task_digest,
        task_scope_patterns=("src/runtime.py",),
        target_branch=branch,
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
        ),
    )
    current_fingerprint = task_digest
    commit_authority_state: WorkflowState | None = None
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
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    if commit_authority_state is not None:
        _append_test_commit_authority(
            driver, commit_authority_state, commit_ref=head
        )
    driver.bind_work_unit(state)
    contract = CodexStepContract(
        f"native-codex-{request_kind.value}-recovery",
        readiness,
        "01",
        1,
        review_fingerprint=(
            None
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
        "schema_version": "native-agent-codex-result-v2",
        "result_type": result_type,
        "request_id": bundle.bound_context.request_id,
        "ready": True,
    }
    if request_kind is NativeCodexRequestKind.PLAN:
        document["finding_dispositions"] = []
        document["slice_plan"] = [
            {
                "slice_id": 1,
                "summary": "Implement the bound plan.",
                "scope_paths": ["src/runtime.py"],
                "acceptance_criteria": [{
                    "text": "Implement the bound plan.",
                    "measured_against": "SOURCE",
                }],
            }
        ]
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
    driver._persist_provider_content(
        role=Role.CODEX,
        work_unit_id=state.current_work_unit_id,
        request_sequence=invocation.request_sequence,
        operation=step.value,
        request_id=output.request_id,
        canonical=output.canonical_json,
        content_kind="agent_result",
        fingerprint=current_fingerprint,
        fingerprint_kind=(
            FingerprintKind.CONTRACT
            if state.current_work_unit.kind is WorkUnitKind.PLAN
            else FingerprintKind.IMPLEMENTATION
        ),
    )

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
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/structured-round-transition",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
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
        progress_made=True,
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
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
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

    reviewed = _append_test_commit_authority(
        driver, state, commit_ref=second_slice_start
    )
    slice_two = reviewed.complete_current_slice(
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
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=(slice_path, "src/runtime.py"),
        audit_report_path="docs/internal/structured-audit-review-12345678.md",
        target_branch="feature/structured-audit",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
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
    assert "<!-- audit:overview:begin -->" in overall.read_text(
        encoding="utf-8"
    )
    rendered = slice_audit.read_text(encoding="utf-8")
    assert "<!-- audit:status:begin -->" in rendered
    assert "artifact-records" not in rendered
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
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/structured-audit-mismatch",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
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

    with pytest.raises(WorkflowExecutionError, match="audit projection failed"):
        driver.checkpoint(mismatched, WorkflowHistory(1))


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
        first_slice_start_commit="a" * 40,
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
        first_slice_start_commit="a" * 40,
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
        first_slice_start_commit="a" * 40,
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


def test_runtime_inherits_exact_prior_test_gate_at_final_review_boundary() -> None:
    test_path = "tests/rounding.test.mjs"
    fingerprint = "c" * 64
    state = orchestrator.init_workflow_state(
        run_id="resume-repeated-test-gate",
        task_file="/repo/inbox/rounding.md",
        branch="feature/rounding",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
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
        first_slice_start_commit="b" * 40,
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


def test_audit_test_approval_is_projected_from_gate_record_authority_and_time(
    tmp_path: Path,
) -> None:
    fingerprint = "a" * 64
    later_fingerprint = "b" * 64
    paths = ("tests/test_gate.py",)
    state = init_workflow_state(
        run_id="gate-audit-authority",
        task_file=str(tmp_path / "task.md"),
        branch="feature/gate-audit",
        branch_base="b" * 40,
        first_slice_start_commit="b" * 40,
        slice_count=1,
        task_digest="c" * 64,
        task_scope_patterns=paths,
        target_branch="feature/gate-audit",
    ).await_user_gate(
        reason=GateReason.TEST_CHANGE,
        detail="test approval required",
        fingerprint=fingerprint,
        paths=paths,
    ).record_user_gate_decision(
        approved=True,
        fingerprint=fingerprint,
        paths=paths,
        rationale="reviewed exact test delta",
    ).await_user_gate(
        reason=GateReason.TEST_CHANGE,
        detail="later test approval",
        fingerprint=later_fingerprint,
        paths=paths,
    ).record_user_gate_decision(
        approved=True,
        fingerprint=later_fingerprint,
        paths=paths,
        rationale="later approval is not active",
    ).record_active_test_approval(fingerprint, paths)
    unit = state.current_work_unit
    created_at = "2026-08-31T12:34:56+00:00"
    bridge = ArtifactBridge(
        ArtifactStore(tmp_path, state.run_id), now=lambda: created_at
    )
    _append_run_binding(bridge, state)
    bridge.append(
        WorkflowTransitionPayload(
            str(unit.slice_id),
            state.current_slice.status.value,
            str(unit.work_unit_id),
            unit.current_step.value,
            unit.status.value,
        ),
        logical_id="workflow-transition",
        idempotency_key="workflow-transition:1",
        fingerprint_sha256=state.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        ProductionWorkflowDriver._gate_transition_payload(unit),
        logical_id="gate-transition-1",
        idempotency_key="gate-transition:1:1",
        fingerprint_sha256=state.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    gate_record = bridge.append(
        GatePayload(
            "test-change", "approved", Role.USER, "reviewed exact test delta"
        ),
        logical_id="gate-test-change",
        idempotency_key="gate:test-change:approved",
        fingerprint_sha256=fingerprint,
    )
    bridge.append(
        GateDecisionPayload(
            str(unit.work_unit_id), gate_record.record_id, paths, None
        ),
        logical_id="gate-decision-1",
        idempotency_key="gate-decision:1",
        fingerprint_sha256=fingerprint,
    )
    later_gate_record = bridge.append(
        GatePayload(
            "test-change", "approved", Role.USER, "later approval is not active"
        ),
        logical_id="gate-test-change-later",
        idempotency_key="gate:test-change:approved:later",
        fingerprint_sha256=later_fingerprint,
    )
    bridge.append(
        GateDecisionPayload(
            str(unit.work_unit_id), later_gate_record.record_id, paths, None
        ),
        logical_id="gate-decision-1-later",
        idempotency_key="gate-decision:1:later",
        fingerprint_sha256=later_fingerprint,
    )
    replay = replay_artifacts(bridge.store.load_chain(), state.run_id)

    from readable_audit import AuditFacts, render_overall
    rendered = render_overall(
        AuditFacts(replay, read_blob=bridge.store.read_blob, read_plan=lambda _commit, _path: ""),
        task="gate task", branch=state.branch,
    )
    assert "Entscheidung: freigegeben. Begründung: reviewed exact test delta" in rendered  # allowlist:german
    assert "Test-Diff-Fingerprint" not in rendered
    assert created_at not in rendered


def test_r5_gate_pending_decision_and_resume_records_precede_state_readers(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/r5-gate-order")
    task = repository / "task.md"
    task.write_text("gate task", encoding="utf-8")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="r5-gate-order",
        task_file=str(task),
        branch="feature/r5-gate-order",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="d" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/r5-gate-order",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    history = WorkflowHistory(state.current_work_unit_id)
    driver.checkpoint(state, history)
    pending = state.await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="exact runtime path requires approval",
        fingerprint="e" * 64,
        paths=("src/runtime.py",),
        resume_step=WorkflowStep.CODEX_PLAN,
    )
    driver.checkpoint(pending, history)

    result = WorkflowEngine(driver).decide_current_gate(
        pending,
        history,
        approved=True,
        rationale="reviewed exact runtime path",
    )

    replay = resolve_resume_state(repository, result.state).replay_result
    assert replay is not None
    transitions = tuple(
        record
        for record in replay.records
        if record.record_type is RecordType.GATE_TRANSITION
        and record.payload.work_unit_id == "1"
    )
    assert tuple(record.payload.gate_status for record in transitions) == (
        "clear",
        "awaiting_user_decision",
        "clear",
    )
    gate_record = next(
        record for record in replay.records
        if isinstance(record.payload, GatePayload)
    )
    decision_record = next(
        record for record in replay.records
        if isinstance(record.payload, GateDecisionPayload)
    )
    assert replay.records.index(gate_record) < replay.records.index(decision_record)
    assert replay.records.index(decision_record) < replay.records.index(transitions[-1])
    assert replay.gate_decisions[0].paths == ("src/runtime.py",)
    assert replay.gate_decisions[0].resume_step == WorkflowStep.CODEX_PLAN.value
    assert result.state.current_work_unit.gate_decisions[0].paths == (
        "src/runtime.py",
    )
    assert result.state.current_work_unit.gate_decisions[0].resume_step is WorkflowStep.CODEX_PLAN


def test_quota_resume_diff_approval_record_binds_timeout_invocation_and_time(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/quota-resume-approval")
    task = repository / "task.md"
    task.write_text("resume task", encoding="utf-8")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="quota-resume-approval",
        task_file=str(task),
        branch="feature/quota-resume-approval",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="d" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/quota-resume-approval",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    history = WorkflowHistory(state.current_work_unit_id)
    driver.checkpoint(state, history)
    invocation_id = "timeout-invocation-17"
    failure = AgentInvocationError(
        agent_key="codex",
        kind=AgentFailureKind.TIMEOUT,
        invocation_id=invocation_id,
        provider_text="codex timed out",
        received_at=datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc),
    )
    halted, _ = WorkflowEngine(driver)._persist_invocation_failure(
        state,
        history,
        WorkflowContext(
            "assignment",
            "plan",
            "slice",
            transient_retry_policy=TransientRetryPolicy(automatic=False),
        ),
        AgentRole.CODEX,
        failure,
    )
    fingerprint = "e" * 64
    paths = ("src/runtime.py",)
    pending = halted.resume_after_invocation_halt().await_user_gate(
        reason=GateReason.QUOTA_RESUME_DIFF,
        detail=(
            "QUOTA-RESUME-DIFF | repository changed while the role was waiting; "
            f"paths={paths[0]}; continue with --resume --approve-gate"
        ),
        fingerprint=fingerprint,
        paths=paths,
        resume_step=WorkflowStep.CODEX_PLAN,
    )
    driver.checkpoint(pending, history)

    before = ArtifactStore(repository, state.run_id).load_chain()
    assert pending.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert not any(isinstance(record.payload, GatePayload) for record in before)

    result = WorkflowEngine(driver).decide_current_gate(
        pending,
        history,
        approved=True,
        rationale="reviewed the exact changed path",
    )

    chain = ArtifactStore(repository, state.run_id).load_chain()
    decision_record = next(
        record
        for record in chain
        if isinstance(record.payload, GateDecisionPayload)
    )
    assert decision_record.payload.invocation_id == invocation_id
    assert decision_record.fingerprint.sha256 == fingerprint
    assert datetime.fromisoformat(decision_record.created_at).tzinfo is not None
    assert result.state.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert (
        f"quota-resume-diff:{invocation_id}:{fingerprint}"
        in result.state.current_work_unit.completed_side_effects
    )


def test_r5_repeated_identical_rejection_remains_resume_safe(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "feature/r5-repeat-rejection")
    task = repository / "task.md"
    task.write_text("gate task", encoding="utf-8")
    head = _git(repository, "rev-parse", "HEAD")
    state = init_workflow_state(
        run_id="r5-repeat-rejection",
        task_file=str(task),
        branch="feature/r5-repeat-rejection",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest="d" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/r5-repeat-rejection",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    history = WorkflowHistory(state.current_work_unit_id)
    driver.checkpoint(state, history)
    pending = state.await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="exact runtime path requires approval",
        fingerprint="e" * 64,
        paths=("src/runtime.py",),
        resume_step=WorkflowStep.CODEX_PLAN,
    )
    driver.checkpoint(pending, history)

    first = WorkflowEngine(driver).decide_current_gate(
        pending,
        history,
        approved=False,
        rationale="path remains out of scope",
    )
    second = WorkflowEngine(driver).decide_current_gate(
        first.state,
        first.history,
        approved=False,
        rationale="path remains out of scope",
    )

    resolution = resolve_resume_state(repository, second.state)
    assert resolution.replay_result is not None
    assert len(second.state.current_work_unit.gate_decisions) == 2
    assert len(resolution.replay_result.gate_decisions) == 1
    assert resolution.replay_result.gate_decisions[0].approved is False


def test_runtime_does_not_reuse_gate_approval_for_changed_fingerprint() -> None:
    paths = ("src/agent_runtime.py", "tests/test_agent_runtime.py")
    state = orchestrator.init_workflow_state(
        run_id="resume-changed-user-gate",
        task_file="/repo/inbox/native-review.md",
        branch="feature/native-review",
        branch_base="b" * 40,
        first_slice_start_commit="b" * 40,
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
        rationale="first fingerprint reviewed",
    ).await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="changed repository fingerprint",
        fingerprint="c" * 64,
        paths=paths,
    )

    assert orchestrator._current_gate_approval(state) is None


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
    assert result.disposition is WatchTaskDisposition.RESUMABLE_HALT
    assert result.gate_reason == "GIT-TRANSACTION"
    assert result.step == "pipeline"
    assert result.resume_available is False
    assert not (repository / ".orchestrator" / "state.json").exists()


def test_legacy_watch_resume_is_rejected_before_branch_switch(
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
        workflow_production,
        "prepare_new_watch_task_branch",
        unexpected_prepare,
    )
    monkeypatch.chdir(repository)

    with pytest.raises(ArtifactResumeError, match="UNSUPPORTED-PROTOCOL"):
        run_production_workflow(task, args)

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
        ProtocolMode.STRUCTURED_V2, "2"
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


def test_force_new_discards_a_malformed_projection_cache(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/malformed-cache-source")
    _git(repository, "switch", "master")
    task = tmp_path / "malformed-cache-task.md"
    _write_task(task, "feature/malformed-cache-target", "src/new.py")
    state_file = repository / ".orchestrator" / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text('{"cache_format":"broken"}\n', encoding="utf-8")
    args = _args(repository, task)
    args.watch_run_id = "replacement-after-malformed-cache"
    captured = {}

    class StateCaptured(RuntimeError):
        pass

    def capture_state(_engine, state, _context, _history):
        captured["state"] = state
        raise StateCaptured

    monkeypatch.setattr(WorkflowEngine, "run_current_work_unit", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    assert captured["state"].run_id == "replacement-after-malformed-cache"
    loaded = orchestrator.load_workflow_state(
        state_file,
        allowed_roots=(repository, task.parent.resolve()),
    )
    assert loaded.run_id == "replacement-after-malformed-cache"


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
    monkeypatch.chdir(repository)

    result = run_pipeline(task, args, force_new=True)

    assert isinstance(result, WatchTaskResult)
    assert result.exit_code == 4
    assert result.disposition is WatchTaskDisposition.RESUMABLE_HALT
    assert result.gate_reason == "STATE-SCHEMA"
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


def test_branch_head_beyond_base_reaches_first_slice_implementation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path, "feature/measured-first-slice")
    branch_base = _git(repository, "rev-parse", "master")
    (repository / "prior.txt").write_text("prior branch work\n", encoding="utf-8")
    _git(repository, "add", "prior.txt")
    _git(repository, "commit", "-m", "prior branch work")
    measured_head = _git(repository, "rev-parse", "HEAD")
    task = tmp_path / "task.md"
    _write_task(
        task,
        "feature/measured-first-slice",
        "prior.txt",
        "src/one.py",
    )
    reached: dict[str, WorkflowState] = {}
    bound_records: list[ArtifactRecord] = []

    class ImplementationReached(RuntimeError):
        pass

    def codex(
        driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        if invocation.step is WorkflowStep.CODEX_PLAN:
            (repository / "src").mkdir()
            (repository / "src/one.py").write_text("value = 1\n", encoding="utf-8")
            return _native_plan_output(
                invocation,
                summary="add file",
                scope_paths=("prior.txt", "src/one.py"),
            )
        reached["state"] = driver.active_state
        bound_records.extend(
            ArtifactStore(repository, driver.active_state.run_id).load_chain()
        )
        raise ImplementationReached

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "invoke_reviewer",
        lambda _driver, invocation: _native_review_approval(invocation),
    )
    monkeypatch.chdir(repository)

    with pytest.raises(ImplementationReached):
        run_production_workflow(task, _args(repository, task))

    state = reached["state"]
    assert branch_base != measured_head
    assert state.branch_base == branch_base
    assert state.current_slice.start_commit == measured_head
    assert state.current_slice.scope_paths == ("prior.txt", "src/one.py")
    identity_record = next(
        record
        for record in bound_records
        if record.record_type is RecordType.RUN_IDENTITY
    )
    assert identity_record.idempotency_key == "run-identity"
    assert identity_record.payload.first_slice_start_commit == measured_head
    assert all(
        measured_head not in record.idempotency_key for record in bound_records
    )


def test_head_drift_after_plan_becomes_typed_persisted_halt(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/head-drift")
    task = tmp_path / "task.md"
    _write_task(task, "feature/head-drift", "src/one.py")

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        (repository / "src").mkdir()
        (repository / "src/one.py").write_text("value = 1\n", encoding="utf-8")
        _git(repository, "add", "src/one.py")
        _git(repository, "commit", "-m", "external drift")
        return _native_plan_output(
            invocation, summary="add file", scope_paths=("src/one.py",)
        )

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "invoke_reviewer",
        lambda _driver, invocation: _native_review_approval(invocation),
    )
    monkeypatch.chdir(repository)
    result = run_production_workflow(task, _args(repository, task))

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason.value == "unexpected_file"
    assert "SLICE-HEAD-DRIFT" in result.state.current_work_unit.gate.detail
    persisted = json.loads(
        (repository / ".orchestrator" / "state.json").read_text(encoding="utf-8")
    )
    assert persisted["state"]["work_units"][-1]["status"] == "awaiting_user_decision"


def test_empty_implementation_is_a_typed_halt_not_cli_crash(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/empty-slice")
    task = tmp_path / "task.md"
    _write_task(task, "feature/empty-slice", "src/one.py")

    def codex(
        driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        if invocation.step is WorkflowStep.CODEX_PLAN:
            return _native_plan_output(
                invocation, summary="add file", scope_paths=("src/one.py",)
            )
        return _native_implementation_output(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "invoke_reviewer",
        lambda _driver, invocation: _native_review_approval(invocation),
    )
    monkeypatch.chdir(repository)
    args = _args(repository, task)
    result = run_production_workflow(task, args)

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason.value == "stop_request"
    assert "NO-IMPLEMENTATION-CHANGES" in result.state.current_work_unit.gate.detail
    assert not any((repository / "inbox").glob("*followup*"))
    args.force_overwrite_state = True
    args.resume = False
    assert run_pipeline(task, args, force_new=True) == 4


def test_completed_implementation_runs_final_review_and_publishes_one_followup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path, "feature/implementation-final-review")
    task = tmp_path / "task.md"
    _write_task(
        task,
        "feature/implementation-final-review",
        "src/one.py",
    )

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        if invocation.step is WorkflowStep.CODEX_PLAN:
            target = repository / "src" / "one.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("value = 0\n", encoding="utf-8")
            return _native_plan_output(
                invocation,
                summary="add implementation",
                scope_paths=("src/one.py",),
            )
        target = repository / "src" / "one.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("value = 1\n", encoding="utf-8")
        return _native_implementation_output(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    def review(
        driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        if invocation.step is WorkflowStep.CLAUDE_FINAL_REVIEW:
            return _native_final_review_output(driver, invocation, finding_id="C-01")
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", review)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.workflow_completed
    assert result.state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
    chain = ArtifactStore(repository, result.state.run_id).load_chain()
    final_reviews = tuple(
        record for record in chain
        if isinstance(record.payload, FinalReviewCompletedPayload)
    )
    completions = tuple(
        record for record in chain
        if isinstance(record.payload, WorkflowCompletionPayload)
    )
    assert len(final_reviews) == len(completions) == 1

    followup = repository / "inbox" / "task_followup01.md"
    assert followup.is_file()
    followup_text = followup.read_text(encoding="utf-8")
    assert "The completed branch still needs remediation." in followup_text
    assert "`src/one.py`" in followup_text
    assert "C-01" not in followup_text
    assert result.state.run_id not in followup_text
    assert "ACCEPTANCE_REVIEW_NUMBER: 2" in followup_text
    file_results = tuple(
        record.payload
        for record in chain
        if isinstance(record.payload, SideEffectPayload)
        and record.payload.effect_class == "file_write"
        and record.payload.phase == "result"
    )
    assert sum(
        payload.operation[0] == "inbox/task_followup01.md"
        for payload in file_results
    ) == 1

    assert (repository / ".orchestrator" / "state.json").is_file()
    resume_args = _args(repository, task)
    resume_args.resume = True
    resumed = run_production_workflow(task, resume_args)
    resumed_chain = ArtifactStore(repository, resumed.state.run_id).load_chain()
    assert resumed.workflow_completed
    assert sum(
        isinstance(record.payload, FinalReviewCompletedPayload)
        for record in resumed_chain
    ) == 1
    assert sum(
        isinstance(record.payload, WorkflowCompletionPayload)
        for record in resumed_chain
    ) == 1
    assert list(followup.parent.glob("*followup*")) == [followup]


def test_quota_pause_replays_and_resumes_review_before_next_slice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path, "feature/quota-review-resume")
    task = tmp_path / "quota-review-resume.md"
    _write_task(task, "feature/quota-review-resume", "src/one.py", "src/two.py")
    calls: list[str] = []
    review_attempts = 0

    def implement(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        if invocation.step is WorkflowStep.CODEX_PLAN:
            calls.append("plan")
            target = repository / "src" / "one.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("value = 0\n", encoding="utf-8")
            return _native_plan_output(
                invocation,
                summary="complete first slice",
                scope_paths=("src/one.py",),
                second_scope_paths=("src/two.py",),
            )
        calls.append(f"implement-{invocation.work_unit_id - 1}")
        if invocation.work_unit_id == 3:
            raise agent_runtime.classify_agent_failure(
                AgentRole.CODEX.value,
                agent_runtime.AgentProcessError("usage limit", exit_code=1),
                invocation_id="second-slice-quota",
            )
        (repository / "src" / "one.py").write_text("value = 1\n", encoding="utf-8")
        return _native_implementation_output(invocation)

    def review(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        nonlocal review_attempts
        if invocation.step is WorkflowStep.CLAUDE_PLAN_REVIEW:
            calls.append("plan-review")
            return _native_review_approval(invocation)
        assert invocation.step is WorkflowStep.CLAUDE_SLICE_REVIEW
        review_attempts += 1
        calls.append(f"review-{invocation.work_unit_id - 1}-{review_attempts}")
        if review_attempts == 1:
            raise agent_runtime.classify_agent_failure(
                AgentRole.CLAUDE.value,
                agent_runtime.AgentProcessError("usage limit", exit_code=1),
                invocation_id="first-slice-review-quota",
            )
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", implement)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", review)
    monkeypatch.chdir(repository)

    first = run_production_workflow(task, _args(repository, task))

    assert first.state.current_work_unit_id == 2
    assert first.state.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    assert first.state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert first.state.current_work_unit.gate.reason is GateReason.QUOTA
    assert first.state.current_slice.status is SliceStatus.AWAITING_RESUME
    assert not first.workflow_rejected
    pause = WatchTaskResult.from_workflow(first)
    assert pause.disposition is WatchTaskDisposition.RESUMABLE_HALT
    assert pause.resume_available and pause.step == WorkflowStep.CLAUDE_SLICE_REVIEW.value
    assert pause.work_unit_id == 2
    assert calls == ["plan", "plan-review", "implement-1", "review-1-1"]
    replayed = resolve_resume_state(repository, first.state.run_id).state
    assert replayed.current_work_unit_id == 2
    assert replayed.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert replayed.current_work_unit.gate.reason is GateReason.QUOTA

    resumed_args = _args(repository, task)
    resumed_args.resume = True
    resumed = run_production_workflow(task, resumed_args)

    assert calls == [
        "plan", "plan-review", "implement-1", "review-1-1",
        "review-1-2", "implement-2",
    ]
    assert resumed.state.work_units[1].status is WorkUnitStatus.COMPLETED
    assert resumed.state.slices[0].status is SliceStatus.COMPLETED
    assert resumed.state.current_work_unit_id == 3
    assert resumed.state.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    assert resumed.state.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    chain = ArtifactStore(repository, first.state.run_id).load_chain()
    transitions = [
        record.payload for record in chain
        if isinstance(record.payload, WorkflowTransitionPayload)
    ]
    assert any(
        item.work_unit_id == "2" and item.step == "claude_slice_review"
        and item.work_unit_status == "awaiting_resume"
        for item in transitions
    )
    assert resolve_resume_state(repository, first.state.run_id).state == resumed.state


def test_sixth_acceptance_review_keeps_evidence_and_creates_no_followup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path, "feature/acceptance-limit")
    task = repository / "inbox" / "task.md"
    task.parent.mkdir()
    _write_task(task, "feature/acceptance-limit", "src/one.py")
    task.write_text(
        task.read_text(encoding="utf-8")
        + "\nACCEPTANCE_REVIEW_NUMBER: 6\n",
        encoding="utf-8",
    )
    audit_path = workflow_audit_projection._managed_audit_path(
        task, hashlib.sha256(task.read_bytes()).hexdigest()
    )

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        if invocation.step is WorkflowStep.CODEX_PLAN:
            target = repository / "src" / "one.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("value = 0\n", encoding="utf-8")
            return _native_plan_output(
                invocation,
                summary="add implementation",
                scope_paths=(audit_path, "src/one.py"),
            )
        target = repository / "src" / "one.py"
        target.write_text("value = 1\n", encoding="utf-8")
        return _native_implementation_output(invocation)

    def review(
        driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        if invocation.step is WorkflowStep.CLAUDE_FINAL_REVIEW:
            return _native_final_review_output(driver, invocation, finding_id="C-01")
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", review)
    monkeypatch.chdir(repository)

    with pytest.raises(
        AcceptanceReviewLimitReached, match="review=6 limit=6"
    ) as raised:
        run_production_workflow(task, _args(repository, task))

    persisted = json.loads(
        (repository / ".orchestrator" / "state.json").read_text(encoding="utf-8")
    )["state"]
    chain = ArtifactStore(repository, persisted["run_id"]).load_chain()
    assert sum(
        isinstance(record.payload, FinalReviewCompletedPayload)
        for record in chain
    ) == 1
    assert not any((repository / "inbox").glob("*followup*"))
    persisted_audit_path = repository / persisted["audit_report_path"]
    assert persisted_audit_path.is_file()
    assert "The completed branch still needs remediation." in persisted_audit_path.read_text(
        encoding="utf-8"
    )
    assert _git(repository, "show", "HEAD:src/one.py") == "value = 1"
    watch_result = WatchTaskResult.from_failure(
        classify_exception(raised.value),
        run_id=persisted["run_id"],
        records_written=True,
        protocol_mode="structured-v2",
    )
    assert watch_result.disposition is WatchTaskDisposition.REJECTED
    assert watch_result.exit_code == 5
    assert watch_result.resume_available is False



def test_internal_plan_validation_honors_exact_approved_hotfix_paths(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/approved-plan-hotfix")
    work_plan = "docs/internal/work-plan.md"
    plan = repository / work_plan
    plan.parent.mkdir(parents=True)
    plan.write_text(
        "# Work plan\n\n### Slice 1 - Future implementation\n\n"
        "**Exakter Änderungspfad**\n\n- `src/future.py`\n\n"
        "#### Akzeptanzkriterien\n\n- Future behavior is covered.\n",  # allowlist:german -- plan contract fixture
        encoding="utf-8",
    )
    hotfix_paths = ("src/orchestrator.py", "tests/test_orchestrator_runtime.py")
    fingerprint = "a" * 64
    state = init_workflow_state(
        run_id="run-approved-plan-hotfix",
        task_file="/repo/inbox/plan.md",
        branch="feature/approved-plan-hotfix",
        branch_base="b" * 40,
        first_slice_start_commit="b" * 40,
        slice_count=1,
        task_scope_patterns=(work_plan,),
    ).bind_slice_plan(
        (PlannedSlice(1, "create reviewed work plan", (work_plan,)),),
        first_start_commit="b" * 40,
    ).await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="reviewed bootstrap hotfix paths",
        fingerprint=fingerprint,
        paths=hotfix_paths,
    ).record_user_gate_decision(
        approved=True,
        fingerprint=fingerprint,
        paths=hotfix_paths,
        rationale="bootstrap hotfixes reviewed",
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = state
    changes = WorkflowChanges(
        start_commit="b" * 40,
        fingerprint=fingerprint,
        paths=(work_plan, *hotfix_paths),
        full_diff="approved bootstrap changes",
    )

    attestation = driver.validate_plan(
        changes,
        work_plan_path=work_plan,
        scope_patterns=(work_plan,),
        plan_only=True,
    )

    assert attestation.diff_fingerprint == fingerprint
    with pytest.raises(
        WorkflowExecutionError,
        match="out-of-scope planning changes",
    ):
        driver.validate_plan(
            replace(changes, fingerprint="c" * 64),
            work_plan_path=work_plan,
            scope_patterns=(work_plan,),
            plan_only=True,
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

    def codex(
        driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        codex_steps.append(invocation.step)
        plan = repository / "docs" / "internal" / "work-plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "# Work plan\n\n### Slice 1 - Future implementation\n\n"
            "**Exakter Änderungspfad**\n\n- `.orchestrator/forbidden.md`\n\n"
            "#### \u0041kzeptanzkriterien\n\n- Future behavior is covered.\n",
            encoding="utf-8",
        )
        return _native_plan_output(
            invocation,
            summary="create reviewed work plan",
            scope_paths=("docs/internal/work-plan.md",),
        )

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        reviewer_steps.append(invocation.step)
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.exit_code == 4
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert "PLAN-CONTRACT-INVALID" in result.state.current_work_unit.gate.detail
    assert "canonical repository-relative POSIX paths outside .orchestrator" in (
        result.state.current_work_unit.gate.detail
    )
    assert codex_steps == [WorkflowStep.CODEX_PLAN, WorkflowStep.CODEX_PLAN_REVISION]
    assert reviewer_steps == []
    assert _git(repository, "status", "--short") == "?? docs/"


def test_plan_only_repairs_missing_work_plan_before_review(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/repaired-missing-plan")
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/work-plan.md",
                "TARGET_BRANCH: feature/repaired-missing-plan",
                "TASK_SCOPE: docs/internal/work-plan.md",
            )
        ),
        encoding="utf-8",
    )
    codex_steps: list[WorkflowStep] = []
    reviewer_steps: list[WorkflowStep] = []

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        codex_steps.append(invocation.step)
        assert invocation.native_request is not None
        request_json = invocation.native_request.canonical_json
        assert (
            "Create or update the file docs/internal/work-plan.md in the repository "
            "now. Its complete content is the deliverable of this step."
            in request_json
        )
        assert "native JSON record is only a receipt" in request_json
        assert "one repository file you must write" in request_json
        assert "Plan the requested work." not in request_json
        if invocation.step is WorkflowStep.CODEX_PLAN_REVISION:
            assert "AUTOMATIC PLAN CONTRACT REPAIR" in request_json
            assert (
                "PLAN_ONLY Codex planning must create or update WORK_PLAN_PATH"
                in request_json
            )
            assert (
                "Create the missing file docs/internal/work-plan.md in the repository now"
                in request_json
            )
            assert "Repair only the declared work-plan artifact" not in request_json
            plan = repository / "docs" / "internal" / "work-plan.md"
            plan.parent.mkdir(parents=True, exist_ok=True)
            plan.write_text(
                "# Work plan\n\n### Slice 1 - Future implementation\n\n"
                "**Exakter Änderungspfad**\n\n- `src/future.py`\n\n"
                "#### \u0041kzeptanzkriterien\n\n- Future behavior is covered.\n",
                encoding="utf-8",
            )
        output = _native_plan_output(
            invocation,
            summary="create reviewed work plan",
            scope_paths=("docs/internal/work-plan.md",),
        )
        _driver.last_codex_output = output.canonical_json
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        reviewer_steps.append(invocation.step)
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "assert_structured_decision_context",
        lambda _driver: None,
    )
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.workflow_completed
    assert codex_steps == [WorkflowStep.CODEX_PLAN, WorkflowStep.CODEX_PLAN_REVISION]
    assert reviewer_steps == [WorkflowStep.CLAUDE_PLAN_REVIEW]
    assert task.with_name("task-implement.md").is_file()


def test_plan_only_missing_work_plan_halts_after_one_repair_before_review(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/missing-plan")
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/work-plan.md",
                "TARGET_BRANCH: feature/missing-plan",
                "TASK_SCOPE: docs/internal/work-plan.md",
            )
        ),
        encoding="utf-8",
    )
    codex_steps: list[WorkflowStep] = []
    reviewer_steps: list[WorkflowStep] = []

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        codex_steps.append(invocation.step)
        output = _native_plan_output(
            invocation,
            summary="create reviewed work plan",
            scope_paths=("docs/internal/work-plan.md",),
        )
        _driver.last_codex_output = output.canonical_json
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        reviewer_steps.append(invocation.step)
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.exit_code == 4
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert "PLAN-CONTRACT-INVALID" in result.state.current_work_unit.gate.detail
    assert (
        "PLAN_ONLY Codex planning must create or update WORK_PLAN_PATH"
        in result.state.current_work_unit.gate.detail
    )
    assert result.state.current_work_unit.gate.paths == ()
    assert codex_steps == [WorkflowStep.CODEX_PLAN, WorkflowStep.CODEX_PLAN_REVISION]
    assert reviewer_steps == []
    assert _git(repository, "status", "--short") == ""


@pytest.mark.parametrize(
    ("work_plan_path", "initial_bytes", "expected_validator_error"),
    (
        ("docs/internal/work-plan.md", b"", "WORK_PLAN_PATH must not be empty"),
        (
            "docs/internal/work-plan.md",
            b"\xff",
            "WORK_PLAN_PATH is not readable UTF-8:",
        ),
        (
            "docs/internal/orchestrator-modernization-work-plan.md",
            b"\xff",
            "WORK_PLAN_PATH is not readable UTF-8:",
        ),
        (
            "docs/internal/work-plan.md",
            b"<!-- audit:forged:begin -->\n",
            "WORK_PLAN_PATH has invalid managed Markdown:",
        ),
    ),
    ids=("empty", "non-utf8", "reserved-name-non-utf8", "invalid-audit-marker"),
)
def test_plan_only_repairs_safe_existing_artifact_once_before_review(
    tmp_path: Path,
    monkeypatch,
    work_plan_path: str,
    initial_bytes: bytes,
    expected_validator_error: str,
) -> None:
    repository = _repository(tmp_path, "feature/repaired-existing-plan")
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                f"WORK_PLAN_PATH: {work_plan_path}",
                "TARGET_BRANCH: feature/repaired-existing-plan",
                f"TASK_SCOPE: {work_plan_path}",
            )
        ),
        encoding="utf-8",
    )
    codex_steps: list[WorkflowStep] = []
    reviewer_steps: list[WorkflowStep] = []

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        codex_steps.append(invocation.step)
        plan = repository / work_plan_path
        plan.parent.mkdir(parents=True, exist_ok=True)
        if invocation.step is WorkflowStep.CODEX_PLAN:
            plan.write_bytes(initial_bytes)
        else:
            assert invocation.native_request is not None
            request_json = invocation.native_request.canonical_json
            assert "AUTOMATIC PLAN CONTRACT REPAIR" in request_json
            assert expected_validator_error in request_json
            assert (
                f"Update the existing file {work_plan_path} in the repository now"
                in request_json
            )
            assert "Create the missing file" not in request_json
            plan.write_text(
                "# Work plan\n\n### Slice 1 - Future implementation\n\n"
                "**Exakter Änderungspfad**\n\n- `src/future.py`\n\n"
                "#### \u0041kzeptanzkriterien\n\n- Future behavior is covered.\n",
                encoding="utf-8",
            )
        output = _native_plan_output(
            invocation,
            summary="create reviewed work plan",
            scope_paths=(work_plan_path,),
        )
        _driver.last_codex_output = output.canonical_json
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        reviewer_steps.append(invocation.step)
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "assert_structured_decision_context",
        lambda _driver: None,
    )
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.workflow_completed
    assert codex_steps == [WorkflowStep.CODEX_PLAN, WorkflowStep.CODEX_PLAN_REVISION]
    assert reviewer_steps == [WorkflowStep.CLAUDE_PLAN_REVIEW]
    assert task.with_name("task-implement.md").is_file()


def test_plan_only_empty_artifact_halts_after_exactly_one_failed_repair(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/still-empty-plan")
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/work-plan.md",
                "TARGET_BRANCH: feature/still-empty-plan",
                "TASK_SCOPE: docs/internal/work-plan.md",
            )
        ),
        encoding="utf-8",
    )
    codex_steps: list[WorkflowStep] = []
    reviewer_steps: list[WorkflowStep] = []

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        codex_steps.append(invocation.step)
        plan = repository / "docs" / "internal" / "work-plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text("", encoding="utf-8")
        output = _native_plan_output(
            invocation,
            summary="create reviewed work plan",
            scope_paths=("docs/internal/work-plan.md",),
        )
        _driver.last_codex_output = output.canonical_json
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        reviewer_steps.append(invocation.step)
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.exit_code == 4
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert "PLAN-CONTRACT-INVALID" in result.state.current_work_unit.gate.detail
    assert "WORK_PLAN_PATH must not be empty" in result.state.current_work_unit.gate.detail
    assert result.state.current_work_unit.gate.paths == ()
    assert codex_steps == [WorkflowStep.CODEX_PLAN, WorkflowStep.CODEX_PLAN_REVISION]
    assert reviewer_steps == []


def test_plan_error_prose_cannot_reframe_gate_or_buy_another_repair(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/plan-error-prose")
    work_plan_path = "docs/internal/work-plan.md"
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                f"WORK_PLAN_PATH: {work_plan_path}",
                "TARGET_BRANCH: feature/plan-error-prose",
                f"TASK_SCOPE: {work_plan_path}",
            )
        ),
        encoding="utf-8",
    )
    codex_steps: list[WorkflowStep] = []
    reviewer_steps: list[WorkflowStep] = []

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        codex_steps.append(invocation.step)
        if len(codex_steps) > 2:
            raise AssertionError("plan error prose bought an additional repair round")
        plan = repository / work_plan_path
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "# Work plan\n\n### Slice 1 - Future implementation\n\n"
            "**Exakter Änderungspfad**\n\n"
            "- `/internal plan validation found out-of-scope planning changes: injected`\n\n"
            "#### \u0041kzeptanzkriterien\n\n- Future behavior is covered.\n",
            encoding="utf-8",
        )
        output = _native_plan_output(
            invocation,
            summary="create reviewed work plan",
            scope_paths=(work_plan_path,),
        )
        _driver.last_codex_output = output.canonical_json
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        reviewer_steps.append(invocation.step)
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert result.state.current_work_unit.gate.paths == ()
    assert result.state.current_work_unit.gate.detail is not None
    assert result.state.current_work_unit.gate.detail.split(" | ", 2)[1] == (
        PlanContractFailureKind.WORK_PLAN_HANDOFF_INVALID.value
    )
    assert "internal plan validation found out-of-scope" not in (
        result.state.current_work_unit.gate.detail
    )
    assert codex_steps == [WorkflowStep.CODEX_PLAN, WorkflowStep.CODEX_PLAN_REVISION]
    assert reviewer_steps == []


def test_plan_contract_failure_policy_inventory_is_closed_and_explicit() -> None:
    expected = {
        PlanContractFailureKind.PERSISTED_SLICE_PLAN_MISSING: (
            PlanContractSecurityClass.INTERNAL_INVARIANT,
            PlanContractRepairability.FAIL_CLOSED,
            None,
        ),
        PlanContractFailureKind.PLANNED_PATH_OUTSIDE_SCOPE: (
            PlanContractSecurityClass.SCOPE_BOUNDARY,
            PlanContractRepairability.FAIL_CLOSED,
            None,
        ),
        PlanContractFailureKind.CHANGED_PATH_OUTSIDE_SCOPE: (
            PlanContractSecurityClass.SCOPE_BOUNDARY,
            PlanContractRepairability.FAIL_CLOSED,
            None,
        ),
        PlanContractFailureKind.PLAN_ARTIFACT_SLICE_COUNT_INVALID: (
            PlanContractSecurityClass.INTERNAL_INVARIANT,
            PlanContractRepairability.FAIL_CLOSED,
            None,
        ),
        PlanContractFailureKind.WORK_PLAN_PATH_NOT_PLANNED: (
            PlanContractSecurityClass.INTERNAL_INVARIANT,
            PlanContractRepairability.FAIL_CLOSED,
            None,
        ),
        PlanContractFailureKind.WORK_PLAN_PATH_NOT_CHANGED: (
            PlanContractSecurityClass.SAFE_REPOSITORY_ARTIFACT,
            PlanContractRepairability.REPAIR_ONCE,
            PlanContractRepairAction.CREATE_MISSING_FILE,
        ),
        PlanContractFailureKind.WORK_PLAN_PATH_UNSAFE_RESOLUTION: (
            PlanContractSecurityClass.PATH_BOUNDARY,
            PlanContractRepairability.FAIL_CLOSED,
            None,
        ),
        PlanContractFailureKind.WORK_PLAN_PATH_NOT_REGULAR: (
            PlanContractSecurityClass.PATH_BOUNDARY,
            PlanContractRepairability.FAIL_CLOSED,
            None,
        ),
        PlanContractFailureKind.WORK_PLAN_PATH_NOT_UTF8: (
            PlanContractSecurityClass.SAFE_REPOSITORY_ARTIFACT,
            PlanContractRepairability.REPAIR_ONCE,
            PlanContractRepairAction.UPDATE_EXISTING_FILE,
        ),
        PlanContractFailureKind.WORK_PLAN_SEMANTIC_MARKDOWN_INVALID: (
            PlanContractSecurityClass.SAFE_REPOSITORY_ARTIFACT,
            PlanContractRepairability.REPAIR_ONCE,
            PlanContractRepairAction.UPDATE_EXISTING_FILE,
        ),
        PlanContractFailureKind.WORK_PLAN_PATH_EMPTY: (
            PlanContractSecurityClass.SAFE_REPOSITORY_ARTIFACT,
            PlanContractRepairability.REPAIR_ONCE,
            PlanContractRepairAction.UPDATE_EXISTING_FILE,
        ),
        PlanContractFailureKind.WORK_PLAN_HANDOFF_INVALID: (
            PlanContractSecurityClass.HANDOFF_STRUCTURE,
            PlanContractRepairability.REPAIR_ONCE,
            PlanContractRepairAction.UPDATE_EXISTING_FILE,
        ),
    }

    assert set(PLAN_CONTRACT_FAILURE_POLICIES) == set(PlanContractFailureKind)
    assert {
        kind: (
            policy.security_class,
            policy.repairability,
            policy.repair_action,
        )
        for kind, policy in PLAN_CONTRACT_FAILURE_POLICIES.items()
    } == expected


@pytest.mark.skipif(not can_symlink(), reason="symlinks are unavailable")
@pytest.mark.parametrize(
    ("link_target", "expected_detail"),
    (
        ("../../../seed.txt", "WORK_PLAN_PATH must be a regular non-symlink file"),
        ("work-plan.md", "WORK_PLAN_PATH cannot be resolved safely:"),
    ),
    ids=("symlink", "unsafe-resolution"),
)
def test_plan_only_path_security_failures_never_invoke_repair_or_review(
    tmp_path: Path,
    monkeypatch,
    link_target: str,
    expected_detail: str,
) -> None:
    repository = _repository(tmp_path, "feature/unsafe-plan-path")
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/work-plan.md",
                "TARGET_BRANCH: feature/unsafe-plan-path",
                "TASK_SCOPE: docs/internal/work-plan.md",
            )
        ),
        encoding="utf-8",
    )
    codex_steps: list[WorkflowStep] = []
    reviewer_steps: list[WorkflowStep] = []

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        codex_steps.append(invocation.step)
        plan = repository / "docs" / "internal" / "work-plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.symlink_to(link_target)
        output = _native_plan_output(
            invocation,
            summary="create reviewed work plan",
            scope_paths=("docs/internal/work-plan.md",),
        )
        _driver.last_codex_output = output.canonical_json
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        reviewer_steps.append(invocation.step)
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert expected_detail in result.state.current_work_unit.gate.detail
    assert codex_steps == [WorkflowStep.CODEX_PLAN]
    assert reviewer_steps == []


def test_plan_only_scope_violation_never_invokes_repair_or_review(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/plan-scope-violation")
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/work-plan.md",
                "TARGET_BRANCH: feature/plan-scope-violation",
                "TASK_SCOPE: docs/internal/work-plan.md",
            )
        ),
        encoding="utf-8",
    )
    codex_steps: list[WorkflowStep] = []
    reviewer_steps: list[WorkflowStep] = []

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        codex_steps.append(invocation.step)
        plan = repository / "docs" / "internal" / "work-plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "# Work plan\n\n### Slice 1 - Future implementation\n\n"
            "**Exakter Änderungspfad**\n\n- `src/future.py`\n\n"
            "#### \u0041kzeptanzkriterien\n\n- Future behavior is covered.\n",
            encoding="utf-8",
        )
        (repository / "outside.txt").write_text("outside\n", encoding="utf-8")
        output = _native_plan_output(
            invocation,
            summary="create reviewed work plan",
            scope_paths=("docs/internal/work-plan.md",),
        )
        _driver.last_codex_output = output.canonical_json
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        reviewer_steps.append(invocation.step)
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason is GateReason.UNEXPECTED_FILE
    assert result.state.current_work_unit.gate.paths == ("outside.txt",)
    assert codex_steps == [WorkflowStep.CODEX_PLAN]
    assert reviewer_steps == []


def test_plan_only_work_plan_path_escape_is_rejected_before_agents(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/plan-path-escape")
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: ../outside.md",
                "TARGET_BRANCH: feature/plan-path-escape",
                "TASK_SCOPE: docs/internal/**",
            )
        ),
        encoding="utf-8",
    )
    agent_steps: list[WorkflowStep] = []

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        agent_steps.append(invocation.step)
        raise AssertionError("Codex must not run for an escaping WORK_PLAN_PATH")

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        agent_steps.append(invocation.step)
        raise AssertionError("Claude must not run for an escaping WORK_PLAN_PATH")

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    with pytest.raises(
        TaskContractError,
        match="WORK_PLAN_PATH must be one exact repository-relative POSIX path",
    ):
        run_production_workflow(task, _args(repository, task))

    assert agent_steps == []


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
            body = (
                "WORK_PLAN_PATH must not be empty\n\n"
                "No exact path section.\n"
            )
        else:
            assert invocation.native_request is not None
            request_json = invocation.native_request.canonical_json
            assert "AUTOMATIC PLAN CONTRACT REPAIR" in request_json
            assert (
                "Validator error: WORK_PLAN_PATH cannot produce an IMPLEMENT handoff:"
                in request_json
            )
            assert "Slice 1 has no exact change-path section" in request_json
            assert "path as a bullet with the path enclosed in backticks" in request_json
            assert "standalone line `**Akzeptanzkriterien**`" in request_json  # allowlist:german -- canonical plan contract
            assert (
                "Update the existing file docs/internal/work-plan.md in the repository now"
                in request_json
            )
            assert "Create the missing file" not in request_json
            body = (
                "**Exakter Änderungspfad**\n\n- `src/future.py`\n\n"
                "#### \u0041kzeptanzkriterien\n\n- Future behavior is covered.\n"
            )
        plan.write_text(
            "# Work plan\n\n### Slice 1 - Future implementation\n\n" + body,
            encoding="utf-8",
        )
        return _native_plan_output(
            invocation,
            summary="create reviewed work plan",
            scope_paths=("docs/internal/work-plan.md",),
        )

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        reviewer_steps.append(invocation.step)
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "assert_structured_decision_context",
        lambda _driver: None,
    )
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.workflow_completed
    assert codex_steps == [WorkflowStep.CODEX_PLAN, WorkflowStep.CODEX_PLAN_REVISION]
    assert reviewer_steps == [WorkflowStep.CLAUDE_PLAN_REVIEW]
    assert task.with_name("task-implement.md").is_file()






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

    def codex(
        driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        agent_steps.append(invocation.step)
        plan = repository / "docs" / "internal" / "resume.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "# Resume plan\n\n### Slice 1 – Implement resume\n\n"
            "**Exakter Änderungspfad**\n\n- `src/resume.py`\n\n"
            "#### \u0041kzeptanzkriterien\n\n- Resume behavior is covered.\n",
            encoding="utf-8",
        )
        return _native_plan_output(
            invocation,
            summary="create resume plan",
            scope_paths=("docs/internal/resume.md",),
        )

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        agent_steps.append(invocation.step)
        return _native_review_approval(invocation)

    real_handoff = workflow_production.write_implementation_handoff
    handoff_calls = 0

    def fail_once(**kwargs):
        nonlocal handoff_calls
        handoff_calls += 1
        if handoff_calls == 1:
            raise PlanHandoffError("simulated post-commit handoff failure")
        return real_handoff(**kwargs)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "assert_structured_decision_context",
        lambda _driver: None,
    )
    monkeypatch.setattr(workflow_production, "write_implementation_handoff", fail_once)
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



def test_explicit_resume_of_watch_origin_runs_terminal_workflow_once_then_finalizes_queue(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "runtime-resume.md"
    task.write_text("runtime-bound resume", encoding="utf-8")
    digest = hashlib.sha256(task.read_bytes()).hexdigest()
    identity = WatchTaskIdentity(
        "watch-runtime-resume", digest, True, "structured-v2", 2
    )
    save_watch_identity(task, identity)
    terminal, _, _ = orchestrator.run_default_dry_run(task, run_id=identity.run_id)
    terminal = WorkflowRunResult(
        replace(terminal.state, task_digest=digest), terminal.history
    )
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

    assert run_pipeline(task, args) == 0
    assert calls == {"workflow": 1}
    moved = list((outbox / "done").glob("*.md"))
    assert len(moved) == 1
    assert moved[0].read_text(encoding="utf-8") == "runtime-bound resume"
    assert not task.exists()
    assert not success_marker_path(task).exists()
    assert not watch_identity_path(task).exists()


def test_explicit_resume_with_bound_success_and_corrupt_state_returns_one(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    repository = _repository(tmp_path, "feature/bound-corrupt-state")
    inbox = repository / "inbox"
    outbox = repository / "outbox"
    inbox.mkdir()
    task = inbox / "bound-corrupt-state.md"
    task.write_text("bound payload", encoding="utf-8")
    digest = hashlib.sha256(task.read_bytes()).hexdigest()
    identity = WatchTaskIdentity(
        "watch-bound-corrupt-state", digest, True, "structured-v2", 2
    )
    save_watch_identity(task, identity)

    def interrupt_move(*_args, **_kwargs):
        raise OSError("leave bound success evidence pending")

    monkeypatch.setattr("inbox_watcher.move_to_reserved_outbox", interrupt_move)
    published = finalize_queue_success(
        task,
        inbox_dir=inbox,
        outbox_dir=outbox,
        run_id=identity.run_id,
        task_digest=identity.task_digest,
        publish=True,
    )
    assert published.disposition is QueueFinalizationDisposition.FAILED
    assert success_marker_path(task).is_file()

    state_path = repository / ".orchestrator" / "state.json"
    state_path.parent.mkdir(exist_ok=True)
    state_path.write_text("{not-json", encoding="utf-8")
    args = parse_args(
        [
            "--resume", "--task-file", str(task),
            "--inbox-dir", str(inbox), "--outbox-dir", str(outbox),
        ],
        cwd=repository,
        environ={},
    )
    monkeypatch.chdir(repository)

    with caplog.at_level("ERROR"):
        result = run_pipeline(task, args)

    assert result == 1
    assert "Direct queue recovery rejected" in caplog.text
