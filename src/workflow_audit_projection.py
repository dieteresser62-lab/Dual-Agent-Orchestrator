"""Record-backed history reconstruction and managed audit projection inputs.

``workflow_audit`` owns driver-side document rendering and the final audit
commit boundary.  This module owns the persisted facts supplied to that
boundary: workflow-history reconstruction, audit-entry projection, and the
managed audit target lifecycle.  The orchestrator remains the sole production
composition root; this module imports neither the orchestrator nor the
driver-side ``workflow_audit`` boundary.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Callable

from artifact_bridge import review_payload_matches_result
from artifact_models import (
    AgentResultPayload,
    ProviderContentPayload,
    ReviewPacketPayload,
    ReviewPayload,
    WorkflowTransitionPayload,
)
from artifact_replay import (
    ArtifactReplayResult,
    project_review_contracts,
    project_validation_attestations,
)
from audit_trail import (
    AuditProjection,
    AuthorizedTestChanges,
    OverallAuditEntry,
    ReviewAuditEvent,
    ValidationAuditEvent,
    allowed_review_finding_origins,
    managed_slice_document_path,
)
from contracts import (
    ContractResult,
    FindingRecord,
    PlannedSlice,
    ValidationAttestation,
)
from finding_reducer import reduce_findings
from gates import matches_path_patterns
from git_service import inspect_repository
from inbox_watcher import move_to_outbox
from repo_changes import collect_repository_changes
from review_packets import ReviewPacket
from workflow import WorkflowExecutionError, WorkflowHistory
from workflow_state import (
    GateReason,
    SliceStatus,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitRecord,
    WorkUnitStatus,
)


logger = logging.getLogger(__name__)


def _authorized_test_approval(
    unit: WorkUnitRecord,
    structured_replay: ArtifactReplayResult | None,
) -> AuthorizedTestChanges | None:
    if unit.active_test_fingerprint is None or structured_replay is None:
        return None
    decision = next(
        (
            item
            for item in reversed(structured_replay.gate_decisions)
            if item.work_unit_id == str(unit.work_unit_id)
            if item.approved
            and item.reason == GateReason.TEST_CHANGE.value
            and item.fingerprint == unit.active_test_fingerprint
            and item.paths == unit.active_test_paths
        ),
        None,
    )
    if decision is None:
        return None
    return AuthorizedTestChanges(
        approved=True,
        paths=decision.paths,
        approved_by=decision.authority.value,
        rationale=decision.rationale,
        approved_at=decision.gate_created_at,
        diff_fingerprint=decision.fingerprint,
    )


def _latest_review_approved(history: WorkflowHistory) -> bool:
    """Report whether this work unit's own latest review approved its work."""

    latest = next(
        (
            event
            for event in reversed(history.events)
            if isinstance(event, ReviewAuditEvent)
        ),
        None,
    )
    return latest is not None and latest.result.approval is True


def _audit_projection(
    state: WorkflowState,
    unit: WorkUnitRecord,
    history: WorkflowHistory,
    approval: AuthorizedTestChanges | None = None,
    structured_replay: ArtifactReplayResult | None = None,
) -> AuditProjection:
    slice_record = next(item for item in state.slices if item.slice_id == unit.slice_id)
    implementation_ready = (
        None
        if unit.kind is WorkUnitKind.PLAN
        else unit.current_step not in {
            WorkflowStep.CODEX_IMPLEMENTATION,
            WorkflowStep.CODEX_CORRECTION,
        }
    )
    commit_authorized = (
        unit.kind is WorkUnitKind.SLICE
        and (
            unit.current_step is WorkflowStep.SLICE_COMMIT
            or slice_record.status is SliceStatus.COMPLETED
        )
    )
    latest_review = next(
        (
            event.result
            for event in reversed(history.events)
            if isinstance(event, ReviewAuditEvent)
        ),
        None,
    )
    review_record = None
    if (
        structured_replay is not None
        and latest_review is not None
        and latest_review.validation is not None
    ):
        review_record = next(
            (
                record
                for record in reversed(structured_replay.records)
                if isinstance(record.payload, ReviewPayload)
                and record.payload.work_unit_id == str(unit.work_unit_id)
                and record.payload.verdict == "approved"
                and record.fingerprint.sha256
                == latest_review.validation.diff_fingerprint
                and review_payload_matches_result(record.payload, latest_review)
            ),
            None,
        )
    return AuditProjection(
        slice_id=unit.slice_id,
        events=history.events,
        test_approval=approval or _authorized_test_approval(unit, structured_replay),
        implementation_ready=implementation_ready,
        commit_authorized=commit_authorized,
        red_state_followup_slice=(
            None
            if review_record is None
            else review_record.payload.red_state_followup_slice
        ),
        review_record=review_record,
        review_work_unit_id=str(unit.work_unit_id),
    )


def _persisted_histories(
    state: WorkflowState,
    structured_replay: ArtifactReplayResult | None = None,
    read_blob: Callable[[object], bytes] | None = None,
) -> dict[int, WorkflowHistory]:
    raw = state.runtime_history
    if not isinstance(raw, dict):
        return {}
    if raw and all(
        isinstance(key, str)
        and key.isdigit()
        and isinstance(value, dict)
        and "workflow_event_record_refs" in value
        for key, value in raw.items()
    ):
        histories = {int(key): WorkflowHistory(int(key)) for key in raw}
        if structured_replay is not None and read_blob is not None:
            return _attach_record_events(histories, structured_replay, read_blob)
        return histories
    candidates: list[object] = []
    if set(raw) == {"current", "archive"}:
        archive = raw.get("archive")
        if isinstance(archive, list):
            candidates.extend(archive)
        candidates.append(raw.get("current"))
    else:
        candidates.append(raw)
    histories: dict[int, WorkflowHistory] = {}
    for candidate in candidates:
        try:
            parsed = WorkflowHistory.from_dict(candidate)
        except (TypeError, ValueError):
            continue
        histories[parsed.work_unit_id] = parsed
    if structured_replay is not None and read_blob is not None:
        histories = _attach_record_events(histories, structured_replay, read_blob)
    return histories


def _attach_record_events(
    histories: dict[int, WorkflowHistory],
    replay: ArtifactReplayResult,
    read_blob: Callable[[object], bytes],
) -> dict[int, WorkflowHistory]:
    """Rehydrate the retired event mirror from record references only."""
    reviews = {
        item.record_id: item for item in project_review_contracts(replay, read_blob)
    }
    validations = dict(project_validation_attestations(replay, read_blob))
    records = {record.record_id: record for record in replay.records}
    positions = {
        record.record_id: index for index, record in enumerate(replay.records)
    }
    projected = dict(histories)
    event_lists: dict[int, list[ReviewAuditEvent | ValidationAuditEvent]] = {}
    attestation_lists: dict[int, list[ValidationAttestation]] = {}
    latest_reviews: dict[int, tuple[str, ContractResult]] = {}
    for event in replay.workflow_events:
        if event.event_kind == "transition" or event.work_unit_id is None:
            continue
        try:
            work_unit_id = int(event.work_unit_id)
        except ValueError as exc:
            raise WorkflowExecutionError(
                "workflow event work-unit identity is not numeric"
            ) from exc
        work_unit_events = event_lists.setdefault(work_unit_id, [])
        referenced_id = event.record_refs[0]
        if event.event_kind == "validation":
            attestation = validations.get(referenced_id)
            if attestation is None:
                raise WorkflowExecutionError(
                    "validation workflow event has no projected attestation"
                )
            work_unit_events.append(
                ValidationAuditEvent(
                    len(work_unit_events) + 1,
                    int(event.slice_id),
                    attestation,
                )
            )
            if all(
                existing.attestation_id != attestation.attestation_id
                for existing in attestation_lists.setdefault(work_unit_id, [])
            ):
                attestation_lists[work_unit_id].append(attestation)
            continue
        review = reviews.get(referenced_id)
        if review is None or event.round_number is None:
            raise WorkflowExecutionError(
                "review workflow event has no complete projected review contract"
            )
        review_record = records[referenced_id]
        review_position = positions[referenced_id]
        finding_ledger = reduce_findings(
            replay.subset(replay.records[:review_position])
        ).ledger.findings
        prior_steps = tuple(
            record.payload.step
            for record in replay.records[: review_position + 1]
            if isinstance(record.payload, WorkflowTransitionPayload)
            and record.payload.work_unit_id == event.work_unit_id
        )
        is_final_review = (
            prior_steps
            and prior_steps[-1]
            == WorkflowStep.CLAUDE_FINAL_REVIEW.value
        )
        allowed_origins = allowed_review_finding_origins(
            finding_ledger,
            current_slice_id=int(event.slice_id),
            is_final_review=bool(is_final_review),
        )
        review_validation = review.result.validation
        work_unit_attestations = attestation_lists.setdefault(work_unit_id, [])
        if (
            review_validation is not None
            and all(
                existing.attestation_id != review_validation.attestation_id
                for existing in work_unit_attestations
            )
        ):
            # Final review may reuse the last Slice attestation without writing
            # another ValidationAttestation/WorkflowEvent for its own work unit.
            # Its ReviewValidationBinding is nevertheless authoritative and the
            # audit contract requires that attestation to precede the review.
            work_unit_attestations.append(review_validation)
            work_unit_events.append(
                ValidationAuditEvent(
                    len(work_unit_events) + 1,
                    int(event.slice_id),
                    review_validation,
                )
            )
        work_unit_events.append(
            ReviewAuditEvent(
                len(work_unit_events) + 1,
                int(event.slice_id),
                event.round_number,
                review.result,
                allowed_origins,
                is_final_review=bool(is_final_review),
            )
        )
        latest_reviews[work_unit_id] = (
            review_record.fingerprint.sha256,
            review.result,
        )
    for work_unit_id in {*projected, *event_lists}:
        history = projected.get(work_unit_id, WorkflowHistory(work_unit_id))
        latest = latest_reviews.get(work_unit_id)
        projected[work_unit_id] = replace(
            history,
            events=tuple(event_lists.get(work_unit_id, ())),
            attestations=tuple(attestation_lists.get(work_unit_id, ())),
            last_claude_fingerprint=(  # allowlist:provider -- canonical history field
                history.last_claude_fingerprint  # allowlist:provider -- canonical history field
                if latest is None
                else latest[0]
            ),
            latest_claude_review=(  # allowlist:provider -- canonical history field
                history.latest_claude_review  # allowlist:provider -- canonical history field
                if latest is None
                else latest[1]
            ),
        )
    return projected


def _hydrate_record_history(
    history: WorkflowHistory,
    replay: ArtifactReplayResult,
    read_blob: Callable[[object], bytes],
) -> WorkflowHistory:
    """Project every durable history fact for one request-building work unit.

    Optional facts remain absent only when the validated record prefix contains
    no bound authority for them; a cache miss can therefore never masquerade as
    authoritative absence after this boundary.
    """
    projected = _attach_record_events(
        {history.work_unit_id: history}, replay, read_blob
    )[history.work_unit_id]
    reduced = reduce_findings(replay)
    findings = reduced.ledger.findings

    packet_records = tuple(
        record
        for record in replay.records
        if isinstance(record.payload, ReviewPacketPayload)
        and record.payload.work_unit_id == str(history.work_unit_id)
    )
    active_review_packet = None
    if packet_records:
        payload = packet_records[-1].payload
        packet_bytes = read_blob(payload.blob)
        active_review_packet = ReviewPacket.restore(packet_bytes, payload.blob.sha256)
        if (
            active_review_packet.purpose != payload.purpose
            or active_review_packet.fingerprint != payload.fingerprint
            or active_review_packet.manifest.paths != payload.manifest
            or active_review_packet.manifest.diff_coverage_digest
            != payload.diff_coverage_sha256
        ):
            raise WorkflowExecutionError(
                "record-backed review packet differs from its authoritative metadata"
            )

    return replace(
        projected,
        findings=findings,
        active_review_packet=active_review_packet,
    )


def _recover_final_review_attestation(
    state: WorkflowState,
    current_history: WorkflowHistory,
    structured_replay: ArtifactReplayResult | None = None,
    read_blob: Callable[[object], bytes] | None = None,
) -> WorkflowHistory:
    """Recover the latest prior attestation at a final-review transition.

    Attestations are fingerprint-bound rather than work-unit-bound.  Keeping the
    latest prior fact lets ``_attestation`` reuse it when the branch is unchanged;
    a changed branch still selects and persists a new validation normally.
    """
    if (
        state.current_work_unit.kind is not WorkUnitKind.FINAL_REVIEW
    ):
        return current_history
    carried = current_history.attestations[-1:]
    if not carried:
        histories = _persisted_histories(state, structured_replay, read_blob)
        prior = tuple(
            history
            for work_unit_id, history in sorted(histories.items())
            if work_unit_id < current_history.work_unit_id and history.attestations
        )
        if not prior:
            return current_history
        carried = prior[-1].attestations[-1:]
    attestation = carried[0]
    if any(
        isinstance(event, ValidationAuditEvent)
        and event.attestation.attestation_id == attestation.attestation_id
        for event in current_history.events
    ):
        return current_history
    events = (
        ValidationAuditEvent(
            event_id=1,
            slice_id=state.current_slice_id,
            attestation=attestation,
        ),
        *(
            replace(event, event_id=index)
            for index, event in enumerate(current_history.events, start=2)
        ),
    )
    return replace(
        current_history,
        events=events,
        attestations=carried,
    )


def _overall_audit_entries(
    state: WorkflowState,
    structured_replay: ArtifactReplayResult | None = None,
    read_blob: Callable[[object], bytes] | None = None,
) -> tuple[OverallAuditEntry, ...]:
    histories = _persisted_histories(state, structured_replay, read_blob)
    entries: list[OverallAuditEntry] = []
    for unit in state.work_units:
        history = histories.get(unit.work_unit_id, WorkflowHistory(unit.work_unit_id))
        planned = next(
            (item for item in state.planned_slices if item.slice_id == unit.slice_id),
            None,
        )
        if unit.kind is WorkUnitKind.PLAN:
            label = "Arbeitseinheit %02d – Planung" % unit.work_unit_id
            summary = "Planung und Review der geordneten Implementierungsslices"
            scope = tuple(
                sorted(
                    {
                        *state.task_scope_patterns,
                        *((state.audit_report_path,) if state.audit_report_path else ()),
                        *(
                            path
                            for item in state.planned_slices
                            for path in item.scope_paths
                        ),
                    }
                )
            )
        elif unit.kind is WorkUnitKind.FINAL_REVIEW:
            label = "Arbeitseinheit %02d – Branch-Entdeckung" % unit.work_unit_id
            summary = "Branchweiter Entdeckungsreview durch Claude"
            scope = tuple(
                sorted({path for item in state.slices for path in item.scope_paths})
            )
        else:
            label = "Arbeitseinheit %02d – Slice %02d" % (
                unit.work_unit_id,
                unit.slice_id,
            )
            summary = (
                planned.summary
                if planned is not None
                else "Direkte Implementierungseinheit"
            )
            scope = (
                planned.scope_paths
                if planned is not None
                else next(
                    item for item in state.slices if item.slice_id == unit.slice_id
                ).scope_paths
            )
        entries.append(
            OverallAuditEntry(
                label=label,
                summary=summary,
                scope_paths=scope,
                projection=_audit_projection(
                    state,
                    unit,
                    history,
                    structured_replay=structured_replay,
                ),
            )
        )
    return tuple(entries)


def _managed_audit_path(task_file: Path, task_digest: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task_file.stem.lower()).strip("-") or "task"
    return f"docs/internal/{slug}-review-{task_digest[:8]}.md"


def _archive_stale_untracked_audit_reports(
    repository_root: Path,
    *,
    current_audit_path: str,
    outbox_failed_dir: Path,
) -> tuple[Path, ...]:
    """Archive abandoned audit reports from an older digest of the same task.

    A regenerated Inbox handoff keeps its filename but receives a new content
    digest and therefore a new managed audit path.  An audit report left
    untracked by the abandoned run must not contaminate the next Slice diff.
    Only exact, untracked sibling reports are moved; tracked history and all
    unrelated documents remain untouched.
    """
    current = PurePosixPath(current_audit_path)
    match = re.fullmatch(r"(?P<prefix>.+-review-)[0-9a-f]{8}\.md", current.name)
    if current.parent.as_posix() != "docs/internal" or match is None:
        raise WorkflowExecutionError(
            "managed audit path cannot identify stale sibling reports"
        )
    identity = inspect_repository(repository_root)
    changes = collect_repository_changes(repository_root, identity.head)
    untracked = {
        entry.path
        for entry in changes.entries
        if not entry.tracked and entry.path != current_audit_path
    }
    sibling_pattern = re.compile(
        rf"{re.escape(match.group('prefix'))}[0-9a-f]{{8}}\.md"
    )
    candidates = tuple(
        sorted(
            path
            for path in untracked
            if PurePosixPath(path).parent == current.parent
            and sibling_pattern.fullmatch(PurePosixPath(path).name)
        )
    )
    if not candidates:
        return ()
    outbox_failed_dir.mkdir(parents=True, exist_ok=True)
    archived: list[Path] = []
    for relative_path in candidates:
        source = repository_root.joinpath(*PurePosixPath(relative_path).parts)
        if source.is_symlink() or not source.is_file():
            raise WorkflowExecutionError(
                f"stale managed audit candidate is not a regular file: {relative_path}"
            )
        destination = move_to_outbox(
            source,
            outbox_failed_dir,
            source_name=f"{source.name}.stale-audit",
        )
        archived.append(destination)
        logger.info(
            "Archived stale untracked managed audit from an abandoned watch run: %s",
            destination,
        )
    return tuple(archived)


def _managed_slice_scope_pattern(audit_report_path: str) -> str:
    stem = Path(audit_report_path).stem
    stem = re.sub(  # allowlist:german
        r"-(?:gesamtpruefung|review)-[0-9a-f]{8}$", "", stem  # allowlist:german
    )
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "task"
    return f"docs/internal/slice-{slug}-*.md"


def _is_managed_audit_path(state: WorkflowState, path: str) -> bool:
    return (
        path == state.audit_report_path
        or _is_planned_slice_document(state, path)
        or (
            state.audit_report_path is not None
            and matches_path_patterns(
                path,
                (_managed_slice_scope_pattern(state.audit_report_path),),
            )
        )
    )


def _is_planned_slice_document(state: WorkflowState, path: str) -> bool:
    candidate = PurePosixPath(path)
    if not path.startswith("docs/internal/slice-") or candidate.suffix != ".md":
        return False
    return any(
        path in planned.scope_paths
        and f"-{planned.slice_id:02d}-" in candidate.name
        for planned in state.planned_slices
    )


def _attach_managed_audit_paths(state: WorkflowState) -> WorkflowState:
    """Backfill pre-feature Inbox plans before their first implementation Slice."""
    if state.audit_report_path is None or not state.planned_slices:
        return state
    if any(item.status is SliceStatus.COMPLETED for item in state.slices):
        return state
    updated = tuple(
        PlannedSlice(
            slice_id=item.slice_id,
            summary=item.summary,
            scope_paths=tuple(
                sorted(
                    {
                        *item.scope_paths,
                        state.audit_report_path,
                        next(
                            (
                                path
                                for path in item.scope_paths
                                if path.startswith("docs/internal/slice-")
                                and path.endswith(".md")
                                and f"-{item.slice_id:02d}-" in Path(path).name
                            ),
                            managed_slice_document_path(
                                state.audit_report_path,
                                item.slice_id,
                                item.summary,
                            ),
                        ),
                    }
                )
            ),
            acceptance_criteria=item.acceptance_criteria,
        )
        for item in state.planned_slices
    )
    if updated == state.planned_slices:
        return state
    if any(item.scope_paths for item in state.slices):
        raise WorkflowExecutionError(
            "cannot retrofit managed audit paths after a Slice Git boundary was bound"
        )
    logger.info(
        "Backfilled consolidated audit and deferred Slice document paths into the approved plan."
    )
    return replace(state, planned_slices=updated)
