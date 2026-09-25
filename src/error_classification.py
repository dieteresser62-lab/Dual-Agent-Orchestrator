"""Central, typed classification for failures that reach the Watch boundary.

The workflow has exactly three operational failure classes.  Project exception
types are listed explicitly here so a newly introduced type cannot silently
inherit retry behaviour.  Explicit exception causes are followed to preserve
the class of errors wrapped by workflow persistence boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Mapping

from agent_adapters import AgentBudgetError, AgentOutputError, AgentPermissionError
from agent_config import AgentConfigError
from agent_runtime import (
    AgentCompatibilityError,
    AgentInvocationError,
    AgentProcessError,
    ProviderRequestRoundRequired,
    QuotaReachedError,
    is_structured_output_retry_exhaustion,
)
from artifact_bridge import ArtifactBridgeError
from artifact_resume import ArtifactResumeError
from artifact_models import ArtifactValidationError
from artifact_replay import ArtifactReplayError
from artifact_store import (
    ArtifactConflictError,
    ArtifactCorruptionError,
    ArtifactStoreError,
)
from audit_trail import AuditTrailError
from cli import ConfigError
from dry_run_scenarios import DryRunScenarioError, ScriptedInterruption
from final_review_preflight import FinalReviewPreflightDenied
from git_service import GitTransactionError
from native_codex_contract import (  # allowlist:provider -- typed module boundary
    NativeCodexContractError as NativeImplementerContractError,  # allowlist:provider
    find_native_implementer_contract_error,
    is_retryable_native_codex_response_error as is_retryable_native_implementer_response_error,  # allowlist:provider -- typed implementer boundary
)
from native_codex_request import (  # allowlist:provider -- typed module boundary
    NativeCodexRequestError as NativeImplementerRequestError,  # allowlist:provider
)
from native_provider_schema import NativeProviderSchemaError
from native_review_contract import (
    NativeReviewContractError,
    find_native_review_contract_error,
    is_retryable_native_review_response_error,
)
from native_review_request import NativeReviewRequestError
from orchestrator_diagnostics import (
    STRUCTURED_OUTPUT_DIAGNOSTIC_CODE,
    OrchestratorDiagnostic,
)
from path_policy import PathPolicyError
from plan_handoff import AcceptanceReviewLimitReached, PlanHandoffError
from provider_input_budget import ProviderInputBudgetError, ProviderInputBudgetExceeded
from provider_input_efficiency import ProviderInputEfficiencyError
from provider_process import ProviderOutcomeUnknown
from repo_changes import NotGitRepositoryError, RepositoryChangeError
from readable_audit import ReadableAuditError
from review_packets import ReviewPacketError
from schema_validation import SchemaDefinitionError, SchemaMismatch
from semantic_markdown import SemanticMarkdownError
from side_effects import SideEffectReconciliationError
from state_io import (
    ActiveV2StateError,
    StatePathError,
    StateSchemaError,
    UnknownStateVersionError,
)
from task_contract import TaskContractError
from validation_matrix import ValidationMatrixError
from workflow import (
    NoWorkflowChangesError,
    PlanContractValidationError,
    ValidationExecutionError,
    WorkflowCommitApprovalRequired,
    WorkflowCompletionRejected,
    WorkflowContractError,
    WorkflowDriverContractError,
    WorkflowExecutionError,
)
from workflow_state import WorkflowStateValidationError


class FailureClass(StrEnum):
    """The three exhaustive operational outcomes at the Watch boundary."""

    TRANSIENT = "transient"
    RESUMABLE_HALT = "resumable_halt"
    TERMINAL_REJECTION = "terminal_rejection"


@dataclass(frozen=True, slots=True)
class ClassifiedFailure:
    """Typed failure evidence carried from an exception to the Watch result."""

    failure_class: FailureClass
    diagnostic_code: str
    exception_type: str
    detail: str
    cause_depth: int
    explicitly_mapped: bool
    promoted_after_record_start: bool = False


def _entry(
    failure_class: FailureClass, diagnostic_code: str
) -> tuple[FailureClass, str]:
    return failure_class, diagnostic_code


_HALT = FailureClass.RESUMABLE_HALT
_TRANSIENT = FailureClass.TRANSIENT
_REJECT = FailureClass.TERMINAL_REJECTION

# Most terminal rejections describe invalid input and therefore become a
# resumable halt once an authoritative record chain exists.  Reaching the
# configured acceptance-review ceiling is different: it is the intended,
# record-backed terminal outcome of a completed outer loop and must still be
# routed to the failed outbox.
_RECORD_BACKED_TERMINAL_REJECTIONS = frozenset({"ACCEPTANCE-REVIEW-LIMIT"})


# Authoritative inventory: all 48 ``*Error`` classes currently defined in
# ``src/`` plus the schema validator's typed ``SchemaMismatch`` exception and
# the seven project exceptions whose names do not end in ``Error``.  Subclasses
# are intentionally repeated instead of inheriting an implicit classification.
# This keeps the complete assignment readable and auditable in one place.
ERROR_CLASSIFICATIONS: dict[type[BaseException], tuple[FailureClass, str]] = {
    AgentConfigError: _entry(_HALT, "AGENT-CONFIG"),
    AgentOutputError: _entry(_HALT, "AGENT-OUTPUT"),
    AgentPermissionError: _entry(_HALT, "AGENT-PERMISSION"),
    AgentBudgetError: _entry(_HALT, "AGENT-BUDGET"),
    AgentInvocationError: _entry(_TRANSIENT, "AGENT-INVOCATION"),
    QuotaReachedError: _entry(_TRANSIENT, "PROVIDER-QUOTA"),
    AgentCompatibilityError: _entry(_HALT, "AGENT-COMPATIBILITY"),
    AgentProcessError: _entry(_TRANSIENT, "AGENT-PROCESS"),
    ProviderRequestRoundRequired: _entry(_HALT, "PROVIDER-REQUEST-ROUND"),
    ArtifactBridgeError: _entry(_HALT, "ARTIFACT-BRIDGE"),
    ArtifactValidationError: _entry(_HALT, "ARTIFACT-VALIDATION"),
    ArtifactResumeError: _entry(_HALT, "ARTIFACT-RESUME"),
    ArtifactReplayError: _entry(_HALT, "ARTIFACT-REPLAY"),
    ArtifactStoreError: _entry(_HALT, "ARTIFACT-STORE"),
    ArtifactConflictError: _entry(_HALT, "ARTIFACT-CONFLICT"),
    ArtifactCorruptionError: _entry(_HALT, "ARTIFACT-CORRUPTION"),
    AuditTrailError: _entry(_HALT, "AUDIT-TRAIL"),
    ReadableAuditError: _entry(_HALT, "READABLE-AUDIT"),
    ConfigError: _entry(_HALT, "CLI-CONFIG"),
    DryRunScenarioError: _entry(_REJECT, "DRY-RUN-SCENARIO"),
    ScriptedInterruption: _entry(_HALT, "SCRIPTED-INTERRUPTION"),
    FinalReviewPreflightDenied: _entry(_HALT, "FINAL-REVIEW-PREFLIGHT-DENIED"),
    AcceptanceReviewLimitReached: _entry(_REJECT, "ACCEPTANCE-REVIEW-LIMIT"),
    GitTransactionError: _entry(_HALT, "GIT-TRANSACTION"),
    NativeImplementerContractError: _entry(_HALT, "NATIVE-IMPLEMENTER-CONTRACT"),
    NativeImplementerRequestError: _entry(_HALT, "NATIVE-IMPLEMENTER-REQUEST"),
    NativeProviderSchemaError: _entry(_HALT, "NATIVE-PROVIDER-SCHEMA"),
    NativeReviewContractError: _entry(_HALT, "NATIVE-REVIEW-CONTRACT"),
    NativeReviewRequestError: _entry(_HALT, "NATIVE-REVIEW-REQUEST"),
    PathPolicyError: _entry(_HALT, "PATH-POLICY"),
    PlanHandoffError: _entry(_HALT, "PLAN-HANDOFF"),
    PlanContractValidationError: _entry(_HALT, "PLAN-CONTRACT-VALIDATION"),
    ProviderInputBudgetError: _entry(_HALT, "PROVIDER-BUDGET-CONFIG"),
    ProviderInputBudgetExceeded: _entry(_HALT, "PROVIDER-BUDGET-EXCEEDED"),
    ProviderInputEfficiencyError: _entry(_HALT, "PROVIDER-INPUT-EFFICIENCY"),
    RepositoryChangeError: _entry(_HALT, "REPOSITORY-CHANGE"),
    NotGitRepositoryError: _entry(_HALT, "NOT-GIT-REPOSITORY"),
    ReviewPacketError: _entry(_HALT, "REVIEW-PACKET"),
    SchemaDefinitionError: _entry(_HALT, "SCHEMA-DEFINITION"),
    SchemaMismatch: _entry(_HALT, "SCHEMA-MISMATCH"),
    SemanticMarkdownError: _entry(_HALT, "SEMANTIC-MARKDOWN"),
    SideEffectReconciliationError: _entry(_HALT, "SIDE-EFFECT-RECONCILIATION"),
    ProviderOutcomeUnknown: _entry(_HALT, "SIDE-EFFECT-RECONCILIATION"),
    StateSchemaError: _entry(_HALT, "STATE-SCHEMA"),
    UnknownStateVersionError: _entry(_HALT, "UNKNOWN-STATE-VERSION"),
    ActiveV2StateError: _entry(_HALT, "ACTIVE-V2-STATE"),
    StatePathError: _entry(_HALT, "STATE-PATH"),
    TaskContractError: _entry(_REJECT, "TASK-CONTRACT"),
    ValidationMatrixError: _entry(_HALT, "VALIDATION-MATRIX"),
    WorkflowStateValidationError: _entry(_HALT, "WORKFLOW-STATE-VALIDATION"),
    WorkflowExecutionError: _entry(_HALT, "WORKFLOW-EXECUTION"),
    WorkflowCompletionRejected: _entry(_HALT, "WORKFLOW-COMPLETION-REJECTED"),
    WorkflowDriverContractError: _entry(_HALT, "WORKFLOW-DRIVER-CONTRACT"),
    WorkflowCommitApprovalRequired: _entry(_HALT, "WORKFLOW-COMMIT-APPROVAL"),
    NoWorkflowChangesError: _entry(_HALT, "NO-WORKFLOW-CHANGES"),
    WorkflowContractError: _entry(_HALT, "WORKFLOW-CONTRACT"),
    ValidationExecutionError: _entry(_HALT, "VALIDATION-EXECUTION"),
}


_HALT_DIAGNOSTIC_CODES = frozenset(
    {
        "ACTIVE-V2-STATE",
        "AGENT-BUDGET",
        "AGENT-COMPATIBILITY",
        "AGENT-CONFIG",
        "AGENT-OUTPUT",
        "AGENT-PERMISSION",
        "ARTIFACT-BRIDGE",
        "ARTIFACT-CONFLICT",
        "ARTIFACT-CORRUPTION",
        "ARTIFACT-REPLAY",
        "ARTIFACT-RESUME",
        "ARTIFACT-STORE",
        "ARTIFACT-VALIDATION",
        "AUDIT-TRAIL",
        "READABLE-AUDIT",
        "CLI-CONFIG",
        "FINAL-REVIEW-PREFLIGHT-DENIED",
        "GIT-TRANSACTION",
        "NATIVE-IMPLEMENTER-CONTRACT",
        "NATIVE-IMPLEMENTER-REQUEST",
        "NATIVE-PROVIDER-SCHEMA",
        "NATIVE-REVIEW-CONTRACT",
        "NATIVE-REVIEW-REQUEST",
        "NOT-GIT-REPOSITORY",
        "NO-WORKFLOW-CHANGES",
        "PATH-POLICY",
        "PLAN-CONTRACT-VALIDATION",
        "PLAN-HANDOFF",
        "PROVIDER-BUDGET-CONFIG",
        "PROVIDER-BUDGET-EXCEEDED",
        "PROVIDER-INPUT-EFFICIENCY",
        "PROVIDER-REQUEST-ROUND",
        "REPOSITORY-CHANGE",
        "REVIEW-PACKET",
        "SCHEMA-DEFINITION",
        "SCHEMA-MISMATCH",
        "SCRIPTED-INTERRUPTION",
        "SEMANTIC-MARKDOWN",
        "SIDE-EFFECT-RECONCILIATION",
        "STATE-PATH",
        "STATE-SCHEMA",
        "UNKNOWN-STATE-VERSION",
        "VALIDATION-EXECUTION",
        "VALIDATION-MATRIX",
        "WORKFLOW-COMMIT-APPROVAL",
        "WORKFLOW-COMPLETION-REJECTED",
        "WORKFLOW-CONTRACT",
        "WORKFLOW-DRIVER-CONTRACT",
        "WORKFLOW-EXECUTION",
        "WORKFLOW-STATE-VALIDATION",
    }
)
_HALT_DIAGNOSTIC_BY_CODE: dict[str, OrchestratorDiagnostic] = {
    code: OrchestratorDiagnostic.CLASSIFIED_HALT_RULE
    for code in _HALT_DIAGNOSTIC_CODES
}
_HALT_DIAGNOSTIC_BY_CODE["PROVIDER-BUDGET-CONFIG"] = (
    OrchestratorDiagnostic.PROVIDER_BUDGET_CONFIG_RULE
)


def _assert_halt_diagnostic_coverage(
    classifications: Mapping[
        type[BaseException], tuple[FailureClass, str]
    ] = ERROR_CLASSIFICATIONS,
    diagnostics: Mapping[
        str, OrchestratorDiagnostic
    ] = _HALT_DIAGNOSTIC_BY_CODE,
) -> None:
    """Require readable, provider-free guidance for every mapped halt."""

    expected = {
        diagnostic_code
        for failure_class, diagnostic_code in classifications.values()
        if failure_class is FailureClass.RESUMABLE_HALT
    }
    assert set(diagnostics) == expected
    assert all(
        isinstance(diagnostic, OrchestratorDiagnostic)
        for diagnostic in diagnostics.values()
    )


_assert_halt_diagnostic_coverage()


def orchestrator_diagnostic_for_exception(
    error: BaseException,
) -> OrchestratorDiagnostic | None:
    """Return closed operator guidance without copying exception values."""

    diagnostic = getattr(error, "orchestrator_diagnostic", None)
    if isinstance(diagnostic, OrchestratorDiagnostic):
        return diagnostic
    assignment = ERROR_CLASSIFICATIONS.get(type(error))
    if assignment is None or assignment[0] is not FailureClass.RESUMABLE_HALT:
        return None
    return _HALT_DIAGNOSTIC_BY_CODE[assignment[1]]


def classify_exception(error: BaseException) -> ClassifiedFailure:
    """Classify ``error`` from type identity, preferring its deepest typed cause.

    Unknown types are deliberately not inferred from inheritance or message
    text.  They fail closed as an operator halt with an explicit diagnostic.
    """

    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        # Only an explicit ``raise ... from cause`` is a typed wrapping
        # boundary.  ``__context__`` merely records an exception that happened
        # to be handled when the current failure was raised; following it can
        # let an older transient provider error override a later deterministic
        # checkpoint or record-authority failure.
        current = current.__cause__

    if (
        isinstance(error, AgentInvocationError)
        and is_structured_output_retry_exhaustion(error.provider_data)
    ):
        return ClassifiedFailure(
            failure_class=_TRANSIENT,
            diagnostic_code=STRUCTURED_OUTPUT_DIAGNOSTIC_CODE,
            exception_type=type(error).__name__,
            detail=(
                f"{type(error).__name__}: provider_diagnostic.subtype="
                "error_max_structured_output_retries"
            ),
            cause_depth=0,
            explicitly_mapped=True,
        )

    native_review_rejection = find_native_review_contract_error(error)
    if (
        native_review_rejection is not None
        and is_retryable_native_review_response_error(native_review_rejection)
    ):
        return ClassifiedFailure(
            failure_class=_TRANSIENT,
            diagnostic_code="NATIVE-REVIEW-FORM",
            exception_type=type(native_review_rejection).__name__,
            detail=f"{type(error).__name__}: {error}",
            cause_depth=chain.index(native_review_rejection),
            explicitly_mapped=True,
        )
    native_implementer_rejection = find_native_implementer_contract_error(error)
    if (
        native_implementer_rejection is not None
        and is_retryable_native_implementer_response_error(
            native_implementer_rejection
        )
    ):
        return ClassifiedFailure(
            failure_class=_TRANSIENT,
            diagnostic_code="NATIVE-IMPLEMENTER-FORM",
            exception_type=type(native_implementer_rejection).__name__,
            detail=f"{type(error).__name__}: {error}",
            cause_depth=chain.index(native_implementer_rejection),
            explicitly_mapped=True,
        )

    for depth in range(len(chain) - 1, -1, -1):
        candidate = chain[depth]
        assignment = ERROR_CLASSIFICATIONS.get(type(candidate))
        if assignment is None:
            continue
        failure_class, diagnostic_code = assignment
        return ClassifiedFailure(
            failure_class=failure_class,
            diagnostic_code=diagnostic_code,
            exception_type=type(candidate).__name__,
            detail=f"{type(error).__name__}: {error}",
            cause_depth=depth,
            explicitly_mapped=True,
        )

    return ClassifiedFailure(
        failure_class=_HALT,
        diagnostic_code="UNCLASSIFIED-ERROR",
        exception_type=type(error).__name__,
        detail=f"{type(error).__name__}: {error}",
        cause_depth=0,
        explicitly_mapped=False,
    )


def enforce_record_start_boundary(
    failure: ClassifiedFailure, *, records_written: bool
) -> ClassifiedFailure:
    """Prevent a terminal input rejection after a record chain has started."""

    if (
        records_written
        and failure.failure_class is FailureClass.TERMINAL_REJECTION
        and failure.diagnostic_code not in _RECORD_BACKED_TERMINAL_REJECTIONS
    ):
        return replace(
            failure,
            failure_class=FailureClass.RESUMABLE_HALT,
            diagnostic_code=f"{failure.diagnostic_code}-AFTER-RECORD-START",
            promoted_after_record_start=True,
        )
    return failure
