"""Closed native Codex JSON results with parity to ``CodexStepContract``."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, TypeAlias

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
from schema_validation import (
    SchemaDefinitionError,
    SchemaMismatch,
    check_schema,
    validate_schema_document,
)
from orchestrator_diagnostics import OrchestratorDiagnostic
from native_provider_schema import defensive_provider_projection


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
    FINAL_REPORT = "final_report"


@dataclass(frozen=True, slots=True)
class NativeCodexContext:
    run_id: str
    work_unit_id: str
    operation: str
    current_fingerprint: str
    request_kind: NativeCodexRequestKind
    contract: CodexStepContract
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
        if not isinstance(self.contract, CodexStepContract):
            raise NativeCodexContractError(
                NativeCodexErrorCode.CONTEXT_INVALID,
                "native Codex context requires CodexStepContract",
            )
        finding_ids = tuple(item.finding_id for item in self.previous_findings)
        if finding_ids != tuple(sorted(set(finding_ids))):
            raise NativeCodexContractError(
                NativeCodexErrorCode.CONTEXT_INVALID,
                "previous findings must be sorted and unique",
            )
        expected_readiness = {
            NativeCodexRequestKind.PLAN: ReadinessMarker.PLAN,
            NativeCodexRequestKind.IMPLEMENTATION: ReadinessMarker.IMPLEMENTATION,
            NativeCodexRequestKind.CORRECTION: ReadinessMarker.IMPLEMENTATION,
            NativeCodexRequestKind.FINAL_REPORT: ReadinessMarker.FINAL_REPORT,
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


@dataclass(frozen=True, slots=True)
class NativePlanResult:
    request_id: str
    ready: bool
    slice_plan: tuple[PlannedSlice, ...]
    dispositions: tuple[NativeFindingDisposition, ...] = ()


@dataclass(frozen=True, slots=True)
class NativeWorkResult:
    request_id: str
    result_kind: NativeCodexRequestKind
    ready: bool
    test_files: tuple[str, ...]
    dispositions: tuple[NativeFindingDisposition, ...]


@dataclass(frozen=True, slots=True)
class NativeFinalReportResult:
    request_id: str
    ready: bool
    dispositions: tuple[NativeFindingDisposition, ...]
    self_check: str


@dataclass(frozen=True, slots=True)
class NativeCodexStopResult:
    request_id: str
    rule_id: str
    rationale: str
    remediation_paths: tuple[str, ...]


NativeCodexResponse: TypeAlias = (
    NativePlanResult
    | NativeWorkResult
    | NativeFinalReportResult
    | NativeCodexStopResult
)


def load_native_codex_schema() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise NativeCodexContractError(
            NativeCodexErrorCode.SCHEMA_INVALID,
            "bundled native Codex schema must be an object",
        )
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
    schema = defensive_provider_projection(
        load_native_codex_schema(),
        provider="codex",
        required_features=("closed_object", "min_max_items", "nested_any_of"),
    )
    required = schema["$defs"]["plan_result"]["required"]
    if "finding_dispositions" not in required:
        required.append("finding_dispositions")
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
        "final_report_result",
    ):
        items = schema["$defs"][result_name]["properties"]["finding_dispositions"]
        items["minItems"] = len(open_ids)
        items["maxItems"] = len(open_ids)

    contract = context.contract
    expected_result = {
        NativeCodexRequestKind.PLAN: "plan_result",
        NativeCodexRequestKind.IMPLEMENTATION: "implementation_result",
        NativeCodexRequestKind.CORRECTION: "correction_result",
        NativeCodexRequestKind.FINAL_REPORT: "final_report_result",
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
    return {
        "title": f"Native Codex {context.request_kind.value} writer projection",
        "type": "object",
        "properties": {
            "result": {"anyOf": result_refs}
        },
        "required": ["result"],
        "additionalProperties": False,
        "$defs": schema["$defs"],
    }


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
        NativeCodexRequestKind.FINAL_REPORT: "final_report_result",
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
                raise NativeCodexContractError(
                    NativeCodexErrorCode.SLICE_PLAN_INVALID,
                    diagnostic.detail,
                    orchestrator_diagnostic=diagnostic,
                )
        try:
            slices = tuple(
                PlannedSlice(
                    slice_id=item["slice_id"],
                    summary=item["summary"],
                    scope_paths=tuple(item["scope_paths"]),
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
        return NativePlanResult(
            document["request_id"],
            document["ready"],
            slices,
            _parse_dispositions(document.get("finding_dispositions", [])),
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
    return NativeFinalReportResult(
        request_id=document["request_id"],
        ready=document["ready"],
        dispositions=dispositions,
        self_check=document["self_check"],
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
        self_check = None
        if not contract.require_slice_plan or not slice_plan:
            raise NativeCodexContractError(
                NativeCodexErrorCode.SLICE_PLAN_INVALID,
                "plan result requires a slice plan contract",
            )
    elif isinstance(response, NativeWorkResult):
        dispositions = response.dispositions
        test_files = response.test_files
        slice_plan = ()
        self_check = None
        _validate_test_files(response.ready, test_files, contract)
    else:
        dispositions = response.dispositions
        test_files = ()
        slice_plan = ()
        self_check = response.self_check
        _require_text(
            response.self_check,
            "self_check",
            12000,
            NativeCodexErrorCode.RESULT_CONTENT_INVALID,
        )
    findings = _apply_dispositions(prior, dispositions)
    return CodexContractResult(
        ready=response.ready,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=test_files,
        findings=findings,
        slice_plan=slice_plan,
        self_check=self_check,
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
        )
        for item in items
    )
    ids = tuple(item.finding_id for item in dispositions)
    if ids != tuple(sorted(set(ids))):
        raise NativeCodexContractError(
            NativeCodexErrorCode.FINDING_REFERENCE_INVALID,
            "finding dispositions must be sorted and unique",
        )
    return dispositions


def _apply_dispositions(
    prior: tuple[FindingRecord, ...],
    dispositions: tuple[NativeFindingDisposition, ...],
) -> tuple[FindingRecord, ...]:
    try:
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
            NativeCodexErrorCode.FINDING_REFERENCE_INVALID, str(exc)
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
