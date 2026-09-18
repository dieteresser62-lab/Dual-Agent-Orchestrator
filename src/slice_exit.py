"""Pure, dormant Slice-exit evaluation over authoritative artifact records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from artifact_models import (
    ArtifactRecord,
    BindingPayload,
    CorrectionWorkUnitPayload,
    FindingSeverity,
    FingerprintKind,
    PlanPayload,
    SliceSpec,
    WorkUnitPayload,
)
from finding_order import sorted_finding_ids
from finding_reducer import (
    FindingTransitionProjection,
    SliceExitFindingHeadProjection,
    project_slice_exit_findings,
)
from finding_responsibility import (
    BranchPlanningResponsibility,
    FindingResponsibility,
    SliceResponsibility,
    responsibility_document,
)
from finding_signature import mentioned_repository_paths


CONDITION_4_ACCEPTANCE_UNDECIDABLE = (
    "target Slice has no record-bound acceptance criteria, so its named condition "
    "cannot be resolved from the record chain"
)


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
    responsibility_finding_ids: tuple[str, ...]
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

    target_slice_id = str(slice_id)
    run_records = tuple(record for record in records if record.run_id == run_id)
    plan = _select_plan(run_records, approved_plan_commit)
    plan_commit = (
        approved_plan_commit
        if approved_plan_commit is not None
        else "" if plan is None else plan.approved_plan_commit
    )
    finding_projection = project_slice_exit_findings(run_records)
    events = finding_projection.events
    heads = {item.finding_id: item for item in finding_projection.heads}
    cohort_ids, slice_work_unit_ids, boundary_reasons = _slice_cohort(
        run_records,
        events,
        run_id=run_id,
        approved_plan_commit=plan_commit,
        slice_id=target_slice_id,
    )

    condition_1_reasons: list[str] = []
    condition_2_reasons: list[str] = list(boundary_reasons)
    condition_3_reasons: list[str] = []
    condition_4_reasons: list[str] = []
    condition_4_unknown: list[str] = []
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

        if head.is_open and not _valid_responsibility(head.responsibility):
            condition_2_reasons.append(
                f"open finding {finding_id} has no valid responsibility assignment"
            )

        if isinstance(head.responsibility, SliceResponsibility) and (
            head.responsibility.target_run_id == run_id
            and head.responsibility.approved_plan_commit == plan_commit
            and head.responsibility.slice_id == target_slice_id
        ):
            condition_3_reasons.append(
                f"{finding_id} still has responsibility slice:{target_slice_id} at exit"
            )

        slice_routes = tuple(
            event
            for event in events
            if event.payload.finding_id == finding_id
            and event.payload.action == "routed"
            and event.payload.work_unit_id in slice_work_unit_ids
            and isinstance(event.payload.responsibility, SliceResponsibility)
        )
        if (
            head.is_open
            and isinstance(head.responsibility, SliceResponsibility)
            and not (
                head.responsibility.target_run_id == run_id
                and head.responsibility.approved_plan_commit == plan_commit
                and head.responsibility.slice_id == target_slice_id
            )
            and head.last_assignment not in slice_routes
        ):
            condition_4_reasons.append(
                f"Slice routing for {finding_id} is not recorded in a Work Unit "
                f"bound to Slice {target_slice_id}"
            )
        for route in slice_routes:
            assert isinstance(route.payload.responsibility, SliceResponsibility)
            route_errors = _slice_route_errors(
                finding_id,
                head,
                responsibility=route.payload.responsibility,
                run_id=run_id,
                current_slice_id=target_slice_id,
                plan=plan,
                records=run_records,
            )
            if route_errors:
                condition_4_reasons.extend(route_errors)
            elif not _slice_route_has_record_criteria(
                route.payload.responsibility, plan
            ):
                condition_4_unknown.append(
                    f"{finding_id}: {CONDITION_4_ACCEPTANCE_UNDECIDABLE}"
                )

        if head.is_open and isinstance(
            head.responsibility, BranchPlanningResponsibility
        ):
            assignment = head.last_assignment
            if (
                assignment is None
                or assignment.payload.action != "routed"
                or assignment.payload.work_unit_id is None
                or assignment.payload.work_unit_id not in slice_work_unit_ids
                or assignment.payload.responsibility != head.responsibility
                or assignment.record.fingerprint.kind is not FingerprintKind.IMPLEMENTATION
            ):
                condition_5_reasons.append(
                    f"Branch routing for {finding_id} is not completely "
                    "recorded and implementation-fingerprint-bound"
                )
            else:
                condition_5_reasons.append(
                    f"Branch routing for {finding_id} cannot be matched to a "
                    "run-bound family identity before point 67"
                )

        if head.is_closed:
            if not head.has_complete_closure_record:
                condition_6_reasons.append(
                    f"closure decision for {finding_id} has no complete transition record"
                )
        elif head.last_assignment is None:
            condition_6_reasons.append(
                f"responsibility decision for {finding_id} has no transition record"
            )

    conditions = (
        _condition(1, condition_1_reasons),
        _condition(2, condition_2_reasons),
        _condition(3, condition_3_reasons),
        _condition(4, condition_4_reasons, condition_4_unknown),
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
        responsibility_finding_ids=cohort_ids,
        conditions=conditions,
        status=status,
    )


def _slice_cohort(
    records: Sequence[ArtifactRecord],
    events: Sequence[FindingTransitionProjection],
    *,
    run_id: str,
    approved_plan_commit: str,
    slice_id: str,
) -> tuple[tuple[str, ...], frozenset[str], tuple[str, ...]]:
    start_unit = next(
        (
            record.payload
            for record in records
            if isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
            and record.payload.slice_id == slice_id
        ),
        None,
    )
    work_unit_ids = frozenset(
        record.logical_id.removeprefix("work-unit-")
        for record in records
        if record.logical_id.startswith("work-unit-")
        and isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
        and record.payload.slice_id == slice_id
    )
    cohort: set[str] = set()
    reasons: tuple[str, ...] = ()
    if isinstance(start_unit, WorkUnitPayload):
        cohort.update(start_unit.open_finding_ids)
    elif isinstance(start_unit, CorrectionWorkUnitPayload):
        cohort.update(start_unit.finding_ids)
    else:
        reasons = (f"Slice {slice_id} has no record-bound start Work Unit",)
    cohort.update(
        event.payload.finding_id
        for event in events
        if event.payload.action == "opened"
        and event.payload.origin_slice_id == slice_id
    )
    cohort.update(
        event.payload.finding_id
        for event in events
        if event.payload.action == "routed"
        and isinstance(event.payload.responsibility, SliceResponsibility)
        and event.payload.responsibility.target_run_id == run_id
        and event.payload.responsibility.approved_plan_commit == approved_plan_commit
        and event.payload.responsibility.slice_id == slice_id
    )
    return sorted_finding_ids(cohort), work_unit_ids, reasons


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


def _valid_responsibility(
    responsibility: FindingResponsibility | None,
) -> bool:
    if responsibility is None:
        return False
    try:
        responsibility_document(responsibility)
    except ValueError:
        return False
    return True


def _slice_route_errors(
    finding_id: str,
    head: SliceExitFindingHeadProjection,
    *,
    responsibility: SliceResponsibility,
    run_id: str,
    current_slice_id: str,
    plan: PlanPayload | None,
    records: Sequence[ArtifactRecord],
) -> tuple[str, ...]:
    target = responsibility.slice_id
    if responsibility.target_run_id != run_id:
        return (
            f"Slice routing for {finding_id} targets run "
            f"{responsibility.target_run_id}, whose plan is absent from this record chain",
        )
    if plan is None or responsibility.approved_plan_commit != plan.approved_plan_commit:
        return (
            f"Slice routing for {finding_id} has no matching approved Plan record",
        )
    positions = {item.slice_id: index for index, item in enumerate(plan.slices)}
    if current_slice_id not in positions or target not in positions:
        return (
            f"Slice routing for {finding_id} targets unknown planned Slice {target}",
        )
    if positions[target] <= positions[current_slice_id]:
        return (
            f"Slice routing for {finding_id} does not target a later planned Slice",
        )
    if _slice_is_committed(records, target):
        return (
            f"Slice routing for {finding_id} targets already completed Slice {target}",
        )
    target_spec = next(item for item in plan.slices if item.slice_id == target)
    mentioned_paths = mentioned_repository_paths(head.summary, head.acceptance_test)
    if not mentioned_paths:
        return (
            f"Slice routing for {finding_id} has no record-bound remediation path "
            "with which to check target scope",
        )
    uncovered = tuple(
        path for path in mentioned_paths if not _scope_covers(target_spec, path)
    )
    if uncovered:
        return (
            f"Slice routing for {finding_id} targets Slice {target}, whose approved "
            f"scope does not cover {', '.join(uncovered)}",
        )
    if target_spec.acceptance_criteria:
        criterion_id = responsibility.acceptance_criterion_id
        if criterion_id is None:
            return (
                f"Slice routing for {finding_id} does not name an acceptance "
                f"condition of target Slice {target}",
            )
        known_ids = {
            criterion.criterion_id for criterion in target_spec.acceptance_criteria
        }
        if criterion_id not in known_ids:
            return (
                f"Slice routing for {finding_id} names unresolved acceptance "
                f"condition {criterion_id} in target Slice {target}",
            )
    return ()


def _slice_route_has_record_criteria(
    responsibility: SliceResponsibility, plan: PlanPayload | None
) -> bool:
    if plan is None:
        return False
    target = next(
        (item for item in plan.slices if item.slice_id == responsibility.slice_id),
        None,
    )
    return target is not None and bool(target.acceptance_criteria)


def _scope_covers(spec: SliceSpec, path: str) -> bool:
    return any(
        path == scope or path.startswith(scope.rstrip("/") + "/")
        for scope in spec.paths
    )


def _slice_is_committed(records: Sequence[ArtifactRecord], slice_id: str) -> bool:
    prefix = f"commit-{slice_id}"
    return any(
        isinstance(record.payload, BindingPayload)
        and record.payload.binding_kind == "commit"
        and (
            record.logical_id == prefix
            or record.logical_id.startswith(prefix + "-")
        )
        for record in records
    )


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
    "CONDITION_4_ACCEPTANCE_UNDECIDABLE",
    "SliceExitCondition",
    "SliceExitEvaluation",
    "SliceExitStatus",
    "evaluate_slice_exit",
]
