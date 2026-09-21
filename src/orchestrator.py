#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from dataclasses import replace
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Callable, get_args, get_type_hints

from agent_adapters import (
    AgentAdapter,
    NativeClaudeReviewAdapter,
    NativeCodexAdapter,
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
    ArtifactBridge,
    branch_discovery_handoff_export_payload,
    finding_payload as finding_payload,
    finding_handoff_export_payload,
)
from artifact_resume import (
    ArtifactResumeError,
    ResumeResolution,
    resolve_resume_state,
)
from artifact_models import (
    ArtifactRecord, BindingPayload, DiagnosticPayload,
    FingerprintKind, GateDecisionPayload, GateTransitionPayload,
    AgentResultPayload, InvocationFailurePayload, ReviewPayload,
    Role,
    BlobReference, ProviderContentPayload,
    ProviderInputMeasurementPayload, canonical_json,
    ProviderAttemptPayload, ProviderUsagePayload,
    BranchDiscoveryCompletedPayload,
    BranchDiscoveryFindingPayload,
    BranchDiscoveryHandoffExportPayload,
    BranchDiscoveryHandoffImportPayload,
    FamilyBindingPayload,
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    FindingSnapshotItem,
    flatten_finding_transition_history,
    SideEffectPayload,
    ScopeExtensionPayload,
    WorkflowEventPayload, WorkflowTransitionPayload,
    RecordType, stable_record_id,
)
from artifact_store import ArtifactStore, ArtifactStoreError
from artifact_replay import (
    ArtifactReplayError,
    ArtifactReplayResult,
    effective_family_binding,
    pending_workflow_event_payload,
    replay_artifacts,
)
from finding_reducer import reduce_findings
from finding_planning import (
    RemediationRoundEvaluation,
    RemediationRoundOutcome,
    evaluate_remediation_round,
)
from finding_convergence import (
    SliceConvergenceEvaluation,
    evaluate_slice_convergence,
)
from provider_input_budget import ProviderInputMeasurement
from review_packets import ReviewPacket
from cli import DEFAULT_AGENTS_FILE, DEFAULT_TASK_FILE
from contracts import (
    AgentRole,
    CodexContractResult,
    CodexStepContract,
    ContractResult,
    FindingAcceptanceMeasurement,
    FindingRecord,
    StepContract,
    ValidationAttestation,
)
from gates import TestChangeEvidence, detect_test_changes
from path_policy import PathPolicyError, resolve_path_within_roots
from git_service import (
    inspect_repository,
    GitTransactionError,
    path_exists_at_commit,
)
from plan_handoff import (
    branch_discovery_task_path,
    extract_implementation_slices,
    implementation_task_path,
    remediation_plan_paths,
    render_branch_discovery_task,
    render_implementation_task,
    render_remediation_plan_task,
)
from repo_changes import (
    FinalReviewEvidenceSnapshot,
    RepositoryChangeError,
    RepositoryChanges,
    RepositorySnapshotProbe,
    build_final_review_evidence_snapshot,
    collect_repository_changes,
    load_final_review_evidence_cache,
    probe_repository_snapshot,
    render_final_review_evidence_cache,
)
from semantic_markdown import SemanticMarkdownError, canonical_semantic_markdown
from state_io import (
    StateSchemaError,
    load_resumable_workflow_state,
    load_workflow_state,
    save_workflow_state,
    write_file,
    write_workflow_checkpoint,
    write_workflow_projection_checkpoint,
    write_workflow_state_projection,
    workflow_checkpoint_path,
)
from task_contract import TaskMode
from workflow import (
    CodexInvocation,
    PersistedNativeReviewerReplay,
    ReviewerInvocation,
    NoWorkflowChangesError,
    WorkflowChanges,
    WorkflowCommitRequest,
    WorkflowContext,
    WorkflowEngine,
    WorkflowCompletionRejected,
    WorkflowExecutionError,
    WorkflowHistory,
    WorkflowRunResult,
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
from workflow_audit_projection import (
    _archive_stale_untracked_audit_reports,
    _attach_managed_audit_paths,
    _audit_projection,
    _authorized_test_approval,
    _hydrate_record_history,
    _is_managed_audit_path,
    _managed_audit_path,
    _managed_slice_scope_pattern,
    _overall_audit_entries,
    _recover_final_review_attestation,
)
from workflow_git_commit import (
    WorkflowGitCommit,
    WorkflowGitCommitDependencies,
)
from workflow_state import (
    GateReason,
    GateDecisionRecord,
    SliceStatus,
    ProtocolMode,
    WorkflowState,
    WorkUnitRecord,
    WorkUnitKind,
    WorkUnitStatus,
    NATIVE_CLAUDE_REVIEW_TRANSPORT,
    NATIVE_CODEX_RESULT_TRANSPORT,
    init_workflow_state,
    BootstrapCheckFact,
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
from error_classification import (
    FailureClass,
    classify_exception,
    enforce_record_start_boundary,
)
from orchestrator_diagnostics import (
    STRUCTURED_OUTPUT_DIAGNOSTIC_CODE,
    STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE,
)
from inbox_watcher import (
    QueueFinalizationDisposition,
    QueueSuccessEvidence,
    WatchTaskDisposition,
    WatchTaskIdentity,
    WatchTaskResult,
    finalize_queue_success,
    load_queue_success_evidence,
    load_watch_identity,
    success_marker_path,
    write_pre_baseline_halt_diagnostic,
    watch_run_has_records,
    watch_identity_path,
    watch_inbox,
)
from orchestrator_version import orchestrator_code_version
import workflow_dry_run
import workflow_production
from workflow_run_setup import (
    _apply_resumed_agent_profiles,
    _context as _context_unbound,
    _current_gate_approval,
    _fresh_state,
    _initialize_finding_handoff as _initialize_finding_handoff_unbound,
    _new_watch_task_control_paths,
    _new_watch_task_preserved_paths,
    _recover_legacy_plan_only_post_gate,
)


logger = logging.getLogger(__name__)


def _evaluate_remediation_discovery_round(
    *,
    remediation_round_number: int,
    source_snapshot: tuple[FindingSnapshotItem, ...],
    implementation_snapshot: tuple[FindingSnapshotItem, ...],
    new_findings: tuple[BranchDiscoveryFindingPayload, ...],
) -> RemediationRoundEvaluation:
    """Evaluate one linked family round with the existing absolute cap."""

    inherited: dict[str, list[str]] = {}
    for item in source_snapshot:
        if item.is_open:
            inherited.setdefault(item.signature, []).append(item.finding_id)
    if not inherited:
        raise ValueError("remediation round source snapshot has no open Finding cohort")
    current = {item.finding_id: item for item in implementation_snapshot}
    unresolved = tuple(
        signature
        for signature, finding_ids in sorted(inherited.items())
        if any(
            finding_id not in current or current[finding_id].is_open
            for finding_id in finding_ids
        )
    )
    return evaluate_remediation_round(
        remediation_round_number=remediation_round_number,
        inherited_signatures=tuple(sorted(inherited)),
        unresolved_inherited_signatures=unresolved,
        new_findings=tuple(
            (item.finding_id, item.summary, item.acceptance_test)
            for item in new_findings
        ),
    )

_context = partial(
    _context_unbound,
    _managed_slice_scope_pattern=_managed_slice_scope_pattern,
)


def run_v3_work_unit(
    engine: WorkflowEngine,
    state: WorkflowState,
    context: WorkflowContext,
    history: WorkflowHistory | None = None,
) -> WorkflowRunResult:
    return engine.run_current_work_unit(state, context, history)


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


ProductionWorkflowLoopDriver = workflow_production.ProductionWorkflowLoopDriver
PRODUCTION_LOOP_INTERNAL_DRIVER_METHODS = (
    workflow_production.PRODUCTION_LOOP_INTERNAL_DRIVER_METHODS
)
require_production_workflow_loop_driver = (
    workflow_production.require_production_workflow_loop_driver
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
        validated_store: ArtifactStore | None = None,
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
        self._validated_startup_store = validated_store
        self._replace_existing_run_id = replace_existing_run_id
        self._side_effect_boundary_observer = side_effect_boundary_observer
        self._reported_code_version_changes: set[tuple[str, str]] = set()

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
                prepare_completion_finding_handoff=(
                    self._prepare_completion_finding_handoff
                ),
                prepare_completion_family_handoff=(
                    self._prepare_completion_family_handoff
                ),
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
            self.active_state = resolve_resume_state(
                self.root,
                state.run_id,
                validated_store=self._artifact_bridge.store,
            ).state
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
                startup_store = self._validated_startup_store
                if startup_store is not None and (
                    startup_store.repository_root != self.root
                    or startup_store.run_id != state.run_id
                ):
                    raise WorkflowExecutionError(
                        "validated startup artifact store belongs to another chain"
                    )
                self._artifact_bridge = ArtifactBridge(
                    startup_store
                    or ArtifactStore(
                        self.root,
                        state.run_id,
                        progress_threshold_seconds=(
                            self.config.phase_progress_threshold_seconds
                        ),
                    )
                )
                self._validated_startup_store = None
        else:
            self._artifact_bridge = None

    @staticmethod
    def _matches_baseline_initialization_prefix(
        records: tuple[ArtifactRecord, ...], state: WorkflowState
    ) -> bool:
        return matches_baseline_initialization_prefix(records, state)

    def assert_structured_decision_context(self) -> None:
        """Revalidate the authoritative chain before an external side effect."""
        state = self.active_state
        if state is None or state.effective_protocol_mode is ProtocolMode.LEGACY_STATE_V3:
            return
        try:
            bridge = self._artifact_bridge
            if bridge is None:
                raise WorkflowExecutionError(
                    "structured decision context has no artifact store"
                )
            resolution = resolve_resume_state(
                self.root, state, validated_store=bridge.store
            )
            if self._reconcile_pending_side_effects(
                resolution.state, resolution.replay_result
            ):
                resolution = resolve_resume_state(
                    self.root,
                    resolution.state,
                    validated_store=bridge.store,
                )
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
        self._record_orchestrator_code_version_change(state)

    def _record_orchestrator_code_version_change(
        self, state: WorkflowState
    ) -> None:
        bridge = self._artifact_bridge
        if bridge is None or state.task_digest is None:
            return
        replay = replay_artifacts(bridge.store.current_chain(), state.run_id)
        profile = replay.run_profile
        if profile is None:
            return
        persisted = profile.orchestrator_code_version
        current = orchestrator_code_version()
        if persisted == current:
            return
        reason = (
            "Orchestrator code version changed | "
            f"persisted={persisted} current={current}"
        )
        version_pair = (persisted, current)
        if version_pair not in self._reported_code_version_changes:
            logger.warning(
                "Orchestrator code version changed before provider start: "
                "persisted=%s current=%s; request changes will open a new round "
                "only while binding_fingerprint remains unchanged.",
                persisted,
                current,
            )
            self._reported_code_version_changes.add(version_pair)
        if any(
            isinstance(record.payload, DiagnosticPayload)
            and record.payload.role is Role.ORCHESTRATOR
            and record.payload.reason == reason
            for record in replay.records
        ):
            return
        prior = tuple(
            record
            for record in replay.records
            if isinstance(record.payload, DiagnosticPayload)
            and record.logical_id == "orchestrator-code-version-diff"
        )
        bridge.append(
            DiagnosticPayload(
                role=Role.ORCHESTRATOR,
                work_unit_id=str(state.current_work_unit_id),
                attempt=len(prior) + 1,
                output_sha256=hashlib.sha256(reason.encode("utf-8")).hexdigest(),
                reason=reason,
            ),
            logical_id="orchestrator-code-version-diff",
            idempotency_key=(
                f"orchestrator-code-version-diff:{persisted}:{current}"
            ),
            fingerprint_sha256=state.task_digest,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )

    def _persist_workflow_snapshot(self, state: WorkflowState) -> None:
        self._persistence_boundary()._persist_workflow_snapshot(state)

    def _persist_request_identity_prerequisites(
        self, state: WorkflowState
    ) -> None:
        self._persistence_boundary()._persist_request_identity_prerequisites(state)

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
        invocation_id = None
        if decision.reason is GateReason.QUOTA_RESUME_DIFF:
            invocation_id = next(
                (
                    record.payload.invocation_id
                    for record in reversed(bridge.store.current_chain())
                    if isinstance(record.payload, InvocationFailurePayload)
                    and record.payload.work_unit_id == unit_id
                ),
                None,
            )
            if invocation_id is None:
                raise WorkflowExecutionError(
                    "quota-resume-diff decision has no authoritative invocation failure"
                )
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
                invocation_id=invocation_id,
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
        chain = bridge.store.current_chain()
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

    @staticmethod
    def _provider_attempt_failure_path(response_path: Path) -> Path:
        return response_path.with_suffix(response_path.suffix + ".failure.json")

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
            for record in bridge.store.current_chain()
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
        bridge = self._artifact_bridge
        resolution = resolve_resume_state(
            self.root,
            state.run_id,
            validated_store=(None if bridge is None else bridge.store),
        )
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
            request_sequence=projected.current_work_unit.request_sequence,
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
                            operation_instance=f"request:{invocation.request_sequence}",
                            durable_response_path=raw_path,
                        ),
                        terminal=self._finish_provider_attempt,
                        durable_response_path=lambda handle: handle[2],
                        failure_path=self._provider_attempt_failure_path,
                    )
                    if self._artifact_bridge is not None
                    else None
                ),
                accepted_output_callback=lambda accepted: (
                    self.persist_native_codex_contract(
                        accepted,
                        invocation.request_sequence,
                        invocation.previous_findings,
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
                bridge.store.current_chain(), state.run_id, allow_empty=True
            )
            projected = reduce_findings(replay).ledger.findings
        except (ArtifactReplayError, ValueError) as exc:
            raise WorkflowExecutionError(
                f"authoritative finding replay failed: {exc}"
            ) from exc
        return projected

    def evaluate_slice_finding_convergence(
        self,
        state: WorkflowState,
        *,
        round_number: int,
    ) -> SliceConvergenceEvaluation:
        """Evaluate dormant E5 policy against the accepted record prefix."""

        active = self.active_state
        bridge = self._artifact_bridge
        if (
            active is None
            or bridge is None
            or active.run_id != state.run_id
            or active.current_work_unit_id != state.current_work_unit_id
            or state.current_work_unit.kind is not WorkUnitKind.SLICE
        ):
            raise WorkflowExecutionError(
                "Slice convergence replay lacks its immutable work-unit binding"
            )
        return evaluate_slice_convergence(
            bridge.store.current_chain(),
            run_id=state.run_id,
            slice_id=state.current_slice_id,
            work_unit_id=state.current_work_unit_id,
            round_number=round_number,
            approved_plan_commit=state.approved_plan_commit,
        )

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
                bridge.store.current_chain(), state.run_id, allow_empty=True
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
                f"{invocation.step.value}-request-{invocation.request_sequence:04d}.json"
            )
        )

    def _native_reviewer_response_path(self, invocation: ReviewerInvocation) -> Path:
        state = self.active_state
        if state is None:
            raise WorkflowExecutionError("native reviewer response has no active state")
        return (
            self.root
            / ".orchestrator"
            / "artifacts"
            / state.run_id
            / "native-review-responses"
            / (
                f"work-unit-{invocation.work_unit_id:04d}-"
                f"{invocation.step.value}-request-{invocation.request_sequence:04d}.json"
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
                f"{invocation.step.value}-request-{invocation.request_sequence:04d}.json"
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
        request_id: str | None = None,
        request_findings: tuple[FindingRecord, ...] | None = None,
    ) -> object | None:
        path = self._native_agent_request_path(invocation)
        if request_id is not None:
            prefixes = (
                f"work-unit-{invocation.work_unit_id:04d}-"
                f"{invocation.step.value}-request-",
                # Compatibility with persisted requests created before B152.
                f"work-unit-{invocation.work_unit_id:04d}-"
                f"{invocation.step.value}-round-",
            )
            matches: list[Path] = []
            if path.parent.is_dir():
                for candidate in sorted(path.parent.iterdir()):
                    prefix = next(
                        (
                            item
                            for item in prefixes
                            if candidate.name.startswith(item)
                        ),
                        None,
                    )
                    sequence_text = (
                        ""
                        if prefix is None
                        else candidate.name.removeprefix(prefix).removesuffix(
                            ".json"
                        )
                    )
                    if (
                        prefix is None
                        or not candidate.name.endswith(".json")
                        or not sequence_text.isdigit()
                        or not candidate.is_file()
                    ):
                        continue
                    try:
                        envelope = json.loads(candidate.read_text(encoding="utf-8"))
                        canonical_request = envelope["canonical_request"]
                        candidate_request = json.loads(canonical_request)
                        candidate_request_id = candidate_request["request_id"]
                    except (
                        KeyError,
                        OSError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                    ) as exc:
                        raise WorkflowExecutionError(
                            "native agent request recovery artifact cannot be "
                            f"identified: {candidate.name}: {exc}"
                        ) from exc
                    if candidate_request_id == request_id:
                        matches.append(candidate)
            if len(matches) > 1:
                raise WorkflowExecutionError(
                    "native agent request recovery has duplicate request-id "
                    f"authority: {request_id}"
                )
            if not matches:
                return None
            path = matches[0]
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
            context = rebuilt.bound_context.context
            if request_findings is not None:
                findings_by_id = {
                    item.finding_id: item for item in request_findings
                }
                offered_ids = tuple(
                    item["finding_id"] for item in request_document["open_findings"]
                )
                if any(
                    finding_id not in findings_by_id
                    for finding_id in offered_ids
                ):
                    raise ValueError(
                        "persisted native agent request finding subset is not "
                        "present at its measured ledger"
                    )
                context = replace(
                    context,
                    previous_findings=tuple(
                        findings_by_id[finding_id]
                        for finding_id in offered_ids
                    ),
                )
            context = replace(
                context,
                current_fingerprint=fingerprint,
                contract=replace(
                    context.contract,
                    round_number=request_document["codex_contract"][  # allowlist:provider -- canonical field
                        "round_number"
                    ],
                    request_sequence=request_document["codex_contract"].get(  # allowlist:provider -- canonical field
                        "request_sequence",
                        request_document["codex_contract"]["round_number"],  # allowlist:provider -- canonical field
                    ),
                ),
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

    def _write_native_agent_raw_response(self, path: Path, content: str) -> None:
        self._write_side_effect_file(path, content, normalized_text=False)

    def _write_native_codex_raw_response(self, path: Path, content: str) -> None:
        self._write_native_agent_raw_response(path, content)

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
        raw_response_path = self._native_reviewer_response_path(invocation)
        return run_native_review_agent_checked(
                adapter=native_adapter,
                bundle=invocation.native_request,
                log_prefix=(
                    f"work-unit-{invocation.work_unit_id:04d}-"
                    f"{invocation.step.value}-request-{invocation.request_sequence:04d}"
                ),
                config=self.config,
                log_dir=self.log_dir,
                raw_response_path=raw_response_path,
                write_file=self._write_native_agent_raw_response,
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
                            operation_instance=f"request:{invocation.request_sequence}",
                            durable_response_path=raw_response_path,
                        ),
                        terminal=self._finish_provider_attempt,
                        durable_response_path=lambda handle: handle[2],
                        failure_path=self._provider_attempt_failure_path,
                    )
                    if self._artifact_bridge is not None
                    else None
                ),
                accepted_output_callback=lambda output: (
                    self.persist_native_review_contract(
                        output,
                        invocation.fingerprint,
                        invocation.round_number,
                        invocation.request_sequence,
                        invocation.previous_findings,
                    )
                ),
                pre_accept_output_callback=invocation.pre_accept_output_callback,
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
        request_sequence: int,
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
            request_sequence=request_sequence,
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
        request_sequence: int,
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
            for record in (chain if chain is not None else bridge.store.current_chain())
            if isinstance(record.payload, ProviderContentPayload)
            and record.payload.role is role
            and record.payload.work_unit_id == str(work_unit_id)
            and record.payload.round_number == request_sequence
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
            state.branch_review_base_commit
            if state.current_work_unit.kind is WorkUnitKind.BRANCH_DISCOVERY
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
        request_sequence: int,
        previous_findings: tuple[FindingRecord, ...],
        *,
        recovery_fingerprint: str | None = None,
    ) -> None:
        self._persistence_boundary().persist_native_implementer_contract(
            output,
            request_sequence,
            previous_findings,
            recovery_fingerprint=recovery_fingerprint,
        )

    def persist_scope_extension(
        self,
        state: WorkflowState,
        payload: ScopeExtensionPayload,
    ) -> None:
        self._persistence_boundary().persist_scope_extension(state, payload)

    def persist_native_review_contract(
        self,
        output: NativeAgentReviewOutput,
        fingerprint: str,
        round_number: int,
        request_sequence: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        self._persistence_boundary().persist_native_review_contract(
            output,
            fingerprint,
            round_number,
            request_sequence,
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

    def persist_finding_acceptance_measurement(
        self,
        finding: FindingRecord,
        measurement: FindingAcceptanceMeasurement,
    ) -> None:
        self._persistence_boundary().persist_finding_acceptance_measurement(
            finding, measurement
        )

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
        state = self.active_state
        if state is None:
            raise WorkflowExecutionError(
                "invocation failure diagnostic has no active workflow"
            )
        attempt = len(state.current_work_unit.invocation_failures) + 1
        round_number = state.current_work_unit.round_number
        request_sequence = state.current_work_unit.request_sequence
        path = self.log_dir / (
            f"{state.run_id}.work-unit-{payload.work_unit_id}."
            f"request-{request_sequence:04d}.attempt-{attempt:04d}.failure.json"
        )
        document = {
            "version": 1,
            "run_id": state.run_id,
            "work_unit_id": payload.work_unit_id,
            "round_number": round_number,
            "request_sequence": request_sequence,
            "attempt_number": attempt,
            "invocation_id": payload.invocation_id,
            "role": payload.role.value,
            "step": payload.step,
            "failure_kind": payload.failure_kind,
            "failure_class": payload.failure_class,
            "diagnostic_code": payload.diagnostic_code,
            "orchestrator_diagnostic": payload.orchestrator_diagnostic,
            "provider_diagnostic_subtype": (
                STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE
                if payload.diagnostic_code == STRUCTURED_OUTPUT_DIAGNOSTIC_CODE
                else None
            ),
            "provider_text": payload.provider_text,
            "provider_text_sha256": payload.provider_text_sha256,
            "provider_text_bytes": payload.provider_text_bytes,
            "technical_text": payload.technical_text,
            "technical_text_sha256": payload.technical_text_sha256,
            "technical_text_bytes": payload.technical_text_bytes,
            "recorded_at": payload.decision_at_utc,
        }
        self._write_immutable_file(
            path, canonical_json(document).decode("utf-8")
        )

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
        _state: WorkflowState | None = None,
    ) -> tuple[str, str] | None:
        """Append the export before publishing task bytes, or recover it exactly."""
        bridge = self._artifact_bridge
        state = _state or self.active_state
        if bridge is None or state is None:
            return None
        chain = bridge.store.current_chain()
        replay = replay_artifacts(chain, state.run_id)
        transitions = flatten_finding_transition_history(replay.records)
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

    def _prepare_completion_finding_handoff(
        self, state: WorkflowState
    ) -> tuple[str, str] | None:
        """Record a linked IMPLEMENT handoff before PLAN_ONLY completion."""

        return self.prepare_finding_handoff(
            plan_task_path=Path(state.task_file),
            work_plan_path=state.work_plan_path or "",
            target_branch=state.target_branch or state.branch,
            approved_plan_commit=state.approved_plan_commit or "",
            _state=state,
        )

    def _assert_remediation_round_can_advance(
        self,
        replay: ArtifactReplayResult,
    ) -> None:
        """Apply the existing E4 evaluator before opening another round."""

        discovery_import = next(
            (
                record
                for record in replay.records
                if isinstance(record.payload, BranchDiscoveryHandoffImportPayload)
                and record.payload.target_execution_mode
                == TaskMode.BRANCH_DISCOVERY.value
            ),
            None,
        )
        if discovery_import is None:
            return
        discovery = next(
            (
                record
                for record in replay.records
                if isinstance(record.payload, BranchDiscoveryCompletedPayload)
            ),
            None,
        )
        if discovery is None:
            raise WorkflowExecutionError(
                "remediation round evaluation requires BRANCH_DISCOVERY_COMPLETED"
            )
        try:
            source_replay = replay_artifacts(
                ArtifactStore(
                    self.root,
                    discovery_import.payload.source_run_id,
                ).load_chain(),
                discovery_import.payload.source_run_id,
            )
            plan_import = next(
                (
                    record.payload
                    for record in source_replay.records
                    if isinstance(record.payload, FindingHandoffImportPayload)
                ),
                None,
            )
            plan_replay = (
                source_replay
                if plan_import is None
                else replay_artifacts(
                    ArtifactStore(self.root, plan_import.source_run_id).load_chain(),
                    plan_import.source_run_id,
                )
            )
            source_snapshot = next(
                (
                    record
                    for record in plan_replay.records
                    if isinstance(
                        record.payload, BranchDiscoveryHandoffImportPayload
                    )
                    and record.payload.target_execution_mode
                    == TaskMode.PLAN_ONLY.value
                ),
                None,
            )
        except (
            ArtifactReplayError,
            ArtifactStoreError,
            OSError,
            StopIteration,
        ) as exc:
            raise WorkflowExecutionError(
                f"could not resolve the remediation round snapshot: {exc}"
            ) from exc
        if source_snapshot is None:
            return
        # Discovery edges increment the cycle; PLAN_ONLY -> IMPLEMENT preserves it.
        # With cycle one bound at entry, remediation PLAN_ONLY snapshots are the
        # odd cycles 3, 5, ...; floor division still maps them to rounds 1, 2, ... .
        remediation_round_number = source_snapshot.payload.cycle_number // 2
        if remediation_round_number < 1:
            raise WorkflowCompletionRejected(
                "remediation family cycle cannot identify a positive round"
            )
        try:
            evaluation = _evaluate_remediation_discovery_round(
                remediation_round_number=remediation_round_number,
                source_snapshot=source_snapshot.payload.finding_snapshot,
                implementation_snapshot=(
                    discovery_import.payload.finding_snapshot
                ),
                new_findings=discovery.payload.new_findings,
            )
        except ValueError as exc:
            raise WorkflowCompletionRejected(
                f"remediation round evaluation rejected the family: {exc}"
            ) from exc
        if evaluation.outcome is not RemediationRoundOutcome.NEXT_ROUND:
            raise WorkflowCompletionRejected(evaluation.log_message)

    def _branch_discovery_handoff_material(
        self,
        state: WorkflowState,
        replay: ArtifactReplayResult,
        source_completion_record_id: str,
    ) -> tuple[Path, bytes, str, FamilyBindingPayload, BindingPayload, str]:
        """Derive the immutable child identity, task bytes, and family edge."""

        if state.execution_mode != TaskMode.IMPLEMENT.value:
            raise WorkflowExecutionError(
                "only an IMPLEMENT run may chain BRANCH_DISCOVERY"
            )
        final_binding_record = next(
            (
                record
                for record in reversed(replay.records)
                if isinstance(record.payload, BindingPayload)
                and record.payload.binding_kind == "commit"
            ),
            None,
        )
        if final_binding_record is None:
            raise WorkflowExecutionError(
                "BRANCH_DISCOVERY handoff requires the final implementation binding"
            )
        final_binding = final_binding_record.payload
        identity = replay.run_identity
        if identity is None:
            raise WorkflowExecutionError(
                "BRANCH_DISCOVERY handoff requires the source RunIdentity"
            )
        source_binding = effective_family_binding(replay)
        scope_paths = tuple(
            sorted(
                {
                    *(
                        ()
                        if source_binding is None
                        else source_binding.family_authorized_change_set
                    ),
                    *state.branch_review_authorized_change_set,
                }
            )
        )
        if not scope_paths:
            raise WorkflowExecutionError(
                "BRANCH_DISCOVERY handoff requires an authorized change set"
            )
        family_id = (
            source_binding.family_id
            if source_binding is not None
            else "family-"
            + hashlib.sha256(
                f"{state.run_id}:{identity.branch_base}".encode("utf-8")
            ).hexdigest()[:32]
        )
        family_binding = FamilyBindingPayload(
            family_id=family_id,
            family_base_commit=(
                source_binding.family_base_commit
                if source_binding is not None
                else identity.branch_base
            ),
            family_authorized_change_set=scope_paths,
            predecessor_run_id=state.run_id,
            predecessor_head_record_id=source_completion_record_id,
            cycle_number=(
                1 if source_binding is None else source_binding.cycle_number + 1
            ),
            current_plan_commit=(
                state.approved_plan_commit
                if state.approved_plan_commit is not None
                else (
                    None
                    if source_binding is None
                    else source_binding.current_plan_commit
                )
            ),
            current_implementation_commit=final_binding.target,
        )
        source_task = Path(state.task_file).resolve()
        try:
            source_task.relative_to(self.root)
        except ValueError:
            source_task = self.root / "inbox" / source_task.name
        target = branch_discovery_task_path(source_task)
        try:
            target_relative = target.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise WorkflowExecutionError(
                "BRANCH_DISCOVERY handoff task must be inside the repository"
            ) from exc
        export_record_id = stable_record_id(
            state.run_id,
            RecordType.BRANCH_DISCOVERY_HANDOFF_EXPORT,
            "branch-discovery-handoff-export",
            1,
        )
        target_run_id = "branch-discovery-" + hashlib.sha256(
            f"{state.run_id}:{source_completion_record_id}:{target_relative}".encode(
                "utf-8"
            )
        ).hexdigest()[:32]
        task_bytes = render_branch_discovery_task(
            target_branch=state.target_branch or state.branch,
            scope_paths=scope_paths,
            finding_handoff=(state.run_id, export_record_id),
        ).encode("utf-8")
        return (
            target,
            task_bytes,
            target_run_id,
            family_binding,
            final_binding,
            final_binding_record.fingerprint.sha256,
        )

    def _remediation_plan_handoff_material(
        self,
        state: WorkflowState,
        replay: ArtifactReplayResult,
        *,
        source_head_record_id: str | None = None,
    ) -> tuple[
        Path,
        bytes,
        str,
        FamilyBindingPayload,
        BranchDiscoveryCompletedPayload,
        str,
        str,
    ]:
        """Derive the PLAN_ONLY child bound to an open discovery snapshot."""

        if state.execution_mode != TaskMode.BRANCH_DISCOVERY.value:
            raise WorkflowExecutionError(
                "only a BRANCH_DISCOVERY run may chain remediation planning"
            )
        if source_head_record_id is not None:
            head_position = next(
                (
                    index
                    for index, record in enumerate(replay.records)
                    if record.record_id == source_head_record_id
                ),
                None,
            )
            if head_position is None:
                raise WorkflowExecutionError(
                    "remediation PLAN_ONLY export source head is not in the discovery run"
                )
            replay = replace(
                replay,
                records=replay.records[: head_position + 1],
                head_record_id=source_head_record_id,
            )
        source_binding = effective_family_binding(replay)
        if source_binding is None or replay.head_record_id is None:
            raise WorkflowExecutionError(
                "remediation PLAN_ONLY handoff requires the discovery family binding"
            )
        discovery_record = next(
            (
                record
                for record in reversed(replay.records)
                if isinstance(record.payload, BranchDiscoveryCompletedPayload)
            ),
            None,
        )
        if discovery_record is None:
            raise WorkflowExecutionError(
                "remediation PLAN_ONLY handoff requires BRANCH_DISCOVERY_COMPLETED"
            )
        if not reduce_findings(replay).open_set.finding_ids:
            raise WorkflowExecutionError(
                "terminal branch discovery cannot create a remediation handoff"
            )
        self._assert_remediation_round_can_advance(replay)
        source_task = Path(state.task_file).resolve()
        try:
            source_task.relative_to(self.root)
        except ValueError:
            source_task = self.root / "inbox" / source_task.name
        target_cycle = source_binding.cycle_number + 1
        target, work_plan_path = remediation_plan_paths(
            source_task,
            cycle_number=target_cycle,
        )
        try:
            target_relative = target.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise WorkflowExecutionError(
                "remediation PLAN_ONLY handoff task must be inside the repository"
            ) from exc
        export_record_id = stable_record_id(
            state.run_id,
            RecordType.BRANCH_DISCOVERY_HANDOFF_EXPORT,
            "branch-discovery-handoff-export",
            1,
        )
        target_run_id = "remediation-plan-" + hashlib.sha256(
            f"{state.run_id}:{replay.head_record_id}:{target_relative}".encode("utf-8")
        ).hexdigest()[:32]
        task_bytes = render_remediation_plan_task(
            work_plan_path=work_plan_path,
            target_branch=state.target_branch or state.branch,
            scope_paths=source_binding.family_authorized_change_set,
            finding_handoff=(state.run_id, export_record_id),
        ).encode("utf-8")
        family_binding = FamilyBindingPayload(
            family_id=source_binding.family_id,
            family_base_commit=source_binding.family_base_commit,
            family_authorized_change_set=tuple(
                sorted(
                    {
                        *source_binding.family_authorized_change_set,
                        work_plan_path,
                    }
                )
            ),
            predecessor_run_id=state.run_id,
            predecessor_head_record_id=replay.head_record_id,
            cycle_number=target_cycle,
            current_plan_commit=source_binding.current_plan_commit,
            current_implementation_commit=(
                source_binding.current_implementation_commit
            ),
        )
        return (
            target,
            task_bytes,
            target_run_id,
            family_binding,
            discovery_record.payload,
            discovery_record.record_id,
            discovery_record.fingerprint.sha256,
        )

    def _prepare_completion_family_handoff(
        self,
        state: WorkflowState,
        source_completion_record_id: str,
    ) -> BranchDiscoveryHandoffExportPayload | None:
        """Prepare the next family edge for atomic pre-completion append."""

        bridge = self._artifact_bridge
        if bridge is None:
            raise WorkflowExecutionError(
                "family handoff requires the artifact bridge"
            )
        replay = replay_artifacts(bridge.store.current_chain(), state.run_id)
        if state.execution_mode == TaskMode.BRANCH_DISCOVERY.value:
            if not reduce_findings(replay).open_set.finding_ids:
                return None
            (
                target,
                task_bytes,
                target_run_id,
                family_binding,
                discovery,
                discovery_record_id,
                _,
            ) = self._remediation_plan_handoff_material(state, replay)
            return branch_discovery_handoff_export_payload(
                replay,
                discovery_review_record_id=discovery_record_id,
                validation_attestation_record_id=(
                    discovery.validation_attestation_record_id
                ),
                reviewed_head_commit=discovery.reviewed_head_commit,
                family_binding=family_binding,
                target_task_path=target.relative_to(self.root).as_posix(),
                target_task_bytes=task_bytes,
                target_run_identity=target_run_id,
                target_execution_mode=TaskMode.PLAN_ONLY.value,
            )
        if state.execution_mode != TaskMode.IMPLEMENT.value:
            return None
        (
            target,
            task_bytes,
            target_run_id,
            family_binding,
            final_binding,
            _,
        ) = self._branch_discovery_handoff_material(
            state,
            replay,
            source_completion_record_id,
        )
        try:
            target_relative = target.relative_to(self.root).as_posix()
        except ValueError as exc:  # pragma: no cover - guarded by material builder
            raise WorkflowExecutionError(
                "BRANCH_DISCOVERY handoff task must be inside the repository"
            ) from exc
        return branch_discovery_handoff_export_payload(
            replay,
            discovery_review_record_id=None,
            validation_attestation_record_id=final_binding.attestation_id,
            reviewed_head_commit=final_binding.target,
            family_binding=family_binding,
            target_task_path=target_relative,
            target_task_bytes=task_bytes,
            target_run_identity=target_run_id,
            target_execution_mode=TaskMode.BRANCH_DISCOVERY.value,
            source_completion_record_id=source_completion_record_id,
            remediation_cohort_checkpoint_record_id=None,
            allow_pending_source_completion=True,
        )

    def publish_family_handoff(self, state: WorkflowState) -> Path | None:
        """Publish one bound child task exclusively through the side-effect ledger."""

        if state.execution_mode not in {
            TaskMode.IMPLEMENT.value,
            TaskMode.BRANCH_DISCOVERY.value,
        }:
            raise WorkflowExecutionError(
                "only IMPLEMENT or BRANCH_DISCOVERY may publish a family handoff"
            )
        bridge = self._artifact_bridge
        if bridge is None:
            raise WorkflowExecutionError(
                "BRANCH_DISCOVERY handoff requires the artifact bridge"
            )
        replay = replay_artifacts(bridge.store.current_chain(), state.run_id)
        target_mode = (
            TaskMode.BRANCH_DISCOVERY.value
            if state.execution_mode == TaskMode.IMPLEMENT.value
            else TaskMode.PLAN_ONLY.value
        )
        exports = tuple(
            record
            for record in replay.records
            if isinstance(record.payload, BranchDiscoveryHandoffExportPayload)
            and record.payload.target_execution_mode == target_mode
        )
        if (
            state.execution_mode == TaskMode.BRANCH_DISCOVERY.value
            and not reduce_findings(replay).open_set.finding_ids
        ):
            if exports:
                raise WorkflowExecutionError(
                    "terminal BRANCH_DISCOVERY run unexpectedly contains a PLAN_ONLY export"
                )
            return None
        if len(exports) != 1:
            raise WorkflowExecutionError(
                f"completed {state.execution_mode} run requires exactly one "
                f"{target_mode} export"
            )
        export_record = exports[0]
        export = export_record.payload
        if target_mode == TaskMode.BRANCH_DISCOVERY.value:
            assert export.source_completion_record_id is not None
            target, task_bytes, target_run_id, _, _, fingerprint = (
                self._branch_discovery_handoff_material(
                    state,
                    replay,
                    export.source_completion_record_id,
                )
            )
        else:
            target, task_bytes, target_run_id, _, _, _, fingerprint = (
                self._remediation_plan_handoff_material(
                    state,
                    replay,
                    source_head_record_id=export.source_head_record_id,
                )
            )
        if (
            target.relative_to(self.root).as_posix() != export.target_task_path
            or hashlib.sha256(task_bytes).hexdigest() != export.target_task_sha256
            or target_run_id != export.target_run_identity
        ):
            raise WorkflowExecutionError(
                "persisted BRANCH_DISCOVERY export differs from its child task"
            )
        identity = WatchTaskIdentity(
            run_id=target_run_id,
            task_digest=export.target_task_sha256,
        )
        identity_content = json.dumps(identity.to_dict(), sort_keys=True) + "\n"
        self._write_side_effect_file(
            watch_identity_path(target),
            identity_content,
            normalized_text=False,
            fingerprint=fingerprint,
        )
        self._write_side_effect_file(
            target,
            task_bytes.decode("utf-8"),
            normalized_text=False,
            fingerprint=fingerprint,
        )
        return target

    def _read_semantic_plan_artifact(self, candidate: Path) -> None:
        """Read and validate the plan operation protected by its two mitigations."""

        if not candidate.is_symlink() and candidate.is_file():
            content = candidate.read_text(encoding="utf-8")
            canonical_semantic_markdown(
                content,
                path=self.active_state.work_plan_path,
                remove_appendix=True,
            )

    def _collect_change_path_selection(self) -> tuple[tuple[str, ...], str | None]:
        """Select semantic evidence paths while owning both plan mitigations."""

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
                    self._read_semantic_plan_artifact(candidate)
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
        return semantic_paths, raw_plan_artifact

    def _reuse_final_review_evidence(
        self,
        start_commit: str,
        semantic_paths: tuple[str, ...],
        excluded_control_paths: tuple[str, ...],
        final_review: bool,
        audit_path: str | None,
    ) -> FinalReviewEvidenceSnapshot | None:
        """Reuse only evidence matching the current repository and boundaries."""

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
        return snapshot

    def _build_final_review_snapshot(
        self,
        changes: RepositoryChanges,
        probe: RepositorySnapshotProbe,
        start_commit: str,
        semantic_paths: tuple[str, ...],
        excluded_control_paths: tuple[str, ...],
        audit_path: str | None,
        elapsed_ms: int,
    ) -> FinalReviewEvidenceSnapshot:
        """Build the snapshot operation protected by its ValueError translation."""

        return build_final_review_evidence_snapshot(
            changes,
            probe,
            start_commit=start_commit,
            semantic_markdown_paths=semantic_paths,
            excluded_paths=excluded_control_paths,
            audit_path=audit_path,
            collection_elapsed_ms=elapsed_ms,
        )

    def _final_review_cache_target_digest(self, cache_path: Path) -> str:
        """Read the cache target state protected by the invalid-digest mitigation."""

        return file_state_digest(cache_path)

    def _collect_fresh_final_review_evidence(
        self,
        start_commit: str,
        semantic_paths: tuple[str, ...],
        raw_plan_artifact: str | None,
        excluded_control_paths: tuple[str, ...],
        final_review: bool,
        audit_path: str | None,
        snapshot: FinalReviewEvidenceSnapshot | None,
    ) -> tuple[RepositoryChanges, FinalReviewEvidenceSnapshot | None]:
        """Collect fresh evidence while owning both collection mitigations."""

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
                snapshot = self._build_final_review_snapshot(
                    changes,
                    probe,
                    start_commit,
                    semantic_paths,
                    excluded_control_paths,
                    audit_path,
                    elapsed_ms,
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
                current_digest = self._final_review_cache_target_digest(cache_path)
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
        return changes, snapshot

    def collect_changes(self, start_commit: str) -> WorkflowChanges:
        semantic_paths, raw_plan_artifact = self._collect_change_path_selection()
        excluded_control_paths = _bound_task_control_paths(
            self.root, self.active_state
        )
        final_review = (
            self.active_state is not None
            and self.active_state.current_work_unit.kind
            is WorkUnitKind.BRANCH_DISCOVERY
        )
        audit_path = (
            self.active_state.audit_report_path if final_review else None
        )
        snapshot = self._reuse_final_review_evidence(
            start_commit,
            semantic_paths,
            excluded_control_paths,
            final_review,
            audit_path,
        )
        if snapshot is not None:
            changes = snapshot.repository_changes(self.root)
        else:
            changes, snapshot = self._collect_fresh_final_review_evidence(
                start_commit,
                semantic_paths,
                raw_plan_artifact,
                excluded_control_paths,
                final_review,
                audit_path,
                snapshot,
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
        for record in reversed(bridge.store.current_chain()):
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

    def path_exists_at_commit(self, commit: str, path: str) -> bool:
        return path_exists_at_commit(
            self.root,
            commit=commit,
            relative_path=path,
        )

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
        new_request_identity = WorkflowPersistence.is_request_identity_checkpoint(
            self.active_state,
            state,
        )
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
            if new_request_identity:
                self._persist_request_identity_prerequisites(persisted)
            self._persist_structured_baseline(persisted)
            self._project_audit(persisted, history)
        except WorkflowCompletionRejected:
            raise
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
        bridge = self._artifact_bridge
        resolution = resolve_resume_state(
            self.root,
            persisted.run_id,
            validated_store=(None if bridge is None else bridge.store),
        )
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
            request_sequence=projected.current_work_unit.request_sequence,
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


def _history(
    state: WorkflowState,
    repository_root: Path | None = None,
    *,
    validated_store: ArtifactStore | None = None,
) -> WorkflowHistory:
    history = WorkflowHistory(state.current_work_unit_id)
    if state.runtime_history is None:
        return history
    try:
        raw = dict(state.runtime_history)
        if not (
            raw
            and all(
                isinstance(key, str)
                and key.isdigit()
                and isinstance(value, dict)
                and "workflow_event_record_refs" in value
                for key, value in raw.items()
            )
        ):
            current = (
                raw.get("current")
                if set(raw) == {"current", "archive"}
                else raw
            )
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
            root = Path(repository_root).resolve()
            store = validated_store or ArtifactStore(root, state.run_id)
            if store.repository_root != root or store.run_id != state.run_id:
                raise ArtifactStoreError(
                    "validated startup artifact store belongs to another chain"
                )
            replay = replay_artifacts(
                (
                    store.current_chain()
                    if validated_store is not None
                    else store.load_chain()
                ),
                state.run_id,
                require_content_authority=True,
                require_review_authority=True,
                allow_incomplete_review_tail=True,
                allow_finding_import_bootstrap=True,
            )
            history = _hydrate_record_history(history, replay, store.read_blob)
            history = _recover_final_review_attestation(
                state, history, replay, store.read_blob
            )
        except (
            ArtifactReplayError,
            ArtifactStoreError,
            UnicodeError,
            ValueError,
        ) as exc:
            raise WorkflowExecutionError(
                f"record-backed workflow history projection is invalid: {exc}"
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


_initialize_finding_handoff = partial(
    _initialize_finding_handoff_unbound,
    _history_payload=_history_payload,
)


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


def _production_workflow_dependencies(
) -> workflow_production.ProductionWorkflowDependencies:
    """Bind the extracted production entry to orchestrator-owned implementations."""
    return workflow_production.ProductionWorkflowDependencies(
        driver_factory=ProductionWorkflowDriver,
        apply_resumed_agent_profiles=_apply_resumed_agent_profiles,
        archive_stale_untracked_audit_reports=_archive_stale_untracked_audit_reports,
        attach_managed_audit_paths=_attach_managed_audit_paths,
        bound_task_control_paths=_bound_task_control_paths,
        context=_context,
        current_gate_approval=_current_gate_approval,
        fresh_state=_fresh_state,
        history=_history,
        inherit_redundant_test_gate=_inherit_redundant_test_gate,
        initialize_finding_handoff=_initialize_finding_handoff,
        managed_audit_path=_managed_audit_path,
        new_watch_task_control_paths=_new_watch_task_control_paths,
        new_watch_task_preserved_paths=_new_watch_task_preserved_paths,
        recover_final_review_attestation=_recover_final_review_attestation,
        recover_legacy_plan_only_post_gate=_recover_legacy_plan_only_post_gate,
        unused_run_id=_unused_run_id,
    )


def run_production_workflow(
    task_file: Path,
    args: argparse.Namespace,
    *,
    force_new: bool = False,
) -> WorkflowRunResult:
    return workflow_production.run_production_workflow(
        task_file,
        args,
        _production_workflow_dependencies(),
        force_new=force_new,
    )


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


def run_default_dry_run(task_file: Path, *, run_id: str | None = None):
    return workflow_dry_run.run_default_dry_run(task_file, run_id=run_id)


def _load_bound_queue_terminal(
    task_file: Path,
    evidence: QueueSuccessEvidence,
) -> Path:
    repository_root = Path.cwd().resolve()
    startup_resolutions: list[ResumeResolution] = []

    resumed = load_resumable_workflow_state(
        repository_root / ".orchestrator" / "state.json",
        repository_root=repository_root,
        allowed_roots=tuple(
            dict.fromkeys((repository_root, task_file.parent.resolve()))
        ),
        expected_run_id=evidence.run_id,
        expected_task_file=task_file,
        expected_task_digest=evidence.task_digest,
        resolution_observer=startup_resolutions.append,
    )
    if not isinstance(resumed, WorkflowState):
        raise ValueError("bound queue recovery requires version-3 state")
    [startup_resolution] = startup_resolutions
    terminal = WorkflowRunResult(
        resumed,
        _history(
            resumed,
            repository_root,
            validated_store=startup_resolution.validated_store,
        ),
    )
    if (
        not terminal.workflow_completed
        or resumed.run_id != evidence.run_id
        or Path(resumed.task_file).resolve() != task_file.resolve()
        or resumed.task_digest != evidence.task_digest
        or resumed.effective_protocol_mode.value != evidence.protocol_mode
    ):
        raise ValueError("bound success evidence differs from terminal workflow state")
    return repository_root


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
                repository_root = _load_bound_queue_terminal(task_file, evidence)
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
            task_result = WatchTaskResult.from_failure(
                failure,
                run_id=watch_run_id,
                records_written=records_written,
                protocol_mode=(
                    None
                    if failure.failure_class is FailureClass.TERMINAL_REJECTION
                    else "structured-v2"
                ),
            )
            try:
                write_pre_baseline_halt_diagnostic(
                    Path.cwd(), task_result, error=exc
                )
            except Exception as diagnostic_error:
                logger.warning(
                    "Pre-baseline halt diagnostic could not be written: %s",
                    diagnostic_error,
                )
            return task_result
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
        if result.workflow_rejected:
            logger.warning(
                "Workflow completed with a reviewer rejection: exit=%s detail=%s",
                exit_code,
                result.rejection_detail,
            )
        else:
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
