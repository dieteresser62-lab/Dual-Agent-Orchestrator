from __future__ import annotations

from pathlib import Path

import json
from dataclasses import replace

import pytest

from artifact_migration import ArtifactResumeError

from state_io import (
    ActiveV2StateError,
    CompletedV2State,
    StatePathError,
    StateSchemaError,
    UnknownStateVersionError,
    atomic_write_file,
    ensure_state_shape,
    init_state,
    load_cycle_checkpoint,
    load_resumable_workflow_state,
    load_workflow_checkpoint,
    load_workflow_state,
    new_run_id,
    read_file,
    save_workflow_state,
    workflow_checkpoint_path,
    write_cycle_checkpoint,
    write_workflow_checkpoint,
)
from workflow_state import (
    ProtocolBinding,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    init_workflow_state,
)


def test_init_state_has_expected_defaults(tmp_path: Path) -> None:
    artifacts = {
        "run_id": "r1",
        "run_dir": str(tmp_path / "runs" / "r1"),
        "task": str(tmp_path / "runs" / "r1" / "00_task.md"),
        "phase1_shared": str(tmp_path / "runs" / "r1" / "10_phase1_plan.md"),
        "phase2_shared": str(tmp_path / "runs" / "r1" / "20_phase2_implementation.md"),
    }
    state = init_state(Path("task.md"), 3, 4, artifacts)

    assert state["version"] == 2
    assert state["phase"] == "phase1"
    assert state["phase1"]["max_cycles"] == 3
    assert state["phase2"]["max_cycles"] == 4
    assert state["phase1"]["open_findings"] == []
    assert state["phase2"]["open_findings"] == []


def test_ensure_state_shape_keeps_v2_and_sanitizes_findings(tmp_path: Path) -> None:
    state = {
        "version": 2,
        "task_file": "task.md",
        "phase": "phase1",
        "updated_at": "now",
        "artifacts": {},
        "phase1": {
            "open_findings": ["f-001", "BAD"],
            "finding_history": {"f-001": "open", "X": "OPEN"},
        },
        "phase2": {
            "open_findings": ["F-002", "invalid"],
            "finding_history": {"f-002": "closed", "y": "OPEN"},
        },
    }

    shaped = ensure_state_shape(state, Path("task.md"), 2, 2, tmp_path)
    assert shaped["phase1"]["open_findings"] == ["F-001"]
    assert shaped["phase1"]["finding_history"] == {"F-001": "OPEN"}
    assert shaped["phase2"]["open_findings"] == ["F-002"]
    assert shaped["phase2"]["finding_history"] == {"F-002": "CLOSED"}
    assert shaped["artifacts"]["run_id"]


def test_ensure_state_shape_rejects_non_v2_state_without_mutation(tmp_path: Path) -> None:
    old_state = {"version": 1, "phase": "phase2"}
    original = old_state.copy()

    with pytest.raises(UnknownStateVersionError, match="left unchanged"):
        ensure_state_shape(old_state, Path("task.md"), 1, 1, tmp_path)

    assert old_state == original


def test_ensure_state_shape_rejects_incomplete_v2_state_without_mutation(
    tmp_path: Path,
) -> None:
    incomplete_state = {"version": 2, "phase1": {"status": "running"}}
    original = {"version": 2, "phase1": {"status": "running"}}

    with pytest.raises(StateSchemaError, match="missing phase1 or phase2 data"):
        ensure_state_shape(incomplete_state, Path("task.md"), 1, 1, tmp_path)

    assert incomplete_state == original


def test_atomic_write_file_and_read_file_roundtrip(tmp_path: Path) -> None:
    file_path = tmp_path / "a" / "b.txt"
    atomic_write_file(file_path, "hello\nworld")

    assert file_path.exists()
    assert read_file(file_path) == "hello\nworld"


def test_read_file_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert read_file(tmp_path / "missing.txt") == ""


def test_cycle_checkpoint_roundtrip(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "checkpoints"
    state = {"phase": "phase1", "phase1": {"cycle": 1}}

    path = write_cycle_checkpoint(checkpoint_dir, "phase1", 1, state)
    loaded = load_cycle_checkpoint(checkpoint_dir, "phase1", 1)

    assert path.exists()
    assert loaded == state


def test_cycle_checkpoint_missing_returns_none(tmp_path: Path) -> None:
    assert load_cycle_checkpoint(tmp_path / "checkpoints", "phase2", 5) is None


def test_new_run_id_format() -> None:
    run_id = new_run_id()
    assert len(run_id) == 16
    assert run_id[8] == "-"
    assert run_id.endswith("Z")


def test_ensure_state_shape_accepts_paths_under_artifact_root(tmp_path: Path) -> None:
    artifact_runs_dir = tmp_path / "artifacts" / "runs"
    run_dir = artifact_runs_dir / "r-fixed"
    task_file = tmp_path / "task.md"
    task_file.write_text("x", encoding="utf-8")
    state = {
        "version": 2,
        "task_file": str(task_file),
        "phase": "phase1",
        "updated_at": "now",
        "artifacts": {
            "run_id": "r-fixed",
            "run_dir": str(run_dir),
            "task": str(run_dir / "00_task.md"),
            "phase1_shared": str(run_dir / "10_phase1_plan.md"),
            "phase2_shared": str(run_dir / "20_phase2_implementation.md"),
        },
        "phase1": {"open_findings": [], "finding_history": {}},
        "phase2": {"open_findings": [], "finding_history": {}},
    }

    shaped = ensure_state_shape(state, task_file, 2, 2, artifact_runs_dir)
    assert shaped["artifacts"]["run_id"] == "r-fixed"
    assert shaped["artifacts"]["run_dir"] == str(run_dir.resolve())


def test_ensure_state_shape_accepts_paths_under_cwd(monkeypatch, tmp_path: Path) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    artifact_runs_dir = tmp_path / "artifacts" / "runs"
    run_dir = cwd / "local-run"
    task_file = cwd / "task.md"
    task_file.write_text("x", encoding="utf-8")
    state = {
        "version": 2,
        "task_file": str(task_file),
        "phase": "phase1",
        "updated_at": "now",
        "artifacts": {
            "run_id": "cwd-run",
            "run_dir": str(run_dir),
            "task": str(run_dir / "00_task.md"),
            "phase1_shared": str(run_dir / "10_phase1_plan.md"),
            "phase2_shared": str(run_dir / "20_phase2_implementation.md"),
        },
        "phase1": {"open_findings": [], "finding_history": {}},
        "phase2": {"open_findings": [], "finding_history": {}},
    }

    shaped = ensure_state_shape(state, task_file, 2, 2, artifact_runs_dir)
    assert shaped["artifacts"]["run_id"] == "cwd-run"
    assert shaped["artifacts"]["run_dir"] == str(run_dir.resolve())
    assert shaped["task_file"] == str(task_file.resolve())


def test_ensure_state_shape_rejects_traversal_and_regenerates_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    artifact_runs_dir = tmp_path / "artifacts" / "runs"
    task_file = cwd / "task.md"
    task_file.write_text("x", encoding="utf-8")
    bad_path = "../outside/evil-path"
    state = {
        "version": 2,
        "task_file": bad_path,
        "phase": "phase1",
        "updated_at": "now",
        "artifacts": {
            "run_id": "bad-run",
            "run_dir": bad_path,
            "task": bad_path,
            "phase1_shared": bad_path,
            "phase2_shared": bad_path,
        },
        "phase1": {"open_findings": [], "finding_history": {}},
        "phase2": {"open_findings": [], "finding_history": {}},
    }

    shaped = ensure_state_shape(state, task_file, 2, 2, artifact_runs_dir)
    assert shaped["artifacts"]["run_id"] != "bad-run"
    assert Path(str(shaped["artifacts"]["run_dir"])).is_relative_to(artifact_runs_dir.parent.resolve())
    assert shaped["task_file"] == str(task_file.resolve())


def make_v3_state(tmp_path: Path) -> WorkflowState:
    task_file = tmp_path / "task.md"
    task_file.write_text("task", encoding="utf-8")
    return init_workflow_state(
        run_id="run-v3",
        task_file=str(task_file),
        branch="feature/state-v3",
        branch_base="b" * 40,
        slice_count=2,
        timestamp="2026-08-11T12:00:00+00:00",
    )


def test_workflow_state_atomic_roundtrip(tmp_path: Path) -> None:
    state_file = tmp_path / ".orchestrator" / "state.json"
    state = make_v3_state(tmp_path)

    save_workflow_state(state_file, state, allowed_roots=(tmp_path,))
    loaded = load_workflow_state(state_file, allowed_roots=(tmp_path,))

    assert loaded == state
    assert list(state_file.parent.glob(".state.json.*.tmp")) == []


def test_failed_atomic_workflow_write_preserves_previous_state(
    monkeypatch, tmp_path: Path
) -> None:
    state_file = tmp_path / "state.json"
    state = make_v3_state(tmp_path)
    save_workflow_state(state_file, state, allowed_roots=(tmp_path,))
    original = state_file.read_bytes()
    changed = state.mark_side_effect_completed("effect:one", updated_at="later")

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("state_io.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        save_workflow_state(state_file, changed, allowed_roots=(tmp_path,))

    assert state_file.read_bytes() == original
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_workflow_state_paths_are_root_bound(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    state = make_v3_state(repository)

    with pytest.raises(StatePathError, match="not allowed"):
        save_workflow_state(
            tmp_path / "outside" / "state.json",
            state,
            allowed_roots=(repository,),
        )

    outside_task = tmp_path / "outside-task.md"
    outside_task.write_text("x", encoding="utf-8")
    invalid_state = replace(state, task_file=str(outside_task))
    with pytest.raises(StateSchemaError, match="refusing to save"):
        save_workflow_state(
            repository / "state.json",
            invalid_state,
            allowed_roots=(repository,),
        )


def test_completed_v2_state_is_recognized_without_rewrite(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    raw = {
        "version": 2,
        "phase": "done",
        "task_file": "task.md",
        "phase2": {"status": "completed", "completed_at": "finished"},
    }
    state_file.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    before = state_file.read_bytes()

    loaded = load_workflow_state(state_file, allowed_roots=(tmp_path,))

    assert loaded == CompletedV2State(
        version=2,
        phase="done",
        task_file="task.md",
        completed_at="finished",
    )
    assert state_file.read_bytes() == before


@pytest.mark.parametrize(
    ("phase", "phase1_status", "phase2_status"),
    [
        ("phase1", "running", "pending"),
        ("phase2", "completed", "frozen"),
    ],
)
def test_active_or_frozen_v2_state_is_rejected_without_rewrite(
    tmp_path: Path, phase: str, phase1_status: str, phase2_status: str
) -> None:
    state_file = tmp_path / "state.json"
    raw = {
        "version": 2,
        "phase": phase,
        "phase1": {"status": phase1_status},
        "phase2": {"status": phase2_status},
    }
    state_file.write_text(json.dumps(raw), encoding="utf-8")
    before = state_file.read_bytes()

    with pytest.raises(ActiveV2StateError, match="explicitly"):
        load_workflow_state(state_file, allowed_roots=(tmp_path,))

    assert state_file.read_bytes() == before


@pytest.mark.parametrize(
    "raw",
    [{"version": 99}, {"version": 2.0, "phase": "done"}, {"phase": "done"}],
)
def test_unknown_state_version_is_rejected_without_rewrite(tmp_path: Path, raw: dict) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(raw), encoding="utf-8")
    before = state_file.read_bytes()

    with pytest.raises(UnknownStateVersionError, match="left unchanged"):
        load_workflow_state(state_file, allowed_roots=(tmp_path,))

    assert state_file.read_bytes() == before


def test_invalid_v3_state_fails_loudly_without_rewrite(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    raw = make_v3_state(tmp_path).to_dict()
    raw["current_slice_id"] = 0
    state_file.write_text(json.dumps(raw), encoding="utf-8")
    before = state_file.read_bytes()

    with pytest.raises(StateSchemaError, match="invalid version-3"):
        load_workflow_state(state_file, allowed_roots=(tmp_path,))

    assert state_file.read_bytes() == before


def test_workflow_checkpoint_name_contains_all_one_based_coordinates(tmp_path: Path) -> None:
    checkpoint = workflow_checkpoint_path(
        tmp_path,
        work_unit_id=2,
        slice_id=7,
        round_number=3,
    )
    other = workflow_checkpoint_path(
        tmp_path,
        work_unit_id=2,
        slice_id=8,
        round_number=3,
    )

    assert checkpoint.name == "work-unit-0002-slice-0007-round-0003.json"
    assert checkpoint != other
    with pytest.raises(StateSchemaError, match="1-based"):
        workflow_checkpoint_path(tmp_path, work_unit_id=0, slice_id=7, round_number=3)


def test_workflow_checkpoint_roundtrip_and_missing(tmp_path: Path) -> None:
    state = make_v3_state(tmp_path)
    checkpoint_dir = tmp_path / ".orchestrator" / "checkpoints"

    path = write_workflow_checkpoint(
        checkpoint_dir,
        state,
        allowed_roots=(tmp_path,),
    )
    loaded = load_workflow_checkpoint(
        checkpoint_dir,
        work_unit_id=1,
        slice_id=1,
        round_number=1,
        allowed_roots=(tmp_path,),
    )
    missing = load_workflow_checkpoint(
        checkpoint_dir,
        work_unit_id=1,
        slice_id=1,
        round_number=2,
        allowed_roots=(tmp_path,),
    )

    assert path.name == "work-unit-0001-slice-0001-round-0001.json"
    assert loaded == state
    assert missing is None


def test_bound_state_and_checkpoint_require_exact_resume_protocol(tmp_path: Path) -> None:
    binding = ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2")
    state = replace(make_v3_state(tmp_path), protocol_binding=binding)
    state_file = tmp_path / "state.json"
    checkpoint_dir = tmp_path / "checkpoints"
    save_workflow_state(state_file, state, allowed_roots=(tmp_path,))
    write_workflow_checkpoint(checkpoint_dir, state, allowed_roots=(tmp_path,))

    assert load_workflow_state(
        state_file,
        allowed_roots=(tmp_path,),
        expected_protocol_binding=binding,
    ) == state
    with pytest.raises(StateSchemaError, match="does not match"):
        load_workflow_state(
            state_file,
            allowed_roots=(tmp_path,),
            expected_protocol_binding=None,
        )
    with pytest.raises(StateSchemaError, match="does not match"):
        load_workflow_checkpoint(
            checkpoint_dir,
            work_unit_id=1,
            slice_id=1,
            round_number=1,
            allowed_roots=(tmp_path,),
            expected_protocol_binding=ProtocolBinding(
                ProtocolMode.LEGACY_STATE_V3, "3", None, None
            ),
        )


def test_resumable_loader_rejects_unbound_v3_state_as_unsupported(tmp_path: Path) -> None:
    state = make_v3_state(tmp_path)
    state_file = tmp_path / "state.json"
    save_workflow_state(state_file, state, allowed_roots=(tmp_path,))

    with pytest.raises(ArtifactResumeError, match="UNSUPPORTED-PROTOCOL"):
        load_resumable_workflow_state(
            state_file,
            repository_root=tmp_path,
            allowed_roots=(tmp_path,),
        )
    assert not (tmp_path / ".orchestrator" / "artifacts").exists()


def test_existing_state_protocol_binding_cannot_be_added_or_switched(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    legacy = make_v3_state(tmp_path)
    save_workflow_state(state_file, legacy, allowed_roots=(tmp_path,))
    with pytest.raises(StateSchemaError, match="add or change"):
        save_workflow_state(
            state_file,
            replace(
                legacy,
                protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
            ),
            allowed_roots=(tmp_path,),
        )

    state_file.unlink()
    structured = replace(
        legacy, protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2")
    )
    save_workflow_state(state_file, structured, allowed_roots=(tmp_path,))
    with pytest.raises(StateSchemaError, match="add or change"):
        save_workflow_state(
            state_file,
            replace(
                structured,
                protocol_binding=ProtocolBinding(
                    ProtocolMode.LEGACY_STATE_V3, "3", None, None
                ),
            ),
            allowed_roots=(tmp_path,),
        )


def test_existing_workflow_run_requires_exact_replacement_authorization(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "state.json"
    existing = make_v3_state(tmp_path)
    replacement = replace(
        existing,
        run_id="replacement-run",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    )
    save_workflow_state(state_file, existing, allowed_roots=(tmp_path,))

    with pytest.raises(StateSchemaError, match="matching authorization"):
        save_workflow_state(
            state_file,
            replacement,
            allowed_roots=(tmp_path,),
        )
    with pytest.raises(StateSchemaError, match="matching authorization"):
        save_workflow_state(
            state_file,
            replacement,
            allowed_roots=(tmp_path,),
            replace_existing_run_id="wrong-run",
        )

    save_workflow_state(
        state_file,
        replacement,
        allowed_roots=(tmp_path,),
        replace_existing_run_id=existing.run_id,
    )

    assert load_workflow_state(state_file, allowed_roots=(tmp_path,)) == replacement


def test_replacement_authorization_never_rebinds_protocol_within_same_run(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "state.json"
    existing = make_v3_state(tmp_path)
    save_workflow_state(state_file, existing, allowed_roots=(tmp_path,))

    with pytest.raises(StateSchemaError, match="add or change"):
        save_workflow_state(
            state_file,
            replace(
                existing,
                protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
            ),
            allowed_roots=(tmp_path,),
            replace_existing_run_id=existing.run_id,
        )


from conftest import can_symlink


@pytest.mark.skipif(not can_symlink(), reason="symlinks are unavailable")
def test_workflow_state_rejects_symlinked_storage_escape(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    outside = tmp_path / "outside"
    repository.mkdir()
    outside.mkdir()
    (repository / "linked").symlink_to(outside, target_is_directory=True)
    state = make_v3_state(repository)

    with pytest.raises(StatePathError, match="not allowed"):
        save_workflow_state(
            repository / "linked" / "state.json",
            state,
            allowed_roots=(repository,),
        )

    assert not (outside / "state.json").exists()


def test_checkpoint_rejects_identity_mismatch(tmp_path: Path) -> None:
    state = make_v3_state(tmp_path)
    checkpoint_dir = tmp_path / "checkpoints"
    wrong_path = workflow_checkpoint_path(
        checkpoint_dir,
        work_unit_id=1,
        slice_id=1,
        round_number=2,
    )
    save_workflow_state(wrong_path, state, allowed_roots=(tmp_path,))

    with pytest.raises(StateSchemaError, match="identity mismatch"):
        load_workflow_checkpoint(
            checkpoint_dir,
            work_unit_id=1,
            slice_id=1,
            round_number=2,
            allowed_roots=(tmp_path,),
        )


def test_persisted_side_effect_is_not_repeated_after_resume(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = make_v3_state(tmp_path).mark_side_effect_completed(
        "claude:review:round-1", updated_at="after-review"
    )
    save_workflow_state(state_file, state, allowed_roots=(tmp_path,))

    loaded = load_workflow_state(state_file, allowed_roots=(tmp_path,))

    assert isinstance(loaded, WorkflowState)
    assert loaded.current_step is WorkflowStep.CODEX_PLAN
    assert loaded.resume_cursor().should_execute("claude:review:round-1") is False
