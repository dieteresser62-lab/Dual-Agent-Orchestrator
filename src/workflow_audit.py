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
from artifact_replay import replay_artifacts
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
from workflow import WorkflowExecutionError, WorkflowHistory
from workflow_state import ProtocolMode, WorkflowState, WorkUnitKind


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
        """Project human documents from the accepted record chain."""
        if state.protocol_binding is None or state.protocol_binding.mode is not ProtocolMode.STRUCTURED_V2:
            return
        bridge = self._dependencies.artifact_bridge()
        try:
            resolution = resolve_resume_state(
                self._dependencies.root(), state,
                validated_store=None if bridge is None else bridge.store,
            )
        except (ArtifactResumeError, ValueError) as exc:
            raise WorkflowExecutionError(f"structured audit replay failed: {exc}") from exc
        replay = resolution.replay_result
        if replay is None:
            raise WorkflowExecutionError("structured audit requires an accepted record replay")
        from artifact_store import ArtifactStore
        from audit_document_contract import PLAN_APPENDIX_HEADING
        from path_policy import resolve_repository_path
        from readable_audit import (
            AuditFacts, authored_slice_sections, read_approved_plan,
            render_overall, render_plan_appendix, render_slice,
        )
        from semantic_markdown import parse_semantic_markdown
        from state_io import atomic_write_file

        root = self._dependencies.root()
        store = bridge.store if bridge is not None else ArtifactStore(root, state.run_id)
        facts = AuditFacts(
            replay, read_blob=store.read_blob,
            read_plan=lambda commit, path: read_approved_plan(root, commit, path),
        )

        def audit_target(path: str | Path) -> Path:
            lexical = Path(str(path).replace("\\", "/"))
            if not lexical.is_absolute():
                lexical = root / lexical
            if any(part.is_symlink() for part in (lexical, *lexical.parents)):
                raise WorkflowExecutionError("audit target must not be a symlink")
            return resolve_repository_path(path, root)

        def write(path: str | Path, markdown: str) -> None:
            target = audit_target(path)
            if target.exists() and not target.is_file():
                raise WorkflowExecutionError("audit target must be a regular file")
            parse_semantic_markdown(markdown, path=str(target), require_managed=True)
            if not target.exists() or target.read_text(encoding="utf-8") != markdown:
                atomic_write_file(target, markdown)

        if state.audit_report_path is not None:
            write(
                state.audit_report_path,
                render_overall(
                    facts, task=Path(state.task_file).stem, branch=state.branch,
                ),
            )
        if state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW:
            return
        if state.current_slice.commit_ref is not None:
            return
        if state.current_work_unit.kind is WorkUnitKind.PLAN:
            if state.work_plan_path is None:
                return
            try:
                lexical_plan = root / state.work_plan_path
                if lexical_plan.is_symlink():
                    return
                plan_path = audit_target(state.work_plan_path)
                if not plan_path.is_file():
                    return
                plan_text = plan_path.read_text(encoding="utf-8")
                parse_semantic_markdown(plan_text, path=str(plan_path))
            except (OSError, UnicodeError, ValueError):
                logger.debug("Work-plan audit target is not yet safe to project")
                return
            heading = f"\n## {PLAN_APPENDIX_HEADING}\n"
            authored = plan_text.split(heading, 1)[0].rstrip("\n")
            write(state.work_plan_path, authored + "\n\n" + render_plan_appendix(facts))
            return
        planned = next(
            (item for item in state.planned_slices if item.slice_id == state.current_slice_id),
            None,
        )
        scope = planned.scope_paths if planned is not None else state.current_slice.scope_paths
        candidates = [
            path for path in scope
            if path.startswith("docs/internal/")
            and f"-{state.current_slice_id:02d}-" in Path(path).name
            and path.endswith(".md")
        ]
        if len(candidates) != 1:
            logger.debug("No unique Slice audit target is present for Slice %s", state.current_slice_id)
            return
        target = audit_target(candidates[0])
        implementation, deviations = (
            authored_slice_sections(target.read_text(encoding="utf-8"))
            if target.is_file() else ("Noch nicht dokumentiert.", "Keine.")
        )
        write(
            candidates[0],
            render_slice(
                facts, state.current_slice_id,
                implementation=implementation, deviations=deviations,
            ),
        )
