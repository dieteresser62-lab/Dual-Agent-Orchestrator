from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Canonical finding identifiers exchanged by both agents, e.g. F-001.
FINDING_ID_PATTERN = re.compile(r"^F-\d{3}$")
logger = logging.getLogger(__name__)


def read_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def atomic_write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same directory, then atomically replace the target.
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def write_file(path: Path, content: str) -> None:
    atomic_write_file(path, content.strip() + "\n")


def append_markdown(path: Path, heading: str, body: str) -> None:
    stamp = now_iso()
    section = f"## {heading}\n\n_Time: {stamp}_\n\n{body.strip()}\n"
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    if existing.strip():
        # Keep an explicit separator so each cycle appends as an independent section.
        next_content = existing.rstrip() + "\n\n---\n\n" + section
    else:
        next_content = section
    atomic_write_file(path, next_content)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def build_artifact_paths(run_id: str, artifact_runs_dir: Path) -> dict[str, str | Path]:
    run_dir = artifact_runs_dir / run_id
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "task": run_dir / "00_task.md",
        "phase1_shared": run_dir / "10_phase1_plan.md",
        "phase2_shared": run_dir / "20_phase2_implementation.md",
    }


def _is_within_allowed_roots(path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    """Return True when `path` resolves inside any trusted root directory."""
    return any(path.is_relative_to(root) for root in allowed_roots)


def _validate_loaded_path(raw: str, allowed_roots: tuple[Path, ...]) -> str:
    """Validate persisted paths from state before they are reused."""
    resolved = Path(raw).resolve()
    if not _is_within_allowed_roots(resolved, allowed_roots):
        # Reject paths outside orchestrator/workspace roots to prevent state-file path injection.
        raise ValueError(f"path '{raw}' resolves outside allowed roots")
    return str(resolved)


def load_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text(encoding="utf-8"))


def save_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_file(state_file, json.dumps(state, indent=2, ensure_ascii=True) + "\n")


def init_state(
    task_file: Path,
    phase1_max_cycles: int,
    phase2_max_cycles: int,
    artifacts: dict[str, str | Path],
) -> dict:
    """Create a fresh version-2 orchestrator state document."""
    serialized_artifacts = {
        "run_id": str(artifacts["run_id"]),
        "run_dir": str(artifacts["run_dir"]),
        "task": str(artifacts["task"]),
        "phase1_shared": str(artifacts["phase1_shared"]),
        "phase2_shared": str(artifacts["phase2_shared"]),
    }
    return {
        "version": 2,
        "task_file": str(task_file),
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "phase": "phase1",
        "artifacts": serialized_artifacts,
        "phase1": {
            "status": "pending",
            "cycle": 0,
            "max_cycles": phase1_max_cycles,
            "codex_approval": "NO",
            "claude_approval": "NO",
            "open_findings": [],
            "finding_history": {},
            "error": None,
            "completed_at": None,
        },
        "phase2": {
            "status": "pending",
            "cycle": 0,
            "max_cycles": phase2_max_cycles,
            "claude_approval": "NO",
            "open_findings": [],
            "finding_history": {},
            "implementation_ready": "NO",
            "last_test_exit": None,
            "last_test_snapshot": "",
            "error": None,
            "completed_at": None,
        },
    }


def ensure_state_shape(
    state: dict,
    task_file: Path,
    phase1_max_cycles: int,
    phase2_max_cycles: int,
    artifact_runs_dir: Path,
) -> dict:
    """Normalize loaded state (schema, safe paths, and finding-id hygiene)."""

    def sanitize_phase_findings(phase_state: dict) -> None:
        raw_open = phase_state.get("open_findings", [])
        sanitized_open = [
            str(fid).upper()
            for fid in raw_open
            if FINDING_ID_PATTERN.match(str(fid).upper())
        ]
        phase_state["open_findings"] = sanitized_open

        raw_history = dict(phase_state.get("finding_history", {}))
        sanitized_history: dict[str, str] = {}
        for fid, status in raw_history.items():
            fid_up = str(fid).upper()
            if not FINDING_ID_PATTERN.match(fid_up):
                continue
            sanitized_history[fid_up] = str(status).upper()
        phase_state["finding_history"] = sanitized_history

    if state.get("version") == 2 and "phase1" in state and "phase2" in state:
        # Loaded paths are validated against known roots before reuse.
        allowed_roots = (artifact_runs_dir.parent.resolve(), Path.cwd().resolve())
        raw_task_file = str(state.get("task_file", str(task_file)))
        try:
            state["task_file"] = _validate_loaded_path(raw_task_file, allowed_roots)
        except ValueError:
            logger.warning(
                "Invalid task_file in state: %r; using CLI task file.",
                raw_task_file,
            )
            state["task_file"] = str(task_file.resolve())
        state.setdefault("phase", "phase1")
        state.setdefault("updated_at", now_iso())
        artifacts = state.setdefault("artifacts", {})
        if not artifacts.get("run_id"):
            # Backfill missing artifact metadata for older state files.
            migrated = build_artifact_paths(new_run_id(), artifact_runs_dir)
            artifacts.setdefault("run_id", str(migrated["run_id"]))
            artifacts.setdefault("run_dir", str(migrated["run_dir"]))
            artifacts.setdefault("task", str(migrated["task"]))
            artifacts.setdefault("phase1_shared", str(migrated["phase1_shared"]))
            artifacts.setdefault("phase2_shared", str(migrated["phase2_shared"]))
        else:
            try:
                artifacts["run_dir"] = _validate_loaded_path(str(artifacts["run_dir"]), allowed_roots)
                artifacts["task"] = _validate_loaded_path(str(artifacts["task"]), allowed_roots)
                artifacts["phase1_shared"] = _validate_loaded_path(
                    str(artifacts["phase1_shared"]), allowed_roots
                )
                artifacts["phase2_shared"] = _validate_loaded_path(
                    str(artifacts["phase2_shared"]), allowed_roots
                )
            except (KeyError, ValueError) as exc:
                logger.warning(
                    "Invalid artifact paths in state (reason: %s); regenerating artifact paths.",
                    exc,
                )
                # Regenerate a safe artifact bundle when persisted paths are incomplete/invalid.
                migrated = build_artifact_paths(new_run_id(), artifact_runs_dir)
                artifacts["run_id"] = str(migrated["run_id"])
                artifacts["run_dir"] = str(migrated["run_dir"])
                artifacts["task"] = str(migrated["task"])
                artifacts["phase1_shared"] = str(migrated["phase1_shared"])
                artifacts["phase2_shared"] = str(migrated["phase2_shared"])
        state["phase1"].setdefault("open_findings", [])
        state["phase1"].setdefault("finding_history", {})
        state["phase2"].setdefault("open_findings", [])
        state["phase2"].setdefault("finding_history", {})
        state["phase2"].setdefault("last_test_snapshot", "")
        sanitize_phase_findings(state["phase1"])
        sanitize_phase_findings(state["phase2"])
        return state
    return init_state(
        task_file,
        phase1_max_cycles,
        phase2_max_cycles,
        build_artifact_paths(new_run_id(), artifact_runs_dir),
    )


def checkpoint_path(checkpoint_dir: Path, phase: str, cycle: int) -> Path:
    return checkpoint_dir / f"{phase}-cycle-{cycle}.json"


def write_cycle_checkpoint(checkpoint_dir: Path, phase: str, cycle: int, state: dict) -> Path:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    path = checkpoint_path(checkpoint_dir, phase, cycle)
    atomic_write_file(path, json.dumps(state, indent=2, ensure_ascii=True) + "\n")
    return path


def load_cycle_checkpoint(checkpoint_dir: Path, phase: str, cycle: int) -> dict | None:
    path = checkpoint_path(checkpoint_dir, phase, cycle)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
