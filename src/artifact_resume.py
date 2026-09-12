"""Fail-closed record-authoritative resume for structured-v2 runs.

The append-only record chain is the only technical authority. ``state.json``
is only a disposable run locator and checkpoints are disposable projections,
never independent workflow facts. Replay accepts only the bound reducer
semantics; unsupported, corrupt, or non-canonical histories remain fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from artifact_models import (
    ArtifactRecord,
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    RecordType,
    ReviewPayload,
    RunIdentityPayload,
    ValidationAttestationPayload,
    WorkflowEventPayload,
    WorkflowTransitionPayload,
)
from artifact_store import ArtifactStore, ArtifactStoreError
from artifact_replay import (
    ArtifactReplayError,
    ArtifactReplayResult,
    ReplayDiagnosticCode,
    project_workflow_state,
    replay_artifacts,
)
from workflow_state import ProtocolMode, WorkflowState


class ArtifactResumeError(ValueError):
    """Raised when records cannot safely project a resumable workflow state."""

    def __init__(
        self,
        message: str,
        *,
        code: ReplayDiagnosticCode = ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
        record_id: str | None = None,
    ) -> None:
        self.code = code
        self.record_id = record_id
        location = f" at record {record_id}" if record_id is not None else ""
        super().__init__(f"{code.value}{location}: {message}")


@dataclass(frozen=True, slots=True)
class ResumeResolution:
    state: WorkflowState
    mode: ProtocolMode
    record_head_id: str | None
    replay_result: ArtifactReplayResult | None
    validated_store: ArtifactStore | None = None


def require_workflow_status_prefix(
    replay: ArtifactReplayResult,
) -> tuple[tuple[ArtifactRecord, ...], tuple[ArtifactRecord, ...]]:
    """Reject every pre-R2 chain before it can be silently backfilled."""
    transition_records = tuple(
        record
        for record in replay.records
        if record.record_type is RecordType.WORKFLOW_TRANSITION
    )
    policy_records = tuple(
        record
        for record in replay.records
        if record.record_type is RecordType.WORKFLOW_POLICY
    )
    if not transition_records or replay.workflow_cursor is None:
        raise ArtifactResumeError(
            "structured-v2 run has no workflow transition prefix",
            code=ReplayDiagnosticCode.RECORD_MISSING,
        )
    if not policy_records:
        raise ArtifactResumeError(
            "structured-v2 run has no workflow policy prefix",
            code=ReplayDiagnosticCode.RECORD_MISSING,
        )
    return transition_records, policy_records


def require_workflow_event_prefix(
    replay: ArtifactReplayResult,
    *,
    allow_incomplete_tail: bool = False,
) -> None:
    """Require one non-duplicating event reference for every auditable fact."""
    expected = {
        record.record_id: kind
        for record in replay.records
        if record.record_id != replay.pending_review_record_id
        for kind in (
            "run"
            if isinstance(record.payload, RunIdentityPayload)
            else "transition"
            if isinstance(record.payload, WorkflowTransitionPayload)
            else "validation"
            if isinstance(record.payload, ValidationAttestationPayload)
            else "review"
            if isinstance(record.payload, ReviewPayload)
            else None,
        )
        if kind is not None
    }
    observed: dict[str, str] = {}
    event_records = tuple(
        record
        for record in replay.records
        if isinstance(record.payload, WorkflowEventPayload)
    )
    for record in event_records:
        payload = record.payload
        referenced = payload.record_refs[0]
        if referenced in observed:
            raise ArtifactResumeError(
                "workflow event prefix contains a duplicate domain reference",
                code=ReplayDiagnosticCode.RECORD_DUPLICATE,
                record_id=record.record_id,
            )
        observed[referenced] = payload.event_kind
    if expected == observed:
        return
    missing = set(expected) - set(observed)
    if (
        allow_incomplete_tail
        and not (set(observed) - set(expected))
        and missing == {replay.pending_workflow_event_record_id}
    ):
        return
    differing = next(iter(set(expected) ^ set(observed)), None)
    related = next(
        (
            record.record_id
            for record in event_records
            if record.payload.record_refs[0] == differing
        ),
        differing,
    )
    raise ArtifactResumeError(
        "structured-v2 run has no complete workflow event prefix",
        code=(
            ReplayDiagnosticCode.RECORD_MISSING
            if set(observed).issubset(expected)
            else ReplayDiagnosticCode.MIRROR_AMBIGUOUS
        ),
        record_id=related,
    )


def require_gate_prefix(replay: ArtifactReplayResult) -> None:
    """Reject pre-R5 chains; gate authority is never inferred from a cache."""
    records = tuple(
        record
        for record in replay.records
        if record.record_type is RecordType.GATE_TRANSITION
    )
    if not records or not replay.gate_transitions:
        raise ArtifactResumeError(
            "structured-v2 run has no gate transition prefix",
            code=ReplayDiagnosticCode.RECORD_MISSING,
        )


def require_side_effect_ledger_prefix(replay: ArtifactReplayResult) -> None:
    """Reject pre-R4 chains; the ledger is never synthesized from projections."""
    initializers = tuple(
        item
        for item in replay.side_effects
        if item.effect_class == "ledger"
        and item.operation == ("structured-v2-side-effect-ledger",)
        and item.result == "initialized"
    )
    if len(initializers) != 1:
        raise ArtifactResumeError(
            "structured-v2 run has no unique initialized side-effect ledger",
            code=ReplayDiagnosticCode.RECORD_MISSING,
            record_id=(initializers[0].intent_record_id if initializers else None),
        )


def resolve_resume_state(
    repository_root: Path,
    state_or_run_id: WorkflowState | str,
    *,
    validated_store: ArtifactStore | None = None,
) -> ResumeResolution:
    """Project one resumable state solely from its authoritative record chain.

    A supplied ``WorkflowState`` is used only as a run-id locator. None of its
    workflow fields participates in replay, validation, or a decision.
    """
    if isinstance(state_or_run_id, WorkflowState):
        run_id = state_or_run_id.run_id
    elif isinstance(state_or_run_id, str) and state_or_run_id.strip():
        run_id = state_or_run_id
    else:
        raise ArtifactResumeError(
            "structured-v2 resume requires an exact run id",
            code=ReplayDiagnosticCode.RECORD_MISSING,
        )

    try:
        root = Path(repository_root).resolve()
        if validated_store is None:
            store = ArtifactStore(root, run_id)
            chain = store.load_chain()
        else:
            store = validated_store
            if store.repository_root != root or store.run_id != run_id:
                raise ArtifactStoreError(
                    "process-local artifact derivative belongs to another chain"
                )
            chain = store.current_chain()
    except ArtifactStoreError as exc:
        raise ArtifactResumeError(
            f"structured-v2 record chain for run {run_id!r} is invalid: {exc}; "
            "repair or restore the append-only records before resuming",
            code=ReplayDiagnosticCode.RECORD_UNKNOWN,
        ) from exc
    if not chain:
        raise ArtifactResumeError(
            f"structured-v2 run {run_id!r} has no records; restore its record "
            "directory before resuming",
            code=ReplayDiagnosticCode.RECORD_MISSING,
        )

    try:
        with store.progress_phase("artifact-replay"):
            replay = replay_artifacts(
                chain,
                run_id,
                require_content_authority=True,
                allow_incomplete_review_tail=True,
            )
        require_workflow_event_prefix(replay, allow_incomplete_tail=True)
        require_workflow_status_prefix(replay)
        require_gate_prefix(replay)
        require_side_effect_ledger_prefix(replay)
        projected = project_workflow_state(replay).state
    except ArtifactReplayError as exc:
        raise ArtifactResumeError(
            exc.diagnostic.message,
            code=exc.code,
            record_id=exc.record_id,
        ) from exc

    _validate_finding_handoff(repository_root, replay, projected)
    head = replay.head_record_id
    assert head is not None
    return ResumeResolution(
        projected,
        ProtocolMode.STRUCTURED_V2,
        head,
        replay,
        validated_store=store,
    )


def _validate_finding_handoff(
    repository_root: Path,
    replay: ArtifactReplayResult,
    projected: WorkflowState,
) -> None:
    """Revalidate cross-run import causality without consulting a state cache."""
    import_records = tuple(
        record
        for record in replay.records
        if isinstance(record.payload, FindingHandoffImportPayload)
    )
    source_run_id = projected.finding_handoff_source_run_id
    export_record_id = projected.finding_handoff_export_record_id
    if source_run_id is None:
        if import_records:
            raise ArtifactResumeError(
                "record chain contains an unbound finding import",
                code=ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                record_id=import_records[0].record_id,
            )
        return
    if export_record_id is None or len(import_records) != 1:
        raise ArtifactResumeError(
            f"finding handoff requires exactly one import, found {len(import_records)}",
            code=ReplayDiagnosticCode.RECORD_MISSING,
        )

    try:
        # Lazy import avoids the state_io -> artifact_resume -> bridge ->
        # task_contract -> audit_trail -> state_io initialization cycle.
        from artifact_bridge import (
            ArtifactBridgeError,
            finding_handoff_import_payload,
        )

        source_store = ArtifactStore(repository_root, source_run_id)
        source_replay = replay_artifacts(
            source_store.load_chain(),
            source_run_id,
            require_content_authority=True,
        )
        export_record = next(
            record
            for record in source_replay.records
            if record.record_id == export_record_id
        )
        if not isinstance(export_record.payload, FindingHandoffExportPayload):
            raise ArtifactBridgeError(
                "referenced source record is not a finding export"
            )
        if (
            export_record.payload.approved_plan_commit
            != projected.approved_plan_commit
        ):
            raise ArtifactBridgeError(
                "source export plan commit differs from target records"
            )
        expected_import = finding_handoff_import_payload(
            source_replay,
            export_record,
            target_run_id=projected.run_id,
            target_task_bytes=Path(projected.task_file).read_bytes(),
        )
    except (
        ArtifactStoreError,
        ArtifactReplayError,
        RuntimeError,
        OSError,
        StopIteration,
    ) as exc:
        raise ArtifactResumeError(
            f"finding handoff source is no longer valid: {exc}",
            code=ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
            record_id=export_record_id,
        ) from exc
    if import_records[0].payload != expected_import:
        raise ArtifactResumeError(
            "finding import differs from its revalidated source",
            code=ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH,
            record_id=import_records[0].record_id,
        )


__all__ = [
    "ArtifactResumeError",
    "ResumeResolution",
    "require_gate_prefix",
    "require_side_effect_ledger_prefix",
    "require_workflow_event_prefix",
    "require_workflow_status_prefix",
    "resolve_resume_state",
]
