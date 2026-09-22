"""Lossless writers from workflow domain objects to authoritative records.

The bridge validates every append against the typed source object and reloads
the durable record before the caller may act. The record chain is the only
technical authority; ``state.json`` is a disposable run locator and every
state-v3 document is a reducer-version-bound projection. Foreign semantics and
non-canonical resume histories remain fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import logging
from typing import Callable, Iterable, Mapping

from artifact_models import (
    AgentResultPayload,
    ArtifactPayload,
    ArtifactRecord,
    ArtifactValidationError,
    BranchDiscoveryCompletedPayload,
    BranchDiscoveryFindingPayload,
    BranchDiscoveryHandoffExportPayload,
    BranchDiscoveryHandoffImportPayload,
    BranchDiscoveryOccurrencePayload,
    ClosedFindingDispositionSnapshot,
    ClosedFindingOccurrencePayload,
    BindingPayload,
    CommandSpec,
    CorrectionWorkUnitPayload,
    DiagnosticPayload,
    Fingerprint,
    FingerprintKind,
    FindingSeverity,
    FindingSnapshotItem,
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    ImportedFindingTransition,
    FindingTransitionPayload,
    FamilyBindingPayload,
    PlanAssignmentPayload,
    PlanTreatmentAssignment,
    PlanTreatmentDecisionPayload,
    PlanTreatmentProposalPayload,
    NoImplementationRequiredPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ProviderAttemptPayload,
    ProviderUsagePayload,
    RecordType,
    GatePayload,
    PlanPayload,
    ReviewPayload,
    ReviewStopRequestPayload,
    ReviewEvidencePayload,
    Role,
    RemediationCohortCheckpointPayload,
    SideEffectPayload,
    SliceSpec,
    TaskPayload,
    ValidationAttestationPayload,
    ValidationRequestPayload,
    ValidationResult,
    WorkUnitPayload,
    WorkflowCompletionPayload,
    canonical_json,
    stable_side_effect_key,
    finding_transition_sequence_sha256,
    flatten_finding_transition_history,
)
from artifact_store import ArtifactStore
from contracts import (
    AgentRole,
    CodexContractResult,
    ContractResult,
    FindingRecord,
    FindingResponseDecision,
    PlannedSlice,
    ValidationAttestation,
    ValidationCommandSpec,
)
from finding_order import replay_compatible_finding_ids, sorted_finding_ids
from finding_signature import finding_record_signature
from finding_planning import (
    FindingSignatureGroup,
    PlanTreatmentDecision,
    PlanTreatmentDecisionKind,
    PlanTreatmentKind,
    PlanTreatmentProposal,
    RemediationRoundEvaluation,
    evaluate_remediation_round,
    validate_plan_treatment_coverage,
    validate_plan_treatment_decisions,
    validate_plan_completion,
)
import native_finding_decisions
from task_contract import TaskContract
from validation_matrix import ValidationRequest
from provider_input_budget import ProviderInputMeasurement
from artifact_replay import (
    ArtifactReplayError,
    ArtifactReplayResult,
    ReplayDiagnostic,
    ReplayDiagnosticCode,
    _validate_work_unit_revision,
    replay_artifacts,
)


class ArtifactBridgeError(RuntimeError):
    """Raised when structured and state-v3 meanings are not identical."""


def _finding_decision_bridge_error(detail: str) -> ArtifactBridgeError:
    """Create an E7 contract error without expanding the public error taxonomy."""
    return ArtifactBridgeError(detail)


logger = logging.getLogger(__name__)


def _foreign_provider_binding_error(
    chain: tuple[ArtifactRecord, ...],
    measurement: ProviderInputMeasurementPayload,
    binding_fingerprint: str,
    operation_instance: str | None,
    prior: tuple[ArtifactRecord, ...],
) -> ArtifactBridgeError | None:
    if prior:
        return None
    prior_same_operation = tuple(
        record
        for record in chain
        if isinstance(record.payload, ProviderAttemptPayload)
        and record.payload.provider == measurement.provider
        and record.payload.role == measurement.role
        and record.payload.operation == measurement.operation
        and record.payload.work_unit_id == measurement.work_unit_id
        and record.payload.logical_operation_id
        in {
            logical_provider_operation_id(
                run_id=record.run_id,
                work_unit_id=measurement.work_unit_id,
                provider=measurement.provider,
                operation=measurement.operation,
                binding_fingerprint=record.payload.binding_fingerprint,
                operation_instance=operation_instance,
            ),
            logical_provider_operation_id(
                run_id=record.run_id,
                work_unit_id=measurement.work_unit_id,
                provider=measurement.provider,
                operation=measurement.operation,
                binding_fingerprint=record.payload.binding_fingerprint,
            ),
        }
    )
    if not prior_same_operation:
        return None
    first = prior_same_operation[0].payload
    if first.binding_fingerprint == binding_fingerprint:
        return None
    return ArtifactBridgeError(
        "provider attempt immutable binding differs from its first attempt: "
        "field=binding_fingerprint "
        f"first={first.binding_fingerprint[:12]} "
        f"current={binding_fingerprint[:12]}"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _role(role: AgentRole) -> Role:
    return Role(role.value)


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def command_payload(spec: ValidationCommandSpec) -> CommandSpec:
    """Map a command without parsing its presentation string."""
    if spec.argv:
        return CommandSpec(family="validation", argv=spec.argv)
    assert spec.legacy_shell is not None
    return CommandSpec(
        family="legacy-validation",
        argv=(spec.legacy_shell,),
        mode="legacy_shell",
    )


def task_payload(contract: TaskContract) -> TaskPayload:
    return TaskPayload(
        target_branch=contract.target_branch,
        scope_paths=contract.scope_patterns,
        assignment_sha256=contract.digest,
        work_plan_path=contract.work_plan_path,
    )


def plan_payload(
    *, work_plan_path: str, approved_plan_commit: str, slices: Iterable[PlannedSlice]
) -> PlanPayload:
    return PlanPayload(
        work_plan_path=work_plan_path,
        approved_plan_commit=approved_plan_commit,
        slices=tuple(
            SliceSpec(
                str(item.slice_id),
                item.summary,
                item.scope_paths,
                item.acceptance_criteria,
            )
            for item in slices
        ),
    )


def agent_result_payload(
    result: CodexContractResult,
    *,
    role: AgentRole,
    work_unit_id: int | str,
    transport_schema: str,
    request_id: str,
    response_sha256: str,
) -> AgentResultPayload:
    outcome = "stopped" if result.stopped else "ready" if result.ready else "not_ready"
    return AgentResultPayload(
        role=_role(role),
        work_unit_id=str(work_unit_id),
        outcome=outcome,
        test_files=result.test_files,
        transport_schema=transport_schema,
        request_id=request_id,
        response_sha256=response_sha256,
        slice_plan=tuple(
            SliceSpec(
                str(item.slice_id),
                item.summary,
                item.scope_paths,
                item.acceptance_criteria,
            )
            for item in result.slice_plan
        ),
        plan_treatments=tuple(
            PlanTreatmentProposalPayload(
                signature=item.signature,
                finding_ids=item.finding_ids,
                treatment_kind=item.treatment_kind.value,
                closing_slice_ids=tuple(
                    str(value) for value in item.closing_slice_ids
                ),
                no_code_reason=(
                    None
                    if item.no_code_reason is None
                    else item.no_code_reason.value
                ),
                evidence=item.evidence,
                evidence_paths=item.evidence_paths,
                affected_paths=item.affected_paths,
            )
            for item in result.plan_treatments
        ),
        plan_completion=(
            None
            if result.plan_completion is None
            else result.plan_completion.value
        ),
    )


def review_payload(
    result: ContractResult,
    *,
    work_unit_id: int | str,
    transport_schema: str,
    request_id: str,
    response_sha256: str,
) -> ReviewPayload:
    verdict = "stop" if result.stopped else "approved" if result.approval else "denied"
    structured_evidence = (
        None
        if result.evidence is None
        else ReviewEvidencePayload(
            dimensions=result.evidence.dimensions,
            largest_residual_risk=result.evidence.largest_residual_risk,
            break_condition=result.evidence.break_condition,
        )
    )
    return ReviewPayload(
        reviewer=_role(result.reviewer),
        work_unit_id=str(work_unit_id),
        verdict=verdict,
        finding_ids=tuple(item.finding_id for item in result.findings),
        # Schema-2 records written before S4a used this string field.  Keep it
        # readable as opaque legacy data, but never create or split it again.
        evidence=None,
        transport_schema=transport_schema,
        request_id=request_id,
        response_sha256=response_sha256,
        review_evidence=structured_evidence,
        red_state_followup_slice=result.red_state_followup_slice,
        test_files=result.test_files,
        pre_mortem=result.pre_mortem,
        stop_request=(
            None
            if result.stop_request is None
            else ReviewStopRequestPayload(
                result.stop_request.rule_id,
                result.stop_request.rationale,
                result.stop_request.remediation_paths,
            )
        ),
        plan_treatment_decisions=tuple(
            PlanTreatmentDecisionPayload(
                item.signature,
                item.decision.value,
                item.rationale,
            )
            for item in result.plan_treatment_decisions
        ),
    )


def branch_discovery_completed_payload(
    result: ContractResult,
    previous_findings: tuple[FindingRecord, ...],
    *,
    work_unit_id: int | str,
    validation_attestation_record_id: str,
    reviewed_head_commit: str,
    transport_schema: str,
    request_id: str,
    response_sha256: str,
) -> BranchDiscoveryCompletedPayload:
    """Persist the E6 delivery without manufacturing an approval verdict."""

    if result.delivery_kind != "branch_discovery_completed":
        raise ArtifactBridgeError(
            "branch discovery payload requires BRANCH_DISCOVERY_COMPLETED"
        )
    if result.approval is not None or result.stopped:
        raise ArtifactBridgeError(
            "branch discovery completion cannot carry approved, denied, or stop"
        )
    if result.evidence is None or result.pre_mortem is None:
        raise ArtifactBridgeError(
            "branch discovery completion requires evidence and pre_mortem"
        )
    previous = {item.finding_id: item for item in previous_findings}
    unknown_occurrences = tuple(
        item.finding_id for item in result.occurrences
        if item.finding_id not in previous
    )
    if unknown_occurrences:
        raise ArtifactBridgeError(
            "branch discovery occurrence references an unknown prior finding"
        )
    new_findings = tuple(
        BranchDiscoveryFindingPayload(
            finding_id=finding.finding_id,
            severity=FindingSeverity(finding.finding_class.value),
            summary=finding.summary,
            acceptance_test=finding.acceptance_test,
            predecessor_finding_ref=finding.predecessor_finding_ref,
            evidence_anchor_sha256=finding.evidence_anchor_sha256,
            affected_paths=finding.affected_paths,
        )
        for finding in result.findings
        if finding.finding_id not in previous
    )
    occurrences = tuple(
        BranchDiscoveryOccurrencePayload(
            occurrence.finding_id,
            occurrence.rationale,
            occurrence.evidence_anchor_sha256,
        )
        for occurrence in result.occurrences
    )
    return BranchDiscoveryCompletedPayload(
        reviewer=_role(result.reviewer),
        work_unit_id=str(work_unit_id),
        new_findings=new_findings,
        occurrences=occurrences,
        review_evidence=ReviewEvidencePayload(
            result.evidence.dimensions,
            result.evidence.largest_residual_risk,
            result.evidence.break_condition,
        ),
        pre_mortem=result.pre_mortem,
        validation_attestation_record_id=validation_attestation_record_id,
        reviewed_head_commit=reviewed_head_commit,
        transport_schema=transport_schema,
        request_id=request_id,
        response_sha256=response_sha256,
        scan_complete=result.scan_complete,
    )


def branch_discovery_completed_payload_matches_result(
    payload: BranchDiscoveryCompletedPayload,
    result: ContractResult,
    previous_findings: tuple[FindingRecord, ...],
) -> bool:
    """Compare the E6 delivery with its request-bound native result."""

    try:
        expected = branch_discovery_completed_payload(
            result,
            previous_findings,
            work_unit_id=payload.work_unit_id,
            validation_attestation_record_id=(
                payload.validation_attestation_record_id
            ),
            reviewed_head_commit=payload.reviewed_head_commit,
            transport_schema=payload.transport_schema,
            request_id=payload.request_id,
            response_sha256=payload.response_sha256,
        )
    except (ArtifactBridgeError, ArtifactValidationError):
        return False
    return payload == expected


def review_payload_matches_result(
    payload: ReviewPayload,
    result: ContractResult,
) -> bool:
    """Compare a review fact without ever parsing the legacy evidence string."""
    verdict = "stop" if result.stopped else "approved" if result.approval else "denied"
    finding_ids = tuple(item.finding_id for item in result.findings)
    if payload.review_evidence is not None:
        evidence_matches = payload.review_evidence == (
            None
            if result.evidence is None
            else ReviewEvidencePayload(
                result.evidence.dimensions,
                result.evidence.largest_residual_risk,
                result.evidence.break_condition,
            )
        ) and payload.evidence is None
    else:
        legacy_evidence = (
            None
            if result.evidence is None
            else " | ".join(
                (
                    result.evidence.dimensions,
                    result.evidence.largest_residual_risk,
                    result.evidence.break_condition,
                )
            )
        )
        evidence_matches = payload.evidence == legacy_evidence
    return (
        payload.verdict == verdict
        and replay_compatible_finding_ids(payload.finding_ids) == finding_ids
        and evidence_matches
        and payload.red_state_followup_slice == result.red_state_followup_slice
        and payload.test_files == result.test_files
        and payload.pre_mortem == result.pre_mortem
        and payload.stop_request
        == (
            None
            if result.stop_request is None
            else ReviewStopRequestPayload(
                result.stop_request.rule_id,
                result.stop_request.rationale,
                result.stop_request.remediation_paths,
            )
        )
    )


def review_payload_matches_complete_result(
    payload: ReviewPayload,
    result: ContractResult,
) -> bool:
    """Compare a request-bound ReviewPayload with a complete-ledger result."""
    result_by_id = {item.finding_id: item for item in result.findings}
    if any(finding_id not in result_by_id for finding_id in payload.finding_ids):
        return False
    request_bound = replace(
        result,
        findings=tuple(
            result_by_id[finding_id]
            for finding_id in sorted_finding_ids(payload.finding_ids)
        ),
    )
    return review_payload_matches_result(payload, request_bound)


def finding_payload(
    finding: FindingRecord,
    *,
    actor: AgentRole | None = None,
    action: str = "opened",
    rationale: str | None = None,
    work_unit_id: int | str | None = None,
    response_decision: FindingResponseDecision | None = None,
    closure: native_finding_decisions.NativeFindingClosure | None = None,
) -> FindingTransitionPayload:
    reporter = _role(finding.origin.reporter)
    structured = work_unit_id is not None
    return FindingTransitionPayload(
        finding_id=finding.finding_id,
        reporter=reporter,
        actor=_role(actor) if actor is not None else reporter,
        action=action,
        severity=FindingSeverity(finding.finding_class.value),
        finding_status=finding.status.value.lower(),
        rationale=rationale or finding.status_rationale or finding.summary,
        work_unit_id=None if work_unit_id is None else str(work_unit_id),
        summary=finding.summary if structured and action == "opened" else None,
        acceptance_test=(
            finding.acceptance_test if structured and action == "opened" else None
        ),
        origin_slice_id=(
            finding.origin.slice_id if structured and action == "opened" else None
        ),
        origin_round_number=(
            finding.origin.round_number if structured and action == "opened" else None
        ),
        response_decision=(
            response_decision.value.lower() if response_decision is not None else None
        ),
        closure_kind=None if closure is None else closure.kind.value,
        rejection_reason=(
            None
            if closure is None or closure.rejection_reason is None
            else closure.rejection_reason.value
        ),
        closure_evidence=None if closure is None else closure.evidence,
        predecessor_finding_ref=(
            finding.predecessor_finding_ref
            if structured and action == "opened"
            else None
        ),
        evidence_anchor_sha256=(
            finding.evidence_anchor_sha256
            if structured and action == "opened"
            else None
        ),
        affected_paths=(finding.affected_paths if action == "opened" else ()),
    )


def finding_handoff_export_payload(
    replay: ArtifactReplayResult,
    *,
    approved_plan_commit: str,
    approval_review_record_id: str,
    target_task_path: str,
    target_task_bytes: bytes,
) -> FindingHandoffExportPayload:
    """Build an export only from the ordered facts of an accepted replay."""
    if replay.head_record_id is None:
        raise ArtifactBridgeError("finding export requires a non-empty accepted replay")
    if any(
        isinstance(record.payload, NoImplementationRequiredPayload)
        for record in replay.records
    ):
        raise _finding_decision_bridge_error(
            "NO_IMPLEMENTATION_REQUIRED forbids an IMPLEMENT handoff"
        )
    review = next(
        (record for record in replay.records if record.record_id == approval_review_record_id),
        None,
    )
    if (
        review is None
        or not isinstance(review.payload, ReviewPayload)
        or review.payload.verdict != "approved"
    ):
        raise ArtifactBridgeError("finding export requires its approved reviewer record")
    plans = [record.payload for record in replay.records if isinstance(record.payload, PlanPayload)]
    if not plans or not any(plan.approved_plan_commit == approved_plan_commit for plan in plans):
        raise ArtifactBridgeError("finding export plan commit is not present in accepted replay")
    transitions = flatten_finding_transition_history(replay.records)
    if not transitions:
        raise ArtifactBridgeError("finding export requires at least one source transition")
    return FindingHandoffExportPayload(
        source_run_id=replay.expected_run_id,
        source_head_record_id=replay.head_record_id,
        approved_plan_commit=approved_plan_commit,
        approval_review_record_id=approval_review_record_id,
        finding_transition_record_ids=tuple(item.record_id for item in transitions),
        finding_transitions_sha256=finding_transition_sequence_sha256(transitions),
        target_task_path=target_task_path,
        target_task_sha256=hashlib.sha256(target_task_bytes).hexdigest(),
        authority=Role.ORCHESTRATOR,
    )


def finding_handoff_import_payload(
    source_replay: ArtifactReplayResult,
    export_record: ArtifactRecord,
    *,
    target_run_id: str,
    target_task_bytes: bytes,
) -> FindingHandoffImportPayload:
    """Verify an export against its source replay and copy its ordered history."""
    export = export_record.payload
    if not isinstance(export, FindingHandoffExportPayload):
        raise ArtifactBridgeError("finding import requires a finding handoff export record")
    accepted_export = next(
        (record for record in source_replay.records if record.record_id == export_record.record_id),
        None,
    )
    if accepted_export != export_record:
        raise ArtifactBridgeError("finding export record is not in the accepted source replay")
    if export_record.run_id != source_replay.expected_run_id:
        raise ArtifactBridgeError("finding export record belongs to another source run")
    source_prefix = source_replay.records[: source_replay.records.index(export_record)]
    transitive = flatten_finding_transition_history(source_prefix)
    legacy = tuple(
        ImportedFindingTransition(record.record_id, record.payload)
        for record in source_prefix
        if isinstance(record.payload, FindingTransitionPayload)
    )
    transitions = next(
        (
            candidate
            for candidate in (transitive, legacy)
            if export.finding_transition_record_ids
            == tuple(item.record_id for item in candidate)
            and export.finding_transitions_sha256
            == finding_transition_sequence_sha256(candidate)
        ),
        None,
    )
    if (
        export.source_run_id != source_replay.expected_run_id
        or not export_record.predecessor_ids
        or export.source_head_record_id != export_record.predecessor_ids[0]
        or transitions is None
    ):
        raise ArtifactBridgeError("finding export differs from its accepted source replay")
    target_digest = hashlib.sha256(target_task_bytes).hexdigest()
    if target_digest != export.target_task_sha256:
        raise ArtifactBridgeError("finding import task bytes differ from the export binding")
    return FindingHandoffImportPayload(
        source_run_id=export.source_run_id,
        source_head_record_id=export.source_head_record_id,
        approved_plan_commit=export.approved_plan_commit,
        approval_review_record_id=export.approval_review_record_id,
        export_record_id=export_record.record_id,
        target_run_id=target_run_id,
        target_task_sha256=target_digest,
        finding_transitions_sha256=export.finding_transitions_sha256,
        transitions=transitions,
        authority=Role.ORCHESTRATOR,
    )


def _finding_snapshot(
    replay: ArtifactReplayResult, *, allow_empty: bool = False
) -> tuple[FindingSnapshotItem, ...]:
    from finding_reducer import reduce_findings

    findings = reduce_findings(replay).ledger.findings
    findings_by_id = {finding.finding_id: finding for finding in findings}
    snapshot = tuple(
        FindingSnapshotItem(
            finding.finding_id,
            finding_record_signature(finding),
            finding.status.value.lower(),
            FindingSeverity(finding.finding_class.value),
        )
        for finding in (
            findings_by_id[finding_id]
            for finding_id in sorted_finding_ids(findings_by_id)
        )
    )
    if not snapshot and not allow_empty:
        raise ArtifactBridgeError(
            "branch discovery handoff requires a non-empty finding snapshot"
        )
    return snapshot


def _validate_remediation_cohort_handoff(
    replay: ArtifactReplayResult,
    checkpoint_record_id: str | None,
    family_binding: FamilyBindingPayload,
) -> None:
    if checkpoint_record_id is None:
        return
    checkpoint = next(
        (
            record
            for record in replay.records
            if record.record_id == checkpoint_record_id
        ),
        None,
    )
    if checkpoint is None or not isinstance(
        checkpoint.payload, RemediationCohortCheckpointPayload
    ):
        raise ArtifactBridgeError(
            "BRANCH_DISCOVERY remediation handoff requires its cohort checkpoint"
        )
    if (
        checkpoint.payload.family_id != family_binding.family_id
        or checkpoint.payload.implementation_run_id != replay.expected_run_id
    ):
        raise ArtifactBridgeError(
            "remediation cohort checkpoint differs from the source run family"
        )


def _validate_no_implementation_discovery_handoff(
    replay: ArtifactReplayResult,
    *,
    target_execution_mode: str,
    reviewed_head_commit: str,
    remediation_cohort_checkpoint_record_id: str | None,
) -> None:
    no_implementation = next(
        (
            record.payload
            for record in replay.records
            if isinstance(record.payload, NoImplementationRequiredPayload)
        ),
        None,
    )
    source_identity = replay.run_identity
    if (
        target_execution_mode == "BRANCH_DISCOVERY"
        and source_identity is not None
        and source_identity.execution_mode == "PLAN_ONLY"
        and no_implementation is None
    ):
        raise _finding_decision_bridge_error(
            "PLAN_ONLY may hand off directly to BRANCH_DISCOVERY only after "
            "NO_IMPLEMENTATION_REQUIRED"
        )
    if no_implementation is None:
        return
    if target_execution_mode != "BRANCH_DISCOVERY":
        raise _finding_decision_bridge_error(
            "NO_IMPLEMENTATION_REQUIRED must hand off to a "
            "BRANCH_DISCOVERY child run"
        )
    if reviewed_head_commit != no_implementation.reviewed_plan_commit:
        raise _finding_decision_bridge_error(
            "NO_IMPLEMENTATION_REQUIRED discovery handoff must bind the "
            "new reviewed plan HEAD"
        )
    if remediation_cohort_checkpoint_record_id is not None:
        raise _finding_decision_bridge_error(
            "NO_IMPLEMENTATION_REQUIRED forbids an implementation cohort checkpoint"
        )


def branch_discovery_handoff_export_payload(
    replay: ArtifactReplayResult,
    *,
    discovery_review_record_id: str | None,
    validation_attestation_record_id: str,
    reviewed_head_commit: str,
    family_binding: object,
    target_task_path: str,
    target_task_bytes: bytes,
    target_run_identity: str,
    target_execution_mode: str = "PLAN_ONLY",
    source_completion_record_id: str | None = None,
    remediation_cohort_checkpoint_record_id: str | None = None,
    allow_pending_source_completion: bool = False,
) -> BranchDiscoveryHandoffExportPayload:
    """Build the one family handoff used on every E1/E9 edge."""

    from artifact_models import FamilyBindingPayload

    if replay.head_record_id is None:
        raise ArtifactBridgeError(
            "branch discovery handoff export requires a non-empty accepted replay"
        )
    if not isinstance(family_binding, FamilyBindingPayload):
        raise ArtifactBridgeError(
            "branch discovery handoff export requires a family binding"
        )
    if target_execution_mode not in {"PLAN_ONLY", "BRANCH_DISCOVERY"}:
        raise ArtifactBridgeError(
            "branch discovery handoff target execution mode is invalid"
        )
    _validate_no_implementation_discovery_handoff(
        replay,
        target_execution_mode=target_execution_mode,
        reviewed_head_commit=reviewed_head_commit,
        remediation_cohort_checkpoint_record_id=(
            remediation_cohort_checkpoint_record_id
        ),
    )
    attestation = next(
        (
            record
            for record in replay.records
            if record.record_id == validation_attestation_record_id
        ),
        None,
    )
    if attestation is None or not isinstance(
        attestation.payload, ValidationAttestationPayload
    ):
        raise ArtifactBridgeError(
            "branch discovery handoff export requires its validation attestation record"
        )
    if target_execution_mode == "PLAN_ONLY":
        review = next(
            (
                record
                for record in replay.records
                if record.record_id == discovery_review_record_id
            ),
            None,
        )
        if review is None or not isinstance(
            review.payload, BranchDiscoveryCompletedPayload
        ):
            raise ArtifactBridgeError(
                "branch discovery handoff export requires its "
                "BRANCH_DISCOVERY_COMPLETED record; approved is not scan completion"
            )
        if review.fingerprint != attestation.fingerprint:
            raise ArtifactBridgeError(
                "branch discovery completion and validation attestation fingerprints differ"
            )
        if (
            review.payload.validation_attestation_record_id
            != validation_attestation_record_id
            or review.payload.reviewed_head_commit != reviewed_head_commit
        ):
            raise ArtifactBridgeError(
                "branch discovery completion differs from its validation or HEAD binding"
            )
        source_completion_record_id = None
        remediation_cohort_checkpoint_record_id = None
    else:
        completion = next(
            (
                record
                for record in replay.records
                if record.record_id == source_completion_record_id
            ),
            None,
        )
        if completion is None and allow_pending_source_completion:
            pass
        elif completion is None or not isinstance(
            completion.payload, WorkflowCompletionPayload
        ) or completion.payload.outcome != "completed":
            raise ArtifactBridgeError(
                "BRANCH_DISCOVERY family handoff requires a completed source run"
            )
        _validate_remediation_cohort_handoff(
            replay,
            remediation_cohort_checkpoint_record_id,
            family_binding,
        )
        discovery_review_record_id = None
    transitions = flatten_finding_transition_history(replay.records)
    if not transitions and target_execution_mode != "BRANCH_DISCOVERY":
        raise ArtifactBridgeError(
            "branch discovery handoff export requires at least one source transition"
        )
    expected_predecessor_head = (
        source_completion_record_id
        if target_execution_mode == "BRANCH_DISCOVERY"
        else replay.head_record_id
    )
    if (
        family_binding.predecessor_run_id != replay.expected_run_id
        or family_binding.predecessor_head_record_id != expected_predecessor_head
    ):
        raise ArtifactBridgeError(
            "branch discovery target family predecessor differs from the source head"
        )
    return BranchDiscoveryHandoffExportPayload(
        source_run_id=replay.expected_run_id,
        source_head_record_id=replay.head_record_id,
        discovery_review_record_id=discovery_review_record_id,
        validation_attestation_record_id=validation_attestation_record_id,
        reviewed_head_commit=reviewed_head_commit,
        family_id=family_binding.family_id,
        family_base_commit=family_binding.family_base_commit,
        cycle_number=family_binding.cycle_number,
        predecessor_run_id=family_binding.predecessor_run_id,
        predecessor_head_record_id=family_binding.predecessor_head_record_id,
        finding_transition_record_ids=tuple(
            item.source_record_id or item.record_id for item in transitions
        ),
        finding_transitions_sha256=finding_transition_sequence_sha256(transitions),
        target_task_path=target_task_path,
        target_task_sha256=hashlib.sha256(target_task_bytes).hexdigest(),
        target_run_identity=target_run_identity,
        authority=Role.ORCHESTRATOR,
        target_execution_mode=target_execution_mode,
        source_completion_record_id=source_completion_record_id,
        remediation_cohort_checkpoint_record_id=(
            remediation_cohort_checkpoint_record_id
        ),
        closed_finding_dispositions=_closed_disposition_snapshots(replay),
    )


def branch_discovery_handoff_import_payload(
    source_replay: ArtifactReplayResult,
    export_record: ArtifactRecord,
    *,
    target_run_id: str,
    target_task_path: str,
    target_task_bytes: bytes,
    target_family_binding: object,
) -> BranchDiscoveryHandoffImportPayload:
    """Revalidate E9's source, task, identity, family and transitive history."""

    from artifact_models import FamilyBindingPayload

    export = export_record.payload
    if not isinstance(export, BranchDiscoveryHandoffExportPayload):
        raise ArtifactBridgeError(
            "branch discovery import requires a branch discovery export record"
        )
    if not isinstance(target_family_binding, FamilyBindingPayload):
        raise ArtifactBridgeError(
            "branch discovery import requires the target RunProfile family binding"
        )
    accepted_export = next(
        (
            record
            for record in source_replay.records
            if record.record_id == export_record.record_id
        ),
        None,
    )
    if accepted_export != export_record:
        raise ArtifactBridgeError(
            "branch discovery export record is not resolvable in the source run"
        )
    if export.target_execution_mode == "PLAN_ONLY":
        validate_plan_handoff_export_position(source_replay, export_record)
    else:
        completion = next(
            (
                record
                for record in source_replay.records
                if record.record_id == export.source_completion_record_id
            ),
            None,
        )
        if (
            completion is None
            or not isinstance(completion.payload, WorkflowCompletionPayload)
            or completion.payload.outcome != "completed"
        ):
            raise ArtifactBridgeError(
                "BRANCH_DISCOVERY family handoff requires a completed source run"
            )
    if (
        export_record.run_id != source_replay.expected_run_id
        or export.source_run_id != source_replay.expected_run_id
        or not export_record.predecessor_ids
        or export.source_head_record_id != export_record.predecessor_ids[0]
    ):
        raise ArtifactBridgeError(
            "branch discovery export source run or bound source head differs"
        )
    export_position = source_replay.records.index(export_record)
    source_prefix = source_replay.records[:export_position]
    transitions = flatten_finding_transition_history(source_prefix)
    if (
        export.finding_transition_record_ids
        != tuple(item.source_record_id or item.record_id for item in transitions)
        or export.finding_transitions_sha256
        != finding_transition_sequence_sha256(transitions)
    ):
        raise ArtifactBridgeError(
            "branch discovery export differs from its flattened source history"
        )
    task_digest = hashlib.sha256(target_task_bytes).hexdigest()
    if task_digest != export.target_task_sha256:
        raise ArtifactBridgeError(
            "branch discovery import target_task_sha256 differs from task bytes"
        )
    if target_task_path != export.target_task_path:
        raise ArtifactBridgeError(
            "branch discovery import target_task_path differs from queue position"
        )
    if target_run_id != export.target_run_identity:
        raise ArtifactBridgeError(
            "branch discovery import target_run_identity differs from target run"
        )
    family_values = (
        target_family_binding.family_id,
        target_family_binding.family_base_commit,
        target_family_binding.cycle_number,
        target_family_binding.predecessor_run_id,
        target_family_binding.predecessor_head_record_id,
    )
    if family_values != (
        export.family_id,
        export.family_base_commit,
        export.cycle_number,
        export.predecessor_run_id,
        export.predecessor_head_record_id,
    ):
        raise ArtifactBridgeError(
            "branch discovery import family binding differs from target RunProfile"
        )
    source_without_export = replace(
        source_replay,
        records=source_prefix,
        head_record_id=export.source_head_record_id,
    )
    return BranchDiscoveryHandoffImportPayload(
        source_run_id=export.source_run_id,
        source_head_record_id=export.source_head_record_id,
        discovery_review_record_id=export.discovery_review_record_id,
        validation_attestation_record_id=export.validation_attestation_record_id,
        reviewed_head_commit=export.reviewed_head_commit,
        family_id=export.family_id,
        family_base_commit=export.family_base_commit,
        cycle_number=export.cycle_number,
        predecessor_run_id=export.predecessor_run_id,
        predecessor_head_record_id=export.predecessor_head_record_id,
        export_record_id=export_record.record_id,
        target_run_id=target_run_id,
        target_task_path=target_task_path,
        target_task_sha256=task_digest,
        target_run_identity=export.target_run_identity,
        finding_transitions_sha256=export.finding_transitions_sha256,
        transitions=transitions,
        finding_snapshot=_finding_snapshot(
            source_without_export,
            allow_empty=export.target_execution_mode == "BRANCH_DISCOVERY",
        ),
        authority=Role.ORCHESTRATOR,
        target_execution_mode=export.target_execution_mode,
        source_completion_record_id=export.source_completion_record_id,
        remediation_cohort_checkpoint_record_id=(
            export.remediation_cohort_checkpoint_record_id
        ),
        closed_finding_dispositions=export.closed_finding_dispositions,
    )


def validate_plan_handoff_export_position(
    source_replay: ArtifactReplayResult,
    export_record: ArtifactRecord,
) -> None:
    """Accept E9 at the head or before completion plus ledgered publication."""

    export = export_record.payload
    assert isinstance(export, BranchDiscoveryHandoffExportPayload)
    export_position = source_replay.records.index(export_record)
    trailing = source_replay.records[export_position + 1 :]
    if not trailing:
        return
    completion, *publication = trailing
    if (
        not isinstance(completion.payload, WorkflowCompletionPayload)
        or completion.payload.outcome != "completed"
        or completion.payload.final_binding_id
        != export.discovery_review_record_id
        or any(
            not isinstance(record.payload, SideEffectPayload)
            or record.payload.effect_class != "file_write"
            for record in publication
        )
    ):
        raise ArtifactBridgeError(
            "branch discovery export must be the accepted source replay head "
            "or precede only its completed workflow and ledgered publication; "
            "branch discovery export is not the source run head otherwise"
        )


def derive_family_acceptance(replay: ArtifactReplayResult) -> bool:
    """Derive E6 family acceptance only from a terminal discovery run.

    This is deliberately separate from ``ReviewPayload.verdict``: a completed
    scan can contain open findings and therefore need not accept the family.
    """

    identity = replay.run_identity
    profile = replay.run_profile
    if (
        identity is None
        or identity.execution_mode != "BRANCH_DISCOVERY"
        or profile is None
        or profile.family_binding is None
    ):
        raise ArtifactBridgeError(
            "family acceptance may be derived only from its own BRANCH_DISCOVERY run"
        )
    completions = tuple(
        record
        for record in replay.records
        if isinstance(record.payload, BranchDiscoveryCompletedPayload)
    )
    terminal = tuple(
        record
        for record in replay.records
        if isinstance(record.payload, WorkflowCompletionPayload)
        and record.payload.outcome == "completed"
    )
    if len(completions) != 1 or len(terminal) != 1:
        raise ArtifactBridgeError(
            "family acceptance requires one completed scan and one terminal workflow"
        )
    completion_position = replay.records.index(completions[0])
    terminal_position = replay.records.index(terminal[0])
    if completion_position >= terminal_position:
        raise ArtifactBridgeError(
            "family acceptance completion order is invalid"
        )
    from finding_reducer import reduce_findings

    return not reduce_findings(replay).open_set.finding_ids


def _plan_treatment_assignments(
    treatments: tuple[PlanTreatmentProposal, ...],
    *,
    review_fingerprint: str,
    evidence_anchors: Mapping[
        str, native_finding_decisions.NoCodeEvidenceAnchor
    ] | None,
) -> tuple[PlanTreatmentAssignment, ...]:
    anchors = {} if evidence_anchors is None else dict(evidence_anchors)
    no_code_signatures = {
        item.signature
        for item in treatments
        if item.treatment_kind is PlanTreatmentKind.NO_CODE
    }
    if set(anchors) - no_code_signatures:
        raise _finding_decision_bridge_error(
            "No-Code evidence anchors contain a foreign treatment signature"
        )
    for treatment in treatments:
        anchor = anchors.get(treatment.signature)
        if anchor is None:
            continue
        if (
            anchor.evidence_paths != treatment.evidence_paths
            or anchor.affected_paths != treatment.affected_paths
        ):
            raise _finding_decision_bridge_error(
                "No-Code evidence anchor paths differ from the reviewed proposal"
            )
    return tuple(
        PlanTreatmentAssignment(
            signature=item.signature,
            finding_ids=item.finding_ids,
            treatment_kind=item.treatment_kind.value,
            closing_slice_ids=tuple(str(value) for value in item.closing_slice_ids),
            no_code_reason=(
                None if item.no_code_reason is None else item.no_code_reason.value
            ),
            evidence=item.evidence,
            authoritative_fingerprint=(
                review_fingerprint
                if item.treatment_kind is PlanTreatmentKind.NO_CODE
                else None
            ),
            evidence_anchor=anchors.get(item.signature),
        )
        for item in treatments
    )


def plan_assignment_payload(
    *,
    source_snapshot_record: ArtifactRecord,
    plan_result_record: ArtifactRecord,
    review_record: ArtifactRecord,
    family_binding: FamilyBindingPayload,
    remediation_round_number: int,
    evidence_anchors: Mapping[
        str, native_finding_decisions.NoCodeEvidenceAnchor
    ] | None = None,
) -> PlanAssignmentPayload:
    """Build the sole E3 authority from a positive explicit plan review."""

    snapshot = source_snapshot_record.payload
    plan_result = plan_result_record.payload
    review = review_record.payload
    if not isinstance(snapshot, BranchDiscoveryHandoffImportPayload):
        raise ArtifactBridgeError(
            "plan assignment requires a branch discovery Finding snapshot"
        )
    if snapshot.target_execution_mode != "PLAN_ONLY":
        raise ArtifactBridgeError(
            "plan assignment source snapshot must target PLAN_ONLY"
        )
    plan_run_id = snapshot.target_run_id
    if (
        source_snapshot_record.run_id != plan_run_id
        or plan_result_record.run_id != plan_run_id
        or review_record.run_id != plan_run_id
    ):
        raise ArtifactBridgeError(
            "plan assignment records do not belong to the snapshot-bound plan run"
        )
    if not isinstance(plan_result, AgentResultPayload) or plan_result.outcome != "ready":
        raise ArtifactBridgeError(
            "plan assignment requires a ready native implementer plan result"
        )
    if not isinstance(review, ReviewPayload) or review.verdict != "approved":
        raise ArtifactBridgeError(
            "plan assignment requires a positive plan Review record"
        )
    if review.work_unit_id != plan_result.work_unit_id:
        raise ArtifactBridgeError(
            "plan assignment result and Review use different work units"
        )
    if not isinstance(family_binding, FamilyBindingPayload):
        raise ArtifactBridgeError(
            "plan assignment requires a typed family binding"
        )
    family_id = family_binding.family_id
    cycle_number = family_binding.cycle_number
    if family_id != snapshot.family_id or cycle_number != snapshot.cycle_number:
        raise ArtifactBridgeError(
            "plan assignment family binding differs from its source snapshot"
        )
    groups_by_signature: dict[str, list[str]] = {}
    for item in snapshot.finding_snapshot:
        if item.is_open:
            groups_by_signature.setdefault(item.signature, []).append(item.finding_id)
    groups = tuple(
        FindingSignatureGroup(signature, sorted_finding_ids(finding_ids))
        for signature, finding_ids in sorted(groups_by_signature.items())
    )
    try:
        treatments = tuple(
            PlanTreatmentProposal(
                signature=item.signature,
                finding_ids=item.finding_ids,
                treatment_kind=PlanTreatmentKind(item.treatment_kind),
                closing_slice_ids=tuple(
                    int(value) for value in item.closing_slice_ids
                ),
                no_code_reason=(
                    None
                    if item.no_code_reason is None
                    else native_finding_decisions.NativeRejectionReason(
                        item.no_code_reason
                    )
                ),
                evidence=item.evidence,
                evidence_paths=item.evidence_paths,
                affected_paths=item.affected_paths,
            )
            for item in plan_result.plan_treatments
        )
        planned_slices = tuple(
            PlannedSlice(
                int(item.slice_id),
                item.summary,
                item.paths,
                item.acceptance_criteria,
            )
            for item in plan_result.slice_plan
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactBridgeError(
            f"persisted implementer plan treatment is invalid: {exc}"
        ) from exc
    try:
        implementation_scope = validate_plan_treatment_coverage(
            groups, treatments, planned_slices
        )
        decisions = tuple(
            PlanTreatmentDecision(
                item.signature,
                PlanTreatmentDecisionKind(item.decision),
                item.rationale,
            )
            for item in review.plan_treatment_decisions
        )
        validate_plan_treatment_decisions(
            treatments, decisions, plan_approved=True
        )
        if plan_result.plan_completion is not None:
            validate_plan_completion(
                treatments,
                planned_slices,
                native_finding_decisions.PlanCompletionKind(
                    plan_result.plan_completion
                ),
            )
    except (TypeError, ValueError) as exc:
        raise ArtifactBridgeError(f"plan assignment coverage is invalid: {exc}") from exc
    review_fingerprint = review_record.fingerprint.sha256
    assignments = _plan_treatment_assignments(
        treatments,
        review_fingerprint=review_fingerprint,
        evidence_anchors=evidence_anchors,
    )
    snapshot_document = [
        {
            "finding_id": item.finding_id,
            "signature": item.signature,
            "finding_status": item.finding_status,
            "severity": item.severity.value,
        }
        for item in snapshot.finding_snapshot
    ]
    return PlanAssignmentPayload(
        family_id=family_id,
        cycle_number=cycle_number,
        remediation_round_number=remediation_round_number,
        source_snapshot_record_id=source_snapshot_record.record_id,
        finding_snapshot_sha256=hashlib.sha256(
            canonical_json(snapshot_document)
        ).hexdigest(),
        plan_result_record_id=plan_result_record.record_id,
        review_record_id=review_record.record_id,
        review_fingerprint=review_fingerprint,
        treatments=assignments,
        slices=tuple(
            SliceSpec(
                str(item.slice_id),
                item.summary,
                item.scope_paths,
                item.acceptance_criteria,
            )
            for item in planned_slices
        ),
        implementation_scope=implementation_scope,
        authority=Role.CLAUDE,  # allowlist:provider -- reviewer authority
        plan_completion=plan_result.plan_completion,
    )


def no_implementation_required_payload(
    replay: ArtifactReplayResult,
    *,
    plan_assignment_record_id: str,
    reviewed_plan_commit: str,
) -> NoImplementationRequiredPayload:
    """Build E7's explicit completion only from a fully closed No-Code plan."""

    assignment_record = next(
        (
            record
            for record in replay.records
            if record.record_id == plan_assignment_record_id
        ),
        None,
    )
    assignment = None if assignment_record is None else assignment_record.payload
    if not isinstance(assignment, PlanAssignmentPayload):
        raise _finding_decision_bridge_error(
            "NO_IMPLEMENTATION_REQUIRED requires its PlanAssignment record"
        )
    if assignment.plan_completion != (
        native_finding_decisions.PlanCompletionKind.NO_IMPLEMENTATION_REQUIRED.value
    ):
        raise _finding_decision_bridge_error(
            "plan assignment does not declare NO_IMPLEMENTATION_REQUIRED"
        )
    if (
        assignment.slices
        or assignment.implementation_scope
        or any(
            item.treatment_kind != "no_code"
            for item in assignment.treatments
        )
    ):
        raise _finding_decision_bridge_error(
            "NO_IMPLEMENTATION_REQUIRED contains implementation work"
        )
    if any(item.evidence_anchor is None for item in assignment.treatments):
        raise _finding_decision_bridge_error(
            "NO_IMPLEMENTATION_REQUIRED lacks a stable No-Code evidence anchor"
        )
    plan = next(
        (
            record.payload
            for record in replay.records
            if isinstance(record.payload, PlanPayload)
            and record.payload.approved_plan_commit == reviewed_plan_commit
        ),
        None,
    )
    if plan is None:
        raise _finding_decision_bridge_error(
            "NO_IMPLEMENTATION_REQUIRED requires the reviewed plan commit"
        )
    review = next(
        (
            record
            for record in replay.records
            if record.record_id == assignment.review_record_id
        ),
        None,
    )
    if (
        review is None
        or not isinstance(review.payload, ReviewPayload)
        or review.payload.verdict != "approved"
    ):
        raise _finding_decision_bridge_error(
            "NO_IMPLEMENTATION_REQUIRED lacks reviewer authority"
        )
    expected_by_id = {
        finding_id: treatment
        for treatment in assignment.treatments
        for finding_id in treatment.finding_ids
    }
    from finding_reducer import is_closed_finding_transition

    heads: dict[str, ImportedFindingTransition] = {}
    for transition in flatten_finding_transition_history(replay.records):
        if transition.payload.action == "status_changed":
            heads[transition.payload.finding_id] = transition
    for finding_id, treatment in expected_by_id.items():
        transition = heads.get(finding_id)
        if (
            transition is None
            or not is_closed_finding_transition(transition)
            or transition.payload.closure_kind != "rejected"
            or transition.payload.rejection_reason != treatment.no_code_reason
        ):
            raise _finding_decision_bridge_error(
                "NO_IMPLEMENTATION_REQUIRED requires every offered Finding "
                f"to be reviewer-authoritatively closed: {finding_id}"
            )
    return NoImplementationRequiredPayload(
        family_id=assignment.family_id,
        cycle_number=assignment.cycle_number,
        remediation_round_number=assignment.remediation_round_number,
        plan_assignment_record_id=plan_assignment_record_id,
        review_record_id=assignment.review_record_id,
        reviewed_plan_commit=reviewed_plan_commit,
        closed_finding_ids=sorted_finding_ids(expected_by_id),
        authority=Role.ORCHESTRATOR,
    )


def _closed_disposition_snapshots(
    replay: ArtifactReplayResult,
) -> tuple[ClosedFindingDispositionSnapshot, ...]:
    snapshots: dict[str, ClosedFindingDispositionSnapshot] = {}
    for record in replay.records:
        payload = record.payload
        if isinstance(payload, BranchDiscoveryHandoffImportPayload):
            for item in payload.closed_finding_dispositions:
                snapshots[item.finding_id] = item
        if not isinstance(payload, PlanAssignmentPayload):
            continue
        for treatment in payload.treatments:
            if treatment.treatment_kind != "no_code":
                continue
            if treatment.evidence_anchor is None:
                continue
            for finding_id in treatment.finding_ids:
                snapshots[finding_id] = ClosedFindingDispositionSnapshot(
                    finding_id=finding_id,
                    signature=treatment.signature,
                    source_plan_assignment_record_id=record.record_id,
                    evidence_anchor=treatment.evidence_anchor,
                )
    return tuple(
        snapshots[finding_id]
        for finding_id in sorted_finding_ids(snapshots)
    )


def closed_finding_review_bindings(
    handoff_import: BranchDiscoveryHandoffImportPayload,
    current_anchors: Mapping[
        str, native_finding_decisions.NoCodeEvidenceAnchor
    ],
) -> tuple[native_finding_decisions.ClosedFindingReviewBinding, ...]:
    """Bind current content measurements to imported closed dispositions."""

    dispositions = {
        item.finding_id: item
        for item in handoff_import.closed_finding_dispositions
    }
    if set(current_anchors) != set(dispositions):
        raise _finding_decision_bridge_error(
            "current closed-Finding anchors differ from the imported disposition set"
        )
    return tuple(
        native_finding_decisions.ClosedFindingReviewBinding(
            finding_id=finding_id,
            signature=dispositions[finding_id].signature,
            source_plan_assignment_record_id=(
                dispositions[finding_id].source_plan_assignment_record_id
            ),
            original_anchor=dispositions[finding_id].evidence_anchor,
            current_anchor=current_anchors[finding_id],
        )
        for finding_id in sorted_finding_ids(dispositions)
    )


def closed_finding_occurrence_payloads(
    *,
    discovery_completion_record: ArtifactRecord,
    handoff_import_record: ArtifactRecord,
) -> tuple[ClosedFindingOccurrencePayload, ...]:
    """Materialize each stable closed occurrence as its own typed record."""

    completion = discovery_completion_record.payload
    handoff = handoff_import_record.payload
    if not isinstance(completion, BranchDiscoveryCompletedPayload):
        raise _finding_decision_bridge_error(
            "closed occurrence requires a BRANCH_DISCOVERY_COMPLETED record"
        )
    if not isinstance(handoff, BranchDiscoveryHandoffImportPayload):
        raise _finding_decision_bridge_error(
            "closed occurrence requires its branch discovery handoff import"
        )
    dispositions = {
        item.finding_id: item for item in handoff.closed_finding_dispositions
    }
    payloads: list[ClosedFindingOccurrencePayload] = []
    for occurrence in completion.occurrences:
        disposition = dispositions.get(occurrence.finding_id)
        if disposition is None:
            continue
        expected_anchor = disposition.evidence_anchor.stability_sha256
        if occurrence.evidence_anchor_sha256 != expected_anchor:
            raise _finding_decision_bridge_error(
                "closed Finding occurrence evidence anchor changed"
            )
        payloads.append(
            ClosedFindingOccurrencePayload(
                finding_id=occurrence.finding_id,
                signature=disposition.signature,
                source_plan_assignment_record_id=(
                    disposition.source_plan_assignment_record_id
                ),
                discovery_completion_record_id=(
                    discovery_completion_record.record_id
                ),
                evidence_anchor_sha256=expected_anchor,
                rationale=occurrence.rationale,
                reviewer=completion.reviewer,
            )
        )
    return tuple(payloads)


def remediation_cohort_checkpoint_payload(
    *,
    assignment_record: ArtifactRecord,
    implementation_replay: ArtifactReplayResult,
) -> RemediationCohortCheckpointPayload:
    """Measure S_r at the pre-discovery boundary from authoritative records."""

    assignment = assignment_record.payload
    if not isinstance(assignment, PlanAssignmentPayload):
        raise ArtifactBridgeError(
            "remediation cohort checkpoint requires a PlanAssignment record"
        )
    profile = implementation_replay.run_profile
    binding = None if profile is None else profile.family_binding
    if (
        binding is None
        or binding.family_id != assignment.family_id
        or binding.cycle_number != assignment.cycle_number
    ):
        raise ArtifactBridgeError(
            "remediation cohort checkpoint family differs from implementation run"
        )
    if implementation_replay.head_record_id is None:
        raise ArtifactBridgeError(
            "remediation cohort checkpoint requires an implementation record head"
        )
    from finding_reducer import reduce_findings

    finding_projection = reduce_findings(implementation_replay)
    open_ids = frozenset(finding_projection.open_set.finding_ids)
    known_ids = frozenset(
        finding.finding_id for finding in finding_projection.ledger.findings
    )
    inherited = tuple(item.signature for item in assignment.treatments)
    unresolved = tuple(
        item.signature
        for item in assignment.treatments
        if item.treatment_kind == "implementation"
        and any(
            finding_id not in known_ids or finding_id in open_ids
            for finding_id in item.finding_ids
        )
    )
    return RemediationCohortCheckpointPayload(
        family_id=assignment.family_id,
        remediation_round_number=assignment.remediation_round_number,
        plan_assignment_record_id=assignment_record.record_id,
        implementation_run_id=implementation_replay.expected_run_id,
        implementation_head_record_id=implementation_replay.head_record_id,
        inherited_signatures=inherited,
        unresolved_inherited_signatures=unresolved,
        authority=Role.ORCHESTRATOR,
    )


def evaluate_recorded_remediation_round(
    *,
    assignment_record: ArtifactRecord,
    checkpoint_record: ArtifactRecord,
    discovery_import_record: ArtifactRecord,
    discovery_record: ArtifactRecord,
) -> RemediationRoundEvaluation:
    """Join S_r and N_r by their distinct record types, never by counts."""

    assignment = assignment_record.payload
    checkpoint = checkpoint_record.payload
    discovery_import = discovery_import_record.payload
    discovery = discovery_record.payload
    if not isinstance(assignment, PlanAssignmentPayload):
        raise ArtifactBridgeError("recorded remediation round lacks PlanAssignment")
    if not isinstance(checkpoint, RemediationCohortCheckpointPayload):
        raise ArtifactBridgeError(
            "recorded remediation round lacks its pre-discovery cohort checkpoint"
        )
    if not isinstance(discovery, BranchDiscoveryCompletedPayload):
        raise ArtifactBridgeError(
            "recorded remediation round lacks BRANCH_DISCOVERY_COMPLETED"
        )
    if not isinstance(discovery_import, BranchDiscoveryHandoffImportPayload):
        raise ArtifactBridgeError(
            "recorded remediation round lacks its BRANCH_DISCOVERY handoff import"
        )
    if (
        checkpoint.plan_assignment_record_id != assignment_record.record_id
        or checkpoint.family_id != assignment.family_id
        or checkpoint.remediation_round_number
        != assignment.remediation_round_number
    ):
        raise ArtifactBridgeError(
            "recorded remediation round has inconsistent assignment and checkpoint"
        )
    if (
        discovery_import.target_execution_mode != "BRANCH_DISCOVERY"
        or discovery_import.remediation_cohort_checkpoint_record_id
        != checkpoint_record.record_id
        or discovery_import.source_run_id != checkpoint.implementation_run_id
        or discovery_import.family_id != checkpoint.family_id
        or discovery_import_record.run_id != discovery_record.run_id
        or discovery_import.target_run_id != discovery_record.run_id
        or discovery.validation_attestation_record_id
        != discovery_import.validation_attestation_record_id
        or discovery.reviewed_head_commit
        != discovery_import.reviewed_head_commit
    ):
        raise ArtifactBridgeError(
            "BRANCH_DISCOVERY result is not bound to the recorded remediation cohort"
        )
    return evaluate_remediation_round(
        remediation_round_number=assignment.remediation_round_number,
        inherited_signatures=checkpoint.inherited_signatures,
        unresolved_inherited_signatures=(
            checkpoint.unresolved_inherited_signatures
        ),
        new_findings=tuple(
            (item.finding_id, item.summary, item.acceptance_test)
            for item in discovery.new_findings
        ),
    )


def attestation_payload(
    attestation: ValidationAttestation,
    content_record_id: str,
) -> ValidationAttestationPayload:
    records_by_display = {record.command: record for record in attestation.records}
    results = tuple(
        ValidationResult(
            command=command_payload(spec),
            outcome=(
                records_by_display[spec.display].status.value.lower()
                if spec.display in records_by_display
                else "unavailable"
            ),
            exit_code=(
                records_by_display[spec.display].exit_code
                if spec.display in records_by_display
                else -1
            ),
            output_sha256=hashlib.sha256(
                (
                    records_by_display[spec.display].output
                    if spec.display in records_by_display
                    else ""
                ).encode("utf-8")
            ).hexdigest(),
        )
        for spec in attestation.command_specs
    )
    return ValidationAttestationPayload(
        results=results,
        attested_by=Role.ORCHESTRATOR,
        output_digest=attestation.output_digest,
        content_record_id=content_record_id,
    )


def validation_request_payload(request: ValidationRequest) -> ValidationRequestPayload:
    return ValidationRequestPayload(
        commands=tuple(command_payload(command.command_spec) for command in request.commands),
        requested_by=Role.ORCHESTRATOR,
    )


def provider_input_measurement_payload(
    measurement: ProviderInputMeasurement,
    *,
    work_unit_id: int | str,
    transition_fingerprint: str,
    relevant_record_head: str,
) -> ProviderInputMeasurementPayload:
    return ProviderInputMeasurementPayload(
        provider=Role(measurement.provider),
        role=Role(measurement.role),
        operation=measurement.operation,
        work_unit_id=str(work_unit_id),
        transition_fingerprint=transition_fingerprint,
        relevant_record_head=relevant_record_head,
        input_digest=measurement.input_digest,
        policy_digest=measurement.policy_digest,
        components=tuple(
            ProviderInputComponentPayload(item.name, item.chars, item.bytes)
            for item in measurement.components
        ),
        total_chars=measurement.total_chars,
        total_bytes=measurement.total_bytes,
        safety_limit_chars=measurement.safety_limit_chars,
        safety_limit_bytes=measurement.safety_limit_bytes,
        technical_limit_chars=measurement.technical_limit_chars,
        technical_limit_bytes=measurement.technical_limit_bytes,
        technical_limit_source=measurement.technical_limit_source,
        effective_limit_chars=measurement.effective_limit_chars,
        effective_limit_bytes=measurement.effective_limit_bytes,
        allowed=measurement.allowed,
        violated_dimensions=measurement.violated_dimensions,
        char_overage=measurement.char_overage,
        byte_overage=measurement.byte_overage,
        largest_component=measurement.largest_component,
    )


@dataclass(slots=True)
class ArtifactBridge:
    """Idempotently persist and re-read typed domain statements."""

    store: ArtifactStore
    now: Callable[[], str] = _now

    def append(
        self,
        payload: ArtifactPayload,
        *,
        logical_id: str,
        idempotency_key: str,
        fingerprint_sha256: str,
        fingerprint_kind: FingerprintKind = FingerprintKind.IMPLEMENTATION,
    ) -> ArtifactRecord:
        if isinstance(payload, CorrectionWorkUnitPayload):
            raise ArtifactReplayError(
                ReplayDiagnostic(
                    ReplayDiagnosticCode.UNSUPPORTED_PROTOCOL,
                    "legacy correction_work_unit records cannot be written under "
                    "the installed reducer; inspect historical chains with "
                    "scripts/verify_legacy_chain.py",
                )
            )
        fingerprint = Fingerprint(fingerprint_kind, fingerprint_sha256)
        context = self.store.append_context(
            record_type=payload.record_type,
            logical_id=logical_id,
            idempotency_key=idempotency_key,
        )
        existing = context.existing
        if existing is not None:
            self._assert_equal(existing, payload, logical_id, fingerprint)
            return existing
        record = ArtifactRecord.create(
            run_id=self.store.run_id,
            logical_id=logical_id,
            revision=context.next_revision,
            fingerprint=fingerprint,
            predecessor_ids=context.predecessor_ids,
            created_at=self.now(),
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if isinstance(
            payload, (WorkUnitPayload, CorrectionWorkUnitPayload)
        ) and logical_id.startswith("work-unit-"):
            prior = {
                item.logical_id: item
                for item in self.store.current_chain()
                if isinstance(
                    item.payload, (WorkUnitPayload, CorrectionWorkUnitPayload)
                )
            }.get(logical_id)
            if prior:
                _validate_work_unit_revision(prior, record)
        try:
            persisted = self.store.put(record)
        except Exception:
            # put() documents durable-but-reported-failed publication.  Resolve
            # that state before propagating the original failure.
            recovered = next(
                (
                    item
                    for item in self.store.load_chain()
                    if item.idempotency_key == idempotency_key
                ),
                None,
            )
            if recovered is None:
                raise
            persisted = recovered
        self._assert_equal(persisted, payload, logical_id, fingerprint)
        return persisted

    def append_batch(
        self,
        entries: tuple[
            tuple[ArtifactPayload, str, str, str, FingerprintKind], ...
        ],
    ) -> tuple[ArtifactRecord, ...]:
        """Atomically append domain records which form one replay invariant."""

        if len(entries) < 2:
            raise ArtifactBridgeError("artifact batch requires at least two entries")
        contexts = tuple(
            self.store.append_context(
                record_type=payload.record_type,
                logical_id=logical_id,
                idempotency_key=idempotency_key,
            )
            for payload, logical_id, idempotency_key, _, _ in entries
        )
        existing = tuple(context.existing for context in contexts)
        if any(item is not None for item in existing):
            if not all(item is not None for item in existing):
                raise ArtifactBridgeError(
                    "atomic artifact batch is only partially present"
                )
            persisted = tuple(item for item in existing if item is not None)
            for record, entry in zip(persisted, entries, strict=True):
                payload, logical_id, _, fingerprint_sha256, fingerprint_kind = entry
                self._assert_equal(
                    record,
                    payload,
                    logical_id,
                    Fingerprint(fingerprint_kind, fingerprint_sha256),
                )
            return persisted

        chain = self.store.current_chain()
        revisions = {
            (record.record_type, record.logical_id): record.revision
            for record in chain
        }
        predecessor_ids = () if not chain else (chain[-1].record_id,)
        candidates: list[ArtifactRecord] = []
        for payload, logical_id, idempotency_key, fingerprint_sha256, fingerprint_kind in entries:
            identity = (payload.record_type, logical_id)
            revision = revisions.get(identity, 0) + 1
            record = ArtifactRecord.create(
                run_id=self.store.run_id,
                logical_id=logical_id,
                revision=revision,
                fingerprint=Fingerprint(fingerprint_kind, fingerprint_sha256),
                predecessor_ids=predecessor_ids,
                created_at=self.now(),
                idempotency_key=idempotency_key,
                payload=payload,
            )
            candidates.append(record)
            revisions[identity] = revision
            predecessor_ids = (record.record_id,)
        persisted = self.store.put_batch(tuple(candidates))
        for record, entry in zip(persisted, entries, strict=True):
            payload, logical_id, _, fingerprint_sha256, fingerprint_kind = entry
            self._assert_equal(
                record,
                payload,
                logical_id,
                Fingerprint(fingerprint_kind, fingerprint_sha256),
            )
        return persisted

    @staticmethod
    def _side_effect_logical_id(effect_key: str) -> str:
        return f"side-effect-{hashlib.sha256(effect_key.encode('utf-8')).hexdigest()[:32]}"

    def record_side_effect_intent(
        self,
        *,
        effect_class: str,
        work_unit_id: int | str,
        operation: tuple[str, ...],
        fingerprint_sha256: str,
        fingerprint_kind: FingerprintKind = FingerprintKind.IMPLEMENTATION,
    ) -> tuple[ArtifactRecord, bool]:
        """Persist an intent and report whether this call created it."""
        unit_id = str(work_unit_id)
        effect_key = stable_side_effect_key(effect_class, unit_id, operation)
        logical_id = self._side_effect_logical_id(effect_key)
        existing = self.store.append_context(
            record_type=RecordType.SIDE_EFFECT,
            logical_id=logical_id,
            idempotency_key=f"side-effect-intent:{hashlib.sha256(effect_key.encode('utf-8')).hexdigest()}",
        ).existing
        record = self.append(
            SideEffectPayload(
                effect_key,
                effect_class,
                unit_id,
                operation,
                "intent",
                None,
            ),
            logical_id=logical_id,
            idempotency_key=f"side-effect-intent:{hashlib.sha256(effect_key.encode('utf-8')).hexdigest()}",
            fingerprint_sha256=fingerprint_sha256,
            fingerprint_kind=fingerprint_kind,
        )
        return record, existing is None

    def record_side_effect_result(
        self,
        *,
        effect_class: str,
        work_unit_id: int | str,
        operation: tuple[str, ...],
        result: str,
        fingerprint_sha256: str,
        fingerprint_kind: FingerprintKind = FingerprintKind.IMPLEMENTATION,
    ) -> ArtifactRecord:
        """Persist the result only when the matching intent is authoritative."""
        unit_id = str(work_unit_id)
        effect_key = stable_side_effect_key(effect_class, unit_id, operation)
        logical_id = self._side_effect_logical_id(effect_key)
        intent = self.store.append_context(
            record_type=RecordType.SIDE_EFFECT,
            logical_id=logical_id,
            idempotency_key=(
                f"side-effect-intent:{hashlib.sha256(effect_key.encode('utf-8')).hexdigest()}"
            ),
        )
        expected_intent = SideEffectPayload(
            effect_key,
            effect_class,
            unit_id,
            operation,
            "intent",
            None,
        )
        if (
            intent.existing is None
            or intent.existing.logical_id != logical_id
            or intent.existing.payload != expected_intent
            or intent.existing.fingerprint
            != Fingerprint(fingerprint_kind, fingerprint_sha256)
        ):
            raise ArtifactBridgeError("side effect result has no authoritative intent")
        return self.append(
            SideEffectPayload(
                effect_key,
                effect_class,
                unit_id,
                operation,
                "result",
                result,
            ),
            logical_id=logical_id,
            idempotency_key=f"side-effect-result:{hashlib.sha256(effect_key.encode('utf-8')).hexdigest()}",
            fingerprint_sha256=fingerprint_sha256,
            fingerprint_kind=fingerprint_kind,
        )

    def side_effect_result(
        self,
        *,
        effect_class: str,
        work_unit_id: int | str,
        operation: tuple[str, ...],
    ) -> str | None:
        """Return one indexed result without invalidating the append index."""
        unit_id = str(work_unit_id)
        effect_key = stable_side_effect_key(effect_class, unit_id, operation)
        logical_id = self._side_effect_logical_id(effect_key)
        record = self.store.append_context(
            record_type=RecordType.SIDE_EFFECT,
            logical_id=logical_id,
            idempotency_key=(
                f"side-effect-result:{hashlib.sha256(effect_key.encode('utf-8')).hexdigest()}"
            ),
        ).existing
        if record is None:
            return None
        payload = record.payload
        if (
            not isinstance(payload, SideEffectPayload)
            or payload.effect_key != effect_key
            or payload.effect_class != effect_class
            or payload.work_unit_id != unit_id
            or payload.operation != operation
            or payload.phase != "result"
            or payload.result is None
        ):
            raise ArtifactBridgeError(
                "side effect result changed its immutable intent binding"
            )
        return payload.result

    @staticmethod
    def _assert_equal(
        record: ArtifactRecord,
        payload: ArtifactPayload,
        logical_id: str,
        fingerprint: Fingerprint,
    ) -> None:
        if (
            record.logical_id != logical_id
            or record.fingerprint != fingerprint
            or _digest(record.payload) != _digest(payload)
        ):
            raise ArtifactBridgeError(
                "structured artifact differs semantically from its existing record"
            )

    def diagnostic(
        self,
        *,
        role: AgentRole,
        work_unit_id: int | str,
        attempt: int,
        output: str,
        reason: str,
        fingerprint_sha256: str,
    ) -> ArtifactRecord:
        payload = DiagnosticPayload(
            role=_role(role),
            work_unit_id=str(work_unit_id),
            attempt=attempt,
            output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
            reason=reason,
        )
        return self.append(
            payload,
            logical_id=f"diagnostic-{role.value}-{work_unit_id}-{attempt}",
            idempotency_key=(
                f"diagnostic:{role.value}:{work_unit_id}:{attempt}:"
                f"{payload.output_sha256}"
            ),
            fingerprint_sha256=fingerprint_sha256,
        )

    def start_provider_attempt(
        self,
        *,
        measurement_record: ArtifactRecord,
        binding_fingerprint: str,
        work_unit_id: int | str,
        operation_instance: str | None = None,
        model: str = "unknown",
        effort: str = "unknown",
    ) -> ArtifactRecord:
        """Persist one physical provider start after all local preflights pass."""
        chain = self.store.current_chain()
        replay_artifacts(chain, self.store.run_id)
        if (
            measurement_record not in chain
            or not isinstance(measurement_record.payload, ProviderInputMeasurementPayload)
        ):
            raise ArtifactBridgeError("provider attempt measurement is not in the accepted chain")
        measurement = measurement_record.payload
        if str(work_unit_id) != measurement.work_unit_id:
            raise ArtifactBridgeError("provider attempt work unit differs from its measurement")
        if operation_instance is not None and not operation_instance.strip():
            raise ArtifactBridgeError("provider attempt operation instance must be non-empty")
        logical_operation_id = logical_provider_operation_id(
            run_id=self.store.run_id,
            work_unit_id=str(work_unit_id),
            provider=measurement.provider,
            operation=measurement.operation,
            binding_fingerprint=binding_fingerprint,
            operation_instance=operation_instance,
        )
        prior = tuple(
            record
            for record in chain
            if isinstance(record.payload, ProviderAttemptPayload)
            and record.payload.logical_operation_id == logical_operation_id
        )
        if binding_error := _foreign_provider_binding_error(
            chain,
            measurement,
            binding_fingerprint,
            operation_instance,
            prior,
        ):
            raise binding_error
        if operation_instance is not None and not prior:
            legacy_operation_id = logical_provider_operation_id(
                run_id=self.store.run_id,
                work_unit_id=str(work_unit_id),
                provider=measurement.provider,
                operation=measurement.operation,
                binding_fingerprint=binding_fingerprint,
            )
            legacy_prior = tuple(
                record
                for record in chain
                if isinstance(record.payload, ProviderAttemptPayload)
                and record.payload.logical_operation_id == legacy_operation_id
            )
            if legacy_prior and all(
                record.payload.provider == measurement.provider
                and record.payload.role == measurement.role
                and record.payload.operation == measurement.operation
                and record.payload.work_unit_id == measurement.work_unit_id
                and record.payload.binding_fingerprint == binding_fingerprint
                and record.payload.input_digest == measurement.input_digest
                for record in legacy_prior
            ):
                # Preserve an in-flight pre-instance operation across an upgrade.
                # A semantically different round cannot inherit it because its
                # immutable input digest differs.
                logical_operation_id = legacy_operation_id
                prior = legacy_prior
        for record in prior:
            payload = record.payload
            comparisons = (
                ("provider", payload.provider.value, measurement.provider.value),
                ("role", payload.role.value, measurement.role.value),
                ("operation", payload.operation, measurement.operation),
                ("work_unit_id", payload.work_unit_id, measurement.work_unit_id),
                (
                    "binding_fingerprint",
                    payload.binding_fingerprint,
                    binding_fingerprint,
                ),
                ("input_digest", payload.input_digest, measurement.input_digest),
            )
            difference = next(
                (
                    (field, first, current)
                    for field, first, current in comparisons
                    if first != current
                ),
                None,
            )
            if difference is not None:
                field, first, current = difference
                raise ArtifactBridgeError(
                    "provider attempt immutable binding differs from its first "
                    f"attempt: field={field} first={first[:12]} "
                    f"current={current[:12]}"
                )
        if prior:
            latest_attempt = max(record.payload.attempt_number for record in prior)
            latest_records = tuple(
                record for record in prior
                if record.payload.attempt_number == latest_attempt
            )
            latest_terminal = tuple(
                record for record in latest_records
                if record.payload.phase in {"succeeded", "failed"}
            )
            if len(latest_terminal) != 1:
                raise ArtifactBridgeError(
                    "provider attempt requires one terminal direct predecessor"
                )
        attempt_number = max(
            (record.payload.attempt_number for record in prior), default=0
        ) + 1
        started_at = self.now()
        payload = ProviderAttemptPayload(
            provider=measurement.provider,
            role=measurement.role,
            operation=measurement.operation,
            work_unit_id=measurement.work_unit_id,
            logical_operation_id=logical_operation_id,
            binding_fingerprint=binding_fingerprint,
            measurement_record_id=measurement_record.record_id,
            input_digest=measurement.input_digest,
            attempt_number=attempt_number,
            phase="started",
            started_at=started_at,
            ended_at=None,
            duration_seconds=None,
            failure_kind=None,
            usage=None,
            model=model,
            effort=effort,
        )
        record = self.append(
            payload,
            logical_id=f"{logical_operation_id}-{attempt_number}",
            idempotency_key=f"provider-attempt:{logical_operation_id}:{attempt_number}:started",
            fingerprint_sha256=measurement_record.fingerprint.sha256,
        )
        logger.info(
            "provider attempt started provider=%s operation=%s logical_operation_id=%s attempt=%d status=started",
            measurement.provider.value,
            measurement.operation,
            logical_operation_id,
            attempt_number,
        )
        return record

    def finish_provider_attempt(
        self,
        started_record: ArtifactRecord,
        *,
        duration_seconds: float,
        failure_kind: str | None,
        usage: ProviderUsagePayload | None,
    ) -> ArtifactRecord:
        """Persist the sole terminal revision for a previously durable start."""
        chain = self.store.current_chain()
        if started_record not in chain:
            raise ArtifactBridgeError(
                "provider attempt start is not in the accepted chain"
            )
        replay_artifacts(chain, self.store.run_id)
        if not isinstance(started_record.payload, ProviderAttemptPayload) or started_record.payload.phase != "started":
            raise ArtifactBridgeError("provider attempt terminal requires a started record")
        started = started_record.payload
        phase = "failed" if failure_kind is not None else "succeeded"
        terminal_key = (
            f"provider-attempt:{started.logical_operation_id}:"
            f"{started.attempt_number}:terminal"
        )
        existing = next(
            (
                record for record in chain
                if record.idempotency_key == terminal_key
            ),
            None,
        )
        if existing is not None:
            payload = existing.payload
            if (
                not isinstance(payload, ProviderAttemptPayload)
                or payload.phase != phase
                or payload.failure_kind != failure_kind
                or payload.usage != usage
                or payload.logical_operation_id != started.logical_operation_id
                or payload.attempt_number != started.attempt_number
            ):
                raise ArtifactBridgeError("provider attempt terminal differs from its durable result")
            return existing
        payload = ProviderAttemptPayload(
            provider=started.provider,
            role=started.role,
            operation=started.operation,
            work_unit_id=started.work_unit_id,
            logical_operation_id=started.logical_operation_id,
            binding_fingerprint=started.binding_fingerprint,
            measurement_record_id=started.measurement_record_id,
            input_digest=started.input_digest,
            attempt_number=started.attempt_number,
            phase=phase,
            started_at=started.started_at,
            ended_at=self.now(),
            duration_seconds=duration_seconds,
            failure_kind=failure_kind,
            usage=usage,
            model=started.model,
            effort=started.effort,
        )
        record = self.append(
            payload,
            logical_id=started_record.logical_id,
            idempotency_key=terminal_key,
            fingerprint_sha256=started_record.fingerprint.sha256,
        )
        logger.info(
            "provider attempt terminal provider=%s operation=%s logical_operation_id=%s attempt=%d status=%s",
            started.provider.value,
            started.operation,
            started.logical_operation_id,
            started.attempt_number,
            phase,
        )
        return record


def logical_provider_operation_id(
    *, run_id: str, work_unit_id: str, provider: Role, operation: str,
    binding_fingerprint: str, operation_instance: str | None = None,
) -> str:
    digest = hashlib.sha256(
        canonical_json(
            [
                run_id,
                work_unit_id,
                provider.value,
                operation,
                binding_fingerprint,
                operation_instance,
            ]
            if operation_instance is not None
            else [run_id, work_unit_id, provider.value, operation, binding_fingerprint]
        )
    ).hexdigest()
    return f"provider-operation-{digest}"


__all__ = [
    "ArtifactBridge", "ArtifactBridgeError", "agent_result_payload",
    "attestation_payload", "command_payload", "finding_payload",
    "finding_handoff_export_payload", "finding_handoff_import_payload", "plan_payload",
    "branch_discovery_handoff_export_payload",
    "branch_discovery_handoff_import_payload",
    "validate_plan_handoff_export_position",
    "branch_discovery_completed_payload",
    "branch_discovery_completed_payload_matches_result",
    "closed_finding_occurrence_payloads",
    "closed_finding_review_bindings",
    "derive_family_acceptance",
    "evaluate_recorded_remediation_round",
    "plan_assignment_payload",
    "no_implementation_required_payload",
    "remediation_cohort_checkpoint_payload",
    "review_payload", "review_payload_matches_complete_result",
    "review_payload_matches_result", "task_payload", "validation_request_payload",
    "provider_input_measurement_payload",
    "logical_provider_operation_id",
    "BindingPayload", "GatePayload", "ProviderUsagePayload",
    "WorkUnitPayload",
]
