"""Pure Slice-exit evaluation over authoritative artifact records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from artifact_models import (
    ArtifactRecord,
    BindingPayload,
    FamilyBindingPayload,
    FindingHandoffExportPayload,
    FindingSeverity,
    FingerprintKind,
    PlanPayload,
    ReviewPayload,
    RunProfilePayload,
    SliceSpec,
    WorkUnitPayload,
    finding_transition_sequence_sha256,
    flatten_finding_transition_history,
)
from finding_order import sorted_finding_ids
from finding_reducer import (
    FindingTransitionProjection,
    SliceExitFindingHeadProjection,
    SliceExitFindingProjection,
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
UNOWNED_OPEN_FINDING_DIAGNOSTIC = "OPEN-FINDING-WITHOUT-RESPONSIBILITY"
UNDECIDED_FINDING_DIAGNOSTIC = "FINDING-WITHOUT-VALID-EXIT"


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
    heads = {item.finding_id: item for item in finding_projection.heads}
    work_unit_slices = _work_unit_slices(run_records)
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
    # Exhaust the record-derived Finding set.  The Work Unit contribution
    # preserves the existing fail-closed case where a bound Finding has no
    # opening record; no responsibility, route, or origin predicate may remove
    # a projected Finding from this evaluation.
    cohort_ids = slice_commit_decision_finding_ids(
        tuple(item.finding_id for item in finding_projection.heads),
        _work_unit_bound_finding_ids(run_records),
    )
    plan_positions = (
        {} if plan is None else {
            item.slice_id: index for index, item in enumerate(plan.slices)
        }
    )
    plan_specs = {} if plan is None else {item.slice_id: item for item in plan.slices}
    committed_slice_ids = _committed_slice_ids(
        run_records, frozenset(plan_positions)
    )
    family_binding = _run_family_binding(run_records)

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

        if head.is_open and not isinstance(
            head.responsibility,
            (SliceResponsibility, BranchPlanningResponsibility),
        ):
            if not _valid_responsibility(head.responsibility):
                condition_2_reasons.append(
                    f"{UNOWNED_OPEN_FINDING_DIAGNOSTIC}: open finding {finding_id} "
                    "has no valid responsibility assignment"
                )
            else:
                condition_2_reasons.append(
                    f"{UNDECIDED_FINDING_DIAGNOSTIC}: open finding {finding_id} "
                    "has no responsibility target valid for Slice exit"
                )

        if isinstance(head.responsibility, SliceResponsibility) and (
            head.responsibility.target_run_id == run_id
            and head.responsibility.approved_plan_commit == plan_commit
            and head.responsibility.slice_id == target_slice_id
        ):
            condition_3_reasons.append(
                f"{finding_id} still has responsibility slice:{target_slice_id} at exit"
            )

        if (
            head.is_open
            and isinstance(head.responsibility, SliceResponsibility)
            and not (
                head.responsibility.target_run_id == run_id
                and head.responsibility.approved_plan_commit == plan_commit
                and head.responsibility.slice_id == target_slice_id
            )
        ):
            route = head.last_assignment
            source_slice_id = (
                None
                if route is None or route.payload.work_unit_id is None
                else work_unit_slices.get(route.payload.work_unit_id)
            )
            if (
                route is None
                or route.payload.action != "routed"
                or route.payload.responsibility != head.responsibility
                or source_slice_id is None
            ):
                condition_4_reasons.append(
                    f"Slice routing for {finding_id} is not recorded in a "
                    "record-bound source Work Unit"
                )
            else:
                route_errors = _slice_route_errors(
                    finding_id,
                    head,
                    responsibility=head.responsibility,
                    run_id=run_id,
                    source_slice_id=source_slice_id,
                    plan=plan,
                    plan_positions=plan_positions,
                    plan_specs=plan_specs,
                    committed_slice_ids=committed_slice_ids,
                )
                if route_errors:
                    condition_4_reasons.extend(route_errors)
                elif not _slice_route_has_record_criteria(
                    head.responsibility, plan_specs
                ):
                    condition_4_unknown.append(
                        f"{finding_id}: {CONDITION_4_ACCEPTANCE_UNDECIDABLE}"
                    )

        if head.is_open and isinstance(
            head.responsibility, BranchPlanningResponsibility
        ):
            condition_5_reasons.extend(
                _branch_route_errors(
                    finding_id,
                    head,
                    work_unit_ids=frozenset(work_unit_slices),
                    family_binding=family_binding,
                    require_route=True,
                )
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
            if isinstance(record.payload, WorkUnitPayload)
            and record.payload.slice_id == slice_id
        ),
        None,
    )
    work_unit_ids = frozenset(
        record.logical_id.removeprefix("work-unit-")
        for record in records
        if record.logical_id.startswith("work-unit-")
        and isinstance(record.payload, WorkUnitPayload)
        and record.payload.slice_id == slice_id
    )
    cohort: set[str] = set()
    reasons: tuple[str, ...] = ()
    if isinstance(start_unit, WorkUnitPayload):
        cohort.update(start_unit.open_finding_ids)
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
        and event.payload.work_unit_id in work_unit_ids
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


def slice_cohort_finding_ids(
    records: Sequence[ArtifactRecord],
    *,
    run_id: str,
    slice_id: int | str,
    approved_plan_commit: str | None = None,
) -> tuple[str, ...]:
    """Return historical ``A_s`` membership for E5 convergence only.

    E4 deliberately does not consume this filtered set: its exit decision is
    quantified over every Finding head in the run.
    """

    target_slice_id = str(slice_id)
    run_records = tuple(record for record in records if record.run_id == run_id)
    plan = _select_plan(run_records, approved_plan_commit)
    plan_commit = (
        approved_plan_commit
        if approved_plan_commit is not None
        else "" if plan is None else plan.approved_plan_commit
    )
    projection = project_slice_exit_findings(run_records)
    cohort, _work_units, _reasons = _slice_cohort(
        run_records,
        projection.events,
        run_id=run_id,
        approved_plan_commit=plan_commit,
        slice_id=target_slice_id,
    )
    return cohort


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


def _unowned_open_finding_ids(
    heads: Sequence[SliceExitFindingHeadProjection],
) -> tuple[str, ...]:
    return sorted_finding_ids(
        item.finding_id
        for item in heads
        if item.is_open and not _valid_responsibility(item.responsibility)
    )


def unowned_open_finding_ids(
    records: Sequence[ArtifactRecord],
    *,
    run_id: str,
) -> tuple[str, ...]:
    """Return every open Finding that has no valid responsibility assignment."""

    run_records = tuple(record for record in records if record.run_id == run_id)
    return _unowned_open_finding_ids(
        project_slice_exit_findings(run_records).heads
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
    family_binding = _run_family_binding(run_records)
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
        if isinstance(head.responsibility, BranchPlanningResponsibility):
            if not _branch_route_errors(
                head.finding_id,
                head,
                work_unit_ids=None,
                family_binding=family_binding,
                require_route=False,
            ):
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
) -> tuple[str, ...]:
    return tuple(
        finding_id
        for record in records
        if isinstance(record.payload, WorkUnitPayload)
        for finding_id in record.payload.open_finding_ids
    )


def _work_unit_slices(records: Sequence[ArtifactRecord]) -> dict[str, str]:
    return {
        record.logical_id.removeprefix("work-unit-"): record.payload.slice_id
        for record in records
        if record.logical_id.startswith("work-unit-")
        and isinstance(record.payload, WorkUnitPayload)
    }


def _run_family_binding(
    records: Sequence[ArtifactRecord],
) -> FamilyBindingPayload | None:
    return next(
        (
            record.payload.family_binding
            for record in records
            if isinstance(record.payload, RunProfilePayload)
        ),
        None,
    )


def _branch_route_errors(
    finding_id: str,
    head: SliceExitFindingHeadProjection,
    *,
    work_unit_ids: frozenset[str] | None,
    family_binding: FamilyBindingPayload | None,
    require_route: bool,
) -> tuple[str, ...]:
    assignment = head.last_assignment
    permitted_actions = {"routed"} if require_route else {"opened", "routed"}
    if (
        assignment is None
        or assignment.payload.action not in permitted_actions
        or assignment.payload.work_unit_id is None
        or (
            work_unit_ids is not None
            and assignment.payload.work_unit_id not in work_unit_ids
        )
        or assignment.payload.responsibility != head.responsibility
        or assignment.record.fingerprint.kind is not FingerprintKind.IMPLEMENTATION
    ):
        return (
            f"Branch routing for {finding_id} is not completely recorded and "
            "implementation-fingerprint-bound",
        )
    assert isinstance(head.responsibility, BranchPlanningResponsibility)
    if family_binding is None:
        return (
            f"Branch routing for {finding_id} cannot be matched to a run-bound "
            "family identity before point 67",
        )
    if (
        head.responsibility.family_id != family_binding.family_id
        or head.responsibility.cycle_number != family_binding.cycle_number
    ):
        return (
            f"Branch routing for {finding_id} differs from the run-bound family "
            "identity",
        )
    return ()


def _slice_route_errors(
    finding_id: str,
    head: SliceExitFindingHeadProjection,
    *,
    responsibility: SliceResponsibility,
    run_id: str,
    source_slice_id: str,
    plan: PlanPayload | None,
    plan_positions: dict[str, int],
    plan_specs: dict[str, SliceSpec],
    committed_slice_ids: frozenset[str],
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
    if source_slice_id not in plan_positions or target not in plan_positions:
        return (
            f"Slice routing for {finding_id} targets unknown planned Slice {target}",
        )
    if plan_positions[target] <= plan_positions[source_slice_id]:
        return (
            f"Slice routing for {finding_id} does not target a later planned Slice",
        )
    if target in committed_slice_ids:
        return (
            f"Slice routing for {finding_id} targets already completed Slice {target}",
        )
    target_spec = plan_specs[target]
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
    responsibility: SliceResponsibility, plan_specs: dict[str, SliceSpec]
) -> bool:
    target = plan_specs.get(responsibility.slice_id)
    return target is not None and bool(target.acceptance_criteria)


def _scope_covers(spec: SliceSpec, path: str) -> bool:
    return any(
        path == scope or path.startswith(scope.rstrip("/") + "/")
        for scope in spec.paths
    )


def _committed_slice_ids(
    records: Sequence[ArtifactRecord], plan_slice_ids: frozenset[str]
) -> frozenset[str]:
    prefixes: dict[str, object] = {}
    terminal = object()
    for slice_id in plan_slice_ids:
        node = prefixes
        for character in slice_id:
            node = node.setdefault(character, {})  # type: ignore[assignment]
        node[""] = terminal

    committed: set[str] = set()
    for record in records:
        if (
            not isinstance(record.payload, BindingPayload)
            or record.payload.binding_kind != "commit"
            or not record.logical_id.startswith("commit-")
        ):
            continue
        suffix = record.logical_id.removeprefix("commit-")
        node = prefixes
        matched: str | None = None
        consumed: list[str] = []
        for character in suffix:
            child = node.get(character)
            if not isinstance(child, dict):
                break
            consumed.append(character)
            node = child
            if node.get("") is terminal and (
                len(consumed) == len(suffix) or suffix[len(consumed)] == "-"
            ):
                matched = "".join(consumed)
        if matched is not None:
            committed.add(matched)
    return frozenset(committed)


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
    "UNDECIDED_FINDING_DIAGNOSTIC",
    "UNOWNED_OPEN_FINDING_DIAGNOSTIC",
    "SliceExitCondition",
    "SliceExitEvaluation",
    "SliceExitStatus",
    "evaluate_slice_exit",
    "slice_commit_decision_finding_ids",
    "slice_cohort_finding_ids",
    "unowned_open_finding_ids",
    "workflow_completion_blocking_finding_ids",
]
