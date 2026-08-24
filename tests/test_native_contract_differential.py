from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

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
from native_provider_schema import registered_exceptions
from native_review_contract import (
    BoundNativeReviewContext,
    NativeReviewContext,
    NativeReviewContractError,
    native_review_provider_response_schema,
    parse_bound_native_contract_result,
)
from schema_validation import validate_schema_document


ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = "a" * 64


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _finding() -> FindingRecord:
    return FindingRecord(
        "C-01",
        FindingClass.BLOCKER,
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
        NativeCodexRequestKind.FINAL_REPORT: ReadinessMarker.FINAL_REPORT,
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
        "schema_version": "native-agent-codex-result-v1",
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
                }
            ],
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
    return {
        **common,
        "result_type": "final_report_result",
        "self_check": "All bound dimensions were checked.",
    }


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
        "final": ApprovalMarker.FINAL,
    }[form]
    context = NativeReviewContext(
        run_id="differential-claude",
        work_unit_id=f"work-{form}",
        operation={
            ApprovalMarker.PLAN: "claude_plan_review",
            ApprovalMarker.SLICE: "claude_slice_review",
            ApprovalMarker.FINAL: "claude_final_review",
        }[marker],
        diff_fingerprint=FINGERPRINT,
        reviewer=AgentRole.CLAUDE,
        approval_marker=marker,
        slice_id="01" if marker is not ApprovalMarker.FINAL else "final",
        round_number=2 if convergence else 1,
        previous_findings=(_finding(),) if convergence else (),
        validation_attestation=_attestation(),
        test_files=("tests/test_native_contract_differential.py",),
        test_changes_approved=True,
        allow_new_observations=not convergence,
        anchor_origin="docs/internal/plan.md",
        validation_command_prefixes=(("python3", "-m", "pytest"),),
    )
    return BoundNativeReviewContext(
        context, "native-review-request-" + "c" * 64, "c" * 64
    )


def _review_response(bound: BoundNativeReviewContext) -> dict[str, object]:
    return {
        "schema_version": "native-agent-review-result-v1",
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
                }
            ]
            if bound.context.previous_findings
            else []
        ),
        "reclassifications": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "correctness, contracts, failure paths, resume",
            "largest_residual_risk": "future provider subset drift",
            "break_condition": "a writer-valid response fails local binding",
        },
        "pre_mortem": "A later provider version could change its schema subset.",
    }


def test_all_eight_writer_forms_accept_their_local_domain_result() -> None:
    digests: set[str] = set()
    for kind in NativeCodexRequestKind:
        bound = _codex_bound(kind)
        response = _codex_response(bound)
        writer = native_codex_provider_response_schema(bound.context)
        validate_schema_document({"result": response}, writer)
        parse_bound_native_codex_contract_result(response, bound)
        digests.add(_canonical(writer))
    for form in ("plan", "initial_slice", "convergence", "final"):
        bound = _review_bound(form)
        response = _review_response(bound)
        writer = native_review_provider_response_schema(bound.context)
        validate_schema_document({"result": response}, writer)
        parse_bound_native_contract_result(response, bound)
        digests.add(_canonical(writer))
    assert len(digests) == 8


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
