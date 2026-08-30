"""Lossless adapters between state-v3 domain objects and artifact records.

The bridge is intentionally a write-through comparator, not a second workflow
engine.  State-v3 remains authoritative until the explicit cutover.  Every
successful write is reloaded from the append-only store and compared with the
typed source object before the caller may act on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
from typing import Callable, Iterable

from artifact_models import (
    AgentResultPayload,
    ArtifactPayload,
    ArtifactRecord,
    BindingPayload,
    CommandSpec,
    DiagnosticPayload,
    Fingerprint,
    FingerprintKind,
    FindingSeverity,
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    ImportedFindingTransition,
    FindingTransitionPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ProviderAttemptPayload,
    ProviderUsagePayload,
    GatePayload,
    PlanPayload,
    ReviewPayload,
    ReviewEvidencePayload,
    Role,
    SliceSpec,
    TaskPayload,
    ValidationAttestationPayload,
    ValidationRequestPayload,
    ValidationResult,
    WorkUnitPayload,
    canonical_json,
    finding_transition_sequence_sha256,
)
from artifact_store import ArtifactStore
from contracts import (
    AgentRole,
    CodexContractResult,
    ContractResult,
    FindingRecord,
    FindingResponseDecision,
    PlannedSlice,
    ValidationAttestation,
    ValidationCommandSpec,
)
from task_contract import TaskContract
from validation_matrix import ValidationRequest
from provider_input_budget import ProviderInputMeasurement
from artifact_replay import ArtifactReplayResult, replay_artifacts


class ArtifactBridgeError(RuntimeError):
    """Raised when structured and state-v3 meanings are not identical."""


logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _role(role: AgentRole) -> Role:
    return Role(role.value)


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def command_payload(spec: ValidationCommandSpec) -> CommandSpec:
    """Map a command without parsing its presentation string."""
    if spec.argv:
        return CommandSpec(family="validation", argv=spec.argv)
    assert spec.legacy_shell is not None
    return CommandSpec(
        family="legacy-validation",
        argv=(spec.legacy_shell,),
        mode="legacy_shell",
    )


def task_payload(contract: TaskContract) -> TaskPayload:
    return TaskPayload(
        target_branch=contract.target_branch,
        scope_paths=contract.scope_patterns,
        assignment_sha256=contract.digest,
    )


def plan_payload(
    *, work_plan_path: str, approved_plan_commit: str, slices: Iterable[PlannedSlice]
) -> PlanPayload:
    return PlanPayload(
        work_plan_path=work_plan_path,
        approved_plan_commit=approved_plan_commit,
        slices=tuple(
            SliceSpec(str(item.slice_id), item.summary, item.scope_paths)
            for item in slices
        ),
    )


def agent_result_payload(
    result: CodexContractResult,
    *,
    role: AgentRole,
    work_unit_id: int | str,
    transport_schema: str,
    request_id: str,
    response_sha256: str,
) -> AgentResultPayload:
    outcome = "stopped" if result.stopped else "ready" if result.ready else "not_ready"
    return AgentResultPayload(
        role=_role(role),
        work_unit_id=str(work_unit_id),
        outcome=outcome,
        test_files=result.test_files,
        transport_schema=transport_schema,
        request_id=request_id,
        response_sha256=response_sha256,
    )


def review_payload(
    result: ContractResult,
    *,
    work_unit_id: int | str,
    transport_schema: str,
    request_id: str,
    response_sha256: str,
) -> ReviewPayload:
    verdict = "stop" if result.stopped else "approved" if result.approval else "denied"
    structured_evidence = (
        None
        if result.evidence is None
        else ReviewEvidencePayload(
            dimensions=result.evidence.dimensions,
            largest_residual_risk=result.evidence.largest_residual_risk,
            break_condition=result.evidence.break_condition,
        )
    )
    return ReviewPayload(
        reviewer=_role(result.reviewer),
        work_unit_id=str(work_unit_id),
        verdict=verdict,
        finding_ids=tuple(item.finding_id for item in result.findings),
        # Schema-2 records written before S4a used this string field.  Keep it
        # readable as opaque legacy data, but never create or split it again.
        evidence=None,
        transport_schema=transport_schema,
        request_id=request_id,
        response_sha256=response_sha256,
        review_evidence=structured_evidence,
        red_state_followup_slice=result.red_state_followup_slice,
    )


def review_payload_matches_result(
    payload: ReviewPayload,
    result: ContractResult,
) -> bool:
    """Compare a review fact without ever parsing the legacy evidence string."""
    verdict = "stop" if result.stopped else "approved" if result.approval else "denied"
    finding_ids = tuple(item.finding_id for item in result.findings)
    if payload.review_evidence is not None:
        evidence_matches = payload.review_evidence == (
            None
            if result.evidence is None
            else ReviewEvidencePayload(
                result.evidence.dimensions,
                result.evidence.largest_residual_risk,
                result.evidence.break_condition,
            )
        ) and payload.evidence is None
    else:
        legacy_evidence = (
            None
            if result.evidence is None
            else " | ".join(
                (
                    result.evidence.dimensions,
                    result.evidence.largest_residual_risk,
                    result.evidence.break_condition,
                )
            )
        )
        evidence_matches = payload.evidence == legacy_evidence
    return (
        payload.verdict == verdict
        and payload.finding_ids == finding_ids
        and evidence_matches
        and payload.red_state_followup_slice == result.red_state_followup_slice
    )


def finding_payload(
    finding: FindingRecord,
    *,
    actor: AgentRole | None = None,
    action: str = "opened",
    rationale: str | None = None,
    work_unit_id: int | str | None = None,
    response_decision: FindingResponseDecision | None = None,
) -> FindingTransitionPayload:
    reporter = _role(finding.origin.reporter)
    structured = work_unit_id is not None
    return FindingTransitionPayload(
        finding_id=finding.finding_id,
        reporter=reporter,
        actor=_role(actor) if actor is not None else reporter,
        action=action,
        severity=FindingSeverity(finding.finding_class.value),
        finding_status=finding.status.value.lower(),
        rationale=rationale or finding.status_rationale or finding.summary,
        work_unit_id=None if work_unit_id is None else str(work_unit_id),
        summary=finding.summary if structured and action == "opened" else None,
        acceptance_test=(
            finding.acceptance_test if structured and action == "opened" else None
        ),
        origin_slice_id=(
            finding.origin.slice_id if structured and action == "opened" else None
        ),
        origin_round_number=(
            finding.origin.round_number if structured and action == "opened" else None
        ),
        response_decision=(
            response_decision.value.lower() if response_decision is not None else None
        ),
    )


def finding_handoff_export_payload(
    replay: ArtifactReplayResult,
    *,
    approved_plan_commit: str,
    approval_review_record_id: str,
    target_task_path: str,
    target_task_bytes: bytes,
) -> FindingHandoffExportPayload:
    """Build an export only from the ordered facts of an accepted replay."""
    if replay.head_record_id is None:
        raise ArtifactBridgeError("finding export requires a non-empty accepted replay")
    review = next(
        (record for record in replay.records if record.record_id == approval_review_record_id),
        None,
    )
    if (
        review is None
        or not isinstance(review.payload, ReviewPayload)
        or review.payload.verdict != "approved"
    ):
        raise ArtifactBridgeError("finding export requires its approved reviewer record")
    plans = [record.payload for record in replay.records if isinstance(record.payload, PlanPayload)]
    if not plans or not any(plan.approved_plan_commit == approved_plan_commit for plan in plans):
        raise ArtifactBridgeError("finding export plan commit is not present in accepted replay")
    transitions = tuple(
        ImportedFindingTransition(record.record_id, record.payload)
        for record in replay.records
        if isinstance(record.payload, FindingTransitionPayload)
    )
    if not transitions:
        raise ArtifactBridgeError("finding export requires at least one source transition")
    return FindingHandoffExportPayload(
        source_run_id=replay.expected_run_id,
        source_head_record_id=replay.head_record_id,
        approved_plan_commit=approved_plan_commit,
        approval_review_record_id=approval_review_record_id,
        finding_transition_record_ids=tuple(item.record_id for item in transitions),
        finding_transitions_sha256=finding_transition_sequence_sha256(transitions),
        target_task_path=target_task_path,
        target_task_sha256=hashlib.sha256(target_task_bytes).hexdigest(),
        authority=Role.ORCHESTRATOR,
    )


def finding_handoff_import_payload(
    source_replay: ArtifactReplayResult,
    export_record: ArtifactRecord,
    *,
    target_run_id: str,
    target_task_bytes: bytes,
) -> FindingHandoffImportPayload:
    """Verify an export against its source replay and copy its ordered history."""
    export = export_record.payload
    if not isinstance(export, FindingHandoffExportPayload):
        raise ArtifactBridgeError("finding import requires a finding handoff export record")
    accepted_export = next(
        (record for record in source_replay.records if record.record_id == export_record.record_id),
        None,
    )
    if accepted_export != export_record:
        raise ArtifactBridgeError("finding export record is not in the accepted source replay")
    if export_record.run_id != source_replay.expected_run_id:
        raise ArtifactBridgeError("finding export record belongs to another source run")
    transitions = tuple(
        ImportedFindingTransition(record.record_id, record.payload)
        for record in source_replay.records
        if isinstance(record.payload, FindingTransitionPayload)
    )
    if (
        export.source_run_id != source_replay.expected_run_id
        or not export_record.predecessor_ids
        or export.source_head_record_id != export_record.predecessor_ids[0]
        or export.finding_transition_record_ids != tuple(item.record_id for item in transitions)
        or export.finding_transitions_sha256
        != finding_transition_sequence_sha256(transitions)
    ):
        raise ArtifactBridgeError("finding export differs from its accepted source replay")
    target_digest = hashlib.sha256(target_task_bytes).hexdigest()
    if target_digest != export.target_task_sha256:
        raise ArtifactBridgeError("finding import task bytes differ from the export binding")
    return FindingHandoffImportPayload(
        source_run_id=export.source_run_id,
        source_head_record_id=export.source_head_record_id,
        approved_plan_commit=export.approved_plan_commit,
        approval_review_record_id=export.approval_review_record_id,
        export_record_id=export_record.record_id,
        target_run_id=target_run_id,
        target_task_sha256=target_digest,
        finding_transitions_sha256=export.finding_transitions_sha256,
        transitions=transitions,
        authority=Role.ORCHESTRATOR,
    )


def attestation_payload(attestation: ValidationAttestation) -> ValidationAttestationPayload:
    records_by_display = {record.command: record for record in attestation.records}
    results = tuple(
        ValidationResult(
            command=command_payload(spec),
            outcome=(
                records_by_display[spec.display].status.value.lower()
                if spec.display in records_by_display
                else "unavailable"
            ),
            exit_code=(
                records_by_display[spec.display].exit_code
                if spec.display in records_by_display
                else -1
            ),
            output_sha256=hashlib.sha256(
                (
                    records_by_display[spec.display].output
                    if spec.display in records_by_display
                    else ""
                ).encode("utf-8")
            ).hexdigest(),
        )
        for spec in attestation.command_specs
    )
    return ValidationAttestationPayload(results=results, attested_by=Role.ORCHESTRATOR)


def validation_request_payload(request: ValidationRequest) -> ValidationRequestPayload:
    return ValidationRequestPayload(
        commands=tuple(command_payload(command.command_spec) for command in request.commands),
        requested_by=Role.ORCHESTRATOR,
    )


def provider_input_measurement_payload(
    measurement: ProviderInputMeasurement,
    *,
    work_unit_id: int | str,
    transition_fingerprint: str,
    relevant_record_head: str,
) -> ProviderInputMeasurementPayload:
    return ProviderInputMeasurementPayload(
        provider=Role(measurement.provider),
        role=Role(measurement.role),
        operation=measurement.operation,
        work_unit_id=str(work_unit_id),
        transition_fingerprint=transition_fingerprint,
        relevant_record_head=relevant_record_head,
        input_digest=measurement.input_digest,
        policy_digest=measurement.policy_digest,
        components=tuple(
            ProviderInputComponentPayload(item.name, item.chars, item.bytes)
            for item in measurement.components
        ),
        total_chars=measurement.total_chars,
        total_bytes=measurement.total_bytes,
        safety_limit_chars=measurement.safety_limit_chars,
        safety_limit_bytes=measurement.safety_limit_bytes,
        technical_limit_chars=measurement.technical_limit_chars,
        technical_limit_bytes=measurement.technical_limit_bytes,
        technical_limit_source=measurement.technical_limit_source,
        effective_limit_chars=measurement.effective_limit_chars,
        effective_limit_bytes=measurement.effective_limit_bytes,
        allowed=measurement.allowed,
        violated_dimensions=measurement.violated_dimensions,
        char_overage=measurement.char_overage,
        byte_overage=measurement.byte_overage,
        largest_component=measurement.largest_component,
    )


@dataclass(slots=True)
class ArtifactBridge:
    """Idempotently persist and re-read typed domain statements."""

    store: ArtifactStore
    now: Callable[[], str] = _now

    def append(
        self,
        payload: ArtifactPayload,
        *,
        logical_id: str,
        idempotency_key: str,
        fingerprint_sha256: str,
        fingerprint_kind: FingerprintKind = FingerprintKind.IMPLEMENTATION,
    ) -> ArtifactRecord:
        fingerprint = Fingerprint(fingerprint_kind, fingerprint_sha256)
        chain = self.store.load_chain()
        existing = next(
            (item for item in chain if item.idempotency_key == idempotency_key), None
        )
        if existing is not None:
            self._assert_equal(existing, payload, logical_id, fingerprint)
            return existing
        revisions = [
            item.revision
            for item in chain
            if item.record_type is payload.record_type and item.logical_id == logical_id
        ]
        record = ArtifactRecord.create(
            run_id=self.store.run_id,
            logical_id=logical_id,
            revision=max(revisions, default=0) + 1,
            fingerprint=fingerprint,
            predecessor_ids=((chain[-1].record_id,) if chain else ()),
            created_at=self.now(),
            idempotency_key=idempotency_key,
            payload=payload,
        )
        try:
            persisted = self.store.put(record)
        except Exception:
            # put() documents durable-but-reported-failed publication.  Resolve
            # that state before propagating the original failure.
            recovered = next(
                (
                    item
                    for item in self.store.load_chain()
                    if item.idempotency_key == idempotency_key
                ),
                None,
            )
            if recovered is None:
                raise
            persisted = recovered
        self._assert_equal(persisted, payload, logical_id, fingerprint)
        return persisted

    @staticmethod
    def _assert_equal(
        record: ArtifactRecord,
        payload: ArtifactPayload,
        logical_id: str,
        fingerprint: Fingerprint,
    ) -> None:
        if (
            record.logical_id != logical_id
            or record.fingerprint != fingerprint
            or _digest(record.payload) != _digest(payload)
        ):
            raise ArtifactBridgeError(
                "structured artifact differs semantically from the state-v3 statement"
            )

    def diagnostic(
        self,
        *,
        role: AgentRole,
        work_unit_id: int | str,
        attempt: int,
        output: str,
        reason: str,
        fingerprint_sha256: str,
    ) -> ArtifactRecord:
        payload = DiagnosticPayload(
            role=_role(role),
            work_unit_id=str(work_unit_id),
            attempt=attempt,
            output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
            reason=reason,
        )
        return self.append(
            payload,
            logical_id=f"diagnostic-{role.value}-{work_unit_id}-{attempt}",
            idempotency_key=(
                f"diagnostic:{role.value}:{work_unit_id}:{attempt}:"
                f"{payload.output_sha256}"
            ),
            fingerprint_sha256=fingerprint_sha256,
        )

    def start_provider_attempt(
        self,
        *,
        measurement_record: ArtifactRecord,
        binding_fingerprint: str,
        work_unit_id: int | str,
        operation_instance: str | None = None,
        model: str = "unknown",
        effort: str = "unknown",
    ) -> ArtifactRecord:
        """Persist one physical provider start after all local preflights pass."""
        chain = self.store.load_chain()
        replay_artifacts(chain, self.store.run_id)
        if (
            measurement_record not in chain
            or not isinstance(measurement_record.payload, ProviderInputMeasurementPayload)
        ):
            raise ArtifactBridgeError("provider attempt measurement is not in the accepted chain")
        measurement = measurement_record.payload
        if str(work_unit_id) != measurement.work_unit_id:
            raise ArtifactBridgeError("provider attempt work unit differs from its measurement")
        if operation_instance is not None and not operation_instance.strip():
            raise ArtifactBridgeError("provider attempt operation instance must be non-empty")
        logical_operation_id = _logical_provider_operation_id(
            run_id=self.store.run_id,
            work_unit_id=str(work_unit_id),
            provider=measurement.provider,
            operation=measurement.operation,
            binding_fingerprint=binding_fingerprint,
            operation_instance=operation_instance,
        )
        prior = tuple(
            record
            for record in chain
            if isinstance(record.payload, ProviderAttemptPayload)
            and record.payload.logical_operation_id == logical_operation_id
        )
        if operation_instance is not None and not prior:
            legacy_operation_id = _logical_provider_operation_id(
                run_id=self.store.run_id,
                work_unit_id=str(work_unit_id),
                provider=measurement.provider,
                operation=measurement.operation,
                binding_fingerprint=binding_fingerprint,
            )
            legacy_prior = tuple(
                record
                for record in chain
                if isinstance(record.payload, ProviderAttemptPayload)
                and record.payload.logical_operation_id == legacy_operation_id
            )
            if legacy_prior and all(
                record.payload.provider == measurement.provider
                and record.payload.role == measurement.role
                and record.payload.operation == measurement.operation
                and record.payload.work_unit_id == measurement.work_unit_id
                and record.payload.binding_fingerprint == binding_fingerprint
                and record.payload.input_digest == measurement.input_digest
                for record in legacy_prior
            ):
                # Preserve an in-flight pre-instance operation across an upgrade.
                # A semantically different round cannot inherit it because its
                # immutable input digest differs.
                logical_operation_id = legacy_operation_id
                prior = legacy_prior
        for record in prior:
            payload = record.payload
            if (
                payload.provider != measurement.provider
                or payload.role != measurement.role
                or payload.operation != measurement.operation
                or payload.work_unit_id != measurement.work_unit_id
                or payload.binding_fingerprint != binding_fingerprint
                or payload.input_digest != measurement.input_digest
            ):
                raise ArtifactBridgeError("provider attempt immutable binding differs from its first attempt")
        if prior:
            latest_attempt = max(record.payload.attempt_number for record in prior)
            latest_records = tuple(
                record for record in prior
                if record.payload.attempt_number == latest_attempt
            )
            latest_terminal = tuple(
                record for record in latest_records
                if record.payload.phase in {"succeeded", "failed"}
            )
            if len(latest_terminal) != 1:
                raise ArtifactBridgeError(
                    "provider attempt requires one terminal direct predecessor"
                )
        attempt_number = max(
            (record.payload.attempt_number for record in prior), default=0
        ) + 1
        started_at = self.now()
        payload = ProviderAttemptPayload(
            provider=measurement.provider,
            role=measurement.role,
            operation=measurement.operation,
            work_unit_id=measurement.work_unit_id,
            logical_operation_id=logical_operation_id,
            binding_fingerprint=binding_fingerprint,
            measurement_record_id=measurement_record.record_id,
            input_digest=measurement.input_digest,
            attempt_number=attempt_number,
            phase="started",
            started_at=started_at,
            ended_at=None,
            duration_seconds=None,
            failure_kind=None,
            usage=None,
            model=model,
            effort=effort,
        )
        record = self.append(
            payload,
            logical_id=f"{logical_operation_id}-{attempt_number}",
            idempotency_key=f"provider-attempt:{logical_operation_id}:{attempt_number}:started",
            fingerprint_sha256=measurement_record.fingerprint.sha256,
        )
        logger.info(
            "provider attempt started provider=%s operation=%s logical_operation_id=%s attempt=%d status=started",
            measurement.provider.value,
            measurement.operation,
            logical_operation_id,
            attempt_number,
        )
        return record

    def finish_provider_attempt(
        self,
        started_record: ArtifactRecord,
        *,
        duration_seconds: float,
        failure_kind: str | None,
        usage: ProviderUsagePayload | None,
    ) -> ArtifactRecord:
        """Persist the sole terminal revision for a previously durable start."""
        chain = self.store.load_chain()
        if started_record not in chain:
            raise ArtifactBridgeError(
                "provider attempt start is not in the accepted chain"
            )
        replay_artifacts(chain, self.store.run_id)
        if not isinstance(started_record.payload, ProviderAttemptPayload) or started_record.payload.phase != "started":
            raise ArtifactBridgeError("provider attempt terminal requires a started record")
        started = started_record.payload
        phase = "failed" if failure_kind is not None else "succeeded"
        terminal_key = (
            f"provider-attempt:{started.logical_operation_id}:"
            f"{started.attempt_number}:terminal"
        )
        existing = next(
            (
                record for record in chain
                if record.idempotency_key == terminal_key
            ),
            None,
        )
        if existing is not None:
            payload = existing.payload
            if (
                not isinstance(payload, ProviderAttemptPayload)
                or payload.phase != phase
                or payload.failure_kind != failure_kind
                or payload.usage != usage
                or payload.logical_operation_id != started.logical_operation_id
                or payload.attempt_number != started.attempt_number
            ):
                raise ArtifactBridgeError("provider attempt terminal differs from its durable result")
            return existing
        payload = ProviderAttemptPayload(
            provider=started.provider,
            role=started.role,
            operation=started.operation,
            work_unit_id=started.work_unit_id,
            logical_operation_id=started.logical_operation_id,
            binding_fingerprint=started.binding_fingerprint,
            measurement_record_id=started.measurement_record_id,
            input_digest=started.input_digest,
            attempt_number=started.attempt_number,
            phase=phase,
            started_at=started.started_at,
            ended_at=self.now(),
            duration_seconds=duration_seconds,
            failure_kind=failure_kind,
            usage=usage,
            model=started.model,
            effort=started.effort,
        )
        record = self.append(
            payload,
            logical_id=started_record.logical_id,
            idempotency_key=terminal_key,
            fingerprint_sha256=started_record.fingerprint.sha256,
        )
        logger.info(
            "provider attempt terminal provider=%s operation=%s logical_operation_id=%s attempt=%d status=%s",
            started.provider.value,
            started.operation,
            started.logical_operation_id,
            started.attempt_number,
            phase,
        )
        return record


def _logical_provider_operation_id(
    *, run_id: str, work_unit_id: str, provider: Role, operation: str,
    binding_fingerprint: str, operation_instance: str | None = None,
) -> str:
    digest = hashlib.sha256(
        canonical_json(
            [
                run_id,
                work_unit_id,
                provider.value,
                operation,
                binding_fingerprint,
                operation_instance,
            ]
            if operation_instance is not None
            else [run_id, work_unit_id, provider.value, operation, binding_fingerprint]
        )
    ).hexdigest()
    return f"provider-operation-{digest}"


__all__ = [
    "ArtifactBridge", "ArtifactBridgeError", "agent_result_payload",
    "attestation_payload", "command_payload", "finding_payload",
    "finding_handoff_export_payload", "finding_handoff_import_payload", "plan_payload",
    "review_payload", "review_payload_matches_result", "task_payload", "validation_request_payload",
    "provider_input_measurement_payload",
    "BindingPayload", "GatePayload", "ProviderUsagePayload",
    "WorkUnitPayload",
]
