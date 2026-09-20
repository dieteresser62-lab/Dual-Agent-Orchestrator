"""Durable provider-failure recording before any retry state transition.

The workflow engine is the sole importer and composition root.  This leaf
owns the R6/B22 failure record and its decision-ahead ordering, but no wait or
provider restart behavior.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from agent_runtime import (
    AgentInvocationError,
    is_structured_output_retry_exhaustion,
    normalize_provider_usage,
)
from artifact_models import (
    InvocationFailurePayload,
    Role,
    provider_text_evidence,
    technical_text_evidence,
)
from contracts import AgentRole
from orchestrator_diagnostics import (
    STRUCTURED_OUTPUT_DIAGNOSTIC_CODE,
    STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE,
)
from workflow_state import (
    AgentFailureKind,
    InvocationFailureRecord,
    WorkflowState,
    WorkUnitKind,
    is_native_implementer_output_retry,
    is_native_review_output_retry,
)


logger = logging.getLogger("workflow")
ErrorType = type[RuntimeError]


@dataclass(frozen=True)
class WorkflowFailureRecordingDependencies:
    """Exact engine/driver edges required to record a failed invocation."""

    current_invocation_fingerprint: Callable[[WorkflowState], str | None]
    persist_invocation_failure: Callable[[InvocationFailurePayload], None]
    checkpoint: Callable[[WorkflowState, Any], None]
    now: Callable[[], datetime]
    execution_error: ErrorType


def _log_invocation_failure(
    *,
    role: AgentRole,
    operation: str,
    physical_attempt: int,
    error: AgentInvocationError,
    automatic: bool,
    retryable_transient: bool,
    transient_automatic: bool,
    prior_auto_resumes: int,
    maximum_auto_resumes: int,
    diagnostic_code: str,
    provider_subtype: str,
    orchestrator_diagnostic: str | None,
    native_review_rejection: str | None,
    native_implementer_rejection: str | None,
) -> None:
    if native_implementer_rejection is None:
        logger.info(
            "provider invocation terminal role=%s operation=%s physical_attempt=%d "
            "status=failed failure_kind=%s process_exit_code=%s retry=%s "
            "attempts_exhausted=%s diagnostic_code=%s provider_subtype=%s "
            "orchestrator_diagnostic=%s native_review_rejection=%s",
            role.value,
            operation,
            physical_attempt,
            error.kind.value,
            (
                str(error.process_exit_code)
                if error.process_exit_code is not None
                else "none"
            ),
            "scheduled" if automatic else "halted",
            str(physical_attempt)
            if retryable_transient
            and transient_automatic
            and prior_auto_resumes >= maximum_auto_resumes
            else "none",
            diagnostic_code,
            provider_subtype,
            orchestrator_diagnostic or "none",
            native_review_rejection or "none",
        )
        return
    logger.info(
        "provider invocation terminal role=%s operation=%s physical_attempt=%d "
        "status=failed failure_kind=%s process_exit_code=%s retry=%s "
        "attempts_exhausted=%s diagnostic_code=%s provider_subtype=%s "
        "orchestrator_diagnostic=%s native_review_rejection=%s "
        "native_implementer_rejection=%s",
        role.value,
        operation,
        physical_attempt,
        error.kind.value,
        str(error.process_exit_code) if error.process_exit_code is not None else "none",
        "scheduled" if automatic else "halted",
        str(physical_attempt)
        if retryable_transient
        and transient_automatic
        and prior_auto_resumes >= maximum_auto_resumes
        else "none",
        diagnostic_code,
        provider_subtype,
        orchestrator_diagnostic or "none",
        native_review_rejection or "none",
        native_implementer_rejection or "none",
    )


def _invocation_failure_key(
    state: WorkflowState,
    role: AgentRole,
    disposition_limit_failure: bool,
) -> str:
    key = (
        f"{state.run_id}:{state.current_work_unit_id}:{state.current_step.value}:"
        f"{role.value}"
    )
    return key + ":disposition-limit" if disposition_limit_failure else key


def _quota_attempt_did_work(error: AgentInvocationError) -> bool:
    """Use only the provider's normalized per-attempt usage as work evidence."""

    usage = normalize_provider_usage(error.provider_data)
    if usage is None:
        return False
    return any(
        value is not None and value > 0
        for value in (
            usage.input_tokens,
            usage.tool_input_tokens,
            usage.cache_read_input_tokens,
            usage.cache_creation_input_tokens,
            usage.thinking_tokens,
            usage.output_tokens,
            usage.total_tokens,
            usage.turns,
            usage.cost_usd,
        )
    )


def _quota_resume_decision(
    *,
    error: AgentInvocationError,
    unit_kind: WorkUnitKind,
    fingerprint: str | None,
    matching_failures: tuple[InvocationFailureRecord, ...],
    prior_auto_resumes: int,
    quota_policy: Any,
    now_utc: datetime,
) -> tuple[datetime | None, datetime | None, bool]:
    """Return reset, resume time, and whether progress authorizes continuation."""

    reset_at = error.quota_reset.reset_at_utc if error.quota_reset else None
    quota_resume_at = (
        reset_at + timedelta(seconds=quota_policy.safety_margin_seconds)
        if reset_at is not None
        else None
    )
    prior_automatic = tuple(
        item
        for item in matching_failures
        if item.failure_kind is AgentFailureKind.QUOTA and item.automatic_resume
    )
    previous_reset = (
        datetime.fromisoformat(
            prior_automatic[-1].reset_at_utc.replace("Z", "+00:00")
        )
        if prior_automatic and prior_automatic[-1].reset_at_utc is not None
        else None
    )
    progress = (
        not prior_automatic
        or _quota_attempt_did_work(error)
        or (
            reset_at is not None
            and previous_reset is not None
            and reset_at > previous_reset
        )
    )
    reset_delay = (
        max(0.0, (reset_at - now_utc).total_seconds())
        if reset_at is not None
        else None
    )
    automatic = (
        error.kind is AgentFailureKind.QUOTA
        and quota_policy.automatic
        and reset_at is not None
        and (unit_kind is WorkUnitKind.PLAN or fingerprint is not None)
        and reset_delay is not None
        and reset_delay <= quota_policy.maximum_wait_seconds
        and progress
        and prior_auto_resumes < quota_policy.maximum_auto_resumes
    )
    return reset_at, quota_resume_at, automatic


@dataclass(frozen=True)
class _NativeResponseFailureDiagnostics:
    orchestrator: str | None
    readable_rejection: str | None
    persisted_rejection: str | None
    provider_subtype: str
    implementer_readable_rejection: str | None = None
    implementer_persisted_rejection: str | None = None


def _native_response_failure_diagnostics(
    error: AgentInvocationError,
    review_retry_with_feedback: bool,
    implementer_retry_with_feedback: bool,
    diagnostic_code: str,
) -> _NativeResponseFailureDiagnostics:
    persisted_rejection = (
        error.native_review_rejection.value
        if review_retry_with_feedback and error.native_review_rejection is not None
        else None
    )
    implementer_persisted_rejection = (
        error.native_implementer_rejection.value
        if implementer_retry_with_feedback
        and error.native_implementer_rejection is not None
        else None
    )
    provider_subtype = (
        STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE
        if diagnostic_code == STRUCTURED_OUTPUT_DIAGNOSTIC_CODE
        else "none"
    )
    return _NativeResponseFailureDiagnostics(
        orchestrator=error.readable_orchestrator_diagnostic,
        readable_rejection=error.readable_native_review_rejection,
        persisted_rejection=persisted_rejection,
        provider_subtype=provider_subtype,
        implementer_readable_rejection=error.readable_native_implementer_rejection,
        implementer_persisted_rejection=implementer_persisted_rejection,
    )


def _native_response_retryability(
    error: AgentInvocationError,
    role: AgentRole,
    step: Any,
    diagnostic_code: str,
) -> tuple[bool, bool, bool]:
    native_review = is_native_review_output_retry(error.kind, role.value, step)
    native_implementer = is_native_implementer_output_retry(
        error.kind, role.value, step
    )
    return (
        native_review and error.native_review_response_retryable,
        native_implementer and error.native_implementer_response_retryable,
        native_review
        and is_structured_output_retry_exhaustion(error.provider_data)
        and diagnostic_code == STRUCTURED_OUTPUT_DIAGNOSTIC_CODE,
    )


@dataclass(frozen=True)
class _InvocationRetryDecision:
    key: str
    matching_failures: tuple[InvocationFailureRecord, ...]
    prior_auto_resumes: int
    quota_policy: Any
    transient_policy: Any
    now_utc: datetime
    reset_at: datetime | None
    resume_at: datetime | None
    automatic_quota: bool
    automatic_transient: bool
    automatic: bool
    retryable_transient: bool
    transient_delay: float
    response_diagnostics: _NativeResponseFailureDiagnostics
    native_review_retry_round: int | None
    native_implementer_retry_round: int | None


def _invocation_retry_decision(
    *,
    state: WorkflowState,
    context: Any,
    role: AgentRole,
    error: AgentInvocationError,
    disposition_limit_failure: bool,
    native_response_retry_allowed: bool,
    fingerprint: str | None,
    now_utc: datetime,
    diagnostic_code: str,
) -> _InvocationRetryDecision:
    unit = state.current_work_unit
    key = _invocation_failure_key(state, role, disposition_limit_failure)
    matching_failures = tuple(
        item
        for item in unit.invocation_failures
        if item.idempotency_key == key and item.diff_fingerprint == fingerprint
    )
    prior_auto_resumes = sum(
        item.failure_kind is error.kind and item.automatic_resume
        for item in matching_failures
    )
    quota_policy = context.quota_wait_policy
    transient_policy = context.transient_retry_policy
    reset_at, quota_resume_at, automatic_quota = _quota_resume_decision(
        error=error,
        unit_kind=unit.kind,
        fingerprint=fingerprint,
        matching_failures=matching_failures,
        prior_auto_resumes=prior_auto_resumes,
        quota_policy=quota_policy,
        now_utc=now_utc,
    )
    (
        automatic_review_feedback,
        automatic_implementer_feedback,
        automatic_structured_output,
    ) = _native_response_retryability(
        error, role, state.current_step, diagnostic_code
    )
    if not native_response_retry_allowed:
        automatic_review_feedback = False
        automatic_implementer_feedback = False
    retryable_transient = (
        error.kind in {AgentFailureKind.NETWORK, AgentFailureKind.TIMEOUT}
        or automatic_review_feedback
        or automatic_implementer_feedback
        or automatic_structured_output
    )
    automatic_transient = (
        disposition_limit_failure and fingerprint is not None
    ) or (
        retryable_transient
        and transient_policy.automatic
        and (unit.kind is WorkUnitKind.PLAN or fingerprint is not None)
        and prior_auto_resumes < transient_policy.maximum_auto_resumes
    )
    transient_delay = min(
        transient_policy.maximum_delay_seconds,
        transient_policy.initial_delay_seconds * (2**prior_auto_resumes),
    )
    resume_at = (
        quota_resume_at
        if error.kind is AgentFailureKind.QUOTA
        else now_utc + timedelta(seconds=transient_delay)
        if automatic_transient
        else None
    )
    response_diagnostics = _native_response_failure_diagnostics(
        error,
        automatic_review_feedback,
        automatic_implementer_feedback,
        diagnostic_code,
    )
    next_request_sequence = state.current_work_unit.request_sequence + 1
    return _InvocationRetryDecision(
        key=key,
        matching_failures=matching_failures,
        prior_auto_resumes=prior_auto_resumes,
        quota_policy=quota_policy,
        transient_policy=transient_policy,
        now_utc=now_utc,
        reset_at=reset_at,
        resume_at=resume_at,
        automatic_quota=automatic_quota,
        automatic_transient=automatic_transient,
        automatic=automatic_quota or automatic_transient,
        retryable_transient=retryable_transient,
        transient_delay=transient_delay,
        response_diagnostics=response_diagnostics,
        native_review_retry_round=(
            next_request_sequence
            if response_diagnostics.persisted_rejection is not None
            else None
        ),
        native_implementer_retry_round=(
            next_request_sequence
            if response_diagnostics.implementer_persisted_rejection is not None
            else None
        ),
    )


def _invocation_failure_documents(
    *,
    state: WorkflowState,
    role: AgentRole,
    error: AgentInvocationError,
    classified: Any,
    fingerprint: str | None,
    decision: _InvocationRetryDecision,
) -> tuple[InvocationFailureRecord, InvocationFailurePayload, str]:
    # Kept local to preserve the workflow/error-classification import boundary.
    from error_classification import FailureClass

    provider_marker, provider_digest, provider_bytes = provider_text_evidence(
        error.provider_text
    )
    technical_marker, technical_digest, technical_bytes = technical_text_evidence(
        error.technical_text
    )
    diagnostics = decision.response_diagnostics
    safety_margin_seconds = (
        decision.quota_policy.safety_margin_seconds
        if decision.reset_at is not None
        else 0
    )
    retry_delay_seconds = (
        safety_margin_seconds
        if error.kind is AgentFailureKind.QUOTA and decision.reset_at is not None
        else decision.transient_delay
        if decision.automatic_transient
        else 0
    )
    received_at = error.received_at.astimezone(timezone.utc).isoformat()
    decision_at = decision.now_utc.isoformat()
    record = InvocationFailureRecord(
        invocation_id=error.invocation_id,
        idempotency_key=decision.key,
        role=role.value,
        failure_kind=error.kind,
        provider_text=provider_marker,
        received_at=received_at,
        step=state.current_step,
        slice_id=state.current_slice_id,
        work_unit_id=state.current_work_unit_id,
        diagnostic_exit_code=(2 if error.kind is AgentFailureKind.QUOTA else 3),
        process_exit_code=error.process_exit_code,
        technical_text=technical_marker,
        parse_path=(error.quota_reset.parse_path if error.quota_reset else None),
        source_timezone=(
            error.quota_reset.source_timezone if error.quota_reset else None
        ),
        reset_at_utc=(
            decision.reset_at.isoformat() if decision.reset_at is not None else None
        ),
        resume_at_utc=(
            decision.resume_at.isoformat() if decision.resume_at is not None else None
        ),
        safety_margin_seconds=safety_margin_seconds,
        auto_resume_count=(
            sum(item.automatic_resume for item in decision.matching_failures)
            + (1 if decision.automatic else 0)
        ),
        automatic_resume=decision.automatic,
        diff_fingerprint=fingerprint,
        orchestrator_diagnostic=(
            diagnostics.orchestrator
            if diagnostics.implementer_persisted_rejection is not None
            else None
        ),
        native_review_rejection=diagnostics.persisted_rejection,
        native_review_retry_round=decision.native_review_retry_round,
        native_implementer_rejection=diagnostics.implementer_persisted_rejection,
        native_implementer_retry_round=decision.native_implementer_retry_round,
    )
    quota_terminal_verdict = (
        error.kind is AgentFailureKind.QUOTA
        and not decision.automatic_quota
        and (
            state.current_work_unit.kind is WorkUnitKind.PLAN
            or fingerprint is not None
        )
    )
    effective_failure_class = (
        FailureClass.TRANSIENT
        if decision.automatic
        else FailureClass.TERMINAL_REJECTION
        if quota_terminal_verdict
        else FailureClass.RESUMABLE_HALT
        if classified.failure_class is FailureClass.TRANSIENT
        else classified.failure_class
    )
    payload = InvocationFailurePayload(
        invocation_id=record.invocation_id,
        idempotency_key=record.idempotency_key,
        role=Role(role.value),
        failure_kind=record.failure_kind.value,
        failure_class=effective_failure_class.value,
        diagnostic_code=classified.diagnostic_code,
        provider_text=provider_marker,
        provider_text_sha256=provider_digest,
        provider_text_bytes=provider_bytes,
        technical_text=technical_marker,
        technical_text_sha256=technical_digest,
        technical_text_bytes=technical_bytes,
        received_at=record.received_at,
        decision_at_utc=decision_at,
        step=record.step.value,
        slice_id=str(record.slice_id),
        work_unit_id=str(record.work_unit_id),
        diagnostic_exit_code=record.diagnostic_exit_code,
        process_exit_code=record.process_exit_code,
        parse_path=record.parse_path,
        source_timezone=record.source_timezone,
        reset_at_utc=record.reset_at_utc,
        resume_at_utc=record.resume_at_utc,
        safety_margin_seconds=record.safety_margin_seconds,
        retry_delay_seconds=retry_delay_seconds,
        auto_resume_count=record.auto_resume_count,
        automatic_resume=record.automatic_resume,
        diff_fingerprint=record.diff_fingerprint,
        orchestrator_diagnostic=diagnostics.orchestrator,
        native_review_rejection=diagnostics.persisted_rejection,
        native_review_retry_round=decision.native_review_retry_round,
        native_implementer_rejection=diagnostics.implementer_persisted_rejection,
        native_implementer_retry_round=decision.native_implementer_retry_round,
    )
    return record, payload, decision_at


class WorkflowFailureRecording:
    """Build and append failure evidence before changing retry state."""

    def __init__(self, dependencies: WorkflowFailureRecordingDependencies) -> None:
        self._dependencies = dependencies

    def persist_invocation_failure(
        self,
        state: WorkflowState,
        history: Any,
        context: Any,
        role: AgentRole,
        error: AgentInvocationError,
        disposition_limit_failure: bool = False,
        native_response_retry_allowed: bool = True,
    ) -> tuple[WorkflowState, InvocationFailureRecord]:
        if error.agent_key != role.value:
            raise self._dependencies.execution_error(
                "agent failure role differs from the required workflow role"
            )
        # S1 owns this class; its inventory imports workflow's typed exceptions.
        from error_classification import classify_exception
        classified = classify_exception(error)
        fingerprint = self._dependencies.current_invocation_fingerprint(state)
        now_value = self._dependencies.now()
        if now_value.tzinfo is None or now_value.utcoffset() is None:
            raise self._dependencies.execution_error(
                "quota clock must return a timezone-aware datetime"
            )
        decision = _invocation_retry_decision(
            state=state,
            context=context,
            role=role,
            error=error,
            disposition_limit_failure=disposition_limit_failure,
            native_response_retry_allowed=native_response_retry_allowed,
            fingerprint=fingerprint,
            now_utc=now_value.astimezone(timezone.utc),
            diagnostic_code=classified.diagnostic_code,
        )
        record, payload, decision_at = _invocation_failure_documents(
            state=state,
            role=role,
            error=error,
            classified=classified,
            fingerprint=fingerprint,
            decision=decision,
        )
        automatic = decision.automatic
        # Decision-ahead authority boundary: the append must complete before
        # workflow status/counters, waits, or provider restarts can change.
        self._dependencies.persist_invocation_failure(payload)
        state = state.record_invocation_failure(
            record, wait_automatically=automatic, updated_at=decision_at
        )
        _log_invocation_failure(
            role=role,
            operation=state.current_step.value,
            physical_attempt=len(decision.matching_failures) + 1,
            error=error,
            automatic=decision.automatic,
            retryable_transient=decision.retryable_transient,
            transient_automatic=decision.transient_policy.automatic,
            prior_auto_resumes=decision.prior_auto_resumes,
            maximum_auto_resumes=(
                decision.quota_policy.maximum_auto_resumes
                if error.kind is AgentFailureKind.QUOTA
                else decision.transient_policy.maximum_auto_resumes
            ),
            diagnostic_code=classified.diagnostic_code,
            provider_subtype=decision.response_diagnostics.provider_subtype,
            orchestrator_diagnostic=payload.orchestrator_diagnostic,
            native_review_rejection=(
                decision.response_diagnostics.readable_rejection
            ),
            native_implementer_rejection=(
                decision.response_diagnostics.implementer_readable_rejection
            ),
        )
        self._dependencies.checkpoint(state, history)
        return state, record
