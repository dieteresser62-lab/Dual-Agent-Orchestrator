from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from types import TracebackType
from typing import Callable

import pytest

import artifact_replay
import native_finding_decisions
from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BranchDiscoveryCompletedPayload,
    BranchDiscoveryHandoffExportPayload,
    BranchDiscoveryHandoffImportPayload,
    BindingPayload,
    BlobReference,
    CommandSpec,
    FinalReviewPreflightPayload,
    FamilyBindingPayload,
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    FindingSeverity,
    FindingSnapshotItem,
    FindingTransitionPayload,
    Fingerprint,
    FingerprintKind,
    GateDecisionPayload,
    GatePayload,
    GateTransitionPayload,
    ImportedFindingTransition,
    InvocationFailurePayload,
    PlanPayload,
    ProviderAttemptPayload,
    ProviderContentPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ProviderUsagePayload,
    QuotaPausePayload,
    RecordType,
    ResumeCheckPayload,
    ReviewAnchorPayload,
    ReviewEvidencePayload,
    ReviewPacketPayload,
    ReviewPayload,
    ReviewValidationBindingPayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    SideEffectPayload,
    SliceBoundaryPayload,
    SliceSpec,
    TransientRetryPayload,
    TaskPayload,
    ValidationAttestationPayload,
    ValidationContentPayload,
    ValidationOutputContent,
    ValidationResult,
    WorkflowCompletionPayload,
    WorkflowEventPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    WorkUnitPayload,
    finding_transition_sequence_sha256,
    provider_text_evidence,
    stable_record_id,
    stable_side_effect_key,
    technical_text_evidence,
)
from artifact_replay import ArtifactReplayError, ReplayDiagnosticCode
from finding_signature import finding_signature


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/artifact_replay.py"
BASELINE = ROOT / "tests/fixtures/replay-rejection-corpus-v1.json"
RUN_ID = "b40-rejection-corpus"
FP_A = Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64)
FP_B = Fingerprint(FingerprintKind.IMPLEMENTATION, "b" * 64)
REQUEST_ID = "native-codex-request-" + "c" * 64
RESPONSE_SHA = "d" * 64
VALIDATION_PASSES = (
    "_validate_workflow_transitions_and_events",
    "_validate_invocation_failures_and_retries",
    "_validate_gate_transitions_and_decisions",
    "_index_validation_content",
    "_validate_attestation_content_bindings",
    "_validate_unbound_validation_content",
    "_validate_provider_decision_content",
    "_validate_unbound_provider_content",
    "_validate_review_anchors",
    "_validate_review_validation_bindings",
    "_validate_required_review_authority",
    "_validate_review_packet_bindings",
    "_validate_work_unit_revisions",
    "_validate_single_finding_import",
    "_validate_chain_record_references",
    "_validate_provider_attempt_sequences",
    "_validate_side_effect_sequences",
)


@dataclass(frozen=True, slots=True)
class RejectionInput:
    records: tuple[ArtifactRecord, ...]
    require_content_authority: bool = False
    require_review_authority: bool = False
    allow_incomplete_review_tail: bool = False


def _append(
    records: list[ArtifactRecord],
    payload: object,
    *,
    logical_id: str | None = None,
    revision: int = 1,
    fingerprint: Fingerprint = FP_A,
    idempotency_key: str | None = None,
    predecessor_ids: tuple[str, ...] | None = None,
    run_id: str = RUN_ID,
) -> ArtifactRecord:
    index = len(records) + 1
    logical_id = logical_id or f"b40-{payload.record_type.value}-{index}"
    record = ArtifactRecord.create(
        run_id=run_id,
        logical_id=logical_id,
        revision=revision,
        fingerprint=fingerprint,
        predecessor_ids=(
            predecessor_ids
            if predecessor_ids is not None
            else ((records[-1].record_id,) if records else ())
        ),
        created_at=f"2026-09-03T10:00:{index:02d}+00:00",
        idempotency_key=idempotency_key or f"b40:{logical_id}:{revision}:{index}",
        payload=payload,  # type: ignore[arg-type]
    )
    records.append(record)
    return record


def _transition(
    work_unit_id: str = "1",
    *,
    slice_id: str = "1",
    step: str = "codex_implementation",
) -> WorkflowTransitionPayload:
    return WorkflowTransitionPayload(
        slice_id, "in_progress", work_unit_id, step, "in_progress"
    )


def _event(
    kind: str,
    referenced: ArtifactRecord,
    *,
    work_unit_id: str | None,
    slice_id: str = "1",
    round_number: int | None = None,
    fingerprint: Fingerprint = FP_A,
    records: list[ArtifactRecord],
) -> ArtifactRecord:
    return _append(
        records,
        WorkflowEventPayload(
            kind, work_unit_id, slice_id, round_number, (referenced.record_id,)
        ),
        fingerprint=fingerprint,
    )


def _agent(work_unit_id: str = "1") -> AgentResultPayload:
    return AgentResultPayload(
        Role.CODEX,
        work_unit_id,
        "ready",
        (),
        "native-codex-v2",
        REQUEST_ID,
        RESPONSE_SHA,
    )


def _review(
    work_unit_id: str = "1", *, finding_ids: tuple[str, ...] = ()
) -> ReviewPayload:
    return ReviewPayload(
        Role.CLAUDE,
        work_unit_id,
        "approved",
        finding_ids,
        "checked",
        "native-claude-review-v2",
        "native-review-request-" + "c" * 64,
        RESPONSE_SHA,
    )


def _failure(
    *,
    invocation_id: str = "invocation-1",
    work_unit_id: str = "1",
    step: str = "codex_implementation",
    slice_id: str = "1",
    failure_kind: str = "quota",
    resume_at: str = "2026-09-03T10:01:00+00:00",
    fingerprint: str | None = "a" * 64,
) -> InvocationFailurePayload:
    provider, provider_sha, provider_bytes = provider_text_evidence("provider")
    technical, technical_sha, technical_bytes = technical_text_evidence("technical")
    return InvocationFailurePayload(
        invocation_id,
        f"invoke:{invocation_id}",
        Role.CODEX,
        failure_kind,
        "transient",
        "AGENT-INVOCATION",
        provider,
        provider_sha,
        provider_bytes,
        technical,
        technical_sha,
        technical_bytes,
        "2026-09-03T10:00:00+00:00",
        "2026-09-03T10:00:01+00:00",
        step,
        slice_id,
        work_unit_id,
        2 if failure_kind == "quota" else 3,
        None,
        None,
        None,
        None,
        resume_at,
        0,
        59,
        1,
        True,
        fingerprint,
    )


def _append_failure(
    records: list[ArtifactRecord],
    payload: InvocationFailurePayload,
    *,
    logical_id: str | None = None,
    revision: int = 1,
    fingerprint: Fingerprint = FP_A,
) -> ArtifactRecord:
    invocation_id = payload.invocation_id
    return _append(
        records,
        payload,
        logical_id=logical_id or f"invocation-failure-{invocation_id}",
        revision=revision,
        fingerprint=fingerprint,
        idempotency_key=f"invocation-failure:{invocation_id}",
    )


def _gate_transition(work_unit_id: str = "1") -> GateTransitionPayload:
    return GateTransitionPayload(
        work_unit_id, "clear", "none", None, None, (), None, None, ()
    )


def _validation_parts() -> tuple[
    ValidationOutputContent, ValidationResult
]:
    command = CommandSpec("pytest", ("pytest", "tests/test_b40.py"))
    empty = BlobReference(hashlib.sha256(b"").hexdigest(), 0)
    output = ValidationOutputContent(command, "pass", 0, empty, empty, empty, 0)
    result = ValidationResult(command, "pass", 0, empty.sha256)
    return output, result


def _append_validation_pair(
    records: list[ArtifactRecord],
    *,
    fingerprint: Fingerprint = FP_A,
) -> tuple[ArtifactRecord, ArtifactRecord]:
    logical_id = "validation-1"
    output, result = _validation_parts()
    result_record_id = stable_record_id(
        RUN_ID, RecordType.VALIDATION_ATTESTATION, logical_id, 1
    )
    content = _append(
        records,
        ValidationContentPayload(
            logical_id,
            result_record_id,
            "validation-matrix-v1",
            "e" * 64,
            "B40 validation",
            (output,),
        ),
        logical_id=f"validation-content-{logical_id}",
        fingerprint=fingerprint,
    )
    attestation = _append(
        records,
        ValidationAttestationPayload(
            (result,), Role.ORCHESTRATOR, "e" * 64, content.record_id
        ),
        logical_id=logical_id,
        fingerprint=fingerprint,
    )
    assert attestation.record_id == result_record_id
    return content, attestation


def _append_attestation(
    records: list[ArtifactRecord],
    *,
    fingerprint: Fingerprint = FP_A,
    logical_id: str = "validation-1",
) -> ArtifactRecord:
    _, result = _validation_parts()
    return _append(
        records,
        ValidationAttestationPayload(
            (result,), Role.ORCHESTRATOR, "e" * 64, "ar1-" + "f" * 64
        ),
        logical_id=logical_id,
        fingerprint=fingerprint,
    )


def _measurement(
    *, operation: str = "codex_implementation", input_digest: str = "1" * 64
) -> ProviderInputMeasurementPayload:
    return ProviderInputMeasurementPayload(
        Role.CODEX,
        Role.CODEX,
        operation,
        "1",
        "a" * 64,
        "b" * 64,
        input_digest,
        "2" * 64,
        (ProviderInputComponentPayload("prompt", 3, 3),),
        3,
        3,
        10,
        10,
        None,
        None,
        None,
        10,
        10,
        True,
        (),
        0,
        0,
        "prompt",
    )


def _attempt(
    measurement: ArtifactRecord,
    *,
    operation: str = "codex_implementation",
    logical_operation_id: str = "provider-operation-1",
    attempt_number: int = 1,
    phase: str = "started",
) -> ProviderAttemptPayload:
    assert isinstance(measurement.payload, ProviderInputMeasurementPayload)
    terminal = phase != "started"
    return ProviderAttemptPayload(
        Role.CODEX,
        Role.CODEX,
        operation,
        "1",
        logical_operation_id,
        "a" * 64,
        measurement.record_id,
        measurement.payload.input_digest,
        attempt_number,
        phase,
        "2026-09-03T10:00:00+00:00",
        "2026-09-03T10:00:01+00:00" if terminal else None,
        1.0 if terminal else None,
        None,
        ProviderUsagePayload(output_tokens=1) if phase == "succeeded" else None,
    )


def _provider_pair(
    records: list[ArtifactRecord], *, content_kind: str = "agent_result"
) -> tuple[ArtifactRecord, ArtifactRecord]:
    blob = BlobReference(RESPONSE_SHA, 3)
    content = _append(
        records,
        ProviderContentPayload(
            Role.CODEX,
            "1",
            1,
            "codex_implementation",
            REQUEST_ID,
            RESPONSE_SHA,
            content_kind,
            3,
            blob,
        ),
    )
    decision = _append(
        records,
        _agent(),
        logical_id="agent-1-codex_implementation-1",
    )
    return content, decision


def _append_review_anchor(
    records: list[ArtifactRecord], review: ArtifactRecord, *, revision: int = 1
) -> ArtifactRecord:
    digest = hashlib.sha256(review.record_id.encode("utf-8")).hexdigest()[:16]
    return _append(
        records,
        ReviewAnchorPayload(review.record_id, ()),
        logical_id=f"review-anchors-{digest}",
        revision=revision,
    )


def _append_review_validation(
    records: list[ArtifactRecord],
    review: ArtifactRecord,
    attestation: ArtifactRecord,
    *,
    revision: int = 1,
    fingerprint: Fingerprint = FP_A,
) -> ArtifactRecord:
    digest = hashlib.sha256(review.record_id.encode("utf-8")).hexdigest()[:16]
    return _append(
        records,
        ReviewValidationBindingPayload(review.record_id, attestation.record_id),
        logical_id=f"review-validation-{digest}",
        revision=revision,
        fingerprint=fingerprint,
    )


def _finding_transition() -> FindingTransitionPayload:
    return FindingTransitionPayload(
        "C-01",
        Role.CLAUDE,
        Role.CLAUDE,
        "opened",
        FindingSeverity.OBSERVATION,
        "open",
        "Carry the finding.",
        "source-work-unit",
        "Imported observation.",
        "The observation remains visible.",
        "1",
        1,
    )


def _import_payload(*, target_run_id: str = RUN_ID, source_run_id: str = "source-run") -> FindingHandoffImportPayload:
    imported = ImportedFindingTransition("ar1-" + "3" * 64, _finding_transition())
    digest = finding_transition_sequence_sha256((imported,))
    return FindingHandoffImportPayload(
        source_run_id,
        "ar1-" + "4" * 64,
        "5" * 40,
        "ar1-" + "6" * 64,
        "ar1-" + "7" * 64,
        target_run_id,
        "8" * 64,
        digest,
        (imported,),
        Role.ORCHESTRATOR,
    )


def _export_payload(
    *,
    source_head: str,
    approval_review_id: str,
    transition_ids: tuple[str, ...] = ("ar1-" + "0" * 64,),
    transition_digest: str = "9" * 64,
    source_run_id: str = RUN_ID,
) -> FindingHandoffExportPayload:
    return FindingHandoffExportPayload(
        source_run_id,
        source_head,
        "5" * 40,
        approval_review_id,
        transition_ids,
        transition_digest,
        "inbox/backlog/b40-followup.md",
        "8" * 64,
        Role.ORCHESTRATOR,
    )


def _family_binding(*, family_id: str = "finding-family") -> FamilyBindingPayload:
    return FamilyBindingPayload(
        family_id,
        "1" * 40,
        ("src/a.py",),
        "source-run",
        "ar1-" + "4" * 64,
        2,
        None,
        "2" * 40,
    )


def _branch_import_payload(
    *,
    target_run_id: str = RUN_ID,
    source_run_id: str = "source-run",
    snapshot_signature: str | None = None,
) -> BranchDiscoveryHandoffImportPayload:
    transition = _finding_transition()
    imported = ImportedFindingTransition(
        "ar1-" + "3" * 64,
        transition,
        source_run_id,
        "ar1-" + "3" * 64,
    )
    digest = finding_transition_sequence_sha256((imported,))
    return BranchDiscoveryHandoffImportPayload(
        source_run_id,
        "ar1-" + "4" * 64,
        "ar1-" + "5" * 64,
        "ar1-" + "6" * 64,
        "2" * 40,
        "finding-family",
        "1" * 40,
        2,
        "source-run",
        "ar1-" + "4" * 64,
        "ar1-" + "7" * 64,
        target_run_id,
        "inbox/backlog/b40-followup.md",
        "8" * 64,
        target_run_id,
        digest,
        (imported,),
        (
            FindingSnapshotItem(
                "C-01",
                snapshot_signature
                or finding_signature("The observation remains visible.", ()),
                "open",
                FindingSeverity.OBSERVATION,
            ),
        ),
        Role.ORCHESTRATOR,
    )


def _with_joint_cutover(factory: Callable[[], object]) -> object:
    prior = native_finding_decisions.JOINT_67_68_NATIVE_CONTRACT_CUTOVER
    native_finding_decisions.JOINT_67_68_NATIVE_CONTRACT_CUTOVER = True
    try:
        return factory()
    finally:
        native_finding_decisions.JOINT_67_68_NATIVE_CONTRACT_CUTOVER = prior


def _branch_export_prefix(
    records: list[ArtifactRecord],
    *,
    attestation_fingerprint: Fingerprint = FP_A,
) -> tuple[ArtifactRecord, ArtifactRecord, ArtifactRecord]:
    _append(
        records,
        _with_joint_cutover(
            lambda: RunIdentityPayload(
                "inbox/b40.md",
                "feature/b40",
                "1" * 40,
                "2" * 40,
                "BRANCH_DISCOVERY",
                None,
            )
        ),
    )
    _append(
        records,
        RunProfilePayload(
            RoleProfilePayload("implementer", "medium"),
            RoleProfilePayload("reviewer", "high"),
            family_binding=replace(
                _family_binding(),
                predecessor_run_id=None,
                predecessor_head_record_id=None,
                cycle_number=1,
            ),
        ),
    )
    finding = _append(records, _finding_transition())
    attestation = _append_attestation(
        records,
        fingerprint=attestation_fingerprint,
    )
    completion = _append(
        records,
        _with_joint_cutover(
            lambda: BranchDiscoveryCompletedPayload(
                reviewer=Role.CLAUDE,
                work_unit_id="1",
                new_findings=(),
                occurrences=(),
                review_evidence=ReviewEvidencePayload(
                    "all dimensions",
                    "residual risk",
                    "break condition",
                ),
                pre_mortem="pre mortem",
                validation_attestation_record_id=attestation.record_id,
                reviewed_head_commit="2" * 40,
                transport_schema="native-claude-review-v2",
                request_id="native-review-request-" + "c" * 64,
                response_sha256=RESPONSE_SHA,
                scan_complete=True,
            )
        ),
        logical_id="review-claude-discovery-1",
    )
    return finding, completion, attestation


def _branch_export_payload(
    finding: ArtifactRecord,
    review: ArtifactRecord,
    attestation: ArtifactRecord,
    *,
    family_id: str = "finding-family",
    transition_digest: str | None = None,
) -> BranchDiscoveryHandoffExportPayload:
    transition = ImportedFindingTransition(
        finding.record_id,
        finding.payload,  # type: ignore[arg-type]
        RUN_ID,
        finding.record_id,
    )
    return BranchDiscoveryHandoffExportPayload(
        RUN_ID,
        review.record_id,
        review.record_id,
        attestation.record_id,
        "2" * 40,
        family_id,
        "1" * 40,
        2,
        RUN_ID,
        review.record_id,
        (finding.record_id,),
        transition_digest or finding_transition_sequence_sha256((transition,)),
        "inbox/backlog/b40-followup.md",
        "8" * 64,
        "target-run",
        Role.ORCHESTRATOR,
    )


def _branch_discovery_target_export_prefix(
    records: list[ArtifactRecord],
    *,
    source_mode: str = "IMPLEMENT",
) -> tuple[ArtifactRecord, ArtifactRecord]:
    identity = (
        RunIdentityPayload(
            "inbox/b40.md",
            "feature/b40",
            "1" * 40,
            "2" * 40,
            source_mode,
            None,
        )
        if source_mode != "BRANCH_DISCOVERY"
        else _with_joint_cutover(
            lambda: RunIdentityPayload(
                "inbox/b40.md",
                "feature/b40",
                "1" * 40,
                "2" * 40,
                source_mode,
                None,
            )
        )
    )
    _append(
        records,
        identity,
    )
    _append(
        records,
        RunProfilePayload(
            RoleProfilePayload("implementer", "medium"),
            RoleProfilePayload("reviewer", "high"),
            family_binding=replace(
                _family_binding(),
                predecessor_run_id=None,
                predecessor_head_record_id=None,
                cycle_number=1,
            ),
        ),
    )
    attestation = _append_attestation(records)
    approval = _append(
        records,
        _review(),
        logical_id="review-claude-source-1",
    )
    binding = _append(
        records,
        BindingPayload(
            "commit",
            "2" * 40,
            attestation.record_id,
            (approval.record_id,),
        ),
    )
    completion = _append(
        records,
        WorkflowCompletionPayload("completed", binding.record_id),
    )
    return attestation, completion


def _branch_discovery_target_export_payload(
    attestation: ArtifactRecord,
    completion: ArtifactRecord,
    *,
    source_completion_record_id: str | None = None,
) -> BranchDiscoveryHandoffExportPayload:
    return BranchDiscoveryHandoffExportPayload(
        source_run_id=RUN_ID,
        source_head_record_id=completion.record_id,
        discovery_review_record_id=None,
        validation_attestation_record_id=attestation.record_id,
        reviewed_head_commit="2" * 40,
        family_id="finding-family",
        family_base_commit="1" * 40,
        cycle_number=2,
        predecessor_run_id=RUN_ID,
        predecessor_head_record_id=completion.record_id,
        finding_transition_record_ids=(),
        finding_transitions_sha256=finding_transition_sequence_sha256(()),
        target_task_path="inbox/backlog/b40-discovery.md",
        target_task_sha256="8" * 64,
        target_run_identity="target-run",
        authority=Role.ORCHESTRATOR,
        target_execution_mode="BRANCH_DISCOVERY",
        source_completion_record_id=(
            completion.record_id
            if source_completion_record_id is None
            else source_completion_record_id
        ),
    )


def _side_effect(*, phase: str = "intent") -> SideEffectPayload:
    operation = ("b40-marker",)
    key = stable_side_effect_key("internal", "1", operation)
    return SideEffectPayload(
        key,
        "internal",
        "1",
        operation,
        phase,
        None if phase == "intent" else "completed",
    )


def _case(line: int) -> RejectionInput:  # noqa: C901, PLR0912, PLR0915
    records: list[ArtifactRecord] = []

    if line in {3001, 3002, 3003, 3004, 3005, 3013}:
        finding, review, attestation = _branch_export_prefix(records)
        if line == 3003:
            export_attestation = _append_attestation(
                records,
                fingerprint=FP_B,
                logical_id="validation-2",
            )
        else:
            export_attestation = attestation
        payload = _branch_export_payload(
            finding,
            review,
            export_attestation,
            family_id="wrong-family" if line == 3005 else "finding-family",
            transition_digest="9" * 64 if line == 3004 else None,
        )
        if line == 3001:
            payload = replace(payload, predecessor_run_id="wrong-run")
        elif line == 3002:
            payload = replace(
                payload,
                discovery_review_record_id="ar1-" + "9" * 64,
            )
        elif line == 3013:
            payload = replace(
                payload,
                validation_attestation_record_id="ar1-" + "9" * 64,
            )
        elif line == 3003:
            payload = replace(
                payload,
                source_head_record_id=export_attestation.record_id,
                predecessor_head_record_id=export_attestation.record_id,
            )
        if line != 3012:
            _append(records, payload)
    elif line in {3014, 3015}:
        attestation, completion = _branch_discovery_target_export_prefix(
            records,
            source_mode="BRANCH_DISCOVERY" if line == 3015 else "IMPLEMENT",
        )
        payload = _branch_discovery_target_export_payload(
            attestation,
            completion,
            source_completion_record_id=(
                "ar1-" + "9" * 64 if line == 3014 else None
            ),
        )
        if line == 3014:
            payload = replace(
                payload,
                predecessor_head_record_id="ar1-" + "9" * 64,
            )
        _append(records, payload)
    elif line in {3006, 3007, 3008, 3009, 3010, 3011, 3012, 3016}:
        payload = _branch_import_payload(
            target_run_id="wrong-run" if line == 3006 else RUN_ID,
            source_run_id=RUN_ID if line == 3007 else "source-run",
            snapshot_signature="9" * 64 if line == 3008 else None,
        )
        if line != 3012:
            _append(records, payload)
        if line in {3009, 3010, 3011, 3012, 3016}:
            _append(
                records,
                RunProfilePayload(
                    RoleProfilePayload("implementer", "medium"),
                    RoleProfilePayload("reviewer", "high"),
                    family_binding=_family_binding(
                        family_id=(
                            "wrong-family" if line == 3009 else "finding-family"
                        )
                    ),
                ),
            )
        if line == 3010:
            _append(
                records,
                TaskPayload("feature/b40", ("src/a.py",), "9" * 64),
            )
        elif line == 3011:
            _append(
                records,
                RunIdentityPayload(
                    "inbox/backlog/wrong.md",
                    "feature/b40",
                    "1" * 40,
                    "1" * 40,
                    "PLAN_ONLY",
                    None,
                ),
            )
            _append(
                records,
                TaskPayload("feature/b40", ("src/a.py",), "8" * 64),
            )
        elif line == 3012:
            _append(records, _measurement(operation="codex_plan"))
            _append(records, payload)
        elif line == 3016:
            _append(
                records,
                _with_joint_cutover(
                    lambda: RunIdentityPayload(
                        "inbox/backlog/b40-followup.md",
                        "feature/b40",
                        "1" * 40,
                        "1" * 40,
                        "BRANCH_DISCOVERY",
                        None,
                    )
                ),
            )
            _append(
                records,
                TaskPayload("feature/b40", ("src/a.py",), "8" * 64),
            )
    elif line in {3017, 3018, 3019}:
        _append(
            records,
            (
                RunIdentityPayload(
                    "inbox/b40.md",
                    "feature/b40",
                    "1" * 40,
                    "2" * 40,
                    "IMPLEMENT",
                    None,
                )
                if line == 3019
                else _with_joint_cutover(
                    lambda: RunIdentityPayload(
                        "inbox/b40.md",
                        "feature/b40",
                        "1" * 40,
                        "2" * 40,
                        "BRANCH_DISCOVERY",
                        None,
                    )
                )
            ),
        )
        attestation = _append_attestation(
            records,
            fingerprint=FP_B if line == 3018 else FP_A,
        )
        _append(
            records,
            _with_joint_cutover(
                lambda: BranchDiscoveryCompletedPayload(
                    reviewer=Role.CLAUDE,
                    work_unit_id="1",
                    new_findings=(),
                    occurrences=(),
                    review_evidence=ReviewEvidencePayload(
                        "all dimensions",
                        "residual risk",
                        "break condition",
                    ),
                    pre_mortem="pre mortem",
                    validation_attestation_record_id=(
                        "ar1-" + "9" * 64
                        if line == 3017
                        else attestation.record_id
                    ),
                    reviewed_head_commit="2" * 40,
                    transport_schema="native-claude-review-v2",
                    request_id="native-review-request-" + "c" * 64,
                    response_sha256=RESPONSE_SHA,
                    scan_complete=True,
                )
            ),
        )
    elif line == 1416:
        _append(records, _transition(slice_id="1"))
        _append(records, _transition(slice_id="2"))
    elif line == 1424:
        _append(records, WorkflowPolicyPayload("1", 0, 4))
    elif line == 1431:
        _append(records, SliceBoundaryPayload("1", "1" * 40, (("src/a.py",),), "2" * 64))
    elif line == 1445:
        _append(records, _transition())
        _append(records, SliceBoundaryPayload("1", "1" * 40, (("src/a.py",),), "2" * 64))
        _append(records, SliceBoundaryPayload("1", "3" * 40, (("src/a.py",),), "2" * 64))
    elif line == 1454:
        domain = _append(records, _transition())
        _event("transition", domain, work_unit_id="1", records=records)
        _event("transition", domain, work_unit_id="1", records=records)
    elif line == 1466:
        missing = _append(records, _transition())
        records.clear()
        _event("transition", missing, work_unit_id="1", records=records)
    elif line == 1483:
        domain = _append(records, _transition())
        _event("run", domain, work_unit_id=None, records=records)
    elif line == 1490:
        domain = _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "1" * 40, "IMPLEMENT", None))
        _event("run", domain, work_unit_id=None, fingerprint=FP_B, records=records)
    elif line == 1498:
        domain = _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "1" * 40, "IMPLEMENT", None))
        event = _event("run", domain, work_unit_id=None, records=records)
        object.__setattr__(event.payload, "work_unit_id", "1")
    elif line == 1508:
        domain = _append(records, _transition())
        _event("transition", domain, work_unit_id="1", slice_id="2", records=records)
    elif line == 1515:
        domain = _append(records, _review("1"), logical_id="review-claude-1-1")
        _event("review", domain, work_unit_id="2", round_number=1, records=records)
    elif line == 1530:
        _append(records, _transition())
        _append_failure(records, _failure(), logical_id="malformed")
    elif line == 1540:
        _append_failure(records, _failure())
    elif line == 1557:
        _append(records, _transition(step="codex_plan"))
        _append_failure(records, _failure())
    elif line == 1566:
        _append(records, _transition())
        _append_failure(records, _failure(fingerprint="b" * 64))
    elif line == 1572:
        _append(records, _transition())
        failure = _failure()
        _append_failure(records, failure)
        _append_failure(records, failure, revision=2)
    elif line == 1585:
        _append(records, QuotaPausePayload(Role.CODEX, "a" * 64, "2026-09-03T10:01:00+00:00"), logical_id="malformed")
    elif line == 1593:
        _append(records, QuotaPausePayload(Role.CODEX, "a" * 64, "2026-09-03T10:01:00+00:00"), logical_id="quota-pause-missing")
    elif line == 1616:
        _append(records, _transition())
        _append_failure(records, _failure())
        _append(records, QuotaPausePayload(Role.CODEX, "a" * 64, "2026-09-03T10:02:00+00:00"), logical_id="quota-pause-invocation-1")
    elif line in {1627, 1635, 1645}:
        gate = _gate_transition()
        logical_id = "gate-transition-1"
        if line == 1627:
            logical_id = "gate-transition-wrong"
        gate_record = _append(records, gate, logical_id=logical_id)
        if line == 1635:
            object.__setattr__(
                gate_record.payload,
                "active_test_paths",
                ("tests/test_b40.py",),
            )
    elif line in {1657, 1667, 1673, 1679, 1686}:
        transition = _append(records, _transition())
        _ = transition
        gate_record: ArtifactRecord | None = None
        if line != 1657:
            gate_record = _append(
                records,
                GatePayload("b40", "pending" if line == 1673 else "approved", Role.USER, "reviewed"),
            )
        gate_id = gate_record.record_id if gate_record is not None else "ar1-" + "4" * 64
        if line == 1667:
            gate_id = "ar1-" + "4" * 64
        decision_fp = FP_B if line == 1679 else FP_A
        _append(records, GateDecisionPayload("404" if line == 1657 else "1", gate_id, (), None), fingerprint=decision_fp)
        if line == 1686:
            _append(records, GateDecisionPayload("1", gate_id, (), None), revision=2)
    elif line == 1706:
        target = "ar1-" + "4" * 64
        output, _ = _validation_parts()
        for index in range(2):
            _append(records, ValidationContentPayload(f"validation-{index}", target, "validation-matrix-v1", "e" * 64, "duplicate result", (output,)))
    elif line == 1716:
        _append_attestation(records)
        return RejectionInput(tuple(records), require_content_authority=True)
    elif line in {1733, 1751}:
        content, attestation = _append_validation_pair(records)
        if line == 1733:
            object.__setattr__(attestation.payload, "content_record_id", "ar1-" + "4" * 64)
        else:
            result = attestation.payload.results[0]
            object.__setattr__(attestation.payload, "results", (replace(result, output_sha256="4" * 64),))
        return RejectionInput(tuple(records), require_content_authority=True)
    elif line == 1761:
        output, _ = _validation_parts()
        _append(records, ValidationContentPayload("validation-1", "ar1-" + "4" * 64, "validation-matrix-v1", "e" * 64, "orphan", (output,)))
        _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "1" * 40, "IMPLEMENT", None))
    elif line == 1781:
        payload = _agent()
        decision = _append(records, payload)
        object.__setattr__(decision.payload, "request_id", None)
    elif line == 1814:
        _append(records, _agent(), logical_id="agent-without-round")
        return RejectionInput(tuple(records), require_content_authority=True)
    elif line == 1834:
        _append(records, _agent(), logical_id="agent-1-codex_implementation-1")
        return RejectionInput(tuple(records), require_content_authority=True)
    elif line == 1864:
        _provider_pair(records, content_kind="final_report")
    elif line == 1874:
        blob = BlobReference(RESPONSE_SHA, 3)
        _append(records, ProviderContentPayload(Role.CODEX, "1", 1, "codex_implementation", REQUEST_ID, RESPONSE_SHA, "agent_result", 3, blob))
        _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "1" * 40, "IMPLEMENT", None))
    elif line == 1906:
        _append(records, ReviewAnchorPayload("ar1-" + "4" * 64, ()), logical_id="review-anchors-missing")
    elif line == 1912:
        review = _append(records, _review(), logical_id="review-claude-1-1")
        _append_review_anchor(records, review)
        _append_review_anchor(records, review, revision=2)
    elif line == 1937:
        _append(records, ReviewValidationBindingPayload("ar1-" + "4" * 64, "ar1-" + "5" * 64), logical_id="review-validation-missing")
    elif line in {1946, 1952}:
        attestation = _append_attestation(records)
        review = _append(records, _review(), logical_id="review-claude-1-1")
        _append_review_validation(records, review, attestation, fingerprint=FP_B if line == 1946 else FP_A)
        if line == 1952:
            _append_review_validation(records, review, attestation, revision=2)
    elif line in {2003, 2009, 2014}:
        attestation: ArtifactRecord | None = None
        if line == 2014:
            attestation = _append_attestation(records)
        review = _append(records, _review(finding_ids=("C-01",) if line == 2014 else ()), logical_id="review-claude-1-1")
        if line in {2009, 2014}:
            _append_review_anchor(records, review)
        if line == 2014:
            assert attestation is not None
            _append_review_validation(records, review, attestation)
        return RejectionInput(tuple(records), require_review_authority=True)
    elif line == 2029:
        blob = BlobReference("6" * 64, 3)
        _append(records, ReviewPacketPayload("1", "a" * 64, "slice", ("src/a.py",), "7" * 64, 3, blob), logical_id="review-packet-wrong", fingerprint=FP_A)
    elif line == 2059:
        _append(records, WorkUnitPayload("1", 2, ("src/a.py",)), logical_id="work-unit-1")
        _append(records, WorkUnitPayload("2", 1, ("src/a.py",)), logical_id="work-unit-1", revision=2)
    elif line == 2080:
        _append(records, _import_payload())
        _append(records, _import_payload(source_run_id="source-run-2"))
    elif line == 2093:
        previous = _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "1" * 40, "IMPLEMENT", None))
        _append(records, _export_payload(source_head=previous.record_id, approval_review_id="ar1-" + "4" * 64, source_run_id="wrong-run"))
    elif line == 2105:
        previous = _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "1" * 40, "IMPLEMENT", None))
        _append(records, _export_payload(source_head=previous.record_id, approval_review_id="ar1-" + "4" * 64))
    elif line == 2120:
        review = _append(records, _review(), logical_id="review-claude-1-1")
        _append(records, _export_payload(source_head=review.record_id, approval_review_id=review.record_id, transition_ids=("ar1-" + "4" * 64,)))
    elif line in {2139, 2149}:
        finding = _append(records, _finding_transition())
        review = _append(records, _review(), logical_id="review-claude-1-1")
        imported = ImportedFindingTransition(finding.record_id, finding.payload)
        digest = finding_transition_sequence_sha256((imported,))
        _append(records, _export_payload(source_head=review.record_id, approval_review_id=review.record_id, transition_ids=(finding.record_id,), transition_digest="9" * 64 if line == 2139 else digest))
    elif line == 2156:
        _append(records, _import_payload(target_run_id="another-run"))
    elif line == 2162:
        _append(records, _import_payload(source_run_id=RUN_ID))
    elif line == 2177:
        _append(records, WorkUnitPayload("1", 1, ("src/a.py",), (), "ar1-" + "4" * 64), logical_id="work-unit-1")
    elif line == 2199:
        imported = _append(records, _import_payload())
        _append(records, WorkUnitPayload("1", 1, ("src/a.py",), (), imported.record_id), logical_id="work-unit-1")
    elif line == 2228:
        _append(records, WorkUnitPayload("1", 1, ("src/a.py",)), logical_id="work-unit-1")
        from artifact_models import DiagnosticPayload
        _append(records, DiagnosticPayload(Role.CODEX, "404", 1, "a" * 64, "missing work unit"))
    elif line == 2240:
        _append(records, BindingPayload("commit", "deadbeef", "ar1-" + "4" * 64, ("ar1-" + "5" * 64,)))
    elif line == 2245:
        attestation = _append_attestation(records)
        _append(
            records,
            BindingPayload(
                "commit",
                "deadbeef",
                attestation.record_id,
                ("ar1-" + "5" * 64,),
            ),
            fingerprint=FP_B,
        )
    elif line == 2254:
        attestation = _append_attestation(records)
        _append(records, BindingPayload("commit", "deadbeef", attestation.record_id, ("ar1-" + "4" * 64,)))
    elif line == 2259:
        attestation = _append_attestation(records)
        review = _append(
            records,
            _review(),
            logical_id="review-claude-1-1",
            fingerprint=FP_B,
        )
        _append(
            records,
            BindingPayload(
                "commit", "deadbeef", attestation.record_id, (review.record_id,)
            ),
        )
    elif line == 2267:
        _append(records, WorkflowCompletionPayload("completed", "ar1-" + "4" * 64))
    elif line == 2272:
        attestation = _append_attestation(records)
        review = _append(records, _review(), logical_id="review-claude-1-1")
        binding = _append(
            records,
            BindingPayload(
                "commit", "deadbeef", attestation.record_id, (review.record_id,)
            ),
        )
        _append(
            records,
            WorkflowCompletionPayload("completed", binding.record_id),
            fingerprint=FP_B,
        )
    elif line == 2280:
        _append(records, FinalReviewPreflightPayload(Role.CODEX, Role.CODEX, "codex_final_review", "1", "a" * 64, "b" * 64, "missing-measurement", "passed", None, None, (), (), None))
    elif line == 2285:
        measurement = _append(records, _measurement())
        _append(
            records,
            FinalReviewPreflightPayload(
                Role.CODEX,
                Role.CODEX,
                "codex_final_review",
                "1",
                "a" * 64,
                "b" * 64,
                measurement.record_id,
                "passed",
                None,
                None,
                (),
                (),
                None,
            ),
            fingerprint=FP_B,
        )
    elif line == 2298:
        measurement = _append(records, _measurement())
        _append(records, _attempt(measurement), fingerprint=FP_B)
    elif line in {2293, 2307, 2331, 2340, 2345, 2354, 2360, 2371}:
        if line == 2293:
            fake = ArtifactRecord.create(run_id=RUN_ID, logical_id="measurement-missing", revision=1, fingerprint=FP_A, predecessor_ids=(), created_at="2026-09-03T09:00:00+00:00", idempotency_key="missing", payload=_measurement())
            _append(records, _attempt(fake))
        elif line == 2307:
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement, operation="codex_plan"))
        elif line == 2331:
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement, attempt_number=2))
        elif line == 2340:
            measurement = _append(records, _measurement())
            for index in range(3):
                _append(records, _attempt(measurement), logical_id=f"attempt-{index}", revision=index + 1)
        elif line == 2345:
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement, phase="succeeded"), revision=1)
        elif line == 2354:
            first_measurement = _append(records, _measurement(operation="codex_implementation"))
            _append(records, _attempt(first_measurement, operation="codex_implementation", attempt_number=1))
            second_measurement = _append(records, _measurement(operation="codex_plan", input_digest="2" * 64))
            _append(records, _attempt(second_measurement, operation="codex_plan", attempt_number=2))
        elif line == 2360:
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement), logical_id="attempt-start", revision=1)
            _append(records, _attempt(measurement), logical_id="attempt-terminal", revision=2)
        else:
            first_measurement = _append(records, _measurement(operation="codex_implementation"))
            _append(records, _attempt(first_measurement), logical_id="attempt-start", revision=1)
            second_measurement = _append(records, _measurement(operation="codex_plan", input_digest="2" * 64))
            _append(records, _attempt(second_measurement, operation="codex_plan", phase="succeeded"), logical_id="attempt-terminal", revision=2)
    elif line in {2379, 2388, 2395, 2405, 2418}:
        intent_payload = _side_effect()
        expected_logical = "side-effect-" + hashlib.sha256(intent_payload.effect_key.encode("utf-8")).hexdigest()[:32]
        if line == 2379:
            for index in range(3):
                _append(records, intent_payload, logical_id=f"effect-{index}", revision=index + 1)
        elif line == 2388:
            _append(records, _side_effect(phase="result"), logical_id=expected_logical)
        elif line == 2395:
            _append(records, intent_payload, logical_id="side-effect-wrong")
        else:
            _append(records, intent_payload, logical_id=expected_logical)
            if line == 2405:
                _append(records, intent_payload, logical_id=expected_logical, revision=2)
            else:
                _append(records, _side_effect(phase="result"), logical_id="side-effect-other", revision=2)
    elif line == 2315:
        _append(records, ResumeCheckPayload("ar1-" + "4" * 64, "a" * 64, "matched"))
    else:
        raise AssertionError(f"missing B40 rejection input for source line {line}")

    return RejectionInput(tuple(records))


def _load_baseline() -> dict[str, object]:
    document = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert set(document) == {"schema_version", "entries", "unreachable"}
    assert document["schema_version"] == "replay-rejection-corpus-v1"
    unreachable = document["unreachable"]
    assert isinstance(unreachable, list)
    assert all(
        isinstance(entry, dict)
        and set(entry) == {"line", "barrier", "reason"}
        and isinstance(entry["line"], int)
        and isinstance(entry["barrier"], str)
        and isinstance(entry["reason"], str)
        for entry in unreachable
    )
    entries = document["entries"]
    assert isinstance(entries, list)
    unreachable_case_ids = {"site-1498", "site-1635", "site-1781"}
    assert {entry["line"] for entry in unreachable} == {
        entry["line"]
        for entry in entries
        if isinstance(entry, dict) and entry.get("case_id") in unreachable_case_ids
    }
    return document


def _entries() -> tuple[dict[str, object], ...]:
    entries = _load_baseline()["entries"]
    assert isinstance(entries, list) and len(entries) == 100
    assert all(
        isinstance(entry, dict)
        and set(entry) == {"case_id", "input", "code", "line", "message"}
        and isinstance(entry["case_id"], str)
        and isinstance(entry["input"], str)
        and isinstance(entry["code"], str)
        and isinstance(entry["line"], int)
        and isinstance(entry["message"], str)
        for entry in entries
    )
    return tuple(entries)


def _capture(
    validator: Callable[..., str | None], case: RejectionInput
) -> tuple[ArtifactReplayError, int]:
    try:
        validator(
            case.records,
            {record.record_id: record for record in case.records},
            require_content_authority=case.require_content_authority,
            require_review_authority=case.require_review_authority,
            allow_incomplete_review_tail=case.allow_incomplete_review_tail,
        )
    except ArtifactReplayError as error:
        traceback: TracebackType | None = error.__traceback__
        call_lines: list[int] = []
        emission_lines = {line for line, _ in _source_emissions()}
        while traceback is not None:
            if traceback.tb_lineno in emission_lines:
                call_lines.append(traceback.tb_lineno)
            traceback = traceback.tb_next
        assert len(call_lines) == 1
        return error, call_lines[0]
    raise AssertionError("rejection corpus input was unexpectedly accepted")


def _assert_entry(
    entry: dict[str, object],
    *,
    validator: Callable[..., str | None] = artifact_replay._validate_payload_references,
) -> None:
    case_id = str(entry["case_id"])
    assert case_id.startswith("site-")
    error, line = _capture(validator, _case(int(case_id.removeprefix("site-"))))
    assert error.code.value == entry["code"]
    assert error.diagnostic.message == entry["message"]
    assert line == entry["line"]


def _source_emissions() -> tuple[tuple[int, str], ...]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    functions = tuple(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and (node.name.startswith("_validate_") or node.name == "_index_validation_content")
    )
    emissions = []
    for function in functions:
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id == "_fail":
                code = node.args[0]
                assert isinstance(code, ast.Attribute)
                emissions.append((node.lineno, code.attr.replace("_", "-")))
            elif node.func.id == "_same_fingerprint":
                emissions.append((node.lineno, "RECORD-FINGERPRINT-MISMATCH"))
    return tuple(sorted(emissions))


def test_rejection_corpus_baseline_is_complete_and_source_bound() -> None:
    entries = _entries()
    expected = tuple((int(entry["line"]), str(entry["code"])) for entry in entries)
    assert _source_emissions() == expected
    assert len({entry["case_id"] for entry in entries}) == 100
    assert len({entry["line"] for entry in entries}) == 100
    assert Counter(entry["code"] for entry in entries) == {
        "RECORD-FINGERPRINT-MISMATCH": 45,
        "RECORD-REFERENCE-MISSING": 31,
        "RECORD-DUPLICATE": 9,
        "RECORD-MISSING": 9,
        "RECORD-RUN-MISMATCH": 2,
        "RECORD-TYPE-MISMATCH": 4,
    }
    # All 100 rejection sites are reducer-reachable. Three deliberately duplicated
    # defences cannot be produced by schema-valid persisted bytes; their cases
    # mutate an already validated object so the independent reducer check stays
    # executable and their ingress unreachability remains explicit.
    assert len(_load_baseline()["unreachable"]) == 3


def test_replay_validator_has_exact_named_pass_order_and_split_chain_pass() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    validator = functions["_validate_payload_references"]
    pass_calls = tuple(
        node.func.id
        for node in sorted(
            (
                child
                for child in ast.walk(validator)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id in VALIDATION_PASSES
            ),
            key=lambda child: child.lineno,
        )
    )
    assert pass_calls == VALIDATION_PASSES

    chain_pass = functions["_validate_chain_record_references"]
    split_calls = tuple(
        node.func.id
        for node in sorted(
            (
                child
                for child in ast.walk(chain_pass)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id.startswith("_validate_")
            ),
            key=lambda child: child.lineno,
        )
    )
    assert split_calls == (
        "_validate_finding_handoff_record",
        "_validate_work_unit_finding_import",
        "_validate_work_unit_activity_reference",
        "_validate_bound_record_references",
    )


@pytest.mark.parametrize("entry", _entries(), ids=lambda entry: entry["case_id"])
def test_every_reachable_rejection_emission_has_a_bound_input(
    entry: dict[str, object],
) -> None:
    _assert_entry(entry)


def test_equal_code_site_swap_is_detected_by_the_corpus() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    entries = {str(entry["case_id"]): entry for entry in _entries()}
    left_entry = entries["site-1424"]
    right_entry = entries["site-1431"]
    target_lines = {int(left_entry["line"]), int(right_entry["line"])}
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and target_lines.issubset(
            {
                child.lineno
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "_fail"
            }
        )
    )
    calls = {
        node.lineno: node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_fail"
    }
    left, right = calls[int(left_entry["line"])], calls[int(right_entry["line"])]
    left.args[1], right.args[1] = right.args[1], left.args[1]
    validator_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_validate_payload_references"
    )
    module = ast.Module(body=[function, validator_function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = dict(vars(artifact_replay))
    exec(compile(module, "<b40-equal-code-site-swap>", "exec"), namespace)
    mutant = namespace["_validate_payload_references"]

    for entry, other_entry in (
        (left_entry, right_entry),
        (right_entry, left_entry),
    ):
        case_line = int(str(entry["case_id"]).removeprefix("site-"))
        error, actual_line = _capture(mutant, _case(case_line))
        assert error.code.value == entry["code"]
        assert actual_line == entry["line"]
        assert error.diagnostic.message == other_entry["message"]
        assert error.diagnostic.message != entry["message"]


def test_validation_pass_order_swap_is_detected() -> None:
    records: list[ArtifactRecord] = []
    _append(
        records,
        ReviewPacketPayload(
            "1",
            "a" * 64,
            "slice",
            ("src/a.py",),
            "7" * 64,
            3,
            BlobReference("6" * 64, 3),
        ),
        logical_id="review-packet-wrong",
    )
    _append(records, _import_payload())
    _append(records, _import_payload(source_run_id="source-run-2"))
    case = RejectionInput(tuple(records))

    original_error, _ = _capture(
        artifact_replay._validate_payload_references, case
    )
    assert (
        original_error.diagnostic.message
        == "review packet record identity differs from its content binding"
    )

    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    validator_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_validate_payload_references"
    )
    pass_calls = {
        node.value.func.id: index
        for index, node in enumerate(validator_function.body)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    }
    packet_index = pass_calls["_validate_review_packet_bindings"]
    import_index = pass_calls["_validate_single_finding_import"]
    validator_function.body[packet_index], validator_function.body[import_index] = (
        validator_function.body[import_index],
        validator_function.body[packet_index],
    )
    module = ast.Module(body=[validator_function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = dict(vars(artifact_replay))
    exec(compile(module, "<b41-validation-pass-order-swap>", "exec"), namespace)
    mutant_error, _ = _capture(namespace["_validate_payload_references"], case)
    assert mutant_error.code is ReplayDiagnosticCode.RECORD_DUPLICATE
    assert (
        mutant_error.diagnostic.message
        == "a run may contain only one finding handoff import"
    )
    assert mutant_error.diagnostic.message != original_error.diagnostic.message


def test_valid_reference_chain_remains_accepted() -> None:
    records: list[ArtifactRecord] = []
    _append(records, _transition())
    assert artifact_replay._validate_payload_references(
        tuple(records),
        {record.record_id: record for record in records},
        require_content_authority=False,
        require_review_authority=False,
        allow_incomplete_review_tail=False,
    ) is None
