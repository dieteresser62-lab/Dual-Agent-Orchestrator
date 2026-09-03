from __future__ import annotations

import ast
import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping

import pytest

import artifact_projection as artifact_projection_module
from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BindingPayload,
    CommandSpec,
    CorrectionWorkUnitPayload,
    FinalReviewPreflightPayload,
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    FindingSeverity,
    FindingTransitionPayload,
    Fingerprint,
    FingerprintKind,
    GatePayload,
    ImportedFindingTransition,
    PlanPayload,
    ProviderAttemptPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ProviderUsagePayload,
    ReviewAnchor,
    ReviewAnchorPayload,
    ReviewEvidencePayload,
    ReviewPayload,
    ReviewValidationBindingPayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    SliceSpec,
    ValidationAttestationPayload,
    ValidationRequestPayload,
    ValidationResult,
    WorkUnitPayload,
    canonical_json,
    finding_transition_sequence_sha256,
)
from artifact_projection import SECTION_KEYS, render_replay_sections
from artifact_replay import ArtifactReplayResult, replay_artifacts


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests/fixtures/replay-rendering-baseline-v1.json"
BASE_COMMIT = "c4081c4"
FIXED_TIME = "2026-09-03T16:00:00+00:00"
RUN_ID = "b43-rendering-anchor"
FINGERPRINT = Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64)
RENDERED_RECORD_TYPES = (
    "agent_result",
    "binding",
    "correction_work_unit",
    "final_review_preflight",
    "finding_handoff_export",
    "finding_handoff_import",
    "finding_transition",
    "gate",
    "provider_attempt",
    "provider_input_measurement",
    "review",
    "review_anchor",
    "review_validation_binding",
    "validation_attestation",
    "validation_request",
    "work_unit",
)
RECORD_RENDERER_HELPERS = {
    "_render_agent_result_record",
    "_render_binding_record",
    "_render_final_review_preflight_record",
    "_render_finding_handoff_export_record",
    "_render_finding_handoff_import_record",
    "_render_finding_transition_record",
    "_render_gate_record",
    "_render_provider_input_measurement_record",
    "_render_review_anchor_record",
    "_render_review_record",
    "_render_review_validation_binding_record",
    "_render_validation_attestation_record",
    "_render_validation_request_record",
    "_render_work_unit_record",
}


def _append(
    records: list[ArtifactRecord],
    payload: object,
    logical_id: str,
    *,
    run_id: str = RUN_ID,
    revision: int = 1,
    fingerprint: Fingerprint = FINGERPRINT,
) -> ArtifactRecord:
    record = ArtifactRecord.create(
        run_id=run_id,
        logical_id=logical_id,
        revision=revision,
        fingerprint=fingerprint,
        predecessor_ids=((records[-1].record_id,) if records else ()),
        created_at=f"2026-09-03T16:00:{len(records):02d}+00:00",
        idempotency_key=f"{logical_id}:{revision}",
        payload=payload,  # type: ignore[arg-type]
    )
    records.append(record)
    return record


def _accepted_replay(
    records: list[ArtifactRecord], run_id: str = RUN_ID
) -> ArtifactReplayResult:
    _append(
        records,
        RunIdentityPayload(
            "inbox/backlog/00-b43-auftrag-replay-rendering-zerlegen.md",
            "feature/backlog-followups",
            "b" * 40,
            "IMPLEMENT",
            None,
        ),
        "run-identity",
        run_id=run_id,
    )
    _append(
        records,
        RunProfilePayload(
            RoleProfilePayload("gpt-5.6-sol", "medium"),
            RoleProfilePayload("opus", "max"),
        ),
        "run-profile",
        run_id=run_id,
    )
    return replay_artifacts(
        tuple(records),
        run_id,
        require_content_authority=False,
        require_review_authority=False,
    )


def _core_review_replay() -> ArtifactReplayResult:
    records: list[ArtifactRecord] = []
    _append(records, WorkUnitPayload("1", 1, ("src/one.py",)), "work-unit-1")
    _append(
        records,
        AgentResultPayload(
            Role.CODEX,
            "1",
            "ready",
            ("tests/test_one.py",),
            "native-codex-v2",
            "native-codex-request-" + "b" * 64,
            "c" * 64,
        ),
        "agent-1-codex_implementation-1",
    )
    _append(
        records,
        CorrectionWorkUnitPayload("2", 2, ("src/two.py",), ("C-01",)),
        "work-unit-2",
    )
    _append(
        records,
        FindingTransitionPayload(
            "C-01",
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.BLOCKER,
            "open",
            "The renderer must preserve every byte.",
            "2",
            "Rendering drift.",
            "The frozen projection stays equal.",
            "2",
            2,
        ),
        "finding-C-01",
    )
    _append(
        records,
        ValidationRequestPayload(
            (CommandSpec("pytest", ("python3", "-m", "pytest", "tests/ -v")),),
            Role.ORCHESTRATOR,
        ),
        "validation-request-2",
    )
    attestation = _append(
        records,
        ValidationAttestationPayload(
            (
                ValidationResult(
                    CommandSpec("pytest", ("python3", "-m", "pytest", "tests/ -v")),
                    "pass",
                    0,
                    "d" * 64,
                ),
            ),
            Role.ORCHESTRATOR,
            "d" * 64,
            "ar1-" + "0" * 64,
        ),
        "validation-2",
    )
    review = _append(
        records,
        ReviewPayload(
            Role.CLAUDE,
            "2",
            "approved",
            ("C-01",),
            None,
            "native-claude-review-v2",
            "native-review-request-" + "e" * 64,
            "f" * 64,
            ReviewEvidencePayload(
                "Output bytes, record ordering, and failure paths checked.",
                "A newly introduced payload type could remain unrendered.",
                "Adding a renderer without extending the inventory breaks coverage.",
            ),
            test_files=("tests/test_replay_rendering_baseline.py",),
            pre_mortem="A heading or record block moves while tests compare only live code.",
        ),
        "review-claude-2-2",
    )
    binding_digest = hashlib.sha256(review.record_id.encode("utf-8")).hexdigest()[:16]
    _append(
        records,
        ReviewAnchorPayload(
            review.record_id,
            (
                ReviewAnchor(
                    "render-bytes",
                    "c4081c4",
                    "replay-rendering-baseline-v1.json",
                    "byte-identical Markdown sections",
                    "none",
                ),
            ),
        ),
        f"review-anchors-{binding_digest}",
    )
    _append(
        records,
        ReviewValidationBindingPayload(review.record_id, attestation.record_id),
        f"review-validation-{binding_digest}",
    )
    _append(
        records,
        GatePayload("test-change", "approved", Role.USER, "Explicit approval."),
        "gate-test-change",
    )
    _append(
        records,
        BindingPayload(
            "commit",
            "0123456789abcdef0123456789abcdef01234567",
            attestation.record_id,
            (review.record_id,),
        ),
        "commit-2",
    )
    return _accepted_replay(records)


def _provider_replay() -> ArtifactReplayResult:
    run_id = f"{RUN_ID}-provider"
    records: list[ArtifactRecord] = []
    _append(records, WorkUnitPayload("3", 1, ("src/provider.py",)), "work-unit-3", run_id=run_id)
    measurement = _append(
        records,
        ProviderInputMeasurementPayload(
            Role.CLAUDE,
            Role.CLAUDE,
            "claude_slice_review",
            "3",
            "1" * 64,
            "2" * 64,
            "3" * 64,
            "4" * 64,
            (ProviderInputComponentPayload("prompt", 12, 13),),
            12,
            13,
            100,
            100,
            80,
            90,
            "provider-contract",
            80,
            90,
            True,
            (),
            0,
            0,
            "prompt",
        ),
        "measurement-3",
        run_id=run_id,
    )
    started = ProviderAttemptPayload(
        Role.CLAUDE,
        Role.CLAUDE,
        "claude_slice_review",
        "3",
        "provider-operation-3",
        "a" * 64,
        measurement.record_id,
        "3" * 64,
        1,
        "started",
        "2026-09-03T16:01:00+00:00",
        None,
        None,
        None,
        None,
        "opus",
        "max",
    )
    _append(records, started, "provider-attempt-3-1", run_id=run_id)
    _append(
        records,
        ProviderAttemptPayload(
            Role.CLAUDE,
            Role.CLAUDE,
            "claude_slice_review",
            "3",
            "provider-operation-3",
            "a" * 64,
            measurement.record_id,
            "3" * 64,
            1,
            "failed",
            "2026-09-03T16:01:00+00:00",
            "2026-09-03T16:01:02+00:00",
            2.0,
            "network",
            ProviderUsagePayload(input_tokens=21, output_tokens=5, total_tokens=26),
            "opus",
            "max",
        ),
        "provider-attempt-3-1",
        run_id=run_id,
        revision=2,
    )
    _append(
        records,
        FinalReviewPreflightPayload(
            Role.CLAUDE,
            Role.CLAUDE,
            "claude_final_review",
            "3",
            "1" * 64,
            "2" * 64,
            measurement.record_id,
            "denied",
            "technical",
            "INPUT-TOO-LARGE",
            (measurement.record_id,),
            ("src/provider.py",),
            "Reduce the bounded review packet.",
        ),
        "preflight-3",
        run_id=run_id,
    )
    return _accepted_replay(records, run_id)


def _handoff_import_replay() -> ArtifactReplayResult:
    run_id = f"{RUN_ID}-import"
    records: list[ArtifactRecord] = []
    source = ImportedFindingTransition(
        "ar1-" + "5" * 64,
        FindingTransitionPayload(
            "C-02",
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.OBSERVATION,
            "open",
            "Imported audit history.",
            "source-unit",
            "Imported rendering note.",
            "The note remains visible.",
            "source-slice",
            1,
        ),
    )
    imported = _append(
        records,
        FindingHandoffImportPayload(
            "source-run",
            "ar1-" + "6" * 64,
            "7" * 40,
            "ar1-" + "8" * 64,
            "ar1-" + "9" * 64,
            run_id,
            "b" * 64,
            finding_transition_sequence_sha256((source,)),
            (source,),
            Role.ORCHESTRATOR,
        ),
        "finding-import",
        run_id=run_id,
    )
    _append(
        records,
        WorkUnitPayload("4", 1, ("src/import.py",), ("C-02",), imported.record_id),
        "work-unit-4",
        run_id=run_id,
    )
    return _accepted_replay(records, run_id)


def _handoff_export_replay() -> ArtifactReplayResult:
    run_id = f"{RUN_ID}-export"
    records: list[ArtifactRecord] = []
    _append(
        records,
        PlanPayload(
            "inbox/backlog/b43-plan.md",
            "7" * 40,
            (SliceSpec("1", "Preserve rendering.", ("src/artifact_projection.py",)),),
        ),
        "plan",
        run_id=run_id,
    )
    finding = _append(
        records,
        FindingTransitionPayload(
            "C-03",
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.OBSERVATION,
            "open",
            "Export this rendering history.",
            work_unit_id="plan",
            summary="Exported rendering note.",
            acceptance_test="The export remains visible.",
            origin_slice_id="1",
            origin_round_number=1,
        ),
        "finding-C-03",
        run_id=run_id,
    )
    review = _append(
        records,
        ReviewPayload(
            Role.CLAUDE,
            "plan",
            "approved",
            ("C-03",),
            "Export approval evidence.",
            "native-claude-review-v2",
            "native-review-request-" + "c" * 64,
            "d" * 64,
        ),
        "review-claude-plan-1",
        run_id=run_id,
    )
    transition = ImportedFindingTransition(finding.record_id, finding.payload)
    _append(
        records,
        FindingHandoffExportPayload(
            run_id,
            records[-1].record_id,
            "7" * 40,
            review.record_id,
            (finding.record_id,),
            finding_transition_sequence_sha256((transition,)),
            "inbox/backlog/b43-followup.md",
            "e" * 64,
            Role.ORCHESTRATOR,
        ),
        "finding-export",
        run_id=run_id,
    )
    return _accepted_replay(records, run_id)


def _anchor_replays() -> Mapping[str, ArtifactReplayResult]:
    return {
        "core-review": _core_review_replay(),
        "provider-attempt": _provider_replay(),
        "finding-import": _handoff_import_replay(),
        "finding-export": _handoff_export_replay(),
    }


def _rendered_document(replay: ArtifactReplayResult) -> bytes:
    sections = render_replay_sections(replay)
    assert tuple(sections) == SECTION_KEYS
    return canonical_json({key: sections[key] for key in SECTION_KEYS})


def _anchor_entries(
    replays: Mapping[str, ArtifactReplayResult],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for case_id, replay in replays.items():
        document = _rendered_document(replay)
        record_ids = tuple(record.record_id for record in replay.records)
        entries.append(
            {
                "case_id": case_id,
                "record_count": len(record_ids),
                "head_record_id": record_ids[-1],
                "record_ids_sha256": hashlib.sha256(canonical_json(record_ids)).hexdigest(),
                "rendered_document_sha256": hashlib.sha256(document).hexdigest(),
                "rendered_document_base64": base64.b64encode(document).decode("ascii"),
            }
        )
    return entries


def _baseline_document(entries: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "replay-rendering-baseline-v1",
        "source_commit": BASE_COMMIT,
        "fixed_time": FIXED_TIME,
        "rendered_record_types": list(RENDERED_RECORD_TYPES),
        "uncovered_record_types": [],
        "entries": entries,
    }


def _load_baseline() -> dict[str, object]:
    document = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert set(document) == {
        "schema_version",
        "source_commit",
        "fixed_time",
        "rendered_record_types",
        "uncovered_record_types",
        "entries",
    }
    assert document["schema_version"] == "replay-rendering-baseline-v1"
    assert document["source_commit"] == BASE_COMMIT
    assert document["fixed_time"] == FIXED_TIME
    assert tuple(document["rendered_record_types"]) == RENDERED_RECORD_TYPES
    assert document["uncovered_record_types"] == []
    return document


@dataclass(frozen=True)
class RenderingAnchor:
    replays: Mapping[str, ArtifactReplayResult]
    expected: Mapping[str, dict[str, object]]
    expected_document: Mapping[str, object]
    actual_document: Mapping[str, object]
    actual: Mapping[str, bytes]


_ANCHOR_BUILD_COUNT = 0


@pytest.fixture(scope="session")
def replay_rendering_anchor() -> RenderingAnchor:
    global _ANCHOR_BUILD_COUNT
    _ANCHOR_BUILD_COUNT += 1
    replays = _anchor_replays()
    baseline = _load_baseline()
    expected_entries = baseline["entries"]
    assert isinstance(expected_entries, list)
    actual_document = _baseline_document(_anchor_entries(replays))
    actual_entries = actual_document["entries"]
    assert isinstance(actual_entries, list)
    expected = {entry["case_id"]: entry for entry in expected_entries}
    return RenderingAnchor(
        replays,
        expected,
        baseline,
        actual_document,
        {
            entry["case_id"]: base64.b64decode(
                entry["rendered_document_base64"], validate=True
            )
            for entry in actual_entries
        },
    )


def _assert_anchored_bytes(entry: Mapping[str, object], actual: bytes) -> None:
    expected = base64.b64decode(entry["rendered_document_base64"], validate=True)
    assert (
        hashlib.sha256(expected).hexdigest() == entry["rendered_document_sha256"]
    ), "anchored byte payload differs from its stored digest"
    assert actual == expected, "rendered bytes differ from pre-split anchor"


def test_rendering_anchor_covers_every_record_type_distinguished_by_renderer(
    replay_rendering_anchor: RenderingAnchor,
) -> None:
    present = {
        record.record_type.value
        for replay in replay_rendering_anchor.replays.values()
        for record in replay.records
        if record.record_type.value in RENDERED_RECORD_TYPES
    }
    assert present == set(RENDERED_RECORD_TYPES)
    assert set(replay_rendering_anchor.replays) == set(replay_rendering_anchor.expected)
    assert _ANCHOR_BUILD_COUNT == 1


def test_replay_renderer_is_split_into_named_bounded_helpers() -> None:
    source = Path(artifact_projection_module.__file__).read_text(encoding="utf-8")
    functions = {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
    }

    def direct_local_calls(function_name: str) -> set[str]:
        return {
            node.func.id
            for node in ast.walk(functions[function_name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in functions
        }

    assert direct_local_calls("render_replay_sections") == {
        "_collect_provider_rendering_fact",
        "_finalize_replay_rendering",
        "_new_replay_rendering",
        "_render_convergence_summary",
        "_render_provider_attempts",
        "_render_record",
    }
    assert direct_local_calls("_render_record") == {*RECORD_RENDERER_HELPERS, "_safe"}
    for helper in {
        "render_replay_sections",
        "_render_record",
        "_render_provider_attempts",
        *RECORD_RENDERER_HELPERS,
    }:
        function = functions[helper]
        assert function.end_lineno is not None
        assert function.end_lineno - function.lineno + 1 < 200


def test_every_pre_split_rendering_stays_byte_identical(
    replay_rendering_anchor: RenderingAnchor,
) -> None:
    assert (
        replay_rendering_anchor.actual_document
        == replay_rendering_anchor.expected_document
    )
    for case_id, actual in replay_rendering_anchor.actual.items():
        _assert_anchored_bytes(replay_rendering_anchor.expected[case_id], actual)
    assert _ANCHOR_BUILD_COUNT == 1


def test_rendering_anchor_rejects_a_heading_mutation(
    replay_rendering_anchor: RenderingAnchor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = "core-review"
    original = artifact_projection_module._render_agent_result_record

    def change_heading(*args, **kwargs):  # type: ignore[no-untyped-def]
        rendering = args[0]
        start = len(rendering.bindings_and_units)
        original(*args, **kwargs)
        rendering.bindings_and_units[start] = rendering.bindings_and_units[
            start
        ].replace(" · ready", " · changed")

    monkeypatch.setattr(
        artifact_projection_module,
        "_render_agent_result_record",
        change_heading,
    )
    with pytest.raises(AssertionError, match="rendered bytes differ"):
        _assert_anchored_bytes(
            replay_rendering_anchor.expected[case_id],
            _rendered_document(replay_rendering_anchor.replays[case_id]),
        )
    assert _ANCHOR_BUILD_COUNT == 1


def test_rendering_anchor_rejects_swapped_record_sections(
    replay_rendering_anchor: RenderingAnchor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = "core-review"
    original = artifact_projection_module._finalize_replay_rendering

    def swap_record_sections(rendering):  # type: ignore[no-untyped-def]
        output = rendering.bindings_and_units
        first = output.index("### Codex · Runde 1 · ready")
        second = output.index("### Korrektur-Work-Unit · Slice 2 · Runde 2")
        after_second = next(
            index
            for index in range(second + 1, len(output))
            if output[index].startswith("### ")
        )
        output[first:after_second] = (
            output[second:after_second] + output[first:second]
        )
        return original(rendering)

    monkeypatch.setattr(
        artifact_projection_module,
        "_finalize_replay_rendering",
        swap_record_sections,
    )
    with pytest.raises(AssertionError, match="rendered bytes differ"):
        _assert_anchored_bytes(
            replay_rendering_anchor.expected[case_id],
            _rendered_document(replay_rendering_anchor.replays[case_id]),
        )
    assert _ANCHOR_BUILD_COUNT == 1
