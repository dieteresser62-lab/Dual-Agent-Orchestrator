"""Fail-closed resume resolution for legacy state-v3 and structured-v1 runs."""

from __future__ import annotations

from dataclasses import dataclass
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
)
from artifact_store import ArtifactStore, ArtifactStoreError
from workflow_state import (
    AgentFailureKind,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
)


class ArtifactResumeError(ValueError):
    """Raised when records cannot safely rehydrate their state-v3 mirror."""


@dataclass(frozen=True, slots=True)
class ResumeResolution:
    state: WorkflowState
    mode: ProtocolMode
    record_head_id: str | None


def resolve_resume_state(repository_root: Path, state: WorkflowState) -> ResumeResolution:
    """Resolve the immutable protocol binding and verify structured mirror facts.

    Historical states without a binding remain on the legacy path and are never
    imported.  A structured binding, in contrast, makes the append-only record
    chain mandatory and authoritative for every fact represented by that chain.
    """
    mode = state.effective_protocol_mode
    if mode is ProtocolMode.LEGACY_STATE_V3:
        return ResumeResolution(state, mode, None)

    try:
        chain = ArtifactStore(repository_root, state.run_id).load_chain()
    except ArtifactStoreError as exc:
        raise ArtifactResumeError(
            f"structured-v1 record chain for run {state.run_id!r} is invalid: {exc}; "
            "repair or restore the append-only records before resuming"
        ) from exc
    if not chain:
        raise ArtifactResumeError(
            f"structured-v1 run {state.run_id!r} has no records; restore its record "
            "directory before resuming"
        )

    head = chain[-1].record_id

    def mismatch(message: str, record_id: str | None = None) -> ArtifactResumeError:
        location = record_id or head
        return ArtifactResumeError(
            f"structured-v1 resume mismatch at record {location}: {message}; "
            "repair the state mirror or restore the matching record chain before resuming"
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
    for record in work_records:
        payload = record.payload
        assert isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
        unit_id = record.logical_id.removeprefix("work-unit-")
        unit = unit_by_id.get(unit_id)
        slice_record = slice_by_id.get(payload.slice_id)
        if unit is None or slice_record is None:
            raise mismatch("work-unit record has no state-v3 counterpart", record.record_id)
        if (
            str(unit.slice_id) != payload.slice_id
            or payload.round_number > unit.round_number
            or payload.paths != slice_record.scope_paths
        ):
            raise mismatch("work-unit round, slice, or path allowlist differs", record.record_id)
        if isinstance(payload, CorrectionWorkUnitPayload) and (
            unit.kind is not WorkUnitKind.CORRECTION
            or payload.finding_ids != unit.open_findings
        ):
            raise mismatch(
                "correction work-unit finding attribution differs from state-v3",
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
            raise mismatch("current work unit has no structured record")
        latest = current_records[-1]
        payload = latest.payload
        assert isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
        if payload.round_number != current.round_number:
            raise mismatch("current work-unit round differs from the record chain", latest.record_id)

    plans = [item for item in chain if item.record_type is RecordType.PLAN]
    if (
        state.execution_mode == "IMPLEMENT"
        and state.planned_slices
        and state.work_plan_path
    ):
        if not plans:
            raise mismatch("approved plan has no structured record")
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
        )

    finding_statuses = _finding_statuses(state)
    latest_findings: dict[str, ArtifactRecord] = {}
    for record in chain:
        if isinstance(record.payload, FindingTransitionPayload):
            latest_findings[record.payload.finding_id] = record
    if set(latest_findings) != set(finding_statuses):
        differing = next(iter(set(latest_findings) ^ set(finding_statuses)), None)
        record = latest_findings.get(differing) if differing is not None else None
        raise mismatch(
            "finding transitions differ from state-v3",
            None if record is None else record.record_id,
        )
    for finding_id, record in latest_findings.items():
        assert isinstance(record.payload, FindingTransitionPayload)
        if record.payload.finding_status != finding_statuses[finding_id]:
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
        )

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
        raise mismatch("completed slice is missing a structured commit binding")

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
        raise mismatch("state-v3 mirror reports workflow completion without a structured record")
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

    return ResumeResolution(state, mode, head)


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
            statuses[finding_id] = "open"
    return statuses


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
