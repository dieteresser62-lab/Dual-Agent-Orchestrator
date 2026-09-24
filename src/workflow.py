from __future__ import annotations

import hashlib
import inspect
import logging
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from functools import partial
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Protocol

from agent_runtime import (
    AgentInvocationError,
    NativeAgentCodexOutput,
    NativeAgentReviewOutput,
    ProviderRequestRoundRequired,
    QuotaWaitPolicy,
    RecoveredFindingComparison,
    TransientRetryPolicy,
    wait_until_quota_resume,
    wait_until_transient_retry,
)
import workflow_requests
import workflow_failure_recording
import workflow_validation_evidence
import native_finding_decisions
from acceptance_criteria import validate_measurement_support
from provider_input_budget import ProviderInputBudgetExceeded
from final_review_preflight import FinalReviewPreflightDenied
from artifact_models import (
    InvocationFailurePayload,
    ScopeExtensionPathPayload,
    ScopeExtensionPayload,
)
from finding_order import sorted_finding_ids
from finding_convergence import SliceConvergenceEvaluation
from native_review_contract import (
    DISCOVERY_OUTPUT_LIMIT_RULE_ID,
    find_native_review_disposition_limit_error,
)
from orchestrator_diagnostics import OrchestratorDiagnostic
from finding_reducer import (
    merge_review_request_result,
    merge_request_result,
    project_finding_transition_ids,
    project_open_set,
    project_request_subset,
)

from audit_trail import (
    AuditEvent,
    ReviewAuditEvent,
    ValidationAuditEvent,
    allowed_review_finding_origins,
    managed_slice_document_path,
)
from contracts import (
    AgentRole,
    AnchorRecord,
    ApprovalMarker,
    CodexContractResult,
    CodexStepContract,
    ContractResult,
    FindingRecord,
    FindingClass,
    FindingOccurrence,
    FindingOrigin,
    FindingResponse,
    FindingResponseDecision,
    FindingStatus,
    PlannedSlice,
    ReadinessMarker,
    StepContract,
    StopRequest,
    ReviewEvidence,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from gates import (
    BRANCH_MISMATCH_RULE_ID,
    BUILTIN_STOP_RULES,
    SCOPE_EXTENSION_REQUESTED_RULE_ID,
    UNEXPECTED_PATH_RULE_ID,
    VALIDATION_UNAVAILABLE_RULE_ID,
    AnchorChangeEvidence,
    PathClass,
    PathClassification,
    PathClasses,
    StopRule,
    TestChangeEvidence,
    classify_path,
    detect_anchor_changes,
    matches_path_patterns,
    validate_builtin_stop_content,
)
from review_packets import (
    BinaryFileMetadata, ReviewPacket, ReviewPacketError, build_review_packet,
    exclude_review_diff_paths,
)
from repo_changes import ReviewDiff
from task_contract import TaskMode
from validation_matrix import (
    ValidationCommand,
    ValidationMatrix,
    ValidationRequest,
)
from workflow_state import (
    AgentFailureKind,
    GateDecisionRecord,
    GateReason,
    GateStatus,
    InvocationFailureRecord,
    ProtocolMode,
    Reviewer,
    SliceStatus,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitRecord,
    WorkUnitStatus,
    NATIVE_CLAUDE_REVIEW_TRANSPORT,
    NATIVE_CODEX_RESULT_TRANSPORT,
    project_implementer_return_policy,
    quota_resume_diff_acknowledgement,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
logger = logging.getLogger(__name__)
DEFAULT_WORKFLOW_VALIDATION_MATRIX = ValidationMatrix(
    default_command=ValidationCommand(
        argv=("python3", "-m", "pytest", "tests/", "-v")
    )
)
AGENT_SANDBOX_VALIDATION_PATTERN = re.compile(
    r"(?:listen|bind(?:ing|en)?|testserver|test server|browserstart|browser start)"
    r".{0,200}(?:eperm|eacces|eaddrinuse|operation not permitted|permission denied)"
    r"|(?:eperm|eacces|eaddrinuse|operation not permitted|permission denied)"
    r".{0,200}(?:listen|bind(?:ing|en)?|testserver|test server|browserstart|browser start)",
    re.IGNORECASE | re.DOTALL,
)


class WorkflowExecutionError(RuntimeError):
    """Raised when the development workflow must stop without advancing a step."""

    def __init__(
        self,
        detail: str,
        *,
        orchestrator_diagnostic: OrchestratorDiagnostic | None = None,
    ) -> None:
        if orchestrator_diagnostic is not None and not isinstance(
            orchestrator_diagnostic, OrchestratorDiagnostic
        ):
            raise TypeError("orchestrator diagnostic must be a closed enum member")
        if orchestrator_diagnostic is None:
            rendered = f"workflow-execution: {detail}"
            try:
                orchestrator_diagnostic = OrchestratorDiagnostic(rendered)
            except ValueError:
                # Dynamic WorkflowExecutionError details can include provider or
                # repository values.  Keep only the repository-owned rule name.
                orchestrator_diagnostic = (
                    OrchestratorDiagnostic.WORKFLOW_EXECUTION_RULE
                )
        self.orchestrator_diagnostic = orchestrator_diagnostic
        super().__init__(detail)


class WorkflowCompletionRejected(WorkflowExecutionError):
    """Raised when authoritative completion policy deliberately rejects a run."""


class PlanContractFailureKind(str, Enum):
    """Closed inventory of failures before a PLAN_ONLY artifact can be reviewed."""

    PERSISTED_SLICE_PLAN_MISSING = "persisted_slice_plan_missing"
    PLANNED_PATH_OUTSIDE_SCOPE = "planned_path_outside_scope"
    CHANGED_PATH_OUTSIDE_SCOPE = "changed_path_outside_scope"
    PLAN_ARTIFACT_SLICE_COUNT_INVALID = "plan_artifact_slice_count_invalid"
    WORK_PLAN_PATH_NOT_PLANNED = "work_plan_path_not_planned"
    WORK_PLAN_PATH_NOT_CHANGED = "work_plan_path_not_changed"
    WORK_PLAN_PATH_UNSAFE_RESOLUTION = "work_plan_path_unsafe_resolution"
    WORK_PLAN_PATH_NOT_REGULAR = "work_plan_path_not_regular"
    WORK_PLAN_PATH_NOT_UTF8 = "work_plan_path_not_utf8"
    WORK_PLAN_SEMANTIC_MARKDOWN_INVALID = "work_plan_semantic_markdown_invalid"
    WORK_PLAN_PATH_EMPTY = "work_plan_path_empty"
    WORK_PLAN_HANDOFF_INVALID = "work_plan_handoff_invalid"


class PlanContractSecurityClass(str, Enum):
    INTERNAL_INVARIANT = "internal_invariant"
    SCOPE_BOUNDARY = "scope_boundary"
    PATH_BOUNDARY = "path_boundary"
    SAFE_REPOSITORY_ARTIFACT = "safe_repository_artifact"
    HANDOFF_STRUCTURE = "handoff_structure"


class PlanContractRepairability(str, Enum):
    FAIL_CLOSED = "fail_closed"
    REPAIR_ONCE = "repair_once"


class PlanContractRepairAction(str, Enum):
    CREATE_MISSING_FILE = "create_missing_file"
    UPDATE_EXISTING_FILE = "update_existing_file"


@dataclass(frozen=True)
class PlanContractFailurePolicy:
    security_class: PlanContractSecurityClass
    repairability: PlanContractRepairability
    repair_action: PlanContractRepairAction | None = None

    def __post_init__(self) -> None:
        repairable = self.repairability is PlanContractRepairability.REPAIR_ONCE
        if repairable != (self.repair_action is not None):
            raise ValueError(
                "plan-contract repair action must match its repairability class"
            )


PLAN_CONTRACT_FAILURE_POLICIES = MappingProxyType({
    PlanContractFailureKind.PERSISTED_SLICE_PLAN_MISSING: PlanContractFailurePolicy(
        PlanContractSecurityClass.INTERNAL_INVARIANT,
        PlanContractRepairability.FAIL_CLOSED,
    ),
    PlanContractFailureKind.PLANNED_PATH_OUTSIDE_SCOPE: PlanContractFailurePolicy(
        PlanContractSecurityClass.SCOPE_BOUNDARY,
        PlanContractRepairability.FAIL_CLOSED,
    ),
    PlanContractFailureKind.CHANGED_PATH_OUTSIDE_SCOPE: PlanContractFailurePolicy(
        PlanContractSecurityClass.SCOPE_BOUNDARY,
        PlanContractRepairability.FAIL_CLOSED,
    ),
    PlanContractFailureKind.PLAN_ARTIFACT_SLICE_COUNT_INVALID: PlanContractFailurePolicy(
        PlanContractSecurityClass.INTERNAL_INVARIANT,
        PlanContractRepairability.FAIL_CLOSED,
    ),
    PlanContractFailureKind.WORK_PLAN_PATH_NOT_PLANNED: PlanContractFailurePolicy(
        PlanContractSecurityClass.INTERNAL_INVARIANT,
        PlanContractRepairability.FAIL_CLOSED,
    ),
    PlanContractFailureKind.WORK_PLAN_PATH_NOT_CHANGED: PlanContractFailurePolicy(
        PlanContractSecurityClass.SAFE_REPOSITORY_ARTIFACT,
        PlanContractRepairability.REPAIR_ONCE,
        PlanContractRepairAction.CREATE_MISSING_FILE,
    ),
    PlanContractFailureKind.WORK_PLAN_PATH_UNSAFE_RESOLUTION: PlanContractFailurePolicy(
        PlanContractSecurityClass.PATH_BOUNDARY,
        PlanContractRepairability.FAIL_CLOSED,
    ),
    PlanContractFailureKind.WORK_PLAN_PATH_NOT_REGULAR: PlanContractFailurePolicy(
        PlanContractSecurityClass.PATH_BOUNDARY,
        PlanContractRepairability.FAIL_CLOSED,
    ),
    PlanContractFailureKind.WORK_PLAN_PATH_NOT_UTF8: PlanContractFailurePolicy(
        PlanContractSecurityClass.SAFE_REPOSITORY_ARTIFACT,
        PlanContractRepairability.REPAIR_ONCE,
        PlanContractRepairAction.UPDATE_EXISTING_FILE,
    ),
    PlanContractFailureKind.WORK_PLAN_SEMANTIC_MARKDOWN_INVALID: PlanContractFailurePolicy(
        PlanContractSecurityClass.SAFE_REPOSITORY_ARTIFACT,
        PlanContractRepairability.REPAIR_ONCE,
        PlanContractRepairAction.UPDATE_EXISTING_FILE,
    ),
    PlanContractFailureKind.WORK_PLAN_PATH_EMPTY: PlanContractFailurePolicy(
        PlanContractSecurityClass.SAFE_REPOSITORY_ARTIFACT,
        PlanContractRepairability.REPAIR_ONCE,
        PlanContractRepairAction.UPDATE_EXISTING_FILE,
    ),
    PlanContractFailureKind.WORK_PLAN_HANDOFF_INVALID: PlanContractFailurePolicy(
        PlanContractSecurityClass.HANDOFF_STRUCTURE,
        PlanContractRepairability.REPAIR_ONCE,
        PlanContractRepairAction.UPDATE_EXISTING_FILE,
    ),
})


class PlanContractValidationError(WorkflowExecutionError):
    """A typed PLAN_ONLY validation failure whose prose has no policy authority."""

    def __init__(self, kind: PlanContractFailureKind, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.policy = PLAN_CONTRACT_FAILURE_POLICIES[kind]


class NoWorkflowChangesError(WorkflowExecutionError):
    """Raised when an implementation reports readiness without repository changes."""


class WorkflowContractError(WorkflowExecutionError):
    """Raised after a reviewer response and its compact format repair both fail."""


class WorkflowCommitApprovalRequired(WorkflowExecutionError):
    """Requests an exact user gate before committing across a changed Slice HEAD."""

    def __init__(self, detail: str, paths: tuple[str, ...]) -> None:
        super().__init__(detail)
        self.detail = detail
        self.paths = tuple(sorted(set(paths)))


class ValidationExecutionError(WorkflowExecutionError):
    """Raised by a v3 driver when required validation cannot be executed."""


class WorkflowDriverContractError(WorkflowExecutionError):
    """Raised before a workflow uses an incomplete driver surface."""


class EvidenceKind(str, Enum):
    FULL_SLICE = "full_slice"
    FULL_BRANCH = "full_branch"


@dataclass(frozen=True)
class WorkflowChanges:
    start_commit: str
    fingerprint: str
    paths: tuple[str, ...]
    full_diff: str
    gate_paths: tuple[str, ...] = ()
    binary_metadata: tuple[BinaryFileMetadata, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.full_diff, ReviewDiff):
            if self.binary_metadata and self.binary_metadata != self.full_diff.binary_metadata:
                raise ValueError("workflow binary metadata differs from the collected diff")
            object.__setattr__(self, "binary_metadata", self.full_diff.binary_metadata)
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
class ApprovedScopeExtension:
    state: WorkflowState
    additions: tuple[PathClassification, ...]


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
    current_branch: str | None = None
    validation_matrix: ValidationMatrix = DEFAULT_WORKFLOW_VALIDATION_MATRIX
    red_state_followup_slice: str | None = None
    retry_incomplete_validation: bool = False
    retry_failed_validation: bool = False
    quota_wait_policy: QuotaWaitPolicy = QuotaWaitPolicy()
    transient_retry_policy: TransientRetryPolicy = TransientRetryPolicy()
    max_transport_failures: int = 3
    max_contract_rejections: int = 3
    require_slice_plan: bool = False
    dynamic_test_scope: bool = False
    plan_gate: bool = False
    plan_only: bool = False
    task_scope_patterns: tuple[str, ...] = ()
    work_plan_path: str | None = None
    approved_plan_text: str | None = None
    audit_report_path: str | None = None
    current_scope_paths: tuple[str, ...] = ()
    scope_extension_gate: bool = False

    def __post_init__(self) -> None:
        if not self.assignment.strip():
            raise ValueError("workflow context requires an assignment")
        if not self.distilled_plan.strip():
            raise ValueError("workflow context requires a distilled plan")
        if not isinstance(self.slice_summary, str):
            raise ValueError("workflow context slice_summary must be a string")
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
        if not isinstance(self.retry_failed_validation, bool):
            raise ValueError("retry_failed_validation must be a boolean")
        if not isinstance(self.quota_wait_policy, QuotaWaitPolicy):
            raise ValueError("quota_wait_policy must be a QuotaWaitPolicy")
        if not isinstance(self.transient_retry_policy, TransientRetryPolicy):
            raise ValueError("transient_retry_policy must be a TransientRetryPolicy")
        for value, label in (
            (self.max_transport_failures, "max_transport_failures"),
            (self.max_contract_rejections, "max_contract_rejections"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{label} must be a positive integer")
        if not isinstance(self.require_slice_plan, bool):
            raise ValueError("require_slice_plan must be a boolean")
        if not isinstance(self.dynamic_test_scope, bool):
            raise ValueError("dynamic_test_scope must be a boolean")
        if not isinstance(self.plan_gate, bool):
            raise ValueError("plan_gate must be a boolean")
        if not isinstance(self.scope_extension_gate, bool):
            raise ValueError("scope_extension_gate must be a boolean")
        if not isinstance(self.plan_only, bool):
            raise ValueError("plan_only must be a boolean")
        if self.plan_only and (
            self.work_plan_path is None or not self.task_scope_patterns
        ):
            raise ValueError(
                "plan-only context requires work_plan_path and task scope patterns"
            )
        if self.approved_plan_text is not None and not self.approved_plan_text.strip():
            raise ValueError("approved plan text must be non-empty when provided")
        if self.audit_report_path is not None and not self.audit_report_path.startswith(
            "docs/internal/"
        ):
            raise ValueError("audit_report_path must be below docs/internal")
        if self.current_scope_paths != tuple(sorted(set(self.current_scope_paths))):
            raise ValueError("current scope paths must be sorted and unique")

    @property
    def distilled_context(self) -> str:
        return self.render_distilled_context()

    def render_distilled_context(
        self,
        *,
        unit_heading: str = "CURRENT SLICE",
        unit_summary: str | None = None,
        current_scope_paths: tuple[str, ...] | None = None,
    ) -> str:
        """Render one request-local unit without inventing persisted Slice facts."""
        approved = self._render_anchors(self.approved_anchors)
        current = self._render_anchors(self.current_anchors)
        summary = self.slice_summary if unit_summary is None else unit_summary
        scope = (
            self.current_scope_paths
            if current_scope_paths is None
            else current_scope_paths
        )
        return (
            f"PLAN\n{self.distilled_plan}\n\n"
            f"{unit_heading}\n{summary}\n\n"
            f"EXACT {unit_heading} ALLOWLIST\n"
            f"{self._render_scope(scope)}\n\n"
            f"APPROVED PLAN ANCHORS\n{approved}\n\n"
            f"CURRENT ANCHORS\n{current}\n\n"
            f"MACHINE STOP RULES (complete, untruncated)\n{self._render_stop_rules()}"
        )

    @staticmethod
    def _render_scope(paths: tuple[str, ...]) -> str:
        return "\n".join(paths) or "PLAN STEP: not bound yet"

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
    work_unit_id: int
    step: WorkflowStep
    round_number: int
    prompt: str
    native_request: workflow_requests.NativeCodexRequestBundle | None = None
    previous_findings: tuple[FindingRecord, ...] = ()
    request_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.request_sequence is None:
            object.__setattr__(self, "request_sequence", self.round_number)
        elif self.request_sequence < 1:
            raise ValueError("Codex request sequence must be 1-based")


def _merge_request_finding_subset(
    authoritative: tuple[FindingRecord, ...],
    offered: tuple[FindingRecord, ...],
    returned: tuple[FindingRecord, ...],
    *,
    recovered_comparison: RecoveredFindingComparison | None = None,
) -> tuple[FindingRecord, ...]:
    """Validate one response at request time, then confirm record-ahead state."""
    try:
        if recovered_comparison is not None:
            merge_request_result(
                recovered_comparison.request_findings,
                recovered_comparison.offered_findings,
                returned,
            )
            return merge_request_result(authoritative, returned, returned)
        return merge_request_result(authoritative, offered, returned)
    except ValueError as exc:
        diagnostic = (
            ""
            if recovered_comparison is None
            else (
                "; request-time offered/returned measured-at: "
                f"{recovered_comparison.request_position}; recovery-time "
                "authoritative compared-at: "
                f"{recovered_comparison.recovery_position}"
            )
        )
        raise WorkflowExecutionError(f"{exc}{diagnostic}") from exc


@dataclass(frozen=True)
class ReviewerInvocation:
    work_unit_id: int
    step: WorkflowStep
    reviewer: AgentRole
    round_number: int
    evidence_kind: EvidenceKind
    fingerprint: str
    paths: tuple[str, ...]
    prompt: str
    review_packet: ReviewPacket | None = None
    native_request: workflow_requests.NativeReviewRequestBundle | None = None
    previous_findings: tuple[FindingRecord, ...] = ()
    request_sequence: int | None = None
    def __post_init__(self) -> None:
        if self.request_sequence is None:
            object.__setattr__(self, "request_sequence", self.round_number)
        elif self.request_sequence < 1:
            raise ValueError("review request sequence must be 1-based")
        if self.native_request is not None and self.reviewer is not AgentRole.CLAUDE:
            raise ValueError("native review requests are supported only for Claude")


@dataclass(frozen=True)
class PersistedNativeReviewerReplay:
    """One request-bound native decision durable ahead of its next transition."""

    output: NativeAgentReviewOutput
    fingerprint: str
    round_number: int

    def __post_init__(self) -> None:
        if not SHA256_PATTERN.fullmatch(self.fingerprint):
            raise ValueError("native reviewer replay requires a SHA-256 fingerprint")
        if self.round_number < 1:
            raise ValueError("native reviewer replay round must be 1-based")


@dataclass(frozen=True)
class WorkflowCommitRequest:
    slice_id: int
    fingerprint: str
    attestation: ValidationAttestation
    claude_review: ContractResult
    findings: tuple[FindingRecord, ...]
    red_state_followup_slice: str | None = None


class WorkflowDriver(Protocol):
    active_state: WorkflowState | None

    def bind_work_unit(self, state: WorkflowState) -> None: ...

    def authoritative_native_findings(
        self,
        state: WorkflowState,
        _projected_findings: tuple[FindingRecord, ...],
    ) -> tuple[FindingRecord, ...]: ...

    def evaluate_slice_finding_convergence(
        self,
        state: WorkflowState,
        *,
        round_number: int,
    ) -> SliceConvergenceEvaluation: ...

    def carry_forward_native_findings(
        self,
        state: WorkflowState,
        _projected_findings: tuple[FindingRecord, ...],
    ) -> tuple[FindingRecord, ...]: ...

    def invoke_codex(
        self, invocation: CodexInvocation
    ) -> str | NativeAgentCodexOutput: ...

    def recover_pending_native_codex(  # allowlist:provider -- canonical capability
        self,
        invocation: CodexInvocation,  # allowlist:provider -- typed boundary
        contract: CodexStepContract,  # allowlist:provider -- typed boundary
        history: WorkflowHistory,
    ) -> NativeAgentCodexOutput | None: ...  # allowlist:provider -- typed boundary

    def collect_changes(self, start_commit: str) -> WorkflowChanges: ...

    def path_exists_at_commit(self, commit: str, path: str) -> bool: ...

    def detect_test_changes(
        self, changes: WorkflowChanges, patterns: tuple[str, ...]
    ) -> TestChangeEvidence | None: ...

    def validate(
        self, changes: WorkflowChanges, request: ValidationRequest
    ) -> ValidationAttestation: ...

    def validate_plan(
        self,
        changes: WorkflowChanges,
        *,
        work_plan_path: str | None,
        scope_patterns: tuple[str, ...],
        plan_only: bool,
    ) -> ValidationAttestation: ...

    def recover_pending_validation_attestation(
        self,
        fingerprint: str,
        expected_commands: tuple[str, ...],
        attestation_id: str,
    ) -> ValidationAttestation | None: ...

    def invoke_reviewer(
        self, invocation: ReviewerInvocation
    ) -> str | NativeAgentReviewOutput: ...

    def recover_pending_native_reviewer(
        self,
        invocation: ReviewerInvocation,
        contract: StepContract,
        history: WorkflowHistory,
    ) -> NativeAgentReviewOutput | None: ...

    def recover_pending_native_reviewer_before_policy(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> PersistedNativeReviewerReplay | None: ...

    def commit_slice(self, request: WorkflowCommitRequest) -> str: ...

    def checkpoint(self, state: WorkflowState, history: WorkflowHistory) -> None: ...

    def persist_gate_decision(
        self, work_unit_id: int, decision: GateDecisionRecord
    ) -> None: ...

    def persist_gate_transition(self, state: WorkflowState) -> None: ...

    def persist_invocation_failure(
        self, payload: InvocationFailurePayload
    ) -> None: ...

    def write_invocation_failure_diagnostic(
        self,
        payload: InvocationFailurePayload,
        provider_text: str,
        technical_text: str,
        provider_data: Mapping[str, object] | None,
    ) -> None: ...

    def persist_scope_extension(
        self,
        state: WorkflowState,
        payload: ScopeExtensionPayload,
    ) -> None: ...

    def scope_extension_source_request_id(
        self,
        state: WorkflowState,
        fingerprint: str,
    ) -> str: ...

    def persist_native_codex_contract(  # allowlist:provider -- canonical capability
        self,
        output: NativeAgentCodexOutput,  # allowlist:provider -- typed boundary
        request_sequence: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None: ...

    def persist_native_review_contract(
        self,
        output: NativeAgentReviewOutput,
        fingerprint: str,
        round_number: int,
        request_sequence: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None: ...

    def persist_review_packet(self, packet: ReviewPacket) -> None: ...

    def persist_validation_request(self, request: ValidationRequest) -> None: ...

    def persist_validation_attestation(
        self, attestation: ValidationAttestation
    ) -> None: ...

MANDATORY_WORKFLOW_DRIVER_METHODS = frozenset(
    {
        "authoritative_native_findings",
        "bind_work_unit",
        "carry_forward_native_findings",
        "checkpoint",
        "collect_changes",
        "commit_slice",
        "detect_test_changes",
        "evaluate_slice_finding_convergence",
        "invoke_codex",  # allowlist:provider -- canonical capability
        "invoke_reviewer",
        "persist_gate_decision",
        "persist_gate_transition",
        "persist_invocation_failure",
        "write_invocation_failure_diagnostic",
        "persist_scope_extension",
        "scope_extension_source_request_id",
        "persist_native_codex_contract",  # allowlist:provider -- canonical capability
        "persist_native_review_contract",
        "persist_review_packet",
        "persist_validation_attestation",
        "persist_validation_request",
        "recover_pending_native_codex",  # allowlist:provider -- canonical capability
        "recover_pending_native_reviewer",
        "recover_pending_native_reviewer_before_policy",
        "recover_pending_validation_attestation",
        "validate",
        "validate_plan",
        "path_exists_at_commit",
    }
)
MANDATORY_WORKFLOW_DRIVER_STATE_ATTRIBUTES = frozenset({"active_state"})
OPTIONAL_WORKFLOW_DRIVER_CAPABILITIES: frozenset[str] = frozenset()


def require_driver_capabilities(
    driver: object,
    *,
    methods: frozenset[str],
    state_attributes: frozenset[str] = frozenset(),
    label: str,
) -> None:
    """Validate one named structural driver surface without invoking hooks."""
    missing: list[str] = []
    invalid: list[str] = []
    for name in sorted(methods):
        try:
            member = inspect.getattr_static(driver, name)
        except AttributeError:
            missing.append(name)
            continue
        if not callable(member):
            invalid.append(name)
    for name in sorted(state_attributes):
        try:
            inspect.getattr_static(driver, name)
        except AttributeError:
            missing.append(name)
    if missing or invalid:
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if invalid:
            detail.append("non-callable=" + ",".join(invalid))
        raise WorkflowDriverContractError(
            f"{label} does not satisfy its mandatory capability contract: "
            + "; ".join(detail)
        )


def require_workflow_driver(driver: object) -> None:
    """Validate the complete structural driver contract without invoking hooks."""
    require_driver_capabilities(
        driver,
        methods=MANDATORY_WORKFLOW_DRIVER_METHODS,
        state_attributes=MANDATORY_WORKFLOW_DRIVER_STATE_ATTRIBUTES,
        label="workflow driver",
    )


@dataclass(frozen=True)
class WorkflowHistory:
    work_unit_id: int
    findings: tuple[FindingRecord, ...] = ()
    events: tuple[AuditEvent, ...] = ()
    attestations: tuple[ValidationAttestation, ...] = ()
    last_claude_fingerprint: str | None = None
    latest_claude_review: ContractResult | None = None
    active_review_packet: ReviewPacket | None = None

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

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "work_unit_id": self.work_unit_id,
            "findings": [_finding_to_dict(item) for item in self.findings],
            "attestations": [_attestation_to_dict(item) for item in self.attestations],
            "last_claude_fingerprint": self.last_claude_fingerprint,
            "latest_claude_review": _review_to_dict(self.latest_claude_review),
        }
        if self.active_review_packet is not None:
            packet = self.active_review_packet
            result["active_review_packet"] = {
                "purpose": packet.purpose,
                "fingerprint": packet.fingerprint,
                "paths": list(packet.manifest.paths),
                "canonical_text": packet.text,
                "digest": packet.digest,
            }
        return result

    @classmethod
    def from_dict(cls, raw: object) -> WorkflowHistory:
        if not isinstance(raw, dict):
            raise ValueError("workflow history must be an object")
        expected = {
            "work_unit_id", "findings", "attestations",
            "last_claude_fingerprint", "latest_claude_review",
        }
        allowed = {*expected, "active_review_packet"}
        if not expected.issubset(raw) or not set(raw).issubset(allowed):
            raise ValueError("workflow history has unknown or missing fields")
        packet_raw = raw.get("active_review_packet")
        active_review_packet: ReviewPacket | None = None
        if packet_raw is not None:
            if not isinstance(packet_raw, dict) or set(packet_raw) != {
                "purpose", "fingerprint", "paths", "canonical_text", "digest"
            }:
                raise ValueError("workflow history review packet has invalid fields")
            active_review_packet = ReviewPacket.restore(
                str(packet_raw["canonical_text"]).encode("utf-8"),
                str(packet_raw["digest"]),
            )
            if (
                active_review_packet.purpose != str(packet_raw["purpose"])
                or active_review_packet.fingerprint != str(packet_raw["fingerprint"])
                or active_review_packet.manifest.paths
                != tuple(str(item) for item in _json_list(packet_raw["paths"]))
            ):
                raise ValueError("workflow history review packet cache differs from canonical bytes")
        return cls(
            work_unit_id=int(raw["work_unit_id"]),
            findings=tuple(_finding_from_dict(item) for item in _json_list(raw["findings"])),
            events=(),
            attestations=tuple(
                _attestation_from_dict(item) for item in _json_list(raw["attestations"])
            ),
            last_claude_fingerprint=(
                None if raw["last_claude_fingerprint"] is None
                else str(raw["last_claude_fingerprint"])
            ),
            latest_claude_review=_review_from_dict(raw["latest_claude_review"]),
            active_review_packet=active_review_packet,
        )


def _json_list(raw: object) -> list[object]:
    if not isinstance(raw, list):
        raise ValueError("workflow history collection must be a list")
    return raw


def _finding_to_dict(item: FindingRecord) -> dict[str, object]:
    return {
        "finding_id": item.finding_id,
        "finding_class": item.finding_class.value,
        "status": item.status.value,
        "summary": item.summary,
        "acceptance_test": item.acceptance_test,
        "affected_paths": list(item.affected_paths),
        "origin": {
            "slice_id": item.origin.slice_id,
            "round_number": item.origin.round_number,
            "reporter": item.origin.reporter.value,
        },
        "responses": [
            {"decision": response.decision.value, "rationale": response.rationale}
            for response in item.responses
        ],
        "status_rationale": item.status_rationale,
    }


def _finding_from_dict(raw: object) -> FindingRecord:
    if not isinstance(raw, dict) or not isinstance(raw.get("origin"), dict):
        raise ValueError("invalid persisted finding")
    origin = raw["origin"]
    return FindingRecord(
        finding_id=str(raw["finding_id"]),
        finding_class=FindingClass(str(raw["finding_class"])),
        status=FindingStatus(str(raw["status"])),
        summary=str(raw["summary"]),
        acceptance_test=str(raw["acceptance_test"]),
        affected_paths=tuple(
            str(value) for value in _json_list(raw.get("affected_paths", []))
        ),
        origin=FindingOrigin(
            slice_id=str(origin["slice_id"]),
            round_number=int(origin["round_number"]),
            reporter=AgentRole(str(origin["reporter"])),
        ),
        responses=tuple(
            FindingResponse(
                decision=FindingResponseDecision(str(item["decision"])),
                rationale=str(item["rationale"]),
            )
            for item in _json_list(raw.get("responses", []))
            if isinstance(item, dict)
        ),
        status_rationale=(
            None if raw.get("status_rationale") is None else str(raw["status_rationale"])
        ),
    )


def _attestation_to_dict(item: ValidationAttestation) -> dict[str, object]:
    return {
        "attestation_id": item.attestation_id,
        "diff_fingerprint": item.diff_fingerprint,
        "expected_commands": list(item.expected_commands),
        "records": [
            {
                "status": record.status.value,
                "command": record.command,
                "exit_code": record.exit_code,
                "output": record.output,
            }
            for record in item.records
        ],
        "output_digest": item.output_digest,
        "summary": item.summary,
        "command_specs": [
            {"mode": spec.mode, "argv": list(spec.argv), "legacy_shell": spec.legacy_shell}
            for spec in item.command_specs
        ],
    }


def _attestation_from_dict(raw: object) -> ValidationAttestation:
    if not isinstance(raw, dict):
        raise ValueError("invalid persisted validation attestation")
    specs_raw = raw.get("command_specs", [])
    return ValidationAttestation(
        attestation_id=str(raw["attestation_id"]),
        diff_fingerprint=str(raw["diff_fingerprint"]),
        expected_commands=tuple(str(item) for item in _json_list(raw["expected_commands"])),
        records=tuple(
            ValidationRecord(
                status=ValidationStatus(str(item["status"])),
                command=str(item["command"]),
                exit_code=int(item["exit_code"]),
                output=str(item.get("output", "")),
            )
            for item in _json_list(raw["records"])
            if isinstance(item, dict)
        ),
        output_digest=str(raw["output_digest"]),
        summary=str(raw["summary"]),
        command_specs=tuple(
            ValidationCommandSpec(
                argv=tuple(str(part) for part in _json_list(item.get("argv", []))),
                legacy_shell=(
                    None if item.get("legacy_shell") is None
                    else str(item["legacy_shell"])
                ),
            )
            for item in _json_list(specs_raw)
            if isinstance(item, dict)
        ),
    )


def _review_to_dict(item: ContractResult | None) -> dict[str, object] | None:
    if item is None:
        return None
    return {
        "reviewer": item.reviewer.value,
        "approval": item.approval,
        "stopped": item.stopped,
        "stop_request": (
            None if item.stop_request is None
            else {
                "rule_id": item.stop_request.rule_id,
                "rationale": item.stop_request.rationale,
                "remediation_paths": list(item.stop_request.remediation_paths),
            }
        ),
        "validation": (
            None if item.validation is None else _attestation_to_dict(item.validation)
        ),
        "test_files": list(item.test_files),
        "pre_mortem": item.pre_mortem,
        "evidence": (
            None if item.evidence is None else {
                "dimensions": item.evidence.dimensions,
                "largest_residual_risk": item.evidence.largest_residual_risk,
                "break_condition": item.evidence.break_condition,
            }
        ),
        "findings": [_finding_to_dict(value) for value in item.findings],
        "anchors": [
            {
                "anchor_id": value.anchor_id, "origin": value.origin,
                "input_fixture": value.input_fixture, "expected": value.expected,
                "tolerance": value.tolerance,
            }
            for value in item.anchors
        ],
        "red_state_followup_slice": item.red_state_followup_slice,
        "finding_closures": [
            {
                "finding_id": finding_id,
                "kind": closure.kind.value,
                "rejection_reason": (
                    None
                    if closure.rejection_reason is None
                    else closure.rejection_reason.value
                ),
                "evidence": closure.evidence,
            }
            for finding_id, closure in item.finding_closures
        ],
        **(
            {"delivery_kind": item.delivery_kind}
            if item.delivery_kind != "review"
            else {}
        ),
        **(
            {
                "occurrences": [
                    {
                        "finding_id": occurrence.finding_id,
                        "rationale": occurrence.rationale,
                    }
                    for occurrence in item.occurrences
                ]
            }
            if item.occurrences
            else {}
        ),
    }


def _review_from_dict(raw: object) -> ContractResult | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("invalid persisted review")
    stop_raw = raw.get("stop_request")
    evidence_raw = raw.get("evidence")
    return ContractResult(
        reviewer=AgentRole(str(raw["reviewer"])),
        approval=raw.get("approval") if isinstance(raw.get("approval"), bool) else None,
        stopped=bool(raw["stopped"]),
        stop_request=(
            None
            if stop_raw is None
            else StopRequest(
                str(stop_raw["rule_id"]),
                str(stop_raw["rationale"]),
                tuple(str(path) for path in stop_raw.get("remediation_paths", ())),
            )
        ),
        validation=(
            None if raw.get("validation") is None
            else _attestation_from_dict(raw["validation"])
        ),
        test_files=tuple(str(item) for item in _json_list(raw["test_files"])),
        pre_mortem=None if raw.get("pre_mortem") is None else str(raw["pre_mortem"]),
        evidence=(
            None if evidence_raw is None else ReviewEvidence(
                str(evidence_raw["dimensions"]),
                str(evidence_raw["largest_residual_risk"]),
                str(evidence_raw["break_condition"]),
            )
        ),
        findings=tuple(_finding_from_dict(item) for item in _json_list(raw["findings"])),
        anchors=tuple(
            AnchorRecord(
                anchor_id=str(item["anchor_id"]), origin=str(item["origin"]),
                input_fixture=str(item["input_fixture"]), expected=str(item["expected"]),
                tolerance=str(item["tolerance"]),
            )
            for item in _json_list(raw["anchors"])
            if isinstance(item, dict)
        ),
        red_state_followup_slice=(
            None if raw.get("red_state_followup_slice") is None
            else str(raw["red_state_followup_slice"])
        ),
        delivery_kind=str(raw.get("delivery_kind", "review")),
        occurrences=tuple(
            FindingOccurrence(str(item["finding_id"]), str(item["rationale"]))
            for item in _json_list(raw.get("occurrences", []))
            if isinstance(item, dict)
        ),
        finding_closures=tuple(
            (
                str(value["finding_id"]),
                native_finding_decisions.NativeFindingClosure(
                    kind=native_finding_decisions.NativeClosureKind(
                        str(value["kind"])
                    ),
                    rejection_reason=(
                        None
                        if value.get("rejection_reason") is None
                        else native_finding_decisions.NativeRejectionReason(
                            str(value["rejection_reason"])
                        )
                    ),
                    evidence=(
                        None
                        if value.get("evidence") is None
                        else str(value["evidence"])
                    ),
                ),
            )
            for value in _json_list(raw.get("finding_closures", []))
            if isinstance(value, dict)
        ),
    )


def _event_to_dict(item: AuditEvent) -> dict[str, object]:
    if isinstance(item, ValidationAuditEvent):
        return {
            "kind": "validation", "event_id": item.event_id,
            "slice_id": item.slice_id, "attestation": _attestation_to_dict(item.attestation),
        }
    return {
        "kind": "review", "event_id": item.event_id, "slice_id": item.slice_id,
        "round_number": item.round_number, "result": _review_to_dict(item.result),
        "allowed_finding_origins": list(item.allowed_finding_origins),
        "is_final_review": item.is_final_review,
    }


def _event_from_dict(raw: object) -> AuditEvent:
    if not isinstance(raw, dict):
        raise ValueError("invalid persisted audit event")
    if raw.get("kind") == "validation":
        return ValidationAuditEvent(
            int(raw["event_id"]), int(raw["slice_id"]),
            _attestation_from_dict(raw["attestation"]),
        )
    if raw.get("kind") == "review":
        result = _review_from_dict(raw["result"])
        if result is None:
            raise ValueError("persisted review event is missing its result")
        return ReviewAuditEvent(
            int(raw["event_id"]), int(raw["slice_id"]), int(raw["round_number"]),
            result,
            tuple(str(item) for item in _json_list(raw["allowed_finding_origins"])),
            bool(raw.get("is_final_review", False)),
        )
    raise ValueError("unknown persisted audit event kind")


@dataclass(frozen=True)
class WorkflowRunResult:
    state: WorkflowState
    history: WorkflowHistory
    commit_ref: str | None = None

    @property
    def completed(self) -> bool:
        return (
            self.state.current_work_unit.status is WorkUnitStatus.COMPLETED
            and not self.workflow_rejected
        )

    @property
    def workflow_completed(self) -> bool:
        """Require this run to reach its own terminal state."""
        return (
            self.completed
            and self.state.current_step is WorkflowStep.COMPLETED
            and all(item.status is SliceStatus.COMPLETED for item in self.state.slices)
            and not self.workflow_rejected
        )

    @property
    def workflow_rejected(self) -> bool:
        """Whether the workflow ended with a reviewer or input-boundary denial."""

        if self._provider_input_boundary_verdict or self.legacy_quota_automation_verdict:
            return True
        if self.state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW:
            # The terminal review may complete with new open findings. They are
            # published as an ordinary Inbox task, not interpreted as a denial.
            return False
        return bool(workflow_rejection_finding_ids(self.state, self.history))

    @property
    def _provider_input_boundary_verdict(self) -> bool:
        unit = self.state.current_work_unit
        if (
            unit.status is not WorkUnitStatus.COMPLETED
            or unit.current_step is WorkflowStep.COMPLETED
        ):
            return False
        return any(
            fact.check_kind == "provider_input_measurement"
            and fact.work_unit_id == unit.work_unit_id
            and fact.operation == unit.current_step.value
            and fact.decision == "denied"
            and fact.error_code == "PROVIDER-INPUT-BUDGET"
            for fact in self.state.bootstrap_checks
        )

    @property
    def legacy_quota_automation_verdict(self) -> bool:
        """Keep previously persisted terminal quota states from advancing."""
        return legacy_quota_automation_verdict(self.state)

    @property
    def rejection_code(self) -> str | None:
        if not self.workflow_rejected:
            return None
        if self._provider_input_boundary_verdict:
            return "PROVIDER-INPUT-BUDGET"
        if self.legacy_quota_automation_verdict:
            return "QUOTA-AUTOMATION-STOPPED"
        return "SLICE-REVIEW-DENIED"

    @property
    def rejection_exception_type(self) -> str | None:
        if not self.workflow_rejected:
            return None
        if self._provider_input_boundary_verdict:
            return "ProviderInputBoundaryVerdict"
        if self.legacy_quota_automation_verdict:
            return "QuotaAutomationVerdict"
        return "SliceReviewVerdict"

    @property
    def rejection_detail(self) -> str | None:
        if not self.workflow_rejected:
            return None
        if self._provider_input_boundary_verdict:
            fact = next(
                fact
                for fact in reversed(self.state.bootstrap_checks)
                if fact.check_kind == "provider_input_measurement"
                and fact.work_unit_id == self.state.current_work_unit_id
                and fact.operation == self.state.current_step.value
                and fact.decision == "denied"
                and fact.error_code == "PROVIDER-INPUT-BUDGET"
            )
            return (
                "PROVIDER-INPUT-BUDGET | the request still exceeds its bound after "
                "safe evidence compaction; no provider was started; measurement="
                f"{fact.transition_fingerprint}"
            )
        if self.legacy_quota_automation_verdict:
            return (
                "QUOTA-AUTOMATION-STOPPED | this persisted terminal quota "
                "state predates resumable quota pauses and cannot advance"
            )
        remaining = project_open_set(self.history.findings).finding_ids
        round_limit_reached = (
            self.state.current_work_unit.round_number
            >= self.state.current_work_unit.max_codex_returns
        )
        return (
            "SLICE-REVIEW-DENIED | "
            + (
                "the configured review-round limit was reached despite continued "
                "disposition progress"
                if round_limit_reached
                else (
                    "the discovery round opened no findings"
                    if (
                        self.state.current_work_unit.kind is WorkUnitKind.SLICE
                        and self.state.current_work_unit.round_number == 1
                    )
                    else (
                        "the convergence round closed no previously known finding "
                        "and recorded no attested fingerprint-changing remediation"
                    )
                )
            )
            + "; remaining open findings: "
            + ", ".join(remaining)
        )

    @property
    def exit_code(self) -> int:
        if self.workflow_rejected:
            return 5
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


def legacy_quota_automation_verdict(state: WorkflowState) -> bool:
    unit = state.current_work_unit
    return (
        unit.status is WorkUnitStatus.COMPLETED
        and unit.current_step is not WorkflowStep.COMPLETED
        and bool(unit.invocation_failures)
        and unit.invocation_failures[-1].failure_kind is AgentFailureKind.QUOTA
        and not unit.invocation_failures[-1].automatic_resume
        and unit.invocation_failures[-1].step is unit.current_step
    )


def workflow_rejection_finding_ids(
    state: WorkflowState, history: WorkflowHistory
) -> tuple[str, ...]:
    """Return the open IDs carried by a terminal reviewer denial, if any."""

    review = history.latest_claude_review  # allowlist:provider -- canonical history field
    if (
        state.current_work_unit.status is not WorkUnitStatus.COMPLETED
        or state.current_step is not WorkflowStep.COMPLETED
        or review is None
        or review.approval is not False
    ):
        return ()
    return project_open_set(history.findings).finding_ids


def resolve_retired_iteration_limit(
    state: WorkflowState, history: WorkflowHistory
) -> WorkflowState:
    """Convert one legacy counter gate using its persisted review progress."""

    if state.current_work_unit.gate.reason is not GateReason.ITERATION_LIMIT:
        return state
    review_events = tuple(
        item for item in history.events if isinstance(item, ReviewAuditEvent)
    )
    progress_made = True
    if len(review_events) >= 2:
        previous = review_events[-2].result.findings
        current = review_events[-1].result.findings
        previous_ids = frozenset(item.finding_id for item in previous)
        newly_opened = bool(
            set(project_open_set(current).finding_ids) - previous_ids
        )
        progress_made = bool(
            newly_opened or project_finding_transition_ids(previous, current)
        )
    return state.continue_retired_iteration_limit(progress_made=progress_made)


def _uses_correction_finding_authority(state: WorkflowState) -> bool:
    """Return whether this round must resolve Findings from records."""

    return (
        state.current_step is WorkflowStep.CODEX_CORRECTION
        or state.current_work_unit.round_number > 1
    )


def _project_correction_request_findings(
    state: WorkflowState,
    findings: tuple[FindingRecord, ...],
) -> tuple[FindingRecord, ...]:
    """Select the exact active correction set from a record-backed ledger."""

    if not _uses_correction_finding_authority(state):
        return findings
    try:
        projection = project_request_subset(
            findings,
            finding_ids=state.current_work_unit.open_findings,
            open_only=True,
        )
    except ValueError as exc:
        raise WorkflowExecutionError(
            f"authoritative correction finding replay failed: {exc}"
        ) from exc
    expected_ids = sorted_finding_ids(state.current_work_unit.open_findings)
    if projection.finding_ids != expected_ids:
        raise WorkflowExecutionError(
            "authoritative correction finding replay differs from the "
            "exact active open finding set"
        )
    return projection.findings


def _review_round_number(
    unit: WorkUnitRecord,
    history: WorkflowHistory,
    reviewer: AgentRole,
) -> int:
    """Return the accepted domain round, independent of request retries."""

    _ = (history, reviewer)
    return unit.round_number


def _validate_plan_measurement_support(
    slices: tuple[PlannedSlice, ...], matrix: ValidationMatrix
) -> None:
    try:
        validate_measurement_support(
            (
                criterion
                for planned in slices
                for criterion in planned.acceptance_criteria
            ),
            build_output_declared=bool(matrix.required_artifacts),
            running_product_declared=matrix.product_command is not None,
        )
    except ValueError as exc:
        raise WorkflowExecutionError(str(exc)) from exc


class WorkflowEngine:
    """Additive state-v3 engine for the Codex/Claude chain."""

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
        self._retried_failed_validation_fingerprints: set[str] = set()
        self._validation_evidence = (
            workflow_validation_evidence.WorkflowValidationEvidence(
                workflow_validation_evidence.WorkflowValidationEvidenceDependencies(
                    validate_plan=lambda changes, **kwargs: self.driver.validate_plan(
                        changes, **kwargs
                    ),
                    persist_validation_attestation=lambda attestation: (
                        self._persist_structured(
                            self.driver.persist_validation_attestation,
                            attestation,
                        )
                    ),
                    persist_validation_request=lambda request: self._persist_structured(
                        self.driver.persist_validation_request,
                        request,
                    ),
                    recover_pending_validation_attestation=(
                        lambda *args: self.driver.recover_pending_validation_attestation(
                            *args
                        )
                    ),
                    validate=lambda changes, request: self.driver.validate(
                        changes, request
                    ),
                    retried_failed_validation_fingerprints=(
                        self._retried_failed_validation_fingerprints
                    ),
                    execution_error=WorkflowExecutionError,
                    validation_execution_error=ValidationExecutionError,
                )
            )
        )
        self._failure_recording = workflow_failure_recording.WorkflowFailureRecording(
            workflow_failure_recording.WorkflowFailureRecordingDependencies(
                current_invocation_fingerprint=(
                    lambda state: self._current_invocation_fingerprint(state)
                ),
                write_invocation_failure_diagnostic=(
                    lambda payload, provider_text, technical_text, provider_data: (
                        self.driver.write_invocation_failure_diagnostic(
                            payload,
                            provider_text,
                            technical_text,
                            provider_data,
                        )
                    )
                ),
                persist_invocation_failure=lambda payload: self._persist_structured(
                    self.driver.persist_invocation_failure,
                    payload,
                ),
                checkpoint=lambda state, history: self.driver.checkpoint(
                    state, history
                ),
                now=lambda: self.now_fn(),
                execution_error=WorkflowExecutionError,
            )
        )

    def _persist_structured(
        self, sink: Callable[..., None], *args: object
    ) -> None:
        """Invoke one declared driver sink and fail closed on divergence."""
        try:
            sink(*args)
        except Exception as exc:
            diagnostic = (
                exc.orchestrator_diagnostic
                if isinstance(exc, WorkflowExecutionError)
                else None
            )
            raise WorkflowExecutionError(
                f"structured dual-write failed before workflow decision: {exc}",
                orchestrator_diagnostic=diagnostic,
            ) from exc

    def _bind_context_to_current_unit(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> WorkflowContext:
        """Keep transient request prose aligned with the persisted unit boundary."""
        planned = next(
            (
                item
                for item in state.planned_slices
                if item.slice_id == state.current_slice_id
            ),
            None,
        )
        slice_summary = context.slice_summary
        scope_paths = state.current_slice.scope_paths
        if state.current_work_unit.kind is WorkUnitKind.SLICE and planned is not None:
            slice_summary = planned.summary
        if (
            slice_summary == context.slice_summary
            and context.current_scope_paths == scope_paths
        ):
            return context
        return replace(
            context,
            slice_summary=slice_summary,
            current_scope_paths=scope_paths,
        )

    def run_current_work_unit(
        self, state: WorkflowState, context: WorkflowContext,
        history: WorkflowHistory | None = None,
    ) -> WorkflowRunResult:
        require_workflow_driver(self.driver)
        current = state.current_work_unit
        active_history = history or WorkflowHistory(current.work_unit_id)
        if active_history.work_unit_id != current.work_unit_id:
            raise WorkflowExecutionError("workflow history belongs to a different work unit")
        self._bind_driver_work_unit(state)
        context = self._bind_context_to_current_unit(state, context, active_history)
        bound_state = self._bind_current_open_findings(state, active_history)
        if bound_state is not state:
            state = bound_state
            self._bind_driver_work_unit(state)
            self.driver.checkpoint(state, active_history)
        current = state.current_work_unit
        if (
            current.status is WorkUnitStatus.AWAITING_USER_DECISION
            and current.gate.reason is GateReason.TEST_CHANGE
            and current.gate.fingerprint is not None
        ):
            state = state.inherit_prior_test_approval(
                current.gate.fingerprint,
                current.gate.paths,
            )
            self._persist_structured(self.driver.persist_gate_transition, state)
            current = state.current_work_unit
            self._bind_driver_work_unit(state)
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            return WorkflowRunResult(state, active_history)

        pending_native = self.driver.recover_pending_native_reviewer_before_policy(
            state, context, active_history
        )
        if pending_native is not None:
            state, active_history = self._apply_pre_policy_recovered_review(
                state, context, active_history, pending_native
            )
            if state.current_work_unit.status is not WorkUnitStatus.IN_PROGRESS:
                return WorkflowRunResult(state, active_history)
            self._bind_driver_work_unit(state)

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
            resume_step = state.current_step
            state, resume_halted = self._revalidate_waiting_diff(
                state, latest_failure, context
            )
            if resume_halted:
                self.driver.checkpoint(state, active_history)
                return WorkflowRunResult(state, active_history)
            if state.current_step is not resume_step:
                active_history = replace(
                    active_history,
                    last_claude_fingerprint=None,
                    latest_claude_review=None,
                )
                self.driver.checkpoint(state, active_history)

        state, active_history, anchor_halted = self._apply_anchor_gate(
            state, context, active_history
        )
        if anchor_halted:
            return WorkflowRunResult(state, active_history)

        for _ in range(1024):
            context = self._bind_context_to_current_unit(state, context, active_history)
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
                WorkflowStep.CLAUDE_FINAL_REVIEW,
            ):
                state, active_history = self._run_review(
                    state, context, active_history, AgentRole.CLAUDE
                )
                if state.current_work_unit.status is not WorkUnitStatus.IN_PROGRESS:
                    return WorkflowRunResult(state, active_history)
                continue
            if step is WorkflowStep.SLICE_COMMIT:
                committed = self._commit(state, active_history, context)
                if (
                    not committed.completed
                    and committed.state.current_work_unit.status
                    is WorkUnitStatus.IN_PROGRESS
                    and committed.state.current_step is not WorkflowStep.SLICE_COMMIT
                ):
                    state = committed.state
                    active_history = committed.history
                    continue
                return committed
            if step is WorkflowStep.COMPLETED:
                if (
                    state.current_work_unit.kind is WorkUnitKind.PLAN
                    and state.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
                ):
                    state = state.complete_current_work_unit()
                    self.driver.checkpoint(state, active_history)
                return WorkflowRunResult(state, active_history)
            raise WorkflowExecutionError(
                f"step {step.value} is outside the state-v3 development engine"
            )
        raise WorkflowExecutionError("workflow exceeded its deterministic transition bound")

    def _apply_pre_policy_recovered_review(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
        pending: PersistedNativeReviewerReplay,
    ) -> tuple[WorkflowState, WorkflowHistory]:
        """Apply one durable reviewer decision using its request-time ledger cut."""

        if not isinstance(pending, PersistedNativeReviewerReplay):
            raise WorkflowExecutionError(
                "pre-policy native reviewer recovery returned an invalid contract"
            )
        is_plan_review = state.current_step is WorkflowStep.CLAUDE_PLAN_REVIEW
        is_final_review = state.current_step is WorkflowStep.CLAUDE_FINAL_REVIEW
        request_result = pending.output.result
        result = request_result
        finding_ledger = self._authoritative_finding_ledger(
            state, history.findings
        )
        requested_ids: tuple[str, ...] | None = None
        new_ids: tuple[str, ...] = ()
        progress_ids: tuple[str, ...] = ()
        recovered = pending.output.recovered_finding_comparison
        if pending.output.context is not None:
            offered = pending.output.context.previous_findings
            if recovered is not None:
                result = replace(
                    result,
                    findings=self._merge_review_request_subset(
                        finding_ledger,
                        offered,
                        result.findings,
                        review_type=(
                            "final review" if is_final_review else "slice review"
                        ),
                        recovered_comparison=recovered,
                    ),
                )
            elif is_final_review and offered != finding_ledger:
                result = replace(
                    result,
                    findings=self._merge_review_request_subset(
                        finding_ledger,
                        offered,
                        result.findings,
                        review_type="final review",
                    ),
                )
            if is_final_review:
                requested_ids = tuple(item.finding_id for item in offered)
                offered_id_set = frozenset(requested_ids)
                new_ids = tuple(
                    item.finding_id
                    for item in request_result.findings
                    if item.finding_id not in offered_id_set
                )
                progress_ids = project_finding_transition_ids(
                    offered, request_result.findings
                )
        return self._apply_review_result(
            state=state,
            context=context,
            history=history,
            reviewer=AgentRole.CLAUDE,
            result=result,
            fingerprint=pending.fingerprint,
            round_number=pending.round_number,
            is_plan_review=is_plan_review,
            is_final_review=is_final_review,
            finding_ledger=finding_ledger,
            final_review_requested_ids=requested_ids,
            final_review_new_finding_ids=new_ids,
            final_review_progress_ids=progress_ids,
        )

    def decide_current_gate(
        self,
        state: WorkflowState,
        history: WorkflowHistory,
        *,
        approved: bool,
        rationale: str,
        path_classes: PathClasses | None = None,
    ) -> WorkflowRunResult:
        """Record an explicit decision against the exact persisted gate evidence."""
        require_workflow_driver(self.driver)
        if history.work_unit_id != state.current_work_unit_id:
            raise WorkflowExecutionError(
                "workflow history belongs to a different gated work unit"
            )
        gate = state.current_work_unit.gate
        if gate.fingerprint is None:
            stop_reason = gate.detail.split(" | ", 1)[0] if gate.detail else "unspecified"
            raise WorkflowExecutionError(
                f"current gate has no fingerprint (gate reason: {gate.reason.value}; "
                f"stop reason: {stop_reason}); no approval is required. "
                "Provide the prerequisite, then run_task --watch --resume "
                "without --approve-gate."
            )
        scope_extension_gate = (
            gate.reason is GateReason.STOP_REQUEST
            and gate.detail is not None
            and gate.detail.startswith(f"{SCOPE_EXTENSION_REQUESTED_RULE_ID} |")
        )
        source_request_id: str | None = None
        if approved and scope_extension_gate:
            if path_classes is None:
                raise WorkflowExecutionError(
                    "scope-extension approval requires the configured path classes"
                )
            source_request_id = self.driver.scope_extension_source_request_id(
                state,
                gate.fingerprint,
            )
        prior_decision_count = len(state.current_work_unit.gate_decisions)
        try:
            updated = state.record_user_gate_decision(
                approved=approved,
                fingerprint=gate.fingerprint,
                paths=gate.paths,
                rationale=rationale,
            )
        except ValueError as exc:
            raise WorkflowExecutionError(f"invalid user gate decision: {exc}") from exc
        if len(updated.current_work_unit.gate_decisions) > prior_decision_count:
            self._persist_structured(
                self.driver.persist_gate_decision,
                updated.current_work_unit_id,
                updated.current_work_unit.gate_decisions[-1],
            )
        if approved and scope_extension_gate:
            additions = tuple(
                sorted(set(gate.paths).difference(state.current_slice.scope_paths))
            )
            if additions:
                updated = updated.approve_current_slice_scope_extension(additions)
                assert source_request_id is not None
                self.driver.persist_scope_extension(
                    updated,
                    ScopeExtensionPayload(
                        work_unit_id=str(state.current_work_unit_id),
                        slice_id=str(state.current_slice_id),
                        source_request_id=source_request_id,
                        stop_rule_id=SCOPE_EXTENSION_REQUESTED_RULE_ID,
                        rationale=gate.detail.split(" | ", 1)[1],
                        additions=tuple(
                            ScopeExtensionPathPayload(
                                path,
                                classify_path(path, path_classes).path_class.value,
                            )
                            for path in additions
                        ),
                    ),
                )
            updated = updated.mark_side_effect_completed(
                self._authorized_remediation_reprompt_key(gate.paths)
            )
            # The stopped response belongs to the request before approval.
            # A fresh sequence prevents record-ahead recovery from replaying it.
            updated = updated.start_recomposed_request()
        self._persist_structured(self.driver.persist_gate_transition, updated)
        self.driver.checkpoint(updated, history)
        return WorkflowRunResult(updated, history)

    def reframe_unexpected_path_stop_gate(
        self, state: WorkflowState
    ) -> WorkflowState:
        """Upgrade a legacy Codex UNEXPECTED-PATH stop to an exact user gate."""
        require_driver_capabilities(
            self.driver,
            methods=frozenset({"collect_changes"}),
            label="gate-reframing driver",
        )
        current = state.current_work_unit
        gate = current.gate
        direct_unexpected_path = (
            gate.detail is not None
            and gate.detail.startswith(f"{UNEXPECTED_PATH_RULE_ID} |")
        )
        plan_contract_kind: PlanContractFailureKind | None = None
        if gate.detail is not None:
            plan_gate_parts = gate.detail.split(" | ", 2)
            if len(plan_gate_parts) == 3 and plan_gate_parts[0] == "PLAN-CONTRACT-INVALID":
                try:
                    plan_contract_kind = PlanContractFailureKind(plan_gate_parts[1])
                except ValueError:
                    pass
        plan_validation_scope_stop = (
            current.kind is WorkUnitKind.PLAN
            and plan_contract_kind
            is PlanContractFailureKind.CHANGED_PATH_OUTSIDE_SCOPE
        )
        if (
            current.status is not WorkUnitStatus.AWAITING_USER_DECISION
            or gate.reason is not GateReason.STOP_REQUEST
            or gate.fingerprint is not None
            or not (direct_unexpected_path or plan_validation_scope_stop)
        ):
            return state
        start_commit = self._change_start_commit(state)
        if start_commit is None:
            return state
        try:
            changes = self.driver.collect_changes(start_commit)
        except Exception:
            return state
        unexpected = self._validate_change_boundary(
            state, changes, current.kind
        )
        resumed = state.resume_after_user_decision()
        if not unexpected:
            return resumed
        next_step = (
            WorkflowStep.CLAUDE_PLAN_REVIEW
            if plan_validation_scope_stop
            else current.current_step
        )
        return resumed.await_user_gate(
            reason=GateReason.UNEXPECTED_FILE,
            detail=(
                f"{UNEXPECTED_PATH_RULE_ID} | canonical changes contain paths "
                f"outside the persisted Slice scope: {', '.join(unexpected)}"
            ),
            fingerprint=changes.fingerprint,
            paths=unexpected,
            gate_step=next_step,
            resume_step=next_step,
        )

    def _prepare_agent_dispatch(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> tuple[
        bool,
        CodexStepContract,  # allowlist:provider -- typed boundary
        CodexInvocation,  # allowlist:provider -- typed boundary
        NativeAgentCodexOutput | None,  # allowlist:provider -- typed boundary
        WorkflowHistory,
    ]:
        unit, context = (
            state.current_work_unit,
            self._context_with_scope_approval(state, context),
        )
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
            request_sequence=unit.request_sequence,
            require_test_files_record=not is_plan,
            expected_test_files=context.expected_test_files if not is_plan else (),
            # R-10 gates after implementation readiness and before review. Codex must be
            # able to report test paths before a user approval exists.
            test_changes_approved=True,
            require_slice_plan=is_plan and context.require_slice_plan,
            plan_artifact_path=(
                context.work_plan_path if is_plan and context.plan_only else None
            ),
            enforce_expected_test_files=not context.dynamic_test_scope,
        )
        request_kind = (
            workflow_requests.NativeCodexRequestKind.PLAN
            if is_plan
            else workflow_requests.NativeCodexRequestKind.CORRECTION
            if state.current_step is WorkflowStep.CODEX_CORRECTION
            else workflow_requests.NativeCodexRequestKind.IMPLEMENTATION
        )
        is_correction_request = (
            request_kind is workflow_requests.NativeCodexRequestKind.CORRECTION
        )
        history = self._bind_correction_request_history(state, history)
        additional_authorized_paths = self._fingerprint_bound_codex_scope_paths(
            state
        )
        current_slice_diff: str | None = None
        correction_fingerprint: str | None = None
        if is_correction_request:
            correction_start = state.current_slice.start_fingerprint
            if correction_start is None:
                raise WorkflowExecutionError(
                    "native Codex correction requires a persisted start fingerprint"
                )
            correction_changes = self.driver.collect_changes(
                state.current_slice.start_commit or state.branch_base
            )
            correction_fingerprint = correction_changes.fingerprint
            # collect_changes() is already bound to the persisted Slice start
            # commit. Its full diff is therefore the canonical current delta;
            # asking the driver to reconstruct the same delta a second time
            # would add another mutable input surface.
            current_slice_diff = correction_changes.full_diff
        native_request = workflow_requests.native_codex_request(
            state=state,
            context=context,
            history=history,
            contract=contract,
            request_kind=request_kind,
            execution_error=WorkflowExecutionError,
            additional_authorized_paths=additional_authorized_paths,
            current_slice_diff=current_slice_diff,
            correction_fingerprint=correction_fingerprint,
            correction_findings=history.findings if is_correction_request else None,
        )
        invocation = CodexInvocation(
            unit.work_unit_id,
            state.current_step,
            unit.round_number,
            "",
            request_sequence=unit.request_sequence,
            native_request=native_request,
            previous_findings=history.findings,
        )
        recovered = (
            self.driver.recover_pending_native_codex(  # allowlist:provider
                invocation, contract, history
            )
            if native_request is not None
            else None
        )
        if recovered is None and native_request is not None:
            authoritative_history = self._rebind_unsent_request_history(
                state, history
            )
            if authoritative_history.findings != history.findings:
                history = authoritative_history
                native_request = workflow_requests.native_codex_request(
                    state=state,
                    context=context,
                    history=history,
                    contract=contract,
                    request_kind=request_kind,
                    execution_error=WorkflowExecutionError,
                    additional_authorized_paths=additional_authorized_paths,
                    current_slice_diff=current_slice_diff,
                    correction_fingerprint=correction_fingerprint,
                    correction_findings=history.findings,
                )
                invocation = CodexInvocation(
                    unit.work_unit_id,
                    state.current_step,
                    unit.round_number,
                    "",
                    request_sequence=unit.request_sequence,
                    native_request=native_request,
                    previous_findings=history.findings,
                )
            else:
                history = authoritative_history
        return is_plan, contract, invocation, recovered, history

    def _context_with_scope_approval(
        self, state: WorkflowState, context: WorkflowContext
    ) -> WorkflowContext:
        """Rebuild the approval notice from durable current-Slice state."""
        unit = state.current_work_unit
        decision = next(
            (
                item
                for item in reversed(unit.gate_decisions)
                if item.approved
                and item.reason is GateReason.STOP_REQUEST
                and item.resume_step is state.current_step
                and item.paths
                and set(item.paths).issubset(state.current_slice.scope_paths)
            ),
            None,
        )
        if decision is not None:
            return replace(
                context,
                current_scope_paths=state.current_slice.scope_paths,
                slice_summary=(
                    f"{context.slice_summary}\n\n"
                    "OPERATOR-APPROVED IMPLEMENTER SCOPE EXTENSION\n"
                    "Approved paths for this Slice: "
                    + ", ".join(decision.paths)
                    + "\nApproval reason: "
                    + decision.rationale
                ),
            )
        approved_scope = any(
            key.startswith("authorized-remediation-reprompt:")
            for key in unit.completed_side_effects
        ) or bool(self._approved_scope_additions(state))
        if approved_scope and not any(
            heading in context.slice_summary
            for heading in (
                "AUTOMATIC IMPLEMENTER SCOPE EXTENSION",
                "AUTOMATIC PRIOR-SLICE REMEDIATION",
                "REMEDIATION PATHS ALREADY AUTHORIZED",
            )
        ):
            return replace(
                context,
                current_scope_paths=state.current_slice.scope_paths,
                slice_summary=(
                    f"{context.slice_summary}\n\n"
                    "APPROVED IMPLEMENTER SCOPE FOR CURRENT SLICE\n"
                    "Authorized paths: "
                    + ", ".join(state.current_slice.scope_paths)
                ),
            )
        return context

    def _run_codex(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> tuple[WorkflowState, WorkflowHistory]:
        is_plan, contract, invocation, recovered, history = (
            self._prepare_agent_dispatch(state, context, history)
        )
        state, output = self._invoke_role(
            state,
            history,
            context,
            AgentRole.CODEX,
            lambda: recovered or self.driver.invoke_codex(invocation),
        )
        return self._apply_agent_output(
            state,
            context,
            history,
            is_plan,
            invocation,
            output,
        )

    def _apply_agent_output(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
        is_plan: bool,
        invocation: CodexInvocation,  # allowlist:provider -- typed boundary
        output: NativeAgentCodexOutput | None,  # allowlist:provider -- typed boundary
    ) -> tuple[WorkflowState, WorkflowHistory]:
        if output is None:
            return state, history
        if not isinstance(output, NativeAgentCodexOutput):
            raise WorkflowContractError("Codex returned a non-native result")
        result = output.result
        output_text = output.canonical_json
        self._persist_structured(
            self.driver.persist_native_codex_contract,  # allowlist:provider
            output,
            invocation.request_sequence,
            history.findings,
        )
        assert invocation.native_request is not None
        history = self._merge_implementer_output_findings(
            state, history, invocation, output
        )
        if result.stopped:
            if result.stop_request is None:
                raise WorkflowExecutionError("Codex stop has no structured stop request")
            return self._apply_stopped_agent_output(
                state, context, history, output
            )
        if result.ready is not True:
            state = state.await_policy_gate(
                reason=GateReason.STOP_REQUEST,
                detail=(
                    "CODEX-NOT-READY | Codex reported the current step as not ready; "
                    "ready=false does not document a blocker; resolve why the result "
                    "was not ready before resuming the same step"
                ),
            )
            self.driver.checkpoint(state, history)
            return state, history
        if is_plan and result.slice_plan:
            _validate_plan_measurement_support(
                result.slice_plan, context.validation_matrix
            )
            unexpected_plan_paths = tuple(
                sorted(
                    {
                        path
                        for planned in result.slice_plan
                        for path in planned.scope_paths
                        if context.task_scope_patterns
                        and not matches_path_patterns(path, context.task_scope_patterns)
                    }
                )
            )
            if unexpected_plan_paths:
                raise WorkflowExecutionError(
                    "TASK-SCOPE | SLICE_PLAN contains paths outside the declared task "
                    f"scope: {', '.join(unexpected_plan_paths)}"
                )
            if context.plan_only:
                if len(result.slice_plan) != 1:
                    raise WorkflowExecutionError(
                        "PLAN_ONLY requires exactly one executable Slice for the plan artifact"
                    )
                assert context.work_plan_path is not None
                if context.work_plan_path not in result.slice_plan[0].scope_paths:
                    raise WorkflowExecutionError(
                        "PLAN_ONLY Slice must include the declared WORK_PLAN_PATH"
                    )
            planned_slices = result.slice_plan
            if context.audit_report_path is not None and not context.plan_only:
                planned_slices = tuple(
                    PlannedSlice(
                        slice_id=planned.slice_id,
                        summary=planned.summary,
                        scope_paths=tuple(
                            sorted(
                                {
                                    *planned.scope_paths,
                                    context.audit_report_path,
                                    managed_slice_document_path(
                                        context.audit_report_path,
                                        planned.slice_id,
                                        planned.summary,
                                    ),
                                }
                            )
                        ),
                        acceptance_criteria=planned.acceptance_criteria,
                    )
                    for planned in result.slice_plan
                )
            state = state.bind_slice_plan(
                planned_slices,
                first_start_commit=state.current_slice.start_commit or state.branch_base,
            )
        if is_plan and context.plan_only:
            state, history, halted = self._validate_plan_before_review(
                state, context, history
            )
            if halted:
                return state, history
        next_step = (
            WorkflowStep.CLAUDE_PLAN_REVIEW
            if is_plan
            else WorkflowStep.CLAUDE_SLICE_REVIEW
        )
        state = state.with_current_step(next_step)
        self.driver.checkpoint(state, history)
        return state, history

    def _apply_stopped_agent_output(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
        output: NativeAgentCodexOutput,
    ) -> tuple[WorkflowState, WorkflowHistory]:
        stop_request = output.result.stop_request
        assert stop_request is not None
        try:
            validate_builtin_stop_content(
                stop_request.rule_id,
                stop_request.rationale,
                stop_request.remediation_paths,
            )
        except ValueError as exc:
            raise WorkflowExecutionError(
                "STOP_REQUESTED has invalid content for "
                f"{stop_request.rule_id!r}: {exc}"
            ) from exc
        scope_approval = self._expand_approved_remediation_scope(
            state, context, stop_request
        )
        if scope_approval is not None:
            return self._apply_scope_extension(
                state, context, history, output, stop_request, scope_approval
            )
        validation_handoff = self._handoff_agent_sandbox_validation(
            state, stop_request
        )
        if validation_handoff is not None:
            self.driver.checkpoint(validation_handoff, history)
            self._bind_driver_work_unit(validation_handoff)
            logger.info(
                "Agent-local validation was blocked by its sandbox; handing the "
                "authoritative matrix back to the orchestrator for Slice %02d.",
                validation_handoff.current_slice_id,
            )
            handoff_context = replace(
                context,
                slice_summary=(
                    f"{context.slice_summary}\n\n"
                    "AUTOMATIC ORCHESTRATOR VALIDATION HANDOFF\n"
                    f"Agent-local failure: {stop_request.rationale}\n"
                    "The implementation itself is not blocked. Do not rerun the "
                    "configured full validation matrix inside the agent sandbox. "
                    "Complete any remaining implementation bookkeeping and emit the "
                    "normal readiness record; the orchestrator will execute the "
                    "authoritative matrix immediately afterward. Request a stop only "
                    "for a genuine implementation blocker or product decision."
                ),
            )
            return self._run_codex(validation_handoff, handoff_context, history)
        state = self._halt_for_stop_request(state, context, stop_request)
        self.driver.checkpoint(state, history)
        return state, history

    def _apply_scope_extension(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
        output: NativeAgentCodexOutput,
        stop_request: StopRequest,
        scope_approval: ApprovedScopeExtension,
    ) -> tuple[WorkflowState, WorkflowHistory]:
        expanded = (
            scope_approval.state.mark_side_effect_completed(
                self._authorized_remediation_reprompt_key(stop_request.remediation_paths)
            ).start_recomposed_request()
        )
        if scope_approval.additions:
            self.driver.persist_scope_extension(
                expanded,
                ScopeExtensionPayload(
                    work_unit_id=str(state.current_work_unit_id),
                    slice_id=str(state.current_slice_id),
                    source_request_id=output.request_id,
                    stop_rule_id=stop_request.rule_id,
                    rationale=stop_request.rationale,
                    additions=tuple(
                        ScopeExtensionPathPayload(item.path, item.path_class.value)
                        for item in scope_approval.additions
                    ),
                ),
            )
        self.driver.checkpoint(expanded, history)
        self._bind_driver_work_unit(expanded)
        remediation = ", ".join(stop_request.remediation_paths)
        added_paths = tuple(
            sorted(
                set(expanded.current_slice.scope_paths).difference(
                    state.current_slice.scope_paths
                )
            )
        )
        if added_paths:
            logger.info(
                "Automatically extending Slice %02d with approved remediation paths: %s",
                expanded.current_slice_id,
                remediation,
            )
            if stop_request.rule_id == VALIDATION_UNAVAILABLE_RULE_ID:
                remediation_heading = "AUTOMATIC PRIOR-SLICE REMEDIATION"
                remediation_instruction = (
                    "Repair these approved paths before completing the current "
                    f"Slice: {remediation}"
                )
            else:
                remediation_heading = "AUTOMATIC IMPLEMENTER SCOPE EXTENSION"
                remediation_instruction = (
                    "Use these approved paths to finish the current Slice: "
                    f"{remediation}"
                )
        else:
            logger.info(
                "Remediation paths are already authorized for Slice %02d; "
                "re-prompting Codex once instead of requesting user approval: %s",
                expanded.current_slice_id,
                remediation,
            )
            remediation_heading = (
                "REMEDIATION PATHS ALREADY AUTHORIZED — DO NOT REQUEST AGAIN"
            )
            remediation_instruction = (
                f"The exact current allowlist already contains: {remediation}. "
                "Implement and validate the required correction now. Do not emit "
                "STOP_REQUESTED or REMEDIATION_PATHS for these paths again."
            )
        expanded_context = replace(
            context,
            current_scope_paths=expanded.current_slice.scope_paths,
            slice_summary=(
                f"{context.slice_summary}\n\n"
                f"{remediation_heading}\n"
                f"Cause: {stop_request.rationale}\n"
                f"{remediation_instruction}"
            ),
        )
        return self._run_codex(expanded, expanded_context, history)

    def _merge_implementer_output_findings(
        self,
        state: WorkflowState,
        history: WorkflowHistory,
        invocation: CodexInvocation,  # allowlist:provider -- typed boundary
        output: NativeAgentCodexOutput,  # allowlist:provider -- typed boundary
    ) -> WorkflowHistory:
        """Merge fresh or recovered output without mixing its ledger cuts."""

        recovered = output.recovered_finding_comparison
        if recovered is not None:
            history = replace(
                history,
                findings=self._authoritative_finding_ledger(
                    state, history.findings
                ),
            )
        assert invocation.native_request is not None
        return replace(
            history,
            findings=_merge_request_finding_subset(
                history.findings,
                invocation.native_request.bound_context.context.previous_findings,
                output.result.findings,
                recovered_comparison=recovered,
            ),
        )

    def _validate_plan_before_review(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> tuple[WorkflowState, WorkflowHistory, bool]:
        """Validate a PLAN_ONLY handoff before spending either reviewer budget."""
        self._bind_driver_work_unit(state)
        start_commit = state.current_slice.start_commit or state.branch_base
        try:
            changes = self.driver.collect_changes(start_commit)
            unexpected = self._validate_change_boundary(
                state,
                changes,
                WorkUnitKind.PLAN,
                context=context,
            )
            if unexpected:
                next_step = WorkflowStep.CLAUDE_PLAN_REVIEW
                state = state.await_user_gate(
                    reason=GateReason.UNEXPECTED_FILE,
                    detail=(
                        f"{UNEXPECTED_PATH_RULE_ID} | canonical changes contain paths "
                        f"outside the persisted Slice scope: {', '.join(unexpected)}"
                    ),
                    fingerprint=changes.fingerprint,
                    paths=unexpected,
                    gate_step=next_step,
                    resume_step=next_step,
                )
                self.driver.checkpoint(state, history)
                return state, history, True
            _, history = self._attestation(
                changes,
                history,
                context,
                state.current_slice_id,
                plan_contract=True,
            )
        except WorkflowExecutionError as exc:
            detail = str(exc)
            failure_kind = (
                exc.kind if isinstance(exc, PlanContractValidationError) else None
            )
            failure_policy = (
                exc.policy if failure_kind is not None else None
            )
            repairable = (
                failure_policy is not None
                and failure_policy.repairability
                is PlanContractRepairability.REPAIR_ONCE
            )
            repair_key = "automatic-plan-contract-repair"
            if repairable and not state.current_work_unit.has_completed_side_effect(
                repair_key
            ):
                logger.warning(
                    "Plan contract is not handoff-ready; returning it to Codex once "
                    "before reviewer invocation: %s",
                    detail,
                )
                state = state.mark_side_effect_completed(repair_key)
                state = state.with_current_step(WorkflowStep.CODEX_PLAN_REVISION)
                self.driver.checkpoint(state, history)
                assert failure_policy is not None
                if (
                    failure_policy.repair_action
                    is PlanContractRepairAction.CREATE_MISSING_FILE
                ):
                    repair_action = (
                        "The previous response returned its native JSON record without "
                        "writing the required repository artifact. Create the missing "
                        f"file {state.work_plan_path} in the repository now, write the "
                        "complete executable work plan into it, then reread and verify "
                        "the file before returning `ready: true`. The JSON result is only "
                        "a receipt and does not replace the file."
                    )
                else:
                    assert (
                        failure_policy.repair_action
                        is PlanContractRepairAction.UPDATE_EXISTING_FILE
                    )
                    repair_action = (
                        f"Update the existing file {state.work_plan_path} in the "
                        "repository now so it satisfies the declared work-plan contract, "
                        "then reread and verify it before returning `ready: true`."
                    )
                repair_context = replace(
                    context,
                    slice_summary=(
                        f"{context.slice_summary}\n\n"
                        "AUTOMATIC PLAN CONTRACT REPAIR\n"
                        f"Validator error: {detail}\n"
                        f"{repair_action}\n"
                        "Keep the approved task scope unchanged and follow the complete "
                        "parser-derived codex_contract.plan_artifact_format_contract "
                        "included in this request. Emit the "
                        "normal PLAN_READY and "
                        "single PLAN_ONLY SLICE_PLAN records; do not request user input."
                    ),
                )
                repaired_state, repaired_history = self._run_codex(
                    state, repair_context, history
                )
                return repaired_state, repaired_history, True
            state = state.await_policy_gate(
                reason=GateReason.STOP_REQUEST,
                detail=(
                    "PLAN-CONTRACT-INVALID | "
                    f"{failure_kind.value if failure_kind is not None else 'untyped'} | "
                    "The work plan is not safe to hand to "
                    f"reviewers or implementation: {detail}"
                ),
            )
            self.driver.checkpoint(state, history)
            return state, history, True
        return state, history, False

    def _collect_review_dispatch_changes(
        self,
        start_commit: str,
    ) -> WorkflowChanges:
        return self.driver.collect_changes(start_commit)

    def _collect_review_dispatch_attestation(
        self,
        changes: WorkflowChanges,
        history: WorkflowHistory,
        context: WorkflowContext,
        unit: WorkUnitRecord,
        is_plan_review: bool,
    ) -> tuple[ValidationAttestation, WorkflowHistory]:
        return self._attestation(
            changes,
            history,
            context,
            unit.slice_id,
            plan_contract=(
                (is_plan_review and unit.kind is WorkUnitKind.PLAN)
                or context.plan_only
            ),
        )

    def _select_review_evidence(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
        reviewer: AgentRole,
        unit: WorkUnitRecord,
        changes: WorkflowChanges,
        is_plan_review: bool,
        is_final_review: bool,
    ) -> tuple[EvidenceKind, str]:
        if is_final_review:
            evidence_kind = EvidenceKind.FULL_BRANCH
            review_diff = changes.full_diff
        else:
            evidence_kind = EvidenceKind.FULL_SLICE
            review_diff = changes.full_diff
        return evidence_kind, review_diff

    def _build_review_dispatch_packet(
        self,
        context: WorkflowContext,
        history: WorkflowHistory,
        unit: WorkUnitRecord,
        changes: WorkflowChanges,
        review_diff: str,
        attestation: ValidationAttestation,
        start_fingerprint: str,
        packet_purpose: str,
    ) -> ReviewPacket:
        packet_paths = tuple(
            path
            for path in changes.paths
            if path != context.audit_report_path
            and not path.startswith(".orchestrator/")
            and not path.startswith("docs/internal/slice-")
        )
        excluded_packet_paths = tuple(
            path for path in changes.paths if path not in packet_paths
        )
        return build_review_packet(
            purpose=packet_purpose,
            fingerprint=changes.fingerprint,
            start_fingerprint=start_fingerprint,
            paths=packet_paths,
            review_diff=exclude_review_diff_paths(
                review_diff, excluded_packet_paths
            ),
            plan_text=context.approved_plan_text,
            slice_id=unit.slice_id,
            attestation=attestation,
            findings=history.findings,
            affected_finding_ids=(
                unit.open_findings
                if packet_purpose == "correction"
                else ()
            ),
            binary_metadata=tuple(
                item for item in changes.binary_metadata if item.path in packet_paths
            ),
        )

    @staticmethod
    def _build_native_review_request(
        *,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
        contract: StepContract,
        changes: WorkflowChanges,
        evidence_kind: EvidenceKind,
        review_diff: str,
        review_packet: ReviewPacket | None,
        expected_test_files: tuple[str, ...],
        is_plan_review: bool,
        final_review_pending_count: int | None = None,
        known_open_findings: tuple[FindingRecord, ...] | None = None,
        correction_findings: tuple[FindingRecord, ...] | None = None,
    ) -> workflow_requests.NativeReviewRequestBundle:
        return workflow_requests.native_review_request(
            state=state,
            context=context,
            history=history,
            contract=contract,
            changes=changes,
            evidence_kind=evidence_kind,
            review_diff=review_diff,
            review_packet=review_packet,
            expected_test_files=(expected_test_files if not is_plan_review else ()),
            execution_error=WorkflowExecutionError,
            full_branch_evidence_kind=EvidenceKind.FULL_BRANCH,
            final_review_pending_count=final_review_pending_count,
            known_open_findings=known_open_findings,
            correction_findings=correction_findings,
        )

    def _dispatch_native_review(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
        reviewer: AgentRole,
        unit: WorkUnitRecord,
        contract: StepContract,
        changes: WorkflowChanges,
        evidence_kind: EvidenceKind,
        review_diff: str,
        review_packet: ReviewPacket | None,
        expected_test_files: tuple[str, ...],
        is_plan_review: bool,
        is_final_review: bool,
        review_round: int,
        request_findings: tuple[FindingRecord, ...],
        finding_ledger: tuple[FindingRecord, ...],
        final_review_pending_count: int | None = None,
    ) -> tuple[WorkflowState, WorkflowHistory]:
        request_history = replace(history, findings=request_findings)
        build_request = partial(
            self._build_native_review_request,
            state=state,
            context=context,
            history=request_history,
            changes=changes,
            evidence_kind=evidence_kind,
            review_diff=review_diff,
            review_packet=review_packet,
            expected_test_files=expected_test_files,
            is_plan_review=is_plan_review,
            final_review_pending_count=final_review_pending_count,
            known_open_findings=project_open_set(finding_ledger).findings,
            correction_findings=(
                request_findings
                if _uses_correction_finding_authority(state)
                else None
            ),
        )
        native_request = build_request(contract=contract)
        invocation = ReviewerInvocation(
            work_unit_id=unit.work_unit_id,
            step=state.current_step,
            reviewer=reviewer,
            round_number=review_round,
            request_sequence=unit.request_sequence,
            evidence_kind=evidence_kind,
            fingerprint=changes.fingerprint,
            paths=(
                review_packet.manifest.paths
                if review_packet is not None
                else changes.paths
            ),
            prompt="",
            review_packet=review_packet,
            native_request=native_request,
            previous_findings=request_findings,
        )
        native_output = (
            self.driver.recover_pending_native_reviewer(
                invocation, contract, request_history
            )
            if native_request is not None
            else None
        )
        if native_output is not None and not isinstance(
            native_output, NativeAgentReviewOutput
        ):
            raise WorkflowExecutionError(
                "native reviewer recovery returned an invalid result contract"
            )
        finding_ledger = self._refresh_review_finding_ledger(
            state, history, finding_ledger
        )
        if native_output is None and native_request is not None:
            # Correction requests were record-bound before their recovery probe.
            # Other still-unsent requests now refresh the complete ledger while
            # retaining their compact offered subset.
            history = self._rebind_unsent_request_history(state, history)
            finding_ledger = (
                finding_ledger
                if _uses_correction_finding_authority(state)
                else history.findings
            )
            authoritative_contract = replace(
                contract,
                existing_finding_ids=sorted_finding_ids(
                    item.finding_id for item in finding_ledger
                ),
            )
            native_request = build_request(contract=authoritative_contract)
            invocation = replace(invocation, native_request=native_request)
        output: NativeAgentReviewOutput | None = native_output
        if output is None:
            state, output = self._invoke_role(
                state,
                history,
                context,
                reviewer,
                lambda: self.driver.invoke_reviewer(invocation),
            )
        if output is None:
            return state, history
        if not isinstance(output, NativeAgentReviewOutput):
            raise WorkflowContractError("Claude returned a non-native review result")
        result = output.result
        self._persist_structured(
            self.driver.persist_native_review_contract,
            output,
            changes.fingerprint,
            review_round,
            invocation.request_sequence,
            (
                output.recovered_finding_comparison.offered_findings
                if output.recovered_finding_comparison is not None
                else request_findings
            ),
        )
        request_result = result
        requested_findings = request_findings
        request_findings, history, result = self._prepare_recovered_review_merge(
            output,
            result,
            request_findings,
            history,
            finding_ledger,
            is_final_review=is_final_review,
        )
        if request_findings != history.findings:
            result = replace(
                result,
                findings=self._merge_review_request_subset(
                    history.findings,
                    request_findings,
                    result.findings,
                    review_type=(
                        "final review" if is_final_review else "slice review"
                    ),
                ),
            )
        requested_ids = tuple(item.finding_id for item in requested_findings)
        new_ids = tuple(
            item.finding_id
            for item in request_result.findings
            if item.finding_id not in frozenset(requested_ids)
        )
        progress_ids = (
            project_finding_transition_ids(
                requested_findings, request_result.findings
            )
            if is_final_review
            else ()
        )
        return self._apply_review_result(
            state=state,
            context=context,
            history=history,
            reviewer=reviewer,
            result=result,
            fingerprint=changes.fingerprint,
            round_number=review_round,
            is_plan_review=is_plan_review,
            is_final_review=is_final_review,
            finding_ledger=finding_ledger,
            user_gate_paths=changes.user_gate_paths,
            final_review_requested_ids=(requested_ids if is_final_review else None),
            final_review_new_finding_ids=new_ids,
            final_review_progress_ids=progress_ids,
        )

    def _prepare_recovered_review_merge(
        self,
        output: NativeAgentReviewOutput,
        result: ContractResult,
        request_findings: tuple[FindingRecord, ...],
        history: WorkflowHistory,
        finding_ledger: tuple[FindingRecord, ...],
        *,
        is_final_review: bool,
    ) -> tuple[
        tuple[FindingRecord, ...],
        WorkflowHistory,
        ContractResult,
    ]:
        """Consume recovery-only timing authority outside dispatch inventory."""

        recovered = output.recovered_finding_comparison
        if recovered is None:
            return request_findings, history, result
        offered = (
            output.context.previous_findings
            if output.context is not None
            else recovered.offered_findings
        )
        result = replace(
            result,
            findings=self._merge_review_request_subset(
                finding_ledger,
                offered,
                result.findings,
                review_type=(
                    "final review" if is_final_review else "slice review"
                ),
                recovered_comparison=recovered,
            ),
        )
        return finding_ledger, replace(history, findings=finding_ledger), result

    def _review_test_context(
        self,
        context: WorkflowContext,
        changes: WorkflowChanges,
    ) -> tuple[TestChangeEvidence | None, tuple[str, ...]]:
        detected = (
            self.driver.detect_test_changes(changes, context.test_path_patterns)
            if context.dynamic_test_scope
            else None
        )
        expected = (
            detected.paths
            if detected is not None
            else ()
            if context.dynamic_test_scope
            else context.expected_test_files
        )
        return detected, expected

    def _apply_review_change_boundary(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
        changes: WorkflowChanges,
    ) -> tuple[WorkflowState, bool]:
        unexpected = self._validate_change_boundary(
            state, changes, state.current_work_unit.kind, context=context
        )
        if not unexpected:
            return state, False
        state = state.await_user_gate(
            reason=GateReason.UNEXPECTED_FILE,
            detail=(
                f"{UNEXPECTED_PATH_RULE_ID} | canonical changes contain paths "
                f"outside the persisted Slice scope: {', '.join(unexpected)}"
            ),
            fingerprint=changes.fingerprint,
            paths=unexpected,
        )
        self.driver.checkpoint(state, history)
        return state, True

    def _run_review(
        self, state: WorkflowState, context: WorkflowContext,
        history: WorkflowHistory, reviewer: AgentRole,
    ) -> tuple[WorkflowState, WorkflowHistory]:
        unit = state.current_work_unit
        if reviewer is not AgentRole.CLAUDE:
            raise WorkflowExecutionError("only Claude may execute review steps")
        is_plan_review = state.current_step is WorkflowStep.CLAUDE_PLAN_REVIEW
        is_final_review = state.current_step is WorkflowStep.CLAUDE_FINAL_REVIEW
        history = self._bind_correction_request_history(state, history)
        start_commit = state.branch_review_base_commit if is_final_review else (
            state.current_slice.start_commit or state.branch_base
        )
        try:
            changes = self._collect_review_dispatch_changes(start_commit)
        except NoWorkflowChangesError as exc:
            state = state.await_policy_gate(
                reason=GateReason.STOP_REQUEST,
                detail=f"NO-IMPLEMENTATION-CHANGES | {exc}",
            )
            self.driver.checkpoint(state, history)
            return state, history
        state, halted = self._apply_review_change_boundary(
            state, context, history, changes,
        )
        if halted:
            return state, history
        state, test_changes_approved, halted = self._apply_test_change_gate(
            state, context, changes
        )
        if halted:
            self.driver.checkpoint(state, history)
            return state, history
        detected_test_changes, expected_test_files = self._review_test_context(
            context, changes
        )
        try:
            attestation, history = self._collect_review_dispatch_attestation(
                changes,
                history,
                context,
                unit,
                is_plan_review,
            )
        except ValidationExecutionError as exc:
            state = state.await_policy_gate(
                reason=GateReason.STOP_REQUEST,
                detail=f"{VALIDATION_UNAVAILABLE_RULE_ID} | {exc}",
            )
            self.driver.checkpoint(state, history)
            return state, history
        # Project the appended attestation before invoking the reviewer; the
        # idempotent checkpoint protects record-ahead recovery.
        self.driver.checkpoint(state, history)
        if not attestation.complete:
            raise WorkflowExecutionError(
                "validation attestation is incomplete and cannot be overridden"
            )
        review_round = _review_round_number(unit, history, reviewer)
        request_findings = _project_correction_request_findings(
            state, history.findings
        )
        finding_ledger = history.findings
        final_review_pending_count: int | None = None
        contract = StepContract(
            name=f"work-unit-{unit.work_unit_id}-{state.current_step.value}",
            reviewer=reviewer,
            approval_marker=(
                ApprovalMarker.FINAL_REVIEW
                if is_final_review
                else ApprovalMarker.PLAN
                if is_plan_review
                else ApprovalMarker.SLICE
            ),
            slice_id=(
                "DISCOVERY" if is_final_review else f"{unit.slice_id:02d}"
            ),
            round_number=review_round,
            request_sequence=unit.request_sequence,
            review_fingerprint=changes.fingerprint,
            validation_attestation=attestation,
            expected_test_files=(expected_test_files if not is_plan_review else ()),
            test_changes_approved=test_changes_approved,
            red_state_followup_slice=context.red_state_followup_slice,
            existing_finding_ids=sorted_finding_ids(
                finding.finding_id for finding in finding_ledger
            ),
            allow_new_findings=not _uses_correction_finding_authority(state),
        )
        review_packet: ReviewPacket | None = None
        evidence_kind, review_diff = self._select_review_evidence(
            state,
            context,
            history,
            reviewer,
            unit,
            changes,
            is_plan_review,
            is_final_review,
        )
        if (
            not is_plan_review
            and not is_final_review
            and context.approved_plan_text is not None
        ):
            start_fingerprint = state.current_slice.start_fingerprint
            if start_fingerprint is None:
                raise WorkflowExecutionError(
                    "Slice review packet requires a persisted start fingerprint"
                )
            packet_purpose = "slice"
            try:
                review_packet = self._build_review_dispatch_packet(
                    context,
                    history,
                    unit,
                    changes,
                    review_diff,
                    attestation,
                    start_fingerprint,
                    packet_purpose,
                )
            except ReviewPacketError as exc:
                raise WorkflowExecutionError(
                    f"canonical review packet could not be built: {exc}"
                ) from exc
            self._persist_structured(self.driver.persist_review_packet, review_packet)
            history = replace(history, active_review_packet=review_packet)
            self.driver.checkpoint(state, history)
        return self._dispatch_native_review(
            state,
            context,
            history,
            reviewer,
            unit,
            contract,
            changes,
            evidence_kind,
            review_diff,
            review_packet,
            expected_test_files,
            is_plan_review,
            is_final_review,
            review_round,
            request_findings,
            finding_ledger,
            final_review_pending_count,
        )

    def _apply_review_result(
        self,
        *,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
        reviewer: AgentRole,
        result: ContractResult,
        fingerprint: str,
        round_number: int,
        is_plan_review: bool,
        is_final_review: bool,
        finding_ledger: tuple[FindingRecord, ...],
        user_gate_paths: tuple[str, ...] = (),
        final_review_requested_ids: tuple[str, ...] | None = None,
        final_review_new_finding_ids: tuple[str, ...] = (),
        final_review_progress_ids: tuple[str, ...] = (),
    ) -> tuple[WorkflowState, WorkflowHistory]:
        """Mirror one durable verdict and perform its deterministic transition."""
        unit = state.current_work_unit
        history = self._record_review(
            history,
            unit.slice_id,
            round_number,
            result,
            fingerprint,
            track_slice_approval=(
                not is_plan_review or unit.kind is WorkUnitKind.PLAN
            ),
            is_final_review=is_final_review,
            allowed_finding_origins=allowed_review_finding_origins(
                finding_ledger,
                current_slice_id=unit.slice_id,
                is_final_review=is_final_review,
            ),
        )
        if result.stopped:
            if result.stop_request is None:
                raise WorkflowExecutionError(
                    f"{reviewer.value} stop has no structured stop request"
                )
            state = self._halt_for_stop_request(state, context, result.stop_request)
            self.driver.checkpoint(state, history)
            return state, history
        if result.delivery_kind == "final_review_completed":
            if not is_final_review:
                raise WorkflowExecutionError(
                    "FINAL_REVIEW_COMPLETED is outside the final review step"
                )
            state = state.complete_current_work_unit()
            self.driver.checkpoint(state, history)
            return state, history
        if is_final_review:
            raise WorkflowExecutionError(
                "final review requires FINAL_REVIEW_COMPLETED"
            )

        if result.approval is True:
            if is_plan_review and unit.kind is WorkUnitKind.PLAN:
                if context.plan_gate:
                    state = state.await_user_gate(
                        reason=GateReason.PLAN_APPROVAL,
                        detail=(
                            "PLAN-APPROVAL | Claude approved the bound plan; explicit "
                            "user approval is required before execution"
                        ),
                        fingerprint=fingerprint,
                        paths=user_gate_paths,
                        gate_step=(
                            WorkflowStep.SLICE_COMMIT
                            if context.plan_only
                            else WorkflowStep.COMPLETED
                        ),
                    )
                elif context.plan_only:
                    state = state.with_current_step(WorkflowStep.SLICE_COMMIT)
                else:
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
                state = state.with_current_step(WorkflowStep.SLICE_COMMIT)
        else:
            correction_progress = self._slice_review_progress(
                state,
                result,
                finding_ledger=finding_ledger,
                round_number=round_number,
                is_final_review=is_final_review,
            )
            own_ids = tuple(item.finding_id for item in result.own_open_findings)
            return_step = (
                WorkflowStep.CODEX_PLAN_REVISION
                if unit.kind is WorkUnitKind.PLAN
                else WorkflowStep.CODEX_CORRECTION
            )
            state = state.record_review_denial(
                reviewer=Reviewer.CLAUDE,
                open_findings=own_ids,
                return_step=return_step,
                progress_made=correction_progress,
            )
        self.driver.checkpoint(state, history)
        return state, history

    def _slice_review_progress(
        self,
        state: WorkflowState,
        result: ContractResult,
        *,
        finding_ledger: tuple[FindingRecord, ...],
        round_number: int,
        is_final_review: bool,
    ) -> bool:
        """Use E5 for Slice convergence and the plan activity rule elsewhere."""

        if is_final_review:
            return False
        if state.current_work_unit.kind is WorkUnitKind.SLICE:
            evaluation = self.driver.evaluate_slice_finding_convergence(
                state,
                round_number=round_number,
            )
            log = logger.info if evaluation.progress_made else logger.warning
            log(
                "Slice %02d round %s %s: progress=%s; opened=%s closed=%s "
                "remediated=%s; reason=%s",
                state.current_slice_id,
                round_number,
                evaluation.phase.value,
                evaluation.progress_made,
                ",".join(evaluation.newly_opened_finding_ids) or "none",
                ",".join(evaluation.closed_local_finding_ids) or "none",
                ",".join(evaluation.attested_remediation_finding_ids) or "none",
                evaluation.reason,
            )
            return evaluation.progress_made
        prior_ids = frozenset(item.finding_id for item in finding_ledger)
        newly_opened = tuple(
            finding_id
            for finding_id in project_open_set(result.findings).finding_ids
            if finding_id not in prior_ids
        )
        transitioned = project_finding_transition_ids(
            finding_ledger, result.findings
        )
        return bool(newly_opened or transitioned)

    @staticmethod
    def _merge_review_request_subset(
        authoritative: tuple[FindingRecord, ...],
        offered: tuple[FindingRecord, ...],
        returned: tuple[FindingRecord, ...],
        *,
        review_type: str,
        recovered_comparison: RecoveredFindingComparison | None = None,
    ) -> tuple[FindingRecord, ...]:
        """Validate one review at request time, then confirm record-ahead state."""

        try:
            if recovered_comparison is not None:
                request_merge = merge_review_request_result(
                    recovered_comparison.request_findings,
                    recovered_comparison.offered_findings,
                    returned,
                    review_type=review_type,
                )
                return merge_review_request_result(
                    authoritative,
                    request_merge.request_bound,
                    request_merge.request_bound,
                    review_type=review_type,
                ).complete_ledger
            return merge_review_request_result(
                authoritative,
                offered,
                returned,
                review_type=review_type,
            ).complete_ledger
        except ValueError as exc:
            diagnostic = (
                ""
                if recovered_comparison is None
                else (
                    "; request-time offered/returned measured-at: "
                    f"{recovered_comparison.request_position}; recovery-time "
                    "authoritative compared-at: "
                    f"{recovered_comparison.recovery_position}"
                )
            )
            raise WorkflowExecutionError(f"{exc}{diagnostic}") from exc

    def _invoke_role(
        self,
        state: WorkflowState,
        history: WorkflowHistory,
        context: WorkflowContext,
        role: AgentRole,
        invoke: Callable[
            [], str | NativeAgentCodexOutput | NativeAgentReviewOutput
        ],
    ) -> tuple[
        WorkflowState,
        str | NativeAgentCodexOutput | NativeAgentReviewOutput | None,
    ]:
        """Invoke one fixed role, persisting every failure before any optional wait."""
        while True:
            try:
                output = invoke()
                driver_state = self.driver.active_state
                if isinstance(driver_state, WorkflowState) and driver_state.run_id == state.run_id:
                    state = replace(state, bootstrap_checks=driver_state.bootstrap_checks)
                return state, output
            except FinalReviewPreflightDenied as error:
                fingerprint = error.fingerprint or hashlib.sha256(
                    str(error).encode("utf-8")
                ).hexdigest()
                code = error.result.error_code or "FINAL-REVIEW-PREFLIGHT"
                detail = str(error)
                affected_paths = error.result.affected_paths
                state = state.await_bootstrap_resume(
                    detail=f"{code} | {detail}",
                    fingerprint=fingerprint,
                    paths=affected_paths,
                )
                self.driver.checkpoint(state, history)
                return state, None
            except ProviderInputBudgetExceeded as error:
                driver_state = self.driver.active_state
                if isinstance(driver_state, WorkflowState) and driver_state.run_id == state.run_id:
                    state = replace(state, bootstrap_checks=driver_state.bootstrap_checks)
                state = state.complete_provider_input_boundary_verdict()
                self._persist_structured(self.driver.persist_gate_transition, state)
                self.driver.checkpoint(state, history)
                return state, None
            except ProviderRequestRoundRequired as changed:
                logger.warning(
                    "Provider request composition changed with unchanged Slice binding; "
                    "opening a new request sequence: work_unit=%s request=%s->%s "
                    "binding_fingerprint=%s previous_input=%s current_input=%s",
                    state.current_work_unit_id,
                    state.current_work_unit.request_sequence,
                    state.current_work_unit.request_sequence + 1,
                    changed.binding_fingerprint,
                    changed.previous_input_digest,
                    changed.current_input_digest,
                )
                state = state.start_recomposed_request()
                self.driver.checkpoint(state, history)
                return state, None
            except AgentInvocationError as error:
                disposition_limit_failure = (
                    find_native_review_disposition_limit_error(error)
                )
                native_response_retry_allowed = not (
                    error.native_implementer_response_retryable
                    and self._native_implementer_retry_crosses_scope(
                        state, context
                    )
                )
                state, failure = self._persist_invocation_failure(
                    state,
                    history,
                    context,
                    role,
                    error,
                    disposition_limit_failure is not None,
                    native_response_retry_allowed,
                )
                if not failure.automatic_resume:
                    return state, None
                assert failure.resume_at_utc is not None
                resume_at = datetime.fromisoformat(
                    failure.resume_at_utc.replace("Z", "+00:00")
                )
                if failure.failure_kind is AgentFailureKind.QUOTA:
                    wait_until_quota_resume(
                        role=role.value,
                        task_label=state.task_file,
                        work_unit_id=state.current_work_unit_id,
                        reset_at_utc=error.quota_reset.reset_at_utc,
                        resume_at_utc=resume_at,
                        heartbeat_interval_seconds=(
                            context.quota_wait_policy.heartbeat_interval_seconds
                        ),
                        now_fn=self.now_fn,
                        sleep_fn=self.sleep_fn,
                        heartbeat_fn=self.heartbeat_fn,
                    )
                else:
                    wait_until_transient_retry(
                        role=role.value,
                        task_label=state.task_file,
                        work_unit_id=state.current_work_unit_id,
                        resume_at_utc=resume_at,
                        now_fn=self.now_fn,
                        sleep_fn=self.sleep_fn,
                        heartbeat_fn=self.heartbeat_fn,
                    )
                state = state.resume_after_invocation_halt()
                self._persist_structured(self.driver.persist_gate_transition, state)
                state, halted = self._apply_pre_agent_policy_gates(state, context)
                if not halted:
                    state, halted = self._revalidate_waiting_diff(
                        state, failure, context
                    )
                self.driver.checkpoint(state, history)
                if halted:
                    return state, None
                if disposition_limit_failure is not None:
                    logger.warning(
                        "Rejected over-budget review result; recomposing a "
                        "smaller request: work_unit=%s request=%s actual=%s maximum=%s",
                        state.current_work_unit_id,
                        state.current_work_unit.request_sequence,
                        disposition_limit_failure.actual_items,
                        disposition_limit_failure.maximum_items,
                    )
                    state = state.start_recomposed_request()
                    self.driver.checkpoint(state, history)
                    return state, None
                native_rejection = (
                    failure.native_review_rejection
                    or failure.native_implementer_rejection
                )
                if native_rejection is not None:
                    role_label = (
                        "review"
                        if failure.native_review_rejection is not None
                        else "implementer"
                    )
                    logger.warning(
                        "Rejected native %s response; recomposing the retry "
                        "request with corrective feedback: work_unit=%s "
                        "request=%s->%s rejection=%s",
                        role_label,
                        state.current_work_unit_id,
                        state.current_work_unit.request_sequence,
                        state.current_work_unit.request_sequence + 1,
                        native_rejection,
                    )
                    state = state.start_recomposed_request()
                    self.driver.checkpoint(state, history)
                    return state, None

    def _persist_invocation_failure(
        self,
        state: WorkflowState,
        history: WorkflowHistory,
        context: WorkflowContext,
        role: AgentRole,
        error: AgentInvocationError,
        disposition_limit_failure: bool = False,
        native_response_retry_allowed: bool = True,
    ) -> tuple[WorkflowState, InvocationFailureRecord]:
        return self._failure_recording.persist_invocation_failure(
            state,
            history,
            context,
            role,
            error,
            disposition_limit_failure,
            native_response_retry_allowed,
        )

    def _native_implementer_retry_crosses_scope(
        self, state: WorkflowState, context: WorkflowContext
    ) -> bool:
        """Fail closed when an invalid response also left out-of-scope work."""

        start_commit = self._change_start_commit(state)
        if start_commit is None:
            return True
        try:
            changes = self.driver.collect_changes(start_commit)
            unexpected = self._validate_change_boundary(
                state,
                changes,
                state.current_work_unit.kind,
                context=context,
            )
        except Exception:
            return True
        if unexpected:
            logger.warning(
                "Native implementer response retry suppressed by out-of-scope "
                "work: work_unit=%s paths=%s",
                state.current_work_unit_id,
                ",".join(unexpected),
            )
            return True
        return False

    def _current_invocation_fingerprint(
        self, state: WorkflowState
    ) -> str | None:
        if state.current_work_unit.kind is WorkUnitKind.PLAN:
            return None
        start_commit = self._change_start_commit(state)
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
        context: WorkflowContext,
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
        start_commit = self._change_start_commit(state)
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
            state,
            changes,
            state.current_work_unit.kind,
            context=context,
        )
        acknowledged = quota_resume_diff_acknowledgement(
            failure.invocation_id, changes.fingerprint
        ) in state.current_work_unit.completed_side_effects
        fingerprint_changed = changes.fingerprint != failure.diff_fingerprint
        if (unexpected or fingerprint_changed) and not acknowledged:
            paths = unexpected or changes.user_gate_paths
            path_text = ", ".join(paths) or "(none)"
            halted = state.await_user_gate(
                reason=GateReason.QUOTA_RESUME_DIFF,
                detail=(
                    "QUOTA-RESUME-DIFF | repository changed while the role was waiting; "
                    f"expected {failure.diff_fingerprint}, got {changes.fingerprint}; "
                    f"paths={path_text}; continue with --resume --approve-gate "
                    "--gate-rationale '<reviewed reason>' and the unchanged --task-file"
                ),
                fingerprint=changes.fingerprint,
                paths=paths,
                resume_step=failure.step,
            )
            return halted, True
        return state, False

    def _attestation(
        self,
        changes: WorkflowChanges,
        history: WorkflowHistory,
        context: WorkflowContext,
        slice_id: int,
        *,
        plan_contract: bool = False,
    ) -> tuple[ValidationAttestation, WorkflowHistory]:
        return self._validation_evidence.attestation(
            changes,
            history,
            context,
            slice_id,
            plan_contract=plan_contract,
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
        allowed_finding_origins: tuple[str, ...] = (),
        is_final_review: bool = False,
    ) -> WorkflowHistory:
        event = ReviewAuditEvent(
            event_id=len(history.events) + 1,
            slice_id=slice_id,
            round_number=round_number,
            result=result,
            allowed_finding_origins=allowed_finding_origins,
            is_final_review=is_final_review,
        )
        updates: dict[str, object] = {
            "findings": result.findings,
            "events": (*history.events, event),
        }
        if result.reviewer is not AgentRole.CLAUDE:
            raise WorkflowExecutionError("review history accepts only Claude results")
        if track_slice_approval:
            updates["last_claude_fingerprint"] = fingerprint
            updates["latest_claude_review"] = result
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
        if (
            context.plan_only
            and state.current_work_unit.kind is WorkUnitKind.PLAN
            and not state.current_slice.scope_paths
        ):
            planned = next(
                (
                    item
                    for item in state.planned_slices
                    if item.slice_id == state.current_slice_id
                ),
                None,
            )
            if planned is None:
                raise WorkflowExecutionError(
                    "PLAN_ONLY commit requires the persisted plan-artifact Slice"
                )
            boundary_changes = self.driver.collect_changes(start_commit)
            state = state.bind_current_slice_git_boundary(
                start_commit=start_commit,
                scope_paths=planned.scope_paths,
                start_fingerprint=boundary_changes.fingerprint,
            )
            self._bind_driver_work_unit(state)
            self.driver.checkpoint(state, history)
        changes = self.driver.collect_changes(start_commit)
        unexpected = self._validate_change_boundary(
            state, changes, state.current_work_unit.kind
        )
        if unexpected:
            state = state.await_user_gate(
                reason=GateReason.UNEXPECTED_FILE,
                detail=(
                    f"{UNEXPECTED_PATH_RULE_ID} | canonical changes contain paths "
                    f"outside the persisted Slice scope: {', '.join(unexpected)}"
                ),
                fingerprint=changes.fingerprint,
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
        validation_authorized = attestation is not None and (
            attestation.passed
            or (
                attestation.complete
                and context.red_state_followup_slice is not None
            )
        )
        reviews_current = (
            validation_authorized
            and claude is not None
            and claude.approval is True
            and claude.validation == attestation
        )
        if not reviews_current:
            review_step = (
                WorkflowStep.CLAUDE_PLAN_REVIEW
                if state.current_work_unit.kind is WorkUnitKind.PLAN
                else WorkflowStep.CLAUDE_SLICE_REVIEW
            )
            state = state.with_current_step(review_step)
            history = replace(
                history,
                last_claude_fingerprint=None,
                latest_claude_review=None,
            )
            self.driver.checkpoint(state, history)
            return WorkflowRunResult(state, history)
        if any(
            finding.finding_class is FindingClass.BLOCKER
            for finding in project_open_set(history.findings).findings
        ):
            raise WorkflowExecutionError("slice commit requires no open blockers")
        gate_paths = changes.user_gate_paths
        if (
            context.manual_slice_gate
            and not context.plan_only
            and not state.current_work_unit.has_gate_approval(
            GateReason.MANUAL_SLICE, changes.fingerprint, gate_paths
            )
        ):
            state = state.await_user_gate(
                reason=GateReason.MANUAL_SLICE,
                detail="manual slice approval is required before commit",
                fingerprint=changes.fingerprint,
                paths=gate_paths,
            )
            self.driver.checkpoint(state, history)
            return WorkflowRunResult(state, history)
        try:
            commit_ref = self.driver.commit_slice(
                WorkflowCommitRequest(
                    slice_id=state.current_slice_id,
                    fingerprint=changes.fingerprint,
                    attestation=attestation,
                    claude_review=claude,
                    findings=history.findings,
                    red_state_followup_slice=context.red_state_followup_slice,
                )
            )
        except WorkflowCommitApprovalRequired as exc:
            state = state.await_user_gate(
                reason=GateReason.UNEXPECTED_FILE,
                detail=exc.detail,
                fingerprint=changes.fingerprint,
                paths=exc.paths,
                resume_step=WorkflowStep.SLICE_COMMIT,
            )
            self.driver.checkpoint(state, history)
            return WorkflowRunResult(state, history)
        if not isinstance(commit_ref, str) or not commit_ref.strip():
            raise WorkflowExecutionError("slice commit did not return a commit reference")
        state = state.complete_current_slice(commit_ref=commit_ref)
        if context.plan_only and state.current_work_unit.kind is WorkUnitKind.PLAN:
            state = state.bind_completed_plan_commit(commit_ref=commit_ref)
        self.driver.checkpoint(state, history)
        return WorkflowRunResult(state, history, commit_ref)

    def _apply_anchor_gate(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> tuple[WorkflowState, WorkflowHistory, bool]:
        if state.current_work_unit.kind in {
            WorkUnitKind.PLAN,
            WorkUnitKind.FINAL_REVIEW,
        }:
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
        return state, False

    def _halt_for_stop_request(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        stop_request: StopRequest,
    ) -> WorkflowState:
        discovery_output_limit = (
            state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
            and stop_request.rule_id == DISCOVERY_OUTPUT_LIMIT_RULE_ID
        )
        if (
            stop_request.rule_id not in context.known_stop_rule_ids
            and not discovery_output_limit
        ):
            raise WorkflowExecutionError(
                f"STOP_REQUESTED references unknown rule {stop_request.rule_id!r}"
            )
        try:
            validate_builtin_stop_content(
                stop_request.rule_id,
                stop_request.rationale,
                stop_request.remediation_paths,
            )
        except ValueError as exc:
            raise WorkflowExecutionError(
                f"STOP_REQUESTED has invalid content for "
                f"{stop_request.rule_id!r}: {exc}"
            ) from exc
        if (
            stop_request.rule_id == SCOPE_EXTENSION_REQUESTED_RULE_ID
            and stop_request.remediation_paths
            and state.current_work_unit.kind is WorkUnitKind.SLICE
        ):
            start_commit = self._change_start_commit(state)
            # A running Slice is state-invalid without both boundary values; use
            # the already validated boundary directly so this gate measures the
            # same full Slice delta as persistence and later review.
            assert start_commit is not None
            assert state.current_slice.start_fingerprint is not None
            try:
                fingerprint = self.driver.collect_changes(start_commit).fingerprint
            except NoWorkflowChangesError:
                fingerprint = state.current_slice.start_fingerprint
            return state.await_user_gate(
                reason=GateReason.STOP_REQUEST,
                detail=f"{stop_request.rule_id} | {stop_request.rationale}",
                fingerprint=fingerprint,
                paths=stop_request.remediation_paths,
                resume_step=state.current_step,
            )
        return state.await_policy_gate(
            reason=GateReason.STOP_REQUEST,
            detail=f"{stop_request.rule_id} | {stop_request.rationale}",
        )

    def _expand_approved_remediation_scope(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        stop_request: StopRequest,
    ) -> ApprovedScopeExtension | None:
        """Approve one exact, policy-safe scope request or fail closed to a gate."""
        if (
            stop_request.rule_id
            not in {
                VALIDATION_UNAVAILABLE_RULE_ID,
                SCOPE_EXTENSION_REQUESTED_RULE_ID,
            }
            or not stop_request.remediation_paths
            or state.current_work_unit.kind is not WorkUnitKind.SLICE
        ):
            return None
        requested = set(stop_request.remediation_paths)
        protected = self._protected_scope_extension_paths(state)
        if requested.intersection(protected):
            return None

        if stop_request.rule_id == VALIDATION_UNAVAILABLE_RULE_ID:
            completed_ids = {
                item.slice_id
                for item in state.slices
                if item.status is SliceStatus.COMPLETED
            }
            eligible = {
                path
                for planned in state.planned_slices
                if planned.slice_id in completed_ids
                for path in planned.scope_paths
            }
            if not requested.issubset(eligible):
                return None
        else:
            additions = requested.difference(state.current_slice.scope_paths)
            later_owned_paths = {
                path
                for planned in state.planned_slices
                if planned.slice_id > state.current_slice_id
                for path in planned.scope_paths
            }
            start_commit = state.current_slice.start_commit
            if start_commit is None:
                return None
            for path in sorted(additions):
                path_class = classify_path(path, context.path_classes).path_class
                if path_class is PathClass.DOCUMENTATION:
                    continue
                if path_class is PathClass.TEST:
                    if not self.driver.path_exists_at_commit(start_commit, path):
                        continue
                    if context.scope_extension_gate or not context.test_changes_approved:
                        return None
                    continue
                if path_class is PathClass.PRODUCTIVE:
                    if context.scope_extension_gate and path in later_owned_paths:
                        return None
                    continue
                return None

        additions = tuple(sorted(requested.difference(state.current_slice.scope_paths)))
        if not additions:
            retry_key = self._authorized_remediation_reprompt_key(requested)
            if state.current_work_unit.has_completed_side_effect(retry_key) or (
                state.current_work_unit.request_sequence
                > state.current_work_unit.round_number
                and requested.intersection(self._approved_scope_additions(state))
            ):
                return None
            return ApprovedScopeExtension(
                state.mark_side_effect_completed(retry_key),
                (),
            )
        classified = tuple(
            classify_path(path, context.path_classes) for path in additions
        )
        return ApprovedScopeExtension(
            state.approve_current_slice_scope_extension(additions),
            classified,
        )

    @staticmethod
    def _authorized_remediation_reprompt_key(paths: Iterable[str]) -> str:
        digest = hashlib.sha256("\0".join(sorted(paths)).encode("utf-8")).hexdigest()
        return f"authorized-remediation-reprompt:{digest}"

    @staticmethod
    def _approved_scope_additions(state: WorkflowState) -> frozenset[str]:
        planned = next(
            (
                item for item in state.planned_slices
                if item.slice_id == state.current_slice_id
            ),
            None,
        )
        if planned is None:
            return frozenset()
        return frozenset(state.current_slice.scope_paths).difference(planned.scope_paths)

    @staticmethod
    def _protected_scope_extension_paths(state: WorkflowState) -> frozenset[str]:
        protected = {
            path
            for path in (state.work_plan_path, state.audit_report_path)
            if path is not None
        }
        if state.audit_report_path is not None:
            protected.update(
                managed_slice_document_path(
                    state.audit_report_path,
                    planned.slice_id,
                    planned.summary,
                )
                for planned in state.planned_slices
            )
        return frozenset(protected)

    @staticmethod
    def _handoff_agent_sandbox_validation(
        state: WorkflowState,
        stop_request: StopRequest,
    ) -> WorkflowState | None:
        """Re-prompt once when only the agent sandbox blocked orchestrator-owned tests."""
        if (
            stop_request.rule_id != VALIDATION_UNAVAILABLE_RULE_ID
            or stop_request.remediation_paths
            or state.current_work_unit.kind is not WorkUnitKind.SLICE
            or AGENT_SANDBOX_VALIDATION_PATTERN.search(stop_request.rationale) is None
        ):
            return None
        retry_key = "agent-sandbox-validation-handoff"
        if state.current_work_unit.has_completed_side_effect(retry_key):
            return None
        return state.mark_side_effect_completed(retry_key)

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
        *,
        context: WorkflowContext | None = None,
    ) -> tuple[str, ...]:
        return workflow_validation_evidence.validate_change_boundary(
            state,
            changes,
            kind,
            context=context,
            execution_error=WorkflowExecutionError,
        )

    def _apply_test_change_gate(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        changes: WorkflowChanges,
    ) -> tuple[WorkflowState, bool, bool]:
        approved = context.test_changes_approved
        if approved:
            state = state.record_active_test_approval(None)
            self._persist_structured(self.driver.persist_gate_transition, state)
            return state, True, False
        evidence = self.driver.detect_test_changes(changes, context.test_path_patterns)
        if evidence is None:
            state = state.record_active_test_approval(None)
            self._persist_structured(self.driver.persist_gate_transition, state)
            return state, False, False
        state = state.inherit_prior_test_approval(
            evidence.fingerprint,
            evidence.paths,
        )
        self._persist_structured(self.driver.persist_gate_transition, state)
        approved = state.current_work_unit.has_gate_approval(
            GateReason.TEST_CHANGE, evidence.fingerprint, evidence.paths
        )
        if not approved:
            state = state.await_user_gate(
                    reason=GateReason.TEST_CHANGE,
                    detail="test changes require explicit approval before review",
                    fingerprint=evidence.fingerprint,
                    paths=evidence.paths,
            )
            self._persist_structured(self.driver.persist_gate_transition, state)
            return state, False, True
        state = state.record_active_test_approval(evidence.fingerprint, evidence.paths)
        self._persist_structured(self.driver.persist_gate_transition, state)
        return state, True, False

    @staticmethod
    def _change_start_commit(state: WorkflowState) -> str | None:
        if state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW:
            return state.branch_review_base_commit
        return state.current_slice.start_commit

    def _bind_driver_work_unit(self, state: WorkflowState) -> None:
        self.driver.bind_work_unit(state)

    def _bind_authoritative_request_findings(
        self, state: WorkflowState, history: WorkflowHistory
    ) -> WorkflowHistory:
        """Bind a native request to its replayed, request-specific projection."""
        binding = state.protocol_binding
        if (
            binding is None
            or binding.codex_result_transport != NATIVE_CODEX_RESULT_TRANSPORT
            or binding.claude_review_transport != NATIVE_CLAUDE_REVIEW_TRANSPORT
        ):
            return history
        findings = self.driver.authoritative_native_findings(state, history.findings)
        if not isinstance(findings, tuple) or any(
            not isinstance(item, FindingRecord) for item in findings
        ):
            raise WorkflowExecutionError(
                "authoritative finding replay returned an invalid projection"
            )
        _project_correction_request_findings(state, findings)
        return replace(history, findings=findings)

    def _bind_correction_request_history(
        self, state: WorkflowState, history: WorkflowHistory
    ) -> WorkflowHistory:
        if not _uses_correction_finding_authority(state):
            return history
        return self._bind_authoritative_request_findings(state, history)

    def _rebind_unsent_request_history(
        self, state: WorkflowState, history: WorkflowHistory
    ) -> WorkflowHistory:
        if _uses_correction_finding_authority(state):
            return history
        return self._bind_authoritative_request_findings(state, history)

    def _refresh_review_finding_ledger(
        self,
        state: WorkflowState,
        history: WorkflowHistory,
        record_bound_ledger: tuple[FindingRecord, ...],
    ) -> tuple[FindingRecord, ...]:
        if _uses_correction_finding_authority(state):
            return record_bound_ledger
        return self._authoritative_finding_ledger(state, history.findings)

    def _bind_current_open_findings(
        self, state: WorkflowState, history: WorkflowHistory
    ) -> WorkflowState:
        """Mirror the carried open ledger into each non-correction work unit."""
        binding = state.protocol_binding
        unit = state.current_work_unit
        if (
            binding is None
            or binding.mode is not ProtocolMode.STRUCTURED_V2
            or unit.kind is WorkUnitKind.PLAN
            or _uses_correction_finding_authority(state)
        ):
            return state
        # Production resume now hydrates the complete record-backed history
        # before engine entry. Keep this guard as a defense for record-ahead
        # recovery and direct engine invocations with an event-only history.
        if not history.findings and unit.open_findings:
            return state
        open_ids = project_open_set(history.findings).finding_ids
        if unit.open_findings == open_ids:
            return state
        work_units = tuple(
            replace(item, open_findings=open_ids)
            if item.work_unit_id == unit.work_unit_id
            else item
            for item in state.work_units
        )
        return replace(state, work_units=work_units)

    def _authoritative_finding_ledger(
        self,
        state: WorkflowState,
        current_findings: tuple[FindingRecord, ...],
    ) -> tuple[FindingRecord, ...]:
        """Return the complete ledger without confusing it with a request subset."""
        binding = state.protocol_binding
        if (
            binding is None
            or binding.codex_result_transport != NATIVE_CODEX_RESULT_TRANSPORT
            or binding.claude_review_transport != NATIVE_CLAUDE_REVIEW_TRANSPORT
        ):
            return current_findings
        findings = self.driver.carry_forward_native_findings(
            state, current_findings
        )
        if not isinstance(findings, tuple) or any(
            not isinstance(item, FindingRecord) for item in findings
        ):
            raise WorkflowExecutionError(
                "native finding carry-forward returned an invalid projection"
            )
        return findings

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
        boundary_label = (
            "BRANCH BASE COMMIT"
            if evidence_kind is EvidenceKind.FULL_BRANCH
            else "SLICE START COMMIT"
        )
        final_dimensions = (
            "\n\nMANDATORY FINAL-REVIEW DIMENSIONS\n"
            "architecture drift | interface consistency | dead transition states | "
            "documentation synchronization | declared requirements and acceptance criteria"
            if evidence_kind is EvidenceKind.FULL_BRANCH
            else ""
        )
        return (
            f"DISTILLED CONTEXT\n{context.distilled_context}\n\n"
            f"EVIDENCE KIND\n{evidence_kind.value}\n\n"
            f"{boundary_label}\n{changes.start_commit}\n\n"
            f"CURRENT FINGERPRINT\n{changes.fingerprint}\n\n"
            f"STRUCTURED FINDINGS\n{findings}\n\n"
            f"REVIEW DIFF\n{review_diff}{final_dimensions}"
        )

    def _fingerprint_bound_codex_scope_paths(
        self, state: WorkflowState
    ) -> tuple[str, ...]:
        """Expose only the latest exact resume-gate path approval to Codex."""
        decision = next(
            (
                item
                for item in reversed(state.current_work_unit.gate_decisions)
                if item.approved
                and item.reason
                in {GateReason.UNEXPECTED_FILE, GateReason.QUOTA_RESUME_DIFF}
                and item.resume_step is state.current_step
            ),
            None,
        )
        if decision is None:
            return ()
        start_commit = self._change_start_commit(state)
        if start_commit is None:
            return ()
        try:
            fingerprint = self.driver.collect_changes(start_commit).fingerprint
        except Exception:
            return ()
        return decision.paths if decision.fingerprint == fingerprint else ()

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
