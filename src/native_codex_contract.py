"""Closed native Codex JSON results with parity to ``CodexStepContract``."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import StrEnum
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, TypeAlias

from acceptance_criteria import acceptance_criteria_from_specs
from contracts import (
    AgentRole,
    CodexContractResult,
    CodexStepContract,
    FindingRecord,
    FindingResponseDecision,
    FindingStatus,
    PlannedSlice,
    planned_slice_path_diagnostic,
    ReadinessMarker,
    StopRequest,
)
from finding_reducer import (
    FindingResponseEvent,
    apply_finding_responses,
    project_open_set,
)
from finding_order import sorted_finding_ids
from finding_responsibility import (
    parse_responsibility,
    responsibility_json_schema,
)
from gates import BUILTIN_STOP_RULES, STOP_RULE_ID_PATTERN
import native_finding_decisions
from native_finding_decisions import (
    NativeRejectionReason,
    NativeResponsibilityProposal,
    PlanCompletionKind,
    PlanTreatmentKind,
    PlanTreatmentProposal,
)
from finding_planning import (
    canonical_open_signature_groups,
    validate_plan_completion,
    validate_plan_treatment_coverage,
)
from schema_validation import (
    SchemaDefinitionError,
    SchemaMismatch,
    check_schema,
    validate_schema_document,
)
from orchestrator_diagnostics import OrchestratorDiagnostic
from native_provider_schema import (
    assert_projected_provider_schema,
    defensive_provider_projection,
)


SCHEMA_VERSION = "native-agent-codex-result-v2"
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "native-agent-codex-result-v2.schema.json"
)
REQUEST_ID_PREFIX = "native-codex-request-"


class NativeCodexErrorCode(StrEnum):
    SCHEMA_INVALID = "schema-invalid"
    CONTEXT_INVALID = "context-invalid"
    REQUEST_MISMATCH = "request-mismatch"
    RESULT_KIND_MISMATCH = "result-kind-mismatch"
    RESULT_CONTENT_INVALID = "result-content-invalid"
    FINDING_REFERENCE_INVALID = "finding-reference-invalid"
    TEST_FILES_INVALID = "test-files-invalid"
    SLICE_PLAN_INVALID = "slice-plan-invalid"
    STOP_CONTENT_INVALID = "stop-content-invalid"
    DORMANT_FINDING_DECISION_FIELD = "dormant-finding-decision-field"


class NativeCodexContractError(ValueError):
    """A stable machine-readable native Codex contract failure."""

    def __init__(
        self,
        code: NativeCodexErrorCode,
        detail: str,
        *,
        orchestrator_diagnostic: OrchestratorDiagnostic | None = None,
    ) -> None:
        if orchestrator_diagnostic is not None and not isinstance(
            orchestrator_diagnostic, OrchestratorDiagnostic
        ):
            raise TypeError("orchestrator diagnostic must be a closed enum member")
        self.code = code
        self.detail = detail
        self.orchestrator_diagnostic = orchestrator_diagnostic
        super().__init__(f"{code.value}: {detail}")


class NativeCodexRequestKind(StrEnum):
    PLAN = "plan"
    IMPLEMENTATION = "implementation"
    CORRECTION = "correction"


@dataclass(frozen=True, slots=True)
class NativeCodexContext:
    run_id: str
    work_unit_id: str
    operation: str
    current_fingerprint: str
    request_kind: NativeCodexRequestKind
    contract: CodexStepContract
    known_stop_rule_ids: frozenset[str] = field(
        default_factory=lambda: frozenset(rule.id for rule in BUILTIN_STOP_RULES)
    )
    previous_findings: tuple[FindingRecord, ...] = ()

    def __post_init__(self) -> None:
        for label, value in (
            ("run_id", self.run_id),
            ("work_unit_id", self.work_unit_id),
            ("operation", self.operation),
        ):
            _require_text(value, label, 300, NativeCodexErrorCode.CONTEXT_INVALID)
        _require_sha256(
            self.current_fingerprint,
            "current_fingerprint",
            NativeCodexErrorCode.CONTEXT_INVALID,
        )
        if not isinstance(self.request_kind, NativeCodexRequestKind):
            raise NativeCodexContractError(
                NativeCodexErrorCode.CONTEXT_INVALID,
                "request_kind is invalid",
            )
        stop_rule_ids_valid = (
            isinstance(self.known_stop_rule_ids, frozenset)
            and bool(self.known_stop_rule_ids)
            and all(
                isinstance(rule_id, str)
                and STOP_RULE_ID_PATTERN.fullmatch(rule_id) is not None
                for rule_id in self.known_stop_rule_ids
            )
        )
        if not isinstance(self.contract, CodexStepContract) or not stop_rule_ids_valid:
            raise NativeCodexContractError(
                NativeCodexErrorCode.CONTEXT_INVALID,
                "native Codex context requires CodexStepContract and a non-empty "
                "frozenset of known stop rule ids",
            )
        finding_ids = tuple(item.finding_id for item in self.previous_findings)
        if finding_ids != sorted_finding_ids(finding_ids):
            raise NativeCodexContractError(
                NativeCodexErrorCode.CONTEXT_INVALID,
                "previous findings must be sorted and unique",
            )
        expected_readiness = {
            NativeCodexRequestKind.PLAN: ReadinessMarker.PLAN,
            NativeCodexRequestKind.IMPLEMENTATION: ReadinessMarker.IMPLEMENTATION,
            NativeCodexRequestKind.CORRECTION: ReadinessMarker.IMPLEMENTATION,
        }[self.request_kind]
        if self.contract.readiness_marker is not expected_readiness:
            raise NativeCodexContractError(
                NativeCodexErrorCode.CONTEXT_INVALID,
                f"{self.request_kind.value} requires {expected_readiness.value}",
            )


@dataclass(frozen=True, slots=True)
class BoundNativeCodexContext:
    context: NativeCodexContext
    request_id: str
    request_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.context, NativeCodexContext):
            raise NativeCodexContractError(
                NativeCodexErrorCode.CONTEXT_INVALID,
                "bound native Codex context requires NativeCodexContext",
            )
        _require_sha256(
            self.request_digest,
            "request_digest",
            NativeCodexErrorCode.CONTEXT_INVALID,
        )
        if self.request_id != REQUEST_ID_PREFIX + self.request_digest:
            raise NativeCodexContractError(
                NativeCodexErrorCode.CONTEXT_INVALID,
                "bound request_id must contain request_digest",
            )


@dataclass(frozen=True, slots=True)
class NativeFindingDisposition:
    finding_id: str
    decision: FindingResponseDecision
    rationale: str
    responsibility_proposal: NativeResponsibilityProposal | None = None


@dataclass(frozen=True, slots=True)
class NativePlanResult:
    request_id: str
    ready: bool
    slice_plan: tuple[PlannedSlice, ...]
    dispositions: tuple[NativeFindingDisposition, ...] = ()
    plan_treatments: tuple[PlanTreatmentProposal, ...] = ()
    plan_completion: PlanCompletionKind | None = None


@dataclass(frozen=True, slots=True)
class NativeWorkResult:
    request_id: str
    result_kind: NativeCodexRequestKind
    ready: bool
    test_files: tuple[str, ...]
    dispositions: tuple[NativeFindingDisposition, ...]


@dataclass(frozen=True, slots=True)
class NativeCodexStopResult:
    request_id: str
    rule_id: str
    rationale: str
    remediation_paths: tuple[str, ...]


NativeCodexResponse: TypeAlias = (
    NativePlanResult
    | NativeWorkResult
    | NativeCodexStopResult
)


def load_native_codex_schema() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise NativeCodexContractError(
            NativeCodexErrorCode.SCHEMA_INVALID,
            "bundled native Codex schema must be an object",
        )
    _enable_native_finding_decision_schema(schema)
    try:
        check_schema(schema, location="<native-codex-result-schema>")
    except SchemaDefinitionError as exc:
        raise NativeCodexContractError(
            NativeCodexErrorCode.SCHEMA_INVALID, str(exc)
        ) from exc
    return schema


def native_codex_provider_response_schema(
    context: NativeCodexContext,
) -> dict[str, Any]:
    """Project the immutable reader schema into one request-specific writer.

    The bundled v2 reader and every request-specific writer both require the
    plan disposition list. Historical v1 results are intentionally outside
    this protocol and are not accepted through this parser.

    OpenAI Structured Outputs requires an object at the schema root.  The
    persisted contract is a discriminated top-level union, so the provider
    projection places that union below one required ``result`` property.  The
    adapter unwraps the envelope before applying the unchanged local contract.
    """
    if not isinstance(context, NativeCodexContext):
        raise NativeCodexContractError(
            NativeCodexErrorCode.CONTEXT_INVALID,
            "provider schema projection requires NativeCodexContext",
        )
    provider = "codex"
    schema = defensive_provider_projection(
        load_native_codex_schema(),
        provider=provider,
        required_features=("closed_object", "min_max_items", "nested_any_of"),
    )
    if context.request_kind.value != "plan":
        # Keep non-planning writer contracts byte-stable: planned_slice is
        # unreachable from implementation/correction result variants, and the
        # active measurement cutover belongs only to plan output.
        schema["$defs"]["planned_slice"]["properties"]["acceptance_criteria"] = {
            "type": "array",
            "description": (
                "Ordered, non-empty acceptance conditions for this Slice; exact "
                "duplicate texts are forbidden."
            ),
            "minItems": 1,
            "maxItems": 256,
            "items": {"$ref": "#/$defs/safe_text"},
        }
    stop_rule_id = schema["$defs"]["stop_result"]["properties"]["rule_id"]
    projected_stop_rule_id = {
        "type": "string",
        "enum": sorted(context.known_stop_rule_ids),
    }
    if "description" in stop_rule_id:
        projected_stop_rule_id["description"] = stop_rule_id["description"]
    schema["$defs"]["stop_result"]["properties"][
        "rule_id"
    ] = projected_stop_rule_id
    required = schema["$defs"]["plan_result"]["required"]
    if "finding_dispositions" not in required:
        required.append("finding_dispositions")
    required.append("plan_treatments")
    slice_responsibility = schema["$defs"]["finding_responsibility"][
        "anyOf"
    ][0]
    acceptance_criterion = slice_responsibility["properties"][
        "acceptance_criterion_id"
    ]
    slice_responsibility["properties"]["acceptance_criterion_id"] = {
        "anyOf": [acceptance_criterion, {"type": "null"}]
    }
    slice_responsibility["required"].append("acceptance_criterion_id")
    open_ids = project_open_set(context.previous_findings).finding_ids
    disposition = schema["$defs"]["finding_disposition"]
    if open_ids:
        disposition["properties"]["finding_id"] = {
            "type": "string",
            "enum": list(open_ids),
        }
    for result_name in (
        "plan_result",
        "implementation_result",
        "correction_result",
    ):
        items = schema["$defs"][result_name]["properties"]["finding_dispositions"]
        items["minItems"] = 0
        items["maxItems"] = len(open_ids)

    contract = context.contract
    expected_result = {
        NativeCodexRequestKind.PLAN: "plan_result",
        NativeCodexRequestKind.IMPLEMENTATION: "implementation_result",
        NativeCodexRequestKind.CORRECTION: "correction_result",
    }[context.request_kind]
    if context.request_kind is NativeCodexRequestKind.PLAN and not contract.require_slice_plan:
        result_refs = [{"$ref": "#/$defs/stop_result"}]
    else:
        result_refs = [
            {"$ref": f"#/$defs/{expected_result}"},
            {"$ref": "#/$defs/stop_result"},
        ]

    if context.request_kind in {
        NativeCodexRequestKind.IMPLEMENTATION,
        NativeCodexRequestKind.CORRECTION,
    }:
        work_result = schema["$defs"][expected_result]
        test_files = work_result["properties"]["test_files"]
        if not contract.require_test_files_record:
            test_files["minItems"] = 0
            test_files["maxItems"] = 0
        elif contract.enforce_expected_test_files:
            expected_tests = tuple(contract.expected_test_files)
            test_files["minItems"] = len(expected_tests)
            test_files["maxItems"] = len(expected_tests)
            if expected_tests:
                test_files["items"] = {
                    "type": "string",
                    "enum": list(expected_tests),
                }
        if not contract.test_changes_approved:
            ready_false_name = f"bound_{expected_result}_ready_false"
            ready_false = copy.deepcopy(work_result)
            ready_false["properties"]["ready"]["const"] = False
            schema["$defs"][ready_false_name] = ready_false
            readiness_refs: list[dict[str, str]] = [
                {"$ref": f"#/$defs/{ready_false_name}"}
            ]
            fixed_nonempty_tests = (
                contract.enforce_expected_test_files
                and bool(contract.expected_test_files)
            )
            if not fixed_nonempty_tests:
                ready_true_name = f"bound_{expected_result}_ready_true_no_tests"
                ready_true = copy.deepcopy(work_result)
                ready_true["properties"]["ready"]["const"] = True
                ready_true["properties"]["test_files"]["minItems"] = 0
                ready_true["properties"]["test_files"]["maxItems"] = 0
                schema["$defs"][ready_true_name] = ready_true
                readiness_refs.append(
                    {"$ref": f"#/$defs/{ready_true_name}"}
                )
            result_refs[0:1] = readiness_refs
    projected_schema = {
        "title": f"Native Codex {context.request_kind.value} writer projection",
        "type": "object",
        "properties": {
            "result": {"anyOf": result_refs}
        },
        "required": ["result"],
        "additionalProperties": False,
        "$defs": schema["$defs"],
    }
    assert_projected_provider_schema(projected_schema, provider=provider)
    return projected_schema


def validate_native_codex_document(document: Mapping[str, Any]) -> None:
    try:
        validate_schema_document(document, load_native_codex_schema())
    except SchemaMismatch as exc:
        location = ".".join(str(part) for part in exc.path) or "<response>"
        raise NativeCodexContractError(
            NativeCodexErrorCode.SCHEMA_INVALID,
            f"schema validation failed at {location}: {exc.message}",
        ) from None


def parse_native_codex_response(
    document: Mapping[str, Any], bound_context: BoundNativeCodexContext
) -> NativeCodexResponse:
    if not isinstance(bound_context, BoundNativeCodexContext):
        raise NativeCodexContractError(
            NativeCodexErrorCode.CONTEXT_INVALID,
            "native parsing requires BoundNativeCodexContext",
        )
    validate_native_codex_document(document)
    if document["request_id"] != bound_context.request_id:
        raise NativeCodexContractError(
            NativeCodexErrorCode.REQUEST_MISMATCH,
            "response request_id does not match bound request",
        )
    result_type = document["result_type"]
    if result_type == "stop_result":
        remediation_paths = tuple(document["remediation_paths"])
        _require_sorted_paths(
            remediation_paths,
            "remediation_paths",
            NativeCodexErrorCode.STOP_CONTENT_INVALID,
            allow_empty=True,
        )
        return NativeCodexStopResult(
            request_id=document["request_id"],
            rule_id=document["rule_id"],
            rationale=document["rationale"],
            remediation_paths=remediation_paths,
        )
    expected_type = {
        NativeCodexRequestKind.PLAN: "plan_result",
        NativeCodexRequestKind.IMPLEMENTATION: "implementation_result",
        NativeCodexRequestKind.CORRECTION: "correction_result",
    }[bound_context.context.request_kind]
    if result_type != expected_type:
        raise NativeCodexContractError(
            NativeCodexErrorCode.RESULT_KIND_MISMATCH,
            f"{bound_context.context.request_kind.value} request requires {expected_type}",
        )
    if result_type == "plan_result":
        for item in document["slice_plan"]:
            diagnostic = planned_slice_path_diagnostic(tuple(item["scope_paths"]))
            if diagnostic is not None:
                raise NativeCodexContractError(  # allowlist:provider -- contract boundary
                    NativeCodexErrorCode.SLICE_PLAN_INVALID,  # allowlist:provider -- error vocabulary
                    diagnostic.detail,
                    orchestrator_diagnostic=diagnostic,
                )
        try:
            slices = tuple(
                PlannedSlice(
                    slice_id=item["slice_id"],
                    summary=item["summary"],
                    scope_paths=tuple(item["scope_paths"]),
                    acceptance_criteria=acceptance_criteria_from_specs(
                        item["slice_id"], item.get("acceptance_criteria", ())
                    ),
                )
                for item in document["slice_plan"]
            )
        except ValueError as exc:
            raise NativeCodexContractError(
                NativeCodexErrorCode.SLICE_PLAN_INVALID,
                str(exc),
            ) from exc
        if tuple(item.slice_id for item in slices) != tuple(range(1, len(slices) + 1)):
            raise NativeCodexContractError(
                NativeCodexErrorCode.SLICE_PLAN_INVALID,
                "slice plan ids must be contiguous and 1-based",
            )
        treatments = _parse_plan_treatments(document.get("plan_treatments", []))
        try:
            validate_plan_treatment_coverage(
                canonical_open_signature_groups(
                    bound_context.context.previous_findings
                ),
                treatments,
                slices,
            )
            completion = PlanCompletionKind(document["plan_completion"])
            validate_plan_completion(treatments, slices, completion)
        except ValueError as exc:
            raise NativeCodexContractError(  # allowlist:provider -- contract boundary
                NativeCodexErrorCode.SLICE_PLAN_INVALID,  # allowlist:provider -- error vocabulary
                str(exc),
            ) from exc
        return NativePlanResult(
            request_id=document["request_id"],
            ready=document["ready"],
            slice_plan=slices,
            dispositions=_parse_dispositions(
                document.get("finding_dispositions", [])
            ),
            plan_treatments=treatments,
            plan_completion=completion,
        )
    dispositions = _parse_dispositions(document["finding_dispositions"])
    if result_type in {"implementation_result", "correction_result"}:
        test_files = tuple(document["test_files"])
        _require_sorted_paths(
            test_files,
            "test_files",
            NativeCodexErrorCode.TEST_FILES_INVALID,
            allow_empty=True,
        )
        return NativeWorkResult(
            request_id=document["request_id"],
            result_kind=(
                NativeCodexRequestKind.IMPLEMENTATION
                if result_type == "implementation_result"
                else NativeCodexRequestKind.CORRECTION
            ),
            ready=document["ready"],
            test_files=test_files,
            dispositions=dispositions,
        )
    raise NativeCodexContractError(
        NativeCodexErrorCode.RESULT_KIND_MISMATCH,
        f"unsupported Codex result type {result_type!r}",
    )


def native_codex_response_to_contract_result(
    response: NativeCodexResponse, bound_context: BoundNativeCodexContext
) -> CodexContractResult:
    if response.request_id != bound_context.request_id:
        raise NativeCodexContractError(
            NativeCodexErrorCode.REQUEST_MISMATCH,
            "parsed response does not match bound request",
        )
    context = bound_context.context
    prior = context.previous_findings
    if isinstance(response, NativeCodexStopResult):
        try:
            stop = StopRequest(
                response.rule_id,
                response.rationale,
                response.remediation_paths,
            )
        except ValueError as exc:
            raise NativeCodexContractError(
                NativeCodexErrorCode.STOP_CONTENT_INVALID, str(exc)
            ) from exc
        return CodexContractResult(
            ready=None,
            stopped=True,
            stop_request=stop,
            validation=None,
            test_files=(),
            findings=prior,
            slice_plan=(),
            self_check=None,
        )

    contract = context.contract
    dispositions: tuple[NativeFindingDisposition, ...]
    test_files: tuple[str, ...]
    slice_plan: tuple[PlannedSlice, ...]
    self_check: str | None
    if isinstance(response, NativePlanResult):
        dispositions = response.dispositions
        test_files = ()
        slice_plan = response.slice_plan
        plan_treatments = response.plan_treatments
        plan_completion = response.plan_completion
        self_check = None
        no_implementation_required = (
            plan_completion is PlanCompletionKind.NO_IMPLEMENTATION_REQUIRED
        )
        if not contract.require_slice_plan or (
            not slice_plan and not no_implementation_required
        ):
            raise NativeCodexContractError(
                NativeCodexErrorCode.SLICE_PLAN_INVALID,
                "plan result requires a slice plan contract",
            )
    elif isinstance(response, NativeWorkResult):
        dispositions = response.dispositions
        test_files = response.test_files
        slice_plan = ()
        self_check = None
        plan_treatments = ()
        plan_completion = None
        _validate_test_files(response.ready, test_files, contract)
    else:
        raise NativeCodexContractError(
            NativeCodexErrorCode.RESULT_KIND_MISMATCH,
            "unsupported Codex response variant",
        )
    findings = _apply_dispositions(
        prior,
        dispositions,
        work_unit_id=context.work_unit_id,
        round_number=context.contract.round_number,
        require_complete=False,
    )
    return CodexContractResult(
        ready=response.ready,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=test_files,
        findings=findings,
        slice_plan=slice_plan,
        self_check=self_check,
        plan_treatments=plan_treatments,
        plan_completion=plan_completion,
    )


def parse_bound_native_codex_contract_result(
    document: Mapping[str, Any], bound_context: BoundNativeCodexContext
) -> CodexContractResult:
    return native_codex_response_to_contract_result(
        parse_native_codex_response(document, bound_context), bound_context
    )


def canonical_native_codex_json(document: Mapping[str, Any]) -> str:
    validate_native_codex_document(document)
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _parse_dispositions(
    items: list[Mapping[str, Any]],
) -> tuple[NativeFindingDisposition, ...]:
    dispositions = tuple(
        NativeFindingDisposition(
            finding_id=item["finding_id"],
            decision=FindingResponseDecision(item["decision"].upper()),
            rationale=item["rationale"],
            responsibility_proposal=_parse_responsibility_proposal(item),
        )
        for item in items
    )
    ids = tuple(item.finding_id for item in dispositions)
    if ids != sorted_finding_ids(ids):
        raise NativeCodexContractError(
            NativeCodexErrorCode.FINDING_REFERENCE_INVALID,
            "finding dispositions must be sorted and unique",
        )
    return dispositions


def _parse_plan_treatments(
    items: list[Mapping[str, Any]],
) -> tuple[PlanTreatmentProposal, ...]:
    try:
        treatments = tuple(
            PlanTreatmentProposal(
                signature=item["signature"],
                finding_ids=tuple(item["finding_ids"]),
                treatment_kind=PlanTreatmentKind(item["treatment_kind"]),
                closing_slice_ids=tuple(item["closing_slice_ids"]),
                no_code_reason=(
                    None
                    if item["no_code_reason"] is None
                    else NativeRejectionReason(item["no_code_reason"])
                ),
                evidence=item["evidence"],
                evidence_paths=tuple(item.get("evidence_paths", ())),
                affected_paths=tuple(item.get("affected_paths", ())),
            )
            for item in items
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise NativeCodexContractError(  # allowlist:provider -- contract boundary
            NativeCodexErrorCode.SLICE_PLAN_INVALID,  # allowlist:provider -- error vocabulary
            f"plan treatment is invalid: {exc}",
        ) from exc
    signatures = tuple(item.signature for item in treatments)
    if signatures != tuple(sorted(set(signatures))):
        raise NativeCodexContractError(  # allowlist:provider -- contract boundary
            NativeCodexErrorCode.SLICE_PLAN_INVALID,  # allowlist:provider -- error vocabulary
            "plan treatments must be sorted and unique by signature",
        )
    return treatments


def native_responsibility_proposals(
    response: NativeCodexResponse,  # allowlist:provider -- bound result type
) -> tuple[NativeResponsibilityProposal, ...]:
    """Expose implementer proposals solely as non-authoritative reviewer input."""

    if isinstance(response, NativeCodexStopResult):  # allowlist:provider -- result variant
        return ()
    return tuple(
        disposition.responsibility_proposal
        for disposition in response.dispositions
        if disposition.responsibility_proposal is not None
    )


def _parse_responsibility_proposal(
    item: Mapping[str, Any],
) -> NativeResponsibilityProposal | None:
    raw = item.get("responsibility_proposal")
    if raw is None:
        return None
    try:
        responsibility = parse_responsibility(raw)
    except ValueError as exc:
        raise NativeCodexContractError(  # allowlist:provider -- contract boundary
            NativeCodexErrorCode.RESULT_CONTENT_INVALID,  # allowlist:provider -- error vocabulary
            f"responsibility proposal for {item['finding_id']} is invalid: {exc}",
        ) from exc
    return NativeResponsibilityProposal(
        finding_id=item["finding_id"],
        responsibility=responsibility,
        rationale=item["rationale"],
    )


def _enable_native_finding_decision_schema(schema: dict[str, Any]) -> None:
    definitions = schema["$defs"]
    planned_slice = definitions["planned_slice"]
    planned_slice["properties"]["acceptance_criteria"] = {
        "type": "array",
        "description": (
            "Ordered, non-empty acceptance conditions for this Slice; exact "
            "duplicate specifications are forbidden. Every condition declares "
            "the product boundary at which it is measured."
        ),
        "minItems": 1,
        "maxItems": 256,
        "uniqueItems": True,
        "items": {
            "type": "object",
            "properties": {
                "text": {"$ref": "#/$defs/safe_text"},
                "measured_against": {
                    "type": "string",
                    "enum": ["SOURCE", "BUILD_OUTPUT", "RUNNING_PRODUCT"]
                },
            },
            "required": ["text", "measured_against"],
            "additionalProperties": False,
        },
    }
    planned_slice["required"].append("acceptance_criteria")
    definitions["finding_responsibility"] = responsibility_json_schema()
    disposition = definitions["finding_disposition"]
    disposition["properties"]["responsibility_proposal"] = {
        "anyOf": [
            {"$ref": "#/$defs/finding_responsibility"},
            {"type": "null"},
        ]
    }
    disposition["required"].append("responsibility_proposal")
    definitions["plan_treatment"] = {
        "type": "object",
        "properties": {
            "signature": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "finding_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "pattern": "^C-(0[1-9]|[1-9][0-9]*)$",
                },
            },
            "treatment_kind": {
                "type": "string",
                "enum": [item.value for item in PlanTreatmentKind],
            },
            "closing_slice_ids": {
                "type": "array",
                "maxItems": 1,
                "uniqueItems": True,
                "items": {"type": "integer", "minimum": 1},
            },
            "no_code_reason": {
                "anyOf": [
                    {
                        "type": "string",
                        "enum": [item.value for item in NativeRejectionReason],
                    },
                    {"type": "null"},
                ]
            },
            "evidence": {
                "anyOf": [
                    {"$ref": "#/$defs/safe_text"},
                    {"type": "null"},
                ]
            },
            "evidence_paths": {
                "type": "array",
                "maxItems": 1000,
                "uniqueItems": True,
                "items": {"$ref": "#/$defs/safe_path"},
            },
            "affected_paths": {
                "type": "array",
                "maxItems": 1000,
                "uniqueItems": True,
                "items": {"$ref": "#/$defs/safe_path"},
            },
        },
        "required": [
            "signature",
            "finding_ids",
            "treatment_kind",
            "closing_slice_ids",
            "no_code_reason",
            "evidence",
            "evidence_paths",
            "affected_paths",
        ],
        "additionalProperties": False,
    }
    plan_result = definitions["plan_result"]
    plan_result["properties"]["plan_treatments"] = {
        "type": "array",
        "maxItems": 128,
        "items": {"$ref": "#/$defs/plan_treatment"},
    }
    plan_result["properties"]["plan_completion"] = {
        "type": "string",
        "enum": [item.value for item in PlanCompletionKind],
    }
    plan_result["properties"]["slice_plan"]["minItems"] = 0
    plan_result["required"].append("plan_completion")


def _apply_dispositions(
    prior: tuple[FindingRecord, ...],
    dispositions: tuple[NativeFindingDisposition, ...],
    *,
    work_unit_id: str,
    round_number: int,
    require_complete: bool,
) -> tuple[FindingRecord, ...]:
    try:
        if require_complete:
            open_ids = project_open_set(prior).finding_ids
            disposition_ids = tuple(item.finding_id for item in dispositions)
            missing = sorted_finding_ids(set(open_ids) - set(disposition_ids))
            if missing:
                raise ValueError(f"missing disposition for {missing[0]}")
        return apply_finding_responses(
            prior,
            tuple(
                FindingResponseEvent(
                    item.finding_id, item.decision, item.rationale
                )
                for item in dispositions
            ),
        )
    except ValueError as exc:
        raise NativeCodexContractError(
            NativeCodexErrorCode.FINDING_REFERENCE_INVALID,
            f"{exc} (context: work-unit={work_unit_id} round={round_number})",
        ) from exc


def _validate_test_files(
    ready: bool, test_files: tuple[str, ...], contract: CodexStepContract
) -> None:
    if not contract.require_test_files_record:
        if test_files:
            raise NativeCodexContractError(
                NativeCodexErrorCode.TEST_FILES_INVALID,
                "unexpected test_files for this step",
            )
        return
    if contract.enforce_expected_test_files and test_files != contract.expected_test_files:
        raise NativeCodexContractError(
            NativeCodexErrorCode.TEST_FILES_INVALID,
            "test_files do not match the step contract",
        )
    if ready and test_files and not contract.test_changes_approved:
        raise NativeCodexContractError(
            NativeCodexErrorCode.TEST_FILES_INVALID,
            "ready result with test changes requires prior approval",
        )


def _require_text(
    value: object,
    label: str,
    maximum: int,
    code: NativeCodexErrorCode,
) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise NativeCodexContractError(
            code, f"{label} must be non-blank, NUL-free, and at most {maximum} characters"
        )


def _require_sha256(
    value: object, label: str, code: NativeCodexErrorCode
) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise NativeCodexContractError(code, f"{label} must be lowercase SHA-256")


def _require_sorted_paths(
    values: tuple[str, ...],
    label: str,
    code: NativeCodexErrorCode,
    *,
    allow_empty: bool,
) -> None:
    if values != tuple(sorted(set(values))) or (not allow_empty and not values):
        raise NativeCodexContractError(
            code, f"{label} must be sorted and unique" + ("" if allow_empty else " and non-empty")
        )
    for raw_path in values:
        path = PurePosixPath(raw_path)
        if (
            not raw_path.strip()
            or path.is_absolute()
            or "\\" in raw_path
            or ".." in path.parts
            or raw_path != path.as_posix()
            or path.parts[0] == ".orchestrator"
        ):
            raise NativeCodexContractError(
                code, f"{label} contains an unsafe repository path"
            )
