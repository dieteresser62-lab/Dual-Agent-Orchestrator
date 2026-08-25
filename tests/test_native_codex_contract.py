from __future__ import annotations

import json
from dataclasses import replace

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
from native_provider_schema import registered_exceptions
from schema_validation import SchemaMismatch, validate_schema_document


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
    require_slice_plan: bool | None = None,
    enforce_expected_test_files: bool = True,
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
        enforce_expected_test_files=enforce_expected_test_files,
        require_slice_plan=(
            kind is NativeCodexRequestKind.PLAN
            if require_slice_plan is None
            else require_slice_plan
        ),
        plan_artifact_path=(
            "docs/internal/plan.md"
            if kind is NativeCodexRequestKind.PLAN
            and require_slice_plan is not False
            else None
        ),
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
        "schema_version": "native-agent-codex-result-v2",
        "result_type": result_type,
        "request_id": bound.request_id,
    }


def test_native_codex_schema_is_checked_and_canonical() -> None:
    assert load_native_codex_schema()["$id"] == "native-agent-codex-result-v2"
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
        "finding_dispositions": [],
    }
    canonical = canonical_native_codex_json(document)
    assert canonical == json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.ready is True
    assert result.slice_plan[0].slice_id == 1


def test_v2_plan_result_without_dispositions_is_rejected() -> None:
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

    provider_schema = native_codex_provider_response_schema(bound.context)
    persisted_schema = load_native_codex_schema()
    with pytest.raises(SchemaMismatch):
        validate_schema_document(historical, persisted_schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(historical, bound)
    assert raised.value.code is NativeCodexErrorCode.SCHEMA_INVALID
    assert "finding_dispositions" in persisted_schema["$defs"]["plan_result"][
        "required"
    ]
    assert provider_schema["type"] == "object"
    assert provider_schema["required"] == ["result"]
    assert "oneOf" not in provider_schema
    assert "finding_dispositions" in provider_schema["$defs"]["plan_result"][
        "required"
    ]


def test_provider_schema_uses_explicit_scalar_types_and_closed_objects() -> None:
    provider_schema = native_codex_provider_response_schema(
        _bound(NativeCodexRequestKind.PLAN).context
    )

    def visit(node: object) -> None:
        if isinstance(node, dict):
            assert "uniqueItems" not in node
            assert "oneOf" not in node
            assert "(?" not in str(node.get("pattern", ""))
            if "const" in node or "enum" in node:
                assert "type" in node
            if node.get("type") == "object" and "properties" in node:
                assert node.get("additionalProperties") is False
                assert set(node["properties"]) == set(node.get("required", []))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(provider_schema)


def test_writer_schema_exposes_only_bound_result_kind_and_stop() -> None:
    expected = {
        NativeCodexRequestKind.PLAN: "plan_result",
        NativeCodexRequestKind.IMPLEMENTATION: "implementation_result",
        NativeCodexRequestKind.CORRECTION: "correction_result",
        NativeCodexRequestKind.FINAL_REPORT: "final_report_result",
    }
    digests: set[str] = set()
    for kind, result_name in expected.items():
        schema = native_codex_provider_response_schema(_bound(kind).context)
        result_options = schema["properties"]["result"]["anyOf"]
        assert result_options[-1] == {"$ref": "#/$defs/stop_result"}
        if kind in {
            NativeCodexRequestKind.IMPLEMENTATION,
            NativeCodexRequestKind.CORRECTION,
        }:
            assert all(
                result_name in item["$ref"]
                for item in result_options[:-1]
            )
        else:
            assert result_options[0] == {"$ref": f"#/$defs/{result_name}"}
        digests.add(json.dumps(schema, sort_keys=True, separators=(",", ":")))
    assert len(digests) == 4


def test_plan_without_slice_contract_offers_only_stop_result() -> None:
    bound = _bound(
        NativeCodexRequestKind.PLAN,
        require_slice_plan=False,
    )
    schema = native_codex_provider_response_schema(bound.context)
    assert schema["properties"]["result"]["anyOf"] == [
        {"$ref": "#/$defs/stop_result"}
    ]

    plan = {
        **_base(bound, "plan_result"),
        "ready": False,
        "slice_plan": [],
        "finding_dispositions": [],
    }
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": plan}, schema)


def test_unapproved_bound_test_changes_force_ready_false_in_writer() -> None:
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        expected_tests=("tests/test_native_codex_contract.py",),
        test_changes_approved=False,
    )
    schema = native_codex_provider_response_schema(bound.context)
    response = {
        **_base(bound, "implementation_result"),
        "ready": True,
        "test_files": ["tests/test_native_codex_contract.py"],
        "finding_dispositions": [],
    }

    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": response}, schema)
    response["ready"] = False
    validate_schema_document({"result": response}, schema)


def test_unapproved_dynamic_test_scope_allows_ready_only_without_test_files() -> None:
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        expected_tests=("tests/expected.py",),
        test_changes_approved=False,
        enforce_expected_test_files=False,
    )
    schema = native_codex_provider_response_schema(bound.context)
    response = {
        **_base(bound, "implementation_result"),
        "ready": True,
        "test_files": [],
        "finding_dispositions": [],
    }

    validate_schema_document({"result": response}, schema)
    assert parse_bound_native_codex_contract_result(response, bound).ready is True

    response["test_files"] = ["tests/new.py"]
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": response}, schema)

    response["ready"] = False
    validate_schema_document({"result": response}, schema)


def test_unapproved_empty_fixed_test_scope_closes_nonempty_ready_result() -> None:
    bound = _bound(
        NativeCodexRequestKind.CORRECTION,
        expected_tests=(),
        test_changes_approved=False,
        enforce_expected_test_files=True,
    )
    schema = native_codex_provider_response_schema(bound.context)
    response = {
        **_base(bound, "correction_result"),
        "ready": True,
        "test_files": ["tests/new.py"],
        "finding_dispositions": [],
    }

    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": response}, schema)
    response["test_files"] = []
    validate_schema_document({"result": response}, schema)
    assert parse_bound_native_codex_contract_result(response, bound).ready is True


@pytest.mark.parametrize("enforce_expected", [True, False])
def test_unapproved_readiness_projection_has_no_nested_any_of(
    enforce_expected: bool,
) -> None:
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        expected_tests=("tests/expected.py",),
        test_changes_approved=False,
        enforce_expected_test_files=enforce_expected,
    )
    schema = native_codex_provider_response_schema(bound.context)
    result_options = schema["properties"]["result"]["anyOf"]

    assert result_options[-1] == {"$ref": "#/$defs/stop_result"}
    assert len(result_options) == (2 if enforce_expected else 3)
    assert all(set(option) == {"$ref"} for option in result_options)


def test_writer_schema_closes_finding_membership_and_cardinality() -> None:
    second = replace(_finding(), finding_id="C-02")
    bound = _bound(
        NativeCodexRequestKind.CORRECTION,
        findings=(_finding(), second),
        test_changes_approved=True,
    )
    schema = native_codex_provider_response_schema(bound.context)
    base = {
        **_base(bound, "correction_result"),
        "ready": False,
        "test_files": [],
    }
    valid_dispositions = [
        {"finding_id": finding_id, "decision": "accepted", "rationale": "fixed"}
        for finding_id in ("C-01", "C-02")
    ]
    validate_schema_document(
        {"result": {**base, "finding_dispositions": valid_dispositions}}, schema
    )
    for invalid in (
        valid_dispositions[:1],
        [*valid_dispositions, valid_dispositions[-1]],
        [valid_dispositions[0], {**valid_dispositions[1], "finding_id": "C-99"}],
    ):
        with pytest.raises(SchemaMismatch):
            validate_schema_document(
                {"result": {**base, "finding_dispositions": invalid}}, schema
            )


def test_writer_schema_leaves_only_registered_disposition_order_exception() -> None:
    second = replace(_finding(), finding_id="C-02")
    bound = _bound(
        NativeCodexRequestKind.CORRECTION,
        findings=(_finding(), second),
        test_changes_approved=True,
    )
    schema = native_codex_provider_response_schema(bound.context)
    duplicate = {
        **_base(bound, "correction_result"),
        "ready": False,
        "test_files": [],
        "finding_dispositions": [
            {"finding_id": "C-01", "decision": "accepted", "rationale": "fixed"},
            {"finding_id": "C-01", "decision": "accepted", "rationale": "fixed"},
        ],
    }

    validate_schema_document({"result": duplicate}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(duplicate, bound)
    assert raised.value.code is NativeCodexErrorCode.FINDING_REFERENCE_INVALID


def test_writer_schema_keeps_safe_path_validation_fail_closed_locally() -> None:
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        test_changes_approved=True,
        enforce_expected_test_files=False,
    )
    schema = native_codex_provider_response_schema(bound.context)
    response = {
        **_base(bound, "implementation_result"),
        "ready": False,
        "test_files": ["../escape.py"],
        "finding_dispositions": [],
    }

    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.SCHEMA_INVALID


def test_writer_schema_keeps_nonblank_text_validation_fail_closed_locally() -> None:
    bound = _bound(NativeCodexRequestKind.FINAL_REPORT)
    schema = native_codex_provider_response_schema(bound.context)
    response = {
        **_base(bound, "final_report_result"),
        "ready": False,
        "finding_dispositions": [],
        "self_check": "   ",
    }

    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.SCHEMA_INVALID


@pytest.mark.parametrize("enforce_expected", [True, False])
def test_writer_schema_keeps_test_file_order_fail_closed_locally(
    enforce_expected: bool,
) -> None:
    expected = ("tests/a.py", "tests/b.py") if enforce_expected else ()
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        expected_tests=expected,
        test_changes_approved=True,
        enforce_expected_test_files=enforce_expected,
    )
    schema = native_codex_provider_response_schema(bound.context)
    response = {
        **_base(bound, "implementation_result"),
        "ready": False,
        "test_files": ["tests/b.py", "tests/a.py"],
        "finding_dispositions": [],
    }

    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.TEST_FILES_INVALID


def test_writer_schema_keeps_slice_path_order_fail_closed_locally() -> None:
    bound = _bound(NativeCodexRequestKind.PLAN)
    schema = native_codex_provider_response_schema(bound.context)
    response = {
        **_base(bound, "plan_result"),
        "ready": False,
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Implement the contract.",
                "scope_paths": ["tests/test_contract.py", "src/contract.py"],
            }
        ],
        "finding_dispositions": [],
    }

    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.SLICE_PLAN_INVALID

    response["slice_plan"] = [
        {
            "slice_id": 3,
            "summary": "Implement the contract.",
            "scope_paths": ["src/contract.py", "tests/test_contract.py"],
        }
    ]
    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.SLICE_PLAN_INVALID


def test_writer_schema_keeps_stop_path_order_fail_closed_locally() -> None:
    bound = _bound(NativeCodexRequestKind.PLAN)
    schema = native_codex_provider_response_schema(bound.context)
    response = {
        **_base(bound, "stop_result"),
        "rule_id": "UNEXPECTED-PATH",
        "rationale": "The correction requires additional paths.",
        "remediation_paths": ["tests/test_contract.py", "src/contract.py"],
    }

    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.STOP_CONTENT_INVALID


def test_writer_schema_keeps_cyclic_request_id_binding_fail_closed_locally() -> None:
    bound = _bound(NativeCodexRequestKind.PLAN)
    schema = native_codex_provider_response_schema(bound.context)
    response = {
        **_base(bound, "plan_result"),
        "request_id": "native-codex-request-" + "c" * 64,
        "ready": False,
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Implement the contract.",
                "scope_paths": ["src/contract.py"],
            }
        ],
        "finding_dispositions": [],
    }

    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.REQUEST_MISMATCH


def test_registered_exception_codes_cover_writer_valid_local_rejections() -> None:
    second = replace(_finding(), finding_id="C-02")
    disposition_bound = _bound(
        NativeCodexRequestKind.CORRECTION,
        findings=(_finding(), second),
        test_changes_approved=True,
    )
    plan_bound = _bound(NativeCodexRequestKind.PLAN)
    work_bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        test_changes_approved=True,
        enforce_expected_test_files=False,
    )
    final_bound = _bound(NativeCodexRequestKind.FINAL_REPORT)
    cases = (
        (
            disposition_bound,
            {
                **_base(disposition_bound, "correction_result"),
                "ready": False,
                "test_files": [],
                "finding_dispositions": [
                    {
                        "finding_id": "C-01",
                        "decision": "accepted",
                        "rationale": "fixed",
                    },
                    {
                        "finding_id": "C-01",
                        "decision": "accepted",
                        "rationale": "fixed",
                    },
                ],
            },
        ),
        (
            final_bound,
            {
                **_base(final_bound, "final_report_result"),
                "ready": False,
                "finding_dispositions": [],
                "self_check": "   ",
            },
        ),
        (
            plan_bound,
            {
                **_base(plan_bound, "plan_result"),
                "request_id": "native-codex-request-" + "c" * 64,
                "ready": False,
                "slice_plan": [
                    {
                        "slice_id": 1,
                        "summary": "Implement the contract.",
                        "scope_paths": ["src/contract.py"],
                    }
                ],
                "finding_dispositions": [],
            },
        ),
        (
            plan_bound,
            {
                **_base(plan_bound, "plan_result"),
                "ready": False,
                "slice_plan": [
                    {
                        "slice_id": 1,
                        "summary": "Implement the contract.",
                        "scope_paths": ["tests/test_contract.py", "src/contract.py"],
                    }
                ],
                "finding_dispositions": [],
            },
        ),
        (
            plan_bound,
            {
                **_base(plan_bound, "stop_result"),
                "rule_id": "UNEXPECTED-PATH",
                "rationale": "Additional paths are required.",
                "remediation_paths": ["tests/test_contract.py", "src/contract.py"],
            },
        ),
        (
            work_bound,
            {
                **_base(work_bound, "implementation_result"),
                "ready": False,
                "test_files": ["tests/b.py", "tests/a.py"],
                "finding_dispositions": [],
            },
        ),
    )
    encountered: set[str] = set()
    for bound, response in cases:
        validate_schema_document(
            {"result": response}, native_codex_provider_response_schema(bound.context)
        )
        with pytest.raises(NativeCodexContractError) as raised:
            parse_bound_native_codex_contract_result(response, bound)
        encountered.add(raised.value.code.value)
    registered = {
        str(item["error_code"]) for item in registered_exceptions("codex")
    }

    assert registered == encountered


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
        "remediation_paths": ["schemas/native-agent-codex-result-v2.schema.json"],
    }
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.stopped is True
    assert result.ready is None
    assert result.stop_request is not None
    assert result.stop_request.remediation_paths == (
        "schemas/native-agent-codex-result-v2.schema.json",
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
