from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest

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
    parse_bound_native_contract_result,
)
from native_review_request import (
    NativeReviewEvidenceInput,
    NativeReviewKind,
    NativeReviewRepairError,
    NativeReviewRequestError,
    NativeReviewRequestBundle,
    NativeReviewRequestSpec,
    build_native_review_repair_request,
    build_native_review_request,
    canonical_native_review_request_json,
    load_native_review_request_schema,
)
from review_packets import ReviewPacket, build_review_packet


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
        "schema_version": "native-agent-review-result-v1",
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
    assert load_native_review_request_schema()["title"] == "Native Agent Review Request v1"
    first = build_native_review_request(_spec())
    second = build_native_review_request(_spec())
    assert first == second
    assert first.canonical_json == canonical_native_review_request_json(first.document)
    assert first.bound_context.request_id == first.document["request_id"]
    assert first.bound_context.request_id != first.bound_context.context.request_id
    assert first.document["review_contract"]["next_finding_id"] == "C-01"
    assert first.evidence_assets == ()


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
        )


def test_compact_repair_request_contains_no_review_evidence() -> None:
    bundle = build_native_review_request(_spec())
    rejected = json.dumps(
        _response(bundle.bound_context.request_id),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    repair = build_native_review_repair_request(
        parent=bundle,
        rejected_response_json=rejected,
        errors=(NativeReviewRepairError("approval-invalid", "pre_mortem is blank"),),
    )
    assert repair.document["request_type"] == "review_contract_repair_request"
    assert repair.document["parent_request_id"] == bundle.bound_context.request_id
    assert repair.bound_context.context is bundle.bound_context.context
    assert repair.bound_context.request_id != bundle.bound_context.request_id
    assert "evidence_manifest" not in repair.document
    assert "authorized_paths" not in repair.document
    assert "acceptance_criteria" not in repair.document
