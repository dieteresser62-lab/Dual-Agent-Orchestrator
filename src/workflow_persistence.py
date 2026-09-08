"""Structured workflow persistence sinks.

The production driver owns mutable runtime resources and composes baseline,
audit, and state projections.  This module owns the durable sink behavior and
receives every driver-owned edge explicitly, keeping the import direction
one-way: the driver imports persistence, never conversely.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from agent_runtime import (
    NativeAgentCodexOutput as NativeAgentImplementerOutput,
    NativeAgentReviewOutput,
)
from artifact_bridge import (
    ArtifactBridge,
    agent_result_payload,
    attestation_payload,
    command_payload,
    finding_payload,
    plan_payload,
    review_payload,
    validation_request_payload,
)
from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BindingPayload,
    FingerprintKind,
    GatePayload,
    GateTransitionPayload,
    InvocationFailurePayload,
    ProviderContentPayload,
    QuotaPausePayload,
    RecordType,
    ReviewAnchor,
    ReviewAnchorPayload,
    ReviewPacketPayload,
    ReviewValidationBindingPayload,
    Role,
    SliceBoundaryPayload,
    TransientRetryPayload,
    ValidationAttestationPayload,
    ValidationContentPayload,
    ValidationOutputContent,
    WorkflowCompletionPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    stable_record_id,
)
from artifact_replay import (
    ReplayedWorkflowCursor,
    ReplayedWorkUnitState,
    replay_artifacts,
)
from contracts import (
    AgentRole,
    ContractResult,
    FindingRecord,
    ValidationAttestation,
)
from finding_reducer import (
    project_finding_response_delta,
    project_reviewer_persistence_transitions,
)
from review_packets import ReviewPacket
from task_contract import TaskMode
from workflow import WorkflowExecutionError
from workflow_state import (
    AgentFailureKind,
    GateDecisionRecord,
    NATIVE_CLAUDE_REVIEW_TRANSPORT as NATIVE_REVIEW_TRANSPORT,
    NATIVE_CODEX_RESULT_TRANSPORT as NATIVE_IMPLEMENTER_TRANSPORT,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitRecord,
    project_implementer_return_policy,
)


class AppendWorkflowTransition(Protocol):
    def __call__(
        self, payload: WorkflowTransitionPayload, fingerprint: str
    ) -> None: ...


class GateTransitionPayloadFactory(Protocol):
    def __call__(self, unit: WorkUnitRecord) -> GateTransitionPayload: ...


class AppendGateDecisionBinding(Protocol):
    def __call__(
        self,
        work_unit_id: int | str,
        decision: GateDecisionRecord,
        gate_record: ArtifactRecord,
    ) -> ArtifactRecord: ...


class AppendWorkflowEvent(Protocol):
    def __call__(
        self,
        *,
        event_kind: str,
        work_unit_id: str | None,
        slice_id: str,
        round_number: int | None,
        domain_record: ArtifactRecord,
    ) -> ArtifactRecord: ...


class PersistedRequestPath(Protocol):
    def __call__(self, invocation: object) -> Path: ...


@dataclass(frozen=True)
class WorkflowPersistenceDependencies:
    """Driver-owned resources and composition edges required by the sinks."""

    artifact_bridge: Callable[[], ArtifactBridge | None]
    active_state: Callable[[], WorkflowState | None]
    artifact_fingerprint: Callable[[], str]
    append_workflow_transition: AppendWorkflowTransition
    gate_transition_payload: GateTransitionPayloadFactory
    append_gate_decision_binding: AppendGateDecisionBinding
    append_workflow_event: AppendWorkflowEvent
    native_agent_request_path: PersistedRequestPath
    native_agent_request_bundle_json: Callable[[object], str]
    materialize_review_packet: Callable[[ReviewPacket], Path]
    canonical_agent_result: Callable[
        [tuple[ArtifactRecord, ...], str], ArtifactRecord | None
    ]


class WorkflowPersistence:
    """Append durable workflow facts without owning driver state or imports."""

    def __init__(self, dependencies: WorkflowPersistenceDependencies) -> None:
        self._dependencies = dependencies

    @property
    def _artifact_bridge(self) -> ArtifactBridge | None:
        return self._dependencies.artifact_bridge()

    @property
    def active_state(self) -> WorkflowState | None:
        return self._dependencies.active_state()

    def _append_workflow_event(
        self,
        *,
        event_kind: str,
        work_unit_id: str | None,
        slice_id: str,
        round_number: int | None,
        domain_record: ArtifactRecord,
    ) -> ArtifactRecord:
        record = self._dependencies.append_workflow_event(
            event_kind=event_kind,
            work_unit_id=work_unit_id,
            slice_id=slice_id,
            round_number=round_number,
            domain_record=domain_record,
        )
        if not isinstance(record, ArtifactRecord):
            raise WorkflowExecutionError(
                "structured persistence omitted its required workflow event"
            )
        return record

    def _persist_workflow_snapshot(self, state: WorkflowState) -> None:
        """Append exactly the status and policy deltas needed by one checkpoint."""
        bridge = self._artifact_bridge
        if bridge is None or state.task_digest is None:
            return
        replay = replay_artifacts(bridge.store.current_chain(), state.run_id)
        recorded_slices = dict(replay.slice_statuses)
        recorded_units = {
            item.work_unit_id: item for item in replay.work_unit_states
        }
        expected_units = {
            str(item.work_unit_id): ReplayedWorkUnitState(
                str(item.work_unit_id),
                str(item.slice_id),
                item.status.value,
                item.current_step.value,
            )
            for item in state.work_units
        }
        changed_units = {
            key for key, value in expected_units.items()
            if recorded_units.get(key) != value
        }
        changed_unit_slices = {
            expected_units[key].slice_id for key in changed_units
        }

        for item in state.slices:
            slice_id = str(item.slice_id)
            if (
                recorded_slices.get(slice_id) != item.status.value
                and slice_id not in changed_unit_slices
            ):
                self._dependencies.append_workflow_transition(
                    WorkflowTransitionPayload(
                        slice_id, item.status.value, None, None, None
                    ),
                    state.task_digest,
                )
                recorded_slices[slice_id] = item.status.value

        current_unit_id = str(state.current_work_unit_id)
        for item in state.work_units:
            work_unit_id = str(item.work_unit_id)
            if work_unit_id not in changed_units or work_unit_id == current_unit_id:
                continue
            slice_status = next(
                candidate.status.value
                for candidate in state.slices
                if candidate.slice_id == item.slice_id
            )
            self._dependencies.append_workflow_transition(
                WorkflowTransitionPayload(
                    str(item.slice_id),
                    slice_status,
                    work_unit_id,
                    item.current_step.value,
                    item.status.value,
                ),
                state.task_digest,
            )

        current = state.current_work_unit
        expected_cursor = ReplayedWorkflowCursor(
            str(state.current_slice_id), current_unit_id, state.current_step.value
        )
        if (
            current_unit_id in changed_units
            or replay.workflow_cursor != expected_cursor
            or recorded_slices.get(str(state.current_slice_id))
            != state.current_slice.status.value
            or any(key != current_unit_id for key in changed_units)
        ):
            self._dependencies.append_workflow_transition(
                WorkflowTransitionPayload(
                    str(state.current_slice_id),
                    state.current_slice.status.value,
                    current_unit_id,
                    state.current_step.value,
                    current.status.value,
                ),
                state.task_digest,
            )

        recorded_policies = {
            item.work_unit_id: item for item in replay.workflow_policies
        }
        for item in state.work_units:
            work_unit_id = str(item.work_unit_id)
            policy = WorkflowPolicyPayload(
                work_unit_id,
                *project_implementer_return_policy(item),
            )
            if recorded_policies.get(work_unit_id) == policy:
                continue
            chain = bridge.store.current_chain()
            logical_id = f"workflow-policy-{work_unit_id}"
            revision = 1 + max(
                (
                    record.revision for record in chain
                    if record.record_type is RecordType.WORKFLOW_POLICY
                    and record.logical_id == logical_id
                ),
                default=0,
            )
            bridge.append(
                policy,
                logical_id=logical_id,
                idempotency_key=f"workflow-policy:{work_unit_id}:{revision}",
                fingerprint_sha256=state.task_digest,
                fingerprint_kind=FingerprintKind.CONTRACT,
            )

    def _persist_slice_boundaries(self, state: WorkflowState) -> None:
        """Append exact Slice Git/scope facts before any guarded side effect."""
        bridge = self._artifact_bridge
        if bridge is None or state.task_digest is None:
            return
        replay = replay_artifacts(bridge.store.current_chain(), state.run_id)
        recorded = {item.slice_id: item for item in replay.slice_boundaries}
        for item in state.slices:
            if item.start_commit is None or item.start_fingerprint is None:
                continue
            payload = SliceBoundaryPayload(
                str(item.slice_id),
                item.start_commit,
                item.scope_change_groups,
                item.start_fingerprint,
            )
            if recorded.get(payload.slice_id) == payload:
                continue
            logical_id = f"slice-boundary-{payload.slice_id}"
            chain = bridge.store.current_chain()
            revision = 1 + max(
                (
                    record.revision for record in chain
                    if record.record_type is RecordType.SLICE_BOUNDARY
                    and record.logical_id == logical_id
                ),
                default=0,
            )
            bridge.append(
                payload,
                logical_id=logical_id,
                idempotency_key=f"slice-boundary:{payload.slice_id}:{revision}",
                fingerprint_sha256=state.task_digest,
                fingerprint_kind=FingerprintKind.CONTRACT,
            )
            recorded[payload.slice_id] = payload

    @staticmethod
    def _matching_gate_record(
        chain: tuple[ArtifactRecord, ...] | list[ArtifactRecord],
        decision: GateDecisionRecord,
    ) -> ArtifactRecord | None:
        return next(
            (
                record
                for record in reversed(chain)
                if isinstance(record.payload, GatePayload)
                and record.payload.gate_kind
                == decision.reason.value.replace("_", "-")
                and record.payload.decision
                == ("approved" if decision.approved else "rejected")
                and record.payload.rationale == decision.rationale
                and record.fingerprint.sha256 == decision.fingerprint
            ),
            None,
        )

    def _persist_gate_snapshot(self, state: WorkflowState) -> None:
        """Append R5 gate facts before any state-based gate reader runs."""
        bridge = self._artifact_bridge
        if bridge is None or state.task_digest is None:
            return
        chain = bridge.store.current_chain()
        replay = replay_artifacts(chain, state.run_id)
        recorded = {
            payload.work_unit_id: payload for payload in replay.gate_transitions
        }
        transition_revisions = {
            record.logical_id: record.revision
            for record in chain
            if record.record_type is RecordType.GATE_TRANSITION
        }
        for unit in state.work_units:
            payload = self._dependencies.gate_transition_payload(unit)
            if recorded.get(payload.work_unit_id) == payload:
                continue
            logical_id = f"gate-transition-{payload.work_unit_id}"
            revision = transition_revisions.get(logical_id, 0) + 1
            transition_record = bridge.append(
                payload,
                logical_id=logical_id,
                idempotency_key=(
                    f"gate-transition:{payload.work_unit_id}:{revision}"
                ),
                fingerprint_sha256=state.task_digest,
                fingerprint_kind=FingerprintKind.CONTRACT,
            )
            chain = (*chain, transition_record)
            transition_revisions[logical_id] = revision
            recorded[payload.work_unit_id] = payload

        bound = {
            (item.work_unit_id, item.gate_record_id)
            for item in replay.gate_decisions
        }
        for unit in state.work_units:
            for decision in unit.gate_decisions:
                gate_record = self._matching_gate_record(chain, decision)
                if gate_record is None:
                    raise WorkflowExecutionError(
                        "gate decision projection has no authoritative GatePayload"
                    )
                binding = (str(unit.work_unit_id), gate_record.record_id)
                if binding in bound:
                    continue
                decision_record = self._dependencies.append_gate_decision_binding(
                    unit.work_unit_id, decision, gate_record
                )
                chain = (*chain, decision_record)
                bound.add(binding)

    def _persist_structured_tail(self, state: WorkflowState) -> None:
        bridge = self._artifact_bridge
        if bridge is None or state.task_digest is None:
            return
        contract_fingerprint = state.task_digest
        for persisted_unit in state.work_units:
            for failure in persisted_unit.invocation_failures:
                if (
                    failure.diff_fingerprint is None
                    or failure.resume_at_utc is None
                ):
                    continue
                if failure.automatic_resume and failure.failure_kind in {
                    AgentFailureKind.NETWORK,
                    AgentFailureKind.OUTPUT,
                }:
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
            chain = bridge.store.current_chain()
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

    def _persist_native_agent_request_bundle(self, invocation: object) -> None:
        bundle = invocation.native_request
        if bundle is None:
            raise WorkflowExecutionError("native agent invocation has no request bundle")
        path = self._dependencies.native_agent_request_path(invocation)
        content = self._dependencies.native_agent_request_bundle_json(bundle)
        if path.exists():
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                raise WorkflowExecutionError(
                    "native agent request differs from its persisted recovery artifact"
                )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8", newline="") as stream:
                stream.write(content)
        except FileExistsError:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                raise WorkflowExecutionError(
                    "native agent request differs from its persisted recovery artifact"
                )
        if path.read_text(encoding="utf-8") != content:
            raise WorkflowExecutionError(
                "native agent request recovery artifact verification failed"
            )

    def persist_review_packet(self, packet: ReviewPacket) -> None:
        """Bind locally generated review evidence before it enters projection."""
        bridge = self._artifact_bridge
        state = self.active_state
        if bridge is None or state is None:
            return
        coverage_digest = packet.manifest.diff_coverage_digest
        if coverage_digest is None:
            raise WorkflowExecutionError(
                "structured-v2 review packet lacks its diff-coverage digest"
            )
        blob = bridge.store.put_blob(packet.canonical_bytes)
        bridge.append(
            ReviewPacketPayload(
                work_unit_id=str(state.current_work_unit_id),
                fingerprint=packet.fingerprint,
                purpose=packet.purpose,
                manifest=packet.manifest.paths,
                diff_coverage_sha256=coverage_digest,
                content_bytes=len(packet.canonical_bytes),
                blob=blob,
            ),
            logical_id=(
                f"review-packet-{state.current_work_unit_id}-"
                f"{packet.fingerprint[:12]}"
            ),
            idempotency_key=f"review-packet:{state.current_work_unit_id}:{packet.digest}",
            fingerprint_sha256=packet.fingerprint,
        )
        self._dependencies.materialize_review_packet(packet)

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
        bridge = self._artifact_bridge
        if bridge is None:
            raise WorkflowExecutionError("provider content has no artifact authority")
        content = canonical.encode("utf-8")
        blob = bridge.store.put_blob(content)
        payload = ProviderContentPayload(
            role=role,
            work_unit_id=str(work_unit_id),
            round_number=round_number,
            operation=operation,
            request_id=request_id,
            response_sha256=blob.sha256,
            content_kind=content_kind,
            content_bytes=blob.bytes,
            blob=blob,
        )
        return bridge.append(
            payload,
            logical_id=(
                f"provider-content-{role.value}-{work_unit_id}-"
                f"{request_id.rsplit('-', 1)[-1][:12]}"
            ),
            idempotency_key=(
                f"provider-content:{role.value}:{work_unit_id}:"
                f"{round_number}:{operation}:{request_id}:{blob.sha256}"
            ),
            fingerprint_sha256=fingerprint,
            fingerprint_kind=fingerprint_kind,
        )

    def persist_native_implementer_contract(
        self,
        output: NativeAgentImplementerOutput,
        previous_findings: tuple[FindingRecord, ...],
        *,
        recovery_fingerprint: str | None = None,
    ) -> None:
        bridge = self._artifact_bridge
        state = self.active_state
        if bridge is None or state is None:
            return
        if (
            state.protocol_binding is None
            or state.protocol_binding.codex_result_transport
            != NATIVE_IMPLEMENTER_TRANSPORT
        ):
            raise WorkflowExecutionError(
                "native Codex persistence lacks its immutable transport binding"
            )
        unit = state.current_work_unit
        logical = f"agent-{unit.work_unit_id}-{state.current_step.value}-{unit.round_number}"
        payload = agent_result_payload(
            output.result,
            role=AgentRole.CODEX,
            work_unit_id=unit.work_unit_id,
            transport_schema=NATIVE_IMPLEMENTER_TRANSPORT,
            request_id=output.request_id,
            response_sha256=output.response_sha256,
        )
        chain = bridge.store.current_chain()
        canonical = self._dependencies.canonical_agent_result(
            tuple(
                record
                for record in chain
                if isinstance(record.payload, AgentResultPayload)
                and record.logical_id == logical
            ),
            logical,
        )
        if canonical is not None:
            if canonical.payload != payload:
                raise WorkflowExecutionError(
                    "native agent result logical binding differs"
                )
            fingerprint = canonical.fingerprint.sha256
            idempotency_key = canonical.idempotency_key
        else:
            fingerprint = (
                recovery_fingerprint
                or self._dependencies.artifact_fingerprint()
            )
            binding_digest = hashlib.sha256(
                (
                    f"{fingerprint}:{output.request_id}:{output.response_sha256}"
                ).encode("utf-8")
            ).hexdigest()
            idempotency_key = f"native:{logical}:{binding_digest}"
        fingerprint_kind = (
            FingerprintKind.CONTRACT
            if unit.kind is WorkUnitKind.PLAN
            else FingerprintKind.IMPLEMENTATION
        )
        content_record = self._persist_provider_content(
            role=Role.CODEX,
            work_unit_id=unit.work_unit_id,
            round_number=unit.round_number,
            operation=state.current_step.value,
            request_id=output.request_id,
            canonical=output.canonical_json,
            content_kind=(
                "final_report"
                if state.current_step is WorkflowStep.CODEX_FINAL_REVIEW
                and output.result.ready is True
                else "agent_result"
            ),
            fingerprint=fingerprint,
            fingerprint_kind=fingerprint_kind,
        )
        assert isinstance(content_record.payload, ProviderContentPayload)
        if content_record.payload.response_sha256 != output.response_sha256:
            raise WorkflowExecutionError(
                "native agent content digest differs from its result binding"
            )
        bridge.append(
            payload,
            logical_id=logical,
            idempotency_key=idempotency_key,
            fingerprint_sha256=fingerprint,
            fingerprint_kind=fingerprint_kind,
        )
        try:
            response_delta = project_finding_response_delta(
                previous_findings, output.result.findings
            )
        except ValueError as exc:
            raise WorkflowExecutionError(
                f"native implementer finding response delta is invalid: {exc}"
            ) from exc
        for item in response_delta:
            finding = item.finding
            response = item.response
            bridge.append(
                finding_payload(
                    finding,
                    actor=AgentRole.CODEX,
                    action="responded",
                    rationale=response.rationale,
                    work_unit_id=unit.work_unit_id,
                    response_decision=response.decision,
                ),
                logical_id=f"finding-{finding.finding_id}",
                idempotency_key=(
                    f"finding-response:{finding.finding_id}:"
                    f"{item.response_index}"
                ),
                fingerprint_sha256=fingerprint,
            )

    def persist_native_review_contract(
        self,
        output: NativeAgentReviewOutput,
        fingerprint: str,
        round_number: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        bridge = self._artifact_bridge
        state = self.active_state
        if bridge is None or state is None:
            return
        if (
            state.protocol_binding is None
            or state.protocol_binding.claude_review_transport
            != NATIVE_REVIEW_TRANSPORT
            or output.result.reviewer is not AgentRole.CLAUDE
        ):
            raise WorkflowExecutionError(
                "native review persistence lacks its immutable Claude binding"
            )
        unit = state.current_work_unit
        native_context = output.context
        if (
            native_context is None
            or native_context.work_unit_id != str(unit.work_unit_id)
            or native_context.diff_fingerprint != fingerprint
            or native_context.round_number != round_number
            or native_context.reviewer is not output.result.reviewer
            or native_context.validation_attestation != output.result.validation
            or (
                not output.result.stopped
                and native_context.test_files != output.result.test_files
            )
            or output.result.red_state_followup_slice
            != (
                native_context.red_state_followup_slice
                if output.result.approval is True
                else None
            )
        ):
            raise WorkflowExecutionError(
                "native review persistence differs from its exact review context"
            )
        if output.result.validation is None:
            raise WorkflowExecutionError(
                "native review persistence lacks its validation attestation"
            )
        attestation_records = tuple(
            record
            for record in bridge.store.current_chain()
            if isinstance(record.payload, ValidationAttestationPayload)
            and record.logical_id == output.result.validation.attestation_id
            and record.fingerprint.sha256 == fingerprint
        )
        if len(attestation_records) != 1:
            raise WorkflowExecutionError(
                "native review persistence has no unique earlier validation record"
            )
        attestation_record = attestation_records[0]
        logical = f"review-claude-{unit.work_unit_id}-{round_number}"
        response_sha256 = hashlib.sha256(
            output.canonical_json.encode("utf-8")
        ).hexdigest()
        binding_digest = hashlib.sha256(
            (
                f"{fingerprint}:{output.request_id}:{response_sha256}"
            ).encode("utf-8")
        ).hexdigest()
        content_record = self._persist_provider_content(
            role=Role(output.result.reviewer.value),
            work_unit_id=unit.work_unit_id,
            round_number=round_number,
            operation=state.current_step.value,
            request_id=output.request_id,
            canonical=output.canonical_json,
            content_kind="review_result",
            fingerprint=fingerprint,
        )
        assert isinstance(content_record.payload, ProviderContentPayload)
        if content_record.payload.response_sha256 != response_sha256:
            raise WorkflowExecutionError(
                "native reviewer content digest differs from its review binding"
            )
        review_record = bridge.append(
            review_payload(
                output.result,
                work_unit_id=unit.work_unit_id,
                transport_schema=NATIVE_REVIEW_TRANSPORT,
                request_id=output.request_id,
                response_sha256=response_sha256,
            ),
            logical_id=logical,
            idempotency_key=f"native:{logical}:{binding_digest}",
            fingerprint_sha256=fingerprint,
        )
        review_binding_digest = hashlib.sha256(
            review_record.record_id.encode("utf-8")
        ).hexdigest()
        bridge.append(
            ReviewAnchorPayload(
                review_record_id=review_record.record_id,
                anchors=tuple(
                    ReviewAnchor(
                        anchor.anchor_id,
                        anchor.origin,
                        anchor.input_fixture,
                        anchor.expected,
                        anchor.tolerance,
                    )
                    for anchor in output.result.anchors
                ),
            ),
            logical_id=f"review-anchors-{review_binding_digest[:16]}",
            idempotency_key=f"review-anchors:{review_binding_digest}",
            fingerprint_sha256=fingerprint,
        )
        bridge.append(
            ReviewValidationBindingPayload(
                review_record_id=review_record.record_id,
                attestation_record_id=attestation_record.record_id,
            ),
            logical_id=f"review-validation-{review_binding_digest[:16]}",
            idempotency_key=f"review-validation:{review_binding_digest}",
            fingerprint_sha256=fingerprint,
        )
        self._persist_review_finding_transitions(
            output.result,
            fingerprint=fingerprint,
            round_number=round_number,
            previous_findings=previous_findings,
            structured=True,
        )
        self._append_workflow_event(
            event_kind="review",
            work_unit_id=str(unit.work_unit_id),
            slice_id=str(unit.slice_id),
            round_number=round_number,
            domain_record=review_record,
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
        bridge = self._artifact_bridge
        if bridge is None:
            return
        work_unit_id: str | None = None
        if structured:
            if self.active_state is None:
                raise WorkflowExecutionError(
                    "structured finding persistence lacks an active work unit"
                )
            work_unit_id = str(self.active_state.current_work_unit_id)
        transitions = project_reviewer_persistence_transitions(
            previous_findings,
            result.findings,
            work_unit_id=work_unit_id,
        )
        for transition in transitions:
            finding = transition.finding
            action = transition.action
            rationale = transition.rationale
            transition_identity = transition.identity
            payload = finding_payload(
                finding,
                action=action,
                rationale=rationale,
                work_unit_id=work_unit_id,
            )
            logical_id = f"finding-{finding.finding_id}"
            legacy_key = (
                f"finding:{finding.finding_id}:{transition_identity}:"
                f"{round_number}:{result.reviewer.value}"
            )
            idempotency_key = legacy_key
            if structured and not transition_identity.startswith(
                "status_rationale:"
            ):
                assert work_unit_id is not None
                idempotency_key = (
                    f"finding:{finding.finding_id}:{transition_identity}:"
                    f"work_unit:{work_unit_id}:{round_number}:"
                    f"{result.reviewer.value}"
                )
                legacy_record = next(
                    (
                        record
                        for record in bridge.store.current_chain()
                        if record.idempotency_key == legacy_key
                    ),
                    None,
                )
                if (
                    legacy_record is not None
                    and getattr(legacy_record.payload, "work_unit_id", None)
                    == work_unit_id
                ):
                    bridge.append(
                        payload,
                        logical_id=logical_id,
                        idempotency_key=legacy_key,
                        fingerprint_sha256=fingerprint,
                    )
                    continue
            bridge.append(
                payload,
                logical_id=logical_id,
                idempotency_key=idempotency_key,
                fingerprint_sha256=fingerprint,
            )

    def persist_contract_diagnostic(
        self, role: AgentRole, output: str, reason: str, attempt: int
    ) -> None:
        bridge = self._artifact_bridge
        state = self.active_state
        if bridge is None or state is None:
            return
        bridge.diagnostic(
            role=role,
            work_unit_id=state.current_work_unit_id,
            attempt=attempt,
            output=output,
            reason=reason,
            fingerprint_sha256=self._dependencies.artifact_fingerprint(),
        )

    def persist_validation_attestation(
        self, attestation: ValidationAttestation
    ) -> None:
        bridge = self._artifact_bridge
        if bridge is None:
            return
        if any(not spec.argv for spec in attestation.command_specs):
            raise WorkflowExecutionError(
                "structured-v2 validation accepts only matrix-provided argv commands"
            )
        if (
            not attestation.content_captures
            or tuple(item.command for item in attestation.content_captures)
            != attestation.expected_commands
        ):
            raise WorkflowExecutionError(
                "structured-v2 validation requires exact content for every command"
            )
        store = bridge.store
        result_key = f"attestation:{attestation.attestation_id}"
        result_context = store.append_context(
            record_type=RecordType.VALIDATION_ATTESTATION,
            logical_id=attestation.attestation_id,
            idempotency_key=result_key,
        )
        result_record_id = (
            result_context.existing.record_id
            if result_context.existing is not None
            else stable_record_id(
                store.run_id,
                RecordType.VALIDATION_ATTESTATION,
                attestation.attestation_id,
                result_context.next_revision,
            )
        )
        outputs: list[ValidationOutputContent] = []
        for spec, capture in zip(
            attestation.command_specs,
            attestation.content_captures,
            strict=True,
        ):
            stdout = capture.stdout.encode("utf-8")
            stderr = capture.stderr.encode("utf-8")
            compact = capture.compact_output.encode("utf-8")
            outputs.append(
                ValidationOutputContent(
                    command=command_payload(spec),
                    digest_outcome=capture.outcome,
                    exit_code=capture.exit_code,
                    raw_stdout=store.put_blob(stdout),
                    raw_stderr=store.put_blob(stderr),
                    compact_output=store.put_blob(compact),
                    output_bytes=len(stdout) + len(stderr),
                )
            )
        content_record = bridge.append(
            ValidationContentPayload(
                attestation_id=attestation.attestation_id,
                result_record_id=result_record_id,
                digest_format=attestation.content_digest_format,
                output_digest=attestation.output_digest,
                summary=attestation.summary,
                outputs=tuple(outputs),
            ),
            logical_id=f"validation-content-{attestation.attestation_id}",
            idempotency_key=f"validation-content:{attestation.attestation_id}",
            fingerprint_sha256=attestation.diff_fingerprint,
        )
        result_record = bridge.append(
            attestation_payload(attestation, content_record.record_id),
            logical_id=attestation.attestation_id,
            idempotency_key=result_key,
            fingerprint_sha256=attestation.diff_fingerprint,
        )
        if result_record.record_id != result_record_id:
            raise WorkflowExecutionError(
                "validation content forward binding is not stable"
            )
        state = self.active_state
        if state is None:
            raise WorkflowExecutionError(
                "validation event persistence lacks an active workflow state"
            )
        unit = state.current_work_unit
        self._append_workflow_event(
            event_kind="validation",
            work_unit_id=str(unit.work_unit_id),
            slice_id=str(unit.slice_id),
            round_number=unit.round_number,
            domain_record=result_record,
        )

    def persist_validation_request(self, request: object) -> None:
        bridge = self._artifact_bridge
        if bridge is None:
            return
        commands = request.commands
        if any(not command.argv for command in commands):
            raise WorkflowExecutionError(
                "structured-v2 validation accepts only matrix-provided argv commands"
            )
        bridge.append(
            validation_request_payload(request),
            logical_id=f"validation-request-{request.diff_fingerprint[:12]}",
            idempotency_key=(
                f"validation-request:{request.diff_fingerprint}:"
                f"{request.attempt_number}"
            ),
            fingerprint_sha256=request.diff_fingerprint,
        )

    def persist_invocation_failure(
        self, payload: InvocationFailurePayload
    ) -> None:
        """Append the classified failure before its state retry decision."""
        bridge = self._artifact_bridge
        state = self.active_state
        if bridge is None or state is None:
            raise WorkflowExecutionError(
                "structured invocation failure has no active artifact authority"
            )
        if payload.work_unit_id != str(state.current_work_unit_id):
            raise WorkflowExecutionError(
                "invocation failure work unit differs from the active workflow"
            )
        fingerprint = payload.diff_fingerprint or state.task_digest
        if fingerprint is None:
            raise WorkflowExecutionError(
                "invocation failure requires a contract or implementation fingerprint"
            )
        bridge.append(
            payload,
            logical_id=f"invocation-failure-{payload.invocation_id}",
            idempotency_key=f"invocation-failure:{payload.invocation_id}",
            fingerprint_sha256=fingerprint,
            fingerprint_kind=(
                FingerprintKind.IMPLEMENTATION
                if payload.diff_fingerprint is not None
                else FingerprintKind.CONTRACT
            ),
        )

    def persist_gate_decision(
        self, work_unit_id: int, decision: GateDecisionRecord
    ) -> None:
        bridge = self._artifact_bridge
        if bridge is None:
            return
        logical = f"gate-{decision.reason.value}-{decision.fingerprint[:12]}"
        gate_record = bridge.append(
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
        self._dependencies.append_gate_decision_binding(
            work_unit_id, decision, gate_record
        )

    def persist_gate_transition(self, state: WorkflowState) -> None:
        self._persist_gate_snapshot(state)

    def persist_implementation_handoff(
        self, handoff_path: Path, approved_plan_commit: str
    ) -> None:
        """Bind an idempotent IMPLEMENT handoff to its reviewed plan commit."""
        bridge = self._artifact_bridge
        if bridge is None:
            return
        chain = bridge.store.current_chain()
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
        bridge.append(
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
