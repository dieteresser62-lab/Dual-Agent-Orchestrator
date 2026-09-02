from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

import pytest
import plan_handoff

from agent_adapters import AgentOutputError
from audit_trail import ReviewAuditEvent
from artifact_models import InvocationFailurePayload, provider_text_evidence
from agent_runtime import (
    AgentInvocationError,
    AgentProcessError,
    NativeAgentCodexOutput,
    NativeAgentReviewOutput,
    QuotaReset,
    QuotaWaitPolicy,
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
    ReviewEvidence,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
    StopRequest,
    apply_finding_response,
    apply_reviewer_finding_update,
)
from gates import PathClasses, StopRule, TestChangeEvidence as GateTestChangeEvidence
from native_codex_contract import NativeCodexRequestKind
from inbox_watcher import WatchTaskDisposition, WatchTaskResult
from orchestrator import run_v3_final_review, run_v3_work_unit
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
    WorkflowCorrectionBoundary,
    WorkflowContext,
    WorkflowContractError,
    WorkflowEngine,
    WorkflowExecutionError,
    WorkflowHistory,
    WorkflowRunResult,
    ValidationExecutionError,
)
from workflow_state import (
    AgentFailureKind,
    GateReason,
    GateStatus,
    WorkflowStep,
    WorkflowState,
    WorkUnitKind,
    WorkUnitStatus,
    ProtocolBinding,
    ProtocolMode,
    init_workflow_state,
)


TEST_FILE = "tests/test_workflow.py"
START_COMMIT = "a" * 40


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
    correction_boundaries: list[WorkflowCorrectionBoundary] = field(default_factory=list)
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
        if invocation.step is WorkflowStep.CODEX_FINAL_REVIEW:
            self._assert_checkpointed_attestation(
                self.snapshots[max(self.snapshot_index, 0)].fingerprint
            )
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

    def prepare_correction(
        self, findings
    ) -> WorkflowCorrectionBoundary:
        assert any(item.status.value == "OPEN" for item in findings)
        return self.correction_boundaries.pop(0)

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
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        self.structured_events.append(
            ("native-codex", (output, previous_findings))
        )

    def persist_native_review_contract(
        self,
        output: NativeAgentReviewOutput,
        fingerprint: str,
        round_number: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        self.structured_events.append(
            (
                "native-review",
                (output, fingerprint, round_number, previous_findings),
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


def _slice_state(
    scope_paths: tuple[str, ...] = ("src/early.py", "src/latest.py", TEST_FILE),
    scope_change_groups: tuple[tuple[str, ...], ...] | None = None,
):
    state = init_workflow_state(
        run_id="run-1",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
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

    bundle = WorkflowEngine._native_codex_request(
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
    )


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
    ("kind", "expected_automatic", "expected_diagnostic"),
    (
        (AgentFailureKind.QUOTA, True, "AGENT-INVOCATION"),
        (AgentFailureKind.NETWORK, True, "AGENT-INVOCATION"),
        (AgentFailureKind.PROCESS, False, "AGENT-PROCESS"),
    ),
)
def test_r6_failure_record_precedes_retry_decision_and_uses_s1_classification(
    kind: AgentFailureKind,
    expected_automatic: bool,
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
    assert payload.failure_class == classified.failure_class.value == "transient"
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
    elif kind is AgentFailureKind.NETWORK:
        assert payload.retry_delay_seconds == 5
        assert payload.resume_at_utc == "2026-08-31T10:00:05+00:00"
    else:
        assert payload.retry_delay_seconds == 0
        assert payload.resume_at_utc is None


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
    assert payload.failure_class == "resumable_halt"
    assert payload.diagnostic_code == "AGENT-OUTPUT"
    assert payload.automatic_resume is failure.automatic_resume is True
    assert payload.auto_resume_count == failure.auto_resume_count == 1
    assert payload.parse_path == "claude:text:local-clock"
    assert payload.source_timezone == "Europe/Berlin"
    assert payload.reset_at_utc == "2026-08-31T13:00:00+00:00"
    assert payload.retry_delay_seconds == payload.safety_margin_seconds == 7
    assert payload.resume_at_utc == "2026-08-31T13:00:07+00:00"
    assert persisted.current_work_unit.status is WorkUnitStatus.WAITING_FOR_QUOTA


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
        persisted_native: list[tuple[NativeAgentReviewOutput, str, int]] = field(
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
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            assert previous_findings == ()
            self.persisted_native.append((output, fingerprint, round_number))

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
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
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
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            _ = output
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
    implementation = WorkflowEngine._native_codex_request(
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
    correction = WorkflowEngine._native_codex_request(
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
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            assert previous_findings == ()
            self.persisted.append(output)

    state = replace(
        init_workflow_state(
            run_id="native-plan-runtime",
            task_file="/repo/task.md",
            branch="feature/workflow",
            branch_base=START_COMMIT,
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
            _previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            self.persisted_reviews.append(output)

        def persist_native_codex_contract(
            self,
            output: NativeAgentCodexOutput,
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
            _previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            self.persisted_reviews.append(output)

        def persist_native_codex_contract(
            self,
            output: NativeAgentCodexOutput,
            _previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            self.persisted_codex.append(output)

    state = init_workflow_state(
        run_id="combined-native-plan-convergence",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
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


def test_combined_native_final_restart_rebinds_codex_and_claude_without_legacy_parsers(
    monkeypatch,
) -> None:
    changes = _changes("f", "src/early.py", TEST_FILE, full_diff="CORRECTED BRANCH")
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The final branch required a commit-bound correction.",
        acceptance_test="The repeated final review closes the corrected fingerprint.",
        origin=FindingOrigin("FINAL", 1, AgentRole.CLAUDE),
        responses=(
            FindingResponse(
                FindingResponseDecision.ACCEPTED,
                "The correction work unit is committed and ready for final review.",
            ),
        ),
    )

    @dataclass
    class RestartedNativeFinalDriver(FakeDriver):
        persisted_reviews: list[NativeAgentReviewOutput] = field(default_factory=list)
        persisted_codex: list[NativeAgentCodexOutput] = field(default_factory=list)

        def invoke_codex(self, invocation: CodexInvocation) -> NativeAgentCodexOutput:
            self.codex_calls.append(invocation)
            assert invocation.step is WorkflowStep.CODEX_FINAL_REVIEW
            assert invocation.prompt == ""
            assert invocation.native_request is not None
            bound = invocation.native_request.bound_context
            assert bound.context.current_fingerprint == changes.fingerprint
            assert bound.context.previous_findings == (finding,)
            reported = replace(
                finding,
                responses=(
                    *finding.responses,
                    FindingResponse(
                        FindingResponseDecision.ACCEPTED,
                        "The corrected branch passes the complete final self-check.",
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
                    findings=(reported,),
                    self_check="Rechecked the complete corrected branch and its bindings.",
                ),
                canonical_json=json.dumps(
                    {
                        "request_id": bound.request_id,
                        "result_type": "final_report_result",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                request_id=bound.request_id,
                response_sha256="a" * 64,
            )

        def invoke_reviewer(
            self, invocation: ReviewerInvocation
        ) -> NativeAgentReviewOutput:
            self.reviewer_calls.append(invocation)
            assert invocation.step is WorkflowStep.CLAUDE_FINAL_REVIEW
            assert invocation.prompt == ""
            assert invocation.native_request is not None
            bound = invocation.native_request.bound_context
            assert bound.context.diff_fingerprint == changes.fingerprint
            assert any(
                item["evidence_id"] == "codex-final-report"
                for item in invocation.native_request.document["evidence_manifest"]
            )
            final_finding = bound.context.previous_findings[0]
            assert len(final_finding.responses) == 2
            closed = replace(
                final_finding,
                status=FindingStatus.CLOSED,
                status_rationale="The repeated final review verifies the correction.",
            )
            attestation = bound.context.validation_attestation
            assert attestation is not None
            return NativeAgentReviewOutput(
                result=ContractResult(
                    reviewer=AgentRole.CLAUDE,
                    approval=True,
                    stopped=False,
                    stop_request=None,
                    validation=attestation,
                    test_files=bound.context.test_files,
                    pre_mortem="A later final restart could bind the stale branch fingerprint.",
                    evidence=ReviewEvidence(
                        "native final restart and correction binding",
                        "stale final-report reuse",
                        "the corrected fingerprint is not bound end to end",
                    ),
                    findings=(closed,),
                    anchors=(),
                ),
                canonical_json=json.dumps(
                    {"request_id": bound.request_id, "decision": "approved"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                request_id=bound.request_id,
            )

        def persist_native_review_contract(
            self,
            output: NativeAgentReviewOutput,
            _fingerprint: str,
            _round_number: int,
            _previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            self.persisted_reviews.append(output)

        def persist_native_codex_contract(
            self,
            output: NativeAgentCodexOutput,
            _previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            self.persisted_codex.append(output)

    state = replace(
        _completed_single_slice_state().start_final_review_work_unit(),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    )
    history = WorkflowHistory(state.current_work_unit_id, findings=(finding,))
    driver = RestartedNativeFinalDriver(
        snapshots=[changes], codex_outputs=[], reviewer_outputs=[]
    )
    state, history = WorkflowEngine(driver)._run_final_codex_report(
        state, _context(), history
    )
    assert state.current_step is WorkflowStep.CLAUDE_FINAL_REVIEW
    state, history = WorkflowEngine(driver)._run_review(
        state, _context(), history, AgentRole.CLAUDE
    )

    assert state.current_step is WorkflowStep.COMPLETED
    assert history.findings[0].status is FindingStatus.CLOSED
    assert len(driver.persisted_codex) == 1
    assert len(driver.persisted_reviews) == 1
    assert [item[0] for item in driver.authoritative_finding_calls] == [
        WorkflowStep.CODEX_FINAL_REVIEW.value,
        WorkflowStep.CLAUDE_FINAL_REVIEW.value,
    ]


def test_combined_native_post_correction_final_transition_carries_complete_ledger(
    monkeypatch,
) -> None:
    corrected = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The correction addresses the final-review blocker.",
        acceptance_test="The next final review receives the complete ledger.",
        origin=FindingOrigin("FINAL", 1, AgentRole.CLAUDE),
    )
    historical = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.CLOSED,
        summary="A prior finding remains part of the branch-wide ledger.",
        acceptance_test="The completed finding is carried into final review.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
        status_rationale="Verified before the correction work unit.",
    )

    @dataclass
    class CarryingDriver(FakeDriver):
        def carry_forward_native_findings(
            self,
            _state: WorkflowState,
            current_findings: tuple[FindingRecord, ...],
        ) -> tuple[FindingRecord, ...]:
            assert current_findings == (corrected,)
            return (historical, corrected)

    state = (
        _completed_single_slice_state()
        .start_final_review_work_unit()
        .complete_current_work_unit()
        .start_correction_work_unit(
            start_commit="b" * 40,
            scope_paths=("src/early.py", TEST_FILE),
            start_fingerprint="c" * 64,
            finding_ids=("C-02",),
        )
        .complete_current_slice(commit_ref="d" * 40)
    )
    state = replace(
        state,
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    )
    history = WorkflowHistory(state.current_work_unit_id, findings=(corrected,))
    driver = CarryingDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])
    engine = WorkflowEngine(driver)
    captured: list[tuple[WorkflowState, WorkflowHistory]] = []

    def capture(
        current_state: WorkflowState,
        _context: WorkflowContext,
        current_history: WorkflowHistory | None = None,
    ) -> WorkflowRunResult:
        assert current_history is not None
        captured.append((current_state, current_history))
        return WorkflowRunResult(current_state, current_history)

    monkeypatch.setattr(engine, "run_current_work_unit", capture)
    result = engine.run_final_review(state, _context(), history)

    assert result.state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
    assert result.history.findings == (historical, corrected)
    assert driver.checkpoint_histories[-1].findings == (historical, corrected)


def test_combined_native_codex_record_ahead_recovery_precedes_mirror_guard() -> None:
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
    )

    @dataclass
    class RecoveryDriver(FakeDriver):
        persisted: list[NativeAgentCodexOutput] = field(default_factory=list)

        def recover_pending_native_codex(self, *_args):  # type: ignore[no-untyped-def]
            return recovered_output

        def invoke_codex(self, invocation: CodexInvocation) -> NativeAgentCodexOutput:
            raise AssertionError("record-ahead recovery must suppress the provider")

        def persist_native_codex_contract(
            self,
            output: NativeAgentCodexOutput,
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            assert previous_findings == (finding,)
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
        authoritative_finding_error="record-ahead mirror is expected to differ",
    )

    advanced, history = WorkflowEngine(driver)._run_codex(
        state,
        _context(),
        WorkflowHistory(state.current_work_unit_id, findings=(finding,)),
    )

    assert advanced.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert history.findings == (answered,)
    assert driver.authoritative_finding_calls == []
    assert driver.persisted == [recovered_output]


def test_native_codex_final_report_bypasses_legacy_marker_parser(
    monkeypatch,
) -> None:
    @dataclass
    class NativeFinalDriver(FakeDriver):
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
                self_check="Checked contracts, recovery, and branch boundaries.",
            )
            return NativeAgentCodexOutput(
                result=result,
                canonical_json=json.dumps(
                    {
                        "request_id": bound.request_id,
                        "result_type": "final_report_result",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                request_id=bound.request_id,
                response_sha256="d" * 64,
            )

        def persist_native_codex_contract(
            self,
            output: NativeAgentCodexOutput,
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            assert previous_findings == ()
            self.persisted.append(output)

    changes = _changes("e", "src/early.py", TEST_FILE)
    state = replace(
        _completed_single_slice_state().start_final_review_work_unit(),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
        ),
    )
    driver = NativeFinalDriver(
        snapshots=[changes], codex_outputs=[], reviewer_outputs=[]
    )
    advanced, history = WorkflowEngine(driver)._run_final_codex_report(
        state, _context(), WorkflowHistory(state.current_work_unit_id)
    )

    assert advanced.current_step is WorkflowStep.CLAUDE_FINAL_REVIEW
    assert history.codex_final_report is not None
    assert len(driver.persisted) == 1
    invocation = driver.codex_calls[0]
    assert invocation.native_request is not None
    assert invocation.native_request.document["request_type"] == "final_report"


def test_native_codex_request_builder_covers_plan_and_final_report() -> None:
    plan_state = replace(
        init_workflow_state(
            run_id="native-plan",
            task_file="/repo/task.md",
            branch="feature/workflow",
            branch_base=START_COMMIT,
            slice_count=1,
            task_digest="a" * 64,
            task_scope_patterns=("docs/internal/plan.md",),
            target_branch="feature/workflow",
        ),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
        ),
    )
    plan_contract = CodexStepContract(
        "native-plan",
        ReadinessMarker.PLAN,
        "01",
        1,
        require_slice_plan=True,
        plan_artifact_path="docs/internal/plan.md",
    )
    plan_bundle = WorkflowEngine._native_codex_request(
        state=plan_state,
        context=_context(),
        history=WorkflowHistory(plan_state.current_work_unit_id),
        contract=plan_contract,
        request_kind=NativeCodexRequestKind.PLAN,
    )

    final_state = replace(
        _completed_single_slice_state().start_final_review_work_unit(),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
        ),
    )
    final_contract = CodexStepContract(
        "native-final",
        ReadinessMarker.FINAL_REPORT,
        "FINAL",
        1,
        review_fingerprint="f" * 64,
    )
    final_bundle = WorkflowEngine._native_codex_request(
        state=final_state,
        context=_context(),
        history=WorkflowHistory(final_state.current_work_unit_id),
        contract=final_contract,
        request_kind=NativeCodexRequestKind.FINAL_REPORT,
    )

    assert plan_bundle.document["request_type"] == "plan"
    assert plan_bundle.document["current_fingerprint"] == "a" * 64
    assert plan_bundle.document["authorized_paths"] == ["docs/internal/plan.md"]
    transported_plan_request = json.loads(plan_bundle.canonical_json)
    assert transported_plan_request["assignment"] == _context().assignment
    assert final_bundle.document["request_type"] == "final_report"
    assert final_bundle.document["current_fingerprint"] == "f" * 64
    assert final_bundle.document["authorized_paths"] == sorted(
        final_state.slices[0].scope_paths
    )


def test_native_final_review_rejects_missing_codex_report_before_request() -> None:
    changes = _changes("c", "src/early.py", TEST_FILE)
    state = replace(
        _slice_state().with_current_step(WorkflowStep.CLAUDE_FINAL_REVIEW),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            "native-claude-review-v2",
        ),
    )
    contract = StepContract(
        "native-final-review",
        AgentRole.CLAUDE,
        ApprovalMarker.FINAL,
        "FINAL",
        1,
        changes.fingerprint,
        _attestation(changes),
    )

    with pytest.raises(
        WorkflowExecutionError,
        match="requires a persisted Codex final report",
    ):
        WorkflowEngine._native_review_request(
            state=state,
            context=_context(),
            history=WorkflowHistory(state.current_work_unit_id),
            contract=contract,
            changes=changes,
            evidence_kind=EvidenceKind.FULL_BRANCH,
            review_diff=changes.full_diff,
            review_packet=None,
            expected_test_files=(TEST_FILE,),
        )


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
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            ApprovalMarker.FINAL,
            "final",
            EvidenceKind.FULL_BRANCH,
        ),
    ),
)
def test_native_request_builder_covers_plan_slice_and_final_reviews(
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
    if marker is ApprovalMarker.FINAL:
        history = replace(history, codex_final_report=_final_report())
    contract = StepContract(
        f"native-{expected_kind}-review",
        AgentRole.CLAUDE,
        marker,
        "FINAL" if marker is ApprovalMarker.FINAL else "01",
        1,
        changes.fingerprint,
        _attestation(changes),
    )

    bundle = WorkflowEngine._native_review_request(
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
    assert ("codex-final-report" in evidence_ids) is (
        marker is ApprovalMarker.FINAL
    )


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

    bundle = WorkflowEngine._native_review_request(
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
                    findings=history.findings,
                    anchors=(),
                ),
                canonical_json=(
                    '{"request_id":"' + bound.request_id + '","result":"approved"}'
                ),
                request_id=bound.request_id,
            )

        def invoke_reviewer(self, invocation: ReviewerInvocation):  # type: ignore[no-untyped-def]
            raise AssertionError("provider must not run during record-ahead recovery")

        def persist_native_review_contract(
            self,
            output: NativeAgentReviewOutput,
            fingerprint: str,
            round_number: int,
            previous_findings: tuple[FindingRecord, ...],
        ) -> None:
            assert fingerprint == changes.fingerprint
            assert round_number == 1
            assert previous_findings == ()
            self.persisted_native.append(output)

    state = replace(
        _slice_state().with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW),
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            "native-claude-review-v2",
        ),
    )
    history = WorkflowHistory(state.current_work_unit_id)
    driver = RecoveringNativeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[],
        authoritative_finding_error="record-ahead mirror is expected to differ",
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


def test_managed_audit_paths_are_added_after_codex_plan_only() -> None:
    state = init_workflow_state(
        run_id="run-managed-audit",
        task_file="/repo/inbox/Bug.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
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


def test_new_validation_requirement_without_new_fingerprint_does_not_rerun() -> None:
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
        reviewer_outputs=[denial],
        deltas={(unchanged.fingerprint, unchanged.fingerprint): "no content change"},
    )

    with pytest.raises(WorkflowExecutionError, match="requirements changed"):
        WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert len(driver.validation_requests) == 1


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


def test_second_quota_on_same_step_stops_with_exit_two_without_reviewer() -> None:
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

    assert result.exit_code == 2
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    assert result.state.current_work_unit.gate.reason is GateReason.QUOTA
    assert len(result.state.current_work_unit.invocation_failures) == 2
    assert result.state.current_work_unit.invocation_failures[-1].automatic_resume is False
    assert len(driver.codex_calls) == 2
    assert driver.reviewer_calls == []


@pytest.mark.parametrize(
    ("policy", "reset_after_seconds"),
    (
        (QuotaWaitPolicy(automatic=False), 10),
        (QuotaWaitPolicy(maximum_wait_seconds=5), 10),
        (QuotaWaitPolicy(), None),
    ),
)
def test_non_terminable_quota_is_manual_exit_two(
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

    assert result.exit_code == 2
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
    failure = result.state.current_work_unit.invocation_failures[-1]
    assert failure.automatic_resume is False
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
    revalidated, halted = engine._revalidate_waiting_diff(acknowledged, failure)

    assert halted is False
    assert revalidated.current_work_unit.status is WorkUnitStatus.IN_PROGRESS

    changed_again = _changes("3", "src/early.py", TEST_FILE)
    driver.snapshots[0] = changed_again
    halted_again, halted = engine._revalidate_waiting_diff(acknowledged, failure)

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


def test_productive_file_limit_halts_before_first_agent_and_rechecks_on_resume() -> None:
    scope = tuple(f"src/file_{index}.py" for index in range(11))
    driver = FakeDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])
    engine = WorkflowEngine(driver)

    halted = engine.run_current_work_unit(_slice_state(scope), _context())

    assert halted.exit_code == 4
    assert halted.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert "PRODUCTIVE-FILE-LIMIT" in halted.state.current_work_unit.gate.detail
    assert "src/file_0.py=productive" in halted.state.current_work_unit.gate.detail
    assert driver.codex_calls == []
    resumed = halted.state.resume_after_user_decision()
    halted_again = engine.run_current_work_unit(resumed, _context(), halted.history)
    assert halted_again.exit_code == 4
    assert driver.codex_calls == []


def test_productive_file_limit_uses_persisted_rename_groups() -> None:
    rename_group = ("src/renamed_new.py", "src/renamed_old.py")
    nine_singletons = tuple((f"src/file_{index}.py",) for index in range(9))
    ten_groups = tuple(sorted((*nine_singletons, rename_group)))
    ten_paths = tuple(sorted(path for group in ten_groups for path in group))
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[_codex_stop("DOMAIN-001")],
        reviewer_outputs=[],
    )
    context = replace(
        _context(), stop_rules=(StopRule("DOMAIN-001", "stop after limit check"),)
    )

    allowed = WorkflowEngine(driver).run_current_work_unit(
        _slice_state(ten_paths, ten_groups), context
    )

    assert allowed.exit_code == 4
    assert allowed.state.current_work_unit.gate.detail.startswith("DOMAIN-001 |")
    assert len(driver.codex_calls) == 1

    eleven_groups = tuple(sorted((*ten_groups, ("src/file_9.py",))))
    eleven_paths = tuple(sorted(path for group in eleven_groups for path in group))
    blocked_driver = FakeDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])
    blocked = WorkflowEngine(blocked_driver).run_current_work_unit(
        _slice_state(eleven_paths, eleven_groups), _context()
    )

    assert blocked.exit_code == 4
    assert "11 productive change units" in blocked.state.current_work_unit.gate.detail
    assert "12 productive change units" not in blocked.state.current_work_unit.gate.detail
    assert blocked_driver.codex_calls == []


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


def test_codex_validation_stop_auto_extends_scope_from_completed_approved_slice() -> None:
    state = init_workflow_state(
        run_id="run-auto-remediation",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
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
        scope_paths=("src/current.py", "tests/current.py"),
        start_fingerprint="1" * 64,
    )
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[
            "STOP_REQUESTED: VALIDATION-UNAVAILABLE | prior adapter needs normalization\n"
            "REMEDIATION_PATHS: src/prior.py, tests/prior.py\n"
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
    assert advanced.current_slice.scope_paths == (
        "src/current.py",
        "src/prior.py",
        "tests/current.py",
        "tests/prior.py",
    )
    assert len(driver.codex_calls) == 2
    assert driver.codex_calls[1].native_request is not None
    assert "AUTOMATIC PRIOR-SLICE REMEDIATION" in driver.codex_calls[1].native_request.canonical_json
    assert "src/prior.py" in driver.codex_calls[1].native_request.canonical_json


def test_codex_reprompts_once_when_remediation_path_is_already_authorized() -> None:
    state = init_workflow_state(
        run_id="run-existing-remediation",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
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
        "resolve the documented blocker before resuming the same step"
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


def test_final_review_resumes_redundant_gate_from_exact_prior_test_approval() -> None:
    branch = _changes("7", "src/early.py", TEST_FILE, full_diff="COMPLETE BRANCH")
    evidence = GateTestChangeEvidence((TEST_FILE,), "9" * 64)
    state = init_workflow_state(
        run_id="run-final-test-inheritance",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
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
    ).await_user_gate(
        reason=GateReason.TEST_CHANGE,
        detail="test approval required",
        fingerprint=evidence.fingerprint,
        paths=evidence.paths,
    ).record_user_gate_decision(
        approved=True,
        fingerprint=evidence.fingerprint,
        paths=evidence.paths,
        rationale="exact Slice test diff reviewed",
    ).record_active_test_approval(
        evidence.fingerprint,
        evidence.paths,
    ).complete_current_slice(
        commit_ref="b" * 40,
    ).start_final_review_work_unit().await_user_gate(
        reason=GateReason.TEST_CHANGE,
        detail="test changes require explicit approval before review",
        fingerprint=evidence.fingerprint,
        paths=evidence.paths,
    )
    driver = FakeDriver(
        snapshots=[branch],
        codex_outputs=[_final_report()],
        reviewer_outputs=[
            _final_approval(AgentRole.CLAUDE),
        ],
        test_evidence_by_fingerprint={branch.fingerprint: evidence},
    )
    context = replace(
        _context(),
        test_changes_approved=False,
        dynamic_test_scope=True,
    )

    result = run_v3_final_review(
        WorkflowEngine(driver),
        state,
        context,
        WorkflowHistory(state.current_work_unit_id),
    )

    assert result.completed
    assert result.state.current_work_unit.gate.reason is GateReason.NONE
    assert result.state.current_work_unit.active_test_fingerprint == evidence.fingerprint
    assert result.state.current_work_unit.active_test_paths == evidence.paths
    inherited = result.state.current_work_unit.gate_decisions
    assert len(inherited) == 1
    assert inherited[0].rationale == "exact Slice test diff reviewed"
    assert len(driver.codex_calls) == 1
    assert len(driver.reviewer_calls) == 1


def test_codex_final_report_not_ready_is_resumable_without_review() -> None:
    branch = _changes(
        "7",
        "src/early.py",
        full_diff="COMPLETE BRANCH",
        start_commit=START_COMMIT,
    )
    driver = FakeDriver(
        snapshots=[branch],
        codex_outputs=["FINAL_REPORT_READY: NO\nSTATUS: DONE"],
        reviewer_outputs=[],
    )

    result = run_v3_final_review(
        WorkflowEngine(driver), _completed_single_slice_state(), _context()
    )

    assert result.exit_code == 4
    assert result.state.current_step is WorkflowStep.CODEX_FINAL_REVIEW
    assert result.state.current_work_unit.status is WorkUnitStatus.AWAITING_USER_DECISION
    assert result.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert result.state.current_work_unit.gate.detail is not None
    assert result.state.current_work_unit.gate.detail.startswith(
        "CODEX-FINAL-REPORT-NOT-READY |"
    )
    assert driver.reviewer_calls == []


def test_workflow_history_roundtrips_final_report_and_loads_legacy_shape() -> None:
    history = WorkflowHistory(
        7,
        codex_final_report="Branch risk found.\nFINAL_REPORT_READY: YES\nSTATUS: DONE",
    )

    restored = WorkflowHistory.from_dict(history.to_dict())
    legacy = history.to_dict()
    legacy.pop("codex_final_report")

    assert restored == history
    assert WorkflowHistory.from_dict(legacy).codex_final_report is None


def test_terminable_quota_resumes_same_final_review_for_watch_completion() -> None:
    received = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    branch = _changes(
        "7",
        "src/early.py",
        TEST_FILE,
        full_diff="COMPLETE BRANCH",
        start_commit=START_COMMIT,
    )
    driver = FakeDriver(
        snapshots=[branch],
        codex_outputs=[_final_report()],
        reviewer_outputs=[_final_approval(AgentRole.CLAUDE)],
        reviewer_failures=[
            _invocation_failure(
                AgentRole.CLAUDE,
                AgentFailureKind.QUOTA,
                "final-claude-quota",
                received_at=received,
                reset_after_seconds=2,
            ),
            None,
        ],
    )
    clock = {"now": received}
    sleeps: list[float] = []
    heartbeats: list[str] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += timedelta(seconds=seconds)

    result = WorkflowEngine(
        driver,
        now_fn=lambda: clock["now"],
        sleep_fn=sleep,
        heartbeat_fn=heartbeats.append,
    ).run_final_review(
        replace(
            _completed_single_slice_state(),
            protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
        ),
        replace(
            _context(),
            quota_wait_policy=QuotaWaitPolicy(
                automatic=True,
                safety_margin_seconds=1,
                maximum_wait_seconds=10,
                maximum_auto_resumes=1,
                heartbeat_interval_seconds=1,
            ),
        ),
    )
    watch_result = WatchTaskResult.from_workflow(result)

    assert result.completed
    assert watch_result.disposition is WatchTaskDisposition.COMPLETED
    assert watch_result.exit_code == 0
    assert [call.step for call in driver.reviewer_calls] == [
        WorkflowStep.CLAUDE_FINAL_REVIEW,
        WorkflowStep.CLAUDE_FINAL_REVIEW,
    ]
    assert sleeps == [1, 1, 1]
    assert heartbeats
    assert any("work_unit=3" in item for item in heartbeats)


def test_correction_actual_diff_still_enforces_productive_file_limit() -> None:
    first_branch = _changes(
        "7",
        "src/early.py",
        TEST_FILE,
        full_diff="ORIGINAL BRANCH",
        start_commit=START_COMMIT,
    )
    productive_paths = tuple(f"src/fix_{index}.py" for index in range(11))
    correction = _changes(
        "8",
        *productive_paths,
        TEST_FILE,
        full_diff="LARGE CORRECTION",
        start_commit="b" * 40,
    )
    broad_scope = tuple(sorted((*productive_paths, TEST_FILE)))
    driver = FakeDriver(
        snapshots=[first_branch, correction],
        codex_outputs=[
            _final_report(),
            _codex_ready("C-01", slice_id="02"),
        ],
        reviewer_outputs=[_final_denial(AgentRole.CLAUDE, "C-01")],
        correction_boundaries=[
            WorkflowCorrectionBoundary(
                start_commit="b" * 40,
                scope_paths=broad_scope,
                start_fingerprint="0" * 64,
            )
        ],
    )

    halted = WorkflowEngine(driver).run_final_review(
        _completed_single_slice_state(), _context()
    )

    assert halted.exit_code == 4
    assert halted.state.current_work_unit.kind is WorkUnitKind.CORRECTION
    assert halted.state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert halted.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert "PRODUCTIVE-FILE-LIMIT" in halted.state.current_work_unit.gate.detail
    assert driver.validation_calls == [first_branch.fingerprint]
    assert [call.reviewer for call in driver.reviewer_calls] == [AgentRole.CLAUDE]


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
