"""Pure, deterministic reduction of one authoritative structured record chain.

The store remains responsible for decoding bytes and validating the physical
append-only chain. Models and loaders bind and validate the installed reducer version
before replay. This module has no filesystem or clock access: it
enforces cross-record relationships, rejects non-canonical histories, and
returns the immutable view used to rebuild every disposable state and audit
projection.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
import hashlib
import json
import re
from typing import Callable, Sequence

from finding_order import replay_compatible_finding_ids
from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    artifact_payload_document, family_binding_document,
    BranchDiscoveryCompletedPayload,
    BranchDiscoveryHandoffExportPayload,
    BranchDiscoveryHandoffImportPayload,
    BindingPayload,
    DiagnosticPayload,
    FinalReviewPreflightPayload,
    FindingSeverity,
    FindingTransitionPayload,
    FindingSnapshotItem,
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    GateDecisionPayload,
    GatePayload,
    GateTransitionPayload,
    ImportedFindingTransition,
    InvocationFailurePayload,
    finding_transition_sequence_sha256,
    flatten_finding_transition_history,
    ProviderInputMeasurementPayload,
    ProviderAttemptPayload,
    PlanPayload,
    TaskPayload,
    RecordType,
    Role,
    STATE_PROJECTION_REDUCER_VERSION,
    RunIdentityPayload,
    RunProfilePayload,
    QuotaPausePayload,
    SideEffectPayload,
    SliceSpec,
    SliceBoundaryPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    ResumeCheckPayload,
    ReviewPayload,
    ReviewAnchorPayload,
    ReviewValidationBindingPayload,
    BlobReference,
    ProviderContentPayload,
    ReviewPacketPayload,
    ValidationContentPayload,
    ValidationAttestationPayload,
    WorkflowCompletionPayload,
    WorkflowEventPayload,
    WorkUnitPayload,
    CorrectionWorkUnitPayload,
    TransientRetryPayload,
    canonical_json,
)
from finding_order import sorted_finding_ids
from finding_signature import finding_record_signature
from contracts import (
    AgentRole,
    AnchorRecord,
    ContractResult,
    FindingRecord,
    ReviewEvidence,
    StopRequest,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
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
class ReplayedWorkflowEvent:
    record_id: str
    event_kind: str
    work_unit_id: str | None
    slice_id: str
    round_number: int | None
    record_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReplayedWorkflowState:
    """Canonical, cache-free projection of every WorkflowState fact group."""

    state: object
    canonical_document: bytes

    def to_document(self) -> dict[str, object]:
        document = json.loads(self.canonical_document)
        assert isinstance(document, dict)
        return document


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


@dataclass(frozen=True, slots=True)
class ReplayedReviewContract:
    record_id: str
    work_unit_id: str
    round_number: int
    result: ContractResult


def project_work_unit_reviewers(
    records: Sequence[ArtifactRecord],
) -> tuple[tuple[str, Role | None], ...]:
    """Project reviewer facts without introducing a reviewer record."""
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
    workflow_events: tuple[ReplayedWorkflowEvent, ...] = ()
    pending_review_record_id: str | None = None
    pending_workflow_event_record_id: str | None = None
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
    review_anchors: tuple[ReviewAnchorPayload, ...] = ()
    review_validation_bindings: tuple[ReviewValidationBindingPayload, ...] = ()
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
            pending_review_record_id=(
                self.pending_review_record_id
                if self.pending_review_record_id is not None
                and any(
                    record.record_id == self.pending_review_record_id
                    for record in selected
                )
                else None
            ),
        )


def replay_artifacts(
    records: Sequence[ArtifactRecord],
    expected_run_id: str,
    *,
    allow_empty: bool = False,
    require_content_authority: bool | None = None,
    require_review_authority: bool | None = None,
    allow_incomplete_review_tail: bool = False,
    allow_finding_import_bootstrap: bool = False,
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
        if isinstance(record.payload, CorrectionWorkUnitPayload):
            _fail(
                ReplayDiagnosticCode.UNSUPPORTED_PROTOCOL,
                "legacy correction_work_unit records cannot be written under the "
                f"installed reducer; inspect historical chains with "
                "scripts/verify_legacy_chain.py",
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
            RecordType.BRANCH_DISCOVERY_HANDOFF_EXPORT,
            RecordType.BRANCH_DISCOVERY_HANDOFF_IMPORT,
            RecordType.BRANCH_DISCOVERY_COMPLETED,
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

    has_run_identity = RecordType.RUN_IDENTITY in singleton_types
    has_run_profile = RecordType.RUN_PROFILE in singleton_types
    finding_import_bootstrap = (
        allow_finding_import_bootstrap
        and all(
            isinstance(
                record.payload,
                (FindingHandoffImportPayload, BranchDiscoveryHandoffImportPayload),
            )
            for record in chain
        )
    )
    if (not has_run_identity or not has_run_profile) and not finding_import_bootstrap:
        missing = []
        if not has_run_identity:
            missing.append("run identity")
        if not has_run_profile:
            missing.append("run profile")
        _fail(
            ReplayDiagnosticCode.RECORD_MISSING,
            "record chain requires exactly one run identity and run profile; missing "
            + " and ".join(missing),
        )

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
    strict_reviews = (
        require_review_authority
        if require_review_authority is not None
        else any(isinstance(record.payload, RunIdentityPayload) for record in chain)
        or any(
            isinstance(
                record.payload,
                (ReviewAnchorPayload, ReviewValidationBindingPayload),
            )
            for record in chain
        )
    )
    pending_review_record_id = _validate_payload_references(
        chain,
        seen_ids,
        require_content_authority=strict_content,
        require_review_authority=strict_reviews,
        allow_incomplete_review_tail=allow_incomplete_review_tail,
    )
    return _result(
        expected_run_id,
        chain,
        pending_review_record_id=pending_review_record_id,
    )


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


@dataclass(frozen=True, slots=True)
class _ProjectionTransitionIndex:
    positions: dict[str, int]
    transition_history: tuple[ArtifactRecord, ...]
    latest_workflow_positions: dict[str, int]
    latest_gate_positions: dict[str, int]
    failure_statuses: dict[str, tuple[str, str, dict[str, object]]]
    failure_slice_statuses: dict[str, str]


@dataclass(frozen=True, slots=True)
class _WorkUnitProjectionIndex:
    definitions: dict[str, ArtifactRecord]
    policies: dict[str, WorkflowPolicyPayload]
    gates: dict[str, GateTransitionPayload]
    reviewers: dict[str, Role | None]
    latest_reviews: dict[str, ArtifactRecord]


def _ensure_projection_event_prefix(
    replay: ArtifactReplayResult,
    records: tuple[ArtifactRecord, ...],
) -> None:
    if not records:
        _fail(ReplayDiagnosticCode.RECORD_MISSING, "cannot project an empty workflow")
    expected_events = {
        record.record_id: kind
        for record in records
        for kind in (
            "run"
            if isinstance(record.payload, RunIdentityPayload)
            else "transition"
            if isinstance(record.payload, WorkflowTransitionPayload)
            else "validation"
            if isinstance(record.payload, ValidationAttestationPayload)
            else "review"
            if isinstance(record.payload, (ReviewPayload, BranchDiscoveryCompletedPayload))
            else None,
        )
        if kind is not None
        and record.record_id != replay.pending_review_record_id
    }
    actual_events: dict[str, str] = {}
    for event in replay.workflow_events:
        referenced = event.record_refs[0]
        if referenced in actual_events:
            related = next(
                record for record in records if record.record_id == event.record_id
            )
            _fail(
                ReplayDiagnosticCode.RECORD_DUPLICATE,
                "workflow state projection found a duplicate event reference",
                related,
            )
        actual_events[referenced] = event.event_kind
    if actual_events != expected_events:
        differing = next(iter(set(actual_events) ^ set(expected_events)), None)
        related = next(
            (record for record in records if record.record_id == differing), None
        )
        _fail(
            ReplayDiagnosticCode.RECORD_MISSING,
            "workflow state projection requires a complete workflow event prefix",
            related,
        )


def _index_projection_transitions(
    replay: ArtifactReplayResult,
    records: tuple[ArtifactRecord, ...],
) -> _ProjectionTransitionIndex:
    transition_history = tuple(
        record for record in records
        if isinstance(record.payload, WorkflowTransitionPayload)
    )
    if replay.workflow_cursor is None or not transition_history:
        _fail(
            ReplayDiagnosticCode.RECORD_MISSING,
            "workflow state projection requires a current cursor",
        )
    positions = {record.record_id: index for index, record in enumerate(records)}
    latest_transition_positions: dict[str, int] = {}
    latest_workflow_positions: dict[str, int] = {}
    latest_gate_positions: dict[str, int] = {}
    latest_slice_positions: dict[str, int] = {}
    for record in records:
        payload = record.payload
        if isinstance(payload, WorkflowTransitionPayload):
            if payload.work_unit_id is not None:
                position = positions[record.record_id]
                latest_transition_positions[payload.work_unit_id] = position
                latest_workflow_positions[payload.work_unit_id] = position
                latest_slice_positions[payload.slice_id] = position
        elif isinstance(payload, GateTransitionPayload):
            position = positions[record.record_id]
            latest_transition_positions[payload.work_unit_id] = position
            latest_gate_positions[payload.work_unit_id] = position
    failure_statuses: dict[str, tuple[str, str, dict[str, object]]] = {}
    failure_positions: dict[str, int] = {}
    for record in records:
        payload = record.payload
        if not isinstance(payload, InvocationFailurePayload):
            continue
        if positions[record.record_id] <= latest_transition_positions.get(
            payload.work_unit_id, -1
        ):
            continue
        automatic_quota = payload.automatic_resume and payload.failure_kind == "quota"
        automatic_retry = payload.automatic_resume and payload.failure_kind in {"network", "timeout", "output"}
        status = (
            "waiting_for_quota"
            if automatic_quota
            else "waiting_for_retry"
            if automatic_retry
            else "awaiting_resume"
        )
        reset = payload.resume_at_utc or "manual resume required"
        failure_statuses[payload.work_unit_id] = (
            payload.slice_id,
            status,
            {
                "status": status,
                "reason": (
                    "quota" if payload.failure_kind == "quota" else "instance_failure"
                ),
                "detail": (
                    f"role={payload.role.value} step={payload.step} "
                    f"invocation={payload.invocation_id} kind={payload.failure_kind} "
                    f"resume={reset} auto={str(payload.automatic_resume).lower()} "
                    f"continuations={payload.auto_resume_count} "
                    f"provider={payload.provider_text}"
                ),
                "fingerprint": payload.diff_fingerprint,
                "paths": (),
                "resume_step": payload.step,
            },
        )
        failure_positions[payload.work_unit_id] = positions[record.record_id]
    failure_slice_statuses = {
        slice_id: status
        for work_unit_id, (slice_id, status, _gate) in failure_statuses.items()
        if failure_positions[work_unit_id]
        > latest_slice_positions.get(slice_id, -1)
    }
    return _ProjectionTransitionIndex(
        positions,
        transition_history,
        latest_workflow_positions,
        latest_gate_positions,
        failure_statuses,
        failure_slice_statuses,
    )


def _project_slice_documents(
    replay: ArtifactReplayResult,
    records: tuple[ArtifactRecord, ...],
    identity: RunIdentityPayload,
    planned_slices: Sequence[SliceSpec],
    transitions: _ProjectionTransitionIndex,
) -> list[dict[str, object]]:
    slice_ids = {slice_id for slice_id, _status in replay.slice_statuses}
    if planned_slices:
        slice_ids.update(item.slice_id for item in planned_slices)
    boundaries = {item.slice_id: item for item in replay.slice_boundaries}
    commit_refs: dict[str, str] = {}
    for record in records:
        payload = record.payload
        if not isinstance(payload, BindingPayload) or payload.binding_kind != "commit":
            continue
        match = re.fullmatch(r"commit-([A-Za-z0-9._:]+)(?:-.+)?", record.logical_id)
        if match is not None:
            commit_refs[match.group(1)] = payload.target
    slices: list[dict[str, object]] = []
    prior_commit: str | None = None
    for slice_id in sorted(slice_ids, key=_identifier_order):
        boundary = boundaries.get(slice_id)
        planned = next(
            (item for item in planned_slices if item.slice_id == slice_id),
            None,
        )
        scope_groups = () if boundary is None else boundary.scope_change_groups
        scope_paths = tuple(path for group in scope_groups for path in group)
        start_commit = (
            boundary.start_commit
            if boundary is not None
            else identity.first_slice_start_commit
            if slice_id == "1"
            else prior_commit
        )
        commit_ref = identity.first_slice_start_commit if identity.execution_mode == "BRANCH_DISCOVERY" and slice_id == "1" else commit_refs.get(slice_id)
        slices.append(
            {
                "slice_id": int(slice_id),
                "status": transitions.failure_slice_statuses.get(
                    slice_id,
                    dict(replay.slice_statuses).get(slice_id, "pending"),
                ),
                "start_commit": start_commit,
                # The approved plan allowlist is not the executed Slice boundary.
                # Pending Slices therefore stay unbound until a SliceBoundary
                # record supplies their scope and fingerprint.
                "scope_paths": scope_paths,
                "scope_change_groups": scope_groups,
                "start_fingerprint": None if boundary is None else boundary.start_fingerprint,
                "commit_ref": commit_ref,
            }
        )
        if commit_ref is not None:
            prior_commit = commit_ref
    return slices


def _index_work_unit_projection_authority(
    replay: ArtifactReplayResult,
    records: tuple[ArtifactRecord, ...],
) -> _WorkUnitProjectionIndex:
    definitions: dict[str, ArtifactRecord] = {}
    for record in records:
        if isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload)):
            definitions[record.logical_id.removeprefix("work-unit-")] = record
    latest_reviews: dict[str, ArtifactRecord] = {}
    for record in records:
        if (
            isinstance(record.payload, ReviewPayload)
            and record.record_id != replay.pending_review_record_id
        ):
            latest_reviews[record.payload.work_unit_id] = record
    return _WorkUnitProjectionIndex(
        definitions,
        {item.work_unit_id: item for item in replay.workflow_policies},
        {item.work_unit_id: item for item in replay.gate_transitions},
        dict(replay.work_unit_reviewers),
        latest_reviews,
    )


def _project_work_unit_round_and_kind(
    replay: ArtifactReplayResult,
    unit: ReplayedWorkUnitState,
    definition: object,
    transitions: _ProjectionTransitionIndex,
    records: tuple[ArtifactRecord, ...],
) -> tuple[int, str]:
    round_candidates = [
        event.round_number
        for event in replay.workflow_events
        if event.work_unit_id == unit.work_unit_id
        and event.round_number is not None
    ]
    gate_history = tuple(
        record.payload
        for record in records
        if isinstance(record.payload, GateTransitionPayload)
        and record.payload.work_unit_id == unit.work_unit_id
    )
    resumed_gate_round = 1 + sum(
        previous.gate_status != "clear" and current.gate_status == "clear"
        for previous, current in zip(gate_history, gate_history[1:])
    )
    round_candidates.append(resumed_gate_round)
    if isinstance(definition, (WorkUnitPayload, CorrectionWorkUnitPayload)):
        round_candidates.append(definition.round_number)
    round_number = max(round_candidates, default=1)
    first_step = next(
        candidate.payload.step
        for candidate in transitions.transition_history
        if candidate.payload.work_unit_id == unit.work_unit_id
    )
    if first_step == "codex_final_correction" and not isinstance(  # allowlist:provider -- canonical state-v3 step
        definition, CorrectionWorkUnitPayload
    ):
        _fail(
            ReplayDiagnosticCode.RECORD_MISSING,
            "a correction cursor requires its correction work-unit record",
            next(
                record
                for record in transitions.transition_history
                if record.payload.work_unit_id == unit.work_unit_id
            ),
        )
    kind = (
        "correction"
        if isinstance(definition, CorrectionWorkUnitPayload)
        else "plan"
        if unit.work_unit_id == "1"
        or first_step in {"codex_plan", "claude_plan_review", "codex_plan_revision"}  # allowlist:provider -- canonical state-v3 steps
        else "final_review"
        if first_step in {"codex_final_review", "claude_final_review"}  # allowlist:provider -- canonical state-v3 steps
        else "slice"
    )
    return round_number, kind


def _project_work_unit_gate(
    unit: ReplayedWorkUnitState,
    gate: GateTransitionPayload | None,
    transitions: _ProjectionTransitionIndex,
) -> tuple[str, dict[str, object]]:
    failure_projection = transitions.failure_statuses.get(unit.work_unit_id)
    gate_supersedes_transition = transitions.latest_gate_positions.get(
        unit.work_unit_id, -1
    ) > transitions.latest_workflow_positions.get(unit.work_unit_id, -1)
    projected_status = (
        failure_projection[1]
        if failure_projection is not None
        else gate.gate_status
        if (
            gate is not None
            and gate.gate_status != "clear"
            and gate_supersedes_transition
        )
        else unit.status
    )
    gate_document = (
        failure_projection[2]
        if failure_projection is not None
        else
        {
            "status": "clear",
            "reason": "none",
            "detail": None,
            "fingerprint": None,
            "paths": (),
            "resume_step": None,
        }
        if gate is None
        else {
            "status": gate.gate_status,
            "reason": gate.reason,
            "detail": gate.detail,
            "fingerprint": gate.fingerprint,
            "paths": gate.paths,
            "resume_step": gate.resume_step,
        }
    )
    return projected_status, gate_document


def _project_work_unit_open_findings(
    replay: ArtifactReplayResult,
    records: tuple[ArtifactRecord, ...],
    unit: ReplayedWorkUnitState, definition: object,
    latest_review_record: ArtifactRecord | None,
    positions: dict[str, int],
) -> tuple[str, ...]:
    from contracts import FindingClass
    from finding_reducer import project_open_set, reduce_findings
    definition_findings = (
        definition.finding_ids if isinstance(definition, CorrectionWorkUnitPayload)
        else definition.open_finding_ids if isinstance(definition, WorkUnitPayload) else ()
    )
    if latest_review_record is None:
        is_current = unit.work_unit_id == replay.workflow_cursor.work_unit_id
        if isinstance(definition, WorkUnitPayload) and is_current:
            return reduce_findings(replay).open_set.finding_ids
        return definition_findings
    review_payload = latest_review_record.payload
    assert isinstance(review_payload, ReviewPayload)
    review_prefix = replay.subset(
        records[:_review_prefix_end(records, positions, latest_review_record)]
    )
    if isinstance(definition, WorkUnitPayload):
        return reduce_findings(review_prefix).open_set.finding_ids
    reviewed_findings = reduce_findings(review_prefix).request_subset(
        finding_ids=review_payload.finding_ids
    ).findings
    return tuple(
        finding.finding_id for finding in project_open_set(reviewed_findings).findings
        if finding.finding_class is FindingClass.BLOCKER
        and finding.origin.reporter.value == review_payload.reviewer.value
    )


def _project_work_unit_document(
    replay: ArtifactReplayResult,
    records: tuple[ArtifactRecord, ...],
    unit: ReplayedWorkUnitState,
    transitions: _ProjectionTransitionIndex,
    authority: _WorkUnitProjectionIndex,
) -> dict[str, object]:
    definition_record = authority.definitions.get(unit.work_unit_id)
    definition = None if definition_record is None else definition_record.payload
    round_number, kind = _project_work_unit_round_and_kind(
        replay, unit, definition, transitions, records
    )
    policy = authority.policies.get(unit.work_unit_id)
    gate = authority.gates.get(unit.work_unit_id)
    decisions = tuple(
        asdict(item)
        for item in replay.gate_decisions
        if item.work_unit_id == unit.work_unit_id
    )
    failures = tuple(
        {
            "invocation_id": item.invocation_id,
            "idempotency_key": item.idempotency_key,
            "role": item.role.value,
            "failure_kind": item.failure_kind,
            "provider_text": item.provider_text,
            "technical_text": item.technical_text,
            "received_at": item.received_at,
            "step": item.step,
            "slice_id": int(item.slice_id),
            "work_unit_id": int(item.work_unit_id),
            "diagnostic_exit_code": item.diagnostic_exit_code,
            "process_exit_code": item.process_exit_code,
            "parse_path": item.parse_path,
            "source_timezone": item.source_timezone,
            "reset_at_utc": item.reset_at_utc,
            "resume_at_utc": item.resume_at_utc,
            "safety_margin_seconds": item.safety_margin_seconds,
            "auto_resume_count": item.auto_resume_count,
            "automatic_resume": item.automatic_resume,
            "diff_fingerprint": item.diff_fingerprint,
            **item.native_response_feedback_document,
        }
        for item in replay.invocation_failures
        if item.work_unit_id == unit.work_unit_id
    )
    projected_status, gate_document = _project_work_unit_gate(
        unit, gate, transitions
    )
    definition_findings = _project_work_unit_open_findings(
        replay,
        records,
        unit, definition,
        authority.latest_reviews.get(unit.work_unit_id),
        transitions.positions,
    )
    return {
        "work_unit_id": int(unit.work_unit_id),
        "slice_id": int(unit.slice_id),
        "kind": kind,
        "status": projected_status,
        "current_step": unit.step,
        "round_number": round_number,
        "codex_return_count": (  # allowlist:provider -- canonical state-v3 field
            0 if policy is None else policy.implementer_return_count
        ),
        "max_codex_returns": (  # allowlist:provider -- canonical state-v3 field
            4 if policy is None else policy.max_implementer_returns
        ),
        "gate": gate_document,
        "reviewer": (
            None if authority.reviewers.get(unit.work_unit_id) is None
            else authority.reviewers[unit.work_unit_id].value
        ),
        "open_findings": definition_findings,
        "completed_side_effects": replay.completed_side_effects(unit.work_unit_id),
        "gate_decisions": tuple(
            {
                "approved": item["approved"],
                "reason": item["reason"],
                "fingerprint": item["fingerprint"],
                "paths": item["paths"],
                "rationale": item["rationale"],
                "resume_step": item["resume_step"],
            }
            for item in decisions
        ),
        "active_test_fingerprint": (
            None if gate is None else gate.active_test_fingerprint
        ),
        "active_test_paths": () if gate is None else gate.active_test_paths,
        "invocation_failures": failures,
    }


def _project_work_unit_documents(
    replay: ArtifactReplayResult,
    records: tuple[ArtifactRecord, ...],
    transitions: _ProjectionTransitionIndex,
) -> list[dict[str, object]]:
    authority = _index_work_unit_projection_authority(replay, records)
    documents = []
    for unit in replay.work_unit_states:
        documents.append(
            _project_work_unit_document(
                replay,
                records,
                unit,
                transitions,
                authority,
            )
        )
    return documents


def _assemble_workflow_state_document(
    replay: ArtifactReplayResult,
    records: tuple[ArtifactRecord, ...],
    identity: RunIdentityPayload,
    profile: RunProfilePayload,
    task: TaskPayload,
    plan: PlanPayload | None,
    planned_slices: Sequence[SliceSpec],
    slices: Sequence[dict[str, object]],
    work_units: Sequence[dict[str, object]],
    import_record: ArtifactRecord | None,
    runtime_history: dict[str, dict[str, tuple[str, ...]]],
) -> dict[str, object]:
    from finding_reducer import reduce_findings, responsibility_projection_document
    responsibilities = responsibility_projection_document(reduce_findings(replay))
    return {
        **({"finding_responsibilities": responsibilities} if responsibilities else {}),
        **({"family_binding": family_binding_document(profile.family_binding)} if profile.family_binding is not None else {}),
        "version": 3,
        "run_id": replay.expected_run_id,
        "task_file": identity.task_file,
        "branch": identity.branch,
        "branch_base": identity.branch_base,
        "created_at": records[0].created_at,
        "updated_at": records[-1].created_at,
        "current_slice_id": int(replay.workflow_cursor.slice_id),
        "current_work_unit_id": int(replay.workflow_cursor.work_unit_id),
        "current_step": replay.workflow_cursor.step,
        "slices": tuple(slices),
        "work_units": tuple(work_units),
        "planned_slices": (
            tuple(
                {
                    "slice_id": int(item.slice_id),
                    "summary": item.summary,
                    "scope_paths": item.paths,
                } | _slice_acceptance_projection(item)
                for item in planned_slices
            )
        ),
        "runtime_history": runtime_history,
        "task_digest": task.assignment_sha256,
        "execution_mode": identity.execution_mode,
        "task_scope_patterns": task.scope_paths,
        "work_plan_path": (
            task.work_plan_path if plan is None else plan.work_plan_path
        ),
        "approved_plan_commit": None if plan is None else plan.approved_plan_commit,
        "finding_handoff_source_run_id": (
            None if import_record is None else import_record.payload.source_run_id
        ),
        "finding_handoff_export_record_id": (
            None if import_record is None else import_record.payload.export_record_id
        ),
        "audit_report_path": identity.audit_report_path,
        "target_branch": task.target_branch,
        "protocol_binding": {
            "mode": "structured-v2",
            "schema_version": "2",
            "claude_review_transport": "native-claude-review-v2",  # allowlist:provider -- canonical protocol binding
            "codex_result_transport": "native-codex-v2",  # allowlist:provider -- canonical protocol binding
            "codex_profile": {  # allowlist:provider -- canonical state-v3 field
                "model": profile.implementer.model,
                "effort": profile.implementer.effort,
            },
            "claude_profile": {  # allowlist:provider -- canonical state-v3 field
                "model": profile.reviewer.model,
                "effort": profile.reviewer.effort,
            },
        },
        "bootstrap_checks": tuple(
            _project_bootstrap_fact(record.payload)
            for record in records
            if isinstance(
                record.payload,
                (ProviderInputMeasurementPayload, FinalReviewPreflightPayload),
            )
        ),
    }


def project_workflow_state(replay: ArtifactReplayResult) -> ReplayedWorkflowState:
    """Project the complete semantic state from records, without filesystem I/O.

    Volatile state timestamps are normalized to the first and last record time.
    Former ``runtime_history`` content is represented by its authoritative
    record references; embedded review/validation bodies and ``events`` are not
    recreated as a second authority.
    """
    records = replay.records
    _ensure_projection_event_prefix(replay, records)
    identity = replay.run_identity
    profile = replay.run_profile
    task_record = next(
        (record for record in records if isinstance(record.payload, TaskPayload)),
        None,
    )
    if identity is None or profile is None or task_record is None:
        _fail(
            ReplayDiagnosticCode.RECORD_MISSING,
            "workflow state projection requires run identity, profile, and task",
        )
    task = task_record.payload
    assert isinstance(task, TaskPayload)
    plan_record = next(
        (record for record in reversed(records) if isinstance(record.payload, PlanPayload)),
        None,
    )
    plan = None if plan_record is None else plan_record.payload
    assert plan is None or isinstance(plan, PlanPayload)
    proposed_plan_record = next(
        (
            record
            for record in reversed(records)
            if isinstance(record.payload, AgentResultPayload)
            and record.payload.slice_plan
        ),
        None,
    )
    proposed_plan = (
        ()
        if proposed_plan_record is None
        else proposed_plan_record.payload.slice_plan
    )
    planned_slices = plan.slices if plan is not None else proposed_plan

    transitions = _index_projection_transitions(replay, records)
    slices = _project_slice_documents(
        replay,
        records,
        identity,
        planned_slices,
        transitions,
    )

    work_units = _project_work_unit_documents(replay, records, transitions)

    import_record = next(
        (
            record
            for record in records
            if isinstance(
                record.payload,
                (FindingHandoffImportPayload, BranchDiscoveryHandoffImportPayload),
            )
        ),
        None,
    )
    runtime_history = project_runtime_history_references(replay)

    document = _assemble_workflow_state_document(
        replay,
        records,
        identity,
        profile,
        task,
        plan,
        planned_slices,
        slices,
        work_units,
        import_record,
        runtime_history,
    )
    # Parsing the projection through the real state-v3 validator proves that
    # this is a complete state document rather than a look-alike audit view.
    # The import remains local so workflow_state does not acquire a replay
    # dependency in the opposite direction.
    from workflow_state import WorkflowState, WorkflowStateValidationError

    try:
        state = WorkflowState.from_dict(json.loads(canonical_json(document)))
    except WorkflowStateValidationError as exc:
        _fail(
            ReplayDiagnosticCode.RECORD_MISSING,
            f"workflow state projection is not yet a complete accepted prefix: {exc}",
            records[-1],
        )
    canonical_document = canonical_json(state.to_dict())
    return ReplayedWorkflowState(state, canonical_document)


def project_runtime_history_references(
    replay: ArtifactReplayResult,
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Project each runtime history as immutable record references only."""
    records = replay.records
    runtime_history: dict[str, dict[str, tuple[str, ...]]] = {}
    for unit in replay.work_unit_states:
        unit_id = unit.work_unit_id
        runtime_history[unit_id] = {
            "finding_record_refs": tuple(
                record.record_id
                for record in records
                if isinstance(record.payload, FindingTransitionPayload)
                and record.payload.work_unit_id == unit_id
            ),
            "review_record_refs": tuple(
                record.record_id
                for record in records
                if isinstance(record.payload, ReviewPayload)
                and record.payload.work_unit_id == unit_id
            ),
            "attestation_record_refs": tuple(
                event.record_refs[0]
                for event in replay.workflow_events
                if event.event_kind == "validation" and event.work_unit_id == unit_id
            ),
            "provider_content_record_refs": tuple(
                record.record_id
                for record in records
                if isinstance(record.payload, ProviderContentPayload)
                and record.payload.work_unit_id == unit_id
            ),
            "review_packet_record_refs": tuple(
                record.record_id
                for record in records
                if isinstance(record.payload, ReviewPacketPayload)
                and record.payload.work_unit_id == unit_id
            ),
            "workflow_event_record_refs": tuple(
                event.record_id
                for event in replay.workflow_events
                if event.work_unit_id == unit_id
            ),
        }

    return runtime_history


def _project_bootstrap_fact(
    payload: ProviderInputMeasurementPayload | FinalReviewPreflightPayload,
) -> dict[str, object]:
    """Apply the versioned state-v3 bootstrap projection without I/O."""
    decision = (
        "allowed"
        if isinstance(payload, ProviderInputMeasurementPayload) and payload.allowed
        else "denied"
        if isinstance(payload, ProviderInputMeasurementPayload)
        or payload.outcome == "denied"
        else "passed"
    )
    return {
        "check_kind": payload.record_type.value,
        "transition_fingerprint": payload.transition_fingerprint,
        "provider": payload.provider.value,
        "role": payload.role.value,
        "operation": payload.operation,
        "work_unit_id": int(payload.work_unit_id),
        "semantic_digest": hashlib.sha256(canonical_json(payload)).hexdigest(),
        "decision": decision,
        "error_code": (
            payload.record_type.value.removesuffix("_measurement")
            .replace("_", "-")
            .upper()
            + "-BUDGET"
            if isinstance(payload, ProviderInputMeasurementPayload) and not payload.allowed
            else payload.error_code
            if isinstance(payload, FinalReviewPreflightPayload)
            else None
        ),
    }


def _identifier_order(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def project_review_contracts(
    replay: ArtifactReplayResult,
    read_blob: Callable[[BlobReference], bytes],
) -> tuple[ReplayedReviewContract, ...]:
    """Rebuild reviewer domain results from the accepted R7/R8 prefix alone."""
    chain = replay.records
    positions = {record.record_id: index for index, record in enumerate(chain)}
    records_by_id = {record.record_id: record for record in chain}
    anchors_by_review = {
        record.payload.review_record_id: record
        for record in chain
        if isinstance(record.payload, ReviewAnchorPayload)
    }
    validations_by_review = {
        record.payload.review_record_id: record
        for record in chain
        if isinstance(record.payload, ReviewValidationBindingPayload)
    }
    projected: list[ReplayedReviewContract] = []
    for review_record in chain:
        payload = review_record.payload
        if not isinstance(payload, ReviewPayload):
            continue
        if review_record.record_id == replay.pending_review_record_id:
            continue
        anchor_record = anchors_by_review.get(review_record.record_id)
        validation_record = validations_by_review.get(review_record.record_id)
        if anchor_record is None or validation_record is None:
            _fail(
                ReplayDiagnosticCode.RECORD_MISSING,
                "review contract projection lacks its R7 component records",
                review_record,
            )
        assert isinstance(anchor_record.payload, ReviewAnchorPayload)
        assert isinstance(
            validation_record.payload, ReviewValidationBindingPayload
        )
        attestation_record = records_by_id.get(
            validation_record.payload.attestation_record_id
        )
        if attestation_record is None or not isinstance(
            attestation_record.payload, ValidationAttestationPayload
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                "review contract projection cannot resolve its validation attestation",
                validation_record,
            )
        attestation = _project_validation_attestation(
            replay,
            attestation_record,
            read_blob,
        )

        prefix_end = _review_prefix_end(chain, positions, review_record)
        from finding_reducer import reduce_findings

        prefix_replay = replay.subset(chain[:prefix_end])
        try:
            findings = reduce_findings(prefix_replay).request_subset(
                finding_ids=payload.finding_ids
            ).findings
        except ArtifactReplayError:
            raise
        except ValueError:
            _fail(
                ReplayDiagnosticCode.RECORD_MISSING,
                "review finding transition set is incomplete",
                review_record,
            )
        if tuple(item.finding_id for item in findings) != replay_compatible_finding_ids(payload.finding_ids):
            _fail(
                ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                "review finding snapshot differs from its transition prefix",
                review_record,
            )
        if payload.evidence is not None:
            _fail(
                ReplayDiagnosticCode.UNSUPPORTED_PROTOCOL,
                "opaque legacy review evidence cannot project the R7 contract",
                review_record,
            )
        evidence = (
            None
            if payload.review_evidence is None
            else ReviewEvidence(
                payload.review_evidence.dimensions,
                payload.review_evidence.largest_residual_risk,
                payload.review_evidence.break_condition,
            )
        )
        stop_request = (
            None
            if payload.stop_request is None
            else StopRequest(
                payload.stop_request.rule_id,
                payload.stop_request.rationale,
                payload.stop_request.remediation_paths,
            )
        )
        anchors = tuple(
            AnchorRecord(
                anchor.anchor_id,
                anchor.origin,
                anchor.input_fixture,
                anchor.expected,
                anchor.tolerance,
            )
            for anchor in anchor_record.payload.anchors
        )
        round_suffix = review_record.logical_id.rsplit("-", 1)[-1]
        if not round_suffix.isdigit() or int(round_suffix) < 1:
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                "review contract has no canonical logical round",
                review_record,
            )
        projected.append(
            ReplayedReviewContract(
                record_id=review_record.record_id,
                work_unit_id=payload.work_unit_id,
                round_number=int(round_suffix),
                result=ContractResult(
                    reviewer=AgentRole(payload.reviewer.value),
                    approval=(
                        None
                        if payload.verdict == "stop"
                        else payload.verdict == "approved"
                    ),
                    stopped=payload.verdict == "stop",
                    stop_request=stop_request,
                    validation=attestation,
                    test_files=payload.test_files,
                    pre_mortem=payload.pre_mortem,
                    evidence=evidence,
                    findings=findings,
                    anchors=anchors,
                    red_state_followup_slice=payload.red_state_followup_slice,
                ),
            )
        )
    return tuple(projected)

def project_validation_attestations(
    replay: ArtifactReplayResult,
    read_blob: Callable[[BlobReference], bytes],
) -> tuple[tuple[str, ValidationAttestation], ...]:
    """Project validation domain objects keyed by their authoritative record ID."""
    return tuple(
        (
            record.record_id,
            _project_validation_attestation(replay, record, read_blob),
        )
        for record in replay.records
        if isinstance(record.payload, ValidationAttestationPayload)
    )

def _review_prefix_end(
    chain: tuple[ArtifactRecord, ...],
    positions: dict[str, int],
    review_record: ArtifactRecord,
) -> int:
    """Return the exclusive end of one contiguous review transaction prefix."""
    payload = review_record.payload
    assert isinstance(payload, ReviewPayload)
    prefix_end = positions[review_record.record_id] + 1
    while prefix_end < len(chain):
        candidate = chain[prefix_end].payload
        if (
            isinstance(candidate, ReviewAnchorPayload)
            and candidate.review_record_id == review_record.record_id
        ) or (
            isinstance(candidate, ReviewValidationBindingPayload)
            and candidate.review_record_id == review_record.record_id
        ) or (
            isinstance(candidate, FindingTransitionPayload)
            and candidate.work_unit_id == payload.work_unit_id
            and candidate.actor is payload.reviewer
        ) or (
            isinstance(candidate, WorkflowEventPayload)
            and candidate.event_kind == "review"
            and candidate.record_refs == (review_record.record_id,)
        ):
            prefix_end += 1
            continue
        break
    return prefix_end

def _review_prefix_finding_ids(
    chain: tuple[ArtifactRecord, ...],
    positions: dict[str, int],
    review_record: ArtifactRecord,
) -> tuple[str, ...] | None:
    """Project finding ids only when this review's contiguous prefix is complete."""
    from finding_reducer import reduce_finding_records

    payload = review_record.payload
    assert isinstance(payload, ReviewPayload)
    prefix_end = _review_prefix_end(chain, positions, review_record)
    try:
        findings = reduce_finding_records(chain[:prefix_end]).request_subset(
            finding_ids=payload.finding_ids
        ).findings
    except ArtifactReplayError:
        raise
    except ValueError:
        return None
    return tuple(item.finding_id for item in findings)

def project_latest_review(
    replay: ArtifactReplayResult,
    read_blob: Callable[[BlobReference], bytes],
    work_unit_id: int | str,
) -> ContractResult | None:
    """Return one work unit's latest review projection, never an aggregate record."""
    unit_id = str(work_unit_id)
    reviews = tuple(
        review
        for review in project_review_contracts(replay, read_blob)
        if review.work_unit_id == unit_id
    )
    return None if not reviews else reviews[-1].result


def _project_validation_attestation(
    replay: ArtifactReplayResult,
    attestation_record: ArtifactRecord,
    read_blob: Callable[[BlobReference], bytes],
) -> ValidationAttestation:
    payload = attestation_record.payload
    assert isinstance(payload, ValidationAttestationPayload)
    content_record = next(
        (
            record
            for record in replay.records
            if record.record_id == payload.content_record_id
        ),
        None,
    )
    if content_record is None or not isinstance(
        content_record.payload, ValidationContentPayload
    ):
        _fail(
            ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
            "review validation projection lacks exact content",
            attestation_record,
        )
    content = content_record.payload
    specs: list[ValidationCommandSpec] = []
    expected_commands: list[str] = []
    records: list[ValidationRecord] = []
    for result, output in zip(payload.results, content.outputs, strict=True):
        command = result.command
        spec = (
            ValidationCommandSpec(argv=command.argv)
            if command.mode == "argv"
            else ValidationCommandSpec(legacy_shell=command.argv[0])
        )
        display = spec.display
        specs.append(spec)
        expected_commands.append(display)
        if result.outcome == "unavailable":
            continue
        try:
            compact_output = read_blob(output.compact_output).decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            _fail(
                ReplayDiagnosticCode.RECORD_UNKNOWN,
                f"review validation compact output cannot be read: {exc}",
                content_record,
            )
        records.append(
            ValidationRecord(
                ValidationStatus.PASS
                if result.outcome == "pass"
                else ValidationStatus.FAIL,
                display,
                result.exit_code,
                compact_output,
            )
        )
    return ValidationAttestation(
        attestation_id=attestation_record.logical_id,
        diff_fingerprint=attestation_record.fingerprint.sha256,
        expected_commands=tuple(expected_commands),
        records=tuple(records),
        output_digest=payload.output_digest,
        summary=content.summary,
        command_specs=tuple(specs),
    )
def _validate_workflow_transitions_and_events(
    chain: tuple[ArtifactRecord, ...],
    records_by_id: dict[str, ArtifactRecord],
    positions: dict[str, int],
) -> dict[str, ArtifactRecord]:
    # Keep rejection-site line anchors stable: the corpus deliberately binds
    # each reachable fail-closed branch to its exact source location.  The
    # review-prefix optimization above changes computation, not this audited
    # rejection surface or the locations that identify it.
    transition_units: dict[str, ArtifactRecord] = {}
    transition_slices: set[str] = set()
    slice_boundaries: dict[str, SliceBoundaryPayload] = {}
    workflow_event_domain_refs: set[str] = set()
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
        elif isinstance(payload, WorkflowEventPayload):
            referenced_id = payload.record_refs[0]
            if referenced_id in workflow_event_domain_refs:
                _fail(
                    ReplayDiagnosticCode.RECORD_DUPLICATE,
                    "workflow event domain reference is duplicated",
                    record,
                )
            workflow_event_domain_refs.add(referenced_id)
            references = tuple(records_by_id.get(item) for item in payload.record_refs)
            if any(
                referenced is None
                or positions[referenced.record_id] >= positions[record.record_id]
                for referenced in references
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                    "workflow event references a missing or later record",
                    record,
                )
            typed_references = tuple(
                referenced for referenced in references if referenced is not None
            )
            expected_type = {
                "run": RunIdentityPayload,
                "transition": WorkflowTransitionPayload,
                "validation": ValidationAttestationPayload,
                "review": (ReviewPayload, BranchDiscoveryCompletedPayload),
            }[payload.event_kind]
            if len(typed_references) != 1 or not isinstance(
                typed_references[0].payload, expected_type
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                    "workflow event must reference exactly one matching domain record",
                    record,
                )
            referenced = typed_references[0]
            if referenced.fingerprint != record.fingerprint:
                _fail(
                    ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                    "workflow event fingerprint differs from its domain record",
                    record,
                )
            domain = referenced.payload
            if isinstance(domain, RunIdentityPayload):
                if payload.work_unit_id is not None or payload.round_number is not None:
                    _fail(
                        ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                        "run event carries workflow-local identity",
                        record,
                    )
            elif isinstance(domain, WorkflowTransitionPayload):
                if (
                    domain.slice_id != payload.slice_id
                    or domain.work_unit_id != payload.work_unit_id
                ):
                    _fail(
                        ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                        "transition event identity differs from its referenced transition",
                        record,
                    )
            elif isinstance(domain, ReviewPayload):
                if domain.work_unit_id != payload.work_unit_id:
                    _fail(
                        ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                        "review event work unit differs from its referenced review",
                        record,
                    )
    return transition_units


def _validate_invocation_failures_and_retries(
    chain: tuple[ArtifactRecord, ...],
    positions: dict[str, int],
    transition_units: dict[str, ArtifactRecord],
) -> None:
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
            expected_kinds = (
                {"quota"} if isinstance(payload, QuotaPausePayload) else {"network", "timeout", "output"}
            )
            if (
                positions[failure_record.record_id] >= positions[record.record_id]
                or failure.failure_kind not in expected_kinds
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


def _validate_gate_transitions_and_decisions(
    chain: tuple[ArtifactRecord, ...],
    records_by_id: dict[str, ArtifactRecord],
    positions: dict[str, int],
    transition_units: dict[str, ArtifactRecord],
) -> None:
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


def _index_validation_content(
    chain: tuple[ArtifactRecord, ...],
) -> tuple[
    tuple[ArtifactRecord, ...],
    tuple[ArtifactRecord, ...],
    dict[str, ArtifactRecord],
]:
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
    return validation_content_records, attestation_records, content_by_result


def _validate_attestation_content_bindings(
    attestation_records: tuple[ArtifactRecord, ...],
    content_by_result: dict[str, ArtifactRecord],
    positions: dict[str, int],
    *,
    require_content_authority: bool,
) -> None:
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


def _validate_unbound_validation_content(
    chain: tuple[ArtifactRecord, ...],
    validation_content_records: tuple[ArtifactRecord, ...],
    attestation_records: tuple[ArtifactRecord, ...],
    positions: dict[str, int],
) -> None:
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


def _validate_provider_decision_content(
    chain: tuple[ArtifactRecord, ...],
    positions: dict[str, int],
    *,
    require_content_authority: bool,
) -> tuple[tuple[ArtifactRecord, ...], set[str]]:
    provider_content_records = tuple(
        record for record in chain
        if isinstance(record.payload, ProviderContentPayload)
    )
    provider_decisions = tuple(
        record for record in chain
        if isinstance(
            record.payload,
            (AgentResultPayload, ReviewPayload, BranchDiscoveryCompletedPayload),
        )
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
        role = (
            decision.role
            if isinstance(decision, AgentResultPayload)
            else decision.reviewer
        )
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
        minimum_round = decision_unit.round_number if (
            isinstance(decision, AgentResultPayload) and decision_unit is not None
        ) else 1
        # The decision logical ID is the durable authority for the invocation
        # round. For implementers, a WorkUnitPayload additionally supplies the
        # minimum round, while continuation can advance beyond that immutable
        # boundary. Reviewer attempts have their own one-based round sequence.
        # ProviderContentPayload stores either round separately, so comparison
        # remains an independent cross-record check rather than deriving both
        # values from the decision logical ID. This still rejects stale content
        # from an earlier invocation round.
        round_suffix = decision_record.logical_id.rsplit("-", 1)[-1]
        if not round_suffix.isdigit() or int(round_suffix) < minimum_round:
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
                _provider_content_mismatch_detail(provider_content_records, decision_record, role, decision_round, positions),
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
            "review_result"
            if isinstance(
                decision, (ReviewPayload, BranchDiscoveryCompletedPayload)
            )
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
    return provider_content_records, bound_provider_records


def _validate_unbound_provider_content(
    chain: tuple[ArtifactRecord, ...],
    provider_content_records: tuple[ArtifactRecord, ...],
    bound_provider_records: set[str],
    positions: dict[str, int],
) -> None:
    for content_record in provider_content_records:
        if content_record.record_id not in bound_provider_records:
            if positions[content_record.record_id] == len(chain) - 1:
                continue
            _fail(
                ReplayDiagnosticCode.RECORD_MISSING,
                "provider content has no bound native decision",
                content_record,
            )


def _validate_review_anchors(
    chain: tuple[ArtifactRecord, ...],
    records_by_id: dict[str, ArtifactRecord],
    positions: dict[str, int],
) -> tuple[
    tuple[ArtifactRecord, ...],
    tuple[ArtifactRecord, ...],
    dict[str, ArtifactRecord],
]:
    review_records = tuple(
        record for record in chain if isinstance(record.payload, ReviewPayload)
    )
    review_anchor_records = tuple(
        record for record in chain if isinstance(record.payload, ReviewAnchorPayload)
    )
    review_validation_records = tuple(
        record
        for record in chain
        if isinstance(record.payload, ReviewValidationBindingPayload)
    )
    anchors_by_review: dict[str, ArtifactRecord] = {}
    for anchor_record in review_anchor_records:
        anchor = anchor_record.payload
        review = records_by_id.get(anchor.review_record_id)
        binding_digest = hashlib.sha256(
            anchor.review_record_id.encode("utf-8")
        ).hexdigest()
        if (
            review is None
            or not isinstance(review.payload, ReviewPayload)
            or positions[review.record_id] >= positions[anchor_record.record_id]
            or review.fingerprint != anchor_record.fingerprint
            or anchor_record.logical_id
            != f"review-anchors-{binding_digest[:16]}"
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                "review anchors do not bind one earlier fingerprint-matched review",
                anchor_record,
            )
        if anchor.review_record_id in anchors_by_review:
            _fail(
                ReplayDiagnosticCode.RECORD_DUPLICATE,
                "review anchor binding is duplicated",
                anchor_record,
            )
        anchors_by_review[anchor.review_record_id] = anchor_record
    return review_records, review_validation_records, anchors_by_review


def _validate_review_validation_bindings(
    review_validation_records: tuple[ArtifactRecord, ...],
    records_by_id: dict[str, ArtifactRecord],
    positions: dict[str, int],
) -> dict[str, ArtifactRecord]:
    validations_by_review: dict[str, ArtifactRecord] = {}
    for binding_record in review_validation_records:
        binding = binding_record.payload
        review = records_by_id.get(binding.review_record_id)
        attestation = records_by_id.get(binding.attestation_record_id)
        binding_digest = hashlib.sha256(
            binding.review_record_id.encode("utf-8")
        ).hexdigest()
        if (
            review is None
            or not isinstance(review.payload, ReviewPayload)
            or attestation is None
            or not isinstance(attestation.payload, ValidationAttestationPayload)
            or positions[review.record_id] >= positions[binding_record.record_id]
            or positions[attestation.record_id] >= positions[binding_record.record_id]
            or binding_record.logical_id
            != f"review-validation-{binding_digest[:16]}"
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                "review validation binding does not reference earlier fingerprint-matched facts",
                binding_record,
            )
        if (
            review.fingerprint != binding_record.fingerprint
            or attestation.fingerprint != binding_record.fingerprint
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                "review validation binding fingerprint differs from its review or attestation",
                binding_record,
            )
        if binding.review_record_id in validations_by_review:
            _fail(
                ReplayDiagnosticCode.RECORD_DUPLICATE,
                "review validation binding is duplicated",
                binding_record,
            )
        validations_by_review[binding.review_record_id] = binding_record
    return validations_by_review


def _validate_required_review_authority(
    chain: tuple[ArtifactRecord, ...],
    review_records: tuple[ArtifactRecord, ...],
    anchors_by_review: dict[str, ArtifactRecord],
    validations_by_review: dict[str, ArtifactRecord],
    positions: dict[str, int],
    *,
    require_review_authority: bool,
    allow_incomplete_review_tail: bool,
) -> str | None:
    pending_review_record_id: str | None = None
    if require_review_authority:
        for review in review_records:
            anchor_record = anchors_by_review.get(review.record_id)
            validation_record = validations_by_review.get(review.record_id)
            review_position = positions[review.record_id]
            finding_ids_complete = (
                anchor_record is not None
                and validation_record is not None
                and _review_prefix_finding_ids(chain, positions, review)
                == replay_compatible_finding_ids(review.payload.finding_ids)
            )
            if finding_ids_complete:
                continue
            prefix_end = _review_prefix_end(chain, positions, review)
            recoverable_tail = (
                allow_incomplete_review_tail
                and pending_review_record_id is None
                and (
                    (
                        anchor_record is None
                        and validation_record is None
                        and review_position == len(chain) - 1
                    )
                    or (
                        anchor_record is not None
                        and validation_record is None
                        and positions[anchor_record.record_id] == review_position + 1
                        and positions[anchor_record.record_id] == len(chain) - 1
                    )
                    or (
                        anchor_record is not None
                        and validation_record is not None
                        and positions[anchor_record.record_id] == review_position + 1
                        and positions[validation_record.record_id]
                        == positions[anchor_record.record_id] + 1
                        and prefix_end == len(chain)
                    )
                )
            )
            if recoverable_tail:
                pending_review_record_id = review.record_id
                continue
            if anchor_record is None:
                _fail(
                    ReplayDiagnosticCode.RECORD_MISSING,
                    "review has no authoritative anchor-list record",
                    review,
                )
            if validation_record is None:
                _fail(
                    ReplayDiagnosticCode.RECORD_MISSING,
                    "review has no authoritative validation binding",
                    review,
                )
            _fail(
                ReplayDiagnosticCode.RECORD_MISSING,
                "review finding transition set is incomplete",
                review,
            )
    return pending_review_record_id


def _validate_review_packet_bindings(
    chain: tuple[ArtifactRecord, ...],
) -> None:
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


def _validate_work_unit_revisions(
    chain: tuple[ArtifactRecord, ...],
) -> dict[str, ArtifactRecord]:
    work_units: dict[str, ArtifactRecord] = {}
    latest_work_units: dict[str, ArtifactRecord] = {}
    for record in chain:
        if isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload)) and (
            record.logical_id.startswith("work-unit-")
        ):
            work_unit_id = record.logical_id.removeprefix("work-unit-")
            prior = latest_work_units.get(work_unit_id)
            if prior is not None:
                _validate_work_unit_revision(prior, record)
            latest_work_units[work_unit_id] = record
            # The first revision establishes authority for this logical work
            # unit.  Later revisions must not move the global implementation
            # boundary forward or make intervening activity look like a
            # forward reference.
            work_units.setdefault(work_unit_id, record)
    return work_units


def _validate_work_unit_revision(
    prior: ArtifactRecord,
    current: ArtifactRecord,
) -> None:
    """Enforce the single read/write invariant for one work-unit revision."""

    prior_payload = prior.payload
    payload = current.payload
    assert isinstance(prior_payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
    assert isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
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
            current,
        )


def _validate_single_finding_import(
    chain: tuple[ArtifactRecord, ...],
) -> None:
    import_records = [
        record for record in chain
        if isinstance(
            record.payload,
            (FindingHandoffImportPayload, BranchDiscoveryHandoffImportPayload),
        )
    ]
    if len(import_records) > 1:
        _fail(
            ReplayDiagnosticCode.RECORD_DUPLICATE,
            "a run may contain only one finding handoff import",
            import_records[-1],
        )


def _validate_finding_handoff_record(
    record: ArtifactRecord,
    payload: object,
    chain: tuple[ArtifactRecord, ...],
    records_by_id: dict[str, ArtifactRecord],
    positions: dict[str, int],
    reduce_findings: Callable[..., object],
) -> None:
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
        source_prefix = chain[:positions[record.record_id]]
        transitive = flatten_finding_transition_history(source_prefix)
        legacy = tuple(
            ImportedFindingTransition(source.record_id, source.payload)
            for source in source_prefix
            if isinstance(source.payload, FindingTransitionPayload)
        )
        available_ids = {
            item.record_id for candidate in (transitive, legacy) for item in candidate
        }
        if any(
            record_id not in available_ids
            for record_id in payload.finding_transition_record_ids
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                "finding export transition sequence is not present",
                record,
            )
        if not any(
            payload.finding_transition_record_ids
            == tuple(item.record_id for item in candidate)
            and payload.finding_transitions_sha256
            == finding_transition_sequence_sha256(candidate)
            for candidate in (transitive, legacy)
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
    elif isinstance(
        payload,
        (BranchDiscoveryHandoffExportPayload, BranchDiscoveryHandoffImportPayload),
    ):
        _validate_branch_discovery_handoff_record(
            record,
            payload,
            chain,
            records_by_id,
            positions,
            reduce_findings,
        )


def _validate_branch_discovery_handoff_record(
    record: ArtifactRecord,
    payload: BranchDiscoveryHandoffExportPayload | BranchDiscoveryHandoffImportPayload,
    chain: tuple[ArtifactRecord, ...],
    records_by_id: dict[str, ArtifactRecord],
    positions: dict[str, int],
    reduce_findings: Callable[..., object],
) -> None:
    if isinstance(payload, BranchDiscoveryHandoffExportPayload):
        _validate_branch_discovery_export(
            record, payload, chain, records_by_id, positions
        )
    else:
        _validate_branch_discovery_import(
            record, payload, chain, positions, reduce_findings
        )


def _validate_branch_discovery_export(
    record: ArtifactRecord,
    payload: BranchDiscoveryHandoffExportPayload,
    chain: tuple[ArtifactRecord, ...],
    records_by_id: dict[str, ArtifactRecord],
    positions: dict[str, int],
) -> None:
    if (
        payload.source_run_id != record.run_id
        or not record.predecessor_ids
        or payload.source_head_record_id != record.predecessor_ids[0]
        or payload.predecessor_run_id != payload.source_run_id
        or payload.predecessor_head_record_id != payload.source_head_record_id
    ):
        _fail(
            ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
            "branch discovery export source or predecessor binding differs",
            record,
        )
    attestation = records_by_id.get(payload.validation_attestation_record_id)
    if (
        attestation is None
        or not isinstance(attestation.payload, ValidationAttestationPayload)
        or positions[attestation.record_id] >= positions[record.record_id]
    ):
        _fail(
            ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
            "branch discovery export validation attestation is not present",
            record,
        )
    if payload.target_execution_mode == "PLAN_ONLY":
        review = records_by_id.get(payload.discovery_review_record_id or "")
        if (
            review is None
            or not isinstance(review.payload, BranchDiscoveryCompletedPayload)
            or positions[review.record_id] >= positions[record.record_id]
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                "branch discovery export completion is not present; approved is not scan completion",
                record,
            )
        if review.fingerprint != attestation.fingerprint:
            _fail(
                ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                "branch discovery export completion and attestation fingerprints differ",
                record,
            )
    else:
        completion = records_by_id.get(payload.source_completion_record_id or "")
        if (
            completion is None
            or not isinstance(completion.payload, WorkflowCompletionPayload)
            or completion.payload.outcome != "completed"
            or positions[completion.record_id] >= positions[record.record_id]
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                "BRANCH_DISCOVERY family handoff source completion is not present",
                record,
            )
    source_prefix = chain[:positions[record.record_id]]
    flattened = flatten_finding_transition_history(source_prefix)
    if (
        payload.finding_transition_record_ids
        != tuple(item.record_id for item in flattened)
        or payload.finding_transitions_sha256
        != finding_transition_sequence_sha256(flattened)
    ):
        _fail(
            ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
            "branch discovery export flattened transition history differs",
            record,
        )
    identity = next(
        (
            candidate.payload
            for candidate in source_prefix
            if isinstance(candidate.payload, RunIdentityPayload)
        ),
        None,
    )
    if identity is not None and (
        (
            payload.target_execution_mode == "PLAN_ONLY"
            and identity.execution_mode != "BRANCH_DISCOVERY"
        )
        or (
            payload.target_execution_mode == "BRANCH_DISCOVERY"
            and identity.execution_mode not in {"IMPLEMENT", "PLAN_ONLY"}
        )
    ):
        _fail(
            ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
            "branch discovery handoff source and target run kinds are invalid",
            record,
        )
    profile = next(
        (
            candidate.payload
            for candidate in source_prefix
            if isinstance(candidate.payload, RunProfilePayload)
        ),
        None,
    )
    binding = None if profile is None else profile.family_binding
    if (
        binding is None
        or binding.family_id != payload.family_id
        or binding.family_base_commit != payload.family_base_commit
    ):
        _fail(
            ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
            "branch discovery export family identity differs from RunProfile",
            record,
        )


def _validate_branch_discovery_import(
    record: ArtifactRecord,
    payload: BranchDiscoveryHandoffImportPayload,
    chain: tuple[ArtifactRecord, ...],
    positions: dict[str, int],
    reduce_findings: Callable[..., object],
) -> None:
    if (
        payload.target_run_id != record.run_id
        or payload.target_run_identity != record.run_id
    ):
        _fail(
            ReplayDiagnosticCode.RECORD_RUN_MISMATCH,
            "branch discovery import target run differs",
            record,
        )
    if payload.source_run_id == record.run_id:
        _fail(
            ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
            "branch discovery import must retain foreign provenance",
            record,
        )
    reduction = reduce_findings(_result(record.run_id, (record,)))
    findings_by_id = {
        finding.finding_id: finding for finding in reduction.ledger.findings
    }
    expected_snapshot = tuple(
        FindingSnapshotItem(
            finding.finding_id,
            finding_record_signature(finding),
            finding.status.value.lower(),
            FindingSeverity(finding.finding_class.value),
        )
        for finding in (
            findings_by_id[finding_id]
            for finding_id in sorted_finding_ids(findings_by_id)
        )
    )
    if payload.finding_snapshot != expected_snapshot:
        _fail(
            ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
            "branch discovery import finding_snapshot differs from transitions",
            record,
        )
    profile = next(
        (
            candidate.payload
            for candidate in chain
            if isinstance(candidate.payload, RunProfilePayload)
        ),
        None,
    )
    binding = None if profile is None else profile.family_binding
    bootstrap_only = all(
        isinstance(candidate.payload, BranchDiscoveryHandoffImportPayload)
        for candidate in chain
    )
    actual_family = None if binding is None else (
        binding.family_id,
        binding.family_base_commit,
        binding.cycle_number,
        binding.predecessor_run_id,
        binding.predecessor_head_record_id,
    )
    expected_family = (
        payload.family_id,
        payload.family_base_commit,
        payload.cycle_number,
        payload.predecessor_run_id,
        payload.predecessor_head_record_id,
    )
    if not bootstrap_only and actual_family != expected_family:
        _fail(
            ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
            "branch discovery import family binding differs from RunProfile",
            record,
        )
    task = next(
        (item.payload for item in chain if isinstance(item.payload, TaskPayload)),
        None,
    )
    identity = next(
        (
            item.payload
            for item in chain
            if isinstance(item.payload, RunIdentityPayload)
        ),
        None,
    )
    if task is not None and task.assignment_sha256 != payload.target_task_sha256:
        _fail(
            ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
            "branch discovery import task bytes digest differs from Task record",
            record,
        )
    if identity is not None:
        normalized_task = identity.task_file.replace("\\", "/")
        if not (
            normalized_task == payload.target_task_path
            or normalized_task.endswith("/" + payload.target_task_path)
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                "branch discovery import task path differs from RunIdentity",
                record,
            )
        if identity.execution_mode != payload.target_execution_mode:
            _fail(
                ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                "branch discovery import target execution mode differs from RunIdentity",
                record,
            )
    first_dispatch = next(
        (
            positions[item.record_id]
            for item in chain
            if isinstance(
                item.payload,
                (
                    ProviderInputMeasurementPayload,
                    ProviderAttemptPayload,
                    AgentResultPayload,
                ),
            )
        ),
        None,
    )
    if first_dispatch is not None and positions[record.record_id] >= first_dispatch:
        _fail(
            ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
            "branch discovery import must precede the first dispatch",
            record,
        )


def _validate_work_unit_finding_import(
    record: ArtifactRecord,
    payload: object,
    chain: tuple[ArtifactRecord, ...],
    records_by_id: dict[str, ArtifactRecord],
    positions: dict[str, int],
    reduce_findings: Callable[..., object],
) -> None:
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


def _validate_work_unit_activity_reference(
    record: ArtifactRecord,
    payload: object,
    work_units: dict[str, ArtifactRecord],
    first_work_unit_position: int | None,
    positions: dict[str, int],
) -> None:
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
                BranchDiscoveryCompletedPayload,
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


def _validate_bound_record_references(
    record: ArtifactRecord,
    payload: object,
    records_by_id: dict[str, ArtifactRecord],
    positions: dict[str, int],
) -> None:
    if isinstance(payload, BranchDiscoveryCompletedPayload):
        attestation = records_by_id.get(
            payload.validation_attestation_record_id
        )
        identity = next(
            (
                item.payload
                for item in records_by_id.values()
                if isinstance(item.payload, RunIdentityPayload)
            ),
            None,
        )
        if (
            attestation is None
            or not isinstance(attestation.payload, ValidationAttestationPayload)
            or positions[attestation.record_id] >= positions[record.record_id]
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                "branch discovery completion validation attestation is not present",
                record,
            )
        _same_fingerprint(record, attestation)
        if (
            identity is None
            or identity.execution_mode != "BRANCH_DISCOVERY"
            or identity.first_slice_start_commit != payload.reviewed_head_commit
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                "branch discovery completion requires its own HEAD-bound BRANCH_DISCOVERY run",
                record,
            )
    elif isinstance(payload, BindingPayload):
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
        identity = next(
            (
                item.payload
                for item in records_by_id.values()
                if isinstance(item.payload, RunIdentityPayload)
            ),
            None,
        )
        discovery_binding = (
            identity is not None
            and identity.execution_mode == "BRANCH_DISCOVERY"
            and binding is not None
            and isinstance(binding.payload, BranchDiscoveryCompletedPayload)
        )
        if (
            binding is None
            or not (
                isinstance(binding.payload, BindingPayload) or discovery_binding
            )
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


def _validate_chain_record_references(
    chain: tuple[ArtifactRecord, ...],
    records_by_id: dict[str, ArtifactRecord],
    positions: dict[str, int],
    work_units: dict[str, ArtifactRecord],
    first_work_unit_position: int | None,
    reduce_findings: Callable[..., object],
) -> None:
    for record in chain:
        payload = record.payload
        _validate_finding_handoff_record(
            record, payload, chain, records_by_id, positions, reduce_findings
        )
        _validate_work_unit_finding_import(
            record, payload, chain, records_by_id, positions, reduce_findings
        )
        _validate_work_unit_activity_reference(
            record, payload, work_units, first_work_unit_position, positions
        )
        _validate_bound_record_references(
            record, payload, records_by_id, positions
        )


def _validate_provider_attempt_sequences(
    chain: tuple[ArtifactRecord, ...],
) -> None:
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


def _validate_side_effect_sequences(
    chain: tuple[ArtifactRecord, ...],
) -> None:
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


def _provider_content_mismatch_detail(
    provider_records: tuple[ArtifactRecord, ...],
    decision_record: ArtifactRecord,
    role: Role,
    decision_round: int,
    positions: dict[str, int],
) -> str:
    """Name the first failed immutable binding without exposing provider bytes."""
    decision = decision_record.payload
    assert isinstance(decision, (AgentResultPayload, ReviewPayload))
    same_request = tuple(
        record
        for record in provider_records
        if record.payload.request_id == decision.request_id
    )
    prefix = "native decision has no unique earlier provider-content record"
    if not same_request:
        return (
            f"{prefix}: feature=request_id expected={decision.request_id!r} "
            "actual='<missing>'"
        )
    earlier = tuple(
        record
        for record in same_request
        if positions[record.record_id] < positions[decision_record.record_id]
    )
    if not earlier:
        return f"{prefix}: feature=record_order expected='earlier' actual='later'"
    comparisons = (
        ("role", role.value, lambda record: record.payload.role.value),
        ("work_unit_id", decision.work_unit_id, lambda record: record.payload.work_unit_id),
        ("round_number", decision_round, lambda record: record.payload.round_number),
        ("response_sha256", decision.response_sha256, lambda record: record.payload.response_sha256),
        ("fingerprint", decision_record.fingerprint.sha256, lambda record: record.fingerprint.sha256),
    )
    candidates = earlier
    for feature, expected, value_of in comparisons:
        actual_values = tuple(dict.fromkeys(value_of(record) for record in candidates))
        narrowed = tuple(record for record in candidates if value_of(record) == expected)
        if not narrowed:
            actual = actual_values[0] if len(actual_values) == 1 else actual_values
            return f"{prefix}: feature={feature} expected={expected!r} actual={actual!r}"
        candidates = narrowed
    return f"{prefix}: feature=candidate_count expected=1 actual={len(candidates)}"


def _validate_payload_references(
    chain: tuple[ArtifactRecord, ...],
    records_by_id: dict[str, ArtifactRecord],
    *,
    require_content_authority: bool,
    require_review_authority: bool,
    allow_incomplete_review_tail: bool,
) -> str | None:
    from finding_reducer import reduce_findings

    positions = {record.record_id: index for index, record in enumerate(chain)}
    transition_units = _validate_workflow_transitions_and_events(
        chain, records_by_id, positions
    )
    _validate_invocation_failures_and_retries(chain, positions, transition_units)
    _validate_gate_transitions_and_decisions(
        chain, records_by_id, positions, transition_units
    )
    (
        validation_content_records,
        attestation_records,
        content_by_result,
    ) = _index_validation_content(chain)
    _validate_attestation_content_bindings(
        attestation_records,
        content_by_result,
        positions,
        require_content_authority=require_content_authority,
    )
    _validate_unbound_validation_content(
        chain, validation_content_records, attestation_records, positions
    )
    provider_content_records, bound_provider_records = (
        _validate_provider_decision_content(
            chain,
            positions,
            require_content_authority=require_content_authority,
        )
    )
    _validate_unbound_provider_content(
        chain, provider_content_records, bound_provider_records, positions
    )
    review_records, review_validation_records, anchors_by_review = (
        _validate_review_anchors(chain, records_by_id, positions)
    )
    validations_by_review = _validate_review_validation_bindings(
        review_validation_records, records_by_id, positions
    )
    pending_review_record_id = _validate_required_review_authority(
        chain,
        review_records,
        anchors_by_review,
        validations_by_review,
        positions,
        require_review_authority=require_review_authority,
        allow_incomplete_review_tail=allow_incomplete_review_tail,
    )
    _validate_review_packet_bindings(chain)
    work_units = _validate_work_unit_revisions(chain)
    first_work_unit_position = min(
        (positions[record.record_id] for record in work_units.values()),
        default=None,
    )
    _validate_single_finding_import(chain)
    _validate_chain_record_references(
        chain,
        records_by_id,
        positions,
        work_units,
        first_work_unit_position,
        reduce_findings,
    )
    _validate_provider_attempt_sequences(chain)
    _validate_side_effect_sequences(chain)
    return pending_review_record_id


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
            payload_json=canonical_json(_semantic_payload_document(record.payload)),
        )
        for record in records
    )


def _workflow_event_domain_kind(payload: object) -> str | None:
    if isinstance(payload, RunIdentityPayload):
        return "run"
    if isinstance(payload, WorkflowTransitionPayload):
        return "transition"
    if isinstance(payload, ValidationAttestationPayload):
        return "validation"
    if isinstance(payload, ReviewPayload):
        return "review"
    if isinstance(payload, BranchDiscoveryCompletedPayload):
        return "review"
    return None


def _pending_workflow_event_record_id(
    records: tuple[ArtifactRecord, ...],
    pending_review_record_id: str | None,
) -> str | None:
    """Recognize exactly one auditable domain transaction at the chain tail."""
    observed = {
        record.payload.record_refs[0]
        for record in records
        if isinstance(record.payload, WorkflowEventPayload)
    }
    missing = tuple(
        record
        for record in records
        if _workflow_event_domain_kind(record.payload) is not None
        and record.record_id != pending_review_record_id
        and record.record_id not in observed
    )
    if len(missing) != 1:
        return None
    candidate = missing[0]
    if isinstance(candidate.payload, ReviewPayload):
        positions = {record.record_id: index for index, record in enumerate(records)}
        if _review_prefix_end(records, positions, candidate) != len(records):
            return None
    elif isinstance(candidate.payload, BranchDiscoveryCompletedPayload):
        candidate_index = records.index(candidate)
        if any(
            not isinstance(record.payload, FindingTransitionPayload)
            for record in records[candidate_index + 1 :]
        ):
            return None
    elif candidate is not records[-1]:
        return None
    return candidate.record_id


def pending_workflow_event_payload(
    replay: ArtifactReplayResult,
) -> WorkflowEventPayload | None:
    """Project the sole crash-tail event from its already durable domain fact."""
    record_id = replay.pending_workflow_event_record_id
    if record_id is None:
        return None
    positions = {
        record.record_id: index for index, record in enumerate(replay.records)
    }
    domain_record = next(
        record for record in replay.records if record.record_id == record_id
    )
    domain = domain_record.payload
    kind = _workflow_event_domain_kind(domain)
    assert kind is not None
    if isinstance(domain, RunIdentityPayload):
        return WorkflowEventPayload("run", None, "1", None, (record_id,))
    if isinstance(domain, WorkflowTransitionPayload):
        return WorkflowEventPayload(
            "transition",
            domain.work_unit_id,
            domain.slice_id,
            None,
            (record_id,),
        )
    work_unit_id = (
        domain.work_unit_id
        if isinstance(domain, (ReviewPayload, BranchDiscoveryCompletedPayload))
        else None
    )
    prior_transitions = tuple(
        record.payload
        for record in replay.records[: positions[record_id] + 1]
        if isinstance(record.payload, WorkflowTransitionPayload)
        and record.payload.work_unit_id is not None
        and (work_unit_id is None or record.payload.work_unit_id == work_unit_id)
    )
    if not prior_transitions:
        _fail(
            ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
            "pending workflow event has no earlier workflow cursor",
            domain_record,
        )
    cursor = prior_transitions[-1]
    assert cursor.work_unit_id is not None
    if work_unit_id is None:
        work_unit_id = cursor.work_unit_id
    round_candidates = [
        payload.round_number
        for record in replay.records[: positions[record_id] + 1]
        for payload in (record.payload,)
        if isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
        and record.logical_id.removeprefix("work-unit-") == work_unit_id
    ]
    if isinstance(domain, (ReviewPayload, BranchDiscoveryCompletedPayload)):
        suffix = domain_record.logical_id.rsplit("-", 1)[-1]
        if not suffix.isdigit():
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                "pending review event has no canonical logical round",
                domain_record,
            )
        round_candidates.append(int(suffix))
    return WorkflowEventPayload(
        kind,
        work_unit_id,
        cursor.slice_id,
        max(round_candidates, default=1),
        (record_id,),
    )


def _result(
    expected_run_id: str,
    records: tuple[ArtifactRecord, ...],
    *,
    reference_records: tuple[ArtifactRecord, ...] | None = None,
    pending_review_record_id: str | None = None,
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
    review_anchors: list[ReviewAnchorPayload] = []
    review_validation_bindings: list[ReviewValidationBindingPayload] = []
    workflow_events: list[ReplayedWorkflowEvent] = []
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
        elif isinstance(payload, WorkflowEventPayload):
            workflow_events.append(
                ReplayedWorkflowEvent(
                    record.record_id,
                    payload.event_kind,
                    payload.work_unit_id,
                    payload.slice_id,
                    payload.round_number,
                    payload.record_refs,
                )
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
        elif isinstance(payload, ReviewAnchorPayload):
            review_anchors.append(payload)
        elif isinstance(payload, ReviewValidationBindingPayload):
            review_validation_bindings.append(payload)
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
        pending_review_record_id=pending_review_record_id,
        pending_workflow_event_record_id=_pending_workflow_event_record_id(
            records, pending_review_record_id
        ),
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
        workflow_events=tuple(workflow_events),
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
        review_anchors=tuple(review_anchors),
        review_validation_bindings=tuple(review_validation_bindings),
        work_unit_reviewers=project_work_unit_reviewers(
            tuple(
                record
                for record in records
                if record.record_id != pending_review_record_id
            )
        ),
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


def _semantic_payload_document(payload: object) -> dict[str, object]:
    raw = asdict(payload)  # type: ignore[arg-type]
    if isinstance(payload, ReviewPayload) and not payload.plan_treatment_decisions:
        raw.pop("plan_treatment_decisions", None)
    if isinstance(payload, (AgentResultPayload, PlanPayload, FindingTransitionPayload, RunProfilePayload)):
        return artifact_payload_document(payload)
    if isinstance(
        payload,
        (
            FindingHandoffExportPayload,
            FindingHandoffImportPayload,
            BranchDiscoveryHandoffExportPayload,
            BranchDiscoveryHandoffImportPayload,
        ),
    ):
        return artifact_payload_document(payload)
    if (
        isinstance(payload, InvocationFailurePayload)
        and payload.orchestrator_diagnostic is None
    ):
        raw.pop("orchestrator_diagnostic", None)
    if (
        isinstance(payload, InvocationFailurePayload)
        and payload.native_review_rejection is None
    ):
        raw.pop("native_review_rejection", None)
        raw.pop("native_review_retry_round", None)
    if (
        isinstance(payload, InvocationFailurePayload)
        and payload.native_implementer_rejection is None
    ):
        raw.pop("native_implementer_rejection", None)
        raw.pop("native_implementer_retry_round", None)
    return raw


def _slice_acceptance_projection(spec: SliceSpec) -> dict[str, object]:
    if not spec.acceptance_criteria:
        return {}
    return {
        "acceptance_criteria": tuple(
            {
                "criterion_id": item.criterion_id,
                "text": item.text,
                "measured_against": item.measured_against.value,
            }
            for item in spec.acceptance_criteria
        )
    }


__all__ = [
    "ArtifactReplayError",
    "ArtifactReplayResult",
    "ReplayAuditEvent",
    "ReplayDiagnostic",
    "ReplayDiagnosticCode",
    "ReplayFact",
    "STATE_PROJECTION_REDUCER_VERSION",
    "ReplayedWorkflowCursor",
    "ReplayedWorkUnitState",
    "ReplayedSideEffect",
    "ReplayedGateDecision",
    "project_work_unit_reviewers",
    "replay_artifacts",
]
