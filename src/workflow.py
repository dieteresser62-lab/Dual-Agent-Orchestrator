from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

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
    ValidationAttestation,
    validate_codex_response,
    validate_review_response,
)
from gates import AnchorChangeEvidence, TestChangeEvidence, detect_anchor_changes
from prompts import (
    build_v3_codex_prompt,
    build_v3_review_contract,
    build_v3_review_prompt,
)
from workflow_state import (
    GateDecisionRecord,
    GateReason,
    Reviewer,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class WorkflowExecutionError(RuntimeError):
    """Raised when the development workflow must stop without advancing a step."""


class WorkflowContractError(WorkflowExecutionError):
    """Raised after a reviewer response and its compact format repair both fail."""


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

    @property
    def distilled_context(self) -> str:
        approved = self._render_anchors(self.approved_anchors)
        current = self._render_anchors(self.current_anchors)
        return (
            f"PLAN\n{self.distilled_plan}\n\n"
            f"CURRENT SLICE\n{self.slice_summary}\n\n"
            f"APPROVED PLAN ANCHORS\n{approved}\n\n"
            f"CURRENT ANCHORS\n{current}"
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


class WorkflowDriver(Protocol):
    def invoke_codex(self, invocation: CodexInvocation) -> str: ...

    def collect_changes(self, start_commit: str) -> WorkflowChanges: ...

    def collect_correction_delta(
        self, previous_fingerprint: str, current_fingerprint: str
    ) -> str: ...

    def detect_test_changes(
        self, changes: WorkflowChanges, patterns: tuple[str, ...]
    ) -> TestChangeEvidence | None: ...

    def validate(self, changes: WorkflowChanges) -> ValidationAttestation: ...

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

    def __init__(self, driver: WorkflowDriver) -> None:
        self.driver = driver

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
        output = self.driver.invoke_codex(
            CodexInvocation(state.current_step, unit.round_number, prompt)
        )
        try:
            result = validate_codex_response(output, contract, history.findings)
        except ContractValidationError as exc:
            raise WorkflowContractError(f"invalid Codex response: {exc}") from exc
        if result.stopped or result.ready is not True:
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
        self._validate_change_boundary(state, changes, unit.kind)
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
        attestation, history = self._attestation(changes, history, unit.slice_id)
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
        output = self.driver.invoke_reviewer(invocation)
        result = self._validate_or_repair_review(
            output=output,
            contract=contract,
            findings=history.findings,
        )
        if result.stopped:
            raise WorkflowExecutionError(f"{reviewer.value} requested a workflow stop")
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
        slice_id: int,
    ) -> tuple[ValidationAttestation, WorkflowHistory]:
        for existing in history.attestations:
            if existing.diff_fingerprint == changes.fingerprint:
                if not existing.passed:
                    raise WorkflowExecutionError("cached validation attestation is not passing")
                return existing, history
        attestation = self.driver.validate(changes)
        if attestation.diff_fingerprint != changes.fingerprint:
            raise WorkflowExecutionError("validation attestation fingerprint is foreign")
        if not attestation.complete or not attestation.passed:
            raise WorkflowExecutionError("validation attestation is incomplete or failing")
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
        self._validate_change_boundary(state, changes, state.current_work_unit.kind)
        attestation = next(
            (
                item
                for item in history.attestations
                if item.diff_fingerprint == changes.fingerprint
            ),
            None,
        )
        claude = history.latest_claude_review
        antigravity = history.latest_antigravity_review
        if attestation is None or not attestation.passed:
            raise WorkflowExecutionError("slice commit requires the current passing attestation")
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
    ) -> None:
        expected_start = state.current_slice.start_commit or state.branch_base
        if changes.start_commit != expected_start:
            raise WorkflowExecutionError("change evidence uses a foreign slice start commit")
        if kind is WorkUnitKind.PLAN:
            return
        scope = state.current_slice.scope_paths
        if not scope:
            raise WorkflowExecutionError("slice review requires a persisted Git boundary")
        unexpected = tuple(path for path in changes.paths if path not in scope)
        if unexpected:
            raise WorkflowExecutionError(
                "change evidence contains paths outside the persisted slice scope: "
                + ", ".join(unexpected)
            )

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
