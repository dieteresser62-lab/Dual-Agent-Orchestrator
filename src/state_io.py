from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from path_policy import PathPolicyError, resolve_path_within_roots
from artifact_resume import ArtifactResumeError, ResumeResolution, resolve_resume_state
from artifact_models import canonical_json
from artifact_replay import STATE_PROJECTION_REDUCER_VERSION
from workflow_state import ProtocolBinding, WorkflowState, WorkflowStateValidationError

# Canonical finding identifiers exchanged by both agents, e.g. F-001.
FINDING_ID_PATTERN = re.compile(r"^F-\d{3}$")
logger = logging.getLogger(__name__)
_UNSPECIFIED_PROTOCOL = object()
STATE_PROJECTION_CACHE_FORMAT = "workflow-state-projection-v1"


class StateSchemaError(ValueError):
    """Raised when persisted state cannot be interpreted without guessing."""


class UnknownStateVersionError(StateSchemaError):
    """Raised for an unsupported or missing state schema version."""


class ActiveV2StateError(StateSchemaError):
    """Raised when an unfinished v2 run requires an explicit user restart choice."""


class StatePathError(StateSchemaError):
    """Raised when a state or checkpoint path escapes its configured roots."""


@dataclass(frozen=True)
class CompletedV2State:
    """Read-only recognition result for a historically completed v2 run."""

    version: int
    phase: str
    task_file: str | None
    completed_at: str | None


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
        newline="",
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, path)
    finally:
        # A failed replace must not leave an ambiguous partial-state candidate behind.
        tmp_path.unlink(missing_ok=True)


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


def _validate_loaded_path(raw: str, allowed_roots: tuple[Path, ...]) -> str:
    """Validate persisted paths from state before they are reused."""
    try:
        return str(resolve_path_within_roots(raw, allowed_roots))
    except PathPolicyError as exc:
        # Preserve the state layer's existing ValueError contract for safe fallback handling.
        raise ValueError(f"path '{raw}' is not allowed: {exc}") from exc


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

    version = state.get("version")
    if type(version) is int and version == 2 and "phase1" in state and "phase2" in state:
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
    if type(version) is int and version == 2:
        raise StateSchemaError("version-2 state is missing phase1 or phase2 data")
    raise UnknownStateVersionError(
        f"cannot resume unsupported state version {version!r}; state was left unchanged"
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


def load_workflow_state(
    state_file: Path,
    *,
    allowed_roots: tuple[Path, ...],
    expected_protocol_binding: ProtocolBinding | None | object = _UNSPECIFIED_PROTOCOL,
) -> WorkflowState | CompletedV2State | None:
    """Load v3 state or classify a completed v2 state without modifying either."""
    path = _resolve_state_storage_path(state_file, allowed_roots)
    if not path.exists():
        return None
    raw = _read_json_object(path, "workflow state")
    raw = _unwrap_projection_cache(raw)
    version = raw.get("version")
    if type(version) is int and version == 3:
        try:
            state = WorkflowState.from_dict(raw)
            resolve_path_within_roots(state.task_file, allowed_roots)
            if (
                expected_protocol_binding is not _UNSPECIFIED_PROTOCOL
                and state.protocol_binding != expected_protocol_binding
            ):
                raise WorkflowStateValidationError(
                    "persisted protocol binding does not match the resume binding"
                )
        except (WorkflowStateValidationError, PathPolicyError) as exc:
            raise StateSchemaError(f"invalid version-3 workflow state: {exc}") from exc
        return state
    if type(version) is int and version == 2:
        phase = raw.get("phase")
        if phase == "done":
            phase2 = raw.get("phase2")
            completed_at = (
                phase2.get("completed_at") if isinstance(phase2, Mapping) else None
            )
            return CompletedV2State(
                version=2,
                phase="done",
                task_file=raw.get("task_file") if isinstance(raw.get("task_file"), str) else None,
                completed_at=(completed_at if isinstance(completed_at, str) else None),
            )
        status = _legacy_v2_status(raw)
        raise ActiveV2StateError(
            "version-2 state is active or frozen "
            f"({status}); start a new version-3 run explicitly; state was left unchanged"
        )
    raise UnknownStateVersionError(
        f"unsupported state version {version!r}; state was left unchanged"
    )


def load_resumable_workflow_state(
    state_file: Path,
    *,
    repository_root: Path,
    allowed_roots: tuple[Path, ...],
    expected_run_id: str | None = None,
    expected_task_file: Path | None = None,
    expected_task_digest: str | None = None,
    resolution_observer: Callable[[ResumeResolution], None] | None = None,
) -> WorkflowState | CompletedV2State | None:
    """Project resume state from records and refresh the disposable cache.

    ``state.json`` contributes at most a run-id locator. Its remaining bytes,
    including a stale or invalid cache binding, never authorize a decision.
    When the cache is absent, an exact task-bound record run is discovered.
    """
    path = _resolve_state_storage_path(state_file, allowed_roots)
    if path.exists():
        try:
            raw_locator = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raw_locator = None
        if isinstance(raw_locator, dict) and raw_locator.get("version") == 2:
            return load_workflow_state(path, allowed_roots=allowed_roots)
        if (
            isinstance(raw_locator, dict)
            and raw_locator.get("version") == 3
            and raw_locator.get("cache_format") is None
        ):
            binding = raw_locator.get("protocol_binding")
            if (
                not isinstance(binding, dict)
                or binding.get("mode") != "structured-v2"
            ):
                raise ArtifactResumeError(
                    "UNSUPPORTED-PROTOCOL: legacy-state-v3 is not resumable; "
                    "restore a structured-v2 record chain or start a new run"
                )
    run_id = expected_run_id or _projection_cache_run_id(path)
    if run_id is None:
        resolution = _discover_resume_resolution(
            repository_root,
            expected_task_file=expected_task_file,
            expected_task_digest=expected_task_digest,
        )
    else:
        resolution = resolve_resume_state(repository_root, run_id)
    projected = resolution.state
    if (
        expected_task_file is not None
        and Path(projected.task_file).resolve() != expected_task_file.resolve()
    ):
        raise StateSchemaError(
            "record-projected task identity differs from the requested resume task"
        )
    if (
        expected_task_digest is not None
        and projected.task_digest != expected_task_digest
    ):
        raise StateSchemaError(
            "record-projected task digest differs from the requested resume task"
        )
    if resolution_observer is not None:
        resolution_observer(resolution)
    write_workflow_state_projection(
        path,
        resolution,
        allowed_roots=allowed_roots,
    )
    return projected


def write_workflow_state_projection(
    state_file: Path,
    resolution: ResumeResolution,
    *,
    allowed_roots: tuple[Path, ...],
) -> None:
    """Atomically write one head/reducer/digest-bound state cache."""
    path = _resolve_state_storage_path(state_file, allowed_roots)
    if resolution.replay_result is None or resolution.record_head_id is None:
        raise StateSchemaError(
            "workflow state projection requires an authoritative replay head"
        )
    state = resolution.state
    try:
        resolve_path_within_roots(state.task_file, allowed_roots)
        validated = WorkflowState.from_dict(state.to_dict())
    except (PathPolicyError, WorkflowStateValidationError) as exc:
        raise StateSchemaError(
            f"refusing to cache invalid projected workflow state: {exc}"
        ) from exc
    state_document = validated.to_dict()
    projection_digest = hashlib.sha256(canonical_json(state_document)).hexdigest()
    document = {
        "cache_format": STATE_PROJECTION_CACHE_FORMAT,
        "record_head_id": resolution.record_head_id,
        "reducer_version": STATE_PROJECTION_REDUCER_VERSION,
        "projection_digest": projection_digest,
        "state": state_document,
    }
    expected = json.dumps(document, indent=2, ensure_ascii=True) + "\n"
    try:
        current = path.read_text(encoding="utf-8") if path.exists() else None
    except (OSError, UnicodeError):
        current = None
    if current != expected:
        atomic_write_file(path, expected)


def write_workflow_projection_checkpoint(
    checkpoint_dir: Path,
    resolution: ResumeResolution,
    *,
    allowed_roots: tuple[Path, ...],
) -> Path:
    """Write a disposable checkpoint using the same cache envelope."""
    state = resolution.state
    current = state.current_work_unit
    path = workflow_checkpoint_path(
        checkpoint_dir,
        work_unit_id=current.work_unit_id,
        slice_id=current.slice_id,
        round_number=current.round_number,
    )
    write_workflow_state_projection(path, resolution, allowed_roots=allowed_roots)
    return path.resolve()


def save_workflow_state(
    state_file: Path,
    state: WorkflowState,
    *,
    allowed_roots: tuple[Path, ...],
    replace_existing_run_id: str | None = None,
) -> None:
    """Atomically persist validated v3 state below an explicit root."""
    path = _resolve_state_storage_path(state_file, allowed_roots)
    try:
        resolve_path_within_roots(state.task_file, allowed_roots)
        validated = WorkflowState.from_dict(state.to_dict())
    except (PathPolicyError, WorkflowStateValidationError) as exc:
        raise StateSchemaError(f"refusing to save invalid version-3 state: {exc}") from exc
    if path.exists():
        raw_existing = _read_json_object(path, "existing workflow state")
        if raw_existing.get("version") == 3:
            try:
                existing = WorkflowState.from_dict(raw_existing)
            except WorkflowStateValidationError as exc:
                raise StateSchemaError(
                    f"refusing to overwrite invalid version-3 state: {exc}"
                ) from exc
            if existing.run_id != validated.run_id:
                if replace_existing_run_id != existing.run_id:
                    raise StateSchemaError(
                        "refusing to replace an existing workflow run without "
                        "matching authorization"
                    )
            elif existing.protocol_binding != validated.protocol_binding:
                raise StateSchemaError(
                    "refusing to add or change an existing workflow protocol binding"
                )
    atomic_write_file(path, json.dumps(validated.to_dict(), indent=2, ensure_ascii=True) + "\n")


def workflow_checkpoint_path(
    checkpoint_dir: Path,
    *,
    work_unit_id: int,
    slice_id: int,
    round_number: int,
) -> Path:
    """Return the collision-free, 1-based v3 checkpoint name."""
    for value, label in (
        (work_unit_id, "work_unit_id"),
        (slice_id, "slice_id"),
        (round_number, "round_number"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise StateSchemaError(f"{label} must be a 1-based integer")
    return checkpoint_dir / (
        f"work-unit-{work_unit_id:04d}-slice-{slice_id:04d}-round-{round_number:04d}.json"
    )


def write_workflow_checkpoint(
    checkpoint_dir: Path,
    state: WorkflowState,
    *,
    allowed_roots: tuple[Path, ...],
) -> Path:
    current = state.current_work_unit
    path = workflow_checkpoint_path(
        checkpoint_dir,
        work_unit_id=current.work_unit_id,
        slice_id=current.slice_id,
        round_number=current.round_number,
    )
    save_workflow_state(path, state, allowed_roots=allowed_roots)
    return path.resolve()


def load_workflow_checkpoint(
    checkpoint_dir: Path,
    *,
    work_unit_id: int,
    slice_id: int,
    round_number: int,
    allowed_roots: tuple[Path, ...],
    expected_protocol_binding: ProtocolBinding | None | object = _UNSPECIFIED_PROTOCOL,
) -> WorkflowState | None:
    path = workflow_checkpoint_path(
        checkpoint_dir,
        work_unit_id=work_unit_id,
        slice_id=slice_id,
        round_number=round_number,
    )
    loaded = load_workflow_state(
        path,
        allowed_roots=allowed_roots,
        expected_protocol_binding=expected_protocol_binding,
    )
    if loaded is None:
        return None
    if isinstance(loaded, CompletedV2State):
        raise StateSchemaError("a version-3 checkpoint cannot contain version-2 state")
    current = loaded.current_work_unit
    expected = (work_unit_id, slice_id, round_number)
    actual = (current.work_unit_id, current.slice_id, current.round_number)
    if actual != expected:
        raise StateSchemaError(
            f"checkpoint identity mismatch: filename={expected}, state={actual}"
        )
    return loaded


def _resolve_state_storage_path(path: Path, allowed_roots: tuple[Path, ...]) -> Path:
    try:
        return resolve_path_within_roots(path, allowed_roots)
    except PathPolicyError as exc:
        raise StatePathError(f"state path '{path}' is not allowed: {exc}") from exc


def _unwrap_projection_cache(raw: dict) -> dict:
    """Validate and unwrap a non-authoritative state projection cache."""
    if "cache_format" not in raw:
        return raw
    expected_keys = {
        "cache_format",
        "record_head_id",
        "reducer_version",
        "projection_digest",
        "state",
    }
    if set(raw) != expected_keys:
        raise StateSchemaError("workflow state projection cache has unknown fields")
    if raw.get("cache_format") != STATE_PROJECTION_CACHE_FORMAT:
        raise StateSchemaError("workflow state projection cache format is unsupported")
    if raw.get("reducer_version") != STATE_PROJECTION_REDUCER_VERSION:
        raise StateSchemaError("workflow state projection cache reducer is unsupported")
    if not isinstance(raw.get("record_head_id"), str) or not raw["record_head_id"]:
        raise StateSchemaError("workflow state projection cache has no record head")
    state_document = raw.get("state")
    if not isinstance(state_document, dict):
        raise StateSchemaError("workflow state projection cache has no state document")
    try:
        canonical_state = WorkflowState.from_dict(state_document).to_dict()
    except WorkflowStateValidationError as exc:
        raise StateSchemaError(
            f"workflow state projection cache contains invalid state: {exc}"
        ) from exc
    digest = hashlib.sha256(canonical_json(canonical_state)).hexdigest()
    if raw.get("projection_digest") != digest:
        raise StateSchemaError("workflow state projection cache digest is invalid")
    return canonical_state


def _projection_cache_run_id(path: Path) -> str | None:
    """Read only the cache locator; malformed cache content has no authority."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    state = raw.get("state") if "cache_format" in raw else raw
    if not isinstance(state, dict):
        return None
    run_id = state.get("run_id")
    return run_id if isinstance(run_id, str) and run_id.strip() else None


def _discover_resume_resolution(
    repository_root: Path,
    *,
    expected_task_file: Path | None,
    expected_task_digest: str | None,
) -> ResumeResolution:
    """Find one unambiguous record run when the disposable cache is absent."""
    artifacts_root = repository_root.resolve() / ".orchestrator" / "artifacts"
    if not artifacts_root.is_dir() or artifacts_root.is_symlink():
        raise ArtifactResumeError(
            "no state cache and no structured artifact runs are available"
        )
    matches: list[ResumeResolution] = []
    candidate_errors: list[ArtifactResumeError] = []
    for candidate in sorted(artifacts_root.iterdir(), key=lambda item: item.name):
        if not candidate.is_dir() or candidate.is_symlink():
            continue
        try:
            resolution = resolve_resume_state(repository_root, candidate.name)
        except ArtifactResumeError as exc:
            candidate_errors.append(exc)
            continue
        state = resolution.state
        if (
            expected_task_file is not None
            and Path(state.task_file).resolve() != expected_task_file.resolve()
        ):
            continue
        if (
            expected_task_digest is not None
            and state.task_digest != expected_task_digest
        ):
            continue
        matches.append(resolution)
    if candidate_errors:
        first_error = candidate_errors[0]
        raise ArtifactResumeError(
            "record run discovery encountered an invalid candidate: "
            f"{first_error}"
        ) from first_error
    if len(matches) != 1:
        raise ArtifactResumeError(
            "state cache is absent and the requested record run is not unique"
        )
    return matches[0]


def _read_json_object(path: Path, label: str) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateSchemaError(f"could not read {label}: {exc}") from exc
    if not isinstance(raw, dict):
        raise StateSchemaError(f"{label} must contain a JSON object")
    return raw


def _legacy_v2_status(raw: Mapping[str, object]) -> str:
    states: list[str] = []
    for phase_name in ("phase1", "phase2"):
        phase = raw.get(phase_name)
        if isinstance(phase, Mapping) and isinstance(phase.get("status"), str):
            states.append(f"{phase_name}={phase['status']}")
    return ", ".join(states) or f"phase={raw.get('phase')!r}"
