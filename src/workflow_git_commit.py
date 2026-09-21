"""Ledger-bracketed Git Slice commit transaction.

The production driver remains the composition root and public workflow
surface.  This module owns the complete commit authorization, intent/result
bracket, reconciliation, and authoritative commit binding as one indivisible
operation; no lower workflow layer imports it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, cast

from artifact_bridge import (
    ArtifactBridge,
    attestation_payload,
    review_payload_matches_complete_result,
)
from artifact_models import ArtifactRecord, BindingPayload, ReviewPayload
from artifact_replay import replay_artifacts
from git_service import (
    CommitAuthorization,
    RepositoryIdentity,
    SliceGitBoundary,
    commit_slice,
    inspect_commit_tree,
    inspect_repository,
    preview_commit_tree,
)
from repo_changes import RepositoryChanges, collect_repository_changes
from side_effects import (
    ReconciliationOutcome,
    SideEffectExecutor,
    SideEffectSpec,
    reconcile_git_commit,
)
from slice_exit import evaluate_slice_exit
from workflow import (
    WorkflowCommitApprovalRequired,
    WorkflowCommitRequest,
    WorkflowExecutionError,
)
from workflow_state import (
    GateDecisionRecord,
    GateReason,
    SliceRecord,
    WorkflowState,
    WorkUnitKind,
)


class RootProvider(Protocol):
    def __call__(self) -> Path: ...


class ActiveStateProvider(Protocol):
    def __call__(self) -> WorkflowState | None: ...


class ArtifactBridgeProvider(Protocol):
    def __call__(self) -> ArtifactBridge | None: ...


class RepositoryChangesProvider(Protocol):
    def __call__(self, fingerprint: str) -> RepositoryChanges | None: ...


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
class WorkflowGitCommitDependencies:
    """Driver-owned edges required by the complete Slice commit transaction."""

    root: RootProvider
    active_state: ActiveStateProvider
    artifact_bridge: ArtifactBridgeProvider
    assert_structured_decision_context: Callable[[], None]
    repository_changes: RepositoryChangesProvider
    mark_completed_side_effect: Callable[[str], None]
    side_effect_executor: SideEffectExecutorFactory
    side_effect_spec: SideEffectSpecFactory
    bound_task_control_paths: BoundTaskControlPaths


@dataclass(frozen=True)
class _CommitContext:
    root: Path
    state: WorkflowState
    current: SliceRecord
    boundary: SliceGitBoundary
    summary: str
    reviewed_changes: RepositoryChanges
    unexpected_paths: tuple[str, ...]
    exact_scope_approval: bool
    artifact_bridge: ArtifactBridge | None
    head_approval: GateDecisionRecord | None


class WorkflowGitCommit:
    """Authorize and execute exactly one ledger-bracketed Slice commit."""

    def __init__(self, dependencies: WorkflowGitCommitDependencies) -> None:
        self._dependencies = dependencies

    def _prepare_commit_context(self, request: WorkflowCommitRequest) -> _CommitContext:
        if self._dependencies.active_state() is None:
            raise WorkflowExecutionError("slice commit has no active state")
        self._dependencies.assert_structured_decision_context()
        state = cast(WorkflowState, self._dependencies.active_state())
        current = state.current_slice
        if current.start_commit is None or current.start_fingerprint is None:
            raise WorkflowExecutionError("slice commit has no persisted Git boundary")
        root = self._dependencies.root()
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
            excluded_control_paths=self._dependencies.bound_task_control_paths(
                root, state
            ),
        )
        summary = next(
            (
                item.summary
                for item in state.planned_slices
                if item.slice_id == current.slice_id
            ),
            "apply approved correction",
        )
        reviewed_changes = self._dependencies.repository_changes(request.fingerprint)
        if reviewed_changes is None:
            raise WorkflowExecutionError(
                "slice commit has no canonical repository evidence for its fingerprint"
            )
        unexpected_paths = tuple(
            path for path in reviewed_changes.paths if path not in current.scope_paths
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
        artifact_bridge = self._dependencies.artifact_bridge()
        head_approval = next(
            (
                decision
                for decision in reversed(state.current_work_unit.gate_decisions)
                if decision.approved
                and decision.fingerprint == request.fingerprint
                and decision.paths == (unexpected_paths or reviewed_changes.paths)
                and decision.reason
                in {GateReason.UNEXPECTED_FILE, GateReason.QUOTA_RESUME_DIFF}
            ),
            None,
        )
        return _CommitContext(
            root=root,
            state=state,
            current=current,
            boundary=boundary,
            summary=summary,
            reviewed_changes=reviewed_changes,
            unexpected_paths=unexpected_paths,
            exact_scope_approval=exact_scope_approval,
            artifact_bridge=artifact_bridge,
            head_approval=head_approval,
        )

    def _prepare_git_operation(
        self,
        context: _CommitContext,
        request: WorkflowCommitRequest,
    ) -> tuple[RepositoryIdentity, tuple[str, ...]]:
        root = context.root
        state = context.state
        current = context.current
        boundary = context.boundary
        summary = context.summary
        reviewed_changes = context.reviewed_changes
        unexpected_paths = context.unexpected_paths
        artifact_bridge = context.artifact_bridge
        head_approval = context.head_approval
        identity = inspect_repository(root)
        existing_git_effect = None
        if artifact_bridge is not None:
            effect_replay = replay_artifacts(
                artifact_bridge.store.current_chain(), state.run_id
            )
            existing_git_effect = next(
                (
                    item
                    for item in reversed(effect_replay.side_effects)
                    if item.effect_class == "git_commit"
                    and item.work_unit_id == str(state.current_work_unit_id)
                    and len(item.operation) == 6
                    and item.operation[0] == "slice_commit"
                    and item.operation[1] == str(request.slice_id)
                    and item.result is None
                ),
                None,
            )
        if (
            existing_git_effect is not None
            and existing_git_effect.operation[4] != request.fingerprint
        ):
            raise WorkflowExecutionError(
                "pending Slice commit belongs to another reviewed fingerprint"
            )
        if existing_git_effect is None:
            if identity.head == boundary.start_commit:
                transaction_changes = reviewed_changes
            else:
                semantic_transaction_paths = tuple(
                    sorted(
                        path
                        for path in (
                            *boundary.semantic_markdown_paths,
                            *unexpected_paths,
                        )
                        if path.startswith("docs/internal/")
                        and path.endswith(".md")
                    )
                )
                transaction_changes = collect_repository_changes(
                    root,
                    identity.head,
                    semantic_markdown_paths=semantic_transaction_paths,
                    excluded_paths=boundary.excluded_control_paths,
                )
            expected_tree = preview_commit_tree(root, transaction_changes)
        else:
            expected_tree = existing_git_effect.operation[3]
        git_operation = (
            existing_git_effect.operation
            if existing_git_effect is not None
            else (
                "slice_commit",
                str(request.slice_id),
                identity.head,
                expected_tree,
                request.fingerprint,
                hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            )
        )
        own_commit_recovery = False
        if existing_git_effect is not None and identity.head != git_operation[2]:
            parent, tree = inspect_commit_tree(root, identity.head)
            own_commit_recovery = (
                reconcile_git_commit(
                    prior_head=git_operation[2],
                    current_head=identity.head,
                    current_parent=parent,
                    expected_tree=git_operation[3],
                    current_tree=tree,
                ).outcome
                is ReconciliationOutcome.OCCURRED
            )
        if (
            identity.head != current.start_commit
            and head_approval is None
            and not own_commit_recovery
        ):
            raise WorkflowCommitApprovalRequired(
                (
                    "HEAD-DRIFT | the Slice HEAD changed after its persisted start; "
                    f"approve the exact reviewed fingerprint {request.fingerprint} "
                    f"and current HEAD {identity.head} before committing"
                ),
                unexpected_paths or reviewed_changes.paths,
            )

        return identity, git_operation

    def _resolve_structured_binding(
        self,
        context: _CommitContext,
        request: WorkflowCommitRequest,
        review_result: object,
    ) -> tuple[tuple[str, tuple[str, ...]] | None, ArtifactRecord | None]:
        artifact_bridge = context.artifact_bridge
        state = context.state
        structured_attestation = None
        approval_records: tuple[ArtifactRecord, ...] = ()
        current_review_record: ArtifactRecord | None = None
        structured_binding: tuple[str, tuple[str, ...]] | None = None
        if artifact_bridge is not None:
            chain = artifact_bridge.store.current_chain()
            structured_attestation = next(
                (
                    item
                    for item in reversed(chain)
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
                    item
                    for item in reversed(approval_records)
                    if item.payload.work_unit_id == str(state.current_work_unit_id)
                ),
                None,
            )
            if structured_attestation is None or not approval_records:
                raise WorkflowExecutionError(
                    "structured commit binding requires persisted attestation and approvals"
                )
            if structured_attestation.payload != attestation_payload(
                request.attestation,
                structured_attestation.payload.content_record_id,
            ):
                raise WorkflowExecutionError(
                    "structured commit attestation differs from the commit request"
                )
            if current_review_record is None or not review_payload_matches_complete_result(
                current_review_record.payload,
                review_result,
            ):
                raise WorkflowExecutionError(
                    "structured commit review differs from the commit request"
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
        return structured_binding, current_review_record

    def commit_slice(self, request: WorkflowCommitRequest) -> str:
        context = self._prepare_commit_context(request)
        root = context.root
        state = context.state
        boundary = context.boundary
        summary = context.summary
        reviewed_changes = context.reviewed_changes
        unexpected_paths = context.unexpected_paths
        exact_scope_approval = context.exact_scope_approval
        artifact_bridge = context.artifact_bridge
        head_approval = context.head_approval
        identity, git_operation = self._prepare_git_operation(context, request)
        review_result = request.claude_review
        structured_binding, current_review_record = self._resolve_structured_binding(
            context,
            request,
            review_result,
        )
        if (artifact_bridge is None) != (structured_binding is None):
            raise WorkflowExecutionError(
                "structured commit binding was not established before the Git transaction"
            )
        slice_exit = None
        if state.current_work_unit.kind is WorkUnitKind.SLICE:
            if artifact_bridge is None:
                raise WorkflowExecutionError(
                    "Slice commit requires the authoritative record chain for E4"
                )
            slice_exit = evaluate_slice_exit(
                artifact_bridge.store.current_chain(),
                run_id=state.run_id,
                slice_id=request.slice_id,
                approved_plan_commit=state.approved_plan_commit,
            )
            if not slice_exit.commit_eligible:
                reasons = "; ".join(
                    reason
                    for condition in slice_exit.conditions
                    for reason in condition.reasons
                )
                raise WorkflowExecutionError(
                    "Slice commit rejected by the record-derived E4 exit condition"
                    + (f": {reasons}" if reasons else "")
                )

        def perform_commit():  # type: ignore[no-untyped-def]
            committed = commit_slice(
                repository_root=root,
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
                    slice_exit=slice_exit,
                ),
                title=summary,
            )
            return committed, committed.commit_hash

        if artifact_bridge is None:
            result = perform_commit()[0]
            commit_hash = result.commit_hash
        else:
            git_spec = self._dependencies.side_effect_spec(
                "git_commit",
                git_operation,
                fingerprint=request.fingerprint,
            )

            def reconcile_commit():  # type: ignore[no-untyped-def]
                current_identity = inspect_repository(root)
                if current_identity.head == git_operation[2]:
                    return reconcile_git_commit(
                        prior_head=git_operation[2],
                        current_head=current_identity.head,
                        current_parent=None,
                        expected_tree=git_operation[3],
                        current_tree=None,
                    )
                parent, tree = inspect_commit_tree(root, current_identity.head)
                return reconcile_git_commit(
                    prior_head=git_operation[2],
                    current_head=current_identity.head,
                    current_parent=parent,
                    expected_tree=git_operation[3],
                    current_tree=tree,
                )

            executed = self._dependencies.side_effect_executor(
                artifact_bridge
            ).execute(
                git_spec,
                reconcile=reconcile_commit,
                perform=perform_commit,
            )
            commit_hash = (
                executed.commit_hash
                if hasattr(executed, "commit_hash")
                else str(executed)
            )
            self._dependencies.mark_completed_side_effect(git_spec.effect_key)
        if artifact_bridge is not None and structured_binding is not None:
            attestation_record_id, approval_record_ids = structured_binding
            artifact_bridge.append(
                BindingPayload(
                    binding_kind="commit",
                    target=commit_hash,
                    attestation_id=attestation_record_id,
                    approval_ids=approval_record_ids,
                ),
                logical_id=f"commit-{request.slice_id}-{commit_hash[:12]}",
                idempotency_key=f"commit:{request.slice_id}:{request.fingerprint}",
                fingerprint_sha256=request.fingerprint,
            )
        return commit_hash
