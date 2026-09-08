"""Versioned native JSON review results with state-v3 domain parity.

This module is deliberately provider- and persistence-free.  It validates one
closed transport document, binds it to immutable local review context, and
converts it to the existing :class:`contracts.ContractResult` domain model.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ReviewEvidence,
    StopRequest,
    ValidationAttestation,
)
from finding_reducer import (
    ReviewerReclassification,
    ReviewerStatusChange,
    apply_reviewer_events,
    project_open_set,
)
from validation_matrix import FINDING_COMMAND_PREFIX, matches_validation_family
from native_provider_schema import defensive_provider_projection


SCHEMA_VERSION = "native-agent-review-result-v2"
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
    ACCEPTANCE_INVALID = "acceptance-invalid"
    ANCHOR_INVALID = "anchor-invalid"
    REVIEW_CONTENT_MISSING = "review-content-missing"
    STOP_CONTENT_INVALID = "stop-content-invalid"
    APPROVAL_INVALID = "approval-invalid"


NATIVE_REVIEW_RETRYABLE_FORM_CODES: frozenset[NativeReviewErrorCode] = frozenset(
    {
        NativeReviewErrorCode.SCHEMA_INVALID,  # Provider output can satisfy the closed shape on another attempt.
        NativeReviewErrorCode.FINDING_ID_INVALID,  # Provider output can select the next allowed finding id.
        NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,  # Provider output can reference a finding present in the bound request.
        NativeReviewErrorCode.FINDING_REFERENCE_NOT_OPEN,  # Provider output can omit updates for findings that are no longer open.
        NativeReviewErrorCode.FINDING_EVENT_CONFLICT,  # Provider output can emit one non-conflicting event per finding.
        NativeReviewErrorCode.FINDING_UPDATE_MISSING,  # Provider output can supply every required own-finding disposition.
        NativeReviewErrorCode.FINDING_CONTENT_INVALID,  # Provider output can replace malformed finding content.
        NativeReviewErrorCode.ACCEPTANCE_INVALID,  # Provider output can use the allowed typed acceptance form.
        NativeReviewErrorCode.ANCHOR_INVALID,  # Provider output can supply anchors consistent with the bound review.
        NativeReviewErrorCode.REVIEW_CONTENT_MISSING,  # Provider output can supply the required review evidence.
        NativeReviewErrorCode.STOP_CONTENT_INVALID,  # Provider output can supply a complete typed stop request.
        NativeReviewErrorCode.APPROVAL_INVALID,  # Provider output can make its decision consistent with its finding events.
    }
)

# Local context construction cannot be repaired by asking the provider again.
assert NativeReviewErrorCode.CONTEXT_INVALID not in NATIVE_REVIEW_RETRYABLE_FORM_CODES
# A foreign request id is a binding violation, not a response-form failure.
assert NativeReviewErrorCode.REQUEST_MISMATCH not in NATIVE_REVIEW_RETRYABLE_FORM_CODES
# A foreign reviewer is an identity violation, not a response-form failure.
assert NativeReviewErrorCode.REVIEWER_MISMATCH not in NATIVE_REVIEW_RETRYABLE_FORM_CODES


def is_retryable_native_review_form_error(error: BaseException) -> bool:
    """Return whether a provider can repair this review response on retry."""
    return (
        isinstance(error, NativeReviewContractError)
        and error.code in NATIVE_REVIEW_RETRYABLE_FORM_CODES
    )


class NativeReviewContractError(ValueError):
    """A stable, machine-readable native review contract failure."""

    def __init__(self, code: NativeReviewErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


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


@dataclass(frozen=True, slots=True)
class NativeStatusChange:
    finding_id: str
    status: FindingStatus
    rationale: str


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
class NativeStopResult:
    request_id: str
    reviewer: AgentRole
    rule_id: str
    rationale: str
    remediation_paths: tuple[str, ...] = ()


NativeReviewResponse: TypeAlias = NativeReviewResult | NativeStopResult


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
    validation_attestation: ValidationAttestation | None = None
    test_files: tuple[str, ...] = ()
    test_changes_approved: bool = False
    allow_new_observations: bool = True
    anchor_origin: str | None = None
    validation_command_prefixes: tuple[tuple[str, ...], ...] = ()
    red_state_followup_slice: str | None = None
    plan_artifact_path: str | None = None

    def __post_init__(self) -> None:
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
        if previous_ids != tuple(sorted(set(previous_ids))):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "previous findings must be sorted and unique",
            )
        normalized_tests = tuple(sorted(set(self.test_files)))
        if normalized_tests != self.test_files or any(
            not item.strip() for item in self.test_files
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "test files must be sorted, unique, and non-empty",
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
        )
    try:
        check_schema(schema, location="<native-review-schema>")
    except SchemaDefinitionError as exc:
        raise NativeReviewContractError(
            NativeReviewErrorCode.SCHEMA_INVALID, str(exc)
        ) from exc
    return schema


def native_review_provider_response_schema(
    context: NativeReviewContext,
    *,
    base_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the immutable reader schema into one bound Claude writer schema.

    The projection is selected solely by typed, request-bound review context.
    In particular, the free-form operation name is deliberately not consulted.
    The reader schema is the closed v2 result contract; live generation is
    constrained here before the provider is invoked.
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
    definitions["review_result"]["allOf"][1]["properties"]["anchors"][
        "maxItems"
    ] = 64 if context.anchor_origin is not None else 0
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
    branch = {
        ApprovalMarker.PLAN: "plan",
        ApprovalMarker.SLICE: (
            "slice_initial"
            if context.round_number == 1 and context.allow_new_observations
            else "slice_convergence"
        ),
        ApprovalMarker.FINAL: "final",
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
    observations_allowed = (
        context.approval_marker is not ApprovalMarker.FINAL
        and context.allow_new_observations
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
    approved["properties"]["new_findings"].update(
        maxItems=approved_new_max,
        items={"$ref": "#/$defs/bound_approved_finding"},
    )
    approved["properties"]["status_changes"].update(
        minItems=0,
        maxItems=len(own_open_ids),
        items={"$ref": "#/$defs/bound_status_change"},
    )
    if own_open_ids:
        status_options: list[dict[str, Any]] = []
        for finding in own_open:
            option = json.loads(json.dumps(status))
            option["properties"]["finding_id"] = {
                "type": "string",
                "const": finding.finding_id,
            }
            if (
                context.approval_marker is ApprovalMarker.FINAL
                or finding.finding_class is FindingClass.BLOCKER
            ):
                option["properties"]["status"] = {
                    "type": "string",
                    "const": FindingStatus.CLOSED.value,
                }
            status_options.append(option)
        approved["properties"]["status_changes"]["items"] = {
            "oneOf": status_options
        }
    approved["properties"]["reclassifications"].update(maxItems=0)
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
            maxItems=len(own_open_ids),
        )
        approved["properties"]["reclassifications"].update(
            minItems=0,
            maxItems=len(own_open_ids),
            items={"$ref": "#/$defs/bound_approved_reclassification"},
        )
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
        minItems=0 if own_open_blockers else 1,
        maxItems=len(new_ids),
        items={"$ref": "#/$defs/bound_denied_finding"},
    )
    denied["properties"]["status_changes"].update(
        maxItems=len(own_open_ids),
        items={"$ref": "#/$defs/bound_status_change"},
    )
    denied["properties"]["reclassifications"].update(
        maxItems=len(own_open_ids),
        items={"$ref": "#/$defs/bound_reclassification"},
    )
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
    return {
        "title": f"Native Claude {branch} writer projection",
        "type": "object",
        "properties": {"result": {"oneOf": result_refs}},
        "required": ["result"],
        "additionalProperties": False,
        "$defs": definitions,
    }


def _native_finding_id_window(
    context: NativeReviewContext, *, size: int
) -> tuple[str, ...]:
    first = next_native_finding_id(context)
    prefix, raw_number = first.split("-", 1)
    start = int(raw_number)
    return tuple(f"{prefix}-{number:02d}" for number in range(start, start + size))


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
    projected["properties"]["anchors"]["maxItems"] = anchor_count
    return projected


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
    """Return the first reviewer-owned finding id available in this context."""
    prefix = "C"
    number = max(
        (
            int(item.finding_id.split("-", 1)[1])
            for item in context.previous_findings
            if item.finding_id.startswith(prefix + "-")
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
    )


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
    for update in response.status_changes:
        _require_native_text(
            update.rationale,
            "finding status rationale",
            max_length=3000,
            code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
        )
    for update in response.reclassifications:
        _require_native_text(
            update.rationale,
            "finding reclassification rationale",
            max_length=3000,
            code=NativeReviewErrorCode.FINDING_CONTENT_INVALID,
        )
    previous = {item.finding_id: item for item in context.previous_findings}
    previous_open_ids = frozenset(
        project_open_set(context.previous_findings).finding_ids
    )
    new_ids = [item.finding_id for item in response.new_findings]
    status_ids = [item.finding_id for item in response.status_changes]
    class_ids = [item.finding_id for item in response.reclassifications]
    all_ids = (*new_ids, *status_ids, *class_ids)
    if len(set(all_ids)) != len(all_ids):
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
            "finding id occurs in more than one event",
        )
    if any(finding_id in previous for finding_id in new_ids):
        raise NativeReviewContractError(
            NativeReviewErrorCode.FINDING_EVENT_CONFLICT,
            "new finding reuses a previous finding id",
        )
    for finding_id in (*status_ids, *class_ids):
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


def _merge_findings(
    response: NativeReviewResult, context: NativeReviewContext
) -> tuple[FindingRecord, ...]:
    opened: list[FindingRecord] = []
    for native in response.new_findings:
        acceptance = (
            native.acceptance_test.text
            if isinstance(native.acceptance_test, NativeProseAcceptance)
            else FINDING_COMMAND_PREFIX
            + " "
            + json.dumps(
                list(native.acceptance_test.argv),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
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
    if context.approval_marker is ApprovalMarker.FINAL or not context.allow_new_observations:
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
    if context.approval_marker is ApprovalMarker.FINAL:
        own_open = tuple(
            item
            for item in open_findings
            if item.origin.reporter is context.reviewer
        )
        if own_open:
            raise NativeReviewContractError(
                NativeReviewErrorCode.APPROVAL_INVALID,
                "final approval is invalid while an own finding is open",
            )


def native_review_context_binding(context: NativeReviewContext) -> dict[str, Any]:
    """Return the canonical provider-independent domain-context binding."""
    binding = {
        "run_id": context.run_id,
        "work_unit_id": context.work_unit_id,
        "operation": context.operation,
        "diff_fingerprint": context.diff_fingerprint,
        "reviewer": context.reviewer.value,
        "approval_marker": context.approval_marker.value,
        "slice_id": context.slice_id,
        "round_number": context.round_number,
        "previous_findings": [_finding_binding(item) for item in context.previous_findings],
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
    }
    if context.approval_marker is ApprovalMarker.PLAN:
        binding["plan_artifact_path"] = context.plan_artifact_path
    return binding


# Private compatibility alias for the original provider-independent request-id
# implementation.  New live callers use BoundNativeReviewContext instead.
_context_binding = native_review_context_binding


def _finding_binding(finding: FindingRecord) -> dict[str, Any]:
    return {
        "finding_id": finding.finding_id,
        "finding_class": finding.finding_class.value,
        "status": finding.status.value,
        "summary": finding.summary,
        "acceptance_test": finding.acceptance_test,
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
