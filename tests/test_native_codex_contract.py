from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

import native_codex_contract
import native_finding_decisions

from acceptance_criteria import acceptance_criterion_id
from contracts import (
    AgentRole,
    CodexStepContract,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingResponseDecision,
    FindingStatus,
    ReadinessMarker,
)
from finding_reducer import project_finding_response_delta
from finding_signature import finding_record_signature
from finding_responsibility import SliceResponsibility
from native_codex_contract import (
    BoundNativeCodexContext,
    NativeCodexContext,
    NativeCodexContractError,
    NativeCodexErrorCode,
    NativeCodexRequestKind,
    canonical_native_codex_json,
    load_native_codex_schema,
    native_codex_provider_response_schema,
    native_responsibility_proposals,
    parse_native_codex_response,
    parse_bound_native_codex_contract_result,
)
from native_provider_schema import (
    NativeProviderSchemaError,
    assert_projected_provider_schema,
    defensive_provider_projection,
    registered_exceptions,
)
from orchestrator_diagnostics import OrchestratorDiagnostic
from schema_validation import SchemaMismatch, validate_schema_document


_COMMUNICATED_PROVIDER_CONTRACT_RULES = (
    "planned_slice.scope_paths",
    "finding_dispositions",
    "safe_text",
    "safe_path",
    "slice_plan.slice_id",
    "stop_result.remediation_paths",
    "work_result.test_files",
)

# B70 semantic audit: these meanings remain unstated after the six requested
# field descriptions and the stop-result routing instruction are added.  There
# are more than three, so the task's stop condition keeps this as an explicit
# inventory rather than expanding the Slice into opportunistic contract prose.
_REMAINING_PROVIDER_SEMANTIC_GAPS = (
    "plan_result, implementation_result, and correction_result: "
    "task-specific result purposes",
    "planned_slice.slice_id, summary, and scope_paths: execution semantics",
    "finding_disposition.finding_id, decision, and rationale: lifecycle effects",
    "work_result.test_files: whether paths denote touched or executed tests",
    "stop_result.remediation_paths: operational meaning beyond canonical form",
)


def _criterion(text: str, measured_against: str = "SOURCE") -> dict[str, str]:
    return {"text": text, "measured_against": measured_against}


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
    document: dict[str, object] = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": result_type,
        "request_id": bound.request_id,
    }
    if result_type == "plan_result":
        document.update(
            plan_treatments=[],
            plan_completion="IMPLEMENTATION_REQUIRED",
        )
    return document


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
                "acceptance_criteria": [_criterion("The native contract is implemented.")],
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
                "acceptance_criteria": [_criterion("The plan remains well formed.")],
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


def test_generated_codex_writer_schema_passes_provider_conformance_ratchet() -> None:
    provider_schema = native_codex_provider_response_schema(
        _bound(NativeCodexRequestKind.PLAN).context
    )

    assert_projected_provider_schema(provider_schema, provider="codex")

    pending: list[object] = [provider_schema]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            assert "oneOf" not in node
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)


def test_writer_schema_exposes_only_bound_result_kind_and_stop() -> None:
    expected = {
        NativeCodexRequestKind.PLAN: "plan_result",
        NativeCodexRequestKind.IMPLEMENTATION: "implementation_result",
        NativeCodexRequestKind.CORRECTION: "correction_result",
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
    assert len(digests) == 3


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
        {"finding_id": finding_id, "decision": "accepted", "rationale": "fixed", "responsibility_proposal": None}
        for finding_id in ("C-01", "C-02")
    ]
    validate_schema_document(
        {"result": {**base, "finding_dispositions": valid_dispositions}}, schema
    )
    validate_schema_document(
        {"result": {**base, "finding_dispositions": valid_dispositions[:1]}}, schema
    )
    for invalid in (
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
            {"finding_id": "C-01", "decision": "accepted", "rationale": "fixed", "responsibility_proposal": None},
            {"finding_id": "C-01", "decision": "accepted", "rationale": "fixed", "responsibility_proposal": None},
        ],
    }
    provider_output = json.loads(json.dumps(duplicate))

    validate_schema_document({"result": duplicate}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(duplicate, bound)
    assert raised.value.code is NativeCodexErrorCode.FINDING_REFERENCE_INVALID
    assert raised.value.detail == "finding dispositions must be sorted and unique"
    assert duplicate == provider_output


def test_finding_dispositions_use_natural_order_beyond_one_hundred() -> None:
    finding_ids = (
        "C-62",
        "C-71",
        "C-101",
        "C-102",
        "C-103",
        "C-104",
        "C-105",
    )
    findings = tuple(replace(_finding(), finding_id=finding_id) for finding_id in finding_ids)
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        findings=findings,
        test_changes_approved=True,
    )
    base = {
        **_base(bound, "implementation_result"),
        "ready": True,
        "test_files": [],
    }

    def dispositions(ids: tuple[str, ...]) -> list[dict[str, str]]:
        return [
            {
                "finding_id": finding_id,
                "decision": "accepted",
                "rationale": "The finding is addressed.",
                "responsibility_proposal": None,
            }
            for finding_id in ids
        ]

    accepted = {**base, "finding_dispositions": dispositions(finding_ids)}
    assert parse_bound_native_codex_contract_result(accepted, bound).ready is True

    unsorted = (*finding_ids[:2], "C-105", *finding_ids[2:6])
    for invalid in (unsorted, (*finding_ids, "C-105")):
        with pytest.raises(NativeCodexContractError) as raised:
            parse_bound_native_codex_contract_result(
                {**base, "finding_dispositions": dispositions(invalid)}, bound
            )
        assert raised.value.code is NativeCodexErrorCode.FINDING_REFERENCE_INVALID
        assert raised.value.detail == "finding dispositions must be sorted and unique"


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
    provider_output = json.loads(json.dumps(response))

    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.SCHEMA_INVALID
    assert raised.value.detail == (
        "schema validation failed at <response>: must match exactly one allowed schema"
    )
    assert response == provider_output


def test_writer_schema_keeps_nonblank_text_validation_fail_closed_locally() -> None:
    for request_kind in NativeCodexRequestKind:
        bound = _bound(request_kind)
        schema = native_codex_provider_response_schema(bound.context)
        response = {
            **_base(bound, "stop_result"),
            "rule_id": "CONTRACT-UNCLEAR",
            "rationale": "   ",
            "remediation_paths": [],
        }
        provider_output = json.loads(json.dumps(response))

        validate_schema_document({"result": response}, schema)
        with pytest.raises(NativeCodexContractError) as raised:
            parse_bound_native_codex_contract_result(response, bound)
        assert raised.value.code is NativeCodexErrorCode.SCHEMA_INVALID
        assert raised.value.detail == (
            "schema validation failed at <response>: must match exactly one allowed schema"
        )
        assert response == provider_output


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
    provider_output = json.loads(json.dumps(response))

    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.TEST_FILES_INVALID
    assert raised.value.detail == "test_files must be sorted and unique"
    assert response == provider_output


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
                "acceptance_criteria": [_criterion("The contract is implemented.")],
            }
        ],
        "finding_dispositions": [],
    }
    provider_output = json.loads(json.dumps(response))

    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.SLICE_PLAN_INVALID
    assert raised.value.detail == (
        "planned slice paths must be sorted, unique, and non-empty"
    )
    assert raised.value.orchestrator_diagnostic is (
        OrchestratorDiagnostic.SLICE_PLAN_PATHS_INVALID
    )
    assert response == provider_output

    response["slice_plan"] = [
        {
            "slice_id": 3,
            "summary": "Implement the contract.",
            "scope_paths": ["src/contract.py", "tests/test_contract.py"],
            "acceptance_criteria": [_criterion("The contract is implemented.")],
        }
    ]
    provider_output = json.loads(json.dumps(response))
    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.SLICE_PLAN_INVALID
    assert raised.value.detail == "slice plan ids must be contiguous and 1-based"
    assert response == provider_output


def test_all_seven_local_contract_rules_survive_provider_projection() -> None:
    projected = defensive_provider_projection(
        load_native_codex_schema(),
        provider="codex",
        required_features=("closed_object", "min_max_items", "nested_any_of"),
    )
    definitions = projected["$defs"]
    finding_dispositions = [
        definitions[result_name]["properties"]["finding_dispositions"]
        for result_name in (
            "plan_result",
            "implementation_result",
            "correction_result",
        )
    ]
    test_files = [
        definitions[result_name]["properties"]["test_files"]
        for result_name in ("implementation_result", "correction_result")
    ]
    rules = {
        "planned_slice.scope_paths": (
            [definitions["planned_slice"]["properties"]["scope_paths"]],
            "Each scope_paths array must be non-empty, contain no duplicates, "
            "and list paths in ascending lexicographic order.",
        ),
        "finding_dispositions": (
            finding_dispositions,
            "List sparse dispositions in ascending finding_id order with no "
            "duplicate finding_id.",
        ),
        "safe_text": (
            [definitions["safe_text"]],
            "Text must contain at least one non-whitespace character and must "
            "not contain NUL.",
        ),
        "safe_path": (
            [definitions["safe_path"]],
            "Paths must be canonical repository-relative POSIX paths without "
            "traversal and must remain outside .orchestrator.",
        ),
        "slice_plan.slice_id": (
            [definitions["plan_result"]["properties"]["slice_plan"]],
            "slice_id values must start at 1 and increase contiguously in "
            "slice_plan order.",
        ),
        "stop_result.remediation_paths": (
            [definitions["stop_result"]["properties"]["remediation_paths"]],
            "List canonical remediation_paths in ascending lexicographic order "
            "with no duplicates.",
        ),
        "work_result.test_files": (
            test_files,
            "List canonical test_files paths in ascending lexicographic order "
            "with no duplicates.",
        ),
    }

    assert tuple(rules) == _COMMUNICATED_PROVIDER_CONTRACT_RULES
    for nodes, expected_description in rules.values():
        assert all(node["description"] == expected_description for node in nodes)


def test_b70_semantic_descriptions_survive_provider_projection() -> None:
    projected = defensive_provider_projection(
        load_native_codex_schema(),
        provider="codex",
        required_features=("closed_object", "min_max_items", "nested_any_of"),
    )
    definitions = projected["$defs"]
    result_names = (
        "plan_result",
        "implementation_result",
        "correction_result",
    )
    ready_description = (
        "Set ready to true only when the requested step is complete and "
        "orchestration may continue. A false value halts the step but does not "
        "document a blocker; if the step cannot be performed, return "
        "stop_result instead."
    )

    assert all(
        definitions[result_name]["properties"]["ready"]["description"]
        == ready_description
        for result_name in result_names
    )
    stop = definitions["stop_result"]
    assert stop["properties"]["rule_id"]["description"] == (
        "Identifies the rule that blocks the current step."
    )
    assert stop["properties"]["rationale"]["description"] == (
        "Explains the blocker that prevents the current step from being performed."
    )
    assert stop["properties"]["rule_id"]["anyOf"] == [
        {"$ref": "#/$defs/safe_text"}
    ]
    assert stop["properties"]["rationale"]["anyOf"] == [
        {"$ref": "#/$defs/safe_text"}
    ]
    for kind in NativeCodexRequestKind:
        writer_definitions = native_codex_provider_response_schema(
            _bound(kind).context
        )["$defs"]
        assert writer_definitions[
            f"{kind.value}_result"
        ]["properties"]["ready"]["description"] == ready_description
        for definition_name, definition in writer_definitions.items():
            if definition_name.startswith(f"bound_{kind.value}_result_ready_"):
                assert definition["properties"]["ready"]["description"] == (
                    ready_description
                )


def test_b71_projected_schema_has_all_descriptions_and_no_ref_siblings() -> None:
    schema = native_codex_provider_response_schema(
        _bound(NativeCodexRequestKind.PLAN).context
    )
    descriptions: list[str] = []
    ref_siblings: list[tuple[str, ...]] = []
    pending: list[object] = [schema]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if "description" in node:
                descriptions.append(node["description"])
            if "$ref" in node and set(node) != {"$ref"}:
                ref_siblings.append(tuple(sorted(set(node) - {"$ref"})))
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)

    assert len(descriptions) == 17
    assert "Identifies the rule that blocks the current step." in descriptions
    assert (
        "Explains the blocker that prevents the current step from being performed."
        in descriptions
    )
    assert ref_siblings == []


def test_b78_writer_schema_closes_stop_rule_vocabulary() -> None:
    bound = _bound(NativeCodexRequestKind.IMPLEMENTATION)
    schema = native_codex_provider_response_schema(bound.context)
    rule_id = schema["$defs"]["stop_result"]["properties"]["rule_id"]
    assert rule_id == {
        "description": "Identifies the rule that blocks the current step.",
        "type": "string",
        "enum": sorted(bound.context.known_stop_rule_ids),
    }

    response = {
        **_base(bound, "stop_result"),
        "rule_id": "SCOPE-DECISION-REQUIRED",
        "rationale": "A stale guard requires a scope decision.",
        "remediation_paths": [],
    }
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": response}, schema)

    response["rule_id"] = "CONTRACT-UNCLEAR"
    validate_schema_document({"result": response}, schema)


def test_b71_provider_guard_rejects_description_beside_any_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = copy.deepcopy(load_native_codex_schema())
    invalid["$defs"]["planned_slice"]["properties"]["summary"][
        "description"
    ] = "A future description beside a ref must fail closed."
    monkeypatch.setattr(
        native_codex_contract,
        "load_native_codex_schema",
        lambda: copy.deepcopy(invalid),
    )

    with pytest.raises(
        NativeProviderSchemaError,
        match=r"/\$defs/planned_slice/properties/summary: \$ref must not have sibling",
    ):
        native_codex_provider_response_schema(
            _bound(NativeCodexRequestKind.PLAN).context
        )


def test_b70_remaining_provider_semantic_gap_inventory_is_explicit() -> None:
    assert len(_REMAINING_PROVIDER_SEMANTIC_GAPS) > 3
    assert _REMAINING_PROVIDER_SEMANTIC_GAPS == (
        "plan_result, implementation_result, and correction_result: "
        "task-specific result purposes",
        "planned_slice.slice_id, summary, and scope_paths: execution semantics",
        "finding_disposition.finding_id, decision, and rationale: lifecycle effects",
        "work_result.test_files: whether paths denote touched or executed tests",
        "stop_result.remediation_paths: operational meaning beyond canonical form",
    )


def test_writer_schema_keeps_stop_path_order_fail_closed_locally() -> None:
    bound = _bound(NativeCodexRequestKind.PLAN)
    schema = native_codex_provider_response_schema(bound.context)
    response = {
        **_base(bound, "stop_result"),
        "rule_id": "UNEXPECTED-PATH",
        "rationale": "The correction requires additional paths.",
        "remediation_paths": ["tests/test_contract.py", "src/contract.py"],
    }
    provider_output = json.loads(json.dumps(response))

    validate_schema_document({"result": response}, schema)
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(response, bound)
    assert raised.value.code is NativeCodexErrorCode.STOP_CONTENT_INVALID
    assert raised.value.detail == "remediation_paths must be sorted and unique"
    assert response == provider_output


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
                "acceptance_criteria": [_criterion("The contract is implemented.")],
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
                        "responsibility_proposal": None,
                    },
                    {
                        "finding_id": "C-01",
                        "decision": "accepted",
                        "rationale": "fixed",
                        "responsibility_proposal": None,
                    },
                ],
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
                        "acceptance_criteria": [_criterion("The contract is implemented.")],
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
                        "acceptance_criteria": [_criterion("The contract is implemented.")],
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

    assert registered - encountered == {"schema-invalid"}
    assert encountered <= registered


def test_plan_revision_accepts_sparse_finding_dispositions() -> None:
    bound = _bound(NativeCodexRequestKind.PLAN, findings=(_finding(),))
    document = {
        **_base(bound, "plan_result"),
        "ready": True,
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Revise the native plan.",
                "scope_paths": ["docs/internal/plan.md"],
                "acceptance_criteria": [_criterion("The native plan is revised.")],
            }
        ],
        "finding_dispositions": [],
        "plan_treatments": [
            {
                "signature": finding_record_signature(_finding()),
                "finding_ids": ["C-01"],
                "treatment_kind": "implementation",
                "closing_slice_ids": [1],
                "no_code_reason": None,
                "evidence": None,
                "evidence_paths": [],
                "affected_paths": [],
            }
        ],
    }
    validate_schema_document(
        {"result": document}, native_codex_provider_response_schema(bound.context)
    )
    unchanged = parse_bound_native_codex_contract_result(document, bound)
    assert unchanged.findings == bound.context.previous_findings

    document["finding_dispositions"] = [
        {
            "finding_id": "C-01",
            "decision": "accepted",
            "rationale": "The revised plan now closes the contractual gap.",
            "responsibility_proposal": None,
        }
    ]
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.findings[0].responses[-1].decision is FindingResponseDecision.ACCEPTED


def test_enabled_plan_contract_carries_ordered_acceptance_criteria_losslessly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    finding = _finding()
    bound = _bound(NativeCodexRequestKind.PLAN, findings=(finding,))
    texts = ["An unrelated reviewer-owned condition.", "Replay remains stable."]
    document = {
        **_base(bound, "plan_result"),
        "ready": True,
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Build the record fact.",
                "scope_paths": ["src/record.py"],
                "acceptance_criteria": [_criterion(text) for text in texts],
            }
        ],
        "finding_dispositions": [],
        "plan_treatments": [
            {
                "signature": finding_record_signature(finding),
                "finding_ids": ["C-01"],
                "treatment_kind": "implementation",
                "closing_slice_ids": [1],
                "no_code_reason": None,
                "evidence": None,
                "evidence_paths": [],
                "affected_paths": [],
            }
        ],
        "plan_completion": "IMPLEMENTATION_REQUIRED",
    }

    response = parse_native_codex_response(document, bound)

    assert tuple(
        criterion.text for criterion in response.slice_plan[0].acceptance_criteria
    ) == tuple(texts)
    assert tuple(
        criterion.criterion_id
        for criterion in response.slice_plan[0].acceptance_criteria
    ) == tuple(acceptance_criterion_id(1, text) for text in texts)


def test_active_plan_contract_accepts_acceptance_criteria_field() -> None:
    bound = _bound(NativeCodexRequestKind.PLAN)
    document = {
        **_base(bound, "plan_result"),
        "ready": True,
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Active plan.",
                "scope_paths": ["src/record.py"],
                "acceptance_criteria": [_criterion("The record path is covered.")],
            }
        ],
        "finding_dispositions": [],
    }

    parsed = parse_native_codex_response(document, bound)
    assert tuple(
        criterion.text for criterion in parsed.slice_plan[0].acceptance_criteria
    ) == ("The record path is covered.",)


def test_implementation_result_applies_each_supplied_finding_disposition() -> None:
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
                "responsibility_proposal": None,
            }
        ],
    }
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.test_files == ("tests/test_native_codex_contract.py",)
    assert result.findings[0].responses[0].decision is FindingResponseDecision.ACCEPTED


def test_codex_responsibility_proposal_is_visible_but_never_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        findings=(_finding(),),
        expected_tests=(),
        test_changes_approved=True,
    )
    document = {
        **_base(bound, "implementation_result"),
        "ready": True,
        "test_files": [],
        "finding_dispositions": [
            {
                "finding_id": "C-01",
                "decision": "accepted",
                "rationale": "A later Slice is the proposed owner.",
                "responsibility_proposal": {
                    "responsibility_kind": "SLICE",
                    "target_run_id": "later-run",
                    "approved_plan_commit": "c" * 40,
                    "slice_id": "6",
                },
            }
        ],
    }

    response = parse_native_codex_response(document, bound)
    result = parse_bound_native_codex_contract_result(document, bound)

    proposals = native_responsibility_proposals(response)
    assert proposals[0].responsibility == SliceResponsibility(
        "later-run", "c" * 40, "6"
    )
    delta = project_finding_response_delta(
        bound.context.previous_findings, result.findings
    )
    assert tuple(item.finding.finding_id for item in delta) == ("C-01",)
    assert result.findings[0].status is FindingStatus.OPEN
    assert not hasattr(result.findings[0], "responsibility")


def test_active_codex_contract_accepts_explicit_null_proposal() -> None:
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        findings=(_finding(),),
        expected_tests=(),
        test_changes_approved=True,
    )
    document = {
        **_base(bound, "implementation_result"),
        "ready": True,
        "test_files": [],
        "finding_dispositions": [
            {
                "finding_id": "C-01",
                "decision": "accepted",
                "rationale": "Codex proposes a later owner.",
                "responsibility_proposal": None,
            }
        ],
    }

    parsed = parse_native_codex_response(document, bound)
    assert native_responsibility_proposals(parsed) == ()


def test_implementation_disposition_records_do_not_scale_with_open_findings() -> None:
    findings = tuple(
        replace(_finding(), finding_id=f"C-{number:02d}")
        for number in range(1, 66)
    )
    bound = _bound(
        NativeCodexRequestKind.IMPLEMENTATION,
        findings=findings,
        expected_tests=(),
        test_changes_approved=True,
    )
    document = {
        **_base(bound, "implementation_result"),
        "ready": True,
        "test_files": [],
        "finding_dispositions": [],
    }
    writer = native_codex_provider_response_schema(bound.context)

    validate_schema_document({"result": document}, writer)
    result = parse_bound_native_codex_contract_result(document, bound)

    disposition_schema = writer["$defs"]["implementation_result"]["properties"][
        "finding_dispositions"
    ]
    assert disposition_schema["minItems"] == 0
    assert disposition_schema["maxItems"] == 65
    assert result.findings == findings
    assert project_finding_response_delta(findings, result.findings) == ()

    document["finding_dispositions"] = [
        {
            "finding_id": "C-65",
            "decision": "accepted",
            "rationale": "Only this finding needs a new implementation answer.",
            "responsibility_proposal": None,
        }
    ]
    validate_schema_document({"result": document}, writer)
    answered = parse_bound_native_codex_contract_result(document, bound)
    response_delta = project_finding_response_delta(findings, answered.findings)
    assert tuple(item.finding.finding_id for item in response_delta) == ("C-65",)


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


def test_native_result_accepts_missing_but_rejects_foreign_dispositions() -> None:
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
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.findings == bound.context.previous_findings

    document["finding_dispositions"] = [
        {
            "finding_id": "C-02",
            "decision": "accepted",
            "rationale": "This finding was never offered in the bound context.",
            "responsibility_proposal": None,
        }
    ]
    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)
    assert raised.value.code is NativeCodexErrorCode.FINDING_REFERENCE_INVALID
    assert raised.value.detail == (
        "disposition references non-open finding C-02 "
        "(context: work-unit=work-unit-1 round=1)"
    )


def test_native_result_rejects_disposition_to_closed_finding_with_context() -> None:
    closed = replace(
        _finding(),
        status=FindingStatus.CLOSED,
        status_rationale="The reviewer closed this finding before the request.",
    )
    bound = _bound(
        NativeCodexRequestKind.CORRECTION,
        findings=(closed,),
        expected_tests=(),
        test_changes_approved=True,
    )
    document = {
        **_base(bound, "correction_result"),
        "ready": True,
        "test_files": [],
        "finding_dispositions": [
            {
                "finding_id": "C-01",
                "decision": "accepted",
                "rationale": "This closed finding must remain unavailable.",
                "responsibility_proposal": None,
            }
        ],
    }

    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)

    assert raised.value.code is NativeCodexErrorCode.FINDING_REFERENCE_INVALID
    assert raised.value.detail == (
        "disposition references non-open finding C-01 "
        "(context: work-unit=work-unit-1 round=1)"
    )


def test_native_result_rejects_unknown_finding_with_context() -> None:
    bound = _bound(
        NativeCodexRequestKind.CORRECTION,
        findings=(),
        expected_tests=(),
        test_changes_approved=True,
    )
    document = {
        **_base(bound, "correction_result"),
        "ready": True,
        "test_files": [],
        "finding_dispositions": [
            {
                "finding_id": "C-99",
                "decision": "accepted",
                "rationale": "This identifier is absent from the bound context.",
                "responsibility_proposal": None,
            }
        ],
    }

    with pytest.raises(NativeCodexContractError) as raised:
        parse_bound_native_codex_contract_result(document, bound)

    assert raised.value.code is NativeCodexErrorCode.FINDING_REFERENCE_INVALID
    assert raised.value.detail == (
        "disposition references non-open finding C-99 "
        "(context: work-unit=work-unit-1 round=1)"
    )


def test_correction_without_dispositions_preserves_open_finding() -> None:
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
    validate_schema_document(
        {"result": native_document},
        native_codex_provider_response_schema(bound.context),
    )
    result = parse_bound_native_codex_contract_result(native_document, bound)
    assert result.findings == bound.context.previous_findings


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
                "responsibility_proposal": None,
            }
        ],
    }
    result = parse_bound_native_codex_contract_result(document, bound)
    assert result.ready is True
    assert result.test_files == ("tests/test_native_codex_contract.py",)
    assert result.findings[0].responses[0].decision is FindingResponseDecision.ACCEPTED
    assert result.self_check is None


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
