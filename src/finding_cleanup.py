"""Record-derived Slice finding balances."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from artifact_models import (
    ArtifactRecord,
    FindingTransitionPayload,
    PlanPayload,
    WorkUnitPayload,
    WorkflowTransitionPayload,
)
from finding_reducer import is_closed_finding_transition


@dataclass(frozen=True, slots=True)
class SliceFindingBalance:
    slice_id: int
    opened: int
    closed: int
    locally_fixed: int = 0
    rejected: int = 0

    @property
    def net(self) -> int:
        return self.opened - self.closed


def derive_slice_finding_balances(
    records: Iterable[ArtifactRecord],
) -> tuple[SliceFindingBalance, ...]:
    """Derive the B55/E6 balance solely from typed record-chain facts."""

    chain = tuple(records)
    slice_by_work_unit = _slice_work_units(chain)
    counts: dict[int, list[int]] = {
        slice_id: [0, 0, 0, 0]
        for slice_id in sorted(set(slice_by_work_unit.values()))
    }
    for record in chain:
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
            if payload.closure_kind == "fixed":
                counts[slice_id][2] += 1
            elif payload.closure_kind == "rejected":
                counts[slice_id][3] += 1
    return tuple(
        SliceFindingBalance(slice_id, *values)
        for slice_id, values in sorted(counts.items())
    )


def _slice_work_units(records: Sequence[ArtifactRecord]) -> dict[str, int]:
    """Map regular implementation Work Units to numeric planned Slice ids."""

    plan_slice_ids = {
        spec.slice_id
        for record in records
        if isinstance(record.payload, PlanPayload)
        for spec in record.payload.slices
    }
    steps_by_unit: dict[str, set[str]] = {}
    for record in records:
        payload = record.payload
        if (
            isinstance(payload, WorkflowTransitionPayload)
            and payload.work_unit_id is not None
            and payload.step is not None
        ):
            steps_by_unit.setdefault(payload.work_unit_id, set()).add(payload.step)
    excluded_steps = {
        "codex_plan",  # allowlist:provider -- persisted workflow step
        "claude_plan_review",  # allowlist:provider -- persisted workflow step
        "codex_plan_revision",  # allowlist:provider -- persisted workflow step
        "claude_branch_discovery",  # allowlist:provider -- persisted workflow step
    }
    result: dict[str, int] = {}
    for record in records:
        payload = record.payload
        if (
            not isinstance(payload, WorkUnitPayload)
            or not record.logical_id.startswith("work-unit-")
            or not payload.slice_id.isdigit()
        ):
            continue
        unit_id = record.logical_id.removeprefix("work-unit-")
        steps = steps_by_unit.get(unit_id, set())
        if steps and not (steps - excluded_steps):
            continue
        if plan_slice_ids and payload.slice_id not in plan_slice_ids:
            continue
        result[unit_id] = int(payload.slice_id)
    return result


def positive_balance_streak(balances: Sequence[SliceFindingBalance]) -> int:
    """Return the trailing number of Slices whose finding balance is positive."""

    streak = 0
    for balance in reversed(balances):
        if balance.net <= 0:
            break
        streak += 1
    return streak


__all__ = [
    "SliceFindingBalance",
    "derive_slice_finding_balances",
    "positive_balance_streak",
]
