from __future__ import annotations

from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest

import native_finding_decisions
from artifact_bridge import (
    ArtifactBridgeError,
    branch_discovery_completed_payload,
    branch_discovery_handoff_export_payload,
    closed_finding_occurrence_payloads,
    finding_handoff_export_payload,
    no_implementation_required_payload,
)
from artifact_models import (
    ArtifactRecord,
    BranchDiscoveryHandoffImportPayload,
    ClosedFindingDispositionSnapshot,
    CommandSpec,
    FamilyBindingPayload,
    FindingSeverity,
    FindingTransitionPayload,
    Fingerprint,
    FingerprintKind,
    NoImplementationRequiredPayload,
    PlanAssignmentPayload,
    PlanPayload,
    PlanTreatmentAssignment,
    ReviewEvidencePayload,
    ReviewPayload,
    Role,
    RunIdentityPayload,
    SliceSpec,
    ValidationAttestationPayload,
    ValidationResult,
    WorkflowCompletionPayload,
    finding_transition_sequence_sha256,
)
from artifact_replay import ArtifactReplayError
from contracts import (
    AgentRole,
    ApprovalMarker,
    CodexStepContract,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ReadinessMarker,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from finding_reducer import reduce_finding_records
from finding_signature import finding_record_signature
from native_codex_contract import (
    BoundNativeCodexContext,
    NativeCodexContext,
    NativeCodexRequestKind,
    parse_bound_native_codex_contract_result,
)
from native_finding_decisions import (
    ClosedFindingReviewBinding,
    NativeRejectionReason,
    NoCodeEvidenceAnchor,
    content_path_digest,
)
from native_review_contract import (
    NativeReviewContext,
    NativeReviewContractError,
    native_review_provider_response_schema,
    parse_native_contract_result,
)
from schema_validation import validate_schema_document


FINGERPRINT = Fingerprint(FingerprintKind.CONTRACT, "a" * 64)
PLAN_COMMIT = "d" * 40


@pytest.fixture(autouse=True)
def active_joint_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )


def _finding(*, closed: bool = False) -> FindingRecord:
    return FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.CLOSED if closed else FindingStatus.OPEN,
        summary="The defect affects src/fix.py.",
        acceptance_test="Repair src/fix.py and preserve its contract.",
        origin=FindingOrigin("discovery", 1, AgentRole.CLAUDE),
        status_rationale="The reviewed evidence rejects the defect." if closed else None,
    )


def _validation() -> ValidationAttestation:
    command = ("python3", "-m", "pytest", "tests/", "-v")
    return ValidationAttestation(
        attestation_id="validation-discovery",
        diff_fingerprint="a" * 64,
        expected_commands=("python3 -m pytest tests/ -v",),
        records=(
            ValidationRecord(
                ValidationStatus.PASS,
                "python3 -m pytest tests/ -v",
                0,
                "passed",
            ),
        ),
        output_digest=hashlib.sha256(b"passed").hexdigest(),
        summary="passed",
        command_specs=(ValidationCommandSpec(command),),
    )


def _anchor(
    reason: NativeRejectionReason,
    *,
    provenance: str = "a" * 64,
    evidence_digest: str = "b" * 64,
) -> NoCodeEvidenceAnchor:
    common = {
        "rejection_reason": reason,
        "provenance_fingerprint": provenance,
        "evidence_paths": ("tests/evidence.txt",),
        "evidence_content_sha256": evidence_digest,
    }
    if reason is NativeRejectionReason.OUT_OF_SCOPE:
        return NoCodeEvidenceAnchor(
            **common,
            task_sha256="c" * 64,
            scope_sha256="d" * 64,
        )
    if reason is NativeRejectionReason.ALREADY_FIXED:
        return NoCodeEvidenceAnchor(
            **common,
            affected_paths=("src/fix.py",),
            affected_content_sha256="e" * 64,
        )
    return NoCodeEvidenceAnchor(**common)


def _record(
    records: list[ArtifactRecord],
    logical_id: str,
    payload: object,
) -> ArtifactRecord:
    record = ArtifactRecord.create(
        run_id="plan-run",
        logical_id=logical_id,
        revision=1,
        fingerprint=FINGERPRINT,
        predecessor_ids=((records[-1].record_id,) if records else ()),
        created_at=f"2026-09-19T10:00:{len(records):02d}+00:00",
        idempotency_key=f"plan-run:{logical_id}",
        payload=payload,  # type: ignore[arg-type]
    )
    records.append(record)
    return record


def _no_code_plan_records(
    *, close_finding: bool
) -> tuple[list[ArtifactRecord], ArtifactRecord, ArtifactRecord]:
    records: list[ArtifactRecord] = []
    finding = _finding()
    signature = finding_record_signature(finding)
    anchor = _anchor(NativeRejectionReason.NO_DEFECT)
    _record(
        records,
        "finding-open",
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="The discovery review opened the finding.",
            work_unit_id="discovery",
            summary=finding.summary,
            acceptance_test=finding.acceptance_test,
            origin_slice_id="discovery",
            origin_round_number=1,
        ),
    )
    if close_finding:
        _record(
            records,
            "finding-close",
            FindingTransitionPayload(
                finding_id="C-01",
                reporter=Role.CLAUDE,
                actor=Role.CLAUDE,
                action="status_changed",
                severity=FindingSeverity.BLOCKER,
                finding_status="closed",
                rationale="The reviewed evidence establishes no defect.",
                work_unit_id="plan",
                closure_kind="rejected",
                rejection_reason="no_defect",
                closure_evidence="tests/evidence.txt is authoritative.",
            ),
        )
    _record(
        records,
        "plan",
        PlanPayload(
            "docs/internal/no-code-plan.md",
            PLAN_COMMIT,
            (SliceSpec("1", "Review the no-code plan", ("docs/internal/no-code-plan.md",)),),
        ),
    )
    review = _record(
        records,
        "plan-review",
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="plan",
            verdict="approved",
            finding_ids=("C-01",),
            evidence=None,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "1" * 64,
            response_sha256="2" * 64,
            review_evidence=ReviewEvidencePayload(
                "No-Code evidence and closure checked.",
                "The evidence file could later change.",
                "A content-digest change invalidates the disposition.",
            ),
            pre_mortem="A Finding could remain open accidentally.",
        ),
    )
    assignment = _record(
        records,
        "plan-assignment",
        PlanAssignmentPayload(
            family_id="family-1",
            cycle_number=2,
            remediation_round_number=1,
            source_snapshot_record_id="ar1-" + "3" * 64,
            finding_snapshot_sha256="4" * 64,
            plan_result_record_id="ar1-" + "5" * 64,
            review_record_id=review.record_id,
            review_fingerprint=FINGERPRINT.sha256,
            treatments=(
                PlanTreatmentAssignment(
                    signature=signature,
                    finding_ids=("C-01",),
                    treatment_kind="no_code",
                    no_code_reason="no_defect",
                    evidence="tests/evidence.txt establishes the intended behavior.",
                    authoritative_fingerprint=FINGERPRINT.sha256,
                    evidence_anchor=anchor,
                ),
            ),
            slices=(),
            implementation_scope=(),
            authority=Role.CLAUDE,
            plan_completion="NO_IMPLEMENTATION_REQUIRED",
        ),
    )
    return records, review, assignment


def test_native_plan_can_finish_explicitly_without_an_implementation_slice() -> None:
    finding = _finding()
    signature = finding_record_signature(finding)
    context = NativeCodexContext(
        run_id="plan-run",
        work_unit_id="plan",
        operation="codex_plan",
        current_fingerprint="a" * 64,
        request_kind=NativeCodexRequestKind.PLAN,
        contract=CodexStepContract(
            name="plan",
            readiness_marker=ReadinessMarker.PLAN,
            slice_id="PLAN",
            round_number=1,
            require_slice_plan=True,
            plan_artifact_path="docs/internal/no-code-plan.md",
        ),
        previous_findings=(finding,),
    )
    bound = BoundNativeCodexContext(
        context,
        "native-codex-request-" + "6" * 64,
        "6" * 64,
    )
    result = parse_bound_native_codex_contract_result(
        {
            "schema_version": "native-agent-codex-result-v2",
            "result_type": "plan_result",
            "request_id": bound.request_id,
            "ready": True,
            "slice_plan": [],
            "finding_dispositions": [],
            "plan_treatments": [
                {
                    "signature": signature,
                    "finding_ids": ["C-01"],
                    "treatment_kind": "no_code",
                    "no_code_reason": "no_defect",
                    "evidence": "tests/evidence.txt establishes the intended behavior.",
                    "evidence_paths": ["tests/evidence.txt"],
                    "affected_paths": [],
                }
            ],
            "plan_completion": "NO_IMPLEMENTATION_REQUIRED",
        },
        bound,
    )

    assert result.slice_plan == ()
    assert result.plan_completion is not None
    assert result.plan_completion.value == "NO_IMPLEMENTATION_REQUIRED"


def test_no_implementation_completion_requires_every_finding_closed_and_blocks_implement() -> None:
    records, review, assignment = _no_code_plan_records(close_finding=True)
    replay = SimpleNamespace(records=tuple(records))
    completion = no_implementation_required_payload(
        replay,  # type: ignore[arg-type]
        plan_assignment_record_id=assignment.record_id,
        reviewed_plan_commit=PLAN_COMMIT,
    )
    completion_record = _record(records, "no-implementation", completion)
    accepted = SimpleNamespace(
        records=tuple(records),
        head_record_id=completion_record.record_id,
    )

    assert isinstance(completion, NoImplementationRequiredPayload)
    assert completion.closed_finding_ids == ("C-01",)
    with pytest.raises(ArtifactBridgeError, match="forbids an IMPLEMENT handoff"):
        finding_handoff_export_payload(
            accepted,  # type: ignore[arg-type]
            approved_plan_commit=PLAN_COMMIT,
            approval_review_record_id=review.record_id,
            target_task_path="inbox/doing/implementation.md",
            target_task_bytes=b"implementation",
        )

    open_records, _, open_assignment = _no_code_plan_records(close_finding=False)
    with pytest.raises(ArtifactBridgeError, match="every offered Finding"):
        no_implementation_required_payload(
            SimpleNamespace(records=tuple(open_records)),  # type: ignore[arg-type]
            plan_assignment_record_id=open_assignment.record_id,
            reviewed_plan_commit=PLAN_COMMIT,
        )


def test_no_code_plan_hands_only_to_head_exact_branch_discovery() -> None:
    records, _, assignment = _no_code_plan_records(close_finding=True)
    completion = no_implementation_required_payload(
        SimpleNamespace(records=tuple(records)),  # type: ignore[arg-type]
        plan_assignment_record_id=assignment.record_id,
        reviewed_plan_commit=PLAN_COMMIT,
    )
    _record(records, "no-implementation", completion)
    command = CommandSpec("pytest", ("python3", "-m", "pytest"))
    attestation = _record(
        records,
        "validation",
        ValidationAttestationPayload(
            (ValidationResult(command, "pass", 0, "7" * 64),),
            Role.ORCHESTRATOR,
            "7" * 64,
            "ar1-" + "8" * 64,
        ),
    )
    workflow_completion = _record(
        records,
        "workflow-completion",
        WorkflowCompletionPayload("completed", "plan-no-code"),
    )
    replay = SimpleNamespace(
        records=tuple(records),
        head_record_id=workflow_completion.record_id,
        expected_run_id="plan-run",
        run_identity=RunIdentityPayload(
            "inbox/doing/plan.md",
            "feature/finding-entscheidung-in-der-slice",  # allowlist:german
            "9" * 40,
            PLAN_COMMIT,
            "PLAN_ONLY",
            None,
        ),
    )
    family = FamilyBindingPayload(
        "family-1",
        "9" * 40,
        ("src/fix.py",),
        "plan-run",
        workflow_completion.record_id,
        3,
        PLAN_COMMIT,
        None,
    )
    arguments = {
        "discovery_review_record_id": None,
        "validation_attestation_record_id": attestation.record_id,
        "reviewed_head_commit": PLAN_COMMIT,
        "family_binding": family,
        "target_task_path": "inbox/doing/discovery.md",
        "target_task_bytes": b"ORCHESTRATOR_MODE: BRANCH_DISCOVERY\n",
        "target_run_identity": "discovery-after-no-code",
        "target_execution_mode": "BRANCH_DISCOVERY",
        "source_completion_record_id": workflow_completion.record_id,
    }

    handoff = branch_discovery_handoff_export_payload(
        replay,  # type: ignore[arg-type]
        **arguments,
    )
    assert handoff.target_execution_mode == "BRANCH_DISCOVERY"
    assert handoff.reviewed_head_commit == PLAN_COMMIT
    assert handoff.remediation_cohort_checkpoint_record_id is None
    with pytest.raises(ArtifactBridgeError, match="new reviewed plan HEAD"):
        branch_discovery_handoff_export_payload(
            replay,  # type: ignore[arg-type]
            **{**arguments, "reviewed_head_commit": "f" * 40},
        )
    with pytest.raises(ArtifactBridgeError, match="must hand off"):
        branch_discovery_handoff_export_payload(
            replay,  # type: ignore[arg-type]
            **{
                **arguments,
                "target_execution_mode": "PLAN_ONLY",
                "source_completion_record_id": None,
            },
        )


@pytest.mark.parametrize(
    "reason",
    tuple(NativeRejectionReason),
)
def test_evidence_anchor_is_reason_complete_and_ignores_only_provenance(
    reason: NativeRejectionReason,
) -> None:
    original = _anchor(reason, provenance="a" * 64)
    next_head = _anchor(reason, provenance="f" * 64)
    changed = _anchor(reason, provenance="f" * 64, evidence_digest="0" * 64)

    assert original.stability_sha256 == next_head.stability_sha256
    assert original.stability_sha256 != changed.stability_sha256
    assert content_path_digest(
        ("tests/evidence.txt",),
        {"tests/evidence.txt": "b" * 64},
    ) == content_path_digest(
        ("tests/evidence.txt",),
        {"tests/evidence.txt": "b" * 64},
    )


def test_reason_specific_evidence_anchor_fields_are_mandatory() -> None:
    common = {
        "provenance_fingerprint": "a" * 64,
        "evidence_paths": ("tests/evidence.txt",),
        "evidence_content_sha256": "b" * 64,
    }
    with pytest.raises(ValueError, match="task digest"):
        NoCodeEvidenceAnchor(
            NativeRejectionReason.OUT_OF_SCOPE,
            **common,
        )
    with pytest.raises(ValueError, match="affected paths"):
        NoCodeEvidenceAnchor(
            NativeRejectionReason.ALREADY_FIXED,
            **common,
        )


def _discovery_context(
    original: NoCodeEvidenceAnchor,
    current: NoCodeEvidenceAnchor,
) -> NativeReviewContext:
    finding = _finding(closed=True)
    return NativeReviewContext(
        run_id="discovery-run",
        work_unit_id="discovery",
        operation="claude_final_review",
        diff_fingerprint="a" * 64,
        reviewer=AgentRole.CLAUDE,
        approval_marker=ApprovalMarker.BRANCH_DISCOVERY,
        slice_id="FINAL",
        round_number=1,
        previous_findings=(finding,),
        validation_attestation=_validation(),
        anchor_origin=None,
        max_new_findings=10,
        closed_finding_bindings=(
            ClosedFindingReviewBinding(
                "C-01",
                finding_record_signature(finding),
                "ar1-" + "3" * 64,
                original,
                current,
            ),
        ),
    )


def _discovery_document(context: NativeReviewContext) -> dict[str, object]:
    return {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "branch_discovery_completed",
        "request_id": context.request_id,
        "reviewer": "claude",
        "scan_complete": True,
        "new_findings": [],
        "occurrences": [],
        "review_evidence": {
            "dimensions": "Closed signatures, evidence anchors, and lineage checked.",
            "largest_residual_risk": "A future evidence source could be omitted.",
            "break_condition": "A changed content digest is treated as unchanged.",
        },
        "pre_mortem": "A regression could be suppressed as a prior occurrence.",
    }


def test_stable_closed_signature_becomes_typed_occurrence_and_stays_closed() -> None:
    original = _anchor(NativeRejectionReason.NO_DEFECT)
    context = _discovery_context(
        original,
        _anchor(NativeRejectionReason.NO_DEFECT, provenance="f" * 64),
    )
    document = _discovery_document(context)
    document["occurrences"] = [
        {
            "finding_id": "C-01",
            "rationale": "The same closed condition is visible on the new HEAD.",
            "evidence_anchor_sha256": original.stability_sha256,
        }
    ]
    validate_schema_document(
        {"result": document},
        native_review_provider_response_schema(context),
    )
    result = parse_native_contract_result(document, context)

    assert result.findings[0].status is FindingStatus.CLOSED
    payload = branch_discovery_completed_payload(
        result,
        context.previous_findings,
        work_unit_id="discovery",
        validation_attestation_record_id="ar1-" + "4" * 64,
        reviewed_head_commit=PLAN_COMMIT,
        transport_schema="native-claude-review-v2",
        request_id=context.request_id,
        response_sha256="5" * 64,
    )
    completion_record = _record([], "discovery-completion", payload)
    disposition = ClosedFindingDispositionSnapshot(
        "C-01",
        finding_record_signature(context.previous_findings[0]),
        "ar1-" + "3" * 64,
        original,
    )
    handoff = BranchDiscoveryHandoffImportPayload(
        source_run_id="plan-run",
        source_head_record_id="ar1-" + "6" * 64,
        discovery_review_record_id=None,
        validation_attestation_record_id="ar1-" + "4" * 64,
        reviewed_head_commit=PLAN_COMMIT,
        family_id="family-1",
        family_base_commit="9" * 40,
        cycle_number=3,
        predecessor_run_id="plan-run",
        predecessor_head_record_id="ar1-" + "6" * 64,
        export_record_id="ar1-" + "7" * 64,
        target_run_id="discovery-run",
        target_task_path="inbox/doing/discovery.md",
        target_task_sha256="8" * 64,
        target_run_identity="discovery-run",
        finding_transitions_sha256=finding_transition_sequence_sha256(()),
        transitions=(),
        finding_snapshot=(),
        authority=Role.ORCHESTRATOR,
        target_execution_mode="BRANCH_DISCOVERY",
        source_completion_record_id="ar1-" + "9" * 64,
        closed_finding_dispositions=(disposition,),
    )
    handoff_record = _record([], "discovery-import", handoff)

    occurrences = closed_finding_occurrence_payloads(
        discovery_completion_record=completion_record,
        handoff_import_record=handoff_record,
    )
    assert len(occurrences) == 1
    assert occurrences[0].record_type.value == "closed_finding_occurrence"
    assert occurrences[0].evidence_anchor_sha256 == original.stability_sha256
    occurrence_record = _record([], "closed-occurrence", occurrences[0])
    assert ArtifactRecord.from_dict(occurrence_record.to_dict()) == occurrence_record


def test_changed_closed_signature_requires_a_new_generation() -> None:
    original = _anchor(NativeRejectionReason.NO_DEFECT)
    changed = _anchor(
        NativeRejectionReason.NO_DEFECT,
        provenance="f" * 64,
        evidence_digest="0" * 64,
    )
    context = _discovery_context(original, changed)
    occurrence = _discovery_document(context)
    occurrence["occurrences"] = [
        {
            "finding_id": "C-01",
            "rationale": "The condition occurs again.",
            "evidence_anchor_sha256": changed.stability_sha256,
        }
    ]
    with pytest.raises(NativeReviewContractError, match="unchanged evidence anchor"):
        parse_native_contract_result(occurrence, context)

    generation = _discovery_document(context)
    generation["new_findings"] = [
        {
            "finding_id": "C-02",
            "finding_class": "BLOCKER",
            "affected_paths": ["src/fix.py"],
            "summary": "The defect affects src/fix.py.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Repair src/fix.py and preserve its contract.",
            },
            "predecessor_finding_ref": "C-01",
            "evidence_anchor_sha256": changed.stability_sha256,
        }
    ]
    validate_schema_document(
        {"result": generation},
        native_review_provider_response_schema(context),
    )
    result = parse_native_contract_result(generation, context)
    assert tuple(item.status for item in result.findings) == (
        FindingStatus.CLOSED,
        FindingStatus.OPEN,
    )
    assert result.findings[1].predecessor_finding_ref == "C-01"

    unchanged = _discovery_context(original, replace(original, provenance_fingerprint="f" * 64))
    generation["request_id"] = unchanged.request_id
    generation["new_findings"][0]["evidence_anchor_sha256"] = (  # type: ignore[index]
        original.stability_sha256
    )
    with pytest.raises(NativeReviewContractError, match="must be recorded as an occurrence"):
        parse_native_contract_result(generation, unchanged)


def test_reopened_transition_is_rejected_by_the_canonical_reducer() -> None:
    records: list[ArtifactRecord] = []
    _record(
        records,
        "open",
        FindingTransitionPayload(
            "C-01",
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.BLOCKER,
            "open",
            "Opened.",
            "review",
            "The defect affects src/fix.py.",
            "Repair src/fix.py and preserve its contract.",
            "review",
            1,
        ),
    )
    _record(
        records,
        "close",
        FindingTransitionPayload(
            "C-01",
            Role.CLAUDE,
            Role.CLAUDE,
            "status_changed",
            FindingSeverity.BLOCKER,
            "closed",
            "Closed.",
            "review",
            closure_kind="fixed",
        ),
    )
    _record(
        records,
        "reopen",
        FindingTransitionPayload(
            "C-01",
            Role.CLAUDE,
            Role.CLAUDE,
            "status_changed",
            FindingSeverity.BLOCKER,
            "open",
            "Reopened.",
            "review",
        ),
    )

    with pytest.raises(ArtifactReplayError, match="cannot be reopened"):
        reduce_finding_records(records)
