from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from content_authority_support import prior_role_wire_document
from contracts import (
    AgentRole,
    ImplementerStepContract,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ReadinessMarker,
)
import native_implementer_contract
import plan_handoff
from native_implementer_contract import (
    NativeImplementerContext,
    NativeImplementerErrorCode,
    NativeImplementerRequestKind,
    load_native_implementer_schema,
)
from native_implementer_request import (
    NativeImplementerRequestBundle,
    NativeImplementerEvidenceInput,
    NativeImplementerRequestError,
    NativeImplementerRequestSpec,
    NativeImplementerRetryFeedback,
    build_native_implementer_request,
    load_native_implementer_request_schema,
    validate_native_implementer_request_document,
)


def _spec(*, assignment: str = "Implement the native boundary.") -> NativeImplementerRequestSpec:
    contract = ImplementerStepContract(
        name="native-plan",
        readiness_marker=ReadinessMarker.PLAN,
        slice_id="01",
        round_number=1,
        require_slice_plan=True,
        plan_artifact_path="docs/internal/plan.md",
    )
    context = NativeImplementerContext(
        run_id="run-1",
        work_unit_id="work-unit-1",
        operation="implementer_plan",
        current_fingerprint="a" * 64,
        request_kind=NativeImplementerRequestKind.PLAN,
        contract=contract,
    )
    return NativeImplementerRequestSpec(
        context=context,
        target_branch="feature/native-codex",
        base_commit="b" * 40,
        authorized_paths=("docs/internal/plan.md",),
        assignment=assignment,
        work_context="Repository-grounded planning context.",
        evidence=(
            NativeImplementerEvidenceInput(
                "e01_plan",
                "work_plan",
                "A" * 100,
                "docs/internal/plan.md",
            ),
        ),
    )


def test_native_implementer_request_is_deterministic_and_digest_bound() -> None:
    assert load_native_implementer_request_schema()["$id"] == "native-agent-implementer-request-v3"
    first = build_native_implementer_request(_spec())
    assert hashlib.sha256(first.provider_response_schema_json.encode()).hexdigest() == (
        "8bf937f092c712f02515ebd9c8edc905c9bb64b893a619b485d057d88fb687e4"
    )
    second = build_native_implementer_request(_spec())
    changed = build_native_implementer_request(_spec(assignment="A changed assignment."))
    assert first.canonical_json == second.canonical_json
    assert first.bound_context.request_id == second.bound_context.request_id
    assert changed.bound_context.request_id != first.bound_context.request_id
    document = json.loads(first.canonical_json)
    assert document["request_id"] == first.bound_context.request_id
    assert document["implementer_contract"]["readiness_kind"] == "plan"
    assert document["response_contract"]["schema_version"] == (
        "native-agent-implementer-result-v3"
    )
    assert json.loads(first.provider_response_schema_json) == (
        first.provider_response_schema
    )


def test_retry_feedback_is_typed_and_changes_codex_request_identity() -> None:
    initial = build_native_implementer_request(_spec())
    feedback = NativeImplementerRetryFeedback(
        prior_invocation_id="codex-attempt-1",
        rejection_code=NativeImplementerErrorCode.SLICE_PLAN_INVALID,
        correction_instruction=(
            "slice-plan-invalid: planned slice paths must be sorted, unique, "
            "and non-empty"
        ),
    )

    retried = build_native_implementer_request(
        replace(_spec(), retry_feedback=feedback)
    )

    assert "retry_feedback" not in initial.document
    assert retried.document["retry_feedback"] == {
        "prior_invocation_id": "codex-attempt-1",
        "rejection_code": "slice-plan-invalid",
        "correction_instruction": feedback.correction_instruction,
    }
    assert retried.bound_context.request_id != initial.bound_context.request_id


def test_context_invalid_cannot_be_bound_as_codex_retry_feedback() -> None:
    with pytest.raises(NativeImplementerRequestError, match="response-dependent"):
        NativeImplementerRetryFeedback(
            prior_invocation_id="codex-attempt-1",
            rejection_code=NativeImplementerErrorCode.CONTEXT_INVALID,
            correction_instruction="Do not retry a local context error.",
        )


def test_plan_artifact_format_contract_is_the_parser_derived_contract() -> None:
    bundle = build_native_implementer_request(_spec())

    communicated = bundle.document["implementer_contract"][
        "plan_artifact_format_contract"
    ]
    parser_derived = plan_handoff.render_plan_artifact_format_contract()

    assert communicated == parser_derived
    assert "`### Slice N - title`" in communicated
    assert "ids start at 1, remain contiguous" in communicated
    assert "`**Exakter Änderungspfad**`" in communicated
    assert "path as a bullet with the path enclosed in backticks" in communicated
    assert "`**\u0041kzeptanzkriterien**`" in communicated
    assert "`(?:\\d+|[A-Za-z])[.)]`" in communicated
    assert "free-text paragraph without a list marker is invalid" in communicated

    without_contract = copy.deepcopy(bundle.document)
    without_contract["implementer_contract"].pop("plan_artifact_format_contract")
    with pytest.raises(NativeImplementerRequestError, match="schema validation failed"):
        validate_native_implementer_request_document(without_contract)


def test_non_artifact_request_does_not_communicate_plan_artifact_format() -> None:
    spec = _spec()
    context = replace(
        spec.context,
        request_kind=NativeImplementerRequestKind.IMPLEMENTATION,
        contract=replace(
            spec.context.contract,
            readiness_marker=ReadinessMarker.IMPLEMENTATION,
            require_slice_plan=False,
            plan_artifact_path=None,
        ),
    )

    bundle = build_native_implementer_request(replace(spec, context=context))

    assert "plan_artifact_format_contract" not in bundle.document["implementer_contract"]


def test_active_codex_request_bytes_match_the_cutover_baseline() -> None:
    bundle = build_native_implementer_request(_spec())

    # Slice 8b wire cut: reversing only the bound names recreates the old bytes.
    prior = prior_role_wire_document(bundle.document)
    assert hashlib.sha256(json.dumps(prior, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == (
        "273a857534275427ceb50300b5bbf92b98e913d83da17868d7e66722d39c3be6"
    )
    assert hashlib.sha256(bundle.canonical_json.encode("utf-8")).hexdigest() == (
        "4cde1043b4abcb1e20d24a28195a7fbbfde2229866853ad8b09a1dc07b2c8ae4"
    )
    assert hashlib.sha256(
        bundle.provider_response_schema_json.encode("utf-8")
    ).hexdigest() == (
        "8bf937f092c712f02515ebd9c8edc905c9bb64b893a619b485d057d88fb687e4"
    )


def test_capability_profile_selects_writer_and_remains_request_bound() -> None:
    from native_provider_schema import (
        ANTHROPIC_PROVIDER, OPENAI_PROVIDER, NativeProviderSchemaError,
    )

    first = build_native_implementer_request(_spec(), profile=OPENAI_PROVIDER)
    second = build_native_implementer_request(_spec(), profile=ANTHROPIC_PROVIDER)
    assert first.provider_response_schema != second.provider_response_schema
    assert first.bound_context.request_id != second.bound_context.request_id
    for bundle in (first, second):
        assert bundle.document["response_contract"]["schema_sha256"] == hashlib.sha256(
            bundle.provider_response_schema_json.encode()
        ).hexdigest()
    with pytest.raises(NativeImplementerRequestError, match="differs from bound context"):
        replace(first, capability_profile=ANTHROPIC_PROVIDER)
    with pytest.raises(NativeProviderSchemaError, match="no capability entry"):
        build_native_implementer_request(_spec(), profile="unknown")


def test_plan_request_tells_provider_the_scope_path_order_contract() -> None:
    bundle = build_native_implementer_request(_spec())
    scope_paths = bundle.provider_response_schema["$defs"]["planned_slice"][
        "properties"
    ]["scope_paths"]

    assert scope_paths["description"] == (
        "Each scope_paths array must be non-empty, contain no duplicates, and "
        "list paths in ascending lexicographic order."
    )
    assert "uniqueItems" not in scope_paths
    assert bundle.document["response_contract"]["schema_sha256"] == (
        __import__("hashlib").sha256(
            bundle.provider_response_schema_json.encode("utf-8")
        ).hexdigest()
    )


def test_b69_request_anchor_change_is_description_digest_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_bundle = build_native_implementer_request(_spec())
    b68_schema = copy.deepcopy(load_native_implementer_schema())
    definitions = b68_schema["$defs"]
    definitions["safe_text"].pop("description")
    definitions["safe_path"].pop("description")
    definitions["plan_result"]["properties"]["slice_plan"].pop("description")
    for result_name in (
        "plan_result",
        "implementation_result",
        "correction_result",
    ):
        definitions[result_name]["properties"]["finding_dispositions"].pop(
            "description"
        )
    for result_name in ("implementation_result", "correction_result"):
        definitions[result_name]["properties"]["test_files"].pop("description")
    definitions["stop_result"]["properties"]["remediation_paths"].pop(
        "description"
    )

    with monkeypatch.context() as patch:
        patch.setattr(
            native_implementer_contract,
            "load_native_implementer_schema",
            lambda: copy.deepcopy(b68_schema),
        )
        b68_bundle = build_native_implementer_request(_spec())

    current = copy.deepcopy(current_bundle.document)
    b68 = copy.deepcopy(b68_bundle.document)
    assert current["request_id"] != b68["request_id"]
    assert current["response_contract"]["schema_sha256"] != (
        b68["response_contract"]["schema_sha256"]
    )
    assert current["response_contract"]["schema_version"] == (
        b68["response_contract"]["schema_version"]
    )
    b68["request_id"] = current["request_id"]
    b68["response_contract"]["schema_sha256"] = current["response_contract"][
        "schema_sha256"
    ]
    assert b68 == current


def test_b70_request_anchor_change_is_description_digest_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_bundle = build_native_implementer_request(_spec())
    b69_schema = copy.deepcopy(load_native_implementer_schema())
    definitions = b69_schema["$defs"]
    for result_name in (
        "plan_result",
        "implementation_result",
        "correction_result",
    ):
        definitions[result_name]["properties"]["ready"].pop("description")
    stop = definitions["stop_result"]
    stop.pop("description")
    stop["properties"]["rule_id"] = {"$ref": "#/$defs/safe_text"}
    stop["properties"]["rationale"] = {"$ref": "#/$defs/safe_text"}

    with monkeypatch.context() as patch:
        patch.setattr(
            native_implementer_contract,
            "load_native_implementer_schema",
            lambda: copy.deepcopy(b69_schema),
        )
        b69_bundle = build_native_implementer_request(_spec())

    current = copy.deepcopy(current_bundle.document)
    b69 = copy.deepcopy(b69_bundle.document)
    assert current["request_id"] != b69["request_id"]
    assert current["response_contract"]["schema_sha256"] != (
        b69["response_contract"]["schema_sha256"]
    )
    assert current["response_contract"]["schema_version"] == (
        b69["response_contract"]["schema_version"]
    )
    b69["request_id"] = current["request_id"]
    b69["response_contract"]["schema_sha256"] = current["response_contract"][
        "schema_sha256"
    ]
    assert b69 == current


def test_b71_request_anchor_change_is_schema_digest_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_bundle = build_native_implementer_request(_spec())
    b70_schema = copy.deepcopy(load_native_implementer_schema())
    definitions = b70_schema["$defs"]
    stop_properties = definitions["stop_result"]["properties"]
    rule_description = stop_properties["rule_id"]["description"]
    rationale_description = stop_properties["rationale"]["description"]
    stop_properties["rule_id"] = {
        "$ref": "#/$defs/safe_text",
        "description": rule_description,
    }
    stop_properties["rationale"] = {
        "$ref": "#/$defs/safe_text",
        "description": rationale_description,
    }

    with monkeypatch.context() as patch:
        patch.setattr(
            native_implementer_contract,
            "load_native_implementer_schema",
            lambda: copy.deepcopy(b70_schema),
        )
        patch.setattr(
            native_implementer_contract,
            "assert_projected_provider_schema",
            lambda *_args, **_kwargs: None,
        )
        b70_bundle = build_native_implementer_request(_spec())

    current = copy.deepcopy(current_bundle.document)
    b70 = copy.deepcopy(b70_bundle.document)
    assert current["request_id"] != b70["request_id"]
    assert current["response_contract"]["schema_sha256"] != (
        b70["response_contract"]["schema_sha256"]
    )
    assert current["response_contract"]["schema_version"] == (
        b70["response_contract"]["schema_version"]
    )
    b70["request_id"] = current["request_id"]
    b70["response_contract"]["schema_sha256"] = current["response_contract"][
        "schema_sha256"
    ]
    assert b70 == current


def test_plan_request_explains_when_to_return_stop_result() -> None:
    bundle = build_native_implementer_request(_spec())
    definitions = bundle.provider_response_schema["$defs"]

    assert definitions["stop_result"]["description"] == (
        "Return stop_result instead of a task result when the current step "
        "cannot be performed; rule_id and rationale document the blocker."
    )
    assert "return stop_result instead" in definitions["plan_result"]["properties"][
        "ready"
    ]["description"]


def test_request_kinds_bind_distinct_writer_schema_digests() -> None:
    base = _spec()
    readiness = {
        NativeImplementerRequestKind.PLAN: ReadinessMarker.PLAN,
        NativeImplementerRequestKind.IMPLEMENTATION: ReadinessMarker.IMPLEMENTATION,
        NativeImplementerRequestKind.CORRECTION: ReadinessMarker.IMPLEMENTATION,
    }
    digests: set[str] = set()
    for kind in NativeImplementerRequestKind:
        contract = ImplementerStepContract(
            name=f"native-{kind.value}",
            readiness_marker=readiness[kind],
            slice_id="01",
            round_number=1,
            require_test_files_record=kind in {
                NativeImplementerRequestKind.IMPLEMENTATION,
                NativeImplementerRequestKind.CORRECTION,
            },
            require_slice_plan=kind is NativeImplementerRequestKind.PLAN,
            plan_artifact_path=(
                "docs/internal/plan.md"
                if kind is NativeImplementerRequestKind.PLAN
                else None
            ),
        )
        context = NativeImplementerContext(
            run_id=base.context.run_id,
            work_unit_id=base.context.work_unit_id,
            operation=f"codex_{kind.value}",
            current_fingerprint=base.context.current_fingerprint,
            request_kind=kind,
            contract=contract,
        )
        bundle = build_native_implementer_request(
            replace(base, context=context)
        )
        digests.add(bundle.document["response_contract"]["schema_sha256"])
    assert len(digests) == 3


def test_bundle_rejects_schema_bytes_not_derived_from_bound_context() -> None:
    bundle = build_native_implementer_request(_spec())
    schema = json.loads(bundle.provider_response_schema_json)
    schema["title"] = "tampered"
    tampered = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    with pytest.raises(NativeImplementerRequestError, match="differs from bound context"):
        replace(bundle, provider_response_schema_json=tampered)


def test_bundle_rejects_context_digest_and_evidence_misbinding() -> None:
    bundle = build_native_implementer_request(_spec())
    changed_context = replace(bundle.bound_context.context, run_id="run-tampered")
    with pytest.raises(NativeImplementerRequestError, match="context projection"):
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
    with pytest.raises(NativeImplementerRequestError, match="bound digest"):
        NativeImplementerRequestBundle(
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
    inline["request_id"] = "native-implementer-request-" + digest
    with pytest.raises(NativeImplementerRequestError, match="metadata differs"):
        NativeImplementerRequestBundle(
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
        "R-01",
        FindingClass.BLOCKER,
        FindingStatus.CLOSED,
        "Closed finding",
        "Already fixed",
        FindingOrigin("01", 1, AgentRole.REVIEWER),
        status_rationale="Verified closed",
    )
    changed_closed = replace(closed, summary="Mirror-only tampering")
    first = build_native_implementer_request(
        replace(base, context=replace(base.context, previous_findings=(closed,)))
    )
    second = build_native_implementer_request(
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
    large = NativeImplementerEvidenceInput(
        "e01_large",
        "diff",
        "x" * 1000,
        "src/workflow.py",
    )
    bundle = build_native_implementer_request(
        NativeImplementerRequestSpec(
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
            NativeImplementerEvidenceInput("e01_first", "diff", "same"),
            NativeImplementerEvidenceInput("e02_second", "diff", "same"),
        ),
        (
            NativeImplementerEvidenceInput("e01_first", "diff", "first", "src/a.py"),
            NativeImplementerEvidenceInput("e02_second", "diff", "second", "src/a.py"),
        ),
    ),
)
def test_request_rejects_duplicate_evidence_content_or_source_path(
    evidence: tuple[NativeImplementerEvidenceInput, ...],
) -> None:
    with pytest.raises(NativeImplementerRequestError, match="unique"):
        replace(_spec(), evidence=evidence)


def test_content_ref_assets_reject_missing_extra_duplicate_swapped_and_changed() -> None:
    spec = _spec()
    evidence = (
        NativeImplementerEvidenceInput("e01_first", "diff", "a" * 100),
        NativeImplementerEvidenceInput("e02_second", "diff", "b" * 100),
    )
    bundle = build_native_implementer_request(
        replace(spec, evidence=evidence), inline_evidence_chars=10
    )
    first, second = bundle.evidence_assets

    with pytest.raises(NativeImplementerRequestError, match="differ from content references"):
        replace(bundle, evidence_assets=(first,))
    with pytest.raises(NativeImplementerRequestError, match="paths must be unique"):
        replace(bundle, evidence_assets=(first, second, second))

    extra_bundle = build_native_implementer_request(
        replace(
            spec,
            evidence=(NativeImplementerEvidenceInput("e03_extra", "diff", "c" * 100),),
        ),
        inline_evidence_chars=10,
    )
    with pytest.raises(NativeImplementerRequestError, match="differ from content references"):
        replace(bundle, evidence_assets=(*bundle.evidence_assets, *extra_bundle.evidence_assets))

    swapped = (replace(first, path=second.path), replace(second, path=first.path))
    with pytest.raises(NativeImplementerRequestError, match="differ from content references"):
        replace(bundle, evidence_assets=swapped)

    with pytest.raises(NativeImplementerRequestError, match="byte_count differs"):
        replace(first, content="changed")


def test_context_contract_and_evidence_changes_rebind_request() -> None:
    base = _spec()
    first = build_native_implementer_request(base)
    changed_context = NativeImplementerContext(
        run_id=base.context.run_id,
        work_unit_id=base.context.work_unit_id,
        operation=base.context.operation,
        current_fingerprint="c" * 64,
        request_kind=base.context.request_kind,
        contract=base.context.contract,
    )
    changed = build_native_implementer_request(
        NativeImplementerRequestSpec(
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
    with pytest.raises(NativeImplementerRequestError):
        _spec(assignment=value)


def test_request_rejects_unsorted_or_unsafe_paths() -> None:
    spec = _spec()
    with pytest.raises(NativeImplementerRequestError):
        NativeImplementerRequestSpec(
            context=spec.context,
            target_branch=spec.target_branch,
            base_commit=spec.base_commit,
            authorized_paths=("src/z.py", "src/a.py"),
            assignment=spec.assignment,
            work_context=spec.work_context,
            evidence=spec.evidence,
        )
    with pytest.raises(NativeImplementerRequestError):
        NativeImplementerEvidenceInput("e01_bad", "diff", "data", "../secret")
