from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import plan_handoff
import workflow_requests
import native_finding_decisions

from agent_adapters import AgentOutputError
from audit_trail import ReviewAuditEvent
from artifact_models import InvocationFailurePayload, provider_text_evidence
from finding_convergence import SliceConvergenceEvaluation, SliceReviewPhase
from agent_runtime import (
    AgentInvocationError,
    AgentProcessError,
    NativeAgentCodexOutput,
    NativeAgentReviewOutput,
    ProviderRequestRoundRequired,
    QuotaReset,
    QuotaWaitPolicy,
    RecoveredFindingComparison,
    TransientRetryPolicy,
    classify_agent_failure,
)

from contracts import (
    AgentRole,
    AnchorRecord,
    ApprovalMarker,
    CodexContractResult,
    CodexStepContract,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingResponse,
    FindingResponseDecision,
    FindingStatus,
    PlannedSlice,
    ReadinessMarker,
    StepContract,
    ContractResult,
    FindingAcceptanceMeasurement,
    ReviewEvidence,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
    StopRequest,
    apply_finding_response,
    apply_reviewer_finding_update,
)
from gates import (
    OPERATOR_PREREQUISITE_MISSING_RULE_ID,
    PathClasses,
    StopRule,
    TestChangeEvidence as GateTestChangeEvidence,
)
from finding_reducer import project_open_set
from native_codex_contract import (
    NativeCodexContractError,
    NativeCodexErrorCode,
    NativeCodexRequestKind,
)
from native_review_contract import (
    DISCOVERY_OUTPUT_LIMIT_RULE_ID,
    NativeReviewContext,
    NativeReviewContractError,
    NativeReviewDispositionLimit,
    NativeReviewErrorCode,
)
from orchestrator_diagnostics import OrchestratorDiagnostic
from inbox_watcher import WatchTaskDisposition, WatchTaskResult
from orchestrator import run_v3_work_unit
from review_packets import ReviewPacket
from validation_matrix import ValidationCommand, ValidationMatrix, ValidationRequest, ValidationRule
from workflow import (
    CodexInvocation,
    EvidenceKind,
    PersistedNativeReviewerReplay,
    ReviewerInvocation,
    WorkflowChanges,
    WorkflowCommitRequest,
    WorkflowCommitApprovalRequired,
    WorkflowContext,
    WorkflowContractError,
    WorkflowEngine,
    WorkflowExecutionError,
    WorkflowHistory,
    WorkflowRunResult,
    ValidationExecutionError,
    _merge_request_finding_subset,
    _review_round_number,
    resolve_retired_iteration_limit,
)
from workflow_state import (
    AgentFailureKind,
    GateRecord,
    GateReason,
    GateStatus,
    SliceStatus,
    WorkflowStep,
    WorkflowState,
    WorkUnitKind,
    WorkUnitStatus,
    ProtocolBinding,
    ProtocolMode,
    Reviewer,
    init_workflow_state,
)


TEST_FILE = "tests/test_workflow.py"
START_COMMIT = "a" * 40


def test_request_recomposition_does_not_advance_reviewer_round() -> None:
    unit = SimpleNamespace(
        round_number=1,
        request_sequence=2,
        invocation_failures=(SimpleNamespace(role=AgentRole.CODEX.value),),
    )

    assert _review_round_number(unit, WorkflowHistory(3), AgentRole.CLAUDE) == 1


def _test_native_codex_output(
    invocation: CodexInvocation, text: str
) -> NativeAgentCodexOutput:
    assert invocation.native_request is not None
    findings = list(invocation.previous_findings)
    for finding_id, decision, rationale in re.findall(
        r"^FINDING_RESPONSE: (C-\d+) \| (ACCEPTED|REJECTED) \| (.+)$",
        text,
        re.MULTILINE,
    ):
        index = next(i for i, item in enumerate(findings) if item.finding_id == finding_id)
        findings[index] = apply_finding_response(
            findings[index], FindingResponseDecision(decision), rationale
        )
    stop = re.search(r"^STOP_REQUESTED: ([^|]+) \| (.+)$", text, re.MULTILINE)
    remediation = re.search(r"^REMEDIATION_PATHS: (.+)$", text, re.MULTILINE)
    stop_request = (
        StopRequest(
            stop.group(1).strip(),
            stop.group(2).strip(),
            tuple(sorted(part.strip() for part in remediation.group(1).split(",")))
            if remediation
            else (),
        )
        if stop
        else None
    )
    ready_match = re.search(
        r"^(?:PLAN_READY:|IMPLEMENTATION_READY: \d+ \||FINAL_REPORT_READY:) (YES|NO)$",
        text,
        re.MULTILINE,
    )
    tests = re.search(r"^TEST_FILES_TOUCHED: (.+)$", text, re.MULTILINE)
    test_files = () if tests is None or tests.group(1) == "NONE" else tuple(
        sorted(part.strip() for part in tests.group(1).split(","))
    )
    slices = tuple(
        PlannedSlice(
            int(slice_id),
            summary.strip(),
            tuple(sorted(part.strip() for part in paths.split(","))),
        )
        for slice_id, summary, paths in re.findall(
            r"^SLICE_PLAN: (\d+) \| ([^|]+) \| (.+)$", text, re.MULTILINE
        )
    )
    result = CodexContractResult(
        ready=None if stop else bool(ready_match and ready_match.group(1) == "YES"),
        stopped=stop is not None,
        stop_request=stop_request,
        validation=None,
        test_files=test_files,
        findings=tuple(findings),
        slice_plan=slices,
    )
    request_id = invocation.native_request.bound_context.request_id
    canonical = json.dumps({"request_id": request_id}, sort_keys=True)
    return NativeAgentCodexOutput(
        result, canonical, request_id, hashlib.sha256(canonical.encode()).hexdigest()
    )


def _test_native_review_output(
    invocation: ReviewerInvocation, text: str
) -> NativeAgentReviewOutput:
    assert invocation.native_request is not None
    context = invocation.native_request.bound_context.context
    findings = list(invocation.previous_findings)
    for finding_id, kind, summary, acceptance in re.findall(
        r"^NEW_FINDING: (C-\d+) \| (BLOCKER|OBSERVATION) \| ([^|]+) \| (.+)$",
        text,
        re.MULTILINE,
    ):
        findings.append(
            FindingRecord(
                finding_id,
                FindingClass(kind),
                FindingStatus.OPEN,
                summary.strip(),
                acceptance.strip(),
                FindingOrigin(context.slice_id, context.round_number, AgentRole.CLAUDE),
            )
        )
    for finding_id, status, rationale in re.findall(
        r"^FINDING_STATUS: (C-\d+) \| (OPEN|CLOSED) \| (.+)$",
        text,
        re.MULTILINE,
    ):
        index = next(i for i, item in enumerate(findings) if item.finding_id == finding_id)
        findings[index] = apply_reviewer_finding_update(
            findings[index], reviewer=AgentRole.CLAUDE,
            status=FindingStatus(status), rationale=rationale,
        )
    stop = re.search(r"^STOP_REQUESTED: ([^|]+) \| (.+)$", text, re.MULTILINE)
    approval = re.search(
        r"^(?:PLAN_APPROVAL:|SLICE_APPROVAL: \d+ \||FINAL_APPROVAL:) (YES|NO)$",
        text,
        re.MULTILINE,
    )
    evidence_match = re.search(r"^REVIEW_EVIDENCE: ([^|]+) \| ([^|]+) \| (.+)$", text, re.MULTILINE)
    evidence = ReviewEvidence(*(part.strip() for part in evidence_match.groups())) if evidence_match else None
    pre_mortem = re.search(r"^PRE_MORTEM: (.+)$", text, re.MULTILINE)
    result = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=None if stop else bool(approval and approval.group(1) == "YES"),
        stopped=stop is not None,
        stop_request=StopRequest(stop.group(1).strip(), stop.group(2).strip()) if stop else None,
        validation=context.validation_attestation,
        test_files=context.test_files,
        pre_mortem=pre_mortem.group(1) if pre_mortem else None,
        evidence=evidence,
        findings=tuple(findings),
        anchors=(),
    )
    request_id = invocation.native_request.bound_context.request_id
    return NativeAgentReviewOutput(result, json.dumps({"request_id": request_id}), request_id)


def _changes(
    token: str,
    *paths: str,
    full_diff: str | None = None,
    start_commit: str = START_COMMIT,
) -> WorkflowChanges:
    return WorkflowChanges(
        start_commit=start_commit,
        fingerprint=token * 64,
        paths=tuple(sorted(paths)),
        full_diff=full_diff or "".join(
            f"diff --git a/{path} b/{path}\n"
            "index 1111111..2222222 100644\n"
            f"--- a/{path}\n+++ b/{path}\n"
            "@@ -1 +1 @@\n-old\n+new\n"
            for path in paths
        ),
    )


def _attestation(changes: WorkflowChanges) -> ValidationAttestation:
    command = "python3 -m pytest tests/ -v"
    return ValidationAttestation(
        attestation_id=f"validation-{changes.fingerprint[:12]}",
        diff_fingerprint=changes.fingerprint,
        expected_commands=(command,),
        records=(ValidationRecord(ValidationStatus.PASS, command, 0),),
        output_digest="f" * 64,
        summary="tests passed",
    )


def _codex_ready(
    *finding_ids: str, plan: bool = False, slice_id: str = "01"
) -> str:
    lines = [
        *(f"FINDING_RESPONSE: {finding_id} | ACCEPTED | fixed with regression" for finding_id in finding_ids),
    ]
    if plan:
        lines.append("PLAN_READY: YES")
    else:
        lines.extend(
            (
                f"TEST_FILES_TOUCHED: {TEST_FILE}",
                f"IMPLEMENTATION_READY: {slice_id} | YES",
            )
        )
    lines.append("STATUS: DONE")
    return "\n".join(lines)


def _codex_stop(rule_id: str) -> str:
    return "\n".join(
        (
            f"STOP_REQUESTED: {rule_id} | domain semantics require user direction",
            "STATUS: DONE",
        )
    )


def _codex_not_ready(
    *finding_ids: str, plan: bool = False, slice_id: str = "01"
) -> str:
    lines = [
        *(
            f"FINDING_RESPONSE: {finding_id} | ACCEPTED | blocker remains unresolved"
            for finding_id in finding_ids
        ),
    ]
    if plan:
        lines.append("PLAN_READY: NO")
    else:
        lines.extend(
            (
                f"TEST_FILES_TOUCHED: {TEST_FILE}",
                f"IMPLEMENTATION_READY: {slice_id} | NO",
            )
        )
    lines.append("STATUS: DONE")
    return "\n".join(lines)


def _review_stop(role: AgentRole, rule_id: str) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"STOP_REQUESTED: {rule_id} | review cannot resolve the domain choice",
            "STATUS: DONE",
        )
    )


def _review_approval(
    role: AgentRole,
    *,
    finding_status: str | None = None,
    slice_id: str = "01",
) -> str:
    lines = [f"REVIEWER: {role.value}", f"TEST_FILES_TOUCHED: {TEST_FILE}"]
    if finding_status is None:
        lines.append(
            "REVIEW_EVIDENCE: reviewed invariants | residual concurrency risk | parallel mutation"
        )
    else:
        lines.append(finding_status)
    lines.extend(
        (
            "PRE_MORTEM: a future transition could bypass the role order",
            f"SLICE_APPROVAL: {slice_id} | YES",
            "STATUS: DONE",
        )
    )
    return "\n".join(lines)


def _review_denial(role: AgentRole, finding_id: str) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            f"NEW_FINDING: {finding_id} | BLOCKER | unsafe transition | add regression test",
            "SLICE_APPROVAL: 01 | NO",
            "STATUS: DONE",
        )
    )


def _review_keeps_open(role: AgentRole, finding_id: str) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            f"FINDING_STATUS: {finding_id} | OPEN | regression is still incomplete",
            "SLICE_APPROVAL: 01 | NO",
            "STATUS: DONE",
        )
    )


def _review_closes(role: AgentRole, finding_id: str) -> str:
    return _review_approval(
        role,
        finding_status=f"FINDING_STATUS: {finding_id} | CLOSED | regression proves the fix",
    )


def _final_report(*finding_ids: str) -> str:
    return "\n".join(
        (
            *(
                f"FINDING_RESPONSE: {finding_id} | ACCEPTED | addressed in correction"
                for finding_id in finding_ids
            ),
            "FINAL_REPORT_READY: YES",
            "STATUS: DONE",
        )
    )


def _final_approval(
    role: AgentRole, *, finding_status: str | None = None
) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            finding_status
            or "REVIEW_EVIDENCE: architecture and R-1..R-18 | provider drift | stale transition",
            "PRE_MORTEM: a later slice leaves an interface transition dead",
            "FINAL_APPROVAL: YES",
            "STATUS: DONE",
        )
    )


def _final_denial(role: AgentRole, finding_id: str) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            f"NEW_FINDING: {finding_id} | BLOCKER | branch interface drift | add branch regression",
            "FINAL_APPROVAL: NO",
            "STATUS: DONE",
        )
    )


@dataclass
class FakeDriver:
    snapshots: list[WorkflowChanges]
    codex_outputs: list[str]
    reviewer_outputs: list[str]
    codex_failures: list[AgentInvocationError | None] = field(default_factory=list)
    reviewer_failures: list[AgentInvocationError | None] = field(default_factory=list)
    commit_refs: list[str] = field(default_factory=list)
    commit_failure: WorkflowCommitApprovalRequired | None = None
    deltas: dict[tuple[str, str], str] = field(default_factory=dict)
    invalid_attestation: str | None = None
    fail_reviewer_once: bool = False
    validation_unavailable: str | None = None
    test_evidence_by_fingerprint: dict[str, GateTestChangeEvidence] = field(
        default_factory=dict
    )
    codex_calls: list[CodexInvocation] = field(default_factory=list)
    reviewer_calls: list[ReviewerInvocation] = field(default_factory=list)
    validation_calls: list[str] = field(default_factory=list)
    validation_requests: list[ValidationRequest] = field(default_factory=list)
    validation_recoveries: dict[
        tuple[str, tuple[str, ...], str], ValidationAttestation
    ] = field(default_factory=dict)
    validation_recovery_calls: list[
        tuple[str, tuple[str, ...], str]
    ] = field(default_factory=list)
    commit_calls: list[WorkflowCommitRequest] = field(default_factory=list)
    checkpoints: list = field(default_factory=list)
    checkpoint_histories: list = field(default_factory=list)
    failure_payloads: list[InvocationFailurePayload] = field(default_factory=list)
    failure_persistence_events: list[str] = field(default_factory=list)
    require_checkpointed_attestation: bool = False
    authoritative_finding_error: str | None = None
    authoritative_finding_calls: list[tuple[str, tuple[FindingRecord, ...]]] = field(
        default_factory=list
    )
    active_state: WorkflowState | None = None
    structured_events: list[tuple[str, object]] = field(default_factory=list)
    convergence_evaluations: list[SliceConvergenceEvaluation] = field(
        default_factory=list
    )
    convergence_calls: list[tuple[int, int]] = field(default_factory=list)
    snapshot_index: int = -1

    def bind_work_unit(self, state: WorkflowState) -> None:
        self.active_state = state

    def authoritative_native_findings(
        self,
        state: WorkflowState,
        mirror_findings: tuple[FindingRecord, ...],
    ) -> tuple[FindingRecord, ...]:
        self.authoritative_finding_calls.append((state.current_step.value, mirror_findings))
        if self.authoritative_finding_error is not None:
            raise WorkflowExecutionError(self.authoritative_finding_error)
        return mirror_findings

    def evaluate_slice_finding_convergence(
        self,
        state: WorkflowState,
        *,
        round_number: int,
    ) -> SliceConvergenceEvaluation:
        self.convergence_calls.append((state.current_work_unit_id, round_number))
        if not self.convergence_evaluations:
            raise AssertionError("unexpected Slice convergence evaluation")
        return self.convergence_evaluations.pop(0)

    def carry_forward_native_findings(
        self,
        _state: WorkflowState,
        current_findings: tuple[FindingRecord, ...],
    ) -> tuple[FindingRecord, ...]:
        return current_findings

    def _assert_checkpointed_attestation(self, fingerprint: str) -> None:
        if not self.require_checkpointed_attestation:
            return
        assert self.checkpoint_histories
        assert any(
            item.diff_fingerprint == fingerprint
            for item in self.checkpoint_histories[-1].attestations
        )

    def invoke_codex(self, invocation: CodexInvocation) -> NativeAgentCodexOutput:
        self.codex_calls.append(invocation)
        self.snapshot_index += 1
        if self.codex_failures:
            failure = self.codex_failures.pop(0)
            if failure is not None:
                raise failure
        output = self.codex_outputs.pop(0)
        return (
            output
            if isinstance(output, NativeAgentCodexOutput)
            else _test_native_codex_output(invocation, output)
        )

    def recover_pending_native_codex(
        self,
        invocation: CodexInvocation,
        contract: CodexStepContract,
        history: WorkflowHistory,
    ) -> NativeAgentCodexOutput | None:
        self.structured_events.append(
            ("recover-native-codex", (invocation, contract, history))
        )
        return None

    def collect_changes(self, start_commit: str) -> WorkflowChanges:
        index = max(self.snapshot_index, 0)
        snapshot = self.snapshots[index]
        if snapshot.start_commit == start_commit:
            return snapshot
        for candidate in self.snapshots[index + 1 :]:
            if candidate.start_commit == start_commit:
                return candidate
        raise AssertionError(
            f"no snapshot at or after index {index} starts at {start_commit}"
        )

    def collect_correction_delta(
        self, previous_fingerprint: str, current_fingerprint: str
    ) -> str:
        return self.deltas[(previous_fingerprint, current_fingerprint)]

    def detect_test_changes(
        self, changes: WorkflowChanges, patterns: tuple[str, ...]
    ) -> GateTestChangeEvidence | None:
        assert patterns
        return self.test_evidence_by_fingerprint.get(changes.fingerprint)

    def validate(
        self, changes: WorkflowChanges, request: ValidationRequest
    ) -> ValidationAttestation:
        self.validation_calls.append(changes.fingerprint)
        self.validation_requests.append(request)
        if self.validation_unavailable is not None:
            raise ValidationExecutionError(self.validation_unavailable)
        attestation = _attestation(changes)
        if request.expected_commands != attestation.expected_commands:
            records = tuple(
                ValidationRecord(ValidationStatus.PASS, command, 0)
                for command in request.expected_commands
            )
            attestation = ValidationAttestation(
                attestation.attestation_id,
                attestation.diff_fingerprint,
                request.expected_commands,
                records,
                attestation.output_digest,
                attestation.summary,
            )
        if request.attempt_number > 1:
            attestation = ValidationAttestation(
                f"{attestation.attestation_id}-retry-{request.attempt_number}",
                attestation.diff_fingerprint,
                attestation.expected_commands,
                attestation.records,
                attestation.output_digest,
                attestation.summary,
            )
        if self.invalid_attestation == "foreign":
            return ValidationAttestation(
                attestation.attestation_id,
                "e" * 64,
                attestation.expected_commands,
                attestation.records,
                attestation.output_digest,
                attestation.summary,
            )
        if self.invalid_attestation == "incomplete":
            return ValidationAttestation(
                attestation.attestation_id,
                attestation.diff_fingerprint,
                request.expected_commands,
                attestation.records[:-1],
                attestation.output_digest,
                attestation.summary,
            )
        if self.invalid_attestation == "failing":
            return ValidationAttestation(
                attestation.attestation_id,
                attestation.diff_fingerprint,
                request.expected_commands,
                tuple(
                    ValidationRecord(ValidationStatus.FAIL, command, 1, "red")
                    for command in request.expected_commands
                ),
                attestation.output_digest,
                "validation failed",
            )
        return attestation

    def recover_pending_validation_attestation(
        self,
        fingerprint: str,
        expected_commands: tuple[str, ...],
        attestation_id: str,
    ) -> ValidationAttestation | None:
        key = (fingerprint, expected_commands, attestation_id)
        self.validation_recovery_calls.append(key)
        return self.validation_recoveries.get(key)

    def validate_plan(
        self,
        changes: WorkflowChanges,
        *,
        work_plan_path: str | None,
        scope_patterns: tuple[str, ...],
        plan_only: bool,
    ) -> ValidationAttestation:
        _ = (work_plan_path, scope_patterns, plan_only)
        return self.validate(
            changes,
            ValidationRequest(
                diff_fingerprint=changes.fingerprint,
                commands=(ValidationCommand(argv=("internal:plan-contract",)),),
            ),
        )

    def invoke_reviewer(self, invocation: ReviewerInvocation) -> NativeAgentReviewOutput:
        self._assert_checkpointed_attestation(invocation.fingerprint)
        self.reviewer_calls.append(invocation)
        if self.reviewer_failures:
            failure = self.reviewer_failures.pop(0)
            if failure is not None:
                raise failure
        if self.fail_reviewer_once:
            self.fail_reviewer_once = False
            raise RuntimeError("simulated process interruption")
        output = self.reviewer_outputs.pop(0)
        return (
            output
            if isinstance(output, NativeAgentReviewOutput)
            else _test_native_review_output(invocation, output)
        )

    def recover_pending_native_reviewer(
        self,
        invocation: ReviewerInvocation,
        contract: StepContract,
        history: WorkflowHistory,
    ) -> NativeAgentReviewOutput | None:
        self.structured_events.append(
            ("recover-native-reviewer", (invocation, contract, history))
        )
        return None

    def recover_pending_native_reviewer_before_policy(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> PersistedNativeReviewerReplay | None:
        self.structured_events.append(
            ("recover-native-reviewer-before-policy", (state, context, history))
        )
        return None

    def commit_slice(self, request: WorkflowCommitRequest) -> str:
        self.commit_calls.append(request)
        if self.commit_failure is not None:
            failure = self.commit_failure
            self.commit_failure = None
            raise failure
        return self.commit_refs.pop(0) if self.commit_refs else "b" * 40

    def checkpoint(self, state, history) -> None:
        self.active_state = state
        self.failure_persistence_events.append("checkpoint")
        self.checkpoints.append(state)
        self.checkpoint_histories.append(history)

    def persist_gate_decision(self, work_unit_id: int, decision: object) -> None:
        self.structured_events.append(
            ("gate-decision", (work_unit_id, decision))
        )

    def persist_gate_transition(self, state: WorkflowState) -> None:
        self.structured_events.append(("gate-transition", state))

    def persist_invocation_failure(
        self, payload: InvocationFailurePayload
    ) -> None:
        self.failure_persistence_events.append("failure-record")
        self.failure_payloads.append(payload)
        self.structured_events.append(("invocation-failure", payload))

    def persist_native_codex_contract(
        self,
        output: NativeAgentCodexOutput,
        request_sequence: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        self.structured_events.append(
            ("native-codex", (output, request_sequence, previous_findings))
        )

    def persist_native_review_contract(
        self,
        output: NativeAgentReviewOutput,
        fingerprint: str,
        round_number: int,
        request_sequence: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        self.structured_events.append(
            (
                "native-review",
                (
                    output,
                    fingerprint,
                    round_number,
                    request_sequence,
                    previous_findings,
                ),
            )
        )

    def persist_review_packet(self, packet: ReviewPacket) -> None:
        self.structured_events.append(("review-packet", packet))

    def persist_validation_request(self, request: ValidationRequest) -> None:
        self.structured_events.append(("validation-request", request))

    def persist_validation_attestation(
        self, attestation: ValidationAttestation
    ) -> None:
        self.structured_events.append(("validation-attestation", attestation))

    def persist_finding_acceptance_measurement(
        self,
        finding: FindingRecord,
        measurement: FindingAcceptanceMeasurement,
    ) -> None:
        self.structured_events.append(
            ("finding-acceptance-measurement", (finding, measurement))
        )


def _slice_state(
    scope_paths: tuple[str, ...] = ("src/early.py", "src/latest.py", TEST_FILE),
    scope_change_groups: tuple[tuple[str, ...], ...] | None = None,
):
    state = init_workflow_state(
        run_id="run-1",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=2,
        timestamp="2026-08-12T10:00:00+00:00",
    ).complete_current_work_unit()
    state = state.start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    )
    return state.bind_current_slice_git_boundary(
        start_commit=START_COMMIT,
        scope_paths=scope_paths,
        scope_change_groups=scope_change_groups,
        start_fingerprint="0" * 64,
    )


def _completed_single_slice_state():
    return init_workflow_state(
        run_id="run-final",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=1,
        timestamp="2026-08-12T10:00:00+00:00",
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=START_COMMIT,
        scope_paths=("src/early.py", TEST_FILE),
        start_fingerprint="0" * 64,
    ).complete_current_slice(commit_ref="b" * 40)


def _context() -> WorkflowContext:
    return WorkflowContext(
        assignment="Implement Slice 10",
        distilled_plan="Codex implements; Claude reviews and approves each round.",
        slice_summary="Asymmetric state-v3 workflow engine.",
        work_plan_path="docs/internal/plan.md",
        expected_test_files=(TEST_FILE,),
        test_changes_approved=True,
    )


def _with_open_findings(
    state: WorkflowState, finding_ids: tuple[str, ...]
) -> WorkflowState:
    current = replace(state.current_work_unit, open_findings=finding_ids)
    return replace(state, work_units=(*state.work_units[:-1], current))


def test_native_work_unit_mirrors_carried_open_ledger_before_provider_resume() -> None:
    open_finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="Imported finding remains open.",
        acceptance_test="The next Slice binds the same identity.",
        origin=FindingOrigin("PLAN", 1, AgentRole.CLAUDE),
    )
    closed_finding = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.CLOSED,
        summary="Imported finding was already closed.",
        acceptance_test="The closure stays in the complete ledger.",
        origin=FindingOrigin("PLAN", 1, AgentRole.CLAUDE),
        status_rationale="Closed by the source reviewer.",
    )
    state = replace(
        _slice_state().await_policy_gate(
            reason=GateReason.STOP_REQUEST,
            detail="TECHNICAL-RESUME | wait before provider restart",
        ),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    )
    history = WorkflowHistory(
        state.current_work_unit_id,
        findings=(open_finding, closed_finding),
    )
    driver = FakeDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])

    result = WorkflowEngine(driver).run_current_work_unit(state, _context(), history)

    assert result.state.current_work_unit.open_findings == ("C-01",)
    assert result.history.findings == (open_finding, closed_finding)
    assert driver.checkpoints[-1].current_work_unit.open_findings == ("C-01",)


def _packet_plan() -> str:
    return """# Approved plan

### Slice 1 - Round cause

**Ziel**

Keep request recomposition distinct from review correction.

**\u0041kzeptanzkriterien**

- The review packet follows the persisted cause of the round.
"""


def _combined_native_slice_state() -> WorkflowState:
    return replace(
        _slice_state(),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    )


def test_recomposed_request_round_builds_slice_packet_and_keeps_open_findings() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="A carried observation remains visible.",
        acceptance_test="The next Slice review receives the carried ledger.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )

    @dataclass
    class AuthoritativeRecompositionDriver(FakeDriver):
        def authoritative_native_findings(
            self,
            state: WorkflowState,
            mirror_findings: tuple[FindingRecord, ...],
        ) -> tuple[FindingRecord, ...]:
            self.authoritative_finding_calls.append(
                (state.current_step.value, mirror_findings)
            )
            return (finding,)

    changes = _changes("b", "src/early.py", TEST_FILE)
    driver = AuthoritativeRecompositionDriver(
        snapshots=[changes, changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        codex_failures=[
            ProviderRequestRoundRequired(
                binding_fingerprint="a" * 64,
                previous_input_digest="b" * 64,
                current_input_digest="c" * 64,
            ),
            None,
        ],
    )
    state = _with_open_findings(
        _combined_native_slice_state(),
        (finding.finding_id,),
    )

    result = WorkflowEngine(driver).run_current_work_unit(
        state,
        replace(_context(), approved_plan_text=_packet_plan()),
        WorkflowHistory(state.current_work_unit_id),
    )

    assert result.completed
    assert [call.round_number for call in driver.codex_calls] == [1, 1]
    assert [call.request_sequence for call in driver.codex_calls] == [1, 2]
    assert result.state.current_work_unit.open_findings == (finding.finding_id,)
    review = driver.reviewer_calls[0]
    assert review.evidence_kind is EvidenceKind.FULL_SLICE
    assert review.review_packet is not None
    assert review.review_packet.purpose == "slice"
    packet = json.loads(review.review_packet.canonical_bytes)
    assert [item["id"] for item in packet["open_findings"]] == [finding.finding_id]


def _denied_slice_round(
    finding: FindingRecord,
) -> tuple[WorkflowState, WorkflowHistory]:
    state = _combined_native_slice_state().with_current_step(
        WorkflowStep.CLAUDE_SLICE_REVIEW
    ).record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=(finding.finding_id,),
        return_step=WorkflowStep.CODEX_CORRECTION,
        progress_made=True,
    )
    denial = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=False,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=(TEST_FILE,),
        pre_mortem=None,
        evidence=None,
        findings=(finding,),
        anchors=(),
    )
    return state, WorkflowHistory(
        state.current_work_unit_id,
        findings=(finding,),
        last_claude_fingerprint="d" * 64,
        latest_claude_review=denial,
    )


def _legacy_iteration_gate_state(
    open_findings: tuple[str, ...],
) -> WorkflowState:
    state = _combined_native_slice_state()
    current = replace(
        state.current_work_unit,
        status=WorkUnitStatus.AWAITING_USER_DECISION,
        current_step=WorkflowStep.CODEX_CORRECTION,
        round_number=4,
        codex_return_count=4,
        max_codex_returns=4,
        gate=GateRecord(
            status=GateStatus.AWAITING_USER_DECISION,
            reason=GateReason.ITERATION_LIMIT,
            detail="review denied by claude after 4 Codex returns",
        ),
        reviewer=Reviewer.CLAUDE,
        open_findings=open_findings,
    )
    return replace(
        state,
        current_step=WorkflowStep.CODEX_CORRECTION,
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


def _denied_review_result(
    findings: tuple[FindingRecord, ...],
) -> ContractResult:
    return ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=False,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=(TEST_FILE,),
        pre_mortem=None,
        evidence=None,
        findings=findings,
        anchors=(),
    )


def test_cutover_always_uses_the_slice_convergence_measure() -> None:
    assert native_finding_decisions.JOINT_67_68_NATIVE_CONTRACT_CUTOVER is True
    existing = FindingRecord(
        "C-01",
        FindingClass.BLOCKER,
        FindingStatus.OPEN,
        "Existing local blocker",
        "Close the existing blocker.",
        FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    newly_opened = FindingRecord(
        "C-02",
        FindingClass.BLOCKER,
        FindingStatus.OPEN,
        "Newly discovered blocker",
        "Close the newly discovered blocker.",
        FindingOrigin("01", 2, AgentRole.CLAUDE),
    )
    state = _second_slice_review_state(existing.finding_id)
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[],
        reviewer_outputs=[],
        convergence_evaluations=[_negative_convergence()],
    )

    next_state, _history = WorkflowEngine(driver)._apply_review_result(
        state=state,
        context=_context(),
        history=WorkflowHistory(state.current_work_unit_id, findings=(existing,)),
        reviewer=AgentRole.CLAUDE,
        result=_denied_review_result((existing, newly_opened)),
        fingerprint="d" * 64,
        round_number=2,
        is_plan_review=False,
        is_final_review=False,
        finding_ledger=(existing,),
    )

    assert driver.convergence_calls == [(state.current_work_unit_id, 2)]
    assert next_state.current_work_unit.status is WorkUnitStatus.COMPLETED
    assert next_state.current_step is WorkflowStep.COMPLETED


def test_cutover_convergence_stall_ends_gate_free_without_slice_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    finding = FindingRecord(
        "C-01",
        FindingClass.BLOCKER,
        FindingStatus.OPEN,
        "Still-open local blocker",
        "Close the blocker.",
        FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    state = _second_slice_review_state(finding.finding_id)
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[],
        reviewer_outputs=[],
        convergence_evaluations=[_negative_convergence()],
    )

    next_state, history = WorkflowEngine(driver)._apply_review_result(
        state=state,
        context=_context(),
        history=WorkflowHistory(state.current_work_unit_id, findings=(finding,)),
        reviewer=AgentRole.CLAUDE,
        result=_denied_review_result((finding,)),
        fingerprint="d" * 64,
        round_number=2,
        is_plan_review=False,
        is_final_review=False,
        finding_ledger=(finding,),
    )
    run_result = WorkflowRunResult(next_state, history)

    assert driver.convergence_calls == [(state.current_work_unit_id, 2)]
    assert next_state.current_work_unit.status is WorkUnitStatus.COMPLETED
    assert next_state.current_step is WorkflowStep.COMPLETED
    assert next_state.current_work_unit.gate.status is GateStatus.CLEAR
    assert driver.commit_calls == []
    assert run_result.workflow_rejected
    assert "no attested fingerprint-changing remediation" in (
        run_result.rejection_detail or ""
    )


def _second_slice_review_state(finding_id: str) -> WorkflowState:
    state = _combined_native_slice_state().with_current_step(
        WorkflowStep.CLAUDE_SLICE_REVIEW
    )
    state = state.record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=(finding_id,),
        return_step=WorkflowStep.CODEX_CORRECTION,
        progress_made=True,
    )
    return state.with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)


def _negative_convergence() -> SliceConvergenceEvaluation:
    return SliceConvergenceEvaluation(
        phase=SliceReviewPhase.CONVERGENCE,
        cohort_finding_ids=("C-01",),
        newly_opened_finding_ids=("C-02",),
        closed_local_finding_ids=(),
        forwarded_local_finding_ids=(),
        attested_remediation_finding_ids=(),
        progress_made=False,
        reason=(
            "the convergence round closed or forwarded no previously local "
            "finding and recorded no attested fingerprint-changing remediation"
        ),
    )


def _discovery_convergence() -> SliceConvergenceEvaluation:
    return SliceConvergenceEvaluation(
        phase=SliceReviewPhase.DISCOVERY,
        cohort_finding_ids=("C-01",),
        newly_opened_finding_ids=("C-01",),
        closed_local_finding_ids=(),
        forwarded_local_finding_ids=(),
        attested_remediation_finding_ids=(),
        progress_made=True,
        reason="the discovery round opened reviewer-owned findings",
    )


def test_retired_iteration_gate_continues_from_persisted_progress() -> None:
    prior = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="Prior blocker",
        acceptance_test="Close the prior blocker.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    replacement = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="Newly exposed blocker",
        acceptance_test="Close the newly exposed blocker.",
        origin=FindingOrigin("01", 4, AgentRole.CLAUDE),
    )
    previous = _denied_review_result((prior,))
    current = _denied_review_result(
        (
            replace(prior, status=FindingStatus.CLOSED, status_rationale="Verified."),
            replacement,
        )
    )
    state = _legacy_iteration_gate_state((replacement.finding_id,))
    history = WorkflowHistory(
        state.current_work_unit_id,
        findings=current.findings,
        events=(
            ReviewAuditEvent(1, state.current_slice_id, 3, previous),
            ReviewAuditEvent(2, state.current_slice_id, 4, current),
        ),
        latest_claude_review=current,
    )

    resolved = resolve_retired_iteration_limit(state, history)

    assert resolved.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert resolved.current_work_unit.gate.status is GateStatus.CLEAR
    assert resolved.current_work_unit.max_codex_returns == 8
    assert resolved.current_work_unit.codex_return_count == 4


def test_review_denial_round_builds_correction_packet_with_affected_findings() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The rejected Slice needs a correction.",
        acceptance_test="The correction packet names this blocker.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    state, history = _denied_slice_round(finding)
    state = state.with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    changes = _changes("b", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[_review_closes(AgentRole.CLAUDE, finding.finding_id)],
        deltas={("0" * 64, changes.fingerprint): changes.full_diff},
    )

    advanced, _ = WorkflowEngine(driver)._run_review(
        state,
        replace(_context(), approved_plan_text=_packet_plan()),
        history,
        AgentRole.CLAUDE,
    )

    assert advanced.current_step is WorkflowStep.SLICE_COMMIT
    review = driver.reviewer_calls[0]
    assert review.evidence_kind is EvidenceKind.CORRECTION_DELTA
    assert review.review_packet is not None
    assert review.review_packet.purpose == "correction"
    packet = json.loads(review.review_packet.canonical_bytes)
    assert [item["id"] for item in packet["open_findings"]] == [finding.finding_id]


def test_recomposition_after_review_denial_keeps_correction_semantics() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The rejected Slice needs a correction.",
        acceptance_test="A version change cannot erase correction semantics.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    state, history = _denied_slice_round(finding)
    changes = _changes("b", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes, changes],
        codex_outputs=[_codex_ready(finding.finding_id)],
        reviewer_outputs=[_review_closes(AgentRole.CLAUDE, finding.finding_id)],
        codex_failures=[
            ProviderRequestRoundRequired(
                binding_fingerprint="a" * 64,
                previous_input_digest="b" * 64,
                current_input_digest="c" * 64,
            ),
            None,
        ],
        deltas={("0" * 64, changes.fingerprint): changes.full_diff},
    )

    result = WorkflowEngine(driver).run_current_work_unit(
        state,
        replace(_context(), approved_plan_text=_packet_plan()),
        history,
    )

    assert result.completed
    assert [call.round_number for call in driver.codex_calls] == [2, 2]
    assert [call.request_sequence for call in driver.codex_calls] == [2, 3]
    review = driver.reviewer_calls[0]
    assert review.evidence_kind is EvidenceKind.CORRECTION_DELTA
    assert review.review_packet is not None
    assert review.review_packet.purpose == "correction"
    packet = json.loads(review.review_packet.canonical_bytes)
    assert [item["id"] for item in packet["open_findings"]] == [finding.finding_id]


def test_native_implementation_package_matches_request_open_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plan_handoff,
        "_ACCEPTANCE_HEADINGS",
        (*plan_handoff._ACCEPTANCE_HEADINGS, "**Acceptance Criteria**"),
    )
    open_finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="Imported lifecycle reaches Codex.",
        acceptance_test="Codex dispositions bind this exact finding.",
        origin=FindingOrigin("PLAN", 1, AgentRole.CLAUDE),
    )
    closed_finding = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.CLOSED,
        summary="Closed lifecycle remains reviewer-only context.",
        acceptance_test="Do not offer a closed finding to Codex.",
        origin=FindingOrigin("PLAN", 1, AgentRole.CLAUDE),
        status_rationale="Closed before implementation.",
    )
    state = _slice_state()
    context = replace(
        _context(),
        approved_plan_text=(
            "### Slice 1 - Imported finding contract\n\n"
            "**Ziel**\n\nPreserve the imported lifecycle.\n\n"
            "**Exakter Änderungspfad**\n\n- `src/early.py`\n\n"
            "**Acceptance Criteria**\n\n- Keep one finding identity.\n"
        ),
    )
    contract = CodexStepContract(
        name="work-unit-2-codex_implementation",
        readiness_marker=ReadinessMarker.IMPLEMENTATION,
        slice_id="01",
        round_number=1,
        require_test_files_record=True,
        test_changes_approved=True,
    )

    bundle = workflow_requests.native_codex_request(
        execution_error=WorkflowExecutionError,
        state=state,
        context=context,
        history=WorkflowHistory(
            state.current_work_unit_id,
            findings=(open_finding, closed_finding),
        ),
        contract=contract,
        request_kind=NativeCodexRequestKind.IMPLEMENTATION,
    )

    manifest_item = next(
        item
        for item in bundle.document["evidence_manifest"]
        if item["evidence_id"] == "slice-execution-package"
    )
    package = json.loads(manifest_item["content"])
    assert package["slice"]["open_findings"] == bundle.document["open_findings"]
    assert [item["finding_id"] for item in bundle.document["open_findings"]] == [
        "C-01"
    ]
    assert bundle.bound_context.context.previous_findings == (
        open_finding,
        closed_finding,
    )


def _invocation_failure(
    role: AgentRole,
    kind: AgentFailureKind,
    invocation_id: str,
    *,
    received_at: datetime,
    reset_after_seconds: int | None = None,
    provider_data: dict[str, object] | None = None,
) -> AgentInvocationError:
    reset = (
        QuotaReset(
            received_at + timedelta(seconds=reset_after_seconds),
            f"{role.value}:structured:retry_after_seconds",
            "UTC",
        )
        if reset_after_seconds is not None
        else None
    )
    return AgentInvocationError(
        agent_key=role.value,
        kind=kind,
        invocation_id=invocation_id,
        provider_text=(
            "usage cap reached" if kind is AgentFailureKind.QUOTA else "Execution error"
        ),
        received_at=received_at,
        exit_code=None if kind is AgentFailureKind.QUOTA else 7,
        quota_reset=reset,
        provider_data=provider_data,
    )


def _native_review_contract_failure(
    code: NativeReviewErrorCode,
    invocation_id: str,
    *,
    received_at: datetime,
    detail: str = "provider-authored review rejected",
    operator_detail: str | None = None,
) -> AgentInvocationError:
    contract_error = NativeReviewContractError(
        code, detail, operator_detail=operator_detail
    )
    output_error = AgentOutputError(
        "native review result violates its bound contract",
        technical_text=f"{code.value}: {contract_error.detail}",
    )
    output_error.__cause__ = contract_error
    failure = classify_agent_failure(
        AgentRole.CLAUDE.value,
        output_error,
        invocation_id=invocation_id,
        received_at=received_at,
    )
    failure.__cause__ = output_error
    return failure


def _native_codex_contract_failure(
    code: NativeCodexErrorCode,
    invocation_id: str,
    *,
    received_at: datetime,
    detail: str = "provider-authored implementer result rejected",
    diagnostic: OrchestratorDiagnostic | None = None,
) -> AgentInvocationError:
    contract_error = NativeCodexContractError(
        code,
        detail,
        orchestrator_diagnostic=diagnostic,
    )
    output_error = AgentOutputError(
        "native Codex result violates its bound contract",
        technical_text=f"{code.value}: {contract_error.detail}",
        orchestrator_diagnostic=contract_error.orchestrator_diagnostic,
    )
    output_error.__cause__ = contract_error
    failure = classify_agent_failure(
        AgentRole.CODEX.value,
        output_error,
        invocation_id=invocation_id,
        received_at=received_at,
    )
    failure.__cause__ = output_error
    return failure


def _native_review_limit_failure(
    actual_items: int,
    maximum_items: int,
    invocation_id: str,
    *,
    received_at: datetime,
) -> AgentInvocationError:
    contract_error = NativeReviewContractError(
        NativeReviewErrorCode.SCHEMA_INVALID,
        "final-review disposition count exceeds bound maximum",
    )
    contract_error.disposition_limit = NativeReviewDispositionLimit(
        actual_items, maximum_items
    )
    output_error = AgentOutputError(
        "native review result violates its bound contract",
        technical_text=str(contract_error),
    )
    output_error.__cause__ = contract_error
    failure = classify_agent_failure(
        AgentRole.CLAUDE.value,
        output_error,
        invocation_id=invocation_id,
        received_at=received_at,
    )
    failure.__cause__ = output_error
    return failure


def test_provider_process_failure_reaches_record_with_actual_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 9, 1, 18, 30, tzinfo=timezone.utc)
    raw_technical_text = "stderr sentinel: provider worker was killed"
    error = classify_agent_failure(
        AgentRole.CLAUDE.value,
        AgentProcessError(raw_technical_text, exit_code=137),
        invocation_id="canary-review-process-failure",
        received_at=now,
    )
    assert error.kind is AgentFailureKind.PROCESS
    assert error.process_exit_code == 137
    assert error.technical_text == raw_technical_text
    driver = FakeDriver(
        snapshots=[_changes("1", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )
    caplog.set_level("INFO", logger="workflow")

    persisted, failure = WorkflowEngine(
        driver, now_fn=lambda: now
    )._persist_invocation_failure(
        _slice_state(),
        WorkflowHistory(2),
        _context(),
        AgentRole.CLAUDE,
        error,
    )

    assert driver.failure_persistence_events == ["failure-record", "checkpoint"]
    assert persisted.current_work_unit.invocation_failures == (failure,)
    assert failure.failure_kind is AgentFailureKind.PROCESS
    payload = driver.failure_payloads[0]
    assert payload.failure_kind == "process"
    assert payload.process_exit_code == 137
    assert payload.diagnostic_exit_code == 3
    assert raw_technical_text not in payload.technical_text
    assert payload.technical_text.startswith("[technical text redacted; sha256=")
    assert payload.technical_text_bytes == len(raw_technical_text.encode("utf-8"))
    assert "failure_kind=process" in caplog.text
    assert "process_exit_code=137" in caplog.text


@pytest.mark.parametrize(
    ("kind", "expected_automatic", "expected_class", "expected_diagnostic"),
    (
        (AgentFailureKind.QUOTA, True, "transient", "AGENT-INVOCATION"),
        (AgentFailureKind.NETWORK, True, "transient", "AGENT-INVOCATION"),
        (AgentFailureKind.TIMEOUT, True, "transient", "AGENT-INVOCATION"),
        (AgentFailureKind.OUTPUT, False, "resumable_halt", "AGENT-INVOCATION"),
        (AgentFailureKind.PROCESS, False, "resumable_halt", "AGENT-PROCESS"),
    ),
)
def test_r6_failure_record_precedes_retry_decision_and_uses_s1_classification(
    kind: AgentFailureKind,
    expected_automatic: bool,
    expected_class: str,
    expected_diagnostic: str,
) -> None:
    from error_classification import classify_exception

    now = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
    raw_provider_text = f"secret-provider-diagnostic-{kind.value}"
    raw_technical_text = f"secret-technical-diagnostic-{kind.value}"
    reset = (
        QuotaReset(
            now + timedelta(seconds=30),
            "codex:structured:retry_after_seconds",
            "UTC",
        )
        if kind is AgentFailureKind.QUOTA
        else None
    )
    error = AgentInvocationError(
        agent_key="codex",
        kind=kind,
        invocation_id=f"r6-{kind.value}",
        provider_text=raw_provider_text,
        received_at=now,
        quota_reset=reset,
        technical_text=raw_technical_text,
    )
    if kind is AgentFailureKind.PROCESS:
        error.__cause__ = AgentProcessError("provider process exited", exit_code=7)
    classified = classify_exception(error)
    driver = FakeDriver(
        snapshots=[_changes("1", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )
    context = replace(
        _context(),
        quota_wait_policy=QuotaWaitPolicy(safety_margin_seconds=7),
        transient_retry_policy=TransientRetryPolicy(initial_delay_seconds=5),
    )

    persisted, failure = WorkflowEngine(
        driver, now_fn=lambda: now
    )._persist_invocation_failure(
        _slice_state(),
        WorkflowHistory(2),
        context,
        AgentRole.CODEX,
        error,
    )

    assert driver.failure_persistence_events == ["failure-record", "checkpoint"]
    assert len(driver.failure_payloads) == 1
    payload = driver.failure_payloads[0]
    assert payload.failure_kind == kind.value
    assert classified.failure_class.value == "transient"
    assert payload.failure_class == expected_class
    assert payload.diagnostic_code == classified.diagnostic_code == expected_diagnostic
    assert payload.automatic_resume is expected_automatic
    assert payload.auto_resume_count == (1 if expected_automatic else 0)
    assert raw_provider_text not in payload.provider_text
    assert payload.provider_text.startswith("[provider text redacted; sha256=")
    assert payload.provider_text_bytes == len(raw_provider_text.encode("utf-8"))
    assert len(payload.provider_text) <= 128
    assert raw_technical_text not in payload.technical_text
    assert payload.technical_text.startswith("[technical text redacted; sha256=")
    assert payload.technical_text_bytes == len(raw_technical_text.encode("utf-8"))
    assert payload.process_exit_code is None
    assert failure.provider_text == payload.provider_text
    assert persisted.current_work_unit.invocation_failures == (failure,)
    if kind is AgentFailureKind.QUOTA:
        assert payload.parse_path == "codex:structured:retry_after_seconds"
        assert payload.source_timezone == "UTC"
        assert payload.retry_delay_seconds == payload.safety_margin_seconds == 7
        assert payload.resume_at_utc == "2026-08-31T10:00:37+00:00"
    elif kind in {AgentFailureKind.NETWORK, AgentFailureKind.TIMEOUT}:
        assert payload.retry_delay_seconds == 5
        assert payload.resume_at_utc == "2026-08-31T10:00:05+00:00"
    else:
        assert payload.retry_delay_seconds == 0
        assert payload.resume_at_utc is None


def test_contract_diagnostic_is_readable_but_injected_provider_text_stays_redacted(
    caplog,
) -> None:
    now = datetime(2026, 9, 5, 22, 13, 8, tzinfo=timezone.utc)
    injected_provider_text = "secret text copied from the provider response"
    diagnostic = OrchestratorDiagnostic.SLICE_PLAN_PATHS_INVALID
    contract_error = NativeCodexContractError(
        NativeCodexErrorCode.SLICE_PLAN_INVALID,
        injected_provider_text,
        orchestrator_diagnostic=diagnostic,
    )
    output_error = AgentOutputError(
        "native Codex result violates its bound contract",
        technical_text=injected_provider_text,
        orchestrator_diagnostic=diagnostic,
    )
    output_error.__cause__ = contract_error
    error = classify_agent_failure(
        AgentRole.CODEX.value,
        output_error,
        invocation_id="canary-20260905-221308Z",
        received_at=now,
    )
    error.__cause__ = output_error
    driver = FakeDriver(
        snapshots=[_changes("1", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )
    caplog.set_level("INFO", logger="workflow")

    persisted, failure = WorkflowEngine(
        driver, now_fn=lambda: now
    )._persist_invocation_failure(
        _slice_state(),
        WorkflowHistory(2),
        _context(),
        AgentRole.CODEX,
        error,
    )

    payload = driver.failure_payloads[0]
    assert payload.diagnostic_code == "NATIVE-IMPLEMENTER-FORM"
    assert payload.automatic_resume is True
    assert payload.native_implementer_rejection == "slice-plan-invalid"
    assert payload.orchestrator_diagnostic == diagnostic.text
    assert persisted.current_work_unit.invocation_failures == (failure,)
    assert injected_provider_text not in payload.provider_text
    assert injected_provider_text not in payload.technical_text
    assert injected_provider_text not in caplog.text
    assert payload.provider_text.startswith("[provider text redacted; sha256=")
    assert payload.technical_text.startswith("[technical text redacted; sha256=")
    assert "diagnostic_code=NATIVE-IMPLEMENTER-FORM" in caplog.text
    assert f"orchestrator_diagnostic={diagnostic.text}" in caplog.text


def test_plan_treatment_halt_reason_reaches_failure_record_and_log(caplog) -> None:
    now = datetime(2026, 9, 19, 20, 0, tzinfo=timezone.utc)
    detail = (
        "plan treatment is invalid: implementation treatment forbids No-Code "
        "disposition fields"
    )
    contract_error = NativeCodexContractError(
        NativeCodexErrorCode.SLICE_PLAN_INVALID,
        detail,
    )
    diagnostic = (
        OrchestratorDiagnostic.IMPLEMENTER_IMPLEMENTATION_TREATMENT_FORBIDS_NO_CODE_FIELDS
    )
    assert contract_error.orchestrator_diagnostic is diagnostic
    output_error = AgentOutputError(
        "native Codex result violates its bound contract",
        technical_text=str(contract_error),
        orchestrator_diagnostic=contract_error.orchestrator_diagnostic,
    )
    output_error.__cause__ = contract_error
    error = classify_agent_failure(
        AgentRole.CODEX.value,
        output_error,
        invocation_id="canary-20260919-plan-treatment",
        received_at=now,
    )
    error.__cause__ = output_error
    driver = FakeDriver(
        snapshots=[_changes("1", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )
    caplog.set_level("INFO", logger="workflow")

    WorkflowEngine(driver, now_fn=lambda: now)._persist_invocation_failure(
        _slice_state(),
        WorkflowHistory(2),
        _context(),
        AgentRole.CODEX,
        error,
    )

    payload = driver.failure_payloads[0]
    assert payload.technical_text.startswith("[technical text redacted; sha256=")
    assert payload.orchestrator_diagnostic == diagnostic.text
    assert "implementation treatment forbids No-Code disposition fields" in (
        payload.orchestrator_diagnostic
    )
    assert f"orchestrator_diagnostic={diagnostic.text}" in caplog.text


def test_mutated_orchestrator_diagnostic_cannot_expose_provider_text(caplog) -> None:
    now = datetime(2026, 9, 5, 22, 13, 8, tzinfo=timezone.utc)
    injected_provider_text = "provider-controlled diagnostic mutation"
    output_error = AgentOutputError(
        "native Codex result violates its bound contract",
        technical_text=injected_provider_text,
        orchestrator_diagnostic=(
            OrchestratorDiagnostic.SLICE_PLAN_PATHS_INVALID
        ),
    )
    error = classify_agent_failure(
        AgentRole.CODEX.value,
        output_error,
        invocation_id="mutated-provider-diagnostic",
        received_at=now,
    )
    error.orchestrator_diagnostic = injected_provider_text  # type: ignore[assignment]
    driver = FakeDriver(
        snapshots=[_changes("1", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )
    caplog.set_level("INFO", logger="workflow")

    WorkflowEngine(driver, now_fn=lambda: now)._persist_invocation_failure(
        _slice_state(),
        WorkflowHistory(2),
        _context(),
        AgentRole.CODEX,
        error,
    )

    payload = driver.failure_payloads[0]
    assert payload.orchestrator_diagnostic is None
    assert injected_provider_text not in payload.provider_text
    assert injected_provider_text not in payload.technical_text
    assert injected_provider_text not in caplog.text
    assert "orchestrator_diagnostic=none" in caplog.text


def test_workflow_execution_halt_has_closed_value_free_diagnostic(caplog) -> None:
    now = datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc)
    static_error = WorkflowExecutionError(
        "native review persistence lacks its validation attestation"
    )
    assert static_error.orchestrator_diagnostic is (
        OrchestratorDiagnostic.WORKFLOW_REVIEW_VALIDATION_ATTESTATION_MISSING
    )
    assert static_error.orchestrator_diagnostic.text == (
        "workflow-execution: "
        "native review persistence lacks its validation attestation"
    )
    injected_provider_text = "provider-secret-context-value"
    execution_error = WorkflowExecutionError(
        f"native review persistence rejected before publication: "
        f"{injected_provider_text}"
    )
    assert execution_error.orchestrator_diagnostic is (
        OrchestratorDiagnostic.WORKFLOW_EXECUTION_RULE
    )
    error = classify_agent_failure(
        AgentRole.CLAUDE.value,
        execution_error,
        invocation_id="workflow-execution-diagnostic",
        received_at=now,
    )
    error.__cause__ = execution_error
    driver = FakeDriver(
        snapshots=[_changes("1", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )
    caplog.set_level("INFO", logger="workflow")

    persisted, failure = WorkflowEngine(
        driver, now_fn=lambda: now
    )._persist_invocation_failure(
        _slice_state().with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW),
        WorkflowHistory(2),
        _context(),
        AgentRole.CLAUDE,
        error,
    )

    payload = driver.failure_payloads[0]
    assert payload.diagnostic_code == "WORKFLOW-EXECUTION"
    assert payload.orchestrator_diagnostic == (
        OrchestratorDiagnostic.WORKFLOW_EXECUTION_RULE.text
    )
    assert failure.orchestrator_diagnostic == payload.orchestrator_diagnostic
    assert persisted.current_work_unit.invocation_failures == (failure,)
    assert injected_provider_text not in payload.orchestrator_diagnostic
    assert injected_provider_text not in caplog.text

    precise_error = WorkflowExecutionError(
        "native review persistence differs from its exact review context: "
        "field=request_sequence",
        orchestrator_diagnostic=(
            OrchestratorDiagnostic.WORKFLOW_REVIEW_CONTEXT_REQUEST_SEQUENCE
        ),
    )

    def reject_persistence() -> None:
        raise precise_error

    with pytest.raises(WorkflowExecutionError) as wrapped:
        WorkflowEngine(driver)._persist_structured(reject_persistence)
    assert wrapped.value.orchestrator_diagnostic is (
        OrchestratorDiagnostic.WORKFLOW_REVIEW_CONTEXT_REQUEST_SEQUENCE
    )


def test_r6_wrapped_quota_keeps_policy_despite_deeper_s1_classification() -> None:
    now = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
    source = AgentOutputError(
        "usage limit reached; your limit will reset at 3pm (Europe/Berlin)"
    )
    classified_error = classify_agent_failure(
        AgentRole.CLAUDE.value,
        source,
        invocation_id="r6-wrapped-quota",
        received_at=now,
    )
    try:
        raise classified_error from source
    except AgentInvocationError as captured:
        error = captured
    driver = FakeDriver(
        snapshots=[_changes("1", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )
    context = replace(
        _context(),
        quota_wait_policy=QuotaWaitPolicy(safety_margin_seconds=7),
    )

    persisted, failure = WorkflowEngine(
        driver, now_fn=lambda: now
    )._persist_invocation_failure(
        _slice_state(),
        WorkflowHistory(2),
        context,
        AgentRole.CLAUDE,
        error,
    )

    payload = driver.failure_payloads[0]
    assert failure.failure_kind is AgentFailureKind.QUOTA
    assert payload.failure_class == "transient"
    assert payload.diagnostic_code == "AGENT-OUTPUT"
    assert payload.automatic_resume is failure.automatic_resume is True
    assert payload.auto_resume_count == failure.auto_resume_count == 1
    assert payload.parse_path == "claude:text:local-clock"
    assert payload.source_timezone == "Europe/Berlin"
    assert payload.reset_at_utc == "2026-08-31T13:00:00+00:00"
    assert payload.retry_delay_seconds == payload.safety_margin_seconds == 7
    assert payload.resume_at_utc == "2026-08-31T13:00:07+00:00"
    assert persisted.current_work_unit.status is WorkUnitStatus.WAITING_FOR_QUOTA


def test_failure_recording_resolves_clock_and_fingerprint_at_call_time() -> None:
    constructed_at = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
    invoked_at = constructed_at + timedelta(minutes=3)
    late_fingerprint = "f" * 64
    error = AgentInvocationError(
        agent_key="codex",
        kind=AgentFailureKind.NETWORK,
        invocation_id="late-bound-failure-dependencies",
        provider_text="network unavailable",
        received_at=constructed_at,
        technical_text="connection reset",
    )
    driver = FakeDriver(
        snapshots=[_changes("1", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )
    engine = WorkflowEngine(driver, now_fn=lambda: constructed_at)
    engine.now_fn = lambda: invoked_at
    engine._current_invocation_fingerprint = lambda _state: late_fingerprint  # type: ignore[method-assign]

    _persisted, failure = engine._persist_invocation_failure(
        _slice_state(),
        WorkflowHistory(2),
        _context(),
        AgentRole.CODEX,
        error,
    )

    payload = driver.failure_payloads[0]
    assert payload.decision_at_utc == invoked_at.isoformat()
    assert payload.diff_fingerprint == failure.diff_fingerprint == late_fingerprint


def test_claude_reuses_single_fingerprint_attestation_without_matrix_rerun() -> None:
    changes = _changes("1", "engine/core.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
    )
    matrix = ValidationMatrix(
        default_command=ValidationCommand(argv=("npm", "test")),
        rules=(
            ValidationRule(
                ("engine/**",),
                ValidationCommand(argv=("npm", "run", "build:engine")),
            ),
        ),
    )

    result = WorkflowEngine(driver).run_current_work_unit(
        _slice_state(scope_paths=("engine/core.py", TEST_FILE)),
        replace(_context(), validation_matrix=matrix),
    )

    assert result.completed
    assert len(driver.validation_requests) == 1
    assert driver.validation_requests[0].expected_commands == (
        "npm test",
        "npm run build:engine",
    )
    assert len(driver.reviewer_calls) == 1
    assert driver.reviewer_calls[0].fingerprint == changes.fingerprint
    assert driver.reviewer_calls[0].native_request is not None
    assert "npm run build:engine" in driver.reviewer_calls[0].native_request.canonical_json
    assert driver.commit_calls[0].attestation.diff_fingerprint == changes.fingerprint


def test_incomplete_attestation_never_reaches_claude_or_commit() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        invalid_attestation="incomplete",
    )

    with pytest.raises(WorkflowExecutionError, match="incomplete"):
        WorkflowEngine(driver).run_current_work_unit(
            _slice_state(),
            replace(_context(), red_state_followup_slice="Slice 14"),
        )

    assert driver.reviewer_calls == []
    assert driver.commit_calls == []


def test_slice_commit_with_stale_claude_fingerprint_revalidates_before_commit() -> None:
    changes = _changes("2", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
    )
    state = _slice_state().with_current_step(WorkflowStep.SLICE_COMMIT)

    result = WorkflowEngine(driver).run_current_work_unit(
        state,
        _context(),
        WorkflowHistory(state.current_work_unit_id),
    )

    assert result.completed
    assert driver.validation_calls == [changes.fingerprint]
    assert [call.reviewer for call in driver.reviewer_calls] == [AgentRole.CLAUDE]
    assert driver.codex_calls == []
    assert len(driver.commit_calls) == 1
    assert driver.commit_calls[0].fingerprint == changes.fingerprint


def test_slice_head_drift_is_persisted_as_exact_resume_gate() -> None:
    changes = _changes("2", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        commit_failure=WorkflowCommitApprovalRequired(
            "HEAD-DRIFT | approve the reviewed descendant HEAD",
            changes.paths,
        ),
    )
    state = _slice_state().with_current_step(WorkflowStep.SLICE_COMMIT)

    result = WorkflowEngine(driver).run_current_work_unit(
        state,
        _context(),
        WorkflowHistory(state.current_work_unit_id),
    )

    assert not result.completed
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert result.state.current_work_unit.gate.reason is GateReason.UNEXPECTED_FILE
    assert result.state.current_work_unit.gate.fingerprint == changes.fingerprint
    assert result.state.current_work_unit.gate.paths == changes.paths
    assert result.state.current_work_unit.gate.resume_step is WorkflowStep.SLICE_COMMIT
    assert driver.checkpoints[-1] == result.state


def test_resume_from_persisted_claude_step_does_not_repeat_codex() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    interrupted = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        fail_reviewer_once=True,
    )
    with pytest.raises(RuntimeError, match="interruption"):
        WorkflowEngine(interrupted).run_current_work_unit(_slice_state(), _context())
    persisted = interrupted.checkpoints[-1]
    assert persisted.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW

    resumed = FakeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        snapshot_index=0,
    )
    result = WorkflowEngine(resumed).run_current_work_unit(
        persisted,
        _context(),
        interrupted.checkpoint_histories[-1],
    )

    assert result.completed
    assert resumed.codex_calls == []
    assert [call.reviewer for call in resumed.reviewer_calls] == [AgentRole.CLAUDE]


def _diff_for_paths(*paths: str, content: str = "+corrected") -> str:
    return "".join(
        f"diff --git a/{path} b/{path}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -1 +1 @@\n-old\n{content}\n"
        for path in paths
    )


def test_plan_change_boundary_honors_exact_fingerprint_bound_path_approval() -> None:
    state = init_workflow_state(
        run_id="run-plan-approved-hotfix",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=1,
        timestamp="2026-08-12T10:00:00+00:00",
        task_scope_patterns=("docs/internal/plan.md",),
    )
    changes = _changes(
        "1",
        "docs/internal/plan.md",
        "src/agent_runtime.py",
        "tests/test_agent_runtime.py",
    )
    context = replace(
        _context(),
        plan_only=True,
        task_scope_patterns=("docs/internal/plan.md",),
        work_plan_path="docs/internal/plan.md",
    )
    engine = WorkflowEngine(
        FakeDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])
    )

    unexpected = engine._validate_change_boundary(
        state,
        changes,
        WorkUnitKind.PLAN,
        context=context,
    )
    assert unexpected == ("src/agent_runtime.py", "tests/test_agent_runtime.py")

    gated = state.await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="reviewed hotfix paths",
        fingerprint=changes.fingerprint,
        paths=unexpected,
    )
    approved = gated.record_user_gate_decision(
        approved=True,
        fingerprint=changes.fingerprint,
        paths=unexpected,
        rationale="fingerprint-bound hotfix reviewed",
    )

    assert engine._validate_change_boundary(
        approved,
        changes,
        WorkUnitKind.PLAN,
        context=context,
    ) == ()
    assert engine._validate_change_boundary(
        approved,
        replace(changes, fingerprint="2" * 64),
        WorkUnitKind.PLAN,
        context=context,
    ) == unexpected


def test_native_claude_review_bypasses_legacy_marker_parser(
    monkeypatch,
) -> None:
    changes = _changes("b", "src/early.py", TEST_FILE)

    @dataclass
    class NativeDriver(FakeDriver):
        persisted_native: list[
            tuple[NativeAgentReviewOutput, str, int, int]
        ] = field(
            default_factory=list
        )

        def invoke_reviewer(
            self, invocation: ReviewerInvocation
        ) -> NativeAgentReviewOutput:
            self.reviewer_calls.append(invocation)
            assert invocation.prompt == ""
            assert invocation.native_request is not None
            bound = invocation.native_request.bound_context
            attestation = bound.context.validation_attestation
            assert attestation is not None
            result = ContractResult(
                reviewer=AgentRole.CLAUDE,
                approval=True,
                stopped=False,
                stop_request=None,
                validation=attestation,
                test_files=bound.context.test_files,
                pre_mortem="A recovery branch may accidentally invoke Claude twice.",
                evidence=ReviewEvidence(
                    "correctness and resume",
                    "record-ahead drift",
                    "a second provider start",
                ),
                findings=bound.context.previous_findings,
                anchors=(),
            )
            return NativeAgentReviewOutput(
                result=result,
                canonical_json=(
                    '{"request_id":"' + bound.request_id + '","result":"approved"}'
                ),
                request_id=bound.request_id,
            )

        def persist_native_review_contract(
            self,
            output: NativeAgentReviewOutput,
            fingerprint: str,
            round_number: int,
            request_sequence: int,
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            assert previous_findings == ()
            self.persisted_native.append(
                (output, fingerprint, round_number, request_sequence)
            )

    state = replace(
        _slice_state().with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    )
    driver = NativeDriver(
        snapshots=[changes], codex_outputs=[], reviewer_outputs=[]
    )
    advanced, history = WorkflowEngine(driver)._run_review(
        state,
        _context(),
        WorkflowHistory(state.current_work_unit_id),
        AgentRole.CLAUDE,
    )

    assert advanced.current_step is WorkflowStep.SLICE_COMMIT
    assert history.latest_claude_review is not None
    assert len(driver.persisted_native) == 1
    invocation = driver.reviewer_calls[0]
    assert invocation.native_request is not None
    assert invocation.native_request.document["authorized_paths"] == [
        "src/early.py",
        TEST_FILE,
    ]


@pytest.mark.parametrize(
    ("step", "request_type"),
    (
        (WorkflowStep.CODEX_IMPLEMENTATION, "implementation"),
        (WorkflowStep.CODEX_CORRECTION, "correction"),
    ),
)
def test_native_codex_result_bypasses_legacy_marker_parser(
    monkeypatch, step: WorkflowStep, request_type: str
) -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The native correction stays structured.",
        acceptance_test="No text result parser is invoked.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    previous_findings = (
        (finding,) if step is WorkflowStep.CODEX_CORRECTION else ()
    )
    result_findings = (
        (
            replace(
                finding,
                responses=(
                    FindingResponse(
                        FindingResponseDecision.ACCEPTED,
                        "The native correction remains parser-free.",
                    ),
                ),
            ),
        )
        if previous_findings
        else ()
    )

    @dataclass
    class NativeCodexDriver(FakeDriver):
        persisted: list[NativeAgentCodexOutput] = field(default_factory=list)

        def invoke_codex(
            self, invocation: CodexInvocation
        ) -> NativeAgentCodexOutput:
            self.codex_calls.append(invocation)
            assert invocation.prompt == ""
            assert invocation.native_request is not None
            bound = invocation.native_request.bound_context
            result = CodexContractResult(
                ready=True,
                stopped=False,
                stop_request=None,
                validation=None,
                test_files=(TEST_FILE,),
                findings=result_findings,
                slice_plan=(),
            )
            canonical = json.dumps(
                {
                    "request_id": bound.request_id,
                    "result_type": "implementation_result",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            return NativeAgentCodexOutput(
                result=result,
                canonical_json=canonical,
                request_id=bound.request_id,
                response_sha256="b" * 64,
            )

        def persist_native_codex_contract(
            self,
            output: NativeAgentCodexOutput,
            request_sequence: int,
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            assert request_sequence == 1
            assert previous_findings == previous_findings_expected
            self.persisted.append(output)

    state = replace(
        _slice_state().with_current_step(step),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
        ),
    )
    if previous_findings:
        state = _with_open_findings(state, ("C-01",))
    previous_findings_expected = previous_findings
    driver = NativeCodexDriver(
        snapshots=[_changes("b", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )
    advanced, history = WorkflowEngine(driver)._run_codex(
        state,
        _context(),
        WorkflowHistory(
            state.current_work_unit_id,
            findings=previous_findings,
        ),
    )

    assert advanced.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert history.findings == result_findings
    assert len(driver.persisted) == 1
    invocation = driver.codex_calls[0]
    assert invocation.native_request is not None
    assert invocation.native_request.document["request_type"] == request_type
    assert invocation.native_request.document["authorized_paths"] == sorted(
        state.current_slice.scope_paths
    )


def test_native_codex_correction_merges_offered_blocker_into_complete_ledger() -> None:
    findings = tuple(
        FindingRecord(
            finding_id=f"C-{index:02d}",
            finding_class=(
                FindingClass.BLOCKER if index == 3 else FindingClass.OBSERVATION
            ),
            status=FindingStatus.OPEN,
            summary=f"Finding {index}",
            acceptance_test=f"Acceptance {index}",
            origin=FindingOrigin("02", 1, AgentRole.CLAUDE),
        )
        for index in range(1, 5)
    )
    answered = apply_finding_response(
        findings[2],
        FindingResponseDecision.ACCEPTED,
        "The bounded blocker was corrected.",
    )

    @dataclass
    class SubsetCorrectionDriver(FakeDriver):
        persisted_previous: tuple[FindingRecord, ...] = ()

        def invoke_codex(
            self, invocation: CodexInvocation
        ) -> NativeAgentCodexOutput:
            self.codex_calls.append(invocation)
            assert invocation.native_request is not None
            assert tuple(
                item.finding_id
                for item in invocation.native_request.bound_context.context.previous_findings
            ) == ("C-03",)
            return NativeAgentCodexOutput(
                result=CodexContractResult(
                    ready=True,
                    stopped=False,
                    stop_request=None,
                    validation=None,
                    test_files=(TEST_FILE,),
                    findings=(answered,),
                    slice_plan=(),
                ),
                canonical_json='{"result_type":"correction_result"}',
                request_id=invocation.native_request.bound_context.request_id,
                response_sha256="b" * 64,
            )

        def persist_native_codex_contract(
            self,
            output: NativeAgentCodexOutput,
            request_sequence: int,
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            _ = output
            assert request_sequence == 1
            self.persisted_previous = previous_findings

    state = replace(
        _slice_state().with_current_step(WorkflowStep.CODEX_CORRECTION),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
        ),
    )
    state = _with_open_findings(state, ("C-03",))
    driver = SubsetCorrectionDriver(
        snapshots=[_changes("b", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )

    advanced, history = WorkflowEngine(driver)._run_codex(
        state,
        _context(),
        WorkflowHistory(state.current_work_unit_id, findings=findings),
    )

    assert advanced.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert history.findings == (findings[0], findings[1], answered, findings[3])
    assert driver.persisted_previous == findings


def test_native_codex_execution_packages_exclude_sibling_and_unaffected_evidence() -> None:
    plan = """# Approved multi-Slice plan

### Slice 1 - Target Slice

**Ziel**

Implement TARGET-GOAL-SENTINEL only.

**Exakter Änderungspfad**

- `src/early.py`
- `tests/test_workflow.py`

**Querverweise**

- `README.md#native-transport`

**\u0041kzeptanzkriterien**

- Preserve TARGET-CRITERION-SENTINEL.

### Slice 2 - SIBLING-TWO-SENTINEL

**\u0041kzeptanzkriterien**

- SECOND-CRITERION-SENTINEL

### Slice 3 - SIBLING-THREE-SENTINEL

**\u0041kzeptanzkriterien**

- THIRD-CRITERION-SENTINEL
"""
    implementation_state = _slice_state(
        scope_paths=("src/early.py", TEST_FILE)
    )
    implementation_contract = CodexStepContract(
        name="compact-implementation",
        readiness_marker=ReadinessMarker.IMPLEMENTATION,
        slice_id="01",
        round_number=1,
        require_test_files_record=True,
    )
    implementation = workflow_requests.native_codex_request(
        execution_error=WorkflowExecutionError,
        state=implementation_state,
        context=replace(_context(), approved_plan_text=plan),
        history=WorkflowHistory(implementation_state.current_work_unit_id),
        contract=implementation_contract,
        request_kind=NativeCodexRequestKind.IMPLEMENTATION,
    )
    implementation_json = implementation.canonical_json

    assert "TARGET-GOAL-SENTINEL" in implementation_json
    assert "TARGET-CRITERION-SENTINEL" in implementation_json
    assert "SIBLING-TWO-SENTINEL" not in implementation_json
    assert "SIBLING-THREE-SENTINEL" not in implementation_json
    assert plan not in implementation_json
    assert implementation.document["work_context"] == _context().distilled_context

    affected = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="AFFECTED-FINDING-SENTINEL",
        acceptance_test="AFFECTED-ACCEPTANCE-SENTINEL",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    unrelated = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.CLOSED,
        summary="UNRELATED-CLOSED-SENTINEL",
        acceptance_test="UNRELATED-ACCEPTANCE-SENTINEL",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
        status_rationale="Already closed.",
    )
    correction_state = _with_open_findings(
        implementation_state.with_current_step(WorkflowStep.CODEX_CORRECTION),
        ("C-01",),
    )
    correction_contract = replace(implementation_contract, round_number=2)
    correction = workflow_requests.native_codex_request(
        execution_error=WorkflowExecutionError,
        state=correction_state,
        context=_context(),
        history=WorkflowHistory(
            correction_state.current_work_unit_id,
            findings=(affected, unrelated),
        ),
        contract=correction_contract,
        request_kind=NativeCodexRequestKind.CORRECTION,
        correction_delta="CURRENT-DELTA-SENTINEL",
        correction_fingerprint="c" * 64,
        correction_findings=(affected, unrelated),
    )
    correction_json = correction.canonical_json

    assert correction.document["current_fingerprint"] == "c" * 64
    assert "CURRENT-DELTA-SENTINEL" in correction_json
    assert "AFFECTED-FINDING-SENTINEL" in correction_json
    assert "UNRELATED-CLOSED-SENTINEL" not in correction_json
    assert "PRIOR-FULL-DIFF-SENTINEL" not in correction_json


def test_native_codex_plan_bypasses_legacy_marker_parser(monkeypatch) -> None:
    @dataclass
    class NativePlanDriver(FakeDriver):
        persisted: list[NativeAgentCodexOutput] = field(default_factory=list)

        def invoke_codex(
            self, invocation: CodexInvocation
        ) -> NativeAgentCodexOutput:
            self.codex_calls.append(invocation)
            assert invocation.prompt == ""
            assert invocation.native_request is not None
            bound = invocation.native_request.bound_context
            result = CodexContractResult(
                ready=True,
                stopped=False,
                stop_request=None,
                validation=None,
                test_files=(),
                findings=(),
                slice_plan=(
                    PlannedSlice(
                        1,
                        "Create the native Codex work plan.",
                        ("docs/internal/native-codex-plan.md",),
                    ),
                ),
            )
            return NativeAgentCodexOutput(
                result=result,
                canonical_json=json.dumps(
                    {
                        "request_id": bound.request_id,
                        "result_type": "plan_result",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                request_id=bound.request_id,
                response_sha256="c" * 64,
            )

        def persist_native_codex_contract(
            self,
            output: NativeAgentCodexOutput,
            request_sequence: int,
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            assert request_sequence == 1
            assert previous_findings == ()
            self.persisted.append(output)

    state = replace(
        init_workflow_state(
            run_id="native-plan-runtime",
            task_file="/repo/task.md",
            branch="feature/workflow",
            branch_base=START_COMMIT,
            first_slice_start_commit=START_COMMIT,
            slice_count=1,
            task_digest="d" * 64,
            task_scope_patterns=("docs/internal/native-codex-plan.md",),
            target_branch="feature/workflow",
        ),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
        ),
    )
    context = replace(
        _context(),
        require_slice_plan=True,
        task_scope_patterns=("docs/internal/native-codex-plan.md",),
    )
    driver = NativePlanDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])
    advanced, history = WorkflowEngine(driver)._run_codex(
        state, context, WorkflowHistory(state.current_work_unit_id)
    )

    assert advanced.current_step is WorkflowStep.CLAUDE_PLAN_REVIEW
    assert history.findings == ()
    assert len(driver.persisted) == 1
    invocation = driver.codex_calls[0]
    assert invocation.native_request is not None
    assert invocation.native_request.document["request_type"] == "plan"


@pytest.mark.parametrize(
    "step",
    (WorkflowStep.CODEX_PLAN_REVISION, WorkflowStep.CODEX_CORRECTION),
)
def test_combined_native_codex_finding_steps_fail_before_provider_on_mirror_drift(
    step: WorkflowStep,
) -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The mirror differs from replay.",
        acceptance_test="No provider starts before reconciliation.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    if step is WorkflowStep.CODEX_PLAN_REVISION:
        state = init_workflow_state(
            run_id="combined-native-plan-drift",
            task_file="/repo/task.md",
            branch="feature/workflow",
            branch_base=START_COMMIT,
            first_slice_start_commit=START_COMMIT,
            slice_count=1,
            task_digest="d" * 64,
            task_scope_patterns=("docs/internal/native-plan.md",),
            target_branch="feature/workflow",
            protocol_binding=ProtocolBinding(
                ProtocolMode.STRUCTURED_V2,
                "2",
                claude_review_transport="native-claude-review-v2",
                codex_result_transport="native-codex-v2",
            ),
        ).with_current_step(step)
    else:
        state = replace(
            _slice_state().with_current_step(step),
            protocol_binding=ProtocolBinding(
                ProtocolMode.STRUCTURED_V2,
                "2",
                claude_review_transport="native-claude-review-v2",
                codex_result_transport="native-codex-v2",
            ),
        )
        state = _with_open_findings(state, ("C-01",))
    driver = FakeDriver(
        snapshots=[_changes("b", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
        authoritative_finding_error="authoritative finding replay differs from mirror",
    )

    with pytest.raises(WorkflowExecutionError, match="differs from mirror"):
        WorkflowEngine(driver)._run_codex(
            state,
            _context(),
            WorkflowHistory(state.current_work_unit_id, findings=(finding,)),
        )

    assert driver.codex_calls == []
    assert driver.authoritative_finding_calls == [(step.value, (finding,))]


def test_combined_native_claude_review_fails_before_provider_on_mirror_drift() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The mirror differs from replay.",
        acceptance_test="Claude is not started with mirror-only facts.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    state = replace(
        _slice_state().with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    )
    state = _with_open_findings(state, ("C-01",))
    driver = FakeDriver(
        snapshots=[_changes("b", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
        authoritative_finding_error="authoritative finding replay differs from mirror",
    )

    with pytest.raises(WorkflowExecutionError, match="differs from mirror"):
        WorkflowEngine(driver)._run_review(
            state,
            _context(),
            WorkflowHistory(state.current_work_unit_id, findings=(finding,)),
            AgentRole.CLAUDE,
        )

    assert driver.reviewer_calls == []
    assert driver.authoritative_finding_calls == [
        (WorkflowStep.CLAUDE_SLICE_REVIEW.value, (finding,))
    ]


def test_combined_native_slice_converges_without_legacy_parsers(monkeypatch) -> None:
    changes = _changes("b", "src/early.py", TEST_FILE)

    @dataclass
    class ConvergingNativeDriver(FakeDriver):
        persisted_reviews: list[NativeAgentReviewOutput] = field(default_factory=list)
        persisted_codex: list[NativeAgentCodexOutput] = field(default_factory=list)

        def invoke_reviewer(
            self, invocation: ReviewerInvocation
        ) -> NativeAgentReviewOutput:
            self.reviewer_calls.append(invocation)
            assert invocation.prompt == ""
            assert invocation.native_request is not None
            bound = invocation.native_request.bound_context
            attestation = bound.context.validation_attestation
            assert attestation is not None
            if not bound.context.previous_findings:
                finding = FindingRecord(
                    finding_id="C-01",
                    finding_class=FindingClass.BLOCKER,
                    status=FindingStatus.OPEN,
                    summary="The native convergence path needs a correction.",
                    acceptance_test="Codex dispositions are replayed into the next review.",
                    origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
                )
                approval = False
                findings = (finding,)
            else:
                finding = bound.context.previous_findings[0]
                assert finding.responses == (
                    FindingResponse(
                        FindingResponseDecision.ACCEPTED,
                        "The native correction satisfies the acceptance test.",
                    ),
                )
                approval = True
                findings = (
                    replace(
                        finding,
                        status=FindingStatus.CLOSED,
                        status_rationale="The corrected fingerprint proves convergence.",
                    ),
                )
            result = ContractResult(
                reviewer=AgentRole.CLAUDE,
                approval=approval,
                stopped=False,
                stop_request=None,
                validation=attestation,
                test_files=bound.context.test_files,
                pre_mortem="A future refactor could accidentally restore text parsing.",
                evidence=ReviewEvidence(
                    "native correction convergence",
                    "record/state drift",
                    "a legacy parser is invoked",
                ),
                findings=findings,
                anchors=(),
            )
            return NativeAgentReviewOutput(
                result=result,
                canonical_json=json.dumps(
                    {
                        "request_id": bound.request_id,
                        "decision": "approved" if approval else "denied",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                request_id=bound.request_id,
            )

        def invoke_codex(self, invocation: CodexInvocation) -> NativeAgentCodexOutput:
            self.codex_calls.append(invocation)
            assert invocation.prompt == ""
            assert invocation.native_request is not None
            bound = invocation.native_request.bound_context
            finding = bound.context.previous_findings[0]
            answered = replace(
                finding,
                responses=(
                    FindingResponse(
                        FindingResponseDecision.ACCEPTED,
                        "The native correction satisfies the acceptance test.",
                    ),
                ),
            )
            result = CodexContractResult(
                ready=True,
                stopped=False,
                stop_request=None,
                validation=None,
                test_files=(TEST_FILE,),
                findings=(answered,),
            )
            return NativeAgentCodexOutput(
                result=result,
                canonical_json=json.dumps(
                    {
                        "request_id": bound.request_id,
                        "result_type": "correction_result",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                request_id=bound.request_id,
                response_sha256="c" * 64,
            )

        def persist_native_review_contract(
            self,
            output: NativeAgentReviewOutput,
            _fingerprint: str,
            _round_number: int,
            _request_sequence: int,
            _previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            self.persisted_reviews.append(output)

        def persist_native_codex_contract(
            self,
            output: NativeAgentCodexOutput,
            _request_sequence: int,
            _previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            self.persisted_codex.append(output)

    state = replace(
        _slice_state().with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    )
    driver = ConvergingNativeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[],
        deltas={(changes.fingerprint, changes.fingerprint): changes.full_diff},
        convergence_evaluations=[_discovery_convergence()],
    )
    state, history = WorkflowEngine(driver)._run_review(
        state,
        _context(),
        WorkflowHistory(state.current_work_unit_id),
        AgentRole.CLAUDE,
    )
    assert state.current_step is WorkflowStep.CODEX_CORRECTION
    state, history = WorkflowEngine(driver)._run_codex(state, _context(), history)
    assert state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    state, history = WorkflowEngine(driver)._run_review(
        state, _context(), history, AgentRole.CLAUDE
    )

    assert state.current_step is WorkflowStep.SLICE_COMMIT
    assert history.findings[0].status is FindingStatus.CLOSED
    assert len(driver.persisted_reviews) == 2
    assert len(driver.persisted_codex) == 1
    assert all(call.native_request is not None for call in driver.reviewer_calls)
    assert driver.codex_calls[0].native_request is not None
    assert [item[0] for item in driver.authoritative_finding_calls] == [
        WorkflowStep.CLAUDE_SLICE_REVIEW.value,
        WorkflowStep.CODEX_CORRECTION.value,
        WorkflowStep.CLAUDE_SLICE_REVIEW.value,
    ]


def test_combined_native_plan_revision_converges_without_legacy_parsers(
    monkeypatch,
) -> None:
    changes = _changes("d", "docs/internal/native-plan.md")

    @dataclass
    class ConvergingNativePlanDriver(FakeDriver):
        persisted_reviews: list[NativeAgentReviewOutput] = field(default_factory=list)
        persisted_codex: list[NativeAgentCodexOutput] = field(default_factory=list)

        def invoke_reviewer(
            self, invocation: ReviewerInvocation
        ) -> NativeAgentReviewOutput:
            self.reviewer_calls.append(invocation)
            assert invocation.prompt == ""
            assert invocation.native_request is not None
            bound = invocation.native_request.bound_context
            attestation = bound.context.validation_attestation
            assert attestation is not None
            if not bound.context.previous_findings:
                findings = (
                    FindingRecord(
                        finding_id="C-01",
                        finding_class=FindingClass.BLOCKER,
                        status=FindingStatus.OPEN,
                        summary="The native plan omits a required boundary.",
                        acceptance_test="The revised plan binds the boundary.",
                        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
                    ),
                )
                approval = False
            else:
                finding = bound.context.previous_findings[0]
                assert finding.responses[-1] == FindingResponse(
                    FindingResponseDecision.ACCEPTED,
                    "The revised plan binds the required boundary.",
                )
                findings = (
                    replace(
                        finding,
                        status=FindingStatus.CLOSED,
                        status_rationale="The plan revision satisfies the finding.",
                    ),
                )
                approval = True
            return NativeAgentReviewOutput(
                result=ContractResult(
                    reviewer=AgentRole.CLAUDE,
                    approval=approval,
                    stopped=False,
                    stop_request=None,
                    validation=attestation,
                    test_files=bound.context.test_files,
                    pre_mortem="A future plan revision could lose its finding binding.",
                    evidence=ReviewEvidence(
                        "native plan revision",
                        "record/state drift",
                        "a legacy plan parser is invoked",
                    ),
                    findings=findings,
                    anchors=(),
                ),
                canonical_json=json.dumps(
                    {
                        "request_id": bound.request_id,
                        "decision": "approved" if approval else "denied",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                request_id=bound.request_id,
            )

        def invoke_codex(self, invocation: CodexInvocation) -> NativeAgentCodexOutput:
            self.codex_calls.append(invocation)
            assert invocation.step is WorkflowStep.CODEX_PLAN_REVISION
            assert invocation.prompt == ""
            assert invocation.native_request is not None
            bound = invocation.native_request.bound_context
            assert bound.context.request_kind is NativeCodexRequestKind.PLAN
            finding = bound.context.previous_findings[0]
            answered = replace(
                finding,
                responses=(
                    FindingResponse(
                        FindingResponseDecision.ACCEPTED,
                        "The revised plan binds the required boundary.",
                    ),
                ),
            )
            return NativeAgentCodexOutput(
                result=CodexContractResult(
                    ready=True,
                    stopped=False,
                    stop_request=None,
                    validation=None,
                    test_files=(),
                    findings=(answered,),
                    slice_plan=(
                        PlannedSlice(
                            1,
                            "Implement the bound native plan.",
                            ("docs/internal/native-plan.md",),
                        ),
                    ),
                ),
                canonical_json=json.dumps(
                    {
                        "request_id": bound.request_id,
                        "result_type": "plan_result",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                request_id=bound.request_id,
                response_sha256="e" * 64,
            )

        def persist_native_review_contract(
            self,
            output: NativeAgentReviewOutput,
            _fingerprint: str,
            _round_number: int,
            _request_sequence: int,
            _previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            self.persisted_reviews.append(output)

        def persist_native_codex_contract(
            self,
            output: NativeAgentCodexOutput,
            _request_sequence: int,
            _previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            self.persisted_codex.append(output)

    state = init_workflow_state(
        run_id="combined-native-plan-convergence",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=1,
        task_digest="d" * 64,
        task_scope_patterns=("docs/internal/native-plan.md",),
        target_branch="feature/workflow",
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    ).with_current_step(WorkflowStep.CLAUDE_PLAN_REVIEW)
    context = replace(
        _context(),
        require_slice_plan=True,
        task_scope_patterns=("docs/internal/native-plan.md",),
    )
    driver = ConvergingNativePlanDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[],
        deltas={(changes.fingerprint, changes.fingerprint): changes.full_diff},
    )
    state, history = WorkflowEngine(driver)._run_review(
        state,
        context,
        WorkflowHistory(state.current_work_unit_id),
        AgentRole.CLAUDE,
    )
    assert state.current_step is WorkflowStep.CODEX_PLAN_REVISION
    state, history = WorkflowEngine(driver)._run_codex(state, context, history)
    assert state.current_step is WorkflowStep.CLAUDE_PLAN_REVIEW
    state, history = WorkflowEngine(driver)._run_review(
        state, context, history, AgentRole.CLAUDE
    )

    assert state.current_step is WorkflowStep.COMPLETED
    assert history.findings[0].status is FindingStatus.CLOSED
    assert len(driver.persisted_reviews) == 2
    assert len(driver.persisted_codex) == 1
    assert [item[0] for item in driver.authoritative_finding_calls] == [
        WorkflowStep.CLAUDE_PLAN_REVIEW.value,
        WorkflowStep.CODEX_PLAN_REVISION.value,
        WorkflowStep.CLAUDE_PLAN_REVIEW.value,
    ]


@dataclass
class BatchedFinalReviewDriver(FakeDriver):
    batch_size: int = 25
    approve_complete: bool = True
    pending_ids: tuple[str, ...] = ()
    authoritative_findings: tuple[FindingRecord, ...] | None = None
    requested_ids: list[tuple[str, ...]] = field(default_factory=list)

    def authoritative_native_findings(
        self,
        _state: WorkflowState,
        mirror_findings: tuple[FindingRecord, ...],
    ) -> tuple[FindingRecord, ...]:
        return (
            mirror_findings
            if self.authoritative_findings is None
            else self.authoritative_findings
        )

    def authoritative_final_review_findings(
        self,
        _state: WorkflowState,
        mirror_findings: tuple[FindingRecord, ...],
    ) -> tuple[FindingRecord, ...]:
        pending = frozenset(self.pending_ids)
        source = (
            mirror_findings
            if self.authoritative_findings is None
            else self.authoritative_findings
        )
        return tuple(
            item for item in source if item.finding_id in pending
        )

    def invoke_reviewer(
        self, invocation: ReviewerInvocation
    ) -> NativeAgentReviewOutput:
        self.reviewer_calls.append(invocation)
        offered = tuple(item.finding_id for item in invocation.previous_findings)
        self.requested_ids.append(offered)
        if self.reviewer_failures:
            failure = self.reviewer_failures.pop(0)
            if failure is not None:
                raise failure
        selected = offered[: self.batch_size]
        assert invocation.native_request is not None
        pending_count = invocation.native_request.document["review_contract"][
            "disposition_budget"
        ]["pending_finding_count"]
        complete = (
            len(selected) == len(offered) == pending_count
            and self.approve_complete
        )
        lines = [
            *(
                f"FINDING_STATUS: {finding_id} | CLOSED | "
                "The bound final-review round disposes this finding."
                for finding_id in selected
            ),
            f"FINAL_APPROVAL: {'YES' if complete else 'NO'}",
            "REVIEW_EVIDENCE: final disposition coverage | stale open finding | "
            "a missing identifier reaches approval",
        ]
        if complete:
            lines.append(
                "PRE_MORTEM: A later change could bypass the final disposition ledger."
            )
        return _test_native_review_output(invocation, "\n".join(lines))

    def persist_native_review_contract(
        self,
        output: NativeAgentReviewOutput,
        _fingerprint: str,
        _round_number: int,
        _request_sequence: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        current = {item.finding_id: item for item in output.result.findings}
        disposed = {
            item.finding_id
            for item in previous_findings
            if current[item.finding_id] != item
        }
        self.pending_ids = tuple(
            finding_id
            for finding_id in self.pending_ids
            if finding_id not in disposed
        )


def _batched_final_review_case(
    finding_count: int,
    *,
    batch_size: int,
    approve_complete: bool = True,
    reviewer_failures: list[AgentInvocationError | None] | None = None,
    context: WorkflowContext | None = None,
) -> tuple[WorkflowRunResult, BatchedFinalReviewDriver]:
    findings = tuple(
        FindingRecord(
            finding_id=f"C-{number:02d}",
            finding_class=FindingClass.BLOCKER,
            status=FindingStatus.OPEN,
            summary=f"Final review finding {number}.",
            acceptance_test=f"Disposition {number} is recorded.",
            origin=FindingOrigin(f"{((number - 1) % 42) + 1:02d}", 1, AgentRole.CLAUDE),
        )
        for number in range(1, finding_count + 1)
    )
    state = replace(
        _completed_single_slice_state().start_final_review_work_unit(),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    ).with_current_step(WorkflowStep.CLAUDE_FINAL_REVIEW)
    history = WorkflowHistory(
        state.current_work_unit_id,
        findings=findings,
        codex_final_report='{"result_type":"final_report_result"}',
    )
    changes = _changes("f", "src/early.py", TEST_FILE)
    driver = BatchedFinalReviewDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[],
        batch_size=batch_size,
        approve_complete=approve_complete,
        pending_ids=tuple(item.finding_id for item in findings),
        reviewer_failures=[] if reviewer_failures is None else reviewer_failures,
    )
    return WorkflowEngine(driver, sleep_fn=lambda _seconds: None).run_current_work_unit(
        state, _context() if context is None else context, history
    ), driver


@pytest.mark.parametrize("review_type", ("slice review", "final review"))
@pytest.mark.parametrize(
    "failure_case, expected",
    (
        ("omission", "omits an offered"),
        ("offered-drift", "subset differs from the complete ledger"),
        ("foreign-mutation", "mutates an unoffered"),
    ),
)
def test_subset_merge_diagnostics_name_the_actual_review_type(
    review_type: str,
    failure_case: str,
    expected: str,
) -> None:
    authoritative = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="Existing finding",
        acceptance_test="Keep its identity stable.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    if failure_case == "omission":
        offered = (authoritative,)
        returned = ()
    elif failure_case == "offered-drift":
        offered = (replace(authoritative, summary="Stale mirror"),)
        returned = offered
    else:
        offered = ()
        returned = (
            replace(
                authoritative,
                status=FindingStatus.CLOSED,
                status_rationale="Unexpected update.",
            ),
        )

    with pytest.raises(
        WorkflowExecutionError,
        match=rf"{expected} {review_type}|{review_type} finding {expected}",
    ):
        WorkflowEngine._merge_review_request_subset(
            (authoritative,),
            offered,
            returned,
            review_type=review_type,
        )


def test_subset_merge_names_a_reused_number_as_a_collision() -> None:
    authoritative = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="Earlier finding",
        acceptance_test="The number remains reserved.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    colliding = replace(
        authoritative,
        summary="New finding with an old number",
        origin=FindingOrigin("33", 1, AgentRole.CLAUDE),
    )

    with pytest.raises(
        WorkflowExecutionError,
        match=r"slice review.*collision.*C-01",
    ):
        WorkflowEngine._merge_review_request_subset(
            (authoritative,),
            (),
            (colliding,),
            review_type="slice review",
        )


def test_recovered_implementer_subset_is_validated_at_request_time_then_confirmed_now() -> None:
    offered = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The request offered this finding before record-ahead persistence.",
        acceptance_test="Resume accepts the already persisted disposition.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    returned = apply_finding_response(
        offered,
        FindingResponseDecision.ACCEPTED,
        "The request-time acceptance test is satisfied.",
    )
    comparison = RecoveredFindingComparison(
        request_findings=(offered,),
        offered_findings=(offered,),
        request_position="request-ledger measurement=ar1-request",
        recovery_position="recovery-ledger head=ar1-current",
    )

    assert _merge_request_finding_subset(
        (returned,),
        (returned,),  # freshly rebuilt request; intentionally not the comparison basis
        (returned,),
        recovered_comparison=comparison,
    ) == (returned,)


def test_recovered_final_review_subset_uses_the_historical_disposition_batch() -> None:
    first = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="First final-review disposition.",
        acceptance_test="Close the first historical item.",
        origin=FindingOrigin("FINAL", 1, AgentRole.CLAUDE),
    )
    second = replace(first, finding_id="C-02", summary="Second disposition.")
    closed_first = replace(
        first,
        status=FindingStatus.CLOSED,
        status_rationale="The final reviewer verified the correction.",
    )
    returned = (closed_first, second)
    comparison = RecoveredFindingComparison(
        request_findings=(first, second),
        offered_findings=(first, second),
        request_position="request-ledger measurement=ar1-review-request",
        recovery_position="recovery-ledger head=ar1-review-current",
    )

    assert WorkflowEngine._merge_review_request_subset(
        returned,
        (second,),  # today's pending batch has already lost the closed item
        returned,
        review_type="final review",
        recovered_comparison=comparison,
    ) == returned


@pytest.mark.parametrize("reviewer", (False, True), ids=("implementer", "reviewer"))
def test_invalid_recovered_subset_diagnostic_names_request_and_recovery_time(
    reviewer: bool,
) -> None:
    offered = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The original request offered this finding.",
        acceptance_test="Reject a response that omitted it at request time.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    comparison = RecoveredFindingComparison(
        request_findings=(offered,),
        offered_findings=(offered,),
        request_position="request-ledger measurement=ar1-request",
        recovery_position="recovery-ledger head=ar1-recovery",
    )

    with pytest.raises(WorkflowExecutionError) as caught:
        if reviewer:
            WorkflowEngine._merge_review_request_subset(
                (offered,),
                (),
                (),
                review_type="final review",
                recovered_comparison=comparison,
            )
        else:
            _merge_request_finding_subset(
                (offered,),
                (),
                (),
                recovered_comparison=comparison,
            )

    diagnostic = str(caught.value)
    assert "request-time offered/returned measured-at: request-ledger" in diagnostic
    assert "recovery-time authoritative compared-at: recovery-ledger" in diagnostic


def test_slice_review_reserves_finding_numbers_from_authoritative_replay() -> None:
    ledger = tuple(
        FindingRecord(
            finding_id=f"C-{number:02d}",
            finding_class=FindingClass.OBSERVATION,
            status=(FindingStatus.CLOSED if number == 23 else FindingStatus.OPEN),
            summary=f"Finding {number}",
            acceptance_test=f"Finding {number} remains reserved.",
            origin=FindingOrigin("01", number, AgentRole.CLAUDE),
            status_rationale=("Verified earlier." if number == 23 else None),
        )
        for number in range(1, 66)
    )

    @dataclass
    class AuthoritativeReviewDriver(FakeDriver):
        def authoritative_native_findings(
            self,
            state: WorkflowState,
            mirror_findings: tuple[FindingRecord, ...],
        ) -> tuple[FindingRecord, ...]:
            self.authoritative_finding_calls.append(
                (state.current_step.value, mirror_findings)
            )
            return ledger

    changes = _changes("f", "src/early.py", TEST_FILE)
    state = replace(
        _slice_state(
            scope_paths=("src/early.py", TEST_FILE)
        ).with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    )
    driver = AuthoritativeReviewDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[
            "\n".join(
                (
                    "REVIEWER: claude",
                    f"TEST_FILES_TOUCHED: {TEST_FILE}",
                    "NEW_FINDING: C-66 | OBSERVATION | new issue | verify later",
                    "REVIEW_EVIDENCE: chain numbering | stale subset | C-01 is reused",
                    "PRE_MORTEM: a reduced request could reset the sequence",
                    "SLICE_APPROVAL: 01 | YES",
                    "STATUS: DONE",
                )
            )
        ],
    )

    next_state, next_history = WorkflowEngine(driver)._run_review(
        state,
        _context(),
        WorkflowHistory(state.current_work_unit_id),
        AgentRole.CLAUDE,
    )

    assert next_state.current_step is WorkflowStep.SLICE_COMMIT
    assert tuple(item.finding_id for item in next_history.findings) == tuple(
        f"C-{number:02d}" for number in range(1, 67)
    )
    assert len(driver.reviewer_calls) == 1
    invocation = driver.reviewer_calls[0]
    assert invocation.previous_findings == ()
    assert invocation.native_request is not None
    assert (
        invocation.native_request.document["review_contract"]["next_finding_id"]
        == "C-66"
    )


def test_native_codex_correction_binds_record_authority_before_recovery() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The durable response is ahead of state.",
        acceptance_test="Resume reuses it without a provider start.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    answered = replace(
        finding,
        responses=(
            FindingResponse(
                FindingResponseDecision.ACCEPTED,
                "The correction now satisfies the acceptance test.",
            ),
        ),
    )
    result = CodexContractResult(
        ready=True,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=(TEST_FILE,),
        findings=(answered,),
    )
    recovered_output = NativeAgentCodexOutput(
        result=result,
        canonical_json='{"result_type":"correction_result"}',
        request_id="native-codex-request-" + "a" * 64,
        response_sha256="b" * 64,
        recovered_finding_comparison=RecoveredFindingComparison(
            request_findings=(finding,),
            offered_findings=(finding,),
            request_position="request-ledger measurement=ar1-request",
            recovery_position="recovery-ledger head=ar1-current",
        ),
    )

    @dataclass
    class RecoveryDriver(FakeDriver):
        persisted: list[NativeAgentCodexOutput] = field(default_factory=list)

        def authoritative_native_findings(
            self,
            state: WorkflowState,
            mirror_findings: tuple[FindingRecord, ...],
        ) -> tuple[FindingRecord, ...]:
            self.authoritative_finding_calls.append(
                (state.current_step.value, mirror_findings)
            )
            return (answered,)

        def carry_forward_native_findings(
            self,
            _state: WorkflowState,
            _current_findings: tuple[FindingRecord, ...],
        ) -> tuple[FindingRecord, ...]:
            return (answered,)

        def recover_pending_native_codex(self, *_args):  # type: ignore[no-untyped-def]
            return recovered_output

        def invoke_codex(self, invocation: CodexInvocation) -> NativeAgentCodexOutput:
            raise AssertionError("record-ahead recovery must suppress the provider")

        def persist_native_codex_contract(
            self,
            output: NativeAgentCodexOutput,
            request_sequence: int,
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            assert request_sequence == 1
            assert previous_findings == (answered,)
            self.persisted.append(output)

    state = replace(
        _slice_state().with_current_step(WorkflowStep.CODEX_CORRECTION),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    )
    state = _with_open_findings(state, ("C-01",))
    driver = RecoveryDriver(
        snapshots=[_changes("b", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )

    advanced, history = WorkflowEngine(driver)._run_codex(
        state,
        _context(),
        WorkflowHistory(state.current_work_unit_id, findings=(finding,)),
    )

    assert advanced.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert history.findings == (answered,)
    assert driver.authoritative_finding_calls == [
        (WorkflowStep.CODEX_CORRECTION.value, (finding,))
    ]
    assert driver.persisted == [recovered_output]


@pytest.mark.parametrize(
    ("step", "marker", "expected_kind", "evidence_kind"),
    (
        (
            WorkflowStep.CLAUDE_PLAN_REVIEW,
            ApprovalMarker.PLAN,
            "plan",
            EvidenceKind.FULL_SLICE,
        ),
        (
            WorkflowStep.CLAUDE_SLICE_REVIEW,
            ApprovalMarker.SLICE,
            "slice",
            EvidenceKind.FULL_SLICE,
        ),
        (
            WorkflowStep.CLAUDE_BRANCH_DISCOVERY,
            ApprovalMarker.BRANCH_DISCOVERY,
            "branch_discovery",
            EvidenceKind.FULL_BRANCH,
        ),
    ),
)
def test_native_request_builder_covers_plan_slice_and_branch_discovery_reviews(
    step: WorkflowStep,
    marker: ApprovalMarker,
    expected_kind: str,
    evidence_kind: EvidenceKind,
) -> None:
    changes = _changes("e", "src/early.py", TEST_FILE)
    state = replace(
        _slice_state().with_current_step(step),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            "native-claude-review-v2",
        ),
    )
    history = WorkflowHistory(state.current_work_unit_id)
    contract = StepContract(
        f"native-{expected_kind}-review",
        AgentRole.CLAUDE,
        marker,
        "DISCOVERY" if marker is ApprovalMarker.BRANCH_DISCOVERY else "01",
        1,
        changes.fingerprint,
        _attestation(changes),
    )

    bundle = workflow_requests.native_review_request(
        execution_error=WorkflowExecutionError,
        full_branch_evidence_kind=EvidenceKind.FULL_BRANCH,
        state=state,
        context=_context(),
        history=history,
        contract=contract,
        changes=changes,
        evidence_kind=evidence_kind,
        review_diff=changes.full_diff,
        review_packet=None,
        expected_test_files=(TEST_FILE,),
    )

    assert bundle.document["review_kind"] == expected_kind
    assert bundle.document["operation"] == step.value
    evidence_ids = {
        item["evidence_id"] for item in bundle.document["evidence_manifest"]
    }
    assert "codex-final-report" not in evidence_ids


def test_native_request_reuses_restored_legacy_review_packet_without_semantic_digest() -> None:
    changes = _changes("e", "src/early.py", TEST_FILE)
    state = replace(
        _slice_state().with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            "native-claude-review-v2",
        ),
    )
    canonical = json.dumps(
        {
            "schema": "review-packet-v1",
            "purpose": "slice",
            "fingerprint": changes.fingerprint,
            "manifest": {"paths": sorted(("src/early.py", TEST_FILE))},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    packet = ReviewPacket.restore(canonical, hashlib.sha256(canonical).hexdigest())
    history = WorkflowHistory.from_dict(
        WorkflowHistory(state.current_work_unit_id, active_review_packet=packet).to_dict()
    )
    contract = StepContract(
        "native-legacy-packet-review",
        AgentRole.CLAUDE,
        ApprovalMarker.SLICE,
        "01",
        2,
        changes.fingerprint,
        _attestation(changes),
    )

    bundle = workflow_requests.native_review_request(
        execution_error=WorkflowExecutionError,
        full_branch_evidence_kind=EvidenceKind.FULL_BRANCH,
        state=state,
        context=_context(),
        history=history,
        contract=contract,
        changes=changes,
        evidence_kind=EvidenceKind.FULL_SLICE,
        review_diff=changes.full_diff,
        review_packet=history.active_review_packet,
        expected_test_files=(TEST_FILE,),
    )

    packet_item = next(
        item
        for item in bundle.document["evidence_manifest"]
        if item["evidence_id"] == "review-packet"
    )
    assert packet_item["sha256"] == packet.digest
    assert "semantic_digest" not in packet_item


def test_native_record_ahead_recovery_receives_full_history_and_skips_provider() -> None:
    changes = _changes("d", "src/early.py", TEST_FILE)
    offered = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The recovered review received the request-time state.",
        acceptance_test="The recovered persistence call keeps that same state.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    persisted = replace(
        offered,
        status=FindingStatus.CLOSED,
        status_rationale="The record-ahead review already closed the finding.",
    )

    @dataclass
    class RecoveringNativeDriver(FakeDriver):
        recovered_history: WorkflowHistory | None = None
        persisted_native: list[NativeAgentReviewOutput] = field(default_factory=list)

        def recover_pending_native_reviewer(
            self,
            invocation: ReviewerInvocation,
            contract: StepContract,
            history: WorkflowHistory,
        ) -> NativeAgentReviewOutput:
            assert isinstance(history, WorkflowHistory)
            assert len(history.events) == 1
            assert invocation.native_request is not None
            self.recovered_history = history
            bound = invocation.native_request.bound_context
            attestation = bound.context.validation_attestation
            assert attestation == contract.validation_attestation
            request_context = replace(
                bound.context,
                previous_findings=(offered,),
                known_open_findings=(offered,),
            )
            return NativeAgentReviewOutput(
                result=ContractResult(
                    reviewer=AgentRole.CLAUDE,
                    approval=True,
                    stopped=False,
                    stop_request=None,
                    validation=attestation,
                    test_files=bound.context.test_files,
                    pre_mortem="A recovery caller may pass only finding records.",
                    evidence=ReviewEvidence(
                        "record-ahead recovery",
                        "call-site type drift",
                        "the provider is invoked again",
                    ),
                    findings=(persisted,),
                    anchors=(),
                ),
                canonical_json=(
                    '{"request_id":"' + bound.request_id + '","result":"approved"}'
                ),
                request_id=bound.request_id,
                context=request_context,
                recovered_finding_comparison=RecoveredFindingComparison(
                    request_findings=(offered,),
                    offered_findings=(offered,),
                    request_position="request-ledger measurement=ar1-request",
                    recovery_position="recovery-ledger head=ar1-current",
                ),
            )

        def invoke_reviewer(self, invocation: ReviewerInvocation):  # type: ignore[no-untyped-def]
            raise AssertionError("provider must not run during record-ahead recovery")

        def persist_native_review_contract(
            self,
            output: NativeAgentReviewOutput,
            fingerprint: str,
            round_number: int,
            request_sequence: int,
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            assert fingerprint == changes.fingerprint
            assert round_number == 1
            assert request_sequence == 1
            assert previous_findings == (offered,)
            self.persisted_native.append(output)

    state = replace(
        _slice_state().with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            "native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    )
    history = WorkflowHistory(state.current_work_unit_id, findings=(persisted,))
    driver = RecoveringNativeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[],
    )

    advanced, recovered = WorkflowEngine(driver)._run_review(
        state,
        _context(),
        history,
        AgentRole.CLAUDE,
    )

    assert driver.recovered_history is not None
    assert len(driver.recovered_history.events) == 1
    assert len(driver.persisted_native) == 1
    assert driver.authoritative_finding_calls == []
    assert advanced.current_step is WorkflowStep.SLICE_COMMIT
    assert recovered.latest_claude_review is not None
    assert recovered.findings == (persisted,)


def test_managed_audit_paths_are_added_after_codex_plan_only() -> None:
    state = init_workflow_state(
        run_id="run-managed-audit",
        task_file="/repo/inbox/Bug.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=1,
        timestamp="2026-08-12T10:00:00+00:00",
        task_digest="a" * 64,
        task_scope_patterns=("docs/internal/bug-review-12345678.md",),
        target_branch="feature/workflow",
    )
    output = "\n".join(
        (
            "SLICE_PLAN: 1 | Fix rounding behavior | src/rounding.py",
            "PLAN_READY: YES",
            "STATUS: DONE",
        )
    )
    driver = FakeDriver(snapshots=[], codex_outputs=[output], reviewer_outputs=[])
    context = replace(
        _context(),
        require_slice_plan=True,
        task_scope_patterns=(
            "docs/internal/bug-review-12345678.md",
            "docs/internal/slice-bug-*.md",
            "src/rounding.py",
        ),
        audit_report_path="docs/internal/bug-review-12345678.md",
    )

    planned_state, _history = WorkflowEngine(driver)._run_codex(
        state, context, WorkflowHistory(1)
    )

    assert planned_state.planned_slices[0].scope_paths == (
        "docs/internal/bug-review-12345678.md",
        "docs/internal/slice-bug-01-fix-rounding-behavior.md",
        "src/rounding.py",
    )


def test_reopened_exact_gate_reuses_immutable_approval_without_dual_write() -> None:
    state = init_workflow_state(
        run_id="run-reopened-gate",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=1,
        timestamp="2026-08-12T10:00:00+00:00",
    )
    paths = ("src/agent_runtime.py", "tests/test_agent_runtime.py")
    fingerprint = "a" * 64
    gated = state.await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="reviewed hotfix paths",
        fingerprint=fingerprint,
        paths=paths,
    )
    driver = FakeDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])
    persisted: list[GateDecisionRecord] = []
    driver.persist_gate_decision = (  # type: ignore[attr-defined]
        lambda _work_unit_id, decision: persisted.append(decision)
    )
    engine = WorkflowEngine(driver)

    first = engine.decide_current_gate(
        gated,
        WorkflowHistory(1),
        approved=True,
        rationale="fingerprint-bound hotfix reviewed",
    )
    reopened = first.state.await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="same gate rediscovered after resume",
        fingerprint=fingerprint,
        paths=paths,
    )
    resumed = engine.decide_current_gate(
        reopened,
        first.history,
        approved=True,
        rationale="second confirmation must not rewrite immutable audit semantics",
    )

    assert resumed.state.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    assert resumed.state.current_work_unit.gate.reason is GateReason.NONE
    assert len(resumed.state.current_work_unit.gate_decisions) == 1
    assert resumed.state.current_work_unit.gate_decisions[0].rationale == (
        "fingerprint-bound hotfix reviewed"
    )
    assert len(persisted) == 1


def test_plan_only_rejects_future_product_slices_as_executable_records() -> None:
    state = init_workflow_state(
        run_id="run-plan-only",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=1,
        timestamp="2026-08-12T10:00:00+00:00",
        task_digest="a" * 64,
        task_scope_patterns=("docs/internal/plan.md",),
        target_branch="feature/workflow",
    )
    output = "\n".join(
        (
            "SLICE_PLAN: 1 | write plan | docs/internal/plan.md",
            "SLICE_PLAN: 2 | implement product | src/app.py",
            "PLAN_READY: YES",
            "STATUS: DONE",
        )
    )
    driver = FakeDriver(snapshots=[], codex_outputs=[output], reviewer_outputs=[])
    context = replace(
        _context(),
        require_slice_plan=True,
        plan_only=True,
        task_scope_patterns=("docs/internal/plan.md",),
        work_plan_path="docs/internal/plan.md",
    )

    with pytest.raises(WorkflowExecutionError, match="TASK-SCOPE"):
        run_v3_work_unit(WorkflowEngine(driver), state, context)


def test_new_typed_acceptance_requirement_is_measured_at_existing_fingerprint() -> None:
    unchanged = _changes("1", "src/early.py", TEST_FILE)
    denial = "\n".join(
        (
            "REVIEWER: claude",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            'NEW_FINDING: C-01 | BLOCKER | focused regression required | VALIDATE: ["python3","-m","pytest","tests/test_focus.py","-q"]',
            "SLICE_APPROVAL: 01 | NO",
            "STATUS: DONE",
        )
    )
    driver = FakeDriver(
        snapshots=[unchanged, unchanged],
        codex_outputs=[_codex_ready(), _codex_ready("C-01")],
        reviewer_outputs=[denial, _review_stop(AgentRole.CLAUDE, "CONTRACT-UNCLEAR")],
        deltas={(unchanged.fingerprint, unchanged.fingerprint): "no content change"},
        convergence_evaluations=[_discovery_convergence()],
    )

    result = WorkflowEngine(driver).run_current_work_unit(
        _slice_state(), _context()
    )

    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert len(driver.validation_requests) == 2
    assert driver.validation_requests[-1].expected_commands == (
        "python3 -m pytest tests/ -v",
        "python3 -m pytest tests/test_focus.py -q",
    )
    finding = next(item for item in result.history.findings if item.finding_id == "C-01")
    assert len(finding.acceptance_measurements) == 1
    assert finding.acceptance_measurements[0].fingerprint == unchanged.fingerprint


def test_explicit_failed_retry_runs_once_then_reuses_result_for_review_chain() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    failed = replace(
        _attestation(changes),
        records=(
            ValidationRecord(
                ValidationStatus.FAIL,
                _attestation(changes).expected_commands[0],
                1,
                "red",
            ),
        ),
        summary="validation failed",
    )
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[],
    )
    engine = WorkflowEngine(driver)
    history = WorkflowHistory(2, attestations=(failed,))
    driver.validation_recoveries[
        (
            changes.fingerprint,
            failed.expected_commands,
            failed.attestation_id,
        )
    ] = failed

    retried, retried_history = engine._attestation(
        changes,
        history,
        replace(_context(), retry_failed_validation=True),
        1,
    )
    reused, reused_history = engine._attestation(
        changes,
        retried_history,
        replace(_context(), retry_failed_validation=True),
        1,
    )

    assert [request.attempt_number for request in driver.validation_requests] == [2]
    assert driver.validation_recovery_calls == [
        (
            changes.fingerprint,
            failed.expected_commands,
            f"validation-{changes.fingerprint[:12]}-retry-2",
        )
    ]
    assert retried.passed is True
    assert reused is retried
    assert reused_history is retried_history


def test_plan_contract_validation_does_not_recover_matrix_content() -> None:
    changes = _changes("1", "docs/internal/plan.md")
    driver = FakeDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])

    attestation, _history = WorkflowEngine(driver)._attestation(
        changes,
        WorkflowHistory(1),
        replace(
            _context(),
            plan_only=True,
            task_scope_patterns=("docs/internal/plan.md",),
            work_plan_path="docs/internal/plan.md",
        ),
        1,
        plan_contract=True,
    )

    assert attestation.diff_fingerprint == changes.fingerprint
    assert driver.validation_recovery_calls == []


def test_absolute_quota_backstop_stops_with_terminal_verdict_without_reviewer() -> None:
    now = [datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes, changes],
        codex_outputs=[],
        reviewer_outputs=[],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-first",
                received_at=now[0],
                reset_after_seconds=1,
            ),
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-second",
                received_at=now[0] + timedelta(seconds=1),
                reset_after_seconds=1,
            ),
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    engine = WorkflowEngine(driver, now_fn=lambda: now[0], sleep_fn=sleep)
    result = engine.run_current_work_unit(
        _slice_state(),
        replace(
            _context(),
            quota_wait_policy=QuotaWaitPolicy(
                safety_margin_seconds=0,
                maximum_wait_seconds=60,
                maximum_auto_resumes=1,
                heartbeat_interval_seconds=1,
            ),
        ),
    )

    assert result.exit_code == 5
    assert result.workflow_rejected
    assert result.rejection_code == "QUOTA-AUTOMATION-STOPPED"
    assert result.state.current_work_unit.status is WorkUnitStatus.COMPLETED
    assert result.state.current_work_unit.gate.status is GateStatus.CLEAR
    assert len(result.state.current_work_unit.invocation_failures) == 2
    assert result.state.current_work_unit.invocation_failures[-1].automatic_resume is False
    assert len(driver.codex_calls) == 2
    assert driver.reviewer_calls == []


def test_multiple_progressing_quota_windows_resume_automatically() -> None:
    now = [datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes, changes, changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-window-1",
                received_at=now[0],
                reset_after_seconds=1,
            ),
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-window-2",
                received_at=now[0] + timedelta(seconds=1),
                reset_after_seconds=2,
            ),
            None,
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(
        _slice_state(),
        replace(
            _context(),
            quota_wait_policy=QuotaWaitPolicy(
                safety_margin_seconds=0,
                maximum_wait_seconds=60,
                heartbeat_interval_seconds=1,
            ),
        ),
    )

    assert result.completed
    assert len(driver.codex_calls) == 3
    failures = result.state.current_work_unit.invocation_failures
    assert [failure.automatic_resume for failure in failures] == [True, True]
    assert [failure.auto_resume_count for failure in failures] == [1, 2]


def test_immediate_repeated_quota_without_provider_work_is_terminal() -> None:
    now = [datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes, changes],
        codex_outputs=[],
        reviewer_outputs=[],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-first",
                received_at=now[0],
                reset_after_seconds=1,
            ),
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-immediate-repeat",
                received_at=now[0] + timedelta(seconds=1),
                reset_after_seconds=0,
            ),
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(
        _slice_state(),
        replace(
            _context(),
            quota_wait_policy=QuotaWaitPolicy(
                safety_margin_seconds=0,
                maximum_wait_seconds=60,
                heartbeat_interval_seconds=1,
            ),
        ),
    )

    assert result.exit_code == 5
    assert result.rejection_code == "QUOTA-AUTOMATION-STOPPED"
    assert result.state.current_work_unit.status is WorkUnitStatus.COMPLETED
    assert [
        failure.automatic_resume
        for failure in result.state.current_work_unit.invocation_failures
    ] == [True, False]
    assert driver.failure_payloads[-1].failure_class == "terminal_rejection"
    watch_result = WatchTaskResult.from_workflow(
        WorkflowRunResult(
            replace(
                result.state,
                protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
            ),
            result.history,
        )
    )
    assert watch_result.disposition is (
        WatchTaskDisposition.REJECTED
    )
    assert driver.reviewer_calls == []


def test_provider_usage_allows_same_reset_time_to_count_as_progress() -> None:
    now = [datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes, changes, changes, changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-before-work",
                received_at=now[0],
                reset_after_seconds=1,
            ),
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-after-work",
                received_at=now[0] + timedelta(seconds=1),
                reset_after_seconds=0,
                provider_data={"usage": {"output_tokens": 1}},
            ),
            None,
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(
        _slice_state(),
        replace(
            _context(),
            quota_wait_policy=QuotaWaitPolicy(
                safety_margin_seconds=0,
                maximum_wait_seconds=60,
                heartbeat_interval_seconds=1,
            ),
        ),
    )

    assert result.completed
    assert [
        failure.automatic_resume
        for failure in result.state.current_work_unit.invocation_failures
    ] == [True, True]


@pytest.mark.parametrize(
    ("policy", "reset_after_seconds"),
    (
        (QuotaWaitPolicy(automatic=False), 10),
        (QuotaWaitPolicy(maximum_wait_seconds=5), 10),
        (QuotaWaitPolicy(), None),
    ),
)
def test_non_automatic_quota_is_a_terminal_operator_verdict(
    policy: QuotaWaitPolicy, reset_after_seconds: int | None
) -> None:
    received = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    driver = FakeDriver(
        snapshots=[_changes("1", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-manual",
                received_at=received,
                reset_after_seconds=reset_after_seconds,
            )
        ],
    )

    result = WorkflowEngine(driver, now_fn=lambda: received).run_current_work_unit(
        _slice_state(), replace(_context(), quota_wait_policy=policy)
    )

    assert result.exit_code == 5
    assert result.workflow_rejected
    assert result.rejection_code == "QUOTA-AUTOMATION-STOPPED"
    assert result.state.current_work_unit.status is WorkUnitStatus.COMPLETED
    assert result.state.current_work_unit.gate.status is GateStatus.CLEAR
    failure = result.state.current_work_unit.invocation_failures[-1]
    assert failure.automatic_resume is False
    assert driver.failure_payloads[-1].failure_class == "terminal_rejection"
    marker, _, _ = provider_text_evidence("usage cap reached")
    assert failure.provider_text == marker
    assert "usage cap reached" not in failure.provider_text
    assert driver.reviewer_calls == []


def test_missing_slice_fingerprint_disables_automatic_quota_resume() -> None:
    received = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)

    class UnavailableFingerprintDriver(FakeDriver):
        def collect_changes(self, start_commit: str) -> WorkflowChanges:
            raise RuntimeError(f"cannot collect changes from {start_commit}")

    driver = UnavailableFingerprintDriver(
        snapshots=[],
        codex_outputs=[],
        reviewer_outputs=[],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-without-fingerprint",
                received_at=received,
                reset_after_seconds=1,
            )
        ],
    )
    engine = WorkflowEngine(driver, now_fn=lambda: received)

    halted = engine.run_current_work_unit(
        _slice_state(),
        replace(
            _context(),
            quota_wait_policy=QuotaWaitPolicy(
                safety_margin_seconds=0,
                maximum_wait_seconds=60,
            ),
        ),
    )

    assert halted.exit_code == 2
    failure = halted.state.current_work_unit.invocation_failures[-1]
    assert failure.diff_fingerprint is None
    assert failure.automatic_resume is False
    assert len(driver.codex_calls) == 1

    resumed = engine.run_current_work_unit(
        halted.state.resume_after_invocation_halt(), _context(), halted.history
    )

    assert resumed.exit_code == 4
    assert resumed.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert "no persisted Slice fingerprint" in (
        resumed.state.current_work_unit.gate.detail or ""
    )
    assert len(driver.codex_calls) == 1


def test_changed_fingerprint_during_quota_wait_halts_before_retry() -> None:
    now = [datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)]
    first = _changes("1", "src/early.py", TEST_FILE)
    changed = _changes("2", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[first, first],
        codex_outputs=[],
        reviewer_outputs=[],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-mutated",
                received_at=now[0],
                reset_after_seconds=1,
            )
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)
        driver.snapshots[0] = changed

    engine = WorkflowEngine(driver, now_fn=lambda: now[0], sleep_fn=sleep)
    result = engine.run_current_work_unit(
        _slice_state(),
        replace(
            _context(),
            quota_wait_policy=QuotaWaitPolicy(
                safety_margin_seconds=0,
                maximum_wait_seconds=60,
                heartbeat_interval_seconds=1,
            ),
        ),
    )

    assert result.exit_code == 4
    assert (
        result.state.current_work_unit.gate.reason
        is GateReason.QUOTA_RESUME_DIFF
    )
    assert "QUOTA-RESUME-DIFF" in (result.state.current_work_unit.gate.detail or "")
    assert "src/early.py" in (result.state.current_work_unit.gate.detail or "")
    assert "--resume --approve-gate" in (
        result.state.current_work_unit.gate.detail or ""
    )
    assert len(driver.codex_calls) == 1

    gate = result.state.current_work_unit.gate
    assert gate.fingerprint == changed.fingerprint
    assert gate.resume_step is WorkflowStep.CODEX_IMPLEMENTATION
    acknowledged = result.state.record_user_gate_decision(
        approved=True,
        fingerprint=gate.fingerprint,
        paths=gate.paths,
        rationale="reviewed exact changed fingerprint and paths",
    )
    failure = acknowledged.current_work_unit.invocation_failures[-1]
    revalidated, halted = engine._revalidate_waiting_diff(
        acknowledged, failure, _context()
    )

    assert halted is False
    assert revalidated.current_work_unit.status is WorkUnitStatus.IN_PROGRESS

    changed_again = _changes("3", "src/early.py", TEST_FILE)
    driver.snapshots[0] = changed_again
    halted_again, halted = engine._revalidate_waiting_diff(
        acknowledged, failure, _context()
    )

    assert halted is True
    assert (
        halted_again.current_work_unit.gate.reason
        is GateReason.QUOTA_RESUME_DIFF
    )
    assert changed_again.fingerprint in (halted_again.current_work_unit.gate.detail or "")


def test_codex_quota_retry_keeps_same_step_and_claude_only_review_chain() -> None:
    now = [datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes, changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "codex-quota-resume",
                received_at=now[0],
                reset_after_seconds=10,
            ),
            None,
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(
        _slice_state(),
        replace(
            _context(),
            quota_wait_policy=QuotaWaitPolicy(
                safety_margin_seconds=5,
                maximum_wait_seconds=60,
                heartbeat_interval_seconds=5,
            ),
        ),
    )

    assert result.completed
    assert [call.step for call in driver.codex_calls] == [
        WorkflowStep.CODEX_IMPLEMENTATION,
        WorkflowStep.CODEX_IMPLEMENTATION,
    ]
    assert [call.reviewer for call in driver.reviewer_calls] == [AgentRole.CLAUDE]


def test_claude_network_retry_keeps_configured_two_resume_ceiling() -> None:
    now = [datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        reviewer_failures=[
            _invocation_failure(
                AgentRole.CLAUDE, AgentFailureKind.NETWORK, "network-1",
                received_at=now[0],
            ),
            _invocation_failure(
                AgentRole.CLAUDE, AgentFailureKind.NETWORK, "network-2",
                received_at=now[0],
            ),
            None,
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(_slice_state(), _context())

    assert result.completed
    assert [call.reviewer for call in driver.reviewer_calls] == [
        AgentRole.CLAUDE,
        AgentRole.CLAUDE,
        AgentRole.CLAUDE,
    ]
    assert [
        item.automatic_resume
        for item in result.state.current_work_unit.invocation_failures
    ] == [True, True]
    assert all(
        item.failure_kind is AgentFailureKind.NETWORK
        for item in result.state.current_work_unit.invocation_failures
    )
    assert len(
        {call.native_request.canonical_json for call in driver.reviewer_calls}
    ) == 1


def test_claude_structured_output_failure_keeps_bounded_retry_and_safe_diagnostic(
    caplog,
) -> None:
    now = [datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)]
    failures = [
        classify_agent_failure(
            "claude",
            AgentProcessError(
                "native Claude error",
                exit_code=1,
                provider_data={
                    "type": "result",
                    "subtype": "error_max_structured_output_retries",
                },
            ),
            invocation_id=f"structured-output-{attempt}",
            received_at=now[0],
        )
        for attempt in range(1, 3)
    ]
    driver = FakeDriver(
        snapshots=[_changes("1", "src/early.py", TEST_FILE)],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        reviewer_failures=[*failures, None],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    caplog.set_level("INFO", logger="workflow")
    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(_slice_state(), _context())

    assert result.completed
    assert len(driver.reviewer_calls) == 3
    assert [item.failure_kind for item in driver.failure_payloads] == [
        "output",
        "output",
    ]
    assert all(item.automatic_resume for item in driver.failure_payloads)
    assert all(
        item.diagnostic_code == "PROVIDER-STRUCTURED-OUTPUT"
        for item in driver.failure_payloads
    )
    assert all(
        item.provider_text_sha256 != item.technical_text_sha256
        for item in driver.failure_payloads
    )
    assert "error_max_structured_output_retries" in caplog.text
    assert "native Claude error" not in caplog.text


def test_codex_timeout_retries_automatically_without_operator_input() -> None:
    now = [datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes] * 8,
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.TIMEOUT,
                "timeout-then-success",
                received_at=now[0],
            ),
            None,
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(_slice_state(), _context())

    assert result.completed
    assert len(driver.codex_calls) == 2
    failure = driver.failure_payloads[0]
    assert failure.failure_kind == "timeout"
    assert failure.failure_class == "transient"
    assert failure.automatic_resume is True


def test_codex_timeout_retry_limit_reports_exhausted_attempts(caplog) -> None:
    now = [datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes] * 8,
        codex_outputs=[],
        reviewer_outputs=[],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.TIMEOUT,
                f"timeout-{attempt}",
                received_at=now[0],
            )
            for attempt in range(1, 4)
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    caplog.set_level("INFO", logger="workflow")
    context = replace(
        _context(),
        transient_retry_policy=TransientRetryPolicy(maximum_auto_resumes=2),
    )
    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(_slice_state(), context)

    assert not result.completed
    assert len(driver.codex_calls) == 3
    assert [item.automatic_resume for item in driver.failure_payloads] == [
        True,
        True,
        False,
    ]
    assert [item.failure_class for item in driver.failure_payloads] == [
        "transient",
        "transient",
        "resumable_halt",
    ]
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    assert "attempts_exhausted=3" in caplog.text


def test_slice_plan_rejection_retries_codex_with_closed_precise_guidance(
    caplog,
) -> None:
    now = [datetime(2026, 9, 19, 20, 24, tzinfo=timezone.utc)]
    diagnostic = (
        OrchestratorDiagnostic.IMPLEMENTER_IMPLEMENTATION_TREATMENT_FORBIDS_NO_CODE_FIELDS
    )
    changes = _changes("1", "docs/internal/plan.md")
    state = init_workflow_state(
        run_id="run-codex-form-retry",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=1,
        timestamp="2026-09-19T20:24:00+00:00",
        task_digest="d" * 64,
        task_scope_patterns=("docs/internal/plan.md",),
        target_branch="feature/workflow",
    )
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_not_ready(plan=True)],
        reviewer_outputs=[],
        codex_failures=[
            _native_codex_contract_failure(
                NativeCodexErrorCode.SLICE_PLAN_INVALID,
                "codex-slice-plan-invalid-1",
                received_at=now[0],
                detail=(
                    "plan treatment is invalid: implementation treatment "
                    "forbids No-Code disposition fields"
                ),
                diagnostic=diagnostic,
            ),
            None,
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    caplog.set_level("INFO", logger="workflow")
    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(
        state,
        replace(
            _context(),
            task_scope_patterns=("docs/internal/plan.md",),
        ),
    )

    assert len(driver.codex_calls) == 2
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    failure = driver.failure_payloads[0]
    assert failure.failure_class == "transient"
    assert failure.diagnostic_code == "NATIVE-IMPLEMENTER-FORM"
    assert failure.automatic_resume is True
    assert failure.native_implementer_rejection == "slice-plan-invalid"
    first = driver.codex_calls[0].native_request
    second = driver.codex_calls[1].native_request
    assert first is not None and second is not None
    assert "retry_feedback" not in first.document
    assert second.document["retry_feedback"] == {
        "prior_invocation_id": "codex-slice-plan-invalid-1",
        "rejection_code": "slice-plan-invalid",
        "correction_instruction": diagnostic.text,
    }
    assert "implementation treatment forbids No-Code disposition fields" in (
        second.document["retry_feedback"]["correction_instruction"]
    )
    assert first.bound_context.request_id != second.bound_context.request_id
    assert [call.round_number for call in driver.codex_calls] == [1, 1]
    assert [call.request_sequence for call in driver.codex_calls] == [1, 2]
    assert result.state.current_work_unit.round_number == 1
    assert result.state.current_work_unit.request_sequence == 2
    assert "retry=scheduled" in caplog.text


def test_codex_context_rejection_halts_without_retry() -> None:
    now = datetime(2026, 9, 19, 20, 24, tzinfo=timezone.utc)
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[],
        codex_failures=[
            _native_codex_contract_failure(
                NativeCodexErrorCode.CONTEXT_INVALID,
                "codex-context-invalid",
                received_at=now,
            )
        ],
    )

    result = WorkflowEngine(driver, now_fn=lambda: now).run_current_work_unit(
        _slice_state(), _context()
    )

    assert len(driver.codex_calls) == 1
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    failure = driver.failure_payloads[0]
    assert failure.failure_class == "resumable_halt"
    assert failure.diagnostic_code == "NATIVE-IMPLEMENTER-CONTRACT"
    assert failure.automatic_resume is False
    assert failure.native_implementer_rejection is None


def test_codex_form_rejection_with_scope_violation_halts_without_retry() -> None:
    now = datetime(2026, 9, 19, 20, 24, tzinfo=timezone.utc)
    changes = _changes("1", "outside/scope.py")
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[],
        codex_failures=[
            _native_codex_contract_failure(
                NativeCodexErrorCode.SCHEMA_INVALID,
                "codex-form-with-scope-violation",
                received_at=now,
            )
        ],
    )

    result = WorkflowEngine(driver, now_fn=lambda: now).run_current_work_unit(
        _slice_state(), _context()
    )

    assert len(driver.codex_calls) == 1
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    failure = driver.failure_payloads[0]
    assert failure.diagnostic_code == "NATIVE-IMPLEMENTER-FORM"
    assert failure.failure_class == "resumable_halt"
    assert failure.automatic_resume is False
    assert failure.native_implementer_rejection is None


def test_response_dependent_codex_rejection_uses_shared_bounded_retry_limit() -> None:
    now = [datetime(2026, 9, 19, 20, 24, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    failures = [
        _native_codex_contract_failure(
            NativeCodexErrorCode.RESULT_CONTENT_INVALID,
            f"codex-content-invalid-{attempt}",
            received_at=now[0],
        )
        for attempt in range(1, 4)
    ]
    driver = FakeDriver(
        snapshots=[changes, changes, changes],
        codex_outputs=[],
        reviewer_outputs=[],
        codex_failures=failures,
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    context = replace(
        _context(),
        transient_retry_policy=TransientRetryPolicy(maximum_auto_resumes=2),
    )
    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(_slice_state(), context)

    assert not result.completed
    assert len(driver.codex_calls) == 3
    assert [item.automatic_resume for item in driver.failure_payloads] == [
        True,
        True,
        False,
    ]
    assert [item.failure_class for item in driver.failure_payloads] == [
        "transient",
        "transient",
        "resumable_halt",
    ]
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME


def test_schema_invalid_review_retries_with_bound_corrective_feedback(caplog) -> None:
    now = [datetime(2026, 9, 7, 20, 24, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        reviewer_failures=[
            _native_review_contract_failure(
                NativeReviewErrorCode.SCHEMA_INVALID,
                "review-schema-invalid-1",
                received_at=now[0],
            ),
            _native_review_contract_failure(
                NativeReviewErrorCode.SCHEMA_INVALID,
                "review-schema-invalid-2",
                received_at=now[0],
            ),
            None,
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    caplog.set_level("INFO", logger="workflow")
    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(_slice_state(), _context())

    assert result.completed
    assert len(driver.reviewer_calls) == 3
    assert [event[0] for event in driver.structured_events].count(
        "invocation-failure"
    ) == 2
    failure = driver.failure_payloads[0]
    assert failure.failure_class == "transient"
    assert failure.diagnostic_code == "NATIVE-REVIEW-FORM"
    assert failure.failure_kind == "output"
    assert failure.automatic_resume is True
    assert failure.retry_delay_seconds > 0
    assert failure.native_review_rejection == "schema-invalid"
    first = driver.reviewer_calls[0].native_request
    second = driver.reviewer_calls[1].native_request
    third = driver.reviewer_calls[2].native_request
    assert first is not None and second is not None and third is not None
    assert "retry_feedback" not in first.document
    assert second.document["retry_feedback"] == {
        "prior_invocation_id": "review-schema-invalid-1",
        "rejection_code": "schema-invalid",
        "correction_instruction": (
            "Return one JSON result that conforms exactly to the bound writer schema."
        ),
    }
    assert first.bound_context.request_id != second.bound_context.request_id
    assert second.bound_context.request_id != third.bound_context.request_id
    assert first.bound_context.request_id != third.bound_context.request_id
    assert [call.round_number for call in driver.reviewer_calls] == [1, 1, 1]
    assert [call.request_sequence for call in driver.reviewer_calls] == [1, 2, 3]
    assert result.state.current_work_unit.round_number == 1
    assert result.state.current_work_unit.request_sequence == 3
    assert "native_review_rejection=schema-invalid: provider-authored review rejected" in caplog.text


def test_rejected_first_review_then_two_findings_remains_discovery_round() -> None:
    """Regression for B152's Slice-03 canary failure."""

    now = [datetime(2026, 9, 20, 2, 30, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    denial = "\n".join(
        (
            f"REVIEWER: {AgentRole.CLAUDE.value}",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            "NEW_FINDING: C-01 | BLOCKER | first defect | cover first defect",
            "NEW_FINDING: C-02 | BLOCKER | second defect | cover second defect",
            "SLICE_APPROVAL: 01 | NO",
            "STATUS: DONE",
        )
    )
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready(), _codex_not_ready()],
        reviewer_outputs=[denial],
        reviewer_failures=[
            _native_review_contract_failure(
                NativeReviewErrorCode.FINDING_ID_INVALID,
                "review-invalid-finding-ids",
                received_at=now[0],
            ),
            None,
        ],
        convergence_evaluations=[
            SliceConvergenceEvaluation(
                phase=SliceReviewPhase.DISCOVERY,
                cohort_finding_ids=("C-01", "C-02"),
                newly_opened_finding_ids=("C-01", "C-02"),
                closed_local_finding_ids=(),
                forwarded_local_finding_ids=(),
                attested_remediation_finding_ids=(),
                progress_made=True,
                reason="the discovery round opened reviewer-owned findings",
            )
        ],
    )

    result = WorkflowEngine(
        driver,
        now_fn=lambda: now[0],
        sleep_fn=lambda _seconds: None,
    ).run_current_work_unit(_slice_state(), _context())

    assert not result.completed
    assert [call.round_number for call in driver.reviewer_calls] == [1, 1]
    assert [call.request_sequence for call in driver.reviewer_calls] == [1, 2]
    assert driver.convergence_calls == [(result.state.current_work_unit_id, 1)]
    assert tuple(item.finding_id for item in result.history.findings) == (
        "C-01",
        "C-02",
    )
    assert result.state.current_work_unit.round_number == 2
    assert result.state.current_work_unit.request_sequence == 3
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION


def test_duplicate_review_rejection_logs_the_safe_exact_mapping(caplog) -> None:
    now = datetime(2026, 9, 13, 16, 21, tzinfo=timezone.utc)
    detail = (
        "new finding C-17 has no unique offered open finding occurrence target; "
        "known matches: C-03 (signature " + ("a" * 64) + ")"
    )
    driver = FakeDriver(
        snapshots=[_changes("1", "src/early.py", TEST_FILE)],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        reviewer_failures=[
            _native_review_contract_failure(
                NativeReviewErrorCode.FINDING_SIGNATURE_DUPLICATE,
                "review-duplicate-mapping",
                received_at=now,
                detail=detail,
                operator_detail=detail,
            ),
            None,
        ],
    )

    caplog.set_level("INFO", logger="workflow")
    result = WorkflowEngine(driver, now_fn=lambda: now).run_current_work_unit(
        _slice_state(), _context()
    )

    assert result.completed
    assert len(driver.reviewer_calls) == 2
    assert (
        f"native_review_rejection=finding-signature-duplicate: {detail}"
        in caplog.text
    )


def test_other_review_output_failure_halts_without_automatic_retry() -> None:
    now = datetime(2026, 9, 7, 20, 24, tzinfo=timezone.utc)
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        reviewer_failures=[
            _invocation_failure(
                AgentRole.CLAUDE,
                AgentFailureKind.OUTPUT,
                "review-output-not-form",
                received_at=now,
            )
        ],
    )

    result = WorkflowEngine(driver, now_fn=lambda: now).run_current_work_unit(
        _slice_state(), _context()
    )

    assert not result.completed
    assert len(driver.reviewer_calls) == 1
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    failure = driver.failure_payloads[0]
    assert failure.failure_kind == "output"
    assert failure.diagnostic_code == "AGENT-INVOCATION"
    assert failure.automatic_resume is False


def test_foreign_review_request_id_retries_with_request_binding_feedback() -> None:
    now = datetime(2026, 9, 7, 20, 24, tzinfo=timezone.utc)
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        reviewer_failures=[
            _native_review_contract_failure(
                NativeReviewErrorCode.REQUEST_MISMATCH,
                "review-request-mismatch",
                received_at=now,
            ),
            None,
        ],
    )

    result = WorkflowEngine(driver, now_fn=lambda: now).run_current_work_unit(
        _slice_state(), _context()
    )

    assert result.completed
    assert len(driver.reviewer_calls) == 2
    failure = driver.failure_payloads[0]
    assert failure.failure_class == "transient"
    assert failure.diagnostic_code == "NATIVE-REVIEW-FORM"
    assert failure.automatic_resume is True
    retry = driver.reviewer_calls[1].native_request
    assert retry is not None
    assert retry.document["retry_feedback"]["rejection_code"] == "request-mismatch"


def test_response_dependent_review_rejection_uses_bounded_retry_limit() -> None:
    now = [datetime(2026, 9, 7, 20, 24, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)
    failures = [
        _native_review_contract_failure(
            NativeReviewErrorCode.APPROVAL_INVALID,
            f"review-content-invalid-{attempt}",
            received_at=now[0],
        )
        for attempt in range(1, 4)
    ]
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        reviewer_failures=failures,
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    context = replace(
        _context(),
        transient_retry_policy=TransientRetryPolicy(maximum_auto_resumes=2),
    )
    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(_slice_state(), context)

    assert not result.completed
    assert len(driver.reviewer_calls) == 3
    assert [item.automatic_resume for item in driver.failure_payloads] == [
        True,
        True,
        False,
    ]
    assert [item.failure_kind for item in driver.failure_payloads] == [
        "output",
        "output",
        "output",
    ]
    assert [item.failure_class for item in driver.failure_payloads] == [
        "transient",
        "transient",
        "resumable_halt",
    ]
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    assert result.state.current_work_unit.gate.resume_step is WorkflowStep.CLAUDE_SLICE_REVIEW


def test_collect_changes_exception_during_resume_becomes_policy_halt() -> None:
    now = [datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)]
    changes = _changes("1", "src/early.py", TEST_FILE)

    class RevalidationFailureDriver(FakeDriver):
        collection_count = 0

        def collect_changes(self, start_commit: str) -> WorkflowChanges:
            self.collection_count += 1
            if self.collection_count > 1:
                raise RuntimeError("simulated git index lock")
            return super().collect_changes(start_commit)

    driver = RevalidationFailureDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-git-lock",
                received_at=now[0],
                reset_after_seconds=1,
            )
        ],
    )

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    result = WorkflowEngine(
        driver, now_fn=lambda: now[0], sleep_fn=sleep
    ).run_current_work_unit(
        _slice_state(),
        replace(
            _context(),
            quota_wait_policy=QuotaWaitPolicy(
                safety_margin_seconds=0,
                maximum_wait_seconds=60,
                heartbeat_interval_seconds=1,
            ),
        ),
    )

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert "simulated git index lock" in (
        result.state.current_work_unit.gate.detail or ""
    )
    assert driver.checkpoints[-1] == result.state
    assert len(driver.codex_calls) == 1


def test_interrupt_during_quota_wait_leaves_checkpointed_wait_state() -> None:
    received = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[],
        codex_failures=[
            _invocation_failure(
                AgentRole.CODEX,
                AgentFailureKind.QUOTA,
                "quota-interrupted",
                received_at=received,
                reset_after_seconds=30,
            )
        ],
    )

    with pytest.raises(KeyboardInterrupt):
        WorkflowEngine(
            driver,
            now_fn=lambda: received,
            sleep_fn=lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
        ).run_current_work_unit(
            _slice_state(),
            replace(
                _context(),
                quota_wait_policy=QuotaWaitPolicy(
                    safety_margin_seconds=0,
                    maximum_wait_seconds=60,
                    heartbeat_interval_seconds=5,
                ),
            ),
        )

    assert driver.checkpoints[-1].current_work_unit.status is WorkUnitStatus.WAITING_FOR_QUOTA
    assert driver.checkpoints[-1].current_step is WorkflowStep.CODEX_IMPLEMENTATION


def test_invalid_unified_diff_prevents_any_reviewer_invocation() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE, full_diff="markerless delta")
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
    )
    plan = """# Approved plan

### Slice 1 - Packet Slice

#### Fokussierte synthetische Akzeptanztests

- Invalid coverage fails before provider start.
"""

    with pytest.raises(WorkflowExecutionError, match="marker-delimited"):
        WorkflowEngine(driver).run_current_work_unit(
            _slice_state(), replace(_context(), approved_plan_text=plan)
        )
    assert driver.reviewer_calls == []


@pytest.mark.parametrize("invalid", ["foreign", "incomplete"])
def test_invalid_attestation_stops_before_any_reviewer(invalid: str) -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        invalid_attestation=invalid,
    )

    with pytest.raises(WorkflowExecutionError, match="attestation"):
        WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert driver.reviewer_calls == []
    assert driver.commit_calls == []


def test_complete_failing_attestation_reaches_reviewer_without_red_state() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        invalid_attestation="failing",
        fail_reviewer_once=True,
    )

    with pytest.raises(RuntimeError, match="interruption"):
        WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert [call.reviewer for call in driver.reviewer_calls] == [AgentRole.CLAUDE]
    assert driver.reviewer_calls[0].native_request is not None
    assert "validation failed" in driver.reviewer_calls[0].native_request.canonical_json
    assert driver.commit_calls == []


def test_plan_bound_complete_failing_attestation_reaches_reviewer_as_red_packet() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        invalid_attestation="failing",
        fail_reviewer_once=True,
    )
    plan = """# Approved plan

### Slice 1 - Red packet

**Ziel**

Keep complete failed validation evidence reviewable.

#### \u0041kzeptanzkriterien

- Claude must deny a complete red attestation.
"""

    with pytest.raises(RuntimeError, match="interruption"):
        WorkflowEngine(driver).run_current_work_unit(
            _slice_state(), replace(_context(), approved_plan_text=plan)
        )

    assert [call.reviewer for call in driver.reviewer_calls] == [AgentRole.CLAUDE]
    packet = driver.reviewer_calls[0].review_packet
    assert packet is not None
    assert json.loads(packet.canonical_bytes)["attestation"]["status"] == "FAIL"
    assert driver.reviewer_calls[0].native_request is not None
    assert '"const":"denied"' in driver.reviewer_calls[0].native_request.provider_response_schema_json
    assert driver.commit_calls == []


def test_fifteen_authorized_productive_paths_reach_implementation_and_review() -> None:
    productive_paths = tuple(f"src/file_{index}.py" for index in range(15))
    scope = tuple(sorted((*productive_paths, TEST_FILE)))
    changes = _changes("1", *scope)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
    )

    result = WorkflowEngine(driver).run_current_work_unit(
        _slice_state(scope_paths=scope), _context()
    )

    assert result.completed
    assert len(driver.codex_calls) == 1
    assert [call.reviewer for call in driver.reviewer_calls] == [AgentRole.CLAUDE]
    assert result.state.current_work_unit.gate.status is GateStatus.CLEAR


def test_large_authorized_scope_still_rejects_one_unexpected_path() -> None:
    productive_paths = tuple(f"src/file_{index}.py" for index in range(15))
    scope = tuple(sorted((*productive_paths, TEST_FILE)))
    unexpected = "src/not-authorized.py"
    changes = _changes("1", *scope, unexpected)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
    )

    blocked = WorkflowEngine(driver).run_current_work_unit(
        _slice_state(scope_paths=scope), _context()
    )

    assert blocked.exit_code == 4
    assert blocked.state.current_work_unit.gate.reason is GateReason.UNEXPECTED_FILE
    assert blocked.state.current_work_unit.gate.detail.startswith("UNEXPECTED-PATH |")
    assert blocked.state.current_work_unit.gate.paths == (unexpected,)
    assert len(driver.codex_calls) == 1
    assert driver.reviewer_calls == []


def test_branch_mismatch_halts_before_first_agent() -> None:
    driver = FakeDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])
    context = replace(_context(), current_branch="feature/other")

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), context)

    assert result.exit_code == 4
    assert "BRANCH-MISMATCH" in result.state.current_work_unit.gate.detail
    assert driver.codex_calls == []


def test_codex_stop_request_halts_same_step_without_retry_or_repair() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="domain choice remains unresolved",
        acceptance_test="user selects one policy",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[_codex_stop("DOMAIN-001")],
        reviewer_outputs=[],
    )
    context = replace(
        _context(),
        stop_rules=(StopRule("DOMAIN-001", "engine semantics changed"),),
    )

    state = _slice_state()
    history = WorkflowHistory(state.current_work_unit_id, findings=(finding,))
    result = WorkflowEngine(driver).run_current_work_unit(state, context, history)

    assert result.exit_code == 4
    assert result.state.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert result.state.current_work_unit.gate.detail == (
        "DOMAIN-001 | domain semantics require user direction"
    )
    assert len(driver.codex_calls) == 1
    assert not hasattr(driver, "repair_review_contract")
    assert result.history.findings == (finding,)
    assert driver.checkpoint_histories[-1].findings == (finding,)


def test_contract_unclear_stop_request_becomes_a_policy_gate() -> None:
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[_codex_stop("CONTRACT-UNCLEAR")],
        reviewer_outputs=[],
    )

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert result.exit_code == 4
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert result.state.current_work_unit.gate.detail == (
        "CONTRACT-UNCLEAR | domain semantics require user direction"
    )


def test_codex_agent_sandbox_validation_stop_is_handed_back_automatically() -> None:
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[
            "TEST_FILES_TOUCHED: tests/test_workflow.py\n"
            "STOP_REQUESTED: VALIDATION-UNAVAILABLE | npm run test:browser "
            "scheitert vor Browserstart beim Binden des lokalen Testservers mit "
            "listen EPERM auf 127.0.0.1\n"
            "STATUS: DONE",
            _codex_ready(),
        ],
        reviewer_outputs=[],
    )
    state = _slice_state()

    advanced, _ = WorkflowEngine(driver)._run_codex(
        state, _context(), WorkflowHistory(state.current_work_unit_id)
    )

    assert advanced.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert len(driver.codex_calls) == 2
    assert driver.codex_calls[1].native_request is not None
    assert "AUTOMATIC ORCHESTRATOR VALIDATION HANDOFF" in driver.codex_calls[1].native_request.canonical_json
    assert "Do not rerun the configured full validation matrix" in driver.codex_calls[1].native_request.canonical_json
    assert advanced.current_work_unit.has_completed_side_effect(
        "agent-sandbox-validation-handoff"
    )


def test_repeated_agent_sandbox_validation_stop_still_fails_closed() -> None:
    stop = (
        "STOP_REQUESTED: VALIDATION-UNAVAILABLE | local test server listen EACCES\n"
        "STATUS: DONE"
    )
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[stop, stop],
        reviewer_outputs=[],
    )
    state = _slice_state()

    halted, _ = WorkflowEngine(driver)._run_codex(
        state, _context(), WorkflowHistory(state.current_work_unit_id)
    )

    assert len(driver.codex_calls) == 2
    assert halted.current_work_unit.gate.reason is GateReason.STOP_REQUEST


def test_codex_validation_stop_auto_extends_large_exact_scope_from_completed_slice() -> None:
    prior_productive = tuple(f"src/prior_{index}.py" for index in range(14))
    prior_scope = tuple(sorted((*prior_productive, "tests/prior.py")))
    current_scope = ("src/current.py", "tests/current.py")
    state = init_workflow_state(
        run_id="run-auto-remediation",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=2,
        timestamp="2026-08-12T10:00:00+00:00",
    ).bind_slice_plan(
        (
            PlannedSlice(1, "source adapter", prior_scope),
            PlannedSlice(2, "consumer", current_scope),
        ),
        first_start_commit=START_COMMIT,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=START_COMMIT,
        scope_paths=prior_scope,
        start_fingerprint="0" * 64,
    ).complete_current_slice(
        commit_ref="b" * 40,
    ).start_work_unit(
        slice_id=2,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit="b" * 40,
    ).bind_current_slice_git_boundary(
        start_commit="b" * 40,
        scope_paths=current_scope,
        start_fingerprint="1" * 64,
    )
    remediation_paths = ", ".join(prior_scope)
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[
            "STOP_REQUESTED: VALIDATION-UNAVAILABLE | prior adapter needs normalization\n"
            f"REMEDIATION_PATHS: {remediation_paths}\n"
            "STATUS: DONE",
            "TEST_FILES_TOUCHED: tests/current.py,tests/prior.py\n"
            "IMPLEMENTATION_READY: 02 | YES\n"
            "STATUS: DONE",
        ],
        reviewer_outputs=[],
    )
    context = replace(
        _context(),
        expected_test_files=("tests/current.py", "tests/prior.py"),
        current_scope_paths=state.current_slice.scope_paths,
    )

    advanced, _ = WorkflowEngine(driver)._run_codex(
        state, context, WorkflowHistory(state.current_work_unit_id)
    )

    assert advanced.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert advanced.current_slice.scope_paths == tuple(
        sorted((*current_scope, *prior_scope))
    )
    assert len(
        tuple(path for path in advanced.current_slice.scope_paths if path.startswith("src/"))
    ) == 15
    assert len(driver.codex_calls) == 2
    assert driver.codex_calls[1].native_request is not None
    assert "AUTOMATIC PRIOR-SLICE REMEDIATION" in driver.codex_calls[1].native_request.canonical_json
    assert "src/prior_0.py" in driver.codex_calls[1].native_request.canonical_json
    assert "src/prior_13.py" in driver.codex_calls[1].native_request.canonical_json


def test_codex_reprompts_once_when_remediation_path_is_already_authorized() -> None:
    state = init_workflow_state(
        run_id="run-existing-remediation",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=2,
        timestamp="2026-08-12T10:00:00+00:00",
    ).bind_slice_plan(
        (
            PlannedSlice(1, "source adapter", ("src/prior.py", "tests/prior.py")),
            PlannedSlice(2, "consumer", ("src/current.py", "tests/current.py")),
        ),
        first_start_commit=START_COMMIT,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=START_COMMIT,
        scope_paths=("src/prior.py", "tests/prior.py"),
        start_fingerprint="0" * 64,
    ).complete_current_slice(
        commit_ref="b" * 40,
    ).start_work_unit(
        slice_id=2,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit="b" * 40,
    ).bind_current_slice_git_boundary(
        start_commit="b" * 40,
        scope_paths=(
            "src/current.py",
            "src/prior.py",
            "tests/current.py",
            "tests/prior.py",
        ),
        start_fingerprint="1" * 64,
    )
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[
            "STOP_REQUESTED: VALIDATION-UNAVAILABLE | prior test is allegedly out of scope\n"
            "REMEDIATION_PATHS: tests/prior.py\n"
            "STATUS: DONE",
            "TEST_FILES_TOUCHED: tests/current.py,tests/prior.py\n"
            "IMPLEMENTATION_READY: 02 | YES\n"
            "STATUS: DONE",
        ],
        reviewer_outputs=[],
    )
    context = replace(
        _context(),
        expected_test_files=("tests/current.py", "tests/prior.py"),
        current_scope_paths=state.current_slice.scope_paths,
    )

    advanced, _ = WorkflowEngine(driver)._run_codex(
        state, context, WorkflowHistory(state.current_work_unit_id)
    )

    assert advanced.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert len(driver.codex_calls) == 2
    assert driver.codex_calls[1].native_request is not None
    assert "ALREADY AUTHORIZED" in driver.codex_calls[1].native_request.canonical_json
    assert "Do not emit STOP_REQUESTED" in driver.codex_calls[1].native_request.canonical_json
    assert any(
        key.startswith("authorized-remediation-reprompt:")
        for key in advanced.current_work_unit.completed_side_effects
    )


def test_codex_repeated_already_authorized_remediation_still_halts() -> None:
    state = init_workflow_state(
        run_id="run-repeated-remediation",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=2,
        timestamp="2026-08-12T10:00:00+00:00",
    ).bind_slice_plan(
        (
            PlannedSlice(1, "source adapter", ("tests/prior.py",)),
            PlannedSlice(2, "consumer", ("src/current.py",)),
        ),
        first_start_commit=START_COMMIT,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=START_COMMIT,
        scope_paths=("tests/prior.py",),
        start_fingerprint="0" * 64,
    ).complete_current_slice(
        commit_ref="b" * 40,
    ).start_work_unit(
        slice_id=2,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit="b" * 40,
    ).bind_current_slice_git_boundary(
        start_commit="b" * 40,
        scope_paths=("src/current.py", "tests/prior.py"),
        start_fingerprint="1" * 64,
    )
    repeated_stop = (
        "STOP_REQUESTED: VALIDATION-UNAVAILABLE | still claims prior test is out of scope\n"
        "REMEDIATION_PATHS: tests/prior.py\n"
        "STATUS: DONE"
    )
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[repeated_stop, repeated_stop],
        reviewer_outputs=[],
    )
    context = replace(_context(), current_scope_paths=state.current_slice.scope_paths)

    halted, _ = WorkflowEngine(driver)._run_codex(
        state, context, WorkflowHistory(state.current_work_unit_id)
    )

    assert len(driver.codex_calls) == 2
    assert halted.current_work_unit.gate.reason is GateReason.STOP_REQUEST


def test_codex_remediation_outside_completed_plan_still_halts() -> None:
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[
            "STOP_REQUESTED: VALIDATION-UNAVAILABLE | future path would be required\n"
            "REMEDIATION_PATHS: src/future.py\n"
            "STATUS: DONE"
        ],
        reviewer_outputs=[],
    )

    result = WorkflowEngine(driver).run_current_work_unit(
        _slice_state(), _context()
    )

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert len(driver.codex_calls) == 1


def test_codex_not_ready_persists_gate_and_resumes_same_step() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="required validation remains red",
        acceptance_test="npm test",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[_codex_not_ready("C-01")],
        reviewer_outputs=[],
    )
    state = _slice_state()
    history = WorkflowHistory(state.current_work_unit_id, findings=(finding,))

    result = WorkflowEngine(driver).run_current_work_unit(state, _context(), history)

    assert result.exit_code == 4
    assert result.state.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert result.state.current_work_unit.gate.detail == (
        "CODEX-NOT-READY | Codex reported the current step as not ready; "
        "ready=false does not document a blocker; resolve why the result was not ready "
        "before resuming the same step"
    )
    assert driver.reviewer_calls == []
    assert not hasattr(driver, "repair_review_contract")
    assert result.history.findings[0].responses[0].decision is FindingResponseDecision.ACCEPTED
    assert driver.checkpoint_histories[-1].findings == result.history.findings

    resumed = result.state.resume_after_user_decision()
    assert resumed.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert resumed.current_work_unit.status is WorkUnitStatus.IN_PROGRESS


def test_plan_not_ready_persists_gate_and_resumes_plan_step() -> None:
    state = init_workflow_state(
        run_id="run-plan-not-ready",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=1,
        timestamp="2026-08-12T10:00:00+00:00",
        task_digest="a" * 64,
        task_scope_patterns=("docs/internal/plan.md",),
        target_branch="feature/workflow",
    )
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[_codex_not_ready(plan=True)],
        reviewer_outputs=[],
    )

    result = WorkflowEngine(driver).run_current_work_unit(state, _context())

    assert result.exit_code == 4
    assert result.state.current_step is WorkflowStep.CODEX_PLAN
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert result.state.current_work_unit.gate.detail == (
        "CODEX-NOT-READY | Codex reported the current step as not ready; "
        "ready=false does not document a blocker; resolve why the result was not ready "
        "before resuming the same step"
    )
    resumed = result.state.resume_after_user_decision()
    assert resumed.current_step is WorkflowStep.CODEX_PLAN
    assert resumed.current_work_unit.status is WorkUnitStatus.IN_PROGRESS


def test_reviewer_stop_request_halts_without_contract_repair() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_stop(AgentRole.CLAUDE, "DOMAIN-001")],
    )
    context = replace(
        _context(),
        stop_rules=(StopRule("DOMAIN-001", "engine semantics changed"),),
    )

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), context)

    assert result.exit_code == 4
    assert result.state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert len(driver.reviewer_calls) == 1
    assert not hasattr(driver, "repair_review_contract")
    assert len(result.history.events) == 2
    stopped_review = result.history.events[-1]
    assert stopped_review.result.stopped is True
    assert stopped_review.result.validation == result.history.attestations[-1]
    assert driver.checkpoint_histories[-1] == result.history


def test_unknown_stop_rule_is_rejected_instead_of_becoming_a_gate() -> None:
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[_codex_stop("UNKNOWN-001")],
        reviewer_outputs=[],
    )

    with pytest.raises(WorkflowExecutionError, match="unknown rule"):
        WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert len(driver.codex_calls) == 1
    assert not hasattr(driver, "repair_review_contract")


def test_operator_prerequisite_stop_uses_existing_policy_gate_with_full_diagnostic() -> None:
    rationale = (
        "Missing prerequisite: app/public/assets/fonts/Brand-Regular.woff2\n"
        "Why it cannot be self-provided: the licensed binary requires an operator decision\n"
        "Operator action: provide app/public/assets/fonts/Brand-Regular.woff2"
    )

    halted = WorkflowEngine._halt_for_stop_request(
        _slice_state(),
        _context(),
        StopRequest(OPERATOR_PREREQUISITE_MISSING_RULE_ID, rationale),
    )

    assert halted.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert halted.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert halted.current_work_unit.gate.detail == (
        f"{OPERATOR_PREREQUISITE_MISSING_RULE_ID} | {rationale}"
    )
    assert "Brand-Regular.woff2" in halted.current_work_unit.gate.detail
    assert "licensed binary" in halted.current_work_unit.gate.detail
    assert "provide app/public/assets/fonts" in halted.current_work_unit.gate.detail


def test_operator_prerequisite_stop_cannot_bypass_content_validation() -> None:
    with pytest.raises(WorkflowExecutionError, match="requires operator action"):
        WorkflowEngine._halt_for_stop_request(
            _slice_state(),
            _context(),
            StopRequest(
                OPERATOR_PREREQUISITE_MISSING_RULE_ID,
                "Missing prerequisite: jsdom\n"
                "Why it cannot be self-provided: package installation is forbidden",
            ),
        )


def test_discovery_output_limit_is_a_dedicated_branch_discovery_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    state = init_workflow_state(
        run_id="run-discovery-output-limit",
        task_file="/repo/discovery.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=1,
        execution_mode="BRANCH_DISCOVERY",
    )
    stop_request = StopRequest(
        DISCOVERY_OUTPUT_LIMIT_RULE_ID,
        "the request-bound discovery capacity was reached",
    )

    halted = WorkflowEngine._halt_for_stop_request(state, _context(), stop_request)

    assert halted.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert halted.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert halted.current_work_unit.gate.detail == (
        "DISCOVERY_OUTPUT_LIMIT | the request-bound discovery capacity was reached"
    )

    assert native_finding_decisions.JOINT_67_68_NATIVE_CONTRACT_CUTOVER is True


def test_unavailable_validation_uses_policy_gate_before_reviewer() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        validation_unavailable="pytest executable is missing",
    )

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert result.exit_code == 4
    assert result.state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert "VALIDATION-UNAVAILABLE" in result.state.current_work_unit.gate.detail
    assert driver.reviewer_calls == []


def test_unapproved_test_change_halts_before_validation_and_review() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    evidence = GateTestChangeEvidence((TEST_FILE,), "9" * 64)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        test_evidence_by_fingerprint={changes.fingerprint: evidence},
    )
    context = replace(_context(), test_changes_approved=False)

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), context)

    assert result.exit_code == 4
    assert result.state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert result.state.current_work_unit.gate.reason is GateReason.TEST_CHANGE
    assert result.state.current_work_unit.gate.fingerprint == evidence.fingerprint
    assert result.state.current_work_unit.gate.paths == (TEST_FILE,)
    assert len(driver.codex_calls) == 1
    assert driver.validation_calls == []
    assert driver.reviewer_calls == []


def test_changed_test_fingerprint_expires_previous_gate_approval() -> None:
    first = _changes("1", "src/early.py", TEST_FILE)
    changed = _changes("2", "src/early.py", TEST_FILE)
    first_evidence = GateTestChangeEvidence((TEST_FILE,), "8" * 64)
    changed_evidence = GateTestChangeEvidence((TEST_FILE,), "9" * 64)

    @dataclass
    class ChangedAfterApprovalDriver(FakeDriver):
        use_changed: bool = False

        def collect_changes(self, start_commit: str) -> WorkflowChanges:
            assert start_commit == START_COMMIT
            return changed if self.use_changed else first

    driver = ChangedAfterApprovalDriver(
        snapshots=[first],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        test_evidence_by_fingerprint={
            first.fingerprint: first_evidence,
            changed.fingerprint: changed_evidence,
        },
    )
    engine = WorkflowEngine(driver)
    context = replace(_context(), test_changes_approved=False)
    halted = engine.run_current_work_unit(_slice_state(), context)
    approved = engine.decide_current_gate(
        halted.state,
        halted.history,
        approved=True,
        rationale="first test diff reviewed",
    )
    driver.use_changed = True

    expired = engine.run_current_work_unit(
        approved.state, context, approved.history
    )

    assert expired.exit_code == 4
    assert expired.state.current_work_unit.gate.fingerprint == changed_evidence.fingerprint
    assert driver.reviewer_calls == []


def test_native_record_ahead_review_is_mirrored_before_next_policy_or_provider() -> None:
    fingerprint = "f" * 64
    state = replace(
        _slice_state().with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
        ),
    )
    attestation = ValidationAttestation(
        "validation-record-ahead",
        fingerprint,
        ("python3 -m pytest tests/ -v",),
        (
            ValidationRecord(
                ValidationStatus.PASS,
                "python3 -m pytest tests/ -v",
                0,
                "passed",
            ),
        ),
        "a" * 64,
        "passed",
    )
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="record-ahead finding",
        acceptance_test="resume without another reviewer invocation",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    result = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=False,
        stopped=False,
        stop_request=None,
        validation=attestation,
        test_files=(TEST_FILE,),
        pre_mortem=None,
        evidence=ReviewEvidence(
            "record-ahead recovery",
            "a stale state mirror",
            "Claude is invoked twice",
        ),
        findings=(finding,),
        anchors=(),
    )
    replay = PersistedNativeReviewerReplay(
        output=NativeAgentReviewOutput(
            result=result,
            canonical_json='{"decision":"denied"}',
            request_id="native-review-request-" + "b" * 64,
        ),
        fingerprint=fingerprint,
        round_number=1,
    )

    @dataclass
    class RecoveringDriver(FakeDriver):
        def recover_pending_native_reviewer_before_policy(
            self,
            recovered_state: WorkflowState,
            context: WorkflowContext,
            history: WorkflowHistory,
        ) -> PersistedNativeReviewerReplay:
            assert recovered_state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
            assert not history.events
            return replay

    driver = RecoveringDriver(
        snapshots=[_changes("f", "src/early.py", TEST_FILE)],
        codex_outputs=[
            "STOP_REQUESTED: UNEXPECTED-PATH | await a bound scope decision\n"
            "STATUS: DONE"
        ],
        reviewer_outputs=[],
        convergence_evaluations=[
            SliceConvergenceEvaluation(
                phase=SliceReviewPhase.DISCOVERY,
                cohort_finding_ids=("C-01",),
                newly_opened_finding_ids=("C-01",),
                closed_local_finding_ids=(),
                forwarded_local_finding_ids=(),
                attested_remediation_finding_ids=(),
                progress_made=True,
                reason="the discovery round opened reviewer-owned findings",
            )
        ],
    )
    outcome = WorkflowEngine(driver).run_current_work_unit(
        state,
        _context(),
        WorkflowHistory(state.current_work_unit_id, attestations=(attestation,)),
    )

    assert not driver.reviewer_calls
    assert len(driver.codex_calls) == 1
    assert outcome.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert outcome.history.findings == (finding,)
    reviews = tuple(
        event for event in outcome.history.events if isinstance(event, ReviewAuditEvent)
    )
    assert len(reviews) == 1
    assert reviews[0].round_number == 1
