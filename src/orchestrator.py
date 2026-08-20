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

from agent_adapters import AgentAdapter, build_agent_registry
from agent_runtime import (
    AgentInvocationError,
    OrchestratorConfig,
    run_agent_checked,
    run_validation_matrix,
)
from artifact_bridge import (
    ArtifactBridge, agent_result_payload, attestation_payload, finding_payload,
    plan_payload, review_payload, validation_request_payload,
    provider_input_measurement_payload,
)
from artifact_migration import ArtifactResumeError, resolve_resume_state
from artifact_projection import ArtifactAuditProjection
from artifact_models import (
    BindingPayload, CorrectionWorkUnitPayload, FingerprintKind, GatePayload,
    QuotaPausePayload, ReviewPayload, Role, TaskPayload, TransientRetryPayload,
    WorkUnitPayload,
    WorkflowCompletionPayload,
    ArtifactRecord, Fingerprint, ProviderInputMeasurementPayload, canonical_json,
)
from artifact_store import ArtifactStore
from final_review_preflight import (
    FINAL_REVIEW_OPERATIONS, FinalReviewPreflightDenied, preflight_payload,
    relevant_record_head, run_final_review_preflight, transition_fingerprint,
)
from provider_input_budget import ProviderInputMeasurement
from audit_trail import (
    AuditProjection,
    AuthorizedTestChanges,
    OverallAuditEntry,
    ReviewAuditEvent,
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
    validate_codex_response,
    validate_review_response,
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
from workflow import (
    CodexInvocation,
    ContractRepairInvocation,
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
)
from workflow_state import (
    AgentFailureKind,
    GateReason,
    GateDecisionRecord,
    SliceStatus,
    ProtocolBinding,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    WorkUnitRecord,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
    BootstrapCheckFact,
    managed_correction_slice_report_path,
)
from validation_matrix import ValidationCommand, ValidationMatrix
from inbox_watcher import (
    WatchTaskDisposition,
    WatchTaskResult,
    attempt_sidecar_path,
    move_to_outbox,
    success_marker_path,
    watch_identity_path,
    watch_inbox,
)


logger = logging.getLogger(__name__)


def _recover_completed_reviewer_contract(
    reviewer: AgentRole,
    error: AgentInvocationError,
) -> str | None:
    """Recover only a complete role-bound contract from a false auth classification."""
    if error.kind is not AgentFailureKind.AUTH:
        return None
    text = error.provider_text.strip()
    lines = tuple(line.strip() for line in text.splitlines() if line.strip())
    if (
        not lines
        or lines[0] != f"REVIEWER: {reviewer.value}"
        or lines[-1] != "STATUS: DONE"
    ):
        return None
    return text


def validate_v3_review_contract(
    output: str,
    contract: StepContract,
    previous_findings: tuple[FindingRecord, ...] = (),
) -> ContractResult:
    return validate_review_response(output, contract, previous_findings)


def validate_v3_codex_contract(
    output: str,
    contract: CodexStepContract,
    previous_findings: tuple[FindingRecord, ...] = (),
) -> CodexContractResult:
    return validate_codex_response(output, contract, previous_findings)


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


def _parse_flag(text: str, marker: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(marker)}\s*:\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _has_done(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines and lines[-1] == "STATUS: DONE")


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
            and state.protocol_binding.mode is ProtocolMode.STRUCTURED_V1
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
        assert self._artifact_bridge is not None
        chain = self._artifact_bridge.store.load_chain()
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
        if record_reviews != mirror_reviews:
            raise WorkflowExecutionError(
                "structured reviewer decisions differ from the state-v3 mirror"
            )

    def _persist_structured_baseline(self, state: WorkflowState) -> None:
        bridge = self._artifact_bridge
        if bridge is None or state.task_digest is None:
            return
        contract_fingerprint = state.task_digest
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

    def _agent(
        self,
        role: AgentRole,
        prompt: str,
        label: str,
        *,
        reviewer_repository_required: bool = True,
        operation: WorkflowStep,
        binding_fingerprint: str,
    ) -> str:
        self.assert_structured_decision_context()
        output = run_agent_checked(
            agent_key=role.value,
            prompt=prompt,
            log_prefix=label,
            max_retries=0,
            required_flags=[],
            output_validator=None,
            config=self.config,
            agents=self.agents,
            log_dir=self.log_dir,
            write_file=write_file,
            shorten=_shorten,
            parse_flag=_parse_flag,
            validate_done_marker=_has_done,
            reviewer_repository_required=reviewer_repository_required,
            operation=operation.value,
            binding_fingerprint=binding_fingerprint,
            pre_start_callback=self._persist_provider_bootstrap,
        )
        return output

    def _persist_provider_bootstrap(self, measurement: ProviderInputMeasurement) -> None:
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
        if bridge is not None:
            measurement_record = bridge.append(
                payload, logical_id=f"provider-input-{state.current_work_unit_id}-{measurement.operation}",
                idempotency_key=f"provider-input:{transition}",
                fingerprint_sha256=repository_fingerprint,
            )
        else:
            measurement_record = ArtifactRecord.create(
                run_id=state.run_id, logical_id=f"provider-input-{state.current_work_unit_id}-{measurement.operation}",
                revision=1, fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, repository_fingerprint),
                predecessor_ids=(), created_at=self._artifact_bridge.now() if self._artifact_bridge is not None else "2000-01-01T00:00:00+00:00",
                idempotency_key=f"provider-input:{transition}", payload=payload,
            )
        state = state.with_bootstrap_check(self._bootstrap_fact(payload))
        self._persist_bootstrap_state(state)
        if measurement.operation not in FINAL_REVIEW_OPERATIONS or not measurement.allowed:
            return
        current_chain = bridge.store.load_chain() if bridge is not None else (*chain, measurement_record)
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
            raise FinalReviewPreflightDenied(result)

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

    def invoke_codex(self, invocation: CodexInvocation) -> str:
        state_binding = (
            self.active_state.task_digest
            if self.active_state is not None and self.active_state.task_digest is not None
            else "unbound"
        )
        output = self._agent(
            AgentRole.CODEX,
            invocation.prompt,
            f"work-unit-{invocation.work_unit_id:04d}-{invocation.step.value}",
            operation=invocation.step,
            binding_fingerprint=state_binding,
        )
        self.last_codex_output = output
        if self.active_state is not None:
            run_dir = self.root / ".orchestrator" / "runs" / self.active_state.run_id
            write_file(
                run_dir / f"work-unit-{invocation.work_unit_id:04d}-codex.md",
                output,
            )
        return output

    def invoke_reviewer(self, invocation: ReviewerInvocation) -> str:
        try:
            return self._agent(
                invocation.reviewer,
                invocation.prompt,
                f"work-unit-{invocation.work_unit_id:04d}-{invocation.step.value}",
                operation=invocation.step,
                binding_fingerprint=invocation.fingerprint,
            )
        except AgentInvocationError as exc:
            recovered = _recover_completed_reviewer_contract(
                invocation.reviewer, exc
            )
            if recovered is None:
                raise
            logger.warning(
                "Recovered complete %s reviewer contract from a falsely classified "
                "authentication failure: invocation=%s",
                invocation.reviewer.value,
                exc.invocation_id,
            )
            return recovered

    def _artifact_fingerprint(self) -> str:
        if self.active_state is None:
            raise WorkflowExecutionError("structured persistence has no active state")
        state = self.active_state
        if state.current_work_unit.kind is WorkUnitKind.PLAN:
            if state.task_digest is None:
                raise WorkflowExecutionError("plan artifact requires a task fingerprint")
            return state.task_digest
        try:
            return self.collect_changes(
                state.current_slice.start_commit or state.branch_base
            ).fingerprint
        except NoWorkflowChangesError:
            # A not-ready or empty Codex result is still a decision record.  Bind
            # it to the persisted empty Slice boundary instead of losing the
            # diagnostic before the typed halt is recorded.
            if state.current_slice.start_fingerprint is not None:
                return state.current_slice.start_fingerprint
            raise

    def persist_codex_contract(
        self,
        result: CodexContractResult,
        output: str,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        if self._artifact_bridge is None or self.active_state is None:
            return
        state = self.active_state
        unit = state.current_work_unit
        fingerprint = self._artifact_fingerprint()
        logical = f"agent-{unit.work_unit_id}-{state.current_step.value}-{unit.round_number}"
        self._artifact_bridge.append(
            agent_result_payload(result, role=AgentRole.CODEX, work_unit_id=unit.work_unit_id),
            logical_id=logical,
            idempotency_key=f"parsed:{logical}:{hashlib.sha256(output.encode('utf-8')).hexdigest()}",
            fingerprint_sha256=fingerprint,
            fingerprint_kind=(
                FingerprintKind.CONTRACT
                if unit.kind is WorkUnitKind.PLAN
                else FingerprintKind.IMPLEMENTATION
            ),
        )
        previous_by_id = {item.finding_id: item for item in previous_findings}
        for finding in result.findings:
            prior_count = len(previous_by_id.get(finding.finding_id, finding).responses)
            if finding.finding_id not in previous_by_id:
                prior_count = 0
            for index, response in enumerate(
                finding.responses[prior_count:], start=prior_count + 1
            ):
                self._artifact_bridge.append(
                    finding_payload(
                        finding,
                        actor=AgentRole.CODEX,
                        action="responded",
                        rationale=f"{response.decision.value}: {response.rationale}",
                    ),
                    logical_id=f"finding-{finding.finding_id}",
                    idempotency_key=f"finding-response:{finding.finding_id}:{index}",
                    fingerprint_sha256=fingerprint,
                )

    def persist_review_contract(
        self,
        result: ContractResult,
        output: str,
        fingerprint: str,
        round_number: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        if self._artifact_bridge is None or self.active_state is None:
            return
        unit = self.active_state.current_work_unit
        logical = f"review-{result.reviewer.value}-{unit.work_unit_id}-{round_number}"
        self._artifact_bridge.append(
            review_payload(result, work_unit_id=unit.work_unit_id),
            logical_id=logical,
            idempotency_key=f"parsed:{logical}:{hashlib.sha256(output.encode('utf-8')).hexdigest()}",
            fingerprint_sha256=fingerprint,
        )
        previous_by_id = {item.finding_id: item for item in previous_findings}
        for finding in result.findings:
            previous = previous_by_id.get(finding.finding_id)
            transitions: list[tuple[str, str]] = []
            if previous is None:
                transitions.append(("opened", finding.summary))
            else:
                if previous.finding_class is not finding.finding_class:
                    transitions.append(
                        ("reclassified", finding.status_rationale or finding.summary)
                    )
                if previous.status is not finding.status:
                    transitions.append(
                        ("status_changed", finding.status_rationale or finding.summary)
                    )
            for action, rationale in transitions:
                self._artifact_bridge.append(
                    finding_payload(finding, action=action, rationale=rationale),
                    logical_id=f"finding-{finding.finding_id}",
                    idempotency_key=(
                        f"finding:{finding.finding_id}:{action}:{round_number}:"
                        f"{result.reviewer.value}"
                    ),
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
                "structured-v1 validation accepts only matrix-provided argv commands"
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
                "structured-v1 validation accepts only matrix-provided argv commands"
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

    def repair_review_contract(self, invocation: ContractRepairInvocation) -> str:
        if self.active_state is None:
            raise WorkflowExecutionError("contract repair requires an active workflow state")
        prompt = (
            "Repair only the formal output contract of the rejected review. Preserve its "
            "verdict, findings, evidence, and rationale. Return only the complete corrected "
            "answer without commentary, introduction, or Markdown fences. The first non-empty "
            f"line must be exactly REVIEWER: {invocation.reviewer.value}. Correct every formal "
            "contract violation, including a FINDING_STATUS record for every previous open "
            "finding owned by this reviewer, even when the validation error names only the "
            "first missing record.\n\n"
            f"Validation error:\n{invocation.validation_error}\n\n"
            f"Contract:\n{invocation.contract}\n\n"
            f"Rejected output:\n{invocation.rejected_output}"
        )
        return self._agent(
            invocation.reviewer,
            prompt,
            "review-contract-repair",
            reviewer_repository_required=False,
            operation=self.active_state.current_step,
            binding_fingerprint=self.active_state.task_digest or "unbound",
        )

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
        return (
            f"Correction delta since {previous_fingerprint}:\n"
            f"{current.full_diff}"
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
        if unexpected_actual:
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
                and decision.reason
                in {GateReason.UNEXPECTED_FILE, GateReason.QUOTA_RESUME_DIFF}
            ),
            None,
        )
        identity = inspect_repository(self.root)
        result = commit_slice(
            repository_root=self.root,
            boundary=boundary,
            authorization=CommitAuthorization(
                slice_id=request.slice_id,
                diff_fingerprint=request.fingerprint,
                attestation=request.attestation,
                claude_review=request.claude_review,
                antigravity_review=request.antigravity_review,
                findings=request.findings,
                red_state_followup_slice=request.red_state_followup_slice,
                approved_head_commit=(
                    identity.head if head_approval is not None else None
                ),
                approved_external_paths=(
                    unexpected_paths if exact_scope_approval else ()
                ),
            ),
            title=summary,
        )
        if self._artifact_bridge is not None:
            chain = self._artifact_bridge.store.load_chain()
            attestation = next(
                (
                    item for item in reversed(chain)
                    if item.record_type.value == "validation_attestation"
                    and item.fingerprint.sha256 == request.fingerprint
                ),
                None,
            )
            approvals = tuple(
                item.record_id
                for item in chain
                if isinstance(item.payload, ReviewPayload)
                and item.payload.verdict == "approved"
                and item.fingerprint.sha256 == request.fingerprint
            )
            if attestation is None or not approvals:
                raise WorkflowExecutionError(
                    "structured commit binding requires persisted attestation and approvals"
                )
            self._artifact_bridge.append(
                BindingPayload(
                    binding_kind="commit",
                    target=result.commit_hash,
                    attestation_id=attestation.record_id,
                    approval_ids=approvals,
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
            if persisted.effective_protocol_mode is ProtocolMode.STRUCTURED_V1:
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
        structured_chain = None
        if (
            state.protocol_binding is not None
            and state.protocol_binding.mode is ProtocolMode.STRUCTURED_V1
        ):
            try:
                resolve_resume_state(self.root, state)
                structured_chain = ArtifactStore(self.root, state.run_id).load_chain()
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
            entries = _overall_audit_entries(state)
            if entries:
                project_overall_audit(document, entries)
                if structured_chain is not None:
                    project_structured_work_plan_audit(
                        document, ArtifactAuditProjection(structured_chain)
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
        projection = _audit_projection(state, unit, history, approval)
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
                    if structured_chain is not None:
                        project_structured_work_plan_audit(
                            document, ArtifactAuditProjection(structured_chain)
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
                        if structured_chain is not None:
                            project_structured_work_plan_audit(
                                document, ArtifactAuditProjection(structured_chain)
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
                if structured_chain is not None:
                    project_structured_work_plan_audit(
                        document, ArtifactAuditProjection(structured_chain)
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
            if structured_chain is not None:
                project_structured_slice_audit(
                    document,
                    ArtifactAuditProjection(
                        structured_chain, slice_id=str(state.current_slice_id)
                    ),
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
    return AuditProjection(
        slice_id=unit.slice_id,
        events=history.events,
        test_approval=approval or _authorized_test_approval(unit),
        implementation_ready=implementation_ready,
        commit_authorized=commit_authorized,
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


def _carry_forward_findings(
    state: WorkflowState,
    current_history: WorkflowHistory,
) -> tuple[FindingRecord, ...]:
    """Build a stable cross-work-unit ledger, including pre-upgrade archives.

    Older runs allowed each work unit to reuse IDs such as C-01. When such a run is
    resumed, distinct historical records are deterministically assigned the next free
    reviewer ID while their origin, content, responses, and status remain unchanged.
    Subsequent work units then preserve those assigned IDs through the same identity.
    """
    histories = _persisted_histories(state)
    histories[current_history.work_unit_id] = current_history
    latest_by_identity: dict[tuple[object, ...], FindingRecord] = {}
    identity_order: list[tuple[object, ...]] = []
    all_ids: list[str] = []
    for history in (histories[unit_id] for unit_id in sorted(histories)):
        for finding in history.findings:
            identity = (
                finding.origin.reporter,
                finding.origin.slice_id,
                finding.origin.round_number,
                finding.summary,
                finding.acceptance_test,
            )
            if identity not in latest_by_identity:
                identity_order.append(identity)
            latest_by_identity[identity] = finding
            all_ids.append(finding.finding_id)

    next_number = {
        prefix: max(
            (
                int(finding_id.split("-", 1)[1])
                for finding_id in all_ids
                if finding_id.startswith(prefix + "-")
            ),
            default=0,
        )
        + 1
        for prefix in ("C", "A")
    }
    used_ids: set[str] = set()
    carried: list[FindingRecord] = []
    for identity in identity_order:
        finding = latest_by_identity[identity]
        finding_id = finding.finding_id
        if finding_id in used_ids:
            prefix = "C" if finding.origin.reporter is AgentRole.CLAUDE else "A"
            while True:
                finding_id = f"{prefix}-{next_number[prefix]:02d}"
                next_number[prefix] += 1
                if finding_id not in used_ids:
                    break
            finding = replace(finding, finding_id=finding_id)
        used_ids.add(finding_id)
        carried.append(finding)
    return tuple(sorted(carried, key=lambda finding: finding.finding_id))


def _overall_audit_entries(state: WorkflowState) -> tuple[OverallAuditEntry, ...]:
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
            summary = "Branchweite Gesamtabnahme durch Codex, Claude und Antigravity"
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
                projection=_audit_projection(state, unit, history),
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
                if state.effective_protocol_mode is ProtocolMode.STRUCTURED_V1
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
    slice_summary = planned.summary if planned is not None else "Plan the requested work."
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
        audit_report_path=state.audit_report_path,
        current_scope_paths=state.current_slice.scope_paths,
    )


def _plan_only_step_boundary(state: WorkflowState) -> str:
    """Render PLAN_ONLY instructions that agree with the current step contract."""
    if state.execution_mode != TaskMode.PLAN_ONLY.value:
        return ""
    if state.current_work_unit.kind is WorkUnitKind.PLAN:
        step_rule = (
            "- PLAN_ONLY planning step: emit exactly one executable SLICE_PLAN record "
            f"for creating or updating {state.work_plan_path}.\n"
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
        "- Do not modify product code, tests, configuration, or generated artifacts."
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


def _watch_run_has_persisted_state(task_file: Path, run_id: str) -> bool:
    """Return whether a failed Watch invocation created resumable state for this run."""
    state_file = Path.cwd().resolve() / ".orchestrator" / "state.json"
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        persisted_task = Path(raw["task_file"]).resolve()
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False
    return raw.get("version") == 3 and raw.get("run_id") == run_id and (
        persisted_task == task_file.resolve()
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
        audit_report_path=audit_report_path,
        target_branch=task_contract.target_branch,
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1"),
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
        ):
            raise StateSchemaError("persisted task contract differs from --resume task")
        if getattr(args, "watch_run_id", None) and state.run_id != args.watch_run_id:
            raise StateSchemaError("persisted watch run identity differs from inbox task")
        if state.audit_report_path is None and managed_audit_path is not None:
            state = replace(state, audit_report_path=managed_audit_path)
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
            inherited = _inherit_redundant_test_gate(state)
            if inherited != state:
                state = inherited
                driver.checkpoint(state, history)
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
                driver.assert_structured_decision_context()
                try:
                    handoff = write_implementation_handoff(
                        plan_task_path=task_file,
                        repository_root=root,
                        work_plan_path=state.work_plan_path or "",
                        target_branch=state.target_branch or state.branch,
                        approved_plan_commit=commit_ref,
                    )
                except PlanHandoffError as exc:
                    raise WorkflowExecutionError(
                        f"could not create IMPLEMENT handoff: {exc}"
                    ) from exc
                driver.persist_implementation_handoff(handoff, commit_ref)
                logger.info("Implementation handoff ready: %s", handoff)
                return WorkflowRunResult(state, _history(state), commit_ref)
            if not state.planned_slices:
                raise WorkflowExecutionError("completed plan has no persisted SLICE_PLAN")
            carried_findings = _carry_forward_findings(state, history)
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
            carried_findings = _carry_forward_findings(state, history)
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

        carried_findings = _carry_forward_findings(state, history)
        state = state.start_final_review_work_unit()
        driver.checkpoint(
            state,
            WorkflowHistory(
                state.current_work_unit_id,
                findings=carried_findings,
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

    def review(role: AgentRole, marker: str) -> str:
        return "\n".join(
            (
                f"REVIEWER: {role.value}",
                "TEST_FILES_TOUCHED: NONE",
                "REVIEW_EVIDENCE: correctness, contracts, failure paths, security, resume | "
                "runtime drift | a provider changes its output envelope",
                "PRE_MORTEM: a resumed invocation uses stale evidence",
                f"{marker}: YES",
                "STATUS: DONE",
            )
        )

    def slice_approval(role: AgentRole, slice_id: str) -> str:
        return "\n".join(
            (
                f"REVIEWER: {role.value}",
                "TEST_FILES_TOUCHED: NONE",
                "REVIEW_EVIDENCE: correctness, contracts, failure paths, security, resume | "
                "runtime drift | a provider changes its output envelope",
                "PRE_MORTEM: a resumed invocation uses stale evidence",
                f"SLICE_APPROVAL: {slice_id} | YES",
                "STATUS: DONE",
            )
        )

    scenario = DryRunScenario(
        name="default-v3-cutover",
        initial=ScriptedInitialState(kind=WorkUnitKind.PLAN, slice_count=2),
        agent_events=(
            ScriptedAgentEvent(AgentRole.CODEX, 1, 1, WorkflowStep.CODEX_PLAN,
                               "PLAN_READY: YES\nSTATUS: DONE"),
            ScriptedAgentEvent(AgentRole.CLAUDE, 1, 1, WorkflowStep.CLAUDE_PLAN_REVIEW,
                               review(AgentRole.CLAUDE, "PLAN_APPROVAL")),
            ScriptedAgentEvent(
                AgentRole.ANTIGRAVITY,
                1,
                1,
                WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
                review(AgentRole.ANTIGRAVITY, "PLAN_APPROVAL"),
            ),
            ScriptedAgentEvent(AgentRole.CODEX, 2, 1, WorkflowStep.CODEX_IMPLEMENTATION,
                               "TEST_FILES_TOUCHED: NONE\nIMPLEMENTATION_READY: 01 | YES\nSTATUS: DONE"),
            ScriptedAgentEvent(AgentRole.CLAUDE, 2, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                               slice_approval(AgentRole.CLAUDE, "01")),
            ScriptedAgentEvent(AgentRole.ANTIGRAVITY, 2, 1, WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
                               slice_approval(AgentRole.ANTIGRAVITY, "01")),
            ScriptedAgentEvent(AgentRole.CODEX, 3, 1, WorkflowStep.CODEX_IMPLEMENTATION,
                               "TEST_FILES_TOUCHED: NONE\nIMPLEMENTATION_READY: 02 | YES\nSTATUS: DONE"),
            ScriptedAgentEvent(AgentRole.CLAUDE, 3, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,
                               slice_approval(AgentRole.CLAUDE, "02")),
            ScriptedAgentEvent(AgentRole.ANTIGRAVITY, 3, 1, WorkflowStep.ANTIGRAVITY_SLICE_REVIEW,
                               slice_approval(AgentRole.ANTIGRAVITY, "02")),
            ScriptedAgentEvent(AgentRole.CODEX, 4, 1, WorkflowStep.CODEX_FINAL_REVIEW,
                               "FINAL_REPORT_READY: YES\nSTATUS: DONE"),
            ScriptedAgentEvent(AgentRole.CLAUDE, 4, 1, WorkflowStep.CLAUDE_FINAL_REVIEW,
                               review(AgentRole.CLAUDE, "FINAL_APPROVAL")),
            ScriptedAgentEvent(AgentRole.ANTIGRAVITY, 4, 1, WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
                               review(AgentRole.ANTIGRAVITY, "FINAL_APPROVAL")),
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
    except ArtifactResumeError as exc:
        logger.error("Structured resume halted: %s", exc)
        watch_run_id = getattr(args, "watch_run_id", None)
        if watch_run_id is not None:
            return WatchTaskResult(
                exit_code=4,
                run_id=watch_run_id,
                disposition=WatchTaskDisposition.RESUMABLE_HALT,
                status="awaiting_resume",
                step="resume_reader",
                work_unit_id=1,
                gate_reason="record_mismatch",
                failure_detail=f"ArtifactResumeError: {exc}",
                resume_available=True,
                protocol_mode="structured-v1",
            )
        return 1
    except StateSchemaError as exc:
        logger.error("State-v3 workflow failed: %s", exc)
        watch_run_id = getattr(args, "watch_run_id", None)
        if watch_run_id is not None:
            return WatchTaskResult(
                exit_code=4,
                run_id=watch_run_id,
                disposition=WatchTaskDisposition.RESUMABLE_HALT,
                status="awaiting_user_decision",
                step="pipeline",
                work_unit_id=1,
                gate_reason="state_contract",
                failure_detail=f"StateSchemaError: {exc}",
                resume_available=_watch_run_has_persisted_state(
                    task_file, watch_run_id
                ),
            )
        return 1
    except (
        OSError,
        GitTransactionError,
        RepositoryChangeError,
        WorkflowExecutionError,
        ValueError,
    ) as exc:
        logger.error("State-v3 workflow failed: %s", exc)
        watch_run_id = getattr(args, "watch_run_id", None)
        if watch_run_id is not None:
            return WatchTaskResult(
                exit_code=1,
                run_id=watch_run_id,
                disposition=WatchTaskDisposition.TECHNICAL_FAILURE,
                status="technical_failure",
                step="pipeline",
                work_unit_id=1,
                gate_reason="technical_failure",
                failure_detail=f"{type(exc).__name__}: {exc}",
                resume_available=_watch_run_has_persisted_state(
                    task_file, watch_run_id
                ),
            )
        return 1

    if getattr(args, "watch_run_id", None) is not None:
        return WatchTaskResult.from_workflow(result)
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
