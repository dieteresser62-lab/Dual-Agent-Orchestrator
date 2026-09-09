"""Crash-safe workflow, side-effect, and record-ahead recovery.

The production driver owns mutable runtime resources.  This module owns the
recovery decisions and receives every driver-owned edge explicitly, keeping
the import direction one-way: the driver imports recovery, never conversely.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol

from agent_runtime import (
    NativeAgentCodexOutput as NativeAgentImplementerOutput,
    NativeAgentReviewOutput,
    ProviderRequestRoundRequired,
)
from artifact_bridge import (
    ArtifactBridge,
    agent_result_payload,
    logical_provider_operation_id,
    review_payload_matches_result,
)
from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    ProviderAttemptPayload,
    ProviderContentPayload,
    ProviderInputMeasurementPayload,
    ReviewPayload,
    Role,
    ValidationAttestationPayload,
)
from artifact_replay import ArtifactReplayError, ArtifactReplayResult, replay_artifacts
from contracts import (
    AgentRole,
    ApprovalMarker,
    CodexStepContract as ImplementerStepContract,
    ContractResult,
    FindingRecord,
    StepContract,
    ValidationAttestation,
)
from final_review_preflight import FINAL_REVIEW_OPERATIONS
from finding_reducer import reduce_findings
from gates import matches_path_patterns
from git_service import inspect_commit_tree, inspect_repository
from native_codex_contract import (
    canonical_native_codex_json as canonical_native_implementer_json,
    parse_bound_native_codex_contract_result as parse_bound_native_implementer_result,
)
from native_codex_request import (
    NativeCodexRequestBundle as NativeImplementerRequestBundle,
    validate_native_codex_provider_response as validate_native_implementer_response,
)
from native_review_contract import (
    BoundNativeReviewContext,
    NativeReviewContext,
    NativeReviewContractError,
    parse_bound_native_contract_result,
)
from native_review_request import (
    validate_native_review_provider_response,
    validate_native_review_provider_response_for_context,
)
from provider_input_budget import ProviderInputMeasurement
from side_effects import (
    Reconciliation,
    ReconciliationOutcome,
    SideEffectExecutor,
    SideEffectReconciliationError,
    SideEffectSpec,
    decode_file_write_content,
    file_state_digest,
    reconcile_file_write,
    reconcile_git_commit,
    reconcile_queue_move,
)
from state_io import atomic_write_file
from workflow import (
    CodexInvocation as ImplementerInvocation,
    PersistedNativeReviewerReplay,
    ReviewerInvocation,
    WorkflowContext,
    WorkflowExecutionError,
    WorkflowHistory,
)
from workflow_state import (
    NATIVE_CLAUDE_REVIEW_TRANSPORT as NATIVE_REVIEW_TRANSPORT,
    NATIVE_CODEX_RESULT_TRANSPORT as NATIVE_IMPLEMENTER_TRANSPORT,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitRecord,
)


logger = logging.getLogger(__name__)


def _require_provider_start_binding(
    replay: ArtifactReplayResult,
    measurement: ProviderInputMeasurement,
    work_unit_id: str,
    instance: str,
) -> None:
    related_starts = tuple(
        item
        for item in replay.side_effects
        if item.effect_class == "provider_start"
        and item.work_unit_id == work_unit_id
        and len(item.operation) == 7
        and item.operation[0] == measurement.provider
        and item.operation[1] == measurement.operation
        and item.operation[4] == instance
    )
    foreign_binding = next(
        (
            item.operation[3]
            for item in related_starts
            if item.operation[3] != measurement.binding_fingerprint
        ),
        None,
    )
    if foreign_binding is not None:
        raise WorkflowExecutionError(
            "provider attempt immutable binding differs from its first attempt: "
            "field=binding_fingerprint "
            f"first={foreign_binding[:12]} "
            f"current={measurement.binding_fingerprint[:12]}"
        )


def _require_provider_input_round(
    prior_records: tuple[ArtifactRecord, ...],
    measurement: ProviderInputMeasurement,
) -> None:
    prior_input_digest = next(
        (
            record.payload.input_digest
            for record in reversed(prior_records)
            if isinstance(record.payload, ProviderAttemptPayload)
        ),
        None,
    )
    if (
        prior_input_digest is not None
        and prior_input_digest != measurement.input_digest
    ):
        raise ProviderRequestRoundRequired(
            binding_fingerprint=measurement.binding_fingerprint,
            previous_input_digest=prior_input_digest,
            current_input_digest=measurement.input_digest,
        )


class SideEffectSpecFactory(Protocol):
    def __call__(
        self,
        effect_class: str,
        operation: tuple[str, ...],
        *,
        fingerprint: str | None = None,
    ) -> SideEffectSpec: ...


class ContentTextReader(Protocol):
    def __call__(
        self,
        *,
        role: Role,
        work_unit_id: int,
        round_number: int,
        operation: str,
        request_id: str | None = None,
        response_sha256: str | None = None,
        fingerprint: str | None = None,
        chain: tuple[ArtifactRecord, ...] | None = None,
    ) -> tuple[str, ArtifactRecord] | None: ...


class PersistImplementerContract(Protocol):
    def __call__(
        self,
        output: NativeAgentImplementerOutput,
        previous_findings: tuple[FindingRecord, ...],
        *,
        recovery_fingerprint: str | None = None,
    ) -> None: ...


class PersistReviewContract(Protocol):
    def __call__(
        self,
        output: NativeAgentReviewOutput,
        fingerprint: str,
        round_number: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None: ...


@dataclass(frozen=True)
class WorkflowRecoveryDependencies:
    """Driver-owned resources and persistence edges required by recovery."""

    root: Path
    artifact_bridge: Callable[[], ArtifactBridge | None]
    active_state: Callable[[], WorkflowState | None]
    reconcile_external_attempt: Callable[[SideEffectSpec, Path], Reconciliation]
    side_effect_executor: Callable[[ArtifactBridge], SideEffectExecutor]
    side_effect_spec: SideEffectSpecFactory
    mark_side_effect_completed: Callable[[str], None]
    attempt_response_path: Callable[[Path, int], Path]
    canonical_agent_result: Callable[
        [tuple[ArtifactRecord, ...], str], ArtifactRecord | None
    ]
    load_agent_request_bundle: Callable[
        [ImplementerInvocation, NativeImplementerRequestBundle],
        NativeImplementerRequestBundle | None,
    ]
    content_text: ContentTextReader
    persist_implementer_contract: PersistImplementerContract
    persist_review_contract: PersistReviewContract
    store_implementer_output: Callable[[str], None]
    agent_profile: Callable[[str], tuple[str, str]]


class WorkflowRecovery:
    """Resolve durable crash windows without owning driver state or imports."""

    def __init__(self, dependencies: WorkflowRecoveryDependencies) -> None:
        self._dependencies = dependencies

    def _reconcile_pending_side_effects(
        self,
        state: WorkflowState,
        replay=None,
    ) -> bool:
        """Resolve every crash-window intent before another workflow decision."""
        bridge = self._dependencies.artifact_bridge()
        if bridge is None:
            return False
        if replay is None:
            replay = replay_artifacts(bridge.store.current_chain(), state.run_id)
        records = {record.record_id: record for record in replay.records}
        changed = False

        def complete(item, result: str) -> None:
            nonlocal changed
            intent = records[item.intent_record_id]
            bridge.record_side_effect_result(
                effect_class=item.effect_class,
                work_unit_id=item.work_unit_id,
                operation=item.operation,
                result=result,
                fingerprint_sha256=intent.fingerprint.sha256,
                fingerprint_kind=intent.fingerprint.kind,
            )
            changed = True

        for item in replay.side_effects:
            if item.result is not None:
                continue
            if item.effect_class == "internal":
                complete(item, "completed")
                continue
            if item.effect_class == "ledger":
                complete(item, "initialized")
                continue
            if item.effect_class == "file_write":
                target = item.operation[0]
                path = (
                    Path(target.removeprefix("external:"))
                    if target.startswith("external:")
                    else self._dependencies.root.joinpath(*PurePosixPath(target).parts)
                )
                prior_sha256 = (
                    item.operation[2] if len(item.operation) == 4 else None
                )
                outcome = reconcile_file_write(
                    path, item.operation[1], prior_sha256
                )
                if outcome.outcome is ReconciliationOutcome.OCCURRED:
                    assert outcome.result is not None
                    complete(item, outcome.result)
                    continue
                if outcome.outcome is ReconciliationOutcome.NOT_OCCURRED:
                    if len(item.operation) == 4:
                        content = decode_file_write_content(
                            item.operation[3], item.operation[1]
                        )
                        try:
                            rendered = content.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise SideEffectReconciliationError(
                                "projection intent contains non-UTF-8 content"
                            ) from exc
                        atomic_write_file(path, rendered)
                        actual = file_state_digest(path)
                        if actual != item.operation[1]:
                            raise SideEffectReconciliationError(
                                f"pending projection {item.effect_key!r} was not written identically"
                            )
                        complete(item, actual)
                        continue
                    # The owning writer will re-enter through SideEffectExecutor
                    # and perform the proven-absent write under this same intent.
                    continue
                raise SideEffectReconciliationError(
                    f"pending file write {item.effect_key!r} has no durable identical target"
                )
            if item.effect_class == "git_commit":
                identity = inspect_repository(self._dependencies.root)
                if identity.head == item.operation[2]:
                    outcome = reconcile_git_commit(
                        prior_head=item.operation[2],
                        current_head=identity.head,
                        current_parent=None,
                        expected_tree=item.operation[3],
                        current_tree=None,
                    )
                else:
                    parent, tree = inspect_commit_tree(self._dependencies.root, identity.head)
                    outcome = reconcile_git_commit(
                        prior_head=item.operation[2],
                        current_head=identity.head,
                        current_parent=parent,
                        expected_tree=item.operation[3],
                        current_tree=tree,
                    )
                if outcome.outcome is ReconciliationOutcome.OCCURRED:
                    assert outcome.result is not None
                    complete(item, outcome.result)
                    continue
                if outcome.outcome is ReconciliationOutcome.NOT_OCCURRED:
                    is_current_slice_commit = (
                        item.work_unit_id == str(state.current_work_unit_id)
                        and item.operation[0] == "slice_commit"
                        and state.current_step is WorkflowStep.SLICE_COMMIT
                    )
                    is_current_audit_commit = (
                        item.work_unit_id == str(state.current_work_unit_id)
                        and item.operation[0] == "audit_commit"
                        and (
                            state.current_step is WorkflowStep.COMPLETED
                            or state.current_step.value in FINAL_REVIEW_OPERATIONS
                        )
                    )
                    if is_current_slice_commit or is_current_audit_commit:
                        continue
                raise SideEffectReconciliationError(
                    f"pending Git effect {item.effect_key!r} cannot be reconciled"
                )
            if item.effect_class == "provider_start":
                path = self._dependencies.root.joinpath(*PurePosixPath(item.operation[6]).parts)
                outcome = self._dependencies.reconcile_external_attempt(
                    SideEffectSpec(
                        item.effect_class,
                        item.work_unit_id,
                        item.operation,
                        records[item.intent_record_id].fingerprint.sha256,
                        records[item.intent_record_id].fingerprint.kind,
                    ),
                    path,
                )
                if outcome.outcome is ReconciliationOutcome.OCCURRED:
                    assert outcome.result is not None
                    complete(item, outcome.result)
                    continue
                if (
                    outcome.outcome is ReconciliationOutcome.NOT_OCCURRED
                    and item.work_unit_id == str(state.current_work_unit_id)
                    and item.operation[1] == state.current_step.value
                ):
                    continue
                raise SideEffectReconciliationError(
                    f"pending provider effect {item.effect_key!r} has an unknown outcome"
                )
            if item.effect_class == "queue_move":
                outcome = reconcile_queue_move(
                    Path(item.operation[0]),
                    Path(item.operation[1]),
                    item.operation[2],
                )
                if outcome.outcome is ReconciliationOutcome.OCCURRED:
                    assert outcome.result is not None
                    complete(item, outcome.result)
                    continue
                if outcome.outcome is ReconciliationOutcome.NOT_OCCURRED:
                    # Queue finalization owns the later move and reuses this intent.
                    continue
                raise SideEffectReconciliationError(
                    f"pending queue effect {item.effect_key!r} has an unknown outcome"
                )
            raise SideEffectReconciliationError(
                f"pending {item.effect_class} effect {item.effect_key!r} has no runtime reconciler"
            )
        return changed

    def _start_provider_attempt(
        self,
        measurement: ProviderInputMeasurement,
        bootstrap: object | None,
        *,
        operation_instance: str | None = None,
        durable_response_path: Path,
    ) -> tuple[ArtifactRecord, SideEffectSpec, Path]:
        bridge = self._dependencies.artifact_bridge()
        state = self._dependencies.active_state()
        if (
            bridge is None or state is None or not isinstance(bootstrap, ArtifactRecord)
            or not isinstance(bootstrap.payload, ProviderInputMeasurementPayload)
        ):
            raise WorkflowExecutionError("provider attempt start requires its durable measurement")
        if not re.fullmatch(r"[0-9a-f]{64}", measurement.binding_fingerprint):
            raise WorkflowExecutionError("provider attempt has no bound fingerprint")
        if (
            bootstrap.payload.input_digest != measurement.input_digest
            or bootstrap.payload.provider.value != measurement.provider
            or bootstrap.payload.operation != measurement.operation
        ):
            raise WorkflowExecutionError("provider attempt measurement context diverged")
        try:
            durable_response_path.resolve().relative_to(self._dependencies.root)
        except ValueError as exc:
            raise WorkflowExecutionError("provider response target is outside the repository") from exc
        replay = replay_artifacts(bridge.store.current_chain(), state.run_id)
        instance = operation_instance or "default"
        operation_prefix = (
            measurement.provider,
            measurement.operation,
            measurement.input_digest,
            measurement.binding_fingerprint,
            instance,
        )
        pending = tuple(
            item
            for item in replay.side_effects
            if item.effect_class == "provider_start"
            and item.work_unit_id == str(state.current_work_unit_id)
            and item.operation[:5] == operation_prefix
            and item.result is None
        )
        if len(pending) > 1:
            raise WorkflowExecutionError("provider start has multiple pending ledger intents")
        if pending:
            operation = pending[0].operation
            response_path = self._dependencies.root.joinpath(
                *PurePosixPath(operation[6]).parts
            )
        else:
            _require_provider_start_binding(
                replay,
                measurement,
                str(state.current_work_unit_id),
                instance,
            )
            logical_operation_id = logical_provider_operation_id(
                run_id=state.run_id,
                work_unit_id=str(state.current_work_unit_id),
                provider=bootstrap.payload.provider,
                operation=measurement.operation,
                binding_fingerprint=measurement.binding_fingerprint,
                operation_instance=operation_instance,
            )
            prior_records = tuple(
                record
                for record in replay.records
                if isinstance(record.payload, ProviderAttemptPayload)
                and record.payload.logical_operation_id == logical_operation_id
            )
            if operation_instance is not None and not prior_records:
                legacy_operation_id = logical_provider_operation_id(
                    run_id=state.run_id,
                    work_unit_id=str(state.current_work_unit_id),
                    provider=bootstrap.payload.provider,
                    operation=measurement.operation,
                    binding_fingerprint=measurement.binding_fingerprint,
                )
                legacy_records = tuple(
                    record
                    for record in replay.records
                    if isinstance(record.payload, ProviderAttemptPayload)
                    and record.payload.logical_operation_id == legacy_operation_id
                )
                if legacy_records and all(
                    record.payload.provider == bootstrap.payload.provider
                    and record.payload.role == bootstrap.payload.role
                    and record.payload.operation == measurement.operation
                    and record.payload.work_unit_id
                    == str(state.current_work_unit_id)
                    and record.payload.binding_fingerprint
                    == measurement.binding_fingerprint
                    and record.payload.input_digest == measurement.input_digest
                    for record in legacy_records
                ):
                    prior_records = legacy_records
            _require_provider_input_round(prior_records, measurement)
            attempt_number = max(
                (
                    record.payload.attempt_number
                    for record in prior_records
                ),
                default=0,
            ) + 1
            response_path = self._dependencies.attempt_response_path(
                durable_response_path, attempt_number
            )
            response_target = response_path.resolve().relative_to(
                self._dependencies.root
            ).as_posix()
            operation = (
                *operation_prefix,
                str(attempt_number),
                response_target,
            )
        spec = self._dependencies.side_effect_spec(
            "provider_start", operation,
            fingerprint=measurement.binding_fingerprint,
        )
        may_start = self._dependencies.side_effect_executor(bridge).begin(
            spec,
            reconcile=lambda: self._dependencies.reconcile_external_attempt(
                spec, response_path
            ),
        )
        if not may_start:
            self._dependencies.mark_side_effect_completed(spec.effect_key)
            raise WorkflowExecutionError(
                "provider operation already occurred; recover its durable response instead of starting again"
            )
        model, effort = self._dependencies.agent_profile(measurement.provider)
        started = bridge.start_provider_attempt(
            measurement_record=bootstrap,
            binding_fingerprint=measurement.binding_fingerprint,
            work_unit_id=state.current_work_unit_id,
            operation_instance=operation_instance,
            model=model,
            effort=effort,
        )
        return started, spec, response_path

    def _bind_native_implementer_request(
        self,
        chain: tuple[ArtifactRecord, ...],
        candidate: ArtifactRecord | None,
        invocation: ImplementerInvocation,
        bundle: NativeImplementerRequestBundle,
    ) -> tuple[
        NativeImplementerRequestBundle | None,
        Any,
        bool,
        ArtifactRecord | None,
    ]:
        recovery_bundle = self._dependencies.load_agent_request_bundle(invocation, bundle)
        recovery_bound = (
            recovery_bundle.bound_context
            if recovery_bundle is not None
            else bundle.bound_context
        )
        validate_against_bundle = recovery_bundle is not None or candidate is None
        original_attempt_record = None
        if candidate is not None and recovery_bound.context.previous_findings:
            candidate_index = chain.index(candidate)
            prior_attempts = tuple(
                item
                for item in chain[:candidate_index]
                if isinstance(item.payload, ProviderAttemptPayload)
                and item.payload.provider is candidate.payload.role
                and item.payload.work_unit_id == str(invocation.work_unit_id)
                and item.payload.operation == invocation.step.value
                and item.payload.phase == "started"
            )
            if not prior_attempts:
                raise WorkflowExecutionError(
                    "native agent recovery has no durable original request binding"
                )
            original_attempt_record = prior_attempts[-1]
        if (
            recovery_bundle is None
            and candidate is not None
            and candidate.payload.request_id != bundle.bound_context.request_id
        ):
            if original_attempt_record is None:
                candidate_index = chain.index(candidate)
                prior_attempts = tuple(
                    item
                    for item in chain[:candidate_index]
                    if isinstance(item.payload, ProviderAttemptPayload)
                    and item.payload.provider is candidate.payload.role
                    and item.payload.work_unit_id == str(invocation.work_unit_id)
                    and item.payload.operation == invocation.step.value
                    and item.payload.phase == "started"
                )
                if not prior_attempts:
                    raise WorkflowExecutionError(
                        "native agent recovery has no durable original request binding"
                    )
                original_attempt_record = prior_attempts[-1]
            original_request_id = candidate.payload.request_id
            recovery_bound = type(bundle.bound_context)(
                context=replace(
                    bundle.bound_context.context,
                    current_fingerprint=(
                        original_attempt_record.payload.binding_fingerprint
                    ),
                ),
                request_id=original_request_id,
                request_digest=original_request_id.rsplit("-", 1)[-1],
            )
            validate_against_bundle = False
        return (
            recovery_bundle,
            recovery_bound,
            validate_against_bundle,
            original_attempt_record,
        )

    def _replay_native_implementer_request_findings(
        self,
        chain: tuple[ArtifactRecord, ...],
        original_attempt_record: ArtifactRecord,
        state: WorkflowState,
    ) -> dict[str, FindingRecord]:
        request_replay = replay_artifacts(
            chain[: chain.index(original_attempt_record)], state.run_id
        )
        return {
            item.finding_id: item
            for item in reduce_findings(request_replay).ledger.findings
        }

    def _bind_native_implementer_request_findings(
        self,
        recovery_bound: Any,
        request_findings_by_id: dict[str, FindingRecord],
    ) -> Any:
        offered_ids = tuple(
            item.finding_id for item in recovery_bound.context.previous_findings
        )
        if any(finding_id not in request_findings_by_id for finding_id in offered_ids):
            raise WorkflowExecutionError(
                "native agent request-time finding subset is incomplete"
            )
        return type(recovery_bound)(
            context=replace(
                recovery_bound.context,
                previous_findings=tuple(
                    request_findings_by_id[finding_id] for finding_id in offered_ids
                ),
            ),
            request_id=recovery_bound.request_id,
            request_digest=recovery_bound.request_digest,
        )

    def _parse_native_implementer_recovery(
        self,
        canonical: str,
        validate_against_bundle: bool,
        recovery_bundle: NativeImplementerRequestBundle | None,
        bundle: NativeImplementerRequestBundle,
        recovery_bound: Any,
    ) -> Any:
        document = json.loads(canonical)
        if not isinstance(document, dict):
            raise ValueError("native Codex raw response is not an object")
        if canonical_native_implementer_json(document) != canonical:
            raise ValueError("native Codex raw response is not canonical JSON")
        if validate_against_bundle:
            validate_native_implementer_response(document, recovery_bundle or bundle)
        return parse_bound_native_implementer_result(document, recovery_bound)

    def recover_pending_native_implementer(
        self,
        invocation: ImplementerInvocation,
        contract: ImplementerStepContract,
        history: WorkflowHistory,
    ) -> NativeAgentImplementerOutput | None:
        """Replay one record-ahead Codex result without another provider start."""
        _ = contract
        state = self._dependencies.active_state()
        bridge = self._dependencies.artifact_bridge()
        bundle = invocation.native_request
        if (
            state is None
            or bridge is None
            or bundle is None
            or state.protocol_binding is None
            or state.protocol_binding.codex_result_transport
            != NATIVE_IMPLEMENTER_TRANSPORT
            or state.current_work_unit_id != invocation.work_unit_id
            or state.current_step is not invocation.step
        ):
            return None
        logical = (
            f"agent-{invocation.work_unit_id}-{invocation.step.value}-"
            f"{invocation.round_number}"
        )
        chain = bridge.store.current_chain()
        candidates = tuple(
            item
            for item in chain
            if isinstance(item.payload, AgentResultPayload)
            and item.logical_id == logical
        )
        candidate = self._dependencies.canonical_agent_result(candidates, logical)
        persisted_content = self._dependencies.content_text(
            role=Role.CODEX,
            work_unit_id=invocation.work_unit_id,
            round_number=invocation.round_number,
            operation=invocation.step.value,
            request_id=(None if candidate is None else candidate.payload.request_id),
            response_sha256=(
                None if candidate is None else candidate.payload.response_sha256
            ),
            fingerprint=(
                None if candidate is None else candidate.fingerprint.sha256
            ),
            chain=chain,
        )
        if persisted_content is None:
            if candidate is None:
                return None
            raise WorkflowExecutionError(
                "native agent recovery record has no authoritative provider content"
            )
        canonical, content_record = persisted_content
        content_payload = content_record.payload
        assert isinstance(content_payload, ProviderContentPayload)
        response_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if response_sha256 != content_payload.response_sha256:
            raise WorkflowExecutionError(
                "native provider content digest differs from its record"
            )
        if (
            candidate is not None
            and candidate.payload.response_sha256 != response_sha256
        ):
            raise WorkflowExecutionError(
                "native implementer recovery raw response digest differs from its record"
            )
        (
            recovery_bundle,
            recovery_bound,
            validate_against_bundle,
            original_attempt_record,
        ) = self._bind_native_implementer_request(
            chain,
            candidate,
            invocation,
            bundle,
        )
        if original_attempt_record is not None:
            try:
                request_findings_by_id = (
                    self._replay_native_implementer_request_findings(
                        chain,
                        original_attempt_record,
                        state,
                    )
                )
            except ArtifactReplayError as exc:
                raise WorkflowExecutionError(
                    f"native agent request-time finding replay failed: {exc}"
                ) from exc
            recovery_bound = self._bind_native_implementer_request_findings(
                recovery_bound,
                request_findings_by_id,
            )
        try:
            result = self._parse_native_implementer_recovery(
                canonical,
                validate_against_bundle,
                recovery_bundle,
                bundle,
                recovery_bound,
            )
        except (ValueError, TypeError) as exc:
            raise WorkflowExecutionError(
                f"native Codex recovery response no longer validates: {exc}"
            ) from exc
        output = NativeAgentImplementerOutput(
            result=result,
            canonical_json=canonical,
            request_id=recovery_bound.request_id,
            response_sha256=response_sha256,
        )
        if candidate is None:
            self._dependencies.persist_implementer_contract(
                output,
                invocation.previous_findings,
                recovery_fingerprint=content_record.fingerprint.sha256,
            )
            logger.warning(
                "Recovered native implementer result from raw-response-ahead persistence: "
                "work-unit=%s operation=%s",
                invocation.work_unit_id,
                invocation.step.value,
            )
            self._dependencies.store_implementer_output(canonical)
            return output
        record = candidate
        payload = record.payload
        if (
            payload.role is not Role.CODEX
            or payload.work_unit_id != str(invocation.work_unit_id)
            or payload.transport_schema != NATIVE_IMPLEMENTER_TRANSPORT
            or payload.request_id != recovery_bound.request_id
            or payload.response_sha256 != response_sha256
        ):
            raise WorkflowExecutionError(
                "native Codex recovery record differs from its durable binding"
            )
        expected_payload = agent_result_payload(
            result,
            role=AgentRole.CODEX,
            work_unit_id=invocation.work_unit_id,
            transport_schema=NATIVE_IMPLEMENTER_TRANSPORT,
            request_id=recovery_bound.request_id,
            response_sha256=response_sha256,
        )
        if payload != expected_payload:
            raise WorkflowExecutionError(
                "native Codex recovery result differs from its durable record"
            )
        # AgentResult and its per-finding response transitions are separate
        # append-only records.  Re-drive the idempotent persistence routine so
        # a crash after AgentResult publication cannot make an incomplete
        # finding-disposition set look fully recovered.
        self._dependencies.persist_implementer_contract(
            output,
            invocation.previous_findings,
            recovery_fingerprint=record.fingerprint.sha256,
        )
        logger.warning(
            "Recovered native implementer result from record-ahead persistence: "
            "work-unit=%s operation=%s",
            invocation.work_unit_id,
            invocation.step.value,
        )
        self._dependencies.store_implementer_output(canonical)
        return output

    def _replay_pending_native_reviewer(
        self,
        chain: tuple[ArtifactRecord, ...],
        state: WorkflowState,
    ) -> ArtifactReplayResult:
        return replay_artifacts(
            chain,
            state.run_id,
            allow_incomplete_review_tail=True,
        )

    def _build_pending_native_reviewer_context(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
        unit: WorkUnitRecord,
        record: ArtifactRecord,
        round_number: int,
        attestation: ValidationAttestation,
    ) -> NativeReviewContext:
        expected_test_files = (
            tuple(
                path
                for path in history.active_review_packet.manifest.paths
                if matches_path_patterns(path, context.test_path_patterns)
            )
            if history.active_review_packet is not None
            and history.active_review_packet.fingerprint
            == record.fingerprint.sha256
            else ()
            if state.current_step is WorkflowStep.CLAUDE_PLAN_REVIEW
            else context.expected_test_files
        )
        approval_marker = (
            ApprovalMarker.PLAN
            if state.current_step is WorkflowStep.CLAUDE_PLAN_REVIEW
            else ApprovalMarker.FINAL
            if state.current_step is WorkflowStep.CLAUDE_FINAL_REVIEW
            else ApprovalMarker.SLICE
        )
        payload = record.payload
        assert isinstance(payload, ReviewPayload)
        offered_ids = dict.fromkeys(payload.finding_ids, True)
        final_previous_findings = tuple(
            item
            for item in history.findings
            if offered_ids.get(item.finding_id, False)
        )
        previous_findings = {
            ApprovalMarker.FINAL: final_previous_findings,
        }.get(approval_marker, history.findings)
        return NativeReviewContext(
            run_id=state.run_id,
            work_unit_id=str(unit.work_unit_id),
            operation=state.current_step.value,
            diff_fingerprint=record.fingerprint.sha256,
            reviewer=AgentRole.CLAUDE,
            approval_marker=approval_marker,
            slice_id=(
                "FINAL"
                if approval_marker is ApprovalMarker.FINAL
                else f"{unit.slice_id:02d}"
            ),
            round_number=round_number,
            previous_findings=previous_findings,
            validation_attestation=attestation,
            test_files=tuple(sorted(set(expected_test_files))),
            test_changes_approved=context.test_changes_approved,
            allow_new_observations=unit.kind is not WorkUnitKind.CORRECTION,
            validation_command_prefixes=(
                context.validation_matrix.finding_command_prefixes
            ),
            red_state_followup_slice=context.red_state_followup_slice,
        )

    def _parse_pending_native_reviewer_response(
        self,
        canonical: str,
        native_context: NativeReviewContext,
        payload: ReviewPayload,
        request_digest: str,
    ) -> ContractResult:
        document = json.loads(canonical)
        if not isinstance(document, dict):
            raise ValueError("native response log must contain a JSON object")
        validate_native_review_provider_response_for_context(document, native_context)
        return parse_bound_native_contract_result(
            document,
            BoundNativeReviewContext(
                context=native_context,
                request_id=payload.request_id,
                request_digest=request_digest,
            ),
        )

    def recover_pending_native_reviewer_before_policy(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> PersistedNativeReviewerReplay | None:
        """Recover a native decision before current-worktree policy is evaluated.

        A provider response and its ReviewPayload are written before the state-v3
        checkpoint.  Repository changes made after that durable write must not
        force the already completed reviewer round to be rebuilt against a new
        fingerprint or invoke the provider again.
        """
        bridge = self._dependencies.artifact_bridge()
        unit = state.current_work_unit
        if (
            bridge is None
            or self._dependencies.active_state() is None
            or state.protocol_binding is None
            or state.protocol_binding.claude_review_transport
            != NATIVE_REVIEW_TRANSPORT
            or state.current_step
            not in {
                WorkflowStep.CLAUDE_PLAN_REVIEW,
                WorkflowStep.CLAUDE_SLICE_REVIEW,
                WorkflowStep.CLAUDE_FINAL_REVIEW,
            }
            or self._dependencies.active_state().run_id != state.run_id
            or self._dependencies.active_state().current_work_unit_id != unit.work_unit_id
        ):
            return None

        chain = bridge.store.current_chain()
        try:
            replay = self._replay_pending_native_reviewer(chain, state)
        except ArtifactReplayError as exc:
            raise WorkflowExecutionError(
                f"pre-policy native reviewer recovery cannot replay records: {exc}"
            ) from exc
        pending_record_id = replay.pending_review_record_id
        logical_prefix = f"review-claude-{unit.work_unit_id}-"
        if pending_record_id is not None:
            record = next(
                (item for item in chain if item.record_id == pending_record_id),
                None,
            )
        else:
            # A complete review bundle and WorkflowEvent can be durable before
            # the transition it decides. The current record-derived round and
            # cursor identify that decision-ahead window without consulting
            # runtime history or a state cache.
            logical_id = f"{logical_prefix}{unit.round_number}"
            candidates = tuple(
                item
                for item in chain
                if isinstance(item.payload, ReviewPayload)
                and item.logical_id == logical_id
            )
            if not candidates:
                return None
            if len(candidates) != 1:
                raise WorkflowExecutionError(
                    "pre-policy native reviewer recovery has duplicate round authority"
                )
            record = candidates[0]
        if (
            record is None
            or not isinstance(record.payload, ReviewPayload)
            or record.payload.reviewer is not Role.CLAUDE
            or record.payload.work_unit_id != str(unit.work_unit_id)
            or record.payload.transport_schema != NATIVE_REVIEW_TRANSPORT
            or not record.logical_id.startswith(logical_prefix)
        ):
            raise WorkflowExecutionError(
                "pre-policy native reviewer recovery tail differs from the active work unit"
            )
        suffix = record.logical_id.removeprefix(logical_prefix)
        if not suffix.isdigit() or int(suffix) < 1:
            raise WorkflowExecutionError(
                "native reviewer recovery record has an invalid logical round"
            )
        round_number = int(suffix)
        payload = record.payload
        assert isinstance(payload, ReviewPayload)
        if payload.request_id is None or payload.response_sha256 is None:
            raise WorkflowExecutionError(
                "pre-policy native reviewer recovery lacks its request binding"
            )
        matching_attestations = tuple(
            item
            for item in history.attestations
            if item.complete
            and item.diff_fingerprint == record.fingerprint.sha256
        )
        authoritative_attestations = tuple(
            item
            for item in chain
            if isinstance(item.payload, ValidationAttestationPayload)
            and item.payload.attested_by is Role.ORCHESTRATOR
            and item.fingerprint.sha256 == record.fingerprint.sha256
        )
        if len(matching_attestations) != 1 or len(authoritative_attestations) != 1:
            raise WorkflowExecutionError(
                "pre-policy native reviewer recovery requires one complete "
                "authoritative attestation"
            )
        attestation = matching_attestations[0]

        persisted_content = self._dependencies.content_text(
            role=payload.reviewer,
            work_unit_id=unit.work_unit_id,
            round_number=round_number,
            operation=state.current_step.value,
            request_id=payload.request_id,
            response_sha256=payload.response_sha256,
            fingerprint=record.fingerprint.sha256,
            chain=chain,
        )
        if persisted_content is None:
            raise WorkflowExecutionError(
                "pre-policy native reviewer recovery has no authoritative "
                "provider content"
            )
        canonical, _content_payload = persisted_content
        native_context = self._build_pending_native_reviewer_context(
            state,
            context,
            history,
            unit,
            record,
            round_number,
            attestation,
        )
        request_digest = payload.request_id.removeprefix("native-review-request-")
        try:
            result = self._parse_pending_native_reviewer_response(
                canonical,
                native_context,
                payload,
                request_digest,
            )
        except (json.JSONDecodeError, ValueError, NativeReviewContractError) as exc:
            raise WorkflowExecutionError(
                f"pre-policy native reviewer response no longer validates: {exc}"
            ) from exc
        if not review_payload_matches_result(payload, result):
            raise WorkflowExecutionError(
                "pre-policy native reviewer result differs from its decision record"
            )
        output = NativeAgentReviewOutput(
            result=result,
            canonical_json=canonical,
            request_id=payload.request_id,
            context=native_context,
        )
        self._dependencies.persist_review_contract(
            output,
            record.fingerprint.sha256,
            round_number,
            native_context.previous_findings,
        )
        logger.warning(
            "Mirroring request-bound native Claude review before current-diff "
            "policy: work-unit=%s round=%s fingerprint=%s request=%s",
            unit.work_unit_id,
            round_number,
            record.fingerprint.sha256,
            payload.request_id,
        )
        return PersistedNativeReviewerReplay(
            output=output,
            fingerprint=record.fingerprint.sha256,
            round_number=round_number,
        )

    def recover_pending_native_reviewer(
        self,
        invocation: ReviewerInvocation,
        contract: StepContract,
        history: WorkflowHistory,
    ) -> NativeAgentReviewOutput | None:
        """Replay one native record-ahead decision without starting Claude."""
        state = self._dependencies.active_state()
        bridge = self._dependencies.artifact_bridge()
        bundle = invocation.native_request
        if (
            state is None
            or bridge is None
            or bundle is None
            or invocation.reviewer is not AgentRole.CLAUDE
            or state.protocol_binding is None
            or state.protocol_binding.claude_review_transport
            != NATIVE_REVIEW_TRANSPORT
            or state.current_work_unit_id != invocation.work_unit_id
            or state.current_step is not invocation.step
        ):
            return None
        logical_id = (
            f"review-claude-{invocation.work_unit_id}-{invocation.round_number}"
        )
        chain = bridge.store.current_chain()
        candidates = tuple(
            item
            for item in chain
            if isinstance(item.payload, ReviewPayload)
            and item.logical_id == logical_id
        )
        if len(candidates) > 1:
            raise WorkflowExecutionError(
                "native reviewer recovery has multiple decision records"
            )
        record = candidates[0] if candidates else None
        payload = None if record is None else record.payload
        if record is not None:
            assert isinstance(payload, ReviewPayload)
            if (
                payload.reviewer is not Role.CLAUDE
                or payload.work_unit_id != str(invocation.work_unit_id)
                or payload.transport_schema != NATIVE_REVIEW_TRANSPORT
                or payload.request_id != bundle.bound_context.request_id
                or payload.response_sha256 is None
                or record.fingerprint.sha256 != invocation.fingerprint
            ):
                raise WorkflowExecutionError(
                    "native reviewer recovery record differs from the rebuilt request"
                )
        matching_attestations = tuple(
            item
            for item in chain
            if isinstance(item.payload, ValidationAttestationPayload)
            and item.fingerprint.sha256 == invocation.fingerprint
            and item.payload.attested_by is Role.ORCHESTRATOR
        )
        if len(matching_attestations) != 1:
            raise WorkflowExecutionError(
                "native reviewer recovery requires one authoritative attestation"
            )
        if (
            contract.validation_attestation is None
            or not contract.validation_attestation.complete
            or contract.validation_attestation.diff_fingerprint
            != invocation.fingerprint
        ):
            raise WorkflowExecutionError(
                "native reviewer recovery has no complete bound attestation"
            )
        persisted_content = self._dependencies.content_text(
            role=Role(invocation.reviewer.value),
            work_unit_id=invocation.work_unit_id,
            round_number=invocation.round_number,
            operation=invocation.step.value,
            request_id=(None if payload is None else payload.request_id),
            response_sha256=(None if payload is None else payload.response_sha256),
            fingerprint=(None if record is None else record.fingerprint.sha256),
            chain=chain,
        )
        if persisted_content is None and payload is None:
            return None
        if persisted_content is None:
            raise WorkflowExecutionError(
                "native reviewer recovery has no authoritative provider content"
            )
        canonical, _content_payload = persisted_content
        try:
            document = json.loads(canonical)
            if not isinstance(document, dict):
                raise ValueError("native response log must contain a JSON object")
            validate_native_review_provider_response(document, bundle)
            result = parse_bound_native_contract_result(
                document, bundle.bound_context
            )
        except (json.JSONDecodeError, ValueError, NativeReviewContractError) as exc:
            raise WorkflowExecutionError(
                f"native reviewer recovery response no longer validates: {exc}"
            ) from exc
        if payload is not None and not review_payload_matches_result(payload, result):
            raise WorkflowExecutionError(
                "native reviewer recovery result differs from its decision record"
            )
        output = NativeAgentReviewOutput(
            result=result,
            canonical_json=canonical,
            request_id=bundle.bound_context.request_id,
            context=bundle.bound_context.context,
        )
        self._dependencies.persist_review_contract(
            output,
            invocation.fingerprint,
            invocation.round_number,
            invocation.previous_findings,
        )
        logger.warning(
            "Replaying request-bound native Claude review after its state "
            "checkpoint failed: work-unit=%s round=%s fingerprint=%s request=%s",
            invocation.work_unit_id,
            invocation.round_number,
            invocation.fingerprint,
            bundle.bound_context.request_id,
        )
        return output
