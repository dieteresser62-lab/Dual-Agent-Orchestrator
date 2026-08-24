from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

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
from native_codex_contract import validate_native_codex_document
from native_review_contract import (
    BoundNativeReviewContext,
    NativeReviewContext,
    native_review_provider_response_schema,
    parse_bound_native_contract_result,
    validate_native_review_document,
)
from native_review_request import validate_native_review_request_document
from schema_validation import validate_schema_document


ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = ROOT / "tests" / "fixtures" / "native-contract-corpus"


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_corpus() -> tuple[dict[str, Any], dict[str, Mapping[str, Any]]]:
    manifest = json.loads((CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    rows: dict[str, Mapping[str, Any]] = {}
    for line in (CORPUS_ROOT / "corpus.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        assert row["fixture_id"] not in rows
        rows[row["fixture_id"]] = row
    return manifest, rows


def _archive_native_response_paths() -> set[str]:
    paths: set[str] = set()
    archive_root = ROOT / "docs" / "internal" / "archive"
    for path in archive_root.rglob("*.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(document, dict):
            continue
        if not str(document.get("schema_version", "")).startswith("native-agent-"):
            continue
        if not document.get("result_type"):
            continue
        paths.add(path.relative_to(ROOT).as_posix())
    return paths


def _finding(document: Mapping[str, Any]) -> FindingRecord:
    origin = document["origin"]
    return FindingRecord(
        finding_id=document["finding_id"],
        finding_class=FindingClass(document["finding_class"]),
        status=FindingStatus(document["status"]),
        summary=document["summary"],
        acceptance_test=document["acceptance_test"],
        origin=FindingOrigin(
            origin["slice_id"],
            origin["round_number"],
            AgentRole(origin["reporter"]),
        ),
        responses=tuple(
            FindingResponse(
                FindingResponseDecision(item["decision"]), item["rationale"]
            )
            for item in document.get("responses", ())
        ),
        status_rationale=document.get("status_rationale"),
        class_history=tuple(
            FindingClass(item) for item in document.get("class_history", ())
        ),
    )


def _attestation(document: Mapping[str, Any] | None) -> ValidationAttestation | None:
    if document is None:
        return None
    return ValidationAttestation(
        attestation_id=document["attestation_id"],
        diff_fingerprint=document["diff_fingerprint"],
        expected_commands=tuple(document["expected_commands"]),
        records=tuple(
            ValidationRecord(
                ValidationStatus(item["status"]),
                item["command"],
                item["exit_code"],
                item.get("output", ""),
            )
            for item in document["records"]
        ),
        output_digest=document["output_digest"],
        summary=document["summary"],
        command_specs=tuple(
            ValidationCommandSpec(
                argv=tuple(item.get("argv", ())),
                legacy_shell=item.get("legacy_shell"),
            )
            for item in document["command_specs"]
        ),
    )


def _bound_review_context(request: Mapping[str, Any]) -> BoundNativeReviewContext:
    validate_native_review_request_document(request)
    binding = {key: value for key, value in request.items() if key != "request_id"}
    digest = _sha256(_canonical(binding))
    assert request["request_id"] == f"native-review-request-{digest}"
    contract = request["review_contract"]
    context = NativeReviewContext(
        run_id=request["run_id"],
        work_unit_id=request["work_unit_id"],
        operation=request["operation"],
        diff_fingerprint=request["current_fingerprint"],
        reviewer=AgentRole(request["reviewer"]),
        approval_marker=ApprovalMarker(contract["approval_marker"]),
        slice_id=contract["slice_id"],
        round_number=contract["round_number"],
        previous_findings=tuple(
            _finding(item) for item in contract["previous_findings"]
        ),
        validation_attestation=_attestation(contract["validation_attestation"]),
        test_files=tuple(contract["test_files"]),
        test_changes_approved=contract["test_changes_approved"],
        allow_new_observations=contract["allow_new_observations"],
        anchor_origin=contract["anchor_origin"],
        validation_command_prefixes=tuple(
            tuple(item) for item in contract["validation_command_prefixes"]
        ),
    )
    return BoundNativeReviewContext(context, request["request_id"], digest)


def test_native_contract_corpus_manifest_and_digests_are_complete() -> None:
    manifest, rows = _load_corpus()
    inventory = manifest["inventory"]
    fixtures = manifest["fixtures"]

    assert manifest["schema_version"] == "native-contract-corpus-v1"
    assert inventory["canonical_requests_found"] == 12
    assert inventory["request_bound_pairs_adopted"] == 3
    assert inventory["request_bound_pairs_found"] == 3
    assert inventory["transport_error_envelopes_catalogued"] == 3
    assert len(fixtures) == inventory["native_responses_adopted"]
    archive_paths = _archive_native_response_paths()
    manifested_archive_paths = {
        item["source_path"]
        for item in fixtures
        if item["source_path"].startswith("docs/internal/archive/")
    }
    artifact_fixture_count = sum(
        item["source_path"].startswith(".orchestrator/") for item in fixtures
    )
    assert manifested_archive_paths == archive_paths
    assert inventory["native_responses_found"] == (
        artifact_fixture_count + len(archive_paths)
    )
    assert set(rows) == {item["fixture_id"] for item in fixtures}
    assert sum(item["binding"] == "request_bound" for item in fixtures) == 3
    for item in fixtures:
        row = rows[item["fixture_id"]]
        assert item["response_sha256"] == _sha256(row["response_text"])
        assert row["source_path"] == item["source_path"]
        assert row["provider"] == item["provider"]
        if item["source_path"].startswith("docs/internal/archive/"):
            assert (ROOT / item["source_path"]).read_text(
                encoding="utf-8"
            ) == row["response_text"]


def test_native_contract_corpus_reader_writer_and_domain_results() -> None:
    manifest, rows = _load_corpus()
    for item in manifest["fixtures"]:
        row = rows[item["fixture_id"]]
        response = json.loads(row["response_text"])
        if row["provider"] == "codex":
            validate_native_codex_document(response)
        else:
            validate_native_review_document(response)

        if item["binding"] == "schema_only":
            assert "request_document" not in row
            continue

        request = row["request_document"]
        bound = _bound_review_context(request)
        assert bound.request_id == response["request_id"]
        writer = native_review_provider_response_schema(bound.context)
        assert item["writer_schema_sha256"] == _sha256(_canonical(writer))
        validate_schema_document({"result": response}, writer)
        result = parse_bound_native_contract_result(response, bound)
        assert result.approval is item["expected_approved"]


def _validate_writer_form_evidence(
    manifest: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]
) -> None:
    writer_forms = manifest["writer_forms"]
    fixtures = {item["fixture_id"]: item for item in manifest["fixtures"]}
    expected = {
        ("codex", "plan"),
        ("codex", "implementation"),
        ("codex", "correction"),
        ("codex", "final_report"),
        ("claude", "plan"),
        ("claude", "initial_slice"),
        ("claude", "convergence"),
        ("claude", "final"),
    }
    assert len(writer_forms) == 8
    assert {(item["provider"], item["writer_form"]) for item in writer_forms} == expected
    for item in writer_forms:
        assert len(item["writer_schema_sha256"]) == 64
        assert item["representative_context"]
        evidence = item["evidence"]
        assert evidence["kind"] in {"request_bound", "live_canary"}
        if evidence["kind"] == "request_bound":
            assert evidence["fixture_id"] in rows
            fixture = fixtures[evidence["fixture_id"]]
            assert fixture["binding"] == "request_bound"
            assert fixture["writer_schema_sha256"] == item["writer_schema_sha256"]
        else:
            assert evidence["status"] == "passed"
            assert len(evidence["request_id"].removeprefix("native-")) > 64
            assert len(evidence["response_sha256"]) == 64


def test_writer_form_manifest_has_exactly_eight_closed_evidence_rows() -> None:
    manifest, rows = _load_corpus()
    _validate_writer_form_evidence(manifest, rows)
    writer_forms = manifest["writer_forms"]
    assert sum(
        item["evidence"]["kind"] == "live_canary" for item in writer_forms
    ) == 7
    assert sum(
        item["evidence"]["kind"] == "request_bound" for item in writer_forms
    ) == 1
    initial = next(
        item
        for item in writer_forms
        if item["provider"] == "claude"
        and item["writer_form"] == "initial_slice"
    )
    convergence = next(
        item
        for item in writer_forms
        if item["provider"] == "claude" and item["writer_form"] == "convergence"
    )
    assert initial["evidence"]["kind"] == "request_bound"
    assert convergence["evidence"]["kind"] == "live_canary"
    assert "status_changes.items.oneOf" in convergence["representative_context"]

    schema_only = next(
        item for item in manifest["fixtures"] if item["binding"] == "schema_only"
    )
    wrong_binding = copy.deepcopy(manifest)
    request_bound_form = next(
        item
        for item in wrong_binding["writer_forms"]
        if item["evidence"]["kind"] == "request_bound"
    )
    request_bound_form["evidence"]["fixture_id"] = schema_only["fixture_id"]
    with pytest.raises(AssertionError):
        _validate_writer_form_evidence(wrong_binding, rows)

    wrong_digest = copy.deepcopy(manifest)
    request_bound_form = next(
        item
        for item in wrong_digest["writer_forms"]
        if item["evidence"]["kind"] == "request_bound"
    )
    request_bound_form["writer_schema_sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        _validate_writer_form_evidence(wrong_digest, rows)
