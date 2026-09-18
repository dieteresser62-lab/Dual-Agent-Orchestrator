from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import native_finding_decisions

from contracts import (
    AgentRole,
    ApprovalMarker,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingResponse,
    FindingResponseDecision,
    FindingStatus,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from native_review_contract import (
    BoundNativeReviewContext,
    NativeReviewContext,
    NativeReviewContractError,
    NativeReviewErrorCode,
    NATIVE_REVIEW_RESPONSE_RETRY_CODES,
    load_native_review_schema,
    native_review_context_binding,
    parse_bound_native_contract_result,
    parse_native_contract_result,
    validate_native_review_document,
)
from finding_responsibility import SliceResponsibility
from native_finding_decisions import NativeResponsibilityProposal
from native_review_request import (
    NativeReviewEvidenceInput,
    NativeReviewKind,
    NativeReviewRequestError,
    NativeReviewRequestBundle,
    NativeReviewRequestSpec,
    NativeReviewRetryFeedback,
    build_native_review_request,
    canonical_native_review_request_json,
    load_native_review_request_schema,
    native_review_provider_response_schema,
    validate_native_review_request_document,
)
from review_packets import ReviewPacket, build_review_packet
from schema_validation import SchemaMismatch, validate_schema_document
from native_provider_schema import registered_exceptions


FINGERPRINT = "a" * 64
BASE_COMMIT = "b" * 40


def _attestation() -> ValidationAttestation:
    command = "python3 -m pytest tests/ -v"
    return ValidationAttestation(
        attestation_id="validation-native-request",
        diff_fingerprint=FINGERPRINT,
        expected_commands=(command,),
        records=(ValidationRecord(ValidationStatus.PASS, command, 0, "passed"),),
        output_digest=hashlib.sha256(b"passed").hexdigest(),
        summary="1 passed",
        command_specs=(
            ValidationCommandSpec(argv=("python3", "-m", "pytest", "tests/", "-v")),
        ),
    )


def _context() -> NativeReviewContext:
    return NativeReviewContext(
        run_id="run-native-request",
        work_unit_id="work-unit-1",
        operation="claude_slice_review",
        diff_fingerprint=FINGERPRINT,
        reviewer=AgentRole.CLAUDE,
        approval_marker=ApprovalMarker.SLICE,
        slice_id="01",
        round_number=1,
        validation_attestation=_attestation(),
        test_files=("tests/test_native_review_request.py",),
        test_changes_approved=True,
        anchor_origin="approved-plan",
        validation_command_prefixes=(("python3", "-m", "pytest"),),
    )


def _spec() -> NativeReviewRequestSpec:
    return NativeReviewRequestSpec(
        context=_context(),
        review_kind=NativeReviewKind.SLICE,
        target_branch="feature/native-claude-review-path",
        base_commit=BASE_COMMIT,
        authorized_paths=(
            "src/native_review_request.py",
            "tests/test_native_review_request.py",
        ),
        acceptance_criteria=(
            "Request identity binds the complete evidence.",
            "Legacy text parsing is not used.",
        ),
        evidence=(
            NativeReviewEvidenceInput(
                "current_diff",
                "diff",
                "diff --git a/src/a.py b/src/a.py\n+new content\n",
                "src/native_review_request.py",
            ),
            NativeReviewEvidenceInput(
                "work_plan",
                "plan",
                "# Approved plan\n",
                "docs/internal/native-claude-review-path-arbeitsplan.md",
            ),
        ),
    )


def test_provider_schema_forbids_anchors_without_bound_origin() -> None:
    without_origin = build_native_review_request(
        replace(_spec(), context=replace(_context(), anchor_origin=None))
    )
    with_origin = build_native_review_request(_spec())

    anchors_without_origin = without_origin.provider_response_schema["$defs"][
        "bound_slice_initial_approved"
    ]["properties"]["anchors"]
    anchors_with_origin = with_origin.provider_response_schema["$defs"][
        "bound_slice_initial_approved"
    ]["properties"]["anchors"]

    assert anchors_without_origin["maxItems"] == 0
    assert anchors_with_origin["maxItems"] == 64
    assert without_origin.document["response_contract"]["schema_sha256"] == (
        hashlib.sha256(
            json.dumps(
                without_origin.provider_response_schema,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    assert without_origin.document["response_contract"] != with_origin.document[
        "response_contract"
    ]
    assert (
        without_origin.bound_context.request_id
        != with_origin.bound_context.request_id
    )


def test_dormant_review_request_bytes_match_the_pre_contract_baseline() -> None:
    bundle = build_native_review_request(_spec())

    assert hashlib.sha256(bundle.canonical_json.encode("utf-8")).hexdigest() == (
        "8eefd769f80f52ca88183d64e7d17e877279a95f012ea1ef51024c641ffec502"
    )
    assert hashlib.sha256(
        bundle.provider_response_schema_json.encode("utf-8")
    ).hexdigest() == (
        "70187e6ab499518eb8f29b8f11af6b4befff7c83043829eefb8a68c228e85e70"
    )


def test_enabled_review_request_exposes_codex_proposal_as_non_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    finding = _prior_finding()
    proposal = NativeResponsibilityProposal(
        "C-01",
        SliceResponsibility("later-run", "c" * 40, "6"),
        "Codex proposes that the later Slice owns this work.",
    )
    context = replace(
        _context(),
        previous_findings=(finding,),
        implementer_responsibility_proposals=(proposal,),
    )

    bundle = build_native_review_request(replace(_spec(), context=context))

    assert bundle.document["review_contract"][
        "implementer_responsibility_proposals"
    ] == [
        {
            "finding_id": "C-01",
            "responsibility": {
                "responsibility_kind": "SLICE",
                "target_run_id": "later-run",
                "approved_plan_commit": "c" * 40,
                "slice_id": "6",
            },
            "rationale": "Codex proposes that the later Slice owns this work.",
        }
    ]


def test_plan_disposition_overflow_stops_before_request_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    findings = tuple(
        _prior_finding(f"C-{number:02d}") for number in range(1, 34)
    )
    context = replace(
        _context(),
        operation="claude_plan_review",
        approval_marker=ApprovalMarker.PLAN,
        previous_findings=findings,
        plan_artifact_path="docs/internal/plan.md",
    )
    spec = replace(
        _spec(),
        context=context,
        review_kind=NativeReviewKind.PLAN,
    )

    with pytest.raises(NativeReviewRequestError, match="PLAN_DISPOSITION_LIMIT"):
        build_native_review_request(spec)


def test_default_provider_schema_remains_anchor_capable() -> None:
    schema = native_review_provider_response_schema(_context())
    assert schema["$defs"]["bound_slice_initial_approved"]["properties"][
        "anchors"
    ]["maxItems"] == 64


def _prior_finding(
    finding_id: str = "C-01",
    *,
    finding_class: FindingClass = FindingClass.BLOCKER,
) -> FindingRecord:
    return FindingRecord(
        finding_id=finding_id,
        finding_class=finding_class,
        status=FindingStatus.OPEN,
        summary="Existing Claude finding",
        acceptance_test="Focused regression",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )


def _writer_response(*, decision: str = "approved") -> dict[str, object]:
    return {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": "native-review-request-" + "c" * 64,
        "reviewer": "claude",
        "decision": decision,
        "new_findings": [],
        "status_changes": [],
        "reclassifications": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "correctness, contracts, resume",
            "largest_residual_risk": "future provider drift",
            "break_condition": "an invalid bound response is generated",
        },
        "pre_mortem": (
            "A future schema projection could omit a bound duty."
            if decision == "approved"
            else None
        ),
    }


def test_writer_schema_is_operation_independent_but_round_and_marker_bound() -> None:
    context = _context()
    same_policy = replace(context, operation="free-text-name-does-not-select")
    convergence = replace(
        context,
        operation="another-free-text-name",
        round_number=2,
        allow_new_observations=False,
    )
    final = replace(
        context,
        operation="ignored-final-name",
        approval_marker=ApprovalMarker.FINAL,
    )

    first = native_review_provider_response_schema(context)
    assert native_review_provider_response_schema(same_policy) == first
    assert native_review_provider_response_schema(convergence) != first
    assert native_review_provider_response_schema(final) != first


def test_writer_projection_defensively_copies_the_same_reader_instance() -> None:
    base = load_native_review_schema()
    before = json.dumps(base, sort_keys=True, separators=(",", ":"))

    native_review_provider_response_schema(_context(), base_schema=base)
    between = json.dumps(base, sort_keys=True, separators=(",", ":"))
    native_review_provider_response_schema(
        replace(_context(), round_number=2, allow_new_observations=False),
        base_schema=base,
    )

    assert before == between
    assert json.dumps(base, sort_keys=True, separators=(",", ":")) == before


def test_writer_schema_bounds_touched_findings_anchors_and_convergence_observations() -> None:
    prior = _prior_finding()
    initial = replace(_context(), previous_findings=(prior,), anchor_origin=None)
    schema = native_review_provider_response_schema(initial)
    approved = _writer_response()
    approved["request_id"] = initial.request_id

    validate_schema_document({"result": approved}, schema)
    with pytest.raises(NativeReviewContractError) as missing_blocker:
        parse_native_contract_result(approved, initial)
    assert missing_blocker.value.code is NativeReviewErrorCode.APPROVAL_INVALID
    approved["status_changes"] = [
        {"finding_id": "C-01", "status": "OPEN", "rationale": "not fixed"}
    ]
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": approved}, schema)
    approved["status_changes"] = [
        {"finding_id": "C-01", "status": "CLOSED", "rationale": "fixed"}
    ]
    validate_schema_document({"result": approved}, schema)

    foreign = json.loads(json.dumps(approved))
    foreign["status_changes"][0]["finding_id"] = "C-99"
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": foreign}, schema)

    anchored = json.loads(json.dumps(approved))
    anchored["anchors"] = [
        {
            "anchor_id": "anchor-1",
            "input_fixture": "input",
            "expected": "output",
            "tolerance": "exact",
        }
    ]
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": anchored}, schema)

    convergence = replace(
        _context(), round_number=2, allow_new_observations=False
    )
    convergence_schema = native_review_provider_response_schema(convergence)
    denied = _writer_response(decision="denied")
    denied["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "summary": "Late non-blocking idea",
            "acceptance_test": {"kind": "prose", "text": "Follow up later"},
        }
    ]
    with pytest.raises(SchemaMismatch):
        validate_schema_document({"result": denied}, convergence_schema)
    denied["new_findings"][0]["finding_class"] = "BLOCKER"
    validate_schema_document({"result": denied}, convergence_schema)


def test_initial_slice_approval_can_record_an_open_observation() -> None:
    schema = native_review_provider_response_schema(_context())
    approved = _writer_response()
    approved["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "summary": "Cross-cutting follow-up",
            "acceptance_test": {"kind": "prose", "text": "Address in a later Slice"},
        }
    ]

    validate_schema_document({"result": approved}, schema)


def test_initial_slice_approval_can_reclassify_blocker_and_omit_observation() -> None:
    blocker = _prior_finding("C-01")
    observation = _prior_finding(
        "C-02", finding_class=FindingClass.OBSERVATION
    )
    context = replace(
        _context(), previous_findings=(blocker, observation)
    )
    bundle = build_native_review_request(replace(_spec(), context=context))
    approved = _writer_response()
    approved["request_id"] = bundle.bound_context.request_id
    approved["status_changes"] = [
        {
            "finding_id": "C-02",
            "status": "OPEN",
            "rationale": "Cross-cutting follow-up remains explicit.",
        }
    ]
    approved["reclassifications"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "rationale": "The residual risk is non-blocking.",
        }
    ]

    validate_schema_document(
        {"result": approved}, bundle.provider_response_schema
    )
    result = parse_bound_native_contract_result(
        approved, bundle.bound_context
    )
    assert result.approval is True
    assert tuple(
        (item.finding_id, item.finding_class, item.status)
        for item in result.findings
    ) == (
        ("C-01", FindingClass.OBSERVATION, FindingStatus.OPEN),
        ("C-02", FindingClass.OBSERVATION, FindingStatus.OPEN),
    )

    sparse_disposition = json.loads(json.dumps(approved))
    sparse_disposition["status_changes"] = []
    validate_schema_document(
        {"result": sparse_disposition}, bundle.provider_response_schema
    )
    sparse_result = parse_bound_native_contract_result(
        sparse_disposition, bundle.bound_context
    )
    assert sparse_result.approval is True
    assert tuple(item.finding_id for item in sparse_result.findings) == (
        "C-01",
        "C-02",
    )

    convergence_context = replace(
        context, round_number=2, allow_new_observations=False
    )
    convergence_bundle = build_native_review_request(
        replace(_spec(), context=convergence_context)
    )
    convergence_candidate = json.loads(json.dumps(approved))
    convergence_candidate["request_id"] = (
        convergence_bundle.bound_context.request_id
    )
    with pytest.raises(SchemaMismatch):
        validate_schema_document(
            {"result": convergence_candidate},
            convergence_bundle.provider_response_schema,
        )


def test_denied_writer_response_may_include_nonblank_pre_mortem() -> None:
    bundle = build_native_review_request(_spec())
    denied = _writer_response(decision="denied")
    denied["request_id"] = bundle.bound_context.request_id
    denied["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "BLOCKER",
            "summary": "The bounded contract still has a defect.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Correct the defect and repeat the focused review.",
            },
        }
    ]
    denied["pre_mortem"] = "The same defect could recur after a provider update."

    validate_schema_document(
        {"result": denied}, bundle.provider_response_schema
    )
    result = parse_bound_native_contract_result(denied, bundle.bound_context)
    assert result.approval is False
    assert result.pre_mortem == denied["pre_mortem"]

    blank = json.loads(json.dumps(denied))
    blank["pre_mortem"] = "   "
    with pytest.raises(SchemaMismatch):
        validate_schema_document(
            {"result": blank}, bundle.provider_response_schema
        )


def test_initial_denial_can_add_observation_only_under_observation_policy() -> None:
    blocker = _prior_finding("C-01")
    context = replace(_context(), previous_findings=(blocker,))
    bundle = build_native_review_request(replace(_spec(), context=context))
    denied = _writer_response(decision="denied")
    denied["request_id"] = bundle.bound_context.request_id
    denied["new_findings"] = [
        {
            "finding_id": "C-02",
            "finding_class": "OBSERVATION",
            "summary": "Non-blocking follow-up discovered during denial.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Retain the follow-up for a later Slice.",
            },
        }
    ]

    validate_schema_document(
        {"result": denied}, bundle.provider_response_schema
    )
    result = parse_bound_native_contract_result(denied, bundle.bound_context)
    assert result.approval is False
    assert tuple(
        (item.finding_id, item.finding_class, item.status)
        for item in result.findings
    ) == (
        ("C-01", FindingClass.BLOCKER, FindingStatus.OPEN),
        ("C-02", FindingClass.OBSERVATION, FindingStatus.OPEN),
    )

    convergence_context = replace(
        context, round_number=2, allow_new_observations=False
    )
    convergence_bundle = build_native_review_request(
        replace(_spec(), context=convergence_context)
    )
    convergence_candidate = json.loads(json.dumps(denied))
    convergence_candidate["request_id"] = (
        convergence_bundle.bound_context.request_id
    )
    with pytest.raises(SchemaMismatch):
        validate_schema_document(
            {"result": convergence_candidate},
            convergence_bundle.provider_response_schema,
        )

    final_context = replace(
        context,
        operation="claude_final_review",
        approval_marker=ApprovalMarker.FINAL,
    )
    final_bundle = build_native_review_request(
        replace(
            _spec(),
            review_kind=NativeReviewKind.FINAL,
            context=final_context,
        )
    )
    final_candidate = json.loads(json.dumps(denied))
    final_candidate["request_id"] = final_bundle.bound_context.request_id
    with pytest.raises(SchemaMismatch):
        validate_schema_document(
            {"result": final_candidate}, final_bundle.provider_response_schema
        )

    no_prior_bundle = build_native_review_request(_spec())
    observation_only = _writer_response(decision="denied")
    observation_only["request_id"] = no_prior_bundle.bound_context.request_id
    observation_only["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "summary": "Observation alone cannot justify denial.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Keep the resulting-blocker fold fail-closed.",
            },
        }
    ]
    validate_schema_document(
        {"result": observation_only},
        no_prior_bundle.provider_response_schema,
    )
    with pytest.raises(NativeReviewContractError) as raised:
        parse_bound_native_contract_result(
            observation_only, no_prior_bundle.bound_context
        )
    assert raised.value.code is NativeReviewErrorCode.APPROVAL_INVALID


def test_writer_schema_forces_denial_without_attestation_or_test_approval() -> None:
    approved = _writer_response()
    command = "python3 -m pytest tests/ -v"
    failed_attestation = replace(
        _attestation(),
        records=(
            ValidationRecord(ValidationStatus.FAIL, command, 1, "failed"),
        ),
        summary="1 failed",
    )
    incomplete_attestation = replace(_attestation(), records=())
    for context in (
        replace(_context(), validation_attestation=None),
        replace(_context(), validation_attestation=failed_attestation),
        replace(_context(), validation_attestation=incomplete_attestation),
        replace(_context(), test_changes_approved=False),
    ):
        schema = native_review_provider_response_schema(context)
        with pytest.raises(SchemaMismatch):
            validate_schema_document({"result": approved}, schema)


def test_writer_schema_requires_approval_evidence_pre_mortem_and_closed_stop() -> None:
    schema = native_review_provider_response_schema(_context())
    approved = _writer_response()
    for field, invalid in (
        ("review_evidence", None),
        ("pre_mortem", None),
        ("pre_mortem", "   "),
    ):
        candidate = {**approved, field: invalid}
        with pytest.raises(SchemaMismatch):
            validate_schema_document({"result": candidate}, schema)

    stop = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "stop_request",
        "request_id": "native-review-request-" + "c" * 64,
        "reviewer": "claude",
        "rule_id": "UNEXPECTED-PATH",
        "rationale": "Additional scope is required.",
        "remediation_paths": [],
    }
    validate_schema_document({"result": stop}, schema)
    with pytest.raises(SchemaMismatch):
        validate_schema_document(
            {"result": {**stop, "decision": "denied"}}, schema
        )


def test_plan_slice_convergence_and_final_requests_bind_distinct_writer_digests() -> None:
    base = _spec()
    contexts = (
        replace(
            base,
            review_kind=NativeReviewKind.PLAN,
            context=replace(
                base.context,
                operation="claude_plan_review",
                approval_marker=ApprovalMarker.PLAN,
            ),
        ),
        base,
        replace(
            base,
            context=replace(
                base.context, round_number=2, allow_new_observations=False
            ),
        ),
        replace(
            base,
            review_kind=NativeReviewKind.FINAL,
            context=replace(
                base.context,
                operation="claude_final_review",
                approval_marker=ApprovalMarker.FINAL,
            ),
        ),
    )
    bundles = tuple(build_native_review_request(item) for item in contexts)
    schema_digests = {
        item.document["response_contract"]["schema_sha256"] for item in bundles
    }
    request_ids = {item.bound_context.request_id for item in bundles}

    assert len(schema_digests) == 4
    assert len(request_ids) == 4


def test_final_request_with_unoffered_remainder_forbids_approval_branch() -> None:
    offered = (_prior_finding("C-01"), _prior_finding("C-02"))
    context = replace(
        _context(),
        operation="claude_final_review",
        approval_marker=ApprovalMarker.FINAL,
        previous_findings=offered,
        final_review_pending_count=3,
    )
    bundle = build_native_review_request(
        replace(_spec(), review_kind=NativeReviewKind.FINAL, context=context)
    )
    result_refs = bundle.provider_response_schema["properties"]["result"]["oneOf"]

    assert result_refs == [
        {"$ref": "#/$defs/bound_final_denied"},
        {"$ref": "#/$defs/bound_final_stop"},
    ]
    assert bundle.document["review_contract"]["disposition_budget"] == {
        "maximum_items": 2,
        "eligible_finding_ids": ["C-01", "C-02"],
        "pending_finding_count": 3,
    }


def test_request_bundle_binds_exact_immutable_writer_schema_bytes() -> None:
    bundle = build_native_review_request(_spec())
    detached = bundle.provider_response_schema
    detached["title"] = "tampered"

    assert bundle.provider_response_schema["title"] != "tampered"
    assert hashlib.sha256(
        bundle.provider_response_schema_json.encode("utf-8")
    ).hexdigest() == bundle.document["response_contract"]["schema_sha256"]


def test_registered_review_exceptions_cover_writer_valid_local_rejections() -> None:
    observation_one = _prior_finding(
        "C-01", finding_class=FindingClass.OBSERVATION
    )
    observation_two = _prior_finding(
        "C-02", finding_class=FindingClass.OBSERVATION
    )
    blocker = _prior_finding("C-01")
    duplicate_context = replace(
        _context(), previous_findings=(observation_one, observation_two)
    )
    blocker_context = replace(_context(), previous_findings=(blocker,))
    contexts_and_responses: list[tuple[NativeReviewContext, dict[str, object]]] = []

    request_mismatch = _writer_response()
    contexts_and_responses.append((_context(), request_mismatch))

    invalid_command = _writer_response(decision="denied")
    invalid_command["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "BLOCKER",
            "summary": "Unsafe validation family",
            "acceptance_test": {
                "kind": "validation_command",
                "argv": ["outside", "configured", "family"],
            },
        }
    ]
    contexts_and_responses.append((_context(), invalid_command))

    duplicate_anchors = _writer_response()
    duplicate_anchors["anchors"] = [
        {
            "anchor_id": "same-anchor",
            "input_fixture": "first input",
            "expected": "first output",
            "tolerance": "exact",
        },
        {
            "anchor_id": "same-anchor",
            "input_fixture": "second input",
            "expected": "second output",
            "tolerance": "exact",
        },
    ]
    contexts_and_responses.append((_context(), duplicate_anchors))

    duplicate_events = _writer_response()
    duplicate_events["status_changes"] = [
        {"finding_id": "C-01", "status": "CLOSED", "rationale": "fixed"},
        {"finding_id": "C-01", "status": "CLOSED", "rationale": "fixed"},
    ]
    contexts_and_responses.append((duplicate_context, duplicate_events))

    duplicate_signature = _writer_response(decision="denied")
    duplicate_signature["new_findings"] = [
        {
            "finding_id": finding_id,
            "finding_class": "BLOCKER",
            "summary": "A repeated new issue affects src/repeated.py.",
            "acceptance_test": {
                "kind": "prose",
                "text": "Preserve valid data in src/repeated.py.",
            },
        }
        for finding_id in ("C-02", "C-03")
    ]
    contexts_and_responses.append((blocker_context, duplicate_signature))

    noncontiguous = _writer_response(decision="denied")
    noncontiguous["new_findings"] = [
        {
            "finding_id": finding_id,
            "finding_class": "BLOCKER",
            "summary": "Ordered finding",
            "acceptance_test": {"kind": "prose", "text": "Focused regression"},
        }
        for finding_id in ("C-02", "C-01")
    ]
    contexts_and_responses.append((_context(), noncontiguous))

    no_remaining_blocker = _writer_response(decision="denied")
    no_remaining_blocker["status_changes"] = [
        {"finding_id": "C-01", "status": "CLOSED", "rationale": "fixed"}
    ]
    contexts_and_responses.append((blocker_context, no_remaining_blocker))

    encountered: set[str] = set()
    for index, (context, response) in enumerate(contexts_and_responses):
        bundle = build_native_review_request(
            replace(_spec(), context=context)
        )
        if index != 0:
            response["request_id"] = bundle.bound_context.request_id
        validate_schema_document(
            {"result": response}, bundle.provider_response_schema
        )
        with pytest.raises(NativeReviewContractError) as raised:
            parse_bound_native_contract_result(response, bundle.bound_context)
        encountered.add(raised.value.code.value)

    assert encountered == {
        str(item["error_code"]) for item in registered_exceptions("claude")
    }


def _packet_evidence(*, content: str = "+new content") -> NativeReviewEvidenceInput:
    plan = """# Plan

### Slice 1 - Native packet binding

#### Fokussierte synthetische Akzeptanztests

- Packet manifest digest is bound.
"""
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/src/a.py\n+++ b/src/a.py\n"
        f"@@ -1 +1 @@\n-old\n{content}\n"
    )
    packet = build_review_packet(
        purpose="slice", fingerprint=FINGERPRINT, start_fingerprint="0" * 64,
        paths=("src/a.py",), review_diff=diff, plan_text=plan, slice_id=1,
        attestation=_attestation(), findings=(),
    )
    return NativeReviewEvidenceInput(
        "review-packet", "canonical_review_packet", packet.text,
        semantic_digest=packet.manifest.diff_coverage_digest,
    )


def _legacy_packet_evidence() -> NativeReviewEvidenceInput:
    canonical = json.dumps(
        {
            "schema": "review-packet-v1",
            "purpose": "slice",
            "fingerprint": FINGERPRINT,
            "manifest": {"paths": ["src/a.py"]},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    packet = ReviewPacket.restore(canonical, hashlib.sha256(canonical).hexdigest())
    return NativeReviewEvidenceInput(
        "review-packet", "canonical_review_packet", packet.text
    )


def _response(request_id: str) -> dict[str, object]:
    return {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": request_id,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [],
        "status_changes": [],
        "reclassifications": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "correctness, failure paths, resume",
            "largest_residual_risk": "pilot binding drift",
            "break_condition": "narrow request identity is accepted",
        },
        "pre_mortem": "Recovery could rebuild a different request.",
    }


def test_request_schema_loads_and_build_is_canonical_and_deterministic() -> None:
    assert load_native_review_request_schema()["title"] == "Native Agent Review Request v2"
    first = build_native_review_request(_spec())
    second = build_native_review_request(_spec())
    assert first == second
    assert first.canonical_json == canonical_native_review_request_json(first.document)
    assert first.bound_context.request_id == first.document["request_id"]
    assert first.bound_context.request_id != first.bound_context.context.request_id
    assert first.document["review_contract"]["next_finding_id"] == "C-01"
    assert first.evidence_assets == ()


@pytest.mark.parametrize(
    "rejection_code",
    tuple(sorted(NATIVE_REVIEW_RESPONSE_RETRY_CODES, key=lambda item: item.value)),
)
def test_retry_feedback_is_typed_and_changes_the_request_identity(
    rejection_code: NativeReviewErrorCode,
) -> None:
    initial = build_native_review_request(_spec())
    feedback = NativeReviewRetryFeedback(
        prior_invocation_id="0eview-attempt-1",
        rejection_code=rejection_code,
        correction_instruction=(
            "Return one JSON result that conforms exactly to the bound writer schema."
        ),
    )
    retried = build_native_review_request(replace(_spec(), retry_feedback=feedback))

    assert "retry_feedback" not in initial.document
    assert retried.document["retry_feedback"] == {
        "prior_invocation_id": "0eview-attempt-1",
        "rejection_code": rejection_code.value,
        "correction_instruction": (
            "Return one JSON result that conforms exactly to the bound writer schema."
        ),
    }
    assert retried.bound_context.request_id != initial.bound_context.request_id


@pytest.mark.parametrize(
    "offered_numbers",
    ((), (1, 17, 65)),
    ids=("empty-subset", "arbitrary-subset"),
)
def test_next_finding_id_uses_complete_ledger_not_offered_subset(
    offered_numbers: tuple[int, ...],
) -> None:
    findings = tuple(
        replace(
            _prior_finding(f"C-{number:02d}"),
            status=(FindingStatus.CLOSED if number == 23 else FindingStatus.OPEN),
            status_rationale=("Verified earlier." if number == 23 else None),
        )
        for number in range(1, 66)
    )
    offered = tuple(findings[number - 1] for number in offered_numbers)
    context = replace(
        _context(),
        previous_findings=offered,
        authoritative_finding_ids=tuple(
            finding.finding_id for finding in findings
        ),
    )

    bundle = build_native_review_request(replace(_spec(), context=context))

    assert tuple(
        item["finding_id"]
        for item in bundle.document["review_contract"]["previous_findings"]
    ) == tuple(f"C-{number:02d}" for number in offered_numbers)
    assert bundle.document["review_contract"]["next_finding_id"] == "C-66"
    assert "authoritative_finding_ids" not in bundle.canonical_json


def test_next_finding_id_uses_numeric_maximum_beyond_two_digits() -> None:
    context = replace(
        _context(),
        previous_findings=(),
        authoritative_finding_ids=("C-99", "C-100"),
    )

    bundle = build_native_review_request(replace(_spec(), context=context))

    assert bundle.document["review_contract"]["next_finding_id"] == "C-101"


def test_only_plan_requests_carry_the_mandatory_artifact_path_decision() -> None:
    slice_bundle = build_native_review_request(_spec())
    assert "plan_artifact_path" not in slice_bundle.document["review_contract"]

    plan_spec = replace(
        _spec(),
        review_kind=NativeReviewKind.PLAN,
        context=replace(
            _context(),
            operation="claude_plan_review",
            approval_marker=ApprovalMarker.PLAN,
            plan_artifact_path=None,
        ),
    )
    plan_bundle = build_native_review_request(plan_spec)
    assert plan_bundle.document["review_contract"]["plan_artifact_path"] is None

    missing = dict(plan_bundle.document)
    missing["review_contract"] = dict(missing["review_contract"])
    missing["review_contract"].pop("plan_artifact_path")
    with pytest.raises(NativeReviewRequestError, match="plan reviews alone"):
        validate_native_review_request_document(missing)

    extra = dict(slice_bundle.document)
    extra["review_contract"] = dict(extra["review_contract"])
    extra["review_contract"]["plan_artifact_path"] = None
    with pytest.raises(NativeReviewRequestError, match="plan reviews alone"):
        validate_native_review_request_document(extra)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda spec: replace(spec, base_commit="c" * 40),
        lambda spec: replace(
            spec,
            authorized_paths=("src/extra.py", *spec.authorized_paths),
        ),
        lambda spec: replace(
            spec, acceptance_criteria=(*spec.acceptance_criteria, "Additional invariant.")
        ),
        lambda spec: replace(
            spec,
            context=replace(spec.context, round_number=2),
        ),
        lambda spec: replace(
            spec,
            evidence=(
                replace(spec.evidence[0], content=spec.evidence[0].content + "changed"),
                spec.evidence[1],
            ),
        ),
    ),
)
def test_semantic_request_changes_change_request_id(mutate) -> None:  # type: ignore[no-untyped-def]
    original = build_native_review_request(_spec())
    changed = build_native_review_request(mutate(_spec()))
    assert changed.bound_context.request_id != original.bound_context.request_id


def test_request_binds_persisted_codex_disposition_and_attestation() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="The native loop must carry the response.",
        acceptance_test="Claude sees the durable Codex disposition.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
        responses=(
            FindingResponse(
                FindingResponseDecision.ACCEPTED,
                "The correction now implements the invariant.",
            ),
        ),
    )
    spec = _spec()
    bound = build_native_review_request(
        replace(
            spec,
            context=replace(spec.context, previous_findings=(finding,)),
        )
    )

    persisted = bound.document["review_contract"]["previous_findings"][0]
    assert persisted["responses"] == [
        {
            "decision": "ACCEPTED",
            "rationale": "The correction now implements the invariant.",
        }
    ]
    assert bound.document["review_contract"]["validation_attestation"][
        "attestation_id"
    ] == "validation-native-request"
    assert bound.bound_context.request_id != build_native_review_request(spec).bound_context.request_id


def test_request_names_signatures_for_known_open_findings_outside_offer() -> None:
    first = _prior_finding("C-01")
    offered = replace(
        _prior_finding("C-02", finding_class=FindingClass.OBSERVATION),
        summary="A different issue affects src/other.py.",
        acceptance_test="Preserve valid data in src/other.py.",
    )
    context = replace(
        _context(),
        previous_findings=(offered,),
        known_open_findings=(first, offered),
        authoritative_finding_ids=("C-01", "C-02"),
    )

    bundle = build_native_review_request(replace(_spec(), context=context))
    signatures = bundle.document["review_contract"][
        "known_open_finding_signatures"
    ]

    assert signatures == native_review_context_binding(context)[
        "known_open_finding_signatures"
    ]
    assert [item["finding_id"] for item in signatures] == ["C-01", "C-02"]


def test_large_evidence_is_content_addressed_and_bound() -> None:
    spec = replace(
        _spec(),
        evidence=(
            NativeReviewEvidenceInput("large_diff", "diff", "x" * 101),
        ),
    )
    bundle = build_native_review_request(spec, inline_evidence_chars=100)
    item = bundle.document["evidence_manifest"][0]
    assert item["delivery"] == "content_ref"
    assert "content" not in item
    assert len(bundle.evidence_assets) == 1
    assert bundle.evidence_assets[0].path == item["content_ref"]
    assert bundle.evidence_assets[0].sha256 == item["sha256"]


@pytest.mark.parametrize("inline_limit", (1, 1_000_000))
def test_canonical_packet_semantic_digest_is_bound_for_both_deliveries(
    inline_limit: int,
) -> None:
    evidence = _packet_evidence()
    bundle = build_native_review_request(
        replace(_spec(), evidence=(evidence,)), inline_evidence_chars=inline_limit
    )
    item = bundle.document["evidence_manifest"][0]
    assert item["semantic_digest"] == evidence.semantic_digest
    assert build_native_review_request(
        replace(_spec(), evidence=(_packet_evidence(content="+changed"),)),
        inline_evidence_chars=inline_limit,
    ).bound_context.request_id != bundle.bound_context.request_id


def test_canonical_packet_rejects_missing_or_mismatched_semantic_digest() -> None:
    evidence = _packet_evidence()
    with pytest.raises(NativeReviewRequestError, match="requires semantic_digest"):
        replace(evidence, semantic_digest=None)
    with pytest.raises(NativeReviewRequestError, match="differs from packet manifest"):
        replace(evidence, semantic_digest="f" * 64)
    with pytest.raises(NativeReviewRequestError, match="reserved"):
        replace(_spec().evidence[0], semantic_digest="f" * 64)


@pytest.mark.parametrize("inline_limit", (1, 1_000_000))
def test_legacy_canonical_packet_remains_requestable_without_semantic_digest(
    inline_limit: int,
) -> None:
    bundle = build_native_review_request(
        replace(_spec(), evidence=(_legacy_packet_evidence(),)),
        inline_evidence_chars=inline_limit,
    )
    assert "semantic_digest" not in bundle.document["evidence_manifest"][0]


@pytest.mark.parametrize(
    "paths",
    (
        ("/absolute.py",),
        ("src/../secret.py",),
        ("src/a.py", "src/a.py"),
        ("tests/z.py", "src/a.py"),
    ),
)
def test_request_rejects_unsafe_duplicate_or_unsorted_paths(paths: tuple[str, ...]) -> None:
    with pytest.raises(NativeReviewRequestError):
        replace(_spec(), authorized_paths=paths)


@pytest.mark.parametrize(
    "evidence",
    (
        (
            NativeReviewEvidenceInput("e01_first", "diff", "same"),
            NativeReviewEvidenceInput("e02_second", "diff", "same"),
        ),
        (
            NativeReviewEvidenceInput("e01_first", "diff", "first", "src/a.py"),
            NativeReviewEvidenceInput("e02_second", "diff", "second", "src/a.py"),
        ),
    ),
)
def test_request_rejects_duplicate_evidence_content_or_source_path(
    evidence: tuple[NativeReviewEvidenceInput, ...],
) -> None:
    with pytest.raises(NativeReviewRequestError, match="unique"):
        replace(_spec(), evidence=evidence)


def test_bound_parser_rejects_legacy_narrow_request_id() -> None:
    bundle = build_native_review_request(_spec())
    result = parse_bound_native_contract_result(
        _response(bundle.bound_context.request_id), bundle.bound_context
    )
    assert result.approval is True
    with pytest.raises(NativeReviewContractError) as raised:
        parse_bound_native_contract_result(
            _response(bundle.bound_context.context.request_id), bundle.bound_context
        )
    assert raised.value.code is NativeReviewErrorCode.REQUEST_MISMATCH


def test_request_bundle_document_is_a_detached_view() -> None:
    bundle = build_native_review_request(_spec())
    first = bundle.document
    first["target_branch"] = "tampered"
    first["authorized_paths"].append("src/tampered.py")

    assert bundle.document["target_branch"] == "feature/native-claude-review-path"
    assert "src/tampered.py" not in bundle.document["authorized_paths"]


def test_request_bundle_rejects_inconsistent_inline_evidence_metadata() -> None:
    bundle = build_native_review_request(_spec())
    document = bundle.document
    document["evidence_manifest"][0]["sha256"] = "0" * 64
    binding = {key: value for key, value in document.items() if key != "request_id"}
    canonical_binding = json.dumps(
        binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical_binding.encode("utf-8")).hexdigest()
    document["request_id"] = f"native-review-request-{digest}"
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    with pytest.raises(NativeReviewRequestError, match="metadata differs from content"):
        NativeReviewRequestBundle(
            canonical_json=canonical,
            bound_context=BoundNativeReviewContext(
                context=bundle.bound_context.context,
                request_id=document["request_id"],
                request_digest=digest,
            ),
            provider_response_schema_json=bundle.provider_response_schema_json,
        )


def test_request_bundle_rejects_bound_context_drift() -> None:
    bundle = build_native_review_request(_spec())
    changed_context = replace(bundle.bound_context.context, run_id="run-tampered")
    with pytest.raises(NativeReviewRequestError, match="bound review context"):
        replace(
            bundle,
            bound_context=replace(
                bundle.bound_context, context=changed_context
            ),
        )

    document = bundle.document
    wrong_id = "native-review-request-" + "0" * 64
    document["request_id"] = wrong_id
    with pytest.raises(NativeReviewRequestError, match="bound digest"):
        NativeReviewRequestBundle(
            canonical_json=json.dumps(
                document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            bound_context=replace(
                bundle.bound_context,
                request_id=wrong_id,
                request_digest="0" * 64,
            ),
            provider_response_schema_json=bundle.provider_response_schema_json,
            evidence_assets=bundle.evidence_assets,
        )


def test_retired_repair_request_is_rejected_by_the_active_reader_schema() -> None:
    bundle = build_native_review_request(_spec())
    document = dict(bundle.document)
    document["request_type"] = "review_contract_repair_request"
    with pytest.raises(NativeReviewRequestError) as caught:
        validate_native_review_request_document(document)
    assert caught.value.code.value == "schema-invalid"
