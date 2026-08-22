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
    FindingTransitionPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ProviderAttemptPayload,
    ProviderUsagePayload,
    GatePayload,
    PlanPayload,
    ReviewPayload,
    Role,
    SliceSpec,
    TaskPayload,
    ValidationAttestationPayload,
    ValidationRequestPayload,
    ValidationResult,
    WorkUnitPayload,
    canonical_json,
)
from artifact_store import ArtifactStore
from contracts import (
    AgentRole,
    CodexContractResult,
    ContractResult,
    FindingRecord,
    PlannedSlice,
    ValidationAttestation,
    ValidationCommandSpec,
)
from task_contract import TaskContract
from validation_matrix import ValidationRequest
from provider_input_budget import ProviderInputMeasurement
from artifact_replay import replay_artifacts


class ArtifactBridgeError(RuntimeError):
    """Raised when structured and state-v3 meanings are not identical."""


logger = logging.getLogger(__name__)
_ANTIGRAVITY_TOOL_SCHEMA_FAILURE = "antigravity_tool_schema"


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
    transport_schema: str | None = None,
    request_id: str | None = None,
    response_sha256: str | None = None,
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
    transport_schema: str | None = None,
    request_id: str | None = None,
    response_sha256: str | None = None,
) -> ReviewPayload:
    verdict = "stop" if result.stopped else "approved" if result.approval else "denied"
    evidence = None
    if result.evidence is not None:
        evidence = " | ".join(
            (
                result.evidence.dimensions,
                result.evidence.largest_residual_risk,
                result.evidence.break_condition,
            )
        )
    return ReviewPayload(
        reviewer=_role(result.reviewer),
        work_unit_id=str(work_unit_id),
        verdict=verdict,
        finding_ids=tuple(item.finding_id for item in result.findings),
        evidence=evidence,
        transport_schema=transport_schema,
        request_id=request_id,
        response_sha256=response_sha256,
    )


def finding_payload(
    finding: FindingRecord,
    *,
    actor: AgentRole | None = None,
    action: str = "opened",
    rationale: str | None = None,
) -> FindingTransitionPayload:
    reporter = _role(finding.origin.reporter)
    return FindingTransitionPayload(
        finding_id=finding.finding_id,
        reporter=reporter,
        actor=_role(actor) if actor is not None else reporter,
        action=action,
        severity=FindingSeverity(finding.finding_class.value),
        finding_status=finding.status.value.lower(),
        rationale=rationale or finding.status_rationale or finding.summary,
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
        logical_operation_id = _logical_provider_operation_id(
            run_id=self.store.run_id,
            work_unit_id=str(work_unit_id),
            provider=measurement.provider,
            operation=measurement.operation,
            binding_fingerprint=binding_fingerprint,
        )
        prior = tuple(
            record
            for record in chain
            if isinstance(record.payload, ProviderAttemptPayload)
            and record.payload.logical_operation_id == logical_operation_id
        )
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
            schema_failures = tuple(
                record for record in prior
                if record.payload.phase == "failed"
                and record.payload.failure_kind == _ANTIGRAVITY_TOOL_SCHEMA_FAILURE
            )
            if schema_failures and not (
                len(schema_failures) == 1
                and schema_failures[0].payload.attempt_number == 1
                and latest_attempt == 1
            ):
                raise ArtifactBridgeError(
                    "Antigravity tool-schema failure permits only physical attempt 2"
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
    binding_fingerprint: str,
) -> str:
    digest = hashlib.sha256(
        canonical_json(
            [run_id, work_unit_id, provider.value, operation, binding_fingerprint]
        )
    ).hexdigest()
    return f"provider-operation-{digest}"


__all__ = [
    "ArtifactBridge", "ArtifactBridgeError", "agent_result_payload",
    "attestation_payload", "command_payload", "finding_payload", "plan_payload",
    "review_payload", "task_payload", "validation_request_payload",
    "provider_input_measurement_payload",
    "BindingPayload", "GatePayload", "ProviderUsagePayload",
    "WorkUnitPayload",
]
