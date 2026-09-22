"""Pure Slice-exit evaluation over authoritative artifact records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from artifact_models import (
    ArtifactRecord,
    FindingSeverity,
    PlanPayload,
    WorkUnitPayload,
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
    """Evaluate the single Slice-commit condition from authoritative records.

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
    # Findings are immutably owned by the Slice in which they were opened. The
    # Work Unit contribution keeps the quantified cohort lossless; authoritative
    # replay rejects a bound Finding without its opening transition beforehand.
    cohort_ids = slice_commit_decision_finding_ids(
        tuple(
            item.finding_id
            for item in finding_projection.heads
            if _canonical_slice_id(item.origin_slice_id) == target_slice_id
        ),
        _work_unit_bound_finding_ids(run_records, target_slice_id),
    )

    blocker_reasons: list[str] = []

    for finding_id in cohort_ids:
        head = heads.get(finding_id)
        if head is None:
            continue

        if head.is_open and head.severity is FindingSeverity.BLOCKER:
            blocker_reasons.append(f"open BLOCKER {finding_id} remains in A_s")

    conditions = (_condition(1, blocker_reasons),)
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
    blocked: list[str] = []
    for finding_id in total_finding_ids:
        head = heads.get(finding_id)
        if (
            head is not None
            and head.is_open
            and head.severity is FindingSeverity.BLOCKER
        ):
            blocked.append(head.finding_id)
    return sorted_finding_ids(blocked)


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
