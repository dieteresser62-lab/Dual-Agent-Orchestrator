from __future__ import annotations

from pathlib import Path

from state_io import (
    atomic_write_file,
    ensure_state_shape,
    init_state,
    load_cycle_checkpoint,
    new_run_id,
    read_file,
    write_cycle_checkpoint,
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


def test_ensure_state_shape_reinitializes_non_v2_state(tmp_path: Path) -> None:
    old_state = {"version": 1, "phase": "phase2"}
    shaped = ensure_state_shape(old_state, Path("task.md"), 1, 1, tmp_path)

    assert shaped["version"] == 2
    assert shaped["phase1"]["cycle"] == 0
    assert shaped["phase2"]["cycle"] == 0


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
