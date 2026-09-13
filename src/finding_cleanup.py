"""Record-derived finding balances and bounded cleanup work selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from artifact_models import ArtifactRecord, FindingTransitionPayload
from contracts import FindingRecord
from finding_order import sorted_finding_ids
from finding_reducer import (
    is_closed_finding_transition,
    project_open_set,
    project_request_subset,
)
from finding_signature import authorized_repository_paths
from workflow_state import WorkflowState, WorkUnitKind, WorkUnitRecord


# Cleanup scheduling is dormant.  ``None`` is the explicit threshold switch:
# balance derivation and the bounded planner remain available for measurement
# and controlled backtests, but production never starts the parallel cleanup
# review path.  The former measured threshold was 18.
FINDING_CLEANUP_THRESHOLD: int | None = None
FINDING_CLEANUP_BACKTEST_THRESHOLD = 18
FINDING_CLEANUP_BATCH_LIMIT = 32


@dataclass(frozen=True, slots=True)
class SliceFindingBalance:
    slice_id: int
    opened: int
    closed: int

    @property
    def net(self) -> int:
        return self.opened - self.closed


@dataclass(frozen=True, slots=True)
class FindingCleanupPlan:
    finding_ids: tuple[str, ...]
    scope_paths: tuple[str, ...]


def is_finding_cleanup_work_unit(
    state: WorkflowState, unit: WorkUnitRecord | None = None
) -> bool:
    """Identify the sliceless cleanup encoded through B110's correction path.

    A final-review correction owns a new technical SliceBoundary.  A cleanup
    deliberately reuses a Slice id that already has a regular Slice work unit,
    so it remains distinguishable even for direct IMPLEMENT runs without an
    approved plan.
    """

    candidate = state.current_work_unit if unit is None else unit
    return (
        candidate.kind is WorkUnitKind.CORRECTION
        and any(
            prior.kind is WorkUnitKind.SLICE
            and prior.slice_id == candidate.slice_id
            and prior.work_unit_id < candidate.work_unit_id
            for prior in state.work_units
        )
    )


def derive_slice_finding_balances(
    records: Iterable[ArtifactRecord], state: WorkflowState
) -> tuple[SliceFindingBalance, ...]:
    """Derive opened/closed counts for planned Slice work units from records."""

    slice_by_work_unit = {
        str(unit.work_unit_id): unit.slice_id
        for unit in state.work_units
        if unit.kind is WorkUnitKind.SLICE
        and unit.slice_id <= len(state.planned_slices or state.slices)
    }
    counts: dict[int, list[int]] = {
        slice_id: [0, 0] for slice_id in sorted(set(slice_by_work_unit.values()))
    }
    for record in records:
        payload = record.payload
        if not isinstance(payload, FindingTransitionPayload):
            continue
        slice_id = slice_by_work_unit.get(payload.work_unit_id or "")
        if slice_id is None:
            continue
        if payload.action == "opened":
            counts[slice_id][0] += 1
        elif is_closed_finding_transition(record):
            counts[slice_id][1] += 1
    return tuple(
        SliceFindingBalance(slice_id, opened, closed)
        for slice_id, (opened, closed) in sorted(counts.items())
    )


def positive_balance_streak(balances: Sequence[SliceFindingBalance]) -> int:
    """Return the trailing number of Slices whose finding balance is positive."""

    streak = 0
    for balance in reversed(balances):
        if balance.net <= 0:
            break
        streak += 1
    return streak


def finding_cleanup_scope_paths(
    findings: tuple[FindingRecord, ...],
    finding_ids: tuple[str, ...],
    *,
    repository_root: Path,
) -> tuple[str, ...]:
    """Return the existing worktree paths named by the selected findings."""

    selected = project_request_subset(
        findings,
        finding_ids=sorted_finding_ids(finding_ids),
        open_only=False,
    ).findings
    return tuple(
        sorted(
            {
                path
                for finding in selected
                for path in authorized_repository_paths(
                    repository_root,
                    finding.summary, finding.acceptance_test
                )
            }
        )
    )


def plan_finding_cleanup(
    findings: tuple[FindingRecord, ...],
    *,
    repository_root: Path,
    previously_addressed_ids: Iterable[str] = (),
    threshold: int | None = FINDING_CLEANUP_THRESHOLD,
) -> FindingCleanupPlan | None:
    """Select one bounded cleanup batch, or return ``None`` when dormant/below threshold.

    Findings already offered by an earlier cleanup are excluded even when the
    reviewer left them open.  This makes a no-progress round self-terminating:
    another round is considered only after enough new open findings accumulate.
    """

    if threshold is None:
        return None
    if threshold < 1:
        raise ValueError("finding cleanup threshold must be positive or None")
    addressed = frozenset(previously_addressed_ids)
    eligible = tuple(
        finding
        for finding in project_open_set(findings).findings
        if finding.finding_id not in addressed
    )
    if len(eligible) < threshold:
        return None
    selected_ids = sorted_finding_ids(
        item.finding_id for item in eligible[:FINDING_CLEANUP_BATCH_LIMIT]
    )
    scope_paths = finding_cleanup_scope_paths(
        findings,
        selected_ids,
        repository_root=repository_root,
    )
    if not scope_paths:
        return None
    return FindingCleanupPlan(selected_ids, scope_paths)


__all__ = [
    "FINDING_CLEANUP_BATCH_LIMIT",
    "FINDING_CLEANUP_BACKTEST_THRESHOLD",
    "FINDING_CLEANUP_THRESHOLD",
    "FindingCleanupPlan",
    "SliceFindingBalance",
    "derive_slice_finding_balances",
    "finding_cleanup_scope_paths",
    "is_finding_cleanup_work_unit",
    "plan_finding_cleanup",
    "positive_balance_streak",
]
