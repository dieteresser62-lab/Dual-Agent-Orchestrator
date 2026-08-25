"""Pure, deterministic reduction of one structured artifact record chain.

The store remains responsible for decoding bytes and validating the physical
append-only chain.  This module deliberately has no filesystem or clock access:
it validates the cross-record domain relationships and returns an immutable
view which can be shared by resume and audit projection code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from typing import Sequence

from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BindingPayload,
    DiagnosticPayload,
    FinalReviewPreflightPayload,
    FindingTransitionPayload,
    ProviderInputMeasurementPayload,
    ProviderAttemptPayload,
    RecordType,
    ResumeCheckPayload,
    ReviewPayload,
    ValidationAttestationPayload,
    WorkflowCompletionPayload,
    WorkUnitPayload,
    CorrectionWorkUnitPayload,
    canonical_json,
)
from contracts import (
    AgentRole,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingResponseDecision,
    FindingStatus,
    apply_finding_response,
    apply_reviewer_finding_update,
)


class ReplayDiagnosticCode(StrEnum):
    UNSUPPORTED_PROTOCOL = "UNSUPPORTED-PROTOCOL"
    RECORD_MISSING = "RECORD-MISSING"
    RECORD_DUPLICATE = "RECORD-DUPLICATE"
    RECORD_UNKNOWN = "RECORD-UNKNOWN"
    RECORD_TYPE_MISMATCH = "RECORD-TYPE-MISMATCH"
    RECORD_RUN_MISMATCH = "RECORD-RUN-MISMATCH"
    RECORD_REFERENCE_MISSING = "RECORD-REFERENCE-MISSING"
    RECORD_FINGERPRINT_MISMATCH = "RECORD-FINGERPRINT-MISMATCH"
    MIRROR_AHEAD = "MIRROR-AHEAD"
    MIRROR_AMBIGUOUS = "MIRROR-AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class ReplayDiagnostic:
    code: ReplayDiagnosticCode
    message: str
    record_id: str | None = None


class ArtifactReplayError(ValueError):
    """A stable, machine-readable semantic replay failure."""

    def __init__(self, diagnostic: ReplayDiagnostic) -> None:
        self.diagnostic = diagnostic
        self.code = diagnostic.code
        self.record_id = diagnostic.record_id
        location = f" at {diagnostic.record_id}" if diagnostic.record_id else ""
        super().__init__(f"{diagnostic.code.value}{location}: {diagnostic.message}")


@dataclass(frozen=True, slots=True)
class ReplayAuditEvent:
    """Presentation-neutral audit input for one accepted record."""

    sequence: int
    record_id: str
    record_type: RecordType
    logical_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class ReplayFact:
    """One immutable semantic fact; ``payload_json`` is canonical typed data."""

    record_id: str
    record_type: str
    logical_id: str
    revision: int
    status: str
    fingerprint_kind: str
    fingerprint_sha256: str
    predecessor_ids: tuple[str, ...]
    payload_json: bytes

    def to_document(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "record_type": self.record_type,
            "logical_id": self.logical_id,
            "revision": self.revision,
            "status": self.status,
            "fingerprint": {
                "kind": self.fingerprint_kind,
                "sha256": self.fingerprint_sha256,
            },
            "predecessor_ids": list(self.predecessor_ids),
            "payload": json.loads(self.payload_json),
        }


@dataclass(frozen=True, slots=True)
class ArtifactReplayResult:
    """Immutable result of reducing one chain."""

    expected_run_id: str
    records: tuple[ArtifactRecord, ...]
    head_record_id: str | None
    semantic_facts: tuple[ReplayFact, ...]
    semantic_digest: str
    audit_events: tuple[ReplayAuditEvent, ...]

    def subset(self, records: Sequence[ArtifactRecord]) -> "ArtifactReplayResult":
        """Derive a presentation-only subsequence from an accepted replay.

        Slice audit views are subsequences and therefore are not standalone
        chains.  Membership is checked against this result; semantic references
        are never reinterpreted from the projected Markdown.
        """
        selected = tuple(records)
        if selected == self.records:
            return self
        accepted = {
            record.record_id: (index, record)
            for index, record in enumerate(self.records)
        }
        if any(
            record.record_id not in accepted or accepted[record.record_id][1] != record
            for record in selected
        ):
            _fail(ReplayDiagnosticCode.RECORD_UNKNOWN, "subset contains an unplayed record")
        if any(
            accepted[left.record_id][0] >= accepted[right.record_id][0]
            for left, right in zip(selected, selected[1:])
        ):
            _fail(ReplayDiagnosticCode.RECORD_REFERENCE_MISSING, "subset is not in replay order")
        return _result(self.expected_run_id, selected)


def replay_artifacts(
    records: Sequence[ArtifactRecord],
    expected_run_id: str,
    *,
    allow_empty: bool = False,
) -> ArtifactReplayResult:
    """Validate and reduce ``records`` without I/O or mutation."""
    chain = tuple(records)
    if not chain:
        if allow_empty:
            return _result(expected_run_id, ())
        _fail(ReplayDiagnosticCode.RECORD_MISSING, "the authoritative record chain is empty")

    seen_ids: dict[str, ArtifactRecord] = {}
    seen_keys: set[str] = set()
    seen_revisions: set[tuple[RecordType, str, int]] = set()
    latest_revisions: dict[tuple[RecordType, str], int] = {}
    singleton_types: set[RecordType] = set()

    for index, record in enumerate(chain):
        if not isinstance(record, ArtifactRecord):
            _fail(ReplayDiagnosticCode.RECORD_UNKNOWN, "chain member is not an ArtifactRecord")
        if record.run_id != expected_run_id:
            _fail(
                ReplayDiagnosticCode.RECORD_RUN_MISMATCH,
                f"expected run {expected_run_id!r}, found {record.run_id!r}",
                record,
            )
        if not isinstance(record.record_type, RecordType):
            _fail(ReplayDiagnosticCode.RECORD_UNKNOWN, "record type is unsupported", record)
        payload_type = getattr(record.payload, "record_type", None)
        if payload_type != record.record_type:
            _fail(
                ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                "record type does not match its typed payload",
                record,
            )
        if record.record_type in {
            RecordType.TASK,
            RecordType.PLAN,
            RecordType.WORKFLOW_COMPLETION,
        }:
            if record.record_type in singleton_types:
                _fail(
                    ReplayDiagnosticCode.RECORD_DUPLICATE,
                    f"singleton record type {record.record_type.value!r} is duplicated",
                    record,
                )
            singleton_types.add(record.record_type)
        revision_key = (record.record_type, record.logical_id, record.revision)
        if (
            record.record_id in seen_ids
            or record.idempotency_key in seen_keys
            or revision_key in seen_revisions
        ):
            _fail(ReplayDiagnosticCode.RECORD_DUPLICATE, "record identity is duplicated", record)
        logical_key = (record.record_type, record.logical_id)
        prior_revision = latest_revisions.get(logical_key, 0)
        if record.revision != prior_revision + 1:
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                f"revision {record.revision} does not follow revision {prior_revision}",
                record,
            )
        if index == 0:
            if record.predecessor_ids:
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "chain root references a predecessor and is not in append order",
                    record,
                )
        else:
            if not record.predecessor_ids or record.predecessor_ids[0] != chain[index - 1].record_id:
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "primary predecessor is not the previous record",
                    record,
                )
        missing = [item for item in record.predecessor_ids if item not in seen_ids]
        if missing:
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                f"predecessor {missing[0]!r} has not been observed",
                record,
            )
        seen_ids[record.record_id] = record
        seen_keys.add(record.idempotency_key)
        seen_revisions.add(revision_key)
        latest_revisions[logical_key] = record.revision

    _validate_payload_references(chain, seen_ids)
    return _result(expected_run_id, chain)


def replay_findings(
    replay: ArtifactReplayResult,
    work_unit_id: int | str | None = None,
) -> tuple[FindingRecord, ...]:
    """Project native finding authority from accepted structured transitions.

    Historical finding-transition records remain valid replay inputs, but they
    are unconditionally excluded here: without a persisted work-unit id they
    cannot safely authorize a new native request when finding identifiers may
    be reused in another work unit.
    """
    target = None if work_unit_id is None else str(work_unit_id)
    findings: dict[str, FindingRecord] = {}
    for record in replay.records:
        payload = record.payload
        if not isinstance(payload, FindingTransitionPayload):
            continue
        if payload.work_unit_id is None:
            continue
        if target is not None and payload.work_unit_id != target:
            continue
        if payload.action == "opened":
            if payload.finding_id in findings:
                _fail(
                    ReplayDiagnosticCode.RECORD_DUPLICATE,
                    f"finding {payload.finding_id!r} is opened more than once",
                    record,
                )
            if (
                payload.summary is None
                or payload.acceptance_test is None
                or payload.origin_slice_id is None
                or payload.origin_round_number is None
                or payload.finding_status != "open"
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                    "structured finding opening metadata is incomplete",
                    record,
                )
            try:
                findings[payload.finding_id] = FindingRecord(
                    finding_id=payload.finding_id,
                    finding_class=FindingClass(payload.severity.value),
                    status=FindingStatus.OPEN,
                    summary=payload.summary,
                    acceptance_test=payload.acceptance_test,
                    origin=FindingOrigin(
                        payload.origin_slice_id,
                        payload.origin_round_number,
                        AgentRole(payload.reporter.value),
                    ),
                )
            except ValueError as exc:
                _fail(
                    ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                    f"structured finding opening is invalid: {exc}",
                    record,
                )
            continue
        finding = findings.get(payload.finding_id)
        if finding is None:
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                f"finding transition references unopened finding {payload.finding_id!r}",
                record,
            )
        if payload.reporter.value != finding.origin.reporter.value or (
            payload.action != "reclassified"
            and payload.severity.value != finding.finding_class.value
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                "finding transition changes immutable reviewer ownership",
                record,
            )
        try:
            if payload.action == "responded":
                if payload.response_decision is None:
                    _fail(
                        ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                        "structured finding response lacks response_decision",
                        record,
                    )
                finding = apply_finding_response(
                    finding,
                    FindingResponseDecision(payload.response_decision.upper()),
                    payload.rationale,
                )
            elif payload.action == "reclassified":
                finding = apply_reviewer_finding_update(
                    finding,
                    reviewer=finding.origin.reporter,
                    status=finding.status,
                    rationale=payload.rationale,
                    finding_class=FindingClass(payload.severity.value),
                )
            elif payload.action == "status_changed":
                finding = apply_reviewer_finding_update(
                    finding,
                    reviewer=finding.origin.reporter,
                    status=FindingStatus(payload.finding_status.upper()),
                    rationale=payload.rationale,
                    finding_class=FindingClass(payload.severity.value),
                )
        except ValueError as exc:
            _fail(
                ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                f"structured finding transition is invalid: {exc}",
                record,
            )
        findings[payload.finding_id] = finding
    return tuple(sorted(findings.values(), key=lambda item: item.finding_id))


def _validate_payload_references(
    chain: tuple[ArtifactRecord, ...], records_by_id: dict[str, ArtifactRecord]
) -> None:
    positions = {record.record_id: index for index, record in enumerate(chain)}
    work_units: dict[str, ArtifactRecord] = {}
    for record in chain:
        if isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload)) and (
            record.logical_id.startswith("work-unit-")
        ):
            # The first revision establishes authority for this logical work
            # unit.  Later revisions must not move the global implementation
            # boundary forward or make intervening activity look like a
            # forward reference.
            work_units.setdefault(record.logical_id.removeprefix("work-unit-"), record)
    first_work_unit_position = min(
        (positions[record.record_id] for record in work_units.values()),
        default=None,
    )
    for record in chain:
        payload = record.payload
        # Planning work units intentionally have no WorkUnitPayload: the plan
        # record is their authoritative result. Once the first implementation
        # work unit appears, later work-unit-owned activity must resolve to one
        # of the persisted work-unit records.
        if (
            first_work_unit_position is not None
            and positions[record.record_id] > first_work_unit_position
            and isinstance(
                payload,
                (
                    AgentResultPayload,
                    DiagnosticPayload,
                    ReviewPayload,
                    ProviderInputMeasurementPayload,
                    ProviderAttemptPayload,
                    FinalReviewPreflightPayload,
                ),
            )
            and (
                payload.work_unit_id not in work_units
                or positions[work_units[payload.work_unit_id].record_id]
                >= positions[record.record_id]
            )
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                f"work unit {payload.work_unit_id!r} is not present",
                record,
            )
        if isinstance(payload, BindingPayload):
            attestation = records_by_id.get(payload.attestation_id)
            if (
                attestation is None
                or not isinstance(attestation.payload, ValidationAttestationPayload)
                or positions[attestation.record_id] >= positions[record.record_id]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    f"validation attestation {payload.attestation_id!r} is not present",
                    record,
                )
            _same_fingerprint(record, attestation)
            for approval_id in payload.approval_ids:
                approval = records_by_id.get(approval_id)
                if (
                    approval is None
                    or not isinstance(approval.payload, ReviewPayload)
                    or approval.payload.verdict != "approved"
                    or positions[approval.record_id] >= positions[record.record_id]
                ):
                    _fail(
                        ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                        f"approved review {approval_id!r} is not present",
                        record,
                    )
                _same_fingerprint(record, approval)
        elif isinstance(payload, WorkflowCompletionPayload) and payload.final_binding_id is not None:
            binding = records_by_id.get(payload.final_binding_id)
            if (
                binding is None
                or not isinstance(binding.payload, BindingPayload)
                or positions[binding.record_id] >= positions[record.record_id]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    f"final binding {payload.final_binding_id!r} is not present",
                    record,
                )
            _same_fingerprint(record, binding)
        elif isinstance(payload, FinalReviewPreflightPayload):
            measurement = records_by_id.get(payload.measurement_record_id)
            if (
                measurement is None
                or not isinstance(measurement.payload, ProviderInputMeasurementPayload)
                or positions[measurement.record_id] >= positions[record.record_id]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    f"measurement {payload.measurement_record_id!r} is not present",
                    record,
                )
            _same_fingerprint(record, measurement)
        elif isinstance(payload, ProviderAttemptPayload):
            measurement = records_by_id.get(payload.measurement_record_id)
            if (
                measurement is None
                or not isinstance(measurement.payload, ProviderInputMeasurementPayload)
                or positions[measurement.record_id] >= positions[record.record_id]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    f"measurement {payload.measurement_record_id!r} is not present",
                    record,
                )
            _same_fingerprint(record, measurement)
            measured = measurement.payload
            if (
                payload.provider != measured.provider
                or payload.role != measured.role
                or payload.operation != measured.operation
                or payload.work_unit_id != measured.work_unit_id
                or payload.input_digest != measured.input_digest
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "provider attempt differs from its bound measurement",
                    record,
                )
        elif isinstance(payload, ResumeCheckPayload):
            predecessor = record.predecessor_ids[0] if record.predecessor_ids else None
            if payload.expected_head_id != predecessor:
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "resume check is not bound to its immediate prior head",
                    record,
                )

    attempts: dict[str, dict[int, list[ArtifactRecord]]] = {}
    for record in chain:
        if isinstance(record.payload, ProviderAttemptPayload):
            attempts.setdefault(record.payload.logical_operation_id, {}).setdefault(
                record.payload.attempt_number, []
            ).append(record)
    for logical_operation_id, numbered in attempts.items():
        expected = set(range(1, max(numbered) + 1))
        if set(numbered) != expected:
            record = next(iter(numbered[max(numbered)]))
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                f"provider attempts for {logical_operation_id!r} are not contiguous",
                record,
            )
        immutable: tuple[object, ...] | None = None
        for attempt_number in sorted(numbered):
            records = numbered[attempt_number]
            if len(records) not in {1, 2}:
                _fail(ReplayDiagnosticCode.RECORD_DUPLICATE, "provider attempt has too many revisions", records[-1])
            started = records[0]
            payload = started.payload
            assert isinstance(payload, ProviderAttemptPayload)
            if started.revision != 1 or payload.phase != "started":
                _fail(ReplayDiagnosticCode.RECORD_REFERENCE_MISSING, "provider attempt must begin with revision 1 started", started)
            binding = (
                payload.provider, payload.role, payload.operation, payload.work_unit_id,
                payload.logical_operation_id, payload.binding_fingerprint,
                payload.input_digest,
            )
            if immutable is None:
                immutable = binding
            elif binding != immutable:
                _fail(ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH, "provider attempt immutable binding changed", started)
            if len(records) == 2:
                terminal = records[1]
                terminal_payload = terminal.payload
                assert isinstance(terminal_payload, ProviderAttemptPayload)
                if terminal.revision != 2 or terminal_payload.phase not in {"succeeded", "failed"}:
                    _fail(ReplayDiagnosticCode.RECORD_REFERENCE_MISSING, "provider attempt terminal must be revision 2", terminal)
                if terminal_payload.measurement_record_id != payload.measurement_record_id or (
                    terminal_payload.provider, terminal_payload.role, terminal_payload.operation,
                    terminal_payload.work_unit_id, terminal_payload.logical_operation_id,
                    terminal_payload.binding_fingerprint, terminal_payload.input_digest,
                    terminal_payload.attempt_number, terminal_payload.started_at,
                ) != (
                    payload.provider, payload.role, payload.operation, payload.work_unit_id,
                    payload.logical_operation_id, payload.binding_fingerprint, payload.input_digest,
                    payload.attempt_number, payload.started_at,
                ):
                    _fail(ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH, "provider attempt terminal changed immutable fields", terminal)


def _same_fingerprint(record: ArtifactRecord, referenced: ArtifactRecord) -> None:
    if referenced.fingerprint != record.fingerprint:
        _fail(
            ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
            f"referenced record {referenced.record_id!r} has another fingerprint",
            record,
        )


def _semantic_facts(records: tuple[ArtifactRecord, ...]) -> tuple[ReplayFact, ...]:
    return tuple(
        ReplayFact(
            record_id=record.record_id,
            record_type=record.record_type.value,
            logical_id=record.logical_id,
            revision=record.revision,
            status=record.status,
            fingerprint_kind=record.fingerprint.kind.value,
            fingerprint_sha256=record.fingerprint.sha256,
            predecessor_ids=record.predecessor_ids,
            payload_json=canonical_json(asdict(record.payload)),
        )
        for record in records
    )


def _result(expected_run_id: str, records: tuple[ArtifactRecord, ...]) -> ArtifactReplayResult:
    facts = _semantic_facts(records)
    documents = tuple(fact.to_document() for fact in facts)
    return ArtifactReplayResult(
        expected_run_id=expected_run_id,
        records=records,
        head_record_id=records[-1].record_id if records else None,
        semantic_facts=facts,
        semantic_digest=hashlib.sha256(canonical_json(documents)).hexdigest(),
        audit_events=tuple(
            ReplayAuditEvent(
                index,
                record.record_id,
                record.record_type,
                record.logical_id,
                record.revision,
            )
            for index, record in enumerate(records, start=1)
        ),
    )


def _fail(
    code: ReplayDiagnosticCode,
    message: str,
    record: ArtifactRecord | None = None,
) -> None:
    raise ArtifactReplayError(
        ReplayDiagnostic(code, message, None if record is None else record.record_id)
    )


__all__ = [
    "ArtifactReplayError",
    "ArtifactReplayResult",
    "ReplayAuditEvent",
    "ReplayDiagnostic",
    "ReplayDiagnosticCode",
    "ReplayFact",
    "replay_artifacts",
]
