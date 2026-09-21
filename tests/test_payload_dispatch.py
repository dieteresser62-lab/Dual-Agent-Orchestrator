from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, cast

import pytest

import artifact_models
from artifact_models import ArtifactValidationError, RecordType


ROOT = Path(__file__).resolve().parents[1]
PRE_CUT = ROOT / "tests/fixtures/payload-dispatch-pre-b58-v1.json"
CORPUS = ROOT / "tests/fixtures/payload-dispatch-corpus-v1.json"
FUNCTION_SIZE_BASELINE = ROOT / "tests/fixtures/function-size-baseline-v1.json"
RECORD_SEQUENCE_BASELINE = (
    ROOT / "tests/fixtures/workflow-record-sequence-baseline-v1.json"
)
PAYLOAD_SOURCE = "src/artifact_models.py"
PAYLOAD_FUNCTION = "_payload_from_dict"
PAYLOAD_MAPPING = "_PAYLOAD_READERS"
RECORD_SEQUENCE_BLOB = "26fb661c8fa382f90e70fb921e3d950da5cae09b"


def _load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


PRE_CUT_DOCUMENT = _load_json(PRE_CUT)
CORPUS_DOCUMENT = _load_json(CORPUS)
CORPUS_CASES = tuple(CORPUS_DOCUMENT["cases"])
CORPUS_TYPES = tuple(case["record_type"] for case in CORPUS_CASES)
ADDITIVE_PAYLOAD_TYPES = {
    "branch_discovery_completed": "BranchDiscoveryCompletedPayload",
    "branch_discovery_handoff_export": "BranchDiscoveryHandoffExportPayload",
    "branch_discovery_handoff_import": "BranchDiscoveryHandoffImportPayload",
    "plan_assignment": "PlanAssignmentPayload",
    "remediation_cohort_checkpoint": "RemediationCohortCheckpointPayload",
    "no_implementation_required": "NoImplementationRequiredPayload",
    "closed_finding_occurrence": "ClosedFindingOccurrencePayload",
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(functions) == 1, f"expected exactly one {name} definition"
    return functions[0]


def _pre_cut_source() -> str:
    return _git(
        "show",
        f"{PRE_CUT_DOCUMENT['source_commit']}:{PAYLOAD_SOURCE}",
    )


def _dispatch_test(stmt: ast.If) -> str | None:
    test = stmt.test
    if (
        not isinstance(test, ast.Compare)
        or len(test.ops) != 1
        or not isinstance(test.ops[0], ast.Is)
        or len(test.comparators) != 1
        or not isinstance(test.left, ast.Name)
        or test.left.id != "record_type"
    ):
        return None
    target = test.comparators[0]
    if (
        not isinstance(target, ast.Attribute)
        or not isinstance(target.value, ast.Name)
        or target.value.id != "RecordType"
    ):
        return None
    return target.attr


def _returned_class(stmt: ast.If) -> str:
    returns = [node for node in stmt.body if isinstance(node, ast.Return)]
    assert len(returns) == 1
    value = returns[0].value
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
    return value.func.id


def _pre_cut_dispatch() -> dict[str, str]:
    function = _function(ast.parse(_pre_cut_source()), PAYLOAD_FUNCTION)
    inventory: dict[str, str] = {}
    for stmt in function.body:
        if not isinstance(stmt, ast.If):
            continue
        member = _dispatch_test(stmt)
        if member is not None:
            inventory[RecordType[member].value] = _returned_class(stmt)
    return inventory


def _mapping_node() -> ast.Dict:
    tree = ast.parse((ROOT / PAYLOAD_SOURCE).read_text(encoding="utf-8"))
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == PAYLOAD_MAPPING
    ]
    assert len(assignments) == 1, f"expected exactly one {PAYLOAD_MAPPING}"
    value = assignments[0].value
    assert isinstance(value, ast.Dict)
    return value


def _current_mapping() -> dict[str, str]:
    inventory: dict[str, str] = {}
    mapping = _mapping_node()
    for key, value in zip(mapping.keys, mapping.values, strict=True):
        assert (
            isinstance(key, ast.Attribute)
            and isinstance(key.value, ast.Name)
            and key.value.id == "RecordType"
        )
        assert isinstance(value, ast.Lambda)
        result = value.body
        assert isinstance(result, ast.Call) and isinstance(result.func, ast.Name)
        inventory[RecordType[key.attr].value] = result.func.id
    return inventory


def _normalized_payload_fields(payload: object) -> object:
    if isinstance(payload, artifact_models.AgentResultPayload):
        return artifact_models.artifact_payload_document(payload)
    if isinstance(payload, artifact_models.PlanPayload):
        raw = asdict(payload)
        slices = raw[
            "slices"
            if isinstance(payload, artifact_models.PlanPayload)
            else "slice_plan"
        ]
        for planned_slice in slices:
            if not planned_slice["acceptance_criteria"]:
                planned_slice.pop("acceptance_criteria")
        return artifact_models._json_value(raw)  # type: ignore[arg-type]
    if isinstance(payload, artifact_models.FindingTransitionPayload):
        raw = asdict(payload)
        if payload.responsibility is None:
            raw.pop("responsibility", None)
        if payload.closure_kind is None:
            raw.pop("closure_kind", None)
            raw.pop("rejection_reason", None)
            raw.pop("closure_evidence", None)
            raw.pop("remaining_work", None)
        elif payload.closure_kind == "fixed":
            raw.pop("rejection_reason", None)
            raw.pop("closure_evidence", None)
            raw.pop("remaining_work", None)
        elif payload.closure_kind == "partial":
            raw.pop("rejection_reason", None)
        elif payload.closure_kind == "rejected":
            raw.pop("remaining_work", None)
        if payload.acceptance_command is None:
            for name in (
                "acceptance_command",
                "acceptance_outcome",
                "acceptance_exit_code",
                "acceptance_output_sha256",
                "acceptance_attestation_id",
                "acceptance_fingerprint",
            ):
                raw.pop(name, None)
        if payload.predecessor_finding_ref is None:
            raw.pop("predecessor_finding_ref", None)
            raw.pop("evidence_anchor_sha256", None)
        return artifact_models._json_value(raw)  # type: ignore[arg-type]
    if isinstance(payload, artifact_models.FindingHandoffImportPayload):
        raw = asdict(payload)
        for transition in raw["transitions"]:
            if transition["source_run_id"] is None:
                transition.pop("source_run_id")
                transition.pop("source_record_id")
            transition_payload = transition["payload"]
            if transition_payload["responsibility"] is None:
                transition_payload.pop("responsibility")
            if transition_payload["closure_kind"] is None:
                transition_payload.pop("closure_kind")
                transition_payload.pop("rejection_reason")
                transition_payload.pop("closure_evidence")
                transition_payload.pop("remaining_work")
            elif transition_payload["closure_kind"] == "fixed":
                transition_payload.pop("rejection_reason")
                transition_payload.pop("closure_evidence")
                transition_payload.pop("remaining_work")
            elif transition_payload["closure_kind"] == "partial":
                transition_payload.pop("rejection_reason")
            elif transition_payload["closure_kind"] == "rejected":
                transition_payload.pop("remaining_work")
            if transition_payload["acceptance_command"] is None:
                for name in (
                    "acceptance_command",
                    "acceptance_outcome",
                    "acceptance_exit_code",
                    "acceptance_output_sha256",
                    "acceptance_attestation_id",
                    "acceptance_fingerprint",
                ):
                    transition_payload.pop(name)
            if transition_payload["predecessor_finding_ref"] is None:
                transition_payload.pop("predecessor_finding_ref")
                transition_payload.pop("evidence_anchor_sha256")
        return artifact_models._json_value(raw)  # type: ignore[arg-type]
    raw = asdict(payload)  # type: ignore[arg-type]
    if isinstance(payload, artifact_models.RunProfilePayload):
        if payload.family_binding is None:
            raw.pop("family_binding", None)
    if isinstance(payload, artifact_models.InvocationFailurePayload):
        if payload.orchestrator_diagnostic is None:
            raw.pop("orchestrator_diagnostic", None)
        if payload.native_review_rejection is None:
            raw.pop("native_review_rejection", None)
            raw.pop("native_review_retry_round", None)
        if payload.native_implementer_rejection is None:
            raw.pop("native_implementer_rejection", None)
            raw.pop("native_implementer_retry_round", None)
        if payload.rejected_response_shape is None:
            raw.pop("rejected_response_shape", None)
    if isinstance(payload, artifact_models.GateDecisionPayload):
        if payload.invocation_id is None:
            raw.pop("invocation_id", None)
    if (
        isinstance(payload, artifact_models.ReviewPayload)
        and not payload.plan_treatment_decisions
    ):
        raw.pop("plan_treatment_decisions", None)
    return artifact_models._json_value(raw)  # type: ignore[arg-type]


@dataclass(frozen=True)
class PayloadDispatchCorpus:
    results: Mapping[str, tuple[str, object]]


_CORPUS_BUILD_COUNT = 0


@pytest.fixture(scope="session")
def payload_dispatch_corpus() -> PayloadDispatchCorpus:
    global _CORPUS_BUILD_COUNT
    _CORPUS_BUILD_COUNT += 1
    results: dict[str, tuple[str, object]] = {}
    for case in CORPUS_CASES:
        record_type = RecordType(case["record_type"])
        payload = artifact_models._payload_from_dict(record_type, case["input"])
        results[record_type.value] = (
            type(payload).__name__,
            _normalized_payload_fields(payload),
        )
    return PayloadDispatchCorpus(results)


def _assert_cases_match(
    readers: Mapping[RecordType, object],
    cases: tuple[Mapping[str, Any], ...],
) -> None:
    original = artifact_models._PAYLOAD_READERS
    artifact_models._PAYLOAD_READERS = readers  # type: ignore[assignment]
    failures: list[str] = []
    try:
        for case in cases:
            name = case["record_type"]
            try:
                payload = artifact_models._payload_from_dict(
                    RecordType(name), case["input"]
                )
                actual = (type(payload).__name__, _normalized_payload_fields(payload))
                expected = (case["expected_class"], case["input"])
                if actual != expected:
                    failures.append(name)
            except Exception as error:  # mutation evidence must name every broken type
                failures.append(f"{name} ({type(error).__name__})")
    finally:
        artifact_models._PAYLOAD_READERS = original
    assert not failures, "payload dispatch mismatch: " + ", ".join(failures)


def _assert_unknown_rejected(readers: Mapping[RecordType, object]) -> None:
    original = artifact_models._PAYLOAD_READERS
    artifact_models._PAYLOAD_READERS = readers  # type: ignore[assignment]
    unknown = CORPUS_DOCUMENT["unknown_type"]
    try:
        try:
            artifact_models._payload_from_dict(
                cast(RecordType, unknown["record_type"]), unknown["input"]
            )
        except ArtifactValidationError as error:
            assert str(error) == unknown["expected_error"]
            return
    finally:
        artifact_models._PAYLOAD_READERS = original
    raise AssertionError(f"unknown record type was accepted: {unknown['record_type']}")


def test_b58_pre_cut_anchor_binds_source_and_complete_dispatch() -> None:
    assert PRE_CUT_DOCUMENT == {
        "schema_version": "payload-dispatch-pre-b58-v1",
        "source_commit": "5ed020e1e64263a54c54ea93ad30642cda769a82",
        "source_blob": "21b4abf0fac890ad05a532a3573567efc65e1d67",
        "dispatch_branch_count": 36,
        "constructed_payload_class_count": 41,
        "unknown_type_error": "unsupported record_type: unknown_record_type",
    }
    assert (
        _git("rev-parse", f"{PRE_CUT_DOCUMENT['source_commit']}:{PAYLOAD_SOURCE}")
        == PRE_CUT_DOCUMENT["source_blob"]
    )
    function = _function(ast.parse(_pre_cut_source()), PAYLOAD_FUNCTION)
    payload_classes = {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.endswith("Payload")
    }
    assert len(_pre_cut_dispatch()) == PRE_CUT_DOCUMENT["dispatch_branch_count"]
    assert (
        len(payload_classes)
        == PRE_CUT_DOCUMENT["constructed_payload_class_count"]
    )


def test_corpus_and_mapping_cover_every_pre_cut_branch_exactly() -> None:
    pre_cut = _pre_cut_dispatch()
    current = _current_mapping()
    assert CORPUS_DOCUMENT["schema_version"] == "payload-dispatch-corpus-v1"
    assert CORPUS_DOCUMENT["pre_cut"] == PRE_CUT.relative_to(ROOT).as_posix()
    assert (
        CORPUS_DOCUMENT["unknown_type"]["expected_error"]
        == PRE_CUT_DOCUMENT["unknown_type_error"]
    )
    assert len(CORPUS_TYPES) == len(set(CORPUS_TYPES))
    assert tuple(pre_cut) == CORPUS_TYPES
    assert {case["record_type"]: case["expected_class"] for case in CORPUS_CASES} == pre_cut
    assert {
        name: payload_class
        for name, payload_class in current.items()
        if name not in ADDITIVE_PAYLOAD_TYPES
    } == pre_cut
    assert {
        name: payload_class
        for name, payload_class in current.items()
        if name in ADDITIVE_PAYLOAD_TYPES
    } == ADDITIVE_PAYLOAD_TYPES
    assert tuple(
        item.value
        for item in artifact_models._PAYLOAD_READERS
        if item.value not in ADDITIVE_PAYLOAD_TYPES
    ) == CORPUS_TYPES
    assert set(artifact_models._PAYLOAD_READERS) == set(RecordType)


@pytest.mark.parametrize("record_type", CORPUS_TYPES)
def test_each_record_type_rehydrates_exact_class_and_fields(
    record_type: str,
    payload_dispatch_corpus: PayloadDispatchCorpus,
) -> None:
    case = next(case for case in CORPUS_CASES if case["record_type"] == record_type)
    assert payload_dispatch_corpus.results[record_type] == (
        case["expected_class"],
        case["input"],
    ), record_type


def test_unknown_record_type_keeps_exact_failure() -> None:
    unknown = CORPUS_DOCUMENT["unknown_type"]
    with pytest.raises(
        ArtifactValidationError,
        match=f"^{re.escape(unknown['expected_error'])}$",
    ):
        artifact_models._payload_from_dict(
            cast(RecordType, unknown["record_type"]), unknown["input"]
        )


def test_string_equal_to_known_strenum_value_remains_unsupported() -> None:
    case = CORPUS_CASES[0]
    with pytest.raises(
        ArtifactValidationError,
        match="^unsupported record_type: run_identity$",
    ):
        artifact_models._payload_from_dict(
            cast(RecordType, RecordType.RUN_IDENTITY.value), case["input"]
        )


def test_swapping_two_dispatch_targets_names_both_record_types() -> None:
    first = RecordType.WORK_UNIT
    second = RecordType.CORRECTION_WORK_UNIT
    mutated = dict(artifact_models._PAYLOAD_READERS)
    mutated[first], mutated[second] = mutated[second], mutated[first]
    selected = tuple(
        case
        for case in CORPUS_CASES
        if case["record_type"] in {first.value, second.value}
    )
    with pytest.raises(AssertionError) as captured:
        _assert_cases_match(mutated, selected)
    message = str(captured.value)
    assert first.value in message and second.value in message


def test_accepting_unknown_type_mutation_is_rejected() -> None:
    class AcceptingReaders(dict[RecordType, object]):
        def __missing__(self, _key: RecordType) -> object:
            return self[RecordType.RUN_IDENTITY]

    with pytest.raises(AssertionError, match="unknown_record_type"):
        _assert_unknown_rejected(AcceptingReaders(artifact_models._PAYLOAD_READERS))


def test_payload_dispatch_builds_once_per_test_session(
    payload_dispatch_corpus: PayloadDispatchCorpus,
) -> None:
    assert len(payload_dispatch_corpus.results) == len(CORPUS_TYPES)
    assert _CORPUS_BUILD_COUNT == 1


def test_b58_removes_payload_entry_from_b32_ratchet() -> None:
    baseline = _load_json(FUNCTION_SIZE_BASELINE)
    assert f"{PAYLOAD_SOURCE}::{PAYLOAD_FUNCTION}" not in baseline["functions"]
    function = _function(
        ast.parse((ROOT / PAYLOAD_SOURCE).read_text(encoding="utf-8")),
        PAYLOAD_FUNCTION,
    )
    assert function.end_lineno is not None
    assert function.end_lineno - function.lineno + 1 < baseline["threshold_lines"]
    assert not any(isinstance(node, ast.If) for node in ast.walk(function))
    assert sum(isinstance(node, ast.Raise) for node in ast.walk(function)) == 1


def test_b58_keeps_b25_record_sequence_baseline_byte_identical() -> None:
    assert _git("hash-object", str(RECORD_SEQUENCE_BASELINE)) == RECORD_SEQUENCE_BLOB
    assert (
        _git(
            "rev-parse",
            f"{PRE_CUT_DOCUMENT['source_commit']}:"
            "tests/fixtures/workflow-record-sequence-baseline-v1.json",
        )
        == RECORD_SEQUENCE_BLOB
    )
