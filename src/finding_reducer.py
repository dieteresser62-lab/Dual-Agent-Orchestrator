"""Pure, deterministic finding reduction and named finding projections.

The authoritative entry point accepts one already validated append-only record
prefix.  It performs no I/O and observes neither state-v3 nor process-global
state.  Request/result helpers in this module apply the same canonical finding
semantics before those changes are persisted as new transition records.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

from artifact_models import (
    BranchDiscoveryHandoffImportPayload,
    ArtifactRecord,
    FindingHandoffImportPayload,
    FindingSeverity,
    FindingTransitionPayload,
    ImportedFindingTransition,
    PlanPayload,
    RunProfilePayload,
    TaskPayload,
    WorkUnitPayload,
    WorkflowTransitionPayload,
)
from artifact_replay import (
    ArtifactReplayResult,
    ReplayDiagnosticCode,
    _fail,
)
from contracts import (
    AgentRole,
    FindingAcceptanceMeasurement,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingResponseDecision,
    FindingResponse,
    FindingStatus,
    ValidationCommandSpec,
    ValidationStatus,
    apply_finding_response,
    apply_reviewer_finding_update,
)
from finding_order import finding_id_sort_key, sorted_finding_ids
from finding_signature import finding_record_signature
from finding_responsibility import (
    BranchPlanningResponsibility,
    FindingResponsibility,
    PlanRevisionResponsibility,
    ResponsibilityKind,
    SliceResponsibility,
    responsibility_document,
)


REVIEW_OPENING_RESPONSIBILITY_KINDS: Mapping[str, ResponsibilityKind] = {
    "claude_slice_review": ResponsibilityKind.SLICE,  # allowlist:provider -- persisted step
    "claude_plan_review": ResponsibilityKind.PLAN_REVISION,  # allowlist:provider -- persisted step
    "claude_branch_discovery": ResponsibilityKind.BRANCH_PLANNING,  # allowlist:provider -- persisted step
}


def is_closed_finding_status(status: FindingStatus) -> bool:
    """Keep finding-state decisions inside the canonical reduction boundary."""

    return status is FindingStatus.CLOSED


@dataclass(frozen=True, slots=True)
class FindingTransitionProjection:
    """One ordered local or imported transition in the canonical ledger."""

    sequence: int
    record_id: str
    source_record_id: str
    imported: bool
    payload: FindingTransitionPayload
    _record: ArtifactRecord = field(repr=False, compare=False)

    @property
    def record(self) -> ArtifactRecord:
        return self._record


@dataclass(frozen=True, slots=True)
class FindingLineageProjection:
    """One work-unit-scoped Finding identity and its complete local history."""

    work_unit_id: str
    finding: FindingRecord
    opening_record_id: str
    transition_record_ids: tuple[str, ...]
    responsibility: FindingResponsibility | None


@dataclass(frozen=True, slots=True)
class FindingResponsibilityProjection:
    """Current responsibility while keeping Finding origin independent."""

    finding_id: str
    origin_slice_id: str
    responsibility: FindingResponsibility


@dataclass(frozen=True, slots=True)
class FindingLedgerProjection:
    """Complete cross-run ledger, including its immutable event history."""

    findings: tuple[FindingRecord, ...]
    lineages: tuple[FindingLineageProjection, ...]
    transitions: tuple[FindingTransitionProjection, ...]


@dataclass(frozen=True, slots=True)
class FindingOpenSetProjection:
    """Canonical currently-open subset of one ledger."""

    findings: tuple[FindingRecord, ...]

    @property
    def finding_ids(self) -> tuple[str, ...]:
        return tuple(item.finding_id for item in self.findings)


@dataclass(frozen=True, slots=True)
class FindingOpeningConflictDiagnostic:
    """One historical duplicate opening retained as a replay diagnosis."""

    finding_id: str
    head_opening_record_id: str
    head_opening_revision: int | None
    head_work_unit_id: str
    conflicting_opening_record_id: str
    conflicting_opening_revision: int | None
    conflicting_work_unit_id: str
    code: str = "DUPLICATE-FINDING-OPENING"


@dataclass(frozen=True, slots=True)
class ReviewFindingMerge:
    """Keep a request-bound review distinct from its complete-ledger projection."""

    request_bound: tuple[FindingRecord, ...]
    complete_ledger: tuple[FindingRecord, ...]


@dataclass(frozen=True, slots=True)
class FindingImportSnapshotProjection:
    """Foreign ledger imported for the first implementation work unit."""

    import_record_id: str
    work_unit_id: str | None
    findings: tuple[FindingRecord, ...]
    open_finding_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FindingRequestProjection:
    """Exact canonical Finding subset offered to one agent request."""

    work_unit_id: str | None
    finding_ids: tuple[str, ...]
    findings: tuple[FindingRecord, ...]


@dataclass(frozen=True, slots=True)
class FindingStatusTransitionsProjection:
    """Reviewer-owned opening, status, and reclassification transitions."""

    transitions: tuple[FindingTransitionProjection, ...]


@dataclass(frozen=True, slots=True)
class FindingRecordedStatusProjection:
    """Last explicitly recorded status for one Finding lineage."""

    finding_id: str
    status: str
    record_id: str
    actor: str
    imported: bool

    def matches(self, status: str) -> bool:
        return self.status == status

    @property
    def is_closed(self) -> bool:
        return self.status == "closed"


@dataclass(frozen=True, slots=True)
class SliceExitFindingHeadProjection:
    """Record-derived Finding head facts needed by the dormant Exit policy."""

    finding_id: str
    severity: FindingSeverity
    status: str
    summary: str
    acceptance_test: str
    affected_paths: tuple[str, ...]
    responsibility: FindingResponsibility | None
    last_assignment: FindingTransitionProjection | None
    last_status_change: FindingTransitionProjection | None
    invalid_downgrade: FindingTransitionProjection | None

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def is_closed(self) -> bool:
        return self.status == "closed"

    @property
    def has_complete_closure_record(self) -> bool:
        transition = self.last_status_change
        return bool(
            transition is not None
            and transition.payload.finding_status == "closed"
            and transition.payload.closure_kind in {"fixed", "rejected"}
        )


@dataclass(frozen=True, slots=True)
class SliceExitFindingProjection:
    """Lossless event history plus current heads for one record prefix."""

    events: tuple[FindingTransitionProjection, ...]
    heads: tuple[SliceExitFindingHeadProjection, ...]


@dataclass(slots=True)
class _MutableSliceExitFindingHead:
    severity: FindingSeverity
    status: str
    summary: str
    acceptance_test: str
    affected_paths: tuple[str, ...]
    responsibility: FindingResponsibility | None
    last_assignment: FindingTransitionProjection | None
    last_status_change: FindingTransitionProjection | None = None
    has_record_evidence: bool = False
    invalid_downgrade: FindingTransitionProjection | None = None


@dataclass(frozen=True, slots=True)
class FindingReduction:
    """All named Finding projections derived from one accepted record prefix."""

    ledger: FindingLedgerProjection
    open_set: FindingOpenSetProjection
    import_snapshot: FindingImportSnapshotProjection | None
    status_transitions: FindingStatusTransitionsProjection
    responsibilities: tuple[FindingResponsibilityProjection, ...]
    diagnostics: tuple[FindingOpeningConflictDiagnostic, ...]
    _events: tuple[FindingTransitionProjection, ...] = field(
        repr=False
    )

    def request_subset(
        self,
        *,
        work_unit_id: int | str | None = None,
        finding_ids: Sequence[str] | None = None,
        open_only: bool = False,
    ) -> FindingRequestProjection:
        """Return the sixth projection without consulting any mirror or cache."""
        target = None if work_unit_id is None else str(work_unit_id)
        head_opening_record_ids = frozenset(
            item.opening_record_id for item in self.ledger.lineages
        )
        selected_events = tuple(
            event
            for event in self._events
            if (target is None or event.imported or event.payload.work_unit_id == target)
            and (
                event.payload.action != "opened"
                or event.source_record_id in head_opening_record_ids
            )
        )
        selected = project_request_subset(
            _reduce_events(selected_events),
            finding_ids=finding_ids,
            open_only=open_only,
        )
        return FindingRequestProjection(
            work_unit_id=target,
            finding_ids=selected.finding_ids,
            findings=selected.findings,
        )

@dataclass(frozen=True, slots=True)
class ReviewerStatusChange:
    finding_id: str
    status: FindingStatus
    rationale: str


@dataclass(frozen=True, slots=True)
class ReviewerReclassification:
    finding_id: str
    finding_class: FindingClass
    rationale: str


@dataclass(frozen=True, slots=True)
class FindingResponseEvent:
    finding_id: str
    decision: FindingResponseDecision
    rationale: str


@dataclass(frozen=True, slots=True)
class FindingPersistenceTransition:
    finding: FindingRecord
    action: str
    rationale: str
    identity: str


@dataclass(frozen=True, slots=True)
class FindingResponseDelta:
    finding: FindingRecord
    response: FindingResponse
    response_index: int


def reduce_findings(replay: ArtifactReplayResult) -> FindingReduction:
    """Reduce one accepted append-only record prefix into six projections."""
    return reduce_finding_records(replay.records)


def reduce_finding_records(records: Sequence[ArtifactRecord]) -> FindingReduction:
    """Reduce already validated records without rebuilding replay metadata."""

    events = _transition_events(records)
    diagnostics: list[FindingOpeningConflictDiagnostic] = []
    lineages = _reduce_lineages(
        events, records=records, diagnostics=diagnostics
    )
    ledger_findings = _current_lineage_findings(lineages)
    ledger = FindingLedgerProjection(ledger_findings, lineages, events)
    open_set = project_open_set(ledger_findings)
    status_transitions = FindingStatusTransitionsProjection(
        tuple(event for event in events if event.payload.action != "responded")
    )
    responsibilities = tuple(
        FindingResponsibilityProjection(
            item.finding.finding_id,
            item.finding.origin.slice_id,
            item.responsibility,
        )
        for item in lineages
        if item.responsibility is not None
    )
    import_snapshot = _project_import_snapshot(records, events)
    return FindingReduction(
        ledger=ledger,
        open_set=open_set,
        import_snapshot=import_snapshot,
        status_transitions=status_transitions,
        responsibilities=responsibilities,
        diagnostics=tuple(diagnostics),
        _events=events,
    )


def project_slice_exit_findings(
    records: Sequence[ArtifactRecord],
) -> SliceExitFindingProjection:
    """Fold only record facts required by E4 without changing route policy."""

    events = _transition_events(records)
    mutable: dict[str, _MutableSliceExitFindingHead] = {}
    for event in events:
        payload = event.payload
        if payload.action == "opened":
            mutable.setdefault(
                payload.finding_id,
                _MutableSliceExitFindingHead(
                    severity=payload.severity,
                    status=payload.finding_status,
                    summary=payload.summary or payload.rationale,
                    acceptance_test=payload.acceptance_test or payload.rationale,
                    affected_paths=payload.affected_paths or (),
                    responsibility=payload.responsibility,
                    last_assignment=(
                        event if payload.responsibility is not None else None
                    ),
                ),
            )
            continue
        head = mutable.get(payload.finding_id)
        if head is None:
            continue
        if payload.action == "responded":
            head.has_record_evidence = True
        elif payload.action == "status_changed":
            head.status = payload.finding_status
            head.last_status_change = event
            if payload.finding_status == "closed":
                head.responsibility = None
        elif payload.action == "reclassified":
            if (
                head.severity is FindingSeverity.BLOCKER
                and payload.severity is FindingSeverity.OBSERVATION
                and not head.has_record_evidence
            ):
                head.invalid_downgrade = event
            head.severity = payload.severity
        elif payload.action == "routed":
            head.responsibility = payload.responsibility
            head.last_assignment = event
    return SliceExitFindingProjection(
        events,
        tuple(
            SliceExitFindingHeadProjection(
                finding_id=finding_id,
                severity=head.severity,
                status=head.status,
                summary=head.summary,
                acceptance_test=head.acceptance_test,
                affected_paths=head.affected_paths,
                responsibility=head.responsibility,
                last_assignment=head.last_assignment,
                last_status_change=head.last_status_change,
                invalid_downgrade=head.invalid_downgrade,
            )
            for finding_id, head in sorted(
                mutable.items(), key=lambda item: finding_id_sort_key(item[0])
            )
        ),
    )


def project_open_set(
    findings: Sequence[FindingRecord],
) -> FindingOpenSetProjection:
    """Project the canonical open set from an already reduced ledger."""
    canonical = _canonical_findings(findings)
    return FindingOpenSetProjection(
        tuple(item for item in canonical if item.status is FindingStatus.OPEN)
    )


def project_finding_transition_ids(
    previous: Sequence[FindingRecord],
    current: Sequence[FindingRecord],
) -> tuple[str, ...]:
    """Name Findings whose reviewer-owned state changed between two views."""

    previous_tuple = _canonical_findings(previous)
    current_by_id = {
        item.finding_id: item for item in _canonical_findings(current)
    }
    return tuple(
        item.finding_id
        for item in previous_tuple
        if item.finding_id in current_by_id
        and (
            current_by_id[item.finding_id].status is not item.status
            or current_by_id[item.finding_id].finding_class is not item.finding_class
        )
    )


def project_finding_statuses(
    findings: Sequence[FindingRecord],
) -> tuple[tuple[str, str], ...]:
    """Expose one stable status projection for mirror-integrity comparison."""
    return tuple(
        (item.finding_id, item.status.value.lower())
        for item in _canonical_findings(findings)
    )


def project_record_finding_statuses(
    replay: ArtifactReplayResult,
) -> tuple[tuple[str, str], ...]:
    """Project the last recorded status without requiring a complete lineage.

    Historical audit chains may contain response-only transition rows that were
    accepted for presentation before native Finding authority existed.  Such a
    row cannot enter the canonical ledger, but its explicitly recorded status
    must remain renderable so the human audit projection stays byte-stable.
    """
    return tuple(
        (item.finding_id, item.status)
        for item in project_latest_recorded_statuses(
            replay.records, bound_only=True
        )
    )


def project_latest_recorded_statuses(
    records: Sequence[ArtifactRecord],
    *,
    imported: bool | None = None,
    bound_only: bool = False,
) -> tuple[FindingRecordedStatusProjection, ...]:
    """Select the last recorded status per ID under explicit record filters."""
    latest: dict[str, FindingRecordedStatusProjection] = {}
    for event in _transition_events(records):
        if event.payload.action == "acceptance_measured":
            continue
        if imported is not None and event.imported is not imported:
            continue
        if bound_only and event.payload.work_unit_id is None:
            continue
        latest[event.payload.finding_id] = FindingRecordedStatusProjection(
            finding_id=event.payload.finding_id,
            status=event.payload.finding_status,
            record_id=event.record_id,
            actor=event.payload.actor.value,
            imported=event.imported,
        )
    return tuple(latest[key] for key in sorted(latest, key=finding_id_sort_key))


def is_closed_finding_transition(
    record: ArtifactRecord | ImportedFindingTransition,
) -> bool:
    """Keep the reviewer-owned closed-status predicate inside the reducer."""

    payload = record.payload
    return (
        isinstance(payload, FindingTransitionPayload)
        and payload.action == "status_changed"
        and payload.finding_status == "closed"
    )


def project_legacy_mirror_statuses(
    history_documents: Sequence[object],
    *,
    attributed_open_ids: Sequence[str] = (),
) -> tuple[tuple[str, str], ...]:
    """Canonicalize the pre-cutover mirror for record-integrity comparison.

    This compatibility projection is deliberately data-only.  It does not read
    state-v3 itself, and it never participates in authoritative request
    construction.  ``attributed_open_ids`` preserves old sparse mirrors until
    S4 removes that mirror fallback.
    """
    statuses: dict[str, str] = {}
    for candidate in history_documents:
        if not isinstance(candidate, Mapping):
            continue
        findings = candidate.get("findings")
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            finding_id = finding.get("finding_id")
            status = finding.get("status")
            if isinstance(finding_id, str) and isinstance(status, str):
                statuses[finding_id] = status.lower()
    for finding_id in attributed_open_ids:
        statuses.setdefault(finding_id, "open")
    return tuple(
        sorted(statuses.items(), key=lambda item: finding_id_sort_key(item[0]))
    )


def project_request_subset(
    findings: Sequence[FindingRecord],
    *,
    finding_ids: Sequence[str] | None = None,
    open_only: bool = False,
) -> FindingRequestProjection:
    """Select an exact request subset from a canonical in-memory ledger."""
    canonical = _canonical_findings(findings)
    selected_ids = None if finding_ids is None else frozenset(finding_ids)
    if selected_ids is not None:
        available = {item.finding_id for item in canonical}
        missing = selected_ids - available
        if missing:
            raise ValueError(
                f"finding request subset references unknown id {sorted_finding_ids(missing)[0]}"
            )
    selected = tuple(
        item
        for item in canonical
        if (selected_ids is None or item.finding_id in selected_ids)
        and (not open_only or item.status is FindingStatus.OPEN)
    )
    return FindingRequestProjection(
        work_unit_id=None,
        finding_ids=tuple(item.finding_id for item in selected),
        findings=selected,
    )


def merge_request_result(
    authoritative: Sequence[FindingRecord],
    offered: Sequence[FindingRecord],
    returned: Sequence[FindingRecord],
) -> tuple[FindingRecord, ...]:
    """Merge one request-bounded response into the complete canonical ledger."""
    authoritative_tuple = _canonical_findings(authoritative)
    offered_tuple = _canonical_findings(offered)
    returned_tuple = _canonical_findings(returned)
    authoritative_by_id = {item.finding_id: item for item in authoritative_tuple}
    offered_by_id = {item.finding_id: item for item in offered_tuple}
    returned_by_id = {item.finding_id: item for item in returned_tuple}
    if set(offered_by_id) != set(returned_by_id):
        raise ValueError("native result differs from its exact offered finding subset")
    for finding_id, offered_finding in offered_by_id.items():
        if authoritative_by_id.get(finding_id) != offered_finding:
            raise ValueError("offered finding subset differs from the complete ledger")
    return tuple(
        returned_by_id.get(item.finding_id, item)
        for item in authoritative_tuple
    )


def merge_review_request_result(
    authoritative: Sequence[FindingRecord],
    offered: Sequence[FindingRecord],
    returned: Sequence[FindingRecord],
    *,
    review_type: str,
) -> ReviewFindingMerge:
    """Validate one reviewer response and project it onto the complete ledger."""
    authoritative_tuple = _canonical_findings(authoritative)
    offered_tuple = _canonical_findings(offered)
    returned_tuple = _canonical_findings(returned)
    authoritative_by_id = {item.finding_id: item for item in authoritative_tuple}
    offered_by_id = {item.finding_id: item for item in offered_tuple}
    returned_by_id = {item.finding_id: item for item in returned_tuple}
    if not set(offered_by_id).issubset(returned_by_id):
        raise ValueError(f"native review result omits an offered {review_type} finding")
    if any(
        authoritative_by_id.get(finding_id) != finding
        for finding_id, finding in offered_by_id.items()
    ):
        raise ValueError(
            f"offered {review_type} finding subset differs from the complete ledger"
        )
    foreign_existing = (
        set(returned_by_id) - set(offered_by_id)
    ).intersection(authoritative_by_id)
    collisions = {
        finding_id
        for finding_id in foreign_existing
        if returned_by_id[finding_id].origin
        != authoritative_by_id[finding_id].origin
    }
    if collisions:
        raise ValueError(
            f"native {review_type} result has a finding-number collision: "
            + ", ".join(sorted_finding_ids(collisions))
        )
    foreign_mutations = foreign_existing - collisions
    if foreign_mutations:
        raise ValueError(
            f"native review result mutates an unoffered {review_type} finding: "
            + ", ".join(sorted_finding_ids(foreign_mutations))
        )
    merged = dict(authoritative_by_id)
    merged.update(returned_by_id)
    return ReviewFindingMerge(
        request_bound=returned_tuple,
        complete_ledger=tuple(
            merged[key] for key in sorted(merged, key=finding_id_sort_key)
        ),
    )


def apply_finding_responses(
    prior: Sequence[FindingRecord],
    responses: Sequence[FindingResponseEvent],
) -> tuple[FindingRecord, ...]:
    """Apply a sorted response subset to the open Finding projection."""
    canonical = _canonical_findings(prior)
    open_ids = set(project_open_set(canonical).finding_ids)
    response_ids = tuple(item.finding_id for item in responses)
    if response_ids != sorted_finding_ids(response_ids):
        raise ValueError("finding responses must be sorted and unique")
    unexpected = sorted_finding_ids(set(response_ids) - open_ids)
    if unexpected:
        raise ValueError(
            f"disposition references non-open finding {unexpected[0]}"
        )
    by_id = {item.finding_id: item for item in canonical}
    for response in responses:
        by_id[response.finding_id] = apply_finding_response(
            by_id[response.finding_id], response.decision, response.rationale
        )
    return tuple(by_id[key] for key in sorted(by_id, key=finding_id_sort_key))


def apply_reviewer_events(
    prior: Sequence[FindingRecord],
    *,
    reviewer: AgentRole,
    opened: Sequence[FindingRecord] = (),
    status_changes: Sequence[ReviewerStatusChange] = (),
    reclassifications: Sequence[ReviewerReclassification] = (),
) -> tuple[FindingRecord, ...]:
    """Apply reviewer-owned transitions with one canonical implementation."""
    findings = {item.finding_id: item for item in _canonical_findings(prior)}
    for finding in opened:
        if finding.finding_id in findings:
            raise ValueError(f"new finding reuses previous id {finding.finding_id}")
        if finding.origin.reporter is not reviewer:
            raise ValueError("new finding reporter differs from reviewer")
        findings[finding.finding_id] = finding
    for update in status_changes:
        finding = findings.get(update.finding_id)
        if finding is None:
            raise ValueError(f"finding update references unknown id {update.finding_id}")
        findings[update.finding_id] = apply_reviewer_finding_update(
            finding,
            reviewer=reviewer,
            status=update.status,
            rationale=update.rationale,
        )
    for update in reclassifications:
        finding = findings.get(update.finding_id)
        if finding is None:
            raise ValueError(f"finding update references unknown id {update.finding_id}")
        findings[update.finding_id] = apply_reviewer_finding_update(
            finding,
            reviewer=reviewer,
            status=finding.status,
            rationale=update.rationale,
            finding_class=update.finding_class,
        )
    return tuple(findings[key] for key in sorted(findings, key=finding_id_sort_key))


def project_reviewer_persistence_transitions(
    previous: Sequence[FindingRecord],
    current: Sequence[FindingRecord],
    *,
    work_unit_id: int | str | None,
) -> tuple[FindingPersistenceTransition, ...]:
    """Describe the exact reviewer deltas that require transition records."""
    previous_by_id = {
        item.finding_id: item for item in _canonical_findings(previous)
    }
    structured_unit = None if work_unit_id is None else str(work_unit_id)
    result: list[FindingPersistenceTransition] = []
    for finding in _canonical_findings(current):
        prior = previous_by_id.get(finding.finding_id)
        if prior is None:
            result.append(
                FindingPersistenceTransition(
                    (
                        replace(
                            finding,
                            status=FindingStatus.OPEN,
                            status_rationale=None,
                        )
                        if is_closed_finding_status(finding.status)
                        else finding
                    ),
                    "opened",
                    finding.summary,
                    "opened",
                )
            )
            if is_closed_finding_status(finding.status):
                result.append(
                    FindingPersistenceTransition(
                        finding,
                        "status_changed",
                        finding.status_rationale or finding.summary,
                        "status_changed",
                    )
                )
            continue
        class_changed = prior.finding_class is not finding.finding_class
        rationale_changed = prior.status_rationale != finding.status_rationale
        if class_changed:
            result.append(
                FindingPersistenceTransition(
                    finding,
                    "reclassified",
                    finding.status_rationale or finding.summary,
                    "reclassified",
                )
            )
        if prior.status is not finding.status:
            result.append(
                FindingPersistenceTransition(
                    finding,
                    "status_changed",
                    finding.status_rationale or finding.summary,
                    "status_changed",
                )
            )
        elif rationale_changed and not class_changed:
            result.append(
                FindingPersistenceTransition(
                    finding,
                    "status_changed",
                    finding.status_rationale or finding.summary,
                    (
                        f"status_rationale:{structured_unit}"
                        if structured_unit is not None
                        else "status_rationale"
                    ),
                )
            )
    return tuple(result)


def project_finding_response_delta(
    previous: Sequence[FindingRecord],
    current: Sequence[FindingRecord],
) -> tuple[FindingResponseDelta, ...]:
    """Describe newly appended implementer responses without recomputing state."""
    previous_by_id = {
        item.finding_id: item for item in _canonical_findings(previous)
    }
    result: list[FindingResponseDelta] = []
    for finding in _canonical_findings(current):
        prior = previous_by_id.get(finding.finding_id)
        prior_count = 0 if prior is None else len(prior.responses)
        if prior is not None and finding.responses[:prior_count] != prior.responses:
            raise ValueError(
                f"finding response history diverges for {finding.finding_id}"
            )
        result.extend(
            FindingResponseDelta(finding, response, index)
            for index, response in enumerate(
                finding.responses[prior_count:], start=prior_count + 1
            )
        )
    return tuple(result)


def merge_history_snapshots(
    snapshots: Sequence[Sequence[FindingRecord]],
) -> tuple[FindingRecord, ...]:
    """Reduce ordered state/audit snapshots without inventing record facts."""
    latest: dict[str, FindingRecord] = {}
    for snapshot in snapshots:
        for finding in _canonical_findings(snapshot):
            previous = latest.get(finding.finding_id)
            if previous is not None:
                if (
                    previous.origin != finding.origin
                    or previous.summary != finding.summary
                    or previous.acceptance_test != finding.acceptance_test
                    or previous.affected_paths != finding.affected_paths
                    or previous.predecessor_finding_ref
                    != finding.predecessor_finding_ref
                    or previous.evidence_anchor_sha256
                    != finding.evidence_anchor_sha256
                ):
                    raise ValueError(
                        f"finding identity changed during lifecycle: {finding.finding_id}"
                    )
                if finding.responses[: len(previous.responses)] != previous.responses:
                    raise ValueError(
                        f"finding response history regressed: {finding.finding_id}"
                    )
                if (
                    finding.class_history[: len(previous.class_history)]
                    != previous.class_history
                ):
                    raise ValueError(
                        f"finding class history regressed: {finding.finding_id}"
                    )
                if (
                    finding.acceptance_measurements[
                        : len(previous.acceptance_measurements)
                    ]
                    != previous.acceptance_measurements
                ):
                    raise ValueError(
                        "finding acceptance measurement history regressed: "
                        f"{finding.finding_id}"
                    )
            latest[finding.finding_id] = finding
    return tuple(latest[key] for key in sorted(latest, key=finding_id_sort_key))


def _transition_events(
    records: Sequence[ArtifactRecord],
) -> tuple[FindingTransitionProjection, ...]:
    events: list[FindingTransitionProjection] = []
    for sequence, record in enumerate(records, start=1):
        payload = record.payload
        if isinstance(
            payload,
            (FindingHandoffImportPayload, BranchDiscoveryHandoffImportPayload),
        ):
            events.extend(
                FindingTransitionProjection(
                    sequence=sequence,
                    record_id=record.record_id,
                    source_record_id=item.record_id,
                    imported=True,
                    payload=item.payload,
                    _record=record,
                )
                for item in payload.transitions
            )
        elif isinstance(payload, FindingTransitionPayload):
            events.append(
                FindingTransitionProjection(
                    sequence=sequence,
                    record_id=record.record_id,
                    source_record_id=record.record_id,
                    imported=False,
                    payload=payload,
                    _record=record,
                )
            )
    return tuple(events)


def _reduce_events(
    events: Sequence[FindingTransitionProjection],
) -> tuple[FindingRecord, ...]:
    return _current_lineage_findings(_reduce_lineages(events))


def _reduce_lineages(
    events: Sequence[FindingTransitionProjection],
    *,
    records: Sequence[ArtifactRecord] | None = None,
    diagnostics: list[FindingOpeningConflictDiagnostic] | None = None,
) -> tuple[FindingLineageProjection, ...]:
    findings: dict[tuple[str, str], FindingRecord] = {}
    active_keys: dict[str, tuple[str, str]] = {}
    head_openings: dict[str, FindingTransitionProjection] = {}
    opening_records: dict[tuple[str, str], str] = {}
    transition_records: dict[tuple[str, str], list[str]] = {}
    responsibilities: dict[tuple[str, str], FindingResponsibility | None] = {}
    opening_order: list[tuple[str, str]] = []
    for event in events:
        payload = event.payload
        record = _event_record(event)
        if payload.work_unit_id is None:
            continue
        if payload.action == "opened":
            lineage_key = (payload.work_unit_id, payload.finding_id)
            if (
                payload.summary is None
                or payload.acceptance_test is None
                or payload.origin_slice_id is None
                or payload.origin_round_number is None
                or payload.finding_status != "open"
            ):
                _fail(
                    ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                    "structured finding opening metadata is incomplete",
                    record,
                )
            try:
                opening = FindingRecord(
                    finding_id=payload.finding_id,
                    finding_class=FindingClass(payload.severity.value),
                    status=FindingStatus.OPEN,
                    summary=payload.summary,
                    acceptance_test=payload.acceptance_test,
                    origin=FindingOrigin(
                        payload.origin_slice_id,
                        payload.origin_round_number,
                        AgentRole(payload.reporter.value),
                    ),
                    predecessor_finding_ref=payload.predecessor_finding_ref,
                    evidence_anchor_sha256=payload.evidence_anchor_sha256,
                    affected_paths=payload.affected_paths or (),
                )
            except ValueError as exc:
                _fail(
                    ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                    f"structured finding opening is invalid: {exc}",
                    record,
                )
            if (
                payload.responsibility is not None
                and records is not None
                and not event.imported
            ):
                _validate_opening_responsibility(event, records)
            if payload.predecessor_finding_ref is not None:
                predecessor_key = active_keys.get(
                    payload.predecessor_finding_ref
                )
                predecessor = (
                    None
                    if predecessor_key is None
                    else findings[predecessor_key]
                )
                if (
                    predecessor is None
                    or predecessor.status is not FindingStatus.CLOSED
                ):
                    _fail(
                        ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                        "new Finding generation references no closed predecessor",
                        record,
                    )
                if finding_record_signature(predecessor) != finding_record_signature(
                    opening
                ):
                    _fail(
                        ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
                        "new Finding generation signature differs from its predecessor",
                        record,
                    )
            if lineage_key in findings or payload.finding_id in active_keys:
                head = head_openings[payload.finding_id]
                if diagnostics is not None:
                    diagnostics.append(
                        FindingOpeningConflictDiagnostic(
                            finding_id=payload.finding_id,
                            head_opening_record_id=head.source_record_id,
                            head_opening_revision=(
                                None if head.imported else head._record.revision
                            ),
                            head_work_unit_id=head.payload.work_unit_id,
                            conflicting_opening_record_id=event.source_record_id,
                            conflicting_opening_revision=(
                                None if event.imported else event._record.revision
                            ),
                            conflicting_work_unit_id=payload.work_unit_id,
                        )
                    )
                continue
            findings[lineage_key] = opening
            active_keys[payload.finding_id] = lineage_key
            head_openings[payload.finding_id] = event
            opening_records[lineage_key] = event.source_record_id
            transition_records[lineage_key] = [event.source_record_id]
            responsibilities[lineage_key] = payload.responsibility
            opening_order.append(lineage_key)
            continue
        scoped_key = (payload.work_unit_id, payload.finding_id)
        lineage_key = (
            scoped_key
            if scoped_key in findings
            else active_keys.get(payload.finding_id)
        )
        if lineage_key is None:
            _fail(
                ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                f"finding transition references unopened finding {payload.finding_id!r}",
                record,
            )
        finding = findings[lineage_key]
        if payload.reporter.value != finding.origin.reporter.value or (
            payload.action != "reclassified"
            and payload.severity.value != finding.finding_class.value
        ):
            _fail(
                ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                "finding transition changes immutable reviewer ownership",
                record,
            )
        try:
            finding, responsibility = _apply_transition_to_finding(
                event,
                finding,
                responsibilities[lineage_key],
                records,
            )
            responsibilities[lineage_key] = responsibility
        except ValueError as exc:
            _fail(
                ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                f"structured finding transition is invalid: {exc}",
                record,
            )
        findings[lineage_key] = finding
        transition_records[lineage_key].append(event.source_record_id)
    return tuple(
        FindingLineageProjection(
            work_unit_id=lineage_key[0],
            finding=findings[lineage_key],
            opening_record_id=opening_records[lineage_key],
            transition_record_ids=tuple(transition_records[lineage_key]),
            responsibility=responsibilities[lineage_key],
        )
        for lineage_key in opening_order
    )


def _apply_transition_to_finding(
    event: FindingTransitionProjection,
    finding: FindingRecord,
    responsibility: FindingResponsibility | None,
    records: Sequence[ArtifactRecord] | None,
) -> tuple[FindingRecord, FindingResponsibility | None]:
    payload = event.payload
    record = _event_record(event)
    if payload.action == "responded":
        if payload.response_decision is None:
            _fail(
                ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                "structured finding response lacks response_decision",
                record,
            )
        return (
            apply_finding_response(
                finding,
                FindingResponseDecision(payload.response_decision.upper()),
                payload.rationale,
            ),
            responsibility,
        )
    if payload.action == "reclassified":
        return (
            apply_reviewer_finding_update(
                finding,
                reviewer=finding.origin.reporter,
                status=finding.status,
                rationale=payload.rationale,
                finding_class=FindingClass(payload.severity.value),
            ),
            responsibility,
        )
    if payload.action == "status_changed":
        updated = apply_reviewer_finding_update(
            finding,
            reviewer=finding.origin.reporter,
            status=FindingStatus(payload.finding_status.upper()),
            rationale=payload.rationale,
            finding_class=FindingClass(payload.severity.value),
        )
        return updated, None if is_closed_finding_status(updated.status) else responsibility
    if payload.action == "acceptance_measured":
        return _apply_acceptance_measurement(event, finding), responsibility
    if payload.action == "routed":
        assert payload.responsibility is not None
        if finding.status is not FindingStatus.OPEN:
            _fail(
                ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
                f"cannot route closed finding {payload.finding_id}",
                record,
            )
        if records is not None and not event.imported:
            _validate_routed_responsibility(payload.responsibility, record, records)
        return finding, payload.responsibility
    return finding, responsibility


def _apply_acceptance_measurement(
    event: FindingTransitionProjection,
    finding: FindingRecord,
) -> FindingRecord:
    payload = event.payload
    record = _event_record(event)
    if finding.status is not FindingStatus.OPEN:
        _fail(
            ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
            f"cannot measure closed finding {payload.finding_id}",
            record,
        )
    assert payload.acceptance_command is not None
    assert payload.acceptance_outcome is not None
    assert payload.acceptance_exit_code is not None
    assert payload.acceptance_output_sha256 is not None
    assert payload.acceptance_attestation_id is not None
    assert payload.acceptance_fingerprint is not None
    if (
        not event.imported
        and record.fingerprint.sha256 != payload.acceptance_fingerprint
    ):
        _fail(
            ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
            "finding acceptance measurement payload differs from its record fingerprint",
            record,
        )
    measurement = FindingAcceptanceMeasurement(
        fingerprint=payload.acceptance_fingerprint,
        command=ValidationCommandSpec(argv=payload.acceptance_command.argv),
        status=ValidationStatus(payload.acceptance_outcome.upper()),
        exit_code=payload.acceptance_exit_code,
        output_sha256=payload.acceptance_output_sha256,
        attestation_id=payload.acceptance_attestation_id,
    )
    return replace(
        finding,
        acceptance_measurements=(*finding.acceptance_measurements, measurement),
    )


def responsibility_projection_document(
    reduction: FindingReduction,
) -> dict[str, dict[str, str | int]]:
    """Serialize the current typed assignment in canonical Finding-ID order."""

    return {
        item.finding_id: responsibility_document(item.responsibility)
        for item in sorted(
            reduction.responsibilities,
            key=lambda item: finding_id_sort_key(item.finding_id),
        )
    }


def _validate_opening_responsibility(
    event: FindingTransitionProjection,
    records: Sequence[ArtifactRecord],
) -> None:
    payload = event.payload
    responsibility = payload.responsibility
    assert responsibility is not None
    record = _event_record(event)
    if event.imported:
        # Imported transitions are immutable historical facts. Their source
        # run validated the responsibility against its own family binding
        # before the orchestrator created the digest-bound handoff. Rebinding
        # them to a child would reject a valid BRANCH_PLANNING route as soon as
        # the monotonically increasing family cycle advances.
        return
    prior = records[: event.sequence - 1]
    review_step = next(
        (
            candidate.payload.step
            for candidate in reversed(prior)
            if isinstance(candidate.payload, WorkflowTransitionPayload)
            and candidate.payload.work_unit_id == payload.work_unit_id
        ),
        None,
    )
    required_kind = REVIEW_OPENING_RESPONSIBILITY_KINDS.get(review_step)
    if required_kind is None:
        _responsibility_fail(
            f"responsibility-bearing opening has no supported review context; step={review_step!r}",
            record,
        )
    if responsibility.responsibility_kind is not required_kind:
        review_label = {
            ResponsibilityKind.SLICE: "Slicereview",
            ResponsibilityKind.PLAN_REVISION: "Planreview",
            ResponsibilityKind.BRANCH_PLANNING: "Entdeckungsreview",
        }[required_kind]
        _responsibility_fail(
            f"{review_label} opening requires responsibility kind {required_kind.value}",
            record,
        )
    if required_kind is ResponsibilityKind.SLICE:
        assert isinstance(responsibility, SliceResponsibility)
        plan = next(
            (
                candidate.payload
                for candidate in reversed(prior)
                if isinstance(candidate.payload, PlanPayload)
            ),
            None,
        )
        unit = next(
            (
                candidate.payload
                for candidate in reversed(prior)
                if candidate.logical_id == f"work-unit-{payload.work_unit_id}"
                and isinstance(candidate.payload, WorkUnitPayload)
            ),
            None,
        )
        if responsibility.target_run_id != record.run_id:
            _responsibility_fail(
                "SLICE.target_run_id differs from the opening record run",
                record,
            )
        if plan is None:
            _responsibility_fail(
                "SLICE.approved_plan_commit has no approved Plan fact in the run",
                record,
            )
        if responsibility.approved_plan_commit != plan.approved_plan_commit:
            _responsibility_fail(
                "SLICE.approved_plan_commit differs from the approved Plan fact",
                record,
            )
        expected_slice = (
            payload.origin_slice_id if unit is None else unit.slice_id
        )
        if responsibility.slice_id != expected_slice:
            _responsibility_fail(
                "SLICE.slice_id differs from the opening review Slice",
                record,
            )
        return
    if required_kind is ResponsibilityKind.PLAN_REVISION:
        assert isinstance(responsibility, PlanRevisionResponsibility)
        task = next(
            (
                candidate.payload
                for candidate in reversed(prior)
                if isinstance(candidate.payload, TaskPayload)
            ),
            None,
        )
        if responsibility.run_id != record.run_id:
            _responsibility_fail(
                "PLAN_REVISION.run_id differs from the opening record run",
                record,
            )
        if task is None or task.work_plan_path is None:
            _responsibility_fail(
                "PLAN_REVISION.plan_path has no bound work-plan path in the run",
                record,
            )
        if responsibility.plan_path != task.work_plan_path:
            _responsibility_fail(
                "PLAN_REVISION.plan_path differs from the bound work-plan path",
                record,
            )
        return
    if required_kind is ResponsibilityKind.BRANCH_PLANNING:
        assert isinstance(responsibility, BranchPlanningResponsibility)
        _validate_routed_responsibility(responsibility, record, records)
        return
    raise AssertionError("unhandled review opening responsibility kind")


def _validate_routed_responsibility(
    responsibility: FindingResponsibility,
    record: ArtifactRecord,
    records: Sequence[ArtifactRecord],
) -> None:
    if isinstance(responsibility, BranchPlanningResponsibility):
        profile = next(
            (
                item.payload
                for item in records
                if item.run_id == record.run_id
                and isinstance(item.payload, RunProfilePayload)
            ),
            None,
        )
        binding = None if profile is None else profile.family_binding
        if binding is None:
            _responsibility_fail(
                "BRANCH_PLANNING has no run-bound family_id and cycle_number before point 67",
                record,
            )
        if (
            responsibility.family_id != binding.family_id
            or responsibility.cycle_number != binding.cycle_number
        ):
            _responsibility_fail(
                "BRANCH_PLANNING family_id or cycle_number differs from the "
                "run-bound family identity",
                record,
            )


def _responsibility_fail(message: str, record: ArtifactRecord) -> None:
    _fail(
        ReplayDiagnosticCode.RECORD_TYPE_MISMATCH,
        f"finding responsibility is invalid: {message}",
        record,
    )


def _current_lineage_findings(
    lineages: Sequence[FindingLineageProjection],
) -> tuple[FindingRecord, ...]:
    heads: dict[str, FindingRecord] = {}
    for item in lineages:
        heads.setdefault(item.finding.finding_id, item.finding)
    return tuple(heads[key] for key in sorted(heads, key=finding_id_sort_key))


def _project_import_snapshot(
    records: Sequence[ArtifactRecord],
    events: Sequence[FindingTransitionProjection],
) -> FindingImportSnapshotProjection | None:
    imports = tuple(
        record for record in records
        if isinstance(
            record.payload,
            (FindingHandoffImportPayload, BranchDiscoveryHandoffImportPayload),
        )
    )
    if not imports:
        return None
    imported_events = tuple(event for event in events if event.imported)
    findings = _reduce_events(imported_events)
    open_ids = project_open_set(findings).finding_ids
    import_record = imports[0]
    bound_unit = next(
        (
            record.logical_id.removeprefix("work-unit-")
            for record in records
            if isinstance(record.payload, WorkUnitPayload)
            and record.payload.finding_import_record_id == import_record.record_id
            and record.logical_id.startswith("work-unit-")
        ),
        None,
    )
    return FindingImportSnapshotProjection(
        import_record_id=import_record.record_id,
        work_unit_id=bound_unit,
        findings=findings,
        open_finding_ids=open_ids,
    )


def _canonical_findings(
    findings: Sequence[FindingRecord],
) -> tuple[FindingRecord, ...]:
    canonical = tuple(
        sorted(findings, key=lambda item: finding_id_sort_key(item.finding_id))
    )
    ids = tuple(item.finding_id for item in canonical)
    if ids != sorted_finding_ids(ids):
        raise ValueError("finding ledger contains duplicate finding IDs")
    return canonical


def _event_record(event: FindingTransitionProjection) -> ArtifactRecord:
    return event._record
