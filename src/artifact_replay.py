"""Pure, deterministic reduction of one structured artifact record chain.

The store remains responsible for decoding bytes and validating the physical
append-only chain.  This module deliberately has no filesystem or clock access:
it validates the cross-record domain relationships and returns an immutable
view which can be shared by resume and audit projection code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
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
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    GateDecisionPayload,
    GatePayload,
    GateTransitionPayload,
    ImportedFindingTransition,
    InvocationFailurePayload,
    finding_transition_sequence_sha256,
    ProviderInputMeasurementPayload,
    ProviderAttemptPayload,
    PlanPayload,
    RecordType,
    Role,
    RunIdentityPayload,
    RunProfilePayload,
    QuotaPausePayload,
    SideEffectPayload,
    SliceBoundaryPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    ResumeCheckPayload,
    ReviewPayload,
    ProviderContentPayload,
    ReviewPacketPayload,
    ValidationContentPayload,
    ValidationAttestationPayload,
    WorkflowCompletionPayload,
    WorkUnitPayload,
    CorrectionWorkUnitPayload,
    TransientRetryPayload,
    canonical_json,
)
from contracts import (
    FindingRecord,
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
class ReplayedWorkflowCursor:
    slice_id: str
    work_unit_id: str
    step: str


@dataclass(frozen=True, slots=True)
class ReplayedWorkUnitState:
    work_unit_id: str
    slice_id: str
    status: str
    step: str


@dataclass(frozen=True, slots=True)
class ReplayedSideEffect:
    effect_key: str
    effect_class: str
    work_unit_id: str
    operation: tuple[str, ...]
    result: str | None
    intent_record_id: str
    result_record_id: str | None
    result_sequence: int | None


@dataclass(frozen=True, slots=True)
class ReplayedGateDecision:
    work_unit_id: str
    approved: bool
    reason: str
    fingerprint: str
    paths: tuple[str, ...]
    rationale: str
    resume_step: str | None
    authority: Role
    gate_created_at: str
    gate_record_id: str
    decision_record_id: str


def project_work_unit_reviewers(
    records: Sequence[ArtifactRecord],
) -> tuple[tuple[str, Role | None], ...]:
    """Project the reviewer mirror without introducing a reviewer record."""
    reviewers: dict[str, Role | None] = {}
    for record in records:
        payload = record.payload
        if (
            isinstance(payload, WorkflowTransitionPayload)
            and payload.work_unit_id is not None
        ):
            reviewers.setdefault(payload.work_unit_id, None)
        elif isinstance(payload, ReviewPayload) and payload.verdict == "denied":
            reviewers[payload.work_unit_id] = payload.reviewer

    def identifier_order(value: str) -> tuple[int, int | str]:
        return (0, int(value)) if value.isdigit() else (1, value)

    return tuple(
        (key, reviewers[key]) for key in sorted(reviewers, key=identifier_order)
    )


@dataclass(frozen=True, slots=True)
class ArtifactReplayResult:
    """Immutable result of reducing one chain."""

    expected_run_id: str
    records: tuple[ArtifactRecord, ...]
    head_record_id: str | None
    semantic_facts: tuple[ReplayFact, ...]
    semantic_digest: str
    audit_events: tuple[ReplayAuditEvent, ...]
    run_identity: RunIdentityPayload | None = None
    run_profile: RunProfilePayload | None = None
    workflow_cursor: ReplayedWorkflowCursor | None = None
    slice_statuses: tuple[tuple[str, str], ...] = ()
    work_unit_states: tuple[ReplayedWorkUnitState, ...] = ()
    workflow_policies: tuple[WorkflowPolicyPayload, ...] = ()
    slice_boundaries: tuple[SliceBoundaryPayload, ...] = ()
    gate_transitions: tuple[GateTransitionPayload, ...] = ()
    gate_decisions: tuple[ReplayedGateDecision, ...] = ()
    invocation_failures: tuple[InvocationFailurePayload, ...] = ()
    validation_contents: tuple[ValidationContentPayload, ...] = ()
    provider_contents: tuple[ProviderContentPayload, ...] = ()
    review_packets: tuple[ReviewPacketPayload, ...] = ()
    work_unit_reviewers: tuple[tuple[str, Role | None], ...] = ()
    side_effects: tuple[ReplayedSideEffect, ...] = ()
    _reference_records: tuple[ArtifactRecord, ...] = field(
        default=(), repr=False, compare=False
    )

    def completed_side_effects(self, work_unit_id: str) -> tuple[str, ...]:
        completed = tuple(
            item
            for item in self.side_effects
            if item.work_unit_id == work_unit_id
            and item.result_record_id is not None
            and item.effect_class != "ledger"
        )
        return tuple(
            item.operation[0] if item.effect_class == "internal" else item.effect_key
            for item in sorted(
                completed,
                key=lambda item: item.result_sequence or 0,
            )
        )

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
        return _result(
            self.expected_run_id,
            selected,
            reference_records=self._reference_records or self.records,
        )


def replay_artifacts(
    records: Sequence[ArtifactRecord],
    expected_run_id: str,
    *,
    allow_empty: bool = False,
    require_content_authority: bool | None = None,
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
            RecordType.RUN_IDENTITY,
            RecordType.RUN_PROFILE,
            RecordType.PLAN,
            RecordType.WORKFLOW_COMPLETION,
            RecordType.FINDING_HANDOFF_EXPORT,
            RecordType.FINDING_HANDOFF_IMPORT,
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

    strict_content = (
        require_content_authority
        if require_content_authority is not None
        else any(isinstance(record.payload, RunIdentityPayload) for record in chain)
        or any(
            isinstance(
                record.payload,
                (ValidationContentPayload, ProviderContentPayload, ReviewPacketPayload),
            )
            for record in chain
        )
    )
    _validate_payload_references(
        chain, seen_ids, require_content_authority=strict_content
    )
    return _result(expected_run_id, chain)


def replay_findings(
    replay: ArtifactReplayResult,
    work_unit_id: int | str | None = None,
    *,
    finding_ids: Sequence[str] | None = None,
) -> tuple[FindingRecord, ...]:
    """Compatibility delegate to the single canonical finding reducer."""
    from finding_reducer import reduce_findings

    return reduce_findings(replay).request_subset(
        work_unit_id=work_unit_id,
        finding_ids=finding_ids,
    ).findings


def _validate_payload_references(
    chain: tuple[ArtifactRecord, ...],
    records_by_id: dict[str, ArtifactRecord],
    *,
    require_content_authority: bool,
) -> None:
    from finding_reducer import reduce_findings

    positions = {record.record_id: index for index, record in enumerate(chain)}
    transition_units: dict[str, ArtifactRecord] = {}
    transition_slices: set[str] = set()
    slice_boundaries: dict[str, SliceBoundaryPayload] = {}
    for record in chain:
        payload = record.payload
        if isinstance(payload, WorkflowTransitionPayload):
            transition_slices.add(payload.slice_id)
            if payload.work_unit_id is not None:
                prior = transition_units.setdefault(payload.work_unit_id, record)
                assert isinstance(prior.payload, WorkflowTransitionPayload)
                if prior.payload.slice_id != payload.slice_id:
                    _fail(
                        ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                        "workflow transition moves a work unit to another slice",
                        record,
                    )
        elif isinstance(payload, WorkflowPolicyPayload):
            transition = transition_units.get(payload.work_unit_id)
            if transition is None or positions[transition.record_id] >= positions[record.record_id]:
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "workflow policy precedes its work-unit transition",
                    record,
                )
        elif isinstance(payload, SliceBoundaryPayload):
            if payload.slice_id not in transition_slices:
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "slice boundary precedes its workflow transition",
                    record,
                )
            prior = slice_boundaries.get(payload.slice_id)
            if prior is not None:
                if (
                    payload.start_commit != prior.start_commit
                    or payload.start_fingerprint != prior.start_fingerprint
                    or not set(prior.scope_change_groups).issubset(
                        payload.scope_change_groups
                    )
                ):
                    _fail(
                        ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                        "slice boundary revision changes its immutable start or removes scope groups",
                        record,
                    )
            slice_boundaries[payload.slice_id] = payload

    invocation_failures: dict[str, tuple[ArtifactRecord, InvocationFailurePayload]] = {}
    for record in chain:
        payload = record.payload
        if isinstance(payload, InvocationFailurePayload):
            if (
                record.logical_id != f"invocation-failure-{payload.invocation_id}"
                or record.idempotency_key
                != f"invocation-failure:{payload.invocation_id}"
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "invocation failure record identity differs from its invocation",
                    record,
                )
            transition = transition_units.get(payload.work_unit_id)
            if (
                transition is None
                or positions[transition.record_id] >= positions[record.record_id]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "invocation failure precedes its work-unit transition",
                    record,
                )
            prior_transitions = tuple(
                candidate.payload
                for candidate in chain[: positions[record.record_id]]
                if isinstance(candidate.payload, WorkflowTransitionPayload)
                and candidate.payload.work_unit_id == payload.work_unit_id
            )
            latest_transition = prior_transitions[-1]
            if (
                latest_transition.slice_id != payload.slice_id
                or latest_transition.step != payload.step
                or latest_transition.work_unit_status != "in_progress"
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "invocation failure assignment differs from the active transition",
                    record,
                )
            if (
                payload.diff_fingerprint is not None
                and record.fingerprint.sha256 != payload.diff_fingerprint
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "invocation failure fingerprint differs from its retry binding",
                    record,
                )
            if payload.invocation_id in invocation_failures:
                _fail(
                    ReplayDiagnosticCode.RECORD_DUPLICATE,
                    "invocation failure id is duplicated",
                    record,
                )
            invocation_failures[payload.invocation_id] = (record, payload)
        elif isinstance(payload, (QuotaPausePayload, TransientRetryPayload)):
            prefix = (
                "quota-pause-"
                if isinstance(payload, QuotaPausePayload)
                else "transient-retry-"
            )
            if not record.logical_id.startswith(prefix):
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "retry transition logical identity is malformed",
                    record,
                )
            invocation_id = record.logical_id.removeprefix(prefix)
            failure_entry = invocation_failures.get(invocation_id)
            if failure_entry is None:
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "retry transition has no earlier invocation failure",
                    record,
                )
            failure_record, failure = failure_entry
            expected_kind = (
                "quota" if isinstance(payload, QuotaPausePayload) else "network"
            )
            if (
                positions[failure_record.record_id] >= positions[record.record_id]
                or failure.failure_kind != expected_kind
                or failure.role is not payload.role
                or failure.diff_fingerprint != payload.repository_fingerprint
                or failure.resume_at_utc != payload.retry_at
                or (
                    isinstance(payload, TransientRetryPayload)
                    and (
                        not failure.automatic_resume
                        or failure.auto_resume_count != payload.attempt
                    )
                )
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "retry transition differs from its invocation failure decision",
                    record,
                )

    bound_gate_decisions: set[tuple[str, str]] = set()
    for record in chain:
        payload = record.payload
        if isinstance(payload, GateTransitionPayload):
            if record.logical_id != f"gate-transition-{payload.work_unit_id}":
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "gate transition logical identity differs from its work unit",
                    record,
                )
            if (payload.active_test_fingerprint is None) != (
                not payload.active_test_paths
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "gate transition has a partial active-test binding",
                    record,
                )
            transition = transition_units.get(payload.work_unit_id)
            if (
                transition is None
                or positions[transition.record_id] >= positions[record.record_id]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "gate transition precedes its work-unit transition",
                    record,
                )
        elif isinstance(payload, GateDecisionPayload):
            transition = transition_units.get(payload.work_unit_id)
            gate = records_by_id.get(payload.gate_record_id)
            if (
                transition is None
                or positions[transition.record_id] >= positions[record.record_id]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "gate decision precedes its work-unit transition",
                    record,
                )
            if (
                gate is None
                or not isinstance(gate.payload, GatePayload)
                or positions[gate.record_id] >= positions[record.record_id]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "gate decision does not reference an earlier gate record",
                    record,
                )
            if gate.payload.decision not in {"approved", "rejected"}:
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "gate decision references a non-final gate record",
                    record,
                )
            if gate.fingerprint.sha256 != record.fingerprint.sha256:
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "gate decision fingerprint differs from its gate record",
                    record,
                )
            binding = (payload.work_unit_id, payload.gate_record_id)
            if binding in bound_gate_decisions:
                _fail(
                    ReplayDiagnosticCode.RECORD_DUPLICATE,
                    "gate decision binding is duplicated",
                    record,
                )
            bound_gate_decisions.add(binding)

    validation_content_records = tuple(
        record for record in chain
        if isinstance(record.payload, ValidationContentPayload)
    )
    attestation_records = tuple(
        record for record in chain
        if isinstance(record.payload, ValidationAttestationPayload)
    )
    content_by_result = {
        record.payload.result_record_id: record
        for record in validation_content_records
    }
    if len(content_by_result) != len(validation_content_records):
        _fail(
            ReplayDiagnosticCode.RECORD_DUPLICATE,
            "validation content result binding is duplicated",
        )
    for attestation_record in attestation_records:
        attestation = attestation_record.payload
        content_record = content_by_result.get(attestation_record.record_id)
        if content_record is None:
            if not require_content_authority:
                continue
            _fail(
                ReplayDiagnosticCode.RECORD_MISSING,
                "validation attestation has no authoritative content record",
                attestation_record,
            )
        content = content_record.payload
        assert isinstance(content, ValidationContentPayload)
        if (
            positions[content_record.record_id] >= positions[attestation_record.record_id]
            or content_record.logical_id
            != f"validation-content-{content.attestation_id}"
            or content.attestation_id != attestation_record.logical_id
            or attestation.content_record_id != content_record.record_id
            or content.output_digest != attestation.output_digest
            or content_record.fingerprint != attestation_record.fingerprint
            or len(content.outputs) != len(attestation.results)
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                "validation content differs from its attestation binding",
                attestation_record,
            )
        for output, result in zip(content.outputs, attestation.results, strict=True):
            expected_outcome = (
                "fail" if output.digest_outcome == "timeout"
                else "unavailable"
                if output.digest_outcome in {"missing", "unavailable"}
                else output.digest_outcome
            )
            if (
                output.command != result.command
                or expected_outcome != result.outcome
                or output.exit_code != result.exit_code
                or output.compact_output.sha256 != result.output_sha256
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "validation result differs from its exact content",
                    attestation_record,
                )
    bound_attestation_ids = {record.record_id for record in attestation_records}
    for content_record in validation_content_records:
        if content_record.payload.result_record_id not in bound_attestation_ids:
            if positions[content_record.record_id] == len(chain) - 1:
                continue
            _fail(
                ReplayDiagnosticCode.RECORD_MISSING,
                "validation content has no bound attestation result",
                content_record,
            )

    provider_content_records = tuple(
        record for record in chain
        if isinstance(record.payload, ProviderContentPayload)
    )
    provider_decisions = tuple(
        record for record in chain
        if isinstance(record.payload, (AgentResultPayload, ReviewPayload))
    )
    bound_provider_records: set[str] = set()
    for decision_record in provider_decisions:
        decision = decision_record.payload
        request_id = decision.request_id
        response_sha256 = decision.response_sha256
        if request_id is None or response_sha256 is None:
            _fail(
                ReplayDiagnosticCode.RECORD_MISSING,
                "native decision lacks its provider-content binding",
                decision_record,
            )
        role = decision.role if isinstance(decision, AgentResultPayload) else decision.reviewer
        decision_unit = next(
            (
                candidate.payload
                for candidate in reversed(chain[:positions[decision_record.record_id]])
                if isinstance(
                    candidate.payload,
                    (WorkUnitPayload, CorrectionWorkUnitPayload),
                )
                and candidate.logical_id == f"work-unit-{decision.work_unit_id}"
            ),
            None,
        )
        if decision_unit is not None:
            decision_round = decision_unit.round_number
        else:
            round_suffix = decision_record.logical_id.rsplit("-", 1)[-1]
            if not round_suffix.isdigit():
                if not require_content_authority:
                    continue
                _fail(
                    ReplayDiagnosticCode.RECORD_MISSING,
                    "native decision has no work-unit or logical round binding",
                    decision_record,
                )
            decision_round = int(round_suffix)
        candidates = tuple(
            content_record
            for content_record in provider_content_records
            if positions[content_record.record_id] < positions[decision_record.record_id]
            and content_record.payload.role is role
            and content_record.payload.work_unit_id == decision.work_unit_id
            and content_record.payload.round_number == decision_round
            and content_record.payload.request_id == request_id
            and content_record.payload.response_sha256 == response_sha256
            and content_record.fingerprint == decision_record.fingerprint
        )
        if len(candidates) != 1:
            if not require_content_authority and not candidates:
                continue
            _fail(
                ReplayDiagnosticCode.RECORD_MISSING,
                "native decision has no unique earlier provider-content record",
                decision_record,
            )
        content_record = candidates[0]
        content = content_record.payload
        latest_transition = next(
            (
                candidate.payload
                for candidate in reversed(chain[:positions[decision_record.record_id]])
                if isinstance(candidate.payload, WorkflowTransitionPayload)
                and candidate.payload.work_unit_id == decision.work_unit_id
            ),
            None,
        )
        expected_kind = (
            "review_result" if isinstance(decision, ReviewPayload)
            else "final_report"
            if decision.outcome == "ready"
            and content.operation.endswith("_final_review")
            else "agent_result"
        )
        if (
            (
                latest_transition is not None
                and latest_transition.step != content.operation
            )
            or content.content_kind != expected_kind
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                "provider content differs from its active operation or content kind",
                content_record,
            )
        bound_provider_records.add(content_record.record_id)
    for content_record in provider_content_records:
        if content_record.record_id not in bound_provider_records:
            if positions[content_record.record_id] == len(chain) - 1:
                continue
            _fail(
                ReplayDiagnosticCode.RECORD_MISSING,
                "provider content has no bound native decision",
                content_record,
            )

    for packet_record in (
        record for record in chain if isinstance(record.payload, ReviewPacketPayload)
    ):
        packet = packet_record.payload
        if (
            packet_record.logical_id
            != f"review-packet-{packet.work_unit_id}-{packet.fingerprint[:12]}"
            or packet_record.fingerprint.sha256 != packet.fingerprint
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                "review packet record identity differs from its content binding",
                packet_record,
            )
    work_units: dict[str, ArtifactRecord] = {}
    latest_work_units: dict[str, ArtifactRecord] = {}
    for record in chain:
        if isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload)) and (
            record.logical_id.startswith("work-unit-")
        ):
            work_unit_id = record.logical_id.removeprefix("work-unit-")
            prior = latest_work_units.get(work_unit_id)
            if prior is not None:
                prior_payload = prior.payload
                payload = record.payload
                assert isinstance(
                    prior_payload, (WorkUnitPayload, CorrectionWorkUnitPayload)
                )
                if (
                    type(payload) is not type(prior_payload)
                    or payload.slice_id != prior_payload.slice_id
                    or payload.round_number < prior_payload.round_number
                    or not set(prior_payload.paths).issubset(payload.paths)
                    or (
                        payload.paths != prior_payload.paths
                        and payload.round_number != prior_payload.round_number
                    )
                ):
                    _fail(
                        ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                        "work-unit revision changes its type or slice, rewinds its round, "
                        "or does not extend scope within the same round",
                        record,
                    )
            latest_work_units[work_unit_id] = record
            # The first revision establishes authority for this logical work
            # unit.  Later revisions must not move the global implementation
            # boundary forward or make intervening activity look like a
            # forward reference.
            work_units.setdefault(work_unit_id, record)
    first_work_unit_position = min(
        (positions[record.record_id] for record in work_units.values()),
        default=None,
    )
    import_records = [
        record for record in chain
        if isinstance(record.payload, FindingHandoffImportPayload)
    ]
    if len(import_records) > 1:
        _fail(
            ReplayDiagnosticCode.RECORD_DUPLICATE,
            "a run may contain only one finding handoff import",
            import_records[-1],
        )
    for record in chain:
        payload = record.payload
        if isinstance(payload, FindingHandoffExportPayload):
            if (
                payload.source_run_id != record.run_id
                or not record.predecessor_ids
                or payload.source_head_record_id != record.predecessor_ids[0]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "finding export source run or pre-export head differs",
                    record,
                )
            review = records_by_id.get(payload.approval_review_record_id)
            if (
                review is None
                or not isinstance(review.payload, ReviewPayload)
                or review.payload.verdict != "approved"
                or positions[review.record_id] >= positions[record.record_id]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "finding export approval review is not present",
                    record,
                )
            source_records = tuple(
                records_by_id.get(record_id)
                for record_id in payload.finding_transition_record_ids
            )
            if any(
                source is None
                or not isinstance(source.payload, FindingTransitionPayload)
                or positions[source.record_id] >= positions[record.record_id]
                for source in source_records
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "finding export transition sequence is not present",
                    record,
                )
            actual = tuple(
                ImportedFindingTransition(source.record_id, source.payload)
                for source in source_records
                if source is not None and isinstance(source.payload, FindingTransitionPayload)
            )
            ordered_ids = tuple(
                source.record_id for source in chain[:positions[record.record_id]]
                if isinstance(source.payload, FindingTransitionPayload)
            )
            if (
                payload.finding_transition_record_ids != ordered_ids
                or finding_transition_sequence_sha256(actual)
                != payload.finding_transitions_sha256
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "finding export transition order or digest differs",
                    record,
                )
            if not any(
                isinstance(candidate.payload, PlanPayload)
                and candidate.payload.approved_plan_commit == payload.approved_plan_commit
                for candidate in chain[:positions[record.record_id]]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "finding export approved plan commit is not present",
                    record,
                )
        elif isinstance(payload, FindingHandoffImportPayload):
            if payload.target_run_id != record.run_id:
                _fail(
                    ReplayDiagnosticCode.RECORD_RUN_MISMATCH,
                    "finding import target run differs",
                    record,
                )
            if payload.source_run_id == record.run_id:
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "finding import must retain foreign provenance",
                    record,
                )
            # An import is atomic authority: validate its entire embedded
            # lifecycle now, not only when a later consumer asks for findings.
            reduce_findings(_result(record.run_id, (record,)))
        if isinstance(payload, WorkUnitPayload) and payload.finding_import_record_id is not None:
            imported = records_by_id.get(payload.finding_import_record_id)
            if (
                imported is None
                or not isinstance(imported.payload, FindingHandoffImportPayload)
                or positions[imported.record_id] >= positions[record.record_id]
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "work unit finding import is not present",
                    record,
                )
            # The import establishes the initial ledger, not an immutable view
            # of every later round.  Derive the expected open set from the
            # complete authoritative prefix so reviewer transitions written
            # before a new work-unit revision are reflected without weakening
            # the import provenance binding.
            finding_prefix = tuple(
                candidate
                for candidate in chain[:positions[record.record_id]]
                if isinstance(
                    candidate.payload,
                    (FindingHandoffImportPayload, FindingTransitionPayload),
                )
            )
            expected_open = reduce_findings(
                _result(record.run_id, finding_prefix)
            ).open_set.finding_ids
            if payload.open_finding_ids != expected_open:
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "work unit open findings differ from its authoritative finding prefix",
                    record,
                )
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

    effects: dict[str, list[ArtifactRecord]] = {}
    for record in chain:
        if isinstance(record.payload, SideEffectPayload):
            effects.setdefault(record.payload.effect_key, []).append(record)
    for effect_key, records in effects.items():
        if len(records) not in {1, 2}:
            _fail(
                ReplayDiagnosticCode.RECORD_DUPLICATE,
                "side effect has too many phases",
                records[-1],
            )
        intent = records[0]
        intent_payload = intent.payload
        assert isinstance(intent_payload, SideEffectPayload)
        if intent.revision != 1 or intent_payload.phase != "intent":
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                "side effect must begin with revision 1 intent",
                intent,
            )
        expected_logical = f"side-effect-{hashlib.sha256(effect_key.encode('utf-8')).hexdigest()[:32]}"
        if intent.logical_id != expected_logical:
            _fail(
                ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                "side effect logical identity differs from its key",
                intent,
            )
        if len(records) == 2:
            result = records[1]
            result_payload = result.payload
            assert isinstance(result_payload, SideEffectPayload)
            if result.revision != 2 or result_payload.phase != "result":
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "side effect result must be revision 2 after intent",
                    result,
                )
            if (
                result.logical_id != intent.logical_id
                or result.fingerprint != intent.fingerprint
                or result_payload.effect_key != intent_payload.effect_key
                or result_payload.effect_class != intent_payload.effect_class
                or result_payload.work_unit_id != intent_payload.work_unit_id
                or result_payload.operation != intent_payload.operation
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "side effect result changed its immutable intent binding",
                    result,
                )


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


def _result(
    expected_run_id: str,
    records: tuple[ArtifactRecord, ...],
    *,
    reference_records: tuple[ArtifactRecord, ...] | None = None,
) -> ArtifactReplayResult:
    accepted_references = records if reference_records is None else reference_records
    facts = _semantic_facts(records)
    documents = tuple(fact.to_document() for fact in facts)
    run_identity = next(
        (
            record.payload
            for record in records
            if isinstance(record.payload, RunIdentityPayload)
        ),
        None,
    )
    run_profile = next(
        (
            record.payload
            for record in records
            if isinstance(record.payload, RunProfilePayload)
        ),
        None,
    )
    workflow_cursor: ReplayedWorkflowCursor | None = None
    slice_statuses: dict[str, str] = {}
    work_unit_states: dict[str, ReplayedWorkUnitState] = {}
    workflow_policies: dict[str, WorkflowPolicyPayload] = {}
    slice_boundaries: dict[str, SliceBoundaryPayload] = {}
    gate_transitions: dict[str, GateTransitionPayload] = {}
    gate_decisions: list[ReplayedGateDecision] = []
    invocation_failures: list[InvocationFailurePayload] = []
    validation_contents: list[ValidationContentPayload] = []
    provider_contents: list[ProviderContentPayload] = []
    review_packets: list[ReviewPacketPayload] = []
    side_effects: list[ReplayedSideEffect] = []
    side_effect_indexes: dict[str, int] = {}
    for sequence, record in enumerate(records, start=1):
        payload = record.payload
        if isinstance(payload, WorkflowTransitionPayload):
            slice_statuses[payload.slice_id] = payload.slice_status
            if payload.work_unit_id is not None:
                assert payload.step is not None
                assert payload.work_unit_status is not None
                workflow_cursor = ReplayedWorkflowCursor(
                    payload.slice_id, payload.work_unit_id, payload.step
                )
                work_unit_states[payload.work_unit_id] = ReplayedWorkUnitState(
                    payload.work_unit_id,
                    payload.slice_id,
                    payload.work_unit_status,
                    payload.step,
                )
        elif isinstance(payload, WorkflowPolicyPayload):
            workflow_policies[payload.work_unit_id] = payload
        elif isinstance(payload, SliceBoundaryPayload):
            slice_boundaries[payload.slice_id] = payload
        elif isinstance(payload, GateTransitionPayload):
            gate_transitions[payload.work_unit_id] = payload
        elif isinstance(payload, GateDecisionPayload):
            gate_record = next(
                candidate
                for candidate in accepted_references
                if candidate.record_id == payload.gate_record_id
            )
            gate_payload = gate_record.payload
            assert isinstance(gate_payload, GatePayload)
            gate_decisions.append(
                ReplayedGateDecision(
                    work_unit_id=payload.work_unit_id,
                    approved=gate_payload.decision == "approved",
                    reason=gate_payload.gate_kind.replace("-", "_"),
                    fingerprint=gate_record.fingerprint.sha256,
                    paths=payload.paths,
                    rationale=gate_payload.rationale,
                    resume_step=payload.resume_step,
                    authority=gate_payload.authority,
                    gate_created_at=gate_record.created_at,
                    gate_record_id=gate_record.record_id,
                    decision_record_id=record.record_id,
                )
            )
        elif isinstance(payload, InvocationFailurePayload):
            invocation_failures.append(payload)
        elif isinstance(payload, ValidationContentPayload):
            validation_contents.append(payload)
        elif isinstance(payload, ProviderContentPayload):
            provider_contents.append(payload)
        elif isinstance(payload, ReviewPacketPayload):
            review_packets.append(payload)
        elif isinstance(payload, SideEffectPayload) and payload.phase == "intent":
            side_effect_indexes[payload.effect_key] = len(side_effects)
            side_effects.append(
                ReplayedSideEffect(
                    payload.effect_key,
                    payload.effect_class,
                    payload.work_unit_id,
                    payload.operation,
                    None,
                    record.record_id,
                    None,
                    None,
                )
            )
        elif isinstance(payload, SideEffectPayload):
            index = side_effect_indexes.get(payload.effect_key)
            if index is not None:
                side_effects[index] = replace(
                    side_effects[index],
                    result=payload.result,
                    result_record_id=record.record_id,
                    result_sequence=sequence,
                )

    def identifier_order(value: str) -> tuple[int, int | str]:
        return (0, int(value)) if value.isdigit() else (1, value)

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
        run_identity=run_identity,
        run_profile=run_profile,
        workflow_cursor=workflow_cursor,
        slice_statuses=tuple(sorted(slice_statuses.items(), key=lambda item: identifier_order(item[0]))),
        work_unit_states=tuple(
            work_unit_states[key]
            for key in sorted(work_unit_states, key=identifier_order)
        ),
        workflow_policies=tuple(
            workflow_policies[key]
            for key in sorted(workflow_policies, key=identifier_order)
        ),
        slice_boundaries=tuple(
            slice_boundaries[key]
            for key in sorted(slice_boundaries, key=identifier_order)
        ),
        gate_transitions=tuple(
            gate_transitions[key]
            for key in sorted(gate_transitions, key=identifier_order)
        ),
        gate_decisions=tuple(gate_decisions),
        invocation_failures=tuple(invocation_failures),
        validation_contents=tuple(validation_contents),
        provider_contents=tuple(provider_contents),
        review_packets=tuple(review_packets),
        work_unit_reviewers=project_work_unit_reviewers(records),
        side_effects=tuple(side_effects),
        _reference_records=accepted_references,
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
    "ReplayedWorkflowCursor",
    "ReplayedWorkUnitState",
    "ReplayedSideEffect",
    "ReplayedGateDecision",
    "project_work_unit_reviewers",
    "replay_artifacts",
]
