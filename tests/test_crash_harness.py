from __future__ import annotations

import ast
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import crash_harness
from artifact_models import SIDE_EFFECT_CLASSES
from crash_harness import (
    BOUNDARY_ORDER,
    HARNESS_SCHEMA_VERSION,
    LEDGER_ORDER,
    RESULT_SCHEMA_VERSION,
    CrashHarnessError,
    CrashHarnessManifest,
    run_provider_free_harness,
)


MANIFEST = ROOT / "tests/fixtures/crash_harness/manifest-v1.json"


def test_manifest_is_versioned_and_derived_from_complete_ledger_inventory() -> None:
    manifest = CrashHarnessManifest.load(MANIFEST)

    assert manifest.scenario_version == "s5-v1"
    assert manifest.effect_classes == LEDGER_ORDER
    assert set(manifest.effect_classes) == set(SIDE_EFFECT_CLASSES)
    assert manifest.boundary_matrix == {
        effect_class: (
            BOUNDARY_ORDER
            if effect_class not in {"internal", "ledger"}
            else tuple(
                phase
                for phase in BOUNDARY_ORDER
                if phase not in {"before_effect", "after_effect"}
            )
        )
        for effect_class in LEDGER_ORDER
    }
    schema = json.loads(
        (ROOT / "schemas/orchestrator-artifact-v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    side_effect = schema["$defs"]["side_effect"]
    assert set(side_effect["properties"]["effect_class"]["enum"]) == set(
        manifest.effect_classes
    )


def test_manifest_fails_closed_when_one_ledger_edge_is_missing(tmp_path: Path) -> None:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    document["effect_classes"].remove("provider_start")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CrashHarnessError, match="every ledger"):
        CrashHarnessManifest.load(path)


def test_crash_matrix_uses_production_resume_and_names_ledger_stop(
    tmp_path: Path,
) -> None:
    payload = run_provider_free_harness(
        repository_root=ROOT,
        work_root=tmp_path / "run",
        manifest_path=MANIFEST,
        repository_commit="f" * 40,
    )
    result = json.loads(payload)

    assert result["schema_version"] == RESULT_SCHEMA_VERSION
    assert result["scenario_version"] == "s5-v1"
    assert result["repository_commit"] == "f" * 40
    assert result["mode"] == "provider-free"
    assert result["invocation_counts"]["real_provider_starts"] == 0
    assert result["invocation_counts"]["real_provider_process_starts"] == 0
    assert result["invocation_counts"]["simulated_agent_starts"] > 0
    assert result["commit_count"] == 5
    assert result["end_state"] == "stopped"
    assert result["blocked_acceptance_cases"] == [
        "baseline-crash-convergence",
        "record-backed-long-run",
    ]
    matrix = result["crash_matrix"]
    singles = [row for row in matrix if row["requested_crashes"] == 1]
    repeated = [row for row in matrix if row["requested_crashes"] == 2]
    assert len(singles) == sum(
        len(phases) for phases in CrashHarnessManifest.load(MANIFEST).boundary_matrix.values()
    )
    assert len(repeated) == 4
    assert {
        (row["effect_class"], row["phase"])
        for row in singles
    } == {
        (effect_class, phase)
        for effect_class, phases in CrashHarnessManifest.load(
            MANIFEST
        ).boundary_matrix.items()
        for phase in phases
    }
    converged = [row for row in matrix if row["end_state"] == "converged"]
    stopped = [row for row in matrix if row["end_state"] == "stop_condition"]
    assert {
        (row["effect_class"], row["phase"])
        for row in stopped
    } == {
        ("ledger", phase)
        for phase in (
            "before_intent",
            "after_intent",
            "before_result",
            "after_result",
        )
    }
    assert all(row["effect_class"] != "ledger" for row in converged)
    assert all(
        row["physical_execution_count"]
        == (0 if row["effect_class"] == "internal" else 1)
        for row in converged
    )
    assert all(row["result_completion_count"] == 1 for row in converged)
    assert all(row["physical_execution_count"] == 0 for row in stopped)
    assert all(row["resume_diagnostic_code"] for row in stopped)
    assert all(row["production_resume_attempts"] > 0 for row in matrix)
    assert all(row["production_resume_successes"] > 0 for row in converged)
    assert all(row["production_resume_successes"] == 0 for row in stopped)
    assert all(
        row["production_boundary_entry"]
        == "ProductionWorkflowDriver.bind_work_unit"
        for row in matrix
        if row["effect_class"] in {"internal", "ledger"}
    )
    assert all(
        row["stop_condition_id"] == "STRUCTURED-BASELINE-NONRESUMABLE"
        and row["stop_scope"]
        == "run-identity-through-workflow-status-gate-ledger-prefix"
        for row in stopped
    )
    assert all(row["observed_crashes"] == row["requested_crashes"] for row in matrix)
    assert all(row["phase"] == "after_effect" for row in repeated)
    assert result["semantic_binding"]["rejected"] is True
    assert result["semantic_binding"]["projection_call_count"] == 0
    assert result["semantic_binding"]["record_count_before_corruption"] > 4
    assert result["semantic_binding"]["diagnostic_code"]
    assert result["unknown_reconciliation"]["outcome"] == "unknown"
    assert result["unknown_reconciliation"]["rejected"] is True
    assert result["unknown_reconciliation"]["physical_execution_count"] == 0
    assert set(result["record_semantic_heads"]) == set(LEDGER_ORDER)
    assert all(
        item["all_injected_crashes_observed"]
        for item in result["workflow_boundary_evidence"].values()
    )
    assert result["workflow_boundary_evidence"]["baseline_initialization"][
        "all_cases_converged"
    ] is False
    assert all(
        item["all_cases_converged"]
        for role, item in result["workflow_boundary_evidence"].items()
        if role != "baseline_initialization"
    )
    assert {
        (item["effect_class"], item["phase"])
        for item in result["stop_conditions"]
    } == {
        (row["effect_class"], row["phase"])
        for row in stopped
    }
    assert result["record_ahead_evidence"] == {
        "file_write": {
            "physical_execution_count": 1,
            "result_completion_count": 1,
        },
        "git_commit": {
            "physical_execution_count": 1,
            "result_completion_count": 1,
        },
        "provider_start": {
            "physical_execution_count": 1,
            "result_completion_count": 1,
        },
        "queue_move": {
            "physical_execution_count": 1,
            "result_completion_count": 1,
        },
    }
    retries = result["retry_continuations"]
    assert [item["failure_kind"] for item in retries] == [
        "quota",
        "network",
        "process",
    ]
    assert all(item["resume_step"] == "codex_plan" for item in retries)
    assert all(item["evidence_count"] == 1 for item in retries)
    journeys = {item["scenario_id"]: item for item in result["journeys"]}
    assert set(journeys) == {
        "plan-implement-finalreview",
        "multi-slice-correction-observation-resume",
    }
    assert all(item["end_state"] == "completed" for item in journeys.values())
    assert all(
        item["durability_mode"] == "scripted-state-machine-only"
        and item["record_backing_blocked_by"]
        == "STRUCTURED-BASELINE-NONRESUMABLE"
        for item in journeys.values()
    )
    assert journeys["plan-implement-finalreview"]["plan_only_execution_mode"] == "PLAN_ONLY"
    assert journeys["plan-implement-finalreview"]["implement_execution_mode"] == "IMPLEMENT"
    assert journeys["plan-implement-finalreview"]["handoff_idempotent"] is True
    assert journeys["multi-slice-correction-observation-resume"]["resume_count"] == 1
    assert journeys["multi-slice-correction-observation-resume"][
        "independent_execution"
    ] is True
    assert journeys["multi-slice-correction-observation-resume"]["correction_round_count"] == 1
    assert journeys["multi-slice-correction-observation-resume"][
        "finding_statuses"
    ] == ["C-01:CLOSED", "C-02:CLOSED"]


def test_harness_result_is_byte_stable_and_self_bound(tmp_path: Path) -> None:
    first = run_provider_free_harness(
        repository_root=ROOT,
        work_root=tmp_path / "first",
        manifest_path=MANIFEST,
        repository_commit="e" * 40,
    )
    second = run_provider_free_harness(
        repository_root=ROOT,
        work_root=tmp_path / "second",
        manifest_path=MANIFEST,
        repository_commit="e" * 40,
    )

    assert first == second
    document = json.loads(first)
    assert {
        "inbox/backlog/00-s5-auftrag-crash-injection-und-harness.md",
        "src/artifact_migration.py",
        "src/artifact_replay.py",
        "src/orchestrator.py",
        "src/side_effects.py",
    } <= set(document["measured_source_paths"])
    digest = document.pop("artifact_sha256")
    from artifact_models import canonical_json

    import hashlib

    assert digest == hashlib.sha256(canonical_json(document)).hexdigest()


def test_side_effect_executor_exposes_every_boundary_on_execute_and_split_paths() -> None:
    source = (ROOT / "src/side_effects.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    executor = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SideEffectExecutor"
    )
    observed = {
        method.name: {
            call.args[1].attr
            for call in ast.walk(method)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "_observe"
            and len(call.args) == 2
            and isinstance(call.args[1], ast.Attribute)
        }
        for method in executor.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert {name: observed[name] for name in (
        "_record_intent", "execute", "begin", "complete", "_complete"
    )} == {
        "_record_intent": {"BEFORE_INTENT", "AFTER_INTENT"},
        "execute": {"BEFORE_EFFECT", "AFTER_EFFECT"},
        "begin": {"BEFORE_EFFECT", "AFTER_EFFECT"},
        "complete": {"AFTER_EFFECT"},
        "_complete": {"BEFORE_RESULT", "AFTER_RESULT"},
    }


def test_provider_split_path_executes_every_declared_runtime_boundary(tmp_path: Path) -> None:
    result = json.loads(
        run_provider_free_harness(
            repository_root=ROOT,
            work_root=tmp_path / "run",
            manifest_path=MANIFEST,
            repository_commit="d" * 40,
        )
    )
    rows = [
        row
        for row in result["crash_matrix"]
        if row["effect_class"] == "provider_start"
        and row["requested_crashes"] == 1
    ]

    assert {row["phase"] for row in rows} == set(BOUNDARY_ORDER)
    assert all(row["observed_crashes"] == 1 for row in rows)
    assert all(row["physical_execution_count"] == 1 for row in rows)


def test_record_only_injection_uses_the_production_baseline_writer() -> None:
    harness_tree = ast.parse(
        (ROOT / "scripts/crash_harness.py").read_text(encoding="utf-8")
    )
    direct_injector_calls = tuple(
        node
        for node in ast.walk(harness_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "injector"
    )
    assert direct_injector_calls == ()

    production_tree = ast.parse(
        (ROOT / "src/orchestrator.py").read_text(encoding="utf-8")
    )
    baseline = next(
        node
        for node in ast.walk(production_tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_persist_structured_baseline"
    )
    record_only_specs = {
        call.args[0].value
        for call in ast.walk(baseline)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "SideEffectSpec"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    executor_calls = tuple(
        call
        for call in ast.walk(baseline)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "_side_effect_executor"
    )

    assert record_only_specs == {"internal", "ledger"}
    assert len(executor_calls) == 2


def test_s5_does_not_reintroduce_recoverable_special_cases() -> None:
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src").glob("*.py")
    )

    assert "_recoverable_" not in production
    assert HARNESS_SCHEMA_VERSION in MANIFEST.read_text(encoding="utf-8")


def test_operator_command_writes_the_canonical_harness_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "evidence" / "s5.json"
    captured: dict[str, Path] = {}

    def fake_run(**kwargs: Path) -> bytes:
        captured.update(kwargs)
        return b'{"canonical":true}\n'

    monkeypatch.setattr(crash_harness, "run_provider_free_harness", fake_run)

    assert crash_harness.main(
        [
            "--repository-root", str(ROOT),
            "--work-root", str(tmp_path / "work"),
            "--manifest", str(MANIFEST),
            "--output", str(output),
        ]
    ) == 0
    assert output.read_bytes() == b'{"canonical":true}\n'
    assert captured["repository_root"] == ROOT.resolve()
    assert captured["manifest_path"] == MANIFEST.resolve()
