from __future__ import annotations

import ast
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
from artifact_bridge import agent_result_payload, review_payload
from artifact_models import (
    AgentResultPayload,
    BlobReference,
    CommandSpec,
    ProviderAttemptPayload,
    ProviderContentPayload,
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
    ReadinessMarker,
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
from workflow import CodexInvocation, WorkflowContext, WorkflowHistory
from workflow_state import WorkflowStep, WorkUnitKind


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "src/workflow_recovery.py"
STATIC_BASELINE = ROOT / "tests/fixtures/workflow-recovery-static-pre-b53-v1.json"
RUNTIME_BASELINE = ROOT / "tests/fixtures/workflow-recovery-runtime-pre-b53-v1.json"
SOURCE_COMMIT = "881e944fea9442012aecb7c20b519ea5a2b83bbf"
SOURCE_BLOB = "f3b3cfb77c5b27b8c60d35c7c347095ea8b2514d"
RUN_ID = "b53-recovery-corpus"
FINGERPRINT = "b" * 64
IMPLEMENTER_RECORD_ID = "ar1-" + "1" * 64
REVIEW_RECORD_ID = "ar1-" + "2" * 64
IMPLEMENTER_REQUEST_ID = (
    "native-codex-request-0b399180ea55133ee982e06dec17e5e71051cc9e94e6a6a2a360c957cf5e989d"
)
REVIEW_REQUEST_ID = (
    "native-review-request-fd894b4da471f8995767537bc67fa15da5b34934c093e48b38928ddfc5f6a11d"
)
IMPLEMENTER_RESPONSE_SHA256 = (
    "12dcb176a354f275a7047f2b6a1d71c1587f82934fb2a60817c4a5b189cd6184"
)
REVIEW_RESPONSE_SHA256 = (
    "4567f297b391a4bcd019f0d09995b8c61273b8fc9ad6717a0c5549e3268630e8"
)


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


def _static_layer(tree: ast.Module, name: str, path: str) -> dict[str, object]:
    function = _function(tree, name)
    parents = _parent_map(function)
    conditions = _ordered(function, ast.If)
    raises = _ordered(function, ast.Raise)
    returns = _ordered(function, ast.Return)
    catchers = _ordered(function, ast.ExceptHandler)
    return {
        "path": path,
        "function": f"WorkflowRecovery.{name}",
        "line_count": function.end_lineno - function.lineno + 1,
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
            "round_number",
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
        or payload.round_number != kwargs["round_number"]
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
        _findings: tuple[object, ...],
        *,
        recovery_fingerprint: str | None = None,
    ) -> None:
        capture["implementer_output"] = output
        capture["recovery_fingerprint"] = recovery_fingerprint

    def persist_review(
        output: object,
        fingerprint: str,
        round_number: int,
        _findings: tuple[object, ...],
    ) -> None:
        capture["review_output"] = output
        capture["review_fingerprint"] = fingerprint
        capture["review_round"] = round_number

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
        load_agent_request_bundle=lambda _invocation, _bundle: recovery_bundle,
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
            bound_context=SimpleNamespace(
                context=SimpleNamespace(previous_findings=(offered,)),
                request_id=base["bundle"].bound_context.request_id,
            )
        )
        if spec.scenario_id != "implementer-missing-original-binding-findings":
            chain = (_attempt_record(), content, candidate)
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
        slice_id=1,
        kind=WorkUnitKind.SLICE,
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
    chain: tuple[_Record, ...] = (base["validation_record"], content, record)
    persisted_content: tuple[str, _Record] | None = (canonical, content)
    history = WorkflowHistory(1, attestations=(base["attestation"],))
    pending_record_id: str | None = record.record_id

    def replay(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(pending_review_record_id=pending_record_id)

    patch_replay: Callable[..., object] = replay
    if spec.scenario_id == "reviewer-replay-failure":
        def fail_replay(*_args: object, **_kwargs: object) -> object:
            raise _replay_error()

        patch_replay = fail_replay
    elif spec.scenario_id == "reviewer-duplicate-round-authority":
        duplicate = replace(record, record_id="ar1-" + "8" * 64)
        chain = (base["validation_record"], content, record, duplicate)
        pending_record_id = None
    elif spec.scenario_id == "reviewer-active-unit-divergent":
        record = replace(record, payload=replace(record.payload, work_unit_id="2"))
        chain = (base["validation_record"], content, record)
        pending_record_id = record.record_id
    elif spec.scenario_id == "reviewer-attestation-missing":
        chain = (content, record)
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
        chain = (base["validation_record"], content, record)
        persisted_content = (canonical, content)
        pending_record_id = record.record_id
    elif spec.scenario_id == "reviewer-result-divergent":
        record = replace(record, payload=replace(record.payload, verdict="denied"))
        chain = (base["validation_record"], content, record)
        pending_record_id = record.record_id
    elif spec.scenario_id == "reviewer-success":
        decoy = replace(
            record,
            record_id="ar1-" + "9" * 64,
            fingerprint=_FingerprintRef("e" * 64),
        )
        chain = (base["validation_record"], decoy, content, record)

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


def test_recovery_source_is_byte_identical_to_the_b52_poststate() -> None:
    anchored_blob = subprocess.run(
        ["git", "rev-parse", f"{SOURCE_COMMIT}:src/workflow_recovery.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    actual_blob = subprocess.run(
        ["git", "hash-object", str(SOURCE_PATH)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert anchored_blob == SOURCE_BLOB
    assert actual_blob == SOURCE_BLOB


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
        "round_number": 1,
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
            "round_number": 1,
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
