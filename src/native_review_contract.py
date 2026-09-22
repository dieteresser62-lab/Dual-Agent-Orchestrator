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
)
from finding_reducer import (
    ReviewerReclassification,
    ReviewerStatusChange,
    apply_reviewer_events,
    is_closed_finding_status,
    project_open_set,
)
from finding_order import finding_id_sort_key, sorted_finding_ids
from finding_signature import (
    finding_record_signature,
    finding_signature,
    mentioned_repository_paths,
)
from native_provider_schema import (
    ANTHROPIC_PROVIDER,
    assert_projected_provider_schema,
    bind_required_empty_array as bind_provider_required_empty_array,
    defensive_provider_projection,
)
from orchestrator_diagnostics import OrchestratorDiagnostic, closed_retry_guidance
from rejected_response_shape import RejectedNativeResponseShape
from native_finding_decisions import (
    NativeClosureKind,
    NativeFindingClosure,
    NativeRejectionReason,
)


SCHEMA_VERSION = "native-agent-review-result-v2"
MAX_NATIVE_REVIEW_DISPOSITIONS = 32
DEFAULT_FINAL_REVIEW_MAX_NEW_FINDINGS = 128
MAX_FINAL_REVIEW_NEW_FINDINGS = 512
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
        "For every Finding ID named by the rejection, close it or escalate it "
        "to BLOCKER."
    ),
    NativeReviewErrorCode.DORMANT_FINDING_DECISION_FIELD: (
        "Return the active result shape without retired finding-decision fields."
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
    actual_items = len(status_changes) + len(reclassifications)
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
            f"reclassifications={len(reclassifications)}"
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

    return sum(
        item.origin.reporter is context.reviewer
        for item in project_open_set(context.previous_findings).findings
    )


@dataclass(frozen=True, slots=True)
class NativeProseAcceptance:
    text: str


@dataclass(frozen=True, slots=True)
class NativeFinding:
    finding_id: str
    finding_class: FindingClass
    summary: str
    acceptance_test: NativeProseAcceptance
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


@dataclass(frozen=True, slots=True)
class NativeFindingOccurrence:
    finding_id: str
    rationale: str
    evidence_anchor_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class NativeFinalReviewCompleted:
    """E6 delivery: a completed scan, deliberately not an approval decision."""

    request_id: str
    reviewer: AgentRole
    scan_complete: bool
    new_findings: tuple[NativeFinding, ...]
    occurrences: tuple[NativeFindingOccurrence, ...]
    evidence: ReviewEvidence
    pre_mortem: str


@dataclass(frozen=True, slots=True)
class NativeStopResult:
    request_id: str
    reviewer: AgentRole
    rule_id: str
    rationale: str
    remediation_paths: tuple[str, ...] = ()


NativeReviewResponse: TypeAlias = (
    NativeReviewResult | NativeFinalReviewCompleted | NativeStopResult
)


def _validated_final_review_capacity(
    approval_marker: ApprovalMarker,
    capacity: int | None,
) -> int | None:
    if approval_marker is not ApprovalMarker.FINAL_REVIEW:
        if capacity is not None:
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "max_new_findings is valid only for a final review review",
            )
        return None
    normalized = (
        DEFAULT_FINAL_REVIEW_MAX_NEW_FINDINGS
        if capacity is None
        else capacity
    )
    if (
        isinstance(normalized, bool)
        or not isinstance(normalized, int)
        or normalized < 1
        or normalized > MAX_FINAL_REVIEW_NEW_FINDINGS
    ):
        raise NativeReviewContractError(
            NativeReviewErrorCode.CONTEXT_INVALID,
            "final review max_new_findings must be an integer from 1 to "
            f"{MAX_FINAL_REVIEW_NEW_FINDINGS}",
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
    red_state_followup_slice: str | None = None
    plan_artifact_path: str | None = None
    final_review_pending_count: int | None = None
    max_new_findings: int | None = None
    planned_slices: tuple[PlannedSlice, ...] = ()
    request_sequence: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_new_findings",
            _validated_final_review_capacity(
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


@dataclass(frozen=True, slots=True)
class BoundNativeReviewContext:
    """A live-provider context bound to one complete canonical request."""

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
    _enable_final_review_result_schema(schema)
    try:
        check_schema(schema, location="<native-review-schema>")
    except SchemaDefinitionError as exc:
        raise NativeReviewContractError(
            NativeReviewErrorCode.SCHEMA_INVALID,
            str(exc),
            source=NativeReviewRejectionSource.REQUEST_LEDGER,
        ) from exc
    return schema


def _enable_final_review_result_schema(schema: dict[str, Any]) -> None:
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
    definitions["final_review_occurrence"] = {
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
    definitions["final_review_completed"] = {
        "allOf": [
            {"$ref": "#/$defs/common"},
            {
                "type": "object",
                "properties": {
                    "schema_version": {"const": SCHEMA_VERSION},
                    "result_type": {"const": "final_review_completed"},
                    "request_id": {
                        "type": "string",
                        "pattern": "^native-review-request-[0-9a-f]{64}$",
                    },
                    "reviewer": {"const": "claude"},  # allowlist:provider -- canonical reviewer role
                    "scan_complete": {"type": "boolean"},
                    "new_findings": {
                        "type": "array",
                        "maxItems": MAX_FINAL_REVIEW_NEW_FINDINGS,
                        "items": {"$ref": "#/$defs/finding"},
                    },
                    "occurrences": {
                        "type": "array",
                        "maxItems": 10000,
                        "items": {
                            "$ref": "#/$defs/final_review_occurrence"
                        },
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
        "final_review_completed",
    ]
    schema["oneOf"].append(
        {"$ref": "#/$defs/final_review_completed"}
    )


def _final_review_provider_response_schema(
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
    finding["properties"]["predecessor_finding_ref"] = {"type": "null"}
    finding["properties"]["evidence_anchor_sha256"] = {
        "oneOf": [
            {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            {"type": "null"},
        ]
    }
    finding["required"].extend(
        ["predecessor_finding_ref", "evidence_anchor_sha256"]
    )
    definitions["bound_final_review_finding"] = finding
    open_ids = tuple(
        item.finding_id for item in context.effective_known_open_findings
    )
    known_ids = sorted_finding_ids(open_ids)
    occurrence_options: list[dict[str, Any]] = []
    for finding_id in known_ids:
        option = json.loads(
            json.dumps(definitions["final_review_occurrence"])
        )
        option["properties"]["finding_id"] = {
            "type": "string",
            "const": finding_id,
        }
        option["properties"]["evidence_anchor_sha256"] = {"type": "null"}
        option["required"].append("evidence_anchor_sha256")
        occurrence_options.append(option)
    occurrence = (
        {"oneOf": occurrence_options}
        if occurrence_options
        else _bound_review_definition(
            definitions["final_review_occurrence"], finding_ids=()
        )
    )
    definitions["bound_final_review_occurrence"] = occurrence
    completed = json.loads(
        json.dumps(definitions["final_review_completed"]["allOf"][1])
    )
    completed["properties"]["schema_version"] = {
        "type": "string",
        "const": SCHEMA_VERSION,
    }
    completed["properties"]["result_type"] = {
        "type": "string",
        "const": "final_review_completed",
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
        "$ref": "#/$defs/bound_final_review_finding"
    }
    if known_ids:
        completed["properties"]["occurrences"].update(
            maxItems=len(known_ids),
            items={"$ref": "#/$defs/bound_final_review_occurrence"},
        )
    else:
        _bind_required_empty_array(completed["properties"]["occurrences"])
    definitions["bound_final_review_completed"] = completed
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
    definitions["bound_final_review_stop"] = stop
    return {
        "title": "Native final review writer projection",
        "type": "object",
        "properties": {
            "result": {
                "oneOf": [
                    {"$ref": "#/$defs/bound_final_review_completed"},
                    {"$ref": "#/$defs/bound_final_review_stop"},
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
    if context.approval_marker is ApprovalMarker.FINAL_REVIEW:
        projected_schema = _final_review_provider_response_schema(
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
    own_disposition_max = min(
        MAX_NATIVE_REVIEW_DISPOSITIONS, len(own_open_ids)
    )
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

    if document["result_type"] == "final_review_completed":
        if context.approval_marker is not ApprovalMarker.FINAL_REVIEW:
            raise NativeReviewContractError(
                NativeReviewErrorCode.APPROVAL_INVALID,
                "FINAL_REVIEW_COMPLETED requires a final review request",
            )
        try:
            discovery_evidence = ReviewEvidence(**document["review_evidence"])
        except ValueError as exc:
            raise NativeReviewContractError(
                NativeReviewErrorCode.REVIEW_CONTENT_MISSING, str(exc)
            ) from exc
        discovery = NativeFinalReviewCompleted(
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
        )
        assert context.max_new_findings is not None
        if len(discovery.new_findings) == context.max_new_findings:
            return NativeStopResult(
                request_id=document["request_id"],
                reviewer=reviewer,
                rule_id=DISCOVERY_OUTPUT_LIMIT_RULE_ID,
                rationale=(
                    "Final review reached the request-bound max_new_findings "
                    f"capacity of {context.max_new_findings}; the partial finding "
                    "set is not authoritative and automatic continuation is forbidden."
                ),
            )
        _validate_final_review_completed(discovery, context)
        return discovery

    if context.approval_marker is ApprovalMarker.FINAL_REVIEW:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "final review cannot use approved or denied review_result",
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

    if isinstance(response, NativeFinalReviewCompleted):
        _validate_final_review_completed(response, context)
        findings = _merge_final_review_findings(response, context)
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
            delivery_kind="final_review_completed",
            occurrences=tuple(
                FindingOccurrence(
                    item.finding_id,
                    item.rationale,
                    item.evidence_anchor_sha256,
                )
                for item in response.occurrences
            ),
            scan_complete=response.scan_complete,
        )

    response = _coalesce_known_finding_occurrences(response, context)
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
        finding_closures=tuple(
            (update.finding_id, update.closure)
            for update in response.status_changes
            if update.closure is not None
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
    return NativeFinding(
        finding_id=item["finding_id"],
        finding_class=FindingClass(item["finding_class"]),
        summary=item["summary"],
        acceptance_test=NativeProseAcceptance(acceptance["text"]),
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
    return NativeFindingClosure(
        kind=kind,
        rejection_reason=NativeRejectionReason(raw["rejection_reason"]),
        evidence=raw["evidence"],
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
            )
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                f"fixed closure for {finding_id} forbids rejection fields",
            )
        return
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
    result = definitions["review_result"]["allOf"][1]


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
def _validate_finding_event_collisions(
    response: NativeReviewResult,
) -> tuple[list[str], list[str], list[str]]:
    """Reject each ambiguous event shape with its own actionable diagnosis."""

    new_ids = [item.finding_id for item in response.new_findings]
    status_ids = [item.finding_id for item in response.status_changes]
    class_ids = [item.finding_id for item in response.reclassifications]

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
    )
    for finding_ids, prefix, suffix, diagnostic in collision_checks:
        if finding_ids:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
                prefix + ", ".join(finding_ids) + suffix,
                orchestrator_diagnostic=diagnostic,
            )
    return new_ids, status_ids, class_ids


def _validate_response_events(
    response: NativeReviewResult, context: NativeReviewContext
) -> None:
    if not (
        response.new_findings
        or response.status_changes
        or response.reclassifications
    ) and response.evidence is None:
        raise NativeReviewContractError(
            NativeReviewErrorCode.REVIEW_CONTENT_MISSING,
            "review requires at least one finding event or review evidence",
        )
    _validate_finding_event_content(response)
    previous = {item.finding_id: item for item in context.previous_findings}
    previous_open_ids = frozenset(
        project_open_set(context.previous_findings).finding_ids
    )
    new_ids, status_ids, class_ids = (
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
    touched = set(status_ids) | set(class_ids)
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


def _validate_finding_event_content(response: NativeReviewResult) -> None:
    for finding in response.new_findings:
        _require_native_text(
            finding.summary,
            "finding summary",
            max_length=3000,
            code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
        )
        _require_native_text(
            finding.acceptance_test.text,
            "finding prose acceptance test",
            max_length=2000,
            code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
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
        if not is_closed_finding_status(update.status) and update.closure is not None:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_CONTENT_INVALID,
                f"OPEN status change for {update.finding_id} forbids a closure",
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
def _validate_final_review_completed(
    response: NativeFinalReviewCompleted,
    context: NativeReviewContext,
) -> None:
    """Validate E6 without routing inherited findings or deriving approval."""

    if context.approval_marker is not ApprovalMarker.FINAL_REVIEW:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "FINAL_REVIEW_COMPLETED requires its dedicated request marker",
        )
    if response.scan_complete is not True:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "final review completion requires scan_complete=true",
        )
    assert context.max_new_findings is not None
    finding_count = len(response.new_findings)
    if finding_count > context.max_new_findings:
        raise NativeReviewContractError(
            NativeReviewErrorCode.SCHEMA_INVALID,
            "final review new finding count "
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
        "final review pre_mortem",
        max_length=3000,
        code=NativeReviewErrorCode.REVIEW_CONTENT_MISSING,
    )
    validation = context.validation_attestation
    if validation is None or not validation.complete or not validation.passed:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "final review completion requires a complete PASS attestation",
        )
    if context.test_files and not context.test_changes_approved:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "final review completion with test changes requires prior approval",
        )
    occurrence_ids = tuple(item.finding_id for item in response.occurrences)
    if len(occurrence_ids) != len(set(occurrence_ids)):
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
            "final review occurrences must reference each finding at most once",
        )
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
    )
    _validate_response_events(fake, context)


def _merge_final_review_findings(
    response: NativeFinalReviewCompleted,
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
            escalate_unclosed_rejections=True,
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
    if not response.approved:
        if not own_open_blockers:
            raise NativeReviewContractError(
                NativeReviewErrorCode.APPROVAL_INVALID,
                "denied review requires an open own BLOCKER",
            )
        return
    if context.approval_marker is ApprovalMarker.SLICE:
        unresolved_ids = sorted_finding_ids(
            finding.finding_id
            for finding in open_findings
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
                "or escalate the Finding to BLOCKER; a Finding opened in "
                "new_findings may be decided in the same response"
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
    if context.approval_marker is ApprovalMarker.PLAN and open_findings:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "plan approval cannot leave open findings: "
            + ", ".join(item.finding_id for item in open_findings),
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
    unresolved_ids = sorted_finding_ids(open_ids)
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
        "rejection_reason and named evidence, or escalate it to BLOCKER. A Finding "
        "may be opened in new_findings and decided in that same response."
    )


def native_review_context_binding(context: NativeReviewContext) -> dict[str, Any]:
    """Return the canonical provider-independent domain-context binding."""
    authoritative_ids = (
        context.authoritative_finding_ids
        or tuple(item.finding_id for item in context.previous_findings)
    )
    _validate_slice_commit_decision_scope(context, authoritative_ids)
    binding = {
        "run_id": context.run_id,
        "work_unit_id": context.work_unit_id,
        "operation": context.operation,
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
        "red_state_followup_slice": context.red_state_followup_slice,
    }
    if context.approval_marker is ApprovalMarker.PLAN:
        binding["plan_artifact_path"] = context.plan_artifact_path
    if context.approval_marker is ApprovalMarker.FINAL_REVIEW:
        binding["max_new_findings"] = context.max_new_findings
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
    return finding.acceptance_test.text


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
    }
    if finding.predecessor_finding_ref is not None:
        binding["predecessor_finding_ref"] = finding.predecessor_finding_ref
    if finding.evidence_anchor_sha256 is not None:
        binding["evidence_anchor_sha256"] = finding.evidence_anchor_sha256
    return binding


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
