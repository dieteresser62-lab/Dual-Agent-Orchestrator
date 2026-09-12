"""Structured run baseline and provider bootstrap binding.

The production driver owns mutable runtime resources and the filesystem mirror
composition root.  This module owns baseline admission and append ordering,
and depends one-way on persistence and recovery; neither lower layer imports it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from artifact_bridge import (
    ArtifactBridge,
    plan_payload,
    provider_input_measurement_payload,
)
from finding_order import sorted_finding_ids
from artifact_resume import (
    ArtifactResumeError,
    require_gate_prefix,
    require_side_effect_ledger_prefix,
    require_workflow_event_prefix,
    require_workflow_status_prefix,
)
from artifact_models import (
    ArtifactRecord,
    CorrectionWorkUnitPayload,
    FingerprintKind,
    FindingHandoffImportPayload,
    GateTransitionPayload,
    ProviderInputMeasurementPayload,
    RecordType,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    SideEffectPayload,
    SliceBoundaryPayload,
    TaskPayload,
    WorkflowEventPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    WorkUnitPayload,
    canonical_json,
    stable_record_id,
    stable_side_effect_key,
)
from artifact_replay import (
    ArtifactReplayError,
    ArtifactReplayResult,
    replay_artifacts,
)
from final_review_preflight import (
    FINAL_REVIEW_OPERATIONS,
    FinalReviewPreflightDenied,
    preflight_payload,
    relevant_record_head,
    run_final_review_preflight,
    transition_fingerprint,
)
from finding_cleanup import (
    finding_cleanup_scope_paths,
    is_finding_cleanup_work_unit,
)
from finding_reducer import reduce_findings
from orchestrator_version import orchestrator_code_version
from provider_input_budget import ProviderInputMeasurement
from side_effects import (
    Reconciliation,
    ReconciliationOutcome,
    SideEffectExecutor,
    SideEffectSpec,
)
from workflow import WorkflowChanges, WorkflowExecutionError
from workflow_persistence import WorkflowPersistence
from workflow_recovery import WorkflowRecovery
from workflow_state import (
    BootstrapCheckFact,
    ProtocolBinding,
    WorkflowState,
    WorkUnitKind,
    WorkUnitRecord,
    project_implementer_return_policy,
)


def gate_transition_payload(unit: WorkUnitRecord) -> GateTransitionPayload:
    """Project the exact gate transition fact used by baseline and persistence."""

    return GateTransitionPayload(
        work_unit_id=str(unit.work_unit_id),
        gate_status=unit.gate.status.value,
        reason=unit.gate.reason.value,
        detail=unit.gate.detail,
        fingerprint=unit.gate.fingerprint,
        paths=unit.gate.paths,
        resume_step=(
            None if unit.gate.resume_step is None else unit.gate.resume_step.value
        ),
        active_test_fingerprint=unit.active_test_fingerprint,
        active_test_paths=unit.active_test_paths,
    )


def _resume_code_version(existing_replay: ArtifactReplayResult | None) -> str:
    if existing_replay is not None and existing_replay.run_profile is not None:
        return existing_replay.run_profile.orchestrator_code_version
    return orchestrator_code_version()


def _append_baseline_identity_expectations(
    state: WorkflowState,
    binding: ProtocolBinding,
    expect: Callable[..., None],
    code_version: str | None = None,
) -> None:
    expected_identity = RunIdentityPayload(
        task_file=state.task_file,
        branch=state.branch,
        branch_base=state.branch_base,
        first_slice_start_commit=state.slices[0].start_commit,
        execution_mode=state.execution_mode,
        audit_report_path=state.audit_report_path,
    )
    expected_profile = RunProfilePayload(
        implementer=RoleProfilePayload(
            binding.codex_profile.model, binding.codex_profile.effort  # allowlist:provider
        ),
        reviewer=RoleProfilePayload(
            binding.claude_profile.model, binding.claude_profile.effort  # allowlist:provider
        ),
        orchestrator_code_version=code_version or orchestrator_code_version(),
    )
    identity_record_id = stable_record_id(
        state.run_id, RecordType.RUN_IDENTITY, "run-identity", 1
    )
    expected_event = WorkflowEventPayload(
        event_kind="run",
        work_unit_id=None,
        slice_id="1",
        round_number=None,
        record_refs=(identity_record_id,),
    )
    expect(expected_identity, "run-identity", "run-identity")
    expect(
        expected_event,
        f"workflow-event-{identity_record_id}",
        f"workflow-event:{identity_record_id}",
    )
    expect(expected_profile, "run-profile", "run-profile")

    ledger_operation = ("structured-v2-side-effect-ledger",)
    ledger_key = stable_side_effect_key("ledger", "run", ledger_operation)
    ledger_digest = hashlib.sha256(ledger_key.encode("utf-8")).hexdigest()
    ledger_logical_id = f"side-effect-{ledger_digest[:32]}"
    expect(
        SideEffectPayload(
            ledger_key, "ledger", "run", ledger_operation, "intent", None
        ),
        ledger_logical_id,
        f"side-effect-intent:{ledger_digest}",
        1,
    )
    expect(
        SideEffectPayload(
            ledger_key,
            "ledger",
            "run",
            ledger_operation,
            "result",
            "initialized",
        ),
        ledger_logical_id,
        f"side-effect-result:{ledger_digest}",
        2,
    )


def _append_baseline_transition_expectations(
    state: WorkflowState,
    expect: Callable[..., None],
) -> WorkUnitRecord:
    transition_revision = 0

    def expect_transition(payload: WorkflowTransitionPayload) -> None:
        nonlocal transition_revision
        transition_revision += 1
        transition_id = stable_record_id(
            state.run_id,
            RecordType.WORKFLOW_TRANSITION,
            "workflow-transition",
            transition_revision,
        )
        expect(
            payload,
            "workflow-transition",
            f"workflow-transition:{transition_revision}",
            transition_revision,
        )
        expect(
            WorkflowEventPayload(
                "transition",
                payload.work_unit_id,
                payload.slice_id,
                None,
                (transition_id,),
            ),
            f"workflow-event-{transition_id}",
            f"workflow-event:{transition_id}",
        )

    unit_slice_ids = {str(item.slice_id) for item in state.work_units}
    for item in state.slices:
        slice_id = str(item.slice_id)
        if slice_id not in unit_slice_ids:
            expect_transition(
                WorkflowTransitionPayload(
                    slice_id, item.status.value, None, None, None
                )
            )
    current_unit_id = str(state.current_work_unit_id)
    for item in state.work_units:
        work_unit_id = str(item.work_unit_id)
        if work_unit_id == current_unit_id:
            continue
        slice_status = next(
            candidate.status.value
            for candidate in state.slices
            if candidate.slice_id == item.slice_id
        )
        expect_transition(
            WorkflowTransitionPayload(
                str(item.slice_id),
                slice_status,
                work_unit_id,
                item.current_step.value,
                item.status.value,
            )
        )
    current = state.current_work_unit
    expect_transition(
        WorkflowTransitionPayload(
            str(state.current_slice_id),
            state.current_slice.status.value,
            current_unit_id,
            state.current_step.value,
            current.status.value,
        )
    )
    for item in state.work_units:
        work_unit_id = str(item.work_unit_id)
        expect(
            WorkflowPolicyPayload(
                work_unit_id, *project_implementer_return_policy(item)
            ),
            f"workflow-policy-{work_unit_id}",
            f"workflow-policy:{work_unit_id}:1",
        )
    for item in state.slices:
        if item.start_commit is None or item.start_fingerprint is None:
            continue
        payload = SliceBoundaryPayload(
            str(item.slice_id),
            item.start_commit,
            item.scope_change_groups,
            item.start_fingerprint,
        )
        expect(
            payload,
            f"slice-boundary-{payload.slice_id}",
            f"slice-boundary:{payload.slice_id}:1",
        )
    for item in state.work_units:
        payload = gate_transition_payload(item)
        expect(
            payload,
            f"gate-transition-{payload.work_unit_id}",
            f"gate-transition:{payload.work_unit_id}:1",
        )

    return current


def _append_baseline_contract_expectations(
    records: tuple[ArtifactRecord, ...],
    state: WorkflowState,
    first_domain: int,
    current: WorkUnitRecord,
    expect: Callable[..., None],
) -> None:
    if state.task_scope_patterns:
        expect(
            TaskPayload(
                target_branch=state.target_branch or state.branch,
                scope_paths=state.task_scope_patterns,
                assignment_sha256=state.task_digest,
                work_plan_path=state.work_plan_path,
            ),
            "task-contract",
            "task-contract",
        )
    if current.kind is not WorkUnitKind.PLAN and state.current_slice.scope_paths:
        finding_import = next(
            (
                record
                for record in records[:first_domain]
                if isinstance(record.payload, FindingHandoffImportPayload)
            ),
            None,
        )
        first_implementation_unit_id = next(
            item.work_unit_id
            for item in state.work_units
            if item.kind is not WorkUnitKind.PLAN
        )
        bound_import = (
            finding_import
            if current.work_unit_id == first_implementation_unit_id
            else None
        )
        work_unit_payload = (
            CorrectionWorkUnitPayload(
                slice_id=str(current.slice_id),
                round_number=current.round_number,
                paths=state.current_slice.scope_paths,
                finding_ids=current.open_findings,
            )
            if current.kind is WorkUnitKind.CORRECTION
            else WorkUnitPayload(
                slice_id=str(current.slice_id),
                round_number=current.round_number,
                paths=state.current_slice.scope_paths,
                open_finding_ids=(
                    sorted_finding_ids(current.open_findings)
                    if bound_import is not None
                    else ()
                ),
                finding_import_record_id=(
                    bound_import.record_id if bound_import is not None else None
                ),
            )
        )
        logical_id = f"work-unit-{current.work_unit_id}"
        expect(
            work_unit_payload,
            logical_id,
            f"{'correction-' if current.kind is WorkUnitKind.CORRECTION else ''}"
            f"work-unit:{current.work_unit_id}:round:{current.round_number}",
        )


def matches_baseline_initialization_prefix(
    records: tuple[ArtifactRecord, ...], state: WorkflowState
) -> bool:
    """Recognize every exact cut of the canonical pre-work append sequence.

    This is the protocol grammar for resolution A in S5b.  The expected
    sequence is derived independently from the immutable state input and
    covers identity/profile, the ledger initializer, initial workflow and
    gate projections, task/work-unit facts, and an optional approved-plan
    handoff.  A non-prefix fact or any previously completed non-ledger effect
    therefore keeps the ordinary fail-closed resume behavior.
    """

    binding = state.protocol_binding
    if (
        binding is None
        or state.task_digest is None
        or not state.slices
        or not state.slices[0].start_commit
    ):
        return False
    if state.runtime_history is not None or any(
        unit.invocation_failures
        or unit.completed_side_effects
        or unit.gate_decisions
        for unit in state.work_units
    ):
        return False
    first_domain = next(
        (
            index
            for index, record in enumerate(records)
            if not isinstance(record.payload, FindingHandoffImportPayload)
        ),
        len(records),
    )
    prefix = records[first_domain:]
    if not prefix:
        return False

    expectations: list[tuple[object, str, str, int]] = []

    def expect(
        payload: object,
        logical_id: str,
        idempotency_key: str,
        revision: int = 1,
    ) -> None:
        expectations.append((payload, logical_id, idempotency_key, revision))

    code_version = next(
        (
            record.payload.orchestrator_code_version
            for record in prefix
            if isinstance(record.payload, RunProfilePayload)
        ),
        None,
    )
    _append_baseline_identity_expectations(
        state,
        binding,
        expect,
        code_version,
    )
    current = _append_baseline_transition_expectations(state, expect)
    _append_baseline_contract_expectations(
        records, state, first_domain, current, expect
    )
    if (
        state.work_plan_path is not None
        and state.planned_slices
        and state.approved_plan_commit is not None
    ):
        expect(
            plan_payload(
                work_plan_path=state.work_plan_path,
                approved_plan_commit=state.approved_plan_commit,
                slices=state.planned_slices,
            ),
            "approved-plan",
            f"approved-plan:{state.approved_plan_commit}",
        )

    if len(prefix) > len(expectations):
        return False
    return all(
        record.payload == payload
        and record.logical_id == logical_id
        and record.idempotency_key == idempotency_key
        and record.revision == revision
        and record.fingerprint.sha256 == state.task_digest
        and record.fingerprint.kind is FingerprintKind.CONTRACT
        for record, (payload, logical_id, idempotency_key, revision) in zip(
            prefix, expectations[: len(prefix)], strict=True
        )
    )


def bootstrap_fact(
    payload: ProviderInputMeasurementPayload | object,
) -> BootstrapCheckFact:
    """Project one immutable provider preflight result into workflow state."""

    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    check_kind = payload.record_type.value
    decision = (
        "allowed"
        if isinstance(payload, ProviderInputMeasurementPayload) and payload.allowed
        else "denied"
        if getattr(payload, "outcome", None) == "denied"
        or isinstance(payload, ProviderInputMeasurementPayload)
        else "passed"
    )
    return BootstrapCheckFact(
        check_kind=check_kind,
        transition_fingerprint=payload.transition_fingerprint,
        provider=payload.provider.value,
        role=payload.role.value,
        operation=payload.operation,
        work_unit_id=int(payload.work_unit_id),
        semantic_digest=digest,
        decision=decision,
        error_code=(
            "PROVIDER-INPUT-BUDGET"
            if isinstance(payload, ProviderInputMeasurementPayload)
            and not payload.allowed
            else getattr(payload, "error_code", None)
        ),
    )


@dataclass(frozen=True)
class WorkflowBaselineDependencies:
    """Driver-owned resources and the explicit lower-layer composition edges."""

    artifact_bridge: Callable[[], ArtifactBridge | None]
    active_state: Callable[[], WorkflowState | None]
    artifact_fingerprint: Callable[[], str]
    collect_changes: Callable[[str], WorkflowChanges]
    persist_bootstrap_state: Callable[[WorkflowState], None]
    persistence: Callable[[], WorkflowPersistence]
    recovery: Callable[[], WorkflowRecovery]
    reconcile_pending_workflow_event: Callable[
        [ArtifactReplayResult], ArtifactRecord
    ]
    side_effect_executor: Callable[[ArtifactBridge], SideEffectExecutor]


class WorkflowBaseline:
    """Bind the canonical baseline before any provider or external effect."""

    def __init__(self, dependencies: WorkflowBaselineDependencies) -> None:
        self._dependencies = dependencies

    def _replay_existing_baseline_chain(
        self,
        existing_chain: tuple[ArtifactRecord, ...],
        state: WorkflowState,
        import_only_prefix: bool,
    ) -> ArtifactReplayResult:
        return replay_artifacts(
            existing_chain,
            state.run_id,
            allow_incomplete_review_tail=True,
            allow_finding_import_bootstrap=import_only_prefix,
        )

    def _require_existing_baseline_prefix(
        self, existing_replay: ArtifactReplayResult
    ) -> None:
        require_workflow_status_prefix(existing_replay)
        require_gate_prefix(existing_replay)
        require_side_effect_ledger_prefix(existing_replay)

    def _append_baseline_identity_and_ledger(
        self,
        state: WorkflowState,
        bridge: ArtifactBridge,
        binding: ProtocolBinding,
        contract_fingerprint: str,
        existing_replay: ArtifactReplayResult | None,
    ) -> tuple[WorkflowPersistence, set[str]]:
        persistence = self._dependencies.persistence()
        identity_record = bridge.append(
            RunIdentityPayload(
                task_file=state.task_file,
                branch=state.branch,
                branch_base=state.branch_base,
                first_slice_start_commit=state.slices[0].start_commit,
                execution_mode=state.execution_mode,
                audit_report_path=state.audit_report_path,
            ),
            logical_id="run-identity",
            idempotency_key="run-identity",
            fingerprint_sha256=contract_fingerprint,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
        persistence._append_workflow_event(
            event_kind="run",
            work_unit_id=None,
            slice_id="1",
            round_number=None,
            domain_record=identity_record,
        )
        bridge.append(
            RunProfilePayload(
                implementer=RoleProfilePayload(
                    binding.codex_profile.model, binding.codex_profile.effort
                ),
                reviewer=RoleProfilePayload(
                    binding.claude_profile.model, binding.claude_profile.effort
                ),
                orchestrator_code_version=_resume_code_version(existing_replay),
            ),
            logical_id="run-profile",
            idempotency_key="run-profile",
            fingerprint_sha256=contract_fingerprint,
            fingerprint_kind=FingerprintKind.CONTRACT,
        )
        ledger_operation = ("structured-v2-side-effect-ledger",)
        completed_effect_keys = {
            item.effect_key
            for item in (() if existing_replay is None else existing_replay.side_effects)
            if item.result is not None
        }
        ledger_key = stable_side_effect_key("ledger", "run", ledger_operation)
        if ledger_key not in completed_effect_keys:
            ledger_spec = SideEffectSpec(
                "ledger",
                "run",
                ledger_operation,
                contract_fingerprint,
                FingerprintKind.CONTRACT,
            )
            self._dependencies.side_effect_executor(bridge).execute(
                ledger_spec,
                reconcile=lambda: Reconciliation(ReconciliationOutcome.NOT_OCCURRED),
                perform=lambda: (None, "initialized"),
            )
        if existing_replay is not None:
            self._dependencies.recovery()._reconcile_pending_side_effects(
                state, existing_replay
            )
        return persistence, completed_effect_keys

    def _append_completed_internal_effects(
        self,
        state: WorkflowState,
        bridge: ArtifactBridge,
        contract_fingerprint: str,
        completed_effect_keys: set[str],
    ) -> None:
        for work_unit in state.work_units:
            for marker in work_unit.completed_side_effects:
                if marker.startswith("side-effect:"):
                    continue
                operation = (marker,)
                if stable_side_effect_key(
                    "internal", str(work_unit.work_unit_id), operation
                ) in completed_effect_keys:
                    continue
                internal_spec = SideEffectSpec(
                    "internal",
                    str(work_unit.work_unit_id),
                    operation,
                    contract_fingerprint,
                    FingerprintKind.CONTRACT,
                )
                self._dependencies.side_effect_executor(bridge).execute(
                    internal_spec,
                    reconcile=lambda: Reconciliation(
                        ReconciliationOutcome.OCCURRED, "completed"
                    ),
                    perform=lambda: (None, "completed"),
                )

    def _append_baseline_state_facts(
        self,
        state: WorkflowState,
        bridge: ArtifactBridge,
        persistence: WorkflowPersistence,
        contract_fingerprint: str,
    ) -> None:
        persistence._persist_workflow_snapshot(state)
        persistence._persist_slice_boundaries(state)
        persistence._persist_gate_snapshot(state)
        if state.task_scope_patterns:
            bridge.append(
                TaskPayload(
                    target_branch=state.target_branch or state.branch,
                    scope_paths=state.task_scope_patterns,
                    assignment_sha256=state.task_digest,
                    work_plan_path=state.work_plan_path,
                ),
                logical_id="task-contract",
                idempotency_key="task-contract",
                fingerprint_sha256=contract_fingerprint,
                fingerprint_kind=FingerprintKind.CONTRACT,
            )
        unit = state.current_work_unit
        if unit.kind is not WorkUnitKind.PLAN and state.current_slice.scope_paths:
            chain = bridge.store.current_chain()
            logical_id = f"work-unit-{unit.work_unit_id}"
            work_unit_paths = state.current_slice.scope_paths
            if is_finding_cleanup_work_unit(state, unit):
                cleanup_boundary = next(
                    (
                        record.payload
                        for record in chain
                        if isinstance(record.payload, CorrectionWorkUnitPayload)
                        and record.logical_id == logical_id
                    ),
                    None,
                )
                if cleanup_boundary is not None:
                    work_unit_paths = cleanup_boundary.paths
                else:
                    cleanup_findings = reduce_findings(
                        replay_artifacts(
                            chain,
                            state.run_id,
                            allow_empty=True,
                            allow_incomplete_review_tail=True,
                        )
                    ).ledger.findings
                    work_unit_paths = finding_cleanup_scope_paths(
                        cleanup_findings,
                        unit.open_findings,
                        repository_root=bridge.store.repository_root,
                    )
                if not work_unit_paths:
                    raise WorkflowExecutionError(
                        "finding cleanup has no finding-derived repository scope"
                    )
            finding_import = next(
                (
                    record
                    for record in chain
                    if isinstance(record.payload, FindingHandoffImportPayload)
                ),
                None,
            )
            first_implementation_unit_id = next(
                item.work_unit_id
                for item in state.work_units
                if item.kind is not WorkUnitKind.PLAN
            )
            bound_import = (
                finding_import
                if unit.work_unit_id == first_implementation_unit_id
                else None
            )
            work_unit_payload = (
                CorrectionWorkUnitPayload(
                    slice_id=str(unit.slice_id),
                    round_number=unit.round_number,
                    paths=work_unit_paths,
                    finding_ids=unit.open_findings,
                )
                if unit.kind is WorkUnitKind.CORRECTION
                else WorkUnitPayload(
                    slice_id=str(unit.slice_id),
                    round_number=unit.round_number,
                    paths=work_unit_paths,
                    open_finding_ids=(
                        sorted_finding_ids(unit.open_findings)
                        if bound_import is not None
                        else ()
                    ),
                    finding_import_record_id=(
                        bound_import.record_id if bound_import is not None else None
                    ),
                )
            )
            base_idempotency_key = (
                f"{'correction-' if unit.kind is WorkUnitKind.CORRECTION else ''}"
                f"work-unit:{unit.work_unit_id}:round:{unit.round_number}"
            )
            prior = next(
                (
                    record
                    for record in reversed(chain)
                    if record.record_type is work_unit_payload.record_type
                    and record.logical_id == logical_id
                ),
                None,
            )
            if prior is None or prior.payload != work_unit_payload:
                bridge.append(
                    work_unit_payload,
                    logical_id=logical_id,
                    idempotency_key=(
                        base_idempotency_key
                        if prior is None
                        else f"{base_idempotency_key}:revision:{prior.revision + 1}"
                    ),
                    fingerprint_sha256=contract_fingerprint,
                    fingerprint_kind=FingerprintKind.CONTRACT,
                )
        persistence._persist_structured_tail(state)

    def _persist_structured_baseline(self, state: WorkflowState) -> None:
        bridge = self._dependencies.artifact_bridge()
        if bridge is None or state.task_digest is None:
            return
        contract_fingerprint = state.task_digest
        binding = state.protocol_binding
        if binding is None:
            raise WorkflowExecutionError(
                "structured baseline requires the immutable protocol binding"
            )
        existing_chain = bridge.store.current_chain()
        existing_replay = None
        if existing_chain:
            import_only_prefix = all(
                isinstance(record.payload, FindingHandoffImportPayload)
                for record in existing_chain
            )
            try:
                existing_replay = self._replay_existing_baseline_chain(
                    existing_chain, state, import_only_prefix
                )
            except ArtifactReplayError:
                if not matches_baseline_initialization_prefix(
                    existing_chain, state
                ):
                    raise
                existing_replay = None
            if existing_replay is None:
                # The raw chain is an exact early initializer cut which cannot
                # yet satisfy the general replay minimum (for example identity
                # plus its event but no profile).  The idempotent appends below
                # complete it before any projection reader or external effect.
                pass
            elif existing_replay.pending_workflow_event_record_id is not None:
                self._dependencies.reconcile_pending_workflow_event(existing_replay)
                existing_replay = replay_artifacts(
                    bridge.store.current_chain(),
                    state.run_id,
                    allow_incomplete_review_tail=True,
                    allow_finding_import_bootstrap=import_only_prefix,
                )
            if existing_replay is not None:
                require_workflow_event_prefix(existing_replay)
            if (
                existing_replay is not None
                and existing_replay.pending_review_record_id is not None
            ):
                # The reviewer recovery path is the only writer allowed to
                # complete this exact append tail.  Appending baseline facts
                # here would turn the recoverable suffix into a chain-middle
                # authority gap.
                return
            if existing_replay is not None and not import_only_prefix:
                try:
                    self._require_existing_baseline_prefix(existing_replay)
                except ArtifactResumeError:
                    if not matches_baseline_initialization_prefix(
                        existing_replay.records, state
                    ):
                        raise
        persistence, completed_effect_keys = self._append_baseline_identity_and_ledger(
            state, bridge, binding, contract_fingerprint, existing_replay
        )
        self._append_completed_internal_effects(
            state, bridge, contract_fingerprint, completed_effect_keys
        )
        self._append_baseline_state_facts(
            state, bridge, persistence, contract_fingerprint
        )

    def _persist_provider_bootstrap(
        self, measurement: ProviderInputMeasurement
    ) -> ArtifactRecord | None:
        """Dual-write a lossless measurement and final-transition preflight."""

        state = self._dependencies.active_state()
        if state is None:
            raise WorkflowExecutionError("provider bootstrap has no active state")
        bridge = self._dependencies.artifact_bridge()
        chain = bridge.store.current_chain() if bridge is not None else ()
        record_head = relevant_record_head(chain)
        final_review_changes = (
            self._dependencies.collect_changes(state.branch_base)
            if measurement.operation in FINAL_REVIEW_OPERATIONS
            else None
        )
        repository_fingerprint = (
            final_review_changes.fingerprint
            if final_review_changes is not None
            else self._dependencies.artifact_fingerprint()
        )
        transition = transition_fingerprint(
            provider=measurement.provider,
            role=measurement.role,
            operation=measurement.operation,
            work_unit_id=str(state.current_work_unit_id),
            record_head=record_head,
            repository_fingerprint=repository_fingerprint,
            input_digest=measurement.input_digest,
            policy_digest=measurement.policy_digest,
        )
        payload = provider_input_measurement_payload(
            measurement,
            work_unit_id=state.current_work_unit_id,
            transition_fingerprint=transition,
            relevant_record_head=record_head,
        )
        measurement_record = None
        if bridge is not None:
            measurement_record = bridge.append(
                payload,
                logical_id=(
                    f"provider-input-{state.current_work_unit_id}-"
                    f"{measurement.operation}"
                ),
                idempotency_key=f"provider-input:{transition}",
                fingerprint_sha256=repository_fingerprint,
            )
        state = state.with_bootstrap_check(bootstrap_fact(payload))
        self._dependencies.persist_bootstrap_state(state)
        if (
            measurement.operation not in FINAL_REVIEW_OPERATIONS
            or not measurement.allowed
            or bridge is None
            or is_finding_cleanup_work_unit(state)
        ):
            return measurement_record
        assert measurement_record is not None
        current_chain = bridge.store.current_chain()
        repository_paths = (
            final_review_changes.paths
            if final_review_changes is not None
            else ()
        )
        result = run_final_review_preflight(
            state=state,
            records=current_chain,
            measurement_record=measurement_record,
            repository_paths=repository_paths,
        )
        checked = preflight_payload(
            measurement_record=measurement_record, result=result
        )
        bridge.append(
            checked,
            logical_id=(
                f"final-preflight-{state.current_work_unit_id}-"
                f"{measurement.operation}"
            ),
            idempotency_key=f"final-preflight:{transition}",
            fingerprint_sha256=repository_fingerprint,
        )
        state = state.with_bootstrap_check(bootstrap_fact(checked))
        self._dependencies.persist_bootstrap_state(state)
        if not result.passed:
            raise FinalReviewPreflightDenied(
                result,
                fingerprint=repository_fingerprint,
            )
        return measurement_record
