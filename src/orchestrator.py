#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import hashlib
import re
from dataclasses import replace
from pathlib import Path

from agent_adapters import AgentAdapter, build_agent_registry
from agent_runtime import OrchestratorConfig, run_agent_checked, run_validation_matrix
from audit_trail import (
    AuditProjection,
    AuthorizedTestChanges,
    project_slice_audit,
    project_work_plan_audit,
    validate_slice_document,
    validate_work_plan_document,
)
from cli import DEFAULT_AGENTS_FILE, DEFAULT_TASK_FILE
from contracts import (
    AgentRole,
    CodexContractResult,
    CodexStepContract,
    ContractResult,
    FindingRecord,
    StepContract,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
    validate_codex_response,
    validate_review_response,
)
from gates import TestChangeEvidence, detect_test_changes, matches_path_patterns
from git_service import (
    CommitAuthorization,
    SliceGitBoundary,
    commit_slice,
    inspect_repository,
    GitTransactionError,
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
    GateReason,
    SliceStatus,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
)
from validation_matrix import ValidationCommand, ValidationMatrix
from inbox_watcher import WatchTaskResult, watch_inbox


logger = logging.getLogger(__name__)


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

    def bind_work_unit(self, state: WorkflowState) -> None:
        self.active_state = state
        if state.current_work_unit.kind is WorkUnitKind.PLAN and not self.last_codex_output:
            artifact = (
                self.root / ".orchestrator" / "runs" / state.run_id
                / f"work-unit-{state.current_work_unit_id:04d}-codex.md"
            )
            if artifact.is_file():
                self.last_codex_output = artifact.read_text(encoding="utf-8").strip()

    def _agent(self, role: AgentRole, prompt: str, label: str) -> str:
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
        )
        return output

    def invoke_codex(self, invocation: CodexInvocation) -> str:
        output = self._agent(
            AgentRole.CODEX,
            invocation.prompt,
            f"work-unit-{invocation.work_unit_id:04d}-{invocation.step.value}",
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
        return self._agent(
            invocation.reviewer,
            invocation.prompt,
            f"work-unit-{invocation.work_unit_id:04d}-{invocation.step.value}",
        )

    def repair_review_contract(self, invocation: ContractRepairInvocation) -> str:
        prompt = (
            "Repair only the formal output contract of the rejected review. Preserve its "
            "verdict, findings, evidence, and rationale. Return the complete corrected answer.\n\n"
            f"Validation error:\n{invocation.validation_error}\n\n"
            f"Contract:\n{invocation.contract}\n\n"
            f"Rejected output:\n{invocation.rejected_output}"
        )
        return self._agent(invocation.reviewer, prompt, "review-contract-repair")

    def collect_changes(self, start_commit: str) -> WorkflowChanges:
        changes = collect_repository_changes(self.root, start_commit)
        if changes.entries:
            rendered = WorkflowChanges(
                start_commit=start_commit,
                fingerprint=changes.fingerprint,
                paths=changes.paths,
                full_diff=changes.diff_text or "(binary or metadata-only repository change)",
                gate_paths=changes.review_paths,
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
        return run_validation_matrix(config=self.config, request=request)

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
            future_slice_ids = tuple(
                int(match.group(1))
                for match in re.finditer(
                    r"^#{2,6}[ \t]+Slice[ \t]+([0-9]+)(?:\b|:)",
                    content,
                    re.MULTILINE | re.IGNORECASE,
                )
            )
            if not future_slice_ids:
                raise WorkflowExecutionError(
                    "WORK_PLAN_PATH must contain at least one future Slice heading"
                )
            if future_slice_ids != tuple(range(1, len(future_slice_ids) + 1)):
                raise WorkflowExecutionError(
                    "WORK_PLAN_PATH future Slice headings must be contiguous and 1-based"
                )
            detail += f"; future_slices={len(future_slice_ids)}; work_plan={work_plan_path}"

        digest = hashlib.sha256(detail.encode("utf-8")).hexdigest()
        return ValidationAttestation(
            attestation_id=f"plan-validation-{changes.fingerprint[:12]}",
            diff_fingerprint=changes.fingerprint,
            expected_commands=(command,),
            records=(ValidationRecord(ValidationStatus.PASS, command, 0, detail),),
            output_digest=digest,
            summary="internal plan contract passed",
        )

    def prepare_correction(
        self, findings: tuple[FindingRecord, ...]
    ) -> WorkflowCorrectionBoundary:
        if self.active_state is None:
            raise WorkflowExecutionError("correction preparation has no active state")
        identity = inspect_repository(self.root)
        scope = tuple(
            sorted(
                {
                    path
                    for planned in self.active_state.planned_slices
                    for path in planned.scope_paths
                }
            )
        )
        if not scope:
            raise WorkflowExecutionError("final correction has no persisted planned scope")
        start = collect_repository_changes(self.root, identity.head)
        return WorkflowCorrectionBoundary(identity.head, scope, start.fingerprint)

    def commit_slice(self, request: WorkflowCommitRequest) -> str:
        if self.active_state is None:
            raise WorkflowExecutionError("slice commit has no active state")
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
        )
        summary = next(
            (
                item.summary
                for item in state.planned_slices
                if item.slice_id == current.slice_id
            ),
            "apply approved correction",
        )
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
            ),
            title=summary,
        )
        return result.commit_hash

    def checkpoint(self, state: WorkflowState, history: WorkflowHistory) -> None:
        self._project_audit(state, history)
        persisted = replace(
            state,
            runtime_history=_history_payload(state.runtime_history, history),
        )
        save_workflow_state(
            self.state_file, persisted, allowed_roots=self.allowed_roots
        )
        write_workflow_checkpoint(
            self.checkpoint_dir, persisted, allowed_roots=self.allowed_roots
        )
        self.active_state = persisted

    def _project_audit(self, state: WorkflowState, history: WorkflowHistory) -> None:
        """Write only managed audit blocks when the persisted plan names a target."""
        if not history.events:
            return
        unit = state.current_work_unit
        if unit.kind is WorkUnitKind.FINAL_REVIEW:
            # Final-review evidence remains in state/checkpoints. Reusing the last Slice
            # target would overwrite that Slice's own complete review lifecycle.
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
        projection = AuditProjection(
            slice_id=state.current_slice_id,
            events=history.events,
            test_approval=approval,
            implementation_ready=(
                unit.kind is not WorkUnitKind.PLAN
                and state.current_step not in {
                    WorkflowStep.CODEX_IMPLEMENTATION,
                    WorkflowStep.CODEX_CORRECTION,
                    WorkflowStep.CODEX_FINAL_CORRECTION,
                }
            ),
            commit_authorized=(
                state.current_step is WorkflowStep.SLICE_COMMIT
                or state.current_slice.status is SliceStatus.COMPLETED
            ),
        )
        if unit.kind is WorkUnitKind.PLAN:
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
                    document = validate_work_plan_document(
                        repository_root=self.root, work_plan_path=candidate
                    )
                except ValueError:
                    continue
                project_work_plan_audit(document, projection)
                return
            logger.debug("No prepared work-plan audit target is present in the Slice plan.")
            return
        planned = next(
            (item for item in state.planned_slices if item.slice_id == state.current_slice_id),
            None,
        )
        if planned is None:
            return
        candidates = tuple(
            path
            for path in planned.scope_paths
            if path.startswith("docs/internal/")
            and f"-{state.current_slice_id:02d}-" in Path(path).name
            and Path(path).suffix == ".md"
        )
        for path in candidates:
            try:
                document = validate_slice_document(
                    repository_root=self.root,
                    work_plan_path=next(
                        (
                            value
                            for item in state.planned_slices
                            for value in item.scope_paths
                            if value == state.work_plan_path
                            or (
                                value.startswith("docs/internal/")
                                and value.endswith("work-plan.md")
                            )
                        ),
                        "docs/internal/orchestrator-modernization-work-plan.md",
                    ),
                    slice_id=state.current_slice_id,
                    expected_relative_path=path,
                )
            except ValueError:
                continue
            project_slice_audit(document, projection)
            return
        logger.debug("No prepared Slice audit target is present for Slice %s.", state.current_slice_id)


def _context(
    *, args: argparse.Namespace, assignment: str, state: WorkflowState
) -> WorkflowContext:
    planned = next(
        (item for item in state.planned_slices if item.slice_id == state.current_slice_id),
        None,
    )
    validation_matrix = args.repo_config.validation
    if validation_matrix.default_command is None and str(args.test_command or "").strip():
        validation_matrix = ValidationMatrix(
            default_command=ValidationCommand(shell_command=str(args.test_command).strip()),
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
    if state.execution_mode == TaskMode.PLAN_ONLY.value:
        effective_assignment += (
            "\n- PLAN_ONLY: emit exactly one executable SLICE_PLAN record for creating "
            f"or updating {state.work_plan_path}.\n"
            "- Future product implementation Slices belong only as human-readable "
            "sections inside the work-plan document; do not emit them as executable "
            "SLICE_PLAN records in this run.\n"
            "- Do not modify product code, tests, configuration, or generated artifacts."
        )
    return WorkflowContext(
        assignment=effective_assignment,
        distilled_plan=(
            "Follow the ordered, persisted slice plan and exact path allowlists."
        ),
        slice_summary=(planned.summary if planned is not None else "Plan the requested work."),
        test_changes_approved=False,
        manual_slice_gate=bool(args.manual_slice_gate),
        path_classes=args.repo_config.paths,
        stop_rules=args.repo_config.stop_rules,
        current_branch=inspect_repository(Path.cwd()).branch,
        validation_matrix=validation_matrix,
        retry_incomplete_validation=bool(args.retry_incomplete_validation),
        quota_wait_policy=args.quota_wait_policy,
        require_slice_plan=state.current_work_unit.kind is WorkUnitKind.PLAN,
        dynamic_test_scope=True,
        plan_gate=bool(getattr(args, "plan_gate", True)),
        plan_only=state.execution_mode == TaskMode.PLAN_ONLY.value,
        task_scope_patterns=state.task_scope_patterns,
        work_plan_path=state.work_plan_path,
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
) -> WorkflowState:
    identity = inspect_repository(repository_root)
    if identity.branch != task_contract.target_branch:
        raise StateSchemaError(
            "TARGET_BRANCH mismatch: task requires "
            f"{task_contract.target_branch!r}, active branch is {identity.branch!r}; "
            "create/switch the branch before starting the orchestrator"
        )
    merge_base = resolve_merge_base(repository_root)
    return init_workflow_state(
        run_id=run_id,
        task_file=str(task_file.resolve()),
        branch=identity.branch,
        branch_base=merge_base.commit,
        first_slice_start_commit=identity.head,
        slice_count=1,
        task_digest=task_contract.digest,
        execution_mode=task_contract.mode.value,
        task_scope_patterns=task_contract.scope_patterns,
        work_plan_path=task_contract.work_plan_path,
        target_branch=task_contract.target_branch,
    )


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
    )
    allowed_roots = tuple(dict.fromkeys((root, task_file.parent.resolve())))
    run_id = str(getattr(args, "watch_run_id", "") or new_run_id())

    loaded: WorkflowState | CompletedV2State | None = None
    if state_file.exists() and not force_new:
        try:
            loaded = load_workflow_state(state_file, allowed_roots=allowed_roots)
        except ActiveV2StateError:
            if args.force_overwrite_state:
                loaded = None
            else:
                raise
    if args.resume:
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
        )
    config = OrchestratorConfig(
        dry_run=False,
        agent_output_mode=args.agent_output,
        agent_output_max_chars=args.agent_output_max_chars,
        agent_live_stream=bool(args.agent_live_stream),
        agent_live_stream_mode=args.agent_live_stream_mode,
        agent_live_stream_channels=args.agent_live_stream_channels,
        repo_root=root,
        strict_preflight=bool(args.strict_preflight),
    )
    driver = ProductionWorkflowDriver(
        repository_root=root,
        state_file=state_file,
        agents=build_agent_registry(args.agent_settings),
        config=config,
        allowed_roots=allowed_roots,
    )
    engine = WorkflowEngine(driver)
    driver.checkpoint(state, _history(state))

    for _ in range(100):
        history = _history(state)
        current = state.current_work_unit
        if current.status in {WorkUnitStatus.WAITING_FOR_QUOTA, WorkUnitStatus.AWAITING_RESUME}:
            if not args.resume:
                return WorkflowRunResult(state, history)
            state = state.resume_after_invocation_halt()
            driver.checkpoint(state, history)
        elif current.status is WorkUnitStatus.AWAITING_USER_DECISION:
            if args.gate_decision is not None:
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
                args.resume
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
            start = collect_repository_changes(root, expected_head)
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
            current = state.current_work_unit

        if current.kind is WorkUnitKind.FINAL_REVIEW:
            return WorkflowRunResult(state, _history(state))

        if current.kind is WorkUnitKind.PLAN:
            if not state.planned_slices:
                raise WorkflowExecutionError("completed plan has no persisted SLICE_PLAN")
            state = state.start_work_unit(
                slice_id=1,
                kind=WorkUnitKind.SLICE,
                step=WorkflowStep.CODEX_IMPLEMENTATION,
            )
            driver.checkpoint(state, WorkflowHistory(state.current_work_unit_id))
            continue

        pending = next(
            (item for item in state.slices if item.status is SliceStatus.PENDING), None
        )
        if pending is not None:
            identity = inspect_repository(root)
            state = state.start_work_unit(
                slice_id=pending.slice_id,
                kind=WorkUnitKind.SLICE,
                step=WorkflowStep.CODEX_IMPLEMENTATION,
                slice_start_commit=identity.head,
            )
            driver.checkpoint(state, WorkflowHistory(state.current_work_unit_id))
            continue

        state = state.start_final_review_work_unit()
        driver.checkpoint(state, WorkflowHistory(state.current_work_unit_id))

    raise WorkflowExecutionError("workflow session exceeded its deterministic transition bound")


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
    except (
        OSError,
        GitTransactionError,
        RepositoryChangeError,
        StateSchemaError,
        WorkflowExecutionError,
        ValueError,
    ) as exc:
        logger.error("State-v3 workflow failed: %s", exc)
        return 1

    if getattr(args, "watch_run_id", None) is not None:
        return WatchTaskResult.from_workflow(result)
    return result.exit_code


def main() -> int:
    from cli import main as cli_main

    return cli_main(
        run_pipeline_fn=run_pipeline,
        watch_inbox_fn=watch_inbox,
        find_task_file_fn=find_task_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
