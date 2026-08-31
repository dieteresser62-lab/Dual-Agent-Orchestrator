"""Fail-closed resume resolution for structured-v2 runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
from pathlib import Path
import shlex

from artifact_models import (
    BindingPayload,
    ArtifactRecord,
    CorrectionWorkUnitPayload,
    FindingTransitionPayload,
    GateDecisionPayload,
    GateTransitionPayload,
    InvocationFailurePayload,
    PlanPayload,
    QuotaPausePayload,
    RecordType,
    RoleProfilePayload,
    ReviewPayload,
    ProviderContentPayload,
    ReviewPacketPayload,
    ResumeCheckPayload,
    RunIdentityPayload,
    RunProfilePayload,
    SideEffectPayload,
    SliceBoundaryPayload,
    TaskPayload,
    TransientRetryPayload,
    ValidationAttestationPayload,
    ValidationContentPayload,
    WorkUnitPayload,
    WorkflowCompletionPayload,
    WorkflowEventPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    ProviderInputMeasurementPayload,
    FinalReviewPreflightPayload,
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    canonical_json,
    provider_text_evidence,
)
from artifact_store import ArtifactStore, ArtifactStoreError
from artifact_replay import (
    ArtifactReplayError,
    ArtifactReplayResult,
    ReplayedWorkflowCursor,
    ReplayedWorkUnitState,
    ReplayDiagnosticCode,
    project_review_contracts,
    replay_artifacts,
)
from finding_reducer import (
    FindingRecordedStatusProjection,
    project_finding_statuses,
    project_latest_recorded_statuses,
    project_legacy_mirror_statuses,
    reduce_findings,
)
from workflow_state import (
    AgentFailureKind,
    GateRecord,
    InvocationFailureRecord,
    NATIVE_CLAUDE_REVIEW_TRANSPORT,
    NATIVE_CODEX_RESULT_TRANSPORT,
    ProtocolMode,
    ProtocolBinding,
    SliceStatus,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    project_implementer_return_policy,
)


class ArtifactResumeError(ValueError):
    """Raised when records cannot safely rehydrate their state-v3 mirror."""

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


def assert_run_binding_mirror(
    replay: ArtifactReplayResult,
    state: WorkflowState,
    binding: ProtocolBinding,
) -> None:
    """Reject an early run record that differs from its state-v3 mirror."""
    identity = replay.run_identity
    profile = replay.run_profile
    if identity is None or profile is None:
        raise AssertionError(
            "accepted replay is missing its required run identity or profile"
        )
    expected_identity = RunIdentityPayload(
        task_file=state.task_file,
        branch=state.branch,
        branch_base=state.branch_base,
        execution_mode=state.execution_mode,
        audit_report_path=state.audit_report_path,
    )
    if identity != expected_identity:
        identity_record = next(
            record for record in replay.records
            if record.record_type is RecordType.RUN_IDENTITY
        )
        raise ArtifactResumeError(
            "run identity differs from state-v3; repair the state mirror or "
            "restore the matching record chain before resuming",
            code=ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
            record_id=identity_record.record_id,
        )
    expected_profile = RunProfilePayload(
        implementer=RoleProfilePayload(
            binding.codex_profile.model, binding.codex_profile.effort
        ),
        reviewer=RoleProfilePayload(
            binding.claude_profile.model, binding.claude_profile.effort
        ),
    )
    if profile != expected_profile:
        profile_record = next(
            record for record in replay.records
            if record.record_type is RecordType.RUN_PROFILE
        )
        raise ArtifactResumeError(
            "run profile differs from state-v3; repair the state mirror or "
            "restore the matching record chain before resuming",
            code=ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
            record_id=profile_record.record_id,
        )


def require_workflow_status_prefix(
    replay: ArtifactReplayResult,
) -> tuple[tuple[ArtifactRecord, ...], tuple[ArtifactRecord, ...]]:
    """Reject every pre-R2 chain before it can be silently backfilled."""
    transition_records = tuple(
        record for record in replay.records
        if record.record_type is RecordType.WORKFLOW_TRANSITION
    )
    policy_records = tuple(
        record for record in replay.records
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
    if expected != observed:
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


def assert_workflow_status_mirror(
    replay: ArtifactReplayResult,
    state: WorkflowState,
) -> None:
    """Require the R2 cursor/status prefix and compare it with state-v3."""
    transition_records, policy_records = require_workflow_status_prefix(replay)

    expected_cursor = ReplayedWorkflowCursor(
        str(state.current_slice_id),
        str(state.current_work_unit_id),
        state.current_step.value,
    )
    if replay.workflow_cursor != expected_cursor:
        raise ArtifactResumeError(
            "workflow cursor differs from state-v3",
            record_id=transition_records[-1].record_id,
        )

    expected_slice_statuses = tuple(
        (str(item.slice_id), item.status.value) for item in state.slices
    )
    if replay.slice_statuses != expected_slice_statuses:
        raise ArtifactResumeError(
            "slice statuses differ from state-v3",
            record_id=transition_records[-1].record_id,
        )

    expected_work_unit_states = tuple(
        ReplayedWorkUnitState(
            str(item.work_unit_id),
            str(item.slice_id),
            item.status.value,
            item.current_step.value,
        )
        for item in state.work_units
    )
    if replay.work_unit_states != expected_work_unit_states:
        raise ArtifactResumeError(
            "work-unit statuses or steps differ from state-v3",
            record_id=transition_records[-1].record_id,
        )

    expected_policies = tuple(
        WorkflowPolicyPayload(
            str(item.work_unit_id),
            *project_implementer_return_policy(item),
        )
        for item in state.work_units
    )
    if replay.workflow_policies != expected_policies:
        raise ArtifactResumeError(
            "workflow policies differ from state-v3",
            record_id=policy_records[-1].record_id,
        )


def assert_slice_boundary_mirror(
    replay: ArtifactReplayResult,
    state: WorkflowState,
) -> None:
    """Require every bound Slice boundary and compare its exact grouped facts."""
    expected = tuple(
        SliceBoundaryPayload(
            str(item.slice_id),
            item.start_commit,
            item.scope_change_groups,
            item.start_fingerprint,
        )
        for item in state.slices
        if item.start_commit is not None and item.start_fingerprint is not None
    )
    boundary_records = tuple(
        record for record in replay.records
        if record.record_type is RecordType.SLICE_BOUNDARY
    )
    if expected and not boundary_records:
        raise ArtifactResumeError(
            "structured-v2 run has no slice boundary prefix",
            code=ReplayDiagnosticCode.RECORD_MISSING,
        )
    if replay.slice_boundaries != expected:
        raise ArtifactResumeError(
            "slice boundaries differ from state-v3",
            record_id=(boundary_records[-1].record_id if boundary_records else None),
        )


def require_gate_prefix(replay: ArtifactReplayResult) -> None:
    """Reject pre-R5 chains; gate authority is never backfilled from state."""
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


def assert_gate_mirror(
    replay: ArtifactReplayResult,
    state: WorkflowState,
) -> None:
    """Compare all R5 gate state and decision bindings with state-v3."""
    require_gate_prefix(replay)
    expected_transitions = tuple(
        GateTransitionPayload(
            work_unit_id=str(unit.work_unit_id),
            gate_status=unit.gate.status.value,
            reason=unit.gate.reason.value,
            detail=unit.gate.detail,
            fingerprint=unit.gate.fingerprint,
            paths=unit.gate.paths,
            resume_step=(
                None if unit.gate.resume_step is None else unit.gate.resume_step.value
            ),
            active_test_fingerprint=unit.active_test_fingerprint,
            active_test_paths=unit.active_test_paths,
        )
        for unit in state.work_units
    )
    transition_records = tuple(
        record
        for record in replay.records
        if isinstance(record.payload, GateTransitionPayload)
    )
    if replay.gate_transitions != expected_transitions:
        raise ArtifactResumeError(
            "gate transitions differ from state-v3",
            record_id=transition_records[-1].record_id,
        )

    expected_decisions = tuple(
        (
            str(unit.work_unit_id),
            decision.approved,
            decision.reason.value,
            decision.fingerprint,
            decision.paths,
            decision.rationale,
            None if decision.resume_step is None else decision.resume_step.value,
        )
        for unit in state.work_units
        for decision in unit.gate_decisions
    )
    actual_decisions = tuple(
        (
            decision.work_unit_id,
            decision.approved,
            decision.reason,
            decision.fingerprint,
            decision.paths,
            decision.rationale,
            decision.resume_step,
        )
        for decision in replay.gate_decisions
    )
    if set(actual_decisions) != set(expected_decisions):
        decision_records = tuple(
            record
            for record in replay.records
            if isinstance(record.payload, GateDecisionPayload)
        )
        raise ArtifactResumeError(
            "gate decision bindings differ from state-v3",
            code=_mirror_difference_code(
                set(actual_decisions), set(expected_decisions)
            ),
            record_id=(decision_records[-1].record_id if decision_records else None),
        )


def require_side_effect_ledger_prefix(replay: ArtifactReplayResult) -> None:
    """Reject pre-R4 chains; the ledger is never synthesized from mirrors."""
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


def _invocation_failure_state_signature(
    failure: InvocationFailureRecord,
) -> tuple[object, ...]:
    provider_marker, provider_digest, provider_bytes = provider_text_evidence(
        failure.provider_text
    )
    return (
        failure.invocation_id,
        failure.idempotency_key,
        failure.role,
        failure.failure_kind.value,
        provider_marker,
        provider_digest,
        provider_bytes,
        failure.received_at,
        failure.step.value,
        failure.slice_id,
        failure.work_unit_id,
        failure.diagnostic_exit_code,
        failure.parse_path,
        failure.source_timezone,
        failure.reset_at_utc,
        failure.resume_at_utc,
        failure.safety_margin_seconds,
        failure.auto_resume_count,
        failure.automatic_resume,
        failure.diff_fingerprint,
    )


def _invocation_failure_payload_signature(
    failure: InvocationFailurePayload,
) -> tuple[object, ...]:
    return (
        failure.invocation_id,
        failure.idempotency_key,
        failure.role.value,
        failure.failure_kind,
        failure.provider_text,
        failure.provider_text_sha256,
        failure.provider_text_bytes,
        failure.received_at,
        failure.step,
        int(failure.slice_id),
        int(failure.work_unit_id),
        failure.diagnostic_exit_code,
        failure.parse_path,
        failure.source_timezone,
        failure.reset_at_utc,
        failure.resume_at_utc,
        failure.safety_margin_seconds,
        failure.auto_resume_count,
        failure.automatic_resume,
        failure.diff_fingerprint,
    )


def assert_invocation_failure_mirror(
    replay: ArtifactReplayResult,
    state: WorkflowState,
) -> WorkflowState:
    """Bind every failure mirror or project one record-ahead crash suffix.

    The sole accepted suffix is the current invocation failure itself.  Its
    persisted retry decision rehydrates the halted state, preventing resume
    from starting the same provider invocation a second time.
    """
    mirrored = tuple(
        failure
        for unit in state.work_units
        for failure in unit.invocation_failures
    )
    recorded = replay.invocation_failures
    if mirrored and not recorded:
        raise ArtifactResumeError(
            "structured-v2 run has invocation failures but no R6 failure records",
            code=ReplayDiagnosticCode.RECORD_MISSING,
        )
    try:
        mirrored_signatures = tuple(
            _invocation_failure_state_signature(item) for item in mirrored
        )
        recorded_signatures = tuple(
            _invocation_failure_payload_signature(item) for item in recorded
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactResumeError(
            "invocation failure identity cannot be projected into state-v3",
            code=ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
        ) from exc
    if recorded_signatures[: len(mirrored_signatures)] != mirrored_signatures:
        related = next(
            (
                record
                for record in reversed(replay.records)
                if isinstance(record.payload, InvocationFailurePayload)
            ),
            None,
        )
        raise ArtifactResumeError(
            "invocation failures differ from state-v3",
            code=_mirror_difference_code(
                set(recorded_signatures), set(mirrored_signatures)
            ),
            record_id=None if related is None else related.record_id,
        )
    if len(recorded) == len(mirrored):
        return state
    if len(recorded) != len(mirrored) + 1:
        raise ArtifactResumeError(
            "record chain has an ambiguous invocation failure suffix",
            code=ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
        )
    payload = recorded[-1]
    try:
        failure = InvocationFailureRecord(
            invocation_id=payload.invocation_id,
            idempotency_key=payload.idempotency_key,
            role=payload.role.value,
            failure_kind=AgentFailureKind(payload.failure_kind),
            provider_text=payload.provider_text,
            received_at=payload.received_at,
            step=WorkflowStep(payload.step),
            slice_id=int(payload.slice_id),
            work_unit_id=int(payload.work_unit_id),
            diagnostic_exit_code=payload.diagnostic_exit_code,
            parse_path=payload.parse_path,
            source_timezone=payload.source_timezone,
            reset_at_utc=payload.reset_at_utc,
            resume_at_utc=payload.resume_at_utc,
            safety_margin_seconds=payload.safety_margin_seconds,
            auto_resume_count=payload.auto_resume_count,
            automatic_resume=payload.automatic_resume,
            diff_fingerprint=payload.diff_fingerprint,
        )
        return state.record_invocation_failure(
            failure,
            wait_automatically=payload.automatic_resume,
            updated_at=payload.decision_at_utc,
        )
    except (TypeError, ValueError) as exc:
        related = next(
            record
            for record in reversed(replay.records)
            if isinstance(record.payload, InvocationFailurePayload)
        )
        raise ArtifactResumeError(
            "record-ahead invocation failure cannot rehydrate the current work unit",
            code=ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
            record_id=related.record_id,
        ) from exc


def project_transition_mirror_before_failure(
    state: WorkflowState,
    failure: InvocationFailurePayload,
) -> WorkflowState:
    """Project the exact status/gate prefix immediately before one failure."""
    current = state.current_work_unit
    if (
        not current.invocation_failures
        or current.invocation_failures[-1].invocation_id != failure.invocation_id
    ):
        return state
    prior_unit = replace(
        current,
        status=WorkUnitStatus.IN_PROGRESS,
        gate=GateRecord(),
        invocation_failures=current.invocation_failures[:-1],
    )
    return replace(
        state,
        work_units=tuple(
            prior_unit if unit.work_unit_id == current.work_unit_id else unit
            for unit in state.work_units
        ),
        slices=tuple(
            replace(item, status=SliceStatus.IN_PROGRESS)
            if item.slice_id == current.slice_id
            else item
            for item in state.slices
        ),
    )


def assert_side_effect_mirror(
    replay: ArtifactReplayResult,
    state: WorkflowState,
) -> WorkflowState:
    """Validate the mirror and project any authoritative record-ahead suffix."""
    require_side_effect_ledger_prefix(replay)
    projected_units = []
    for unit in state.work_units:
        recorded = replay.completed_side_effects(str(unit.work_unit_id))
        mirrored = unit.completed_side_effects
        if recorded[: len(mirrored)] != mirrored:
            related = next(
                (
                    item
                    for item in reversed(replay.side_effects)
                    if item.work_unit_id == str(unit.work_unit_id)
                ),
                None,
            )
            raise ArtifactResumeError(
                "completed side effects differ from the authoritative ledger",
                code=_mirror_difference_code(set(recorded), set(mirrored)),
                record_id=None if related is None else related.intent_record_id,
            )
        projected_units.append(
            unit if recorded == mirrored else replace(
                unit, completed_side_effects=recorded
            )
        )
    projected = tuple(projected_units)
    return state if projected == state.work_units else replace(state, work_units=projected)


def resolve_resume_state(repository_root: Path, state: WorkflowState) -> ResumeResolution:
    """Resolve the immutable protocol binding and verify structured mirror facts.

    Only structured-v2 is executable. Historical legacy and structured-v1
    states remain untouched on disk and are rejected without migration.
    """
    mode = state.effective_protocol_mode
    if mode is not ProtocolMode.STRUCTURED_V2:
        raise ArtifactResumeError(
            f"protocol {mode.value!r} is historical and cannot be resumed",
            code=ReplayDiagnosticCode.UNSUPPORTED_PROTOCOL,
        )
    binding = state.protocol_binding
    if (
        binding is None
        or binding.claude_review_transport != NATIVE_CLAUDE_REVIEW_TRANSPORT
        or binding.codex_result_transport != NATIVE_CODEX_RESULT_TRANSPORT
    ):
        raise ArtifactResumeError(
            "structured-v2 state lacks the complete native Codex-Claude transport binding",
            code=ReplayDiagnosticCode.UNSUPPORTED_PROTOCOL,
        )

    try:
        store = ArtifactStore(repository_root, state.run_id)
        chain = store.load_chain()
    except ArtifactStoreError as exc:
        raise ArtifactResumeError(
            f"structured-v2 record chain for run {state.run_id!r} is invalid: {exc}; "
            "repair or restore the append-only records before resuming",
            code=ReplayDiagnosticCode.RECORD_UNKNOWN,
        ) from exc
    if not chain:
        raise ArtifactResumeError(
            f"structured-v2 run {state.run_id!r} has no records; restore its record "
            "directory before resuming",
            code=ReplayDiagnosticCode.RECORD_MISSING,
        )

    try:
        replay = replay_artifacts(
            chain,
            state.run_id,
            require_content_authority=True,
            allow_incomplete_review_tail=True,
        )
    except ArtifactReplayError as exc:
        raise ArtifactResumeError(
            exc.diagnostic.message,
            code=exc.code,
            record_id=exc.record_id,
        ) from exc
    chain = replay.records
    assert_run_binding_mirror(replay, state, binding)
    require_workflow_event_prefix(replay, allow_incomplete_tail=True)
    require_workflow_status_prefix(replay)
    assert_slice_boundary_mirror(replay, state)
    state = assert_invocation_failure_mirror(replay, state)
    latest_failure_record = next(
        (
            record
            for record in reversed(chain)
            if isinstance(record.payload, InvocationFailurePayload)
        ),
        None,
    )
    pending_transition_invocation: str | None = None
    if latest_failure_record is not None:
        failure_position = chain.index(latest_failure_record)
        failure_payload = latest_failure_record.payload
        assert isinstance(failure_payload, InvocationFailurePayload)
        prior_state = project_transition_mirror_before_failure(
            state, failure_payload
        )
        workflow_transition_landed = any(
            index > failure_position
            and record.record_type is RecordType.WORKFLOW_TRANSITION
            for index, record in enumerate(chain)
        )
        gate_transition_landed = any(
            index > failure_position
            and record.record_type is RecordType.GATE_TRANSITION
            for index, record in enumerate(chain)
        )
        assert_workflow_status_mirror(
            replay, state if workflow_transition_landed else prior_state
        )
        assert_gate_mirror(
            replay, state if gate_transition_landed else prior_state
        )
        transition_landed = any(
            index > failure_position
            and record.logical_id
            in {
                f"quota-pause-{failure_payload.invocation_id}",
                f"transient-retry-{failure_payload.invocation_id}",
            }
            for index, record in enumerate(chain)
        )
        if (
            not transition_landed
            and state.current_work_unit.status
            in {
                WorkUnitStatus.WAITING_FOR_QUOTA,
                WorkUnitStatus.WAITING_FOR_RETRY,
                WorkUnitStatus.AWAITING_RESUME,
            }
        ):
            pending_transition_invocation = failure_payload.invocation_id
    else:
        assert_workflow_status_mirror(replay, state)
        assert_gate_mirror(replay, state)
    state = assert_side_effect_mirror(replay, state)

    head = replay.head_record_id
    assert head is not None

    import_records = tuple(
        record for record in chain
        if isinstance(record.payload, FindingHandoffImportPayload)
    )
    source_run_id = state.finding_handoff_source_run_id
    export_record_id = state.finding_handoff_export_record_id
    if (source_run_id is None) != (export_record_id is None):
        raise ArtifactResumeError(
            "finding handoff mirror is incomplete",
            code=ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
        )
    if source_run_id is None:
        if import_records:
            raise ArtifactResumeError(
                "record chain contains an unbound finding import",
                code=ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
                record_id=import_records[0].record_id,
            )
    else:
        if len(import_records) != 1:
            raise ArtifactResumeError(
                f"finding handoff requires exactly one import, found {len(import_records)}",
                code=ReplayDiagnosticCode.RECORD_MISSING,
            )
        try:
            # Lazy import avoids the state_io -> artifact_migration -> bridge ->
            # task_contract -> audit_trail -> state_io initialization cycle.
            from artifact_bridge import (
                ArtifactBridgeError,
                finding_handoff_import_payload,
            )

            source_chain = ArtifactStore(repository_root, source_run_id).load_chain()
            source_replay = replay_artifacts(source_chain, source_run_id)
            export_record = next(
                record for record in source_replay.records
                if record.record_id == export_record_id
            )
            if not isinstance(export_record.payload, FindingHandoffExportPayload):
                raise ArtifactBridgeError("referenced source record is not a finding export")
            if export_record.payload.approved_plan_commit != state.approved_plan_commit:
                raise ArtifactBridgeError("source export plan commit differs from state-v3")
            expected_import = finding_handoff_import_payload(
                source_replay,
                export_record,
                target_run_id=state.run_id,
                target_task_bytes=Path(state.task_file).read_bytes(),
            )
        except (ArtifactStoreError, ArtifactReplayError, RuntimeError, OSError, StopIteration) as exc:
            raise ArtifactResumeError(
                f"finding handoff source is no longer valid: {exc}",
                code=ReplayDiagnosticCode.RECORD_REFERENCE_MISSING,
                record_id=export_record_id,
            ) from exc
        if import_records[0].payload != expected_import:
            raise ArtifactResumeError(
                "finding import differs from its revalidated source",
                code=ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
                record_id=import_records[0].record_id,
            )

    def mismatch(
        message: str,
        record_id: str | None = None,
        *,
        code: ReplayDiagnosticCode = ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
    ) -> ArtifactResumeError:
        location = record_id or head
        return ArtifactResumeError(
            f"structured-v2 resume mismatch at record {location}: {message}; "
            "repair the state mirror or restore the matching record chain before resuming",
            code=code,
            record_id=location,
        )

    tasks = [item for item in chain if item.record_type is RecordType.TASK]
    if state.task_digest is not None and state.task_scope_patterns:
        expected_task = TaskPayload(
            target_branch=state.target_branch or state.branch,
            scope_paths=state.task_scope_patterns,
            assignment_sha256=state.task_digest,
        )
        if len(tasks) != 1:
            raise mismatch(f"expected exactly one task record, found {len(tasks)}")
        if tasks[0].payload != expected_task:
            raise mismatch("task contract differs from state-v3", tasks[0].record_id)

    unit_by_id = {str(item.work_unit_id): item for item in state.work_units}
    slice_by_id = {str(item.slice_id): item for item in state.slices}
    work_records = [
        item
        for item in chain
        if item.record_type in {RecordType.WORK_UNIT, RecordType.CORRECTION_WORK_UNIT}
    ]
    first_implementation_unit_id = next(
        (
            str(unit.work_unit_id) for unit in state.work_units
            if unit.kind is not WorkUnitKind.PLAN
        ),
        None,
    )
    latest_work_record_by_id = {}
    for record in work_records:
        payload = record.payload
        assert isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
        unit_id = record.logical_id.removeprefix("work-unit-")
        latest_work_record_by_id[unit_id] = record
        unit = unit_by_id.get(unit_id)
        slice_record = slice_by_id.get(payload.slice_id)
        pending_work_record = _recoverable_pending_work_record(state, chain, record)
        if unit is None or slice_record is None:
            raise mismatch("work-unit record has no state-v3 counterpart", record.record_id)
        if (
            str(unit.slice_id) != payload.slice_id
            or (payload.round_number > unit.round_number and not pending_work_record)
            or not set(payload.paths).issubset(slice_record.scope_paths)
        ):
            raise mismatch("work-unit round, slice, or path allowlist differs", record.record_id)
        if isinstance(payload, CorrectionWorkUnitPayload) != (
            unit.kind is WorkUnitKind.CORRECTION
        ):
            raise mismatch(
                "correction work-unit finding attribution differs from state-v3",
                record.record_id,
            )
        if isinstance(payload, WorkUnitPayload):
            expected_import_id = (
                import_records[0].record_id
                if import_records and unit_id == first_implementation_unit_id
                else None
            )
            if payload.finding_import_record_id != expected_import_id:
                raise mismatch(
                    "work-unit finding import binding differs from state-v3",
                    record.record_id,
                )

    for unit_id, record in latest_work_record_by_id.items():
        unit = unit_by_id[unit_id]
        payload = record.payload
        assert isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
        pending_work_record = _recoverable_pending_work_record(state, chain, record)
        if payload.paths != slice_by_id[payload.slice_id].scope_paths:
            raise mismatch(
                "latest work-unit path allowlist differs from state-v3",
                record.record_id,
            )
        if payload.round_number != unit.round_number and not pending_work_record:
            raise mismatch(
                "latest work-unit round differs from state-v3",
                record.record_id,
            )
        if (
            isinstance(payload, WorkUnitPayload)
            and payload.finding_import_record_id is not None
            and payload.open_finding_ids != tuple(sorted(unit.open_findings))
            and not pending_work_record
        ):
            raise mismatch(
                "latest work-unit finding state differs from state-v3",
                record.record_id,
            )
        if (
            isinstance(payload, CorrectionWorkUnitPayload)
            and payload.finding_ids != unit.open_findings
            and not pending_work_record
        ):
            raise mismatch(
                "latest correction finding attribution differs from state-v3",
                record.record_id,
            )

    current = state.current_work_unit
    if current.kind is not WorkUnitKind.PLAN and state.current_slice.scope_paths:
        current_records = [
            item
            for item in work_records
            if item.logical_id == f"work-unit-{current.work_unit_id}"
        ]
        if not current_records:
            raise mismatch(
                "current work unit has no structured record",
                code=ReplayDiagnosticCode.MIRROR_AHEAD,
            )
        latest = current_records[-1]
        payload = latest.payload
        assert isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
        if (
            payload.round_number != current.round_number
            and not _recoverable_pending_work_record(state, chain, latest)
        ):
            raise mismatch("current work-unit round differs from the record chain", latest.record_id)

    plans = [item for item in chain if item.record_type is RecordType.PLAN]
    if (
        state.execution_mode == "IMPLEMENT"
        and state.planned_slices
        and state.work_plan_path
    ):
        if not plans:
            raise mismatch(
                "approved plan has no structured record",
                code=ReplayDiagnosticCode.MIRROR_AHEAD,
            )
        if len(plans) != 1:
            raise mismatch(
                f"expected exactly one immutable approved-plan record, found {len(plans)}"
            )
        if state.approved_plan_commit is None:
            raise mismatch("state-v3 mirror is missing its approved-plan commit binding")
        payload = plans[-1].payload
        assert isinstance(payload, PlanPayload)
        expected_slices = tuple(
            (str(item.slice_id), item.summary, item.scope_paths)
            for item in state.planned_slices
        )
        actual_slices = tuple(
            (item.slice_id, item.summary, item.paths) for item in payload.slices
        )
        if (
            payload.work_plan_path != state.work_plan_path
            or payload.approved_plan_commit != state.approved_plan_commit
            or actual_slices != expected_slices
        ):
            raise mismatch("approved-plan binding differs from state-v3", plans[-1].record_id)

    finding_statuses = _finding_statuses(state)
    latest_findings = {
        item.finding_id: item
        for item in project_latest_recorded_statuses(
            chain, imported=False, bound_only=False
        )
    }
    reduced_findings = reduce_findings(replay)
    imported_statuses = {
        finding_id: status
        for finding_id, status in project_finding_statuses(
            reduced_findings.ledger.findings
        )
        if finding_id not in latest_findings
    }
    pending_review_finding_gap = _recoverable_pending_review_finding_gap(
        state,
        chain,
        finding_statuses,
        latest_findings,
    )
    record_finding_ids = set(latest_findings) | set(imported_statuses)
    if record_finding_ids != set(finding_statuses) and not pending_review_finding_gap:
        differing = next(iter(record_finding_ids ^ set(finding_statuses)), None)
        recorded = latest_findings.get(differing) if differing is not None else None
        raise mismatch(
            "finding transitions differ from state-v3",
            None if recorded is None else recorded.record_id,
            code=_mirror_difference_code(record_finding_ids, set(finding_statuses)),
        )
    for finding_id, recorded in latest_findings.items():
        if (
            finding_id in finding_statuses
            and not recorded.matches(finding_statuses[finding_id])
            and not pending_review_finding_gap
        ):
            raise mismatch(
                "finding status differs from state-v3", recorded.record_id
            )
    for finding_id, status in imported_statuses.items():
        if finding_statuses.get(finding_id) != status and not pending_review_finding_gap:
            raise mismatch(
                "imported finding status differs from state-v3",
                import_records[0].record_id if import_records else None,
            )

    attestation_facts = _attestation_facts(state)
    attestation_records = {
        (record.logical_id, record.fingerprint.sha256): record
        for record in chain
        if record.record_type is RecordType.VALIDATION_ATTESTATION
    }
    if set(attestation_records) != attestation_facts:
        differing = next(iter(set(attestation_records) ^ attestation_facts), None)
        record = attestation_records.get(differing) if differing is not None else None
        raise mismatch(
            "validation attestations differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(attestation_records), attestation_facts),
        )
    attestation_mirrors = _attestation_mirrors(state)
    records_by_id = {record.record_id: record for record in chain}
    for key, attestation_record in attestation_records.items():
        mirror = attestation_mirrors.get(key)
        payload = attestation_record.payload
        assert isinstance(payload, ValidationAttestationPayload)
        content_record = records_by_id.get(payload.content_record_id)
        if (
            mirror is None
            or not isinstance(
                content_record.payload if content_record else None,
                ValidationContentPayload,
            )
            or not {"output_digest", "summary", "records"}.issubset(mirror)
        ):
            raise mismatch(
                "validation content has no complete state-v3 counterpart",
                attestation_record.record_id,
            )
        content = content_record.payload
        assert isinstance(content, ValidationContentPayload)
        expected = _mirror_attestation_statement(mirror)
        actual = {
            "output_digest": payload.output_digest,
            "summary": content.summary,
            "results": tuple(
                (
                    result.command.family,
                    result.command.argv,
                    result.command.mode,
                    result.outcome,
                    result.exit_code,
                    result.output_sha256,
                )
                for result in payload.results
            ),
        }
        if actual != expected:
            raise mismatch(
                "validation output or digest differs from state-v3",
                attestation_record.record_id,
            )

    projected_reviews = project_review_contracts(replay, store.read_blob)
    record_reviews = {}
    for projected in projected_reviews:
        key = (projected.work_unit_id, projected.round_number)
        if key in record_reviews:
            raise mismatch(
                "review contract projection is ambiguous", projected.record_id
            )
        record_reviews[key] = projected
    mirror_latest: dict[str, object] = {}
    for history in _runtime_history_mirrors(state):
        unit_id = history.get("work_unit_id")
        if not isinstance(unit_id, int):
            continue
        unit_key = str(unit_id)
        try:
            latest_key = next(
                key
                for key in history
                if key.startswith("latest_") and key.endswith("_review")
            )
        except StopIteration:
            raise mismatch("latest review mirror has no aggregate field") from None
        mirror_latest[unit_key] = history.get(latest_key)
    projected_by_unit: dict[str, list[tuple[int, object, dict[str, object]]]] = {}
    for (unit_id, round_number), projected in record_reviews.items():
        statement = asdict(projected.result)
        validation_statement = statement.get("validation")
        if isinstance(validation_statement, dict):
            validation_statement.pop("content_captures", None)
            validation_statement.pop("content_digest_format", None)
            for spec in validation_statement.get("command_specs", ()):
                if isinstance(spec, dict):
                    spec["mode"] = "argv" if spec.get("argv") else "legacy_shell"
        projected_by_unit.setdefault(unit_id, []).append(
            (round_number, projected, statement)
        )
    for unit_id, latest in mirror_latest.items():
        reviews = projected_by_unit.get(unit_id, [])
        selected = max(reviews, key=lambda item: item[0]) if reviews else None
        expected_latest = None if selected is None else selected[2]
        if (
            latest is not None
            and canonical_json(latest) != canonical_json(expected_latest)
        ):
            raise mismatch(
                "latest review differs from its record projection",
                None if selected is None else selected[1].record_id,
            )

    history_mirrors = _runtime_history_mirrors(state)
    final_report_mirrors: dict[str, bytes] = {}
    for history in history_mirrors:
        unit_id = history.get("work_unit_id")
        candidates = tuple(
            value
            for key, value in history.items()
            if key.endswith("_final_report") and isinstance(value, str)
        )
        if len(candidates) > 1:
            raise mismatch("final-report content differs from state-v3")
        if isinstance(unit_id, int) and candidates:
            final_report_mirrors[str(unit_id)] = candidates[0].encode("utf-8")
    final_report_groups: dict[str, list[ArtifactRecord]] = {}
    for record in chain:
        if (
            isinstance(record.payload, ProviderContentPayload)
            and record.payload.content_kind == "final_report"
        ):
            final_report_groups.setdefault(record.payload.work_unit_id, []).append(
                record
            )
    ambiguous_reports = next(
        (records for records in final_report_groups.values() if len(records) != 1),
        None,
    )
    if ambiguous_reports is not None:
        raise mismatch(
            "final-report content differs from state-v3",
            ambiguous_reports[-1].record_id,
            code=ReplayDiagnosticCode.MIRROR_AMBIGUOUS,
        )
    final_report_records = {
        unit_id: records[0] for unit_id, records in final_report_groups.items()
    }
    if set(final_report_records) != set(final_report_mirrors):
        differing = next(
            iter(set(final_report_records) ^ set(final_report_mirrors)), None
        )
        record = final_report_records.get(differing) if differing else None
        raise mismatch(
            "final-report content differs from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(
                set(final_report_records), set(final_report_mirrors)
            ),
        )
    for unit_id, record in final_report_records.items():
        payload = record.payload
        assert isinstance(payload, ProviderContentPayload)
        if store.read_blob(payload.blob) != final_report_mirrors[unit_id]:
            raise mismatch(
                "final-report bytes differ from state-v3", record.record_id
            )

    packet_mirrors = {
        str(history["work_unit_id"]): packet
        for history in history_mirrors
        for packet in (history.get("active_review_packet"),)
        if isinstance(history.get("work_unit_id"), int)
        and isinstance(packet, dict)
        and isinstance(packet.get("fingerprint"), str)
    }
    packet_records: dict[str, ArtifactRecord] = {}
    for record in chain:
        if isinstance(record.payload, ReviewPacketPayload):
            packet_records[record.payload.work_unit_id] = record
    if set(packet_records) != set(packet_mirrors):
        differing = next(iter(set(packet_records) ^ set(packet_mirrors)), None)
        record = packet_records.get(differing) if differing else None
        raise mismatch(
            "active review packets differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(packet_records), set(packet_mirrors)),
        )
    for key, record in packet_records.items():
        packet = packet_mirrors[key]
        payload = record.payload
        assert isinstance(payload, ReviewPacketPayload)
        canonical = str(packet.get("canonical_text", "")).encode("utf-8")
        if (
            store.read_blob(payload.blob) != canonical
            or payload.fingerprint != packet.get("fingerprint")
            or payload.blob.sha256 != packet.get("digest")
            or payload.purpose != packet.get("purpose")
            or payload.manifest != tuple(packet.get("paths", ()))
        ):
            raise mismatch(
                "active review packet bytes or metadata differ from state-v3",
                record.record_id,
            )

    quota_facts = {
        (failure.role, failure.diff_fingerprint, failure.resume_at_utc)
        for unit in state.work_units
        for failure in unit.invocation_failures
        if (
            failure.failure_kind is AgentFailureKind.QUOTA
            and failure.diff_fingerprint is not None
            and failure.resume_at_utc is not None
            and failure.invocation_id != pending_transition_invocation
        )
    }
    quota_records = {
        (
            record.payload.role.value,
            record.payload.repository_fingerprint,
            record.payload.retry_at,
        ): record
        for record in chain
        if isinstance(record.payload, QuotaPausePayload)
    }
    if set(quota_records) != quota_facts:
        differing = next(iter(set(quota_records) ^ quota_facts), None)
        record = quota_records.get(differing) if differing is not None else None
        raise mismatch(
            "quota pauses differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(quota_records), quota_facts),
        )

    transient_facts = {
        (
            failure.role,
            failure.diff_fingerprint,
            failure.resume_at_utc,
            failure.auto_resume_count,
        )
        for unit in state.work_units
        for failure in unit.invocation_failures
        if (
            failure.failure_kind is AgentFailureKind.NETWORK
            and failure.automatic_resume
            and failure.diff_fingerprint is not None
            and failure.resume_at_utc is not None
            and failure.invocation_id != pending_transition_invocation
        )
    }
    transient_records = {
        (
            record.payload.role.value,
            record.payload.repository_fingerprint,
            record.payload.retry_at,
            record.payload.attempt,
        ): record
        for record in chain
        if isinstance(record.payload, TransientRetryPayload)
    }
    if set(transient_records) != transient_facts:
        differing = next(iter(set(transient_records) ^ transient_facts), None)
        record = transient_records.get(differing) if differing is not None else None
        raise mismatch(
            "transient retries differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(transient_records), transient_facts),
        )

    bootstrap_records = {
        (
            record.record_type.value,
            record.payload.transition_fingerprint,
        ): (
            hashlib.sha256(canonical_json(asdict(record.payload))).hexdigest(),
            record,
        )
        for record in chain
        if isinstance(record.payload, (ProviderInputMeasurementPayload, FinalReviewPreflightPayload))
    }
    bootstrap_facts = {
        (fact.check_kind, fact.transition_fingerprint): fact.semantic_digest
        for fact in state.bootstrap_checks
    }
    if set(bootstrap_records) != set(bootstrap_facts):
        differing = next(iter(set(bootstrap_records) ^ set(bootstrap_facts)), None)
        record = bootstrap_records.get(differing, (None, None))[1] if differing is not None else None
        raise mismatch(
            "bootstrap checks differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(bootstrap_records), set(bootstrap_facts)),
        )
    for key, (digest, record) in bootstrap_records.items():
        if bootstrap_facts[key] != digest:
            raise mismatch("bootstrap check payload differs from state-v3", record.record_id)

    for record in chain:
        if isinstance(record.payload, ResumeCheckPayload):
            predecessor = record.predecessor_ids[0] if record.predecessor_ids else None
            if record.payload.expected_head_id != predecessor:
                raise mismatch("resume check is not bound to its prior record head", record.record_id)

    records_by_id = {record.record_id: record for record in chain}
    for record in chain:
        payload = record.payload
        if not isinstance(payload, BindingPayload):
            continue
        attestation = records_by_id.get(payload.attestation_id)
        if (
            attestation is None
            or not isinstance(attestation.payload, ValidationAttestationPayload)
            or attestation.fingerprint != record.fingerprint
        ):
            raise mismatch(
                "binding references an unknown, invalid, or fingerprint-mismatched "
                "validation attestation",
                record.record_id,
            )
        for approval_id in payload.approval_ids:
            approval = records_by_id.get(approval_id)
            if (
                approval is None
                or not isinstance(approval.payload, ReviewPayload)
                or approval.payload.verdict != "approved"
                or approval.fingerprint != record.fingerprint
            ):
                raise mismatch(
                    "binding references an unknown, unapproved, or "
                    "fingerprint-mismatched review",
                    record.record_id,
                )

    commit_targets = {item.commit_ref for item in state.slices if item.commit_ref is not None}
    bound_commit_targets: set[str] = set()
    for record in chain:
        if isinstance(record.payload, BindingPayload) and record.payload.binding_kind == "commit":
            if record.payload.target not in commit_targets:
                raise mismatch("commit binding has no state-v3 counterpart", record.record_id)
            bound_commit_targets.add(record.payload.target)
    missing_bindings = commit_targets - bound_commit_targets
    if missing_bindings:
        raise mismatch(
            "completed slice is missing a structured commit binding",
            code=ReplayDiagnosticCode.MIRROR_AHEAD,
        )

    completed = (
        state.current_step is WorkflowStep.COMPLETED
        and all(item.commit_ref is not None for item in state.slices)
        and (
            state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
            or (
                state.execution_mode == "PLAN_ONLY"
                and state.current_work_unit.kind is WorkUnitKind.PLAN
            )
        )
    )
    completions = [item for item in chain if isinstance(item.payload, WorkflowCompletionPayload)]
    if completed and not completions:
        raise mismatch(
            "state-v3 mirror reports workflow completion without a structured record",
            code=ReplayDiagnosticCode.MIRROR_AHEAD,
        )
    if completions:
        expected_outcome = "completed" if completed else None
        if completions[-1].payload.outcome != expected_outcome:
            raise mismatch("workflow completion differs from state-v3", completions[-1].record_id)
        completion = completions[-1]
        final_binding_id = completion.payload.final_binding_id
        if final_binding_id is not None:
            final_binding = records_by_id.get(final_binding_id)
            if (
                final_binding is None
                or not isinstance(final_binding.payload, BindingPayload)
                or final_binding.fingerprint != completion.fingerprint
            ):
                raise mismatch(
                    "workflow completion references an unknown, invalid, or "
                    "fingerprint-mismatched final binding",
                    completion.record_id,
                )

    return ResumeResolution(state, mode, head, replay)


def _mirror_difference_code(
    record_facts: set[object], mirror_facts: set[object]
) -> ReplayDiagnosticCode:
    """Classify a strict mirror superset separately from ambiguous divergence."""
    if mirror_facts - record_facts and not record_facts - mirror_facts:
        return ReplayDiagnosticCode.MIRROR_AHEAD
    return ReplayDiagnosticCode.MIRROR_AMBIGUOUS


def _finding_statuses(state: WorkflowState) -> dict[str, str]:
    raw = state.runtime_history
    candidates: list[object] = []
    if isinstance(raw, dict) and set(raw) == {"current", "archive"}:
        archive = raw.get("archive")
        if isinstance(archive, list):
            candidates.extend(archive)
        candidates.append(raw.get("current"))
    elif raw is not None:
        candidates.append(raw)
    attributed_open_ids = tuple(
        finding_id
        for unit in state.work_units
        for finding_id in unit.open_findings
    )
    return dict(
        project_legacy_mirror_statuses(
            tuple(candidates), attributed_open_ids=attributed_open_ids
        )
    )


def _recoverable_pending_review_finding_gap(
    state: WorkflowState,
    chain: tuple[ArtifactRecord, ...],
    mirror_statuses: dict[str, str],
    latest_findings: dict[str, FindingRecordedStatusProjection] | None = None,
) -> bool:
    """Admit one durable review for exact local replay after a failed checkpoint."""
    if latest_findings is None:
        latest_findings = {
            item.finding_id: item
            for item in project_latest_recorded_statuses(
                chain, imported=False, bound_only=False
            )
        }
    unit = state.current_work_unit
    reviewer_by_step = {
        WorkflowStep.CLAUDE_SLICE_REVIEW: "claude",
    }
    reviewer = reviewer_by_step.get(state.current_step)
    if unit.kind not in {WorkUnitKind.SLICE, WorkUnitKind.CORRECTION} or reviewer is None:
        return False
    logical_id_prefix = f"review-{reviewer}-{unit.work_unit_id}-"
    reviews: list[tuple[int, ArtifactRecord]] = []
    for record in chain:
        if (
            not isinstance(record.payload, ReviewPayload)
            or not record.logical_id.startswith(logical_id_prefix)
            or record.payload.work_unit_id != str(unit.work_unit_id)
            or record.payload.reviewer.value != reviewer
            or record.payload.verdict not in {"approved", "denied"}
        ):
            continue
        round_text = record.logical_id.removeprefix(logical_id_prefix)
        if not round_text.isdigit():
            continue
        review_round = int(round_text)
        if (
            review_round > unit.round_number
            or _state_has_review_projection(
                state,
                work_unit_id=unit.work_unit_id,
                reviewer=reviewer,
                round_number=review_round,
                fingerprint=record.fingerprint.sha256,
            )
        ):
            continue
        reviews.append((review_round, record))
    if len(reviews) != 1:
        return False
    _review_round, review = reviews[0]
    review_ids = set(review.payload.finding_ids)
    if not set(unit.open_findings).issubset(review_ids):
        return False
    if not any(
        isinstance(record.payload, ValidationAttestationPayload)
        and record.fingerprint == review.fingerprint
        for record in chain
    ):
        return False
    changed_ids = {
        finding_id
        for finding_id in set(mirror_statuses) | set(latest_findings)
        if finding_id not in mirror_statuses
        or finding_id not in latest_findings
        or not latest_findings[finding_id].matches(mirror_statuses[finding_id])
    }
    if not changed_ids or not changed_ids.issubset(review_ids):
        return False
    if review.payload.verdict == "approved" and any(
        not latest_findings[finding_id].is_closed
        for finding_id in changed_ids
        if finding_id in latest_findings
    ):
        return False
    record_positions = {record.record_id: index for index, record in enumerate(chain)}
    review_position = record_positions[review.record_id]
    for finding_id in changed_ids:
        transition = latest_findings.get(finding_id)
        if (
            transition is None
            or transition.actor != reviewer
            or record_positions[transition.record_id] <= review_position
        ):
            return False
    return True


def _recoverable_pending_correction_record(
    state: WorkflowState,
    chain: tuple[ArtifactRecord, ...],
    record: ArtifactRecord,
) -> bool:
    """Recognize the next correction round durably written before its mirror."""
    payload = record.payload
    unit = state.current_work_unit
    if (
        unit.kind is not WorkUnitKind.CORRECTION
        or not isinstance(payload, CorrectionWorkUnitPayload)
        or record.logical_id != f"work-unit-{unit.work_unit_id}"
        or payload.slice_id != str(unit.slice_id)
        or payload.round_number != unit.round_number + 1
        or not payload.finding_ids
    ):
        return False
    review = _pending_denied_review(state, chain, record)
    if review is None:
        return False
    prefix = "C-"
    return (
        any(
            isinstance(candidate.payload, ValidationAttestationPayload)
            and candidate.fingerprint == review.fingerprint
            for candidate in chain
        )
        and set(payload.finding_ids).issubset(review.payload.finding_ids)
        and all(item.startswith(prefix) for item in payload.finding_ids)
    )


def _recoverable_pending_slice_denial_record(
    state: WorkflowState,
    chain: tuple[ArtifactRecord, ...],
    record: ArtifactRecord,
) -> bool:
    """Recognize a denied Slice review whose next round is record-ahead."""
    payload = record.payload
    unit = state.current_work_unit
    if (
        unit.kind is not WorkUnitKind.SLICE
        or not isinstance(payload, WorkUnitPayload)
        or record.logical_id != f"work-unit-{unit.work_unit_id}"
        or payload.slice_id != str(unit.slice_id)
        or payload.round_number != unit.round_number + 1
        or not payload.open_finding_ids
        or not all(item.startswith("C-") for item in payload.open_finding_ids)
    ):
        return False
    review = _pending_denied_review(state, chain, record)
    if review is None:
        return False
    positions = {candidate.record_id: index for index, candidate in enumerate(chain)}
    review_position = positions[review.record_id]
    return (
        any(
            isinstance(candidate.payload, ValidationAttestationPayload)
            and candidate.fingerprint == review.fingerprint
            and positions[candidate.record_id] < review_position
            for candidate in chain
        )
        and set(payload.open_finding_ids).issubset(review.payload.finding_ids)
    )


def _recoverable_pending_work_record(
    state: WorkflowState,
    chain: tuple[ArtifactRecord, ...],
    record: ArtifactRecord,
) -> bool:
    """Recognize the two bounded record-ahead work-unit transitions."""
    return _recoverable_pending_correction_record(
        state, chain, record
    ) or _recoverable_pending_slice_denial_record(state, chain, record)


def _pending_denied_review(
    state: WorkflowState,
    chain: tuple[ArtifactRecord, ...],
    record: ArtifactRecord,
) -> ArtifactRecord | None:
    """Return the unique prior denial bound to the mirror's current round."""
    unit = state.current_work_unit
    reviewer_by_step = {
        WorkflowStep.CLAUDE_SLICE_REVIEW: "claude",
    }
    reviewer = reviewer_by_step.get(state.current_step)
    if reviewer is None:
        return None
    logical_id = f"review-{reviewer}-{unit.work_unit_id}-{unit.round_number}"
    reviews = tuple(
        candidate
        for candidate in chain
        if isinstance(candidate.payload, ReviewPayload)
        and candidate.logical_id == logical_id
        and candidate.payload.work_unit_id == str(unit.work_unit_id)
        and candidate.payload.reviewer.value == reviewer
        and candidate.payload.verdict == "denied"
        and chain.index(candidate) < chain.index(record)
    )
    return reviews[0] if len(reviews) == 1 else None


def _state_has_review_projection(
    state: WorkflowState,
    *,
    work_unit_id: int,
    reviewer: str,
    round_number: int,
    fingerprint: str,
) -> bool:
    """Recognize a review already represented by the remaining R7 aggregates."""
    raw = state.runtime_history
    if not isinstance(raw, dict):
        return False
    candidates: list[object] = []
    if set(raw) == {"current", "archive"}:
        archive = raw.get("archive")
        if isinstance(archive, list):
            candidates.extend(archive)
        candidates.append(raw.get("current"))
    else:
        candidates.append(raw)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            candidate_work_unit = int(candidate.get("work_unit_id", -1))
        except (TypeError, ValueError):
            continue
        if candidate_work_unit != work_unit_id:
            continue
        latest = candidate.get("latest_claude_review")  # allowlist:provider -- canonical state-v3 field
        if round_number < state.current_work_unit.round_number:
            return True
        if (
            isinstance(latest, dict)
            and latest.get("reviewer") == reviewer
            and candidate.get("last_claude_fingerprint") == fingerprint  # allowlist:provider -- canonical state-v3 field
        ):
            return True
    return False


def _attestation_facts(state: WorkflowState) -> set[tuple[str, str]]:
    facts: set[tuple[str, str]] = set()
    raw = state.runtime_history
    candidates: list[object] = []
    if isinstance(raw, dict) and set(raw) == {"current", "archive"}:
        archive = raw.get("archive")
        if isinstance(archive, list):
            candidates.extend(archive)
        candidates.append(raw.get("current"))
    elif raw is not None:
        candidates.append(raw)
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("attestations"), list):
            continue
        for attestation in candidate["attestations"]:
            if not isinstance(attestation, dict):
                continue
            attestation_id = attestation.get("attestation_id")
            fingerprint = attestation.get("diff_fingerprint")
            if isinstance(attestation_id, str) and isinstance(fingerprint, str):
                facts.add((attestation_id, fingerprint))
    return facts


def _runtime_history_mirrors(state: WorkflowState) -> tuple[dict[str, object], ...]:
    raw = state.runtime_history
    candidates: list[object] = []
    if isinstance(raw, dict) and set(raw) == {"current", "archive"}:
        archive = raw.get("archive")
        if isinstance(archive, list):
            candidates.extend(archive)
        candidates.append(raw.get("current"))
    elif raw is not None:
        candidates.append(raw)
    return tuple(item for item in candidates if isinstance(item, dict))


def _attestation_mirrors(
    state: WorkflowState,
) -> dict[tuple[str, str], dict[str, object]]:
    result: dict[tuple[str, str], dict[str, object]] = {}
    for history in _runtime_history_mirrors(state):
        attestations = history.get("attestations")
        if not isinstance(attestations, list):
            continue
        for item in attestations:
            if not isinstance(item, dict):
                continue
            attestation_id = item.get("attestation_id")
            fingerprint = item.get("diff_fingerprint")
            if isinstance(attestation_id, str) and isinstance(fingerprint, str):
                result[(attestation_id, fingerprint)] = item
    return result


def _mirror_attestation_statement(raw: dict[str, object]) -> dict[str, object]:
    specs_raw = raw.get("command_specs")
    expected_raw = raw.get("expected_commands")
    records_raw = raw.get("records")
    specs = specs_raw if isinstance(specs_raw, list) else []
    expected = expected_raw if isinstance(expected_raw, list) else []
    records = records_raw if isinstance(records_raw, list) else []
    if not specs:
        specs = [
            {"argv": [], "legacy_shell": command}
            for command in expected
            if isinstance(command, str)
        ]
    records_by_command = {
        item.get("command"): item
        for item in records
        if isinstance(item, dict) and isinstance(item.get("command"), str)
    }
    results: list[tuple[object, ...]] = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        argv_raw = spec.get("argv")
        argv = tuple(argv_raw) if isinstance(argv_raw, list) else ()
        legacy = spec.get("legacy_shell")
        if argv:
            family = "validation"
            mode = "argv"
            command = shlex.join(argv)
        else:
            family = "legacy-validation"
            mode = "legacy_shell"
            command = legacy
            argv = (legacy,)
        mirrored = records_by_command.get(command)
        status = mirrored.get("status") if isinstance(mirrored, dict) else None
        output = (
            str(mirrored.get("output", ""))
            if isinstance(mirrored, dict)
            else ""
        )
        results.append(
            (
                family,
                argv,
                mode,
                str(status).lower() if status in {"PASS", "FAIL"} else "unavailable",
                int(mirrored.get("exit_code", -1))
                if isinstance(mirrored, dict)
                else -1,
                hashlib.sha256(output.encode("utf-8")).hexdigest(),
            )
        )
    return {
        "output_digest": raw.get("output_digest"),
        "summary": raw.get("summary"),
        "results": tuple(results),
    }
