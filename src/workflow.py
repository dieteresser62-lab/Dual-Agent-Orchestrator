from __future__ import annotations

import re
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Protocol

from agent_runtime import (
    AgentInvocationError,
    QuotaWaitPolicy,
    wait_until_quota_resume,
)

from audit_trail import (
    AuditEvent,
    AuthorizedTestChanges,
    ReviewAuditEvent,
    ValidationAuditEvent,
)
from contracts import (
    AgentRole,
    AnchorRecord,
    ApprovalMarker,
    CodexContractResult,
    CodexStepContract,
    ContractResult,
    ContractValidationError,
    FindingRecord,
    FindingClass,
    FindingStatus,
    ReadinessMarker,
    StepContract,
    StopRequest,
    ValidationAttestation,
    validate_codex_response,
    validate_review_response,
)
from gates import (
    BRANCH_MISMATCH_RULE_ID,
    BUILTIN_STOP_RULES,
    UNEXPECTED_PATH_RULE_ID,
    VALIDATION_UNAVAILABLE_RULE_ID,
    AnchorChangeEvidence,
    PathClasses,
    StopRule,
    TestChangeEvidence,
    detect_anchor_changes,
    evaluate_productive_file_limit,
)
from prompts import (
    build_v3_codex_prompt,
    build_v3_review_contract,
    build_v3_review_prompt,
)
from validation_matrix import (
    ValidationCommand,
    ValidationMatrix,
    ValidationMatrixError,
    ValidationRequest,
    select_validation_request,
)
from workflow_state import (
    AgentFailureKind,
    GateDecisionRecord,
    GateReason,
    GateStatus,
    InvocationFailureRecord,
    Reviewer,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
logger = logging.getLogger(__name__)
DEFAULT_WORKFLOW_VALIDATION_MATRIX = ValidationMatrix(
    default_command=ValidationCommand(
        argv=("python3", "-m", "pytest", "tests/", "-v")
    )
)


class WorkflowExecutionError(RuntimeError):
    """Raised when the development workflow must stop without advancing a step."""


class WorkflowContractError(WorkflowExecutionError):
    """Raised after a reviewer response and its compact format repair both fail."""


class ValidationExecutionError(WorkflowExecutionError):
    """Raised by a v3 driver when required validation cannot be executed."""


class EvidenceKind(str, Enum):
    FULL_SLICE = "full_slice"
    CORRECTION_DELTA = "correction_delta"


@dataclass(frozen=True)
class WorkflowChanges:
    start_commit: str
    fingerprint: str
    paths: tuple[str, ...]
    full_diff: str
    gate_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.start_commit.strip():
            raise ValueError("workflow changes require a start commit")
        if not SHA256_PATTERN.fullmatch(self.fingerprint):
            raise ValueError("workflow changes require a SHA-256 fingerprint")
        normalized = tuple(sorted(set(self.paths)))
        if not normalized or normalized != self.paths:
            raise ValueError("workflow change paths must be sorted, unique, and non-empty")
        if not self.full_diff.strip():
            raise ValueError("workflow changes require the complete slice diff")
        normalized_gate_paths = tuple(sorted(set(self.gate_paths)))
        if self.gate_paths and normalized_gate_paths != self.gate_paths:
            raise ValueError("workflow gate paths must be sorted and unique")

    @property
    def user_gate_paths(self) -> tuple[str, ...]:
        """Include historical rename paths when the driver captured them."""
        return self.gate_paths or self.paths

@dataclass(frozen=True)
class WorkflowContext:
    assignment: str
    distilled_plan: str
    slice_summary: str
    expected_test_files: tuple[str, ...] = ()
    test_changes_approved: bool = False
    test_path_patterns: tuple[str, ...] = ("tests/**",)
    manual_slice_gate: bool = False
    approved_anchors: tuple[AnchorRecord, ...] = ()
    current_anchors: tuple[AnchorRecord, ...] = ()
    path_classes: PathClasses = PathClasses()
    stop_rules: tuple[StopRule, ...] = ()
    max_productive_files: int = 10
    current_branch: str | None = None
    validation_matrix: ValidationMatrix = DEFAULT_WORKFLOW_VALIDATION_MATRIX
    red_state_followup_slice: str | None = None
    retry_incomplete_validation: bool = False
    quota_wait_policy: QuotaWaitPolicy = QuotaWaitPolicy()

    def __post_init__(self) -> None:
        if not self.assignment.strip():
            raise ValueError("workflow context requires an assignment")
        if not self.distilled_plan.strip():
            raise ValueError("workflow context requires a distilled plan")
        if not self.slice_summary.strip():
            raise ValueError("workflow context requires a current-slice summary")
        normalized = tuple(sorted(set(self.expected_test_files)))
        if normalized != self.expected_test_files:
            raise ValueError("expected test files must be sorted and unique")
        if (
            not self.test_path_patterns
            or any(not pattern.strip() for pattern in self.test_path_patterns)
            or len(set(self.test_path_patterns)) != len(self.test_path_patterns)
        ):
            raise ValueError("test path patterns must be non-empty and unique")
        rule_ids = tuple(rule.id for rule in self.stop_rules)
        builtin_ids = {rule.id for rule in BUILTIN_STOP_RULES}
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("declared stop rule ids must be unique")
        if builtin_ids.intersection(rule_ids):
            raise ValueError("declared stop rules cannot shadow built-in rule ids")
        if (
            isinstance(self.max_productive_files, bool)
            or not isinstance(self.max_productive_files, int)
            or self.max_productive_files < 1
        ):
            raise ValueError("max_productive_files must be a positive integer")
        if self.current_branch is not None and not self.current_branch.strip():
            raise ValueError("current_branch must be non-empty when provided")
        if (
            self.red_state_followup_slice is not None
            and not self.red_state_followup_slice.strip()
        ):
            raise ValueError(
                "red_state_followup_slice must be non-empty when provided"
            )
        if not isinstance(self.retry_incomplete_validation, bool):
            raise ValueError("retry_incomplete_validation must be a boolean")
        if not isinstance(self.quota_wait_policy, QuotaWaitPolicy):
            raise ValueError("quota_wait_policy must be a QuotaWaitPolicy")

    @property
    def distilled_context(self) -> str:
        approved = self._render_anchors(self.approved_anchors)
        current = self._render_anchors(self.current_anchors)
        return (
            f"PLAN\n{self.distilled_plan}\n\n"
            f"CURRENT SLICE\n{self.slice_summary}\n\n"
            f"APPROVED PLAN ANCHORS\n{approved}\n\n"
            f"CURRENT ANCHORS\n{current}\n\n"
            f"MACHINE STOP RULES (complete, untruncated)\n{self._render_stop_rules()}"
        )

    def _render_stop_rules(self) -> str:
        return "\n".join(
            f"{rule.id} | {rule.description}"
            for rule in (*BUILTIN_STOP_RULES, *self.stop_rules)
        )

    @property
    def known_stop_rule_ids(self) -> frozenset[str]:
        return frozenset(
            rule.id for rule in (*BUILTIN_STOP_RULES, *self.stop_rules)
        )

    @staticmethod
    def _render_anchors(anchors: tuple[AnchorRecord, ...]) -> str:
        if not anchors:
            return "NONE"
        return "\n".join(
            f"{item.anchor_id} | {item.origin} | {item.input_fixture} | "
            f"{item.expected} | {item.tolerance}"
            for item in sorted(anchors, key=lambda anchor: anchor.anchor_id)
        )


@dataclass(frozen=True)
class CodexInvocation:
    step: WorkflowStep
    round_number: int
    prompt: str


@dataclass(frozen=True)
class ReviewerInvocation:
    reviewer: AgentRole
    round_number: int
    evidence_kind: EvidenceKind
    fingerprint: str
    paths: tuple[str, ...]
    prompt: str


@dataclass(frozen=True)
class ContractRepairInvocation:
    reviewer: AgentRole
    rejected_output: str
    validation_error: str
    contract: str


@dataclass(frozen=True)
class WorkflowCommitRequest:
    slice_id: int
    fingerprint: str
    attestation: ValidationAttestation
    claude_review: ContractResult
    antigravity_review: ContractResult
    findings: tuple[FindingRecord, ...]
    red_state_followup_slice: str | None = None


class WorkflowDriver(Protocol):
    def invoke_codex(self, invocation: CodexInvocation) -> str: ...

    def collect_changes(self, start_commit: str) -> WorkflowChanges: ...

    def collect_correction_delta(
        self, previous_fingerprint: str, current_fingerprint: str
    ) -> str: ...

    def detect_test_changes(
        self, changes: WorkflowChanges, patterns: tuple[str, ...]
    ) -> TestChangeEvidence | None: ...

    def validate(
        self, changes: WorkflowChanges, request: ValidationRequest
    ) -> ValidationAttestation: ...

    def invoke_reviewer(self, invocation: ReviewerInvocation) -> str: ...

    def repair_review_contract(self, invocation: ContractRepairInvocation) -> str: ...

    def commit_slice(self, request: WorkflowCommitRequest) -> str: ...

    def checkpoint(self, state: WorkflowState, history: WorkflowHistory) -> None: ...


@dataclass(frozen=True)
class WorkflowHistory:
    work_unit_id: int
    findings: tuple[FindingRecord, ...] = ()
    events: tuple[AuditEvent, ...] = ()
    attestations: tuple[ValidationAttestation, ...] = ()
    last_claude_fingerprint: str | None = None
    latest_claude_review: ContractResult | None = None
    latest_antigravity_review: ContractResult | None = None

    def __post_init__(self) -> None:
        if self.work_unit_id < 1:
            raise ValueError("workflow history work_unit_id must be 1-based")
        if self.last_claude_fingerprint is not None and not SHA256_PATTERN.fullmatch(
            self.last_claude_fingerprint
        ):
            raise ValueError("last Claude fingerprint must be a SHA-256 digest")
        finding_ids = tuple(item.finding_id for item in self.findings)
        if len(set(finding_ids)) != len(finding_ids):
            raise ValueError("workflow history finding ids must be unique")
        event_ids = tuple(event.event_id for event in self.events)
        if event_ids != tuple(range(1, len(event_ids) + 1)):
            raise ValueError("workflow history event ids must be contiguous and 1-based")


@dataclass(frozen=True)
class WorkflowRunResult:
    state: WorkflowState
    history: WorkflowHistory
    commit_ref: str | None = None

    @property
    def completed(self) -> bool:
        return self.state.current_work_unit.status is WorkUnitStatus.COMPLETED

    @property
    def exit_code(self) -> int:
        gate = self.state.current_work_unit.gate
        if gate.reason is GateReason.QUOTA:
            return 2
        if gate.reason is GateReason.INSTANCE_FAILURE:
            return 3
        return (
            4
            if self.state.current_work_unit.status
            is WorkUnitStatus.AWAITING_USER_DECISION
            else 0
            if self.completed
            else 1
        )


class WorkflowEngine:
    """Additive state-v3 engine for the asymmetric Codex/Claude/Antigravity chain."""

    def __init__(
        self,
        driver: WorkflowDriver,
        *,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep_fn: Callable[[float], None] = time.sleep,
        heartbeat_fn: Callable[[str], None] = logger.info,
    ) -> None:
        self.driver = driver
        self.now_fn = now_fn
        self.sleep_fn = sleep_fn
        self.heartbeat_fn = heartbeat_fn

    def run_current_work_unit(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory | None = None,
    ) -> WorkflowRunResult:
        current = state.current_work_unit
        active_history = history or WorkflowHistory(current.work_unit_id)
        if active_history.work_unit_id != current.work_unit_id:
            raise WorkflowExecutionError("workflow history belongs to a different work unit")
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            return WorkflowRunResult(state, active_history)

        state, policy_halted = self._apply_pre_agent_policy_gates(state, context)
        if policy_halted:
            self.driver.checkpoint(state, active_history)
            return WorkflowRunResult(state, active_history)
        latest_failure = (
            state.current_work_unit.invocation_failures[-1]
            if state.current_work_unit.invocation_failures
            else None
        )
        if (
            latest_failure is not None
            and latest_failure.step is state.current_step
            and state.current_work_unit.gate.status is GateStatus.CLEAR
        ):
            state, resume_halted = self._revalidate_waiting_diff(
                state, latest_failure
            )
            if resume_halted:
                self.driver.checkpoint(state, active_history)
                return WorkflowRunResult(state, active_history)

        state, active_history, anchor_halted = self._apply_anchor_gate(
            state, context, active_history
        )
        if anchor_halted:
            return WorkflowRunResult(state, active_history)

        for _ in range(100):
            step = state.current_step
            if step in (
                WorkflowStep.CODEX_PLAN,
                WorkflowStep.CODEX_PLAN_REVISION,
                WorkflowStep.CODEX_IMPLEMENTATION,
                WorkflowStep.CODEX_CORRECTION,
            ):
                state, active_history = self._run_codex(
                    state, context, active_history
                )
                if state.current_work_unit.status is not WorkUnitStatus.IN_PROGRESS:
                    return WorkflowRunResult(state, active_history)
                continue
            if step in (
                WorkflowStep.CLAUDE_PLAN_REVIEW,
                WorkflowStep.CLAUDE_SLICE_REVIEW,
            ):
                state, active_history = self._run_review(
                    state, context, active_history, AgentRole.CLAUDE
                )
                if state.current_work_unit.status is not WorkUnitStatus.IN_PROGRESS:
                    return WorkflowRunResult(state, active_history)
                continue
            if step is WorkflowStep.ANTIGRAVITY_SLICE_REVIEW:
                state, active_history = self._run_review(
                    state, context, active_history, AgentRole.ANTIGRAVITY
                )
                if state.current_work_unit.status is not WorkUnitStatus.IN_PROGRESS:
                    return WorkflowRunResult(state, active_history)
                continue
            if step is WorkflowStep.SLICE_COMMIT:
                return self._commit(state, active_history, context)
            if step is WorkflowStep.COMPLETED:
                return WorkflowRunResult(state, active_history)
            raise WorkflowExecutionError(
                f"step {step.value} is outside the Slice-10 development engine"
            )
        raise WorkflowExecutionError("workflow exceeded its deterministic transition bound")

    def decide_current_gate(
        self,
        state: WorkflowState,
        history: WorkflowHistory,
        *,
        approved: bool,
        decided_by: str,
        decided_at: str,
        rationale: str,
    ) -> WorkflowRunResult:
        """Record an explicit decision against the exact persisted gate evidence."""
        if history.work_unit_id != state.current_work_unit_id:
            raise WorkflowExecutionError(
                "workflow history belongs to a different gated work unit"
            )
        gate = state.current_work_unit.gate
        if gate.fingerprint is None:
            raise WorkflowExecutionError(
                "current gate is not a fingerprint-bound Slice-11 user gate"
            )
        try:
            updated = state.record_user_gate_decision(
                approved=approved,
                fingerprint=gate.fingerprint,
                paths=gate.paths,
                decided_by=decided_by,
                decided_at=decided_at,
                rationale=rationale,
            )
        except ValueError as exc:
            raise WorkflowExecutionError(f"invalid user gate decision: {exc}") from exc
        self.driver.checkpoint(updated, history)
        return WorkflowRunResult(updated, history)

    def _run_codex(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> tuple[WorkflowState, WorkflowHistory]:
        unit = state.current_work_unit
        is_plan = state.current_step in (
            WorkflowStep.CODEX_PLAN,
            WorkflowStep.CODEX_PLAN_REVISION,
        )
        readiness = ReadinessMarker.PLAN if is_plan else ReadinessMarker.IMPLEMENTATION
        contract = CodexStepContract(
            name=f"work-unit-{unit.work_unit_id}-{state.current_step.value}",
            readiness_marker=readiness,
            slice_id=f"{unit.slice_id:02d}",
            round_number=unit.round_number,
            require_test_files_record=not is_plan,
            expected_test_files=context.expected_test_files if not is_plan else (),
            # R-10 gates after implementation readiness and before review. Codex must be
            # able to report test paths before a user approval exists.
            test_changes_approved=True,
        )
        prompt = build_v3_codex_prompt(
            assignment=context.assignment,
            distilled_context=context.distilled_context,
            findings=history.findings,
            contract=contract,
        )
        invocation = CodexInvocation(state.current_step, unit.round_number, prompt)
        state, output = self._invoke_role(
            state,
            history,
            context,
            AgentRole.CODEX,
            lambda: self.driver.invoke_codex(invocation),
        )
        if output is None:
            return state, history
        try:
            result = validate_codex_response(output, contract, history.findings)
        except ContractValidationError as exc:
            raise WorkflowContractError(f"invalid Codex response: {exc}") from exc
        if result.stopped:
            if result.stop_request is None:
                raise WorkflowExecutionError("Codex stop has no structured stop request")
            state = self._halt_for_stop_request(state, context, result.stop_request)
            self.driver.checkpoint(state, history)
            return state, history
        if result.ready is not True:
            raise WorkflowExecutionError("Codex did not declare the current step ready")
        history = replace(history, findings=result.findings)
        next_step = (
            WorkflowStep.CLAUDE_PLAN_REVIEW
            if is_plan
            else WorkflowStep.CLAUDE_SLICE_REVIEW
        )
        state = state.with_current_step(next_step)
        self.driver.checkpoint(state, history)
        return state, history

    def _run_review(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
        reviewer: AgentRole,
    ) -> tuple[WorkflowState, WorkflowHistory]:
        unit = state.current_work_unit
        is_plan_review = state.current_step is WorkflowStep.CLAUDE_PLAN_REVIEW
        if reviewer is AgentRole.ANTIGRAVITY and (
            history.latest_claude_review is None
            or history.latest_claude_review.approval is not True
        ):
            raise WorkflowExecutionError(
                "Antigravity cannot run before an approving Claude review"
            )
        start_commit = state.current_slice.start_commit or state.branch_base
        changes = self.driver.collect_changes(start_commit)
        unexpected = self._validate_change_boundary(state, changes, unit.kind)
        if unexpected:
            state = state.await_policy_gate(
                reason=GateReason.UNEXPECTED_FILE,
                detail=(
                    f"{UNEXPECTED_PATH_RULE_ID} | canonical changes contain paths "
                    f"outside the persisted Slice scope: {', '.join(unexpected)}"
                ),
                paths=unexpected,
            )
            self.driver.checkpoint(state, history)
            return state, history
        test_changes_approved = context.test_changes_approved
        if not test_changes_approved:
            test_evidence = self.driver.detect_test_changes(
                changes, context.test_path_patterns
            )
            if test_evidence is not None:
                test_changes_approved = test_changes_approved or unit.has_gate_approval(
                    GateReason.TEST_CHANGE,
                    test_evidence.fingerprint,
                    test_evidence.paths,
                )
                if not test_changes_approved:
                    state = state.await_user_gate(
                        reason=GateReason.TEST_CHANGE,
                        detail="test changes require explicit approval before review",
                        fingerprint=test_evidence.fingerprint,
                        paths=test_evidence.paths,
                    )
                    self.driver.checkpoint(state, history)
                    return state, history
                state = state.record_active_test_approval(
                    test_evidence.fingerprint, test_evidence.paths
                )
            else:
                state = state.record_active_test_approval(None)
        else:
            state = state.record_active_test_approval(None)
        if reviewer is AgentRole.ANTIGRAVITY:
            claude_validation = history.latest_claude_review.validation
            if (
                claude_validation is None
                or claude_validation.diff_fingerprint != changes.fingerprint
            ):
                raise WorkflowExecutionError(
                    "Antigravity requires Claude approval for the current fingerprint"
                )
        try:
            attestation, history = self._attestation(
                changes, history, context, unit.slice_id
            )
        except ValidationExecutionError as exc:
            state = state.await_policy_gate(
                reason=GateReason.STOP_REQUEST,
                detail=f"{VALIDATION_UNAVAILABLE_RULE_ID} | {exc}",
            )
            self.driver.checkpoint(state, history)
            return state, history
        if not attestation.complete:
            self.driver.checkpoint(state, history)
            raise WorkflowExecutionError(
                "validation attestation is incomplete and cannot be overridden"
            )
        if not attestation.passed and context.red_state_followup_slice is None:
            self.driver.checkpoint(state, history)
            raise WorkflowExecutionError(
                "validation attestation is failing without a named red-state follow-up slice"
            )
        review_round = (
            1
            + sum(
                isinstance(event, ReviewAuditEvent)
                and event.result.reviewer is reviewer
                for event in history.events
            )
        )
        contract = StepContract(
            name=f"work-unit-{unit.work_unit_id}-{state.current_step.value}",
            reviewer=reviewer,
            approval_marker=(
                ApprovalMarker.PLAN
                if is_plan_review
                else ApprovalMarker.SLICE
            ),
            slice_id=f"{unit.slice_id:02d}",
            round_number=review_round,
            review_fingerprint=changes.fingerprint,
            validation_attestation=attestation,
            expected_test_files=(
                context.expected_test_files if not is_plan_review else ()
            ),
            test_changes_approved=test_changes_approved,
            red_state_followup_slice=context.red_state_followup_slice,
        )
        if reviewer is AgentRole.CLAUDE and history.last_claude_fingerprint is not None:
            evidence_kind = EvidenceKind.CORRECTION_DELTA
            review_diff = self.driver.collect_correction_delta(
                history.last_claude_fingerprint, changes.fingerprint
            )
        else:
            evidence_kind = EvidenceKind.FULL_SLICE
            review_diff = changes.full_diff
        evidence = self._review_evidence(
            context=context,
            history=history,
            changes=changes,
            evidence_kind=evidence_kind,
            review_diff=review_diff,
        )
        prompt = build_v3_review_prompt(
            assignment=context.assignment,
            evidence=evidence,
            contract=contract,
        )
        invocation = ReviewerInvocation(
            reviewer=reviewer,
            round_number=review_round,
            evidence_kind=evidence_kind,
            fingerprint=changes.fingerprint,
            paths=changes.paths,
            prompt=prompt,
        )
        state, output = self._invoke_role(
            state,
            history,
            context,
            reviewer,
            lambda: self.driver.invoke_reviewer(invocation),
        )
        if output is None:
            return state, history
        result = self._validate_or_repair_review(
            output=output,
            contract=contract,
            findings=history.findings,
        )
        if result.stopped:
            if result.stop_request is None:
                raise WorkflowExecutionError(
                    f"{reviewer.value} stop has no structured stop request"
                )
            state = self._halt_for_stop_request(state, context, result.stop_request)
            self.driver.checkpoint(state, history)
            return state, history
        history = self._record_review(
            history,
            unit.slice_id,
            review_round,
            result,
            changes.fingerprint,
            track_slice_approval=not is_plan_review,
        )

        if result.approval is True:
            if reviewer is AgentRole.CLAUDE:
                if is_plan_review and unit.kind is WorkUnitKind.PLAN:
                    state = state.complete_current_work_unit()
                elif is_plan_review:
                    decision = self._latest_anchor_approval(state)
                    if decision is None or decision.resume_step is None:
                        raise WorkflowExecutionError(
                            "anchor plan review has no persisted resume target"
                        )
                    key = f"anchor-plan-reviewed:{decision.fingerprint}"
                    state = state.mark_side_effect_completed(key)
                    state = state.with_current_step(decision.resume_step)
                else:
                    state = state.with_current_step(
                        WorkflowStep.ANTIGRAVITY_SLICE_REVIEW
                    )
            else:
                state = state.with_current_step(WorkflowStep.SLICE_COMMIT)
        else:
            own_ids = tuple(item.finding_id for item in result.own_open_blockers)
            return_step = (
                WorkflowStep.CODEX_PLAN_REVISION
                if unit.kind is WorkUnitKind.PLAN
                else WorkflowStep.CODEX_CORRECTION
            )
            state = state.record_review_denial(
                reviewer=(
                    Reviewer.CLAUDE
                    if reviewer is AgentRole.CLAUDE
                    else Reviewer.ANTIGRAVITY
                ),
                open_findings=own_ids,
                return_step=return_step,
            )
        self.driver.checkpoint(state, history)
        return state, history

    def _invoke_role(
        self,
        state: WorkflowState,
        history: WorkflowHistory,
        context: WorkflowContext,
        role: AgentRole,
        invoke: Callable[[], str],
    ) -> tuple[WorkflowState, str | None]:
        """Invoke one fixed role, persisting every failure before any optional wait."""
        while True:
            try:
                return state, invoke()
            except AgentInvocationError as error:
                state, failure = self._persist_invocation_failure(
                    state, history, context, role, error
                )
                if not failure.automatic_resume:
                    return state, None
                assert failure.resume_at_utc is not None
                wait_until_quota_resume(
                    role=role.value,
                    task_label=state.task_file,
                    work_unit_id=state.current_work_unit_id,
                    resume_at_utc=datetime.fromisoformat(
                        failure.resume_at_utc.replace("Z", "+00:00")
                    ),
                    heartbeat_interval_seconds=(
                        context.quota_wait_policy.heartbeat_interval_seconds
                    ),
                    now_fn=self.now_fn,
                    sleep_fn=self.sleep_fn,
                    heartbeat_fn=self.heartbeat_fn,
                )
                state = state.resume_after_invocation_halt()
                state, halted = self._apply_pre_agent_policy_gates(state, context)
                if not halted:
                    state, halted = self._revalidate_waiting_diff(
                        state, failure
                    )
                self.driver.checkpoint(state, history)
                if halted:
                    return state, None

    def _persist_invocation_failure(
        self,
        state: WorkflowState,
        history: WorkflowHistory,
        context: WorkflowContext,
        role: AgentRole,
        error: AgentInvocationError,
    ) -> tuple[WorkflowState, InvocationFailureRecord]:
        unit = state.current_work_unit
        if error.agent_key != role.value:
            raise WorkflowExecutionError(
                "agent failure role differs from the required workflow role"
            )
        fingerprint = self._current_invocation_fingerprint(state)
        key = (
            f"{state.run_id}:{unit.work_unit_id}:{state.current_step.value}:"
            f"{role.value}"
        )
        prior_auto_resumes = sum(
            item.idempotency_key == key
            and item.failure_kind is AgentFailureKind.QUOTA
            and item.automatic_resume
            for item in unit.invocation_failures
        )
        policy = context.quota_wait_policy
        reset_at = error.quota_reset.reset_at_utc if error.quota_reset else None
        resume_at = (
            reset_at + timedelta(seconds=policy.safety_margin_seconds)
            if reset_at is not None
            else None
        )
        now_value = self.now_fn()
        if now_value.tzinfo is None or now_value.utcoffset() is None:
            raise WorkflowExecutionError("quota clock must return a timezone-aware datetime")
        now_utc = now_value.astimezone(timezone.utc)
        wait_seconds = (
            max(0.0, (resume_at - now_utc).total_seconds())
            if resume_at is not None
            else None
        )
        automatic = (
            error.kind is AgentFailureKind.QUOTA
            and policy.automatic
            and reset_at is not None
            and (
                unit.kind is WorkUnitKind.PLAN
                or fingerprint is not None
            )
            and wait_seconds is not None
            and wait_seconds <= policy.maximum_wait_seconds
            and prior_auto_resumes < policy.maximum_auto_resumes
        )
        record = InvocationFailureRecord(
            invocation_id=error.invocation_id,
            idempotency_key=key,
            role=role.value,
            failure_kind=error.kind,
            provider_text=error.provider_text,
            received_at=error.received_at.isoformat(),
            step=state.current_step,
            slice_id=state.current_slice_id,
            work_unit_id=state.current_work_unit_id,
            diagnostic_exit_code=(
                2 if error.kind is AgentFailureKind.QUOTA else 3
            ),
            parse_path=(
                error.quota_reset.parse_path if error.quota_reset else None
            ),
            source_timezone=(
                error.quota_reset.source_timezone if error.quota_reset else None
            ),
            reset_at_utc=reset_at.isoformat() if reset_at is not None else None,
            resume_at_utc=resume_at.isoformat() if resume_at is not None else None,
            safety_margin_seconds=policy.safety_margin_seconds,
            auto_resume_count=prior_auto_resumes + (1 if automatic else 0),
            automatic_resume=automatic,
            diff_fingerprint=fingerprint,
        )
        state = state.record_invocation_failure(
            record, wait_automatically=automatic
        )
        self.driver.checkpoint(state, history)
        return state, record

    def _current_invocation_fingerprint(
        self, state: WorkflowState
    ) -> str | None:
        if state.current_work_unit.kind is WorkUnitKind.PLAN:
            return None
        start_commit = state.current_slice.start_commit
        if start_commit is None:
            return None
        try:
            return self.driver.collect_changes(start_commit).fingerprint
        except Exception:
            return None

    def _revalidate_waiting_diff(
        self,
        state: WorkflowState,
        failure: InvocationFailureRecord,
    ) -> tuple[WorkflowState, bool]:
        if failure.diff_fingerprint is None:
            if state.current_work_unit.kind is WorkUnitKind.PLAN:
                return state, False
            halted = state.await_policy_gate(
                reason=GateReason.STOP_REQUEST,
                detail=(
                    "QUOTA-RESUME-DIFF | no persisted Slice fingerprint is "
                    "available for safe resume"
                ),
            )
            return halted, True
        start_commit = state.current_slice.start_commit
        if start_commit is None:
            raise WorkflowExecutionError(
                "quota resume requires the persisted Slice start commit"
            )
        try:
            changes = self.driver.collect_changes(start_commit)
        except Exception as exc:
            halted = state.await_policy_gate(
                reason=GateReason.STOP_REQUEST,
                detail=(
                    "QUOTA-RESUME-DIFF | could not revalidate repository changes: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
            return halted, True
        unexpected = self._validate_change_boundary(
            state, changes, state.current_work_unit.kind
        )
        if unexpected or changes.fingerprint != failure.diff_fingerprint:
            paths = unexpected or changes.user_gate_paths
            halted = state.await_policy_gate(
                reason=GateReason.STOP_REQUEST,
                detail=(
                    "QUOTA-RESUME-DIFF | repository changed while the role was waiting; "
                    f"expected {failure.diff_fingerprint}, got {changes.fingerprint}"
                ),
                paths=paths,
            )
            return halted, True
        return state, False

    def _validate_or_repair_review(
        self,
        *,
        output: str,
        contract: StepContract,
        findings: tuple[FindingRecord, ...],
    ) -> ContractResult:
        try:
            return validate_review_response(output, contract, findings)
        except ContractValidationError as first_error:
            repaired = self.driver.repair_review_contract(
                ContractRepairInvocation(
                    reviewer=contract.reviewer,
                    rejected_output=output,
                    validation_error=str(first_error),
                    contract=build_v3_review_contract(contract),
                )
            )
            try:
                return validate_review_response(repaired, contract, findings)
            except ContractValidationError as second_error:
                raise WorkflowContractError(
                    f"invalid {contract.reviewer.value} verdict after compact repair: "
                    f"{second_error}"
                ) from second_error

    def _attestation(
        self,
        changes: WorkflowChanges,
        history: WorkflowHistory,
        context: WorkflowContext,
        slice_id: int,
    ) -> tuple[ValidationAttestation, WorkflowHistory]:
        try:
            request = select_validation_request(
                context.validation_matrix,
                diff_fingerprint=changes.fingerprint,
                changed_paths=changes.user_gate_paths,
                findings=history.findings,
            )
        except ValidationMatrixError as exc:
            raise ValidationExecutionError(str(exc)) from exc
        matching = tuple(
            existing
            for existing in history.attestations
            if existing.diff_fingerprint == changes.fingerprint
        )
        if matching:
            existing = matching[-1]
            if existing.complete or not context.retry_incomplete_validation:
                if not set(request.expected_commands).issubset(
                    existing.expected_commands
                ):
                    raise WorkflowExecutionError(
                        "validation requirements changed without a new diff fingerprint"
                    )
                return existing, history
            request = replace(request, attempt_number=len(matching) + 1)
        attestation = self.driver.validate(changes, request)
        if attestation.diff_fingerprint != changes.fingerprint:
            raise WorkflowExecutionError("validation attestation fingerprint is foreign")
        if attestation.expected_commands != request.expected_commands:
            raise WorkflowExecutionError(
                "validation attestation does not cover the selected matrix"
            )
        if any(
            item.attestation_id == attestation.attestation_id
            for item in history.attestations
        ):
            raise WorkflowExecutionError("validation attestation id was reused")
        event = ValidationAuditEvent(
            event_id=len(history.events) + 1,
            slice_id=slice_id,
            attestation=attestation,
        )
        return attestation, replace(
            history,
            attestations=(*history.attestations, attestation),
            events=(*history.events, event),
        )

    def _record_review(
        self,
        history: WorkflowHistory,
        slice_id: int,
        round_number: int,
        result: ContractResult,
        fingerprint: str,
        *,
        track_slice_approval: bool = True,
    ) -> WorkflowHistory:
        event = ReviewAuditEvent(
            event_id=len(history.events) + 1,
            slice_id=slice_id,
            round_number=round_number,
            result=result,
        )
        updates: dict[str, object] = {
            "findings": result.findings,
            "events": (*history.events, event),
        }
        if result.reviewer is AgentRole.CLAUDE:
            if track_slice_approval:
                updates["last_claude_fingerprint"] = fingerprint
                updates["latest_claude_review"] = result
        else:
            updates["latest_antigravity_review"] = result
        return replace(history, **updates)

    def _commit(
        self,
        state: WorkflowState,
        history: WorkflowHistory,
        context: WorkflowContext,
    ) -> WorkflowRunResult:
        start_commit = state.current_slice.start_commit
        if start_commit is None:
            raise WorkflowExecutionError("slice commit requires a persisted start commit")
        changes = self.driver.collect_changes(start_commit)
        unexpected = self._validate_change_boundary(
            state, changes, state.current_work_unit.kind
        )
        if unexpected:
            state = state.await_policy_gate(
                reason=GateReason.UNEXPECTED_FILE,
                detail=(
                    f"{UNEXPECTED_PATH_RULE_ID} | canonical changes contain paths "
                    f"outside the persisted Slice scope: {', '.join(unexpected)}"
                ),
                paths=unexpected,
            )
            self.driver.checkpoint(state, history)
            return WorkflowRunResult(state, history)
        attestation = next(
            (
                item
                for item in reversed(history.attestations)
                if item.diff_fingerprint == changes.fingerprint
            ),
            None,
        )
        claude = history.latest_claude_review
        antigravity = history.latest_antigravity_review
        validation_authorized = attestation is not None and (
            attestation.passed
            or (
                attestation.complete
                and context.red_state_followup_slice is not None
            )
        )
        if not validation_authorized:
            raise WorkflowExecutionError(
                "slice commit requires a passing attestation or named complete red-state exception"
            )
        if claude is None or claude.approval is not True or claude.validation != attestation:
            raise WorkflowExecutionError("slice commit requires current Claude approval")
        if (
            antigravity is None
            or antigravity.approval is not True
            or antigravity.validation != attestation
        ):
            raise WorkflowExecutionError("slice commit requires current Antigravity approval")
        if any(
            finding.status is FindingStatus.OPEN
            and finding.finding_class is FindingClass.BLOCKER
            for finding in history.findings
        ):
            raise WorkflowExecutionError("slice commit requires no open blockers")
        gate_paths = changes.user_gate_paths
        if context.manual_slice_gate and not state.current_work_unit.has_gate_approval(
            GateReason.MANUAL_SLICE, changes.fingerprint, gate_paths
        ):
            state = state.await_user_gate(
                reason=GateReason.MANUAL_SLICE,
                detail="manual slice approval is required before commit",
                fingerprint=changes.fingerprint,
                paths=gate_paths,
            )
            self.driver.checkpoint(state, history)
            return WorkflowRunResult(state, history)
        commit_ref = self.driver.commit_slice(
            WorkflowCommitRequest(
                slice_id=state.current_slice_id,
                fingerprint=changes.fingerprint,
                attestation=attestation,
                claude_review=claude,
                antigravity_review=antigravity,
                findings=history.findings,
                red_state_followup_slice=context.red_state_followup_slice,
            )
        )
        if not isinstance(commit_ref, str) or not commit_ref.strip():
            raise WorkflowExecutionError("slice commit did not return a commit reference")
        state = state.complete_current_slice(commit_ref=commit_ref)
        self.driver.checkpoint(state, history)
        return WorkflowRunResult(state, history, commit_ref)

    def _apply_anchor_gate(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> tuple[WorkflowState, WorkflowHistory, bool]:
        if state.current_work_unit.kind is WorkUnitKind.PLAN:
            return state, history, False
        evidence = detect_anchor_changes(
            context.approved_anchors, context.current_anchors
        )
        if evidence is None:
            return state, history, False
        paths = self._anchor_targets(evidence)
        unit = state.current_work_unit
        if unit.has_gate_approval(GateReason.ANCHOR_CHANGE, evidence.fingerprint, paths):
            reset_key = f"anchor-plan-reset:{evidence.fingerprint}"
            if unit.has_completed_side_effect(reset_key):
                return state, history, False
            state = state.mark_side_effect_completed(reset_key)
            state = state.with_current_step(WorkflowStep.CODEX_PLAN_REVISION)
            history = replace(
                history,
                last_claude_fingerprint=None,
                latest_claude_review=None,
                latest_antigravity_review=None,
            )
            self.driver.checkpoint(state, history)
            return state, history, False
        original_step = state.current_step
        resume_step = (
            WorkflowStep.CODEX_CORRECTION
            if original_step is WorkflowStep.CODEX_CORRECTION
            else WorkflowStep.CODEX_IMPLEMENTATION
        )
        state = state.await_user_gate(
            reason=GateReason.ANCHOR_CHANGE,
            detail="approved plan anchors changed and require plan review reset",
            fingerprint=evidence.fingerprint,
            paths=paths,
            resume_step=resume_step,
        )
        self.driver.checkpoint(state, history)
        return state, history, True

    def _apply_pre_agent_policy_gates(
        self,
        state: WorkflowState,
        context: WorkflowContext,
    ) -> tuple[WorkflowState, bool]:
        current_branch = context.current_branch or state.branch
        if current_branch != state.branch:
            return (
                state.await_policy_gate(
                    reason=GateReason.STOP_REQUEST,
                    detail=(
                        f"{BRANCH_MISMATCH_RULE_ID} | active branch "
                        f"{current_branch!r} differs from persisted branch {state.branch!r}"
                    ),
                ),
                True,
            )
        scope_paths = state.current_slice.scope_paths
        if state.current_work_unit.kind is WorkUnitKind.PLAN or not scope_paths:
            return state, False
        evidence = evaluate_productive_file_limit(
            state.current_slice.scope_change_groups,
            context.path_classes,
            maximum=context.max_productive_files,
        )
        if evidence is None:
            return state, False
        return (
            state.await_policy_gate(
                reason=GateReason.STOP_REQUEST,
                detail=evidence.detail,
                paths=evidence.paths,
            ),
            True,
        )

    @staticmethod
    def _halt_for_stop_request(
        state: WorkflowState,
        context: WorkflowContext,
        stop_request: StopRequest,
    ) -> WorkflowState:
        if stop_request.rule_id not in context.known_stop_rule_ids:
            raise WorkflowExecutionError(
                f"STOP_REQUESTED references unknown rule {stop_request.rule_id!r}"
            )
        return state.await_policy_gate(
            reason=GateReason.STOP_REQUEST,
            detail=f"{stop_request.rule_id} | {stop_request.rationale}",
        )

    @staticmethod
    def _latest_anchor_approval(state: WorkflowState) -> GateDecisionRecord | None:
        return next(
            (
                decision
                for decision in reversed(state.current_work_unit.gate_decisions)
                if decision.approved
                and decision.reason is GateReason.ANCHOR_CHANGE
                and state.current_work_unit.has_completed_side_effect(
                    f"anchor-plan-reset:{decision.fingerprint}"
                )
                and not state.current_work_unit.has_completed_side_effect(
                    f"anchor-plan-reviewed:{decision.fingerprint}"
                )
            ),
            None,
        )

    @staticmethod
    def _anchor_targets(evidence: AnchorChangeEvidence) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    *evidence.changes.added,
                    *evidence.changes.removed,
                    *evidence.changes.changed,
                }
            )
        )

    @staticmethod
    def _validate_change_boundary(
        state: WorkflowState,
        changes: WorkflowChanges,
        kind: WorkUnitKind,
    ) -> tuple[str, ...]:
        expected_start = state.current_slice.start_commit or state.branch_base
        if changes.start_commit != expected_start:
            raise WorkflowExecutionError("change evidence uses a foreign slice start commit")
        if kind is WorkUnitKind.PLAN:
            return ()
        scope = state.current_slice.scope_paths
        if not scope:
            raise WorkflowExecutionError("slice review requires a persisted Git boundary")
        return tuple(path for path in changes.paths if path not in scope)

    @staticmethod
    def _review_evidence(
        *,
        context: WorkflowContext,
        history: WorkflowHistory,
        changes: WorkflowChanges,
        evidence_kind: EvidenceKind,
        review_diff: str,
    ) -> str:
        findings = "\n".join(
            WorkflowEngine._render_finding(item) for item in history.findings
        ) or "NONE"
        return (
            f"DISTILLED CONTEXT\n{context.distilled_context}\n\n"
            f"EVIDENCE KIND\n{evidence_kind.value}\n\n"
            f"SLICE START COMMIT\n{changes.start_commit}\n\n"
            f"CURRENT FINGERPRINT\n{changes.fingerprint}\n\n"
            f"STRUCTURED FINDINGS\n{findings}\n\n"
            f"REVIEW DIFF\n{review_diff}"
        )

    @staticmethod
    def _render_finding(finding: FindingRecord) -> str:
        responses = "; ".join(
            f"{response.decision.value}: {response.rationale}"
            for response in finding.responses
        ) or "NONE"
        return (
            f"{finding.finding_id} | {finding.finding_class.value} | "
            f"{finding.status.value} | reporter={finding.origin.reporter.value} | "
            f"slice={finding.origin.slice_id} | round={finding.origin.round_number} | "
            f"summary={finding.summary} | acceptance={finding.acceptance_test} | "
            f"responses={responses} | closure={finding.status_rationale or 'NONE'}"
        )


def authorized_test_changes_from_state(
    state: WorkflowState,
) -> AuthorizedTestChanges | None:
    """Project the exact test approval recorded as active during the final review."""
    unit = state.current_work_unit
    if unit.active_test_fingerprint is None:
        return None
    decision = next(
        (
            item
            for item in reversed(unit.gate_decisions)
            if item.approved
            and item.reason is GateReason.TEST_CHANGE
            and item.fingerprint == unit.active_test_fingerprint
            and item.paths == unit.active_test_paths
        ),
        None,
    )
    if decision is None:
        return None
    return AuthorizedTestChanges(
        approved=True,
        paths=decision.paths,
        approved_by=decision.decided_by,
        rationale=decision.rationale,
        approved_at=decision.decided_at,
        diff_fingerprint=decision.fingerprint,
    )
