from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.crash_harness

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import crash_harness
from artifact_models import (
    SIDE_EFFECT_CLASSES,
    Fingerprint,
    FingerprintKind,
    WorkflowPolicyPayload,
)
from artifact_replay import ArtifactReplayError
from crash_harness import (
    BOUNDARY_ORDER,
    HARNESS_SCHEMA_VERSION,
    LEDGER_ORDER,
    RESULT_SCHEMA_VERSION,
    CrashHarnessError,
    CrashHarnessManifest,
    _tracked_implementation_sources,
    run_provider_free_harness,
)


MANIFEST = ROOT / "tests/fixtures/crash_harness/manifest-v1.json"


def _tracked_repository_snapshot(
    destination: Path, *, source: Path = ROOT
) -> Path:
    result = subprocess.run(
        (
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
        ),
        cwd=source,
        capture_output=True,
        check=True,
    )
    destination.mkdir()
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        source_path = source / relative
        if not source_path.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
    for ignored_root in ("inbox", "outbox", ".orchestrator"):
        assert not (destination / ignored_root).exists()
    subprocess.run(("git", "init", "--quiet"), cwd=destination, check=True)
    subprocess.run(("git", "add", "-f", "--", "."), cwd=destination, check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=S7b Harness",
            "-c",
            "user.email=s7b-harness@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "tracked repository snapshot",
        ),
        cwd=destination,
        check=True,
    )
    return destination


def _run_snapshot_harness(
    repository: Path,
    *,
    work_root: Path,
    manifest: Path,
) -> bytes:
    output = work_root.parent / f"{work_root.name}.json"
    program = "\n".join(
        (
            "from pathlib import Path",
            "import sys",
            "repository = Path(sys.argv[1])",
            "sys.path.insert(0, str(repository / 'scripts'))",
            "from crash_harness import run_provider_free_harness",
            "payload = run_provider_free_harness(",
            "    repository_root=repository,",
            "    work_root=Path(sys.argv[2]),",
            "    manifest_path=Path(sys.argv[3]),",
            ")",
            "Path(sys.argv[4]).write_bytes(payload)",
        )
    )
    completed = subprocess.run(
        (
            sys.executable,
            "-I",
            "-c",
            program,
            str(repository),
            str(work_root),
            str(manifest),
            str(output),
        ),
        cwd=repository,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.fail(
            "Isolated snapshot harness failed"
            f" (exit {completed.returncode}).\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}",
            pytrace=False,
        )
    return output.read_bytes()


def _implementation_source_digest(
    repository: Path, sources: tuple[Path, ...]
) -> str:
    digest = hashlib.sha256()
    for path in sources:
        relative = path.resolve().relative_to(repository.resolve()).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_implementation_sources_include_untracked_and_exclude_ignored_or_unmatched(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    tracked = (
        Path("schemas/root.json"),
        Path("schemas/nested/child.json"),
        Path("scripts/crash_harness.py"),
        Path("src/root.py"),
        Path("src/package/child.py"),
        Path("tests/fixtures/crash_harness/manifest-v1.json"),
    )
    for relative in tracked:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative.as_posix(), encoding="utf-8")
    gitignore = repository / ".gitignore"
    gitignore.write_text(
        "inbox/\noutbox/\n.orchestrator/\nschemas/ignored.json\nsrc/ignored.py\n",
        encoding="utf-8",
    )
    subprocess.run(("git", "init", "--quiet"), cwd=repository, check=True)
    subprocess.run(
        (
            "git",
            "add",
            "--",
            ".gitignore",
            *(relative.as_posix() for relative in tracked),
        ),
        cwd=repository,
        check=True,
    )

    initial_sources = _tracked_implementation_sources(
        repository,
        repository / "tests/fixtures/crash_harness/manifest-v1.json",
    )
    initial_digest = _implementation_source_digest(repository, initial_sources)

    excluded = (
        Path("inbox/ignored.py"),
        Path("outbox/ignored.py"),
        Path(".orchestrator/ignored.py"),
        Path("schemas/ignored.json"),
        Path("src/ignored.py"),
        Path("docs/notiz.md"),
    )
    for relative in excluded:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("excluded\n", encoding="utf-8")
    filtered_sources = _tracked_implementation_sources(
        repository,
        repository / "tests/fixtures/crash_harness/manifest-v1.json",
    )
    assert filtered_sources == initial_sources
    assert _implementation_source_digest(repository, filtered_sources) == initial_digest

    untracked_module = Path("src/untracked_backup.py")
    (repository / untracked_module).write_text(
        "VALUE = 'snapshot-import-ok'\n", encoding="utf-8"
    )
    sources = _tracked_implementation_sources(
        repository,
        repository / "tests/fixtures/crash_harness/manifest-v1.json",
    )

    assert tuple(path.relative_to(repository) for path in sources) == tuple(
        sorted((*tracked, untracked_module), key=lambda path: path.as_posix())
    )
    assert _implementation_source_digest(repository, sources) != initial_digest

    snapshot = _tracked_repository_snapshot(
        tmp_path / "snapshot", source=repository
    )
    assert (snapshot / untracked_module).is_file()
    assert (snapshot / "docs/notiz.md").is_file()
    assert all(not (snapshot / relative).exists() for relative in excluded[:-1])
    program = (
        "from pathlib import Path; import sys; "
        "sys.path.insert(0, str(Path(sys.argv[1]) / 'src')); "
        "import untracked_backup; "
        "assert untracked_backup.VALUE == 'snapshot-import-ok'"
    )
    subprocess.run(
        (sys.executable, "-I", "-c", program, str(snapshot)),
        cwd=snapshot,
        check=True,
    )


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


def test_crash_matrix_uses_production_resume_and_converges_every_boundary(
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
    assert result["baseline_resolution"] == {
        "decision": "A",
        "strategy": "complete-canonical-append-prefix",
        "rationale": (
            "an interrupted initializer contains only idempotent record appends "
            "and no physical effect, so the exact canonical prefix is completed"
        ),
        "admission_rule": (
            "every exact cut of the canonical pre-work record sequence, "
            "including workflow, gate, task, work-unit, and plan facts"
        ),
        "rejection_rule": (
            "any non-prefix fact before baseline completion or prior non-ledger "
            "side effect remains fail-closed"
        ),
    }
    assert result["invocation_counts"]["real_provider_starts"] == 0
    assert result["invocation_counts"]["real_provider_process_starts"] == 0
    assert result["invocation_counts"]["simulated_agent_starts"] > 0
    assert result["commit_count"] == 5
    assert result["end_state"] == "converged"
    assert result["blocked_acceptance_cases"] == []
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
    assert len(converged) == len(matrix)
    assert stopped == []
    assert all(
        row["physical_execution_count"]
        == (0 if row["effect_class"] in {"internal", "ledger"} else 1)
        for row in converged
    )
    assert all(row["result_completion_count"] == 1 for row in converged)
    assert all(row["production_resume_attempts"] > 0 for row in matrix)
    assert all(row["production_resume_successes"] > 0 for row in matrix)
    assert all(
        row["production_boundary_entry"]
        == "ProductionWorkflowDriver.bind_work_unit"
        for row in matrix
        if row["effect_class"] in {"internal", "ledger"}
    )
    assert all(row.get("stop_condition_id") is None for row in matrix)
    assert all(row.get("stop_scope") is None for row in matrix)
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
    assert all(
        item["all_cases_converged"]
        for item in result["workflow_boundary_evidence"].values()
    )
    assert result["stop_conditions"] == []
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
        item["durability_mode"] == "structured-v2-record-chain"
        and item["record_run_ids"]
        and all(item["record_heads"])
        for item in journeys.values()
    )
    assert all("manual_state_interventions" not in item for item in journeys.values())
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


def test_baseline_prefix_completion_rejects_any_later_physical_effect(
    tmp_path: Path,
) -> None:
    run_id = "s5b-non-prefix"
    state = crash_harness._production_state(tmp_path, run_id)
    injector = crash_harness.CrashInjector(
        "ledger", crash_harness.SideEffectBoundaryPhase.BEFORE_INTENT
    )
    driver = crash_harness._production_driver(tmp_path, injector)
    bridge = crash_harness.ArtifactBridge(
        crash_harness.ArtifactStore(tmp_path, run_id),
        now=lambda: crash_harness.FIXED_TIME,
    )
    driver._artifact_bridge = bridge

    with pytest.raises(crash_harness.InjectedCrash):
        driver.bind_work_unit(state)

    content = b"physical effect after an incomplete baseline"
    target = tmp_path / "physical-effect.txt"
    digest = crash_harness.sha256_bytes(content)
    spec = crash_harness.SideEffectSpec(
        "file_write", "run", ("physical-effect.txt", digest), state.task_digest
    )
    physical_execution_count = 0

    def perform() -> tuple[None, str]:
        nonlocal physical_execution_count
        physical_execution_count += 1
        target.write_bytes(content)
        return None, digest

    crash_harness.SideEffectExecutor(bridge).execute(
        spec,
        reconcile=lambda: crash_harness.Reconciliation(
            crash_harness.ReconciliationOutcome.NOT_OCCURRED
        ),
        perform=perform,
    )
    resumed = crash_harness._production_driver(tmp_path)
    resumed._artifact_bridge = bridge

    with pytest.raises(crash_harness.ArtifactResumeError, match="RECORD-MISSING"):
        resumed.bind_work_unit(state)
    assert physical_execution_count == 1
    assert target.read_bytes() == content


def test_every_exact_canonical_baseline_record_cut_converges(tmp_path: Path) -> None:
    run_id = "s5b-every-prefix-cut"
    canonical_root = tmp_path / "canonical"
    canonical_state = crash_harness._production_state(canonical_root, run_id)
    canonical_driver = crash_harness._production_driver(canonical_root)
    canonical_driver._artifact_bridge = crash_harness.ArtifactBridge(
        crash_harness.ArtifactStore(canonical_root, run_id),
        now=lambda: crash_harness.FIXED_TIME,
    )
    canonical_driver.bind_work_unit(canonical_state)
    canonical = crash_harness.ArtifactStore(canonical_root, run_id).load_chain()
    assert len(canonical) > 5

    for cut in range(1, len(canonical)):
        case_root = tmp_path / f"cut-{cut:02d}"
        state = crash_harness._production_state(case_root, run_id)
        store = crash_harness.ArtifactStore(case_root, run_id)
        for record in canonical[:cut]:
            store.put(record)
        driver = crash_harness._production_driver(case_root)
        driver._artifact_bridge = crash_harness.ArtifactBridge(
            store, now=lambda: crash_harness.FIXED_TIME
        )

        driver.bind_work_unit(state)

        assert store.load_chain() == canonical
        assert crash_harness.resolve_resume_state(
            case_root, run_id
        ).state.current_step is crash_harness.WorkflowStep.CODEX_IMPLEMENTATION


def test_baseline_prefix_completion_rejects_later_workflow_fact(
    tmp_path: Path,
) -> None:
    run_id = "s5b-later-workflow-fact"
    state = crash_harness._production_state(tmp_path, run_id)
    injector = crash_harness.CrashInjector(
        "ledger", crash_harness.SideEffectBoundaryPhase.BEFORE_INTENT
    )
    driver = crash_harness._production_driver(tmp_path, injector)
    bridge = crash_harness.ArtifactBridge(
        crash_harness.ArtifactStore(tmp_path, run_id),
        now=lambda: crash_harness.FIXED_TIME,
    )
    driver._artifact_bridge = bridge
    with pytest.raises(crash_harness.InjectedCrash):
        driver.bind_work_unit(state)
    bridge.append(
        WorkflowPolicyPayload("2", 0, 99),
        logical_id="workflow-policy-2",
        idempotency_key="workflow-policy:2:1",
        fingerprint_sha256=state.task_digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )

    resumed = crash_harness._production_driver(tmp_path)
    resumed._artifact_bridge = bridge
    with pytest.raises(
        (crash_harness.ArtifactResumeError, ArtifactReplayError)
    ):
        resumed.bind_work_unit(state)


@pytest.mark.parametrize("near_miss", ["idempotency", "fingerprint-kind"])
def test_baseline_prefix_completion_rejects_near_miss_profile(
    tmp_path: Path, near_miss: str
) -> None:
    run_id = f"s5b-near-miss-{near_miss}"
    canonical_root = tmp_path / "canonical"
    state = crash_harness._production_state(canonical_root, run_id)
    driver = crash_harness._production_driver(canonical_root)
    driver._artifact_bridge = crash_harness.ArtifactBridge(
        crash_harness.ArtifactStore(canonical_root, run_id),
        now=lambda: crash_harness.FIXED_TIME,
    )
    driver.bind_work_unit(state)
    canonical = crash_harness.ArtifactStore(canonical_root, run_id).load_chain()

    case_root = tmp_path / "case"
    case_state = crash_harness._production_state(case_root, run_id)
    store = crash_harness.ArtifactStore(case_root, run_id)
    store.put(canonical[0])
    store.put(canonical[1])
    profile = canonical[2]
    if near_miss == "idempotency":
        profile = replace(profile, idempotency_key="run-profile-near-miss")
    else:
        profile = replace(
            profile,
            fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, state.task_digest),
        )
    store.put(profile)
    resumed = crash_harness._production_driver(case_root)
    resumed._artifact_bridge = crash_harness.ArtifactBridge(
        store, now=lambda: crash_harness.FIXED_TIME
    )

    with pytest.raises(crash_harness.ArtifactResumeError):
        resumed.bind_work_unit(case_state)


def test_harness_result_is_byte_stable_and_self_bound(tmp_path: Path) -> None:
    repository = _tracked_repository_snapshot(tmp_path / "repository")
    manifest = repository / MANIFEST.relative_to(ROOT)
    first = _run_snapshot_harness(
        repository,
        work_root=tmp_path / "first",
        manifest=manifest,
    )
    second = _run_snapshot_harness(
        repository,
        work_root=tmp_path / "second",
        manifest=manifest,
    )

    assert first == second
    document = json.loads(first)
    expected_measured_sources = tuple(
        sorted(
            (
                *(
                    path.relative_to(repository).as_posix()
                    for path in (repository / "schemas").rglob("*.json")
                ),
                *(
                    path.relative_to(repository).as_posix()
                    for path in (repository / "src").rglob("*.py")
                ),
                "scripts/crash_harness.py",
                "tests/fixtures/crash_harness/manifest-v1.json",
            )
        )
    )
    assert document["measured_source_paths"] == list(expected_measured_sources)
    for ignored_root in ("inbox", "outbox", ".orchestrator"):
        assert not (repository / ignored_root).exists()
    digest = document.pop("artifact_sha256")
    from artifact_models import canonical_json

    assert digest == hashlib.sha256(canonical_json(document)).hexdigest()

    baseline_document = json.loads(first)
    excluded_paths = (
        Path("inbox/b34-ignored.txt"),
        Path("outbox/b34-ignored.txt"),
        Path(".orchestrator/b34-ignored.txt"),
        Path("docs/notiz.md"),
    )
    for relative in excluded_paths:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("must not affect implementation digest\n", encoding="utf-8")
    outside = json.loads(
        _run_snapshot_harness(
            repository,
            work_root=tmp_path / "outside",
            manifest=manifest,
        )
    )
    assert (
        outside["harness_implementation_sha256"]
        == baseline_document["harness_implementation_sha256"]
    )
    assert outside["measured_source_paths"] == baseline_document["measured_source_paths"]

    untracked_module = repository / "src/b34_untracked_digest_probe.py"
    untracked_module.write_text("VALUE = 'measured'\n", encoding="utf-8")
    with_module = json.loads(
        _run_snapshot_harness(
            repository,
            work_root=tmp_path / "with-module",
            manifest=manifest,
        )
    )
    assert (
        with_module["harness_implementation_sha256"]
        != baseline_document["harness_implementation_sha256"]
    )
    assert "src/b34_untracked_digest_probe.py" in with_module[
        "measured_source_paths"
    ]


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
        (ROOT / "src/workflow_baseline.py").read_text(encoding="utf-8")
    )
    baseline_writer_names = {
        "_persist_structured_baseline",
        "_append_baseline_identity_and_ledger",
        "_append_completed_internal_effects",
    }
    baseline_writers = tuple(
        node
        for node in ast.walk(production_tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in baseline_writer_names
    )
    assert {node.name for node in baseline_writers} == baseline_writer_names
    record_only_specs = {
        call.args[0].value
        for writer in baseline_writers
        for call in ast.walk(writer)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "SideEffectSpec"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    executor_calls = tuple(
        call
        for writer in baseline_writers
        for call in ast.walk(writer)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "side_effect_executor"
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
