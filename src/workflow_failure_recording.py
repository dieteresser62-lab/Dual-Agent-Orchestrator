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

from agent_runtime import AgentInvocationError
from artifact_models import (
    InvocationFailurePayload,
    Role,
    provider_text_evidence,
    technical_text_evidence,
)
from contracts import AgentRole
from workflow_state import (
    AgentFailureKind,
    InvocationFailureRecord,
    WorkflowState,
    WorkUnitKind,
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
    ) -> tuple[WorkflowState, InvocationFailureRecord]:
        unit = state.current_work_unit
        if error.agent_key != role.value:
            raise self._dependencies.execution_error(
                "agent failure role differs from the required workflow role"
            )
        # S1 is the sole authority for the operational class. Keep this import
        # local because that inventory imports workflow's typed exceptions.
        from error_classification import FailureClass, classify_exception

        classified = classify_exception(error)
        fingerprint = self._dependencies.current_invocation_fingerprint(state)
        key = (
            f"{state.run_id}:{unit.work_unit_id}:{state.current_step.value}:"
            f"{role.value}"
        )
        matching_failures = tuple(
            item
            for item in unit.invocation_failures
            if item.idempotency_key == key
            and item.diff_fingerprint == fingerprint
        )
        prior_auto_resumes = sum(
            item.failure_kind is error.kind and item.automatic_resume
            for item in matching_failures
        )
        quota_policy = context.quota_wait_policy
        transient_policy = context.transient_retry_policy
        reset_at = error.quota_reset.reset_at_utc if error.quota_reset else None
        now_value = self._dependencies.now()
        if now_value.tzinfo is None or now_value.utcoffset() is None:
            raise self._dependencies.execution_error(
                "quota clock must return a timezone-aware datetime"
            )
        now_utc = now_value.astimezone(timezone.utc)
        quota_resume_at = (
            reset_at + timedelta(seconds=quota_policy.safety_margin_seconds)
            if reset_at is not None
            else None
        )
        reset_delay_seconds = (
            max(0.0, (reset_at - now_utc).total_seconds())
            if reset_at is not None
            else None
        )
        automatic_quota = (
            error.kind is AgentFailureKind.QUOTA
            and quota_policy.automatic
            and reset_at is not None
            and (unit.kind is WorkUnitKind.PLAN or fingerprint is not None)
            and reset_delay_seconds is not None
            and reset_delay_seconds <= quota_policy.maximum_wait_seconds
            and prior_auto_resumes < quota_policy.maximum_auto_resumes
        )
        automatic_review_form = (
            is_native_review_output_retry(
                error.kind, role.value, state.current_step
            )
            and classified.diagnostic_code == "NATIVE-REVIEW-FORM"
        )
        retryable_transient = error.kind in {
            AgentFailureKind.NETWORK, AgentFailureKind.TIMEOUT
        } or automatic_review_form
        automatic_transient = (
            retryable_transient
            and transient_policy.automatic
            and (unit.kind is WorkUnitKind.PLAN or fingerprint is not None)
            and prior_auto_resumes < transient_policy.maximum_auto_resumes
        )
        transient_delay = min(
            transient_policy.maximum_delay_seconds,
            transient_policy.initial_delay_seconds * (2 ** prior_auto_resumes),
        )
        resume_at = (
            quota_resume_at
            if error.kind is AgentFailureKind.QUOTA
            else now_utc + timedelta(seconds=transient_delay)
            if automatic_transient
            else None
        )
        automatic = automatic_quota or automatic_transient
        prior_continuations = sum(item.automatic_resume for item in matching_failures)
        provider_marker, provider_digest, provider_bytes = provider_text_evidence(
            error.provider_text
        )
        technical_marker, technical_digest, technical_bytes = (
            technical_text_evidence(error.technical_text)
        )
        orchestrator_diagnostic = error.readable_orchestrator_diagnostic
        received_at = error.received_at.astimezone(timezone.utc).isoformat()
        decision_at = now_utc.isoformat()
        safety_margin_seconds = (
            quota_policy.safety_margin_seconds if reset_at is not None else 0
        )
        retry_delay_seconds = (
            safety_margin_seconds
            if error.kind is AgentFailureKind.QUOTA and reset_at is not None
            else transient_delay
            if automatic_transient
            else 0
        )
        record = InvocationFailureRecord(
            invocation_id=error.invocation_id,
            idempotency_key=key,
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
            reset_at_utc=reset_at.isoformat() if reset_at is not None else None,
            resume_at_utc=resume_at.isoformat() if resume_at is not None else None,
            safety_margin_seconds=safety_margin_seconds,
            auto_resume_count=prior_continuations + (1 if automatic else 0),
            automatic_resume=automatic,
            diff_fingerprint=fingerprint,
        )
        effective_failure_class = FailureClass.TRANSIENT if automatic else (
            FailureClass.RESUMABLE_HALT
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
            orchestrator_diagnostic=orchestrator_diagnostic,
        )
        # Decision-ahead authority boundary: the append must complete before
        # workflow status/counters, waits, or provider restarts can change.
        self._dependencies.persist_invocation_failure(payload)
        state = state.record_invocation_failure(
            record, wait_automatically=automatic, updated_at=decision_at
        )
        logger.info(
            "provider invocation terminal role=%s operation=%s physical_attempt=%d "
            "status=failed failure_kind=%s process_exit_code=%s retry=%s "
            "attempts_exhausted=%s diagnostic_code=%s orchestrator_diagnostic=%s",
            role.value,
            state.current_step.value,
            len(matching_failures) + 1,
            error.kind.value,
            (
                str(error.process_exit_code)
                if error.process_exit_code is not None
                else "none"
            ),
            "scheduled" if automatic else "halted",
            str(len(matching_failures) + 1)
            if retryable_transient and transient_policy.automatic
            and prior_auto_resumes >= transient_policy.maximum_auto_resumes
            else "none",
            classified.diagnostic_code,
            payload.orchestrator_diagnostic or "redacted",
        )
        self._dependencies.checkpoint(state, history)
        return state, record
