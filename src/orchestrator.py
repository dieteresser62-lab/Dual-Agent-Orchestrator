#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import logging
import re
import shlex
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import get_args, get_type_hints

from agent_adapters import (
    AgentAdapter,
    NativeClaudeReviewAdapter,
    NativeCodexAdapter,
    build_agent_registry,
)
from agent_runtime import (
    AgentInvocationError,
    NativeAgentCodexOutput,
    NativeAgentReviewOutput,
    OrchestratorConfig,
    ProviderAttemptLifecycle,
    run_native_codex_agent_checked,
    run_native_review_agent_checked,
    run_validation_matrix,
)
from native_codex_contract import (
    canonical_native_codex_json,
    parse_bound_native_codex_contract_result,
)
from native_codex_request import validate_native_codex_provider_response
from artifact_bridge import (
    ArtifactBridge, ArtifactBridgeError, agent_result_payload, attestation_payload, finding_payload,
    plan_payload, review_payload, review_payload_matches_result,
    validation_request_payload,
    provider_input_measurement_payload,
    finding_handoff_export_payload, finding_handoff_import_payload,
)
from artifact_migration import (
    ArtifactResumeError,
    assert_run_binding_mirror,
    assert_workflow_status_mirror,
    require_workflow_status_prefix,
    resolve_resume_state,
)
from artifact_models import (
    ArtifactRecord, BindingPayload, CorrectionWorkUnitPayload, DiagnosticPayload,
    FingerprintKind, GatePayload,
    AgentResultPayload, QuotaPausePayload, ReviewPayload, Role, TaskPayload, TransientRetryPayload,
    ValidationAttestationPayload,
    WorkUnitPayload,
    WorkflowCompletionPayload,
    ProviderInputMeasurementPayload, canonical_json,
    ProviderAttemptPayload, ProviderUsagePayload,
    FindingHandoffExportPayload, FindingHandoffImportPayload,
    FindingTransitionPayload,
    RunIdentityPayload, RunProfilePayload,
    WorkflowPolicyPayload, WorkflowTransitionPayload,
    RecordType, stable_record_id,
)
from artifact_store import ArtifactStore
from artifact_replay import (
    ArtifactReplayError,
    ArtifactReplayResult,
    ReplayedWorkflowCursor,
    ReplayedWorkUnitState,
    replay_artifacts,
)
from finding_reducer import (
    project_finding_response_delta,
    project_reviewer_persistence_transitions,
    reduce_findings,
)
from final_review_preflight import (
    FINAL_REVIEW_OPERATIONS, FinalReviewPreflightDenied, preflight_payload,
    relevant_record_head, run_final_review_preflight, transition_fingerprint,
)
from provider_input_budget import ProviderInputMeasurement
from review_packets import ReviewPacket
from audit_trail import (
    AuditProjection,
    AuthorizedTestChanges,
    OverallAuditEntry,
    ReviewAuditEvent,
    ValidationAuditEvent,
    project_managed_slice_audit,
    project_overall_audit,
    project_structured_slice_audit,
    project_structured_work_plan_audit,
    project_work_plan_audit,
    prepare_managed_overall_document,
    prepare_managed_slice_document,
    prepare_managed_work_plan_document,
    managed_slice_document_path,
    validate_managed_work_plan_document,
)
from cli import DEFAULT_AGENTS_FILE, DEFAULT_TASK_FILE
from contracts import (
    AgentRole,
    ApprovalMarker,
    CodexContractResult,
    CodexStepContract,
    ContractResult,
    FindingRecord,
    PlannedSlice,
    StepContract,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from gates import TestChangeEvidence, detect_test_changes, matches_path_patterns
from git_service import (
    CommitAuthorization,
    SliceGitBoundary,
    commit_managed_audit_report,
    commit_slice,
    inspect_repository,
    prepare_new_watch_task_branch,
    require_committed_file_at_head,
    GitTransactionError,
)
from plan_handoff import (
    PlanHandoffError,
    extract_implementation_slices,
    implementation_task_path,
    render_implementation_task,
    write_implementation_handoff,
)
from repo_changes import (
    RepositoryChangeError,
    RepositoryChanges,
    collect_repository_changes,
    resolve_merge_base,
)
from state_io import (
    ActiveV2StateError,
    CompletedV2State,
    StateSchemaError,
    load_resumable_workflow_state,
    load_workflow_state,
    new_run_id,
    save_workflow_state,
    write_file,
    write_workflow_checkpoint,
)
from task_contract import TaskContract, TaskMode, parse_task_contract
from native_review_contract import (
    BoundNativeReviewContext,
    NativeReviewContext,
    NativeReviewContractError,
    parse_bound_native_contract_result,
)
from native_review_request import (
    validate_native_review_provider_response,
    validate_native_review_provider_response_for_context,
)
from workflow import (
    CodexInvocation,
    PersistedNativeReviewerReplay,
    ReviewerInvocation,
    NoWorkflowChangesError,
    WorkflowChanges,
    WorkflowCommitApprovalRequired,
    WorkflowCommitRequest,
    WorkflowContext,
    WorkflowCorrectionBoundary,
    WorkflowDriver,
    WorkflowEngine,
    WorkflowExecutionError,
    WorkflowHistory,
    WorkflowRunResult,
)
from workflow_state import (
    AgentProfileBinding,
    AgentFailureKind,
    GateReason,
    GateDecisionRecord,
    SliceStatus,
    ProtocolBinding,
    ProtocolMode,
    WorkflowState,
    WorkflowStateValidationError,
    WorkflowStep,
    WorkUnitRecord,
    WorkUnitKind,
    WorkUnitStatus,
    NATIVE_CLAUDE_REVIEW_TRANSPORT,
    NATIVE_CODEX_RESULT_TRANSPORT,
    init_workflow_state,
    BootstrapCheckFact,
    managed_correction_slice_report_path,
    project_implementer_return_policy,
)
from validation_matrix import ValidationCommand, ValidationMatrix
from error_classification import (
    FailureClass,
    classify_exception,
    enforce_record_start_boundary,
)
from inbox_watcher import (
    QueueFinalizationDisposition,
    WatchTaskDisposition,
    WatchTaskResult,
    attempt_sidecar_path,
    finalize_queue_success,
    load_queue_success_evidence,
    load_watch_identity,
    move_to_outbox,
    success_marker_path,
    watch_run_has_records,
    watch_identity_path,
    watch_inbox,
)


logger = logging.getLogger(__name__)


def run_v3_work_unit(
    engine: WorkflowEngine,
    state: WorkflowState,
    context: WorkflowContext,
    history: WorkflowHistory | None = None,
) -> WorkflowRunResult:
    return engine.run_current_work_unit(state, context, history)


def run_v3_final_review(
    engine: WorkflowEngine,
    state: WorkflowState,
    context: WorkflowContext,
    history: WorkflowHistory | None = None,
) -> WorkflowRunResult:
    return engine.run_final_review(state, context, history)


def find_task_file(explicit_path: str | None) -> Path:
    path = Path(explicit_path or DEFAULT_TASK_FILE)
    if path.is_file():
        return path
    raise FileNotFoundError(f"Task file not found: {path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from cli import parse_args as parse_cli_args

    return parse_cli_args(argv)

def _shorten(value: str | None, maximum: int) -> str:
    text = value or ""
    return text if len(text) <= maximum else text[: max(0, maximum - 3)] + "..."


class ProductionWorkflowDriver(WorkflowDriver):
    """Bind the deterministic v3 engine to real agents, Git, validation, and state."""

    def __init__(
        self,
        *,
        repository_root: Path,
        state_file: Path,
        agents: dict[str, AgentAdapter],
        config: OrchestratorConfig,
        allowed_roots: tuple[Path, ...],
        replace_existing_run_id: str | None = None,
    ) -> None:
        self.root = repository_root.resolve()
        self.state_file = state_file.resolve()
        self.agents = agents
        self.config = config
        self.allowed_roots = allowed_roots
        self.log_dir = self.root / ".orchestrator" / "logs"
        self.checkpoint_dir = self.root / ".orchestrator" / "checkpoints"
        self.active_state: WorkflowState | None = None
        self.last_codex_output = ""
        self._repository_changes: dict[str, RepositoryChanges] = {}
        self._rendered_changes: dict[str, WorkflowChanges] = {}
        self._artifact_bridge: ArtifactBridge | None = None
        self._replace_existing_run_id = replace_existing_run_id

    def bind_work_unit(self, state: WorkflowState) -> None:
        # Runtime history is written by checkpoint(), not by the pure workflow-state
        # transitions.  A transition that starts the next work unit in the same engine
        # invocation can therefore carry an older runtime_history snapshot.  Preserve
        # the driver's last persisted ledger so the subsequent checkpoint can archive
        # the just-completed work unit instead of silently dropping its reviews.
        if (
            self.active_state is not None
            and self.active_state.run_id == state.run_id
            and self.active_state.runtime_history is not None
        ):
            state = replace(
                state,
                runtime_history=self.active_state.runtime_history,
                bootstrap_checks=self.active_state.bootstrap_checks,
            )
        self.active_state = state
        self._bind_artifact_store(state)
        self._persist_structured_baseline(state)
        if state.current_work_unit.kind is WorkUnitKind.PLAN and not self.last_codex_output:
            artifact = (
                self.root / ".orchestrator" / "runs" / state.run_id
                / f"work-unit-{state.current_work_unit_id:04d}-codex.md"
            )
            if artifact.is_file():
                self.last_codex_output = artifact.read_text(encoding="utf-8").strip()

    def _bind_artifact_store(self, state: WorkflowState) -> None:
        """Select the immutable persistence backend without changing workflow state."""
        if (
            state.protocol_binding is not None
            and state.protocol_binding.mode is ProtocolMode.STRUCTURED_V2
        ):
            if (
                self._artifact_bridge is None
                or self._artifact_bridge.store.run_id != state.run_id
            ):
                self._artifact_bridge = ArtifactBridge(
                    ArtifactStore(self.root, state.run_id)
                )
        else:
            self._artifact_bridge = None

    def assert_structured_decision_context(self) -> None:
        """Reload authoritative records before an external workflow side effect."""
        state = self.active_state
        if state is None or state.effective_protocol_mode is ProtocolMode.LEGACY_STATE_V3:
            return
        try:
            resolution = resolve_resume_state(self.root, state)
        except (ArtifactResumeError, ValueError) as exc:
            raise WorkflowExecutionError(
                f"structured decision context is not resumable: {exc}"
            ) from exc
        if resolution.record_head_id is None:
            raise WorkflowExecutionError(
                "structured decision context has no authoritative record head"
            )
        replay = resolution.replay_result
        if replay is None:
            raise WorkflowExecutionError(
                "structured decision context has no authoritative replay result"
            )
        chain = replay.records
        record_reviews = Counter(
            (
                item.payload.work_unit_id,
                item.payload.reviewer.value,
                item.fingerprint.sha256,
                item.payload.verdict,
                item.payload.finding_ids,
            )
            for item in chain
            if isinstance(item.payload, ReviewPayload)
        )
        mirror_reviews: Counter[tuple[object, ...]] = Counter()
        for work_unit_id, history in _persisted_histories(state).items():
            for event in history.events:
                if not isinstance(event, ReviewAuditEvent):
                    continue
                result = event.result
                if result.validation is None:
                    raise WorkflowExecutionError(
                        "structured review mirror is missing its validation binding"
                    )
                mirror_reviews[
                    (
                        str(work_unit_id),
                        result.reviewer.value,
                        result.validation.diff_fingerprint,
                        (
                            "stop"
                            if result.stopped
                            else "approved"
                            if result.approval is True
                            else "denied"
                        ),
                        tuple(item.finding_id for item in result.findings),
                    )
                ] += 1
        # Compatibility for runs checkpointed by the former final-denial
        # transition bug: the authoritative denied ReviewPayload and the
        # following correction work unit were durable, but the redundant
        # ReviewAuditEvent was not archived before the work-unit switch.  Accept
        # only that exact, fully evidenced transition; every other record/mirror
        # difference remains fail-closed.
        recoverable = _recoverable_final_denial_mirror_gap(
            state, record_reviews, mirror_reviews, chain=chain
        )
        if recoverable:
            logger.warning(
                "Accepting an authoritative final-review denial whose legacy "
                "state-v3 mirror is represented by the immediately following "
                "correction work unit."
            )
            mirror_reviews.update(recoverable)
        if record_reviews != mirror_reviews:
            raise WorkflowExecutionError(
                "structured reviewer decisions differ from the state-v3 mirror"
            )

    def _persist_structured_baseline(self, state: WorkflowState) -> None:
        bridge = self._artifact_bridge
        if bridge is None or state.task_digest is None:
            return
        contract_fingerprint = state.task_digest
        binding = state.protocol_binding
        if binding is None:
            raise WorkflowExecutionError(
                "structured baseline requires the immutable protocol binding"
            )
        existing_chain = bridge.store.load_chain()
        if existing_chain:
            existing_replay = replay_artifacts(existing_chain, state.run_id)
            assert_run_binding_mirror(existing_replay, state, binding)
            import_only_prefix = all(
                isinstance(record.payload, FindingHandoffImportPayload)
                for record in existing_replay.records
            )
            if not import_only_prefix:
                require_workflow_status_prefix(existing_replay)
        bridge.append(
            RunIdentityPayload(
                task_file=state.task_file,
                branch=state.branch,
                branch_base=state.branch_base,
                execution_mode=state.execution_mode,
                audit_report_path=state.audit_report_path,
            ),
            logical_id="run-identity",
            idempotency_key="run-identity",
            fingerprint_sha256=contract_fingerprint,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
        bridge.append(
            RunProfilePayload(
                codex_model=binding.codex_profile.model,
                codex_effort=binding.codex_profile.effort,
                claude_model=binding.claude_profile.model,
                claude_effort=binding.claude_profile.effort,
            ),
            logical_id="run-profile",
            idempotency_key="run-profile",
            fingerprint_sha256=contract_fingerprint,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
        self._persist_workflow_snapshot(state)
        if state.task_scope_patterns:
            bridge.append(
                TaskPayload(
                    target_branch=state.target_branch or state.branch,
                    scope_paths=state.task_scope_patterns,
                    assignment_sha256=state.task_digest,
                ),
                logical_id="task-contract",
                idempotency_key="task-contract",
                fingerprint_sha256=contract_fingerprint,
                fingerprint_kind=FingerprintKind.CONTRACT,
            )
        unit = state.current_work_unit
        if unit.kind is not WorkUnitKind.PLAN and state.current_slice.scope_paths:
            chain = bridge.store.load_chain()
            finding_import = next(
                (
                    record for record in chain
                    if isinstance(record.payload, FindingHandoffImportPayload)
                ),
                None,
            )
            first_implementation_unit_id = next(
                item.work_unit_id for item in state.work_units
                if item.kind is not WorkUnitKind.PLAN
            )
            bound_import = (
                finding_import
                if unit.work_unit_id == first_implementation_unit_id else None
            )
            work_unit_payload = (
                CorrectionWorkUnitPayload(
                    slice_id=str(unit.slice_id),
                    round_number=unit.round_number,
                    paths=state.current_slice.scope_paths,
                    finding_ids=unit.open_findings,
                )
                if unit.kind is WorkUnitKind.CORRECTION
                else WorkUnitPayload(
                    slice_id=str(unit.slice_id),
                    round_number=unit.round_number,
                    paths=state.current_slice.scope_paths,
                    open_finding_ids=(
                        tuple(sorted(unit.open_findings))
                        if bound_import is not None else ()
                    ),
                    finding_import_record_id=(
                        bound_import.record_id if bound_import is not None else None
                    ),
                )
            )
            bridge.append(
                work_unit_payload,
                logical_id=f"work-unit-{unit.work_unit_id}",
                idempotency_key=(
                    f"{'correction-' if unit.kind is WorkUnitKind.CORRECTION else ''}"
                    f"work-unit:{unit.work_unit_id}:round:{unit.round_number}"
                ),
                fingerprint_sha256=contract_fingerprint,
                fingerprint_kind=FingerprintKind.CONTRACT,
            )
        self._persist_structured_tail(state)

    def _persist_workflow_snapshot(self, state: WorkflowState) -> None:
        """Append exactly the status and policy deltas needed by one checkpoint."""
        bridge = self._artifact_bridge
        if bridge is None or state.task_digest is None:
            return
        replay = replay_artifacts(bridge.store.load_chain(), state.run_id)
        recorded_slices = dict(replay.slice_statuses)
        recorded_units = {
            item.work_unit_id: item for item in replay.work_unit_states
        }
        expected_units = {
            str(item.work_unit_id): ReplayedWorkUnitState(
                str(item.work_unit_id),
                str(item.slice_id),
                item.status.value,
                item.current_step.value,
            )
            for item in state.work_units
        }
        changed_units = {
            key for key, value in expected_units.items()
            if recorded_units.get(key) != value
        }
        changed_unit_slices = {
            expected_units[key].slice_id for key in changed_units
        }

        for item in state.slices:
            slice_id = str(item.slice_id)
            if (
                recorded_slices.get(slice_id) != item.status.value
                and slice_id not in changed_unit_slices
            ):
                self._append_workflow_transition(
                    WorkflowTransitionPayload(
                        slice_id, item.status.value, None, None, None
                    ),
                    state.task_digest,
                )
                recorded_slices[slice_id] = item.status.value

        current_unit_id = str(state.current_work_unit_id)
        for item in state.work_units:
            work_unit_id = str(item.work_unit_id)
            if work_unit_id not in changed_units or work_unit_id == current_unit_id:
                continue
            slice_status = next(
                candidate.status.value
                for candidate in state.slices
                if candidate.slice_id == item.slice_id
            )
            self._append_workflow_transition(
                WorkflowTransitionPayload(
                    str(item.slice_id),
                    slice_status,
                    work_unit_id,
                    item.current_step.value,
                    item.status.value,
                ),
                state.task_digest,
            )

        current = state.current_work_unit
        expected_cursor = ReplayedWorkflowCursor(
            str(state.current_slice_id), current_unit_id, state.current_step.value
        )
        if (
            current_unit_id in changed_units
            or replay.workflow_cursor != expected_cursor
            or recorded_slices.get(str(state.current_slice_id))
            != state.current_slice.status.value
            or any(key != current_unit_id for key in changed_units)
        ):
            self._append_workflow_transition(
                WorkflowTransitionPayload(
                    str(state.current_slice_id),
                    state.current_slice.status.value,
                    current_unit_id,
                    state.current_step.value,
                    current.status.value,
                ),
                state.task_digest,
            )

        recorded_policies = {
            item.work_unit_id: item for item in replay.workflow_policies
        }
        for item in state.work_units:
            work_unit_id = str(item.work_unit_id)
            policy = WorkflowPolicyPayload(
                work_unit_id,
                *project_implementer_return_policy(item),
            )
            if recorded_policies.get(work_unit_id) == policy:
                continue
            chain = bridge.store.load_chain()
            logical_id = f"workflow-policy-{work_unit_id}"
            revision = 1 + max(
                (
                    record.revision for record in chain
                    if record.record_type is RecordType.WORKFLOW_POLICY
                    and record.logical_id == logical_id
                ),
                default=0,
            )
            bridge.append(
                policy,
                logical_id=logical_id,
                idempotency_key=f"workflow-policy:{work_unit_id}:{revision}",
                fingerprint_sha256=state.task_digest,
                fingerprint_kind=FingerprintKind.CONTRACT,
            )

        assert_workflow_status_mirror(
            replay_artifacts(bridge.store.load_chain(), state.run_id), state
        )

    def _append_workflow_transition(
        self,
        payload: WorkflowTransitionPayload,
        fingerprint: str,
    ) -> None:
        bridge = self._artifact_bridge
        assert bridge is not None
        chain = bridge.store.load_chain()
        revision = 1 + max(
            (
                record.revision for record in chain
                if record.record_type is RecordType.WORKFLOW_TRANSITION
                and record.logical_id == "workflow-transition"
            ),
            default=0,
        )
        bridge.append(
            payload,
            logical_id="workflow-transition",
            idempotency_key=f"workflow-transition:{revision}",
            fingerprint_sha256=fingerprint,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )

    def _persist_structured_tail(self, state: WorkflowState) -> None:
        bridge = self._artifact_bridge
        if bridge is None or state.task_digest is None:
            return
        contract_fingerprint = state.task_digest
        for persisted_unit in state.work_units:
            for failure in persisted_unit.invocation_failures:
                if (
                    failure.diff_fingerprint is None
                    or failure.resume_at_utc is None
                ):
                    continue
                if failure.failure_kind is AgentFailureKind.NETWORK:
                    bridge.append(
                        TransientRetryPayload(
                            role=Role(failure.role),
                            repository_fingerprint=failure.diff_fingerprint,
                            retry_at=failure.resume_at_utc,
                            attempt=failure.auto_resume_count,
                        ),
                        logical_id=f"transient-retry-{failure.invocation_id}",
                        idempotency_key=f"transient-retry:{failure.invocation_id}",
                        fingerprint_sha256=failure.diff_fingerprint,
                        fingerprint_kind=FingerprintKind.IMPLEMENTATION,
                    )
                    continue
                if failure.failure_kind is not AgentFailureKind.QUOTA:
                    continue
                bridge.append(
                    QuotaPausePayload(
                        role=Role(failure.role),
                        repository_fingerprint=failure.diff_fingerprint,
                        retry_at=failure.resume_at_utc,
                    ),
                    logical_id=f"quota-pause-{failure.invocation_id}",
                    idempotency_key=f"quota-pause:{failure.invocation_id}",
                    fingerprint_sha256=failure.diff_fingerprint,
                    fingerprint_kind=FingerprintKind.IMPLEMENTATION,
                )
        if (
            state.work_plan_path is not None
            and state.planned_slices
            and state.approved_plan_commit is not None
        ):
            bridge.append(
                plan_payload(
                    work_plan_path=state.work_plan_path,
                    approved_plan_commit=state.approved_plan_commit,
                    slices=state.planned_slices,
                ),
                logical_id="approved-plan",
                idempotency_key=f"approved-plan:{state.approved_plan_commit}",
                fingerprint_sha256=contract_fingerprint,
                fingerprint_kind=FingerprintKind.CONTRACT,
            )
        if (
            state.current_step is WorkflowStep.COMPLETED
            and all(item.commit_ref is not None for item in state.slices)
            and (
                state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
                or (
                    state.execution_mode == TaskMode.PLAN_ONLY.value
                    and state.current_work_unit.kind is WorkUnitKind.PLAN
                )
            )
        ):
            chain = bridge.store.load_chain()
            final_binding = next(
                (
                    item
                    for item in reversed(chain)
                    if isinstance(item.payload, BindingPayload)
                    and item.payload.binding_kind in {"commit", "plan_commit"}
                ),
                None,
            )
            if final_binding is None:
                raise WorkflowExecutionError(
                    "structured completion requires a reviewed commit binding"
                )
            bridge.append(
                WorkflowCompletionPayload(
                    outcome="completed", final_binding_id=final_binding.record_id
                ),
                logical_id="workflow-completion",
                idempotency_key="workflow-completion:completed",
                fingerprint_sha256=final_binding.fingerprint.sha256,
            )

    def _persist_provider_bootstrap(self, measurement: ProviderInputMeasurement) -> ArtifactRecord | None:
        """Dual-write a lossless measurement and final-transition preflight."""
        state = self.active_state
        if state is None:
            raise WorkflowExecutionError("provider bootstrap has no active state")
        bridge = self._artifact_bridge
        chain = bridge.store.load_chain() if bridge is not None else ()
        record_head = relevant_record_head(chain)
        repository_fingerprint = (
            self.collect_changes(state.branch_base).fingerprint
            if measurement.operation in FINAL_REVIEW_OPERATIONS
            else self._artifact_fingerprint()
        )
        transition = transition_fingerprint(
            provider=measurement.provider, role=measurement.role,
            operation=measurement.operation, work_unit_id=str(state.current_work_unit_id),
            record_head=record_head, repository_fingerprint=repository_fingerprint,
            input_digest=measurement.input_digest, policy_digest=measurement.policy_digest,
        )
        payload = provider_input_measurement_payload(
            measurement, work_unit_id=state.current_work_unit_id,
            transition_fingerprint=transition, relevant_record_head=record_head,
        )
        measurement_record = None
        if bridge is not None:
            measurement_record = bridge.append(
                payload, logical_id=f"provider-input-{state.current_work_unit_id}-{measurement.operation}",
                idempotency_key=f"provider-input:{transition}",
                fingerprint_sha256=repository_fingerprint,
            )
        state = state.with_bootstrap_check(self._bootstrap_fact(payload))
        self._persist_bootstrap_state(state)
        if (
            measurement.operation not in FINAL_REVIEW_OPERATIONS
            or not measurement.allowed
            or bridge is None
        ):
            return measurement_record
        assert measurement_record is not None
        current_chain = bridge.store.load_chain()
        try:
            changes = self.collect_changes(state.branch_base)
            repository_paths = changes.paths
        except NoWorkflowChangesError:
            repository_paths = ()
        result = run_final_review_preflight(
            state=state, records=current_chain, measurement_record=measurement_record,
            repository_paths=repository_paths,
        )
        checked = preflight_payload(measurement_record=measurement_record, result=result)
        if bridge is not None:
            bridge.append(
                checked, logical_id=f"final-preflight-{state.current_work_unit_id}-{measurement.operation}",
                idempotency_key=f"final-preflight:{transition}",
                fingerprint_sha256=repository_fingerprint,
            )
        state = state.with_bootstrap_check(self._bootstrap_fact(checked))
        self._persist_bootstrap_state(state)
        if not result.passed:
            raise FinalReviewPreflightDenied(
                result,
                fingerprint=repository_fingerprint,
            )
        return measurement_record

    def _start_provider_attempt(
        self,
        measurement: ProviderInputMeasurement,
        bootstrap: object | None,
        *,
        operation_instance: str | None = None,
    ) -> ArtifactRecord:
        bridge = self._artifact_bridge
        state = self.active_state
        if (
            bridge is None or state is None or not isinstance(bootstrap, ArtifactRecord)
            or not isinstance(bootstrap.payload, ProviderInputMeasurementPayload)
        ):
            raise WorkflowExecutionError("provider attempt start requires its durable measurement")
        if not re.fullmatch(r"[0-9a-f]{64}", measurement.binding_fingerprint):
            raise WorkflowExecutionError("provider attempt has no bound fingerprint")
        if (
            bootstrap.payload.input_digest != measurement.input_digest
            or bootstrap.payload.provider.value != measurement.provider
            or bootstrap.payload.operation != measurement.operation
        ):
            raise WorkflowExecutionError("provider attempt measurement context diverged")
        return bridge.start_provider_attempt(
            measurement_record=bootstrap,
            binding_fingerprint=measurement.binding_fingerprint,
            work_unit_id=state.current_work_unit_id,
            operation_instance=operation_instance,
            model=self.agents[measurement.provider].model,
            effort=self.agents[measurement.provider].effort,
        )

    def _finish_provider_attempt(
        self,
        started: object,
        duration_seconds: float,
        failure_kind: str | None,
        usage: ProviderUsagePayload | None,
    ) -> None:
        bridge = self._artifact_bridge
        if bridge is None or not isinstance(started, ArtifactRecord):
            raise WorkflowExecutionError("provider attempt terminal requires its durable start")
        bridge.finish_provider_attempt(
            started,
            duration_seconds=duration_seconds,
            failure_kind=failure_kind,
            usage=usage,
        )

    @staticmethod
    def _bootstrap_fact(payload: ProviderInputMeasurementPayload | object) -> BootstrapCheckFact:
        digest = hashlib.sha256(canonical_json(payload)).hexdigest()
        check_kind = payload.record_type.value
        decision = (
            "allowed" if isinstance(payload, ProviderInputMeasurementPayload) and payload.allowed
            else "denied" if getattr(payload, "outcome", None) == "denied" or isinstance(payload, ProviderInputMeasurementPayload)
            else "passed"
        )
        return BootstrapCheckFact(
            check_kind=check_kind, transition_fingerprint=payload.transition_fingerprint,
            provider=payload.provider.value, role=payload.role.value, operation=payload.operation,
            work_unit_id=int(payload.work_unit_id), semantic_digest=digest, decision=decision,
            error_code=(
                "PROVIDER-INPUT-BUDGET" if isinstance(payload, ProviderInputMeasurementPayload) and not payload.allowed
                else getattr(payload, "error_code", None)
            ),
        )

    def _persist_bootstrap_state(self, state: WorkflowState) -> None:
        save_workflow_state(self.state_file, state, allowed_roots=self.allowed_roots, replace_existing_run_id=self._replace_existing_run_id)
        write_workflow_checkpoint(self.checkpoint_dir / state.run_id, state, allowed_roots=self.allowed_roots)
        self._replace_existing_run_id = None
        self.active_state = state

    def invoke_codex(self, invocation: CodexInvocation) -> NativeAgentCodexOutput:
        state = self.active_state
        if (
            state is None
            or invocation.native_request is None
            or state.protocol_binding is None
            or state.protocol_binding.codex_result_transport
            != NATIVE_CODEX_RESULT_TRANSPORT
            or state.current_work_unit_id != invocation.work_unit_id
            or state.current_step is not invocation.step
        ):
            raise WorkflowExecutionError(
                "native Codex invocation lacks its immutable state binding"
            )
        self.assert_structured_decision_context()
        native_adapter = self.agents[AgentRole.CODEX.value]
        if not isinstance(native_adapter, NativeCodexAdapter):
            raise WorkflowExecutionError(
                "configured Codex adapter is not the native result transport"
            )
        self._persist_native_agent_request_bundle(invocation)
        raw_path = self._native_codex_response_path(invocation)
        output = run_native_codex_agent_checked(
                adapter=native_adapter,
                bundle=invocation.native_request,
                raw_response_path=raw_path,
                config=self.config,
                write_file=self._write_native_codex_raw_response,
                shorten=_shorten,
                operation=invocation.step.value,
                binding_fingerprint=(
                    invocation.native_request.bound_context.context.current_fingerprint
                ),
                pre_start_callback=self._persist_provider_bootstrap,
                provider_attempt_lifecycle=(
                    ProviderAttemptLifecycle(
                        start=lambda measurement, bootstrap: self._start_provider_attempt(
                            measurement,
                            bootstrap,
                            operation_instance=f"round:{invocation.round_number}",
                        ),
                        terminal=self._finish_provider_attempt,
                    )
                    if self._artifact_bridge is not None
                    else None
                ),
                accepted_output_callback=lambda accepted: (
                    self.persist_native_codex_contract(
                        accepted, invocation.previous_findings
                    )
                ),
        )
        self.last_codex_output = output.canonical_json
        return output

    def authoritative_native_findings(
        self,
        state: WorkflowState,
        mirror_findings: tuple[FindingRecord, ...],
    ) -> tuple[FindingRecord, ...]:
        """Return the current work unit's finding state only from accepted records."""
        active = self.active_state
        bridge = self._artifact_bridge
        if (
            active is None
            or bridge is None
            or active.run_id != state.run_id
            or active.current_work_unit_id != state.current_work_unit_id
            or state.protocol_binding is None
            or state.protocol_binding.codex_result_transport
            != NATIVE_CODEX_RESULT_TRANSPORT
            or state.protocol_binding.claude_review_transport
            != NATIVE_CLAUDE_REVIEW_TRANSPORT
        ):
            raise WorkflowExecutionError(
                "combined native finding replay lacks its immutable state binding"
            )
        try:
            replay = replay_artifacts(
                bridge.store.load_chain(), state.run_id, allow_empty=True
            )
            reduced = reduce_findings(replay)
            if state.current_work_unit.kind is WorkUnitKind.CORRECTION:
                attribution = reduced.correction_for(
                    state.current_work_unit_id
                )
                if attribution is None:
                    raise WorkflowExecutionError(
                        "correction finding replay requires a bound correction "
                        "work-unit record"
                    )
                projected = reduced.request_subset(
                    finding_ids=attribution.finding_ids
                ).findings
                correction_ids = frozenset(attribution.finding_ids)
                mirror_findings = tuple(
                    finding
                    for finding in mirror_findings
                    if finding.finding_id in correction_ids
                )
            else:
                projected = reduced.ledger.findings
        except (ArtifactReplayError, ValueError) as exc:
            raise WorkflowExecutionError(
                f"authoritative finding replay failed: {exc}"
            ) from exc
        canonical_mirror = tuple(
            sorted(mirror_findings, key=lambda item: item.finding_id)
        )
        if projected != canonical_mirror:
            raise WorkflowExecutionError(
                "authoritative finding replay differs from the state-v3 mirror"
            )
        return projected

    def carry_forward_native_findings(
        self,
        state: WorkflowState,
        current_findings: tuple[FindingRecord, ...],
    ) -> tuple[FindingRecord, ...]:
        """Restore the complete record-native ledger at a work-unit boundary.

        The replay may add findings from earlier work units, but it must contain
        every finding already present in the current state mirror with identical
        semantics.  Otherwise carrying the replay into the next work unit would
        silently bless a damaged or incomplete record chain as the new mirror.
        """
        active = self.active_state
        bridge = self._artifact_bridge
        if (
            active is None
            or bridge is None
            or active.run_id != state.run_id
            or active.current_work_unit_id != state.current_work_unit_id
        ):
            raise WorkflowExecutionError(
                "native finding carry-forward lacks its immutable state binding"
            )
        try:
            replay = replay_artifacts(
                bridge.store.load_chain(), state.run_id, allow_empty=True
            )
            projected = reduce_findings(replay).ledger.findings
        except ArtifactReplayError as exc:
            raise WorkflowExecutionError(
                f"native finding carry-forward failed: {exc}"
            ) from exc
        current_ids = {finding.finding_id for finding in current_findings}
        carried_current = tuple(
            finding for finding in projected if finding.finding_id in current_ids
        )
        canonical_current = tuple(
            sorted(current_findings, key=lambda finding: finding.finding_id)
        )
        if carried_current != canonical_current:
            raise WorkflowExecutionError(
                "record-native finding carry-forward differs from the "
                "state-v3 mirror"
            )
        return projected

    def _native_codex_response_path(self, invocation: CodexInvocation) -> Path:
        state = self.active_state
        if state is None:
            raise WorkflowExecutionError("native Codex response has no active state")
        return (
            self.root
            / ".orchestrator"
            / "artifacts"
            / state.run_id
            / "native-codex-responses"
            / (
                f"work-unit-{invocation.work_unit_id:04d}-"
                f"{invocation.step.value}-round-{invocation.round_number:04d}.json"
            )
        )

    def _native_agent_request_path(self, invocation: object) -> Path:
        state = self.active_state
        if state is None:
            raise WorkflowExecutionError("native agent request has no active state")
        return (
            self.root
            / ".orchestrator"
            / "artifacts"
            / state.run_id
            / "native-agent-requests"
            / (
                f"work-unit-{invocation.work_unit_id:04d}-"
                f"{invocation.step.value}-round-{invocation.round_number:04d}.json"
            )
        )

    @staticmethod
    def _native_agent_request_bundle_json(bundle: object) -> str:
        return canonical_json(
            {
                "schema_version": "native-agent-request-bundle-v1",
                "canonical_request": bundle.canonical_json,
                "provider_response_schema": bundle.provider_response_schema_json,
                "evidence_assets": [
                    {
                        "path": item.path,
                        "sha256": item.sha256,
                        "byte_count": item.byte_count,
                        "content": item.content,
                    }
                    for item in bundle.evidence_assets
                ],
            }
        ).decode("utf-8")

    def _persist_native_agent_request_bundle(
        self, invocation: object
    ) -> None:
        bundle = invocation.native_request
        if bundle is None:
            raise WorkflowExecutionError("native agent invocation has no request bundle")
        path = self._native_agent_request_path(invocation)
        content = self._native_agent_request_bundle_json(bundle)
        if path.exists():
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                raise WorkflowExecutionError(
                    "native agent request differs from its persisted recovery artifact"
                )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8", newline="") as stream:
                stream.write(content)
        except FileExistsError:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                raise WorkflowExecutionError(
                    "native agent request differs from its persisted recovery artifact"
                )
        if path.read_text(encoding="utf-8") != content:
            raise WorkflowExecutionError(
                "native agent request recovery artifact verification failed"
            )

    def _load_native_agent_request_bundle(
        self,
        invocation: object,
        rebuilt: object,
    ) -> object | None:
        path = self._native_agent_request_path(invocation)
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
        try:
            document = json.loads(raw)
            if (
                not isinstance(document, dict)
                or set(document)
                != {
                    "schema_version",
                    "canonical_request",
                    "provider_response_schema",
                    "evidence_assets",
                }
                or document["schema_version"] != "native-agent-request-bundle-v1"
                or canonical_json(document).decode("utf-8") != raw
                or not isinstance(document["canonical_request"], str)
                or not isinstance(document["provider_response_schema"], str)
                or not isinstance(document["evidence_assets"], list)
            ):
                raise ValueError("native agent request recovery artifact is invalid")
            request_document = json.loads(document["canonical_request"])
            if not isinstance(request_document, dict):
                raise ValueError("persisted native agent request is not an object")
            request_id = request_document.get("request_id")
            fingerprint = request_document.get("current_fingerprint")
            if (
                not isinstance(request_id, str)
                or not isinstance(fingerprint, str)
            ):
                raise ValueError("persisted native agent request binding is invalid")
            asset_annotation = get_type_hints(type(rebuilt))["evidence_assets"]
            asset_type = get_args(asset_annotation)[0]
            assets = tuple(
                asset_type(
                    path=item["path"],
                    sha256=item["sha256"],
                    byte_count=item["byte_count"],
                    content=item["content"],
                )
                for item in document["evidence_assets"]
                if isinstance(item, dict)
                and set(item) == {"path", "sha256", "byte_count", "content"}
            )
            if len(assets) != len(document["evidence_assets"]):
                raise ValueError("persisted native agent evidence assets are invalid")
            context = replace(
                rebuilt.bound_context.context,
                current_fingerprint=fingerprint,
            )
            return type(rebuilt)(
                canonical_json=document["canonical_request"],
                bound_context=type(rebuilt.bound_context)(
                    context=context,
                    request_id=request_id,
                    request_digest=request_id.rsplit("-", 1)[-1],
                ),
                provider_response_schema_json=document["provider_response_schema"],
                evidence_assets=assets,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WorkflowExecutionError(
                f"native agent request recovery artifact no longer validates: {exc}"
            ) from exc

    @staticmethod
    def _write_native_codex_raw_response(path: Path, content: str) -> None:
        """Create or verify one immutable raw response artifact."""
        if path.exists():
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                raise WorkflowExecutionError(
                    "native Codex raw response differs from its persisted artifact"
                )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8", newline="") as stream:
                stream.write(content)
        except FileExistsError:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                raise WorkflowExecutionError(
                    "native Codex raw response differs from its persisted artifact"
                )
            return
        if path.read_text(encoding="utf-8") != content:
            raise WorkflowExecutionError(
                "native Codex raw response verification failed"
            )

    def invoke_reviewer(self, invocation: ReviewerInvocation) -> NativeAgentReviewOutput:
        manifest_paths: tuple[str, ...] | None = None
        if invocation.review_packet is not None:
            self._materialize_review_packet(invocation.review_packet)
            manifest_paths = invocation.review_packet.manifest.paths
        state = self.active_state
        if (
            state is None
            or invocation.native_request is None
            or invocation.reviewer is not AgentRole.CLAUDE
            or state.protocol_binding is None
            or state.protocol_binding.claude_review_transport
            != NATIVE_CLAUDE_REVIEW_TRANSPORT
        ):
            raise WorkflowExecutionError(
                "native Claude invocation lacks its immutable state binding"
            )
        self.assert_structured_decision_context()
        native_adapter = self.agents[AgentRole.CLAUDE.value]
        if not isinstance(native_adapter, NativeClaudeReviewAdapter):
            raise WorkflowExecutionError(
                "configured Claude adapter is not the native review transport"
            )
        return run_native_review_agent_checked(
                adapter=native_adapter,
                bundle=invocation.native_request,
                log_prefix=(
                    f"work-unit-{invocation.work_unit_id:04d}-"
                    f"{invocation.step.value}-round-{invocation.round_number:04d}"
                ),
                config=self.config,
                log_dir=self.log_dir,
                write_file=write_file,
                shorten=_shorten,
                reviewer_manifest_paths=manifest_paths,
                operation=invocation.step.value,
                binding_fingerprint=invocation.fingerprint,
                pre_start_callback=self._persist_provider_bootstrap,
                provider_attempt_lifecycle=(
                    ProviderAttemptLifecycle(
                        start=lambda measurement, bootstrap: self._start_provider_attempt(
                            measurement,
                            bootstrap,
                            operation_instance=f"round:{invocation.round_number}",
                        ),
                        terminal=self._finish_provider_attempt,
                    )
                    if self._artifact_bridge is not None
                    else None
                ),
                accepted_output_callback=lambda output: (
                    self.persist_native_review_contract(
                        output,
                        invocation.fingerprint,
                        invocation.round_number,
                        invocation.previous_findings,
                    )
                ),
        )

    def _materialize_review_packet(self, packet: ReviewPacket) -> Path:
        """Write or verify the reconstructable content-addressed packet cache."""
        if self.active_state is None:
            raise WorkflowExecutionError("review packet materialization has no active state")
        packet_dir = (
            self.root / ".orchestrator" / "artifacts" / self.active_state.run_id
            / "review-packets"
        )
        packet_dir.mkdir(parents=True, exist_ok=True)
        target = packet_dir / f"{packet.digest}.json"
        try:
            with target.open("xb") as stream:
                stream.write(packet.canonical_bytes)
        except FileExistsError:
            if not target.is_file() or target.read_bytes() != packet.canonical_bytes:
                raise WorkflowExecutionError(
                    "content-addressed review packet cache differs from canonical bytes"
                )
            return target
        if target.read_bytes() != packet.canonical_bytes:
            raise WorkflowExecutionError("review packet cache verification failed")
        return target

    @staticmethod
    def _canonical_native_agent_result(
        candidates: tuple[ArtifactRecord, ...],
        logical: str,
    ) -> ArtifactRecord | None:
        """Return one durable Codex fact, tolerating only exact legacy duplicates."""
        if not candidates:
            return None
        canonical = candidates[0]
        assert isinstance(canonical.payload, AgentResultPayload)
        for record in candidates:
            payload = record.payload
            if (
                not isinstance(payload, AgentResultPayload)
                or payload != canonical.payload
                or record.logical_id != logical
            ):
                raise WorkflowExecutionError(
                    "native agent recovery has divergent agent-result records"
                )
            binding_digest = hashlib.sha256(
                (
                    f"{record.fingerprint.sha256}:{payload.request_id}:"
                    f"{payload.response_sha256}"
                ).encode("utf-8")
            ).hexdigest()
            if record.idempotency_key != f"native:{logical}:{binding_digest}":
                raise WorkflowExecutionError(
                    "native agent recovery result idempotency binding differs"
                )
        if len(candidates) > 1:
            logger.warning(
                "Native agent recovery found %s semantically identical result "
                "records; retaining the earliest durable binding for %s.",
                len(candidates),
                logical,
            )
        return canonical

    def recover_pending_native_codex(
        self,
        invocation: CodexInvocation,
        contract: CodexStepContract,
        history: WorkflowHistory,
    ) -> NativeAgentCodexOutput | None:
        """Replay one record-ahead Codex result without another provider start."""
        _ = contract
        state = self.active_state
        bridge = self._artifact_bridge
        bundle = invocation.native_request
        if (
            state is None
            or bridge is None
            or bundle is None
            or state.protocol_binding is None
            or state.protocol_binding.codex_result_transport
            != NATIVE_CODEX_RESULT_TRANSPORT
            or state.current_work_unit_id != invocation.work_unit_id
            or state.current_step is not invocation.step
        ):
            return None
        logical = (
            f"agent-{invocation.work_unit_id}-{invocation.step.value}-"
            f"{invocation.round_number}"
        )
        chain = bridge.store.load_chain()
        candidates = tuple(
            item
            for item in chain
            if isinstance(item.payload, AgentResultPayload)
            and item.logical_id == logical
        )
        candidate = self._canonical_native_agent_result(candidates, logical)
        raw_path = self._native_codex_response_path(invocation)
        if not raw_path.is_file():
            if candidate is None:
                return None
            raise WorkflowExecutionError(
                "native Codex recovery record has no raw response artifact"
            )
        canonical = raw_path.read_text(encoding="utf-8")
        response_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if (
            candidate is not None
            and candidate.payload.response_sha256 != response_sha256
        ):
            raise WorkflowExecutionError(
                "native Codex recovery raw response digest differs from its record"
            )
        recovery_bundle = self._load_native_agent_request_bundle(invocation, bundle)
        recovery_bound = (
            recovery_bundle.bound_context if recovery_bundle is not None else bundle.bound_context
        )
        validate_against_bundle = recovery_bundle is not None or candidate is None
        original_attempt_record = None
        if candidate is not None and recovery_bound.context.previous_findings:
            candidate_index = chain.index(candidate)
            prior_attempts = tuple(
                item
                for item in chain[:candidate_index]
                if isinstance(item.payload, ProviderAttemptPayload)
                and item.payload.provider is candidate.payload.role
                and item.payload.work_unit_id == str(invocation.work_unit_id)
                and item.payload.operation == invocation.step.value
                and item.payload.phase == "started"
            )
            if not prior_attempts:
                raise WorkflowExecutionError(
                    "native agent recovery has no durable original request binding"
                )
            original_attempt_record = prior_attempts[-1]
        if (
            recovery_bundle is None
            and candidate is not None
            and candidate.payload.request_id != bundle.bound_context.request_id
        ):
            if original_attempt_record is None:
                candidate_index = chain.index(candidate)
                prior_attempts = tuple(
                    item
                    for item in chain[:candidate_index]
                    if isinstance(item.payload, ProviderAttemptPayload)
                    and item.payload.provider is candidate.payload.role
                    and item.payload.work_unit_id == str(invocation.work_unit_id)
                    and item.payload.operation == invocation.step.value
                    and item.payload.phase == "started"
                )
                if not prior_attempts:
                    raise WorkflowExecutionError(
                        "native agent recovery has no durable original request binding"
                    )
                original_attempt_record = prior_attempts[-1]
            original_request_id = candidate.payload.request_id
            recovery_bound = type(bundle.bound_context)(
                context=replace(
                    bundle.bound_context.context,
                    current_fingerprint=(
                        original_attempt_record.payload.binding_fingerprint
                    ),
                ),
                request_id=original_request_id,
                request_digest=original_request_id.rsplit("-", 1)[-1],
            )
            validate_against_bundle = False
        if original_attempt_record is not None:
            try:
                request_replay = replay_artifacts(
                    chain[: chain.index(original_attempt_record)], state.run_id
                )
                request_findings_by_id = {
                    item.finding_id: item
                    for item in reduce_findings(request_replay).ledger.findings
                }
            except ArtifactReplayError as exc:
                raise WorkflowExecutionError(
                    f"native agent request-time finding replay failed: {exc}"
                ) from exc
            offered_ids = tuple(
                item.finding_id
                for item in recovery_bound.context.previous_findings
            )
            if any(finding_id not in request_findings_by_id for finding_id in offered_ids):
                raise WorkflowExecutionError(
                    "native agent request-time finding subset is incomplete"
                )
            recovery_bound = type(recovery_bound)(
                context=replace(
                    recovery_bound.context,
                    previous_findings=tuple(
                        request_findings_by_id[finding_id]
                        for finding_id in offered_ids
                    ),
                ),
                request_id=recovery_bound.request_id,
                request_digest=recovery_bound.request_digest,
            )
        try:
            document = json.loads(canonical)
            if not isinstance(document, dict):
                raise ValueError("native Codex raw response is not an object")
            if canonical_native_codex_json(document) != canonical:
                raise ValueError("native Codex raw response is not canonical JSON")
            if validate_against_bundle:
                validate_native_codex_provider_response(
                    document, recovery_bundle or bundle
                )
            result = parse_bound_native_codex_contract_result(
                document, recovery_bound
            )
        except (ValueError, TypeError) as exc:
            raise WorkflowExecutionError(
                f"native Codex recovery response no longer validates: {exc}"
            ) from exc
        output = NativeAgentCodexOutput(
            result=result,
            canonical_json=canonical,
            request_id=recovery_bound.request_id,
            response_sha256=response_sha256,
        )
        if candidate is None:
            self.persist_native_codex_contract(
                output, invocation.previous_findings
            )
            logger.warning(
                "Recovered native Codex result from raw-response-ahead persistence: "
                "work-unit=%s operation=%s",
                invocation.work_unit_id,
                invocation.step.value,
            )
            self.last_codex_output = canonical
            return output
        record = candidate
        payload = record.payload
        if (
            payload.role is not Role.CODEX
            or payload.work_unit_id != str(invocation.work_unit_id)
            or payload.transport_schema != NATIVE_CODEX_RESULT_TRANSPORT
            or payload.request_id != recovery_bound.request_id
            or payload.response_sha256 != response_sha256
        ):
            raise WorkflowExecutionError(
                "native Codex recovery record differs from its durable binding"
            )
        expected_payload = agent_result_payload(
            result,
            role=AgentRole.CODEX,
            work_unit_id=invocation.work_unit_id,
            transport_schema=NATIVE_CODEX_RESULT_TRANSPORT,
            request_id=recovery_bound.request_id,
            response_sha256=response_sha256,
        )
        if payload != expected_payload:
            raise WorkflowExecutionError(
                "native Codex recovery result differs from its durable record"
            )
        # AgentResult and its per-finding response transitions are separate
        # append-only records.  Re-drive the idempotent persistence routine so
        # a crash after AgentResult publication cannot make an incomplete
        # finding-disposition set look fully recovered.
        self.persist_native_codex_contract(
            output,
            invocation.previous_findings,
            recovery_fingerprint=record.fingerprint.sha256,
        )
        logger.warning(
            "Recovered native Codex result from record-ahead persistence: "
            "work-unit=%s operation=%s",
            invocation.work_unit_id,
            invocation.step.value,
        )
        self.last_codex_output = canonical
        return output

    def recover_pending_native_reviewer(
        self,
        invocation: ReviewerInvocation,
        contract: StepContract,
        history: WorkflowHistory,
    ) -> NativeAgentReviewOutput | None:
        """Replay one native record-ahead decision without starting Claude."""
        state = self.active_state
        bridge = self._artifact_bridge
        bundle = invocation.native_request
        if (
            state is None
            or bridge is None
            or bundle is None
            or invocation.reviewer is not AgentRole.CLAUDE
            or state.protocol_binding is None
            or state.protocol_binding.claude_review_transport
            != NATIVE_CLAUDE_REVIEW_TRANSPORT
            or state.current_work_unit_id != invocation.work_unit_id
            or state.current_step is not invocation.step
        ):
            return None
        logical_id = (
            f"review-claude-{invocation.work_unit_id}-{invocation.round_number}"
        )
        chain = bridge.store.load_chain()
        candidates = tuple(
            item
            for item in chain
            if isinstance(item.payload, ReviewPayload)
            and item.logical_id == logical_id
        )
        if not candidates:
            return None
        if len(candidates) != 1:
            raise WorkflowExecutionError(
                "native reviewer recovery has multiple decision records"
            )
        record = candidates[0]
        payload = record.payload
        assert isinstance(payload, ReviewPayload)
        if (
            payload.reviewer is not Role.CLAUDE
            or payload.work_unit_id != str(invocation.work_unit_id)
            or payload.transport_schema != NATIVE_CLAUDE_REVIEW_TRANSPORT
            or payload.request_id != bundle.bound_context.request_id
            or payload.response_sha256 is None
            or record.fingerprint.sha256 != invocation.fingerprint
        ):
            raise WorkflowExecutionError(
                "native reviewer recovery record differs from the rebuilt request"
            )
        if any(
            isinstance(event, ReviewAuditEvent)
            and event.round_number == invocation.round_number
            and event.result.reviewer is AgentRole.CLAUDE
            and event.result.validation is not None
            and event.result.validation.diff_fingerprint == invocation.fingerprint
            for event in history.events
        ):
            raise WorkflowExecutionError(
                "native reviewer decision is already mirrored but the workflow step "
                "did not advance"
            )
        matching_attestations = tuple(
            item
            for item in chain
            if isinstance(item.payload, ValidationAttestationPayload)
            and item.fingerprint.sha256 == invocation.fingerprint
            and item.payload.attested_by is Role.ORCHESTRATOR
        )
        if len(matching_attestations) != 1:
            raise WorkflowExecutionError(
                "native reviewer recovery requires one authoritative attestation"
            )
        if (
            contract.validation_attestation is None
            or not contract.validation_attestation.complete
            or contract.validation_attestation.diff_fingerprint
            != invocation.fingerprint
        ):
            raise WorkflowExecutionError(
                "native reviewer recovery has no complete bound attestation"
            )
        log_pattern = (
            f"work-unit-{invocation.work_unit_id:04d}-"
            f"{invocation.step.value}-round-{invocation.round_number:04d}."
            "attempt-*.log"
        )
        outputs = tuple(
            path.read_text(encoding="utf-8").strip()
            for path in sorted(self.log_dir.glob(log_pattern))
            if path.is_file()
            and hashlib.sha256(
                path.read_text(encoding="utf-8").strip().encode("utf-8")
            ).hexdigest()
            == payload.response_sha256
        )
        if len(outputs) != 1:
            raise WorkflowExecutionError(
                "native reviewer recovery has no unique response-digest-bound log"
            )
        canonical = outputs[0]
        try:
            document = json.loads(canonical)
            if not isinstance(document, dict):
                raise ValueError("native response log must contain a JSON object")
            validate_native_review_provider_response(document, bundle)
            result = parse_bound_native_contract_result(
                document, bundle.bound_context
            )
        except (json.JSONDecodeError, ValueError, NativeReviewContractError) as exc:
            raise WorkflowExecutionError(
                f"native reviewer recovery response no longer validates: {exc}"
            ) from exc
        if not review_payload_matches_result(payload, result):
            raise WorkflowExecutionError(
                "native reviewer recovery result differs from its decision record"
            )
        logger.warning(
            "Replaying request-bound native Claude review after its state "
            "checkpoint failed: work-unit=%s round=%s fingerprint=%s request=%s",
            invocation.work_unit_id,
            invocation.round_number,
            invocation.fingerprint,
            bundle.bound_context.request_id,
        )
        return NativeAgentReviewOutput(
            result=result,
            canonical_json=canonical,
            request_id=bundle.bound_context.request_id,
        )

    def recover_pending_native_reviewer_before_policy(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> PersistedNativeReviewerReplay | None:
        """Recover a native decision before current-worktree policy is evaluated.

        A provider response and its ReviewPayload are written before the state-v3
        checkpoint.  Repository changes made after that durable write must not
        force the already completed reviewer round to be rebuilt against a new
        fingerprint or invoke the provider again.
        """
        bridge = self._artifact_bridge
        unit = state.current_work_unit
        if (
            bridge is None
            or self.active_state is None
            or state.protocol_binding is None
            or state.protocol_binding.claude_review_transport
            != NATIVE_CLAUDE_REVIEW_TRANSPORT
            or state.current_step
            not in {
                WorkflowStep.CLAUDE_PLAN_REVIEW,
                WorkflowStep.CLAUDE_SLICE_REVIEW,
                WorkflowStep.CLAUDE_FINAL_REVIEW,
            }
            or self.active_state.run_id != state.run_id
            or self.active_state.current_work_unit_id != unit.work_unit_id
        ):
            return None

        mirrored = {
            (
                event.round_number,
                event.result.validation.diff_fingerprint,
                (
                    "stop"
                    if event.result.stopped
                    else "approved"
                    if event.result.approval is True
                    else "denied"
                ),
                tuple(item.finding_id for item in event.result.findings),
            )
            for event in history.events
            if isinstance(event, ReviewAuditEvent)
            and event.result.reviewer is AgentRole.CLAUDE
            and event.result.validation is not None
        }
        chain = bridge.store.load_chain()
        pending: list[tuple[int, ArtifactRecord]] = []
        logical_prefix = f"review-claude-{unit.work_unit_id}-"
        for record in chain:
            payload = record.payload
            if (
                not isinstance(payload, ReviewPayload)
                or payload.reviewer is not Role.CLAUDE
                or payload.work_unit_id != str(unit.work_unit_id)
                or payload.transport_schema != NATIVE_CLAUDE_REVIEW_TRANSPORT
                or not record.logical_id.startswith(logical_prefix)
            ):
                continue
            suffix = record.logical_id.removeprefix(logical_prefix)
            if not suffix.isdigit() or int(suffix) < 1:
                raise WorkflowExecutionError(
                    "native reviewer recovery record has an invalid logical round"
                )
            round_number = int(suffix)
            signature = (
                round_number,
                record.fingerprint.sha256,
                payload.verdict,
                payload.finding_ids,
            )
            if signature not in mirrored:
                pending.append((round_number, record))
        if not pending:
            return None
        if len(pending) != 1:
            raise WorkflowExecutionError(
                "pre-policy native reviewer recovery has multiple pending decisions"
            )

        round_number, record = pending[0]
        payload = record.payload
        assert isinstance(payload, ReviewPayload)
        if payload.request_id is None or payload.response_sha256 is None:
            raise WorkflowExecutionError(
                "pre-policy native reviewer recovery lacks its request binding"
            )
        matching_attestations = tuple(
            item
            for item in history.attestations
            if item.complete
            and item.diff_fingerprint == record.fingerprint.sha256
        )
        authoritative_attestations = tuple(
            item
            for item in chain
            if isinstance(item.payload, ValidationAttestationPayload)
            and item.payload.attested_by is Role.ORCHESTRATOR
            and item.fingerprint.sha256 == record.fingerprint.sha256
        )
        if len(matching_attestations) != 1 or len(authoritative_attestations) != 1:
            raise WorkflowExecutionError(
                "pre-policy native reviewer recovery requires one complete "
                "authoritative attestation"
            )
        attestation = matching_attestations[0]

        log_pattern = (
            f"work-unit-{unit.work_unit_id:04d}-{state.current_step.value}-"
            f"round-{round_number:04d}.attempt-*.log"
        )
        outputs = tuple(
            output
            for path in sorted(self.log_dir.glob(log_pattern))
            if path.is_file()
            for output in (path.read_text(encoding="utf-8").strip(),)
            if hashlib.sha256(output.encode("utf-8")).hexdigest()
            == payload.response_sha256
        )
        if len(outputs) != 1:
            raise WorkflowExecutionError(
                "pre-policy native reviewer recovery has no unique "
                "response-digest-bound log"
            )
        canonical = outputs[0]
        expected_test_files = (
            tuple(
                path
                for path in history.active_review_packet.manifest.paths
                if matches_path_patterns(path, context.test_path_patterns)
            )
            if history.active_review_packet is not None
            and history.active_review_packet.fingerprint
            == record.fingerprint.sha256
            else ()
            if state.current_step is WorkflowStep.CLAUDE_PLAN_REVIEW
            else context.expected_test_files
        )
        approval_marker = (
            ApprovalMarker.PLAN
            if state.current_step is WorkflowStep.CLAUDE_PLAN_REVIEW
            else ApprovalMarker.FINAL
            if state.current_step is WorkflowStep.CLAUDE_FINAL_REVIEW
            else ApprovalMarker.SLICE
        )
        native_context = NativeReviewContext(
            run_id=state.run_id,
            work_unit_id=str(unit.work_unit_id),
            operation=state.current_step.value,
            diff_fingerprint=record.fingerprint.sha256,
            reviewer=AgentRole.CLAUDE,
            approval_marker=approval_marker,
            slice_id=(
                "FINAL" if approval_marker is ApprovalMarker.FINAL else f"{unit.slice_id:02d}"
            ),
            round_number=round_number,
            previous_findings=history.findings,
            validation_attestation=attestation,
            test_files=tuple(sorted(set(expected_test_files))),
            test_changes_approved=True,
            allow_new_observations=unit.kind is not WorkUnitKind.CORRECTION,
            validation_command_prefixes=(
                context.validation_matrix.finding_command_prefixes
            ),
        )
        request_digest = payload.request_id.removeprefix("native-review-request-")
        try:
            document = json.loads(canonical)
            if not isinstance(document, dict):
                raise ValueError("native response log must contain a JSON object")
            validate_native_review_provider_response_for_context(
                document, native_context
            )
            result = parse_bound_native_contract_result(
                document,
                BoundNativeReviewContext(
                    context=native_context,
                    request_id=payload.request_id,
                    request_digest=request_digest,
                ),
            )
        except (json.JSONDecodeError, ValueError, NativeReviewContractError) as exc:
            raise WorkflowExecutionError(
                f"pre-policy native reviewer response no longer validates: {exc}"
            ) from exc
        if not review_payload_matches_result(payload, result):
            raise WorkflowExecutionError(
                "pre-policy native reviewer result differs from its decision record"
            )
        logger.warning(
            "Mirroring request-bound native Claude review before current-diff "
            "policy: work-unit=%s round=%s fingerprint=%s request=%s",
            unit.work_unit_id,
            round_number,
            record.fingerprint.sha256,
            payload.request_id,
        )
        return PersistedNativeReviewerReplay(
            output=NativeAgentReviewOutput(
                result=result,
                canonical_json=canonical,
                request_id=payload.request_id,
            ),
            fingerprint=record.fingerprint.sha256,
            round_number=round_number,
        )

    def _artifact_fingerprint(self) -> str:
        if self.active_state is None:
            raise WorkflowExecutionError("structured persistence has no active state")
        state = self.active_state
        if state.current_work_unit.kind is WorkUnitKind.PLAN:
            if state.task_digest is None:
                raise WorkflowExecutionError("plan artifact requires a task fingerprint")
            return state.task_digest
        start_commit = (
            state.branch_base
            if state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
            else state.current_slice.start_commit or state.branch_base
        )
        try:
            return self.collect_changes(start_commit).fingerprint
        except NoWorkflowChangesError:
            # A not-ready or empty Codex result is still a decision record.  Bind
            # it to the persisted empty Slice boundary instead of losing the
            # diagnostic before the typed halt is recorded.
            if state.current_slice.start_fingerprint is not None:
                return state.current_slice.start_fingerprint
            raise

    def persist_native_codex_contract(
        self,
        output: NativeAgentCodexOutput,
        previous_findings: tuple[FindingRecord, ...],
        *,
        recovery_fingerprint: str | None = None,
    ) -> None:
        if self._artifact_bridge is None or self.active_state is None:
            return
        state = self.active_state
        if (
            state.protocol_binding is None
            or state.protocol_binding.codex_result_transport
            != NATIVE_CODEX_RESULT_TRANSPORT
        ):
            raise WorkflowExecutionError(
                "native Codex persistence lacks its immutable transport binding"
            )
        unit = state.current_work_unit
        logical = f"agent-{unit.work_unit_id}-{state.current_step.value}-{unit.round_number}"
        payload = agent_result_payload(
            output.result,
            role=AgentRole.CODEX,
            work_unit_id=unit.work_unit_id,
            transport_schema=NATIVE_CODEX_RESULT_TRANSPORT,
            request_id=output.request_id,
            response_sha256=output.response_sha256,
        )
        chain = self._artifact_bridge.store.load_chain()
        canonical = self._canonical_native_agent_result(
            tuple(
                record
                for record in chain
                if isinstance(record.payload, AgentResultPayload)
                and record.logical_id == logical
            ),
            logical,
        )
        if canonical is not None:
            if canonical.payload != payload:
                raise WorkflowExecutionError(
                    "native agent result logical binding differs"
                )
            fingerprint = canonical.fingerprint.sha256
            idempotency_key = canonical.idempotency_key
        else:
            fingerprint = recovery_fingerprint or self._artifact_fingerprint()
            binding_digest = hashlib.sha256(
                (
                    f"{fingerprint}:{output.request_id}:{output.response_sha256}"
                ).encode("utf-8")
            ).hexdigest()
            idempotency_key = f"native:{logical}:{binding_digest}"
        self._artifact_bridge.append(
            payload,
            logical_id=logical,
            idempotency_key=idempotency_key,
            fingerprint_sha256=fingerprint,
            fingerprint_kind=(
                FingerprintKind.CONTRACT
                if unit.kind is WorkUnitKind.PLAN
                else FingerprintKind.IMPLEMENTATION
            ),
        )
        try:
            response_delta = project_finding_response_delta(
                previous_findings, output.result.findings
            )
        except ValueError as exc:
            raise WorkflowExecutionError(
                f"native implementer finding response delta is invalid: {exc}"
            ) from exc
        for item in response_delta:
            finding = item.finding
            response = item.response
            self._artifact_bridge.append(
                finding_payload(
                    finding,
                    actor=AgentRole.CODEX,
                    action="responded",
                    rationale=response.rationale,
                    work_unit_id=unit.work_unit_id,
                    response_decision=response.decision,
                ),
                logical_id=f"finding-{finding.finding_id}",
                idempotency_key=(
                    f"finding-response:{finding.finding_id}:"
                    f"{item.response_index}"
                ),
                fingerprint_sha256=fingerprint,
            )

    def persist_native_review_contract(
        self,
        output: NativeAgentReviewOutput,
        fingerprint: str,
        round_number: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        if self._artifact_bridge is None or self.active_state is None:
            return
        state = self.active_state
        if (
            state.protocol_binding is None
            or state.protocol_binding.claude_review_transport
            != NATIVE_CLAUDE_REVIEW_TRANSPORT
            or output.result.reviewer is not AgentRole.CLAUDE
        ):
            raise WorkflowExecutionError(
                "native review persistence lacks its immutable Claude binding"
            )
        unit = state.current_work_unit
        logical = f"review-claude-{unit.work_unit_id}-{round_number}"
        response_sha256 = hashlib.sha256(
            output.canonical_json.encode("utf-8")
        ).hexdigest()
        binding_digest = hashlib.sha256(
            (
                f"{fingerprint}:{output.request_id}:{response_sha256}"
            ).encode("utf-8")
        ).hexdigest()
        self._artifact_bridge.append(
            review_payload(
                output.result,
                work_unit_id=unit.work_unit_id,
                transport_schema=NATIVE_CLAUDE_REVIEW_TRANSPORT,
                request_id=output.request_id,
                response_sha256=response_sha256,
            ),
            logical_id=logical,
            idempotency_key=f"native:{logical}:{binding_digest}",
            fingerprint_sha256=fingerprint,
        )
        self._persist_review_finding_transitions(
            output.result,
            fingerprint=fingerprint,
            round_number=round_number,
            previous_findings=previous_findings,
            structured=True,
        )

    def _persist_review_finding_transitions(
        self,
        result: ContractResult,
        *,
        fingerprint: str,
        round_number: int,
        previous_findings: tuple[FindingRecord, ...],
        structured: bool,
    ) -> None:
        if self._artifact_bridge is None:
            return
        work_unit_id: str | None = None
        if structured:
            if self.active_state is None:
                raise WorkflowExecutionError(
                    "structured finding persistence lacks an active work unit"
                )
            work_unit_id = str(self.active_state.current_work_unit_id)
        transitions = project_reviewer_persistence_transitions(
            previous_findings,
            result.findings,
            work_unit_id=work_unit_id,
        )
        for transition in transitions:
            finding = transition.finding
            action = transition.action
            rationale = transition.rationale
            transition_identity = transition.identity
            payload = finding_payload(
                finding,
                action=action,
                rationale=rationale,
                work_unit_id=work_unit_id,
            )
            logical_id = f"finding-{finding.finding_id}"
            legacy_key = (
                f"finding:{finding.finding_id}:{transition_identity}:"
                f"{round_number}:{result.reviewer.value}"
            )
            idempotency_key = legacy_key
            if structured and not transition_identity.startswith(
                "status_rationale:"
            ):
                assert work_unit_id is not None
                idempotency_key = (
                    f"finding:{finding.finding_id}:{transition_identity}:"
                    f"work_unit:{work_unit_id}:{round_number}:"
                    f"{result.reviewer.value}"
                )
                legacy_record = next(
                    (
                        record
                        for record in self._artifact_bridge.store.load_chain()
                        if record.idempotency_key == legacy_key
                    ),
                    None,
                )
                if (
                    legacy_record is not None
                    and getattr(legacy_record.payload, "work_unit_id", None)
                    == work_unit_id
                ):
                    self._artifact_bridge.append(
                        payload,
                        logical_id=logical_id,
                        idempotency_key=legacy_key,
                        fingerprint_sha256=fingerprint,
                    )
                    continue
            self._artifact_bridge.append(
                payload,
                logical_id=logical_id,
                idempotency_key=idempotency_key,
                fingerprint_sha256=fingerprint,
            )

    def persist_contract_diagnostic(
        self, role: AgentRole, output: str, reason: str, attempt: int
    ) -> None:
        if self._artifact_bridge is None or self.active_state is None:
            return
        self._artifact_bridge.diagnostic(
            role=role,
            work_unit_id=self.active_state.current_work_unit_id,
            attempt=attempt,
            output=output,
            reason=reason,
            fingerprint_sha256=self._artifact_fingerprint(),
        )

    def persist_validation_attestation(
        self, attestation: ValidationAttestation
    ) -> None:
        if self._artifact_bridge is None:
            return
        if any(not spec.argv for spec in attestation.command_specs):
            raise WorkflowExecutionError(
                "structured-v2 validation accepts only matrix-provided argv commands"
            )
        self._artifact_bridge.append(
            attestation_payload(attestation),
            logical_id=attestation.attestation_id,
            idempotency_key=f"attestation:{attestation.attestation_id}",
            fingerprint_sha256=attestation.diff_fingerprint,
        )

    def persist_validation_request(self, request) -> None:  # type: ignore[no-untyped-def]
        if self._artifact_bridge is None:
            return
        if any(not command.argv for command in request.commands):
            raise WorkflowExecutionError(
                "structured-v2 validation accepts only matrix-provided argv commands"
            )
        self._artifact_bridge.append(
            validation_request_payload(request),
            logical_id=f"validation-request-{request.diff_fingerprint[:12]}",
            idempotency_key=(
                f"validation-request:{request.diff_fingerprint}:{request.attempt_number}"
            ),
            fingerprint_sha256=request.diff_fingerprint,
        )

    def persist_gate_decision(self, decision: GateDecisionRecord) -> None:
        if self._artifact_bridge is None:
            return
        logical = f"gate-{decision.reason.value}-{decision.fingerprint[:12]}"
        self._artifact_bridge.append(
            GatePayload(
                gate_kind=decision.reason.value.replace("_", "-"),
                decision="approved" if decision.approved else "rejected",
                authority=Role.USER,
                rationale=decision.rationale,
            ),
            logical_id=logical,
            idempotency_key=f"gate:{logical}:{decision.approved}",
            fingerprint_sha256=decision.fingerprint,
        )

    def persist_implementation_handoff(
        self, handoff_path: Path, approved_plan_commit: str
    ) -> None:
        """Bind an idempotent IMPLEMENT handoff to its reviewed plan commit."""
        if self._artifact_bridge is None:
            return
        chain = self._artifact_bridge.store.load_chain()
        commit_binding = next(
            (
                item
                for item in reversed(chain)
                if isinstance(item.payload, BindingPayload)
                and item.payload.binding_kind == "commit"
                and item.payload.target == approved_plan_commit
            ),
            None,
        )
        if commit_binding is None:
            raise WorkflowExecutionError(
                "structured implementation handoff requires a bound reviewed plan commit"
            )
        payload = commit_binding.payload
        assert isinstance(payload, BindingPayload)
        self._artifact_bridge.append(
            BindingPayload(
                binding_kind="implementation_handoff",
                target=str(handoff_path.resolve()),
                attestation_id=payload.attestation_id,
                approval_ids=payload.approval_ids,
            ),
            logical_id=f"implementation-handoff-{approved_plan_commit[:12]}",
            idempotency_key=f"implementation-handoff:{approved_plan_commit}",
            fingerprint_sha256=commit_binding.fingerprint.sha256,
        )

    def prepare_finding_handoff(
        self,
        *,
        plan_task_path: Path,
        work_plan_path: str,
        target_branch: str,
        approved_plan_commit: str,
    ) -> tuple[str, str] | None:
        """Append the export before publishing task bytes, or recover it exactly."""
        bridge = self._artifact_bridge
        state = self.active_state
        if bridge is None or state is None:
            return None
        chain = bridge.store.load_chain()
        replay = replay_artifacts(chain, state.run_id)
        transitions = tuple(
            record for record in replay.records
            if isinstance(record.payload, FindingTransitionPayload)
        )
        if not transitions:
            return None
        commit_binding = next(
            (
                record for record in reversed(replay.records)
                if isinstance(record.payload, BindingPayload)
                and record.payload.binding_kind == "commit"
                and record.payload.target == approved_plan_commit
            ),
            None,
        )
        if commit_binding is None:
            raise WorkflowExecutionError(
                "finding handoff requires the reviewed plan commit binding"
            )
        approval_id = next(
            (
                record_id for record_id in commit_binding.payload.approval_ids
                if any(
                    candidate.record_id == record_id
                    and isinstance(candidate.payload, ReviewPayload)
                    and candidate.payload.verdict == "approved"
                    for candidate in replay.records
                )
            ),
            None,
        )
        if approval_id is None:
            raise WorkflowExecutionError(
                "finding handoff requires a positive bound plan review"
            )
        logical_id = f"finding-handoff-export-{approved_plan_commit[:12]}"
        export_id = stable_record_id(
            state.run_id, RecordType.FINDING_HANDOFF_EXPORT, logical_id, 1
        )
        target = implementation_task_path(plan_task_path)
        try:
            target_relative = target.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise WorkflowExecutionError(
                "finding-bearing implementation handoff must be inside the repository"
            ) from exc
        plan = self.root / PurePosixPath(work_plan_path)
        slices = extract_implementation_slices(
            plan.read_text(encoding="utf-8"), plan_stem=plan.stem
        )
        task_bytes = render_implementation_task(
            work_plan_path=work_plan_path,
            target_branch=target_branch,
            approved_plan_commit=approved_plan_commit,
            slices=slices,
            finding_handoff=(state.run_id, export_id),
        ).encode("utf-8")
        existing = next(
            (record for record in replay.records if record.record_id == export_id), None
        )
        if existing is not None:
            payload = existing.payload
            if (
                not isinstance(payload, FindingHandoffExportPayload)
                or payload.target_task_path != target_relative
                or payload.target_task_sha256 != hashlib.sha256(task_bytes).hexdigest()
            ):
                raise WorkflowExecutionError(
                    "persisted finding handoff export differs from the prepared task"
                )
            return state.run_id, export_id
        payload = finding_handoff_export_payload(
            replay,
            approved_plan_commit=approved_plan_commit,
            approval_review_record_id=approval_id,
            target_task_path=target_relative,
            target_task_bytes=task_bytes,
        )
        record = bridge.append(
            payload,
            logical_id=logical_id,
            idempotency_key=f"finding-handoff-export:{approved_plan_commit}",
            fingerprint_sha256=commit_binding.fingerprint.sha256,
            fingerprint_kind=commit_binding.fingerprint.kind,
        )
        if record.record_id != export_id:
            raise WorkflowExecutionError("finding handoff export identity is unstable")
        return state.run_id, export_id

    def collect_changes(self, start_commit: str) -> WorkflowChanges:
        semantic_paths: tuple[str, ...] = ()
        if self.active_state is not None:
            candidates = {
                path
                for path in (
                    self.active_state.work_plan_path,
                    self.active_state.audit_report_path,
                    *self.active_state.current_slice.scope_paths,
                )
                if path is not None
                and path.startswith("docs/internal/")
                and path.endswith(".md")
            }
            semantic_paths = tuple(sorted(candidates))
        excluded_control_paths = _bound_task_control_paths(
            self.root, self.active_state
        )
        changes = collect_repository_changes(
            self.root,
            start_commit,
            semantic_markdown_paths=semantic_paths,
            excluded_paths=excluded_control_paths,
        )
        if changes.entries:
            review_diff = changes.diff_text or (
                "(binary or metadata-only repository change)"
            )
            if (
                self.active_state is not None
                and self.active_state.current_work_unit.kind
                is WorkUnitKind.FINAL_REVIEW
                and self.active_state.audit_report_path is not None
                and self.active_state.audit_report_path in changes.paths
            ):
                audit_path = self.active_state.audit_report_path
                compacted = collect_repository_changes(
                    self.root,
                    start_commit,
                    semantic_markdown_paths=semantic_paths,
                    excluded_paths=tuple(
                        sorted({*excluded_control_paths, audit_path})
                    ),
                )
                summary = _final_review_audit_evidence_summary(
                    changes,
                    audit_path=audit_path,
                    omitted_diff_chars=max(
                        0, len(changes.diff_text) - len(compacted.diff_text)
                    ),
                )
                review_diff = "\n\n".join(
                    part
                    for part in (
                        compacted.diff_text.strip(),
                        summary,
                    )
                    if part
                )
                logger.info(
                    "Final review evidence compacted: audit=%s original_chars=%s "
                    "evidence_chars=%s fingerprint=%s",
                    audit_path,
                    len(changes.diff_text),
                    len(review_diff),
                    changes.fingerprint,
                )
            rendered = WorkflowChanges(
                start_commit=start_commit,
                fingerprint=changes.fingerprint,
                paths=changes.paths,
                full_diff=review_diff,
                gate_paths=tuple(sorted(set(changes.review_paths))),
            )
            self._repository_changes[changes.fingerprint] = changes
        elif (
            self.active_state is not None
            and self.active_state.current_work_unit.kind is WorkUnitKind.PLAN
            and self.last_codex_output
        ):
            fingerprint = hashlib.sha256(
                (start_commit + "\0" + self.last_codex_output).encode("utf-8")
            ).hexdigest()
            rendered = WorkflowChanges(
                start_commit=start_commit,
                fingerprint=fingerprint,
                paths=(".orchestrator/plan-output.md",),
                full_diff="Persisted Codex plan response:\n" + self.last_codex_output,
            )
        else:
            raise NoWorkflowChangesError(
                "the current review boundary contains no repository changes"
            )
        self._rendered_changes[rendered.fingerprint] = rendered
        return rendered

    def collect_correction_delta(
        self, previous_fingerprint: str, current_fingerprint: str
    ) -> str:
        current = self._rendered_changes.get(current_fingerprint)
        if current is None:
            raise WorkflowExecutionError("current correction fingerprint was not collected")
        return current.full_diff

    def detect_test_changes(
        self, changes: WorkflowChanges, patterns: tuple[str, ...]
    ) -> TestChangeEvidence | None:
        repository_changes = self._repository_changes.get(changes.fingerprint)
        return (
            None
            if repository_changes is None
            else detect_test_changes(repository_changes, patterns)
        )

    def validate(self, changes: WorkflowChanges, request) -> object:
        self.assert_structured_decision_context()
        started = time.monotonic()
        logger.info(
            "Validation matrix starting: commands=%s attempt=%s",
            len(request.commands),
            request.attempt_number,
        )
        attestation = run_validation_matrix(config=self.config, request=request)
        logger.info(
            "Validation matrix finished: status=%s elapsed=%.2fs summary=%s",
            attestation.status.value,
            time.monotonic() - started,
            attestation.summary,
        )
        return attestation

    def validate_plan(
        self,
        changes: WorkflowChanges,
        *,
        work_plan_path: str | None,
        scope_patterns: tuple[str, ...],
        plan_only: bool,
    ) -> ValidationAttestation:
        if self.active_state is None or not self.active_state.planned_slices:
            raise WorkflowExecutionError(
                "internal plan validation requires a persisted SLICE_PLAN"
            )
        planned_paths = tuple(
            sorted(
                {
                    path
                    for planned in self.active_state.planned_slices
                    for path in planned.scope_paths
                }
            )
        )
        unexpected_planned = tuple(
            path
            for path in planned_paths
            if scope_patterns and not matches_path_patterns(path, scope_patterns)
        )
        if unexpected_planned:
            raise WorkflowExecutionError(
                "internal plan validation found out-of-scope SLICE_PLAN paths: "
                + ", ".join(unexpected_planned)
            )
        actual_paths = tuple(
            path
            for path in changes.paths
            if path != ".orchestrator/plan-output.md"
        )
        unexpected_actual = tuple(
            path
            for path in actual_paths
            if scope_patterns and not matches_path_patterns(path, scope_patterns)
        )
        approved_actual = any(
            self.active_state.current_work_unit.has_gate_approval(
                reason,
                changes.fingerprint,
                unexpected_actual,
            )
            for reason in (
                GateReason.UNEXPECTED_FILE,
                GateReason.QUOTA_RESUME_DIFF,
            )
        )
        if unexpected_actual and not approved_actual:
            raise WorkflowExecutionError(
                "internal plan validation found out-of-scope planning changes: "
                + ", ".join(unexpected_actual)
            )

        command = "internal:slice-plan-contract"
        detail = (
            f"slices={len(self.active_state.planned_slices)}; "
            f"planned_paths={len(planned_paths)}; changed_paths={len(actual_paths)}"
        )
        if plan_only:
            command = "internal:work-plan-contract"
            if len(self.active_state.planned_slices) != 1:
                raise WorkflowExecutionError(
                    "PLAN_ONLY requires exactly one executable plan-artifact Slice"
                )
            if work_plan_path is None or work_plan_path not in planned_paths:
                raise WorkflowExecutionError(
                    "PLAN_ONLY plan does not include WORK_PLAN_PATH"
                )
            if work_plan_path not in actual_paths:
                raise WorkflowExecutionError(
                    "PLAN_ONLY Codex planning must create or update WORK_PLAN_PATH"
                )
            candidate = self.root / work_plan_path
            try:
                resolved_candidate = candidate.resolve()
            except (OSError, RuntimeError, ValueError) as exc:
                raise WorkflowExecutionError(
                    f"WORK_PLAN_PATH cannot be resolved safely: {exc}"
                ) from exc
            if (
                not resolved_candidate.is_relative_to(self.root)
                or resolved_candidate != candidate.absolute()
                or candidate.is_symlink()
                or not candidate.is_file()
            ):
                raise WorkflowExecutionError(
                    "WORK_PLAN_PATH must be a regular non-symlink file"
                )
            try:
                content = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise WorkflowExecutionError(
                    f"WORK_PLAN_PATH is not readable UTF-8: {exc}"
                ) from exc
            if not content.strip():
                raise WorkflowExecutionError("WORK_PLAN_PATH must not be empty")
            try:
                future_slices = extract_implementation_slices(
                    content,
                    plan_stem=Path(work_plan_path).stem,
                )
            except PlanHandoffError as exc:
                raise WorkflowExecutionError(
                    f"WORK_PLAN_PATH cannot produce an IMPLEMENT handoff: {exc}"
                ) from exc
            detail += f"; future_slices={len(future_slices)}; work_plan={work_plan_path}"

        digest = hashlib.sha256(detail.encode("utf-8")).hexdigest()
        return ValidationAttestation(
            attestation_id=f"plan-validation-{changes.fingerprint[:12]}",
            diff_fingerprint=changes.fingerprint,
            expected_commands=(command,),
            records=(ValidationRecord(ValidationStatus.PASS, command, 0, detail),),
            output_digest=digest,
            summary="internal plan contract passed",
            command_specs=(ValidationCommandSpec(argv=(command,)),),
        )

    def prepare_correction(
        self, findings: tuple[FindingRecord, ...]
    ) -> WorkflowCorrectionBoundary:
        if self.active_state is None:
            raise WorkflowExecutionError("correction preparation has no active state")
        identity = inspect_repository(self.root)
        remediation_scope = tuple(
            sorted(
                {
                    path
                    for planned in self.active_state.planned_slices
                    for path in planned.scope_paths
                    if not _is_managed_audit_path(self.active_state, path)
                }
            )
        )
        if not remediation_scope:
            raise WorkflowExecutionError("final correction has no persisted remediation scope")
        scope = remediation_scope
        correction_slice_id = len(self.active_state.slices) + 1
        if self.active_state.audit_report_path is not None:
            correction_doc = managed_correction_slice_report_path(
                self.active_state.audit_report_path, correction_slice_id
            )
            scope = tuple(
                sorted(
                    {
                        *scope,
                        self.active_state.audit_report_path,
                        correction_doc,
                    }
                )
            )
        start = collect_repository_changes(
            self.root,
            identity.head,
            excluded_paths=_bound_task_control_paths(self.root, self.active_state),
        )
        return WorkflowCorrectionBoundary(identity.head, scope, start.fingerprint)

    def commit_slice(self, request: WorkflowCommitRequest) -> str:
        if self.active_state is None:
            raise WorkflowExecutionError("slice commit has no active state")
        self.assert_structured_decision_context()
        state = self.active_state
        current = state.current_slice
        if current.start_commit is None or current.start_fingerprint is None:
            raise WorkflowExecutionError("slice commit has no persisted Git boundary")
        boundary = SliceGitBoundary(
            slice_id=current.slice_id,
            branch=state.branch,
            start_commit=current.start_commit,
            start_fingerprint=current.start_fingerprint,
            scope_paths=current.scope_paths,
            semantic_markdown_paths=tuple(
                sorted(
                    path
                    for path in current.scope_paths
                    if path.startswith("docs/internal/") and path.endswith(".md")
                )
            ),
            excluded_control_paths=_bound_task_control_paths(self.root, state),
        )
        summary = next(
            (
                item.summary
                for item in state.planned_slices
                if item.slice_id == current.slice_id
            ),
            "apply approved correction",
        )
        reviewed_changes = self._repository_changes.get(request.fingerprint)
        if reviewed_changes is None:
            raise WorkflowExecutionError(
                "slice commit has no canonical repository evidence for its fingerprint"
            )
        unexpected_paths = tuple(
            path
            for path in reviewed_changes.paths
            if path not in current.scope_paths
        )
        exact_scope_approval = any(
            state.current_work_unit.has_gate_approval(
                reason,
                request.fingerprint,
                unexpected_paths,
            )
            for reason in (
                GateReason.UNEXPECTED_FILE,
                GateReason.QUOTA_RESUME_DIFF,
            )
        )
        if unexpected_paths and not exact_scope_approval:
            raise WorkflowExecutionError(
                "slice commit has unapproved paths outside its persisted scope"
            )
        head_approval = next(
            (
                decision
                for decision in reversed(
                    state.current_work_unit.gate_decisions
                )
                if decision.approved
                and decision.fingerprint == request.fingerprint
                and decision.paths == (unexpected_paths or reviewed_changes.paths)
                and decision.reason
                in {GateReason.UNEXPECTED_FILE, GateReason.QUOTA_RESUME_DIFF}
            ),
            None,
        )
        identity = inspect_repository(self.root)
        if identity.head != current.start_commit and head_approval is None:
            raise WorkflowCommitApprovalRequired(
                (
                    "HEAD-DRIFT | the Slice HEAD changed after its persisted start; "
                    f"approve the exact reviewed fingerprint {request.fingerprint} "
                    f"and current HEAD {identity.head} before committing"
                ),
                unexpected_paths or reviewed_changes.paths,
            )
        review_result = request.claude_review
        artifact_bridge = self._artifact_bridge
        structured_attestation = None
        approval_records: tuple[ArtifactRecord, ...] = ()
        current_review_record: ArtifactRecord | None = None
        structured_binding: tuple[str, tuple[str, ...]] | None = None
        if artifact_bridge is not None:
            chain = artifact_bridge.store.load_chain()
            structured_attestation = next(
                (
                    item for item in reversed(chain)
                    if item.record_type.value == "validation_attestation"
                    and item.fingerprint.sha256 == request.fingerprint
                    and item.logical_id == request.attestation.attestation_id
                ),
                None,
            )
            approval_records = tuple(
                item
                for item in chain
                if isinstance(item.payload, ReviewPayload)
                and item.payload.verdict == "approved"
                and item.fingerprint.sha256 == request.fingerprint
            )
            current_review_record = next(
                (
                    item for item in reversed(approval_records)
                    if item.payload.work_unit_id
                    == str(state.current_work_unit_id)
                ),
                None,
            )
            if structured_attestation is None or not approval_records:
                raise WorkflowExecutionError(
                    "structured commit binding requires persisted attestation and approvals"
                )
            if structured_attestation.payload != attestation_payload(
                request.attestation
            ):
                raise WorkflowExecutionError(
                    "structured commit attestation record differs from its state-v3 mirror"
                )
            if (
                current_review_record is None
                or not review_payload_matches_result(
                    current_review_record.payload,
                    review_result,
                )
            ):
                raise WorkflowExecutionError(
                    "structured commit review record differs from its state-v3 mirror"
                )
            if (
                not request.attestation.passed
                and current_review_record.payload.red_state_followup_slice
                != request.red_state_followup_slice
            ):
                raise WorkflowExecutionError(
                    "red-state commit lacks its fingerprint-bound review record authorization"
                )
            structured_binding = (
                structured_attestation.record_id,
                tuple(item.record_id for item in approval_records),
            )
        if (artifact_bridge is None) != (structured_binding is None):
            raise WorkflowExecutionError(
                "structured commit binding was not established before the Git transaction"
            )
        result = commit_slice(
            repository_root=self.root,
            boundary=boundary,
            authorization=CommitAuthorization(
                slice_id=request.slice_id,
                diff_fingerprint=request.fingerprint,
                attestation=request.attestation,
                claude_review=review_result,
                findings=request.findings,
                red_state_followup_slice=request.red_state_followup_slice,
                review_record=current_review_record,
                review_work_unit_id=str(state.current_work_unit_id),
                approved_head_commit=(
                    identity.head if head_approval is not None else None
                ),
                approved_external_paths=(
                    unexpected_paths if exact_scope_approval else ()
                ),
            ),
            title=summary,
        )
        if artifact_bridge is not None and structured_binding is not None:
            attestation_record_id, approval_record_ids = structured_binding
            artifact_bridge.append(
                BindingPayload(
                    binding_kind="commit",
                    target=result.commit_hash,
                    attestation_id=attestation_record_id,
                    approval_ids=approval_record_ids,
                ),
                logical_id=f"commit-{request.slice_id}-{result.commit_hash[:12]}",
                idempotency_key=f"commit:{request.slice_id}:{request.fingerprint}",
                fingerprint_sha256=request.fingerprint,
            )
        return result.commit_hash

    def finalize_audit(self, state: WorkflowState) -> str | None:
        if state.audit_report_path is None:
            return None
        self.assert_structured_decision_context()
        return commit_managed_audit_report(
            repository_root=self.root,
            branch=state.branch,
            audit_path=state.audit_report_path,
            excluded_control_paths=_bound_task_control_paths(self.root, state),
        )

    def checkpoint(self, state: WorkflowState, history: WorkflowHistory) -> None:
        # Pure workflow transitions return a new state without the runtime ledger
        # that checkpoint() persisted on the preceding step.  In particular, a
        # correction commit can start the next final-review work unit in the same
        # engine invocation.  Merge the driver-owned ledger before archiving the
        # completed unit, otherwise the audit can bind the new commit to an older
        # (possibly denying) reviewer result.
        if (
            self.active_state is not None
            and self.active_state.run_id == state.run_id
            and self.active_state.runtime_history is not None
        ):
            state = replace(
                state,
                runtime_history=self.active_state.runtime_history,
                bootstrap_checks=self.active_state.bootstrap_checks,
            )
        persisted = replace(
            state,
            runtime_history=_history_payload(state.runtime_history, history),
        )
        try:
            self._bind_artifact_store(persisted)
            self._persist_structured_baseline(persisted)
            self._project_audit(persisted, history)
        except Exception as exc:
            if persisted.effective_protocol_mode is ProtocolMode.STRUCTURED_V2:
                raise WorkflowExecutionError(
                    f"structured audit dual-write mismatch: {exc}"
                ) from exc
            raise
        replacement_run_id = self._replace_existing_run_id
        save_workflow_state(
            self.state_file,
            persisted,
            allowed_roots=self.allowed_roots,
            replace_existing_run_id=replacement_run_id,
        )
        write_workflow_checkpoint(
            self.checkpoint_dir / persisted.run_id,
            persisted,
            allowed_roots=self.allowed_roots,
        )
        self._replace_existing_run_id = None
        self.active_state = persisted

    def _project_audit(self, state: WorkflowState, history: WorkflowHistory) -> None:
        """Write only managed audit blocks when the persisted plan names a target."""
        unit = state.current_work_unit
        structured_replay = None
        if (
            state.protocol_binding is not None
            and state.protocol_binding.mode is ProtocolMode.STRUCTURED_V2
        ):
            try:
                structured_replay = resolve_resume_state(
                    self.root, state
                ).replay_result
            except (ArtifactResumeError, ValueError) as exc:
                raise WorkflowExecutionError(
                    f"structured audit dual-write mismatch: {exc}"
                ) from exc
        if state.audit_report_path is not None:
            task = Path(state.task_file)
            try:
                task_label = task.resolve().relative_to(self.root).as_posix()
            except ValueError:
                task_label = task.name
            document = prepare_managed_overall_document(
                repository_root=self.root,
                audit_path=state.audit_report_path,
                task_name=task.stem,
                task_file=task_label,
                run_id=state.run_id,
                branch=state.branch,
                task_scope=state.task_scope_patterns,
            )
            entries = _overall_audit_entries(state, structured_replay)
            if entries:
                project_overall_audit(document, entries)
                if structured_replay is not None:
                    project_structured_work_plan_audit(
                        document, structured_replay
                    )
        if unit.kind is WorkUnitKind.FINAL_REVIEW:
            return
        # The Slice audit is part of the authorized Slice commit.  Commit and
        # subsequent workflow-binding records are projected into the overall
        # audit only; rewriting the already committed Slice document would leave
        # a foreign dirty path for the final audit transaction.
        if state.current_slice.commit_ref is not None:
            return
        approval: AuthorizedTestChanges | None = None
        if unit.active_test_fingerprint is not None:
            decision = next(
                (
                    item for item in reversed(unit.gate_decisions)
                    if item.approved
                    and item.fingerprint == unit.active_test_fingerprint
                    and item.paths == unit.active_test_paths
                ),
                None,
            )
            if decision is not None:
                approval = AuthorizedTestChanges(
                    approved=True,
                    paths=decision.paths,
                    approved_by=decision.decided_by,
                    rationale=decision.rationale,
                    approved_at=decision.decided_at,
                    diff_fingerprint=decision.fingerprint,
                )
        projection = _audit_projection(
            state,
            unit,
            history,
            approval,
            structured_replay,
        )
        if (
            state.execution_mode == TaskMode.PLAN_ONLY.value
            and state.work_plan_path is not None
        ):
            # Once the reviewed plan has been committed it is immutable input to
            # the generated IMPLEMENT handoff.  Later commit/binding records stay
            # in the consolidated record projection and must not dirty the plan.
            if state.current_slice.commit_ref is not None:
                return
            try:
                document = prepare_managed_work_plan_document(
                    repository_root=self.root,
                    work_plan_path=state.work_plan_path,
                )
            except ValueError as exc:
                logger.debug("Work-plan audit target is not ready: %s", exc)
            else:
                if history.events:
                    project_work_plan_audit(document, projection)
                    if structured_replay is not None:
                        project_structured_work_plan_audit(
                            document, structured_replay
                        )
                return
        if unit.kind is WorkUnitKind.PLAN:
            if state.work_plan_path is not None:
                try:
                    document = prepare_managed_work_plan_document(
                        repository_root=self.root,
                        work_plan_path=state.work_plan_path,
                    )
                except ValueError as exc:
                    logger.debug("Work-plan audit target is not ready: %s", exc)
                else:
                    if history.events:
                        project_work_plan_audit(document, projection)
                        if structured_replay is not None:
                            project_structured_work_plan_audit(
                                document, structured_replay
                            )
                    return
            if not history.events:
                return
            candidates = [Path(state.task_file)]
            if state.work_plan_path is not None:
                candidates.append(self.root / state.work_plan_path)
            candidates.extend(
                self.root / path
                for planned in state.planned_slices
                for path in planned.scope_paths
                if path.startswith("docs/internal/") and path.endswith("work-plan.md")
            )
            for candidate in candidates:
                try:
                    document = validate_managed_work_plan_document(
                        repository_root=self.root, work_plan_path=candidate
                    )
                except ValueError:
                    continue
                project_work_plan_audit(document, projection)
                if structured_replay is not None:
                    project_structured_work_plan_audit(
                        document, structured_replay
                    )
                return
            logger.debug("No prepared work-plan audit target is present in the Slice plan.")
            return
        planned = next(
            (item for item in state.planned_slices if item.slice_id == state.current_slice_id),
            None,
        )
        scope_paths = (
            planned.scope_paths if planned is not None else state.current_slice.scope_paths
        )
        summary = planned.summary if planned is not None else "Abschlusskorrektur"
        candidates = tuple(
            path
            for path in scope_paths
            if path.startswith("docs/internal/")
            and f"-{state.current_slice_id:02d}-" in Path(path).name
            and Path(path).suffix == ".md"
        )
        for path in candidates:
            try:
                document = prepare_managed_slice_document(
                    repository_root=self.root,
                    work_plan_path=state.audit_report_path
                    or state.work_plan_path
                    or "docs/internal/orchestrator-modernization-work-plan.md",
                    slice_id=state.current_slice_id,
                    slice_path=path,
                    title=summary,
                    scope_paths=scope_paths,
                    branch=state.branch,
                )
            except ValueError:
                continue
            project_managed_slice_audit(document, projection)
            if structured_replay is not None:
                project_structured_slice_audit(
                    document,
                    structured_replay,
                )
            return
        logger.debug("No prepared Slice audit target is present for Slice %s.", state.current_slice_id)


def _authorized_test_approval(unit: WorkUnitRecord) -> AuthorizedTestChanges | None:
    if unit.active_test_fingerprint is None:
        return None
    decision = next(
        (
            item
            for item in reversed(unit.gate_decisions)
            if item.approved
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
        approved_by=decision.decided_by,
        rationale=decision.rationale,
        approved_at=decision.decided_at,
        diff_fingerprint=decision.fingerprint,
    )


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
            WorkflowStep.CODEX_FINAL_CORRECTION,
        }
    )
    commit_authorized = (
        unit.kind in {WorkUnitKind.SLICE, WorkUnitKind.CORRECTION}
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
        test_approval=approval or _authorized_test_approval(unit),
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


def _persisted_histories(state: WorkflowState) -> dict[int, WorkflowHistory]:
    raw = state.runtime_history
    if not isinstance(raw, dict):
        return {}
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
    return histories


def _recoverable_final_denial_mirror_gap(
    state: WorkflowState,
    record_reviews: Counter[tuple[object, ...]],
    mirror_reviews: Counter[tuple[object, ...]],
    *,
    chain: tuple[ArtifactRecord, ...] = (),
) -> Counter[tuple[object, ...]]:
    """Recognize only the historical final-denial checkpoint ordering defect."""
    missing_reviews = record_reviews - mirror_reviews
    if not missing_reviews or mirror_reviews - record_reviews:
        return Counter()
    units = {item.work_unit_id: item for item in state.work_units}
    histories = _persisted_histories(state)
    recoverable: Counter[tuple[object, ...]] = Counter()
    for signature, count in missing_reviews.items():
        work_unit_id, _reviewer, fingerprint, verdict, finding_ids = signature
        try:
            numeric_work_unit_id = int(str(work_unit_id))
        except ValueError:
            return Counter()
        reviewed_unit = units.get(numeric_work_unit_id)
        correction_unit = units.get(numeric_work_unit_id + 1)
        reviewed_history = histories.get(numeric_work_unit_id)
        finding_attribution_matches = (
            _historical_correction_attribution_matches(
                chain,
                reviewed_work_unit_id=numeric_work_unit_id,
                correction_work_unit_id=numeric_work_unit_id + 1,
                review_signature=signature,
            )
            if chain
            else (
                correction_unit is not None
                and set(correction_unit.open_findings).issubset(set(finding_ids))
            )
        )
        if (
            count != 1
            or verdict != "denied"
            or reviewed_unit is None
            or reviewed_unit.kind is not WorkUnitKind.FINAL_REVIEW
            or reviewed_unit.status is not WorkUnitStatus.COMPLETED
            or correction_unit is None
            or correction_unit.kind is not WorkUnitKind.CORRECTION
            or not finding_attribution_matches
            or reviewed_history is None
            or not any(
                item.diff_fingerprint == fingerprint
                for item in reviewed_history.attestations
            )
        ):
            return Counter()
        recoverable[signature] += 1
    return recoverable


def _historical_correction_attribution_matches(
    chain: tuple[ArtifactRecord, ...],
    *,
    reviewed_work_unit_id: int,
    correction_work_unit_id: int,
    review_signature: tuple[object, ...],
) -> bool:
    review_records = tuple(
        record
        for record in chain
        if isinstance(record.payload, ReviewPayload)
        and (
            record.payload.work_unit_id,
            record.payload.reviewer.value,
            record.fingerprint.sha256,
            record.payload.verdict,
            record.payload.finding_ids,
        )
        == review_signature
        and record.payload.work_unit_id == str(reviewed_work_unit_id)
    )
    try:
        replay = replay_artifacts(chain, chain[0].run_id)
        attribution = reduce_findings(replay).correction_for(
            correction_work_unit_id
        )
    except ArtifactReplayError:
        return False
    first_rounds = (
        ()
        if attribution is None
        else tuple(
            item for item in attribution.rounds if item.round_number == 1
        )
    )
    if len(review_records) != 1 or len(first_rounds) != 1:
        return False
    review = review_records[0]
    correction_round = first_rounds[0]
    positions = {record.record_id: index for index, record in enumerate(chain)}
    return (
        positions[review.record_id] < positions[correction_round.record_id]
        and bool(correction_round.finding_ids)
        and set(correction_round.finding_ids).issubset(
            set(review.payload.finding_ids)
        )
    )


def _recover_final_review_attestation(
    state: WorkflowState,
    current_history: WorkflowHistory,
) -> WorkflowHistory:
    """Recover the latest prior attestation at a final-review transition.

    Attestations are fingerprint-bound rather than work-unit-bound.  Keeping the
    latest prior fact lets ``_attestation`` reuse it when the branch is unchanged;
    a changed branch still selects and persists a new validation normally.
    """
    if state.current_work_unit.kind is not WorkUnitKind.FINAL_REVIEW:
        return current_history
    carried = current_history.attestations[-1:]
    if not carried:
        histories = _persisted_histories(state)
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
) -> tuple[OverallAuditEntry, ...]:
    histories = _persisted_histories(state)
    entries: list[OverallAuditEntry] = []
    for unit in state.work_units:
        history = histories.get(unit.work_unit_id, WorkflowHistory(unit.work_unit_id))
        planned = next(
            (item for item in state.planned_slices if item.slice_id == unit.slice_id),
            None,
        )
        if unit.kind is WorkUnitKind.PLAN:
            label = "Work Unit %02d – Planung" % unit.work_unit_id
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
            label = "Work Unit %02d – Gesamtreview" % unit.work_unit_id
            summary = "Branchweite Gesamtabnahme durch Codex und Claude"
            scope = tuple(
                sorted({path for item in state.planned_slices for path in item.scope_paths})
            )
        else:
            label = "Work Unit %02d – Slice %02d" % (
                unit.work_unit_id,
                unit.slice_id,
            )
            summary = planned.summary if planned is not None else "Abschlusskorrektur"
            scope = (
                planned.scope_paths
                if planned is not None
                else next(item for item in state.slices if item.slice_id == unit.slice_id).scope_paths
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


def _context(
    *, args: argparse.Namespace, assignment: str, state: WorkflowState
) -> WorkflowContext:
    planned = next(
        (item for item in state.planned_slices if item.slice_id == state.current_slice_id),
        None,
    )
    validation_matrix = args.repo_config.validation
    if validation_matrix.default_command is None and str(args.test_command or "").strip():
        raw_command = str(args.test_command).strip()
        validation_matrix = ValidationMatrix(
            default_command=(
                ValidationCommand(argv=tuple(shlex.split(raw_command)))
                if state.effective_protocol_mode is ProtocolMode.STRUCTURED_V2
                else ValidationCommand(shell_command=raw_command)
            ),
            rules=validation_matrix.rules,
        )
    agents_path = Path(str(args.agents_file)).expanduser().resolve()
    shared_instructions = (
        agents_path.read_text(encoding="utf-8")[:12_000]
        if agents_path.is_file()
        else ""
    )
    effective_assignment = assignment
    if shared_instructions:
        effective_assignment += (
            "\n\nRepository agent instructions (authoritative):\n" + shared_instructions
        )
    effective_assignment += (
        "\n\nOrchestrator execution boundary (authoritative):\n"
        f"- Mode: {state.execution_mode}\n"
        f"- Persisted target branch: {state.target_branch or state.branch}\n"
        "- Do not create, switch, rename, delete, merge, or publish branches.\n"
        "- Do not stage or commit. Git branch and commit transactions belong only to "
        "the orchestrator and the user.\n"
        "- Every emitted SLICE_PLAN path and every workspace change must remain within "
        f"the declared task scope: {', '.join(state.task_scope_patterns) or 'LEGACY'}"
    )
    effective_scope = state.task_scope_patterns
    approved_plan_text: str | None = None
    if (
        state.work_plan_path is not None
        and state.current_work_unit.kind in {WorkUnitKind.SLICE, WorkUnitKind.CORRECTION}
    ):
        repository_root = Path.cwd().resolve()
        plan_path = (repository_root / state.work_plan_path).resolve()
        if not plan_path.is_relative_to(repository_root) or not plan_path.is_file():
            raise WorkflowExecutionError(
                "approved work plan is unavailable inside the repository boundary"
            )
        approved_plan_text = plan_path.read_text(encoding="utf-8")
    if state.audit_report_path is not None:
        effective_scope = tuple(
            sorted(
                {
                    *effective_scope,
                    state.audit_report_path,
                    _managed_slice_scope_pattern(state.audit_report_path),
                }
            )
        )
        effective_assignment += (
            "\n- The orchestrator owns the consolidated audit report and managed audit "
            "blocks below docs/internal. Do not edit managed audit blocks. A Slice report "
            "may be updated only after the orchestrator creates it at implementation start."
        )
    effective_assignment += _plan_only_step_boundary(state)
    planned_scope = set(planned.scope_paths) if planned is not None else set()
    active_remediation_paths = tuple(
        sorted(set(state.current_slice.scope_paths).difference(planned_scope))
    )
    if planned is not None:
        slice_summary = planned.summary
    elif (
        state.execution_mode == TaskMode.PLAN_ONLY.value
        and state.current_work_unit.kind is WorkUnitKind.PLAN
    ):
        slice_summary = (
            f"Create the executable work-plan artifact now at {state.work_plan_path}; "
            "do not merely describe the planned work in the native JSON result."
        )
    else:
        slice_summary = "Plan the requested work."
    if active_remediation_paths:
        rendered_remediation_paths = ", ".join(active_remediation_paths)
        effective_assignment += (
            "\n\nACTIVE REMEDIATION SCOPE (authoritative):\n"
            f"- These paths are already authorized for the current Slice: "
            f"{rendered_remediation_paths}\n"
            "- They were added by the orchestrator from a completed, approved prior "
            "Slice. Treat them as part of the exact current allowlist.\n"
            "- Do not emit STOP_REQUESTED or REMEDIATION_PATHS merely because these "
            "paths were absent from the original plan. Implement the required repair "
            "within them."
        )
        slice_summary += (
            "\n\nACTIVE REMEDIATION SCOPE — ALREADY AUTHORIZED\n"
            f"{rendered_remediation_paths}\n"
            "Use these paths when needed; do not request them again."
        )
    return WorkflowContext(
        assignment=effective_assignment,
        distilled_plan=(
            "Follow the ordered, persisted slice plan and exact path allowlists."
        ),
        slice_summary=slice_summary,
        test_changes_approved=not bool(getattr(args, "test_change_gate", False)),
        manual_slice_gate=bool(args.manual_slice_gate),
        path_classes=args.repo_config.paths,
        stop_rules=args.repo_config.stop_rules,
        current_branch=inspect_repository(Path.cwd()).branch,
        validation_matrix=validation_matrix,
        retry_incomplete_validation=bool(args.retry_incomplete_validation),
        retry_failed_validation=bool(args.retry_failed_validation),
        quota_wait_policy=args.quota_wait_policy,
        transient_retry_policy=args.transient_retry_policy,
        require_slice_plan=state.current_work_unit.kind is WorkUnitKind.PLAN,
        dynamic_test_scope=True,
        plan_gate=bool(getattr(args, "plan_gate", False)),
        plan_only=state.execution_mode == TaskMode.PLAN_ONLY.value,
        task_scope_patterns=effective_scope,
        work_plan_path=state.work_plan_path,
        approved_plan_text=approved_plan_text,
        audit_report_path=state.audit_report_path,
        current_scope_paths=state.current_slice.scope_paths,
    )


def _plan_only_step_boundary(state: WorkflowState) -> str:
    """Render PLAN_ONLY instructions that agree with the current step contract."""
    if state.execution_mode != TaskMode.PLAN_ONLY.value:
        return ""
    if state.current_work_unit.kind is WorkUnitKind.PLAN:
        step_rule = (
            f"- Create or update the file {state.work_plan_path} in the repository now. "
            "Its complete content is the deliverable of this step.\n"
            "- Before returning `ready: true`, reread that file and verify that it "
            "exists, is non-empty, and contains the executable work plan.\n"
            "- Then emit exactly one executable PLAN_ONLY SLICE_PLAN record for that "
            "artifact. The native JSON record is only a receipt for the written file; "
            "it does not contain or replace the work-plan document.\n"
            "- If you cannot write and verify the work-plan file, do not return "
            "`ready: true`; return the typed stop result instead.\n"
        )
    else:
        scope = ", ".join(state.current_slice.scope_paths)
        step_rule = (
            "- PLAN_ONLY implementation step: do not emit a SLICE_PLAN record. The "
            "executable Slice is already persisted.\n"
            f"- Modify only its persisted artifact scope: {scope}.\n"
        )
    return (
        "\n"
        + step_rule
        + "- Future product implementation Slices belong only as human-readable "
        "sections inside the work-plan document; do not emit them as executable "
        "SLICE_PLAN records in this run.\n"
        "- Do not modify product code, tests, configuration, or generated artifacts. "
        "The declared work-plan document is the one repository file you must write."
    )


def _history(state: WorkflowState) -> WorkflowHistory:
    if state.runtime_history is None:
        return WorkflowHistory(state.current_work_unit_id)
    try:
        raw = dict(state.runtime_history)
        current = raw.get("current") if set(raw) == {"current", "archive"} else raw
        history = WorkflowHistory.from_dict(current)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowExecutionError(
            f"persisted workflow history is invalid: {exc}"
        ) from exc
    if history.work_unit_id != state.current_work_unit_id:
        return WorkflowHistory(state.current_work_unit_id)
    return history


def _bound_task_control_paths(
    repository_root: Path,
    state: WorkflowState | None,
) -> tuple[str, ...]:
    """Exclude an unchanged in-repository task file from product evidence."""
    if state is None or state.task_digest is None:
        return ()
    root = repository_root.resolve()
    task = Path(state.task_file).resolve()
    if not task.is_relative_to(root):
        return ()
    try:
        # The persisted task contract is built from ``Path.read_text()``.  Use
        # the same universal-newline decoding here so a CRLF task file does
        # not look modified merely because its text digest contains LF.
        task_text = task.read_text(encoding="utf-8")
        digest = hashlib.sha256(task_text.encode("utf-8")).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise WorkflowExecutionError(f"bound task file is unreadable: {exc}") from exc
    if digest != state.task_digest:
        raise WorkflowExecutionError(
            "bound task file changed during execution; start a new run with a new task digest"
        )
    return (task.relative_to(root).as_posix(),)


def _final_review_audit_evidence_summary(
    changes: RepositoryChanges,
    *,
    audit_path: str,
    omitted_diff_chars: int,
) -> str:
    """Bind a generated audit projection without repeating its full diff.

    The canonical ``RepositoryChanges`` object remains the source for the complete
    branch fingerprint, path boundary, test detection, and validation attestation.
    Only the agent-facing evidence substitutes this deterministic projection with
    its already-captured semantic payload metadata.
    """
    entries = tuple(
        entry for entry in changes.fingerprint_entries if entry.path == audit_path
    )
    if len(entries) != 1:
        raise WorkflowExecutionError(
            "final review audit compaction requires exactly one fingerprint entry "
            f"for {audit_path!r}"
        )
    entry = entries[0]
    return "\n".join(
        (
            "=== DETERMINISTIC AUDIT PROJECTION (COMPACT EVIDENCE) ===",
            f"path: {audit_path}",
            f"change_kind: {entry.kind}",
            f"semantic_payload_type: {entry.payload_type or 'none'}",
            f"semantic_payload_size: {entry.payload_size if entry.payload_size is not None else 'none'}",
            f"semantic_payload_sha256: {entry.payload_digest or 'none'}",
            f"omitted_full_diff_chars: {omitted_diff_chars}",
            "reason: The full managed audit projection is deterministic and repeats "
            "structured findings, approvals, and validation attestations supplied "
            "separately. Its canonical semantic payload remains bound to the complete "
            "branch fingerprint and validation boundary.",
            "=== END COMPACT AUDIT EVIDENCE ===",
        )
    )


def _new_watch_task_control_paths(
    repository_root: Path,
    task_file: Path,
) -> tuple[str, ...]:
    """Return unpersisted Watch control paths that must survive branch setup."""
    root = repository_root.resolve()
    candidates = (
        task_file.resolve(),
        watch_identity_path(task_file).resolve(),
        attempt_sidecar_path(task_file).resolve(),
        success_marker_path(task_file).resolve(),
    )
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in candidates
            if path.is_relative_to(root)
        )
    )


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


def _new_watch_task_preserved_paths(
    repository_root: Path,
    task_contract: TaskContract,
) -> tuple[str, ...]:
    """Carry an existing untracked PLAN_ONLY artifact onto its target branch."""
    if (
        task_contract.mode is not TaskMode.PLAN_ONLY
        or task_contract.work_plan_path is None
    ):
        return ()
    candidate = repository_root / task_contract.work_plan_path
    if not candidate.exists() and not candidate.is_symlink():
        return ()
    return (task_contract.work_plan_path,)


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


def _history_payload(
    existing: object, current: WorkflowHistory
) -> dict[str, object]:
    archive: list[object] = []
    previous: object | None = None
    if isinstance(existing, dict):
        if set(existing) == {"current", "archive"}:
            raw_archive = existing.get("archive")
            if isinstance(raw_archive, list):
                archive = list(raw_archive)
            previous = existing.get("current")
        else:
            previous = existing
    if isinstance(previous, dict):
        previous_id = previous.get("work_unit_id")
        archived_ids = {
            item.get("work_unit_id")
            for item in archive
            if isinstance(item, dict)
        }
        if previous_id != current.work_unit_id and previous_id not in archived_ids:
            archive.append(previous)
    return {"current": current.to_dict(), "archive": archive}


def _fresh_state(
    *,
    task_file: Path,
    run_id: str,
    repository_root: Path,
    task_contract: TaskContract,
    branch_base_override: str | None = None,
    audit_report_path: str | None = None,
    codex_profile: AgentProfileBinding = AgentProfileBinding("gpt-5.6-sol", "medium"),
    claude_profile: AgentProfileBinding = AgentProfileBinding("sonnet", "high"),
) -> WorkflowState:
    identity = inspect_repository(repository_root)
    if identity.branch != task_contract.target_branch:
        raise StateSchemaError(
            "TARGET_BRANCH mismatch: task requires "
            f"{task_contract.target_branch!r}, active branch is {identity.branch!r}; "
            "create/switch the branch before starting the orchestrator"
        )
    if branch_base_override is not None and branch_base_override != identity.head:
        raise StateSchemaError(
            "prepared watch-task branch HEAD changed before state initialization"
        )
    if branch_base_override is not None:
        branch_base = branch_base_override
    elif task_contract.approved_plan_commit is not None:
        branch_base = identity.head
    else:
        branch_base = resolve_merge_base(repository_root).commit
    state = init_workflow_state(
        run_id=run_id,
        task_file=str(task_file.resolve()),
        branch=identity.branch,
        branch_base=branch_base,
        first_slice_start_commit=identity.head,
        slice_count=1,
        task_digest=task_contract.digest,
        execution_mode=task_contract.mode.value,
        task_scope_patterns=task_contract.scope_patterns,
        work_plan_path=task_contract.work_plan_path,
        approved_plan_commit=task_contract.approved_plan_commit,
        finding_handoff_source_run_id=task_contract.finding_handoff_source_run_id,
        finding_handoff_export_record_id=task_contract.finding_handoff_export_record_id,
        audit_report_path=audit_report_path,
        target_branch=task_contract.target_branch,
        protocol_binding=ProtocolBinding(
            mode=ProtocolMode.STRUCTURED_V2,
            schema_version="2",
            claude_review_transport=NATIVE_CLAUDE_REVIEW_TRANSPORT,
            codex_result_transport=NATIVE_CODEX_RESULT_TRANSPORT,
            codex_profile=codex_profile,
            claude_profile=claude_profile,
        ),
    )
    if task_contract.approved_plan_commit is not None:
        assert task_contract.work_plan_path is not None
        require_committed_file_at_head(
            repository_root,
            expected_commit=task_contract.approved_plan_commit,
            relative_path=task_contract.work_plan_path,
        )
        state = state.bind_slice_plan(
            task_contract.approved_slices,
            first_start_commit=identity.head,
        ).complete_current_work_unit()
        state = state.start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,
        )
    return state


def _initialize_finding_handoff(
    repository_root: Path,
    state: WorkflowState,
    task_contract: TaskContract,
    task_bytes: bytes,
) -> WorkflowState:
    """Import foreign finding authority before the first ordinary checkpoint."""
    source_run_id = task_contract.finding_handoff_source_run_id
    export_record_id = task_contract.finding_handoff_export_record_id
    if source_run_id is None or export_record_id is None:
        return state
    try:
        source_chain = ArtifactStore(repository_root, source_run_id).load_chain()
        source_replay = replay_artifacts(source_chain, source_run_id)
        export_record = next(
            (record for record in source_replay.records if record.record_id == export_record_id),
            None,
        )
        if export_record is None:
            raise ArtifactBridgeError("referenced finding export record is missing")
        export_payload = export_record.payload
        if (
            not isinstance(export_payload, FindingHandoffExportPayload)
            or export_payload.approved_plan_commit != task_contract.approved_plan_commit
        ):
            raise ArtifactBridgeError("finding export plan commit differs from the task")
        payload = finding_handoff_import_payload(
            source_replay,
            export_record,
            target_run_id=state.run_id,
            target_task_bytes=task_bytes,
        )
        bridge = ArtifactBridge(ArtifactStore(repository_root, state.run_id))
        imported = bridge.append(
            payload,
            logical_id="finding-handoff-import",
            idempotency_key=f"finding-handoff-import:{source_run_id}:{export_record_id}",
            fingerprint_sha256=task_contract.digest,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
        local_replay = replay_artifacts(bridge.store.load_chain(), state.run_id)
        reduction = reduce_findings(local_replay)
        findings = reduction.ledger.findings
    except (ArtifactBridgeError, ArtifactReplayError, ValueError) as exc:
        raise StateSchemaError(f"FINDING-HANDOFF-INVALID: {exc}") from exc
    open_ids = reduction.open_set.finding_ids
    current = replace(state.current_work_unit, open_findings=open_ids)
    units = tuple(
        current if unit.work_unit_id == current.work_unit_id else unit
        for unit in state.work_units
    )
    history = WorkflowHistory(state.current_work_unit_id, findings=findings)
    return replace(
        state,
        work_units=units,
        runtime_history=_history_payload(None, history),
    )


def _apply_resumed_agent_profiles(
    args: argparse.Namespace, state: WorkflowState
) -> None:
    """Use persisted profiles unless the caller explicitly requested an equal value."""
    binding = state.protocol_binding
    if binding is None:
        raise StateSchemaError("structured resume requires persisted agent profiles")
    explicit = set(getattr(args, "agent_profile_overrides", ()))
    settings = dict(args.agent_settings)
    for role, profile in (
        ("codex", binding.codex_profile),
        ("claude", binding.claude_profile),
    ):
        current = settings[role]
        for field in ("model", "effort"):
            if (role, field) in explicit and getattr(current, field) != getattr(profile, field):
                raise StateSchemaError(
                    "AGENT-PROFILE-DIFF | explicit "
                    f"{role} {field} differs from the immutable persisted profile"
                )
        settings[role] = replace(
            current,
            model=profile.model,
            effort=profile.effort,
        )
    args.agent_settings = settings


def _unused_run_id(repository_root: Path, proposed: str) -> str:
    """Avoid cross-task record reuse when two runs start in the same second."""
    control_root = repository_root / ".orchestrator"
    for suffix in range(1000):
        candidate = proposed if suffix == 0 else f"{proposed}-{suffix:03d}"
        if not any(
            (control_root / area / candidate).exists()
            for area in ("artifacts", "runs", "logs", "checkpoints")
        ):
            return candidate
    raise WorkflowExecutionError("could not allocate a unique structured run id")


def run_production_workflow(
    task_file: Path,
    args: argparse.Namespace,
    *,
    force_new: bool = False,
) -> WorkflowRunResult:
    root = Path.cwd().resolve()
    state_file = root / ".orchestrator" / "state.json"
    task_file = task_file.resolve()
    assignment = task_file.read_text(encoding="utf-8")
    task_contract = parse_task_contract(
        assignment,
        mode_override=getattr(args, "plan_only", None),
        work_plan_override=getattr(args, "work_plan", None),
        target_branch_override=getattr(args, "target_branch", None),
        source_name=task_file.name,
    )
    if task_contract.informal_intake:
        logger.info(
            "Informal inbox intake: derived mode=PLAN_ONLY work_plan=%s scope=%s",
            task_contract.work_plan_path,
            ",".join(task_contract.scope_patterns),
        )
        assignment += (
            "\n\nINFORMAL INTAKE (orchestrator-derived, authoritative):\n"
            "- The text above is the user's idea, not a detailed implementation contract.\n"
            "- Translate it into a repository-grounded executable work plan. Do not ask "
            "the user to supply paths, Slices, acceptance criteria, risks, or validation "
            "bookkeeping that can be determined from the repository.\n"
            f"- Derived mode: PLAN_ONLY\n"
            f"- Derived work-plan artifact: {task_contract.work_plan_path}\n"
            f"- Derived exact planning scope: {', '.join(task_contract.scope_patterns)}\n"
            "- Stop only for a genuine product choice with materially different outcomes, "
            "missing authority, secrets, or destructive action."
        )
    allowed_roots = tuple(dict.fromkeys((root, task_file.parent.resolve())))
    requested_run_id = str(getattr(args, "watch_run_id", ""))
    run_id = requested_run_id or _unused_run_id(root, new_run_id())
    managed_audit_path = (
        _managed_audit_path(task_file, task_contract.digest)
        if task_contract.mode is TaskMode.IMPLEMENT
        and task_file.parent.name.casefold() == "inbox"
        else None
    )

    watch_run = bool(getattr(args, "watch_run_id", None))
    new_watch_task = watch_run and (force_new or not state_file.exists())
    prepared_branch_base: str | None = None
    if new_watch_task:
        if managed_audit_path is not None:
            configured_outbox = Path(getattr(args, "outbox_dir", "outbox"))
            if not configured_outbox.is_absolute():
                configured_outbox = root / configured_outbox
            _archive_stale_untracked_audit_reports(
                root,
                current_audit_path=managed_audit_path,
                outbox_failed_dir=configured_outbox / "failed",
            )
        prepared = prepare_new_watch_task_branch(
            root,
            target_branch=task_contract.target_branch,
            excluded_control_paths=_new_watch_task_control_paths(root, task_file),
            preserved_task_paths=_new_watch_task_preserved_paths(root, task_contract),
        )
        prepared_branch_base = prepared.identity.head
        logger.info(
            "Watch target branch ready: action=%s previous=%s target=%s head=%s",
            prepared.action,
            prepared.previous_branch,
            prepared.identity.branch,
            prepared.identity.head[:12],
        )

    loaded: WorkflowState | CompletedV2State | None = None
    replacement_run_id: str | None = None
    replacement_requested = force_new or (
        bool(args.force_overwrite_state) and not bool(args.resume)
    )
    if state_file.exists() and replacement_requested:
        existing = load_workflow_state(state_file, allowed_roots=allowed_roots)
        if isinstance(existing, WorkflowState):
            replacement_run_id = existing.run_id
    elif state_file.exists():
        try:
            loaded = load_resumable_workflow_state(
                state_file,
                repository_root=root,
                allowed_roots=allowed_roots,
            )
        except ActiveV2StateError:
            if args.force_overwrite_state:
                loaded = None
            else:
                raise
    effective_resume = bool(args.resume and not new_watch_task)
    if effective_resume:
        if isinstance(loaded, CompletedV2State):
            raise StateSchemaError(
                "completed version-2 state cannot be resumed; start a new v3 run"
            )
        if loaded is None:
            raise StateSchemaError("--resume requested but no version-3 state exists")
        state = loaded
        if state.task_file != str(task_file):
            raise StateSchemaError("persisted task identity differs from --resume task")
        if state.task_digest is None:
            raise StateSchemaError(
                "persisted state predates the hardened task contract; start a new run "
                "with --no-resume --force-overwrite-state"
            )
        if state.task_digest != task_contract.digest:
            raise StateSchemaError(
                "task content changed since the persisted run was created"
            )
        if (
            state.execution_mode != task_contract.mode.value
            or state.task_scope_patterns != task_contract.scope_patterns
            or state.work_plan_path != task_contract.work_plan_path
            or state.target_branch != task_contract.target_branch
            or state.finding_handoff_source_run_id
            != task_contract.finding_handoff_source_run_id
            or state.finding_handoff_export_record_id
            != task_contract.finding_handoff_export_record_id
        ):
            raise StateSchemaError("persisted task contract differs from --resume task")
        if getattr(args, "watch_run_id", None) and state.run_id != args.watch_run_id:
            raise StateSchemaError("persisted watch run identity differs from inbox task")
        if state.audit_report_path is None and managed_audit_path is not None:
            state = replace(state, audit_report_path=managed_audit_path)
        _apply_resumed_agent_profiles(args, state)
    else:
        if loaded is not None and not args.force_overwrite_state and not force_new:
            raise StateSchemaError(
                "existing state requires --resume or --force-overwrite-state"
            )
        state = _fresh_state(
            task_file=task_file,
            run_id=run_id,
            repository_root=root,
            task_contract=task_contract,
            branch_base_override=prepared_branch_base,
            audit_report_path=managed_audit_path,
            codex_profile=AgentProfileBinding(
                args.agent_settings["codex"].model,
                args.agent_settings["codex"].effort,
            ),
            claude_profile=AgentProfileBinding(
                args.agent_settings["claude"].model,
                args.agent_settings["claude"].effort,
            ),
        )
        state = _initialize_finding_handoff(
            root, state, task_contract, task_file.read_bytes()
        )
    state = _attach_managed_audit_paths(state)
    state = _recover_legacy_plan_only_post_gate(state)
    state = state.reopen_legacy_quota_resume_diff_gate()
    config = OrchestratorConfig(
        dry_run=False,
        agent_output_mode=args.agent_output,
        agent_output_max_chars=args.agent_output_max_chars,
        agent_live_stream=bool(args.agent_live_stream),
        agent_live_stream_mode=args.agent_live_stream_mode,
        agent_live_stream_channels=args.agent_live_stream_channels,
        repo_root=root,
        strict_preflight=bool(args.strict_preflight),
        provider_input_budget=args.repo_config.provider_input_budget,
    )
    driver = ProductionWorkflowDriver(
        repository_root=root,
        state_file=state_file,
        agents=build_agent_registry(args.agent_settings),
        config=config,
        allowed_roots=allowed_roots,
        replace_existing_run_id=replacement_run_id,
    )
    engine = WorkflowEngine(driver)
    driver.checkpoint(state, _history(state))

    for _ in range(100):
        history = _history(state)
        current = state.current_work_unit
        recovered_history = _recover_final_review_attestation(state, history)
        if recovered_history != history:
            history = recovered_history
            driver.checkpoint(state, history)
            state = driver.active_state or state
            current = state.current_work_unit
        if current.status in {
            WorkUnitStatus.WAITING_FOR_QUOTA,
            WorkUnitStatus.WAITING_FOR_RETRY,
            WorkUnitStatus.AWAITING_RESUME,
        }:
            if not effective_resume:
                return WorkflowRunResult(state, history)
            state = state.resume_after_invocation_halt()
            driver.checkpoint(state, history)
        elif current.status is WorkUnitStatus.AWAITING_USER_DECISION:
            reframed = engine.reframe_unexpected_path_stop_gate(state)
            if reframed != state:
                state = reframed
                driver.checkpoint(state, history)
                current = state.current_work_unit
                if current.status is WorkUnitStatus.IN_PROGRESS:
                    continue
            inherited = _inherit_redundant_test_gate(state)
            if inherited != state:
                state = inherited
                driver.checkpoint(state, history)
            elif (existing_approval := _current_gate_approval(state)) is not None:
                decided = engine.decide_current_gate(
                    state,
                    history,
                    approved=True,
                    decided_by=existing_approval.decided_by,
                    decided_at=existing_approval.decided_at,
                    rationale=existing_approval.rationale,
                )
                state, history = decided.state, decided.history
            elif args.gate_decision is not None:
                decided = engine.decide_current_gate(
                    state,
                    history,
                    approved=args.gate_decision,
                    decided_by=args.gate_actor,
                    decided_at=state.updated_at,
                    rationale=args.gate_rationale,
                )
                state, history = decided.state, decided.history
                if not args.gate_decision:
                    return decided
            elif (
                effective_resume
                and not args.auto_resume
                and current.gate.fingerprint is None
            ):
                state = state.resume_after_user_decision()
                driver.checkpoint(state, history)
            else:
                return WorkflowRunResult(state, history)

        current = state.current_work_unit
        if (
            current.status is WorkUnitStatus.IN_PROGRESS
            and current.kind is WorkUnitKind.SLICE
            and not state.current_slice.scope_paths
        ):
            identity = inspect_repository(root)
            expected_head = state.current_slice.start_commit
            if expected_head is None:
                raise WorkflowExecutionError(
                    "unbound Slice has no persisted start commit"
                )
            if identity.head != expected_head:
                state = state.await_policy_gate(
                    reason=GateReason.UNEXPECTED_FILE,
                    detail=(
                        "SLICE-HEAD-DRIFT | repository HEAD changed after the "
                        f"Slice boundary was planned: expected {expected_head}, "
                        f"found {identity.head}"
                    ),
                )
                driver.checkpoint(state, history)
                return WorkflowRunResult(state, history)
            planned = state.planned_slices[state.current_slice_id - 1]
            start = collect_repository_changes(
                root,
                expected_head,
                excluded_paths=_bound_task_control_paths(root, state),
            )
            state = state.bind_current_slice_git_boundary(
                start_commit=expected_head,
                scope_paths=planned.scope_paths,
                start_fingerprint=start.fingerprint,
            )
            driver.checkpoint(state, history)
            current = state.current_work_unit
        if current.status is WorkUnitStatus.IN_PROGRESS:
            result = engine.run_current_work_unit(
                state, _context(args=args, assignment=assignment, state=state), history
            )
            state = driver.active_state or result.state
            if not result.completed:
                return WorkflowRunResult(state, result.history, result.commit_ref)
            history = result.history
            current = state.current_work_unit

        if current.kind is WorkUnitKind.FINAL_REVIEW:
            audit_commit = driver.finalize_audit(state)
            return WorkflowRunResult(state, _history(state), audit_commit)

        if current.kind is WorkUnitKind.PLAN:
            if state.execution_mode == TaskMode.PLAN_ONLY.value:
                commit_ref = state.current_slice.commit_ref
                if commit_ref is None:
                    raise WorkflowExecutionError(
                        "completed PLAN_ONLY run has no reviewed plan commit"
                    )
                try:
                    recovered = state.bind_completed_plan_commit(commit_ref=commit_ref)
                except WorkflowStateValidationError as exc:
                    raise WorkflowExecutionError(
                        f"could not bind completed PLAN_ONLY commit: {exc}"
                    ) from exc
                if recovered is not state:
                    logger.warning(
                        "Backfilling the approved-plan state and structured record "
                        "for a completed PLAN_ONLY run before IMPLEMENT handoff."
                    )
                    state = recovered
                    driver.checkpoint(state, history)
                    state = driver.active_state or state
                driver.assert_structured_decision_context()
                try:
                    finding_handoff = driver.prepare_finding_handoff(
                        plan_task_path=task_file,
                        work_plan_path=state.work_plan_path or "",
                        target_branch=state.target_branch or state.branch,
                        approved_plan_commit=commit_ref,
                    )
                    handoff = write_implementation_handoff(
                        plan_task_path=task_file,
                        repository_root=root,
                        work_plan_path=state.work_plan_path or "",
                        target_branch=state.target_branch or state.branch,
                        approved_plan_commit=commit_ref,
                        finding_handoff=finding_handoff,
                    )
                except (ArtifactBridgeError, ArtifactReplayError, PlanHandoffError) as exc:
                    raise WorkflowExecutionError(
                        f"could not create IMPLEMENT handoff: {exc}"
                    ) from exc
                driver.persist_implementation_handoff(handoff, commit_ref)
                logger.info("Implementation handoff ready: %s", handoff)
                return WorkflowRunResult(state, _history(state), commit_ref)
            if not state.planned_slices:
                raise WorkflowExecutionError("completed plan has no persisted SLICE_PLAN")
            carried_findings = driver.carry_forward_native_findings(
                state, history.findings
            )
            state = state.start_work_unit(
                slice_id=1,
                kind=WorkUnitKind.SLICE,
                step=WorkflowStep.CODEX_IMPLEMENTATION,
            )
            driver.checkpoint(
                state,
                WorkflowHistory(
                    state.current_work_unit_id,
                    findings=carried_findings,
                ),
            )
            state = driver.active_state or state
            continue

        pending = next(
            (item for item in state.slices if item.status is SliceStatus.PENDING), None
        )
        if pending is not None:
            identity = inspect_repository(root)
            carried_findings = driver.carry_forward_native_findings(
                state, history.findings
            )
            state = state.start_work_unit(
                slice_id=pending.slice_id,
                kind=WorkUnitKind.SLICE,
                step=WorkflowStep.CODEX_IMPLEMENTATION,
                slice_start_commit=identity.head,
            )
            driver.checkpoint(
                state,
                WorkflowHistory(
                    state.current_work_unit_id,
                    findings=carried_findings,
                ),
            )
            state = driver.active_state or state
            continue

        carried_findings = driver.carry_forward_native_findings(
            state, history.findings
        )
        state = state.start_final_review_work_unit()
        carried_attestations = history.attestations[-1:]
        driver.checkpoint(
            state,
            WorkflowHistory(
                state.current_work_unit_id,
                findings=carried_findings,
                events=(
                    (
                        ValidationAuditEvent(
                            event_id=1,
                            slice_id=state.current_slice_id,
                            attestation=carried_attestations[0],
                        ),
                    )
                    if carried_attestations
                    else ()
                ),
                attestations=carried_attestations,
            ),
        )
        state = driver.active_state or state

    raise WorkflowExecutionError("workflow session exceeded its deterministic transition bound")


def _inherit_redundant_test_gate(state: WorkflowState) -> WorkflowState:
    """Clear a repeated exact test gate before the production loop returns early."""
    current = state.current_work_unit
    gate = current.gate
    if (
        current.status is not WorkUnitStatus.AWAITING_USER_DECISION
        or gate.reason is not GateReason.TEST_CHANGE
        or gate.fingerprint is None
    ):
        return state
    return state.inherit_prior_test_approval(gate.fingerprint, gate.paths)


def _current_gate_approval(state: WorkflowState) -> GateDecisionRecord | None:
    """Return an exact immutable approval when the same gate was reopened."""
    current = state.current_work_unit
    gate = current.gate
    if (
        current.status is not WorkUnitStatus.AWAITING_USER_DECISION
        or gate.fingerprint is None
    ):
        return None
    return next(
        (
            decision
            for decision in reversed(current.gate_decisions)
            if decision.approved
            and decision.reason is gate.reason
            and decision.fingerprint == gate.fingerprint
            and decision.paths == gate.paths
            and decision.resume_step is gate.resume_step
        ),
        None,
    )


def _recover_legacy_plan_only_post_gate(state: WorkflowState) -> WorkflowState:
    """Collapse the obsolete post-gate plan-artifact Slice without editing state files."""
    if (
        state.execution_mode != TaskMode.PLAN_ONLY.value
        or state.current_work_unit.kind is not WorkUnitKind.SLICE
        or state.current_step is not WorkflowStep.CODEX_IMPLEMENTATION
        or state.current_work_unit_id != 2
        or len(state.work_units) != 2
    ):
        return state
    plan_unit = state.work_units[0]
    if (
        plan_unit.kind is not WorkUnitKind.PLAN
        or plan_unit.status is not WorkUnitStatus.COMPLETED
        or not any(
            decision.approved and decision.reason is GateReason.PLAN_APPROVAL
            for decision in plan_unit.gate_decisions
        )
    ):
        return state
    runtime = state.runtime_history
    if not isinstance(runtime, dict):
        return state
    archive = runtime.get("archive")
    if not isinstance(archive, list):
        return state
    plan_history = next(
        (
            item
            for item in archive
            if isinstance(item, dict) and item.get("work_unit_id") == 1
        ),
        None,
    )
    current_history = runtime.get("current")
    if plan_history is None or not isinstance(current_history, dict):
        return state
    if current_history.get("events") or current_history.get("findings"):
        return state
    restored_unit = replace(
        plan_unit,
        status=WorkUnitStatus.IN_PROGRESS,
        current_step=WorkflowStep.SLICE_COMMIT,
    )
    logger.info(
        "Recovering legacy PLAN_ONLY post-gate state; committing the already reviewed plan directly."
    )
    return replace(
        state,
        current_work_unit_id=1,
        current_step=WorkflowStep.SLICE_COMMIT,
        work_units=(restored_unit,),
        runtime_history={"current": plan_history, "archive": []},
    )


def run_default_dry_run(task_file: Path, *, run_id: str | None = None):
    """Exercise plan, two commits, and final review without agents or repository writes."""
    from dry_run_scenarios import (
        DryRunScenario,
        ScriptedAgentEvent,
        ScriptedChange,
        ScriptedCommit,
        ScriptedInitialState,
        ScriptedValidation,
        ScriptedWorkflowSession,
        build_scenario_context,
        build_scenario_state,
    )

    base = "a" * 40
    commit1 = "b" * 40
    commit2 = "c" * 40
    plan_fp, first_fp, second_fp, final_fp = (value * 64 for value in "1234")

    def review() -> dict[str, object]:
        return {
            "schema_version": "native-agent-review-result-v2",
            "result_type": "review_result",
            "request_id": "$BOUND_REQUEST_ID",
            "reviewer": "claude",
            "decision": "approved",
            "new_findings": [],
            "status_changes": [],
            "reclassifications": [],
            "anchors": [],
            "review_evidence": {
                "dimensions": "correctness, contracts, failure paths, security, resume",
                "largest_residual_risk": "runtime drift",
                "break_condition": "a provider changes its output envelope",
            },
            "pre_mortem": "A resumed invocation uses stale evidence.",
        }

    def codex_result(result_type: str, **fields: object) -> dict[str, object]:
        return {
            "schema_version": "native-agent-codex-result-v2",
            "request_id": "$BOUND_REQUEST_ID",
            "result_type": result_type,
            "ready": True,
            "finding_dispositions": [],
            **fields,
        }

    scenario = DryRunScenario(
        name="default-v3-cutover",
        initial=ScriptedInitialState(
            kind=WorkUnitKind.PLAN,
            slice_count=2,
            scope_paths=("docs/internal/plan.md", "src/first.py", "src/second.py"),
        ),
        agent_events=(
            ScriptedAgentEvent(AgentRole.CODEX, 1, 1, WorkflowStep.CODEX_PLAN,
                               codex_result("plan_result", slice_plan=[
                                   {
                                       "slice_id": 1,
                                       "summary": "Execute the first native Slice.",
                                       "scope_paths": ["src/first.py"],
                                   },
                                   {
                                       "slice_id": 2,
                                       "summary": "Execute the second native Slice.",
                                       "scope_paths": ["src/second.py"],
                                   },
                               ])),
            ScriptedAgentEvent(AgentRole.CLAUDE, 1, 1, WorkflowStep.CLAUDE_PLAN_REVIEW,
                               review()),
            ScriptedAgentEvent(AgentRole.CODEX, 2, 1, WorkflowStep.CODEX_IMPLEMENTATION,
                               codex_result("implementation_result", test_files=[])),
            ScriptedAgentEvent(AgentRole.CLAUDE, 2, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                               review()),
            ScriptedAgentEvent(AgentRole.CODEX, 3, 1, WorkflowStep.CODEX_IMPLEMENTATION,
                               codex_result("implementation_result", test_files=[])),
            ScriptedAgentEvent(AgentRole.CLAUDE, 3, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                               review()),
            ScriptedAgentEvent(AgentRole.CODEX, 4, 1, WorkflowStep.CODEX_FINAL_REVIEW,
                               codex_result(
                                   "final_report_result",
                                   self_check="The scripted branch passed its bound final self-check.",
                               )),
            ScriptedAgentEvent(AgentRole.CLAUDE, 4, 1, WorkflowStep.CLAUDE_FINAL_REVIEW,
                               review()),
        ),
        changes=(
            ScriptedChange(1, 1, base, plan_fp, ("docs/internal/plan.md",), "plan diff"),
            ScriptedChange(2, 1, base, first_fp, ("src/first.py",), "first slice diff"),
            ScriptedChange(3, 1, commit1, second_fp, ("src/second.py",), "second slice diff"),
            ScriptedChange(4, 1, base, final_fp,
                           ("src/first.py", "src/second.py"), "full branch diff"),
        ),
        validations=tuple(
            ScriptedValidation(fingerprint, "pass")
            for fingerprint in (plan_fp, first_fp, second_fp, final_fp)
        ),
        commits=(
            ScriptedCommit(1, first_fp, commit1),
            ScriptedCommit(2, second_fp, commit2),
        ),
    )
    session = ScriptedWorkflowSession(scenario)
    context = build_scenario_context(scenario)
    state = build_scenario_state(scenario, task_file=task_file)
    if run_id is not None:
        state = replace(state, run_id=run_id)
    plan = session.run(state, context)
    state = plan.result.state.start_work_unit(
        slice_id=1, kind=WorkUnitKind.SLICE, step=WorkflowStep.CODEX_IMPLEMENTATION
    ).bind_current_slice_git_boundary(
        start_commit=base, scope_paths=("src/first.py",), start_fingerprint="0" * 64
    )
    first = session.run(state, context)
    state = first.result.state.start_work_unit(
        slice_id=2, kind=WorkUnitKind.SLICE, step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit=commit1,
    ).bind_current_slice_git_boundary(
        start_commit=commit1, scope_paths=("src/second.py",), start_fingerprint="0" * 64
    )
    second = session.run(state, context)
    final = session.engine.run_final_review(second.result.state, context)
    return final, tuple(session.driver.calls), dict(session.driver.validation_counts)


def run_pipeline(
    task_file: Path,
    args: argparse.Namespace,
    force_new: bool = False,
) -> int | WatchTaskResult:
    direct_queue_resume = bool(
        not bool(getattr(args, "watch", False))
        and getattr(args, "resume_explicit", False)
        and getattr(args, "task_file_explicit", False)
        and getattr(args, "resume", False)
    )
    watch_invocation = bool(
        getattr(args, "watch_run_id", None) is not None and not direct_queue_resume
    )
    queue_identity = None
    if direct_queue_resume:
        inbox_dir = Path(args.inbox_dir)
        outbox_dir = Path(args.outbox_dir)
        marker = success_marker_path(task_file)
        if marker.exists():
            try:
                evidence = load_queue_success_evidence(
                    task_file, inbox_dir=inbox_dir, outbox_dir=outbox_dir
                )
                repository_root = Path.cwd().resolve()
                resumed = load_resumable_workflow_state(
                    repository_root / ".orchestrator" / "state.json",
                    repository_root=repository_root,
                    allowed_roots=tuple(
                        dict.fromkeys((repository_root, task_file.parent.resolve()))
                    ),
                )
                if not isinstance(resumed, WorkflowState):
                    raise ValueError("bound queue recovery requires version-3 state")
                terminal = WorkflowRunResult(resumed, _history(resumed))
                if (
                    not terminal.workflow_completed
                    or resumed.run_id != evidence.run_id
                    or Path(resumed.task_file).resolve() != task_file.resolve()
                    or resumed.task_digest != evidence.task_digest
                    or resumed.effective_protocol_mode.value != evidence.protocol_mode
                ):
                    raise ValueError("bound success evidence differs from terminal workflow state")
            except (ArtifactResumeError, StateSchemaError, ValueError) as exc:
                logger.error("Direct queue recovery rejected: %s", exc)
                return 1
            args.watch_run_id = evidence.run_id
            queue_result = finalize_queue_success(
                task_file,
                inbox_dir=inbox_dir,
                outbox_dir=outbox_dir,
                run_id=evidence.run_id,
                task_digest=evidence.task_digest,
                protocol_mode=evidence.protocol_mode,
            )
            if queue_result.disposition is QueueFinalizationDisposition.COMPLETED:
                logger.info("Completed pending queue bookkeeping: %s", queue_result.destination)
                return 0
            logger.error("Direct queue recovery failed: %s", queue_result.detail)
            return 1
        if task_file.exists() and watch_identity_path(task_file).exists():
            path_check = finalize_queue_success(
                task_file, inbox_dir=inbox_dir, outbox_dir=outbox_dir
            )
            if path_check.disposition is QueueFinalizationDisposition.FAILED:
                logger.error("Direct watch-origin resume rejected: %s", path_check.detail)
                return 1
            try:
                queue_identity = load_watch_identity(task_file)
            except ValueError as exc:
                logger.error("Direct watch-origin resume rejected: %s", exc)
                return 1
            args.watch_run_id = queue_identity.run_id
        elif not task_file.exists():
            logger.error("Direct queue recovery source is missing without bound success evidence")
            return 1
    try:
        if args.dry_run:
            result, calls, validations = run_default_dry_run(
                task_file, run_id=getattr(args, "watch_run_id", None)
            )
            logger.info(
                "Default state-v3 dry-run completed: work_units=%s commits=%s validations=%s",
                result.state.current_work_unit_id,
                sum(call.startswith("commit:") for call in calls),
                sum(validations.values()),
            )
        else:
            result = run_production_workflow(task_file, args, force_new=force_new)
    except Exception as exc:
        failure = classify_exception(exc)
        watch_run_id = getattr(args, "watch_run_id", None)
        records_written = False
        if watch_invocation and watch_run_id is not None:
            records_written = watch_run_has_records(Path.cwd(), watch_run_id)
            failure = enforce_record_start_boundary(
                failure, records_written=records_written
            )
        logger.error(
            "State-v3 workflow failed: class=%s diagnostic=%s detail=%s",
            failure.failure_class.value,
            failure.diagnostic_code,
            exc,
        )
        if watch_invocation and watch_run_id is not None:
            return WatchTaskResult.from_failure(
                failure,
                run_id=watch_run_id,
                records_written=records_written,
                protocol_mode=(
                    None
                    if failure.failure_class is FailureClass.TERMINAL_REJECTION
                    else "structured-v2"
                ),
            )
        return 1

    if watch_invocation and getattr(args, "watch_run_id", None) is not None:
        return WatchTaskResult.from_workflow(result)
    if queue_identity is not None and result.workflow_completed:
        state = result.state
        state_protocol = state.effective_protocol_mode.value
        if (
            state.run_id != queue_identity.run_id
            or Path(state.task_file).resolve() != task_file.resolve()
            or state.task_digest != queue_identity.task_digest
            or state_protocol != queue_identity.protocol_mode
        ):
            logger.error("Terminal workflow result differs from bound watch task identity")
            return 1
        queue_result = finalize_queue_success(
            task_file,
            inbox_dir=Path(args.inbox_dir),
            outbox_dir=Path(args.outbox_dir),
            run_id=state.run_id,
            task_digest=state.task_digest,
            protocol_mode=state_protocol,
            publish=True,
        )
        if queue_result.disposition is not QueueFinalizationDisposition.COMPLETED:
            logger.error("Terminal workflow succeeded but queue finalization failed: %s", queue_result.detail)
            return 1
        logger.info("Moved directly resumed watch task to done outbox: %s", queue_result.destination)
        return 0
    bootstrap_halt = (
        result.state.current_work_unit.status is WorkUnitStatus.AWAITING_RESUME
        and result.state.current_work_unit.gate.reason is GateReason.BOOTSTRAP_CHECK
    )
    exit_code = 4 if bootstrap_halt else result.exit_code
    if exit_code != 0:
        unit = result.state.current_work_unit
        gate = unit.gate
        logger.warning(
            "Workflow stopped: exit=%s status=%s step=%s reason=%s detail=%s paths=%s",
            exit_code,
            unit.status.value,
            unit.current_step.value,
            gate.reason.value,
            gate.detail or "(none)",
            ", ".join(gate.paths) or "(none)",
        )
        logger.info(
            "Continue this persisted run with --resume and the unchanged --task-file; "
            "use an explicit gate decision only when reason and fingerprint were reviewed."
        )
    return exit_code


def main() -> int:
    from cli import main as cli_main

    return cli_main(
        run_pipeline_fn=run_pipeline,
        watch_inbox_fn=watch_inbox,
        find_task_file_fn=find_task_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
