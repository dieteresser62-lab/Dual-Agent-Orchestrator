"""Versioned native JSON review results with state-v3 domain parity.

This module is deliberately provider- and persistence-free.  It validates one
closed transport document, binds it to immutable local review context, and
converts it to the existing :class:`contracts.ContractResult` domain model.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, TypeAlias

from schema_validation import (
    SchemaDefinitionError,
    SchemaMismatch,
    check_schema,
    describe_one_of_failure,
    validate_schema_document,
)
from contracts import (
    AgentRole,
    AnchorRecord,
    ApprovalMarker,
    ContractResult,
    FindingClass,
    FindingOccurrence,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    PlannedSlice,
    ReviewEvidence,
    SOURCE_FINDING_ID_PATTERN,
    StopRequest,
    ValidationAttestation,
    ValidationStatus,
)
from finding_reducer import (
    REVIEW_OPENING_RESPONSIBILITY_KINDS,
    ReviewerReclassification,
    ReviewerStatusChange,
    apply_reviewer_events,
    is_closed_finding_status,
    project_open_set,
)
from finding_order import finding_id_sort_key, sorted_finding_ids
from finding_responsibility import (
    BranchPlanningResponsibility,
    PlanRevisionResponsibility,
    ResponsibilityKind,
    SliceResponsibility,
    parse_responsibility,
    responsibility_document,
    responsibility_json_schema,
)
from finding_signature import (
    finding_record_signature,
    finding_signature,
    mentioned_repository_paths,
)
from validation_matrix import (
    FINDING_COMMAND_PREFIX,
    ValidationCommand,
    ValidationMatrixError,
    finding_validation_command,
    matches_validation_family,
)
from native_provider_schema import (
    ANTHROPIC_PROVIDER,
    assert_projected_provider_schema,
    bind_required_empty_array as bind_provider_required_empty_array,
    defensive_provider_projection,
)
from orchestrator_diagnostics import OrchestratorDiagnostic, closed_retry_guidance
from rejected_response_shape import RejectedNativeResponseShape
from route_scope import uncovered_route_paths
import native_finding_decisions
from native_finding_decisions import (
    ClosedFindingReviewBinding,
    NativeClosureKind,
    NativeFindingClosure,
    NativeRejectionReason,
    NativeResponsibilityProposal,
    NativeResponsibilityRoute,
    PlanTreatmentDecision,
    PlanTreatmentDecisionKind,
    PlanTreatmentKind,
)
from finding_planning import (
    PlanTreatmentProposal,
    canonical_open_signature_groups,
    validate_plan_treatment_decisions,
)


SCHEMA_VERSION = "native-agent-review-result-v2"
MAX_NATIVE_REVIEW_DISPOSITIONS = 32
DEFAULT_BRANCH_DISCOVERY_MAX_NEW_FINDINGS = 128
MAX_BRANCH_DISCOVERY_NEW_FINDINGS = 512
DISCOVERY_OUTPUT_LIMIT_RULE_ID = "DISCOVERY_OUTPUT_LIMIT"
NONBLANK_TEXT_PATTERN = "^[^\\u0000]*[^\\u0000\\s][^\\u0000]*$"
NONBLANK_LINE_PATTERN = (
    "^[^\\u0000\\r\\n]*[^\\u0000\\r\\n\\s][^\\u0000\\r\\n]*$"
)
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "native-agent-review-result-v2.schema.json"
)


class NativeReviewErrorCode(StrEnum):
    SCHEMA_INVALID = "schema-invalid"
    CONTEXT_INVALID = "context-invalid"
    REQUEST_MISMATCH = "request-mismatch"
    REVIEWER_MISMATCH = "reviewer-mismatch"
    FINDING_ID_INVALID = "finding-id-invalid"
    FINDING_REFERENCE_UNKNOWN = "finding-reference-unknown"
    FINDING_REFERENCE_NOT_OPEN = "finding-reference-not-open"
    FINDING_EVENT_CONFLICT = "finding-event-conflict"
    FINDING_UPDATE_MISSING = "missing-own-finding-update"
    FINDING_CONTENT_INVALID = "finding-content-invalid"
    FINDING_SIGNATURE_DUPLICATE = "finding-signature-duplicate"
    ACCEPTANCE_INVALID = "acceptance-invalid"
    ANCHOR_INVALID = "anchor-invalid"
    REVIEW_CONTENT_MISSING = "review-content-missing"
    STOP_CONTENT_INVALID = "stop-content-invalid"
    APPROVAL_INVALID = "approval-invalid"
    DORMANT_FINDING_DECISION_FIELD = "dormant-finding-decision-field"


_REVIEW_DIAGNOSTIC_BY_CODE = {
    NativeReviewErrorCode.SCHEMA_INVALID: OrchestratorDiagnostic.REVIEW_SCHEMA_INVALID,
    NativeReviewErrorCode.CONTEXT_INVALID: OrchestratorDiagnostic.REVIEW_CONTEXT_INVALID,
    NativeReviewErrorCode.REQUEST_MISMATCH: OrchestratorDiagnostic.REVIEW_REQUEST_MISMATCH,
    NativeReviewErrorCode.REVIEWER_MISMATCH: (
        OrchestratorDiagnostic.REVIEW_REVIEWER_MISMATCH
    ),
    NativeReviewErrorCode.FINDING_ID_INVALID: (
        OrchestratorDiagnostic.REVIEW_FINDING_ID_INVALID
    ),
    NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN: (
        OrchestratorDiagnostic.REVIEW_FINDING_REFERENCE_UNKNOWN
    ),
    NativeReviewErrorCode.FINDING_REFERENCE_NOT_OPEN: (
        OrchestratorDiagnostic.REVIEW_FINDING_REFERENCE_NOT_OPEN
    ),
    NativeReviewErrorCode.FINDING_EVENT_CONFLICT: (
        OrchestratorDiagnostic.REVIEW_FINDING_EVENT_CONFLICT
    ),
    NativeReviewErrorCode.FINDING_UPDATE_MISSING: (
        OrchestratorDiagnostic.REVIEW_FINDING_UPDATE_MISSING
    ),
    NativeReviewErrorCode.FINDING_CONTENT_INVALID: (
        OrchestratorDiagnostic.REVIEW_FINDING_CONTENT_INVALID
    ),
    NativeReviewErrorCode.FINDING_SIGNATURE_DUPLICATE: (
        OrchestratorDiagnostic.REVIEW_FINDING_SIGNATURE_DUPLICATE
    ),
    NativeReviewErrorCode.ACCEPTANCE_INVALID: (
        OrchestratorDiagnostic.REVIEW_ACCEPTANCE_INVALID
    ),
    NativeReviewErrorCode.ANCHOR_INVALID: OrchestratorDiagnostic.REVIEW_ANCHOR_INVALID,
    NativeReviewErrorCode.REVIEW_CONTENT_MISSING: (
        OrchestratorDiagnostic.REVIEW_CONTENT_MISSING
    ),
    NativeReviewErrorCode.STOP_CONTENT_INVALID: (
        OrchestratorDiagnostic.REVIEW_STOP_CONTENT_INVALID
    ),
    NativeReviewErrorCode.APPROVAL_INVALID: (
        OrchestratorDiagnostic.REVIEW_APPROVAL_INVALID
    ),
    NativeReviewErrorCode.DORMANT_FINDING_DECISION_FIELD: (
        OrchestratorDiagnostic.REVIEW_DORMANT_FINDING_DECISION_FIELD
    ),
}


class NativeReviewRejectionSource(StrEnum):
    REQUEST_LEDGER = "request-ledger"
    MODEL_RESPONSE = "model-response"


NATIVE_REVIEW_RESPONSE_RETRY_CODES: frozenset[NativeReviewErrorCode] = frozenset(
    code for code in NativeReviewErrorCode
    if code is not NativeReviewErrorCode.CONTEXT_INVALID
)


_NATIVE_REVIEW_RETRY_GUIDANCE: dict[NativeReviewErrorCode, str] = {
    NativeReviewErrorCode.SCHEMA_INVALID: (
        "Return one JSON result that conforms exactly to the bound writer schema."
    ),
    NativeReviewErrorCode.REQUEST_MISMATCH: (
        "Copy the request_id from this request into the response unchanged."
    ),
    NativeReviewErrorCode.REVIEWER_MISMATCH: (
        "Copy the reviewer field from this request into the response unchanged."
    ),
    NativeReviewErrorCode.FINDING_ID_INVALID: (
        "Use review_contract.next_finding_id and contiguous following C- identifiers."
    ),
    NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN: (
        "Reference only reviewer-owned findings offered in review_contract.previous_findings."
    ),
    NativeReviewErrorCode.FINDING_REFERENCE_NOT_OPEN: (
        "Update only findings that are OPEN in review_contract.previous_findings."
    ),
    NativeReviewErrorCode.FINDING_EVENT_CONFLICT: (
        "Emit at most one compatible event for each finding and do not reuse an existing id as new."
    ),
    NativeReviewErrorCode.FINDING_UPDATE_MISSING: (
        "Supply every disposition required by the bound review kind and offered findings."
    ),
    NativeReviewErrorCode.FINDING_CONTENT_INVALID: (
        "Replace empty, unsafe, or overlong finding prose with bounded non-empty text."
    ),
    NativeReviewErrorCode.FINDING_SIGNATURE_DUPLICATE: (
        "Do not create a duplicate finding; update its unique offered open finding when possible."
    ),
    NativeReviewErrorCode.ACCEPTANCE_INVALID: (
        "Use the acceptance-test kind and command family permitted by the bound contract."
    ),
    NativeReviewErrorCode.ANCHOR_INVALID: (
        "Return only unique anchors permitted by the bound anchor origin, or no anchors."
    ),
    NativeReviewErrorCode.REVIEW_CONTENT_MISSING: (
        "Supply the required review evidence or at least one valid finding event."
    ),
    NativeReviewErrorCode.STOP_CONTENT_INVALID: (
        "Return a complete bounded stop request using a valid rule id and rationale."
    ),
    NativeReviewErrorCode.APPROVAL_INVALID: (
        "For every Finding ID named by the rejection, close it, reject it with "
        "named evidence, or route it to a named later Slice or to branch planning."
    ),
    NativeReviewErrorCode.DORMANT_FINDING_DECISION_FIELD: (
        "Return the legacy result shape without closure, routing, or responsibility proposal fields."
    ),
}

assert set(_NATIVE_REVIEW_RETRY_GUIDANCE) == set(NATIVE_REVIEW_RESPONSE_RETRY_CODES)


def is_retryable_native_review_response_error(error: BaseException) -> bool:
    """Return whether another model response can satisfy the same review task."""

    return (
        isinstance(error, NativeReviewContractError)
        and error.code in NATIVE_REVIEW_RESPONSE_RETRY_CODES
        and error.source is NativeReviewRejectionSource.MODEL_RESPONSE
    )


def native_review_retry_guidance(
    code: NativeReviewErrorCode,
    diagnostic: OrchestratorDiagnostic | None = None,
    context: NativeReviewContext | None = None,
    rejected_response_shape: RejectedNativeResponseShape | None = None,
) -> str:
    """Return bounded corrective guidance for one response-dependent rejection."""

    try:
        fallback = _NATIVE_REVIEW_RETRY_GUIDANCE[code]
    except KeyError as exc:
        raise ValueError(f"native review rejection {code.value} is not retryable") from exc
    if (
        code is NativeReviewErrorCode.APPROVAL_INVALID
        and diagnostic
        in {
            OrchestratorDiagnostic.REVIEW_APPROVAL_INVALID,
            OrchestratorDiagnostic.REVIEW_APPROVAL_NEW_FINDINGS_UNDECIDED,
        }
    ):
        decision_guidance = _slice_decision_retry_guidance(
            context,
            rejected_response_shape,
            include_new_findings=(
                diagnostic
                is OrchestratorDiagnostic.REVIEW_APPROVAL_NEW_FINDINGS_UNDECIDED
            ),
        )
        if decision_guidance is not None:
            return decision_guidance
        route_guidance = _slice_route_scope_retry_guidance(context)
        if route_guidance is not None:
            return route_guidance
    if (
        diagnostic
        is OrchestratorDiagnostic.REVIEW_ACCEPTANCE_COMMAND_ALREADY_PASSING
    ):
        return diagnostic.text + _allowed_command_prefixes_suffix(
            () if context is None else context.validation_command_prefixes
        )
    if diagnostic is _REVIEW_DIAGNOSTIC_BY_CODE[code]:
        diagnostic = None
    return closed_retry_guidance(code.value, fallback, diagnostic)


class NativeReviewContractError(ValueError):
    """A stable, machine-readable native review contract failure."""

    def __init__(
        self,
        code: NativeReviewErrorCode,
        detail: str,
        *,
        orchestrator_diagnostic: OrchestratorDiagnostic | None = None,
        operator_detail: str | None = None,
        source: NativeReviewRejectionSource | None = None,
    ) -> None:
        if orchestrator_diagnostic is not None and not isinstance(
            orchestrator_diagnostic, OrchestratorDiagnostic
        ):
            raise TypeError("orchestrator diagnostic must be a closed enum member")
        if operator_detail is not None and (
            not operator_detail.strip()
            or len(operator_detail) > 1200
            or any(
                character in operator_detail
                for character in ("\x00", "\r", "\n")
            )
        ):
            raise ValueError(
                "operator_detail must be a bounded single-line diagnostic"
            )
        if orchestrator_diagnostic is None:
            rendered = f"{code.value}: {detail}"
            try:
                orchestrator_diagnostic = OrchestratorDiagnostic(rendered)
            except ValueError:
                orchestrator_diagnostic = _REVIEW_DIAGNOSTIC_BY_CODE[code]
        self.code = code
        self.detail = detail
        self.orchestrator_diagnostic = orchestrator_diagnostic
        self.operator_detail = operator_detail
        self.source = source or (
            NativeReviewRejectionSource.REQUEST_LEDGER
            if code is NativeReviewErrorCode.CONTEXT_INVALID
            else NativeReviewRejectionSource.MODEL_RESPONSE
        )
        if not isinstance(self.source, NativeReviewRejectionSource):
            raise TypeError("native review rejection source must be a closed enum member")
        super().__init__(f"{code.value}: {detail}")


def find_native_review_contract_error(
    error: BaseException,
) -> NativeReviewContractError | None:
    """Find a native review rejection through explicit cause edges."""

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, NativeReviewContractError):
            return current
        seen.add(id(current))
        current = current.__cause__
    return None


@dataclass(frozen=True, slots=True)
class NativeReviewDispositionLimit:
    """Typed detail carried by an existing review-contract failure."""

    actual_items: int
    maximum_items: int


def find_native_review_disposition_limit_error(
    error: BaseException,
) -> NativeReviewDispositionLimit | None:
    """Find the typed limit failure through explicit exception boundaries."""

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        detail = getattr(current, "disposition_limit", None)
        if isinstance(detail, NativeReviewDispositionLimit):
            return detail
        seen.add(id(current))
        current = current.__cause__
    return None


def validate_native_review_disposition_budget(
    document: Mapping[str, Any], context: NativeReviewContext
) -> None:
    """Reject a combined disposition overflow before applying reviewer effects."""

    status_changes = document.get("status_changes")
    reclassifications = document.get("reclassifications")
    if not isinstance(status_changes, list) or not isinstance(reclassifications, list):
        return
    routes = document.get("responsibility_routes", [])
    if not isinstance(routes, list):
        return
    plan_decisions = document.get("plan_treatment_decisions", [])
    if not isinstance(plan_decisions, list):
        return
    actual_items = (
        len(status_changes)
        + len(reclassifications)
        + len(routes)
        + len(plan_decisions)
    )
    new_finding_capacity = len(_native_finding_id_window(context, size=32))
    maximum_items = min(
        MAX_NATIVE_REVIEW_DISPOSITIONS,
        native_review_disposition_capacity(context) + new_finding_capacity,
    )
    if actual_items > maximum_items:
        diagnostic = (
            "review disposition count "
            f"{actual_items} exceeds bound maximum {maximum_items}; "
            f"status_changes={len(status_changes)}, "
            f"reclassifications={len(reclassifications)}, "
            f"responsibility_routes={len(routes)}"
            f", plan_treatment_decisions={len(plan_decisions)}"
        )
        error = NativeReviewContractError(
            NativeReviewErrorCode.SCHEMA_INVALID,
            diagnostic,
        )
        error.disposition_limit = NativeReviewDispositionLimit(
            actual_items, maximum_items
        )
        raise error


def native_review_disposition_capacity(context: NativeReviewContext) -> int:
    """Count reviewer-owned open findings eligible for one disposition."""

    if context.approval_marker is ApprovalMarker.PLAN and context.plan_treatments:
        return len(context.plan_treatments) + sum(
            len(item.finding_ids)
            for item in context.plan_treatments
            if item.treatment_kind is PlanTreatmentKind.NO_CODE
        )
    return sum(
        item.origin.reporter is context.reviewer
        for item in project_open_set(context.previous_findings).findings
    )


@dataclass(frozen=True, slots=True)
class NativeProseAcceptance:
    text: str


@dataclass(frozen=True, slots=True)
class NativeValidationAcceptance:
    argv: tuple[str, ...]


NativeAcceptance: TypeAlias = NativeProseAcceptance | NativeValidationAcceptance


@dataclass(frozen=True, slots=True)
class NativeFinding:
    finding_id: str
    finding_class: FindingClass
    summary: str
    acceptance_test: NativeAcceptance
    predecessor_finding_ref: str | None = None
    evidence_anchor_sha256: str | None = None
    affected_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NativeStatusChange:
    finding_id: str
    status: FindingStatus
    rationale: str
    closure: NativeFindingClosure | None = None


@dataclass(frozen=True, slots=True)
class NativeReclassification:
    finding_id: str
    finding_class: FindingClass
    rationale: str


@dataclass(frozen=True, slots=True)
class NativeAnchor:
    anchor_id: str
    input_fixture: str
    expected: str
    tolerance: str


@dataclass(frozen=True, slots=True)
class NativeReviewResult:
    request_id: str
    reviewer: AgentRole
    approved: bool
    new_findings: tuple[NativeFinding, ...]
    status_changes: tuple[NativeStatusChange, ...]
    reclassifications: tuple[NativeReclassification, ...]
    anchors: tuple[NativeAnchor, ...]
    evidence: ReviewEvidence | None
    pre_mortem: str | None
    responsibility_routes: tuple[NativeResponsibilityRoute, ...] = ()
    plan_treatment_decisions: tuple[PlanTreatmentDecision, ...] = ()


@dataclass(frozen=True, slots=True)
class NativeFindingOccurrence:
    finding_id: str
    rationale: str
    evidence_anchor_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class NativeBranchDiscoveryCompleted:
    """E6 delivery: a completed scan, deliberately not an approval decision."""

    request_id: str
    reviewer: AgentRole
    scan_complete: bool
    new_findings: tuple[NativeFinding, ...]
    occurrences: tuple[NativeFindingOccurrence, ...]
    evidence: ReviewEvidence
    pre_mortem: str
    responsibility_routes: tuple[NativeResponsibilityRoute, ...] = ()


@dataclass(frozen=True, slots=True)
class NativeStopResult:
    request_id: str
    reviewer: AgentRole
    rule_id: str
    rationale: str
    remediation_paths: tuple[str, ...] = ()


NativeReviewResponse: TypeAlias = (
    NativeReviewResult | NativeBranchDiscoveryCompleted | NativeStopResult
)


def _validated_branch_discovery_capacity(
    approval_marker: ApprovalMarker,
    capacity: int | None,
) -> int | None:
    if approval_marker is not ApprovalMarker.BRANCH_DISCOVERY:
        if capacity is not None:
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "max_new_findings is valid only for a branch discovery review",
            )
        return None
    normalized = (
        DEFAULT_BRANCH_DISCOVERY_MAX_NEW_FINDINGS
        if capacity is None
        else capacity
    )
    if (
        isinstance(normalized, bool)
        or not isinstance(normalized, int)
        or normalized < 1
        or normalized > MAX_BRANCH_DISCOVERY_NEW_FINDINGS
    ):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "branch discovery max_new_findings must be an integer from 1 to "
            f"{MAX_BRANCH_DISCOVERY_NEW_FINDINGS}",
        )
    return normalized


@dataclass(frozen=True, slots=True)
class NativeReviewContext:
    run_id: str
    work_unit_id: str
    operation: str
    diff_fingerprint: str
    reviewer: AgentRole
    approval_marker: ApprovalMarker
    slice_id: str
    round_number: int
    previous_findings: tuple[FindingRecord, ...] = ()
    known_open_findings: tuple[FindingRecord, ...] | None = None
    authoritative_finding_ids: tuple[str, ...] = ()
    slice_commit_decision_finding_ids: tuple[str, ...] = field(
        init=False, default=()
    )
    validation_attestation: ValidationAttestation | None = None
    test_files: tuple[str, ...] = ()
    test_changes_approved: bool = False
    allow_new_observations: bool = True
    anchor_origin: str | None = None
    validation_command_prefixes: tuple[tuple[str, ...], ...] = ()
    red_state_followup_slice: str | None = None
    plan_artifact_path: str | None = None
    final_review_pending_count: int | None = None
    max_new_findings: int | None = None
    implementer_responsibility_proposals: tuple[NativeResponsibilityProposal, ...] = ()
    planned_slices: tuple[PlannedSlice, ...] = ()
    plan_treatments: tuple[PlanTreatmentProposal, ...] = ()
    closed_finding_bindings: tuple[ClosedFindingReviewBinding, ...] = ()
    pre_change_fingerprint: str | None = None
    request_sequence: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_new_findings",
            _validated_branch_discovery_capacity(
                self.approval_marker, self.max_new_findings
            ),
        )
        for label, value in (
            ("run_id", self.run_id),
            ("work_unit_id", self.work_unit_id),
            ("operation", self.operation),
            ("slice_id", self.slice_id),
        ):
            if not value.strip():
                raise NativeReviewContractError(
                    NativeReviewErrorCode.CONTEXT_INVALID,
                    f"{label} must not be empty",
                )
        if self.round_number < 1:
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "round_number must be 1-based",
            )
        if self.request_sequence is None:
            object.__setattr__(self, "request_sequence", self.round_number)
        elif self.request_sequence < 1:
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "request_sequence must be 1-based",
            )
        if self.reviewer is not AgentRole.CLAUDE:
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "reviewer must be claude",
            )
        if len(self.diff_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.diff_fingerprint
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "diff_fingerprint must be lowercase SHA-256",
            )
        if self.validation_attestation is not None and (
            self.validation_attestation.diff_fingerprint != self.diff_fingerprint
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "validation attestation fingerprint does not match context",
            )
        if self.pre_change_fingerprint is not None and (
            len(self.pre_change_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.pre_change_fingerprint
            )
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "pre_change_fingerprint must be lowercase SHA-256",
            )
        previous_ids = tuple(item.finding_id for item in self.previous_findings)
        if previous_ids != sorted_finding_ids(previous_ids):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "previous findings must be sorted and unique",
            )
        authoritative_ids = self.authoritative_finding_ids or previous_ids
        if authoritative_ids != sorted_finding_ids(authoritative_ids):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "authoritative finding ids must be sorted and unique",
            )
        if any(
            not SOURCE_FINDING_ID_PATTERN.fullmatch(finding_id)
            for finding_id in authoritative_ids
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "authoritative finding ids must use the C-01 namespace",
            )
        if not set(previous_ids).issubset(authoritative_ids):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "offered findings must belong to the authoritative finding set",
            )
        _initialize_slice_commit_decision_scope(self, authoritative_ids)
        offered_open = project_open_set(self.previous_findings).findings
        known_open = (
            offered_open
            if self.known_open_findings is None
            else self.known_open_findings
        )
        known_ids = tuple(item.finding_id for item in known_open)
        if known_ids != sorted_finding_ids(known_ids):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "known open findings must be sorted and unique",
            )
        if project_open_set(known_open).findings != known_open:
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "known open findings must all be open",
            )
        if not set(known_ids).issubset(authoritative_ids):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "known open findings must belong to the authoritative finding set",
            )
        known_by_id = {item.finding_id: item for item in known_open}
        for finding in offered_open:
            if known_by_id.get(finding.finding_id) != finding:
                raise NativeReviewContractError(
                    NativeReviewErrorCode.CONTEXT_INVALID,
                    "offered open findings must match the known open finding set",
                )
        normalized_tests = tuple(sorted(set(self.test_files)))
        if normalized_tests != self.test_files or any(
            not item.strip() for item in self.test_files
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "test files must be sorted, unique, and non-empty",
            )
        if any(not isinstance(item, PlannedSlice) for item in self.planned_slices):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "planned slices must contain only typed PlannedSlice values",
            )
        planned_ids = tuple(item.slice_id for item in self.planned_slices)
        if planned_ids and planned_ids != tuple(range(1, len(planned_ids) + 1)):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "planned slice ids must be contiguous and 1-based",
            )

        if self.anchor_origin is not None and not self.anchor_origin.strip():
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "anchor_origin must be non-empty when present",
            )
        if (
            self.red_state_followup_slice is not None
            and not self.red_state_followup_slice.strip()
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "red_state_followup_slice must be non-empty when present",
            )
        if self.plan_artifact_path is not None:
            path = PurePosixPath(self.plan_artifact_path)
            if (
                not self.plan_artifact_path.strip()
                or "\\" in self.plan_artifact_path
                or path.is_absolute()
                or ".." in path.parts
                or any(character in self.plan_artifact_path for character in "*?[")
                or self.plan_artifact_path != path.as_posix()
                or path.suffix.lower() != ".md"
            ):
                raise NativeReviewContractError(
                    NativeReviewErrorCode.CONTEXT_INVALID,
                    "plan_artifact_path must be one exact repository-relative Markdown path",
                )
            if self.approval_marker is not ApprovalMarker.PLAN:
                raise NativeReviewContractError(
                    NativeReviewErrorCode.CONTEXT_INVALID,
                    "plan_artifact_path is valid only for a plan review",
                )
        if self.final_review_pending_count is not None:
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "legacy final_review_pending_count is unsupported",
            )
        elif self.final_review_pending_count is not None:
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "final_review_pending_count is valid only for a final review",
            )
        normalized_prefixes = tuple(dict.fromkeys(self.validation_command_prefixes))
        if normalized_prefixes != self.validation_command_prefixes or any(
            not prefix
            or any(
                not isinstance(part, str)
                or not part
                or any(character in part for character in ("\x00", "\r", "\n"))
                for part in prefix
            )
            for prefix in self.validation_command_prefixes
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "validation command prefixes must be unique safe argv prefixes",
            )
        _validate_implementer_responsibility_proposals(self)
        _validate_plan_treatments(self)
        _validate_closed_finding_bindings(self)

    @property
    def effective_known_open_findings(self) -> tuple[FindingRecord, ...]:
        if self.known_open_findings is not None:
            return self.known_open_findings
        return project_open_set(self.previous_findings).findings

    @property
    def request_id(self) -> str:
        encoded = json.dumps(
            _context_binding(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "native-review-request-" + hashlib.sha256(encoded).hexdigest()


def _validate_implementer_responsibility_proposals(
    context: NativeReviewContext,
) -> None:
    proposals = context.implementer_responsibility_proposals
    if any(not isinstance(item, NativeResponsibilityProposal) for item in proposals):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "implementer responsibility proposals must be typed, sorted, and unique",
        )
    proposal_ids = tuple(item.finding_id for item in proposals)
    if proposal_ids != sorted_finding_ids(proposal_ids):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "implementer responsibility proposals must be typed, sorted, and unique",
        )
    open_ids = set(project_open_set(context.previous_findings).finding_ids)
    if unknown_proposals := set(proposal_ids) - open_ids:
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "implementer responsibility proposal references unoffered finding "
            f"{sorted_finding_ids(unknown_proposals)[0]}",
        )
    for proposal in proposals:
        try:
            responsibility_document(proposal.responsibility)
        except ValueError as exc:
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                f"implementer responsibility proposal is invalid: {exc}",
            ) from exc
        _require_native_text(
            proposal.rationale,
            "implementer responsibility proposal rationale",
            max_length=3000,
            code=NativeReviewErrorCode.CONTEXT_INVALID,
        )


def _validate_plan_treatments(context: NativeReviewContext) -> None:
    treatments = context.plan_treatments
    if any(not isinstance(item, PlanTreatmentProposal) for item in treatments):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "plan treatments must be typed",
        )
    if treatments and context.approval_marker is not ApprovalMarker.PLAN:
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "plan treatments are valid only for a plan review",
        )
    if not treatments:
        return
    expected = canonical_open_signature_groups(context.previous_findings)
    signatures = tuple(item.signature for item in treatments)
    if signatures != tuple(sorted(set(signatures))):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "plan treatments must be sorted and unique by signature",
        )
    expected_groups = {
        item.signature: item.finding_ids for item in expected
    }
    if set(signatures) != set(expected_groups):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "plan treatments must cover every canonical open signature exactly once",
        )
    if any(
        item.finding_ids != expected_groups[item.signature]
        for item in treatments
    ):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "plan treatment Finding IDs differ from their canonical signature group",
        )


def _validate_closed_finding_bindings(context: NativeReviewContext) -> None:
    bindings = context.closed_finding_bindings
    if any(not isinstance(item, ClosedFindingReviewBinding) for item in bindings):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "closed Finding bindings must be typed",
        )
    if bindings and context.approval_marker is not ApprovalMarker.BRANCH_DISCOVERY:
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "closed Finding bindings are valid only for branch discovery",
        )
    ids = tuple(item.finding_id for item in bindings)
    if ids != sorted_finding_ids(ids):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "closed Finding bindings must be sorted and unique",
        )
    previous = {item.finding_id: item for item in context.previous_findings}
    for binding in bindings:
        finding = previous.get(binding.finding_id)
        if finding is None or not is_closed_finding_status(finding.status):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "closed Finding binding does not reference a closed offered Finding",
            )
        if finding_record_signature(finding) != binding.signature:
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "closed Finding binding signature differs from the Finding",
            )


@dataclass(frozen=True, slots=True)
class BoundNativeReviewContext:
    """A live-provider context bound to one complete canonical request.

    The provider-independent :class:`NativeReviewContext` intentionally keeps
    its original local binding for isolated domain tests.  Productive native
    transport, persistence, and recovery APIs use this distinct wrapper so a
    caller cannot silently fall back to the narrower local request id.
    """

    context: NativeReviewContext
    request_id: str
    request_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.context, NativeReviewContext):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "bound review context requires a NativeReviewContext",
            )
        if len(self.request_digest) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.request_digest
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "bound request_digest must be lowercase SHA-256",
            )
        if self.request_id != f"native-review-request-{self.request_digest}":
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "bound request_id must contain request_digest",
            )


def load_native_review_schema() -> dict[str, Any]:
    """Load and self-check the bundled native response schema offline."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise NativeReviewContractError(
            NativeReviewErrorCode.SCHEMA_INVALID,
            "bundled native review schema must be an object",
            source=NativeReviewRejectionSource.REQUEST_LEDGER,
        )
    _enable_native_review_finding_decision_schema(schema)
    _enable_branch_discovery_result_schema(schema)
    try:
        check_schema(schema, location="<native-review-schema>")
    except SchemaDefinitionError as exc:
        raise NativeReviewContractError(
            NativeReviewErrorCode.SCHEMA_INVALID,
            str(exc),
            source=NativeReviewRejectionSource.REQUEST_LEDGER,
        ) from exc
    return schema


def _enable_branch_discovery_result_schema(schema: dict[str, Any]) -> None:
    """Add E6's delivery without changing the dormant reader schema on disk."""

    definitions = schema["$defs"]
    definitions["finding"]["properties"]["predecessor_finding_ref"] = {
        "oneOf": [
            {
                "type": "string",
                "pattern": "^C-(0[1-9]|[1-9][0-9]*)$",
            },
            {"type": "null"},
        ],
    }
    definitions["finding"]["properties"]["evidence_anchor_sha256"] = {
        "oneOf": [
            {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            {"type": "null"},
        ],
    }
    definitions["branch_discovery_occurrence"] = {
        "type": "object",
        "properties": {
            "finding_id": {
                "type": "string",
                "pattern": "^C-(0[1-9]|[1-9][0-9]*)$",
            },
            "rationale": {
                "type": "string",
                "pattern": NONBLANK_TEXT_PATTERN,
                "maxLength": 3000,
            },
            "evidence_anchor_sha256": {
                "oneOf": [
                    {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    {"type": "null"},
                ],
            },
        },
        "required": ["finding_id", "rationale"],
        "additionalProperties": False,
    }
    definitions["branch_discovery_completed"] = {
        "allOf": [
            {"$ref": "#/$defs/common"},
            {
                "type": "object",
                "properties": {
                    "schema_version": {"const": SCHEMA_VERSION},
                    "result_type": {"const": "branch_discovery_completed"},
                    "request_id": {
                        "type": "string",
                        "pattern": "^native-review-request-[0-9a-f]{64}$",
                    },
                    "reviewer": {"const": "claude"},  # allowlist:provider -- canonical reviewer role
                    "scan_complete": {"type": "boolean"},
                    "new_findings": {
                        "type": "array",
                        "maxItems": MAX_BRANCH_DISCOVERY_NEW_FINDINGS,
                        "items": {"$ref": "#/$defs/finding"},
                    },
                    "occurrences": {
                        "type": "array",
                        "maxItems": 10000,
                        "items": {
                            "$ref": "#/$defs/branch_discovery_occurrence"
                        },
                    },
                    "responsibility_routes": {
                        "type": "array",
                        "maxItems": MAX_BRANCH_DISCOVERY_NEW_FINDINGS,
                        "items": {"$ref": "#/$defs/responsibility_route"},
                    },
                    "review_evidence": {"$ref": "#/$defs/evidence"},
                    "pre_mortem": {
                        "type": "string",
                        "pattern": NONBLANK_TEXT_PATTERN,
                        "maxLength": 3000,
                    },
                },
                "required": [
                    "schema_version",
                    "result_type",
                    "request_id",
                    "reviewer",
                    "scan_complete",
                    "new_findings",
                    "occurrences",
                    "responsibility_routes",
                    "review_evidence",
                    "pre_mortem",
                ],
                "additionalProperties": False,
            },
        ]
    }
    common_result_types = definitions["common"]["properties"]["result_type"]
    common_result_types["enum"] = [
        "review_result",
        "stop_request",
        "branch_discovery_completed",
    ]
    schema["oneOf"].append(
        {"$ref": "#/$defs/branch_discovery_completed"}
    )


def _branch_discovery_provider_response_schema(
    context: NativeReviewContext,
    definitions: dict[str, Any],
) -> dict[str, Any]:
    assert context.max_new_findings is not None
    finding_ids = _native_finding_id_window(
        context, size=context.max_new_findings
    )
    finding = _bound_review_definition(
        definitions["finding"], finding_ids=finding_ids
    )
    finding["properties"]["summary"].update(
        pattern=NONBLANK_TEXT_PATTERN, maxLength=3000
    )
    predecessor_ids = tuple(
        item.finding_id
        for item in context.closed_finding_bindings
        if not item.anchor_unchanged
    )
    finding["properties"]["predecessor_finding_ref"] = (
        {
            "oneOf": [
                {"type": "string", "enum": list(predecessor_ids)},
                {"type": "null"},
            ]
        }
        if predecessor_ids
        else {"type": "null"}
    )
    finding["properties"]["evidence_anchor_sha256"] = {
        "oneOf": [
            {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            {"type": "null"},
        ]
    }
    finding["required"].extend(
        ["predecessor_finding_ref", "evidence_anchor_sha256"]
    )
    definitions["bound_branch_discovery_finding"] = finding
    open_ids = tuple(
        item.finding_id for item in context.effective_known_open_findings
    )
    stable_closed = tuple(
        item for item in context.closed_finding_bindings if item.anchor_unchanged
    )
    known_ids = sorted_finding_ids(
        (*open_ids, *(item.finding_id for item in stable_closed))
    )
    occurrence_options: list[dict[str, Any]] = []
    stable_by_id = {item.finding_id: item for item in stable_closed}
    for finding_id in known_ids:
        option = json.loads(
            json.dumps(definitions["branch_discovery_occurrence"])
        )
        option["properties"]["finding_id"] = {
            "type": "string",
            "const": finding_id,
        }
        binding = stable_by_id.get(finding_id)
        option["properties"]["evidence_anchor_sha256"] = (
            {
                "type": "string",
                "const": binding.current_anchor.stability_sha256,
            }
            if binding is not None
            else {"type": "null"}
        )
        option["required"].append("evidence_anchor_sha256")
        occurrence_options.append(option)
    occurrence = (
        {"oneOf": occurrence_options}
        if occurrence_options
        else _bound_review_definition(
            definitions["branch_discovery_occurrence"], finding_ids=()
        )
    )
    definitions["bound_branch_discovery_occurrence"] = occurrence
    route = _bound_review_definition(
        definitions["responsibility_route"], finding_ids=finding_ids
    )
    route["properties"]["rationale"].update(
        pattern=NONBLANK_TEXT_PATTERN, maxLength=3000
    )
    definitions["bound_branch_discovery_responsibility_route"] = route
    completed = json.loads(
        json.dumps(definitions["branch_discovery_completed"]["allOf"][1])
    )
    completed["properties"]["schema_version"] = {
        "type": "string",
        "const": SCHEMA_VERSION,
    }
    completed["properties"]["result_type"] = {
        "type": "string",
        "const": "branch_discovery_completed",
    }
    completed["properties"]["reviewer"] = {
        "type": "string",
        "const": context.reviewer.value,
    }
    completed["properties"]["scan_complete"] = {
        "type": "boolean",
        "const": True,
    }
    completed["properties"]["new_findings"]["maxItems"] = (
        context.max_new_findings
    )
    completed["properties"]["new_findings"]["items"] = {
        "$ref": "#/$defs/bound_branch_discovery_finding"
    }
    if known_ids:
        completed["properties"]["occurrences"].update(
            maxItems=len(known_ids),
            items={"$ref": "#/$defs/bound_branch_discovery_occurrence"},
        )
    else:
        _bind_required_empty_array(completed["properties"]["occurrences"])
    completed["properties"]["responsibility_routes"].update(
        maxItems=len(finding_ids),
        items={"$ref": "#/$defs/bound_branch_discovery_responsibility_route"},
    )
    definitions["bound_branch_discovery_completed"] = completed
    stop = _bound_stop_result_definition(
        definitions["stop_request"], reviewer=context.reviewer
    )
    stop["properties"]["rule_id"].update(
        pattern=NONBLANK_TEXT_PATTERN, maxLength=200
    )
    stop["properties"]["rationale"].update(
        pattern=NONBLANK_TEXT_PATTERN, maxLength=3000
    )
    stop["properties"]["rule_id"]["description"] = (
        f"Use {DISCOVERY_OUTPUT_LIMIT_RULE_ID} when the scan reaches the "
        "request-bound max_new_findings capacity; no partial result is authoritative."
    )
    definitions["bound_branch_discovery_stop"] = stop
    return {
        "title": "Native branch discovery writer projection",
        "type": "object",
        "properties": {
            "result": {
                "oneOf": [
                    {"$ref": "#/$defs/bound_branch_discovery_completed"},
                    {"$ref": "#/$defs/bound_branch_discovery_stop"},
                ]
            }
        },
        "required": ["result"],
        "additionalProperties": False,
        "$defs": definitions,
    }


def native_review_provider_response_schema(
    context: NativeReviewContext, *, base_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the reader contract into a request-bound reviewer writer schema.

    Typed review context selects the projection; free-form operation names do not.
    """
    if not isinstance(context, NativeReviewContext):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "provider schema projection requires NativeReviewContext",
        )
    if context.reviewer is not AgentRole.CLAUDE:
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "native Claude writer schema requires reviewer=claude",
        )
    schema = defensive_provider_projection(
        base_schema if base_schema is not None else load_native_review_schema(),
        provider="claude",
        required_features=(
            "closed_object",
            "min_max_items",
            "nested_any_of",
            "nested_one_of",
        ),
    )
    definitions = schema["$defs"]
    definitions["finding"]["properties"]["finding_id"] = {
        "type": "string",
        "pattern": "^C-(0[1-9]|[1-9][0-9]*)$",
    }
    definitions["prose_acceptance"]["properties"]["text"]["pattern"] = (
        NONBLANK_TEXT_PATTERN
    )
    definitions["prose_acceptance"]["properties"]["text"]["maxLength"] = 2000
    definitions["validation_acceptance"]["properties"]["argv"]["items"][
        "pattern"
    ] = NONBLANK_LINE_PATTERN
    definitions["validation_acceptance"]["properties"]["argv"]["items"][
        "maxLength"
    ] = 512
    for key in ("dimensions", "largest_residual_risk", "break_condition"):
        definitions["evidence"]["properties"][key]["pattern"] = (
            NONBLANK_TEXT_PATTERN
        )
        definitions["evidence"]["properties"][key]["maxLength"] = 3000
    for key in ("input_fixture", "expected", "tolerance"):
        definitions["anchor"]["properties"][key]["pattern"] = (
            NONBLANK_TEXT_PATTERN
        )
    definitions["anchor"]["properties"]["input_fixture"]["maxLength"] = 2000
    definitions["anchor"]["properties"]["expected"]["maxLength"] = 2000
    definitions["anchor"]["properties"]["tolerance"]["maxLength"] = 1000
    if context.approval_marker is ApprovalMarker.BRANCH_DISCOVERY:
        projected_schema = _branch_discovery_provider_response_schema(
            context, definitions
        )
        assert_projected_provider_schema(
            projected_schema, provider=context.reviewer.value
        )
        return projected_schema
    own_findings = tuple(
        item
        for item in context.previous_findings
        if item.origin.reporter is context.reviewer
    )
    own_open = project_open_set(own_findings).findings
    own_open_ids = tuple(item.finding_id for item in own_open)
    own_open_blockers = tuple(
        item
        for item in own_open
        if item.finding_class is FindingClass.BLOCKER
    )
    new_ids = _native_finding_id_window(context, size=32)
    routable_ids = sorted_finding_ids((*own_open_ids, *new_ids))
    own_disposition_max = min(
        MAX_NATIVE_REVIEW_DISPOSITIONS, len(own_open_ids)
    )
    disposition_max = min(MAX_NATIVE_REVIEW_DISPOSITIONS, len(routable_ids))
    branch = {
        ApprovalMarker.PLAN: "plan",
        ApprovalMarker.SLICE: (
            "slice_initial"
            if context.round_number == 1 and context.allow_new_observations
            else "slice_convergence"
        ),
    }[context.approval_marker]
    approved_finding = _bound_review_definition(
        definitions["finding"], finding_ids=new_ids
    )
    denied_finding = _bound_review_definition(
        definitions["finding"], finding_ids=new_ids
    )
    approved_finding["properties"]["summary"]["pattern"] = NONBLANK_TEXT_PATTERN
    denied_finding["properties"]["summary"]["pattern"] = NONBLANK_TEXT_PATTERN
    approved_finding["properties"]["summary"]["maxLength"] = 3000
    denied_finding["properties"]["summary"]["maxLength"] = 3000
    observations_allowed = context.allow_new_observations
    same_response_closure_ids = (
        new_ids
        if context.approval_marker is ApprovalMarker.SLICE
        and observations_allowed
        else ()
    )
    approved_finding["properties"]["finding_class"] = (
        {"type": "string", "const": FindingClass.OBSERVATION.value}
        if observations_allowed
        else {"type": "string", "const": FindingClass.BLOCKER.value}
    )
    if not observations_allowed:
        # The approved branch below forbids all new findings.  Keeping a typed
        # item definition still makes the branch self-contained for providers.
        approved_new_max = 0
    else:
        approved_new_max = len(new_ids)
    denied_finding["properties"]["finding_class"] = (
        {
            "type": "string",
            "enum": [
                FindingClass.BLOCKER.value,
                FindingClass.OBSERVATION.value,
            ],
        }
        if observations_allowed
        else {
            "type": "string",
            "const": FindingClass.BLOCKER.value,
        }
    )

    status = _bound_review_definition(
        definitions["status_change"],
        finding_ids=own_open_ids,
    )
    reclassification = _bound_review_definition(
        definitions["reclassification"],
        finding_ids=own_open_ids,
    )
    status["properties"]["rationale"]["pattern"] = NONBLANK_TEXT_PATTERN
    status["properties"]["rationale"]["maxLength"] = 3000
    reclassification["properties"]["rationale"]["pattern"] = (
        NONBLANK_TEXT_PATTERN
    )
    reclassification["properties"]["rationale"]["maxLength"] = 3000
    if not observations_allowed:
        reclassification["properties"]["finding_class"] = {
            "type": "string",
            "const": FindingClass.BLOCKER.value,
        }

    definitions["bound_approved_finding"] = approved_finding
    definitions["bound_denied_finding"] = denied_finding
    definitions["bound_status_change"] = status
    definitions["bound_reclassification"] = reclassification
    _bind_responsibility_route_definition(
        definitions,
        routable_ids,
        planned_slice_ids=_routable_planned_slice_ids(context),
    )

    approved = _bound_review_result_definition(
        definitions,
        reviewer=context.reviewer,
        decision="approved",
        anchor_count=64 if context.anchor_origin is not None else 0,
    )
    if approved_new_max:
        approved["properties"]["new_findings"].update(
            maxItems=approved_new_max,
            items={"$ref": "#/$defs/bound_approved_finding"},
        )
    else:
        _bind_required_empty_array(approved["properties"]["new_findings"])
    approved_status_max = _bind_approved_status_changes(
        approved,
        status,
        own_open,
        same_response_closure_ids,
    )
    _bind_native_decision_collections(approved, context, disposition_max)
    if observations_allowed and own_open_ids:
        approved_reclassification = _bound_review_definition(
            reclassification,
            finding_ids=own_open_ids,
        )
        approved_reclassification["properties"]["finding_class"] = {
            "type": "string",
            "const": FindingClass.OBSERVATION.value,
        }
        definitions["bound_approved_reclassification"] = (
            approved_reclassification
        )
        approved["properties"]["status_changes"].update(
            minItems=0,
            maxItems=approved_status_max,
        )
        approved["properties"]["reclassifications"].update(
            minItems=0,
            maxItems=own_disposition_max,
            items={"$ref": "#/$defs/bound_approved_reclassification"},
        )
    else:
        _bind_required_empty_array(approved["properties"]["reclassifications"])
    approved["properties"]["review_evidence"] = {
        "$ref": "#/$defs/evidence"
    }
    approved["properties"]["pre_mortem"] = {
        "type": "string",
        "pattern": NONBLANK_TEXT_PATTERN,
        "maxLength": 3000,
    }

    denied = _bound_review_result_definition(
        definitions,
        reviewer=context.reviewer,
        decision="denied",
        anchor_count=64 if context.anchor_origin is not None else 0,
    )
    denied["properties"]["new_findings"].update(
        minItems=(
            0
            if own_open_blockers
            else 1
        ),
        maxItems=len(new_ids),
        items={"$ref": "#/$defs/bound_denied_finding"},
    )
    if own_disposition_max:
        denied["properties"]["status_changes"].update(
            maxItems=own_disposition_max,
            items={"$ref": "#/$defs/bound_status_change"},
        )
        denied["properties"]["reclassifications"].update(
            maxItems=own_disposition_max,
            items={"$ref": "#/$defs/bound_reclassification"},
        )
    else:
        _bind_required_empty_array(denied["properties"]["status_changes"])
        _bind_required_empty_array(denied["properties"]["reclassifications"])
    _bind_native_decision_collections(denied, context, disposition_max)
    denied["properties"]["pre_mortem"] = {
        "anyOf": [
            {"type": "null"},
            {
                "type": "string",
                "pattern": NONBLANK_TEXT_PATTERN,
                "maxLength": 3000,
            },
        ]
    }

    stop = _bound_stop_result_definition(
        definitions["stop_request"], reviewer=context.reviewer
    )
    stop["properties"]["rule_id"]["pattern"] = NONBLANK_TEXT_PATTERN
    stop["properties"]["rationale"]["pattern"] = NONBLANK_TEXT_PATTERN
    stop["properties"]["rule_id"]["maxLength"] = 200
    stop["properties"]["rationale"]["maxLength"] = 3000
    definitions[f"bound_{branch}_approved"] = approved
    definitions[f"bound_{branch}_denied"] = denied
    definitions[f"bound_{branch}_stop"] = stop

    validation = context.validation_attestation
    approval_possible = (
        validation is not None
        and validation.complete
        and (
            validation.passed
            or context.red_state_followup_slice is not None
        )
        and (not context.test_files or context.test_changes_approved)
    )
    result_refs: list[dict[str, str]] = []
    if approval_possible:
        result_refs.append({"$ref": f"#/$defs/bound_{branch}_approved"})
    result_refs.extend(
        (
            {"$ref": f"#/$defs/bound_{branch}_denied"},
            {"$ref": f"#/$defs/bound_{branch}_stop"},
        )
    )
    projected_schema = {
        "title": f"Native Claude {branch} writer projection",
        "type": "object",
        "properties": {"result": {"oneOf": result_refs}},
        "required": ["result"],
        "additionalProperties": False,
        "$defs": definitions,
    }
    assert_projected_provider_schema(
        projected_schema, provider=context.reviewer.value
    )
    return projected_schema


def _native_finding_id_window(
    context: NativeReviewContext, *, size: int
) -> tuple[str, ...]:
    first = next_native_finding_id(context)
    prefix, raw_number = first.split("-", 1)
    start = int(raw_number)
    return tuple(f"{prefix}-{number:02d}" for number in range(start, start + size))


def _bind_responsibility_route_definition(
    definitions: dict[str, Any],
    own_open_ids: tuple[str, ...],
    *,
    planned_slice_ids: tuple[str, ...],
) -> None:
    responsibility = definitions["finding_responsibility"]
    variants = responsibility["oneOf"]
    slice_responsibility = variants[0]
    if planned_slice_ids:
        slice_responsibility["properties"]["slice_id"] = {
            "type": "string",
            "enum": list(planned_slice_ids),
        }
    else:
        # Without a plan-bound target set, a SLICE route is not representable.
        # The other responsibility kinds remain available.
        responsibility["oneOf"] = variants[1:]
    route = _bound_review_definition(
        definitions["responsibility_route"], finding_ids=own_open_ids
    )
    route["properties"]["rationale"].update(
        pattern=NONBLANK_TEXT_PATTERN, maxLength=3000
    )
    definitions["bound_responsibility_route"] = route


def _routable_planned_slice_ids(context: NativeReviewContext) -> tuple[str, ...]:
    """Return only plan targets that can discharge the current Slice."""

    planned_ids = tuple(str(item.slice_id) for item in context.planned_slices)
    if context.approval_marker is not ApprovalMarker.SLICE:
        return planned_ids
    try:
        current = int(context.slice_id)
    except ValueError:
        return ()
    return tuple(
        str(item.slice_id)
        for item in context.planned_slices
        if item.slice_id > current
    )


def _bind_approved_status_changes(
    approved: dict[str, Any],
    status: dict[str, Any],
    own_open: tuple[FindingRecord, ...],
    same_response_closure_ids: tuple[str, ...],
) -> int:
    """Bind decisions for prior Findings and same-response closures."""

    status_ids = sorted_finding_ids(
        (*(item.finding_id for item in own_open), *same_response_closure_ids)
    )
    maximum = min(MAX_NATIVE_REVIEW_DISPOSITIONS, len(status_ids))
    if not status_ids:
        _bind_required_empty_array(approved["properties"]["status_changes"])
        return maximum
    approved["properties"]["status_changes"].update(
        minItems=0,
        maxItems=maximum,
    )
    options: list[dict[str, Any]] = []
    for finding in own_open:
        option = json.loads(json.dumps(status))
        option["properties"]["finding_id"] = {
            "type": "string",
            "const": finding.finding_id,
        }
        if finding.finding_class is FindingClass.BLOCKER:
            option["properties"]["status"] = {
                "type": "string",
                "const": FindingStatus.CLOSED.value,
            }
        options.append(option)
    for finding_id in same_response_closure_ids:
        option = json.loads(json.dumps(status))
        option["properties"]["finding_id"] = {
            "type": "string",
            "const": finding_id,
        }
        option["properties"]["status"] = {
            "type": "string",
            "const": FindingStatus.CLOSED.value,
        }
        options.append(option)
    approved["properties"]["status_changes"]["items"] = {"oneOf": options}
    return maximum


def _bind_responsibility_route_collection(
    review: dict[str, Any], disposition_max: int
) -> None:
    review["properties"]["responsibility_routes"].update(
        maxItems=disposition_max,
        items={"$ref": "#/$defs/bound_responsibility_route"},
    )


def _bind_native_decision_collections(
    review: dict[str, Any],
    context: NativeReviewContext,
    disposition_max: int,
) -> None:
    _bind_responsibility_route_collection(review, disposition_max)
    _bind_plan_treatment_decision_collection(review, context)


def _bind_plan_treatment_decision_collection(
    review: dict[str, Any], context: NativeReviewContext
) -> None:
    decisions = review["properties"].get("plan_treatment_decisions")
    if not isinstance(decisions, dict):
        return
    if not context.plan_treatments:
        _bind_required_empty_array(decisions)
        return
    options: list[dict[str, Any]] = []
    for treatment in context.plan_treatments:
        option = {
            "type": "object",
            "properties": {
                "signature": {
                    "type": "string",
                    "const": treatment.signature,
                },
                "decision": {
                    "type": "string",
                    "enum": [item.value for item in PlanTreatmentDecisionKind],
                },
                "rationale": {
                    "type": "string",
                    "pattern": NONBLANK_TEXT_PATTERN,
                    "maxLength": 3000,
                },
            },
            "required": ["signature", "decision", "rationale"],
            "additionalProperties": False,
        }
        options.append(option)
    decisions.update(
        minItems=len(options),
        maxItems=len(options),
        items={"oneOf": options},
    )
    if "plan_treatment_decisions" not in review["required"]:
        review["required"].append("plan_treatment_decisions")


def _bound_review_definition(
    definition: Mapping[str, Any],
    *,
    finding_ids: tuple[str, ...],
) -> dict[str, Any]:
    projected = json.loads(json.dumps(definition))
    if finding_ids and "finding_id" in projected.get("properties", {}):
        projected["properties"]["finding_id"] = {
            "type": "string",
            "enum": list(finding_ids),
        }
    return projected


def _bound_review_result_definition(
    definitions: Mapping[str, Any],
    *,
    reviewer: AgentRole,
    decision: str,
    anchor_count: int,
) -> dict[str, Any]:
    projected = json.loads(json.dumps(definitions["review_result"]["allOf"][1]))
    projected["properties"]["schema_version"] = {
        "type": "string",
        "const": SCHEMA_VERSION,
    }
    projected["properties"]["result_type"] = {
        "type": "string",
        "const": "review_result",
    }
    projected["properties"]["reviewer"] = {
        "type": "string",
        "const": reviewer.value,
    }
    projected["properties"]["decision"] = {
        "type": "string",
        "const": decision,
    }
    if anchor_count:
        projected["properties"]["anchors"]["maxItems"] = anchor_count
    else:
        _bind_required_empty_array(projected["properties"]["anchors"])
    return projected


def _bind_required_empty_array(schema: dict[str, Any]) -> None:
    bind_provider_required_empty_array(schema, provider=ANTHROPIC_PROVIDER)


def _bound_stop_result_definition(
    definition: Mapping[str, Any], *, reviewer: AgentRole
) -> dict[str, Any]:
    projected = json.loads(json.dumps(definition["allOf"][1]))
    projected["properties"]["schema_version"] = {
        "type": "string",
        "const": SCHEMA_VERSION,
    }
    projected["properties"]["result_type"] = {
        "type": "string",
        "const": "stop_request",
    }
    projected["properties"]["reviewer"] = {
        "type": "string",
        "const": reviewer.value,
    }
    return projected


def validate_native_review_document(document: Mapping[str, Any]) -> None:
    """Validate only the closed transport shape, without local context."""
    _validate_active_native_review_field_shapes(document)
    schema = load_native_review_schema()
    try:
        validate_schema_document(document, schema)
    except SchemaMismatch as exc:
        location = ".".join(str(part) for part in exc.path) or "<response>"
        variants = describe_one_of_failure(document, schema, path=exc.path)
        detail = variants or exc.message
        raise NativeReviewContractError(
            NativeReviewErrorCode.SCHEMA_INVALID,
            f"schema validation failed at {location}: {detail}",
        ) from None


def parse_native_review_response(
    document: Mapping[str, Any], context: NativeReviewContext
) -> NativeReviewResponse:
    """Validate and parse a native response against immutable local context."""
    return _parse_native_review_response(
        document,
        context,
        expected_request_id=context.request_id,
    )


def _parse_native_review_response(
    document: Mapping[str, Any],
    context: NativeReviewContext,
    *,
    expected_request_id: str,
) -> NativeReviewResponse:
    validate_native_review_disposition_budget(document, context)
    validate_native_review_document(document)
    if document["request_id"] != expected_request_id:
        raise NativeReviewContractError(
            NativeReviewErrorCode.REQUEST_MISMATCH,
            "response request_id does not match bound context",
        )
    reviewer = AgentRole(document["reviewer"])
    if reviewer is not context.reviewer:
        raise NativeReviewContractError(
            NativeReviewErrorCode.REVIEWER_MISMATCH,
            "response reviewer does not match bound context",
        )
    if document["result_type"] == "stop_request":
        return NativeStopResult(
            request_id=document["request_id"],
            reviewer=reviewer,
            rule_id=document["rule_id"],
            rationale=document["rationale"],
            remediation_paths=tuple(document["remediation_paths"]),
        )

    if document["result_type"] == "branch_discovery_completed":
        if context.approval_marker is not ApprovalMarker.BRANCH_DISCOVERY:
            raise NativeReviewContractError(
                NativeReviewErrorCode.APPROVAL_INVALID,
                "BRANCH_DISCOVERY_COMPLETED requires a branch discovery request",
            )
        try:
            discovery_evidence = ReviewEvidence(**document["review_evidence"])
        except ValueError as exc:
            raise NativeReviewContractError(
                NativeReviewErrorCode.REVIEW_CONTENT_MISSING, str(exc)
            ) from exc
        discovery = NativeBranchDiscoveryCompleted(
            request_id=document["request_id"],
            reviewer=reviewer,
            scan_complete=document["scan_complete"],
            new_findings=tuple(
                _parse_native_finding(item) for item in document["new_findings"]
            ),
            occurrences=tuple(
                NativeFindingOccurrence(
                    item["finding_id"],
                    item["rationale"],
                    item.get("evidence_anchor_sha256"),
                )
                for item in document["occurrences"]
            ),
            evidence=discovery_evidence,
            pre_mortem=document["pre_mortem"],
            responsibility_routes=tuple(
                _parse_native_responsibility_route(item)
                for item in document["responsibility_routes"]
            ),
        )
        assert context.max_new_findings is not None
        if len(discovery.new_findings) == context.max_new_findings:
            return NativeStopResult(
                request_id=document["request_id"],
                reviewer=reviewer,
                rule_id=DISCOVERY_OUTPUT_LIMIT_RULE_ID,
                rationale=(
                    "Branch discovery reached the request-bound max_new_findings "
                    f"capacity of {context.max_new_findings}; the partial finding "
                    "set is not authoritative and automatic continuation is forbidden."
                ),
            )
        _validate_branch_discovery_completed(discovery, context)
        return discovery

    if context.approval_marker is ApprovalMarker.BRANCH_DISCOVERY:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "branch discovery cannot use approved or denied review_result",
        )

    evidence: ReviewEvidence | None = None
    if document["review_evidence"] is not None:
        try:
            evidence = ReviewEvidence(**document["review_evidence"])
        except ValueError as exc:
            raise NativeReviewContractError(
                NativeReviewErrorCode.REVIEW_CONTENT_MISSING, str(exc)
            ) from exc

    response = NativeReviewResult(
        request_id=document["request_id"],
        reviewer=reviewer,
        approved=document["decision"] == "approved",
        new_findings=tuple(_parse_native_finding(item) for item in document["new_findings"]),
        status_changes=tuple(
            NativeStatusChange(
                finding_id=item["finding_id"],
                status=FindingStatus(item["status"]),
                rationale=item["rationale"],
                closure=_parse_native_closure(item.get("closure")),
            )
            for item in document["status_changes"]
        ),
        reclassifications=tuple(
            NativeReclassification(
                finding_id=item["finding_id"],
                finding_class=FindingClass(item["finding_class"]),
                rationale=item["rationale"],
            )
            for item in document["reclassifications"]
        ),
        responsibility_routes=tuple(
            _parse_native_responsibility_route(item)
            for item in document.get("responsibility_routes", [])
        ),
        plan_treatment_decisions=tuple(
            PlanTreatmentDecision(
                item["signature"],
                PlanTreatmentDecisionKind(item["decision"]),
                item["rationale"],
            )
            for item in document.get("plan_treatment_decisions", [])
        ),
        anchors=tuple(
            NativeAnchor(
                anchor_id=item["anchor_id"],
                input_fixture=item["input_fixture"],
                expected=item["expected"],
                tolerance=item["tolerance"],
            )
            for item in document["anchors"]
        ),
        evidence=evidence,
        pre_mortem=document["pre_mortem"],
    )
    response = _coalesce_known_finding_occurrences(response, context)
    response = _absorb_redundant_route_status_changes(response)
    _validate_response_events(response, context)
    return response


def native_response_to_contract_result(
    response: NativeReviewResponse, context: NativeReviewContext
) -> ContractResult:
    """Convert a previously validated native response without side effects."""
    return _native_response_to_contract_result(
        response,
        context,
        expected_request_id=context.request_id,
    )


def _native_response_to_contract_result(
    response: NativeReviewResponse,
    context: NativeReviewContext,
    *,
    expected_request_id: str,
) -> ContractResult:
    if response.request_id != expected_request_id:
        raise NativeReviewContractError(
            NativeReviewErrorCode.REQUEST_MISMATCH,
            "parsed response does not match bound context",
        )
    if response.reviewer is not context.reviewer:
        raise NativeReviewContractError(
            NativeReviewErrorCode.REVIEWER_MISMATCH,
            "parsed response reviewer does not match bound context",
        )
    if isinstance(response, NativeStopResult):
        _require_native_text(
            response.rule_id,
            "stop rule id",
            max_length=200,
            code=NativeReviewErrorCode.STOP_CONTENT_INVALID,
        )
        _require_native_text(
            response.rationale,
            "stop rationale",
            max_length=3000,
            code=NativeReviewErrorCode.STOP_CONTENT_INVALID,
        )
        try:
            stop_request = StopRequest(
                response.rule_id,
                response.rationale,
                response.remediation_paths,
            )
        except ValueError as exc:
            raise NativeReviewContractError(
                NativeReviewErrorCode.STOP_CONTENT_INVALID, str(exc)
            ) from exc
        return ContractResult(
            reviewer=response.reviewer,
            approval=None,
            stopped=True,
            stop_request=stop_request,
            validation=context.validation_attestation,
            test_files=(),
            pre_mortem=None,
            evidence=None,
            findings=context.previous_findings,
            anchors=(),
            red_state_followup_slice=None,
        )

    if isinstance(response, NativeBranchDiscoveryCompleted):
        _validate_branch_discovery_completed(response, context)
        findings = _merge_branch_discovery_findings(response, context)
        return ContractResult(
            reviewer=response.reviewer,
            approval=None,
            stopped=False,
            stop_request=None,
            validation=context.validation_attestation,
            test_files=context.test_files,
            pre_mortem=response.pre_mortem,
            evidence=response.evidence,
            findings=findings,
            anchors=(),
            red_state_followup_slice=None,
            delivery_kind="branch_discovery_completed",
            occurrences=tuple(
                FindingOccurrence(
                    item.finding_id,
                    item.rationale,
                    item.evidence_anchor_sha256,
                )
                for item in response.occurrences
            ),
            scan_complete=response.scan_complete,
            responsibility_routes=tuple(
                sorted(
                    response.responsibility_routes,
                    key=lambda route: finding_id_sort_key(route.finding_id),
                )
            ),
        )

    response = _coalesce_known_finding_occurrences(response, context)
    response = _absorb_redundant_route_status_changes(response)
    _validate_response_events(response, context)
    findings = _merge_findings(response, context)
    anchors = _convert_anchors(response, context)
    _validate_decision(response, context, findings)
    return ContractResult(
        reviewer=response.reviewer,
        approval=response.approved,
        stopped=False,
        stop_request=None,
        validation=context.validation_attestation,
        test_files=context.test_files,
        pre_mortem=response.pre_mortem,
        evidence=response.evidence,
        findings=findings,
        anchors=anchors,
        red_state_followup_slice=(
            context.red_state_followup_slice if response.approved else None
        ),
        plan_treatment_decisions=response.plan_treatment_decisions,
        finding_closures=tuple(
            (update.finding_id, update.closure)
            for update in response.status_changes
            if update.closure is not None
        ),
        responsibility_routes=tuple(
            sorted(
                response.responsibility_routes,
                key=lambda route: finding_id_sort_key(route.finding_id),
            )
        ),
    )


def parse_native_contract_result(
    document: Mapping[str, Any], context: NativeReviewContext
) -> ContractResult:
    return native_response_to_contract_result(
        parse_native_review_response(document, context), context
    )


def parse_bound_native_contract_result(
    document: Mapping[str, Any], bound_context: BoundNativeReviewContext
) -> ContractResult:
    """Validate one live native response against its complete request binding."""
    if not isinstance(bound_context, BoundNativeReviewContext):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "live native review parsing requires BoundNativeReviewContext",
        )
    response = _parse_native_review_response(
        document,
        bound_context.context,
        expected_request_id=bound_context.request_id,
    )
    return _native_response_to_contract_result(
        response,
        bound_context.context,
        expected_request_id=bound_context.request_id,
    )


def next_native_finding_id(context: NativeReviewContext) -> str:
    """Return the first reviewer-owned finding id not reserved by the ledger."""
    prefix = "C"
    finding_ids = context.authoritative_finding_ids or tuple(
        item.finding_id for item in context.previous_findings
    )
    number = max(
        (
            int(finding_id.split("-", 1)[1])
            for finding_id in finding_ids
            if finding_id.startswith(prefix + "-")
        ),
        default=0,
    ) + 1
    return f"{prefix}-{number:02d}"


def canonical_native_review_json(document: Mapping[str, Any]) -> str:
    validate_native_review_document(document)
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _parse_native_finding(item: Mapping[str, Any]) -> NativeFinding:
    acceptance = item["acceptance_test"]
    parsed_acceptance: NativeAcceptance
    if acceptance["kind"] == "prose":
        parsed_acceptance = NativeProseAcceptance(acceptance["text"])
    else:
        parsed_acceptance = NativeValidationAcceptance(tuple(acceptance["argv"]))
    return NativeFinding(
        finding_id=item["finding_id"],
        finding_class=FindingClass(item["finding_class"]),
        summary=item["summary"],
        acceptance_test=parsed_acceptance,
        predecessor_finding_ref=item.get("predecessor_finding_ref"),
        evidence_anchor_sha256=item.get("evidence_anchor_sha256"),
        affected_paths=tuple(item["affected_paths"]),
    )


def _parse_native_closure(raw: object) -> NativeFindingClosure | None:
    if raw is None:
        return None
    assert isinstance(raw, Mapping)
    kind = NativeClosureKind(raw["kind"])
    if kind is NativeClosureKind.FIXED:
        return NativeFindingClosure(kind=kind)
    if kind is NativeClosureKind.PARTIAL:
        return NativeFindingClosure(
            kind=kind,
            evidence=raw["evidence"],
            remaining=raw["remaining"],
        )
    return NativeFindingClosure(
        kind=kind,
        rejection_reason=NativeRejectionReason(raw["rejection_reason"]),
        evidence=raw["evidence"],
    )


def _parse_native_responsibility_route(
    item: Mapping[str, Any],
) -> NativeResponsibilityRoute:
    try:
        responsibility = parse_responsibility(item["responsibility"])
    except ValueError as exc:
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_CONTENT_INVALID,
            f"responsibility route for {item['finding_id']} is invalid: {exc}",
        ) from exc
    return NativeResponsibilityRoute(
        finding_id=item["finding_id"],
        responsibility=responsibility,
        rationale=item["rationale"],
    )


def _validate_native_closure(
    finding_id: str, closure: NativeFindingClosure
) -> None:
    if closure.kind is NativeClosureKind.FIXED:
        if any(
            item is not None
            for item in (
                closure.rejection_reason,
                closure.evidence,
                closure.remaining,
            )
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                f"fixed closure for {finding_id} forbids rejection fields",
            )
        return
    if closure.kind is NativeClosureKind.PARTIAL:
        if closure.rejection_reason is not None:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                f"partial finding decision for {finding_id} forbids rejection_reason",
            )
        _require_native_text(
            closure.evidence,
            f"partial finding evidence for {finding_id}",
            max_length=3000,
            code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
        )
        _require_native_text(
            closure.remaining,
            f"partial finding remaining work for {finding_id}",
            max_length=3000,
            code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
        )
        return
    if closure.remaining is not None:
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_CONTENT_INVALID,
            f"rejected closure for {finding_id} forbids remaining work",
        )
    if closure.rejection_reason is None:
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_CONTENT_INVALID,
            f"rejected closure for {finding_id} requires rejection_reason",
        )
    _require_native_text(
        closure.evidence,
        f"rejected closure evidence for {finding_id}",
        max_length=3000,
        code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
    )


def _enable_native_review_finding_decision_schema(schema: dict[str, Any]) -> None:
    definitions = schema["$defs"]
    definitions["finding_responsibility"] = responsibility_json_schema(
        union_keyword="oneOf"
    )
    definitions["finding_closure"] = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "const": "fixed"},
                },
                "required": ["kind"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "const": "partial"},
                    "evidence": {
                        "type": "string",
                        "pattern": NONBLANK_TEXT_PATTERN,
                        "maxLength": 3000,
                    },
                    "remaining": {
                        "type": "string",
                        "pattern": NONBLANK_TEXT_PATTERN,
                        "maxLength": 3000,
                    },
                },
                "required": ["kind", "evidence", "remaining"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "const": "rejected"},
                    "rejection_reason": {
                        "type": "string",
                        "enum": [item.value for item in NativeRejectionReason],
                    },
                    "evidence": {
                        "type": "string",
                        "pattern": NONBLANK_TEXT_PATTERN,
                        "maxLength": 3000,
                    },
                },
                "required": ["kind", "rejection_reason", "evidence"],
                "additionalProperties": False,
            },
        ]
    }
    status = definitions["status_change"]
    status["properties"]["closure"] = {
        "oneOf": [
            {"$ref": "#/$defs/finding_closure"},
            {"type": "null"},
        ]
    }
    status["required"].append("closure")
    definitions["responsibility_route"] = {
        "type": "object",
        "properties": {
            "finding_id": {
                "type": "string",
                "pattern": "^C-(0[1-9]|[1-9][0-9]*)$",
            },
            "responsibility": {"$ref": "#/$defs/finding_responsibility"},
            "rationale": {
                "type": "string",
                "pattern": NONBLANK_TEXT_PATTERN,
                "maxLength": 3000,
            },
        },
        "required": ["finding_id", "responsibility", "rationale"],
        "additionalProperties": False,
    }
    result = definitions["review_result"]["allOf"][1]
    result["properties"]["responsibility_routes"] = {
        "type": "array",
        "maxItems": MAX_NATIVE_REVIEW_DISPOSITIONS,
        "items": {"$ref": "#/$defs/responsibility_route"},
    }
    result["required"].append("responsibility_routes")
    result["anyOf"].append(
        {"properties": {"responsibility_routes": {"minItems": 1}}}
    )
    definitions["plan_treatment_decision"] = {
        "type": "object",
        "properties": {
            "signature": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "decision": {
                "type": "string",
                "enum": [item.value for item in PlanTreatmentDecisionKind],
            },
            "rationale": {
                "type": "string",
                "pattern": NONBLANK_TEXT_PATTERN,
                "maxLength": 3000,
            },
        },
        "required": ["signature", "decision", "rationale"],
        "additionalProperties": False,
    }
    result["properties"]["plan_treatment_decisions"] = {
        "type": "array",
        "maxItems": MAX_NATIVE_REVIEW_DISPOSITIONS,
        "items": {"$ref": "#/$defs/plan_treatment_decision"},
    }


def _validate_active_native_review_field_shapes(
    document: Mapping[str, Any],
) -> None:
    status_changes = document.get("status_changes")
    if isinstance(status_changes, list):
        for item in status_changes:
            if not isinstance(item, Mapping):
                continue
            try:
                closes_finding = is_closed_finding_status(
                    FindingStatus(item.get("status"))
                )
            except (TypeError, ValueError):
                closes_finding = False
            if closes_finding:
                if "closure" not in item or item["closure"] is None:
                    raise NativeReviewContractError(
                        NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                        f"CLOSED status change for {item.get('finding_id', '<unknown>')} "
                        "requires closure",
                    )
                closure = item["closure"]
                if not isinstance(closure, Mapping):
                    continue
                if closure.get("kind") == NativeClosureKind.REJECTED.value:
                    if "rejection_reason" not in closure:
                        raise NativeReviewContractError(
                            NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                            "rejected closure is missing required field rejection_reason",
                        )
                    try:
                        NativeRejectionReason(closure["rejection_reason"])
                    except (TypeError, ValueError):
                        raise NativeReviewContractError(
                            NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                            "rejected closure has unknown rejection_reason "
                            f"{closure['rejection_reason']!r}",
                        ) from None
                    if not isinstance(closure.get("evidence"), str) or not closure[
                        "evidence"
                    ].strip():
                        raise NativeReviewContractError(
                            NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                            "rejected closure requires named evidence",
                        )
    routes = document.get("responsibility_routes")
    if isinstance(routes, list):
        for item in routes:
            if not isinstance(item, Mapping) or "responsibility" not in item:
                continue
            try:
                parse_responsibility(item["responsibility"])
            except ValueError as exc:
                raise NativeReviewContractError(
                    NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                    "responsibility route is invalid: " + str(exc),
                ) from exc


def _validate_plan_treatment_response_decisions(
    response: NativeReviewResult,
    context: NativeReviewContext,
) -> None:
    try:
        validate_plan_treatment_decisions(
            context.plan_treatments,
            response.plan_treatment_decisions,
            plan_approved=response.approved,
        )
    except ValueError as exc:
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_CONTENT_INVALID,
            str(exc),
        ) from exc
    if not response.approved or context.approval_marker is not ApprovalMarker.PLAN:
        return
    status_by_id = {item.finding_id: item for item in response.status_changes}
    for treatment in context.plan_treatments:
        for finding_id in treatment.finding_ids:
            update = status_by_id.get(finding_id)
            if treatment.treatment_kind is PlanTreatmentKind.IMPLEMENTATION:
                if update is not None and is_closed_finding_status(update.status):
                    raise NativeReviewContractError(
                        NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                        "implementation treatment cannot close its Finding in plan review",
                    )
                continue
            if (
                update is None
                or not is_closed_finding_status(update.status)
                or update.closure is None
                or update.closure.kind is not NativeClosureKind.REJECTED
                or update.closure.rejection_reason is not treatment.no_code_reason
            ):
                raise NativeReviewContractError(
                    NativeReviewErrorCode.FINDING_UPDATE_MISSING,
                    "reviewer-accepted No-Code treatment requires a matching "
                    f"typed closure for {finding_id}",
                )


def _validate_fixed_acceptance_evidence(
    finding: FindingRecord,
    context: NativeReviewContext,
) -> None:
    """Require red-before/green-after only for the explicit typed command."""

    try:
        command = finding_validation_command(finding)
    except ValidationMatrixError as exc:
        raise NativeReviewContractError(
            NativeReviewErrorCode.ACCEPTANCE_INVALID,
            f"typed acceptance test for {finding.finding_id} is invalid: {exc}",
        ) from exc
    if command is None:
        # Prose remains opaque.  No textual inference or substitute evidence is
        # permitted at this boundary.
        return
    before_fingerprint = context.pre_change_fingerprint
    if before_fingerprint is None:
        raise NativeReviewContractError(
            NativeReviewErrorCode.ACCEPTANCE_INVALID,
            f"fixed finding {finding.finding_id} lacks a bound pre-change fingerprint",
        )
    if before_fingerprint == context.diff_fingerprint:
        raise NativeReviewContractError(
            NativeReviewErrorCode.ACCEPTANCE_INVALID,
            f"fixed finding {finding.finding_id} has identical before and after fingerprints",
        )
    matching = tuple(
        item
        for item in finding.acceptance_measurements
        if item.command.argv == command.argv
    )
    before = tuple(
        item for item in matching if item.fingerprint == before_fingerprint
    )
    after = tuple(
        item for item in matching if item.fingerprint == context.diff_fingerprint
    )
    if len(before) != 1 or len(after) != 1:
        raise NativeReviewContractError(
            NativeReviewErrorCode.ACCEPTANCE_INVALID,
            f"fixed finding {finding.finding_id} requires one record-backed acceptance "
            "result at each bound before/after fingerprint",
        )
    if before[0].status is ValidationStatus.PASS:
        raise NativeReviewContractError(
            NativeReviewErrorCode.ACCEPTANCE_INVALID,
            f"typed acceptance test for {finding.finding_id} was already passing "
            f"at the pre-change fingerprint {before_fingerprint}; a previously "
            "green test does not prove a fix",
        )
    if after[0].status is not ValidationStatus.PASS:
        raise NativeReviewContractError(
            NativeReviewErrorCode.ACCEPTANCE_INVALID,
            f"typed acceptance test for {finding.finding_id} does not pass at "
            f"the post-change fingerprint {context.diff_fingerprint}",
        )


def _allowed_command_prefixes_suffix(
    allowed_prefixes: tuple[tuple[str, ...], ...],
) -> str:
    """Render configured Finding command families as bounded reviewer guidance."""

    if not allowed_prefixes:
        return ""
    rendered = ", ".join(
        f"`{ValidationCommand(argv=prefix).display}`" for prefix in allowed_prefixes
    )
    return f"; allowed command prefixes are: {rendered}"


def reject_passing_typed_acceptance_binding(
    finding_id: str,
    argv: tuple[str, ...],
    attestation: ValidationAttestation | None,
    fingerprint: str,
    allowed_prefixes: tuple[tuple[str, ...], ...],
) -> None:
    """Reject a typed BLOCKER command that cannot establish a red baseline."""

    if attestation is None or attestation.diff_fingerprint != fingerprint:
        return
    command = ValidationCommand(argv=argv)
    record = next(
        (
            item
            for item in attestation.records
            if item.command == command.display
        ),
        None,
    )
    if record is None or record.status is not ValidationStatus.PASS:
        return
    rendered_argv = json.dumps(list(argv), ensure_ascii=False, separators=(",", ":"))
    raise NativeReviewContractError(
        NativeReviewErrorCode.ACCEPTANCE_INVALID,
        f"typed acceptance test for {finding_id} uses command {rendered_argv}, "
        f"which is already passing at the current fingerprint {fingerprint}; "
        "bind a command that fails now so a later PASS can prove the fix"
        + _allowed_command_prefixes_suffix(allowed_prefixes),
        orchestrator_diagnostic=(
            OrchestratorDiagnostic.REVIEW_ACCEPTANCE_COMMAND_ALREADY_PASSING
        ),
    )


def _absorb_redundant_route_status_changes(
    response: NativeReviewResult,
) -> NativeReviewResult:
    """Let a route absorb the exact OPEN state that the route already implies."""

    status_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    for update in response.status_changes:
        status_counts[update.finding_id] = status_counts.get(update.finding_id, 0) + 1
    for route in response.responsibility_routes:
        route_counts[route.finding_id] = route_counts.get(route.finding_id, 0) + 1
    reclassified_ids = {item.finding_id for item in response.reclassifications}
    absorbed_ids = {
        update.finding_id
        for update in response.status_changes
        if not is_closed_finding_status(update.status)
        and update.closure is None
        and status_counts[update.finding_id] == 1
        and route_counts.get(update.finding_id) == 1
        and update.finding_id not in reclassified_ids
    }
    if not absorbed_ids:
        return response
    return replace(
        response,
        status_changes=tuple(
            update
            for update in response.status_changes
            if update.finding_id not in absorbed_ids
        ),
    )


def _validate_finding_event_collisions(
    response: NativeReviewResult,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Reject each ambiguous event shape with its own actionable diagnosis."""

    new_ids = [item.finding_id for item in response.new_findings]
    status_ids = [item.finding_id for item in response.status_changes]
    class_ids = [item.finding_id for item in response.reclassifications]
    route_ids = [item.finding_id for item in response.responsibility_routes]

    def duplicates(values: list[str]) -> tuple[str, ...]:
        return sorted_finding_ids(
            finding_id
            for finding_id in set(values)
            if values.count(finding_id) > 1
        )

    collision_checks = (
        (
            duplicates(new_ids),
            "finding IDs occur more than once in new_findings: ",
            "; keep exactly one new_findings entry per Finding ID",
            OrchestratorDiagnostic.REVIEW_FINDING_NEW_DUPLICATE,
        ),
        (
            duplicates(status_ids),
            "finding IDs occur more than once in status_changes: ",
            "; keep exactly one status_changes entry per Finding ID",
            OrchestratorDiagnostic.REVIEW_FINDING_STATUS_DUPLICATE,
        ),
        (
            duplicates(class_ids),
            "finding IDs occur more than once in reclassifications: ",
            "; keep exactly one reclassifications entry per Finding ID",
            OrchestratorDiagnostic.REVIEW_FINDING_RECLASSIFICATION_DUPLICATE,
        ),
        (
            duplicates(route_ids),
            "finding IDs occur more than once in responsibility_routes: ",
            "; keep exactly one responsibility_routes entry per Finding ID",
            OrchestratorDiagnostic.REVIEW_FINDING_ROUTE_DUPLICATE,
        ),
        (
            sorted_finding_ids(set(new_ids).intersection(class_ids)),
            "finding IDs occur in both new_findings and reclassifications: ",
            "; set the intended finding_class in new_findings only",
            OrchestratorDiagnostic.REVIEW_FINDING_NEW_RECLASSIFICATION_CONFLICT,
        ),
        (
            sorted_finding_ids(set(status_ids).intersection(class_ids)),
            "finding IDs occur in both status_changes and reclassifications: ",
            "; use only one of those decision fields per response",
            OrchestratorDiagnostic.REVIEW_FINDING_STATUS_RECLASSIFICATION_CONFLICT,
        ),
        (
            sorted_finding_ids(set(route_ids).intersection(status_ids)),
            "finding IDs occur in both responsibility_routes and status_changes with "
            "a non-redundant status decision: ",
            "; use responsibility_routes alone to keep them OPEN and transfer "
            "responsibility, or status_changes alone",
            OrchestratorDiagnostic.REVIEW_FINDING_ROUTE_STATUS_CONFLICT,
        ),
        (
            sorted_finding_ids(set(route_ids).intersection(class_ids)),
            "finding IDs occur in both responsibility_routes and reclassifications: ",
            "; use only one of those decision fields per response",
            OrchestratorDiagnostic.REVIEW_FINDING_ROUTE_RECLASSIFICATION_CONFLICT,
        ),
    )
    for finding_ids, prefix, suffix, diagnostic in collision_checks:
        if finding_ids:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
                prefix + ", ".join(finding_ids) + suffix,
                orchestrator_diagnostic=diagnostic,
            )
    return new_ids, status_ids, class_ids, route_ids


def _validate_response_events(
    response: NativeReviewResult, context: NativeReviewContext
) -> None:
    if not (
        response.new_findings
        or response.status_changes
        or response.reclassifications
        or response.responsibility_routes
        or response.plan_treatment_decisions
    ) and response.evidence is None:
        raise NativeReviewContractError(
            NativeReviewErrorCode.REVIEW_CONTENT_MISSING,
            "review requires at least one finding event or review evidence",
        )
    _validate_finding_event_content(response)
    _validate_opening_responsibility_kinds(response, context)
    _validate_plan_treatment_response_decisions(response, context)
    previous = {item.finding_id: item for item in context.previous_findings}
    previous_open_ids = frozenset(
        project_open_set(context.previous_findings).finding_ids
    )
    new_ids, status_ids, class_ids, route_ids = (
        _validate_finding_event_collisions(response)
    )
    if any(finding_id in previous for finding_id in new_ids):
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
            "new finding reuses a previous finding id",
        )
    new_by_id = {item.finding_id: item for item in response.new_findings}
    possible_new_ids = frozenset(_native_finding_id_window(context, size=32))
    for finding_id in (*status_ids, *class_ids):
        finding = previous.get(finding_id)
        if finding is None and finding_id in new_by_id and finding_id in status_ids:
            continue
        if finding is None:
            if finding_id in status_ids and finding_id in possible_new_ids:
                raise NativeReviewContractError(
                    NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
                    f"status change names new finding {finding_id} without "
                    "opening it in the same response",
                )
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
                f"finding update references unknown id {finding_id}",
            )
        if finding.origin.reporter is not context.reviewer:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
                f"reviewer does not own finding {finding_id}",
            )
        if finding_id not in previous_open_ids:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_REFERENCE_NOT_OPEN,
                f"finding update references non-open id {finding_id}",
            )
    new_id_set = frozenset(new_ids)
    for finding_id in route_ids:
        if finding_id in new_id_set:
            continue
        finding = previous.get(finding_id)
        if finding is None:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
                f"finding update references unknown id {finding_id}",
            )
        if finding.origin.reporter is not context.reviewer:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
                f"reviewer does not own finding {finding_id}",
            )
        if finding_id not in previous_open_ids:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_REFERENCE_NOT_OPEN,
                f"finding update references non-open id {finding_id}",
            )
    for update in response.status_changes:
        previous_finding = previous.get(update.finding_id)
        if previous_finding is None:
            if not is_closed_finding_status(update.status):
                raise NativeReviewContractError(
                    NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
                    f"new finding {update.finding_id} may only be paired with a "
                    "closing status change",
                )
            continue
        if (
            update.closure is not None
            and update.closure.kind is NativeClosureKind.FIXED
        ):
            _validate_fixed_acceptance_evidence(
                previous_finding, context
            )
        if update.rationale == previous_finding.status_rationale:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
                f"finding {update.finding_id} status update must add a new rationale",
            )
    expected_prefix = "C-"
    first_id = next_native_finding_id(context)
    first_number = int(first_id.split("-", 1)[1])
    expected_new_ids = [
        f"{expected_prefix}{first_number + index:02d}"
        for index in range(len(response.new_findings))
    ]
    if new_ids != expected_new_ids:
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_ID_INVALID,
            f"new findings must start at {first_id} and remain contiguous",
        )
    for finding in response.new_findings:
        if not finding.finding_id.startswith(expected_prefix):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_ID_INVALID,
                f"finding {finding.finding_id} does not belong to reviewer",
            )
        if (
            finding.finding_class is FindingClass.OBSERVATION
            and isinstance(finding.acceptance_test, NativeValidationAcceptance)
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.ACCEPTANCE_INVALID,
                "OBSERVATION cannot request a validation command",
            )
        if isinstance(finding.acceptance_test, NativeValidationAcceptance):
            if not any(
                matches_validation_family(finding.acceptance_test.argv, prefix)
                for prefix in context.validation_command_prefixes
            ):
                raise NativeReviewContractError(
                    NativeReviewErrorCode.ACCEPTANCE_INVALID,
                    "validation command is outside configured families",
                )
            reject_passing_typed_acceptance_binding(
                finding.finding_id,
                finding.acceptance_test.argv,
                context.validation_attestation,
                context.diff_fingerprint,
                context.validation_command_prefixes,
            )
    known_signatures: dict[str, list[str]] = {}
    for finding in context.effective_known_open_findings:
        known_signatures.setdefault(
            finding_record_signature(finding), []
        ).append(finding.finding_id)
    for finding in response.new_findings:
        acceptance = _native_finding_acceptance_text(finding)
        signature = finding_signature(
            acceptance,
            mentioned_repository_paths(finding.summary, acceptance),
        )
        existing_ids = known_signatures.get(signature)
        if existing_ids:
            detail = (
                f"new finding {finding.finding_id} duplicates known open finding "
                + ", ".join(sorted_finding_ids(existing_ids))
                + f" (signature {signature})"
            )
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_SIGNATURE_DUPLICATE,
                detail,
                operator_detail=detail,
            )
        known_signatures[signature] = [finding.finding_id]
    touched = set(status_ids) | set(class_ids) | set(route_ids)
    if context.approval_marker is ApprovalMarker.PLAN:
        touched.update(
            finding_id
            for treatment in context.plan_treatments
            for finding_id in treatment.finding_ids
        )
    missing_dispositions = tuple(
        finding.finding_id
        for finding in context.previous_findings
        if response.approved
        and context.approval_marker is not ApprovalMarker.SLICE
        and finding.finding_id in previous_open_ids
        and finding.origin.reporter is context.reviewer
        and finding.finding_id not in touched
    )
    if missing_dispositions:
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_UPDATE_MISSING,
            "missing updates for previous open findings: "
            + ", ".join(missing_dispositions),
        )
    if response.anchors and context.anchor_origin is None:
        raise NativeReviewContractError(
            NativeReviewErrorCode.ANCHOR_INVALID,
            "native anchors require a bound anchor_origin",
        )


def _validate_opening_responsibility_kinds(
    response: NativeReviewResult,
    context: NativeReviewContext,
) -> None:
    """Mirror the reducer's last-line rule at the retryable response edge."""

    new_ids = frozenset(item.finding_id for item in response.new_findings)
    opening_routes = tuple(
        route
        for route in response.responsibility_routes
        if route.finding_id in new_ids
    )
    if not opening_routes:
        return
    required_kind = REVIEW_OPENING_RESPONSIBILITY_KINDS.get(context.operation)
    if required_kind is None:
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "native review context has no supported opening responsibility rule",
        )
    for route in opening_routes:
        responsibility = route.responsibility
        if required_kind is ResponsibilityKind.SLICE and not isinstance(
            responsibility, SliceResponsibility
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                "slice review finding opening requires responsibility kind SLICE",
                orchestrator_diagnostic=(
                    OrchestratorDiagnostic.REVIEW_SLICE_OPENING_RESPONSIBILITY_INVALID
                ),
            )
        if required_kind is ResponsibilityKind.PLAN_REVISION and not isinstance(
            responsibility, PlanRevisionResponsibility
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                "plan review finding opening requires responsibility kind PLAN_REVISION",
                orchestrator_diagnostic=(
                    OrchestratorDiagnostic.REVIEW_PLAN_OPENING_RESPONSIBILITY_INVALID
                ),
            )
        if required_kind is ResponsibilityKind.BRANCH_PLANNING and not isinstance(
            responsibility, BranchPlanningResponsibility
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                "branch discovery finding opening requires responsibility kind BRANCH_PLANNING",
                orchestrator_diagnostic=(
                    OrchestratorDiagnostic.REVIEW_DISCOVERY_OPENING_RESPONSIBILITY_INVALID
                ),
            )


def _validate_finding_event_content(response: NativeReviewResult) -> None:
    for finding in response.new_findings:
        _require_native_text(
            finding.summary,
            "finding summary",
            max_length=3000,
            code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
        )
        if isinstance(finding.acceptance_test, NativeProseAcceptance):
            _require_native_text(
                finding.acceptance_test.text,
                "finding prose acceptance test",
                max_length=2000,
                code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
            )
            if finding.acceptance_test.text.strip().startswith("VALIDATE:"):
                raise NativeReviewContractError(
                    NativeReviewErrorCode.ACCEPTANCE_INVALID,
                    "prose acceptance must not use the reserved typed "
                    "VALIDATE prefix",
                )
    for update in response.status_changes:
        _require_native_text(
            update.rationale,
            "finding status rationale",
            max_length=3000,
            code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
        )
        if is_closed_finding_status(update.status) and update.closure is None:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                f"CLOSED status change for {update.finding_id} requires closure",
            )
        if (
            not is_closed_finding_status(update.status)
            and update.closure is not None
            and update.closure.kind is not NativeClosureKind.PARTIAL
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                f"OPEN status change for {update.finding_id} permits only partial",
            )
        if (
            is_closed_finding_status(update.status)
            and update.closure is not None
            and update.closure.kind is NativeClosureKind.PARTIAL
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                f"partial finding decision for {update.finding_id} must remain OPEN",
                orchestrator_diagnostic=(
                    OrchestratorDiagnostic.REVIEW_PARTIAL_FINDING_MUST_REMAIN_OPEN
                ),
            )
        if update.closure is not None:
            _validate_native_closure(update.finding_id, update.closure)
    for update in response.reclassifications:
        _require_native_text(
            update.rationale,
            "finding reclassification rationale",
            max_length=3000,
            code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
        )
    for route in response.responsibility_routes:
        _require_native_text(
            route.rationale,
            "responsibility routing rationale",
            max_length=3000,
            code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
        )


def _validate_branch_discovery_completed(
    response: NativeBranchDiscoveryCompleted,
    context: NativeReviewContext,
) -> None:
    """Validate E6 without routing inherited findings or deriving approval."""

    if context.approval_marker is not ApprovalMarker.BRANCH_DISCOVERY:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "BRANCH_DISCOVERY_COMPLETED requires its dedicated request marker",
        )
    if response.scan_complete is not True:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "branch discovery completion requires scan_complete=true",
        )
    assert context.max_new_findings is not None
    finding_count = len(response.new_findings)
    if finding_count > context.max_new_findings:
        raise NativeReviewContractError(
            NativeReviewErrorCode.SCHEMA_INVALID,
            "branch discovery new finding count "
            f"{finding_count} exceeds request-bound max_new_findings "
            f"{context.max_new_findings}",
        )
    if finding_count == context.max_new_findings:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            f"{DISCOVERY_OUTPUT_LIMIT_RULE_ID}: a capacity-sized discovery result "
            "must stop without making the partial finding set authoritative",
        )
    _require_native_text(
        response.pre_mortem,
        "branch discovery pre_mortem",
        max_length=3000,
        code=NativeReviewErrorCode.REVIEW_CONTENT_MISSING,
    )
    validation = context.validation_attestation
    if validation is None or not validation.complete or not validation.passed:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "branch discovery completion requires a complete PASS attestation",
        )
    if context.test_files and not context.test_changes_approved:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "branch discovery completion with test changes requires prior approval",
        )
    occurrence_ids = tuple(item.finding_id for item in response.occurrences)
    if len(occurrence_ids) != len(set(occurrence_ids)):
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
            "branch discovery occurrences must reference each finding at most once",
        )
    new_ids = frozenset(item.finding_id for item in response.new_findings)
    if any(
        route.finding_id not in new_ids
        for route in response.responsibility_routes
    ):
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
            "branch discovery responsibility routes may name only findings opened in the same response",
        )
    _validate_closed_finding_rediscoveries(response, context)
    previous = {item.finding_id: item for item in context.previous_findings}
    fake = NativeReviewResult(
        request_id=response.request_id,
        reviewer=response.reviewer,
        approved=False,
        new_findings=response.new_findings,
        status_changes=tuple(
            NativeStatusChange(
                finding_id=item.finding_id,
                status=FindingStatus.OPEN,
                rationale=item.rationale,
            )
            for item in response.occurrences
            if not is_closed_finding_status(previous[item.finding_id].status)
        ),
        reclassifications=(),
        anchors=(),
        evidence=response.evidence,
        pre_mortem=response.pre_mortem,
        responsibility_routes=response.responsibility_routes,
    )
    _validate_response_events(fake, context)


def _validate_closed_finding_rediscoveries(
    response: NativeBranchDiscoveryCompleted,
    context: NativeReviewContext,
) -> None:
    previous = {item.finding_id: item for item in context.previous_findings}
    bindings_by_id = {
        item.finding_id: item for item in context.closed_finding_bindings
    }
    bindings_by_signature: dict[str, list[ClosedFindingReviewBinding]] = {}
    for binding in context.closed_finding_bindings:
        bindings_by_signature.setdefault(binding.signature, []).append(binding)
    for occurrence in response.occurrences:
        finding = previous.get(occurrence.finding_id)
        if finding is None:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
                "branch discovery occurrence references an unknown Finding",
            )
        if not is_closed_finding_status(finding.status):
            if occurrence.evidence_anchor_sha256 is not None:
                raise NativeReviewContractError(
                    NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                    "open Finding occurrence forbids a closed-disposition anchor",
                )
            continue
        binding = bindings_by_id.get(occurrence.finding_id)
        if binding is None or not binding.anchor_unchanged:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
                "closed Finding occurrence requires an unchanged evidence anchor",
            )
        if (
            occurrence.evidence_anchor_sha256
            != binding.current_anchor.stability_sha256
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                "closed Finding occurrence carries another evidence anchor",
            )
    for finding in response.new_findings:
        acceptance = _native_finding_acceptance_text(finding)
        signature = finding_signature(
            acceptance,
            mentioned_repository_paths(finding.summary, acceptance),
        )
        matching = bindings_by_signature.get(signature, [])
        if not matching:
            if (
                finding.predecessor_finding_ref is not None
                or finding.evidence_anchor_sha256 is not None
            ):
                raise NativeReviewContractError(
                    NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
                    "new Finding names no matching closed predecessor",
                )
            continue
        predecessor = next(
            (
                item
                for item in matching
                if item.finding_id == finding.predecessor_finding_ref
            ),
            None,
        )
        if predecessor is None:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
                "rediscovered closed signature requires predecessor_finding_ref",
            )
        if predecessor.anchor_unchanged:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_SIGNATURE_DUPLICATE,
                "unchanged closed signature must be recorded as an occurrence",
            )
        if (
            finding.evidence_anchor_sha256
            != predecessor.current_anchor.stability_sha256
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                "new Finding generation carries another evidence anchor",
            )


def _merge_branch_discovery_findings(
    response: NativeBranchDiscoveryCompleted,
    context: NativeReviewContext,
) -> tuple[FindingRecord, ...]:
    previous = {item.finding_id: item for item in context.previous_findings}
    fake = NativeReviewResult(
        request_id=response.request_id,
        reviewer=response.reviewer,
        approved=False,
        new_findings=response.new_findings,
        status_changes=tuple(
            NativeStatusChange(
                finding_id=item.finding_id,
                status=FindingStatus.OPEN,
                rationale=item.rationale,
            )
            for item in response.occurrences
            if not is_closed_finding_status(previous[item.finding_id].status)
        ),
        reclassifications=(),
        anchors=(),
        evidence=response.evidence,
        pre_mortem=response.pre_mortem,
        responsibility_routes=response.responsibility_routes,
    )
    return _merge_findings(fake, context)


def _coalesce_known_finding_occurrences(
    response: NativeReviewResult, context: NativeReviewContext
) -> NativeReviewResult:
    """Turn a repeated finding family into a visible transition on its owner.

    A candidate finding id has no authoritative identity until the response is
    accepted.  When its signature names one offered open finding, its complete
    prose is therefore retained in that finding's next rationale instead of
    opening a second lineage.  Ambiguous or contradictory candidates still
    fail closed.
    """

    if not response.new_findings:
        return response
    first_number = int(next_native_finding_id(context).split("-", 1)[1])
    raw_ids = tuple(item.finding_id for item in response.new_findings)
    expected_raw_ids = tuple(
        f"C-{first_number + index:02d}"
        for index in range(len(response.new_findings))
    )
    if raw_ids != expected_raw_ids:
        return response

    previous = {item.finding_id: item for item in context.previous_findings}
    known_by_signature: dict[str, list[FindingRecord]] = {}
    for finding in context.effective_known_open_findings:
        known_by_signature.setdefault(finding_record_signature(finding), []).append(
            finding
        )

    retained: list[NativeFinding] = []
    occurrence_notes: dict[str, list[str]] = {}
    retained_signatures: dict[str, str] = {}
    for candidate in response.new_findings:
        acceptance = _native_finding_acceptance_text(candidate)
        signature = finding_signature(
            acceptance,
            mentioned_repository_paths(candidate.summary, acceptance),
        )
        known = known_by_signature.get(signature, [])
        if not known:
            earlier_id = retained_signatures.get(signature)
            if earlier_id is not None:
                detail = (
                    f"new finding {candidate.finding_id} duplicates new finding "
                    f"{earlier_id} (signature {signature})"
                )
                raise NativeReviewContractError(
                    NativeReviewErrorCode.FINDING_SIGNATURE_DUPLICATE,
                    detail,
                    operator_detail=detail,
                )
            retained_signatures[signature] = candidate.finding_id
            retained.append(candidate)
            continue
        if len(known) != 1 or known[0].finding_id not in previous:
            known_ids = sorted_finding_ids(item.finding_id for item in known)
            detail = (
                f"new finding {candidate.finding_id} has no unique offered open "
                "finding occurrence target; known matches: "
                + (", ".join(known_ids) or "none")
                + f" (signature {signature})"
            )
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_SIGNATURE_DUPLICATE,
                detail,
                operator_detail=detail,
            )
        target = known[0]
        if candidate.finding_class is not target.finding_class:
            detail = (
                f"new finding {candidate.finding_id} duplicates known open finding "
                f"{target.finding_id} but changes its class from "
                f"{target.finding_class.value} to {candidate.finding_class.value}"
            )
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_SIGNATURE_DUPLICATE,
                detail,
                operator_detail=detail,
            )
        occurrence_notes.setdefault(target.finding_id, []).append(
            f"Additional occurrence reported as {candidate.finding_id} and merged "
            f"into {target.finding_id}. Summary: {candidate.summary} "
            f"Acceptance test: {acceptance}"
        )

    if not occurrence_notes:
        return response

    status_by_id = {item.finding_id: item for item in response.status_changes}
    class_by_id = {item.finding_id: item for item in response.reclassifications}
    for target_id, notes in occurrence_notes.items():
        status_change = status_by_id.get(target_id)
        reclassification = class_by_id.get(target_id)
        if status_change is not None and is_closed_finding_status(
            status_change.status
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
                f"finding {target_id} cannot be closed while the same response "
                "reports an additional occurrence",
            )
        if status_change is not None and reclassification is not None:
            return response
        occurrence_rationale = "\n\n".join(notes)
        if status_change is not None:
            status_by_id[target_id] = NativeStatusChange(
                target_id,
                FindingStatus.OPEN,
                status_change.rationale + "\n\n" + occurrence_rationale,
            )
        elif reclassification is not None:
            class_by_id[target_id] = NativeReclassification(
                target_id,
                reclassification.finding_class,
                reclassification.rationale + "\n\n" + occurrence_rationale,
            )
        else:
            status_by_id[target_id] = NativeStatusChange(
                target_id, FindingStatus.OPEN, occurrence_rationale
            )

    renumbered = tuple(
        replace(finding, finding_id=f"C-{first_number + index:02d}")
        for index, finding in enumerate(retained)
    )
    return replace(
        response,
        new_findings=renumbered,
        status_changes=tuple(
            status_by_id[finding_id]
            for finding_id in sorted_finding_ids(status_by_id)
        ),
        reclassifications=tuple(
            class_by_id[finding_id]
            for finding_id in sorted_finding_ids(class_by_id)
        ),
    )


def _merge_findings(
    response: NativeReviewResult, context: NativeReviewContext
) -> tuple[FindingRecord, ...]:
    opened: list[FindingRecord] = []
    for native in response.new_findings:
        acceptance = _native_finding_acceptance_text(native)
        try:
            opened.append(
                FindingRecord(
                    finding_id=native.finding_id,
                    finding_class=native.finding_class,
                    status=FindingStatus.OPEN,
                    summary=native.summary,
                    acceptance_test=acceptance,
                    origin=FindingOrigin(
                        slice_id=context.slice_id,
                        round_number=context.round_number,
                        reporter=context.reviewer,
                    ),
                    predecessor_finding_ref=native.predecessor_finding_ref,
                    evidence_anchor_sha256=native.evidence_anchor_sha256,
                    affected_paths=native.affected_paths,
                ),
            )
        except ValueError as exc:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_ID_INVALID, str(exc)
            ) from exc
    try:
        return apply_reviewer_events(
            context.previous_findings,
            reviewer=context.reviewer,
            opened=tuple(opened),
            status_changes=tuple(
                ReviewerStatusChange(
                    update.finding_id, update.status, update.rationale
                )
                for update in response.status_changes
            ),
            reclassifications=tuple(
                ReviewerReclassification(
                    update.finding_id,
                    update.finding_class,
                    update.rationale,
                )
                for update in response.reclassifications
            ),
        )
    except ValueError as exc:
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN, str(exc)
        ) from exc


def _convert_anchors(
    response: NativeReviewResult, context: NativeReviewContext
) -> tuple[AnchorRecord, ...]:
    if not response.anchors:
        return ()
    assert context.anchor_origin is not None
    try:
        anchors = tuple(
            AnchorRecord(
                item.anchor_id,
                context.anchor_origin,
                item.input_fixture,
                item.expected,
                item.tolerance,
            )
            for item in response.anchors
        )
    except ValueError as exc:
        raise NativeReviewContractError(
            NativeReviewErrorCode.ANCHOR_INVALID, str(exc)
        ) from exc
    ids = tuple(item.anchor_id for item in anchors)
    if len(set(ids)) != len(ids):
        raise NativeReviewContractError(
            NativeReviewErrorCode.ANCHOR_INVALID,
            "native anchor ids must be unique",
        )
    return tuple(sorted(anchors, key=lambda item: item.anchor_id))


def _validate_decision(
    response: NativeReviewResult,
    context: NativeReviewContext,
    findings: tuple[FindingRecord, ...],
) -> None:
    previous_by_id = {item.finding_id: item for item in context.previous_findings}
    if not context.allow_new_observations:
        for finding in findings:
            if finding.finding_class is not FindingClass.OBSERVATION:
                continue
            previous = previous_by_id.get(finding.finding_id)
            if previous is None or previous.finding_class is not FindingClass.OBSERVATION:
                raise NativeReviewContractError(
                    NativeReviewErrorCode.APPROVAL_INVALID,
                    "review cannot introduce or reclassify to OBSERVATION",
                )
    open_findings = project_open_set(findings).findings
    own_open_blockers = tuple(
        item
        for item in open_findings
        if item.finding_class is FindingClass.BLOCKER
        and item.origin.reporter is context.reviewer
    )
    if (
        context.approval_marker is ApprovalMarker.PLAN
        and context.plan_treatments
    ):
        # E3 routes this complete cohort into the approved PlanAssignment.
        # These Findings remain open until their assigned Slice (or explicit
        # No-Code disposition) records the authoritative closure.
        own_open_blockers = ()
    _validate_slice_route_scopes(response, context, findings)
    if not response.approved:
        if not own_open_blockers:
            raise NativeReviewContractError(
                NativeReviewErrorCode.APPROVAL_INVALID,
                "denied review requires an open own BLOCKER",
            )
        return
    if context.approval_marker is ApprovalMarker.SLICE:
        routes_by_id = {
            item.finding_id: item.responsibility
            for item in response.responsibility_routes
        }
        unresolved_ids = sorted_finding_ids(
            finding.finding_id
            for finding in open_findings
            if not _routes_beyond_current_slice(
                routes_by_id.get(finding.finding_id), context
            )
        )
        if unresolved_ids:
            newly_opened_ids = frozenset(
                item.finding_id for item in response.new_findings
            )
            unresolved_new_ids = tuple(
                finding_id
                for finding_id in unresolved_ids
                if finding_id in newly_opened_ids
            )
            labeled_ids = ", ".join(
                f"{finding_id} ({'opened in this response' if finding_id in newly_opened_ids else 'existing before this response'})"
                for finding_id in unresolved_ids
            )
            detail = (
                "approval leaves open findings assigned to the current Slice: "
                + labeled_ids
                + "; for each ID, add either a status_changes entry with "
                "status=CLOSED and a typed fixed or evidenced-rejection closure, "
                "or a responsibility_routes entry to a named later Slice or to "
                "branch planning; a Finding opened in new_findings may be decided "
                "in the same response"
            )
            if unresolved_new_ids:
                raise NativeReviewContractError(
                    NativeReviewErrorCode.APPROVAL_INVALID,
                    detail,
                    orchestrator_diagnostic=(
                        OrchestratorDiagnostic.REVIEW_APPROVAL_NEW_FINDINGS_UNDECIDED
                    ),
                )
            raise NativeReviewContractError(
                NativeReviewErrorCode.APPROVAL_INVALID,
                detail,
            )
    validation = context.validation_attestation
    if (
        validation is None
        or not validation.complete
        or (
            not validation.passed
            and context.red_state_followup_slice is None
        )
    ):
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "approval requires a complete PASS attestation or named red-state follow-up",
        )
    if context.test_files and not context.test_changes_approved:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "approval with test changes requires prior approval",
        )
    if response.pre_mortem is None:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "approval requires pre_mortem",
        )
    _require_native_text(
        response.pre_mortem,
        "approval pre_mortem",
        max_length=3000,
        code=NativeReviewErrorCode.APPROVAL_INVALID,
    )
    if own_open_blockers:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "approval is invalid while an own BLOCKER is open",
        )


def _routes_beyond_current_slice(
    responsibility: object,
    context: NativeReviewContext,
) -> bool:
    if isinstance(responsibility, BranchPlanningResponsibility):
        return True
    if not isinstance(responsibility, SliceResponsibility):
        return False
    return (
        responsibility.target_run_id == context.run_id
        and responsibility.slice_id in _routable_planned_slice_ids(context)
    )


def _validate_slice_route_scopes(
    response: NativeReviewResult,
    context: NativeReviewContext,
    findings: tuple[FindingRecord, ...],
) -> None:
    """Reject a typed Slice route whose approved target scope cannot own it."""

    if context.approval_marker is not ApprovalMarker.SLICE:
        return
    findings_by_id = {item.finding_id: item for item in findings}
    planned_by_id = {str(item.slice_id): item for item in context.planned_slices}
    for route in response.responsibility_routes:
        responsibility = route.responsibility
        if not isinstance(responsibility, SliceResponsibility):
            continue
        finding = findings_by_id.get(route.finding_id)
        target = planned_by_id.get(responsibility.slice_id)
        if finding is None or target is None:
            continue
        uncovered = uncovered_route_paths(
            target.scope_paths, finding.affected_paths
        )
        if uncovered:
            raise NativeReviewContractError(
                NativeReviewErrorCode.APPROVAL_INVALID,
                f"route for finding {route.finding_id} cannot target Slice "
                f"{responsibility.slice_id} because its approved scope does not "
                f"cover affected_paths: {', '.join(uncovered)}; close "
                f"{route.finding_id}, reject it with named evidence, route it to a named "
                "later Slice whose approved scope covers every affected_path, "
                "or route it to branch planning",
                orchestrator_diagnostic=OrchestratorDiagnostic.REVIEW_APPROVAL_INVALID,
            )


def _slice_route_scope_retry_guidance(
    context: NativeReviewContext | None,
) -> str | None:
    """Describe invalid target scopes using only request-bound typed facts."""

    if context is None or context.approval_marker is not ApprovalMarker.SLICE:
        return None
    targets = {
        str(item.slice_id): item
        for item in context.planned_slices
        if str(item.slice_id) in _routable_planned_slice_ids(context)
    }
    clauses: list[str] = []
    for finding in project_open_set(context.previous_findings).findings:
        if finding.origin.reporter is not context.reviewer:
            continue
        for target_id, target in targets.items():
            uncovered = uncovered_route_paths(
                target.scope_paths, finding.affected_paths
            )
            if uncovered:
                clauses.append(
                    f"{finding.finding_id} -> Slice {target_id} is invalid because "
                    "its approved scope does not cover affected_paths: "
                    + ", ".join(uncovered)
                )
    if not clauses:
        return None
    suffix = (
        ". Close the affected Finding, reject it with named evidence, route it to "
        "a named later Slice whose approved scope covers every affected_path, or "
        "route it to branch planning."
    )
    prefix = "Correct the unfulfillable Slice route: "
    maximum_body = 3000 - len(prefix) - len(suffix)
    body: list[str] = []
    body_length = 0
    for clause in clauses:
        added = len(clause) + (2 if body else 0)
        if body_length + added > maximum_body:
            break
        body.append(clause)
        body_length += added
    if not body:
        return None
    return prefix + "; ".join(body) + suffix


def _slice_decision_retry_guidance(
    context: NativeReviewContext | None,
    rejected_response_shape: RejectedNativeResponseShape | None,
    *,
    include_new_findings: bool,
) -> str | None:
    """Reconstruct undecided Slice IDs from provider-free response structure."""

    if (
        context is None
        or context.approval_marker is not ApprovalMarker.SLICE
        or rejected_response_shape is None
        or rejected_response_shape.release_decision != "approved"
    ):
        return None
    new_count = next(
        (
            field.item_count
            for field in rejected_response_shape.fields
            if field.name == "new_findings" and field.value_kind == "array"
        ),
        None,
    )
    if new_count is None:
        return None
    if include_new_findings:
        first_number = int(next_native_finding_id(context).split("-", 1)[1])
        new_ids = tuple(
            f"C-{first_number + offset:02d}" for offset in range(new_count)
        )
    else:
        new_ids = ()
    closed_ids = {
        item.finding_id
        for item in rejected_response_shape.status_changes
        if is_closed_finding_status(FindingStatus(item.status))
    }
    open_ids = (
        set(project_open_set(context.previous_findings).finding_ids) | set(new_ids)
    ) - closed_ids
    routable_slice_ids = _routable_planned_slice_ids(context)
    routed_ids = {
        route.finding_id
        for route in rejected_response_shape.responsibility_routes
        if route.target is not None
        and (
            route.target.responsibility_kind == ResponsibilityKind.BRANCH_PLANNING.value
            or (
                route.target.responsibility_kind == ResponsibilityKind.SLICE.value
                and route.target.target_run_id == context.run_id
                and route.target.slice_id in routable_slice_ids
            )
        )
    }
    unresolved_ids = sorted_finding_ids(open_ids - routed_ids)
    if not unresolved_ids:
        return None
    new_id_set = frozenset(new_ids)
    existing = tuple(
        finding_id for finding_id in unresolved_ids if finding_id not in new_id_set
    )
    newly_opened = tuple(
        finding_id for finding_id in unresolved_ids if finding_id in new_id_set
    )
    groups = []
    if existing:
        groups.append("existing Finding IDs: " + ", ".join(existing))
    if newly_opened:
        groups.append(
            "Finding IDs opened in the rejected response: "
            + ", ".join(newly_opened)
        )
    return (
        "Decide every still-current-Slice Finding ("
        + "; ".join(groups)
        + ") in the same response. For each listed ID, either add status_changes "
        "with status=CLOSED and closure.kind=fixed, or closure.kind=rejected plus "
        "rejection_reason and named evidence; otherwise add responsibility_routes "
        "targeting a named later Slice or BRANCH_PLANNING. A Finding may be opened "
        "in new_findings and decided in that same response. A route already implies "
        "status=OPEN, so do not add a confirming OPEN status_changes entry."
    )


def native_review_context_binding(context: NativeReviewContext) -> dict[str, Any]:
    """Return the canonical provider-independent domain-context binding."""
    required_opening_kind = REVIEW_OPENING_RESPONSIBILITY_KINDS.get(
        context.operation
    )
    if required_opening_kind is None:
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "native review context has no supported opening responsibility rule",
        )
    authoritative_ids = (
        context.authoritative_finding_ids
        or tuple(item.finding_id for item in context.previous_findings)
    )
    _validate_slice_commit_decision_scope(context, authoritative_ids)
    binding = {
        "run_id": context.run_id,
        "work_unit_id": context.work_unit_id,
        "operation": context.operation,
        "opening_responsibility_kind": required_opening_kind.value,
        "diff_fingerprint": context.diff_fingerprint,
        "reviewer": context.reviewer.value,
        "approval_marker": context.approval_marker.value,
        "slice_id": context.slice_id,
        "round_number": context.round_number,
        "request_sequence": context.request_sequence,
        "previous_findings": [_finding_binding(item) for item in context.previous_findings],
        "known_open_finding_signatures": [
            {
                "finding_id": item.finding_id,
                "signature": finding_record_signature(item),
            }
            for item in context.effective_known_open_findings
        ],
        "slice_commit_decision_finding_ids": list(
            context.slice_commit_decision_finding_ids
        ),
        "authoritative_finding_ids": list(
            authoritative_ids
        ),
        "validation_attestation": _attestation_binding(
            context.validation_attestation
        ),
        "test_files": list(context.test_files),
        "test_changes_approved": context.test_changes_approved,
        "allow_new_observations": context.allow_new_observations,
        "anchor_origin": context.anchor_origin,
        "validation_command_prefixes": [
            list(prefix) for prefix in context.validation_command_prefixes
        ],
        "red_state_followup_slice": context.red_state_followup_slice,
        "pre_change_fingerprint": context.pre_change_fingerprint,
    }
    if context.approval_marker is ApprovalMarker.PLAN:
        binding["plan_artifact_path"] = context.plan_artifact_path
    if context.approval_marker is ApprovalMarker.BRANCH_DISCOVERY:
        binding["max_new_findings"] = context.max_new_findings
    binding["implementer_responsibility_proposals"] = [
            {
                "finding_id": item.finding_id,
                "responsibility": responsibility_document(item.responsibility),
                "rationale": item.rationale,
            }
            for item in context.implementer_responsibility_proposals
        ]
    binding["planned_slices"] = [
            {
                "slice_id": item.slice_id,
                "summary": item.summary,
                "scope_paths": list(item.scope_paths),
                "acceptance_criteria": [
                    {
                        "criterion_id": criterion.criterion_id,
                        "text": criterion.text,
                        "measured_against": criterion.measured_against.value,
                    }
                    for criterion in item.acceptance_criteria
                ],
            }
            for item in context.planned_slices
        ]
    treatments: list[dict[str, Any]] = []
    for treatment in context.plan_treatments:
        item: dict[str, Any] = {
                "signature": treatment.signature,
                "finding_ids": list(treatment.finding_ids),
                "treatment_kind": treatment.treatment_kind.value,
            }
        if treatment.treatment_kind is PlanTreatmentKind.IMPLEMENTATION:
            item["closing_slice_ids"] = list(treatment.closing_slice_ids)
        else:
            assert treatment.no_code_reason is not None
            item["no_code_reason"] = treatment.no_code_reason.value
            item["evidence"] = treatment.evidence
            item["evidence_paths"] = list(treatment.evidence_paths)
            item["affected_paths"] = list(treatment.affected_paths)
        treatments.append(item)
    binding["plan_treatments"] = treatments
    binding["closed_finding_bindings"] = [
            {
                "finding_id": item.finding_id,
                "signature": item.signature,
                "source_plan_assignment_record_id": (
                    item.source_plan_assignment_record_id
                ),
                "original_anchor": _no_code_anchor_binding(item.original_anchor),
                "current_anchor": _no_code_anchor_binding(item.current_anchor),
            }
            for item in context.closed_finding_bindings
    ]
    return binding


def _required_slice_commit_decision_finding_ids(
    context: NativeReviewContext,
    authoritative_ids: tuple[str, ...],
) -> tuple[str, ...]:
    return (
        authoritative_ids
        if context.approval_marker is ApprovalMarker.SLICE
        else ()
    )


def _initialize_slice_commit_decision_scope(
    context: NativeReviewContext,
    authoritative_ids: tuple[str, ...],
) -> None:
    object.__setattr__(
        context,
        "slice_commit_decision_finding_ids",
        _required_slice_commit_decision_finding_ids(context, authoritative_ids),
    )


def _validate_slice_commit_decision_scope(
    context: NativeReviewContext,
    authoritative_ids: tuple[str, ...],
) -> None:
    expected = _required_slice_commit_decision_finding_ids(
        context, authoritative_ids
    )
    if context.slice_commit_decision_finding_ids != expected:
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "communicated Slice-commit decision Finding set differs from the "
            "authoritative enforced set",
        )


def _native_finding_acceptance_text(finding: NativeFinding) -> str:
    if isinstance(finding.acceptance_test, NativeProseAcceptance):
        return finding.acceptance_test.text
    return FINDING_COMMAND_PREFIX + " " + json.dumps(
        list(finding.acceptance_test.argv),
        ensure_ascii=False,
        separators=(",", ":"),
    )


# Private compatibility alias for the original provider-independent request-id
# implementation.  New live callers use BoundNativeReviewContext instead.
_context_binding = native_review_context_binding


def _finding_binding(finding: FindingRecord) -> dict[str, Any]:
    binding = {
        "finding_id": finding.finding_id,
        "finding_class": finding.finding_class.value,
        "status": finding.status.value,
        "summary": finding.summary,
        "acceptance_test": finding.acceptance_test,
        "affected_paths": list(finding.affected_paths),
        "origin": {
            "slice_id": finding.origin.slice_id,
            "round_number": finding.origin.round_number,
            "reporter": finding.origin.reporter.value,
        },
        "responses": [
            {"decision": item.decision.value, "rationale": item.rationale}
            for item in finding.responses
        ],
        "status_rationale": finding.status_rationale,
        "class_history": [item.value for item in finding.class_history],
        "acceptance_measurements": [
            {
                "fingerprint": item.fingerprint,
                "argv": list(item.command.argv),
                "status": item.status.value,
                "exit_code": item.exit_code,
                "output_sha256": item.output_sha256,
                "attestation_id": item.attestation_id,
            }
            for item in finding.acceptance_measurements
        ],
    }
    if finding.predecessor_finding_ref is not None:
        binding["predecessor_finding_ref"] = finding.predecessor_finding_ref
    if finding.evidence_anchor_sha256 is not None:
        binding["evidence_anchor_sha256"] = finding.evidence_anchor_sha256
    return binding


def _no_code_anchor_binding(
    anchor: native_finding_decisions.NoCodeEvidenceAnchor,
) -> dict[str, Any]:
    return {
        "rejection_reason": anchor.rejection_reason.value,
        "provenance_fingerprint": anchor.provenance_fingerprint,
        "evidence_paths": list(anchor.evidence_paths),
        "evidence_content_sha256": anchor.evidence_content_sha256,
        "task_sha256": anchor.task_sha256,
        "scope_sha256": anchor.scope_sha256,
        "affected_paths": list(anchor.affected_paths),
        "affected_content_sha256": anchor.affected_content_sha256,
        "stability_sha256": anchor.stability_sha256,
    }


def _attestation_binding(
    attestation: ValidationAttestation | None,
) -> dict[str, Any] | None:
    if attestation is None:
        return None
    return {
        "attestation_id": attestation.attestation_id,
        "diff_fingerprint": attestation.diff_fingerprint,
        "expected_commands": list(attestation.expected_commands),
        "records": [
            {
                "status": item.status.value,
                "command": item.command,
                "exit_code": item.exit_code,
                "output": item.output,
            }
            for item in attestation.records
        ],
        "output_digest": attestation.output_digest,
        "summary": attestation.summary,
        "command_specs": [
            {
                "argv": list(item.argv),
                "legacy_shell": item.legacy_shell,
            }
            for item in attestation.command_specs
        ],
    }


def _require_native_text(
    value: object,
    label: str,
    *,
    max_length: int,
    code: NativeReviewErrorCode,
) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or len(value) > max_length
    ):
        raise NativeReviewContractError(
            code,
            f"{label} must be non-blank, NUL-free, and at most {max_length} characters",
        )
