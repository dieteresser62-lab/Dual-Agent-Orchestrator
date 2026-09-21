from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Callable

import pytest

import agent_runtime
import workflow_recovery as recovery_module
from artifact_bridge import (
    agent_result_payload,
    logical_provider_operation_id,
    review_payload,
)
from artifact_models import (
    AgentResultPayload,
    BlobReference,
    CommandSpec,
    ProviderAttemptPayload,
    ProviderContentPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ReviewPayload,
    Role,
    ValidationAttestationPayload,
    ValidationResult,
)
from artifact_replay import (
    ArtifactReplayError,
    ReplayDiagnostic,
    ReplayDiagnosticCode,
)
from contracts import (
    AgentRole,
    ApprovalMarker,
    CodexStepContract,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ReadinessMarker,
    StepContract,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
)
from native_codex_contract import (
    NativeCodexContext,
    NativeCodexRequestKind,
    canonical_native_codex_json,
    parse_bound_native_codex_contract_result,
)
from native_codex_request import (
    NativeCodexEvidenceInput,
    NativeCodexRequestSpec,
    build_native_codex_request,
)
from native_review_contract import (
    NativeReviewContext,
    canonical_native_review_json,
    parse_bound_native_contract_result,
)
from native_review_request import (
    NativeReviewEvidenceInput,
    NativeReviewKind,
    NativeReviewRequestSpec,
    build_native_review_request,
)
from workflow import (
    CodexInvocation,
    EvidenceKind,
    ReviewerInvocation,
    WorkflowContext,
    WorkflowHistory,
)
from workflow_state import WorkflowStep, WorkUnitKind


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "src/workflow_recovery.py"
STATIC_BASELINE = ROOT / "tests/fixtures/workflow-recovery-static-pre-b53-v1.json"
RUNTIME_BASELINE = ROOT / "tests/fixtures/workflow-recovery-runtime-pre-b53-v1.json"
PRE_B54_BASELINE = ROOT / "tests/fixtures/workflow-recovery-pre-b54-v1.json"
HELPER_BINDINGS = ROOT / "tests/fixtures/workflow-recovery-helper-bindings-b54-v1.json"
FUNCTION_SIZE_BASELINE = ROOT / "tests/fixtures/function-size-baseline-v1.json"
RECORD_SEQUENCE_BASELINE = (
    ROOT / "tests/fixtures/workflow-record-sequence-baseline-v1.json"
)
SOURCE_COMMIT = "881e944fea9442012aecb7c20b519ea5a2b83bbf"
SOURCE_BLOB = "f3b3cfb77c5b27b8c60d35c7c347095ea8b2514d"
PRE_B54_COMMIT = "e538d96be18e6857250f6a86ab6f6c499e40d0b0"
RECORD_SEQUENCE_BLOB = "26fb661c8fa382f90e70fb921e3d950da5cae09b"
RUN_ID = "b53-recovery-corpus"
FINGERPRINT = "b" * 64
IMPLEMENTER_RECORD_ID = "ar1-" + "1" * 64
REVIEW_RECORD_ID = "ar1-" + "2" * 64
IMPLEMENTER_REQUEST_ID = (
    "native-codex-request-979e6e7fdeb8d1a0b6c5d6295531524572dca857559aeee4dcf3e0331627d778"
)
REVIEW_REQUEST_ID = (
    "native-review-request-c0b89a403e36b5db9a29f977339c6a531d1cb4c74d8842b7684cfc19597aaad2"
)
IMPLEMENTER_RESPONSE_SHA256 = (
    "673f8fc3e06b44cf70d2e355126183a32ff7f229b5c2e65a8c4463b07392206b"
)
REVIEW_RESPONSE_SHA256 = (
    "45a3f6c06ead0cf1eb9d1a36f3c55cdc6cdbddcc1942c802564462f72811d2d7"
)

RECOVERY_HELPERS = {
    "recover_pending_native_implementer": (
        "_bind_native_implementer_request",
        "_replay_native_implementer_request_findings",
        "_bind_native_implementer_request_findings",
        "_parse_native_implementer_recovery",
    ),
    "recover_pending_native_reviewer_before_policy": (
        "_replay_pending_native_reviewer",
        "_build_pending_native_reviewer_context",
        "_parse_pending_native_reviewer_response",
    ),
}
CAUGHT_HELPERS = {
    "_replay_native_implementer_request_findings": "ArtifactReplayError",
    "_parse_native_implementer_recovery": "(ValueError, TypeError)",
    "_replay_pending_native_reviewer": "ArtifactReplayError",
    "_parse_pending_native_reviewer_response": (
        "(json.JSONDecodeError, ValueError, NativeReviewContractError)"
    ),
}

@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    path: str
    source_aborts: tuple[int, ...]


SCENARIOS = (
    ScenarioSpec("implementer-success", "implementer", ()),
    ScenarioSpec("implementer-missing-provider-content", "implementer", (1,)),
    ScenarioSpec("implementer-missing-original-binding-findings", "implementer", (4,)),
    ScenarioSpec("implementer-missing-original-binding-request-id", "implementer", (5,)),
    ScenarioSpec("implementer-request-time-replay-failure", "implementer", (6,)),
    ScenarioSpec("implementer-request-time-finding-subset", "implementer", (7,)),
    ScenarioSpec("implementer-raw-response-not-object", "implementer", (8, 10)),
    ScenarioSpec("implementer-raw-response-noncanonical", "implementer", (9, 10)),
    ScenarioSpec("implementer-bound-response-invalid", "implementer", (10,)),
    ScenarioSpec("implementer-record-binding-divergent", "implementer", (11,)),
    ScenarioSpec("implementer-result-divergent", "implementer", (12,)),
    ScenarioSpec("reviewer-success", "reviewer", ()),
    ScenarioSpec("reviewer-replay-failure", "reviewer", (1,)),
    ScenarioSpec("reviewer-duplicate-round-authority", "reviewer", (2,)),
    ScenarioSpec("reviewer-active-unit-divergent", "reviewer", (3,)),
    ScenarioSpec("reviewer-attestation-missing", "reviewer", (6,)),
    ScenarioSpec("reviewer-raw-response-not-object", "reviewer", (8, 9)),
    ScenarioSpec("reviewer-bound-response-invalid", "reviewer", (9,)),
    ScenarioSpec("reviewer-result-divergent", "reviewer", (10,)),
)


UNREACHABLE_ABORTS = (
    {
        "path": "implementer",
        "abort_ordinal": 2,
        "exception_type": "WorkflowExecutionError",
        "message": "native provider content digest differs from its record",
        "reason": (
            "The production content_text boundary reads the BlobReference through "
            "ArtifactStore.read_blob; ProviderContentPayload binds response_sha256 to "
            "that blob and read_blob verifies the bytes, so recomputing the same bytes "
            "cannot differ without a SHA-256 collision."
        ),
    },
    {
        "path": "implementer",
        "abort_ordinal": 3,
        "exception_type": "WorkflowExecutionError",
        "message": "native implementer recovery raw response digest differs from its record",
        "reason": (
            "content_text is called with candidate.payload.response_sha256 and can return "
            "only the uniquely matching ProviderContentPayload; its verified blob bytes "
            "therefore hash to the candidate digest."
        ),
    },
    {
        "path": "reviewer",
        "abort_ordinal": 4,
        "exception_type": "WorkflowExecutionError",
        "message": "native reviewer recovery record has an invalid logical round",
        "reason": (
            "A replay-selected pending review already passed provider-decision validation, "
            "which requires a numeric logical-id suffix; the fallback branch constructs the "
            "logical id from the positive current WorkUnit round."
        ),
    },
    {
        "path": "reviewer",
        "abort_ordinal": 5,
        "exception_type": "WorkflowExecutionError",
        "message": "pre-policy native reviewer recovery lacks its request binding",
        "reason": (
            "ArtifactStore.load_chain returns schema-validated ArtifactRecord values and "
            "ReviewPayload rejects a missing request_id or response_sha256 during construction."
        ),
    },
    {
        "path": "reviewer",
        "abort_ordinal": 7,
        "exception_type": "WorkflowExecutionError",
        "message": "pre-policy native reviewer recovery has no authoritative provider content",
        "reason": (
            "Structured-v2 replay requires exactly one earlier fingerprint-matched provider "
            "content record and binds its operation to the latest WorkflowTransition when one "
            "exists; state restoration binds current_step to that record-derived cursor, so the "
            "production content_text lookup repeats the resulting role, work-unit, round, "
            "operation, request, response, and fingerprint binding."
        ),
    },
)


@dataclass(frozen=True)
class _FingerprintRef:
    sha256: str


@dataclass(frozen=True)
class _Record:
    record_id: str
    logical_id: str
    fingerprint: _FingerprintRef
    payload: object


@dataclass
class _Store:
    chain: tuple[_Record, ...]

    def load_chain(self) -> tuple[_Record, ...]:
        return self.chain

    def current_chain(self) -> tuple[_Record, ...]:
        return self.chain


@dataclass(frozen=True)
class _Bridge:
    store: _Store


def _ordered(function: ast.FunctionDef, kind: type[ast.AST]) -> list[ast.AST]:
    return sorted(
        (node for node in ast.walk(function) if isinstance(node, kind)),
        key=lambda node: (node.lineno, node.col_offset),
    )


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    return node


def _parent_map(node: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}


def _read_names(node: ast.AST) -> list[str]:
    return sorted(
        {
            item.id
            for item in ast.walk(node)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
        }
    )


def _raise_type_and_message(node: ast.Raise) -> tuple[str, str]:
    assert isinstance(node.exc, ast.Call)
    call = node.exc
    exception_type = ast.unparse(call.func)
    message = ast.unparse(call.args[0]) if call.args else ""
    return exception_type, message


def _condition_path(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> list[str]:
    conditions: list[str] = []
    current = node
    while current in parents:
        child = current
        current = parents[current]
        if isinstance(current, ast.If):
            expression = ast.unparse(current.test)
            conditions.append(
                f"else: {expression}" if child in current.orelse else expression
            )
    return list(reversed(conditions))


def _direct_recovery_helper_call(
    statement: ast.stmt,
    helper_names: frozenset[str],
) -> ast.Call | None:
    if not isinstance(statement, ast.Assign):
        return None
    value = statement.value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id == "self"
        and value.func.attr in helper_names
    ):
        return value
    return None


def _logical_recovery_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    function = copy.deepcopy(_function(tree, name))
    helper_names = RECOVERY_HELPERS[name]
    helpers = {
        helper_name: copy.deepcopy(_function(tree, helper_name))
        for helper_name in helper_names
    }
    observed_calls = [
        call.func.attr
        for call in _ordered(function, ast.Call)
        if isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "self"
        and call.func.attr in helper_names
    ]
    assert observed_calls == list(helper_names)

    def expand_body(body: list[ast.stmt]) -> list[ast.stmt]:
        expanded: list[ast.stmt] = []
        for statement in body:
            call = _direct_recovery_helper_call(
                statement,
                frozenset(helper_names),
            )
            if call is not None:
                helper = helpers[call.func.attr]
                parameters = [argument.arg for argument in helper.args.args[1:]]
                assert not call.keywords
                assert [ast.unparse(argument) for argument in call.args] == parameters
                helper_body = copy.deepcopy(helper.body)
                terminal = helper_body[-1]
                assert isinstance(terminal, ast.Return)
                assert terminal.value is not None
                helper_body[-1] = ast.Assign(
                    targets=copy.deepcopy(statement.targets),
                    value=terminal.value,
                )
                expanded.extend(expand_body(helper_body))
                continue
            for field in ("body", "orelse", "finalbody"):
                child = getattr(statement, field, None)
                if isinstance(child, list) and child:
                    setattr(statement, field, expand_body(child))
            if isinstance(statement, ast.Try):
                for handler in statement.handlers:
                    handler.body = expand_body(handler.body)
            expanded.append(statement)
        return expanded

    function.body = expand_body(function.body)
    ast.fix_missing_locations(function)
    logical = ast.parse(ast.unparse(function)).body[0]
    assert isinstance(logical, ast.FunctionDef)
    return logical


def _pre_cut_line_count(name: str) -> int:
    source = subprocess.run(
        ["git", "show", f"{PRE_B54_COMMIT}:src/workflow_recovery.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    function = _function(ast.parse(source), name)
    return function.end_lineno - function.lineno + 1


def _static_layer(tree: ast.Module, name: str, path: str) -> dict[str, object]:
    function = _logical_recovery_function(tree, name)
    parents = _parent_map(function)
    conditions = _ordered(function, ast.If)
    raises = _ordered(function, ast.Raise)
    returns = _ordered(function, ast.Return)
    catchers = _ordered(function, ast.ExceptHandler)
    return {
        "path": path,
        "function": f"WorkflowRecovery.{name}",
        "line_count": _pre_cut_line_count(name),
        "conditions": [
            {
                "ordinal": index,
                "expression": ast.unparse(item.test),
                "read_names": _read_names(item.test),
            }
            for index, item in enumerate(conditions, 1)
        ],
        "aborts": [
            {
                "ordinal": index,
                "exception_type": _raise_type_and_message(item)[0],
                "message": _raise_type_and_message(item)[1],
                "condition_path": _condition_path(item, parents),
            }
            for index, item in enumerate(raises, 1)
        ],
        "returns": [
            {
                "ordinal": index,
                "expression": "" if item.value is None else ast.unparse(item.value),
                "condition_path": _condition_path(item, parents),
            }
            for index, item in enumerate(returns, 1)
        ],
        "catchers": [
            {"ordinal": index, "exception_type": ast.unparse(item.type)}
            for index, item in enumerate(catchers, 1)
        ],
    }


def _static_document(source: str | None = None) -> dict[str, object]:
    tree = ast.parse(SOURCE_PATH.read_text("utf-8") if source is None else source)
    return {
        "schema_version": "workflow-recovery-static-pre-b53-v1",
        "source_commit": SOURCE_COMMIT,
        "source_blob": SOURCE_BLOB,
        "layers": [
            _static_layer(tree, "recover_pending_native_implementer", "implementer"),
            _static_layer(
                tree,
                "recover_pending_native_reviewer_before_policy",
                "reviewer",
            ),
        ],
        "unreachable_aborts": list(UNREACHABLE_ABORTS),
    }


def _content_record(
    record_id: str,
    *,
    role: Role,
    canonical: str,
    request_id: str,
    operation: str,
    content_kind: str,
) -> _Record:
    encoded = canonical.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return _Record(
        record_id,
        f"content-{record_id[-8:]}",
        _FingerprintRef(FINGERPRINT),
        ProviderContentPayload(
            role=role,
            work_unit_id="1",
            round_number=1,
            operation=operation,
            request_id=request_id,
            response_sha256=digest,
            content_kind=content_kind,
            content_bytes=len(encoded),
            blob=BlobReference(digest, len(encoded)),
        ),
    )


def _replay_error() -> ArtifactReplayError:
    return ArtifactReplayError(
        ReplayDiagnostic(ReplayDiagnosticCode.RECORD_MISSING, "B53 injected replay gap")
    )


def _implementer_base() -> dict[str, object]:
    contract = CodexStepContract(
        "b53-implementer",
        ReadinessMarker.IMPLEMENTATION,
        "01",
        1,
        require_test_files_record=True,
        expected_test_files=(),
        test_changes_approved=True,
    )
    native_context = NativeCodexContext(
        run_id=RUN_ID,
        work_unit_id="1",
        operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
        current_fingerprint=FINGERPRINT,
        request_kind=NativeCodexRequestKind.IMPLEMENTATION,
        contract=contract,
    )
    bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=native_context,
            target_branch="feature/backlog-followups",
            base_commit="a" * 40,
            authorized_paths=("tests/test_workflow_recovery_corpus.py",),
            assignment="Bind native implementer recovery.",
            work_context="B53 provider-free recovery corpus.",
            evidence=(
                NativeCodexEvidenceInput(
                    "b53-task", "orchestrator_instruction", "Recover exactly once."
                ),
            ),
        )
    )
    assert bundle.bound_context.request_id == IMPLEMENTER_REQUEST_ID
    document = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": "implementation_result",
        "request_id": bundle.bound_context.request_id,
        "ready": True,
        "test_files": [],
        "finding_dispositions": [],
    }
    canonical = canonical_native_codex_json(document)
    result = parse_bound_native_codex_contract_result(document, bundle.bound_context)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert digest == IMPLEMENTER_RESPONSE_SHA256
    payload = agent_result_payload(
        result,
        role=AgentRole.CODEX,
        work_unit_id=1,
        transport_schema=recovery_module.NATIVE_IMPLEMENTER_TRANSPORT,
        request_id=bundle.bound_context.request_id,
        response_sha256=digest,
    )
    candidate = _Record(
        IMPLEMENTER_RECORD_ID,
        "agent-1-codex_implementation-1",
        _FingerprintRef(FINGERPRINT),
        payload,
    )
    content = _content_record(
        "ar1-" + "3" * 64,
        role=Role.CODEX,
        canonical=canonical,
        request_id=bundle.bound_context.request_id,
        operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
        content_kind="agent_result",
    )
    state = SimpleNamespace(
        run_id=RUN_ID,
        current_work_unit_id=1,
        current_step=WorkflowStep.CODEX_IMPLEMENTATION,
        current_work_unit=SimpleNamespace(open_findings=()),
        protocol_binding=SimpleNamespace(
            codex_result_transport=recovery_module.NATIVE_IMPLEMENTER_TRANSPORT
        ),
    )
    return {
        "contract": contract,
        "bundle": bundle,
        "document": document,
        "canonical": canonical,
        "candidate": candidate,
        "content": content,
        "state": state,
    }


def _attempt_record() -> _Record:
    return _Record(
        "ar1-" + "4" * 64,
        "attempt-b53",
        _FingerprintRef(FINGERPRINT),
        ProviderAttemptPayload(
            provider=Role.CODEX,
            role=Role.CODEX,
            operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
            work_unit_id="1",
            logical_operation_id="provider-operation-" + "5" * 64,
            binding_fingerprint=FINGERPRINT,
            measurement_record_id="ar1-" + "6" * 64,
            input_digest="7" * 64,
            attempt_number=1,
            phase="started",
            started_at="2026-09-04T12:00:00+00:00",
            ended_at=None,
            duration_seconds=None,
            failure_kind=None,
            usage=None,
        ),
    )


def _measurement_record(
    *,
    role: Role,
    operation: str,
    work_unit_id: str,
    record_id: str = "ar1-" + "6" * 64,
) -> _Record:
    return _Record(
        record_id,
        f"measurement-{role.value}-{work_unit_id}",
        _FingerprintRef(FINGERPRINT),
        ProviderInputMeasurementPayload(
            provider=role,
            role=role,
            operation=operation,
            work_unit_id=work_unit_id,
            transition_fingerprint="8" * 64,
            relevant_record_head="9" * 64,
            input_digest="7" * 64,
            policy_digest="a" * 64,
            components=(ProviderInputComponentPayload("request", 1, 1),),
            total_chars=1,
            total_bytes=1,
            safety_limit_chars=2,
            safety_limit_bytes=2,
            technical_limit_chars=None,
            technical_limit_bytes=None,
            technical_limit_source=None,
            effective_limit_chars=2,
            effective_limit_bytes=2,
            allowed=True,
            violated_dimensions=(),
            char_overage=0,
            byte_overage=0,
            largest_component="request",
        ),
    )


def _bound_content(
    persisted_content: tuple[str, _Record] | None,
    kwargs: dict[str, object],
    capture: dict[str, object],
) -> tuple[str, _Record] | None:
    capture["content_binding"] = {
        key: kwargs.get(key)
        for key in (
            "role",
            "work_unit_id",
            "request_sequence",
            "operation",
            "request_id",
            "response_sha256",
            "fingerprint",
        )
    }
    if persisted_content is None:
        return None
    canonical, record = persisted_content
    payload = record.payload
    assert isinstance(payload, ProviderContentPayload)
    if (
        payload.role is not kwargs["role"]
        or payload.work_unit_id != str(kwargs["work_unit_id"])
        or payload.round_number != kwargs["request_sequence"]
        or payload.operation != kwargs["operation"]
        or (
            kwargs.get("request_id") is not None
            and payload.request_id != kwargs["request_id"]
        )
        or (
            kwargs.get("response_sha256") is not None
            and payload.response_sha256 != kwargs["response_sha256"]
        )
        or (
            kwargs.get("fingerprint") is not None
            and record.fingerprint.sha256 != kwargs["fingerprint"]
        )
    ):
        return None
    return canonical, record


def _content_binding_state(capture: dict[str, object]) -> dict[str, object]:
    binding = capture["content_binding"]
    assert isinstance(binding, dict)
    return {
        key: value.value if isinstance(value, Role) else value
        for key, value in binding.items()
    }


def _dependencies(
    module: ModuleType,
    *,
    state: object,
    chain: tuple[_Record, ...],
    persisted_content: tuple[str, _Record] | None,
    capture: dict[str, object],
    provider_counter: dict[str, int],
    recovery_bundle: object | None = None,
) -> object:
    def forbidden_provider(*_args: object, **_kwargs: object) -> object:
        provider_counter["count"] += 1
        raise AssertionError("B53 recovery must not start a provider")

    def canonical_result(candidates: tuple[_Record, ...], _logical: str) -> _Record | None:
        chosen = candidates[0] if candidates else None
        capture["selected_record_id"] = None if chosen is None else chosen.record_id
        return chosen

    def persist_implementer(
        output: object,
        request_sequence: int,
        _findings: tuple[object, ...],
        *,
        recovery_fingerprint: str | None = None,
    ) -> None:
        capture["implementer_output"] = output
        capture["implementer_request_sequence"] = request_sequence
        capture["recovery_fingerprint"] = recovery_fingerprint

    def persist_review(
        output: object,
        fingerprint: str,
        round_number: int,
        request_sequence: int,
        _findings: tuple[object, ...],
    ) -> None:
        capture["review_output"] = output
        capture["review_fingerprint"] = fingerprint
        capture["review_round"] = round_number
        capture["review_request_sequence"] = request_sequence

    return module.WorkflowRecoveryDependencies(
        root=ROOT,
        artifact_bridge=lambda: _Bridge(_Store(chain)),
        active_state=lambda: state,
        reconcile_external_attempt=forbidden_provider,
        side_effect_executor=forbidden_provider,
        side_effect_spec=forbidden_provider,
        mark_side_effect_completed=lambda _key: None,
        attempt_response_path=lambda path, _attempt: path,
        canonical_agent_result=canonical_result,
        load_agent_request_bundle=(
            lambda _invocation, _bundle, _request_id=None, _findings=None: (
                recovery_bundle
            )
        ),
        content_text=lambda **kwargs: _bound_content(
            persisted_content,
            kwargs,
            capture,
        ),
        persist_implementer_contract=persist_implementer,
        persist_review_contract=persist_review,
        store_implementer_output=lambda canonical: capture.setdefault(
            "stored_implementer", canonical
        ),
        agent_profile=forbidden_provider,
    )


def _run_implementer_scenario(
    module: ModuleType,
    spec: ScenarioSpec,
    provider_counter: dict[str, int],
) -> dict[str, object]:
    base = _implementer_base()
    bundle = base["bundle"]
    candidate = base["candidate"]
    canonical = base["canonical"]
    content = base["content"]
    assert isinstance(candidate, _Record)
    assert isinstance(candidate.payload, AgentResultPayload)
    assert isinstance(canonical, str)
    assert isinstance(content, _Record)
    chain: tuple[_Record, ...] = (content, candidate)
    persisted_content: tuple[str, _Record] | None = (canonical, content)
    recovery_bundle: object | None = None
    patch_replay: Callable[..., object] | None = None
    patch_reduce: Callable[..., object] | None = None

    if spec.scenario_id == "implementer-missing-provider-content":
        persisted_content = None
    elif spec.scenario_id in {
        "implementer-missing-original-binding-findings",
        "implementer-request-time-replay-failure",
        "implementer-request-time-finding-subset",
    }:
        offered = SimpleNamespace(finding_id="C-01")
        recovery_bundle = None
        bundle = SimpleNamespace(
            canonical_json=json.dumps(
                {"open_findings": [{"finding_id": "C-01"}]},
                sort_keys=True,
                separators=(",", ":"),
            ),
            bound_context=SimpleNamespace(
                context=SimpleNamespace(
                    run_id=RUN_ID,
                    previous_findings=(offered,),
                ),
                request_id=base["bundle"].bound_context.request_id,
            )
        )
        if spec.scenario_id != "implementer-missing-original-binding-findings":
            request_prefix = _Record(
                "ar1-" + "0" * 64,
                "request-prefix-b53",
                _FingerprintRef(FINGERPRINT),
                object(),
            )
            chain = (
                request_prefix,
                _measurement_record(
                    role=Role.CODEX,
                    operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
                    work_unit_id="1",
                ),
                _attempt_record(),
                content,
                candidate,
            )
        if spec.scenario_id == "implementer-request-time-replay-failure":
            def fail_replay(*_args: object, **_kwargs: object) -> object:
                raise _replay_error()

            patch_replay = fail_replay
        elif spec.scenario_id == "implementer-request-time-finding-subset":
            patch_replay = lambda *_args, **_kwargs: SimpleNamespace()
            patch_reduce = lambda _replay: SimpleNamespace(
                ledger=SimpleNamespace(findings=())
            )
    elif spec.scenario_id == "implementer-missing-original-binding-request-id":
        divergent_request_id = "native-codex-request-" + "8" * 64
        candidate = replace(
            candidate,
            payload=replace(
                candidate.payload,
                request_id=divergent_request_id,
            ),
        )
        content = _content_record(
            "ar1-" + "3" * 64,
            role=Role.CODEX,
            canonical=canonical,
            request_id=divergent_request_id,
            operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
            content_kind="agent_result",
        )
        chain = (content, candidate)
        persisted_content = (canonical, content)
    elif spec.scenario_id in {
        "implementer-raw-response-not-object",
        "implementer-raw-response-noncanonical",
    }:
        canonical = (
            "[]"
            if spec.scenario_id == "implementer-raw-response-not-object"
            else json.dumps(base["document"], ensure_ascii=False)
        )
        content = _content_record(
            "ar1-" + "3" * 64,
            role=Role.CODEX,
            canonical=canonical,
            request_id=base["bundle"].bound_context.request_id,
            operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
            content_kind="agent_result",
        )
        candidate = replace(
            candidate,
            payload=replace(
                candidate.payload,
                response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            ),
        )
        chain = (content, candidate)
        persisted_content = (canonical, content)
    elif spec.scenario_id == "implementer-bound-response-invalid":
        document = dict(base["document"])
        document["request_id"] = "native-codex-request-" + "9" * 64
        canonical = canonical_native_codex_json(document)
        content = _content_record(
            "ar1-" + "3" * 64,
            role=Role.CODEX,
            canonical=canonical,
            request_id=base["bundle"].bound_context.request_id,
            operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
            content_kind="agent_result",
        )
        chain = (content,)
        persisted_content = (canonical, content)
    elif spec.scenario_id == "implementer-record-binding-divergent":
        candidate = replace(
            candidate,
            payload=replace(candidate.payload, work_unit_id="2"),
        )
        chain = (content, candidate)
    elif spec.scenario_id == "implementer-result-divergent":
        candidate = replace(
            candidate,
            payload=replace(candidate.payload, outcome="not_ready"),
        )
        chain = (content, candidate)

    capture: dict[str, object] = {}
    dependencies = _dependencies(
        module,
        state=base["state"],
        chain=chain,
        persisted_content=persisted_content,
        capture=capture,
        provider_counter=provider_counter,
        recovery_bundle=recovery_bundle,
    )
    recovery = module.WorkflowRecovery(dependencies)
    invocation = CodexInvocation(
        1,
        WorkflowStep.CODEX_IMPLEMENTATION,
        1,
        "",
        native_request=bundle,
    )
    before = provider_counter["count"]
    with pytest.MonkeyPatch.context() as patch:
        if patch_replay is not None:
            patch.setattr(module, "replay_artifacts", patch_replay)
            patch.setattr(
                module,
                "project_workflow_state",
                lambda _replay: SimpleNamespace(
                    state=SimpleNamespace(
                        current_work_unit_id=1,
                        current_step=WorkflowStep.CODEX_IMPLEMENTATION,
                        current_work_unit=SimpleNamespace(open_findings=("C-01",)),
                    )
                ),
            )
        if patch_reduce is not None:
            patch.setattr(module, "reduce_findings", patch_reduce)
        try:
            output = recovery.recover_pending_native_implementer(
                invocation,
                base["contract"],
                WorkflowHistory(1),
            )
        except Exception as exc:  # noqa: BLE001 - the baseline binds the exact type
            result: dict[str, object] = {
                "scenario_id": spec.scenario_id,
                "path": spec.path,
                "source_aborts": list(spec.source_aborts),
                "outcome": "raised",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        else:
            assert output is not None
            result = {
                "scenario_id": spec.scenario_id,
                "path": spec.path,
                "source_aborts": list(spec.source_aborts),
                "outcome": "recovered",
                "adopted_record_id": capture["selected_record_id"],
                "state": {
                    "ready": output.result.ready,
                    "request_id": output.request_id,
                    "recovery_fingerprint": capture["recovery_fingerprint"],
                    "content_binding": _content_binding_state(capture),
                },
            }
    result["provider_start_count"] = provider_counter["count"] - before
    return result


@pytest.mark.parametrize(
    "record_ahead",
    (False, True),
    ids=("provider-content-ahead", "agent-result-ahead"),
)
def test_implementer_recovery_uses_request_ledger_after_finding_is_closed(
    monkeypatch: pytest.MonkeyPatch,
    record_ahead: bool,
) -> None:
    finding = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="A finding opened in an earlier work unit remains open.",
        acceptance_test="Recovery accepts its request-bound disposition.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    contract = CodexStepContract(
        "b97-implementer",
        ReadinessMarker.IMPLEMENTATION,
        "16",
        2,
        require_test_files_record=True,
        expected_test_files=(),
        test_changes_approved=True,
    )

    def request(previous_findings: tuple[FindingRecord, ...]):
        return build_native_codex_request(
            NativeCodexRequestSpec(
                context=NativeCodexContext(
                    run_id=RUN_ID,
                    work_unit_id="17",
                    operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
                    current_fingerprint=FINGERPRINT,
                    request_kind=NativeCodexRequestKind.IMPLEMENTATION,
                    contract=contract,
                    previous_findings=previous_findings,
                ),
                target_branch="feature/recovery-response-kontext",
                base_commit="a" * 40,
                authorized_paths=("src/workflow_recovery.py",),
                assignment="Recover the request-bound finding context.",
                work_context="B97 provider-free recovery regression.",
                evidence=(
                    NativeCodexEvidenceInput(
                        "b97-task",
                        "orchestrator_instruction",
                        "Recover exactly once in the original finding context.",
                    ),
                ),
            )
        )

    original_bundle = request((finding,))
    rebuilt_bundle = request(())
    document = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": "implementation_result",
        "request_id": original_bundle.bound_context.request_id,
        "ready": True,
        "test_files": [],
        "finding_dispositions": [
            {
                "finding_id": "C-02",
                "decision": "accepted",
                "rationale": "The earlier finding remains valid and open.",
                "responsibility_proposal": None,
            }
        ],
    }
    canonical = canonical_native_codex_json(document)
    parsed = parse_bound_native_codex_contract_result(
        document, original_bundle.bound_context
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    content = _Record(
        "ar1-" + "8" * 64,
        "content-b97",
        _FingerprintRef(FINGERPRINT),
        ProviderContentPayload(
            role=Role.CODEX,
            work_unit_id="17",
            round_number=2,
            operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
            request_id=original_bundle.bound_context.request_id,
            response_sha256=digest,
            content_kind="agent_result",
            content_bytes=len(canonical.encode("utf-8")),
            blob=BlobReference(digest, len(canonical.encode("utf-8"))),
        ),
    )
    candidate = _Record(
        "ar1-" + "9" * 64,
        "agent-17-codex_implementation-2",
        _FingerprintRef(FINGERPRINT),
        agent_result_payload(
            parsed,
            role=AgentRole.CODEX,
            work_unit_id=17,
            transport_schema=recovery_module.NATIVE_IMPLEMENTER_TRANSPORT,
            request_id=original_bundle.bound_context.request_id,
            response_sha256=digest,
        ),
    )
    base_attempt = _attempt_record()
    attempt = replace(
        base_attempt,
        payload=replace(base_attempt.payload, work_unit_id="17"),
    )
    measurement = _measurement_record(
        role=Role.CODEX,
        operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
        work_unit_id="17",
    )
    request_prefix = _Record(
        "ar1-" + "0" * 64,
        "request-prefix-b97",
        _FingerprintRef(FINGERPRINT),
        object(),
    )
    state = SimpleNamespace(
        run_id=RUN_ID,
        current_work_unit_id=17,
        current_step=WorkflowStep.CODEX_IMPLEMENTATION,
        current_work_unit=SimpleNamespace(
            kind=WorkUnitKind.SLICE,
            open_findings=(),
        ),
        protocol_binding=SimpleNamespace(
            codex_result_transport=recovery_module.NATIVE_IMPLEMENTER_TRANSPORT
        ),
    )
    capture: dict[str, object] = {}
    provider_counter = {"count": 0}
    dependencies = _dependencies(
        recovery_module,
        state=state,
        chain=(
            request_prefix,
            measurement,
            attempt,
            content,
            *((candidate,) if record_ahead else ()),
        ),
        persisted_content=(canonical, content),
        capture=capture,
        provider_counter=provider_counter,
        recovery_bundle=original_bundle,
    )
    monkeypatch.setattr(
        recovery_module,
        "replay_artifacts",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        recovery_module,
        "reduce_findings",
        lambda _replay: SimpleNamespace(
            ledger=SimpleNamespace(findings=(finding,))
        ),
    )
    monkeypatch.setattr(
        recovery_module,
        "project_workflow_state",
        lambda _replay: SimpleNamespace(
            state=SimpleNamespace(
                current_work_unit_id=17,
                current_step=WorkflowStep.CODEX_IMPLEMENTATION,
                current_work_unit=SimpleNamespace(open_findings=("C-02",)),
            )
        ),
    )
    invocation = CodexInvocation(
        17,
        WorkflowStep.CODEX_IMPLEMENTATION,
        2,
        "",
        native_request=rebuilt_bundle,
    )

    output = recovery_module.WorkflowRecovery(
        dependencies
    ).recover_pending_native_implementer(
        invocation,
        contract,
        WorkflowHistory(
            17,
            findings=(
                replace(
                    finding,
                    status=FindingStatus.CLOSED,
                    status_rationale="Closed after the recorded Codex request.",
                ),
            ),
        ),
    )

    assert output is not None
    assert output.result.findings[0].responses[-1].rationale == (
        "The earlier finding remains valid and open."
    )
    assert provider_counter["count"] == 0


def test_implementer_request_ledger_rejects_already_closed_disposition_with_anchor(
) -> None:
    closed = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.CLOSED,
        summary="The finding was already closed before this request.",
        acceptance_test="A stale disposition remains invalid.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
        status_rationale="Closed before the recorded Codex request.",
    )
    contract = CodexStepContract(
        "b123-implementer-invalid",
        ReadinessMarker.IMPLEMENTATION,
        "16",
        2,
        require_test_files_record=True,
        expected_test_files=(),
        test_changes_approved=True,
    )
    bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=NativeCodexContext(
                run_id=RUN_ID,
                work_unit_id="17",
                operation=WorkflowStep.CODEX_IMPLEMENTATION.value,
                current_fingerprint=FINGERPRINT,
                request_kind=NativeCodexRequestKind.IMPLEMENTATION,
                contract=contract,
                previous_findings=(closed,),
            ),
            target_branch="feature/recovery-response-kontext",
            base_commit="a" * 40,
            authorized_paths=("src/workflow_recovery.py",),
            assignment="Reject a stale disposition.",
            work_context="B123 provider-free rejection regression.",
            evidence=(
                NativeCodexEvidenceInput(
                    "b123-task",
                    "orchestrator_instruction",
                    "Reject the stale request-time disposition.",
                ),
            ),
        )
    )
    document = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": "implementation_result",
        "request_id": bundle.bound_context.request_id,
        "ready": True,
        "test_files": [],
        "finding_dispositions": [
            {
                "finding_id": "C-02",
                "decision": "accepted",
                "rationale": "This answer was stale when it was written.",
                "responsibility_proposal": None,
            }
        ],
    }
    snapshot = recovery_module._RequestLedgerSnapshot(
        replay=SimpleNamespace(),
        findings_by_id={"C-02": closed},
        open_finding_ids=(),
        measurement_record_id="ar1-" + "6" * 64,
        relevant_record_head="9" * 64,
        prefix_head_record_id="ar1-" + "7" * 64,
    )
    recovery = recovery_module.WorkflowRecovery(
        _dependencies(
            recovery_module,
            state=SimpleNamespace(),
            chain=(),
            persisted_content=None,
            capture={},
            provider_counter={"count": 0},
        )
    )

    with pytest.raises(
        ValueError,
        match=(
            "disposition references non-open finding C-02.*"
            "measurement=ar1-6666666666666666"
        ),
    ):
        recovery._parse_request_bound_implementer_result(
            document, bundle.bound_context, snapshot
        )


def _reviewer_base() -> dict[str, object]:
    command = "python3 -m pytest tests/ -v"
    attestation = ValidationAttestation(
        "validation-b53",
        FINGERPRINT,
        (command,),
        (ValidationRecord(ValidationStatus.PASS, command, 0, "passed"),),
        "c" * 64,
        "passed",
    )
    context = WorkflowContext(
        assignment="Bind reviewer recovery.",
        distilled_plan="Recover the exact durable reviewer response.",
        slice_summary="B53 reviewer recovery corpus.",
    )
    native_context = NativeReviewContext(
        run_id=RUN_ID,
        work_unit_id="1",
        operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
        diff_fingerprint=FINGERPRINT,
        reviewer=AgentRole.CLAUDE,
        approval_marker=ApprovalMarker.SLICE,
        slice_id="01",
        round_number=1,
        validation_attestation=attestation,
        validation_command_prefixes=context.validation_matrix.finding_command_prefixes,
    )
    bundle = build_native_review_request(
        NativeReviewRequestSpec(
            context=native_context,
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/backlog-followups",
            base_commit="a" * 40,
            authorized_paths=("tests/test_workflow_recovery_corpus.py",),
            acceptance_criteria=("Recover exactly one durable review.",),
            evidence=(
                NativeReviewEvidenceInput(
                    "b53-diff", "full_slice", "B53 test-only diff"
                ),
            ),
        )
    )
    assert bundle.bound_context.request_id == REVIEW_REQUEST_ID
    document = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": bundle.bound_context.request_id,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [],
        "status_changes": [],
        "reclassifications": [],
        "responsibility_routes": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "recovery identity and idempotency",
            "largest_residual_risk": "a detached response record",
            "break_condition": "recovery accepts another review round",
        },
        "pre_mortem": "A crash could detach response bytes from the review fact.",
    }
    canonical = canonical_native_review_json(document)
    result = parse_bound_native_contract_result(document, bundle.bound_context)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert digest == REVIEW_RESPONSE_SHA256
    payload = review_payload(
        result,
        work_unit_id=1,
        transport_schema=recovery_module.NATIVE_REVIEW_TRANSPORT,
        request_id=bundle.bound_context.request_id,
        response_sha256=digest,
    )
    record = _Record(
        REVIEW_RECORD_ID,
        "review-claude-1-1",
        _FingerprintRef(FINGERPRINT),
        payload,
    )
    content = _content_record(
        "ar1-" + "5" * 64,
        role=Role.CLAUDE,
        canonical=canonical,
        request_id=bundle.bound_context.request_id,
        operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
        content_kind="review_result",
    )
    validation_payload = ValidationAttestationPayload(
        results=(
            ValidationResult(
                CommandSpec("pytest", ("python3", "-m", "pytest", "tests/", "-v")),
                "pass",
                0,
                "d" * 64,
            ),
        ),
        attested_by=Role.ORCHESTRATOR,
        output_digest="c" * 64,
        content_record_id="ar1-" + "6" * 64,
    )
    validation_record = _Record(
        "ar1-" + "7" * 64,
        "validation-b53",
        _FingerprintRef(FINGERPRINT),
        validation_payload,
    )
    unit = SimpleNamespace(
        work_unit_id=1,
        round_number=1,
        request_sequence=1,
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        open_findings=(),
        codex_return_count=0,
    )
    state = SimpleNamespace(
        run_id=RUN_ID,
        current_work_unit=unit,
        current_work_unit_id=1,
        current_step=WorkflowStep.CLAUDE_SLICE_REVIEW,
        protocol_binding=SimpleNamespace(
            claude_review_transport=recovery_module.NATIVE_REVIEW_TRANSPORT
        ),
    )
    return {
        "attestation": attestation,
        "context": context,
        "document": document,
        "canonical": canonical,
        "record": record,
        "content": content,
        "validation_record": validation_record,
        "state": state,
    }


def test_reviewer_response_uses_request_ledger_after_finding_is_closed() -> None:
    base = _reviewer_base()
    finding = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The request-time reviewer finding remains actionable.",
        acceptance_test="The reviewer may close it at its own ledger head.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    native_context = NativeReviewContext(
        run_id=RUN_ID,
        work_unit_id="1",
        operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
        diff_fingerprint=FINGERPRINT,
        reviewer=AgentRole.CLAUDE,
        approval_marker=ApprovalMarker.SLICE,
        slice_id="01",
        round_number=1,
        previous_findings=(finding,),
        known_open_findings=(finding,),
        authoritative_finding_ids=("C-02",),
        validation_attestation=base["attestation"],
        validation_command_prefixes=base[
            "context"
        ].validation_matrix.finding_command_prefixes,
    )
    bundle = build_native_review_request(
        NativeReviewRequestSpec(
            context=native_context,
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/recovery-response-kontext",
            base_commit="a" * 40,
            authorized_paths=("src/workflow_recovery.py",),
            acceptance_criteria=("Close the request-time finding.",),
            evidence=(
                NativeReviewEvidenceInput(
                    "b123-diff", "full_slice", "B123 provider-free review."
                ),
            ),
        )
    )
    document = dict(base["document"])
    document["request_id"] = bundle.bound_context.request_id
    document["status_changes"] = [
        {
            "finding_id": "C-02",
            "status": "CLOSED",
            "rationale": "The request-time acceptance test is satisfied.",
            "closure": {"kind": "fixed"},
        }
    ]
    expected = parse_bound_native_contract_result(document, bundle.bound_context)
    payload = review_payload(
        expected,
        work_unit_id=1,
        transport_schema=recovery_module.NATIVE_REVIEW_TRANSPORT,
        request_id=bundle.bound_context.request_id,
        response_sha256="d" * 64,
    )
    snapshot = recovery_module._RequestLedgerSnapshot(
        replay=SimpleNamespace(),
        findings_by_id={"C-02": finding},
        open_finding_ids=("C-02",),
        measurement_record_id="ar1-" + "6" * 64,
        relevant_record_head="9" * 64,
        prefix_head_record_id="ar1-" + "7" * 64,
    )
    current_context = replace(
        native_context,
        previous_findings=(
            replace(
                finding,
                status=FindingStatus.CLOSED,
                status_rationale="Closed after the recorded reviewer request.",
            ),
        ),
        known_open_findings=None,
    )
    rebound = recovery_module.WorkflowRecovery._rebind_reviewer_context_to_request_ledger(
        current_context, snapshot
    )
    recovery = recovery_module.WorkflowRecovery(
        _dependencies(
            recovery_module,
            state=SimpleNamespace(),
            chain=(),
            persisted_content=None,
            capture={},
            provider_counter={"count": 0},
        )
    )

    result = recovery._parse_request_bound_reviewer_result(
        document,
        rebound,
        payload,
        bundle.bound_context.request_digest,
        snapshot,
    )

    assert result.findings[0].status is FindingStatus.CLOSED

    current_bundle = build_native_review_request(
        NativeReviewRequestSpec(
            context=replace(
                current_context,
                previous_findings=(),
                authoritative_finding_ids=("C-02",),
            ),
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/recovery-response-kontext",
            base_commit="a" * 40,
            authorized_paths=("src/workflow_recovery.py",),
            acceptance_criteria=("Close the request-time finding.",),
            evidence=(
                NativeReviewEvidenceInput(
                    "b123-diff", "full_slice", "B123 provider-free review."
                ),
            ),
        )
    )
    assert current_bundle.bound_context.request_id != bundle.bound_context.request_id
    measurement = _measurement_record(
        role=Role.CLAUDE,
        operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
        work_unit_id="1",
    )
    attempt = _Record(
        "ar1-" + "4" * 64,
        "attempt-reviewer-b123",
        _FingerprintRef(FINGERPRINT),
        ProviderAttemptPayload(
            provider=Role.CLAUDE,
            role=Role.CLAUDE,
            operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
            work_unit_id="1",
            logical_operation_id=logical_provider_operation_id(
                run_id=RUN_ID,
                work_unit_id="1",
                provider=Role.CLAUDE,
                operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
                binding_fingerprint=FINGERPRINT,
                operation_instance="round:1",
            ),
            binding_fingerprint=FINGERPRINT,
            measurement_record_id=measurement.record_id,
            input_digest="7" * 64,
            attempt_number=1,
            phase="started",
            started_at="2026-09-04T12:00:00+00:00",
            ended_at=None,
            duration_seconds=None,
            failure_kind=None,
            usage=None,
        ),
    )
    canonical = canonical_native_review_json(document)
    content = _content_record(
        "ar1-" + "5" * 64,
        role=Role.CLAUDE,
        canonical=canonical,
        request_id=bundle.bound_context.request_id,
        operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
        content_kind="review_result",
    )
    persisted_payload = replace(
        payload,
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
    record = _Record(
        "ar1-" + "2" * 64,
        "review-claude-1-1",
        _FingerprintRef(FINGERPRINT),
        persisted_payload,
    )
    chain = (
        base["validation_record"],
        measurement,
        attempt,
        content,
        record,
    )
    capture: dict[str, object] = {}
    recovery = recovery_module.WorkflowRecovery(
        _dependencies(
            recovery_module,
            state=base["state"],
            chain=chain,
            persisted_content=(canonical, content),
            capture=capture,
            provider_counter={"count": 0},
        )
    )
    invocation = ReviewerInvocation(
        work_unit_id=1,
        step=WorkflowStep.CLAUDE_SLICE_REVIEW,
        reviewer=AgentRole.CLAUDE,
        round_number=1,
        evidence_kind=EvidenceKind.FULL_SLICE,
        fingerprint=FINGERPRINT,
        paths=("src/workflow_recovery.py",),
        prompt="",
        native_request=current_bundle,
    )
    contract = StepContract(
        "b123-reviewer-recovery",
        AgentRole.CLAUDE,
        ApprovalMarker.SLICE,
        "01",
        1,
        FINGERPRINT,
        base["attestation"],
    )
    request_state = SimpleNamespace(
        current_work_unit_id=1,
        current_step=WorkflowStep.CLAUDE_SLICE_REVIEW,
        current_work_unit=SimpleNamespace(open_findings=("C-02",)),
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(recovery_module, "replay_artifacts", lambda *_args: snapshot.replay)
        patch.setattr(
            recovery_module,
            "project_workflow_state",
            lambda _replay: SimpleNamespace(state=request_state),
        )
        patch.setattr(
            recovery_module,
            "reduce_findings",
            lambda _replay: SimpleNamespace(
                ledger=SimpleNamespace(findings=(finding,))
            ),
        )
        recovered = recovery.recover_pending_native_reviewer(
            invocation,
            contract,
            WorkflowHistory(1),
        )

    assert recovered is not None
    assert recovered.request_id == bundle.bound_context.request_id
    assert recovered.result.findings[0].status is FindingStatus.CLOSED
    assert capture["review_output"] == recovered

    closed = replace(
        finding,
        status=FindingStatus.CLOSED,
        status_rationale="Closed before the recorded reviewer request.",
    )
    closed_snapshot = replace(
        snapshot,
        findings_by_id={"C-02": closed},
        open_finding_ids=(),
    )
    closed_context = recovery._rebind_reviewer_context_to_request_ledger(
        current_context, closed_snapshot
    )
    with pytest.raises(
        ValueError,
        match="measured-at: request-ledger measurement=ar1-6666666666666666",
    ):
        recovery._parse_request_bound_reviewer_result(
            document,
            closed_context,
            payload,
            bundle.bound_context.request_digest,
            closed_snapshot,
        )


def _run_reviewer_scenario(
    module: ModuleType,
    spec: ScenarioSpec,
    provider_counter: dict[str, int],
) -> dict[str, object]:
    base = _reviewer_base()
    record = base["record"]
    content = base["content"]
    canonical = base["canonical"]
    assert isinstance(record, _Record)
    assert isinstance(record.payload, ReviewPayload)
    assert isinstance(content, _Record)
    assert isinstance(canonical, str)
    measurement = _measurement_record(
        role=Role.CLAUDE,
        operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
        work_unit_id="1",
    )
    attempt = _Record(
        "ar1-" + "4" * 64,
        "attempt-reviewer-b53",
        _FingerprintRef(FINGERPRINT),
        ProviderAttemptPayload(
            provider=Role.CLAUDE,
            role=Role.CLAUDE,
            operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
            work_unit_id="1",
            logical_operation_id=logical_provider_operation_id(
                run_id=RUN_ID,
                work_unit_id="1",
                provider=Role.CLAUDE,
                operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
                binding_fingerprint=FINGERPRINT,
                operation_instance="round:1",
            ),
            binding_fingerprint=FINGERPRINT,
            measurement_record_id=measurement.record_id,
            input_digest="7" * 64,
            attempt_number=1,
            phase="started",
            started_at="2026-09-04T12:00:00+00:00",
            ended_at=None,
            duration_seconds=None,
            failure_kind=None,
            usage=None,
        ),
    )
    chain: tuple[_Record, ...] = (
        base["validation_record"],
        measurement,
        attempt,
        content,
        record,
    )
    persisted_content: tuple[str, _Record] | None = (canonical, content)
    history = WorkflowHistory(1, attestations=(base["attestation"],))
    pending_record_id: str | None = record.record_id

    def replay(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(
            pending_review_record_id=pending_record_id,
            subset=lambda _records: SimpleNamespace(),
        )

    patch_replay: Callable[..., object] = replay
    if spec.scenario_id == "reviewer-replay-failure":
        def fail_replay(*_args: object, **_kwargs: object) -> object:
            raise _replay_error()

        patch_replay = fail_replay
    elif spec.scenario_id == "reviewer-duplicate-round-authority":
        duplicate = replace(record, record_id="ar1-" + "8" * 64)
        chain = (
            base["validation_record"], measurement, attempt, content, record, duplicate
        )
        pending_record_id = None
    elif spec.scenario_id == "reviewer-active-unit-divergent":
        record = replace(record, payload=replace(record.payload, work_unit_id="2"))
        chain = (base["validation_record"], measurement, attempt, content, record)
        pending_record_id = record.record_id
    elif spec.scenario_id == "reviewer-attestation-missing":
        chain = (measurement, attempt, content, record)
        history = WorkflowHistory(1)
    elif spec.scenario_id in {
        "reviewer-raw-response-not-object",
        "reviewer-bound-response-invalid",
    }:
        if spec.scenario_id == "reviewer-raw-response-not-object":
            canonical = "[]"
        else:
            document = dict(base["document"])
            document["request_id"] = "native-review-request-" + "9" * 64
            canonical = canonical_native_review_json(document)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        record = replace(record, payload=replace(record.payload, response_sha256=digest))
        content = _content_record(
            "ar1-" + "5" * 64,
            role=Role.CLAUDE,
            canonical=canonical,
            request_id=record.payload.request_id,
            operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
            content_kind="review_result",
        )
        chain = (base["validation_record"], measurement, attempt, content, record)
        persisted_content = (canonical, content)
        pending_record_id = record.record_id
    elif spec.scenario_id == "reviewer-result-divergent":
        record = replace(record, payload=replace(record.payload, verdict="denied"))
        chain = (base["validation_record"], measurement, attempt, content, record)
        pending_record_id = record.record_id
    elif spec.scenario_id == "reviewer-success":
        decoy = replace(
            record,
            record_id="ar1-" + "9" * 64,
            fingerprint=_FingerprintRef("e" * 64),
        )
        chain = (
            base["validation_record"], measurement, attempt, decoy, content, record
        )

    capture: dict[str, object] = {}
    dependencies = _dependencies(
        module,
        state=base["state"],
        chain=chain,
        persisted_content=persisted_content,
        capture=capture,
        provider_counter=provider_counter,
    )
    recovery = module.WorkflowRecovery(dependencies)
    before = provider_counter["count"]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module, "replay_artifacts", patch_replay)
        patch.setattr(
            module,
            "project_workflow_state",
            lambda _replay: SimpleNamespace(state=base["state"]),
        )
        patch.setattr(
            module,
            "reduce_findings",
            lambda _replay: SimpleNamespace(
                ledger=SimpleNamespace(findings=())
            ),
        )
        try:
            output = recovery.recover_pending_native_reviewer_before_policy(
                base["state"],
                base["context"],
                history,
            )
        except Exception as exc:  # noqa: BLE001 - the baseline binds the exact type
            result: dict[str, object] = {
                "scenario_id": spec.scenario_id,
                "path": spec.path,
                "source_aborts": list(spec.source_aborts),
                "outcome": "raised",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        else:
            assert output is not None
            content_binding = capture["content_binding"]
            adopted_record_id = next(
                item.record_id
                for item in chain
                if isinstance(item.payload, ReviewPayload)
                and item.fingerprint.sha256 == content_binding["fingerprint"]
            )
            result = {
                "scenario_id": spec.scenario_id,
                "path": spec.path,
                "source_aborts": list(spec.source_aborts),
                "outcome": "recovered",
                "adopted_record_id": adopted_record_id,
                "state": {
                    "approved": output.output.result.approval,
                    "fingerprint": output.fingerprint,
                    "request_id": output.output.request_id,
                    "round_number": output.round_number,
                    "content_binding": _content_binding_state(capture),
                },
            }
    result["provider_start_count"] = provider_counter["count"] - before
    return result


def _run_scenario(
    module: ModuleType,
    spec: ScenarioSpec,
    provider_counter: dict[str, int],
) -> dict[str, object]:
    if spec.path == "implementer":
        return _run_implementer_scenario(module, spec, provider_counter)
    return _run_reviewer_scenario(module, spec, provider_counter)


_RUNTIME_BUILD_COUNT = 0


@pytest.fixture(scope="session")
def runtime_corpus() -> dict[str, object]:
    global _RUNTIME_BUILD_COUNT
    _RUNTIME_BUILD_COUNT += 1
    provider_counter = {"count": 0}

    def forbidden_real_provider(*_args: object, **_kwargs: object) -> object:
        provider_counter["count"] += 1
        raise AssertionError("B53 must not start a real provider")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(agent_runtime, "run_agent", forbidden_real_provider)
        scenarios = [
            _run_scenario(recovery_module, spec, provider_counter)
            for spec in SCENARIOS
        ]
    assert provider_counter["count"] == 0
    return {
        "schema_version": "workflow-recovery-runtime-pre-b53-v1",
        "source_commit": SOURCE_COMMIT,
        "source_blob": SOURCE_BLOB,
        "scenarios": scenarios,
    }


def _assert_runtime_matches(
    actual: dict[str, object], expected: dict[str, object]
) -> None:
    for key in ("schema_version", "source_commit", "source_blob"):
        if key in expected and actual.get(key) != expected[key]:
            raise AssertionError(key)
    actual_scenarios = {
        item["scenario_id"]: item for item in actual["scenarios"]
    }
    expected_scenarios = {
        item["scenario_id"]: item for item in expected["scenarios"]
    }
    assert list(actual_scenarios) == list(expected_scenarios)
    for scenario_id, expected_scenario in expected_scenarios.items():
        if actual_scenarios[scenario_id] != expected_scenario:
            raise AssertionError(scenario_id)


def _remove_condition_with_message(
    function_name: str, message: str
) -> Callable[[ast.Module], None]:
    def transform(tree: ast.Module) -> None:
        function = _function(tree, function_name)
        target = next(
            item
            for item in _ordered(function, ast.If)
            if any(
                isinstance(statement, ast.Raise)
                and any(
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and message in child.value
                    for child in ast.walk(statement)
                )
                for statement in item.body
            )
        )
        for node in ast.walk(function):
            for field in ("body", "orelse", "finalbody"):
                body = getattr(node, field, None)
                if isinstance(body, list) and target in body:
                    body.remove(target)
                    return
        raise AssertionError(f"condition for {message!r} was not removable")

    return transform


def _helper_catcher_bindings(tree: ast.Module) -> dict[str, str]:
    parents = _parent_map(tree)
    bindings: dict[str, str] = {}
    for helper_name in CAUGHT_HELPERS:
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == helper_name
        ]
        assert len(calls) == 1, helper_name
        current: ast.AST = calls[0]
        while current in parents:
            child = current
            current = parents[current]
            if isinstance(current, ast.Try) and child in current.body:
                assert len(current.handlers) == 1, helper_name
                bindings[helper_name] = ast.unparse(current.handlers[0].type)
                break
        else:
            raise AssertionError(f"helper call left its catcher: {helper_name}")
    return bindings


def _move_helper_call_out_of_catcher(
    helper_name: str,
) -> Callable[[ast.Module], None]:
    def transform(tree: ast.Module) -> None:
        parents = _parent_map(tree)
        call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == helper_name
        )
        current: ast.AST = call
        while current in parents:
            child = current
            current = parents[current]
            if isinstance(current, ast.Try) and child in current.body:
                catcher = current
                statement = child
                break
        else:
            raise AssertionError(helper_name)
        for node in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                body = getattr(node, field, None)
                if isinstance(body, list) and catcher in body:
                    body.insert(body.index(catcher), statement)
                    catcher.body.remove(statement)
                    if not catcher.body:
                        catcher.body.append(ast.Pass())
                    return
        raise AssertionError(f"catcher parent not found: {helper_name}")

    return transform


def _swap_pending_reviewer_record(tree: ast.Module) -> None:
    function = _function(tree, "recover_pending_native_reviewer_before_policy")
    assignment = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and any(ast.unparse(target) == "record" for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "next"
    )
    generator = assignment.value.args[0]
    assert isinstance(generator, ast.GeneratorExp)
    generator.generators[0].ifs = [
        ast.parse(
            "isinstance(item.payload, ReviewPayload) "
            "and item.record_id != pending_record_id",
            mode="eval",
        ).body
    ]


def _load_mutant(transform: Callable[[ast.Module], None]) -> ModuleType:
    tree = ast.parse(SOURCE_PATH.read_text("utf-8"))
    transform(tree)
    ast.fix_missing_locations(tree)
    module = ModuleType(f"_b53_recovery_mutant_{id(tree)}")
    module.__file__ = str(SOURCE_PATH)
    sys.modules[module.__name__] = module
    try:
        exec(compile(tree, str(SOURCE_PATH), "exec"), module.__dict__)
    finally:
        sys.modules.pop(module.__name__, None)
    return module


def test_static_recovery_corpus_is_cleartext_complete_and_source_bound() -> None:
    baseline = json.loads(STATIC_BASELINE.read_text("utf-8"))
    assert _static_document() == baseline
    implementer, reviewer = baseline["layers"]
    assert (len(implementer["conditions"]), len(implementer["aborts"])) == (18, 12)
    assert (len(reviewer["conditions"]), len(reviewer["aborts"])) == (11, 10)
    assert [item["exception_type"] for item in implementer["catchers"]] == [
        "ArtifactReplayError",
        "(ValueError, TypeError)",
    ]
    assert [item["exception_type"] for item in reviewer["catchers"]] == [
        "ArtifactReplayError",
        "(json.JSONDecodeError, ValueError, NativeReviewContractError)",
    ]
    assert all(item["message"] for layer in baseline["layers"] for item in layer["aborts"])
    assert all(
        "read_names" in item
        for layer in baseline["layers"]
        for item in layer["conditions"]
    )


def test_b54_pre_cut_anchor_binds_the_immediate_b53_source() -> None:
    pre_cut = json.loads(PRE_B54_BASELINE.read_text("utf-8"))
    assert pre_cut == {
        "schema_version": "workflow-recovery-pre-b54-v1",
        "source_commit": PRE_B54_COMMIT,
        "source_blob": SOURCE_BLOB,
    }
    anchored_blob = subprocess.run(
        ["git", "rev-parse", f"{PRE_B54_COMMIT}:src/workflow_recovery.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    b53_corpus_blob = subprocess.run(
        ["git", "rev-parse", f"{SOURCE_COMMIT}:src/workflow_recovery.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert anchored_blob == SOURCE_BLOB
    assert b53_corpus_blob == SOURCE_BLOB


def test_b54_helpers_and_previous_catchers_are_explicitly_bound() -> None:
    binding = json.loads(HELPER_BINDINGS.read_text("utf-8"))
    assert binding["schema_version"] == "workflow-recovery-helper-bindings-b54-v1"
    expected_helpers = [
        helper
        for helpers in RECOVERY_HELPERS.values()
        for helper in helpers
    ]
    assert [item["helper"] for item in binding["helpers"]] == expected_helpers
    assert {
        item["helper"]: item["catcher"]
        for item in binding["helpers"]
        if item["catcher"] is not None
    } == CAUGHT_HELPERS
    tree = ast.parse(SOURCE_PATH.read_text("utf-8"))
    assert _helper_catcher_bindings(tree) == CAUGHT_HELPERS


def test_all_five_unreachable_checks_remain_individually_structural() -> None:
    binding = json.loads(HELPER_BINDINGS.read_text("utf-8"))
    static = json.loads(STATIC_BASELINE.read_text("utf-8"))
    expected = {
        (item["path"], item["abort_ordinal"])
        for item in static["unreachable_aborts"]
    }
    post_cut = binding["unreachable_aborts"]
    assert len(post_cut) == 5
    assert {(item["path"], item["abort_ordinal"]) for item in post_cut} == expected
    assert all(item["status"] == "structurally_unreachable" for item in post_cut)
    assert all(item["reason"].strip() for item in post_cut)

    tree = ast.parse(SOURCE_PATH.read_text("utf-8"))
    layers = {item["path"]: item for item in static["layers"]}
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    for item in post_cut:
        abort = layers[item["path"]]["aborts"][item["abort_ordinal"] - 1]
        owners = [
            function.name
            for function in functions
            for raised in _ordered(function, ast.Raise)
            if _raise_type_and_message(raised)
            == (abort["exception_type"], abort["message"])
        ]
        assert owners == [item["owner"]], (item, owners)


def test_each_abort_is_triggered_or_structurally_unreachable(
    runtime_corpus: dict[str, object],
) -> None:
    static = json.loads(STATIC_BASELINE.read_text("utf-8"))
    triggered = {
        (scenario["path"], ordinal)
        for scenario in runtime_corpus["scenarios"]
        for ordinal in scenario["source_aborts"]
    }
    unreachable = {
        (item["path"], item["abort_ordinal"])
        for item in static["unreachable_aborts"]
    }
    expected = {
        (layer["path"], abort["ordinal"])
        for layer in static["layers"]
        for abort in layer["aborts"]
    }
    assert triggered.isdisjoint(unreachable)
    assert triggered | unreachable == expected
    assert all(item["reason"].strip() for item in static["unreachable_aborts"])


def test_runtime_corpus_is_built_once_and_compared_per_scenario(
    runtime_corpus: dict[str, object],
) -> None:
    baseline = json.loads(RUNTIME_BASELINE.read_text("utf-8"))
    _assert_runtime_matches(runtime_corpus, baseline)
    assert _RUNTIME_BUILD_COUNT == 1
    assert len(runtime_corpus["scenarios"]) == len(SCENARIOS) == 19


def test_successes_bind_the_adopted_record_and_resulting_state(
    runtime_corpus: dict[str, object],
) -> None:
    scenarios = {
        item["scenario_id"]: item for item in runtime_corpus["scenarios"]
    }
    implementer = scenarios["implementer-success"]
    reviewer = scenarios["reviewer-success"]
    assert implementer["adopted_record_id"] == IMPLEMENTER_RECORD_ID
    assert implementer["state"]["ready"] is True
    assert implementer["state"]["request_id"] == IMPLEMENTER_REQUEST_ID
    assert implementer["state"]["recovery_fingerprint"] == FINGERPRINT
    assert implementer["state"]["content_binding"] == {
        "role": "codex",
        "work_unit_id": 1,
        "request_sequence": 1,
        "operation": "codex_implementation",
        "request_id": IMPLEMENTER_REQUEST_ID,
        "response_sha256": IMPLEMENTER_RESPONSE_SHA256,
        "fingerprint": FINGERPRINT,
    }
    assert reviewer["adopted_record_id"] == REVIEW_RECORD_ID
    assert reviewer["state"] == {
        "approved": True,
        "fingerprint": FINGERPRINT,
        "request_id": REVIEW_REQUEST_ID,
        "round_number": 1,
        "content_binding": {
            "role": "claude",
            "work_unit_id": 1,
            "request_sequence": 1,
            "operation": "claude_slice_review",
            "request_id": REVIEW_REQUEST_ID,
            "response_sha256": REVIEW_RESPONSE_SHA256,
            "fingerprint": FINGERPRINT,
        },
    }


def test_every_runtime_scenario_is_provider_free(
    runtime_corpus: dict[str, object],
) -> None:
    assert all(
        item["provider_start_count"] == 0
        for item in runtime_corpus["scenarios"]
    )


@pytest.mark.parametrize(
    ("function_name", "message", "scenario_id"),
    (
        (
            "recover_pending_native_implementer",
            "native agent recovery record has no authoritative provider content",
            "implementer-missing-provider-content",
        ),
        (
            "recover_pending_native_reviewer_before_policy",
            "pre-policy native reviewer recovery has duplicate round authority",
            "reviewer-duplicate-round-authority",
        ),
    ),
)
def test_removing_one_condition_per_path_names_the_red_scenario(
    runtime_corpus: dict[str, object],
    function_name: str,
    message: str,
    scenario_id: str,
) -> None:
    module = _load_mutant(_remove_condition_with_message(function_name, message))
    spec = next(item for item in SCENARIOS if item.scenario_id == scenario_id)
    actual_scenario = _run_scenario(module, spec, {"count": 0})
    expected_scenario = next(
        item
        for item in runtime_corpus["scenarios"]
        if item["scenario_id"] == scenario_id
    )
    with pytest.raises(AssertionError, match=scenario_id):
        _assert_runtime_matches(
            {"scenarios": [actual_scenario]},
            {"scenarios": [expected_scenario]},
        )


def test_moving_a_recovery_helper_out_of_its_catcher_turns_binding_red() -> None:
    tree = ast.parse(SOURCE_PATH.read_text("utf-8"))
    helper_name = "_parse_pending_native_reviewer_response"
    _move_helper_call_out_of_catcher(helper_name)(tree)
    ast.fix_missing_locations(tree)
    compile(tree, "<b54-helper-outside-catcher>", "exec")
    with pytest.raises(AssertionError, match=helper_name):
        _helper_catcher_bindings(tree)


def test_swapping_the_adopted_reviewer_record_names_the_success_scenario(
    runtime_corpus: dict[str, object],
) -> None:
    module = _load_mutant(_swap_pending_reviewer_record)
    scenario_id = "reviewer-success"
    spec = next(item for item in SCENARIOS if item.scenario_id == scenario_id)
    actual_scenario = _run_scenario(module, spec, {"count": 0})
    expected_scenario = next(
        item
        for item in runtime_corpus["scenarios"]
        if item["scenario_id"] == scenario_id
    )
    with pytest.raises(AssertionError, match=scenario_id):
        _assert_runtime_matches(
            {"scenarios": [actual_scenario]},
            {"scenarios": [expected_scenario]},
        )


def test_b54_entries_and_helpers_are_below_the_b32_threshold() -> None:
    tree = ast.parse(SOURCE_PATH.read_text("utf-8"))
    names = {
        *RECOVERY_HELPERS,
        *(helper for helpers in RECOVERY_HELPERS.values() for helper in helpers),
    }
    spans = {
        name: _function(tree, name).end_lineno - _function(tree, name).lineno + 1
        for name in names
    }
    assert all(span < 200 for span in spans.values()), spans
    size_baseline = json.loads(FUNCTION_SIZE_BASELINE.read_text("utf-8"))
    assert (
        "src/workflow_recovery.py::WorkflowRecovery."
        "recover_pending_native_implementer"
    ) not in size_baseline["functions"]
    assert (
        "src/workflow_recovery.py::WorkflowRecovery."
        "recover_pending_native_reviewer_before_policy"
    ) not in size_baseline["functions"]


def test_b25_record_sequence_baseline_remains_byte_identical() -> None:
    actual_blob = subprocess.run(
        ["git", "hash-object", str(RECORD_SEQUENCE_BASELINE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    head_blob = subprocess.run(
        [
            "git",
            "rev-parse",
            f"HEAD:{RECORD_SEQUENCE_BASELINE.relative_to(ROOT).as_posix()}",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert actual_blob == head_blob == RECORD_SEQUENCE_BLOB
