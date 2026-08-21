"""Fail-closed resume resolution for legacy state-v3 and structured-v1 runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path

from artifact_models import (
    BindingPayload,
    ArtifactRecord,
    CorrectionWorkUnitPayload,
    FindingTransitionPayload,
    GatePayload,
    PlanPayload,
    QuotaPausePayload,
    RecordType,
    ReviewPayload,
    ResumeCheckPayload,
    TaskPayload,
    TransientRetryPayload,
    ValidationAttestationPayload,
    WorkUnitPayload,
    WorkflowCompletionPayload,
    ProviderInputMeasurementPayload,
    FinalReviewPreflightPayload,
    canonical_json,
)
from artifact_store import ArtifactStore, ArtifactStoreError
from artifact_replay import (
    ArtifactReplayError,
    ArtifactReplayResult,
    ReplayDiagnosticCode,
    replay_artifacts,
)
from workflow_state import (
    AgentFailureKind,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
)


class ArtifactResumeError(ValueError):
    """Raised when records cannot safely rehydrate their state-v3 mirror."""

    def __init__(
        self,
        message: str,
        *,
        code: ReplayDiagnosticCode = ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
        record_id: str | None = None,
    ) -> None:
        self.code = code
        self.record_id = record_id
        location = f" at record {record_id}" if record_id is not None else ""
        super().__init__(f"{code.value}{location}: {message}")


@dataclass(frozen=True, slots=True)
class ResumeResolution:
    state: WorkflowState
    mode: ProtocolMode
    record_head_id: str | None
    replay_result: ArtifactReplayResult | None


def resolve_resume_state(repository_root: Path, state: WorkflowState) -> ResumeResolution:
    """Resolve the immutable protocol binding and verify structured mirror facts.

    Historical states without a binding remain on the legacy path and are never
    imported.  A structured binding, in contrast, makes the append-only record
    chain mandatory and authoritative for every fact represented by that chain.
    """
    mode = state.effective_protocol_mode
    if mode is ProtocolMode.LEGACY_STATE_V3:
        return ResumeResolution(state, mode, None, None)

    try:
        chain = ArtifactStore(repository_root, state.run_id).load_chain()
    except ArtifactStoreError as exc:
        raise ArtifactResumeError(
            f"structured-v1 record chain for run {state.run_id!r} is invalid: {exc}; "
            "repair or restore the append-only records before resuming",
            code=ReplayDiagnosticCode.RECORD_UNKNOWN,
        ) from exc
    if not chain:
        raise ArtifactResumeError(
            f"structured-v1 run {state.run_id!r} has no records; restore its record "
            "directory before resuming",
            code=ReplayDiagnosticCode.RECORD_MISSING,
        )

    try:
        replay = replay_artifacts(chain, state.run_id)
    except ArtifactReplayError as exc:
        raise ArtifactResumeError(
            exc.diagnostic.message,
            code=exc.code,
            record_id=exc.record_id,
        ) from exc
    chain = replay.records

    head = replay.head_record_id
    assert head is not None

    def mismatch(
        message: str,
        record_id: str | None = None,
        *,
        code: ReplayDiagnosticCode = ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
    ) -> ArtifactResumeError:
        location = record_id or head
        return ArtifactResumeError(
            f"structured-v1 resume mismatch at record {location}: {message}; "
            "repair the state mirror or restore the matching record chain before resuming",
            code=code,
            record_id=location,
        )

    tasks = [item for item in chain if item.record_type is RecordType.TASK]
    if state.task_digest is not None and state.task_scope_patterns:
        expected_task = TaskPayload(
            target_branch=state.target_branch or state.branch,
            scope_paths=state.task_scope_patterns,
            assignment_sha256=state.task_digest,
        )
        if len(tasks) != 1:
            raise mismatch(f"expected exactly one task record, found {len(tasks)}")
        if tasks[0].payload != expected_task:
            raise mismatch("task contract differs from state-v3", tasks[0].record_id)

    unit_by_id = {str(item.work_unit_id): item for item in state.work_units}
    slice_by_id = {str(item.slice_id): item for item in state.slices}
    work_records = [
        item
        for item in chain
        if item.record_type in {RecordType.WORK_UNIT, RecordType.CORRECTION_WORK_UNIT}
    ]
    latest_work_record_by_id = {}
    for record in work_records:
        payload = record.payload
        assert isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
        unit_id = record.logical_id.removeprefix("work-unit-")
        latest_work_record_by_id[unit_id] = record
        unit = unit_by_id.get(unit_id)
        slice_record = slice_by_id.get(payload.slice_id)
        pending_correction = _recoverable_pending_correction_record(
            state, chain, record
        )
        if unit is None or slice_record is None:
            raise mismatch("work-unit record has no state-v3 counterpart", record.record_id)
        if (
            str(unit.slice_id) != payload.slice_id
            or (payload.round_number > unit.round_number and not pending_correction)
            or payload.paths != slice_record.scope_paths
        ):
            raise mismatch("work-unit round, slice, or path allowlist differs", record.record_id)
        if isinstance(payload, CorrectionWorkUnitPayload) != (
            unit.kind is WorkUnitKind.CORRECTION
        ):
            raise mismatch(
                "correction work-unit finding attribution differs from state-v3",
                record.record_id,
            )

    for unit_id, record in latest_work_record_by_id.items():
        unit = unit_by_id[unit_id]
        payload = record.payload
        assert isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
        pending_correction = _recoverable_pending_correction_record(
            state, chain, record
        )
        if payload.round_number != unit.round_number and not pending_correction:
            raise mismatch(
                "latest work-unit round differs from state-v3",
                record.record_id,
            )
        if (
            isinstance(payload, CorrectionWorkUnitPayload)
            and payload.finding_ids != unit.open_findings
            and not pending_correction
        ):
            raise mismatch(
                "latest correction finding attribution differs from state-v3",
                record.record_id,
            )

    current = state.current_work_unit
    if current.kind is not WorkUnitKind.PLAN and state.current_slice.scope_paths:
        current_records = [
            item
            for item in work_records
            if item.logical_id == f"work-unit-{current.work_unit_id}"
        ]
        if not current_records:
            raise mismatch(
                "current work unit has no structured record",
                code=ReplayDiagnosticCode.MIRROR_AHEAD,
            )
        latest = current_records[-1]
        payload = latest.payload
        assert isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
        if (
            payload.round_number != current.round_number
            and not _recoverable_pending_correction_record(state, chain, latest)
        ):
            raise mismatch("current work-unit round differs from the record chain", latest.record_id)

    plans = [item for item in chain if item.record_type is RecordType.PLAN]
    if (
        state.execution_mode == "IMPLEMENT"
        and state.planned_slices
        and state.work_plan_path
    ):
        if not plans:
            raise mismatch(
                "approved plan has no structured record",
                code=ReplayDiagnosticCode.MIRROR_AHEAD,
            )
        if len(plans) != 1:
            raise mismatch(
                f"expected exactly one immutable approved-plan record, found {len(plans)}"
            )
        if state.approved_plan_commit is None:
            raise mismatch("state-v3 mirror is missing its approved-plan commit binding")
        payload = plans[-1].payload
        assert isinstance(payload, PlanPayload)
        expected_slices = tuple(
            (str(item.slice_id), item.summary, item.scope_paths)
            for item in state.planned_slices
        )
        actual_slices = tuple(
            (item.slice_id, item.summary, item.paths) for item in payload.slices
        )
        if (
            payload.work_plan_path != state.work_plan_path
            or payload.approved_plan_commit != state.approved_plan_commit
            or actual_slices != expected_slices
        ):
            raise mismatch("approved-plan binding differs from state-v3", plans[-1].record_id)

    decisions = {
        (
            decision.reason.value.replace("_", "-"),
            "approved" if decision.approved else "rejected",
            decision.rationale,
            decision.fingerprint,
        )
        for unit in state.work_units
        for decision in unit.gate_decisions
    }
    decision_records = {
        (
            record.payload.gate_kind,
            record.payload.decision,
            record.payload.rationale,
            record.fingerprint.sha256,
        ): record
        for record in chain
        if isinstance(record.payload, GatePayload)
    }
    if set(decision_records) != decisions:
        differing = next(iter(set(decision_records) ^ decisions), None)
        record = decision_records.get(differing) if differing is not None else None
        raise mismatch(
            "gate decisions differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(decision_records), decisions),
        )

    finding_statuses = _finding_statuses(state)
    latest_findings: dict[str, ArtifactRecord] = {}
    for record in chain:
        if isinstance(record.payload, FindingTransitionPayload):
            latest_findings[record.payload.finding_id] = record
    pending_review_finding_gap = _recoverable_pending_review_finding_gap(
        state,
        chain,
        finding_statuses,
        latest_findings,
    )
    if set(latest_findings) != set(finding_statuses) and not pending_review_finding_gap:
        differing = next(iter(set(latest_findings) ^ set(finding_statuses)), None)
        record = latest_findings.get(differing) if differing is not None else None
        raise mismatch(
            "finding transitions differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(latest_findings), set(finding_statuses)),
        )
    for finding_id, record in latest_findings.items():
        assert isinstance(record.payload, FindingTransitionPayload)
        if (
            finding_id in finding_statuses
            and record.payload.finding_status != finding_statuses[finding_id]
            and not pending_review_finding_gap
        ):
            raise mismatch("finding status differs from state-v3", record.record_id)

    attestation_facts = _attestation_facts(state)
    attestation_records = {
        (record.logical_id, record.fingerprint.sha256): record
        for record in chain
        if record.record_type is RecordType.VALIDATION_ATTESTATION
    }
    if set(attestation_records) != attestation_facts:
        differing = next(iter(set(attestation_records) ^ attestation_facts), None)
        record = attestation_records.get(differing) if differing is not None else None
        raise mismatch(
            "validation attestations differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(attestation_records), attestation_facts),
        )

    quota_facts = {
        (failure.role, failure.diff_fingerprint, failure.resume_at_utc)
        for unit in state.work_units
        for failure in unit.invocation_failures
        if (
            failure.failure_kind is AgentFailureKind.QUOTA
            and failure.diff_fingerprint is not None
            and failure.resume_at_utc is not None
        )
    }
    quota_records = {
        (
            record.payload.role.value,
            record.payload.repository_fingerprint,
            record.payload.retry_at,
        ): record
        for record in chain
        if isinstance(record.payload, QuotaPausePayload)
    }
    if set(quota_records) != quota_facts:
        differing = next(iter(set(quota_records) ^ quota_facts), None)
        record = quota_records.get(differing) if differing is not None else None
        raise mismatch(
            "quota pauses differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(quota_records), quota_facts),
        )

    transient_facts = {
        (
            failure.role,
            failure.diff_fingerprint,
            failure.resume_at_utc,
            failure.auto_resume_count,
        )
        for unit in state.work_units
        for failure in unit.invocation_failures
        if (
            failure.failure_kind is AgentFailureKind.NETWORK
            and failure.automatic_resume
            and failure.diff_fingerprint is not None
            and failure.resume_at_utc is not None
        )
    }
    transient_records = {
        (
            record.payload.role.value,
            record.payload.repository_fingerprint,
            record.payload.retry_at,
            record.payload.attempt,
        ): record
        for record in chain
        if isinstance(record.payload, TransientRetryPayload)
    }
    if set(transient_records) != transient_facts:
        differing = next(iter(set(transient_records) ^ transient_facts), None)
        record = transient_records.get(differing) if differing is not None else None
        raise mismatch(
            "transient retries differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(transient_records), transient_facts),
        )

    bootstrap_records = {
        (
            record.record_type.value,
            record.payload.transition_fingerprint,
        ): (
            hashlib.sha256(canonical_json(asdict(record.payload))).hexdigest(),
            record,
        )
        for record in chain
        if isinstance(record.payload, (ProviderInputMeasurementPayload, FinalReviewPreflightPayload))
    }
    bootstrap_facts = {
        (fact.check_kind, fact.transition_fingerprint): fact.semantic_digest
        for fact in state.bootstrap_checks
    }
    if set(bootstrap_records) != set(bootstrap_facts):
        differing = next(iter(set(bootstrap_records) ^ set(bootstrap_facts)), None)
        record = bootstrap_records.get(differing, (None, None))[1] if differing is not None else None
        raise mismatch(
            "bootstrap checks differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(bootstrap_records), set(bootstrap_facts)),
        )
    for key, (digest, record) in bootstrap_records.items():
        if bootstrap_facts[key] != digest:
            raise mismatch("bootstrap check payload differs from state-v3", record.record_id)

    for record in chain:
        if isinstance(record.payload, ResumeCheckPayload):
            predecessor = record.predecessor_ids[0] if record.predecessor_ids else None
            if record.payload.expected_head_id != predecessor:
                raise mismatch("resume check is not bound to its prior record head", record.record_id)

    records_by_id = {record.record_id: record for record in chain}
    for record in chain:
        payload = record.payload
        if not isinstance(payload, BindingPayload):
            continue
        attestation = records_by_id.get(payload.attestation_id)
        if (
            attestation is None
            or not isinstance(attestation.payload, ValidationAttestationPayload)
            or attestation.fingerprint != record.fingerprint
        ):
            raise mismatch(
                "binding references an unknown, invalid, or fingerprint-mismatched "
                "validation attestation",
                record.record_id,
            )
        for approval_id in payload.approval_ids:
            approval = records_by_id.get(approval_id)
            if (
                approval is None
                or not isinstance(approval.payload, ReviewPayload)
                or approval.payload.verdict != "approved"
                or approval.fingerprint != record.fingerprint
            ):
                raise mismatch(
                    "binding references an unknown, unapproved, or "
                    "fingerprint-mismatched review",
                    record.record_id,
                )

    commit_targets = {item.commit_ref for item in state.slices if item.commit_ref is not None}
    bound_commit_targets: set[str] = set()
    for record in chain:
        if isinstance(record.payload, BindingPayload) and record.payload.binding_kind == "commit":
            if record.payload.target not in commit_targets:
                raise mismatch("commit binding has no state-v3 counterpart", record.record_id)
            bound_commit_targets.add(record.payload.target)
    missing_bindings = commit_targets - bound_commit_targets
    if missing_bindings:
        raise mismatch(
            "completed slice is missing a structured commit binding",
            code=ReplayDiagnosticCode.MIRROR_AHEAD,
        )

    completed = (
        state.current_step is WorkflowStep.COMPLETED
        and all(item.commit_ref is not None for item in state.slices)
        and (
            state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
            or (
                state.execution_mode == "PLAN_ONLY"
                and state.current_work_unit.kind is WorkUnitKind.PLAN
            )
        )
    )
    completions = [item for item in chain if isinstance(item.payload, WorkflowCompletionPayload)]
    if completed and not completions:
        raise mismatch(
            "state-v3 mirror reports workflow completion without a structured record",
            code=ReplayDiagnosticCode.MIRROR_AHEAD,
        )
    if completions:
        expected_outcome = "completed" if completed else None
        if completions[-1].payload.outcome != expected_outcome:
            raise mismatch("workflow completion differs from state-v3", completions[-1].record_id)
        completion = completions[-1]
        final_binding_id = completion.payload.final_binding_id
        if final_binding_id is not None:
            final_binding = records_by_id.get(final_binding_id)
            if (
                final_binding is None
                or not isinstance(final_binding.payload, BindingPayload)
                or final_binding.fingerprint != completion.fingerprint
            ):
                raise mismatch(
                    "workflow completion references an unknown, invalid, or "
                    "fingerprint-mismatched final binding",
                    completion.record_id,
                )

    return ResumeResolution(state, mode, head, replay)


def _mirror_difference_code(
    record_facts: set[object], mirror_facts: set[object]
) -> ReplayDiagnosticCode:
    """Classify a strict mirror superset separately from ambiguous divergence."""
    if mirror_facts - record_facts and not record_facts - mirror_facts:
        return ReplayDiagnosticCode.MIRROR_AHEAD
    return ReplayDiagnosticCode.MIRROR_AMBIGUOUS


def _finding_statuses(state: WorkflowState) -> dict[str, str]:
    statuses: dict[str, str] = {}
    raw = state.runtime_history
    candidates: list[object] = []
    if isinstance(raw, dict) and set(raw) == {"current", "archive"}:
        archive = raw.get("archive")
        if isinstance(archive, list):
            candidates.extend(archive)
        candidates.append(raw.get("current"))
    elif raw is not None:
        candidates.append(raw)
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("findings"), list):
            continue
        for finding in candidate["findings"]:
            if not isinstance(finding, dict):
                continue
            finding_id = finding.get("finding_id")
            status = finding.get("status")
            if isinstance(finding_id, str) and isinstance(status, str):
                statuses[finding_id] = status.lower()
    for unit in state.work_units:
        for finding_id in unit.open_findings:
            # ``open_findings`` records the immutable attribution carried into a
            # correction work unit.  It is not the live finding mirror: an
            # approving reviewer may close that finding while the same work unit
            # remains current.  Prefer the status projected from runtime_history
            # and use the work-unit tuple only to recover legacy mirrors which do
            # not contain a finding entry yet.
            statuses.setdefault(finding_id, "open")
    return statuses


def _recoverable_pending_review_finding_gap(
    state: WorkflowState,
    chain: tuple[ArtifactRecord, ...],
    mirror_statuses: dict[str, str],
    latest_findings: dict[str, ArtifactRecord],
) -> bool:
    """Admit one durable review for exact local replay after a failed checkpoint."""
    unit = state.current_work_unit
    reviewer_by_step = {
        WorkflowStep.CLAUDE_SLICE_REVIEW: "claude",
        WorkflowStep.ANTIGRAVITY_SLICE_REVIEW: "antigravity",
    }
    reviewer = reviewer_by_step.get(state.current_step)
    if unit.kind is not WorkUnitKind.CORRECTION or reviewer is None:
        return False
    logical_id = f"review-{reviewer}-{unit.work_unit_id}-{unit.round_number}"
    reviews = tuple(
        record
        for record in chain
        if isinstance(record.payload, ReviewPayload)
        and record.logical_id == logical_id
        and record.payload.work_unit_id == str(unit.work_unit_id)
        and record.payload.reviewer.value == reviewer
        and record.payload.verdict in {"approved", "denied"}
    )
    if len(reviews) != 1 or _state_has_review_event(
        state,
        work_unit_id=unit.work_unit_id,
        reviewer=reviewer,
        round_number=unit.round_number,
    ):
        return False
    review = reviews[0]
    review_ids = set(review.payload.finding_ids)
    if not set(unit.open_findings).issubset(review_ids):
        return False
    if not any(
        isinstance(record.payload, ValidationAttestationPayload)
        and record.fingerprint == review.fingerprint
        for record in chain
    ):
        return False
    changed_ids = {
        finding_id
        for finding_id in set(mirror_statuses) | set(latest_findings)
        if finding_id not in mirror_statuses
        or finding_id not in latest_findings
        or latest_findings[finding_id].payload.finding_status
        != mirror_statuses[finding_id]
    }
    if not changed_ids or not changed_ids.issubset(review_ids):
        return False
    if review.payload.verdict == "approved" and any(
        latest_findings[finding_id].payload.finding_status != "closed"
        for finding_id in changed_ids
        if finding_id in latest_findings
    ):
        return False
    record_positions = {record.record_id: index for index, record in enumerate(chain)}
    review_position = record_positions[review.record_id]
    for finding_id in changed_ids:
        transition = latest_findings.get(finding_id)
        if (
            transition is None
            or transition.payload.actor.value != reviewer
            or record_positions[transition.record_id] <= review_position
        ):
            return False
    return True


def _recoverable_pending_correction_record(
    state: WorkflowState,
    chain: tuple[ArtifactRecord, ...],
    record: ArtifactRecord,
) -> bool:
    """Recognize the next correction round durably written before its mirror."""
    payload = record.payload
    unit = state.current_work_unit
    reviewer_by_step = {
        WorkflowStep.CLAUDE_SLICE_REVIEW: "claude",
        WorkflowStep.ANTIGRAVITY_SLICE_REVIEW: "antigravity",
    }
    reviewer = reviewer_by_step.get(state.current_step)
    if (
        reviewer is None
        or unit.kind is not WorkUnitKind.CORRECTION
        or not isinstance(payload, CorrectionWorkUnitPayload)
        or record.logical_id != f"work-unit-{unit.work_unit_id}"
        or payload.slice_id != str(unit.slice_id)
        or payload.round_number != unit.round_number + 1
        or not payload.finding_ids
    ):
        return False
    logical_id = f"review-{reviewer}-{unit.work_unit_id}-{unit.round_number}"
    reviews = tuple(
        candidate
        for candidate in chain
        if isinstance(candidate.payload, ReviewPayload)
        and candidate.logical_id == logical_id
        and candidate.payload.work_unit_id == str(unit.work_unit_id)
        and candidate.payload.reviewer.value == reviewer
        and candidate.payload.verdict == "denied"
    )
    if len(reviews) != 1:
        return False
    review = reviews[0]
    prefix = "C-" if reviewer == "claude" else "A-"
    return (
        chain.index(review) < chain.index(record)
        and any(
            isinstance(candidate.payload, ValidationAttestationPayload)
            and candidate.fingerprint == review.fingerprint
            for candidate in chain
        )
        and set(payload.finding_ids).issubset(review.payload.finding_ids)
        and all(item.startswith(prefix) for item in payload.finding_ids)
    )


def _state_has_review_event(
    state: WorkflowState,
    *,
    work_unit_id: int,
    reviewer: str,
    round_number: int,
) -> bool:
    raw = state.runtime_history
    if not isinstance(raw, dict):
        return False
    candidates: list[object] = []
    if set(raw) == {"current", "archive"}:
        archive = raw.get("archive")
        if isinstance(archive, list):
            candidates.extend(archive)
        candidates.append(raw.get("current"))
    else:
        candidates.append(raw)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            candidate_work_unit = int(candidate.get("work_unit_id", -1))
        except (TypeError, ValueError):
            continue
        if candidate_work_unit != work_unit_id:
            continue
        events = candidate.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict) or event.get("kind") != "review":
                continue
            result = event.get("result")
            if (
                isinstance(result, dict)
                and result.get("reviewer") == reviewer
                and event.get("round_number") == round_number
            ):
                return True
    return False


def _attestation_facts(state: WorkflowState) -> set[tuple[str, str]]:
    facts: set[tuple[str, str]] = set()
    raw = state.runtime_history
    candidates: list[object] = []
    if isinstance(raw, dict) and set(raw) == {"current", "archive"}:
        archive = raw.get("archive")
        if isinstance(archive, list):
            candidates.extend(archive)
        candidates.append(raw.get("current"))
    elif raw is not None:
        candidates.append(raw)
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("attestations"), list):
            continue
        for attestation in candidate["attestations"]:
            if not isinstance(attestation, dict):
                continue
            attestation_id = attestation.get("attestation_id")
            fingerprint = attestation.get("diff_fingerprint")
            if isinstance(attestation_id, str) and isinstance(fingerprint, str):
                facts.add((attestation_id, fingerprint))
    return facts
