"""Managed audit projection and final audit commit boundary.

The production driver owns composition and mutable runtime state.  This module
owns audit projection and finalization, receiving every driver-owned operation
explicitly and importing neither the driver nor the validation boundary.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from artifact_bridge import ArtifactBridge
from artifact_resume import ArtifactResumeError, resolve_resume_state
from artifact_replay import ArtifactReplayResult, replay_artifacts
from audit_trail import (
    AuditProjection,
    AuthorizedTestChanges,
    OverallAuditEntry,
    prepare_managed_overall_document,
    prepare_managed_slice_document,
    prepare_managed_work_plan_document,
    project_managed_slice_audit,
    project_overall_audit,
    project_structured_slice_audit,
    project_structured_work_plan_audit,
    project_work_plan_audit,
    validate_managed_work_plan_document,
)
from git_service import (
    commit_managed_audit_report,
    inspect_commit_tree,
    inspect_repository,
    preview_commit_tree,
)
from repo_changes import collect_repository_changes
from side_effects import (
    SideEffectExecutor,
    SideEffectSpec,
    reconcile_git_commit,
)
from task_contract import TaskMode
from workflow import WorkflowExecutionError, WorkflowHistory
from workflow_state import ProtocolMode, WorkflowState, WorkUnitKind, WorkUnitRecord


logger = logging.getLogger(__name__)


class ArtifactBridgeProvider(Protocol):
    def __call__(self) -> ArtifactBridge | None: ...


class RootProvider(Protocol):
    def __call__(self) -> Path: ...


class SideEffectExecutorFactory(Protocol):
    def __call__(self, bridge: ArtifactBridge) -> SideEffectExecutor: ...


class SideEffectSpecFactory(Protocol):
    def __call__(
        self,
        effect_class: str,
        operation: tuple[str, ...],
        *,
        fingerprint: str | None = None,
    ) -> SideEffectSpec: ...


class BoundTaskControlPaths(Protocol):
    def __call__(
        self, repository_root: Path, state: WorkflowState | None
    ) -> tuple[str, ...]: ...


class OverallAuditEntries(Protocol):
    def __call__(
        self,
        state: WorkflowState,
        structured_replay: ArtifactReplayResult | None,
        blob_reader: object | None,
    ) -> tuple[OverallAuditEntry, ...]: ...


class AuthorizedTestApproval(Protocol):
    def __call__(
        self,
        unit: WorkUnitRecord,
        structured_replay: ArtifactReplayResult | None,
    ) -> AuthorizedTestChanges | None: ...


class AuditProjectionFactory(Protocol):
    def __call__(
        self,
        state: WorkflowState,
        unit: WorkUnitRecord,
        history: WorkflowHistory,
        approval: AuthorizedTestChanges | None = None,
        structured_replay: ArtifactReplayResult | None = None,
    ) -> AuditProjection: ...


@dataclass(frozen=True)
class WorkflowAuditDependencies:
    """Driver-owned edges used by audit projection and finalization."""

    root: RootProvider
    artifact_bridge: ArtifactBridgeProvider
    assert_structured_decision_context: Callable[[], None]
    mark_completed_side_effect: Callable[[str], None]
    side_effect_executor: SideEffectExecutorFactory
    side_effect_spec: SideEffectSpecFactory
    bound_task_control_paths: BoundTaskControlPaths
    overall_audit_entries: OverallAuditEntries
    authorized_test_approval: AuthorizedTestApproval
    audit_projection: AuditProjectionFactory


class WorkflowAudit:
    """Project and finalize the managed audit without owning driver state."""

    def __init__(self, dependencies: WorkflowAuditDependencies) -> None:
        self._dependencies = dependencies

    def finalize_audit(self, state: WorkflowState) -> str | None:
        if state.audit_report_path is None:
            return None
        self._dependencies.assert_structured_decision_context()
        bridge = self._dependencies.artifact_bridge()
        if bridge is None:
            return commit_managed_audit_report(
                repository_root=self._dependencies.root(),
                branch=state.branch,
                audit_path=state.audit_report_path,
                excluded_control_paths=self._dependencies.bound_task_control_paths(
                    self._dependencies.root(), state
                ),
            )
        replay = replay_artifacts(bridge.store.current_chain(), state.run_id)
        existing = next(
            (
                item
                for item in reversed(replay.side_effects)
                if item.effect_class == "git_commit"
                and item.work_unit_id == str(state.current_work_unit_id)
                and len(item.operation) == 6
                and item.operation[:2] == ("audit_commit", state.audit_report_path)
                and item.result is None
            ),
            None,
        )
        if existing is None:
            identity = inspect_repository(self._dependencies.root())
            changes = collect_repository_changes(
                self._dependencies.root(),
                identity.head,
                semantic_markdown_paths=(state.audit_report_path,),
                excluded_paths=self._dependencies.bound_task_control_paths(
                    self._dependencies.root(), state
                ),
            )
            if not changes.entries:
                return identity.head
            operation = (
                "audit_commit",
                state.audit_report_path,
                identity.head,
                preview_commit_tree(
                    self._dependencies.root(),
                    changes,
                    force_non_executable_paths=(state.audit_report_path,),
                ),
                changes.fingerprint,
                hashlib.sha256(b"docs: finalize orchestrator audit").hexdigest(),
            )
        else:
            operation = existing.operation
        spec = self._dependencies.side_effect_spec(
            "git_commit",
            operation,
            fingerprint=operation[4],
        )

        def reconcile_audit_commit():  # type: ignore[no-untyped-def]
            identity = inspect_repository(self._dependencies.root())
            if identity.head == operation[2]:
                return reconcile_git_commit(
                    prior_head=operation[2],
                    current_head=identity.head,
                    current_parent=None,
                    expected_tree=operation[3],
                    current_tree=None,
                )
            parent, tree = inspect_commit_tree(self._dependencies.root(), identity.head)
            return reconcile_git_commit(
                prior_head=operation[2],
                current_head=identity.head,
                current_parent=parent,
                expected_tree=operation[3],
                current_tree=tree,
            )

        result = self._dependencies.side_effect_executor(bridge).execute(
            spec,
            reconcile=reconcile_audit_commit,
            perform=lambda: (
                committed := commit_managed_audit_report(
                    repository_root=self._dependencies.root(),
                    branch=state.branch,
                    audit_path=state.audit_report_path,
                    excluded_control_paths=(
                        self._dependencies.bound_task_control_paths(
                            self._dependencies.root(), state
                        )
                    ),
                ),
                committed,
            ),
        )
        self._dependencies.mark_completed_side_effect(spec.effect_key)
        return str(result)

    def project_audit(self, state: WorkflowState, history: WorkflowHistory) -> None:
        """Write only managed audit blocks when the persisted plan names a target."""
        unit = state.current_work_unit
        structured_replay = None
        if (
            state.protocol_binding is not None
            and state.protocol_binding.mode is ProtocolMode.STRUCTURED_V2
        ):
            try:
                bridge = self._dependencies.artifact_bridge()
                structured_replay = resolve_resume_state(
                    self._dependencies.root(),
                    state,
                    validated_store=(None if bridge is None else bridge.store),
                ).replay_result
            except (ArtifactResumeError, ValueError) as exc:
                raise WorkflowExecutionError(
                    f"structured audit dual-write mismatch: {exc}"
                ) from exc
        if state.audit_report_path is not None:
            task = Path(state.task_file)
            try:
                task_label = task.resolve().relative_to(
                    self._dependencies.root()
                ).as_posix()
            except ValueError:
                task_label = task.name
            document = prepare_managed_overall_document(
                repository_root=self._dependencies.root(),
                audit_path=state.audit_report_path,
                task_name=task.stem,
                task_file=task_label,
                run_id=state.run_id,
                branch=state.branch,
                task_scope=state.task_scope_patterns,
            )
            bridge = self._dependencies.artifact_bridge()
            entries = self._dependencies.overall_audit_entries(
                state,
                structured_replay,
                None if bridge is None else bridge.store.read_blob,
            )
            if entries:
                project_overall_audit(document, entries)
                if structured_replay is not None:
                    project_structured_work_plan_audit(document, structured_replay)
        if unit.kind is WorkUnitKind.FINAL_REVIEW:
            return
        # The Slice audit is part of the authorized Slice commit.  Commit and
        # subsequent workflow-binding records are projected into the overall
        # audit only; rewriting the already committed Slice document would leave
        # a foreign dirty path for the final audit transaction.
        if state.current_slice.commit_ref is not None:
            return
        approval = self._dependencies.authorized_test_approval(
            unit, structured_replay
        )
        projection = self._dependencies.audit_projection(
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
                    repository_root=self._dependencies.root(),
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
                        repository_root=self._dependencies.root(),
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
                candidates.append(self._dependencies.root() / state.work_plan_path)
            candidates.extend(
                self._dependencies.root() / path
                for planned in state.planned_slices
                for path in planned.scope_paths
                if path.startswith("docs/internal/")
                and path.endswith("work-plan.md")
            )
            for candidate in candidates:
                try:
                    document = validate_managed_work_plan_document(
                        repository_root=self._dependencies.root(),
                        work_plan_path=candidate,
                    )
                except ValueError:
                    continue
                project_work_plan_audit(document, projection)
                if structured_replay is not None:
                    project_structured_work_plan_audit(document, structured_replay)
                return
            logger.debug(
                "No prepared work-plan audit target is present in the Slice plan."
            )
            return
        planned = next(
            (
                item
                for item in state.planned_slices
                if item.slice_id == state.current_slice_id
            ),
            None,
        )
        scope_paths = (
            planned.scope_paths
            if planned is not None
            else state.current_slice.scope_paths
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
                    repository_root=self._dependencies.root(),
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
                project_structured_slice_audit(document, structured_replay)
            return
        logger.debug(
            "No prepared Slice audit target is present for Slice %s.",
            state.current_slice_id,
        )
