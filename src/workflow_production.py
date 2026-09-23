from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from agent_adapters import build_agent_registry
from agent_runtime import OrchestratorConfig
from audit_trail import ValidationAuditEvent
from artifact_bridge import ArtifactBridgeError
from artifact_resume import ArtifactResumeError, ResumeResolution, resolve_resume_state
from artifact_replay import ArtifactReplayError
from artifact_store import ArtifactStore
from git_service import inspect_repository, prepare_new_watch_task_branch
from inbox_watcher import watch_run_has_records
from plan_handoff import PlanHandoffError, write_implementation_handoff
from repo_changes import collect_repository_changes
from state_io import (
    ActiveV2StateError,
    CompletedV2State,
    StateSchemaError,
    load_resumable_workflow_state,
    load_workflow_state,
    new_run_id,
)
from task_contract import TaskContract, TaskMode, parse_task_contract
from workflow import (
    WorkflowDriver,
    WorkflowEngine,
    WorkflowExecutionError,
    WorkflowHistory,
    WorkflowRunResult,
    require_driver_capabilities,
    require_workflow_driver,
    resolve_retired_iteration_limit,
    workflow_rejection_finding_ids,
)
from workflow_state import (
    AgentProfileBinding,
    GateReason,
    ProtocolMode,
    SliceStatus,
    WorkflowState,
    WorkflowStateValidationError,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
)


logger = logging.getLogger(__name__)


def _validated_store(resolution: ResumeResolution | None) -> ArtifactStore | None:
    return None if resolution is None else resolution.validated_store


def _history_from_startup_resolution(
    history: Callable[..., WorkflowHistory],
    state: WorkflowState,
    root: Path,
    resolution: ResumeResolution | None,
) -> WorkflowHistory:
    store = _validated_store(resolution)
    if store is None:
        return history(state, root)
    return history(state, root, validated_store=store)


class ProductionWorkflowLoopDriver(WorkflowDriver, Protocol):
    """Workflow contract plus capabilities owned only by the production loop."""

    def finalize_audit(self, state: WorkflowState) -> str | None: ...

    def assert_structured_decision_context(self) -> None: ...

    def persist_implementation_handoff(
        self, handoff_path: Path, approved_plan_commit: str
    ) -> None: ...

    def publish_followup_task(self, state: WorkflowState) -> Path | None: ...

    def _write_side_effect_file(
        self, path: Path, content: str, *, normalized_text: bool
    ) -> None: ...


PRODUCTION_LOOP_INTERNAL_DRIVER_METHODS = frozenset(
    {
        "_write_side_effect_file",
        "assert_structured_decision_context",
        "finalize_audit",
        "persist_implementation_handoff",
        "publish_followup_task",
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


@dataclass(frozen=True, slots=True)
class ProductionWorkflowDependencies:
    """Orchestrator-owned seams used by the extracted production composition root."""

    driver_factory: Callable[..., ProductionWorkflowLoopDriver]
    apply_resumed_agent_profiles: Callable[..., Any]
    archive_stale_untracked_audit_reports: Callable[..., Any]
    attach_managed_audit_paths: Callable[..., Any]
    bound_task_control_paths: Callable[..., Any]
    context: Callable[..., Any]
    current_gate_approval: Callable[..., Any]
    fresh_state: Callable[..., Any]
    history: Callable[..., Any]
    inherit_redundant_test_gate: Callable[..., Any]
    managed_audit_path: Callable[..., Any]
    new_watch_task_control_paths: Callable[..., Any]
    new_watch_task_preserved_paths: Callable[..., Any]
    recover_final_review_attestation: Callable[..., Any]
    recover_legacy_plan_only_post_gate: Callable[..., Any]
    unused_run_id: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class _ProductionTask:
    root: Path
    state_file: Path
    task_file: Path
    assignment: str
    task_contract: TaskContract
    allowed_roots: tuple[Path, ...]
    requested_run_id: str
    run_id: str
    managed_audit_path: str | None
    new_watch_task: bool


def _read_production_task(
    root: Path,
    task_file: Path,
    args: argparse.Namespace,
    *,
    force_new: bool,
    unused_run_id: Callable[..., str],
    managed_audit_path_for: Callable[..., str | None],
) -> _ProductionTask:
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
    run_id = requested_run_id or unused_run_id(root, new_run_id())
    managed_audit_path = (
        managed_audit_path_for(task_file, task_contract.digest)
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
    return _ProductionTask(
        root=root,
        state_file=state_file,
        task_file=task_file,
        assignment=assignment,
        task_contract=task_contract,
        allowed_roots=allowed_roots,
        requested_run_id=requested_run_id,
        run_id=run_id,
        managed_audit_path=managed_audit_path,
        new_watch_task=new_watch_task,
    )


def _prepare_new_watch_task(
    *,
    new_watch_task: bool,
    managed_audit_path: str | None,
    args: argparse.Namespace,
    root: Path,
    task_file: Path,
    task_contract: TaskContract,
    archive_stale_untracked_audit_reports: Callable[..., Any],
    new_watch_task_control_paths: Callable[..., Any],
    new_watch_task_preserved_paths: Callable[..., Any],
) -> str | None:
    prepared_branch_base: str | None = None
    if new_watch_task:
        if managed_audit_path is not None:
            configured_outbox = Path(getattr(args, "outbox_dir", "outbox"))
            if not configured_outbox.is_absolute():
                configured_outbox = root / configured_outbox
            archive_stale_untracked_audit_reports(
                root,
                current_audit_path=managed_audit_path,
                outbox_failed_dir=configured_outbox / "failed",
            )
        prepared = prepare_new_watch_task_branch(
            root,
            target_branch=task_contract.target_branch,
            derived_task_digest=(
                task_contract.digest
                if task_contract.target_branch_generated
                else None
            ),
            excluded_control_paths=new_watch_task_control_paths(root, task_file),
            preserved_task_paths=new_watch_task_preserved_paths(root, task_contract),
        )
        prepared_branch_base = prepared.identity.head
        logger.info(
            "Watch target branch ready: action=%s previous=%s target=%s head=%s",
            prepared.action,
            prepared.previous_branch,
            prepared.identity.branch,
            prepared.identity.head[:12],
        )
    return prepared_branch_base


def _validate_resumed_state(
    loaded: WorkflowState | CompletedV2State | None,
    task_file: Path,
    task_contract: TaskContract,
) -> WorkflowState:
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
    return state


def _create_production_state(
    *,
    task_file: Path,
    run_id: str,
    root: Path,
    task_contract: TaskContract,
    prepared_branch_base: str | None,
    managed_audit_path: str | None,
    agent_settings: dict[str, Any],
    max_rounds_per_loop: int,
    base_branch: str | None,
    fresh_state: Callable[..., WorkflowState],
) -> WorkflowState:
    return fresh_state(
        task_file=task_file,
        run_id=run_id,
        repository_root=root,
        task_contract=task_contract,
        branch_base_override=prepared_branch_base,
        audit_report_path=managed_audit_path,
        codex_profile=AgentProfileBinding(
            agent_settings["codex"].model,
            agent_settings["codex"].effort,
        ),
        claude_profile=AgentProfileBinding(
            agent_settings["claude"].model,
            agent_settings["claude"].effort,
        ),
        max_rounds_per_loop=max_rounds_per_loop,
        base_branch=base_branch,
    )


def _recover_final_review_history(
    root: Path,
    state: WorkflowState,
    current: Any,
    history: WorkflowHistory,
    dependencies: ProductionWorkflowDependencies,
) -> WorkflowHistory:
    structured_replay = None
    read_blob = None
    if (
        current.kind is WorkUnitKind.FINAL_REVIEW
        and state.effective_protocol_mode is ProtocolMode.STRUCTURED_V2
    ):
        resolution = resolve_resume_state(root, state)
        structured_replay = resolution.replay_result
        read_blob = ArtifactStore(root, state.run_id).read_blob
    recovered_history = dependencies.recover_final_review_attestation(
        state,
        history,
        structured_replay,
        read_blob,
    )
    return recovered_history


def _prepare_plan_implementation_handoff(
    *,
    driver: ProductionWorkflowLoopDriver,
    task_file: Path,
    root: Path,
    state: WorkflowState,
    commit_ref: str,
) -> Path:
    handoff = write_implementation_handoff(
        plan_task_path=task_file,
        repository_root=root,
        work_plan_path=state.work_plan_path or "",
        target_branch=state.target_branch or state.branch,
        approved_plan_commit=commit_ref,
        write_content=lambda path, content: driver._write_side_effect_file(
            path, content, normalized_text=False
        ),
    )
    return handoff


def _start_first_slice(
    state: WorkflowState,
    history: WorkflowHistory,
    driver: ProductionWorkflowLoopDriver,
) -> tuple[WorkflowState, WorkflowHistory]:
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
    return state, history


def _start_pending_slice(
    root: Path,
    state: WorkflowState,
    pending: Any,
    history: WorkflowHistory,
    driver: ProductionWorkflowLoopDriver,
) -> tuple[WorkflowState, WorkflowHistory]:
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
    return state, history


def _start_final_review(
    state: WorkflowState,
    history: WorkflowHistory,
    driver: ProductionWorkflowLoopDriver,
) -> tuple[WorkflowState, WorkflowHistory]:
    """Enter the implementation run's terminal branch-wide review."""

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
    return state, history


def run_production_workflow(
    task_file: Path, args: argparse.Namespace, dependencies: ProductionWorkflowDependencies,
    *,
    force_new: bool = False,
) -> WorkflowRunResult:
    root = Path.cwd().resolve()
    task = _read_production_task(
        root,
        task_file,
        args,
        force_new=force_new,
        unused_run_id=dependencies.unused_run_id,
        managed_audit_path_for=dependencies.managed_audit_path,
    )
    state_file = task.state_file
    task_file = task.task_file
    assignment = task.assignment
    task_contract = task.task_contract
    allowed_roots = task.allowed_roots
    requested_run_id = task.requested_run_id
    run_id = task.run_id
    managed_audit_path = task.managed_audit_path
    new_watch_task = task.new_watch_task
    prepared_branch_base = _prepare_new_watch_task(
        new_watch_task=new_watch_task,
        managed_audit_path=managed_audit_path,
        args=args,
        root=root,
        task_file=task_file,
        task_contract=task_contract,
        archive_stale_untracked_audit_reports=(
            dependencies.archive_stale_untracked_audit_reports
        ),
        new_watch_task_control_paths=dependencies.new_watch_task_control_paths,
        new_watch_task_preserved_paths=dependencies.new_watch_task_preserved_paths,
    )

    loaded: WorkflowState | CompletedV2State | None = None
    startup_resolution: ResumeResolution | None = None

    def capture_startup_resolution(resolution: ResumeResolution) -> None:
        nonlocal startup_resolution
        startup_resolution = resolution

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
                resolution_observer=capture_startup_resolution,
            )
        except ActiveV2StateError:
            if args.force_overwrite_state:
                loaded = None
            else:
                raise
    effective_resume = bool(args.resume and not new_watch_task)
    workflow_config = getattr(args.repo_config, "workflow", None)
    max_rounds_per_loop = getattr(workflow_config, "max_rounds_per_loop", 6)
    max_acceptance_reviews = getattr(
        workflow_config, "max_acceptance_reviews", 6
    )
    if effective_resume:
        state = _validate_resumed_state(loaded, task_file, task_contract)
        if getattr(args, "watch_run_id", None) and state.run_id != args.watch_run_id:
            raise StateSchemaError("persisted watch run identity differs from inbox task")
        if state.audit_report_path is None and managed_audit_path is not None:
            state = replace(state, audit_report_path=managed_audit_path)
        dependencies.apply_resumed_agent_profiles(args, state)
    else:
        if loaded is not None and not args.force_overwrite_state and not force_new:
            raise StateSchemaError(
                "existing state requires --resume or --force-overwrite-state"
            )
        state = _create_production_state(
            task_file=task_file,
            run_id=run_id,
            root=root,
            task_contract=task_contract,
            prepared_branch_base=prepared_branch_base,
            managed_audit_path=managed_audit_path,
            agent_settings=args.agent_settings,
            max_rounds_per_loop=max_rounds_per_loop,
            base_branch=args.repo_config.repository.base_branch,
            fresh_state=dependencies.fresh_state,
        )
    state = dependencies.attach_managed_audit_paths(state)
    state = dependencies.recover_legacy_plan_only_post_gate(state)
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
        max_acceptance_reviews=max_acceptance_reviews,
        provider_input_budget=args.repo_config.provider_input_budget,
    )
    driver: ProductionWorkflowLoopDriver = dependencies.driver_factory(
        repository_root=root,
        state_file=state_file,
        agents=build_agent_registry(args.agent_settings),
        config=config,
        allowed_roots=allowed_roots,
        replace_existing_run_id=replacement_run_id,
        validated_store=_validated_store(startup_resolution),
    )
    require_production_workflow_loop_driver(driver)
    engine = WorkflowEngine(driver)
    history = _history_from_startup_resolution(
        dependencies.history, state, root, startup_resolution
    )
    state = resolve_retired_iteration_limit(state, history)
    driver.checkpoint(state, history)

    return _run_or_return_retired_iteration_verdict(
        root=root,
        task_file=task_file,
        assignment=assignment,
        args=args,
        dependencies=dependencies,
        state=state,
        history=history,
        effective_resume=effective_resume,
        driver=driver,
        engine=engine,
    )


def _run_or_return_retired_iteration_verdict(
    *,
    root: Path,
    task_file: Path,
    assignment: str,
    args: argparse.Namespace,
    dependencies: ProductionWorkflowDependencies,
    state: WorkflowState,
    history: WorkflowHistory,
    effective_resume: bool,
    driver: ProductionWorkflowLoopDriver,
    engine: WorkflowEngine,
) -> WorkflowRunResult:
    """Return a retired gate verdict before entering the preserved core loop."""

    if workflow_rejection_finding_ids(state, history):
        return WorkflowRunResult(state, history)
    return _run_production_transition_loop(
        root=root,
        task_file=task_file,
        assignment=assignment,
        args=args,
        dependencies=dependencies,
        state=state,
        history=history,
        effective_resume=effective_resume,
        driver=driver,
        engine=engine,
    )


def _run_production_transition_loop(
    *,
    root: Path,
    task_file: Path,
    assignment: str,
    args: argparse.Namespace,
    dependencies: ProductionWorkflowDependencies,
    state: WorkflowState,
    history: WorkflowHistory,
    effective_resume: bool,
    driver: ProductionWorkflowLoopDriver,
    engine: WorkflowEngine,
) -> WorkflowRunResult:

    for _ in range(100):
        current = state.current_work_unit
        recovered_history = _recover_final_review_history(
            root, state, current, history, dependencies
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
            inherited = dependencies.inherit_redundant_test_gate(state)
            if inherited != state:
                state = inherited
                driver.checkpoint(state, history)
            elif (existing_approval := dependencies.current_gate_approval(state)) is not None:
                decided = engine.decide_current_gate(
                    state,
                    history,
                    approved=True,
                    rationale=existing_approval.rationale,
                    path_classes=args.repo_config.paths,
                )
                state, history = decided.state, decided.history
            elif args.gate_decision is not None:
                decided = engine.decide_current_gate(
                    state,
                    history,
                    approved=args.gate_decision,
                    rationale=args.gate_rationale,
                    path_classes=args.repo_config.paths,
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
                excluded_paths=dependencies.bound_task_control_paths(root, state),
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
                state, dependencies.context(args=args, assignment=assignment, state=state), history
            )
            state = driver.active_state or result.state
            if not result.completed:
                return WorkflowRunResult(state, result.history, result.commit_ref)
            history = result.history
            current = state.current_work_unit

        if current.kind is WorkUnitKind.FINAL_REVIEW:
            audit_commit = driver.finalize_audit(state)
            handoff = driver.publish_followup_task(state)
            if handoff is not None:
                logger.info("Follow-up work document ready: %s", handoff)
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
                    handoff = _prepare_plan_implementation_handoff(
                        driver=driver,
                        task_file=task_file,
                        root=root,
                        state=state,
                        commit_ref=commit_ref,
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
            state, history = _start_first_slice(state, history, driver)
            driver.checkpoint(state, history)
            state = driver.active_state or state
            continue

        pending = next(
            (item for item in state.slices if item.status is SliceStatus.PENDING), None
        )
        if pending is not None:
            state, history = _start_pending_slice(
                root, state, pending, history, driver
            )
            driver.checkpoint(state, history)
            state = driver.active_state or state
            continue

        if state.execution_mode == TaskMode.IMPLEMENT.value:
            state, history = _start_final_review(state, history, driver)
            driver.checkpoint(state, history)
            state = driver.active_state or state
            continue
        return WorkflowRunResult(state, history, state.current_slice.commit_ref)

    raise WorkflowExecutionError("workflow session exceeded its deterministic transition bound")
