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
    NativeCodexRequestSpec,
    build_native_codex_request,
)
from provider_input_efficiency import (
    build_correction_execution_package,
    build_slice_execution_package,
    compare_provider_input_components,
)
from prompts import NATIVE_CODEX_SYSTEM_POLICY


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json"
LOCK = FIXTURE.with_name("native-only-cutover-baseline-v1.lock.json")
REVIEWED_BASELINE_SHA256 = "3f07b52b79badf1340998973c4145b20a93892ea0f174942cd21ab1331c7adfc"
REVIEWED_LOCK_SHA256 = "11bb60a6aed1ab78b7911d9a2eb9ea6a216b845a313c324df0558085ad9fabb9"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_reviewed_baseline_and_lock_are_immutable() -> None:
    baseline_bytes = FIXTURE.read_bytes()
    lock_bytes = LOCK.read_bytes()
    lock = json.loads(lock_bytes)

    assert _sha256(baseline_bytes) == REVIEWED_BASELINE_SHA256
    assert _sha256(lock_bytes) == REVIEWED_LOCK_SHA256
    assert lock["baseline_sha256"] == REVIEWED_BASELINE_SHA256
    assert lock["fixture_version"] == "native-only-cutover-baseline-v1"
    assert lock["expected_removed_evidence_ids"] == [
        "approved-plan",
        "workflow-prompt",
    ]


def test_frozen_baseline_component_bindings_are_internally_complete() -> None:
    document = json.loads(FIXTURE.read_bytes())
    assert document["fixture_version"] == "native-only-cutover-baseline-v1"
    operations = document["operations"]
    assert [item["operation"] for item in operations] == [
        "codex_plan",
        "codex_plan_revision",
        "codex_implementation",
        "codex_correction",
        "codex_final_review",
        "codex_final_correction",
    ]
    for row in operations:
        components = {item["name"]: item for item in row["components"]}
        assert set(components) >= {"stdin_prompt", "response_schema"}
        assert row["total_chars"] == sum(item["chars"] for item in components.values())
        assert row["total_bytes"] == sum(item["bytes"] for item in components.values())
        for binding in row["evidence_bindings"]:
            component = components[binding["component_name"]]
            assert component["sha256"] == binding["content_sha256"]
            assert binding["manifest_entry_chars"] > 0
            assert binding["manifest_entry_bytes"] >= binding["manifest_entry_chars"]


def test_current_native_request_builder_does_not_restore_workflow_prompt() -> None:
    workflow_source = (ROOT / "src/workflow.py").read_text(encoding="utf-8")
    request_source = (ROOT / "src/workflow_requests.py").read_text(encoding="utf-8")
    assert '"workflow-prompt"' not in workflow_source
    assert '"workflow-prompt"' not in request_source
    assert '"native-policy", "system_policy"' not in workflow_source
    assert '"native-policy", "system_policy"' in request_source


def _three_slice_plan() -> str:
    return """# Synthetic approved plan

### Slice 1 - SENTINEL-SLICE-ONE

**\u0041kzeptanzkriterien**

- SENTINEL-CRITERION-ONE

### Slice 2 - Target package

**Ziel**

Implement only SENTINEL-TARGET-GOAL.

**Exakter Änderungspfad**

- `src/target.py`
- `tests/test_target.py`

**Querverweise**

- `docs/internal/contract.md#binding`
- `README.md#native-transport`

**\u0041kzeptanzkriterien**

- Preserve SENTINEL-TARGET-CRITERION.
- Reject an unbound package before provider start.

### Slice 3 - SENTINEL-SLICE-THREE

**\u0041kzeptanzkriterien**

- SENTINEL-CRITERION-THREE
"""


def test_slice_package_excludes_sibling_sentinels_and_binds_source_plan() -> None:
    plan = _three_slice_plan()
    package = build_slice_execution_package(
        plan_text=plan,
        source_plan_path="docs/internal/plan.md",
        slice_id=2,
        authorized_paths=("src/target.py", "tests/test_target.py"),
    )
    document = json.loads(package.canonical_json)

    assert plan not in package.canonical_json
    assert "SENTINEL-SLICE-ONE" not in package.canonical_json
    assert "SENTINEL-SLICE-THREE" not in package.canonical_json
    assert document["source_plan"] == {
        "path": "docs/internal/plan.md",
        "sha256": hashlib.sha256(plan.encode("utf-8")).hexdigest(),
    }
    assert document["slice"] == {
        "slice_id": 2,
        "goal": "Implement only SENTINEL-TARGET-GOAL.",
        "acceptance_criteria": [
            "Preserve SENTINEL-TARGET-CRITERION.",
            "Reject an unbound package before provider start.",
        ],
        "authorized_paths": ["src/target.py", "tests/test_target.py"],
        "cross_references": [
            "`docs/internal/contract.md#binding`",
            "`README.md#native-transport`",
        ],
        "open_findings": [],
    }


def test_slice_package_projects_only_open_findings_in_id_order() -> None:
    closed = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.CLOSED,
        summary="Closed imported finding",
        acceptance_test="The closed lifecycle remains review authority.",
        origin=FindingOrigin("PLAN", 1, AgentRole.CLAUDE),
        status_rationale="Closed in the source run.",
    )
    open_finding = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="Open imported finding",
        acceptance_test="Codex receives the exact imported acceptance test.",
        origin=FindingOrigin("PLAN", 2, AgentRole.CLAUDE),
    )

    package = build_slice_execution_package(
        plan_text=_three_slice_plan(),
        source_plan_path="docs/internal/plan.md",
        slice_id=2,
        authorized_paths=("src/target.py", "tests/test_target.py"),
        findings=(closed, open_finding),
    )

    assert json.loads(package.canonical_json)["slice"]["open_findings"] == [
        {
            "finding_id": "C-02",
            "finding_class": "OBSERVATION",
            "reporter": "claude",
            "summary": "Open imported finding",
            "acceptance_test": "Codex receives the exact imported acceptance test.",
        }
    ]


def test_slice_package_rejects_duplicate_finding_identity() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="Duplicated imported finding",
        acceptance_test="Reject before provider construction.",
        origin=FindingOrigin("PLAN", 1, AgentRole.CLAUDE),
    )

    with pytest.raises(ValueError, match="slice findings must be unique"):
        build_slice_execution_package(
            plan_text=_three_slice_plan(),
            source_plan_path="docs/internal/plan.md",
            slice_id=2,
            authorized_paths=("src/target.py", "tests/test_target.py"),
            findings=(finding, finding),
        )


def _current_components(operation: str) -> tuple[tuple[str, str], ...]:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="Synthetic affected finding",
        acceptance_test="The compact correction stays bound.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    fingerprint = hashlib.sha256(f"fingerprint:{operation}".encode()).hexdigest()
    request_kind = (
        NativeCodexRequestKind.IMPLEMENTATION
        if operation == "codex_implementation"
        else NativeCodexRequestKind.CORRECTION
    )
    context = NativeCodexContext(
        run_id="baseline-native-only-cutover",
        work_unit_id="3" if operation == "codex_implementation" else "4",
        operation=operation,
        current_fingerprint=fingerprint,
        request_kind=request_kind,
        contract=CodexStepContract(
            name=f"baseline-{operation}",
            readiness_marker=ReadinessMarker.IMPLEMENTATION,
            slice_id="01",
            round_number=2 if request_kind is NativeCodexRequestKind.CORRECTION else 1,
            require_test_files_record=True,
        ),
        previous_findings=(finding,) if request_kind is NativeCodexRequestKind.CORRECTION else (),
    )
    if request_kind is NativeCodexRequestKind.IMPLEMENTATION:
        package = build_slice_execution_package(
            plan_text=_three_slice_plan(),
            source_plan_path="docs/internal/plan.md",
            slice_id=2,
            authorized_paths=("src/target.py", "tests/test_target.py"),
        )
        assignment = "Implement only the bound Slice execution package."
        evidence = NativeCodexEvidenceInput(
            "slice-execution-package",
            "slice_execution_package",
            package.canonical_json,
            "docs/internal/plan.md",
        )
    else:
        package = build_correction_execution_package(
            current_fingerprint=fingerprint,
            authorized_paths=("src/target.py", "tests/test_target.py"),
            findings=(finding,),
            current_delta="diff --git a/src/target.py b/src/target.py\n+fixed\n",
        )
        assignment = "Correct only the affected findings and current delta."
        evidence = NativeCodexEvidenceInput(
            "correction-execution-package",
            "correction_execution_package",
            package.canonical_json,
        )
    bundle = build_native_codex_request(
        NativeCodexRequestSpec(
            context=context,
            target_branch="feature/native-only-transport-and-efficiency",
            base_commit="bdb955d4ca56ca292dd8bcbd4d1e776a2acb7a1b",
            authorized_paths=("src/target.py", "tests/test_target.py"),
            assignment=assignment,
            work_context="Repository-grounded compact execution context.",
            evidence=tuple(
                sorted(
                    (
                        NativeCodexEvidenceInput(
                            "native-policy",
                            "system_policy",
                            NATIVE_CODEX_SYSTEM_POLICY,
                        ),
                        evidence,
                    ),
                    key=lambda item: item.evidence_id,
                )
            ),
        ),
        inline_evidence_chars=10,
    )
    return (
        ("stdin_prompt", bundle.canonical_json),
        ("response_schema", bundle.provider_response_schema_json),
        *tuple(
            (f"evidence_asset_{index:03d}", asset.content)
            for index, asset in enumerate(bundle.evidence_assets, start=1)
        ),
    )


def test_correction_package_contains_only_affected_open_finding_and_current_delta() -> None:
    affected = FindingRecord(
        finding_id="C-02",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="AFFECTED-SENTINEL",
        acceptance_test="CURRENT-ACCEPTANCE-SENTINEL",
        origin=FindingOrigin("02", 2, AgentRole.CLAUDE),
    )
    package = build_correction_execution_package(
        current_fingerprint="d" * 64,
        authorized_paths=("src/target.py",),
        findings=(affected,),
        current_delta="CURRENT-DELTA-SENTINEL",
    )

    assert "AFFECTED-SENTINEL" in package.canonical_json
    assert "CURRENT-DELTA-SENTINEL" in package.canonical_json
    assert "UNRELATED-CLOSED-SENTINEL" not in package.canonical_json
    assert "PRIOR-FULL-DIFF-SENTINEL" not in package.canonical_json


def test_current_execution_packages_have_exact_frozen_baseline_deltas() -> None:
    baseline_bytes = FIXTURE.read_bytes()
    lock_bytes = LOCK.read_bytes()
    assert _sha256(baseline_bytes) == REVIEWED_BASELINE_SHA256
    assert _sha256(lock_bytes) == REVIEWED_LOCK_SHA256
    lock = json.loads(lock_bytes)
    assert lock["baseline_sha256"] == _sha256(baseline_bytes)
    document = json.loads(baseline_bytes)

    for operation in ("codex_implementation", "codex_correction"):
        row = next(
            item for item in document["operations"] if item["operation"] == operation
        )
        delta = compare_provider_input_components(
            operation=operation,
            baseline_row=row,
            current_components=_current_components(operation),
        )
        assert delta.char_reduction > 0
        assert delta.byte_reduction > 0
        assert delta.char_reduction == sum(item.chars for item in delta.removed) - sum(
            item.chars for item in delta.added
        )
        assert delta.byte_reduction == sum(item.bytes for item in delta.removed) - sum(
            item.bytes for item in delta.added
        )
        for component in delta.unchanged:
            baseline_component = next(
                item for item in row["components"] if item["name"] == component.name
            )
            assert component.sha256 == baseline_component["sha256"]
