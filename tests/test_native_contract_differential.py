from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

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
from native_codex_contract import (
    BoundNativeCodexContext,
    NativeCodexContext,
    NativeCodexRequestKind,
    native_codex_provider_response_schema,
    parse_bound_native_codex_contract_result,
)
from native_provider_schema import defensive_provider_projection, registered_exceptions
from native_review_contract import (
    BoundNativeReviewContext,
    NativeReviewContext,
    NativeReviewContractError,
    NativeReviewErrorCode,
    native_review_provider_response_schema,
    parse_bound_native_contract_result,
)
from schema_validation import SchemaMismatch, validate_schema_document


ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = "a" * 64
PROJECTION_BASELINE = ROOT / "tests/fixtures/native-provider-projection-baseline-v1.json"
EXCEPTION_TABLE = ROOT / "schemas/native-provider-schema-exceptions-v1.json"

_PROVIDER_FEATURES = {
    "codex": ("closed_object", "min_max_items", "nested_any_of"),
    "claude": ("closed_object", "min_max_items", "nested_any_of", "nested_one_of"),
}
_EXPECTED_PROJECTION_COMPENSATIONS = {
    ("codex", "/$defs/correction_result/properties/test_files/uniqueItems", "uniqueItems"):
        ("regex_lookaround_and_unique_items", True),
    ("codex", "/$defs/implementation_result/properties/test_files/uniqueItems", "uniqueItems"):
        ("regex_lookaround_and_unique_items", True),
    ("codex", "/$defs/planned_slice/properties/scope_paths/uniqueItems", "uniqueItems"):
        ("regex_lookaround_and_unique_items", True),
    ("codex", "/$defs/safe_path/pattern", "pattern"):
        (
            "regex_lookaround_and_unique_items",
            r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\u0000\r\n]{1,1000}$",
        ),
    ("codex", "/$defs/safe_text/pattern", "pattern"): (
        "regex_lookaround",
        r"^(?=.*\S)[^\u0000]{1,12000}$",
    ),
    ("codex", "/$defs/stop_result/properties/remediation_paths/uniqueItems", "uniqueItems"):
        ("regex_lookaround_and_unique_items", True),
}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _projection_losses(
    reader: object, writer: object, pointer: str = ""
) -> list[tuple[str, str, object]]:
    losses: list[tuple[str, str, object]] = []
    if isinstance(reader, dict) and isinstance(writer, dict):
        additions = writer.keys() - reader.keys()
        assert not additions, f"projection added keys at {pointer or '/'}: {sorted(additions)}"
        for key in sorted(reader.keys() - writer.keys()):
            escaped = key.replace("~", "~0").replace("/", "~1")
            losses.append((f"{pointer}/{escaped}", key, reader[key]))
        for key in sorted(reader.keys() & writer.keys()):
            escaped = key.replace("~", "~0").replace("/", "~1")
            losses.extend(_projection_losses(reader[key], writer[key], f"{pointer}/{escaped}"))
        return losses
    if isinstance(reader, list) and isinstance(writer, list):
        assert len(reader) == len(writer), f"projection changed array length at {pointer}"
        for index, (reader_item, writer_item) in enumerate(zip(reader, writer, strict=True)):
            losses.extend(_projection_losses(reader_item, writer_item, f"{pointer}/{index}"))
        return losses
    assert type(reader) is type(writer) and reader == writer, (
        f"projection changed value at {pointer}: {reader!r} -> {writer!r}"
    )
    return losses


def _collect_registered_regression(node_id: str, root: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", node_id],
        cwd=root,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(root / "src"),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0 and node_id in completed.stdout, (
        f"registered regression is not collectable: {node_id}\n"
        f"{completed.stdout}\n{completed.stderr}"
    )


def _assert_projection_compensated(
    readers: dict[str, Path], exception_path: Path, *, collect_root: Path | None
) -> dict[str, list[tuple[str, str, object]]]:
    exception_document = json.loads(exception_path.read_text(encoding="utf-8"))
    exceptions = exception_document["exceptions"]
    actual: dict[str, list[tuple[str, str, object]]] = {}
    collected: set[str] = set()
    for provider in ("claude", "codex"):
        reader = json.loads(readers[provider].read_text(encoding="utf-8"))
        writer = defensive_provider_projection(
            reader,
            provider=provider,
            required_features=_PROVIDER_FEATURES[provider],
        )
        losses = _projection_losses(reader, writer)
        actual[provider] = losses
        for pointer, keyword, original in losses:
            compensation = _EXPECTED_PROJECTION_COMPENSATIONS.get(
                (provider, pointer, keyword)
            )
            assert compensation is not None, (
                f"unregistered provider projection loss: {provider} {pointer} {keyword}"
            )
            feature, expected_original = compensation
            assert original == expected_original, (
                f"projection loss changed its reader value: {provider} {pointer} "
                f"{expected_original!r} -> {original!r}"
            )
            matching = [
                item
                for item in exceptions
                if item["provider"] == provider
                and item["missing_schema_feature"] == feature
            ]
            assert matching, (
                f"projection loss has no provider-identical compensation: "
                f"{provider} {pointer} {feature}"
            )
            if collect_root is not None:
                for item in matching:
                    node_id = str(item["regression_test"])
                    if node_id not in collected:
                        _collect_registered_regression(node_id, collect_root)
                        collected.add(node_id)
    actual_keys = {
        (provider, pointer, keyword)
        for provider, losses in actual.items()
        for pointer, keyword, _original in losses
    }
    assert actual_keys == set(_EXPECTED_PROJECTION_COMPENSATIONS), actual_keys
    return actual


def _finding(
    finding_class: FindingClass = FindingClass.BLOCKER,
) -> FindingRecord:
    return FindingRecord(
        "C-01",
        finding_class,
        FindingStatus.OPEN,
        "Close the bound contract.",
        "A focused regression passes.",
        FindingOrigin("01", 1, AgentRole.CLAUDE),
    )


def _codex_bound(kind: NativeCodexRequestKind) -> BoundNativeCodexContext:
    readiness = {
        NativeCodexRequestKind.PLAN: ReadinessMarker.PLAN,
        NativeCodexRequestKind.IMPLEMENTATION: ReadinessMarker.IMPLEMENTATION,
        NativeCodexRequestKind.CORRECTION: ReadinessMarker.IMPLEMENTATION,
    }[kind]
    context = NativeCodexContext(
        run_id="differential-codex",
        work_unit_id=f"work-{kind.value}",
        operation=f"codex_{kind.value}",
        current_fingerprint=FINGERPRINT,
        request_kind=kind,
        contract=CodexStepContract(
            name=f"differential-{kind.value}",
            readiness_marker=readiness,
            slice_id="01",
            round_number=1,
            require_test_files_record=kind in {
                NativeCodexRequestKind.IMPLEMENTATION,
                NativeCodexRequestKind.CORRECTION,
            },
            expected_test_files=(
                ("tests/test_native_contract_differential.py",)
                if kind
                in {
                    NativeCodexRequestKind.IMPLEMENTATION,
                    NativeCodexRequestKind.CORRECTION,
                }
                else ()
            ),
            test_changes_approved=True,
            require_slice_plan=kind is NativeCodexRequestKind.PLAN,
            plan_artifact_path=(
                "docs/internal/plan.md"
                if kind is NativeCodexRequestKind.PLAN
                else None
            ),
        ),
        previous_findings=(
            (_finding(),) if kind is NativeCodexRequestKind.CORRECTION else ()
        ),
    )
    return BoundNativeCodexContext(
        context, "native-codex-request-" + "b" * 64, "b" * 64
    )


def _codex_response(bound: BoundNativeCodexContext) -> dict[str, object]:
    kind = bound.context.request_kind
    common: dict[str, object] = {
        "schema_version": "native-agent-codex-result-v2",
        "request_id": bound.request_id,
        "ready": True,
        "finding_dispositions": (
            [
                {
                    "finding_id": "C-01",
                    "decision": "accepted",
                    "rationale": "The focused regression closes the defect.",
                }
            ]
            if kind is NativeCodexRequestKind.CORRECTION
            else []
        ),
    }
    if kind is NativeCodexRequestKind.PLAN:
        return {
            **common,
            "result_type": "plan_result",
            "slice_plan": [
                {
                    "slice_id": 1,
                    "summary": "Implement the bounded plan.",
                    "scope_paths": ["docs/internal/plan.md"],
                    "acceptance_criteria": [{
                        "text": "The bounded plan is implemented.",
                        "measured_against": "SOURCE",
                    }],
                }
            ],
            "plan_treatments": [],
            "plan_completion": "IMPLEMENTATION_REQUIRED",
        }
    if kind in {
        NativeCodexRequestKind.IMPLEMENTATION,
        NativeCodexRequestKind.CORRECTION,
    }:
        return {
            **common,
            "result_type": f"{kind.value}_result",
            "test_files": ["tests/test_native_contract_differential.py"],
        }
    raise AssertionError(f"unsupported Codex request kind: {kind}")


def _attestation() -> ValidationAttestation:
    command = "python3 -m pytest tests/ -v"
    return ValidationAttestation(
        "differential-validation",
        FINGERPRINT,
        (command,),
        (ValidationRecord(ValidationStatus.PASS, command, 0, "passed"),),
        hashlib.sha256(b"passed").hexdigest(),
        "passed",
        (ValidationCommandSpec(argv=("python3", "-m", "pytest", "tests/", "-v")),),
    )


def _review_bound(form: str) -> BoundNativeReviewContext:
    convergence = form == "convergence"
    marker = {
        "plan": ApprovalMarker.PLAN,
        "initial_slice": ApprovalMarker.SLICE,
        "convergence": ApprovalMarker.SLICE,
    }[form]
    context = NativeReviewContext(
        run_id="differential-claude",
        work_unit_id=f"work-{form}",
        operation={
            ApprovalMarker.PLAN: "claude_plan_review",
            ApprovalMarker.SLICE: "claude_slice_review",
        }[marker],
        diff_fingerprint=FINGERPRINT,
        reviewer=AgentRole.CLAUDE,
        approval_marker=marker,
        slice_id="01",
        round_number=2 if convergence else 1,
        previous_findings=(
            (
                _finding(
                    FindingClass.OBSERVATION
                    if form == "final_observation"
                    else FindingClass.BLOCKER
                ),
            )
            if form in ("plan", "convergence")
            else ()
        ),
        validation_attestation=_attestation(),
        test_files=("tests/test_native_contract_differential.py",),
        test_changes_approved=True,
        allow_new_observations=not convergence,
        anchor_origin="docs/internal/plan.md",
    )
    return BoundNativeReviewContext(
        context, "native-review-request-" + "c" * 64, "c" * 64
    )


def _review_response(bound: BoundNativeReviewContext) -> dict[str, object]:
    return {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": bound.request_id,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [],
        "status_changes": (
            [
                {
                    "finding_id": "C-01",
                    "status": "CLOSED",
                    "rationale": "The focused regression closes the defect.",
                    "closure": {"kind": "fixed"},
                }
            ]
            if bound.context.previous_findings
            else []
        ),
        "reclassifications": [],
        "plan_treatment_decisions": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "correctness, contracts, failure paths, resume",
            "largest_residual_risk": "future provider subset drift",
            "break_condition": "a writer-valid response fails local binding",
        },
        "pre_mortem": "A later provider version could change its schema subset.",
    }


def test_all_writer_forms_accept_their_local_domain_result() -> None:
    actual: list[dict[str, str]] = []
    for kind in NativeCodexRequestKind:
        bound = _codex_bound(kind)
        response = _codex_response(bound)
        writer = native_codex_provider_response_schema(bound.context)
        validate_schema_document({"result": response}, writer)
        parse_bound_native_codex_contract_result(response, bound)
        actual.append(
            {
                "provider": "codex",
                "form": kind.value,
                "sha256": hashlib.sha256(_canonical(writer).encode("utf-8")).hexdigest(),
            }
        )
    for form in ("plan", "initial_slice", "convergence"):
        bound = _review_bound(form)
        response = _review_response(bound)
        writer = native_review_provider_response_schema(bound.context)
        validate_schema_document({"result": response}, writer)
        parse_bound_native_contract_result(response, bound)
        actual.append(
            {
                "provider": "claude",
                "form": form,
                "sha256": hashlib.sha256(_canonical(writer).encode("utf-8")).hexdigest(),
            }
        )
    baseline = json.loads(PROJECTION_BASELINE.read_text(encoding="utf-8"))
    assert baseline["schema_version"] == "native-provider-projection-baseline-v1"
    expected = baseline["writers"]
    assert expected == sorted(expected, key=lambda item: (item["provider"], item["form"]))
    assert sorted(actual, key=lambda item: (item["provider"], item["form"])) == expected


def test_provider_projection_losses_are_exact_and_locally_compensated() -> None:
    actual = _assert_projection_compensated(
        {
            "codex": ROOT / "schemas/native-agent-codex-result-v2.schema.json",
            "claude": ROOT / "schemas/native-agent-review-result-v2.schema.json",
        },
        EXCEPTION_TABLE,
        collect_root=ROOT,
    )
    assert len(actual["codex"]) == 6
    assert actual["claude"] == []


def test_seventh_projection_loss_is_rejected_without_compensation(
    tmp_path: Path,
) -> None:
    copied_schemas = tmp_path / "schemas"
    copied_schemas.mkdir()
    readers = {
        "codex": copied_schemas / "native-agent-codex-result-v2.schema.json",
        "claude": copied_schemas / "native-agent-review-result-v2.schema.json",
    }
    real_readers = {
        "codex": ROOT / "schemas/native-agent-codex-result-v2.schema.json",
        "claude": ROOT / "schemas/native-agent-review-result-v2.schema.json",
    }
    real_bytes = {
        path: path.read_bytes() for path in (*real_readers.values(), EXCEPTION_TABLE)
    }
    for provider, source in real_readers.items():
        shutil.copyfile(source, readers[provider])
    copied_exceptions = copied_schemas / EXCEPTION_TABLE.name
    shutil.copyfile(EXCEPTION_TABLE, copied_exceptions)

    mutated = json.loads(readers["codex"].read_text(encoding="utf-8"))
    mutated["$defs"]["finding_disposition"]["properties"]["finding_id"][
        "uniqueItems"
    ] = True
    readers["codex"].write_text(json.dumps(mutated), encoding="utf-8")

    with pytest.raises(AssertionError, match="unregistered provider projection loss"):
        _assert_projection_compensated(
            readers, copied_exceptions, collect_root=None
        )
    assert all(path.read_bytes() == content for path, content in real_bytes.items())


def test_exception_table_is_closed_and_every_entry_names_a_real_regression() -> None:
    all_entries = (*registered_exceptions("codex"), *registered_exceptions("claude"))
    assert len({item["exception_id"] for item in all_entries}) == len(all_entries)
    for item in all_entries:
        path_text, test_name = str(item["regression_test"]).split("::", 1)
        source = (ROOT / path_text).read_text(encoding="utf-8")
        assert f"def {test_name}(" in source
        assert item["provider_evidence"].strip()
        assert item["missing_schema_feature"].strip()


def test_deliberately_loosened_writer_rule_trips_the_red_control() -> None:
    bound = _review_bound("plan")
    response = _review_response(bound)
    response["review_evidence"] = {
        "dimensions": "   ",
        "largest_residual_risk": "future drift",
        "break_condition": "a bound result fails",
    }
    writer = copy.deepcopy(native_review_provider_response_schema(bound.context))
    writer["$defs"]["evidence"]["properties"]["dimensions"].pop("pattern")
    validate_schema_document({"result": response}, writer)
    registered = {str(item["error_code"]) for item in registered_exceptions("claude")}
    with pytest.raises(NativeReviewContractError) as raised:
        parse_bound_native_contract_result(response, bound)
    assert raised.value.code.value not in registered


def test_closed_own_finding_mutations_are_rejected_by_writer_and_domain() -> None:
    template = _review_bound("convergence")
    closed = FindingRecord(
        "C-01",
        FindingClass.OBSERVATION,
        FindingStatus.CLOSED,
        "Already resolved.",
        "Keep it closed.",
        FindingOrigin("01", 1, AgentRole.CLAUDE),
        status_rationale="Resolved earlier.",
    )
    open_finding = FindingRecord(
        "C-02",
        FindingClass.BLOCKER,
        FindingStatus.OPEN,
        "Current blocker.",
        "Close the current blocker.",
        FindingOrigin("01", 2, AgentRole.CLAUDE),
    )
    context = replace(
        template.context,
        previous_findings=(closed, open_finding),
    )
    bound = BoundNativeReviewContext(
        context, template.request_id, template.request_digest
    )
    writer = native_review_provider_response_schema(context)

    mutations = (
        {
            "status_changes": [
                    {
                        "finding_id": "C-01",
                        "status": "OPEN",
                        "rationale": "Reopen the resolved finding.",
                        "closure": None,
                    }
            ],
            "reclassifications": [],
        },
        {
            "status_changes": [],
            "reclassifications": [
                {
                    "finding_id": "C-01",
                    "finding_class": "BLOCKER",
                    "rationale": "Reclassify the resolved finding.",
                }
            ],
        },
    )
    for mutation in mutations:
        response = _review_response(bound)
        response.update(
            decision="denied",
            new_findings=[],
            pre_mortem=None,
            **mutation,
        )
        with pytest.raises(SchemaMismatch):
            validate_schema_document({"result": response}, writer)
        with pytest.raises(NativeReviewContractError) as local_error:
            parse_bound_native_contract_result(response, bound)
        assert (
            local_error.value.code
            is NativeReviewErrorCode.FINDING_REFERENCE_NOT_OPEN
        )


def test_denial_cannot_satisfy_blocker_state_by_reopening_closed_blocker() -> None:
    template = _review_bound("initial_slice")
    closed = FindingRecord(
        "C-01",
        FindingClass.BLOCKER,
        FindingStatus.CLOSED,
        "Already resolved.",
        "Keep it closed.",
        FindingOrigin("01", 1, AgentRole.CLAUDE),
        status_rationale="Resolved earlier.",
    )
    open_finding = FindingRecord(
        "C-02",
        FindingClass.BLOCKER,
        FindingStatus.OPEN,
        "Current blocker.",
        "Close the current blocker.",
        FindingOrigin("01", 2, AgentRole.CLAUDE),
    )
    context = replace(template.context, previous_findings=(closed, open_finding))
    bound = BoundNativeReviewContext(
        context, template.request_id, template.request_digest
    )
    writer = native_review_provider_response_schema(context)
    response = _review_response(bound)
    response.update(
        decision="denied",
        new_findings=[],
        status_changes=[
            {
                "finding_id": "C-01",
                "status": "OPEN",
                "rationale": "Reopen it to make denial pass.",
                "closure": None,
            }
        ],
        reclassifications=[],
        pre_mortem=None,
    )
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": response}, writer)
    with pytest.raises(NativeReviewContractError) as local_error:
        parse_bound_native_contract_result(response, bound)
    assert local_error.value.code is NativeReviewErrorCode.FINDING_REFERENCE_NOT_OPEN

    control = copy.deepcopy(response)
    control["status_changes"] = []
    parse_bound_native_contract_result(control, bound)
