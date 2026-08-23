from __future__ import annotations

import json

import pytest

from contracts import (
    AgentRole,
    CodexStepContract,
    ContractValidationError,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingResponseDecision,
    FindingStatus,
    ReadinessMarker,
    validate_codex_response,
)
from native_codex_contract import (
    BoundNativeCodexContext,
    NativeCodexContext,
    NativeCodexContractError,
    NativeCodexErrorCode,
    NativeCodexRequestKind,
    canonical_native_codex_json,
    load_native_codex_schema,
    native_codex_provider_response_schema,
    parse_bound_native_codex_contract_result,
)


def _finding() -> FindingRecord:
    return FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="Close the native boundary.",
        acceptance_test="The native result round-trips.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )


def _bound(
    kind: NativeCodexRequestKind,
    *,
    findings: tuple[FindingRecord, ...] = (),
    expected_tests: tuple[str, ...] = (),
    test_changes_approved: bool = False,
) -> BoundNativeCodexContext:
    readiness = {
        NativeCodexRequestKind.PLAN: ReadinessMarker.PLAN,
        NativeCodexRequestKind.IMPLEMENTATION: ReadinessMarker.IMPLEMENTATION,
        NativeCodexRequestKind.CORRECTION: ReadinessMarker.IMPLEMENTATION,
        NativeCodexRequestKind.FINAL_REPORT: ReadinessMarker.FINAL_REPORT,
    }[kind]
    contract = CodexStepContract(
        name=f"native-{kind.value}",
        readiness_marker=readiness,
        slice_id="01",
        round_number=1,
        require_test_files_record=kind in {
            NativeCodexRequestKind.IMPLEMENTATION,
            NativeCodexRequestKind.CORRECTION,
        },
        expected_test_files=expected_tests,
        test_changes_approved=test_changes_approved,
        require_slice_plan=kind is NativeCodexRequestKind.PLAN,
        plan_artifact_path=("docs/internal/plan.md" if kind is NativeCodexRequestKind.PLAN else None),
    )
    context = NativeCodexContext(
        run_id="run-1",
        work_unit_id="work-unit-1",
        operation=f"codex_{kind.value}",
        current_fingerprint="a" * 64,
        request_kind=kind,
        contract=contract,
        previous_findings=findings,
    )
    return BoundNativeCodexContext(
        context=context,
        request_id="native-codex-request-" + "b" * 64,
        request_digest="b" * 64,
    )


def _base(bound: BoundNativeCodexContext, result_type: str) -> dict[str, object]:
    return {
        "schema_version": "native-agent-codex-result-v1",
        "result_type": result_type,
        "request_id": bound.request_id,
    }


def test_native_codex_schema_is_checked_and_canonical() -> None:
    assert load_native_codex_schema()["$id"] == "native-agent-codex-result-v1"
    bound = _bound(NativeCodexRequestKind.PLAN)
    document = {
        **_base(bound, "plan_result"),
        "ready": True,
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Implement native contracts.",
                "scope_paths": ["src/native_codex_contract.py"],
            }
        ],
    }
    canonical = canonical_native_codex_json(document)
    assert canonical == json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.ready is True
    assert result.slice_plan[0].slice_id == 1


def test_historical_plan_result_without_dispositions_remains_readable() -> None:
    bound = _bound(NativeCodexRequestKind.PLAN)
    historical = {
        **_base(bound, "plan_result"),
        "ready": True,
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Historical native plan.",
                "scope_paths": ["docs/internal/plan.md"],
            }
        ],
    }

    provider_schema = native_codex_provider_response_schema()
    persisted_schema = load_native_codex_schema()
    result = parse_bound_native_codex_contract_result(historical, bound)

    assert result.findings == ()
    assert "finding_dispositions" not in (
        persisted_schema["$defs"]["plan_result"]["required"]
    )
    assert "finding_dispositions" in (
        provider_schema["$defs"]["plan_result"]["required"]
    )


def test_plan_revision_requires_every_open_finding_disposition() -> None:
    bound = _bound(NativeCodexRequestKind.PLAN, findings=(_finding(),))
    document = {
        **_base(bound, "plan_result"),
        "ready": True,
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Revise the native plan.",
                "scope_paths": ["docs/internal/plan.md"],
            }
        ],
        "finding_dispositions": [],
    }
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)
    assert raised.value.code is NativeCodexErrorCode.FINDING_REFERENCE_INVALID

    document["finding_dispositions"] = [
        {
            "finding_id": "C-01",
            "decision": "accepted",
            "rationale": "The revised plan now closes the contractual gap.",
        }
    ]
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.findings[0].responses[-1].decision is FindingResponseDecision.ACCEPTED


def test_implementation_result_applies_every_open_finding_disposition() -> None:
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        findings=(_finding(),),
        expected_tests=("tests/test_native_codex_contract.py",),
        test_changes_approved=True,
    )
    document = {
        **_base(bound, "implementation_result"),
        "ready": True,
        "test_files": ["tests/test_native_codex_contract.py"],
        "finding_dispositions": [
            {
                "finding_id": "C-01",
                "decision": "accepted",
                "rationale": "The implementation now covers it.",
            }
        ],
    }
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.test_files == ("tests/test_native_codex_contract.py",)
    assert result.findings[0].responses[0].decision is FindingResponseDecision.ACCEPTED


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda document: document.update(
                request_id="native-codex-request-" + "0" * 64
            ),
            NativeCodexErrorCode.REQUEST_MISMATCH,
        ),
        (
            lambda document: document.update(result_type="correction_result"),
            NativeCodexErrorCode.RESULT_KIND_MISMATCH,
        ),
    ],
)
def test_native_result_rejects_wrong_request_or_result_kind(mutate, code) -> None:  # type: ignore[no-untyped-def]
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        expected_tests=(),
        test_changes_approved=True,
    )
    document = {
        **_base(bound, "implementation_result"),
        "ready": True,
        "test_files": [],
        "finding_dispositions": [],
    }
    mutate(document)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)
    assert raised.value.code is code


def test_native_result_rejects_missing_or_foreign_dispositions() -> None:
    bound = _bound(
        NativeCodexRequestKind.CORRECTION,
        findings=(_finding(),),
        expected_tests=(),
        test_changes_approved=True,
    )
    document = {
        **_base(bound, "correction_result"),
        "ready": True,
        "test_files": [],
        "finding_dispositions": [],
    }
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)
    assert raised.value.code is NativeCodexErrorCode.FINDING_REFERENCE_INVALID


def test_native_and_legacy_corrections_share_open_finding_completeness() -> None:
    bound = _bound(
        NativeCodexRequestKind.CORRECTION,
        findings=(_finding(),),
        expected_tests=(),
        test_changes_approved=True,
    )
    native_document = {
        **_base(bound, "correction_result"),
        "ready": True,
        "test_files": [],
        "finding_dispositions": [],
    }
    with pytest.raises(NativeCodexContractError) as native_error:
        parse_bound_native_codex_contract_result(native_document, bound)
    assert native_error.value.code is NativeCodexErrorCode.FINDING_REFERENCE_INVALID

    legacy_output = "\n".join(
        (
            "TEST_FILES_TOUCHED: NONE",
            "IMPLEMENTATION_READY: 01 | YES",
            "STATUS: DONE",
        )
    )
    with pytest.raises(
        ContractValidationError,
        match="missing FINDING_RESPONSE for open finding C-01",
    ):
        validate_codex_response(
            legacy_output, bound.context.contract, bound.context.previous_findings
        )


def test_correction_result_roundtrips_ready_tests_and_finding_response() -> None:
    bound = _bound(
        NativeCodexRequestKind.CORRECTION,
        findings=(_finding(),),
        expected_tests=("tests/test_native_codex_contract.py",),
        test_changes_approved=True,
    )
    document = {
        **_base(bound, "correction_result"),
        "ready": True,
        "test_files": ["tests/test_native_codex_contract.py"],
        "finding_dispositions": [
            {
                "finding_id": "C-01",
                "decision": "accepted",
                "rationale": "The correction implements the requested invariant.",
            }
        ],
    }
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.ready is True
    assert result.test_files == ("tests/test_native_codex_contract.py",)
    assert result.findings[0].responses[0].decision is FindingResponseDecision.ACCEPTED
    assert result.self_check is None


def test_final_report_roundtrips_self_check_and_finding_response() -> None:
    bound = _bound(
        NativeCodexRequestKind.FINAL_REPORT,
        findings=(_finding(),),
    )
    document = {
        **_base(bound, "final_report_result"),
        "ready": True,
        "finding_dispositions": [
            {
                "finding_id": "C-01",
                "decision": "rejected",
                "rationale": "The alleged defect is disproven by the bound evidence.",
            }
        ],
        "self_check": "Checked contracts, failure paths, resume, and idempotency.",
    }
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.ready is True
    assert result.test_files == ()
    assert result.self_check == (
        "Checked contracts, failure paths, resume, and idempotency."
    )
    assert result.findings[0].responses[0].decision is FindingResponseDecision.REJECTED


def test_final_report_rejects_incomplete_or_foreign_finding_dispositions() -> None:
    bound = _bound(
        NativeCodexRequestKind.FINAL_REPORT,
        findings=(_finding(),),
    )
    document = {
        **_base(bound, "final_report_result"),
        "ready": True,
        "finding_dispositions": [],
        "self_check": "Checked the branch.",
    }
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)
    assert raised.value.code is NativeCodexErrorCode.FINDING_REFERENCE_INVALID

    document["finding_dispositions"] = [
        {
            "finding_id": "C-02",
            "decision": "accepted",
            "rationale": "Foreign finding.",
        }
    ]
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)
    assert raised.value.code is NativeCodexErrorCode.FINDING_REFERENCE_INVALID


def test_ready_test_changes_require_prior_approval() -> None:
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        expected_tests=("tests/test_native_codex_contract.py",),
    )
    document = {
        **_base(bound, "implementation_result"),
        "ready": True,
        "test_files": ["tests/test_native_codex_contract.py"],
        "finding_dispositions": [],
    }
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)
    assert raised.value.code is NativeCodexErrorCode.TEST_FILES_INVALID


def test_final_report_requires_nonblank_self_check() -> None:
    bound = _bound(NativeCodexRequestKind.FINAL_REPORT)
    document = {
        **_base(bound, "final_report_result"),
        "ready": True,
        "finding_dispositions": [],
        "self_check": "   ",
    }
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)
    assert raised.value.code is NativeCodexErrorCode.SCHEMA_INVALID


def test_stop_result_is_exclusive_and_preserves_remediation_paths() -> None:
    bound = _bound(NativeCodexRequestKind.IMPLEMENTATION)
    document = {
        **_base(bound, "stop_result"),
        "rule_id": "UNEXPECTED-PATH",
        "rationale": "The schema path is outside scope.",
        "remediation_paths": ["schemas/native-agent-codex-result-v1.schema.json"],
    }
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.stopped is True
    assert result.ready is None
    assert result.stop_request is not None
    assert result.stop_request.remediation_paths == (
        "schemas/native-agent-codex-result-v1.schema.json",
    )


def test_unknown_properties_and_unsorted_paths_fail_closed() -> None:
    bound = _bound(NativeCodexRequestKind.IMPLEMENTATION)
    document = {
        **_base(bound, "implementation_result"),
        "ready": False,
        "test_files": ["tests/z.py", "tests/a.py"],
        "finding_dispositions": [],
    }
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)
    assert raised.value.code is NativeCodexErrorCode.TEST_FILES_INVALID
    document["test_files"] = []
    document["STATUS"] = "DONE"
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)
    assert raised.value.code is NativeCodexErrorCode.SCHEMA_INVALID
