"""Phase-specific Slice convergence derived only from authoritative records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from artifact_models import (
    ArtifactRecord,
    FindingTransitionPayload,
    FingerprintKind,
    ReviewPayload,
    ReviewValidationBindingPayload,
    Role,
    ValidationAttestationPayload,
    WorkflowEventPayload,
)
from finding_order import sorted_finding_ids
from finding_reducer import (
    is_closed_finding_transition,
    project_slice_exit_findings,
)
from finding_responsibility import SliceResponsibility
from slice_exit import evaluate_slice_exit


class SliceReviewPhase(StrEnum):
    DISCOVERY = "discovery"
    CONVERGENCE = "convergence"


@dataclass(frozen=True, slots=True)
class SliceConvergenceEvaluation:
    """The E5 progress facts for one completed Slice-review round."""

    phase: SliceReviewPhase
    cohort_finding_ids: tuple[str, ...]
    newly_opened_finding_ids: tuple[str, ...]
    closed_local_finding_ids: tuple[str, ...]
    forwarded_local_finding_ids: tuple[str, ...]
    attested_remediation_finding_ids: tuple[str, ...]
    progress_made: bool
    reason: str


def evaluate_slice_convergence(
    records: Sequence[ArtifactRecord],
    *,
    run_id: str,
    slice_id: int | str,
    work_unit_id: int | str,
    round_number: int,
    approved_plan_commit: str | None = None,
) -> SliceConvergenceEvaluation:
    """Evaluate E5 without consulting state projections, prose, or the worktree.

    ``A_s`` comes directly from :func:`slice_exit.evaluate_slice_exit`.  Round
    membership comes from the typed review ``WorkflowEvent`` and its referenced
    ``Review`` record; no logical-id parsing is used for the current round.
    """

    if isinstance(round_number, bool) or not isinstance(round_number, int):
        raise ValueError("Slice convergence round_number must be an integer")
    if round_number < 1:
        raise ValueError("Slice convergence round_number must be positive")
    target_slice = str(slice_id)
    target_unit = str(work_unit_id)
    run_records = tuple(record for record in records if record.run_id == run_id)
    review_record, round_records = _review_round_records(
        run_records,
        slice_id=target_slice,
        work_unit_id=target_unit,
        round_number=round_number,
    )
    exit_evaluation = evaluate_slice_exit(
        run_records,
        run_id=run_id,
        slice_id=target_slice,
        approved_plan_commit=approved_plan_commit,
    )
    # E4 and E5 deliberately quantify over the same exhausted, record-derived
    # Finding set.  In particular, an opening cannot disappear merely because
    # the reviewer has not assigned its responsibility yet.
    cohort = frozenset(exit_evaluation.responsibility_finding_ids)
    review_position = run_records.index(review_record)
    prior_projection = project_slice_exit_findings(run_records[:review_position])
    prior_local = frozenset(
        head.finding_id
        for head in prior_projection.heads
        if head.finding_id in cohort
        and head.is_open
        and _is_current_slice_responsibility(
            head.responsibility,
            run_id=run_id,
            approved_plan_commit=exit_evaluation.approved_plan_commit,
            slice_id=target_slice,
        )
    )
    transitions = tuple(
        record
        for record in round_records
        if isinstance(record.payload, FindingTransitionPayload)
        and record.payload.work_unit_id == target_unit
    )
    newly_opened = sorted_finding_ids(
        record.payload.finding_id
        for record in transitions
        if record.payload.action == "opened"
        and record.payload.finding_id in cohort
    )
    closed_local = sorted_finding_ids(
        record.payload.finding_id
        for record in transitions
        if record.payload.action == "status_changed"
        and is_closed_finding_transition(record)
        and record.payload.finding_id in prior_local
    )
    forwarded_local = sorted_finding_ids(
        record.payload.finding_id
        for record in transitions
        if record.payload.action == "routed"
        and record.payload.finding_id in prior_local
        and record.payload.responsibility is not None
        and not _is_current_slice_responsibility(
            record.payload.responsibility,
            run_id=run_id,
            approved_plan_commit=exit_evaluation.approved_plan_commit,
            slice_id=target_slice,
        )
    )
    attested_remediation = _attested_remediation_ids(
        run_records,
        review_record=review_record,
        round_records=round_records,
        cohort=cohort,
        work_unit_id=target_unit,
        round_number=round_number,
    )
    phase = (
        SliceReviewPhase.DISCOVERY
        if round_number == 1
        else SliceReviewPhase.CONVERGENCE
    )
    progress = (
        bool(newly_opened)
        if phase is SliceReviewPhase.DISCOVERY
        else bool(closed_local or forwarded_local or attested_remediation)
    )
    if progress:
        reason = (
            "the discovery round opened reviewer-owned findings"
            if phase is SliceReviewPhase.DISCOVERY
            else "the convergence round recorded a closing, forwarding, or attested remediation fact"
        )
    else:
        reason = (
            "the discovery round opened no findings"
            if phase is SliceReviewPhase.DISCOVERY
            else (
                "the convergence round closed or forwarded no previously local "
                "finding and recorded no attested fingerprint-changing remediation"
            )
        )
    return SliceConvergenceEvaluation(
        phase=phase,
        cohort_finding_ids=sorted_finding_ids(cohort),
        newly_opened_finding_ids=newly_opened,
        closed_local_finding_ids=closed_local,
        forwarded_local_finding_ids=forwarded_local,
        attested_remediation_finding_ids=attested_remediation,
        progress_made=progress,
        reason=reason,
    )


def _review_round_records(
    records: Sequence[ArtifactRecord],
    *,
    slice_id: str,
    work_unit_id: str,
    round_number: int,
) -> tuple[ArtifactRecord, tuple[ArtifactRecord, ...]]:
    positions = {record.record_id: index for index, record in enumerate(records)}
    candidates: list[tuple[ArtifactRecord, ArtifactRecord]] = []
    for event_record in records:
        event = event_record.payload
        if not (
            isinstance(event, WorkflowEventPayload)
            and event.event_kind == "review"
            and event.slice_id == slice_id
            and event.work_unit_id == work_unit_id
            and event.round_number == round_number
        ):
            continue
        referenced = tuple(
            record
            for record_id in event.record_refs
            for record in records
            if record.record_id == record_id
            and isinstance(record.payload, ReviewPayload)
            and record.payload.work_unit_id == work_unit_id
        )
        if len(referenced) != 1:
            raise ValueError(
                "Slice convergence review event has no unique referenced Review record"
            )
        candidates.append((referenced[0], event_record))
    if len(candidates) != 1:
        raise ValueError(
            "Slice convergence requires exactly one record-bound review round"
        )
    review_record, event_record = candidates[0]
    start = positions[review_record.record_id]
    end = positions[event_record.record_id]
    if start >= end:
        raise ValueError("Slice convergence review event precedes its Review record")
    return review_record, tuple(records[start + 1 : end])


def _is_current_slice_responsibility(
    responsibility: object,
    *,
    run_id: str,
    approved_plan_commit: str,
    slice_id: str,
) -> bool:
    return bool(
        isinstance(responsibility, SliceResponsibility)
        and responsibility.target_run_id == run_id
        and responsibility.approved_plan_commit == approved_plan_commit
        and responsibility.slice_id == slice_id
    )


def _attested_remediation_ids(
    records: Sequence[ArtifactRecord],
    *,
    review_record: ArtifactRecord,
    round_records: Sequence[ArtifactRecord],
    cohort: frozenset[str],
    work_unit_id: str,
    round_number: int,
) -> tuple[str, ...]:
    if review_record.fingerprint.kind is not FingerprintKind.IMPLEMENTATION:
        return ()
    previous_review = _previous_review_record(
        records,
        work_unit_id=work_unit_id,
        round_number=round_number,
    )
    if (
        previous_review is None
        or previous_review.fingerprint.sha256 == review_record.fingerprint.sha256
        or not _review_has_passing_attestation(records, review_record)
    ):
        return ()
    review = review_record.payload
    assert isinstance(review, ReviewPayload)
    reviewed_ids = frozenset(review.finding_ids)
    return sorted_finding_ids(
        record.payload.finding_id
        for record in round_records
        if isinstance(record.payload, FindingTransitionPayload)
        and record.payload.work_unit_id == work_unit_id
        and record.payload.action == "status_changed"
        and record.payload.closure_kind in {"fixed", "partial"}
        and record.payload.finding_id in cohort
        and record.payload.finding_id in reviewed_ids
        and record.fingerprint == review_record.fingerprint
    )


def _previous_review_record(
    records: Sequence[ArtifactRecord],
    *,
    work_unit_id: str,
    round_number: int,
) -> ArtifactRecord | None:
    records_by_id = {record.record_id: record for record in records}
    prior: list[tuple[int, ArtifactRecord]] = []
    for record in records:
        payload = record.payload
        if not (
            isinstance(payload, WorkflowEventPayload)
            and payload.event_kind == "review"
            and payload.work_unit_id == work_unit_id
            and payload.round_number is not None
            and payload.round_number < round_number
        ):
            continue
        referenced = tuple(
            records_by_id[record_id]
            for record_id in payload.record_refs
            if record_id in records_by_id
            and isinstance(records_by_id[record_id].payload, ReviewPayload)
        )
        if len(referenced) == 1:
            prior.append((payload.round_number, referenced[0]))
    return None if not prior else max(prior, key=lambda item: item[0])[1]


def _review_has_passing_attestation(
    records: Sequence[ArtifactRecord], review_record: ArtifactRecord
) -> bool:
    bindings = tuple(
        record.payload
        for record in records
        if isinstance(record.payload, ReviewValidationBindingPayload)
        and record.payload.review_record_id == review_record.record_id
    )
    if len(bindings) != 1:
        return False
    binding = bindings[0]
    attestations = tuple(
        record
        for record in records
        if record.record_id == binding.attestation_record_id
        and isinstance(record.payload, ValidationAttestationPayload)
    )
    return bool(
        len(attestations) == 1
        and attestations[0].fingerprint == review_record.fingerprint
        and attestations[0].payload.attested_by is Role.ORCHESTRATOR
        and all(result.outcome == "pass" for result in attestations[0].payload.results)
    )


__all__ = [
    "SliceConvergenceEvaluation",
    "SliceReviewPhase",
    "evaluate_slice_convergence",
]
