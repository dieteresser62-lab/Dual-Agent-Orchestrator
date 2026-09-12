"""Change-boundary validation and validation-attestation orchestration.

The workflow engine is the sole importer and composition root.  This module
owns only the validation-evidence leaf decisions; it has no reverse import of
``workflow`` and does not own run control.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from audit_trail import ValidationAuditEvent
from contracts import ValidationAttestation
from finding_cleanup import is_finding_cleanup_work_unit
from gates import matches_path_patterns
from validation_matrix import (
    ValidationMatrixError,
    select_validation_request,
    validation_attestation_id,
)
from workflow_state import GateReason, WorkflowState, WorkUnitKind


ErrorType = type[RuntimeError]


def validate_change_boundary(
    state: WorkflowState,
    changes: Any,
    kind: WorkUnitKind,
    *,
    context: Any | None = None,
    execution_error: ErrorType,
) -> tuple[str, ...]:
    """Return out-of-scope paths without introducing a stateful boundary."""
    expected_start = state.current_slice.start_commit or state.branch_base
    if kind is WorkUnitKind.FINAL_REVIEW:
        if changes.start_commit != state.branch_base:
            raise execution_error(
                "branch final review must use the persisted branch base"
            )
        return ()
    if is_finding_cleanup_work_unit(state):
        if changes.start_commit != state.branch_base:
            raise execution_error(
                "finding cleanup review must use the persisted branch base"
            )
        scope = () if context is None else context.current_scope_paths
        if not scope:
            raise execution_error(
                "finding cleanup review requires a finding-derived path boundary"
            )
        return tuple(path for path in changes.paths if path not in scope)
    if changes.start_commit != expected_start:
        raise execution_error("change evidence uses a foreign slice start commit")
    if kind is WorkUnitKind.PLAN:
        scope_patterns = (
            context.task_scope_patterns
            if context is not None
            else state.task_scope_patterns
        )
        if not scope_patterns:
            return ()
        if context is not None and changes.paths == (
            ".orchestrator/plan-output.md",
        ):
            # The driver renders this virtual path only when the planner
            # returned a result without changing the repository. PLAN_ONLY
            # must let its validator diagnose the missing artifact.
            return ()
        unexpected = tuple(
            path
            for path in changes.paths
            if not matches_path_patterns(path, scope_patterns)
        )
        if unexpected and any(
            state.current_work_unit.has_gate_approval(
                reason,
                changes.fingerprint,
                unexpected,
            )
            for reason in (
                GateReason.UNEXPECTED_FILE,
                GateReason.QUOTA_RESUME_DIFF,
            )
        ):
            return ()
        return unexpected
    scope = state.current_slice.scope_paths
    if not scope:
        raise execution_error("slice review requires a persisted Git boundary")
    unexpected = tuple(path for path in changes.paths if path not in scope)
    if unexpected:
        if state.current_work_unit.has_gate_approval(
            GateReason.UNEXPECTED_FILE,
            changes.fingerprint,
            unexpected,
        ):
            return ()
        if state.current_work_unit.has_gate_approval(
            GateReason.QUOTA_RESUME_DIFF,
            changes.fingerprint,
            unexpected,
        ):
            return ()
    return unexpected


@dataclass(frozen=True)
class WorkflowValidationEvidenceDependencies:
    """Exact engine/driver edges used by attestation orchestration."""

    validate_plan: Callable[..., ValidationAttestation]
    persist_validation_attestation: Callable[[ValidationAttestation], None]
    persist_validation_request: Callable[[object], None]
    recover_pending_validation_attestation: Callable[..., ValidationAttestation | None]
    validate: Callable[[Any, object], ValidationAttestation]
    retried_failed_validation_fingerprints: set[str]
    execution_error: ErrorType
    validation_execution_error: ErrorType


class WorkflowValidationEvidence:
    """Select, recover, validate, and durably record one attestation."""

    def __init__(self, dependencies: WorkflowValidationEvidenceDependencies) -> None:
        self._dependencies = dependencies

    def attestation(
        self,
        changes: Any,
        history: Any,
        context: Any,
        slice_id: int,
        *,
        plan_contract: bool = False,
    ) -> tuple[ValidationAttestation, Any]:
        if plan_contract:
            matching = tuple(
                existing
                for existing in history.attestations
                if existing.diff_fingerprint == changes.fingerprint
            )
            if matching:
                return matching[-1], history
            attestation = self._dependencies.validate_plan(
                changes,
                work_plan_path=context.work_plan_path,
                scope_patterns=context.task_scope_patterns,
                plan_only=context.plan_only,
            )
            if attestation.diff_fingerprint != changes.fingerprint:
                raise self._dependencies.execution_error(
                    "plan validation attestation fingerprint is foreign"
                )
            self._dependencies.persist_validation_attestation(attestation)
            event = ValidationAuditEvent(
                event_id=len(history.events) + 1,
                slice_id=slice_id,
                attestation=attestation,
            )
            return attestation, replace(
                history,
                attestations=(*history.attestations, attestation),
                events=(*history.events, event),
            )
        try:
            request = select_validation_request(
                context.validation_matrix,
                diff_fingerprint=changes.fingerprint,
                changed_paths=changes.user_gate_paths,
                findings=history.findings,
            )
        except ValidationMatrixError as exc:
            raise self._dependencies.validation_execution_error(str(exc)) from exc
        matching = tuple(
            existing
            for existing in history.attestations
            if existing.diff_fingerprint == changes.fingerprint
        )
        if matching:
            existing = matching[-1]
            retry_incomplete = (
                not existing.complete and context.retry_incomplete_validation
            )
            retry_failed = (
                existing.complete
                and not existing.passed
                and context.retry_failed_validation
                and changes.fingerprint
                not in self._dependencies.retried_failed_validation_fingerprints
            )
            if not retry_incomplete and not retry_failed:
                if not set(request.expected_commands).issubset(
                    existing.expected_commands
                ):
                    raise self._dependencies.execution_error(
                        "validation requirements changed without a new diff fingerprint"
                    )
                return existing, history
            if retry_failed:
                self._dependencies.retried_failed_validation_fingerprints.add(
                    changes.fingerprint
                )
            request = replace(request, attempt_number=len(matching) + 1)
        self._dependencies.persist_validation_request(request)
        attestation = self._dependencies.recover_pending_validation_attestation(
            changes.fingerprint,
            request.expected_commands,
            validation_attestation_id(request),
        )
        if attestation is None:
            attestation = self._dependencies.validate(changes, request)
        expected_attestation_id = validation_attestation_id(request)
        if attestation.attestation_id != expected_attestation_id:
            raise self._dependencies.execution_error(
                "validation attestation id does not match the selected attempt"
            )
        if attestation.diff_fingerprint != changes.fingerprint:
            raise self._dependencies.execution_error(
                "validation attestation fingerprint is foreign"
            )
        if attestation.expected_commands != request.expected_commands:
            raise self._dependencies.execution_error(
                "validation attestation does not cover the selected matrix"
            )
        if any(
            item.attestation_id == attestation.attestation_id
            for item in history.attestations
        ):
            raise self._dependencies.execution_error(
                "validation attestation id was reused"
            )
        self._dependencies.persist_validation_attestation(attestation)
        event = ValidationAuditEvent(
            event_id=len(history.events) + 1,
            slice_id=slice_id,
            attestation=attestation,
        )
        return attestation, replace(
            history,
            attestations=(*history.attestations, attestation),
            events=(*history.events, event),
        )
