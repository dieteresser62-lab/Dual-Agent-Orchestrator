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
from pathlib import Path
from typing import Any, Mapping, TypeAlias

from schema_validation import (
    SchemaDefinitionError,
    SchemaMismatch,
    check_schema,
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
    apply_reviewer_finding_update,
)
from validation_matrix import FINDING_COMMAND_PREFIX, matches_validation_family


SCHEMA_VERSION = "native-agent-review-result-v1"
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "native-agent-review-result-v1.schema.json"
)


class NativeReviewErrorCode(StrEnum):
    SCHEMA_INVALID = "schema-invalid"
    CONTEXT_INVALID = "context-invalid"
    REQUEST_MISMATCH = "request-mismatch"
    REVIEWER_MISMATCH = "reviewer-mismatch"
    FINDING_ID_INVALID = "finding-id-invalid"
    FINDING_REFERENCE_UNKNOWN = "finding-reference-unknown"
    FINDING_EVENT_CONFLICT = "finding-event-conflict"
    FINDING_UPDATE_MISSING = "missing-own-finding-update"
    FINDING_CONTENT_INVALID = "finding-content-invalid"
    ACCEPTANCE_INVALID = "acceptance-invalid"
    ANCHOR_INVALID = "anchor-invalid"
    REVIEW_CONTENT_MISSING = "review-content-missing"
    STOP_CONTENT_INVALID = "stop-content-invalid"
    APPROVAL_INVALID = "approval-invalid"


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
        if self.reviewer not in (AgentRole.CLAUDE, AgentRole.ANTIGRAVITY):
            raise NativeReviewContractError(
                NativeReviewErrorCode.CONTEXT_INVALID,
                "reviewer must be claude or antigravity",
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


def validate_native_review_document(document: Mapping[str, Any]) -> None:
    """Validate only the closed transport shape, without local context."""
    schema = load_native_review_schema()
    try:
        validate_schema_document(document, schema)
    except SchemaMismatch as exc:
        location = ".".join(str(part) for part in exc.path) or "<response>"
        raise NativeReviewContractError(
            NativeReviewErrorCode.SCHEMA_INVALID,
            f"schema validation failed at {location}: {exc.message}",
        ) from None


def parse_native_review_response(
    document: Mapping[str, Any], context: NativeReviewContext
) -> NativeReviewResponse:
    """Validate and parse a native response against immutable local context."""
    validate_native_review_document(document)
    if document["request_id"] != context.request_id:
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
    if response.request_id != context.request_id:
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
            stop_request = StopRequest(response.rule_id, response.rationale)
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
        red_state_followup_slice=None,
    )


def parse_native_contract_result(
    document: Mapping[str, Any], context: NativeReviewContext
) -> ContractResult:
    return native_response_to_contract_result(
        parse_native_review_response(document, context), context
    )


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
    expected_prefix = "C-" if context.reviewer is AgentRole.CLAUDE else "A-"
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
    for finding in context.previous_findings:
        if (
            finding.status is FindingStatus.OPEN
            and finding.origin.reporter is context.reviewer
            and finding.finding_id not in touched
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_UPDATE_MISSING,
                f"missing update for previous open finding {finding.finding_id}",
            )
    if response.anchors and context.anchor_origin is None:
        raise NativeReviewContractError(
            NativeReviewErrorCode.ANCHOR_INVALID,
            "native anchors require a bound anchor_origin",
        )


def _merge_findings(
    response: NativeReviewResult, context: NativeReviewContext
) -> tuple[FindingRecord, ...]:
    findings = {item.finding_id: item for item in context.previous_findings}
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
            findings[native.finding_id] = FindingRecord(
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
            )
        except ValueError as exc:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_ID_INVALID, str(exc)
            ) from exc
    for update in response.status_changes:
        try:
            findings[update.finding_id] = apply_reviewer_finding_update(
                findings[update.finding_id],
                reviewer=context.reviewer,
                status=update.status,
                rationale=update.rationale,
            )
        except ValueError as exc:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN, str(exc)
            ) from exc
    for update in response.reclassifications:
        current = findings[update.finding_id]
        try:
            findings[update.finding_id] = apply_reviewer_finding_update(
                current,
                reviewer=context.reviewer,
                status=current.status,
                rationale=update.rationale,
                finding_class=update.finding_class,
            )
        except ValueError as exc:
            raise NativeReviewContractError(
                NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN, str(exc)
            ) from exc
    return tuple(findings[key] for key in sorted(findings))


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
            if previous is None or (
                not context.allow_new_observations
                and previous.finding_class is not FindingClass.OBSERVATION
            ):
                raise NativeReviewContractError(
                    NativeReviewErrorCode.APPROVAL_INVALID,
                    "review cannot introduce or reclassify to OBSERVATION",
                )
    own_open_blockers = tuple(
        item
        for item in findings
        if item.status is FindingStatus.OPEN
        and item.finding_class is FindingClass.BLOCKER
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
    if validation is None or not validation.complete or not validation.passed:
        raise NativeReviewContractError(
            NativeReviewErrorCode.APPROVAL_INVALID,
            "approval requires a complete PASS attestation",
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
            for item in findings
            if item.status is FindingStatus.OPEN
            and item.origin.reporter is context.reviewer
        )
        if own_open:
            raise NativeReviewContractError(
                NativeReviewErrorCode.APPROVAL_INVALID,
                "final approval is invalid while an own finding is open",
            )
        if context.reviewer is AgentRole.ANTIGRAVITY and any(
            item.status is FindingStatus.OPEN for item in findings
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.APPROVAL_INVALID,
                "Antigravity final approval requires zero open findings",
            )


def _context_binding(context: NativeReviewContext) -> dict[str, Any]:
    return {
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
    }


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
