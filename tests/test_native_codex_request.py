from __future__ import annotations

import json
from dataclasses import replace

import pytest

from contracts import (
    AgentRole,
    CodexStepContract,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ReadinessMarker,
)
from native_codex_contract import NativeCodexContext, NativeCodexRequestKind
from native_codex_request import (
    NativeCodexRequestBundle,
    NativeCodexEvidenceInput,
    NativeCodexRequestError,
    NativeCodexRequestSpec,
    build_native_codex_request,
    load_native_codex_request_schema,
)


def _spec(*, assignment: str = "Implement the native boundary.") -> NativeCodexRequestSpec:
    contract = CodexStepContract(
        name="native-plan",
        readiness_marker=ReadinessMarker.PLAN,
        slice_id="01",
        round_number=1,
        require_slice_plan=True,
        plan_artifact_path="docs/internal/plan.md",
    )
    context = NativeCodexContext(
        run_id="run-1",
        work_unit_id="work-unit-1",
        operation="codex_plan",
        current_fingerprint="a" * 64,
        request_kind=NativeCodexRequestKind.PLAN,
        contract=contract,
    )
    return NativeCodexRequestSpec(
        context=context,
        target_branch="feature/native-codex",
        base_commit="b" * 40,
        authorized_paths=("docs/internal/plan.md",),
        assignment=assignment,
        work_context="Repository-grounded planning context.",
        evidence=(
            NativeCodexEvidenceInput(
                "e01_plan",
                "work_plan",
                "A" * 100,
                "docs/internal/plan.md",
            ),
        ),
    )


def test_native_codex_request_is_deterministic_and_digest_bound() -> None:
    assert load_native_codex_request_schema()["$id"] == "native-agent-codex-request-v2"
    first = build_native_codex_request(_spec())
    second = build_native_codex_request(_spec())
    changed = build_native_codex_request(_spec(assignment="A changed assignment."))
    assert first.canonical_json == second.canonical_json
    assert first.bound_context.request_id == second.bound_context.request_id
    assert changed.bound_context.request_id != first.bound_context.request_id
    document = json.loads(first.canonical_json)
    assert document["request_id"] == first.bound_context.request_id
    assert document["codex_contract"]["readiness_kind"] == "plan"
    assert document["response_contract"]["schema_version"] == (
        "native-agent-codex-result-v2"
    )
    assert json.loads(first.provider_response_schema_json) == (
        first.provider_response_schema
    )


def test_request_kinds_bind_distinct_writer_schema_digests() -> None:
    base = _spec()
    readiness = {
        NativeCodexRequestKind.PLAN: ReadinessMarker.PLAN,
        NativeCodexRequestKind.IMPLEMENTATION: ReadinessMarker.IMPLEMENTATION,
        NativeCodexRequestKind.CORRECTION: ReadinessMarker.IMPLEMENTATION,
        NativeCodexRequestKind.FINAL_REPORT: ReadinessMarker.FINAL_REPORT,
    }
    digests: set[str] = set()
    for kind in NativeCodexRequestKind:
        contract = CodexStepContract(
            name=f"native-{kind.value}",
            readiness_marker=readiness[kind],
            slice_id="01",
            round_number=1,
            require_test_files_record=kind in {
                NativeCodexRequestKind.IMPLEMENTATION,
                NativeCodexRequestKind.CORRECTION,
            },
            require_slice_plan=kind is NativeCodexRequestKind.PLAN,
            plan_artifact_path=(
                "docs/internal/plan.md"
                if kind is NativeCodexRequestKind.PLAN
                else None
            ),
        )
        context = NativeCodexContext(
            run_id=base.context.run_id,
            work_unit_id=base.context.work_unit_id,
            operation=f"codex_{kind.value}",
            current_fingerprint=base.context.current_fingerprint,
            request_kind=kind,
            contract=contract,
        )
        bundle = build_native_codex_request(
            replace(base, context=context)
        )
        digests.add(bundle.document["response_contract"]["schema_sha256"])
    assert len(digests) == 4


def test_bundle_rejects_schema_bytes_not_derived_from_bound_context() -> None:
    bundle = build_native_codex_request(_spec())
    schema = json.loads(bundle.provider_response_schema_json)
    schema["title"] = "tampered"
    tampered = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    with pytest.raises(NativeCodexRequestError, match="differs from bound context"):
        replace(bundle, provider_response_schema_json=tampered)


def test_bundle_rejects_context_digest_and_evidence_misbinding() -> None:
    bundle = build_native_codex_request(_spec())
    changed_context = replace(bundle.bound_context.context, run_id="run-tampered")
    with pytest.raises(NativeCodexRequestError, match="context projection"):
        replace(
            bundle,
            bound_context=replace(
                bundle.bound_context, context=changed_context
            ),
        )

    document = dict(bundle.document)
    document["assignment"] = "Different request bytes."
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    with pytest.raises(NativeCodexRequestError, match="bound digest"):
        NativeCodexRequestBundle(
            canonical_json=canonical,
            bound_context=bundle.bound_context,
            provider_response_schema_json=bundle.provider_response_schema_json,
            evidence_assets=bundle.evidence_assets,
        )

    inline = dict(bundle.document)
    inline["evidence_manifest"][0]["sha256"] = "0" * 64
    binding = {key: value for key, value in inline.items() if key != "request_id"}
    digest = __import__("hashlib").sha256(
        json.dumps(
            binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    inline["request_id"] = "native-codex-request-" + digest
    with pytest.raises(NativeCodexRequestError, match="metadata differs"):
        NativeCodexRequestBundle(
            canonical_json=json.dumps(
                inline, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            bound_context=replace(
                bundle.bound_context,
                request_id=inline["request_id"],
                request_digest=digest,
            ),
            provider_response_schema_json=bundle.provider_response_schema_json,
        )


def test_closed_findings_are_record_authority_not_codex_request_fields() -> None:
    base = _spec()
    closed = FindingRecord(
        "C-01",
        FindingClass.BLOCKER,
        FindingStatus.CLOSED,
        "Closed finding",
        "Already fixed",
        FindingOrigin("01", 1, AgentRole.CLAUDE),
        status_rationale="Verified closed",
    )
    changed_closed = replace(closed, summary="Mirror-only tampering")
    first = build_native_codex_request(
        replace(base, context=replace(base.context, previous_findings=(closed,)))
    )
    second = build_native_codex_request(
        replace(
            base,
            context=replace(base.context, previous_findings=(changed_closed,)),
        )
    )
    assert first.canonical_json == second.canonical_json
    assert first.document["open_findings"] == []
    assert first.bound_context.context.previous_findings != (
        second.bound_context.context.previous_findings
    )


def test_large_evidence_is_digest_bound_and_uses_internal_artifact_path() -> None:
    spec = _spec()
    large = NativeCodexEvidenceInput(
        "e01_large",
        "diff",
        "x" * 1000,
        "src/workflow.py",
    )
    bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=spec.context,
            target_branch=spec.target_branch,
            base_commit=spec.base_commit,
            authorized_paths=spec.authorized_paths,
            assignment=spec.assignment,
            work_context=spec.work_context,
            evidence=(large,),
        ),
        inline_evidence_chars=10,
    )
    assert len(bundle.evidence_assets) == 1
    asset = bundle.evidence_assets[0]
    assert asset.path.startswith(
        ".orchestrator/artifacts/native-codex-evidence/e01_large-"
    )
    manifest = bundle.document["evidence_manifest"][0]
    assert manifest["content_ref"] == asset.path
    assert manifest["sha256"] == asset.sha256


@pytest.mark.parametrize(
    "evidence",
    (
        (
            NativeCodexEvidenceInput("e01_first", "diff", "same"),
            NativeCodexEvidenceInput("e02_second", "diff", "same"),
        ),
        (
            NativeCodexEvidenceInput("e01_first", "diff", "first", "src/a.py"),
            NativeCodexEvidenceInput("e02_second", "diff", "second", "src/a.py"),
        ),
    ),
)
def test_request_rejects_duplicate_evidence_content_or_source_path(
    evidence: tuple[NativeCodexEvidenceInput, ...],
) -> None:
    with pytest.raises(NativeCodexRequestError, match="unique"):
        replace(_spec(), evidence=evidence)


def test_content_ref_assets_reject_missing_extra_duplicate_swapped_and_changed() -> None:
    spec = _spec()
    evidence = (
        NativeCodexEvidenceInput("e01_first", "diff", "a" * 100),
        NativeCodexEvidenceInput("e02_second", "diff", "b" * 100),
    )
    bundle = build_native_codex_request(
        replace(spec, evidence=evidence), inline_evidence_chars=10
    )
    first, second = bundle.evidence_assets

    with pytest.raises(NativeCodexRequestError, match="differ from content references"):
        replace(bundle, evidence_assets=(first,))
    with pytest.raises(NativeCodexRequestError, match="paths must be unique"):
        replace(bundle, evidence_assets=(first, second, second))

    extra_bundle = build_native_codex_request(
        replace(
            spec,
            evidence=(NativeCodexEvidenceInput("e03_extra", "diff", "c" * 100),),
        ),
        inline_evidence_chars=10,
    )
    with pytest.raises(NativeCodexRequestError, match="differ from content references"):
        replace(bundle, evidence_assets=(*bundle.evidence_assets, *extra_bundle.evidence_assets))

    swapped = (replace(first, path=second.path), replace(second, path=first.path))
    with pytest.raises(NativeCodexRequestError, match="differ from content references"):
        replace(bundle, evidence_assets=swapped)

    with pytest.raises(NativeCodexRequestError, match="byte_count differs"):
        replace(first, content="changed")


def test_context_contract_and_evidence_changes_rebind_request() -> None:
    base = _spec()
    first = build_native_codex_request(base)
    changed_context = NativeCodexContext(
        run_id=base.context.run_id,
        work_unit_id=base.context.work_unit_id,
        operation=base.context.operation,
        current_fingerprint="c" * 64,
        request_kind=base.context.request_kind,
        contract=base.context.contract,
    )
    changed = build_native_codex_request(
        NativeCodexRequestSpec(
            context=changed_context,
            target_branch=base.target_branch,
            base_commit=base.base_commit,
            authorized_paths=base.authorized_paths,
            assignment=base.assignment,
            work_context=base.work_context,
            evidence=base.evidence,
        )
    )
    assert changed.bound_context.request_id != first.bound_context.request_id


@pytest.mark.parametrize("value", ["", "   ", "bad\x00value"])
def test_request_rejects_blank_or_nul_assignment(value: str) -> None:
    with pytest.raises(NativeCodexRequestError):
        _spec(assignment=value)


def test_request_rejects_unsorted_or_unsafe_paths() -> None:
    spec = _spec()
    with pytest.raises(NativeCodexRequestError):
        NativeCodexRequestSpec(
            context=spec.context,
            target_branch=spec.target_branch,
            base_commit=spec.base_commit,
            authorized_paths=("src/z.py", "src/a.py"),
            assignment=spec.assignment,
            work_context=spec.work_context,
            evidence=spec.evidence,
        )
    with pytest.raises(NativeCodexRequestError):
        NativeCodexEvidenceInput("e01_bad", "diff", "data", "../secret")
