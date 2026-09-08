from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import artifact_resume
import artifact_store as artifact_store_module
from artifact_bridge import ArtifactBridge
from artifact_resume import ArtifactResumeError, resolve_resume_state
from artifact_models import FingerprintKind, canonical_json
from artifact_replay import STATE_PROJECTION_REDUCER_VERSION
from artifact_store import ArtifactStore
from orchestrator import OrchestratorConfig, ProductionWorkflowDriver
from state_io import (
    STATE_PROJECTION_CACHE_FORMAT,
    StateSchemaError,
    load_resumable_workflow_state,
    load_workflow_state,
    write_workflow_state_projection,
)
from workflow_state import (
    ProtocolBinding,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    init_workflow_state,
)


ROOT = Path(__file__).resolve().parents[1]


def test_retired_resume_module_name_is_absent_from_versioned_tree() -> None:
    retired_name = b"artifact_" + b"migration"
    archived_history = "docs/internal/" + "archive/"
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    hits: list[str] = []
    for raw_path in listed.split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8")
        if relative.startswith(("inbox/backlog/", archived_history)):
            continue
        path = ROOT / relative
        if path.is_file() and retired_name in path.read_bytes():
            hits.append(relative)

    assert not hits, "retired resume module name remains in: " + ", ".join(hits)


def _record_run(
    repository: Path,
    *,
    run_id: str = "cutover-run",
    task_file: Path | None = None,
) -> tuple[WorkflowState, WorkflowState, ProductionWorkflowDriver]:
    task = task_file or repository / "task.md"
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_text("task", encoding="utf-8")
    state = init_workflow_state(
        run_id=run_id,
        task_file=str(task),
        branch="feature/cutover",
        branch_base="b" * 40,
        first_slice_start_commit="b" * 40,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/cutover.py",),
        target_branch="feature/cutover",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="b" * 40,
        scope_paths=("src/cutover.py",),
        start_fingerprint="c" * 64,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.bind_work_unit(state)
    assert driver.active_state is not None
    return state, driver.active_state, driver


def _record_bytes(repository: Path, run_id: str) -> dict[str, bytes]:
    records = repository / ".orchestrator" / "artifacts" / run_id / "records"
    return {
        path.name: path.read_bytes()
        for path in sorted(records.glob("*.json"))
    }


def _set_path(document: dict, path: tuple[str, ...], value: object) -> None:
    cursor = document
    for key in path[:-1]:
        child = cursor.get(key)
        if not isinstance(child, dict):
            child = {}
            cursor[key] = child
        cursor = child
    cursor[path[-1]] = value


def test_resume_projects_records_and_uses_state_only_as_run_locator(
    tmp_path: Path,
) -> None:
    locator, projected, _driver = _record_run(tmp_path)
    damaged_locator = replace(
        locator,
        branch="feature/cache-lie",
        branch_base="f" * 40,
        task_digest="d" * 64,
        task_scope_patterns=("src/cache-lie.py",),
        target_branch="feature/cache-lie",
    )

    resolved = resolve_resume_state(tmp_path, damaged_locator)

    assert resolved.state == projected
    assert resolved.state.branch == "feature/cutover"
    assert resolved.state.task_scope_patterns == ("src/cutover.py",)


def test_process_local_resolution_is_warm_but_explicit_resume_fully_reloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    locator, projected, driver = _record_run(tmp_path)
    assert driver._artifact_bridge is not None
    store = driver._artifact_bridge.store
    assert store.current_chain()
    original_read = artifact_store_module._read_record
    reads = 0

    def counted_read(path: Path):  # type: ignore[no-untyped-def]
        nonlocal reads
        reads += 1
        return original_read(path)

    monkeypatch.setattr(artifact_store_module, "_read_record", counted_read)

    warm = resolve_resume_state(tmp_path, locator, validated_store=store)
    assert warm.state == projected
    assert reads == 0

    resumed = resolve_resume_state(tmp_path, locator.run_id)
    assert resumed.state == projected
    assert reads == len(store.current_chain())


def test_driver_configures_artifact_phase_progress_threshold(tmp_path: Path) -> None:
    task = tmp_path / "task.md"
    task.write_text("task", encoding="utf-8")
    state = init_workflow_state(
        run_id="configured-progress",
        task_file=str(task),
        branch="feature/cutover",
        branch_base="b" * 40,
        first_slice_start_commit="b" * 40,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/cutover.py",),
        target_branch="feature/cutover",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    )
    driver = ProductionWorkflowDriver(
        repository_root=tmp_path,
        state_file=tmp_path / ".orchestrator" / "state.json",
        agents={},
        config=OrchestratorConfig(
            repo_root=tmp_path, phase_progress_threshold_seconds=0.25
        ),
        allowed_roots=(tmp_path,),
    )

    driver._bind_artifact_store(state)

    assert driver._artifact_bridge is not None
    assert driver._artifact_bridge.store.progress_threshold_seconds == 0.25


def test_authoritative_side_effect_result_projects_without_a_mirror_write(
    tmp_path: Path,
) -> None:
    locator, _projected, _driver = _record_run(tmp_path)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, locator.run_id))
    operation = ("result.json", "d" * 64)
    intent, _ = bridge.record_side_effect_intent(
        effect_class="file_write",
        work_unit_id=locator.current_work_unit_id,
        operation=operation,
        fingerprint_sha256="d" * 64,
    )
    result = bridge.record_side_effect_result(
        effect_class="file_write",
        work_unit_id=locator.current_work_unit_id,
        operation=operation,
        result="d" * 64,
        fingerprint_sha256="d" * 64,
    )

    resolved = resolve_resume_state(tmp_path, locator.run_id)

    assert resolved.state.current_work_unit.completed_side_effects[-1] == (
        result.payload.effect_key
    )
    assert intent.payload.effect_key == result.payload.effect_key


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("cache_format",), "foreign-cache"),
        (("record_head_id",), "foreign-head"),
        (("reducer_version",), "foreign-reducer"),
        (("projection_digest",), "0" * 64),
        (("state", "version"), 99),
        (("state", "run_id"), "wrong-run"),
        (("state", "task_file"), "missing-task.md"),
        (("state", "branch"), "feature/cache-lie"),
        (("state", "branch_base"), "f" * 40),
        (("state", "created_at"), "1900-01-01T00:00:00+00:00"),
        (("state", "updated_at"), "2999-01-01T00:00:00+00:00"),
        (("state", "current_slice_id"), 999),
        (("state", "current_work_unit_id"), 999),
        (("state", "current_step"), "completed"),
        (("state", "slices"), []),
        (("state", "work_units"), []),
        (("state", "planned_slices"), [{"invented": True}]),
        (("state", "runtime_history"), {"invented": True}),
        (("state", "task_digest"), "e" * 64),
        (("state", "execution_mode"), "PLAN_ONLY"),
        (("state", "task_scope_patterns"), ["src/cache-lie.py"]),
        (("state", "work_plan_path"), "docs/cache-lie.md"),
        (("state", "approved_plan_commit"), "e" * 40),
        (("state", "finding_handoff_source_run_id"), "foreign-source"),
        (("state", "finding_handoff_export_record_id"), "ar1-" + "e" * 64),
        (("state", "audit_report_path"), "docs/cache-lie.md"),
        (("state", "target_branch"), "feature/cache-lie"),
        (("state", "protocol_binding", "mode"), "legacy-state-v3"),
        (("state", "protocol_binding", "schema_version"), "999"),
        (("state", "protocol_binding", "codex_profile", "model"), "cache-model"),
        (("state", "protocol_binding", "claude_profile", "effort"), "low"),
        (("state", "bootstrap_checks"), [{"invented": True}]),
    ),
)
def test_deleted_stale_or_manipulated_cache_is_reprojected_without_record_write(
    tmp_path: Path,
    path: tuple[str, ...],
    value: object,
) -> None:
    locator, projected, driver = _record_run(tmp_path)
    resolution = resolve_resume_state(tmp_path, locator.run_id)
    write_workflow_state_projection(
        driver.state_file, resolution, allowed_roots=(tmp_path,)
    )
    document = json.loads(driver.state_file.read_text(encoding="utf-8"))
    _set_path(document, path, value)
    driver.state_file.write_text(json.dumps(document), encoding="utf-8")
    before = _record_bytes(tmp_path, locator.run_id)

    loaded = load_resumable_workflow_state(
        driver.state_file,
        repository_root=tmp_path,
        allowed_roots=(tmp_path,),
        expected_run_id=locator.run_id,
        expected_task_file=Path(projected.task_file),
        expected_task_digest=projected.task_digest,
    )

    assert loaded == projected
    assert _record_bytes(tmp_path, locator.run_id) == before
    repaired = json.loads(driver.state_file.read_text(encoding="utf-8"))
    assert repaired["cache_format"] == STATE_PROJECTION_CACHE_FORMAT
    assert repaired["record_head_id"] == resolution.record_head_id
    assert repaired["reducer_version"] == STATE_PROJECTION_REDUCER_VERSION
    assert load_workflow_state(driver.state_file, allowed_roots=(tmp_path,)) == projected


def test_deleted_cache_discovers_one_task_bound_run_and_recreates_it(
    tmp_path: Path,
) -> None:
    locator, projected, driver = _record_run(tmp_path)
    assert not driver.state_file.exists()
    before = _record_bytes(tmp_path, locator.run_id)

    loaded = load_resumable_workflow_state(
        driver.state_file,
        repository_root=tmp_path,
        allowed_roots=(tmp_path,),
        expected_task_file=Path(projected.task_file),
        expected_task_digest=projected.task_digest,
    )

    assert loaded == projected
    assert driver.state_file.is_file()
    assert _record_bytes(tmp_path, locator.run_id) == before


def test_deleted_cache_rejects_ambiguous_task_bound_runs(tmp_path: Path) -> None:
    task = tmp_path / "task.md"
    _record_run(tmp_path, run_id="cutover-one", task_file=task)
    _record_run(tmp_path, run_id="cutover-two", task_file=task)

    with pytest.raises(ArtifactResumeError, match="not unique"):
        load_resumable_workflow_state(
            tmp_path / ".orchestrator" / "state.json",
            repository_root=tmp_path,
            allowed_roots=(tmp_path,),
            expected_task_file=task,
            expected_task_digest="a" * 64,
        )


def test_deleted_cache_fails_closed_when_any_candidate_chain_is_corrupt(
    tmp_path: Path,
) -> None:
    task = tmp_path / "task.md"
    corrupt, _projected, _driver = _record_run(
        tmp_path, run_id="cutover-corrupt", task_file=task
    )
    _record_run(tmp_path, run_id="cutover-valid", task_file=task)
    store = ArtifactStore(tmp_path, corrupt.run_id)
    first_record = store.load_chain()[0]
    path = store.records_dir / f"{first_record.record_id}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["content_sha256"] = "0" * 64
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
        ArtifactResumeError,
        match="record run discovery encountered an invalid candidate",
    ):
        load_resumable_workflow_state(
            tmp_path / ".orchestrator" / "state.json",
            repository_root=tmp_path,
            allowed_roots=(tmp_path,),
            expected_task_file=task,
            expected_task_digest="a" * 64,
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"branch": "feature/cache-lie", "target_branch": "feature/cache-lie"},
        {"branch_base": "f" * 40},
        {"task_file": "cache-lie.md"},
        {"task_digest": "e" * 64},
        {"task_scope_patterns": ("src/cache-lie.py",)},
        {"audit_report_path": "docs/internal/cache-lie.md"},
        {"created_at": "1900-01-01T00:00:00+00:00"},
        {"updated_at": "2999-01-01T00:00:00+00:00"},
        {"runtime_history": {"invented": True}},
    ),
)
def test_valid_but_stale_locator_fields_never_change_projection(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    locator, projected, _driver = _record_run(tmp_path)
    stale = replace(locator, **changes)

    assert resolve_resume_state(tmp_path, stale).state == projected


@pytest.mark.parametrize(
    "mutation",
    (
        "schema_version",
        "record_id",
        "record_type",
        "run_id",
        "logical_id",
        "status",
        "fingerprint",
        "predecessor_ids",
        "idempotency_key",
        "payload",
    ),
)
def test_unknown_or_mutated_record_bytes_remain_fail_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    locator, _projected, _driver = _record_run(tmp_path)
    store = ArtifactStore(tmp_path, locator.run_id)
    first = store.load_chain()[0]
    path = store.records_dir / f"{first.record_id}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    replacements = {
        "schema_version": "foreign-reducer",
        "record_id": "ar1-" + "f" * 64,
        "record_type": "foreign-record",
        "run_id": "foreign-run",
        "logical_id": "foreign-logical-id",
        "status": "foreign-status",
        "fingerprint": {"kind": "contract", "sha256": "f" * 64},
        "predecessor_ids": ["ar1-" + "f" * 64],
        "idempotency_key": "foreign-idempotency",
        "payload": {"foreign": True},
    }
    document[mutation] = replacements[mutation]
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ArtifactResumeError, match="RECORD-UNKNOWN"):
        resolve_resume_state(tmp_path, locator.run_id)


@pytest.mark.parametrize(
    "field",
    (
        "task_file",
        "branch",
        "branch_base",
        "created_at",
        "updated_at",
        "current_slice_id",
        "current_work_unit_id",
        "current_step",
        "slices",
        "work_units",
        "planned_slices",
        "runtime_history",
        "task_digest",
        "execution_mode",
        "task_scope_patterns",
        "work_plan_path",
        "approved_plan_commit",
        "finding_handoff_source_run_id",
        "finding_handoff_export_record_id",
        "audit_report_path",
        "target_branch",
        "protocol_binding",
        "bootstrap_checks",
    ),
)
def test_resume_source_has_no_workflow_cache_field_reader(field: str) -> None:
    source = Path(artifact_resume.__file__).read_text(encoding="utf-8")

    assert f"state.{field}" not in source
    assert f"state_or_run_id.{field}" not in source


def test_cutover_source_has_no_specialized_recoverable_or_mismatch_language() -> None:
    source = Path(artifact_resume.__file__).read_text(encoding="utf-8")

    assert "_recoverable_" not in source
    assert "differs from state-v3" not in source
    assert "def mismatch" not in source


def test_missing_record_run_is_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ArtifactResumeError, match="RECORD-MISSING"):
        resolve_resume_state(tmp_path, "missing-run")


def test_foreign_cache_reducer_is_rejected_by_direct_cache_reader(
    tmp_path: Path,
) -> None:
    locator, _projected, driver = _record_run(tmp_path)
    write_workflow_state_projection(
        driver.state_file,
        resolve_resume_state(tmp_path, locator.run_id),
        allowed_roots=(tmp_path,),
    )
    document = json.loads(driver.state_file.read_text(encoding="utf-8"))
    document["reducer_version"] = "foreign-reducer"
    driver.state_file.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(StateSchemaError, match="reducer is unsupported"):
        load_workflow_state(driver.state_file, allowed_roots=(tmp_path,))


def test_record_chain_with_foreign_bound_reducer_is_rejected(
    tmp_path: Path,
) -> None:
    locator, _projected, _driver = _record_run(tmp_path)
    store = ArtifactStore(tmp_path, locator.run_id)
    profile = next(
        record
        for record in store.load_chain()
        if record.record_type.value == "run_profile"
    )
    path = store.records_dir / f"{profile.record_id}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["record"]["payload"]["reducer_version"] = "foreign-reducer"
    document["content_sha256"] = hashlib.sha256(
        canonical_json(document["record"])
    ).hexdigest()
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ArtifactResumeError, match="reducer_version"):
        resolve_resume_state(tmp_path, locator.run_id)
