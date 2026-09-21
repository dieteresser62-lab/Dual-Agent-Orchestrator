from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

import native_finding_decisions
from artifact_bridge import (
    ArtifactBridgeError,
    evaluate_recorded_remediation_round,
    plan_assignment_payload,
    remediation_cohort_checkpoint_payload,
)
from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    ArtifactValidationError,
    BranchDiscoveryCompletedPayload,
    BranchDiscoveryFindingPayload,
    BranchDiscoveryHandoffImportPayload,
    FamilyBindingPayload,
    FindingSnapshotItem,
    FindingHandoffImportPayload,
    FindingSeverity,
    FindingTransitionPayload,
    Fingerprint,
    FingerprintKind,
    ImportedFindingTransition,
    PlanTreatmentAssignment,
    PlanAssignmentPayload,
    PlanTreatmentDecisionPayload,
    PlanTreatmentProposalPayload,
    ReviewEvidencePayload,
    ReviewPayload,
    RemediationCohortCheckpointPayload,
    Role,
    RoleProfilePayload,
    RunProfilePayload,
    SliceSpec,
    artifact_payload_document,
    finding_transition_sequence_sha256,
    validate_artifact_document,
)
from artifact_replay import ArtifactReplayResult
from contracts import (
    AgentRole,
    ApprovalMarker,
    CodexStepContract,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    PlannedSlice,
    ReadinessMarker,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from finding_planning import (
    MAX_REMEDIATION_ROUNDS,
    FindingSignatureGroup,
    PlanTreatmentDecision,
    PlanTreatmentDecisionKind,
    PlanTreatmentKind,
    PlanTreatmentProposal,
    RemediationRoundOutcome,
    canonical_open_signature_groups,
    evaluate_remediation_round,
    validate_plan_treatment_coverage,
    validate_plan_treatment_decisions,
)
from finding_signature import finding_record_signature
from finding_responsibility import (
    BranchPlanningResponsibility,
    SliceResponsibility,
)
from native_finding_decisions import NativeRejectionReason
from native_codex_contract import (
    BoundNativeCodexContext,
    NativeCodexContext,
    NativeCodexRequestKind,
    native_codex_provider_response_schema,
    parse_bound_native_codex_contract_result,
)
from native_review_contract import (
    NativeReviewContext,
    NativeReviewContractError,
    native_review_context_binding,
    parse_native_contract_result,
    parse_native_review_response,
)
from schema_validation import validate_schema_document


ROOT = Path(__file__).resolve().parents[1]
RUN_8_FIXTURE = ROOT / "tests/fixtures/run-8-finding-handoff-import-v1.json"


def _finding(
    finding_id: str,
    *,
    summary: str = "The defect affects src/fix.py.",
    acceptance: str = "Repair src/fix.py and preserve its contract.",
    finding_class: FindingClass = FindingClass.BLOCKER,
) -> FindingRecord:
    return FindingRecord(
        finding_id=finding_id,
        finding_class=finding_class,
        status=FindingStatus.OPEN,
        summary=summary,
        acceptance_test=acceptance,
        origin=FindingOrigin("discovery", 1, AgentRole.CLAUDE),
    )


def _implementation(
    group: FindingSignatureGroup, *, closing_slice_id: int = 1
) -> PlanTreatmentProposal:
    return PlanTreatmentProposal(
        group.signature,
        group.finding_ids,
        PlanTreatmentKind.IMPLEMENTATION,
        (closing_slice_id,),
    )


def _new_findings(count: int) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            f"C-{number + 20:02d}",
            f"Independent defect {number} affects src/new-{number}.py.",
            f"Repair src/new-{number}.py.",
        )
        for number in range(1, count + 1)
    )


def _record(
    run_id: str,
    logical_id: str,
    payload: object,
    *,
    fingerprint: str = "f" * 64,
) -> ArtifactRecord:
    return ArtifactRecord.create(
        run_id=run_id,
        logical_id=logical_id,
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.CONTRACT, fingerprint),
        predecessor_ids=(),
        created_at="2026-09-19T08:00:00+00:00",
        idempotency_key=f"{run_id}:{logical_id}",
        payload=payload,  # type: ignore[arg-type]
    )


def test_plan_coverage_rejects_a_missing_and_a_duplicate_signature() -> None:
    groups = canonical_open_signature_groups(
        (
            _finding("C-01"),
            _finding(
                "C-02",
                summary="Another defect affects src/other.py.",
                acceptance="Repair src/other.py.",
            ),
        )
    )
    slices = (
        PlannedSlice(1, "First repair", ("src/fix.py",)),
        PlannedSlice(2, "Second repair", ("src/other.py",)),
    )

    with pytest.raises(ValueError, match="missing signature"):
        validate_plan_treatment_coverage(
            groups,
            (_implementation(groups[0]),),
            slices,
        )
    duplicate = tuple(
        sorted(
            (
                _implementation(groups[0]),
                _implementation(groups[0]),
                _implementation(groups[1], closing_slice_id=2),
            ),
            key=lambda item: item.signature,
        )
    )
    with pytest.raises(ValueError, match="exactly one sorted treatment"):
        validate_plan_treatment_coverage(groups, duplicate, slices)


@pytest.mark.parametrize("closing_slice_ids", ((), (1, 2)))
def test_implementation_treatment_requires_exactly_one_closing_slice(
    closing_slice_ids: tuple[int, ...],
) -> None:
    group = canonical_open_signature_groups((_finding("C-01"),))[0]

    with pytest.raises(ValueError, match="exactly one closing Slice"):
        PlanTreatmentProposal(
            group.signature,
            group.finding_ids,
            PlanTreatmentKind.IMPLEMENTATION,
            closing_slice_ids,
        )


def test_no_code_disposition_requires_evidence_and_authoritative_fingerprint() -> None:
    group = canonical_open_signature_groups((_finding("C-01"),))[0]

    with pytest.raises(ValueError, match="evidence"):
        PlanTreatmentProposal(
            group.signature,
            group.finding_ids,
            PlanTreatmentKind.NO_CODE,
            no_code_reason=NativeRejectionReason.NO_DEFECT,
        )
    with pytest.raises(ArtifactValidationError, match="authoritative_fingerprint"):
        PlanTreatmentAssignment(
            group.signature,
            group.finding_ids,
            "no_code",
            no_code_reason="no_defect",
            evidence="The fingerprint-bound source proves the behavior is intended.",
        )


def test_positive_plan_requires_each_explicit_claude_treatment_acceptance() -> None:
    group = canonical_open_signature_groups((_finding("C-01"),))[0]
    treatment = PlanTreatmentProposal(
        group.signature,
        group.finding_ids,
        PlanTreatmentKind.NO_CODE,
        no_code_reason=NativeRejectionReason.ALREADY_FIXED,
        evidence="The bound HEAD contains the repair and its regression test.",
        evidence_paths=("tests/test_fix.py",),
        affected_paths=("src/fix.py",),
    )

    with pytest.raises(ValueError, match="omitted an explicit decision"):
        validate_plan_treatment_decisions((treatment,), (), plan_approved=True)
    with pytest.raises(ValueError, match="reviewer-rejected"):
        validate_plan_treatment_decisions(
            (treatment,),
            (
                PlanTreatmentDecision(
                    group.signature,
                    PlanTreatmentDecisionKind.REJECTED,
                    "The evidence does not establish the claim.",
                ),
            ),
            plan_approved=True,
        )


def test_signature_deduplication_retains_each_finding_id() -> None:
    groups = canonical_open_signature_groups(
        (_finding("C-01"), _finding("C-02"))
    )

    assert len(groups) == 1
    assert groups[0].finding_ids == ("C-01", "C-02")


def test_implementation_scope_is_exact_union_of_slice_paths() -> None:
    group = canonical_open_signature_groups((_finding("C-01"),))[0]
    slices = (
        PlannedSlice(1, "Prerequisite", ("src/a.py", "tests/test_a.py")),
        PlannedSlice(2, "Closing repair", ("src/b.py", "tests/test_a.py")),
    )

    scope = validate_plan_treatment_coverage(
        (group,),
        (_implementation(group, closing_slice_id=2),),
        slices,
    )

    assert scope == ("src/a.py", "src/b.py", "tests/test_a.py")


def test_unresolved_inherited_cohort_neither_completes_nor_advances() -> None:
    signature = "1" * 64
    outcome = evaluate_remediation_round(
        remediation_round_number=1,
        inherited_signatures=(signature,),
        unresolved_inherited_signatures=(signature,),
        new_findings=_new_findings(1),
    )

    assert outcome.outcome is RemediationRoundOutcome.ROUND_FAILED
    assert outcome.next_round_finding_ids == ()


def test_empty_inherited_and_new_cohorts_accept_the_family() -> None:
    outcome = evaluate_remediation_round(
        remediation_round_number=1,
        inherited_signatures=("1" * 64,),
        unresolved_inherited_signatures=(),
        new_findings=(),
    )

    assert outcome.outcome is RemediationRoundOutcome.FAMILY_ACCEPTED


def test_new_cohort_becomes_the_next_round_input() -> None:
    outcome = evaluate_remediation_round(
        remediation_round_number=1,
        inherited_signatures=("1" * 64,),
        unresolved_inherited_signatures=(),
        new_findings=_new_findings(2),
    )

    assert outcome.outcome is RemediationRoundOutcome.NEXT_ROUND
    assert outcome.next_round_finding_ids == ("C-21", "C-22")


def test_ten_closed_and_ten_new_advance_despite_equal_cardinality() -> None:
    outcome = evaluate_remediation_round(
        remediation_round_number=7,
        inherited_signatures=tuple(f"{number:064x}" for number in range(1, 11)),
        unresolved_inherited_signatures=(),
        new_findings=_new_findings(10),
    )

    assert len(outcome.inherited_signatures) == len(outcome.new_signatures) == 10
    assert outcome.outcome is RemediationRoundOutcome.NEXT_ROUND
    assert len(outcome.next_round_finding_ids) == 10


def test_absolute_round_limit_stops_and_names_its_value(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        outcome = evaluate_remediation_round(
            remediation_round_number=MAX_REMEDIATION_ROUNDS,
            inherited_signatures=("1" * 64,),
            unresolved_inherited_signatures=(),
            new_findings=_new_findings(1),
        )

    assert outcome.outcome is RemediationRoundOutcome.ROUND_LIMIT_STOP
    assert outcome.stop_rule_id == "REMEDIATION_ROUND_LIMIT"
    assert f"absolute_round_limit={MAX_REMEDIATION_ROUNDS}" in caplog.text


def test_routed_branch_finding_becomes_inherited_s_r_in_plan_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    finding = _finding("C-01", finding_class=FindingClass.OBSERVATION)
    signature = finding_record_signature(finding)
    opening = FindingTransitionPayload(
        "C-01",
        Role.CLAUDE,
        Role.CLAUDE,
        "opened",
        FindingSeverity.OBSERVATION,
        "open",
        "Found during branch discovery.",
        "discovery",
        finding.summary,
        finding.acceptance_test,
        "6",
        1,
        responsibility=SliceResponsibility(
            "implementation-run", "0" * 40, "6"
        ),
    )
    imported = ImportedFindingTransition(
        "ar1-" + "1" * 64,
        opening,
        "implementation-run",
        "ar1-" + "1" * 64,
    )
    route = FindingTransitionPayload(
        "C-01",
        Role.CLAUDE,
        Role.CLAUDE,
        "routed",
        FindingSeverity.OBSERVATION,
        "open",
        "No approved later Slice carries the repair.",
        "6",
        responsibility=BranchPlanningResponsibility("family-1", 1),
    )
    routed = ImportedFindingTransition(
        "ar1-" + "6" * 64,
        route,
        "implementation-run",
        "ar1-" + "6" * 64,
    )
    snapshot = BranchDiscoveryHandoffImportPayload(
        source_run_id="discovery-run",
        source_head_record_id="ar1-" + "2" * 64,
        discovery_review_record_id="ar1-" + "3" * 64,
        validation_attestation_record_id="ar1-" + "4" * 64,
        reviewed_head_commit="a" * 40,
        family_id="family-1",
        family_base_commit="b" * 40,
        cycle_number=2,
        predecessor_run_id="discovery-run",
        predecessor_head_record_id="ar1-" + "2" * 64,
        export_record_id="ar1-" + "5" * 64,
        target_run_id="plan-run",
        target_task_path="inbox/doing/remediation.md",
        target_task_sha256="6" * 64,
        target_run_identity="plan-run",
        finding_transitions_sha256=finding_transition_sequence_sha256(
            (imported, routed)
        ),
        transitions=(imported, routed),
        finding_snapshot=(
            FindingSnapshotItem(
                "C-01", signature, "open", FindingSeverity.OBSERVATION
            ),
        ),
        authority=Role.ORCHESTRATOR,
    )
    source_record = _record("plan-run", "snapshot", snapshot)
    codex_record = _record(
        "plan-run",
        "codex-plan",
        AgentResultPayload(
            Role.CODEX,
            "plan",
            "ready",
            (),
            "native-codex-v2",
            "native-codex-request-" + "7" * 64,
            "8" * 64,
            (SliceSpec("1", "Repair the defect", ("src/fix.py",)),),
            (
                PlanTreatmentProposalPayload(
                    signature,
                    ("C-01",),
                    "implementation",
                    ("1",),
                ),
            ),
        ),
    )
    review_record = _record(
        "plan-run",
        "plan-review",
        ReviewPayload(
            Role.CLAUDE,
            "plan",
            "approved",
            ("C-01",),
            None,
            "native-claude-review-v2",
            "native-review-request-" + "9" * 64,
            "a" * 64,
            ReviewEvidencePayload(
                "Coverage, paths, and closing responsibility checked.",
                "The implementation may expose a new integration defect.",
                "A missing closing Slice would invalidate approval.",
            ),
            pre_mortem="A signature could be accidentally omitted.",
            plan_treatment_decisions=(
                PlanTreatmentDecisionPayload(
                    signature,
                    "accepted",
                    "The closing Slice and evidence are sufficient.",
                ),
            ),
        ),
    )
    family = FamilyBindingPayload(
        "family-1",
        "b" * 40,
        ("src/fix.py",),
        "discovery-run",
        "ar1-" + "2" * 64,
        2,
        None,
        None,
    )

    assignment = plan_assignment_payload(
        source_snapshot_record=source_record,
        plan_result_record=codex_record,
        review_record=review_record,
        family_binding=family,
        remediation_round_number=1,
    )

    assert assignment.plan_result_record_id == codex_record.record_id
    assert assignment.review_record_id == review_record.record_id
    assert assignment.implementation_scope == ("src/fix.py",)
    assert tuple(item.signature for item in assignment.treatments) == (signature,)
    assert assignment.treatments[0].finding_ids == ("C-01",)

    assignment_record = _record("plan-run", "assignment", assignment)
    implementation_import = FindingHandoffImportPayload(
        source_run_id="plan-run",
        source_head_record_id="ar1-" + "7" * 64,
        approved_plan_commit="0" * 40,
        approval_review_record_id=review_record.record_id,
        export_record_id="ar1-" + "8" * 64,
        target_run_id="implementation-run",
        target_task_sha256="9" * 64,
        finding_transitions_sha256=finding_transition_sequence_sha256(
            (imported, routed)
        ),
        transitions=(imported, routed),
        authority=Role.ORCHESTRATOR,
    )
    implementation_import_record = _record(
        "implementation-run", "finding-import", implementation_import
    )
    implementation_replay = ArtifactReplayResult(
        expected_run_id="implementation-run",
        records=(implementation_import_record,),
        head_record_id=implementation_import_record.record_id,
        semantic_facts=(),
        semantic_digest="0" * 64,
        audit_events=(),
        run_profile=RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
            family_binding=family,
        ),
    )

    checkpoint = remediation_cohort_checkpoint_payload(
        assignment_record=assignment_record,
        implementation_replay=implementation_replay,
    )

    assert checkpoint.inherited_signatures == (signature,)
    assert checkpoint.unresolved_inherited_signatures == (signature,)

    unrouted_snapshot = replace(
        snapshot,
        finding_transitions_sha256=finding_transition_sequence_sha256((imported,)),
        transitions=(imported,),
    )
    with pytest.raises(ArtifactBridgeError, match="lacks BRANCH_PLANNING"):
        plan_assignment_payload(
            source_snapshot_record=_record(
                "plan-run", "unrouted-snapshot", unrouted_snapshot
            ),
            plan_result_record=codex_record,
            review_record=review_record,
            family_binding=family,
            remediation_round_number=1,
        )

    foreign_codex_record = _record(
        "other-plan-run", "codex-plan", codex_record.payload
    )
    with pytest.raises(ArtifactBridgeError, match="snapshot-bound plan run"):
        plan_assignment_payload(
            source_snapshot_record=source_record,
            plan_result_record=foreign_codex_record,
            review_record=review_record,
            family_binding=family,
            remediation_round_number=1,
        )


def test_optional_treatment_fields_are_absent_from_artifact_wire_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    payload = AgentResultPayload(
        Role.CODEX,
        "plan",
        "ready",
        (),
        "native-codex-v2",
        "native-codex-request-" + "1" * 64,
        "2" * 64,
        (SliceSpec("1", "Repair", ("src/fix.py",)),),
        (
            PlanTreatmentProposalPayload(
                "3" * 64,
                ("C-01",),
                "implementation",
                ("1",),
            ),
        ),
    )

    treatment = artifact_payload_document(payload)["plan_treatments"][0]
    assert treatment == {
        "signature": "3" * 64,
        "finding_ids": ["C-01"],
        "treatment_kind": "implementation",
        "closing_slice_ids": ["1"],
    }


def test_native_codex_plan_transports_complete_signature_treatments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    finding = _finding("C-01")
    signature = finding_record_signature(finding)
    context = NativeCodexContext(
        run_id="plan-run",
        work_unit_id="plan",
        operation="codex_plan",
        current_fingerprint="a" * 64,
        request_kind=NativeCodexRequestKind.PLAN,
        contract=CodexStepContract(
            name="remediation-plan",
            readiness_marker=ReadinessMarker.PLAN,
            slice_id="1",
            round_number=1,
            require_slice_plan=True,
            plan_artifact_path="docs/internal/remediation-plan.md",
        ),
        previous_findings=(finding,),
    )
    bound = BoundNativeCodexContext(
        context,
        "native-codex-request-" + "b" * 64,
        "b" * 64,
    )
    document = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": "plan_result",
        "request_id": bound.request_id,
        "ready": True,
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Repair the inherited defect.",
                "scope_paths": ["src/fix.py"],
                "acceptance_criteria": [{
                    "text": "The inherited defect is closed.",
                    "measured_against": "SOURCE",
                }],
            }
        ],
        "finding_dispositions": [
            {
                "finding_id": "C-01",
                "decision": "accepted",
                "rationale": "The plan assigns the repair to Slice 1.",
                "responsibility_proposal": None,
            }
        ],
        "plan_treatments": [
            {
                "signature": signature,
                "finding_ids": ["C-01"],
                "treatment_kind": "implementation",
                "closing_slice_ids": [1],
            }
        ],
        "plan_completion": "IMPLEMENTATION_REQUIRED",
    }

    validate_schema_document(
        {"result": document}, native_codex_provider_response_schema(context)
    )
    result = parse_bound_native_codex_contract_result(document, bound)

    assert result.plan_treatments == (
        PlanTreatmentProposal(
            signature,
            ("C-01",),
            PlanTreatmentKind.IMPLEMENTATION,
            (1,),
        ),
    )


def test_native_plan_review_binds_and_requires_explicit_treatment_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    finding = _finding("C-01")
    signature = finding_record_signature(finding)
    treatment = PlanTreatmentProposal(
        signature,
        ("C-01",),
        PlanTreatmentKind.NO_CODE,
        no_code_reason=NativeRejectionReason.NO_DEFECT,
        evidence="The fingerprint-bound contract defines this behavior.",
        evidence_paths=("docs/internal/contract.md",),
    )
    context = NativeReviewContext(
        run_id="plan-run",
        work_unit_id="plan",
        operation="claude_plan_review",
        diff_fingerprint="a" * 64,
        reviewer=AgentRole.CLAUDE,
        approval_marker=ApprovalMarker.PLAN,
        slice_id="PLAN",
        round_number=1,
        previous_findings=(finding,),
        validation_attestation=ValidationAttestation(
            attestation_id="validation-plan",
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
            summary="1 passed",
            command_specs=(
                ValidationCommandSpec(
                    argv=("python3", "-m", "pytest", "tests/", "-v")
                ),
            ),
        ),
        anchor_origin=None,
        plan_artifact_path="docs/internal/remediation-plan.md",
        planned_slices=(
            PlannedSlice(1, "Preserve the reviewed plan", ("src/fix.py",)),
        ),
        plan_treatments=(treatment,),
    )
    response = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": context.request_id,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [],
        "status_changes": [
            {
                "finding_id": "C-01",
                "status": "CLOSED",
                "rationale": "The reviewer accepts the evidenced No-Code disposition.",
                "closure": {
                    "kind": "rejected",
                    "rejection_reason": "no_defect",
                    "evidence": "The fingerprint-bound contract defines this behavior.",
                },
            }
        ],
        "reclassifications": [],
        "responsibility_routes": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "Coverage, evidence, and fingerprint binding checked.",
            "largest_residual_risk": "The cited contract may later change.",
            "break_condition": "A different fingerprint invalidates the evidence.",
        },
        "pre_mortem": "A No-Code disposition could be accepted implicitly.",
    }

    assert native_review_context_binding(context)["plan_treatments"] == [
        {
            "signature": signature,
            "finding_ids": ["C-01"],
            "treatment_kind": "no_code",
            "no_code_reason": "no_defect",
            "evidence": "The fingerprint-bound contract defines this behavior.",
            "evidence_paths": ["docs/internal/contract.md"],
            "affected_paths": [],
        }
    ]
    with pytest.raises(NativeReviewContractError, match="omitted an explicit decision"):
        parse_native_review_response(response, context)

    response["plan_treatment_decisions"] = [
        {
            "signature": signature,
            "decision": "accepted",
            "rationale": "The cited evidence proves the disposition on this fingerprint.",
        }
    ]
    parsed = parse_native_review_response(response, context)
    assert parsed.plan_treatment_decisions[0].decision is (
        PlanTreatmentDecisionKind.ACCEPTED
    )
    result = parse_native_contract_result(response, context)
    assert result.approval is True
    assert result.plan_treatment_decisions == parsed.plan_treatment_decisions


def test_recorded_cohorts_take_s_from_checkpoint_and_n_from_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    signature = "1" * 64
    assignment_record = _record(
        "plan-run",
        "assignment",
        PlanAssignmentPayload(
            family_id="family-1",
            cycle_number=2,
            remediation_round_number=4,
            source_snapshot_record_id="ar1-" + "2" * 64,
            finding_snapshot_sha256="3" * 64,
            plan_result_record_id="ar1-" + "4" * 64,
            review_record_id="ar1-" + "5" * 64,
            review_fingerprint="f" * 64,
            treatments=(
                PlanTreatmentAssignment(
                    signature,
                    ("C-01",),
                    "implementation",
                    ("1",),
                ),
            ),
            slices=(SliceSpec("1", "Repair", ("src/fix.py",)),),
            implementation_scope=("src/fix.py",),
            authority=Role.CLAUDE,
        ),
    )
    checkpoint_record = _record(
        "implementation-run",
        "cohort-checkpoint",
        RemediationCohortCheckpointPayload(
            family_id="family-1",
            remediation_round_number=4,
            plan_assignment_record_id=assignment_record.record_id,
            implementation_run_id="implementation-run",
            implementation_head_record_id="ar1-" + "6" * 64,
            inherited_signatures=(signature,),
            unresolved_inherited_signatures=(),
            authority=Role.ORCHESTRATOR,
        ),
    )
    discovery_import_record = _record(
        "discovery-run",
        "remediation-import",
        BranchDiscoveryHandoffImportPayload(
            source_run_id="implementation-run",
            source_head_record_id="ar1-" + "7" * 64,
            discovery_review_record_id=None,
            validation_attestation_record_id="ar1-" + "8" * 64,
            reviewed_head_commit="a" * 40,
            family_id="family-1",
            family_base_commit="b" * 40,
            cycle_number=2,
            predecessor_run_id="implementation-run",
            predecessor_head_record_id="ar1-" + "7" * 64,
            export_record_id="ar1-" + "9" * 64,
            target_run_id="discovery-run",
            target_task_path="inbox/doing/discovery.md",
            target_task_sha256="a" * 64,
            target_run_identity="discovery-run",
            finding_transitions_sha256=finding_transition_sequence_sha256(()),
            transitions=(),
            finding_snapshot=(),
            authority=Role.ORCHESTRATOR,
            target_execution_mode="BRANCH_DISCOVERY",
            source_completion_record_id="ar1-" + "b" * 64,
            remediation_cohort_checkpoint_record_id=checkpoint_record.record_id,
        ),
    )
    discovery_record = _record(
        "discovery-run",
        "discovery-completed",
        BranchDiscoveryCompletedPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="discovery",
            new_findings=(
                BranchDiscoveryFindingPayload(
                    "C-02",
                    FindingSeverity.BLOCKER,
                    "A new integration defect affects src/new.py.",
                    "Repair src/new.py.",
                ),
            ),
            occurrences=(),
            review_evidence=ReviewEvidencePayload(
                "The complete branch and integration boundaries were checked.",
                "Another independent defect may still escape the scan.",
                "A repeated scan finding no C-02 would challenge the result.",
            ),
            pre_mortem="A new finding could be confused with the inherited cohort.",
            validation_attestation_record_id="ar1-" + "8" * 64,
            reviewed_head_commit="a" * 40,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "c" * 64,
            response_sha256="d" * 64,
            scan_complete=True,
        ),
    )

    for record in (assignment_record, checkpoint_record):
        document = record.to_dict()
        validate_artifact_document(document)
        assert ArtifactRecord.from_dict(document) == record

    outcome = evaluate_recorded_remediation_round(
        assignment_record=assignment_record,
        checkpoint_record=checkpoint_record,
        discovery_import_record=discovery_import_record,
        discovery_record=discovery_record,
    )

    assert outcome.inherited_signatures == (signature,)
    assert outcome.unresolved_inherited_signatures == ()
    assert outcome.next_round_finding_ids == ("C-02",)
    assert outcome.outcome is RemediationRoundOutcome.NEXT_ROUND


def test_run_8_projection_digest_is_anchored_with_the_handoff_fixture() -> None:
    fixture = json.loads(RUN_8_FIXTURE.read_text(encoding="utf-8"))

    assert fixture["state_projection_sha256"] == (
        "ac659a29af3cedda9153d18f4b0feb9d6f66e408b11db888a91ac793432a4bc1"
    )
