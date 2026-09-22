from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

import agent_runtime
from agent_runtime import classify_agent_failure
from contracts import (
    AgentRole,
    ApprovalMarker,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from native_review_contract import (
    NativeReviewContext,
    native_review_provider_response_schema,
)
from schema_validation import SchemaMismatch, check_schema, validate_schema_document


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    ROOT / "tests/fixtures/structured_output_pressure/manifest-v1.json"
)
FINGERPRINT = "a" * 64
WRITER_FORMS = ("plan", "initial_slice", "convergence", "final_review")


def _manifest() -> dict[str, object]:
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _attestation() -> ValidationAttestation:
    command = "python3 -m pytest tests/ -v"
    return ValidationAttestation(
        "structured-output-pressure",
        FINGERPRINT,
        (command,),
        (ValidationRecord(ValidationStatus.PASS, command, 0, "passed"),),
        hashlib.sha256(b"passed").hexdigest(),
        "1 passed",
        (ValidationCommandSpec(argv=("python3", "-m", "pytest", "tests/", "-v")),),
    )


def _finding() -> FindingRecord:
    return FindingRecord(
        "C-01",
        FindingClass.BLOCKER,
        FindingStatus.OPEN,
        "Bound convergence finding.",
        "The focused regression passes.",
        FindingOrigin("01", 1, AgentRole.CLAUDE),
    )


def _observation(finding_id: str = "C-02") -> FindingRecord:
    return FindingRecord(
        finding_id,
        FindingClass.OBSERVATION,
        FindingStatus.OPEN,
        "Bound open observation.",
        "The focused regression passes.",
        FindingOrigin("01", 1, AgentRole.CLAUDE),
    )


def _context(form: str) -> NativeReviewContext:
    marker = {
        "plan": ApprovalMarker.PLAN,
        "initial_slice": ApprovalMarker.SLICE,
        "convergence": ApprovalMarker.SLICE,
        "final_review": ApprovalMarker.FINAL_REVIEW,
    }[form]
    return NativeReviewContext(
        run_id="structured-output-pressure",
        work_unit_id=f"work-{form}",
        operation={
            ApprovalMarker.PLAN: "claude_plan_review",
            ApprovalMarker.SLICE: "claude_slice_review",
            ApprovalMarker.FINAL_REVIEW: "claude_final_review",
        }[marker],
        diff_fingerprint=FINGERPRINT,
        reviewer=AgentRole.CLAUDE,
        approval_marker=marker,
        slice_id=(
            "discovery"
            if marker is ApprovalMarker.FINAL_REVIEW
            else "01"
        ),
        round_number=2 if form == "convergence" else 1,
        previous_findings=(_finding(),) if form == "convergence" else (),
        validation_attestation=_attestation(),
        test_files=(),
        test_changes_approved=True,
        allow_new_observations=form != "convergence",
        anchor_origin="docs/internal/approved-plan.md",
    )


def _schema_metrics(value: object) -> tuple[int, int, int, int]:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))

    def depth(node: object) -> int:
        if isinstance(node, dict):
            return 1 + max((depth(child) for child in node.values()), default=0)
        if isinstance(node, list):
            return 1 + max((depth(child) for child in node), default=0)
        return 1

    def compositions(node: object) -> int:
        if isinstance(node, dict):
            return sum(key in node for key in ("allOf", "anyOf", "oneOf")) + sum(
                compositions(child) for child in node.values()
            )
        if isinstance(node, list):
            return sum(compositions(child) for child in node)
        return 0

    definitions = value.get("$defs", {}) if isinstance(value, dict) else {}
    return len(canonical.encode("utf-8")), depth(value), compositions(value), len(definitions)


def _context_variants() -> dict[str, NativeReviewContext]:
    """Conservatively cover every schema-shaping context dimension.

    The production writer branches on approval marker, round, the observation
    policy, and the reviewer's own open ledger.  The Cartesian product is a
    deliberate superset of currently reachable work-unit states, so a newly
    conditional maxLength cannot hide behind one representative form.
    """
    bases = {
        "plan": _context("plan"),
        "slice": _context("initial_slice"),
        "final_review": _context("final_review"),
    }
    ledgers = {
        "empty": (),
        "blocker": (_finding(),),
        "observation": (_observation(),),
        "mixed": (_finding(), _observation()),
    }
    return {
        f"{marker}-round-{round_number}-observations-{int(allow)}-{ledger_name}": replace(
            base,
            round_number=round_number,
            allow_new_observations=allow,
            previous_findings=ledger,
        )
        for marker, base in bases.items()
        for round_number in (1, 2)
        for allow in (False, True)
        for ledger_name, ledger in ledgers.items()
    }


def _writer_schemas() -> dict[str, dict[str, object]]:
    return {
        name: native_review_provider_response_schema(context)
        for name, context in _context_variants().items()
    }


def _context_scope(context: NativeReviewContext) -> set[str]:
    marker = context.approval_marker
    own_open = any(
        finding.origin.reporter is AgentRole.CLAUDE
        and finding.status is FindingStatus.OPEN
        for finding in context.previous_findings
    )
    observations_allowed = (
        marker is not ApprovalMarker.FINAL_REVIEW
        and context.allow_new_observations
    )
    slice_initial = (
        marker is ApprovalMarker.SLICE
        and context.round_number == 1
        and context.allow_new_observations
    )
    scopes = {"all"}
    if marker is not ApprovalMarker.FINAL_REVIEW:
        scopes.add("standard")
    if marker is ApprovalMarker.PLAN:
        scopes.add("plan")
        if own_open:
            scopes.add("plan_open")
    elif marker is ApprovalMarker.SLICE:
        scopes.add("slice_initial" if slice_initial else "slice_convergence")
        if own_open or context.allow_new_observations:
            scopes.add(
                "slice_initial_open" if slice_initial else "slice_convergence_open"
            )
    else:
        scopes.add("final_review")
        if own_open:
            scopes.add("final_review_open")
        else:
            scopes.add("final_review_empty")
    if observations_allowed and own_open:
        scopes.add("observations_allowed_open")
    return scopes


def _bounded_sites(
    schemas: dict[str, dict[str, object]],
) -> set[tuple[str, str, int]]:
    sites: set[tuple[str, str, int]] = set()

    def walk(form: str, node: object, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            if "maxLength" in node:
                sites.add((form, "/".join(path), node["maxLength"]))
            for key, child in node.items():
                walk(form, child, (*path, key))
        elif isinstance(node, list):
            for child in node:
                walk(form, child, (*path, "*"))

    for form, schema in schemas.items():
        walk(form, schema, ())
    return sites


def _manifest_sites(manifest: dict[str, object]) -> set[tuple[str, str, int]]:
    contexts = _context_variants()
    return {
        (name, item["pointer"], item["limit"])
        for item in manifest["schema_inventory"]
        for name, context in contexts.items()
        if item["context_scope"] in _context_scope(context)
    }


def _assert_schema_inventory(
    manifest: dict[str, object], schemas: dict[str, dict[str, object]]
) -> None:
    actual = _bounded_sites(schemas)
    expected = _manifest_sites(manifest)
    unexpected = sorted(actual - expected)
    missing = sorted(expected - actual)
    assert not unexpected, f"unexpected bounded writer sites: {unexpected}"
    assert not missing, f"missing bounded writer sites: {missing}"


def _bounded_fragments(
    schemas: dict[str, dict[str, object]],
) -> dict[tuple[str, str, int], list[dict[str, object]]]:
    fragments: dict[tuple[str, str, int], list[dict[str, object]]] = {}

    def walk(name: str, node: object, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            if "maxLength" in node:
                key = (name, "/".join(path), node["maxLength"])
                fragments.setdefault(key, []).append(node)
            for key, child in node.items():
                walk(name, child, (*path, key))
        elif isinstance(node, list):
            for child in node:
                walk(name, child, (*path, "*"))

    for name, schema in schemas.items():
        walk(name, schema, ())
    return fragments


def test_manifest_snapshot_rederives_historical_pressure_without_archive_read() -> None:
    manifest = _manifest()
    snapshot = manifest["historical_snapshot"]
    assert snapshot["binding_class"] == "historical_schema_only"
    assert snapshot["snapshot_kind"] == "field-lengths-and-content-digests"
    assert snapshot["source_total_rows"] == 14
    assert snapshot["source_claude_rows"] == len(snapshot["source_fixture_ids"]) == 6
    assert len(set(snapshot["source_fixture_ids"])) == 6
    assert len(snapshot["source_corpus_sha256"]) == 64
    archive_token = "docs/internal/" + "archive/"
    assert archive_token not in MANIFEST_PATH.read_text(encoding="utf-8")

    fields = manifest["fields"]
    assert isinstance(fields, list)
    for item in fields:
        lengths = item["sample_lengths"]
        assert all(0 < length <= item["limit"] for length in lengths)
        assert len(item["sample_content_sha256"]) == 64


def test_manifest_is_bidirectionally_closed_over_all_bounded_writer_sites() -> None:
    manifest = _manifest()
    schemas = _writer_schemas()
    assert len(schemas) == 48
    _assert_schema_inventory(manifest, schemas)

    field_limits = {item["path"]: item["limit"] for item in manifest["fields"]}
    assert len(field_limits) == len(manifest["fields"])
    inventory_fields = {item["field"] for item in manifest["schema_inventory"]}
    assert inventory_fields == set(field_limits)
    expanded_inventory = list(_manifest_sites(manifest))
    assert len(expanded_inventory) == len(set(expanded_inventory))
    for item in manifest["schema_inventory"]:
        assert field_limits[item["field"]] == item["limit"]


def test_schema_inventory_fails_closed_on_new_or_removed_bounded_site() -> None:
    manifest = _manifest()
    schemas = _writer_schemas()
    context_name = "plan-round-2-observations-1-mixed"
    schemas[context_name]["$defs"]["finding"]["properties"]["finding_id"][
        "maxLength"
    ] = 64
    with pytest.raises(AssertionError, match="unexpected bounded writer sites"):
        _assert_schema_inventory(manifest, schemas)

    schemas = _writer_schemas()
    del schemas[context_name]["$defs"]["prose_acceptance"]["properties"][
        "text"
    ]["maxLength"]
    with pytest.raises(AssertionError, match="missing bounded writer sites"):
        _assert_schema_inventory(manifest, schemas)


def test_open_observation_context_binds_exact_reclassification_length_edge() -> None:
    context_name = "slice-round-2-observations-1-observation"
    schemas = _writer_schemas()
    pointer = "$defs/bound_approved_reclassification/properties/rationale"
    fragments = _bounded_fragments(schemas)[(context_name, pointer, 3000)]
    assert len(fragments) == 1
    validate_schema_document("x" * 3000, fragments[0])
    with pytest.raises(SchemaMismatch, match="at most 3000"):
        validate_schema_document("x" * 3001, fragments[0])


@pytest.mark.parametrize("percentage", (50, 80, 95, 100, 101))
def test_every_bounded_claude_field_has_synthetic_boundary_evidence(
    percentage: int,
) -> None:
    manifest = _manifest()
    schemas = _writer_schemas()
    _assert_schema_inventory(manifest, schemas)
    for (context_name, pointer, limit), fragments in _bounded_fragments(
        schemas
    ).items():
        for fragment in fragments:
            assert fragment["maxLength"] == limit
            check_schema(fragment, location=f"<{context_name}:{pointer}>")
            length = (limit * percentage + 99) // 100
            value = "x" * length
            if percentage <= 100:
                validate_schema_document(value, fragment)
            else:
                with pytest.raises(SchemaMismatch, match="at most"):
                    validate_schema_document(value, fragment)


def test_all_request_specific_claude_writer_forms_have_deterministic_metrics() -> None:
    metrics: dict[str, tuple[int, int, int, int]] = {}
    digests: set[str] = set()
    for form in WRITER_FORMS:
        first = native_review_provider_response_schema(_context(form))
        second = native_review_provider_response_schema(_context(form))
        assert first == second
        canonical = json.dumps(first, sort_keys=True, separators=(",", ":"))
        digests.add(hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        metrics[form] = _schema_metrics(first)

    assert len(digests) == 4
    assert set(metrics) == {
        "plan",
        "initial_slice",
        "convergence",
        "final_review",
    }
    for byte_count, depth, composition_count, definition_count in metrics.values():
        assert byte_count > 7_000
        assert depth >= 10
        assert composition_count >= 5
        assert definition_count >= 15


def test_exact_retry_subtype_is_output_but_near_misses_remain_fail_closed() -> None:
    manifest = _manifest()
    diagnostic = manifest["diagnostic"]
    assert isinstance(diagnostic, dict)
    exact = diagnostic["exact_envelope"]
    assert diagnostic["classification"] == "structured_output_failure"
    assert diagnostic["persists_rejected_model_content"] is False

    for error_type in (agent_runtime.AgentProcessError, agent_runtime.AgentOutputError):
        failure = classify_agent_failure(
            "claude",
            error_type(
                "native Claude error",
                exit_code=1,
                provider_data={**exact, "structured_output": {"model": "discard"}},
            ),
            invocation_id="structured-output-pressure-exact",
        )
        assert failure.kind.value == "output"
        assert failure.provider_data == exact

    near_misses = (
        ("codex", {**exact, "subtype": "different_error"}),
        ("claude", {"type": "error", "subtype": "different_error"}),
        ("claude", {**exact, "subtype": "error_max_structured_output_retry"}),
        ("claude", {"type": "result"}),
    )
    for provider, envelope in near_misses:
        failure = classify_agent_failure(
            provider,
            agent_runtime.AgentProcessError(
                "native provider error", exit_code=1, provider_data=envelope
            ),
            invocation_id="structured-output-pressure-near-miss",
        )
        assert failure.kind.value == "process"


def test_live_canary_is_documented_but_not_enabled_in_the_test_suite() -> None:
    canary = _manifest()["optional_live_canary"]
    assert isinstance(canary, dict)
    assert canary["enabled_in_tests"] is False
    assert canary["required_evidence"] == [
        "request_id",
        "writer_schema_sha256",
        "response_sha256",
        "provider_diagnostic",
    ]
