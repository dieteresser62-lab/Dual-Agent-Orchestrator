from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib

import pytest
import native_finding_decisions

from contracts import (
    AgentRole,
    AnchorRecord,
    ApprovalMarker,
    FindingAcceptanceMeasurement,
    FindingClass,
    FindingOccurrence,
    FindingOrigin,
    FindingRecord,
    FindingResponseDecision,
    FindingStatus,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
    apply_finding_response,
)
from finding_reducer import project_open_set, project_reviewer_persistence_transitions
from gates import detect_anchor_changes
from native_review_contract import (
    DISCOVERY_OUTPUT_LIMIT_RULE_ID,
    NativeFinding,
    NativeProseAcceptance,
    NativeReclassification,
    NativeReviewContext,
    NativeReviewContractError,
    NativeReviewDispositionLimit,
    NativeReviewErrorCode,
    NativeReviewResult,
    NativeStatusChange,
    NativeStopResult,
    native_response_to_contract_result,
    native_review_retry_guidance,
    native_review_provider_response_schema,
    parse_native_contract_result,
    reject_passing_typed_acceptance_binding,
    validate_native_review_disposition_budget,
    parse_native_review_response,
)
from orchestrator_diagnostics import OrchestratorDiagnostic
from rejected_response_shape import extract_rejected_native_response_shape
from schema_validation import SchemaMismatch, validate_schema_document
from validation_matrix import (
    ValidationCommand,
    ValidationMatrix,
    select_validation_request,
)
from workflow_state import WorkUnitKind


FINGERPRINT = "a" * 64
PRE_CHANGE_FINGERPRINT = "b" * 64
TYPED_ACCEPTANCE_ARGV = (
    "python3",
    "-m",
    "pytest",
    "tests/test_native_review_contract.py",
    "-q",
)


def test_review_contract_diagnostic_is_exact_or_value_free() -> None:
    static = NativeReviewContractError(
        NativeReviewErrorCode.APPROVAL_INVALID,
        "approval requires pre_mortem",
    )
    assert static.orchestrator_diagnostic is (
        OrchestratorDiagnostic.REVIEW_APPROVAL_PRE_MORTEM_REQUIRED
    )

    provider_value = "provider-secret-in-schema-message"
    value_bearing = NativeReviewContractError(
        NativeReviewErrorCode.SCHEMA_INVALID,
        f"schema validation failed at result: {provider_value}",
    )
    assert value_bearing.orchestrator_diagnostic is (
        OrchestratorDiagnostic.REVIEW_SCHEMA_INVALID
    )
    assert provider_value not in value_bearing.orchestrator_diagnostic.text

    assert NativeReviewContractError(
        NativeReviewErrorCode.CONTEXT_INVALID,
        "request_sequence must be 1-based",
    ).orchestrator_diagnostic is (
        OrchestratorDiagnostic.REVIEW_CONTEXT_REQUEST_SEQUENCE_INVALID
    )
    assert NativeReviewContractError(
        NativeReviewErrorCode.CONTEXT_INVALID,
        "communicated Slice-commit decision Finding set differs from the "
        "authoritative enforced set",
    ).orchestrator_diagnostic is (
        OrchestratorDiagnostic.REVIEW_CONTEXT_SLICE_COMMIT_DECISION_SET_MISMATCH
    )


def test_evidence_anchor_without_predecessor_uses_precise_value_free_diagnostic() -> None:
    context = _context()
    provider_digest = "e" * 64
    document = _review(context, approved=False)
    document["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "BLOCKER",
            "summary": "The evidence anchor has no predecessor binding.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Require a predecessor whenever the anchor digest is present.",
            },
            "evidence_anchor_sha256": provider_digest,
            "affected_paths": [],
        }
    ]
    response = parse_native_review_response(document, context)

    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(response, context)

    error = raised.value
    diagnostic = OrchestratorDiagnostic.REVIEW_EVIDENCE_ANCHOR_PREDECESSOR_REQUIRED
    assert error.code is NativeReviewErrorCode.FINDING_ID_INVALID
    assert error.detail == (
        "evidence anchor digest requires a predecessor Finding reference"
    )
    assert error.orchestrator_diagnostic is diagnostic
    assert provider_digest not in diagnostic.text
    assert native_review_retry_guidance(error.code, diagnostic) == diagnostic.text


def _attestation() -> ValidationAttestation:
    command = "python3 -m pytest tests/ -v"
    return ValidationAttestation(
        attestation_id="validation-native",
        diff_fingerprint=FINGERPRINT,
        expected_commands=(command,),
        records=(ValidationRecord(ValidationStatus.PASS, command, 0, "passed"),),
        output_digest=hashlib.sha256(b"passed").hexdigest(),
        summary="1 passed",
        command_specs=(
            ValidationCommandSpec(argv=("python3", "-m", "pytest", "tests/", "-v")),
        ),
    )


def _context(
    *,
    reviewer: AgentRole = AgentRole.CLAUDE,
    approval: ApprovalMarker = ApprovalMarker.SLICE,
    previous: tuple[FindingRecord, ...] = (),
    test_files: tuple[str, ...] = (),
    tests_approved: bool = False,
    allow_observations: bool = True,
    anchor_origin: str | None = "approved-plan-v1",
    red_state_followup_slice: str | None = None,
) -> NativeReviewContext:
    return NativeReviewContext(
        run_id="run-native",
        work_unit_id="work-unit-1",
        operation=f"{reviewer.value}_slice_review",
        diff_fingerprint=FINGERPRINT,
        reviewer=reviewer,
        approval_marker=approval,
        slice_id="1",
        round_number=2,
        previous_findings=previous,
        validation_attestation=_attestation(),
        test_files=test_files,
        test_changes_approved=tests_approved,
        allow_new_observations=allow_observations,
        anchor_origin=anchor_origin,
        validation_command_prefixes=(("python3", "-m", "pytest"),),
        red_state_followup_slice=red_state_followup_slice,
    )


def test_plan_artifact_path_is_exact_and_reserved_for_plan_reviews() -> None:
    plan = replace(
        _context(approval=ApprovalMarker.PLAN),
        operation="claude_plan_review",
        plan_artifact_path="docs/internal/plan.md",
    )
    assert plan.plan_artifact_path == "docs/internal/plan.md"

    with pytest.raises(NativeReviewContractError, match="only for a plan review"):
        replace(_context(), plan_artifact_path="docs/internal/plan.md")
    with pytest.raises(NativeReviewContractError, match="repository-relative"):
        replace(plan, plan_artifact_path="../plan.md")
    with pytest.raises(NativeReviewContractError, match="repository-relative"):
        replace(plan, plan_artifact_path="docs//plan.md")


def _review(context: NativeReviewContext, *, approved: bool = True) -> dict[str, object]:
    return {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": context.request_id,
        "reviewer": context.reviewer.value,
        "decision": "approved" if approved else "denied",
        "new_findings": [],
        "status_changes": [],
        "reclassifications": [],
        "plan_treatment_decisions": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "correctness, failure paths, resume",
            "largest_residual_risk": "later adapter integration",
            "break_condition": "a mismatched response is accepted",
        },
        "pre_mortem": "A future adapter could pass the wrong context.",
    }


def _finding(
    finding_id: str,
    reporter: AgentRole,
    *,
    status: FindingStatus = FindingStatus.OPEN,
    finding_class: FindingClass = FindingClass.BLOCKER,
) -> FindingRecord:
    return FindingRecord(
        finding_id=finding_id,
        finding_class=finding_class,
        status=status,
        summary="Existing finding",
        acceptance_test="Focused regression",
        origin=FindingOrigin("1", 1, reporter),
        status_rationale="Closed earlier" if status is FindingStatus.CLOSED else None,
    )


def _typed_measurement(
    fingerprint: str,
    status: ValidationStatus,
) -> FindingAcceptanceMeasurement:
    return FindingAcceptanceMeasurement(
        fingerprint=fingerprint,
        command=ValidationCommandSpec(argv=TYPED_ACCEPTANCE_ARGV),
        status=status,
        exit_code=0 if status is ValidationStatus.PASS else 1,
        output_sha256=hashlib.sha256(status.value.encode("utf-8")).hexdigest(),
        attestation_id=f"acceptance-{fingerprint[:12]}-{status.value.lower()}",
    )


def test_unclosed_implementer_rejection_is_record_bound_blocker_escalation() -> None:
    finding = apply_finding_response(
        _finding(
            "C-01",
            AgentRole.CLAUDE,
            finding_class=FindingClass.OBSERVATION,
        ),
        FindingResponseDecision.REJECTED,
        "The implementation disputes the finding with a reason.",
    )
    context = _context(previous=(finding,))

    result = parse_native_contract_result(_review(context, approved=False), context)
    escalated = result.findings[0]
    transitions = project_reviewer_persistence_transitions(
        (finding,), result.findings, work_unit_id="work-unit-1"
    )

    assert escalated.finding_class is FindingClass.BLOCKER
    assert escalated.status is FindingStatus.OPEN
    assert escalated.class_history == (FindingClass.OBSERVATION,)
    assert [(item.finding.finding_id, item.action) for item in transitions] == [
        ("C-01", "escalated")
    ]
    assert transitions[0].rationale == (
        "The implementer rejection was not accepted by the reviewer; "
        "the Finding is escalated to BLOCKER."
    )


def _typed_finding(
    *measurements: FindingAcceptanceMeasurement,
) -> FindingRecord:
    return replace(
        _finding("C-01", AgentRole.CLAUDE),
        acceptance_test=(
            'VALIDATE: ["python3","-m","pytest",'
            '"tests/test_native_review_contract.py","-q"]'
        ),
        acceptance_measurements=measurements,
    )


def _fixed_document(context: NativeReviewContext) -> dict[str, object]:
    document = _review(context, approved=True)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": "The bound regression now passes.",
            "closure": {"kind": "fixed"},
        }
    ]
    return document


@pytest.fixture
def active_finding_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )


def test_branch_discovery_completion_is_not_an_approval_and_keeps_open_findings(
    active_finding_decisions: None,
) -> None:
    existing = _finding(
        "C-01",
        AgentRole.CLAUDE,
        finding_class=FindingClass.OBSERVATION,
    )
    context = replace(
        _context(previous=(existing,), anchor_origin=None),
        operation="claude_branch_discovery",
        approval_marker=ApprovalMarker.BRANCH_DISCOVERY,
        slice_id="FINAL",
    )
    document = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "branch_discovery_completed",
        "request_id": context.request_id,
        "reviewer": "claude",
        "scan_complete": True,
        "new_findings": [
            {
                "finding_id": "C-02",
                "finding_class": "BLOCKER",
                "summary": "A new defect remains on the reviewed branch HEAD.",
                "predecessor_finding_ref": None,
                "evidence_anchor_sha256": None,
                "acceptance_test": {
                    "kind": "prose",
                    "text": "A later ordinary plan must address the defect.",
                },
                "affected_paths": [],
            }
        ],
        "occurrences": [
            {
                "finding_id": "C-01",
                "rationale": "The known defect is still reproducible on this HEAD.",
                "evidence_anchor_sha256": None,
            }
        ],
        "review_evidence": {
            "dimensions": "correctness, contracts, failure paths, and resume",
            "largest_residual_risk": "A future family edge could omit a finding.",
            "break_condition": "One imported opening disappears from replay.",
        },
        "pre_mortem": "The scan could complete while silently dropping history.",
    }

    validate_schema_document(
        {"result": document}, native_review_provider_response_schema(context)
    )
    result = parse_native_contract_result(document, context)

    assert result.delivery_kind == "branch_discovery_completed"
    assert result.approval is None
    assert result.stopped is False
    assert result.occurrences == (
        FindingOccurrence(
            "C-01", "The known defect is still reproducible on this HEAD."
        ),
    )
    assert project_open_set(result.findings).finding_ids == ("C-01", "C-02")
    ordinary_review = _review(context)
    with pytest.raises(SchemaMismatch):
        validate_schema_document(
            {"result": ordinary_review},
            native_review_provider_response_schema(context),
        )
    with pytest.raises(NativeReviewContractError, match="cannot use approved"):
        parse_native_contract_result(ordinary_review, context)


def test_branch_discovery_requires_complete_scan_and_never_truncates_at_capacity(
    active_finding_decisions: None,
) -> None:
    context = replace(
        _context(anchor_origin=None),
        operation="claude_branch_discovery",
        approval_marker=ApprovalMarker.BRANCH_DISCOVERY,
        slice_id="FINAL",
        max_new_findings=1,
    )
    document = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "branch_discovery_completed",
        "request_id": context.request_id,
        "reviewer": "claude",
        "scan_complete": True,
        "new_findings": [
            {
                "finding_id": "C-01",
                "finding_class": "BLOCKER",
                "summary": "The discovery capacity has been reached.",
                "acceptance_test": {
                    "kind": "prose",
                    "text": "Increase capacity only through a new explicit product decision.",
                },
                "affected_paths": [],
            }
        ],
        "occurrences": [],
        "review_evidence": {
            "dimensions": "correctness and completeness",
            "largest_residual_risk": "More findings remain undiscovered.",
            "break_condition": "A capacity-sized partial set becomes authoritative.",
        },
        "pre_mortem": "The result could look complete at the exact limit.",
    }

    stopped = parse_native_contract_result(document, context)

    assert stopped.stopped is True
    assert stopped.stop_request is not None
    assert stopped.stop_request.rule_id == DISCOVERY_OUTPUT_LIMIT_RULE_ID
    assert stopped.findings == context.previous_findings

    overflow = deepcopy(document)
    overflow["new_findings"].append(
        {
            "finding_id": "C-02",
            "finding_class": "OBSERVATION",
            "summary": "A second finding exceeds the request-bound capacity.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Reject the whole result instead of slicing the list.",
            },
            "affected_paths": [],
        }
    )
    with pytest.raises(NativeReviewContractError, match="exceeds request-bound"):
        parse_native_contract_result(overflow, context)

    missing_scan = deepcopy(document)
    del missing_scan["scan_complete"]
    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(missing_scan, context)
    assert raised.value.code is NativeReviewErrorCode.SCHEMA_INVALID

    incomplete = deepcopy(document)
    incomplete["new_findings"] = []
    incomplete["scan_complete"] = False
    with pytest.raises(NativeReviewContractError, match="scan_complete=true"):
        parse_native_contract_result(incomplete, context)
















def test_five_finding_event_collisions_have_precise_diagnostics(
    active_finding_decisions: None,
) -> None:
    context = _context(
        previous=(
            _finding("C-01", AgentRole.CLAUDE),
            _finding("C-02", AgentRole.CLAUDE),
        )
    )
    base = parse_native_review_response(_review(context, approved=False), context)
    assert isinstance(base, NativeReviewResult)
    new_finding = NativeFinding(
        "C-03",
        FindingClass.BLOCKER,
        "A new response-local defect.",
        NativeProseAcceptance("The response-local defect is repaired."),
    )
    status = NativeStatusChange(
        "C-01", FindingStatus.OPEN, "The existing blocker remains reproducible."
    )
    reclassification = NativeReclassification(
        "C-01", FindingClass.OBSERVATION, "The impact is non-blocking."
    )
    cases: list[
        tuple[dict[str, object], OrchestratorDiagnostic, tuple[str, ...]]
    ] = []
    for mutation, diagnostic, field_names in (
        (
            {"new_findings": (new_finding, new_finding)},
            OrchestratorDiagnostic.REVIEW_FINDING_NEW_DUPLICATE,
            ("new_findings",),
        ),
        (
            {"status_changes": (status, status)},
            OrchestratorDiagnostic.REVIEW_FINDING_STATUS_DUPLICATE,
            ("status_changes",),
        ),
        (
            {"reclassifications": (reclassification, reclassification)},
            OrchestratorDiagnostic.REVIEW_FINDING_RECLASSIFICATION_DUPLICATE,
            ("reclassifications",),
        ),
        (
            {
                "new_findings": (new_finding,),
                "reclassifications": (
                    replace(reclassification, finding_id="C-03"),
                ),
            },
            OrchestratorDiagnostic.REVIEW_FINDING_NEW_RECLASSIFICATION_CONFLICT,
            ("new_findings", "reclassifications"),
        ),
        (
            {
                "status_changes": (status,),
                "reclassifications": (reclassification,),
            },
            OrchestratorDiagnostic.REVIEW_FINDING_STATUS_RECLASSIFICATION_CONFLICT,
            ("status_changes", "reclassifications"),
        ),
    ):
        cases.append((mutation, diagnostic, field_names))

    seen: set[OrchestratorDiagnostic] = set()
    for mutation, diagnostic, field_names in cases:
        with pytest.raises(NativeReviewContractError) as raised:
            native_response_to_contract_result(replace(base, **mutation), context)
        error = raised.value
        assert error.code is NativeReviewErrorCode.FINDING_EVENT_CONFLICT
        assert error.orchestrator_diagnostic is diagnostic
        assert "C-" in error.detail
        assert all(field_name in error.detail for field_name in field_names)
        assert error.detail != "finding id occurs in more than one event"
        seen.add(diagnostic)

    assert len(seen) == 5




def test_approval_retry_guidance_labels_existing_and_same_response_findings() -> None:
    context = _context(
        previous=(
            _finding(
                "C-01",
                AgentRole.CLAUDE,
                finding_class=FindingClass.OBSERVATION,
            ),
        )
    )
    document = _review(context, approved=True)
    document["new_findings"] = [
        {
            "finding_id": "C-02",
            "finding_class": "OBSERVATION",
            "summary": "A newly opened observation remains undecided.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Decide the new observation in this response.",
            },
            "affected_paths": [],
        }
    ]
    shape = extract_rejected_native_response_shape(document)
    assert shape is not None

    guidance = native_review_retry_guidance(
        NativeReviewErrorCode.APPROVAL_INVALID,
        OrchestratorDiagnostic.REVIEW_APPROVAL_NEW_FINDINGS_UNDECIDED,
        context,
        shape,
    )

    assert "existing Finding IDs: C-01" in guidance
    assert "opened in the rejected response: C-02" in guidance
    assert "status_changes" in guidance
    assert "status=CLOSED" in guidance
    assert "opened in new_findings and decided in that same response" in guidance










def test_slice_approval_rejects_new_open_findings_with_actionable_ids(
    active_finding_decisions: None,
) -> None:
    context = replace(_context(), round_number=1)
    document = _review(context, approved=True)
    document["new_findings"] = [
        {
            "finding_id": finding_id,
            "finding_class": "OBSERVATION",
            "summary": f"{finding_id} remains undecided in this Slice.",
            "acceptance_test": {
                "kind": "prose",
                "text": f"Decide {finding_id} before approving the Slice.",
            },
            "affected_paths": [],
        }
        for finding_id in ("C-01", "C-02")
    ]

    validate_schema_document(
        {"result": document}, native_review_provider_response_schema(context)
    )
    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(document, context)

    assert raised.value.code is NativeReviewErrorCode.APPROVAL_INVALID
    assert raised.value.orchestrator_diagnostic is (
        OrchestratorDiagnostic.REVIEW_APPROVAL_NEW_FINDINGS_UNDECIDED
    )
    assert "C-01 (opened in this response)" in raised.value.detail
    assert "C-02 (opened in this response)" in raised.value.detail
    assert "status_changes" in raised.value.detail
    assert "may be decided in the same response" in raised.value.detail


def test_slice_approval_accepts_finding_opened_and_closed_in_same_response(
    active_finding_decisions: None,
) -> None:
    context = replace(_context(), round_number=1)
    document = _review(context, approved=True)
    document["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "summary": "The reviewed diff may omit the bounded guard.",
            "acceptance_test": {
                "kind": "prose",
                "text": "The bounded guard is present in the reviewed diff.",
            },
            "affected_paths": [],
        }
    ]
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": "The bound evidence shows the guard is present.",
            "closure": {"kind": "fixed"},
        }
    ]

    validate_schema_document(
        {"result": document}, native_review_provider_response_schema(context)
    )
    result = parse_native_contract_result(document, context)
    transitions = project_reviewer_persistence_transitions(
        (), result.findings, work_unit_id="work-unit-1"
    )

    assert result.approval is True
    assert result.findings[0].status is FindingStatus.CLOSED
    assert tuple(item.action for item in transitions) == (
        "opened",
        "status_changed",
    )
    assert transitions[0].finding.status is FindingStatus.OPEN
    assert transitions[1].finding.status is FindingStatus.CLOSED


def test_slice_denial_allows_new_undecided_finding(
    active_finding_decisions: None,
) -> None:
    context = replace(_context(), round_number=1)
    document = _review(context, approved=False)
    document["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "BLOCKER",
            "summary": "The Slice still violates its contract.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Correct the contract violation and review again.",
            },
            "affected_paths": [],
        }
    ]

    validate_schema_document(
        {"result": document}, native_review_provider_response_schema(context)
    )
    result = parse_native_contract_result(document, context)

    assert result.approval is False
    assert project_open_set(result.findings).finding_ids == ("C-01",)










def test_closed_status_requires_a_typed_closure(
    active_finding_decisions: None,
) -> None:
    context = _context(previous=(_finding("C-01", AgentRole.CLAUDE),))
    document = _review(context, approved=False)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": "The evidence resolves the finding.",
        }
    ]

    with pytest.raises(NativeReviewContractError, match="requires closure"):
        parse_native_review_response(document, context)


@pytest.mark.parametrize(
    "closure",
    (
        {"kind": "fixed"},
        {
            "kind": "rejected",
            "rejection_reason": "already_fixed",
            "evidence": "The fingerprint-bound diff already contains the repair.",
        },
    ),
)
def test_closed_status_accepts_fixed_or_evidenced_rejection(
    active_finding_decisions: None,
    closure: dict[str, str],
) -> None:
    context = _context(previous=(_finding("C-01", AgentRole.CLAUDE),))
    document = _review(context, approved=True)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": "Claude decides the finding from bound evidence.",
            "closure": closure,
        }
    ]

    response = parse_native_review_response(document, context)
    result = native_response_to_contract_result(response, context)

    assert response.status_changes[0].closure is not None
    assert result.findings[0].status is FindingStatus.CLOSED


def test_typed_fixed_requires_recorded_red_before_and_green_after() -> None:
    finding = _typed_finding(
        _typed_measurement(PRE_CHANGE_FINGERPRINT, ValidationStatus.FAIL),
        _typed_measurement(FINGERPRINT, ValidationStatus.PASS),
    )
    context = replace(
        _context(previous=(finding,)),
        pre_change_fingerprint=PRE_CHANGE_FINGERPRINT,
    )

    result = parse_native_contract_result(_fixed_document(context), context)

    assert result.findings[0].status is FindingStatus.CLOSED
    assert result.finding_closures[0][1].kind.value == "fixed"


def test_typed_fixed_rejects_an_acceptance_test_that_was_already_green() -> None:
    finding = _typed_finding(
        _typed_measurement(PRE_CHANGE_FINGERPRINT, ValidationStatus.PASS),
        _typed_measurement(FINGERPRINT, ValidationStatus.PASS),
    )
    context = replace(
        _context(previous=(finding,)),
        pre_change_fingerprint=PRE_CHANGE_FINGERPRINT,
    )

    with pytest.raises(NativeReviewContractError, match="already passing") as raised:
        parse_native_contract_result(_fixed_document(context), context)

    assert "previously green test does not prove a fix" in raised.value.detail


@pytest.mark.parametrize(
    "measurements",
    (
        (_typed_measurement(PRE_CHANGE_FINGERPRINT, ValidationStatus.FAIL),),
        (_typed_measurement(FINGERPRINT, ValidationStatus.PASS),),
        (
            _typed_measurement("c" * 64, ValidationStatus.FAIL),
            _typed_measurement(FINGERPRINT, ValidationStatus.PASS),
        ),
        (
            _typed_measurement(PRE_CHANGE_FINGERPRINT, ValidationStatus.PASS),
            _typed_measurement(FINGERPRINT, ValidationStatus.FAIL),
        ),
    ),
)
def test_typed_fixed_rejects_missing_foreign_or_swapped_measurements(
    measurements: tuple[FindingAcceptanceMeasurement, ...],
) -> None:
    context = replace(
        _context(previous=(_typed_finding(*measurements),)),
        pre_change_fingerprint=PRE_CHANGE_FINGERPRINT,
    )

    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(_fixed_document(context), context)

    assert raised.value.code is NativeReviewErrorCode.ACCEPTANCE_INVALID


def test_prose_fixed_does_not_infer_or_require_machine_measurements() -> None:
    finding = _finding("C-01", AgentRole.CLAUDE)
    context = replace(
        _context(previous=(finding,)),
        pre_change_fingerprint=PRE_CHANGE_FINGERPRINT,
    )

    result = parse_native_contract_result(_fixed_document(context), context)

    assert result.findings[0].status is FindingStatus.CLOSED
    assert result.findings[0].acceptance_measurements == ()


def test_partial_finding_decision_records_evidence_and_keeps_blocker_open() -> None:
    finding = _finding("C-01", AgentRole.CLAUDE)
    context = _context(previous=(finding,))
    document = _review(context, approved=False)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "OPEN",
            "rationale": "Nine of twelve factories are now wired.",
            "closure": {
                "kind": "partial",
                "evidence": "The bound regression reaches nine factories.",
                "remaining": "Wire onboarding, backup, and ingredient guide.",
            },
        }
    ]

    result = parse_native_contract_result(document, context)

    assert result.findings[0].status is FindingStatus.OPEN
    assert result.own_open_blockers == (result.findings[0],)
    closure = dict(result.finding_closures)["C-01"]
    assert closure.kind is native_finding_decisions.NativeClosureKind.PARTIAL
    assert closure.evidence == "The bound regression reaches nine factories."
    assert closure.remaining == "Wire onboarding, backup, and ingredient guide."


def test_partial_finding_decision_cannot_close_and_uses_value_free_diagnostic() -> None:
    finding = _finding("C-01", AgentRole.CLAUDE)
    context = _context(previous=(finding,))
    base = parse_native_review_response(_review(context, approved=False), context)
    assert isinstance(base, NativeReviewResult)
    response = replace(
        base,
        status_changes=(
            NativeStatusChange(
                "C-01",
                FindingStatus.CLOSED,
                "Some work remains.",
                native_finding_decisions.NativeFindingClosure(
                    kind=native_finding_decisions.NativeClosureKind.PARTIAL,
                    evidence="The completed subset is covered.",
                    remaining="Finish the outstanding subset.",
                ),
            ),
        ),
    )

    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(response, context)

    assert raised.value.code is NativeReviewErrorCode.FINDING_CONTENT_INVALID
    assert raised.value.orchestrator_diagnostic is (
        OrchestratorDiagnostic.REVIEW_PARTIAL_FINDING_MUST_REMAIN_OPEN
    )
    assert "C-01" not in raised.value.orchestrator_diagnostic.text


@pytest.mark.parametrize(
    ("closure", "diagnostic"),
    (
        (
            {"kind": "rejected", "rejection_reason": "no_defect"},
            "evidence",
        ),
        (
            {
                "kind": "rejected",
                "rejection_reason": "not_convenient",
                "evidence": "Compared against the bound acceptance test.",
            },
            "unknown rejection_reason",
        ),
    ),
)
def test_rejected_closure_requires_named_evidence_and_a_known_reason(
    active_finding_decisions: None,
    closure: dict[str, str],
    diagnostic: str,
) -> None:
    context = _context(previous=(_finding("C-01", AgentRole.CLAUDE),))
    document = _review(context, approved=False)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": "Claude rejects the reported defect.",
            "closure": closure,
        }
    ]

    with pytest.raises(NativeReviewContractError, match=diagnostic):
        parse_native_review_response(document, context)


def test_combined_review_budget_counts_status_and_class_before_effects(
    active_finding_decisions: None,
) -> None:
    previous = tuple(
        _finding(f"C-{number:02d}", AgentRole.CLAUDE)
        for number in range(1, 34)
    )
    context = _context(approval=ApprovalMarker.SLICE, previous=previous)
    document = _review(context, approved=False)
    document["status_changes"] = [{} for _ in range(30)]
    document["reclassifications"] = [{} for _ in range(3)]

    with pytest.raises(NativeReviewContractError) as raised:
        validate_native_review_disposition_budget(document, context)

    assert raised.value.disposition_limit == NativeReviewDispositionLimit(33, 32)
    assert "status_changes=30" in raised.value.detail
    assert "reclassifications=3" in raised.value.detail




def test_active_review_contract_accepts_typed_closure() -> None:
    context = _context(previous=(_finding("C-01", AgentRole.CLAUDE),))
    document = _review(context, approved=False)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": "The finding is fixed.",
            "closure": {"kind": "fixed"},
        }
    ]

    parsed = parse_native_review_response(document, context)
    assert isinstance(parsed, NativeReviewResult)
    assert parsed.status_changes[0].closure is not None
    assert parsed.status_changes[0].closure.kind.value == "fixed"


def _assert_error(
    document: dict[str, object],
    context: NativeReviewContext,
    code: NativeReviewErrorCode,
) -> None:
    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(document, context)
    assert raised.value.code is code


def test_minimal_positive_review_converts_deterministically() -> None:
    context = _context()
    document = _review(context)
    first = parse_native_contract_result(document, context)
    second = parse_native_contract_result(deepcopy(document), context)
    assert first == second
    assert first.approval is True
    assert first.validation is context.validation_attestation
    assert first.evidence is not None
    assert first.red_state_followup_slice is None


def test_named_red_state_followup_allows_complete_failed_validation() -> None:
    command = "python3 -m pytest tests/ -v"
    failed = replace(
        _attestation(),
        records=(ValidationRecord(ValidationStatus.FAIL, command, 1, "failed"),),
        summary="1 failed",
    )
    context = replace(
        _context(red_state_followup_slice="Slice 08 - repair validation"),
        validation_attestation=failed,
    )

    result = parse_native_contract_result(_review(context), context)

    assert result.approval is True
    assert result.validation is failed
    assert not result.validation.passed
    assert result.red_state_followup_slice == "Slice 08 - repair validation"


def test_new_blocker_uses_exact_context_origin_and_denies() -> None:
    context = _context()
    document = _review(context, approved=False)
    document["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "BLOCKER",
            "summary": "Native response may be misbound",
            "acceptance_test": {"kind": "prose", "text": "Reject wrong request id"},
            "affected_paths": [],
        }
    ]
    result = parse_native_contract_result(document, context)
    assert result.approval is False
    assert len(result.findings) == 1
    assert result.findings[0].origin == FindingOrigin("1", 2, AgentRole.CLAUDE)


def test_new_findings_must_start_at_next_reviewer_id_and_remain_contiguous() -> None:
    context = _context(previous=(_finding("C-01", AgentRole.CLAUDE),))
    document = _review(context, approved=False)
    document["status_changes"] = [
        {"finding_id": "C-01", "status": "CLOSED", "rationale": "Fixed", "closure": {"kind": "fixed"}}
    ]
    document["new_findings"] = [
        {
            "finding_id": "C-03",
            "finding_class": "BLOCKER",
            "summary": "Skipped the next id",
            "acceptance_test": {"kind": "prose", "text": "Use C-02 first"},
            "affected_paths": [],
        }
    ]
    _assert_error(document, context, NativeReviewErrorCode.FINDING_ID_INVALID)


def test_validation_command_reaches_existing_matrix_as_identical_argv() -> None:
    context = _context()
    document = _review(context, approved=False)
    argv = ("python3", "-m", "pytest", "tests/test_native_review_contract.py", "-v")
    document["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "BLOCKER",
            "summary": "Focused native regression required",
            "acceptance_test": {
                "kind": "validation_command",
                "argv": list(argv),
            },
            "affected_paths": ["tests/test_native_review_contract.py"],
        }
    ]
    result = parse_native_contract_result(document, context)
    assert result.findings[0].acceptance_test == (
        'VALIDATE: ["python3","-m","pytest",'
        '"tests/test_native_review_contract.py","-v"]'
    )
    request = select_validation_request(
        ValidationMatrix(
            default_command=ValidationCommand(
                argv=("python3", "-m", "pytest", "tests/", "-v")
            )
        ),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("src/native_review_contract.py",),
        findings=result.findings,
    )
    assert request.commands[-1].argv == argv


def _attestation_with_typed_acceptance(
    status: ValidationStatus,
    *,
    fingerprint: str = FINGERPRINT,
) -> ValidationAttestation:
    default = ValidationCommand(argv=("python3", "-m", "pytest", "tests/", "-v"))
    typed = ValidationCommand(argv=TYPED_ACCEPTANCE_ARGV)
    return ValidationAttestation(
        attestation_id=f"validation-typed-{fingerprint[:12]}-{status.value.lower()}",
        diff_fingerprint=fingerprint,
        expected_commands=(default.display, typed.display),
        records=(
            ValidationRecord(ValidationStatus.PASS, default.display, 0, "passed"),
            ValidationRecord(
                status,
                typed.display,
                0 if status is ValidationStatus.PASS else 1,
                status.value.lower(),
            ),
        ),
        output_digest=hashlib.sha256(
            f"{fingerprint}:{status.value}".encode("utf-8")
        ).hexdigest(),
        summary=f"typed acceptance {status.value.lower()}",
        command_specs=(
            ValidationCommandSpec(argv=default.argv),
            ValidationCommandSpec(argv=typed.argv),
        ),
    )


def _new_typed_blocker_document(context: NativeReviewContext) -> dict[str, object]:
    document = _review(context, approved=False)
    document["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "BLOCKER",
            "summary": "A focused regression exposes the defect.",
            "acceptance_test": {
                "kind": "validation_command",
                "argv": list(TYPED_ACCEPTANCE_ARGV),
            },
            "affected_paths": ["tests/test_native_review_contract.py"],
        }
    ]
    return document


def test_new_typed_blocker_rejects_command_already_green_at_binding() -> None:
    context = replace(
        _context(),
        validation_attestation=_attestation_with_typed_acceptance(
            ValidationStatus.PASS
        ),
        validation_command_prefixes=(
            ("python3", "-m", "pytest"),
            ("npm", "test"),
        ),
    )

    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(_new_typed_blocker_document(context), context)

    error = raised.value
    assert error.code is NativeReviewErrorCode.ACCEPTANCE_INVALID
    assert error.orchestrator_diagnostic is (
        OrchestratorDiagnostic.REVIEW_ACCEPTANCE_COMMAND_ALREADY_PASSING
    )
    assert error.detail == (
        "typed acceptance test for C-01 uses command "
        '["python3","-m","pytest","tests/test_native_review_contract.py","-q"], '
        f"which is already passing at the current fingerprint {FINGERPRINT}; "
        "bind a command that fails now so a later PASS can prove the fix; "
        "allowed command prefixes are: `python3 -m pytest`, `npm test`"
    )


def test_passing_typed_acceptance_with_no_prefixes_omits_guidance_suffix() -> None:
    with pytest.raises(NativeReviewContractError) as raised:
        reject_passing_typed_acceptance_binding(
            "C-01",
            TYPED_ACCEPTANCE_ARGV,
            _attestation_with_typed_acceptance(ValidationStatus.PASS),
            FINGERPRINT,
            (),
        )

    assert raised.value.detail == (
        "typed acceptance test for C-01 uses command "
        '["python3","-m","pytest","tests/test_native_review_contract.py","-q"], '
        f"which is already passing at the current fingerprint {FINGERPRINT}; "
        "bind a command that fails now so a later PASS can prove the fix"
    )


def test_new_typed_blocker_accepts_red_binding_and_can_later_close_fixed() -> None:
    opening_context = replace(
        _context(),
        validation_attestation=_attestation_with_typed_acceptance(
            ValidationStatus.FAIL
        ),
    )
    opened = parse_native_contract_result(
        _new_typed_blocker_document(opening_context), opening_context
    )
    post_change_fingerprint = "c" * 64
    measured = replace(
        opened.findings[0],
        acceptance_measurements=(
            _typed_measurement(FINGERPRINT, ValidationStatus.FAIL),
            _typed_measurement(post_change_fingerprint, ValidationStatus.PASS),
        ),
    )
    closing_context = replace(
        _context(previous=(measured,)),
        diff_fingerprint=post_change_fingerprint,
        validation_attestation=_attestation_with_typed_acceptance(
            ValidationStatus.PASS,
            fingerprint=post_change_fingerprint,
        ),
        pre_change_fingerprint=FINGERPRINT,
    )

    closed = parse_native_contract_result(
        _fixed_document(closing_context), closing_context
    )

    assert closed.findings[0].status is FindingStatus.CLOSED
    assert closed.finding_closures[0][1].kind.value == "fixed"


def test_observation_cannot_carry_validation_command() -> None:
    context = _context()
    document = _review(context, approved=False)
    document["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "summary": "Future hardening",
            "acceptance_test": {
                "kind": "validation_command",
                "argv": ["python3", "-m", "pytest", "tests/", "-v"],
            },
            "affected_paths": [],
        }
    ]
    _assert_error(document, context, NativeReviewErrorCode.ACCEPTANCE_INVALID)


def test_new_finding_id_must_belong_to_claude() -> None:
    context = _context()
    document = _review(context, approved=False)
    document["new_findings"] = [
        {
            "finding_id": "A-01",  # retirement-negative-control
            "finding_class": "BLOCKER",
            "summary": "Wrong owner",
            "acceptance_test": {"kind": "prose", "text": "Use the correct prefix"},
            "affected_paths": [],
        }
    ]
    _assert_error(document, context, NativeReviewErrorCode.SCHEMA_INVALID)


def test_slice_denial_preserves_omitted_open_finding_but_approval_rejects_it() -> None:
    blocker = _finding("C-01", AgentRole.CLAUDE)
    denied_context = _context(previous=(blocker,))

    denied = _review(denied_context, approved=False)
    result = parse_native_contract_result(denied, denied_context)
    assert result.approval is False
    assert result.findings[0].status is FindingStatus.OPEN

    observation = _finding(
        "C-01",
        AgentRole.CLAUDE,
        finding_class=FindingClass.OBSERVATION,
    )
    context = _context(previous=(observation,))

    approved = _review(context, approved=True)
    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(approved, context)
    assert raised.value.code is NativeReviewErrorCode.APPROVAL_INVALID
    assert "C-01" in raised.value.detail

    updated = _review(denied_context, approved=False)
    updated["status_changes"] = [
        {"finding_id": "C-01", "status": "OPEN", "rationale": "Still reproducible", "closure": None}
    ]
    assert (
        parse_native_contract_result(updated, denied_context).findings[0].status
        is FindingStatus.OPEN
    )


def test_repeated_finding_becomes_an_occurrence_of_the_existing_identifier() -> None:
    existing = replace(
        _finding("C-01", AgentRole.CLAUDE),
        summary="src/cache.py can retain stale entries.",
        acceptance_test="Reject stale entries in src/cache.py.",
    )
    context = _context(previous=(existing,))
    document = _review(context, approved=False)
    document["new_findings"] = [
        {
            "finding_id": "C-02",
            "finding_class": "BLOCKER",
            "summary": "src/cache.py can retain stale entries.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Reject stale entries in src/cache.py.",
            },
            "affected_paths": ["src/cache.py"],
        }
    ]

    result = parse_native_contract_result(document, context)

    assert len(result.findings) == 1
    occurrence = result.findings[0]
    assert occurrence.finding_id == "C-01"
    assert occurrence.status is FindingStatus.OPEN
    assert occurrence.status_rationale is not None
    assert "Additional occurrence reported as C-02" in occurrence.status_rationale
    assert "src/cache.py" in occurrence.status_rationale
    transitions = project_reviewer_persistence_transitions(
        (existing,), result.findings, work_unit_id="1"
    )
    assert [(item.finding.finding_id, item.action) for item in transitions] == [
        ("C-01", "status_changed")
    ]


def test_repeated_finding_keeps_other_results_and_renumbers_new_ids() -> None:
    existing = replace(
        _finding(
            "C-01",
            AgentRole.CLAUDE,
            finding_class=FindingClass.OBSERVATION,
        ),
        summary="src/old.py has executable file mode 100755.",
        acceptance_test=(
            "Normalize the file mode of non-script documents in src/old.py "
            "to non-executable 100644."
        ),
    )
    context = _context(previous=(existing,))
    document = _review(context, approved=False)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "OPEN",
            "rationale": "src/current.py is another TypeScript occurrence.",
            "closure": None,
        }
    ]
    document["new_findings"] = [
        {
            "finding_id": "C-02",
            "finding_class": "OBSERVATION",
            "summary": "app/public/assets/data.json has executable mode 100755.",
            "acceptance_test": {
                "kind": "prose",
                "text": (
                    "Normalize the file mode of non-script documents in "
                    "app/public/assets/data.json to non-executable 100644."
                ),
            },
            "affected_paths": ["app/public/assets/data.json"],
        },
        {
            "finding_id": "C-03",
            "finding_class": "BLOCKER",
            "summary": "src/cache.py discards valid entries.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Preserve valid entries in src/cache.py.",
            },
            "affected_paths": ["src/cache.py"],
        },
    ]

    result = parse_native_contract_result(document, context)

    assert [item.finding_id for item in result.findings] == ["C-01", "C-02"]
    assert result.findings[1].summary == "src/cache.py discards valid entries."
    rationale = result.findings[0].status_rationale or ""
    assert "src/current.py" in rationale
    assert "app/public/assets/data.json" in rationale


def test_contradictory_repeated_finding_is_still_rejected() -> None:
    existing = replace(
        _finding("C-01", AgentRole.CLAUDE),
        summary="src/cache.py can retain stale entries.",
        acceptance_test="Reject stale entries in src/cache.py.",
    )
    context = _context(previous=(existing,))
    document = _review(context, approved=False)
    document["status_changes"] = [
        {"finding_id": "C-01", "status": "CLOSED", "rationale": "Fixed.", "closure": {"kind": "fixed"}}
    ]
    document["new_findings"] = [
        {
            "finding_id": "C-02",
            "finding_class": "BLOCKER",
            "summary": "src/cache.py can retain stale entries.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Reject stale entries in src/cache.py.",
            },
            "affected_paths": ["src/cache.py"],
        }
    ]

    _assert_error(document, context, NativeReviewErrorCode.FINDING_EVENT_CONFLICT)


def test_genuine_new_problem_on_the_same_path_is_opened() -> None:
    existing = replace(
        _finding("C-01", AgentRole.CLAUDE),
        summary="src/cache.py can retain stale entries.",
        acceptance_test="Reject stale entries in src/cache.py.",
    )
    context = _context(previous=(existing,))
    document = _review(context, approved=False)
    document["new_findings"] = [
        {
            "finding_id": "C-02",
            "finding_class": "BLOCKER",
            "summary": "src/cache.py can discard valid entries.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Preserve valid entries in src/cache.py.",
            },
            "affected_paths": ["src/cache.py"],
        }
    ]

    result = parse_native_contract_result(document, context)

    assert [item.finding_id for item in result.findings] == ["C-01", "C-02"]


def test_existing_id_records_a_visible_open_to_open_occurrence() -> None:
    existing = replace(
        _finding("C-01", AgentRole.CLAUDE),
        finding_class=FindingClass.OBSERVATION,
    )
    context = _context(previous=(existing,))
    document = _review(context, approved=False)
    document["new_findings"] = [
        {
            "finding_id": "C-02",
            "finding_class": "BLOCKER",
            "summary": "The additional occurrence still needs correction.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Correct the additional occurrence before approval.",
            },
            "affected_paths": [],
        }
    ]
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "OPEN",
            "rationale": "Also occurs at src/second_site.py.",
            "closure": None,
        }
    ]
    result = parse_native_contract_result(document, context)
    transitions = project_reviewer_persistence_transitions(
        context.previous_findings,
        result.findings,
        work_unit_id="work-unit-1",
    )

    assert result.findings[0].status is FindingStatus.OPEN
    assert result.findings[0].status_rationale == "Also occurs at src/second_site.py."
    occurrence = tuple(
        item for item in transitions if item.finding.finding_id == "C-01"
    )
    assert len(occurrence) == 1
    assert occurrence[0].action == "status_changed"
    assert occurrence[0].rationale == "Also occurs at src/second_site.py."


def test_existing_id_rejects_an_invisible_open_to_open_noop() -> None:
    existing = replace(
        _finding("C-01", AgentRole.CLAUDE),
        finding_class=FindingClass.OBSERVATION,
        status_rationale="Already recorded at src/first_site.py.",
    )
    context = _context(previous=(existing,))
    document = _review(context, approved=True)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "OPEN",
            "rationale": "Already recorded at src/first_site.py.",
            "closure": None,
        }
    ]

    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(document, context)

    assert raised.value.code is NativeReviewErrorCode.FINDING_EVENT_CONFLICT


def test_slice_writer_exposes_sparse_approval_but_local_contract_rejects_it() -> None:
    previous = tuple(
        _finding(
            f"C-{number:02d}",
            AgentRole.CLAUDE,
            finding_class=FindingClass.OBSERVATION,
        )
        for number in range(1, 11)
    )
    context = _context(previous=previous)
    document = _review(context)
    document["new_findings"] = [
        {
            "finding_id": "C-11",
            "finding_class": "OBSERVATION",
            "summary": "A new cross-cutting follow-up remains.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Address the follow-up in a later slice.",
            },
            "affected_paths": [],
        }
    ]

    writer = native_review_provider_response_schema(context)
    validate_schema_document({"result": document}, writer)
    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(document, context)

    assert raised.value.code is NativeReviewErrorCode.APPROVAL_INVALID
    assert all(
        f"C-{number:02d}" in raised.value.detail for number in range(1, 12)
    )


@pytest.mark.parametrize(
    "approval",
    (ApprovalMarker.PLAN,),
)
def test_plan_approval_defers_complete_open_disposition_to_domain_contract(
    approval: ApprovalMarker,
) -> None:
    prior = _finding(
        "C-01",
        AgentRole.CLAUDE,
        finding_class=FindingClass.OBSERVATION,
    )
    context = _context(approval=approval, previous=(prior,))
    document = _review(context)
    writer = native_review_provider_response_schema(context)

    validate_schema_document({"result": document}, writer)
    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(document, context)
    assert raised.value.code is NativeReviewErrorCode.FINDING_UPDATE_MISSING
    assert raised.value.detail == (
        "missing updates for previous open findings: C-01"
    )


def test_large_plan_approval_rejects_combined_disposition_overflow() -> None:
    previous = tuple(
        _finding(f"C-{number:02d}", AgentRole.CLAUDE)
        for number in range(1, 34)
    )
    context = _context(approval=ApprovalMarker.PLAN, previous=previous)
    document = _review(context)
    document["status_changes"] = [
        {
            "finding_id": f"C-{number:02d}",
            "status": "CLOSED",
            "rationale": "The reviewed correction closes this blocker.",
            "closure": {"kind": "fixed"},
        }
        for number in range(1, 31)
    ]
    document["reclassifications"] = [
        {
            "finding_id": f"C-{number:02d}",
            "finding_class": "OBSERVATION",
            "rationale": "The remaining risk is non-blocking follow-up work.",
        }
        for number in range(31, 34)
    ]

    writer = native_review_provider_response_schema(context)
    validate_schema_document({"result": document}, writer)
    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(document, context)
    assert raised.value.disposition_limit == NativeReviewDispositionLimit(33, 32)


def test_slice_denial_accepts_nonempty_sparse_blocker_delivery() -> None:
    previous = tuple(
        _finding(f"C-{number:02d}", AgentRole.CLAUDE)
        for number in range(1, 4)
    )
    context = _context(approval=ApprovalMarker.SLICE, previous=previous)
    document = _review(context, approved=False)
    document["new_findings"] = []
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": "The Slice review disposes this carried blocker.",
            "closure": {"kind": "fixed"},
        }
    ]

    validate_schema_document(
        {"result": document}, native_review_provider_response_schema(context)
    )
    result = parse_native_contract_result(document, context)

    assert result.approval is False
    assert tuple(
        item.finding_id
        for item in result.findings
        if item.status is FindingStatus.OPEN
    ) == ("C-02", "C-03")


def test_slice_denial_preserves_open_blockers_for_no_progress_policy() -> None:
    previous = tuple(
        _finding(f"C-{number:02d}", AgentRole.CLAUDE)
        for number in range(1, 4)
    )
    context = _context(approval=ApprovalMarker.SLICE, previous=previous)
    document = _review(context, approved=False)

    validate_schema_document(
        {"result": document}, native_review_provider_response_schema(context)
    )
    result = parse_native_contract_result(document, context)

    assert result.approval is False
    assert project_open_set(result.findings).finding_ids == (
        "C-01",
        "C-02",
        "C-03",
    )


@pytest.mark.parametrize(
    ("kept_reclassification_ids", "missing_ids"),
    ((range(31, 33), ("C-33",)), (range(31, 32), ("C-32", "C-33"))),
)
def test_large_non_slice_approval_names_every_missing_disposition(
    kept_reclassification_ids: range,
    missing_ids: tuple[str, ...],
) -> None:
    previous = tuple(
        _finding(f"C-{number:02d}", AgentRole.CLAUDE)
        for number in range(1, 34)
    )
    context = _context(approval=ApprovalMarker.PLAN, previous=previous)
    document = _review(context)
    document["status_changes"] = [
        {
            "finding_id": f"C-{number:02d}",
            "status": "CLOSED",
            "rationale": "The reviewed correction closes this blocker.",
            "closure": {"kind": "fixed"},
        }
        for number in range(1, 31)
    ]
    document["reclassifications"] = [
        {
            "finding_id": f"C-{number:02d}",
            "finding_class": "OBSERVATION",
            "rationale": "The remaining risk is non-blocking follow-up work.",
        }
        for number in kept_reclassification_ids
    ]

    validate_schema_document(
        {"result": document}, native_review_provider_response_schema(context)
    )
    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(document, context)

    assert raised.value.code is NativeReviewErrorCode.FINDING_UPDATE_MISSING
    assert raised.value.detail == (
        "missing updates for previous open findings: " + ", ".join(missing_ids)
    )


def test_seventy_five_finding_plan_approval_names_exact_missing_updates() -> None:
    previous = tuple(
        _finding(f"C-{number:02d}", AgentRole.CLAUDE)
        for number in range(1, 76)
    )
    context = _context(approval=ApprovalMarker.PLAN, previous=previous)
    document = _review(context)
    document["status_changes"] = [
        {
            "finding_id": f"C-{number:02d}",
            "status": "CLOSED",
            "rationale": "The reviewed plan treatment closes this blocker.",
            "closure": {"kind": "fixed"},
        }
        for number in range(1, 33)
    ]

    validate_schema_document(
        {"result": document}, native_review_provider_response_schema(context)
    )
    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(document, context)

    assert raised.value.code is NativeReviewErrorCode.FINDING_UPDATE_MISSING
    assert raised.value.detail == (
        "missing updates for previous open findings: "
        + ", ".join(f"C-{number:02d}" for number in range(33, 76))
    )


@pytest.mark.parametrize(
    ("approval", "branch_name"),
    (
        (ApprovalMarker.PLAN, "plan"),
    ),
)
def test_large_non_slice_schema_has_no_disposition_partition_branch_list(
    approval: ApprovalMarker,
    branch_name: str,
) -> None:
    def approval_schema(finding_count: int) -> dict[str, object]:
        previous = tuple(
            _finding(f"C-{number:02d}", AgentRole.CLAUDE)
            for number in range(1, finding_count + 1)
        )
        context = _context(approval=approval, previous=previous)
        return native_review_provider_response_schema(context)["$defs"][
            f"bound_{branch_name}_approved"
        ]

    one_finding = approval_schema(1)
    thirty_three_findings = approval_schema(33)

    assert thirty_three_findings["anyOf"] == one_finding["anyOf"]
    assert len(thirty_three_findings["anyOf"]) == 4
    assert not any(
        {"status_changes", "reclassifications"}
        <= set(branch.get("properties", {}))
        for branch in thirty_three_findings["anyOf"]
    )


def test_closed_own_finding_is_neither_writer_offered_nor_locally_mutable() -> None:
    closed = _finding("C-01", AgentRole.CLAUDE, status=FindingStatus.CLOSED)
    open_finding = _finding("C-02", AgentRole.CLAUDE)
    context = _context(previous=(closed, open_finding))
    writer = native_review_provider_response_schema(context)

    reopened = _review(context, approved=False)
    reopened["status_changes"] = [
        {"finding_id": "C-01", "status": "OPEN", "rationale": "Reopen it", "closure": None}
    ]
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": reopened}, writer)

    reclassified = _review(context, approved=False)
    reclassified["reclassifications"] = [
        {
            "finding_id": "C-01",
            "finding_class": "BLOCKER",
            "rationale": "Reclassify it",
        }
    ]
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": reclassified}, writer)

    base = parse_native_review_response(_review(context, approved=False), context)
    assert isinstance(base, NativeReviewResult)
    direct_reopen = replace(
        base,
        status_changes=(
            NativeStatusChange("C-01", FindingStatus.OPEN, "Reopen it"),
        ),
    )
    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(direct_reopen, context)
    assert raised.value.code is NativeReviewErrorCode.FINDING_REFERENCE_NOT_OPEN

    direct_reclassification = replace(
        base,
        reclassifications=(
            NativeReclassification(
                "C-01", FindingClass.BLOCKER, "Reclassify it"
            ),
        ),
    )
    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(direct_reclassification, context)
    assert raised.value.code is NativeReviewErrorCode.FINDING_REFERENCE_NOT_OPEN


@pytest.mark.parametrize(
    ("finding_id", "error_code"),
    (
        ("C-99", NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN),
        ("C-02", NativeReviewErrorCode.FINDING_REFERENCE_NOT_OPEN),
    ),
)
def test_domain_rejects_unknown_or_non_open_disposition(
    finding_id: str,
    error_code: NativeReviewErrorCode,
) -> None:
    context = _context(
        previous=(
            _finding("C-01", AgentRole.CLAUDE),
            _finding(
                "C-02",
                AgentRole.CLAUDE,
                status=FindingStatus.CLOSED,
            ),
        )
    )
    base = parse_native_review_response(_review(context, approved=False), context)
    assert isinstance(base, NativeReviewResult)
    response = replace(
        base,
        status_changes=(
            NativeStatusChange(
                finding_id,
                FindingStatus.OPEN,
                "Apply only to a known open finding.",
            ),
        ),
    )

    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(response, context)

    assert raised.value.code is error_code


def test_denial_cannot_create_its_required_blocker_by_reopening_closed_blocker() -> None:
    closed = _finding("C-01", AgentRole.CLAUDE, status=FindingStatus.CLOSED)
    context = _context(previous=(closed,))
    writer = native_review_provider_response_schema(context)
    observation = NativeFinding(
        "C-02",
        FindingClass.OBSERVATION,
        "Future hardening",
        NativeProseAcceptance("Consider this in a later slice"),
    )

    document = _review(context, approved=False)
    document["new_findings"] = [
        {
            "finding_id": "C-02",
            "finding_class": "OBSERVATION",
            "summary": "Future hardening",
            "acceptance_test": {
                "kind": "prose",
                "text": "Consider this in a later slice",
            },
            "affected_paths": [],
        }
    ]
    document["status_changes"] = [
        {"finding_id": "C-01", "status": "OPEN", "rationale": "Reopen it", "closure": None}
    ]
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": document}, writer)

    direct = NativeReviewResult(
        request_id=context.request_id,
        reviewer=AgentRole.CLAUDE,
        approved=False,
        new_findings=(observation,),
        status_changes=(
            NativeStatusChange("C-01", FindingStatus.OPEN, "Reopen it"),
        ),
        reclassifications=(),
        anchors=(),
        evidence=None,
        pre_mortem=None,
    )
    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(direct, context)
    assert raised.value.code is NativeReviewErrorCode.FINDING_REFERENCE_NOT_OPEN

    control = replace(direct, status_changes=())
    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(control, context)
    assert raised.value.code is NativeReviewErrorCode.APPROVAL_INVALID


def test_same_slice_correction_cannot_reclassify_blocker_to_observation() -> None:
    blocker = _finding("C-01", AgentRole.CLAUDE)
    context = replace(
        _context(
            approval=ApprovalMarker.SLICE,
            previous=(blocker,),
            allow_observations=False,
        ),
        operation="claude_slice_review",
        slice_id="01",
    )
    document = _review(context, approved=False)
    document["reclassifications"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "rationale": "Defer this blocker beyond the current Slice.",
        }
    ]
    writer = native_review_provider_response_schema(context)
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": document}, writer)

    parsed = parse_native_review_response(document, context)
    assert isinstance(parsed, NativeReviewResult)
    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(parsed, context)
    assert raised.value.code is NativeReviewErrorCode.APPROVAL_INVALID

def test_test_change_and_observation_convergence_guards() -> None:
    test_context = _context(test_files=("tests/test_native_review_contract.py",))
    _assert_error(_review(test_context), test_context, NativeReviewErrorCode.APPROVAL_INVALID)

    convergence = _context(allow_observations=False)
    observation = _review(convergence, approved=False)
    observation["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "summary": "Future idea",
            "acceptance_test": {"kind": "prose", "text": "Consider later"},
            "affected_paths": [],
        }
    ]
    _assert_error(observation, convergence, NativeReviewErrorCode.APPROVAL_INVALID)

    prior_blocker = _finding("C-01", AgentRole.CLAUDE)
    reclass_context = _context(previous=(prior_blocker,), allow_observations=False)
    reclassified = _review(reclass_context, approved=False)
    reclassified["reclassifications"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "rationale": "No longer blocking",
        }
    ]
    _assert_error(reclassified, reclass_context, NativeReviewErrorCode.APPROVAL_INVALID)

    approved_context = _context(
        test_files=("tests/test_native_review_contract.py",), tests_approved=True
    )
    assert parse_native_contract_result(
        _review(approved_context), approved_context
    ).test_files == ("tests/test_native_review_contract.py",)


def test_denied_review_requires_open_own_blocker() -> None:
    context = _context()
    _assert_error(_review(context, approved=False), context, NativeReviewErrorCode.APPROVAL_INVALID)


def test_direct_domain_conversion_rejects_contentless_review() -> None:
    context = _context()
    response = NativeReviewResult(
        request_id=context.request_id,
        reviewer=AgentRole.CLAUDE,
        approved=True,
        new_findings=(),
        status_changes=(),
        reclassifications=(),
        anchors=(),
        evidence=None,
        pre_mortem="A later adapter could bypass transport validation.",
    )

    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(response, context)

    assert raised.value.code is NativeReviewErrorCode.REVIEW_CONTENT_MISSING


def test_direct_domain_conversion_rejects_blank_finding_content() -> None:
    context = _context()
    response = NativeReviewResult(
        request_id=context.request_id,
        reviewer=AgentRole.CLAUDE,
        approved=False,
        new_findings=(
            NativeFinding(
                finding_id="C-01",
                finding_class=FindingClass.BLOCKER,
                summary="   ",
                acceptance_test=NativeProseAcceptance("Focused regression"),
            ),
        ),
        status_changes=(),
        reclassifications=(),
        anchors=(),
        evidence=None,
        pre_mortem=None,
    )

    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(response, context)

    assert raised.value.code is NativeReviewErrorCode.FINDING_CONTENT_INVALID


@pytest.mark.parametrize(
    "field",
    ("dimensions", "largest_residual_risk", "break_condition"),
)
def test_whitespace_review_evidence_is_a_typed_native_error(field: str) -> None:
    context = _context()
    document = _review(context)
    evidence = document["review_evidence"]
    assert isinstance(evidence, dict)
    evidence[field] = "   "

    _assert_error(
        document,
        context,
        NativeReviewErrorCode.REVIEW_CONTENT_MISSING,
    )


def test_whitespace_status_rationale_has_a_content_error_not_reference_error() -> None:
    own = _finding("C-01", AgentRole.CLAUDE)
    context = _context(previous=(own,))
    document = _review(context, approved=False)
    document["status_changes"] = [
        {"finding_id": "C-01", "status": "OPEN", "rationale": "   ", "closure": None}
    ]

    _assert_error(
        document,
        context,
        NativeReviewErrorCode.FINDING_CONTENT_INVALID,
    )


def test_whitespace_pre_mortem_cannot_approve() -> None:
    context = _context()
    document = _review(context)
    document["pre_mortem"] = "   "

    _assert_error(document, context, NativeReviewErrorCode.APPROVAL_INVALID)


def test_direct_non_string_pre_mortem_is_a_typed_native_error() -> None:
    context = _context()
    parsed = parse_native_review_response(_review(context), context)
    assert isinstance(parsed, NativeReviewResult)
    malformed = replace(parsed, pre_mortem=42)  # type: ignore[arg-type]

    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(malformed, context)

    assert raised.value.code is NativeReviewErrorCode.APPROVAL_INVALID


def test_whitespace_stop_fields_are_a_typed_native_error() -> None:
    context = _context()
    document = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "stop_request",
        "request_id": context.request_id,
        "reviewer": "claude",
        "rule_id": "   ",
        "rationale": "Cannot continue.",
        "remediation_paths": [],
    }

    _assert_error(document, context, NativeReviewErrorCode.STOP_CONTENT_INVALID)


def test_direct_non_string_stop_field_is_a_typed_native_error() -> None:
    context = _context()
    response = NativeStopResult(
        request_id=context.request_id,
        reviewer=AgentRole.CLAUDE,
        rule_id=42,  # type: ignore[arg-type]
        rationale="Cannot continue.",
    )

    with pytest.raises(NativeReviewContractError) as raised:
        native_response_to_contract_result(response, context)

    assert raised.value.code is NativeReviewErrorCode.STOP_CONTENT_INVALID


def test_positive_review_requires_premortem_attestation_and_no_own_blocker() -> None:
    context = _context()
    no_pre_mortem = _review(context)
    no_pre_mortem["pre_mortem"] = None
    _assert_error(no_pre_mortem, context, NativeReviewErrorCode.APPROVAL_INVALID)

    no_attestation = replace(context, validation_attestation=None)
    _assert_error(
        _review(no_attestation), no_attestation, NativeReviewErrorCode.APPROVAL_INVALID
    )

    blocker = _finding("C-01", AgentRole.CLAUDE)
    blocker_context = _context(previous=(blocker,))
    with_open_blocker = _review(blocker_context)
    with_open_blocker["status_changes"] = [
        {"finding_id": "C-01", "status": "OPEN", "rationale": "Still blocking", "closure": None}
    ]
    _assert_error(
        with_open_blocker, blocker_context, NativeReviewErrorCode.APPROVAL_INVALID
    )


def test_native_anchors_preserve_context_origin_and_trigger_drift_guard() -> None:
    context = _context()
    document = _review(context)
    document["anchors"] = [
        {
            "anchor_id": "tax-01",
            "input_fixture": "income=100",
            "expected": "42",
            "tolerance": "exact",
        },
        {
            "anchor_id": "tax-02",
            "input_fixture": "income=200",
            "expected": "80",
            "tolerance": "0.01",
        },
    ]
    result = parse_native_contract_result(document, context)
    assert {item.origin for item in result.anchors} == {"approved-plan-v1"}
    approved = (
        AnchorRecord("tax-01", "approved-plan-v1", "income=100", "41", "exact"),
    )
    changes = detect_anchor_changes(approved, result.anchors)
    assert changes is not None
    assert changes.changes.changed == ("tax-01",)
    assert changes.changes.added == ("tax-02",)


def test_native_anchor_requires_bound_origin() -> None:
    context = _context(anchor_origin=None)
    document = _review(context)
    document["anchors"] = [
        {
            "anchor_id": "tax-01",
            "input_fixture": "income=100",
            "expected": "42",
            "tolerance": "exact",
        }
    ]
    _assert_error(document, context, NativeReviewErrorCode.ANCHOR_INVALID)


def test_stop_request_has_explicit_safe_contract_result_defaults() -> None:
    context = _context(test_files=("tests/test_native_review_contract.py",), tests_approved=True)
    document = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "stop_request",
        "request_id": context.request_id,
        "reviewer": "claude",
        "rule_id": "UNEXPECTED-PATH",
        "rationale": "A required path is outside the bound scope.",
        "remediation_paths": [],
    }
    result = parse_native_contract_result(document, context)
    assert result.stopped is True
    assert result.stop_request is not None
    assert result.stop_request.remediation_paths == ()
    assert result.anchors == ()
    assert result.test_files == ()
    assert result.evidence is None
    assert result.pre_mortem is None
    assert result.validation is context.validation_attestation
