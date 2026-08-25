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
from gates import detect_anchor_changes
from native_review_contract import (
    NativeFinding,
    NativeProseAcceptance,
    NativeReclassification,
    NativeReviewContext,
    NativeReviewContractError,
    NativeReviewErrorCode,
    NativeReviewResult,
    NativeStatusChange,
    NativeStopResult,
    native_response_to_contract_result,
    native_review_provider_response_schema,
    parse_native_contract_result,
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
    )


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


def test_denial_preserves_omitted_open_finding_but_approval_requires_update() -> None:
    own = _finding("C-01", AgentRole.CLAUDE)
    context = _context(previous=(own,))

    denied = _review(context, approved=False)
    result = parse_native_contract_result(denied, context)
    assert result.approval is False
    assert result.findings[0].status is FindingStatus.OPEN

    missing_from_approval = _review(context, approved=True)
    _assert_error(
        missing_from_approval,
        context,
        NativeReviewErrorCode.FINDING_UPDATE_MISSING,
    )

    updated = _review(context, approved=False)
    updated["status_changes"] = [
        {"finding_id": "C-01", "status": "OPEN", "rationale": "Still reproducible"}
    ]
    assert parse_native_contract_result(updated, context).findings[0].status is FindingStatus.OPEN


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
