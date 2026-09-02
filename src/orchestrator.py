#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shlex
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Callable, Protocol, get_args, get_type_hints

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
)
from artifact_bridge import (
    ArtifactBridge, ArtifactBridgeError,
    finding_payload as finding_payload,
    review_payload_matches_result,
    finding_handoff_export_payload, finding_handoff_import_payload,
)
from artifact_migration import (
    ArtifactResumeError,
    resolve_resume_state,
)
from artifact_models import (
    ArtifactRecord, BindingPayload, CorrectionWorkUnitPayload, DiagnosticPayload,
    FingerprintKind, GateDecisionPayload, GateTransitionPayload,
    AgentResultPayload, InvocationFailurePayload, ReviewPayload,
    Role,
    BlobReference, ProviderContentPayload,
    ProviderInputMeasurementPayload, canonical_json,
    ProviderAttemptPayload, ProviderUsagePayload,
    FindingHandoffExportPayload,
    FindingTransitionPayload,
    SideEffectPayload,
    WorkflowEventPayload, WorkflowTransitionPayload,
    RecordType, stable_record_id,
)
from artifact_store import ArtifactStore, ArtifactStoreError
from artifact_replay import (
    ArtifactReplayError,
    ArtifactReplayResult,
    pending_workflow_event_payload,
    project_review_contracts,
    project_validation_attestations,
    replay_artifacts,
)
from finding_reducer import reduce_findings
from provider_input_budget import ProviderInputMeasurement
from review_packets import ReviewPacket
from audit_trail import (
    AuditProjection,
    AuthorizedTestChanges,
    OverallAuditEntry,
    ReviewAuditEvent,
    ValidationAuditEvent,
    managed_slice_document_path,
)
from cli import DEFAULT_AGENTS_FILE, DEFAULT_TASK_FILE
from contracts import (
    AgentRole,
    CodexContractResult,
    CodexStepContract,
    ContractResult,
    FindingRecord,
    PlannedSlice,
    StepContract,
    ValidationAttestation,
)
from gates import TestChangeEvidence, detect_test_changes, matches_path_patterns
from path_policy import PathPolicyError, resolve_path_within_roots
from git_service import (
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
    FinalReviewEvidenceSnapshot,
    RepositoryChangeError,
    RepositoryChanges,
    build_final_review_evidence_snapshot,
    collect_repository_changes,
    load_final_review_evidence_cache,
    probe_repository_snapshot,
    render_final_review_evidence_cache,
    resolve_merge_base,
)
from semantic_markdown import SemanticMarkdownError, canonical_semantic_markdown
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
    write_workflow_projection_checkpoint,
    write_workflow_state_projection,
    workflow_checkpoint_path,
)
from task_contract import TaskContract, TaskMode, parse_task_contract
from workflow import (
    CodexInvocation,
    PersistedNativeReviewerReplay,
    ReviewerInvocation,
    NoWorkflowChangesError,
    WorkflowChanges,
    WorkflowCommitRequest,
    WorkflowContext,
    WorkflowCorrectionBoundary,
    WorkflowDriver,
    WorkflowEngine,
    WorkflowExecutionError,
    WorkflowHistory,
    WorkflowRunResult,
    require_driver_capabilities,
    require_workflow_driver,
)
from workflow_recovery import WorkflowRecovery, WorkflowRecoveryDependencies
from workflow_persistence import (
    WorkflowPersistence,
    WorkflowPersistenceDependencies,
)
from workflow_baseline import (
    WorkflowBaseline,
    WorkflowBaselineDependencies,
    bootstrap_fact,
    gate_transition_payload,
    matches_baseline_initialization_prefix,
)
from workflow_validation import (
    WorkflowValidation,
    WorkflowValidationDependencies,
)
from workflow_audit import WorkflowAudit, WorkflowAuditDependencies
from workflow_git_commit import (
    WorkflowGitCommit,
    WorkflowGitCommitDependencies,
)
from workflow_state import (
    AgentProfileBinding,
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
)
from side_effects import (
    file_state_digest,
    Reconciliation,
    ReconciliationOutcome,
    SideEffectExecutor,
    SideEffectBoundaryObserver,
    SideEffectReconciliationError,
    SideEffectSpec,
    reconcile_file_write,
    reconcile_provider_start,
    sha256_bytes,
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


class ProductionWorkflowLoopDriver(WorkflowDriver, Protocol):
    """Workflow contract plus capabilities owned only by the production loop."""

    def finalize_audit(self, state: WorkflowState) -> str | None: ...

    def assert_structured_decision_context(self) -> None: ...

    def prepare_finding_handoff(
        self,
        *,
        plan_task_path: Path,
        work_plan_path: str,
        target_branch: str,
        approved_plan_commit: str,
    ) -> tuple[str, str] | None: ...

    def persist_implementation_handoff(
        self, handoff_path: Path, approved_plan_commit: str
    ) -> None: ...

    def _write_side_effect_file(
        self, path: Path, content: str, *, normalized_text: bool
    ) -> None: ...


PRODUCTION_LOOP_INTERNAL_DRIVER_METHODS = frozenset(
    {
        "_write_side_effect_file",
        "assert_structured_decision_context",
        "finalize_audit",
        "persist_implementation_handoff",
        "prepare_finding_handoff",
    }
)


def require_production_workflow_loop_driver(driver: object) -> None:
    """Fail before the production loop uses an incomplete internal surface."""
    require_workflow_driver(driver)
    require_driver_capabilities(
        driver,
        methods=PRODUCTION_LOOP_INTERNAL_DRIVER_METHODS,
        label="production workflow driver internal surface",
    )


class ProductionWorkflowDriver:
    """Bind the deterministic v3 engine to real agents, Git, validation, and state."""

    active_state: WorkflowState | None

    def __init__(
        self,
        *,
        repository_root: Path,
        state_file: Path,
        agents: dict[str, AgentAdapter],
        config: OrchestratorConfig,
        allowed_roots: tuple[Path, ...],
        replace_existing_run_id: str | None = None,
        side_effect_boundary_observer: SideEffectBoundaryObserver | None = None,
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
        self._final_review_evidence_snapshot: FinalReviewEvidenceSnapshot | None = None
        self._final_review_evidence_collections = 0
        self._final_review_evidence_reuses = 0
        self._artifact_bridge: ArtifactBridge | None = None
        self._replace_existing_run_id = replace_existing_run_id
        self._side_effect_boundary_observer = side_effect_boundary_observer

    def _recovery_boundary(self) -> WorkflowRecovery:
        """Bind driver-owned state to one recovery operation explicitly."""

        return WorkflowRecovery(
            WorkflowRecoveryDependencies(
                root=self.root,
                artifact_bridge=lambda: self._artifact_bridge,
                active_state=lambda: self.active_state,
                reconcile_external_attempt=self._reconcile_provider_effect,
                side_effect_executor=self._side_effect_executor,
                side_effect_spec=self._side_effect_spec,
                mark_side_effect_completed=self._mark_completed_side_effect,
                attempt_response_path=self._provider_attempt_response_path,
                canonical_agent_result=self._canonical_native_agent_result,
                load_agent_request_bundle=self._load_native_agent_request_bundle,
                content_text=self._provider_content_text,
                persist_implementer_contract=self.persist_native_codex_contract,
                persist_review_contract=self.persist_native_review_contract,
                store_implementer_output=lambda content: setattr(
                    self, "last_codex_output", content
                ),
                agent_profile=lambda name: (
                    self.agents[name].model,
                    self.agents[name].effort,
                ),
            )
        )

    def _persistence_boundary(self) -> WorkflowPersistence:
        """Bind driver-owned resources to one persistence operation explicitly."""

        return WorkflowPersistence(
            WorkflowPersistenceDependencies(
                artifact_bridge=lambda: self._artifact_bridge,
                active_state=lambda: self.active_state,
                artifact_fingerprint=self._artifact_fingerprint,
                append_workflow_transition=self._append_workflow_transition,
                gate_transition_payload=self._gate_transition_payload,
                append_gate_decision_binding=self._append_gate_decision_binding,
                append_workflow_event=self._append_workflow_event,
                native_agent_request_path=self._native_agent_request_path,
                native_agent_request_bundle_json=(
                    self._native_agent_request_bundle_json
                ),
                materialize_review_packet=self._materialize_review_packet,
                canonical_agent_result=self._canonical_native_agent_result,
            )
        )

    def _baseline_boundary(self) -> WorkflowBaseline:
        """Bind driver-owned resources to one baseline operation explicitly."""

        return WorkflowBaseline(
            WorkflowBaselineDependencies(
                artifact_bridge=lambda: self._artifact_bridge,
                active_state=lambda: self.active_state,
                artifact_fingerprint=self._artifact_fingerprint,
                collect_changes=self.collect_changes,
                persist_bootstrap_state=self._persist_bootstrap_state,
                persistence=self._persistence_boundary,
                recovery=self._recovery_boundary,
                reconcile_pending_workflow_event=(
                    self._reconcile_pending_workflow_event
                ),
                side_effect_executor=self._side_effect_executor,
            )
        )

    def _validation_boundary(self) -> WorkflowValidation:
        """Bind driver-owned resources to one validation operation explicitly."""

        return WorkflowValidation(
            WorkflowValidationDependencies(
                root=lambda: self.root,
                active_state=lambda: self.active_state,
                artifact_bridge=lambda: self._artifact_bridge,
                assert_structured_decision_context=(
                    self.assert_structured_decision_context
                ),
                config=lambda: self.config,
                persistence=self._persistence_boundary,
            )
        )

    def _audit_boundary(self) -> WorkflowAudit:
        """Bind driver-owned resources to one managed audit operation explicitly."""

        return WorkflowAudit(
            WorkflowAuditDependencies(
                root=lambda: self.root,
                artifact_bridge=lambda: self._artifact_bridge,
                assert_structured_decision_context=(
                    self.assert_structured_decision_context
                ),
                mark_completed_side_effect=self._mark_completed_side_effect,
                side_effect_executor=self._side_effect_executor,
                side_effect_spec=self._side_effect_spec,
                bound_task_control_paths=_bound_task_control_paths,
                overall_audit_entries=_overall_audit_entries,
                authorized_test_approval=_authorized_test_approval,
                audit_projection=_audit_projection,
            )
        )

    def _git_commit_boundary(self) -> WorkflowGitCommit:
        """Bind the complete ledger-bracketed Slice commit explicitly."""

        return WorkflowGitCommit(
            WorkflowGitCommitDependencies(
                root=lambda: self.root,
                active_state=lambda: self.active_state,
                artifact_bridge=lambda: self._artifact_bridge,
                assert_structured_decision_context=(
                    self.assert_structured_decision_context
                ),
                repository_changes=(
                    lambda fingerprint: self._repository_changes.get(fingerprint)
                ),
                mark_completed_side_effect=self._mark_completed_side_effect,
                side_effect_executor=self._side_effect_executor,
                side_effect_spec=self._side_effect_spec,
                bound_task_control_paths=_bound_task_control_paths,
            )
        )

    def _side_effect_executor(self, bridge: ArtifactBridge) -> SideEffectExecutor:
        """Create the one ledger executor carrying the optional crash observer."""

        return SideEffectExecutor(
            bridge, getattr(self, "_side_effect_boundary_observer", None)
        )

    def _mark_completed_side_effect(self, effect_key: str) -> None:
        if self.active_state is not None:
            self.active_state = self.active_state.mark_side_effect_completed(effect_key)

    def _side_effect_spec(
        self,
        effect_class: str,
        operation: tuple[str, ...],
        *,
        fingerprint: str | None = None,
    ) -> SideEffectSpec:
        state = self.active_state
        if state is None:
            raise WorkflowExecutionError("side effect has no active workflow state")
        return SideEffectSpec(
            effect_class,
            str(state.current_work_unit_id),
            operation,
            fingerprint or self._artifact_fingerprint(),
        )

    def _write_side_effect_file(
        self,
        path: Path,
        content: str,
        *,
        normalized_text: bool,
        fingerprint: str | None = None,
    ) -> None:
        bridge = self._artifact_bridge
        if bridge is None or self.active_state is None:
            if normalized_text:
                write_file(path, content)
            else:
                self._write_immutable_file(path, content)
            return
        rendered = content.strip() + "\n" if normalized_text else content
        expected = rendered.encode("utf-8")
        try:
            target = path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            try:
                resolved = resolve_path_within_roots(path, self.allowed_roots)
            except PathPolicyError as exc:
                raise WorkflowExecutionError(
                    "side-effect file target is outside the authorized roots"
                ) from exc
            target = f"external:{resolved.as_posix()}"
        spec = self._side_effect_spec(
            "file_write", (target, sha256_bytes(expected)), fingerprint=fingerprint
        )

        def perform() -> tuple[None, str]:
            if normalized_text:
                write_file(path, content)
            else:
                self._write_immutable_file(path, content)
            actual = file_state_digest(path)
            if actual != spec.operation[1]:
                raise SideEffectReconciliationError(
                    "file side-effect target differs before result completion"
                )
            return None, actual

        self._side_effect_executor(bridge).execute(
            spec,
            reconcile=lambda: reconcile_file_write(path, sha256_bytes(expected)),
            perform=perform,
        )
        self._mark_completed_side_effect(spec.effect_key)

    def _write_text_side_effect_file(self, path: Path, content: str) -> None:
        self._write_side_effect_file(path, content, normalized_text=True)

    def _reconcile_pending_side_effects(
        self,
        state: WorkflowState,
        replay=None,
    ) -> bool:
        return self._recovery_boundary()._reconcile_pending_side_effects(state, replay)

    def bind_work_unit(self, state: WorkflowState) -> None:
        # Runtime history is written by checkpoint(), not by the pure workflow-state
        # transitions.  A transition that starts the next work unit in the same engine
        # invocation can therefore carry an older runtime_history snapshot.  Preserve
        # the driver's last persisted ledger so the subsequent checkpoint can archive
        # the just-completed work unit instead of silently dropping its reviews.
        if (
            self.active_state is not None
            and self.active_state.run_id == state.run_id
        ):
            active_units = {
                item.work_unit_id: item for item in self.active_state.work_units
            }
            state = replace(
                state,
                work_units=tuple(
                    replace(
                        item,
                        completed_side_effects=tuple(
                            dict.fromkeys(
                                (
                                    *active_units.get(
                                        item.work_unit_id, item
                                    ).completed_side_effects,
                                    *item.completed_side_effects,
                                )
                            )
                        ),
                    )
                    for item in state.work_units
                ),
            )
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
        if self._artifact_bridge is not None:
            self.active_state = resolve_resume_state(self.root, state.run_id).state
            state = self.active_state
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

    @staticmethod
    def _matches_baseline_initialization_prefix(
        records: tuple[ArtifactRecord, ...], state: WorkflowState
    ) -> bool:
        return matches_baseline_initialization_prefix(records, state)

    def assert_structured_decision_context(self) -> None:
        """Reload authoritative records before an external workflow side effect."""
        state = self.active_state
        if state is None or state.effective_protocol_mode is ProtocolMode.LEGACY_STATE_V3:
            return
        try:
            resolution = resolve_resume_state(self.root, state)
            if self._reconcile_pending_side_effects(
                resolution.state, resolution.replay_result
            ):
                resolution = resolve_resume_state(self.root, resolution.state)
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
        self.active_state = resolution.state

    def _persist_structured_baseline(self, state: WorkflowState) -> None:
        self._baseline_boundary()._persist_structured_baseline(state)

    def _persist_workflow_snapshot(self, state: WorkflowState) -> None:
        self._persistence_boundary()._persist_workflow_snapshot(state)

    def _persist_slice_boundaries(self, state: WorkflowState) -> None:
        self._persistence_boundary()._persist_slice_boundaries(state)

    @staticmethod
    def _gate_transition_payload(unit: WorkUnitRecord) -> GateTransitionPayload:
        return gate_transition_payload(unit)

    @staticmethod
    def _matching_gate_record(
        chain: tuple[ArtifactRecord, ...] | list[ArtifactRecord],
        decision: GateDecisionRecord,
    ) -> ArtifactRecord | None:
        return WorkflowPersistence._matching_gate_record(chain, decision)

    def _append_gate_decision_binding(
        self,
        work_unit_id: int | str,
        decision: GateDecisionRecord,
        gate_record: ArtifactRecord,
    ) -> ArtifactRecord:
        bridge = self._artifact_bridge
        assert bridge is not None
        unit_id = str(work_unit_id)
        logical_id = f"gate-decision-{unit_id}-{gate_record.record_id[:20]}"
        return bridge.append(
            GateDecisionPayload(
                work_unit_id=unit_id,
                gate_record_id=gate_record.record_id,
                paths=decision.paths,
                resume_step=(
                    None
                    if decision.resume_step is None
                    else decision.resume_step.value
                ),
            ),
            logical_id=logical_id,
            idempotency_key=f"gate-decision:{unit_id}:{gate_record.record_id}",
            fingerprint_sha256=decision.fingerprint,
        )

    def _persist_gate_snapshot(self, state: WorkflowState) -> None:
        self._persistence_boundary()._persist_gate_snapshot(state)

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
        transition_record = bridge.append(
            payload,
            logical_id="workflow-transition",
            idempotency_key=f"workflow-transition:{revision}",
            fingerprint_sha256=fingerprint,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
        self._append_workflow_event(
            event_kind="transition",
            work_unit_id=payload.work_unit_id,
            slice_id=payload.slice_id,
            round_number=None,
            domain_record=transition_record,
        )

    def _append_workflow_event(
        self,
        *,
        event_kind: str,
        work_unit_id: str | None,
        slice_id: str,
        round_number: int | None,
        domain_record: ArtifactRecord,
    ) -> ArtifactRecord:
        """Append ordering metadata which references, but never copies, a fact."""
        bridge = self._artifact_bridge
        assert bridge is not None
        return bridge.append(
            WorkflowEventPayload(
                event_kind=event_kind,
                work_unit_id=work_unit_id,
                slice_id=slice_id,
                round_number=round_number,
                record_refs=(domain_record.record_id,),
            ),
            logical_id=f"workflow-event-{domain_record.record_id}",
            idempotency_key=f"workflow-event:{domain_record.record_id}",
            fingerprint_sha256=domain_record.fingerprint.sha256,
            fingerprint_kind=domain_record.fingerprint.kind,
        )

    def _reconcile_pending_workflow_event(
        self, replay: ArtifactReplayResult
    ) -> ArtifactRecord:
        """Finish the sole deterministic domain-record/event crash tail."""
        payload = pending_workflow_event_payload(replay)
        if payload is None:
            raise WorkflowExecutionError(
                "workflow event reconciliation has no unique pending domain record"
            )
        domain_record = next(
            record
            for record in replay.records
            if record.record_id == payload.record_refs[0]
        )
        return self._append_workflow_event(
            event_kind=payload.event_kind,
            work_unit_id=payload.work_unit_id,
            slice_id=payload.slice_id,
            round_number=payload.round_number,
            domain_record=domain_record,
        )

    def _persist_structured_tail(self, state: WorkflowState) -> None:
        self._persistence_boundary()._persist_structured_tail(state)

    def _persist_provider_bootstrap(
        self, measurement: ProviderInputMeasurement
    ) -> ArtifactRecord | None:
        return self._baseline_boundary()._persist_provider_bootstrap(measurement)

    @staticmethod
    def _provider_attempt_response_path(base: Path, attempt_number: int) -> Path:
        return base.with_name(
            f"{base.stem}.attempt-{attempt_number}{base.suffix}"
        )

    def _start_provider_attempt(
        self,
        measurement: ProviderInputMeasurement,
        bootstrap: object | None,
        *,
        operation_instance: str | None = None,
        durable_response_path: Path,
    ) -> tuple[ArtifactRecord, SideEffectSpec, Path]:
        return self._recovery_boundary()._start_provider_attempt(
            measurement,
            bootstrap,
            operation_instance=operation_instance,
            durable_response_path=durable_response_path,
        )

    def _reconcile_provider_effect(
        self,
        spec: SideEffectSpec,
        durable_response_path: Path,
    ):
        bridge = self._artifact_bridge
        if bridge is None:
            return Reconciliation(ReconciliationOutcome.UNKNOWN)
        attempt_number = int(spec.operation[5])
        matching_attempt_records = tuple(
            record.payload
            for record in bridge.store.load_chain()
            if isinstance(record.payload, ProviderAttemptPayload)
            and record.payload.provider.value == spec.operation[0]
            and record.payload.operation == spec.operation[1]
            and record.payload.work_unit_id == spec.work_unit_id
            and record.payload.input_digest == spec.operation[2]
            and record.payload.binding_fingerprint == spec.operation[3]
            and record.payload.attempt_number == attempt_number
        )
        terminals = tuple(
            payload
            for payload in matching_attempt_records
            if payload.phase in {"succeeded", "failed"}
        )
        if len(terminals) == 1 and terminals[0].phase == "failed":
            return Reconciliation(
                ReconciliationOutcome.OCCURRED,
                f"failed:{terminals[0].failure_kind}",
            )
        if not matching_attempt_records:
            return Reconciliation(ReconciliationOutcome.NOT_OCCURRED)
        response = reconcile_provider_start(durable_response_path)
        if len(terminals) == 1 and terminals[0].phase == "succeeded":
            return response
        if len(matching_attempt_records) == 1:
            return response
        return Reconciliation(ReconciliationOutcome.UNKNOWN)

    def _finish_provider_attempt(
        self,
        started: object,
        duration_seconds: float,
        failure_kind: str | None,
        usage: ProviderUsagePayload | None,
    ) -> None:
        bridge = self._artifact_bridge
        if (
            bridge is None
            or not isinstance(started, tuple)
            or len(started) != 3
            or not isinstance(started[0], ArtifactRecord)
            or not isinstance(started[1], SideEffectSpec)
            or not isinstance(started[2], Path)
        ):
            raise WorkflowExecutionError("provider attempt terminal requires its durable start")
        started_record, spec, response_path = started
        bridge.finish_provider_attempt(
            started_record,
            duration_seconds=duration_seconds,
            failure_kind=failure_kind,
            usage=usage,
        )
        if response_path.is_file():
            result = sha256_bytes(response_path.read_bytes())
        elif failure_kind is not None:
            result = f"failed:{failure_kind}"
        else:
            raise WorkflowExecutionError(
                "successful provider attempt has no durable response evidence"
            )
        self._side_effect_executor(bridge).complete(spec, result)
        self._mark_completed_side_effect(spec.effect_key)

    @staticmethod
    def _bootstrap_fact(
        payload: ProviderInputMeasurementPayload | object,
    ) -> BootstrapCheckFact:
        return bootstrap_fact(payload)

    def _persist_bootstrap_state(self, state: WorkflowState) -> None:
        if state.effective_protocol_mode is ProtocolMode.LEGACY_STATE_V3:
            save_workflow_state(
                self.state_file,
                state,
                allowed_roots=self.allowed_roots,
                replace_existing_run_id=self._replace_existing_run_id,
            )
            write_workflow_checkpoint(
                self.checkpoint_dir / state.run_id,
                state,
                allowed_roots=self.allowed_roots,
            )
            self._replace_existing_run_id = None
            self.active_state = state
            return
        resolution = resolve_resume_state(self.root, state.run_id)
        write_workflow_state_projection(
            self.state_file,
            resolution,
            allowed_roots=self.allowed_roots,
        )
        projected = resolution.state
        checkpoint_root = self.checkpoint_dir / projected.run_id
        checkpoint_path = workflow_checkpoint_path(
            checkpoint_root,
            work_unit_id=projected.current_work_unit_id,
            slice_id=projected.current_slice_id,
            round_number=projected.current_work_unit.round_number,
        )
        written = write_workflow_projection_checkpoint(
            checkpoint_root,
            resolution,
            allowed_roots=self.allowed_roots,
        )
        if written != checkpoint_path.resolve():
            raise WorkflowExecutionError(
                "workflow projection checkpoint path differs from its cursor"
            )
        self._replace_existing_run_id = None
        self.active_state = projected

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
                            durable_response_path=raw_path,
                        ),
                        terminal=self._finish_provider_attempt,
                        durable_response_path=lambda handle: handle[2],
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
        _projected_findings: tuple[FindingRecord, ...],
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
            else:
                projected = reduced.ledger.findings
        except (ArtifactReplayError, ValueError) as exc:
            raise WorkflowExecutionError(
                f"authoritative finding replay failed: {exc}"
            ) from exc
        return projected

    def carry_forward_native_findings(
        self,
        state: WorkflowState,
        _current_findings: tuple[FindingRecord, ...],
    ) -> tuple[FindingRecord, ...]:
        """Restore the complete record-native ledger at a work-unit boundary."""
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
        self._persistence_boundary()._persist_native_agent_request_bundle(invocation)

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
    def _write_immutable_file(path: Path, content: str) -> None:
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

    def _write_native_codex_raw_response(self, path: Path, content: str) -> None:
        self._write_side_effect_file(path, content, normalized_text=False)

    def _native_agent_response_files(
        self, invocation: CodexInvocation
    ) -> tuple[Path, ...]:
        base = self._native_codex_response_path(invocation)
        candidates = [base] if base.is_file() else []
        candidates.extend(
            path
            for path in sorted(
                base.parent.glob(f"{base.stem}.attempt-*{base.suffix}")
            )
            if path.is_file()
        )
        return tuple(dict.fromkeys(candidates))

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
        review_log_path = self.log_dir / (
            f"work-unit-{invocation.work_unit_id:04d}-"
            f"{invocation.step.value}-round-{invocation.round_number:04d}.log"
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
                write_file=self._write_text_side_effect_file,
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
                            durable_response_path=review_log_path,
                        ),
                        terminal=self._finish_provider_attempt,
                        durable_response_path=lambda handle: handle[2],
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

    def persist_review_packet(self, packet: ReviewPacket) -> None:
        self._persistence_boundary().persist_review_packet(packet)

    def _persist_provider_content(
        self,
        *,
        role: Role,
        work_unit_id: int,
        round_number: int,
        operation: str,
        request_id: str,
        canonical: str,
        content_kind: str,
        fingerprint: str,
        fingerprint_kind: FingerprintKind = FingerprintKind.IMPLEMENTATION,
    ) -> ArtifactRecord:
        return self._persistence_boundary()._persist_provider_content(
            role=role,
            work_unit_id=work_unit_id,
            round_number=round_number,
            operation=operation,
            request_id=request_id,
            canonical=canonical,
            content_kind=content_kind,
            fingerprint=fingerprint,
            fingerprint_kind=fingerprint_kind,
        )

    def _provider_content_text(
        self,
        *,
        role: Role,
        work_unit_id: int,
        round_number: int,
        operation: str,
        request_id: str | None = None,
        response_sha256: str | None = None,
        fingerprint: str | None = None,
        chain: tuple[ArtifactRecord, ...] | None = None,
    ) -> tuple[str, ArtifactRecord] | None:
        bridge = self._artifact_bridge
        if bridge is None:
            return None
        matches = tuple(
            record
            for record in (chain if chain is not None else bridge.store.load_chain())
            if isinstance(record.payload, ProviderContentPayload)
            and record.payload.role is role
            and record.payload.work_unit_id == str(work_unit_id)
            and record.payload.round_number == round_number
            and record.payload.operation == operation
            and (
                fingerprint is None
                or record.fingerprint.sha256 == fingerprint
            )
            and (request_id is None or record.payload.request_id == request_id)
            and (
                response_sha256 is None
                or record.payload.response_sha256 == response_sha256
            )
        )
        if not matches:
            return None
        if len(matches) != 1:
            raise WorkflowExecutionError(
                "provider content recovery has no unique record binding"
            )
        record = matches[0]
        payload = record.payload
        assert isinstance(payload, ProviderContentPayload)
        try:
            content = bridge.store.read_blob(payload.blob).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkflowExecutionError(
                "provider content is not canonical UTF-8"
            ) from exc
        return content, record

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
        return self._recovery_boundary().recover_pending_native_implementer(
            invocation,
            contract,
            history,
        )

    def recover_pending_native_reviewer(
        self,
        invocation: ReviewerInvocation,
        contract: StepContract,
        history: WorkflowHistory,
    ) -> NativeAgentReviewOutput | None:
        return self._recovery_boundary().recover_pending_native_reviewer(
            invocation,
            contract,
            history,
        )

    def recover_pending_native_reviewer_before_policy(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> PersistedNativeReviewerReplay | None:
        return self._recovery_boundary().recover_pending_native_reviewer_before_policy(
            state,
            context,
            history,
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
        self._persistence_boundary().persist_native_implementer_contract(
            output,
            previous_findings,
            recovery_fingerprint=recovery_fingerprint,
        )

    def persist_native_review_contract(
        self,
        output: NativeAgentReviewOutput,
        fingerprint: str,
        round_number: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        self._persistence_boundary().persist_native_review_contract(
            output,
            fingerprint,
            round_number,
            previous_findings,
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
        self._persistence_boundary()._persist_review_finding_transitions(
            result,
            fingerprint=fingerprint,
            round_number=round_number,
            previous_findings=previous_findings,
            structured=structured,
        )

    def persist_contract_diagnostic(
        self, role: AgentRole, output: str, reason: str, attempt: int
    ) -> None:
        self._persistence_boundary().persist_contract_diagnostic(
            role, output, reason, attempt
        )

    def persist_validation_attestation(
        self, attestation: ValidationAttestation
    ) -> None:
        self._persistence_boundary().persist_validation_attestation(attestation)

    def recover_pending_validation_attestation(
        self,
        fingerprint: str,
        expected_commands: tuple[str, ...],
        attestation_id: str,
    ) -> ValidationAttestation | None:
        return self._validation_boundary().recover_pending_validation_attestation(
            fingerprint,
            expected_commands,
            attestation_id,
        )

    def persist_validation_request(self, request) -> None:  # type: ignore[no-untyped-def]
        self._persistence_boundary().persist_validation_request(request)

    def persist_invocation_failure(
        self, payload: InvocationFailurePayload
    ) -> None:
        self._persistence_boundary().persist_invocation_failure(payload)

    def persist_gate_decision(
        self, work_unit_id: int, decision: GateDecisionRecord
    ) -> None:
        self._persistence_boundary().persist_gate_decision(work_unit_id, decision)

    def persist_gate_transition(self, state: WorkflowState) -> None:
        self._persistence_boundary().persist_gate_transition(state)

    def persist_implementation_handoff(
        self, handoff_path: Path, approved_plan_commit: str
    ) -> None:
        self._persistence_boundary().persist_implementation_handoff(
            handoff_path,
            approved_plan_commit,
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
        raw_plan_artifact: str | None = None
        if self.active_state is not None:
            if (
                self.active_state.execution_mode == TaskMode.PLAN_ONLY.value
                and self.active_state.current_work_unit.kind is WorkUnitKind.PLAN
                and self.active_state.work_plan_path is not None
            ):
                candidate = self.root / self.active_state.work_plan_path
                try:
                    if not candidate.is_symlink() and candidate.is_file():
                        content = candidate.read_text(encoding="utf-8")
                        canonical_semantic_markdown(
                            content,
                            path=self.active_state.work_plan_path,
                            remove_appendix=True,
                        )
                except (UnicodeError, SemanticMarkdownError):
                    # The raw fingerprint still binds every byte. Deferring only the
                    # text interpretation lets the typed plan validator offer its one
                    # safe, path-bound rewrite of this regular repository artifact.
                    raw_plan_artifact = self.active_state.work_plan_path
                except OSError:
                    # Preserve the normal semantic collector's fail-closed behavior
                    # for permissions and unstable filesystem objects.
                    pass
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
        final_review = (
            self.active_state is not None
            and self.active_state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
        )
        audit_path = (
            self.active_state.audit_report_path if final_review else None
        )
        snapshot: FinalReviewEvidenceSnapshot | None = None
        probe = None
        cache_path: Path | None = None
        if final_review:
            probe = probe_repository_snapshot(
                self.root,
                start_commit,
                semantic_markdown_paths=semantic_paths,
                excluded_paths=excluded_control_paths,
            )
            cache_path = self._final_review_evidence_cache_path(
                start_commit=start_commit,
                semantic_markdown_paths=semantic_paths,
                excluded_paths=excluded_control_paths,
                audit_path=audit_path,
                repository_identity_digest=probe.identity_digest,
            )
            memory_snapshot = self._final_review_evidence_snapshot
            if memory_snapshot is not None and memory_snapshot.matches(
                probe,
                start_commit=start_commit,
                semantic_markdown_paths=semantic_paths,
                excluded_paths=excluded_control_paths,
                audit_path=audit_path,
            ):
                snapshot = memory_snapshot
            else:
                authorized_digest = self._final_review_cache_authorized_digest(
                    cache_path, repository_fingerprint=probe.fingerprint
                )
                disk_snapshot = load_final_review_evidence_cache(
                    cache_path, expected_file_sha256=authorized_digest
                )
                if disk_snapshot is not None and disk_snapshot.matches(
                    probe,
                    start_commit=start_commit,
                    semantic_markdown_paths=semantic_paths,
                    excluded_paths=excluded_control_paths,
                    audit_path=audit_path,
                ):
                    snapshot = disk_snapshot
            if snapshot is not None:
                self._final_review_evidence_reuses += 1
                logger.debug(
                    "Final review evidence reused: fingerprint=%s reuses=%s "
                    "collections=%s elapsed_ms=%s",
                    snapshot.repository_fingerprint,
                    self._final_review_evidence_reuses,
                    self._final_review_evidence_collections,
                    snapshot.collection_elapsed_ms,
                )
            elif memory_snapshot is not None or cache_path.exists():
                logger.debug(
                    "Final review evidence cache invalidated by the live repository "
                    "or controlling boundaries"
                )
        if snapshot is not None:
            changes = snapshot.repository_changes(self.root)
        else:
            collection_started = time.monotonic()
            changes = collect_repository_changes(
                self.root,
                start_commit,
                semantic_markdown_paths=semantic_paths,
                raw_fingerprint_paths=(
                    (raw_plan_artifact,) if raw_plan_artifact is not None else ()
                ),
                excluded_paths=excluded_control_paths,
            )
            if final_review and changes.entries:
                # Re-probe after the expensive collection. A concurrent repository or
                # index mutation is denied instead of being written into a stale cache.
                probe = probe_repository_snapshot(
                    self.root,
                    start_commit,
                    semantic_markdown_paths=semantic_paths,
                    excluded_paths=excluded_control_paths,
                )
                elapsed_ms = max(
                    0, round((time.monotonic() - collection_started) * 1000)
                )
                try:
                    snapshot = build_final_review_evidence_snapshot(
                        changes,
                        probe,
                        start_commit=start_commit,
                        semantic_markdown_paths=semantic_paths,
                        excluded_paths=excluded_control_paths,
                        audit_path=audit_path,
                        collection_elapsed_ms=elapsed_ms,
                    )
                except ValueError as exc:
                    raise WorkflowExecutionError(
                        "final-review repository changed during evidence collection"
                    ) from exc
                self._final_review_evidence_snapshot = snapshot
                self._final_review_evidence_collections += 1
                cache_path = self._final_review_evidence_cache_path(
                    start_commit=start_commit,
                    semantic_markdown_paths=semantic_paths,
                    excluded_paths=excluded_control_paths,
                    audit_path=audit_path,
                    repository_identity_digest=probe.identity_digest,
                )
                cache_content = render_final_review_evidence_cache(snapshot)
                cache_digest = sha256_bytes(cache_content.encode("utf-8"))
                prior_authority = self._final_review_cache_authorized_digest(
                    cache_path, repository_fingerprint=snapshot.repository_fingerprint
                )
                try:
                    current_digest = file_state_digest(cache_path)
                except SideEffectReconciliationError:
                    # A non-regular or unstable cache target has no authority.
                    # Keep the freshly collected in-memory evidence, but do not
                    # follow, replace, or otherwise repair that filesystem node.
                    current_digest = "invalid"
                if self._artifact_bridge is not None and (
                    prior_authority is None and current_digest in {"absent", cache_digest}
                ):
                    self._write_side_effect_file(
                        cache_path,
                        cache_content,
                        normalized_text=False,
                        fingerprint=snapshot.repository_fingerprint,
                    )
                elif prior_authority != cache_digest or current_digest != cache_digest:
                    # A missing or modified cache is never repaired from its own
                    # contents. The freshly collected repository evidence remains
                    # usable only in this process; a later resume will collect again.
                    logger.warning(
                        "Final review evidence cache is absent, modified, or lacks "
                        "append-only authority; resume will collect it again"
                    )
                if snapshot.audit_summary is not None:
                    logger.info(
                        "Final review evidence compacted: audit=%s original_chars=%s "
                        "evidence_chars=%s fingerprint=%s elapsed_ms=%s collection=%s",
                        snapshot.audit_summary.audit_path,
                        len(snapshot.canonical_diff),
                        len(snapshot.evidence_diff),
                        snapshot.repository_fingerprint,
                        snapshot.collection_elapsed_ms,
                        self._final_review_evidence_collections,
                    )
        if changes.entries:
            review_diff = (
                snapshot.evidence_diff if snapshot is not None else changes.diff_text
            ) or "(binary or metadata-only repository change)"
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

    def _final_review_evidence_cache_path(
        self,
        *,
        start_commit: str,
        semantic_markdown_paths: tuple[str, ...],
        excluded_paths: tuple[str, ...],
        audit_path: str | None,
        repository_identity_digest: str,
    ) -> Path:
        if self.active_state is None:
            raise WorkflowExecutionError("final-review evidence cache has no active run")
        run_key = hashlib.sha256(self.active_state.run_id.encode("utf-8")).hexdigest()
        snapshot_key = hashlib.sha256(
            json.dumps(
                {
                    "start_commit": start_commit,
                    "semantic_markdown_paths": semantic_markdown_paths,
                    "excluded_paths": excluded_paths,
                    "audit_path": audit_path,
                    "repository_identity_digest": repository_identity_digest,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return (
            self.root
            / ".orchestrator"
            / "cache"
            / "final-review-evidence"
            / run_key
            / f"{snapshot_key}.json"
        )

    def _final_review_cache_authorized_digest(
        self, path: Path, *, repository_fingerprint: str
    ) -> str | None:
        bridge = self._artifact_bridge
        if bridge is None:
            return None
        target = path.resolve().relative_to(self.root).as_posix()
        for record in reversed(bridge.store.load_chain()):
            payload = record.payload
            if (
                isinstance(payload, SideEffectPayload)
                and payload.effect_class == "file_write"
                and payload.phase == "result"
                and len(payload.operation) in {2, 4}
                and payload.operation[0] == target
                and payload.result == payload.operation[1]
                and record.fingerprint.sha256 == repository_fingerprint
            ):
                return payload.operation[1]
        return None

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
        return self._validation_boundary().validate(changes, request)

    def validate_plan(
        self,
        changes: WorkflowChanges,
        *,
        work_plan_path: str | None,
        scope_patterns: tuple[str, ...],
        plan_only: bool,
    ) -> ValidationAttestation:
        return self._validation_boundary().validate_plan(
            changes,
            work_plan_path=work_plan_path,
            scope_patterns=scope_patterns,
            plan_only=plan_only,
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
        return self._git_commit_boundary().commit_slice(request)

    def finalize_audit(self, state: WorkflowState) -> str | None:
        return self._audit_boundary().finalize_audit(state)

    def checkpoint(self, state: WorkflowState, history: WorkflowHistory) -> None:
        # B27 inventory: this remains the driver composition root.  It orders the
        # still driver-owned baseline binding and audit projection before choosing
        # legacy state writes or structured mirrors; moving it into the sink module
        # would reverse the intended baseline -> persistence dependency.
        # Pure workflow transitions return a new state without the runtime ledger
        # that checkpoint() persisted on the preceding step.  In particular, a
        # correction commit can start the next final-review work unit in the same
        # engine invocation.  Merge the driver-owned ledger before archiving the
        # completed unit, otherwise the audit can bind the new commit to an older
        # (possibly denying) reviewer result.
        if (
            self.active_state is not None
            and self.active_state.run_id == state.run_id
        ):
            active_units = {
                item.work_unit_id: item for item in self.active_state.work_units
            }
            state = replace(
                state,
                work_units=tuple(
                    replace(
                        item,
                        completed_side_effects=tuple(
                            dict.fromkeys(
                                (
                                    *active_units.get(
                                        item.work_unit_id, item
                                    ).completed_side_effects,
                                    *item.completed_side_effects,
                                )
                            )
                        ),
                    )
                    for item in state.work_units
                ),
            )
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
        if persisted.effective_protocol_mode is ProtocolMode.LEGACY_STATE_V3:
            save_workflow_state(
                self.state_file,
                persisted,
                allowed_roots=self.allowed_roots,
                replace_existing_run_id=self._replace_existing_run_id,
            )
            write_workflow_checkpoint(
                self.checkpoint_dir / persisted.run_id,
                persisted,
                allowed_roots=self.allowed_roots,
            )
            self._replace_existing_run_id = None
            self.active_state = persisted
            return
        resolution = resolve_resume_state(self.root, persisted.run_id)
        projected = resolution.state
        write_workflow_state_projection(
            self.state_file,
            resolution,
            allowed_roots=self.allowed_roots,
        )
        checkpoint_root = self.checkpoint_dir / projected.run_id
        checkpoint_path = workflow_checkpoint_path(
            checkpoint_root,
            work_unit_id=projected.current_work_unit_id,
            slice_id=projected.current_slice_id,
            round_number=projected.current_work_unit.round_number,
        )
        written = write_workflow_projection_checkpoint(
            checkpoint_root,
            resolution,
            allowed_roots=self.allowed_roots,
        )
        if written != checkpoint_path.resolve():
            raise WorkflowExecutionError(
                "workflow projection checkpoint path differs from its cursor"
            )
        self._replace_existing_run_id = None
        self.active_state = projected

    def _project_audit(
        self, state: WorkflowState, history: WorkflowHistory
    ) -> None:
        self._audit_boundary().project_audit(state, history)


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
        histories = {
            int(key): WorkflowHistory(int(key))
            for key in raw
        }
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
        item.record_id: item
        for item in project_review_contracts(replay, read_blob)
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
    prior_review_findings: dict[int, tuple[FindingRecord, ...]] = {}
    correction_finding_ids = {
        int(record.logical_id.removeprefix("work-unit-")): record.payload.finding_ids
        for record in replay.records
        if isinstance(record.payload, CorrectionWorkUnitPayload)
        and record.logical_id.removeprefix("work-unit-").isdigit()
    }
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
        prior_findings = prior_review_findings.get(work_unit_id)
        if prior_findings is None:
            reduced_prefix = reduce_findings(
                replay.subset(replay.records[:review_position])
            )
            correction_ids = correction_finding_ids.get(work_unit_id)
            prior_findings = (
                reduced_prefix.ledger.findings
                if correction_ids is None
                else reduced_prefix.request_subset(
                    finding_ids=correction_ids
                ).findings
            )
        prior_steps = tuple(
            record.payload.step
            for record in replay.records[: review_position + 1]
            if isinstance(record.payload, WorkflowTransitionPayload)
            and record.payload.work_unit_id == event.work_unit_id
        )
        allowed_origin_set = {
            finding.origin.slice_id
            for finding in prior_findings
            if finding.origin.slice_id != f"{int(event.slice_id):02d}"
        }
        if (
            prior_steps
            and prior_steps[-1] == WorkflowStep.CLAUDE_FINAL_REVIEW.value  # allowlist:provider -- canonical state-v3 step
        ):
            allowed_origin_set.add("FINAL")
        allowed_origins = tuple(sorted(allowed_origin_set))
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
            )
        )
        latest_reviews[work_unit_id] = (
            review_record.fingerprint.sha256,
            review.result,
        )
        # _record_review() replaces, rather than merges, history.findings.
        # A later round must therefore inherit exactly the prior review's
        # snapshot and may not self-authorize origins from the run-wide ledger.
        prior_review_findings[work_unit_id] = review.result.findings
    for work_unit_id in {
        *projected,
        *event_lists,
    }:
        history = projected.get(work_unit_id, WorkflowHistory(work_unit_id))
        latest = latest_reviews.get(work_unit_id)
        projected[work_unit_id] = replace(
            history,
            events=tuple(event_lists.get(work_unit_id, ())),
            attestations=tuple(attestation_lists.get(work_unit_id, ())),
            last_claude_fingerprint=(  # allowlist:provider -- canonical history field
                history.last_claude_fingerprint if latest is None else latest[0]  # allowlist:provider -- canonical history field
            ),
            latest_claude_review=(  # allowlist:provider -- canonical history field
                history.latest_claude_review if latest is None else latest[1]  # allowlist:provider -- canonical history field
            ),
        )
    return projected


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
    if state.current_work_unit.kind is not WorkUnitKind.FINAL_REVIEW:
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


def _history(
    state: WorkflowState,
    repository_root: Path | None = None,
) -> WorkflowHistory:
    if state.runtime_history is None:
        return WorkflowHistory(state.current_work_unit_id)
    try:
        raw = dict(state.runtime_history)
        if raw and all(
            isinstance(key, str)
            and key.isdigit()
            and isinstance(value, dict)
            and "workflow_event_record_refs" in value
            for key, value in raw.items()
        ):
            history = WorkflowHistory(state.current_work_unit_id)
        else:
            current = raw.get("current") if set(raw) == {"current", "archive"} else raw
            history = WorkflowHistory.from_dict(current)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowExecutionError(
            f"persisted workflow history is invalid: {exc}"
        ) from exc
    if history.work_unit_id != state.current_work_unit_id:
        history = WorkflowHistory(state.current_work_unit_id)
    if (
        repository_root is not None
        and state.effective_protocol_mode is ProtocolMode.STRUCTURED_V2
    ):
        try:
            store = ArtifactStore(repository_root, state.run_id)
            replay = replay_artifacts(
                store.load_chain(),
                state.run_id,
                require_content_authority=True,
                require_review_authority=True,
                allow_incomplete_review_tail=True,
            )
            history = _attach_record_events(
                {history.work_unit_id: history}, replay, store.read_blob
            ).get(history.work_unit_id, history)
        except (ArtifactReplayError, ArtifactStoreError, ValueError) as exc:
            raise WorkflowExecutionError(
                f"persisted workflow event projection is invalid: {exc}"
            ) from exc
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
        if existing and all(
            isinstance(key, str)
            and key.isdigit()
            and isinstance(value, dict)
            and "workflow_event_record_refs" in value
            for key, value in existing.items()
        ):
            # This is the record-reference projection, not a serialized
            # WorkflowHistory and therefore never belongs in the legacy archive.
            previous = None
        elif set(existing) == {"current", "archive"}:
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
        local_replay = replay_artifacts(
            bridge.store.load_chain(),
            state.run_id,
            allow_finding_import_bootstrap=True,
        )
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
    new_watch_task = watch_run and (
        force_new
        or (
            not state_file.exists()
            and not watch_run_has_records(root, run_id)
        )
    )
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
        try:
            existing = load_workflow_state(state_file, allowed_roots=allowed_roots)
        except StateSchemaError:
            # Force replacement is explicitly authorized to discard the cache.
            # A malformed non-authoritative projection must not veto that action.
            existing = None
        if isinstance(existing, WorkflowState):
            replacement_run_id = existing.run_id
    elif (state_file.exists() or bool(args.resume)) and not new_watch_task:
        try:
            loaded = load_resumable_workflow_state(
                state_file,
                repository_root=root,
                allowed_roots=allowed_roots,
                expected_run_id=(requested_run_id or None),
                expected_task_file=task_file,
                expected_task_digest=task_contract.digest,
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
    driver: ProductionWorkflowLoopDriver = ProductionWorkflowDriver(
        repository_root=root,
        state_file=state_file,
        agents=build_agent_registry(args.agent_settings),
        config=config,
        allowed_roots=allowed_roots,
        replace_existing_run_id=replacement_run_id,
    )
    require_production_workflow_loop_driver(driver)
    engine = WorkflowEngine(driver)
    history = _history(state, root)
    driver.checkpoint(state, history)

    for _ in range(100):
        current = state.current_work_unit
        structured_replay = None
        read_blob = None
        if (
            current.kind is WorkUnitKind.FINAL_REVIEW
            and state.effective_protocol_mode is ProtocolMode.STRUCTURED_V2
        ):
            resolution = resolve_resume_state(root, state)
            structured_replay = resolution.replay_result
            read_blob = ArtifactStore(root, state.run_id).read_blob
        recovered_history = _recover_final_review_attestation(
            state,
            history,
            structured_replay,
            read_blob,
        )
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
                    rationale=existing_approval.rationale,
                )
                state, history = decided.state, decided.history
            elif args.gate_decision is not None:
                decided = engine.decide_current_gate(
                    state,
                    history,
                    approved=args.gate_decision,
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
            return WorkflowRunResult(state, history, audit_commit)

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
                        write_content=lambda path, content: driver._write_side_effect_file(
                            path, content, normalized_text=False
                        ),
                    )
                except (ArtifactBridgeError, ArtifactReplayError, PlanHandoffError) as exc:
                    raise WorkflowExecutionError(
                        f"could not create IMPLEMENT handoff: {exc}"
                    ) from exc
                driver.persist_implementation_handoff(handoff, commit_ref)
                logger.info("Implementation handoff ready: %s", handoff)
                return WorkflowRunResult(state, history, commit_ref)
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
            history = WorkflowHistory(
                state.current_work_unit_id,
                findings=carried_findings,
            )
            driver.checkpoint(state, history)
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
            history = WorkflowHistory(
                state.current_work_unit_id,
                findings=carried_findings,
            )
            driver.checkpoint(state, history)
            state = driver.active_state or state
            continue

        carried_findings = driver.carry_forward_native_findings(
            state, history.findings
        )
        state = state.start_final_review_work_unit()
        carried_attestations = history.attestations[-1:]
        history = WorkflowHistory(
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
        )
        driver.checkpoint(state, history)
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


# This compatibility path repairs a historical work-unit projection, not a
# durable side-effect or record-ahead window, so it remains outside recovery.
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
    if any(
        current_history.get(key)
        for key in (
            "findings",
            "attestations",
            "last_claude_fingerprint",  # allowlist:provider -- canonical history field
            "latest_claude_review",  # allowlist:provider -- canonical history field
            "codex_final_report",  # allowlist:provider -- canonical history field
            "active_review_packet",
        )
    ):
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
                    expected_run_id=evidence.run_id,
                    expected_task_file=task_file,
                    expected_task_digest=evidence.task_digest,
                )
                if not isinstance(resumed, WorkflowState):
                    raise ValueError("bound queue recovery requires version-3 state")
                terminal = WorkflowRunResult(
                    resumed, _history(resumed, repository_root)
                )
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
                repository_root=repository_root,
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
            repository_root=Path.cwd(),
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
