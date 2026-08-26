from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
    NativeCodexEvidenceInput,
    NativeCodexRequestBundle,
    NativeCodexRequestSpec,
    build_native_codex_request,
)
from provider_input_budget import (
    PreparedProviderInput,
    ProviderInputComponent,
    default_provider_input_budget_policy,
    measure_provider_input,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json"
)
LOCK = FIXTURE.with_name("native-only-cutover-baseline-v1.lock.json")
BASE_COMMIT = "bdb955d4ca56ca292dd8bcbd4d1e776a2acb7a1b"
FIXTURE_VERSION = "native-only-cutover-baseline-v1"
GENERATOR_VERSION = "provider-input-baseline-v1"
OPERATIONS = (
    "codex_plan",
    "codex_plan_revision",
    "codex_implementation",
    "codex_correction",
    "codex_final_review",
    "codex_final_correction",
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _synthetic_inputs() -> dict[str, object]:
    return {
        "approved_plan": "APPROVED-PLAN:" + "P" * 24_050,
        "assignment": "Measure the native-only cutover input without a provider.",
        "authorized_paths": ["src/runtime.py", "tests/test_runtime.py"],
        "generator_version": GENERATOR_VERSION,
        "workflow_prompt_prefix": "WORKFLOW-PROMPT:",
        "workflow_prompt_repeat": 24_050,
        "work_context": "Use the complete approved plan and current workflow evidence.",
    }


def _bundle(
    operation: str,
    *,
    omitted_evidence_ids: frozenset[str] = frozenset(),
) -> NativeCodexRequestBundle:
    plan_step = operation in {"codex_plan", "codex_plan_revision"}
    final_step = operation in {"codex_final_review", "codex_final_correction"}
    correction = operation in {
        "codex_plan_revision",
        "codex_correction",
        "codex_final_correction",
    }
    request_kind = (
        NativeCodexRequestKind.PLAN
        if plan_step
        else NativeCodexRequestKind.FINAL_REPORT
        if final_step
        else NativeCodexRequestKind.CORRECTION
        if correction
        else NativeCodexRequestKind.IMPLEMENTATION
    )
    marker = (
        ReadinessMarker.PLAN
        if plan_step
        else ReadinessMarker.FINAL_REPORT
        if final_step
        else ReadinessMarker.IMPLEMENTATION
    )
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="Synthetic bound finding",
        acceptance_test="The disposition remains request-bound.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    fingerprint = hashlib.sha256(f"fingerprint:{operation}".encode()).hexdigest()
    contract = CodexStepContract(
        name=f"baseline-{operation}",
        readiness_marker=marker,
        slice_id="FINAL" if final_step else "01",
        round_number=2 if correction else 1,
        require_test_files_record=not plan_step and not final_step,
        require_slice_plan=plan_step,
        plan_artifact_path=("docs/internal/plan.md" if plan_step else None),
        review_fingerprint=(fingerprint if final_step else None),
    )
    context = NativeCodexContext(
        run_id="baseline-native-only-cutover",
        work_unit_id=str(OPERATIONS.index(operation) + 1),
        operation=operation,
        current_fingerprint=fingerprint,
        request_kind=request_kind,
        contract=contract,
        previous_findings=(finding,) if correction else (),
    )
    synthetic = _synthetic_inputs()
    evidence = [
        NativeCodexEvidenceInput(
            "workflow-prompt",
            "orchestrator_instruction",
            f"{synthetic['workflow_prompt_prefix']}{operation}:"
            + "W" * int(synthetic["workflow_prompt_repeat"]),
        )
    ]
    if not plan_step:
        evidence.append(
            NativeCodexEvidenceInput(
                "approved-plan",
                "approved_plan",
                str(synthetic["approved_plan"]),
                source_path="docs/internal/plan.md",
            )
        )
    retained_evidence = tuple(
        item
        for item in sorted(evidence, key=lambda item: item.evidence_id)
        if item.evidence_id not in omitted_evidence_ids
    )
    return build_native_codex_request(
        NativeCodexRequestSpec(
            context=context,
            target_branch="feature/native-only-transport-and-efficiency",
            base_commit=BASE_COMMIT,
            authorized_paths=tuple(synthetic["authorized_paths"]),
            assignment=str(synthetic["assignment"]),
            work_context=str(synthetic["work_context"]),
            evidence=retained_evidence,
        )
    )


def _prepared(
    operation: str,
    *,
    omitted_evidence_ids: frozenset[str] = frozenset(),
) -> PreparedProviderInput:
    bundle = _bundle(operation, omitted_evidence_ids=omitted_evidence_ids)
    return PreparedProviderInput(
        command=("codex", "exec", "-"),
        stdin_text=bundle.canonical_json,
        components=(
            ProviderInputComponent("stdin_prompt", bundle.canonical_json),
            ProviderInputComponent(
                "response_schema", bundle.provider_response_schema_json
            ),
            *(
                ProviderInputComponent(
                    f"evidence_asset_{index:03d}", asset.content
                )
                for index, asset in enumerate(bundle.evidence_assets, start=1)
            ),
        ),
    )


def _evidence_bindings(bundle: NativeCodexRequestBundle) -> list[dict[str, object]]:
    manifest = [dict(item) for item in bundle.document["evidence_manifest"]]
    full_manifest_json = _canonical(manifest)
    component_by_path = {
        asset.path: f"evidence_asset_{index:03d}"
        for index, asset in enumerate(bundle.evidence_assets, start=1)
    }
    bindings: list[dict[str, object]] = []
    for entry in manifest:
        evidence_id = str(entry["evidence_id"])
        assert entry["delivery"] == "content_ref"
        component_name = component_by_path[str(entry["content_ref"])]
        entry_json = _canonical(entry)
        reduced_manifest_json = _canonical(
            [item for item in manifest if item["evidence_id"] != evidence_id]
        )
        bindings.append(
            {
                "component_name": component_name,
                "content_sha256": entry["sha256"],
                "evidence_id": evidence_id,
                "manifest_contribution_bytes": len(
                    full_manifest_json.encode("utf-8")
                )
                - len(reduced_manifest_json.encode("utf-8")),
                "manifest_contribution_chars": len(full_manifest_json)
                - len(reduced_manifest_json),
                "manifest_entry_bytes": len(entry_json.encode("utf-8")),
                "manifest_entry_chars": len(entry_json),
                "manifest_entry_sha256": hashlib.sha256(
                    entry_json.encode("utf-8")
                ).hexdigest(),
            }
        )
    return bindings


def build_baseline_document() -> dict[str, object]:
    synthetic = _synthetic_inputs()
    rows: list[dict[str, object]] = []
    for operation in OPERATIONS:
        bundle = _bundle(operation)
        prepared = _prepared(operation)
        fingerprint = hashlib.sha256(f"fingerprint:{operation}".encode()).hexdigest()
        measurement = measure_provider_input(
            prepared,
            provider="codex",
            role="codex",
            operation=operation,
            binding_fingerprint=fingerprint,
            policy=default_provider_input_budget_policy(),
        )
        components = [
            {
                "bytes": len(item.content.encode("utf-8")),
                "chars": len(item.content),
                "name": item.name,
                "sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
            }
            for item in sorted(prepared.components, key=lambda value: value.name)
        ]
        rows.append(
            {
                "components": components,
                "evidence_bindings": _evidence_bindings(bundle),
                "input_digest": measurement.input_digest,
                "operation": operation,
                "role": "codex",
                "total_bytes": measurement.total_bytes,
                "total_chars": measurement.total_chars,
            }
        )
    return {
        "base_commit": BASE_COMMIT,
        "fixture_version": FIXTURE_VERSION,
        "generator_version": GENERATOR_VERSION,
        "operations": rows,
        "synthetic_input_digest": hashlib.sha256(
            _canonical(synthetic).encode("utf-8")
        ).hexdigest(),
    }


def test_native_only_cutover_baseline_is_byte_reconstructable() -> None:
    expected = (_canonical(build_baseline_document()) + "\n").encode("utf-8")
    actual = FIXTURE.read_bytes()

    assert actual == expected
    document = json.loads(actual)
    for operation, row in zip(OPERATIONS, document["operations"], strict=True):
        bundle = _bundle(operation)
        prepared = _prepared(operation)
        by_name = {item.name: item.content for item in prepared.components}
        assert [item["name"] for item in row["components"]] == sorted(by_name)
        for component in row["components"]:
            content = by_name[component["name"]]
            assert component["chars"] == len(content)
            assert component["bytes"] == len(content.encode("utf-8"))
            assert component["sha256"] == hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()
        assert row["evidence_bindings"] == _evidence_bindings(bundle)


def _measurement(operation: str, prepared: PreparedProviderInput):
    return measure_provider_input(
        prepared,
        provider="codex",
        role="codex",
        operation=operation,
        binding_fingerprint=hashlib.sha256(
            f"fingerprint:{operation}".encode()
        ).hexdigest(),
        policy=default_provider_input_budget_policy(),
    )


@pytest.mark.parametrize(
    "operation",
    ("codex_implementation", "codex_correction"),
)
def test_workflow_prompt_removal_has_exact_asset_and_manifest_delta(
    operation: str,
) -> None:
    baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
    row = next(item for item in baseline["operations"] if item["operation"] == operation)
    binding = next(
        item
        for item in row["evidence_bindings"]
        if item["evidence_id"] == "workflow-prompt"
    )
    reduced = _prepared(
        operation,
        omitted_evidence_ids=frozenset({"workflow-prompt"}),
    )
    reduced_measurement = _measurement(operation, reduced)
    removed_component = next(
        item
        for item in row["components"]
        if item["name"] == binding["component_name"]
    )

    assert row["total_chars"] - reduced_measurement.total_chars == (
        removed_component["chars"] + binding["manifest_contribution_chars"]
    )
    assert row["total_bytes"] - reduced_measurement.total_bytes == (
        removed_component["bytes"] + binding["manifest_contribution_bytes"]
    )

    reduced_by_name = {item.name: item.content for item in reduced.components}
    full_stdin = next(
        item for item in row["components"] if item["name"] == "stdin_prompt"
    )
    assert full_stdin["chars"] - len(reduced_by_name["stdin_prompt"]) == binding[
        "manifest_contribution_chars"
    ]
    assert full_stdin["bytes"] - len(
        reduced_by_name["stdin_prompt"].encode("utf-8")
    ) == binding["manifest_contribution_bytes"]
    for component in row["components"]:
        if component["name"] in {binding["component_name"], "stdin_prompt"}:
            continue
        content = reduced_by_name[component["name"]]
        assert component["sha256"] == hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()


def test_native_only_cutover_baseline_lock_is_complete() -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    assert lock == {
        "base_commit": BASE_COMMIT,
        "baseline_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "expected_removed_evidence_ids": ["approved-plan", "workflow-prompt"],
        "fixture_version": FIXTURE_VERSION,
    }
