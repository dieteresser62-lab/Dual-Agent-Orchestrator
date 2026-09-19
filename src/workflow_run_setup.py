"""Construct provider context and reconcile run inputs before workflow execution."""

from __future__ import annotations

import argparse
import logging
import shlex
from dataclasses import replace
from pathlib import Path
from typing import Callable

from artifact_bridge import (
    ArtifactBridge,
    ArtifactBridgeError,
    branch_discovery_handoff_import_payload,
    finding_handoff_import_payload,
)
from artifact_models import (
    BranchDiscoveryHandoffExportPayload,
    FamilyBindingPayload,
    FindingHandoffExportPayload,
    FingerprintKind,
)
from artifact_replay import ArtifactReplayError, replay_artifacts
from artifact_store import ArtifactStore
from finding_reducer import reduce_findings
from git_service import inspect_repository, require_committed_file_at_head
from inbox_watcher import (
    attempt_sidecar_path,
    success_marker_path,
    watch_identity_path,
)
import native_finding_decisions
from repo_changes import resolve_merge_base
from state_io import StateSchemaError
from task_contract import TaskContract, TaskMode
from validation_matrix import ValidationCommand, ValidationMatrix
from workflow import (
    WorkflowContext,
    WorkflowExecutionError,
    WorkflowHistory,
)
from workflow_state import (
    AgentProfileBinding,
    GateDecisionRecord,
    GateReason,
    NATIVE_CLAUDE_REVIEW_TRANSPORT,  # allowlist:provider -- transport constant
    NATIVE_CODEX_RESULT_TRANSPORT,  # allowlist:provider -- transport constant
    ProtocolBinding,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
)


logger = logging.getLogger("orchestrator")

HistoryPayloadBuilder = Callable[[object, WorkflowHistory], dict[str, object]]
ManagedSliceScopePattern = Callable[[str], str]


def _context(
    *,
    args: argparse.Namespace,
    assignment: str,
    state: WorkflowState,
    _managed_slice_scope_pattern: ManagedSliceScopePattern,
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
    if agents_path.is_file():
        shared_instructions = agents_path.read_text(encoding="utf-8")[:12_000]
    elif not args.agents_file_explicit:
        shared_instructions = ""
        if not getattr(args, "agents_file_warning_emitted", False):
            logger.warning(
                "Default repository agents file is missing: %s; continuing without "
                "repository agent instructions.",
                agents_path,
            )
    else:
        raise WorkflowExecutionError(
            f"Explicit --agents-file does not exist: {agents_path}"
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
        and state.current_work_unit.kind is WorkUnitKind.SLICE
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
    active_remediation_paths = (
        tuple(sorted(set(state.current_slice.scope_paths).difference(planned_scope)))
        if state.current_work_unit.kind is WorkUnitKind.SLICE
        else ()
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
    family_binding: FamilyBindingPayload | None = None,
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
    if (
        task_contract.mode in {TaskMode.PLAN_ONLY, TaskMode.BRANCH_DISCOVERY}
        and task_contract.finding_handoff_source_run_id is not None
    ):
        discovered_binding = _branch_discovery_family_binding(
            repository_root,
            task_file,
            run_id,
            task_contract,
        )
        if family_binding is not None and family_binding != discovered_binding:
            raise StateSchemaError(
                "branch discovery handoff family binding differs from requested binding"
            )
        family_binding = discovered_binding
    if family_binding is not None:
        branch_base = family_binding.family_base_commit
    elif branch_base_override is not None:
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
        family_binding=family_binding,
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


def _branch_discovery_family_binding(
    repository_root: Path,
    task_file: Path,
    target_run_id: str,
    task_contract: TaskContract,
) -> FamilyBindingPayload:
    """Resolve E9's target family facts before writing the local RunProfile."""

    source_run_id = task_contract.finding_handoff_source_run_id
    export_record_id = task_contract.finding_handoff_export_record_id
    assert source_run_id is not None and export_record_id is not None
    try:
        source_replay = replay_artifacts(
            ArtifactStore(repository_root, source_run_id).load_chain(),
            source_run_id,
        )
        export_record = next(
            record
            for record in source_replay.records
            if record.record_id == export_record_id
        )
        export = export_record.payload
        if not isinstance(export, BranchDiscoveryHandoffExportPayload):
            raise ArtifactBridgeError(
                "family handoff does not reference a branch discovery export"
            )
        source_binding = (
            None
            if source_replay.run_profile is None
            else source_replay.run_profile.family_binding
        )
        if source_binding is None:
            raise ArtifactBridgeError(
                "branch discovery source has no RunProfile family binding"
            )
        try:
            target_path = task_file.resolve().relative_to(
                repository_root.resolve()
            ).as_posix()
        except ValueError as exc:
            raise ArtifactBridgeError(
                "branch discovery target task is outside the repository"
            ) from exc
        if source_replay.head_record_id != export_record.record_id:
            raise ArtifactBridgeError(
                "branch discovery export is not the source run head"
            )
        if export.target_execution_mode != task_contract.mode.value:
            raise ArtifactBridgeError(
                "branch discovery export target mode differs from the target task"
            )
        if export.target_run_identity != target_run_id:
            raise ArtifactBridgeError(
                "branch discovery target_run_identity differs from target run"
            )
        if export.target_task_path != target_path:
            raise ArtifactBridgeError(
                "branch discovery target_task_path differs from queue position"
            )
        if export.target_task_sha256 != task_contract.digest:
            raise ArtifactBridgeError(
                "branch discovery target_task_sha256 differs from loaded task bytes"
            )
        return FamilyBindingPayload(
            family_id=export.family_id,
            family_base_commit=export.family_base_commit,
            family_authorized_change_set=(
                source_binding.family_authorized_change_set
            ),
            predecessor_run_id=export.predecessor_run_id,
            predecessor_head_record_id=export.predecessor_head_record_id,
            cycle_number=export.cycle_number,
            current_plan_commit=source_binding.current_plan_commit,
            current_implementation_commit=(
                source_binding.current_implementation_commit
            ),
        )
    except (
        ArtifactBridgeError,
        ArtifactReplayError,
        OSError,
        StopIteration,
        ValueError,
    ) as exc:
        raise StateSchemaError(f"BRANCH-DISCOVERY-HANDOFF-INVALID: {exc}") from exc


def _initialize_finding_handoff(
    repository_root: Path,
    state: WorkflowState,
    task_contract: TaskContract,
    task_bytes: bytes,
    *,
    _history_payload: HistoryPayloadBuilder,
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
        if isinstance(export_payload, BranchDiscoveryHandoffExportPayload):
            if task_contract.mode not in {
                TaskMode.PLAN_ONLY,
                TaskMode.BRANCH_DISCOVERY,
            }:
                raise ArtifactBridgeError(
                    "branch discovery export requires its bound family target task"
                )
            if export_payload.target_execution_mode != task_contract.mode.value:
                raise ArtifactBridgeError(
                    "branch discovery export target mode differs from the target task"
                )
            if state.family_binding is None:
                raise ArtifactBridgeError(
                    "branch discovery import requires the target family binding"
                )
            try:
                target_task_path = Path(state.task_file).resolve().relative_to(
                    repository_root.resolve()
                ).as_posix()
            except ValueError as exc:
                raise ArtifactBridgeError(
                    "branch discovery target task is outside the repository"
                ) from exc
            payload = branch_discovery_handoff_import_payload(
                source_replay,
                export_record,
                target_run_id=state.run_id,
                target_task_path=target_task_path,
                target_task_bytes=task_bytes,
                target_family_binding=state.family_binding,
            )
            logical_id = "branch-discovery-handoff-import"
            idempotency_key = (
                f"branch-discovery-handoff-import:{source_run_id}:{export_record_id}"
            )
        else:
            if (
                not isinstance(export_payload, FindingHandoffExportPayload)
                or export_payload.approved_plan_commit
                != task_contract.approved_plan_commit
            ):
                raise ArtifactBridgeError(
                    "finding export plan commit differs from the task"
                )
            payload = finding_handoff_import_payload(
                source_replay,
                export_record,
                target_run_id=state.run_id,
                target_task_bytes=task_bytes,
            )
            logical_id = "finding-handoff-import"
            idempotency_key = (
                f"finding-handoff-import:{source_run_id}:{export_record_id}"
            )
        bridge = ArtifactBridge(ArtifactStore(repository_root, state.run_id))
        imported = bridge.append(
            payload,
            logical_id=logical_id,
            idempotency_key=idempotency_key,
            fingerprint_sha256=task_contract.digest,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
        local_replay = replay_artifacts(
            bridge.store.current_chain(),
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


# This pre-loop compatibility projection is run setup: it repairs no durable
# side-effect or record-ahead window and therefore stays outside recovery.
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
