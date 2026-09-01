"""Central, typed classification for failures that reach the Watch boundary.

The workflow has exactly three operational failure classes.  Project exception
types are listed explicitly here so a newly introduced type cannot silently
inherit retry behaviour.  Explicit exception causes are followed to preserve
the class of errors wrapped by workflow persistence boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from agent_adapters import AgentBudgetError, AgentOutputError, AgentPermissionError
from agent_config import AgentConfigError
from agent_runtime import (
    AgentCompatibilityError,
    AgentInvocationError,
    AgentProcessError,
    QuotaReachedError,
)
from artifact_bridge import ArtifactBridgeError
from artifact_migration import ArtifactResumeError
from artifact_models import ArtifactValidationError
from artifact_projection import ArtifactProjectionError
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
)
from native_codex_request import (  # allowlist:provider -- typed module boundary
    NativeCodexRequestError as NativeImplementerRequestError,  # allowlist:provider
)
from native_provider_schema import NativeProviderSchemaError
from native_review_contract import NativeReviewContractError
from native_review_request import NativeReviewRequestError
from path_policy import PathPolicyError
from plan_handoff import PlanHandoffError
from provider_input_budget import ProviderInputBudgetError, ProviderInputBudgetExceeded
from provider_input_efficiency import ProviderInputEfficiencyError
from repo_changes import NotGitRepositoryError, RepositoryChangeError
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
    ValidationExecutionError,
    WorkflowCommitApprovalRequired,
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


# Authoritative inventory: all 47 ``*Error`` classes currently defined in
# ``src/`` plus the schema validator's typed ``SchemaMismatch`` exception and
# the four project exceptions whose names do not end in ``Error``.  Subclasses
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
    ArtifactBridgeError: _entry(_HALT, "ARTIFACT-BRIDGE"),
    ArtifactValidationError: _entry(_HALT, "ARTIFACT-VALIDATION"),
    ArtifactResumeError: _entry(_HALT, "ARTIFACT-RESUME"),
    ArtifactReplayError: _entry(_HALT, "ARTIFACT-REPLAY"),
    ArtifactProjectionError: _entry(_HALT, "ARTIFACT-PROJECTION"),
    ArtifactStoreError: _entry(_HALT, "ARTIFACT-STORE"),
    ArtifactConflictError: _entry(_HALT, "ARTIFACT-CONFLICT"),
    ArtifactCorruptionError: _entry(_HALT, "ARTIFACT-CORRUPTION"),
    AuditTrailError: _entry(_HALT, "AUDIT-TRAIL"),
    ConfigError: _entry(_HALT, "CLI-CONFIG"),
    DryRunScenarioError: _entry(_REJECT, "DRY-RUN-SCENARIO"),
    ScriptedInterruption: _entry(_HALT, "SCRIPTED-INTERRUPTION"),
    FinalReviewPreflightDenied: _entry(_HALT, "FINAL-REVIEW-PREFLIGHT-DENIED"),
    GitTransactionError: _entry(_HALT, "GIT-TRANSACTION"),
    NativeImplementerContractError: _entry(_HALT, "NATIVE-IMPLEMENTER-CONTRACT"),
    NativeImplementerRequestError: _entry(_HALT, "NATIVE-IMPLEMENTER-REQUEST"),
    NativeProviderSchemaError: _entry(_HALT, "NATIVE-PROVIDER-SCHEMA"),
    NativeReviewContractError: _entry(_HALT, "NATIVE-REVIEW-CONTRACT"),
    NativeReviewRequestError: _entry(_HALT, "NATIVE-REVIEW-REQUEST"),
    PathPolicyError: _entry(_HALT, "PATH-POLICY"),
    PlanHandoffError: _entry(_HALT, "PLAN-HANDOFF"),
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
    StateSchemaError: _entry(_HALT, "STATE-SCHEMA"),
    UnknownStateVersionError: _entry(_HALT, "UNKNOWN-STATE-VERSION"),
    ActiveV2StateError: _entry(_HALT, "ACTIVE-V2-STATE"),
    StatePathError: _entry(_HALT, "STATE-PATH"),
    TaskContractError: _entry(_REJECT, "TASK-CONTRACT"),
    ValidationMatrixError: _entry(_HALT, "VALIDATION-MATRIX"),
    WorkflowStateValidationError: _entry(_HALT, "WORKFLOW-STATE-VALIDATION"),
    WorkflowExecutionError: _entry(_HALT, "WORKFLOW-EXECUTION"),
    WorkflowDriverContractError: _entry(_HALT, "WORKFLOW-DRIVER-CONTRACT"),
    WorkflowCommitApprovalRequired: _entry(_HALT, "WORKFLOW-COMMIT-APPROVAL"),
    NoWorkflowChangesError: _entry(_HALT, "NO-WORKFLOW-CHANGES"),
    WorkflowContractError: _entry(_HALT, "WORKFLOW-CONTRACT"),
    ValidationExecutionError: _entry(_HALT, "VALIDATION-EXECUTION"),
}


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
    ):
        return replace(
            failure,
            failure_class=FailureClass.RESUMABLE_HALT,
            diagnostic_code=f"{failure.diagnostic_code}-AFTER-RECORD-START",
            promoted_after_record_start=True,
        )
    return failure
