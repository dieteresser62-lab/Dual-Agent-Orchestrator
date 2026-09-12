from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib

import pytest

from contracts import (
    AgentRole,
    AnchorRecord,
    ApprovalMarker,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from finding_reducer import project_open_set, project_reviewer_persistence_transitions
from gates import detect_anchor_changes
from native_review_contract import (
    NATIVE_REVIEW_RETRYABLE_FORM_CODES,
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
    native_review_provider_response_schema,
    parse_native_contract_result,
    validate_native_review_disposition_budget,
    parse_native_review_response,
)
from schema_validation import SchemaMismatch, validate_schema_document
from validation_matrix import (
    ValidationCommand,
    ValidationMatrix,
    select_validation_request,
)
from workflow_state import WorkUnitKind


FINGERPRINT = "a" * 64


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
        {"finding_id": "C-01", "status": "CLOSED", "rationale": "Fixed"}
    ]
    document["new_findings"] = [
        {
            "finding_id": "C-03",
            "finding_class": "BLOCKER",
            "summary": "Skipped the next id",
            "acceptance_test": {"kind": "prose", "text": "Use C-02 first"},
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
        }
    ]
    _assert_error(document, context, NativeReviewErrorCode.SCHEMA_INVALID)


def test_slice_review_preserves_omitted_open_finding_when_decision_allows_it() -> None:
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
    result = parse_native_contract_result(approved, context)
    assert result.approval is True
    assert result.findings[0].status is FindingStatus.OPEN

    updated = _review(denied_context, approved=False)
    updated["status_changes"] = [
        {"finding_id": "C-01", "status": "OPEN", "rationale": "Still reproducible"}
    ]
    assert (
        parse_native_contract_result(updated, denied_context).findings[0].status
        is FindingStatus.OPEN
    )


def test_repeated_finding_is_rejected_with_the_existing_identifier() -> None:
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
        }
    ]

    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(document, context)

    assert raised.value.code is NativeReviewErrorCode.FINDING_SIGNATURE_DUPLICATE
    assert "C-01" in raised.value.detail


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
    document = _review(context, approved=True)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "OPEN",
            "rationale": "Also occurs at src/second_site.py.",
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
    assert len(transitions) == 1
    assert transitions[0].action == "status_changed"
    assert transitions[0].rationale == "Also occurs at src/second_site.py."


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
        }
    ]

    with pytest.raises(NativeReviewContractError) as raised:
        parse_native_contract_result(document, context)

    assert raised.value.code is NativeReviewErrorCode.FINDING_EVENT_CONFLICT


def test_slice_writer_accepts_one_new_finding_without_ten_open_dispositions() -> None:
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
        }
    ]

    writer = native_review_provider_response_schema(context)
    validate_schema_document({"result": document}, writer)
    result = parse_native_contract_result(document, context)

    assert tuple(item.finding_id for item in result.findings) == tuple(
        f"C-{number:02d}" for number in range(1, 12)
    )
    transitions = project_reviewer_persistence_transitions(
        previous,
        result.findings,
        work_unit_id="2",
    )
    assert tuple(item.finding.finding_id for item in transitions) == ("C-11",)
    assert tuple(item.action for item in transitions) == ("opened",)


@pytest.mark.parametrize(
    "approval",
    (ApprovalMarker.PLAN, ApprovalMarker.FINAL),
)
def test_non_slice_approval_defers_complete_open_disposition_to_domain_contract(
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


def test_large_non_slice_approval_accepts_complete_mixed_disposition() -> None:
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
    result = parse_native_contract_result(document, context)

    assert result.approval is True
    assert tuple(
        item.finding_id
        for item in result.findings
        if item.status is FindingStatus.OPEN
    ) == (
        "C-31",
        "C-32",
        "C-33",
    )


def test_large_final_denial_accepts_thirty_closures_and_three_escalations() -> None:
    previous = tuple(
        _finding(
            f"C-{number:02d}",
            AgentRole.CLAUDE,
            finding_class=(
                FindingClass.BLOCKER
                if number <= 30
                else FindingClass.OBSERVATION
            ),
        )
        for number in range(1, 34)
    )
    context = _context(approval=ApprovalMarker.FINAL, previous=previous)
    document = _review(context, approved=False)
    document["status_changes"] = [
        {
            "finding_id": f"C-{number:02d}",
            "status": "CLOSED",
            "rationale": "The branch correction closes this blocker.",
        }
        for number in range(1, 31)
    ]
    document["reclassifications"] = [
        {
            "finding_id": f"C-{number:02d}",
            "finding_class": "BLOCKER",
            "rationale": "The final review escalates this remaining defect.",
        }
        for number in range(31, 34)
    ]

    writer = native_review_provider_response_schema(context)
    validate_schema_document({"result": document}, writer)
    result = parse_native_contract_result(document, context)

    assert result.approval is False
    assert tuple(
        (item.finding_id, item.finding_class)
        for item in result.findings
        if item.status is FindingStatus.OPEN
    ) == (
        ("C-31", FindingClass.BLOCKER),
        ("C-32", FindingClass.BLOCKER),
        ("C-33", FindingClass.BLOCKER),
    )


def test_final_disposition_budget_rejects_combined_event_overflow() -> None:
    previous = tuple(
        _finding(f"C-{number:02d}", AgentRole.CLAUDE)
        for number in range(1, 34)
    )
    context = _context(approval=ApprovalMarker.FINAL, previous=previous)
    document = _review(context, approved=False)
    document["status_changes"] = [
        {
            "finding_id": f"C-{number:02d}",
            "status": "CLOSED",
            "rationale": "Verified in the branch-wide evidence.",
        }
        for number in range(1, 31)
    ]
    document["reclassifications"] = [
        {
            "finding_id": f"C-{number:02d}",
            "finding_class": "BLOCKER",
            "rationale": "The unresolved defect remains blocking.",
        }
        for number in range(31, 34)
    ]

    with pytest.raises(NativeReviewContractError) as raised:
        validate_native_review_disposition_budget(document, context)

    assert raised.value.disposition_limit == NativeReviewDispositionLimit(33, 32)


def test_final_denial_accepts_nonempty_partial_observation_delivery() -> None:
    previous = tuple(
        _finding(
            f"C-{number:02d}",
            AgentRole.CLAUDE,
            finding_class=FindingClass.OBSERVATION,
        )
        for number in range(1, 4)
    )
    context = _context(approval=ApprovalMarker.FINAL, previous=previous)
    document = _review(context, approved=False)
    document["new_findings"] = []
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": "The final review disposes this carried observation.",
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


def test_final_denial_accepts_zero_progress_with_open_observations() -> None:
    previous = tuple(
        _finding(
            f"C-{number:02d}",
            AgentRole.CLAUDE,
            finding_class=FindingClass.OBSERVATION,
        )
        for number in range(1, 4)
    )
    context = _context(approval=ApprovalMarker.FINAL, previous=previous)
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


def test_final_denial_accepts_closed_offer_when_unoffered_findings_remain() -> None:
    offered = (_finding("C-107", AgentRole.CLAUDE),)
    context = replace(
        _context(approval=ApprovalMarker.FINAL, previous=offered),
        final_review_pending_count=5,
    )
    document = _review(context, approved=False)
    document["status_changes"] = [
        {
            "finding_id": "C-107",
            "status": "CLOSED",
            "rationale": "The offered finding is resolved.",
        }
    ]

    result = parse_native_contract_result(document, context)

    assert result.approval is False
    assert result.findings[0].status is FindingStatus.CLOSED


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


@pytest.mark.parametrize(
    ("last_closed_id", "missing_ids"),
    ((32, ("C-33",)), (31, ("C-32", "C-33"))),
)
def test_large_final_approval_names_every_missing_disposition(
    last_closed_id: int,
    missing_ids: tuple[str, ...],
) -> None:
    previous = tuple(
        _finding(f"C-{number:02d}", AgentRole.CLAUDE)
        for number in range(1, 34)
    )
    context = _context(approval=ApprovalMarker.FINAL, previous=previous)
    document = _review(context)
    document["status_changes"] = [
        {
            "finding_id": f"C-{number:02d}",
            "status": "CLOSED",
            "rationale": "The branch correction closes this blocker.",
        }
        for number in range(1, last_closed_id + 1)
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


def test_seventy_five_finding_final_approval_names_exact_missing_dispositions() -> None:
    previous = tuple(
        _finding(f"C-{number:02d}", AgentRole.CLAUDE)
        for number in range(1, 76)
    )
    context = _context(approval=ApprovalMarker.FINAL, previous=previous)
    document = _review(context)
    document["status_changes"] = [
        {
            "finding_id": f"C-{number:02d}",
            "status": "CLOSED",
            "rationale": "The branch correction closes this blocker.",
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
        (ApprovalMarker.FINAL, "final"),
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
        {"finding_id": "C-01", "status": "OPEN", "rationale": "Reopen it"}
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
                FindingStatus.CLOSED,
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
        }
    ]
    document["status_changes"] = [
        {"finding_id": "C-01", "status": "OPEN", "rationale": "Reopen it"}
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


def test_production_final_context_cannot_reclassify_blocker_to_observation() -> None:
    blocker = _finding("C-01", AgentRole.CLAUDE)
    unit_kind = WorkUnitKind.FINAL_REVIEW
    context = replace(
        _context(
            approval=ApprovalMarker.FINAL,
            previous=(blocker,),
            allow_observations=unit_kind is not WorkUnitKind.CORRECTION,
        ),
        operation="claude_final_review",
        slice_id="FINAL",
    )
    document = _review(context, approved=False)
    document["reclassifications"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "rationale": "Defer this blocker beyond final review.",
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
        {"finding_id": "C-01", "status": "OPEN", "rationale": "   "}
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
        {"finding_id": "C-01", "status": "OPEN", "rationale": "Still blocking"}
    ]
    _assert_error(
        with_open_blocker, blocker_context, NativeReviewErrorCode.APPROVAL_INVALID
    )


def test_final_review_rejects_new_observation_and_open_own_observation() -> None:
    final = _context(approval=ApprovalMarker.FINAL)
    new_observation = _review(final)
    new_observation["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "summary": "Future idea",
            "acceptance_test": {"kind": "prose", "text": "Consider later"},
        }
    ]
    _assert_error(new_observation, final, NativeReviewErrorCode.APPROVAL_INVALID)

    prior = _finding(
        "C-01",
        AgentRole.CLAUDE,
        finding_class=FindingClass.OBSERVATION,
    )
    prior_context = _context(approval=ApprovalMarker.FINAL, previous=(prior,))
    still_open = _review(prior_context)
    still_open["status_changes"] = [
        {"finding_id": "C-01", "status": "OPEN", "rationale": "Still relevant"}
    ]
    _assert_error(still_open, prior_context, NativeReviewErrorCode.APPROVAL_INVALID)


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


def test_retryable_form_code_inventory_excludes_only_local_and_identity_bindings() -> None:
    assert set(NativeReviewErrorCode) - set(NATIVE_REVIEW_RETRYABLE_FORM_CODES) == {
        NativeReviewErrorCode.CONTEXT_INVALID,
        NativeReviewErrorCode.REQUEST_MISMATCH,
        NativeReviewErrorCode.REVIEWER_MISMATCH,
    }
