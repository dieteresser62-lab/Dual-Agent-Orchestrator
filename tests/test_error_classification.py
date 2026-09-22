from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

import artifact_models
import repo_changes
import workflow_state
from agent_runtime import (
    AgentProcessError,
    ProviderRequestRoundRequired,
    QuotaReachedError,
)
from artifact_resume import ArtifactResumeError
from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BlobReference,
    Fingerprint,
    FingerprintKind,
    ProviderContentPayload,
    ReviewPayload,
    Role,
)
from artifact_replay import (
    ArtifactReplayError,
    ArtifactReplayResult,
    ReplayDiagnostic,
    ReplayDiagnosticCode,
)
from dry_run_scenarios import ScriptedInterruption
from error_classification import (
    ERROR_CLASSIFICATIONS,
    FailureClass,
    _HALT_DIAGNOSTIC_BY_CODE,
    _assert_halt_diagnostic_coverage,
    classify_exception,
    enforce_record_start_boundary,
)
from final_review_preflight import FinalReviewPreflightDenied
from native_codex_contract import (
    NATIVE_CODEX_RESPONSE_RETRY_CODES,
    NativeCodexContractError,
    NativeCodexErrorCode,
    NativeImplementerRejectionSource,
)
from native_review_contract import (
    NATIVE_REVIEW_RESPONSE_RETRY_CODES,
    NativeReviewContractError,
    NativeReviewErrorCode,
    NativeReviewRejectionSource,
)
from orchestrator import ProductionWorkflowDriver
from provider_input_budget import ProviderInputBudgetExceeded
from task_contract import TaskContractError, parse_task_contract
from workflow import (
    WorkflowCommitApprovalRequired,
    WorkflowCompletionRejected,
    WorkflowEngine,
    WorkflowExecutionError,
    WorkflowHistory,
)
from workflow_state import ProtocolBinding, ProtocolMode, init_workflow_state


ROOT = Path(__file__).resolve().parents[1]
FAILED = ROOT / "tests" / "fixtures" / "error_classification"


def _capture(call: Callable[[], object]) -> BaseException:
    try:
        call()
    except BaseException as exc:
        return exc
    raise AssertionError("historical failure producer did not fail")


def _invalid_target_branch() -> BaseException:
    return _capture(
        lambda: parse_task_contract(
            "\n".join(
                (
                    "ORCHESTRATOR_MODE: IMPLEMENT",
                    "TARGET_BRANCH: main",
                    "TASK_SCOPE: src/**",
                )
            )
        )
    )


def _invalid_managed_document(tmp_path: Path) -> BaseException:
    relative = "docs/internal/corpus-review.md"
    document = tmp_path / relative
    document.parent.mkdir(parents=True)
    document.write_text(
        "# Review\n<!-- audit:claude-review:begin -->\n",
        encoding="utf-8",
    )
    return _capture(
        lambda: repo_changes._read_path_payload(  # noqa: SLF001 - regression seam
            tmp_path,
            relative,
            semantic_markdown_paths=frozenset((relative,)),
        )
    )


def _invalid_recovery_response(tmp_path: Path) -> BaseException:
    state = init_workflow_state(
        run_id="invalid-recovery-response",
        task_file=str(tmp_path / "task.md"),
        branch="feature/corpus",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            codex_result_transport="native-codex-v2",
        ),
    )
    driver = object.__new__(ProductionWorkflowDriver)
    driver.root = tmp_path
    driver.active_state = state
    raw = b"{}"
    blob = BlobReference(hashlib.sha256(raw).hexdigest(), len(raw))
    content_record = ArtifactRecord.create(
        run_id=state.run_id,
        logical_id="provider-content-invalid-recovery",
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.CONTRACT, "a" * 64),
        predecessor_ids=(),
        created_at="2026-08-29T10:00:00+00:00",
        idempotency_key="provider-content:invalid-recovery",
        payload=ProviderContentPayload(
            role=Role.CODEX,
            work_unit_id=str(state.current_work_unit_id),
            round_number=1,
            operation=state.current_step.value,
            request_id="native-codex-request-" + "b" * 64,
            response_sha256=blob.sha256,
            content_kind="agent_result",
            content_bytes=len(raw),
            blob=blob,
        ),
    )
    driver._artifact_bridge = SimpleNamespace(  # noqa: SLF001
        store=SimpleNamespace(
            load_chain=lambda: (content_record,),
            current_chain=lambda: (content_record,),
            read_blob=lambda _reference: b"[]",
        )
    )
    driver._load_native_agent_request_bundle = lambda *_args: None  # type: ignore[method-assign]  # noqa: SLF001,E501
    invocation = SimpleNamespace(
        work_unit_id=state.current_work_unit_id,
        step=state.current_step,
        round_number=state.current_work_unit.round_number,
        request_sequence=state.current_work_unit.request_sequence,
        native_request=SimpleNamespace(
            bound_context=SimpleNamespace(
                context=SimpleNamespace(previous_findings=())
            )
        ),
    )
    return _capture(
        lambda: driver.recover_pending_native_codex(
            invocation, None, WorkflowHistory(state.current_work_unit_id)
        )
    )


def _divergent_recovery_records() -> BaseException:
    logical_id = "agent-1-codex-implementation-1"
    canonical = ArtifactRecord.create(
        run_id="divergent-recovery",
        logical_id=logical_id,
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.CONTRACT, "a" * 64),
        predecessor_ids=(),
        created_at="2026-08-29T10:00:00+00:00",
        idempotency_key="native:divergent-recovery",
        payload=AgentResultPayload(
            role=Role.CODEX,
            work_unit_id="1",
            outcome="ready",
            test_files=(),
            transport_schema="native-codex-v2",
            request_id="native-codex-request-" + "b" * 64,
            response_sha256="c" * 64,
        ),
    )
    divergent = replace(
        canonical,
        payload=replace(canonical.payload, outcome="not_ready"),
    )
    return _capture(
        lambda: ProductionWorkflowDriver._canonical_native_agent_result(  # noqa: SLF001
            (canonical, divergent), logical_id
        )
    )


def _incomplete_final_attestation() -> BaseException:
    return ArtifactReplayError(
        ReplayDiagnostic(
            ReplayDiagnosticCode.UNSUPPORTED_PROTOCOL,
            "legacy final-review chain is read-only; inspect it with "
            "scripts/verify_legacy_chain.py",
        )
    )


def _mismatched_persisted_request(tmp_path: Path) -> BaseException:
    state = init_workflow_state(
        run_id="mismatched-native-request",
        task_file=str(tmp_path / "task.md"),
        branch="feature/corpus",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    )
    driver = object.__new__(ProductionWorkflowDriver)
    driver.root = tmp_path
    driver.active_state = state
    invocation = SimpleNamespace(
        work_unit_id=state.current_work_unit_id,
        step=state.current_step,
        round_number=state.current_work_unit.round_number,
        request_sequence=state.current_work_unit.request_sequence,
        native_request=SimpleNamespace(
            canonical_json="{}",
            provider_response_schema_json="{}",
            evidence_assets=(),
        ),
    )
    request_path = driver._native_agent_request_path(invocation)  # noqa: SLF001
    request_path.parent.mkdir(parents=True)
    request_path.write_text("different", encoding="utf-8")
    return _capture(
        lambda: driver._persist_native_agent_request_bundle(invocation)  # noqa: SLF001
    )


def _checkpoint_failure_with_prior_quota(tmp_path: Path) -> BaseException:
    state = init_workflow_state(
        run_id="checkpoint-mirror-ambiguous",
        task_file=str(tmp_path / "task.md"),
        branch="feature/corpus",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    )
    driver = object.__new__(ProductionWorkflowDriver)
    driver.active_state = None
    driver._bind_artifact_store = lambda _state: None  # type: ignore[method-assign]  # noqa: SLF001,E501

    def fail_baseline(_state: object) -> None:
        raise ArtifactResumeError("MIRROR-AMBIGUOUS")

    driver._persist_structured_baseline = fail_baseline  # type: ignore[method-assign]  # noqa: SLF001,E501
    driver._project_audit = lambda *_args: None  # type: ignore[method-assign]  # noqa: SLF001,E501
    history = WorkflowHistory(state.current_work_unit_id)
    try:
        raise QuotaReachedError("codex", "quota reached before checkpoint")
    except QuotaReachedError:
        return _capture(lambda: driver.checkpoint(state, history))


def _record_authority_resume_failure() -> BaseException:
    # The historical poison report predates the S4b cutover. Its old
    # record/mirror mismatch now maps to the record-native resume failure that
    # replaces it, without reintroducing mirror-dependent production logic.
    return ArtifactResumeError("record/native finding authority is incomplete")


def test_central_inventory_classifies_all_49_project_error_types_exactly_once() -> None:
    declared: dict[str, tuple[str, tuple[str, ...]]] = {}
    for path in (ROOT / "src").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = tuple(
                base.id
                if isinstance(base, ast.Name)
                else base.attr
                if isinstance(base, ast.Attribute)
                else ""
                for base in node.bases
            )
            declared[f"{path.stem}.{node.name}"] = (node.name, bases)

    named_errors = {
        qualified
        for qualified, (name, _bases) in declared.items()
        if name.endswith("Error") or name == "SchemaMismatch"
    }
    exception_base_names = {
        "BaseException",
        "Exception",
        "RuntimeError",
        "ValueError",
        "TypeError",
        "OSError",
        *(name for name, _bases in declared.values() if name.endswith("Error")),
        "SchemaMismatch",
    }
    project_exceptions = set(named_errors)
    changed = True
    while changed:
        changed = False
        for qualified, (name, bases) in declared.items():
            if qualified in project_exceptions:
                continue
            if any(base in exception_base_names for base in bases):
                project_exceptions.add(qualified)
                exception_base_names.add(name)
                changed = True

    registered = {
        f"{error_type.__module__}.{error_type.__name__}"
        for error_type in ERROR_CLASSIFICATIONS
    }

    assert len(named_errors) == 49
    assert registered == project_exceptions
    assert project_exceptions - named_errors == {
        f"{FinalReviewPreflightDenied.__module__}.FinalReviewPreflightDenied",
        f"{WorkflowCommitApprovalRequired.__module__}.WorkflowCommitApprovalRequired",
        f"{WorkflowCompletionRejected.__module__}.WorkflowCompletionRejected",
        f"{ProviderInputBudgetExceeded.__module__}.ProviderInputBudgetExceeded",
        f"{ProviderRequestRoundRequired.__module__}.ProviderRequestRoundRequired",
        f"{ScriptedInterruption.__module__}.ScriptedInterruption",
    }
    assert set(ERROR_CLASSIFICATIONS.values())
    assert all(
        isinstance(failure_class, FailureClass) and diagnostic_code
        for failure_class, diagnostic_code in ERROR_CLASSIFICATIONS.values()
    )


def test_halt_diagnostic_inventory_rejects_a_missing_error_class_rule() -> None:
    class FutureHaltError(RuntimeError):
        pass

    extended = {
        **ERROR_CLASSIFICATIONS,
        FutureHaltError: (FailureClass.RESUMABLE_HALT, "FUTURE-HALT"),
    }

    with pytest.raises(AssertionError):
        _assert_halt_diagnostic_coverage(extended, _HALT_DIAGNOSTIC_BY_CODE)


def test_wrapped_typed_cause_keeps_its_transient_class() -> None:
    try:
        raise AgentProcessError("temporary process failure")
    except AgentProcessError as cause:
        try:
            raise WorkflowExecutionError("structured persistence failed") from cause
        except WorkflowExecutionError as wrapped:
            classified = classify_exception(wrapped)

    assert classified.failure_class is FailureClass.TRANSIENT
    assert classified.diagnostic_code == "AGENT-PROCESS"
    assert classified.exception_type == "AgentProcessError"
    assert classified.cause_depth == 1


def test_implicit_context_cannot_override_later_explicit_checkpoint_failure(
    tmp_path: Path,
) -> None:
    classified = classify_exception(_checkpoint_failure_with_prior_quota(tmp_path))

    assert classified.failure_class is FailureClass.RESUMABLE_HALT
    assert classified.diagnostic_code == "ARTIFACT-RESUME"
    assert classified.exception_type == "ArtifactResumeError"
    assert classified.cause_depth == 1


def test_unknown_error_fails_closed_without_message_inference() -> None:
    classified = classify_exception(RuntimeError("quota network timeout"))

    assert classified.failure_class is FailureClass.RESUMABLE_HALT
    assert classified.diagnostic_code == "UNCLASSIFIED-ERROR"
    assert classified.explicitly_mapped is False


@pytest.mark.parametrize("code", tuple(NativeReviewErrorCode))
def test_native_review_rejections_distinguish_context_from_model_response(
    code: NativeReviewErrorCode,
) -> None:
    classified = classify_exception(NativeReviewContractError(code, "rejected"))

    if code is NativeReviewErrorCode.CONTEXT_INVALID:
        assert classified.failure_class is FailureClass.RESUMABLE_HALT
        assert classified.diagnostic_code == "NATIVE-REVIEW-CONTRACT"
    else:
        assert classified.failure_class is FailureClass.TRANSIENT
        assert classified.diagnostic_code == "NATIVE-REVIEW-FORM"


def test_local_review_schema_failure_is_not_retried_as_model_output() -> None:
    classified = classify_exception(
        NativeReviewContractError(
            NativeReviewErrorCode.SCHEMA_INVALID,
            "bundled schema is invalid",
            source=NativeReviewRejectionSource.REQUEST_LEDGER,
        )
    )

    assert classified.failure_class is FailureClass.RESUMABLE_HALT
    assert classified.diagnostic_code == "NATIVE-REVIEW-CONTRACT"


@pytest.mark.parametrize("code", tuple(NativeCodexErrorCode))
def test_native_codex_rejections_distinguish_context_from_model_response(
    code: NativeCodexErrorCode,
) -> None:
    classified = classify_exception(NativeCodexContractError(code, "rejected"))

    if code is NativeCodexErrorCode.CONTEXT_INVALID:
        assert classified.failure_class is FailureClass.RESUMABLE_HALT
        assert classified.diagnostic_code == "NATIVE-IMPLEMENTER-CONTRACT"
    else:
        assert classified.failure_class is FailureClass.TRANSIENT
        assert classified.diagnostic_code == "NATIVE-IMPLEMENTER-FORM"


def test_local_codex_schema_failure_is_not_retried_as_model_output() -> None:
    classified = classify_exception(
        NativeCodexContractError(
            NativeCodexErrorCode.SCHEMA_INVALID,
            "bundled schema is invalid",
            source=NativeImplementerRejectionSource.REQUEST_LEDGER,
        )
    )

    assert classified.failure_class is FailureClass.RESUMABLE_HALT
    assert classified.diagnostic_code == "NATIVE-IMPLEMENTER-CONTRACT"


def test_retry_rejection_code_wire_inventories_match_the_contract_enum() -> None:
    expected = {code.value for code in NATIVE_REVIEW_RESPONSE_RETRY_CODES}

    assert workflow_state.NATIVE_REVIEW_RESPONSE_REJECTION_CODES == expected
    assert artifact_models._NATIVE_REVIEW_RESPONSE_REJECTION_CODES == expected

    implementer_expected = {
        code.value for code in NATIVE_CODEX_RESPONSE_RETRY_CODES
    }
    assert (
        workflow_state.NATIVE_IMPLEMENTER_RESPONSE_REJECTION_CODES
        == implementer_expected
    )
    assert (
        artifact_models._NATIVE_IMPLEMENTER_RESPONSE_REJECTION_CODES
        == implementer_expected
    )


def test_terminal_rejection_is_promoted_after_record_start() -> None:
    failure = classify_exception(TaskContractError("invalid task"))

    before = enforce_record_start_boundary(failure, records_written=False)
    after = enforce_record_start_boundary(failure, records_written=True)

    assert before.failure_class is FailureClass.TERMINAL_REJECTION
    assert after.failure_class is FailureClass.RESUMABLE_HALT
    assert after.promoted_after_record_start is True
    assert after.diagnostic_code == "TASK-CONTRACT-AFTER-RECORD-START"


@pytest.mark.parametrize(
    ("report_name", "fragment", "producer", "expected", "reason"),
    (
        (
            "20260826T130701.912Z_Menschenlesbarkeit_der_Auditprojektion_verbessern-implement.md.poison.error.json",
            "authoritative finding replay differs",
            lambda _tmp: _record_authority_resume_failure(),
            FailureClass.RESUMABLE_HALT,
            "record/native finding authority already exists",
        ),
        (
            "20260828T110617.393Z_plan-only-finding-handoffverlust.md.poison.error.json",
            "TARGET_BRANCH must use feature/<name> or codex/<name>",
            lambda _tmp: _invalid_target_branch(),
            FailureClass.TERMINAL_REJECTION,
            "task contract is rejected before the first record",
        ),
        (
            "20260828T111715.118Z_plan-only-finding-handoffverlust.md.poison.error.json",
            "slice document is missing managed sections",
            _invalid_managed_document,
            FailureClass.RESUMABLE_HALT,
            "managed plan/audit content belongs to a begun workflow",
        ),
        (
            "20260828T123228.238Z_plan-only-finding-handoffverlust-implement.md.poison.error.json",
            "native Codex recovery response no longer validates",
            _invalid_recovery_response,
            FailureClass.RESUMABLE_HALT,
            "recovery validation is bound to persisted request records",
        ),
        (
            "20260828T154937.759Z_plan-only-finding-handoffverlust-implement.md.poison.error.json",
            "native Codex recovery has multiple agent-result records",
            lambda _tmp: _divergent_recovery_records(),
            FailureClass.RESUMABLE_HALT,
            "multiple recovery facts require operator repair",
        ),
        (
            "20260828T171758.214Z_plan-only-finding-handoffverlust-implement.md.poison.error.json",
            "branch final review requires a complete passing attestation",
            lambda _tmp: _incomplete_final_attestation(),
            FailureClass.RESUMABLE_HALT,
            "the final-review work unit and attestation facts already exist",
        ),
        (
            "20260828T182652.829Z_plan-only-finding-handoffverlust-implement.md.poison.error.json",
            "native agent request differs from its persisted recovery artifact",
            _mismatched_persisted_request,
            FailureClass.RESUMABLE_HALT,
            "persisted request binding must be repaired, not overwritten",
        ),
        (
            "historical-poison-audit-dual-write.error.json",
            "structured audit dual-write mismatch",
            _checkpoint_failure_with_prior_quota,
            FailureClass.RESUMABLE_HALT,
            "checkpoint cause retains the typed record/mirror halt",
        ),
    ),
)
def test_historical_poison_corpus_is_classified_without_provider_or_message_logic(
    tmp_path: Path,
    report_name: str,
    fragment: str,
    producer: Callable[[Path], BaseException],
    expected: FailureClass,
    reason: str,
) -> None:
    report = json.loads((FAILED / report_name).read_text(encoding="utf-8"))
    assert fragment in report["failure_detail"]
    assert reason

    failure = classify_exception(producer(tmp_path))

    assert failure.failure_class is expected
    assert failure.explicitly_mapped is True
