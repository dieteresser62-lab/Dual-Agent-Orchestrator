"""Fail-closed resume resolution for structured-v2 runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path

from artifact_models import (
    BindingPayload,
    ArtifactRecord,
    CorrectionWorkUnitPayload,
    FindingTransitionPayload,
    GatePayload,
    PlanPayload,
    QuotaPausePayload,
    RecordType,
    ReviewPayload,
    ResumeCheckPayload,
    RunIdentityPayload,
    RunProfilePayload,
    SliceBoundaryPayload,
    TaskPayload,
    TransientRetryPayload,
    ValidationAttestationPayload,
    WorkUnitPayload,
    WorkflowCompletionPayload,
    WorkflowPolicyPayload,
    ProviderInputMeasurementPayload,
    FinalReviewPreflightPayload,
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    canonical_json,
)
from artifact_store import ArtifactStore, ArtifactStoreError
from artifact_replay import (
    ArtifactReplayError,
    ArtifactReplayResult,
    ReplayedWorkflowCursor,
    ReplayedWorkUnitState,
    ReplayDiagnosticCode,
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
    NATIVE_CLAUDE_REVIEW_TRANSPORT,
    NATIVE_CODEX_RESULT_TRANSPORT,
    ProtocolMode,
    ProtocolBinding,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
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
    if replay.run_identity is not None:
        expected_identity = RunIdentityPayload(
            task_file=state.task_file,
            branch=state.branch,
            branch_base=state.branch_base,
            execution_mode=state.execution_mode,
            audit_report_path=state.audit_report_path,
        )
        if replay.run_identity != expected_identity:
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
    if replay.run_profile is not None:
        expected_profile = RunProfilePayload(
            codex_model=binding.codex_profile.model,
            codex_effort=binding.codex_profile.effort,
            claude_model=binding.claude_profile.model,
            claude_effort=binding.claude_profile.effort,
        )
        if replay.run_profile != expected_profile:
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
        chain = ArtifactStore(repository_root, state.run_id).load_chain()
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
        replay = replay_artifacts(chain, state.run_id)
    except ArtifactReplayError as exc:
        raise ArtifactResumeError(
            exc.diagnostic.message,
            code=exc.code,
            record_id=exc.record_id,
        ) from exc
    chain = replay.records
    assert_run_binding_mirror(replay, state, binding)
    assert_workflow_status_mirror(replay, state)
    assert_slice_boundary_mirror(replay, state)

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

    decisions = {
        (
            decision.reason.value.replace("_", "-"),
            "approved" if decision.approved else "rejected",
            decision.rationale,
            decision.fingerprint,
        )
        for unit in state.work_units
        for decision in unit.gate_decisions
    }
    decision_records = {
        (
            record.payload.gate_kind,
            record.payload.decision,
            record.payload.rationale,
            record.fingerprint.sha256,
        ): record
        for record in chain
        if isinstance(record.payload, GatePayload)
    }
    if set(decision_records) != decisions:
        differing = next(iter(set(decision_records) ^ decisions), None)
        record = decision_records.get(differing) if differing is not None else None
        raise mismatch(
            "gate decisions differ from state-v3",
            None if record is None else record.record_id,
            code=_mirror_difference_code(set(decision_records), decisions),
        )

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

    quota_facts = {
        (failure.role, failure.diff_fingerprint, failure.resume_at_utc)
        for unit in state.work_units
        for failure in unit.invocation_failures
        if (
            failure.failure_kind is AgentFailureKind.QUOTA
            and failure.diff_fingerprint is not None
            and failure.resume_at_utc is not None
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
            or _state_has_review_event(
                state,
                work_unit_id=unit.work_unit_id,
                reviewer=reviewer,
                round_number=review_round,
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


def _state_has_review_event(
    state: WorkflowState,
    *,
    work_unit_id: int,
    reviewer: str,
    round_number: int,
) -> bool:
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
        events = candidate.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict) or event.get("kind") != "review":
                continue
            result = event.get("result")
            if (
                isinstance(result, dict)
                and result.get("reviewer") == reviewer
                and event.get("round_number") == round_number
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
