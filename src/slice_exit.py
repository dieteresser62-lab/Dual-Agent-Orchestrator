"""Pure Slice-exit evaluation over authoritative artifact records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from artifact_models import (
    ArtifactRecord,
    FindingHandoffExportPayload,
    FindingSeverity,
    PlanPayload,
    ReviewPayload,
    WorkUnitPayload,
    finding_transition_sequence_sha256,
    flatten_finding_transition_history,
)
from finding_order import sorted_finding_ids
from finding_reducer import SliceExitFindingProjection, project_slice_exit_findings


class SliceExitStatus(StrEnum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class SliceExitCondition:
    number: int
    status: SliceExitStatus
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SliceExitEvaluation:
    run_id: str
    approved_plan_commit: str
    slice_id: str
    cohort_finding_ids: tuple[str, ...]
    conditions: tuple[SliceExitCondition, ...]
    status: SliceExitStatus

    @property
    def commit_eligible(self) -> bool:
        return self.status is SliceExitStatus.SATISFIED

    def condition(self, number: int) -> SliceExitCondition:
        return next(item for item in self.conditions if item.number == number)


def evaluate_slice_exit(
    records: Sequence[ArtifactRecord],
    *,
    run_id: str,
    slice_id: int | str,
    approved_plan_commit: str | None = None,
) -> SliceExitEvaluation:
    """Evaluate all six E4 conditions without I/O or projection-state input.

    The caller supplies an already selected record prefix.  Consequently the
    result is stable for that exact prefix and recovery can derive it again
    without consulting Markdown, state-v3, a provider, or the worktree.
    """

    target_slice_id = _canonical_slice_id(slice_id)
    run_records = tuple(record for record in records if record.run_id == run_id)
    plan = _select_plan(run_records, approved_plan_commit)
    plan_commit = (
        approved_plan_commit
        if approved_plan_commit is not None
        else "" if plan is None else plan.approved_plan_commit
    )
    finding_projection = project_slice_exit_findings(run_records)
    heads = {item.finding_id: item for item in finding_projection.heads}
    start_unit = next(
        (
            record.payload
            for record in run_records
            if isinstance(record.payload, WorkUnitPayload)
            and record.payload.slice_id == target_slice_id
        ),
        None,
    )
    boundary_reasons = (
        ()
        if start_unit is not None
        else (f"Slice {target_slice_id} has no record-bound start Work Unit",)
    )
    # Findings are immutably owned by the Slice in which they were opened. The
    # Work Unit contribution preserves the fail-closed case where a bound
    # Finding has no opening record.
    cohort_ids = slice_commit_decision_finding_ids(
        tuple(
            item.finding_id
            for item in finding_projection.heads
            if _canonical_slice_id(item.origin_slice_id) == target_slice_id
        ),
        _work_unit_bound_finding_ids(run_records, target_slice_id),
    )

    condition_1_reasons: list[str] = []
    condition_2_reasons: list[str] = list(boundary_reasons)
    condition_3_reasons: list[str] = []
    condition_4_reasons: list[str] = []
    condition_5_reasons: list[str] = []
    condition_6_reasons: list[str] = list(boundary_reasons)

    for finding_id in cohort_ids:
        head = heads.get(finding_id)
        if head is None:
            reason = f"{finding_id} is bound to Slice {target_slice_id} but has no opening record"
            condition_2_reasons.append(reason)
            condition_6_reasons.append(reason)
            continue

        if head.is_open and head.severity is FindingSeverity.BLOCKER:
            condition_1_reasons.append(f"open BLOCKER {finding_id} remains in A_s")
        if head.invalid_downgrade is not None:
            condition_1_reasons.append(
                f"{finding_id} was reclassified from BLOCKER to OBSERVATION "
                "without earlier record-bound evidence"
            )

        if head.is_closed:
            if not head.has_complete_closure_record:
                condition_6_reasons.append(
                    f"closure decision for {finding_id} has no complete transition record"
                )

    conditions = (
        _condition(1, condition_1_reasons),
        _condition(2, condition_2_reasons),
        _condition(3, condition_3_reasons),
        _condition(4, condition_4_reasons),
        _condition(5, condition_5_reasons),
        _condition(6, condition_6_reasons),
    )
    status = (
        SliceExitStatus.VIOLATED
        if any(item.status is SliceExitStatus.VIOLATED for item in conditions)
        else SliceExitStatus.INDETERMINATE
        if any(item.status is SliceExitStatus.INDETERMINATE for item in conditions)
        else SliceExitStatus.SATISFIED
    )
    return SliceExitEvaluation(
        run_id=run_id,
        approved_plan_commit=plan_commit,
        slice_id=target_slice_id,
        cohort_finding_ids=cohort_ids,
        conditions=conditions,
        status=status,
    )


def _select_plan(
    records: Sequence[ArtifactRecord], approved_plan_commit: str | None
) -> PlanPayload | None:
    return next(
        (
            record.payload
            for record in reversed(records)
            if isinstance(record.payload, PlanPayload)
            and (
                approved_plan_commit is None
                or record.payload.approved_plan_commit == approved_plan_commit
            )
        ),
        None,
    )


def workflow_completion_blocking_finding_ids(
    records: Sequence[ArtifactRecord],
    *,
    run_id: str,
) -> tuple[str, ...]:
    """Return every totally-quantified Finding that forbids run completion."""

    run_records = tuple(record for record in records if record.run_id == run_id)
    projection = project_slice_exit_findings(run_records)
    heads = {item.finding_id: item for item in projection.heads}
    total_finding_ids = slice_commit_decision_finding_ids(
        tuple(item.finding_id for item in projection.heads),
        _work_unit_bound_finding_ids(run_records),
    )
    handed_off_finding_ids = _handoff_exported_finding_ids(
        run_records, projection
    )
    blocked: list[str] = []
    for finding_id in total_finding_ids:
        head = heads.get(finding_id)
        if head is None:
            blocked.append(finding_id)
            continue
        if head.is_closed:
            if not head.has_complete_closure_record:
                blocked.append(head.finding_id)
            continue
        if head.finding_id in handed_off_finding_ids:
            continue
        blocked.append(head.finding_id)
    return sorted_finding_ids(blocked)


def _handoff_exported_finding_ids(
    records: Sequence[ArtifactRecord],
    projection: SliceExitFindingProjection,
) -> frozenset[str]:
    """Return open Finding heads covered by one valid, recorded handoff export."""

    events = projection.events
    finding_event_ids: dict[str, tuple[str, ...]] = {
        finding_id: tuple(
            event.source_record_id
            for event in events
            if event.payload.finding_id == finding_id
        )
        for finding_id in {event.payload.finding_id for event in events}
    }
    exported: set[str] = set()
    for position, record in enumerate(records):
        payload = record.payload
        if not isinstance(payload, FindingHandoffExportPayload):
            continue
        prefix = tuple(records[:position])
        predecessor_id = prefix[-1].record_id if prefix else None
        if (
            payload.source_run_id != record.run_id
            or payload.source_head_record_id != predecessor_id
            or record.predecessor_ids != ((predecessor_id,) if predecessor_id else ())
        ):
            continue
        approval = next(
            (
                candidate
                for candidate in prefix
                if candidate.record_id == payload.approval_review_record_id
            ),
            None,
        )
        if (
            approval is None
            or not isinstance(approval.payload, ReviewPayload)
            or approval.payload.verdict != "approved"
            or not any(
                isinstance(candidate.payload, PlanPayload)
                and candidate.payload.approved_plan_commit
                == payload.approved_plan_commit
                for candidate in prefix
            )
        ):
            continue
        transitions = flatten_finding_transition_history(prefix)
        transition_ids = tuple(item.record_id for item in transitions)
        if (
            payload.finding_transition_record_ids != transition_ids
            or payload.finding_transitions_sha256
            != finding_transition_sequence_sha256(transitions)
        ):
            continue
        covered = frozenset(transition_ids)
        exported.update(
            finding_id
            for finding_id, event_ids in finding_event_ids.items()
            if event_ids and all(event_id in covered for event_id in event_ids)
        )
    return frozenset(exported)


def slice_commit_decision_finding_ids(
    finding_ids: Sequence[str],
    bound_finding_ids: Sequence[str],
) -> tuple[str, ...]:
    """Return the exact Finding set quantified by the Slice exit conditions."""

    return sorted_finding_ids(
        (
            *finding_ids,
            *bound_finding_ids,
        )
    )


def _work_unit_bound_finding_ids(
    records: Sequence[ArtifactRecord],
    slice_id: str | None = None,
) -> tuple[str, ...]:
    return tuple(
        finding_id
        for record in records
        if isinstance(record.payload, WorkUnitPayload)
        and (
            slice_id is None
            or _canonical_slice_id(record.payload.slice_id)
            == _canonical_slice_id(slice_id)
        )
        for finding_id in record.payload.open_finding_ids
    )


def _canonical_slice_id(value: int | str) -> str:
    raw = str(value)
    return str(int(raw)) if raw.isdigit() else raw


def _condition(
    number: int,
    violations: Sequence[str],
    indeterminate: Sequence[str] = (),
) -> SliceExitCondition:
    if violations:
        return SliceExitCondition(
            number, SliceExitStatus.VIOLATED, tuple(dict.fromkeys(violations))
        )
    if indeterminate:
        return SliceExitCondition(
            number,
            SliceExitStatus.INDETERMINATE,
            tuple(dict.fromkeys(indeterminate)),
        )
    return SliceExitCondition(number, SliceExitStatus.SATISFIED)


__all__ = [
    "SliceExitCondition",
    "SliceExitEvaluation",
    "SliceExitStatus",
    "evaluate_slice_exit",
    "slice_commit_decision_finding_ids",
    "workflow_completion_blocking_finding_ids",
]
