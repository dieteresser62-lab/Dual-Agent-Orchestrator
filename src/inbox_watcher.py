from __future__ import annotations

import argparse
import copy
import contextlib
import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, TextIO

from state_io import atomic_write_file
from workflow import WorkflowRunResult
from workflow_state import GateReason, WorkUnitStatus

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None

logger = logging.getLogger(__name__)
STUCK_RETRY_MULTIPLIER = 3


class WatchTaskDisposition(str, Enum):
    COMPLETED = "completed"
    RESUMABLE_HALT = "resumable_halt"
    TECHNICAL_FAILURE = "technical_failure"


class QueueFinalizationDisposition(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class QueueFinalizationResult:
    disposition: QueueFinalizationDisposition
    destination: Path | None = None
    detail: str | None = None


@dataclass(frozen=True)
class QueueSuccessEvidence:
    run_id: str
    task_digest: str
    protocol_mode: str
    source: str
    destination: str
    evidence_digest: str
    version: int = 1

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ValueError("queue success evidence version is invalid")
        if not self.run_id.strip():
            raise ValueError("queue success evidence requires a run id")
        if len(self.task_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.task_digest
        ):
            raise ValueError("queue success evidence requires a SHA-256 task digest")
        if self.protocol_mode != "structured-v2":
            raise ValueError("queue success evidence protocol mode is invalid")
        if not self.source or not self.destination:
            raise ValueError("queue success evidence requires source and destination")
        if self.evidence_digest != self.calculate_digest(
            run_id=self.run_id,
            task_digest=self.task_digest,
            protocol_mode=self.protocol_mode,
            source=self.source,
            destination=self.destination,
        ):
            raise ValueError("queue success evidence binding digest differs")

    @staticmethod
    def calculate_digest(
        *, run_id: str, task_digest: str, protocol_mode: str, source: str, destination: str
    ) -> str:
        payload = json.dumps(
            {
                "destination": destination,
                "protocol_mode": protocol_mode,
                "run_id": run_id,
                "source": source,
                "task_digest": task_digest,
                "version": 1,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "task_digest": self.task_digest,
            "protocol_mode": self.protocol_mode,
            "source": self.source,
            "destination": self.destination,
            "evidence_digest": self.evidence_digest,
        }

    @classmethod
    def from_dict(cls, raw: object) -> QueueSuccessEvidence:
        expected = {
            "version", "run_id", "task_digest", "protocol_mode", "source", "destination",
            "evidence_digest",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ValueError("queue success evidence has an invalid schema")
        if raw.get("version") != 1 or any(
            not isinstance(raw.get(field), str) for field in expected - {"version"}
        ):
            raise ValueError("queue success evidence has invalid field types")
        return cls(
            run_id=raw["run_id"],
            task_digest=raw["task_digest"],
            protocol_mode=raw["protocol_mode"],
            source=raw["source"],
            destination=raw["destination"],
            evidence_digest=raw["evidence_digest"],
        )


@dataclass(frozen=True)
class WatchTaskResult:
    exit_code: int
    run_id: str
    disposition: WatchTaskDisposition
    status: str
    step: str
    work_unit_id: int
    gate_reason: str
    failure_detail: str | None = None
    resume_available: bool = True
    protocol_mode: str | None = None

    def __post_init__(self) -> None:
        if self.exit_code < 0:
            raise ValueError("watch task exit code must be non-negative")
        if not self.run_id.strip():
            raise ValueError("watch task result requires a run id")
        if self.work_unit_id < 1:
            raise ValueError("watch task result requires a 1-based work unit id")
        if not isinstance(self.resume_available, bool):
            raise ValueError("watch task resume availability must be boolean")
        if self.protocol_mode not in {None, "structured-v2"}:
            raise ValueError("watch task protocol mode is invalid")
        if self.disposition is WatchTaskDisposition.COMPLETED and self.exit_code != 0:
            raise ValueError("completed watch task result requires exit code zero")
        if (
            self.disposition is WatchTaskDisposition.RESUMABLE_HALT
            and self.exit_code not in {2, 3, 4}
        ):
            raise ValueError("resumable watch halt requires exit code 2, 3, or 4")
        if self.gate_reason == GateReason.BOOTSTRAP_CHECK.value:
            if (
                self.disposition is not WatchTaskDisposition.RESUMABLE_HALT
                or self.status != WorkUnitStatus.AWAITING_RESUME.value
                or self.exit_code != 4
                or not self.resume_available
            ):
                raise ValueError(
                    "bootstrap check must remain an exit-4 resumable watch halt"
                )

    @classmethod
    def from_workflow(cls, result: WorkflowRunResult) -> WatchTaskResult:
        state = result.state
        status = state.current_work_unit.status
        resumable = status in {
            WorkUnitStatus.AWAITING_USER_DECISION,
            WorkUnitStatus.WAITING_FOR_QUOTA,
            WorkUnitStatus.WAITING_FOR_RETRY,
            WorkUnitStatus.AWAITING_RESUME,
        }
        bootstrap_halt = (
            status is WorkUnitStatus.AWAITING_RESUME
            and state.current_work_unit.gate.reason is GateReason.BOOTSTRAP_CHECK
        )
        if result.workflow_completed:
            disposition = WatchTaskDisposition.COMPLETED
            exit_code = 0
        elif bootstrap_halt:
            # This local denial happens before a provider process.  Exit 4 keeps it
            # distinct from quota/agent failures while preserving the watch identity
            # for an operator to repair the cause and resume the exact same step.
            disposition = WatchTaskDisposition.RESUMABLE_HALT
            exit_code = 4
        elif resumable and result.exit_code in {2, 3, 4}:
            disposition = WatchTaskDisposition.RESUMABLE_HALT
            exit_code = result.exit_code
        else:
            disposition = WatchTaskDisposition.TECHNICAL_FAILURE
            exit_code = result.exit_code or 1
        return cls(
            exit_code=exit_code,
            run_id=state.run_id,
            disposition=disposition,
            status=status.value,
            step=state.current_step.value,
            work_unit_id=state.current_work_unit_id,
            gate_reason=state.current_work_unit.gate.reason.value,
            failure_detail=(
                state.current_work_unit.gate.detail
                if disposition is WatchTaskDisposition.RESUMABLE_HALT
                else "workflow returned a non-resumable, non-terminal result"
                if disposition is WatchTaskDisposition.TECHNICAL_FAILURE
                else None
            ),
            protocol_mode=state.effective_protocol_mode.value,
        )


@dataclass(frozen=True)
class WatchTaskIdentity:
    run_id: str
    task_digest: str
    started: bool = False
    protocol_mode: str | None = None
    sidecar_version: int = 2

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("watch task identity requires a run id")
        if len(self.task_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.task_digest
        ):
            raise ValueError("watch task identity requires a SHA-256 task digest")
        if self.protocol_mode not in {None, "structured-v2"}:
            raise ValueError("watch task identity protocol mode is invalid")
        if self.sidecar_version not in {1, 2}:
            raise ValueError("watch task identity sidecar version is invalid")
        if self.sidecar_version == 1 and self.protocol_mode is not None:
            raise ValueError("legacy watch identity cannot carry a protocol mode")

    def to_dict(self) -> dict[str, object]:
        document: dict[str, object] = {
            "version": self.sidecar_version,
            "run_id": self.run_id,
            "task_digest": self.task_digest,
            "started": self.started,
        }
        if self.sidecar_version == 2:
            document["protocol_mode"] = self.protocol_mode
        return document

    @classmethod
    def from_dict(cls, raw: object) -> WatchTaskIdentity:
        if not isinstance(raw, dict):
            raise ValueError("watch task identity has an invalid schema")
        version = raw.get("version")
        expected = {
            "version", "run_id", "task_digest", "started",
            *({"protocol_mode"} if version == 2 else set()),
        }
        if set(raw) != expected or version not in {1, 2} or not isinstance(raw["started"], bool):
            raise ValueError("watch task identity has an unsupported version or status")
        if not isinstance(raw["run_id"], str) or not isinstance(raw["task_digest"], str):
            raise ValueError("watch task identity fields have invalid types")
        protocol_mode = raw.get("protocol_mode")
        if protocol_mode is not None and not isinstance(protocol_mode, str):
            raise ValueError("watch task identity protocol mode has an invalid type")
        return cls(
            raw["run_id"], raw["task_digest"], raw["started"], protocol_mode, version
        )


def watch_identity_path(task_file: Path) -> Path:
    return task_file.with_name(f".{task_file.name}.watch.json")


def _task_digest(task_file: Path) -> str:
    return hashlib.sha256(task_file.read_bytes()).hexdigest()


def _new_watch_run_id(task_file: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S.%fZ")
    entropy = hashlib.sha256(
        f"{task_file.resolve()}:{stamp}".encode("utf-8")
    ).hexdigest()[:12]
    return f"watch-{stamp}-{entropy}"


def load_or_create_watch_identity(
    task_file: Path,
    *,
    run_id_fn: Callable[[Path], str] = _new_watch_run_id,
) -> WatchTaskIdentity:
    path = watch_identity_path(task_file)
    digest = _task_digest(task_file)
    if path.exists():
        try:
            identity = WatchTaskIdentity.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"cannot resume invalid watch identity {path.name}: {exc}") from exc
        if identity.task_digest != digest:
            raise ValueError(
                f"watch task {task_file.name} changed after run {identity.run_id} started"
            )
        return identity
    identity = WatchTaskIdentity(run_id_fn(task_file), digest, sidecar_version=2)
    atomic_write_file(path, json.dumps(identity.to_dict(), sort_keys=True) + "\n")
    return identity


def save_watch_identity(task_file: Path, identity: WatchTaskIdentity) -> None:
    atomic_write_file(
        watch_identity_path(task_file),
        json.dumps(identity.to_dict(), sort_keys=True) + "\n",
    )


def delete_watch_identity(task_file: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        watch_identity_path(task_file).unlink()


def list_inbox_tasks(inbox_dir: Path) -> list[Path]:
    tasks = [path for path in inbox_dir.glob("*.md") if path.is_file()]
    # Oldest-first ordering keeps processing deterministic across watcher restarts.
    return sorted(tasks, key=lambda path: (path.stat().st_mtime, path.name))


def is_file_stable(path: Path, now_epoch: float, min_age_seconds: float) -> bool:
    try:
        age_seconds = now_epoch - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age_seconds >= max(0.0, float(min_age_seconds))


def build_outbox_destination(outbox_subdir: Path, source_name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"
    base_name = f"{stamp}_{source_name}"
    candidate = outbox_subdir / base_name
    if not candidate.exists():
        return candidate

    stem = Path(source_name).stem
    suffix = Path(source_name).suffix
    counter = 1
    while True:
        # Add numeric suffix when multiple tasks share the same millisecond timestamp.
        candidate = outbox_subdir / f"{stamp}_{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def move_to_outbox(task_file: Path, outbox_subdir: Path, *, source_name: str | None = None) -> Path:
    destination = build_outbox_destination(outbox_subdir, source_name or task_file.name)
    return move_to_reserved_outbox(task_file, destination)


def move_to_reserved_outbox(task_file: Path, destination: Path) -> Path:
    shutil.move(str(task_file), str(destination))
    return destination


def attempt_sidecar_path(task_file: Path) -> Path:
    return task_file.with_name(f"{task_file.name}.attempts")


def success_marker_path(task_file: Path) -> Path:
    return task_file.with_name(f"{task_file.name}.success")


def has_success_marker(task_file: Path) -> bool:
    return success_marker_path(task_file).exists()


def has_bound_queue_success_marker(task_file: Path) -> bool:
    try:
        return isinstance(
            json.loads(success_marker_path(task_file).read_text(encoding="utf-8")), dict
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def write_success_marker(task_file: Path) -> None:
    marker = success_marker_path(task_file)
    marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


def _canonical(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _validate_queue_paths(task_file: Path, inbox_dir: Path, outbox_dir: Path) -> tuple[Path, Path]:
    source = _canonical(task_file)
    inbox = _canonical(inbox_dir)
    done = _canonical(outbox_dir) / "done"
    if source.parent != inbox or source.suffix.casefold() != ".md":
        raise ValueError("queue task source is not a direct Markdown child of the configured inbox")
    if task_file.is_symlink():
        raise ValueError("queue task source must not be a symlink")
    return source, done


def load_watch_identity(
    task_file: Path, *, expected_digest: str | None = None
) -> WatchTaskIdentity:
    path = watch_identity_path(task_file)
    try:
        identity = WatchTaskIdentity.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot load watch identity {path.name}: {exc}") from exc
    digest = expected_digest
    if task_file.exists():
        if task_file.is_symlink() or not task_file.is_file():
            raise ValueError("watch task source must be a regular non-symlink file")
        digest = _task_digest(task_file)
    if digest is None or identity.task_digest != digest:
        raise ValueError("watch identity task digest differs from queue task")
    if not identity.started or identity.sidecar_version != 2:
        raise ValueError("watch identity is not a started version-2 identity")
    if identity.protocol_mode != "structured-v2":
        raise ValueError("watch identity is not bound to structured-v2")
    return identity


def load_queue_success_evidence(
    task_file: Path,
    *,
    inbox_dir: Path,
    outbox_dir: Path,
    expected_run_id: str | None = None,
    expected_task_digest: str | None = None,
    expected_protocol_mode: str = "structured-v2",
) -> QueueSuccessEvidence:
    source, done = _validate_queue_paths(task_file, inbox_dir, outbox_dir)
    marker = success_marker_path(task_file)
    try:
        evidence = QueueSuccessEvidence.from_dict(
            json.loads(marker.read_text(encoding="utf-8"))
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot load bound queue success evidence: {exc}") from exc
    destination = _canonical(Path(evidence.destination))
    if Path(evidence.source) != source or _canonical(Path(evidence.source)) != source:
        raise ValueError("queue success evidence source binding differs")
    if destination.parent != done or Path(evidence.destination) != destination:
        raise ValueError("queue success evidence destination escapes configured done outbox")
    if expected_run_id is not None and evidence.run_id != expected_run_id:
        raise ValueError("queue success evidence run id differs")
    if expected_task_digest is not None and evidence.task_digest != expected_task_digest:
        raise ValueError("queue success evidence task digest differs")
    if evidence.protocol_mode != expected_protocol_mode:
        raise ValueError("queue success evidence protocol mode differs")
    identity_path = watch_identity_path(task_file)
    if identity_path.exists():
        identity = load_watch_identity(task_file, expected_digest=evidence.task_digest)
        if identity.run_id != evidence.run_id or identity.protocol_mode != evidence.protocol_mode:
            raise ValueError("queue success evidence differs from watch identity")
    return evidence


def finalize_queue_success(
    task_file: Path,
    *,
    inbox_dir: Path,
    outbox_dir: Path,
    run_id: str | None = None,
    task_digest: str | None = None,
    protocol_mode: str = "structured-v2",
    publish: bool = False,
) -> QueueFinalizationResult:
    """Publish or recover a bound, idempotent successful queue finalization."""
    try:
        source, done = _validate_queue_paths(task_file, inbox_dir, outbox_dir)
        marker = success_marker_path(task_file)
        if marker.exists():
            evidence = load_queue_success_evidence(
                task_file,
                inbox_dir=inbox_dir,
                outbox_dir=outbox_dir,
                expected_run_id=run_id,
                expected_task_digest=task_digest,
                expected_protocol_mode=protocol_mode,
            )
        else:
            if not publish:
                return QueueFinalizationResult(QueueFinalizationDisposition.NOT_APPLICABLE)
            if run_id is None or task_digest is None:
                raise ValueError("publishing queue success requires run and task bindings")
            identity = load_watch_identity(task_file, expected_digest=task_digest)
            if identity.run_id != run_id or identity.protocol_mode != protocol_mode:
                raise ValueError("terminal workflow result differs from watch identity")
            if not task_file.is_file():
                raise ValueError("queue source is absent before success evidence publication")
            done.mkdir(parents=True, exist_ok=True)
            destination = _canonical(build_outbox_destination(done, task_file.name))
            evidence = QueueSuccessEvidence(
                run_id=run_id,
                task_digest=task_digest,
                protocol_mode=protocol_mode,
                source=str(source),
                destination=str(destination),
                evidence_digest=QueueSuccessEvidence.calculate_digest(
                    run_id=run_id,
                    task_digest=task_digest,
                    protocol_mode=protocol_mode,
                    source=str(source),
                    destination=str(destination),
                ),
            )
            atomic_write_file(marker, json.dumps(evidence.to_dict(), sort_keys=True) + "\n")

        destination = Path(evidence.destination)
        source_exists = task_file.exists()
        destination_exists = destination.exists()
        if source_exists and destination_exists:
            raise ValueError("queue source and bound destination both exist")
        if not source_exists and not destination_exists:
            raise ValueError("queue source and bound destination are both absent")
        if source_exists:
            if _task_digest(task_file) != evidence.task_digest:
                raise ValueError("queue source digest differs from success evidence")
            move_to_reserved_outbox(task_file, destination)
        elif not destination.is_file() or destination.is_symlink():
            raise ValueError("bound queue destination is not a regular file")
        if _task_digest(destination) != evidence.task_digest:
            raise ValueError("bound queue destination digest differs from success evidence")

        # The marker remains until last, so every partial cleanup is safely resumable.
        delete_attempt_sidecar(task_file)
        delete_watch_identity(task_file)
        delete_success_marker(task_file)
        return QueueFinalizationResult(
            QueueFinalizationDisposition.COMPLETED, destination=destination
        )
    except Exception as exc:
        return QueueFinalizationResult(
            QueueFinalizationDisposition.FAILED,
            detail=f"{type(exc).__name__}: {exc}",
        )


def delete_success_marker(task_file: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        success_marker_path(task_file).unlink()


def read_attempt_count(task_file: Path) -> int:
    sidecar = attempt_sidecar_path(task_file)
    if not sidecar.exists():
        return 0
    try:
        raw = sidecar.read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def write_attempt_count(task_file: Path, count: int) -> None:
    sidecar = attempt_sidecar_path(task_file)
    sidecar.write_text(str(max(0, int(count))), encoding="utf-8")


def delete_attempt_sidecar(task_file: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        attempt_sidecar_path(task_file).unlink()


def write_poison_failure_report(
    destination: Path,
    *,
    attempts: int,
    task_result: WatchTaskResult | None,
    exception_detail: str | None,
) -> Path:
    """Persist the final technical cause next to a poison task."""
    report_path = destination.with_name(destination.name + ".error.json")
    payload = {
        "version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "poison_task": destination.name,
        "attempts": attempts,
        "exit_code": None if task_result is None else task_result.exit_code,
        "run_id": None if task_result is None else task_result.run_id,
        "status": None if task_result is None else task_result.status,
        "step": None if task_result is None else task_result.step,
        "work_unit_id": None if task_result is None else task_result.work_unit_id,
        "gate_reason": None if task_result is None else task_result.gate_reason,
        "protocol_mode": None if task_result is None else task_result.protocol_mode,
        "failure_detail": (
            exception_detail
            if exception_detail is not None
            else None
            if task_result is None
            else task_result.failure_detail
        ),
    }
    atomic_write_file(report_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return report_path


def acquire_inbox_lock(inbox_dir: Path) -> TextIO | None:
    lock_path = inbox_dir / ".lock"
    # Open in a+ so the lock file is created if missing without truncating existing content.
    handle = lock_path.open("a+", encoding="utf-8")
    if fcntl is None:
        # Non-Unix fallback keeps functionality but cannot enforce single-process safety.
        logger.warning("fcntl not available; running without single-instance inbox lock.")
        return handle

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def release_inbox_lock(lock_handle: TextIO | None) -> None:
    if lock_handle is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock_handle.close()


def watch_inbox(
    *,
    inbox_dir: Path,
    outbox_dir: Path,
    poll_interval: float,
    args: argparse.Namespace,
    process_task: Callable[
        [Path, argparse.Namespace, bool], int | WatchTaskResult
    ],
    min_file_age_seconds: float = 1.0,
    max_retries: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.time,
) -> int:
    """Continuously process stable inbox tasks and move them to done/failed outboxes."""

    inbox_dir.mkdir(parents=True, exist_ok=True)
    outbox_done_dir = outbox_dir / "done"
    outbox_failed_dir = outbox_dir / "failed"
    outbox_done_dir.mkdir(parents=True, exist_ok=True)
    outbox_failed_dir.mkdir(parents=True, exist_ok=True)

    lock_handle = acquire_inbox_lock(inbox_dir)
    if lock_handle is None:
        logger.error("Another watcher is already running on inbox: %s", inbox_dir)
        return 1

    logger.info(
        "Watching %s for .md files (outbox: %s, poll: %.2fs, min age: %.2fs, max retries: %s).",
        inbox_dir,
        outbox_dir,
        poll_interval,
        min_file_age_seconds,
        max_retries,
    )

    try:
        while True:
            now_epoch = time_fn()
            pending = list_inbox_tasks(inbox_dir)
            ready = [path for path in pending if is_file_stable(path, now_epoch, min_file_age_seconds)]

            if not ready:
                sleep_fn(poll_interval)
                continue

            task_file = ready[0]
            logger.info("Processing inbox task: %s", task_file)
            stuck_limit = max_retries * STUCK_RETRY_MULTIPLIER
            if stuck_limit > 0:
                current_attempts = read_attempt_count(task_file)
                if current_attempts >= stuck_limit:
                    stuck_destination = task_file.with_suffix(".md.stuck")
                    logger.critical(
                        "Task %s stuck after %s attempts (limit %s). Renaming to %s for manual intervention.",
                        task_file.name,
                        current_attempts,
                        stuck_limit,
                        stuck_destination.name,
                    )
                    try:
                        task_file.rename(stuck_destination)
                        delete_attempt_sidecar(task_file)
                        delete_success_marker(task_file)
                        delete_watch_identity(task_file)
                    except Exception:
                        logger.exception("Failed to rename stuck task %s.", task_file)
                    continue

            exit_code: int | None = None
            task_result: WatchTaskResult | None = None
            failed_with_exception = False
            exception_detail: str | None = None
            task_succeeded_already = has_success_marker(task_file)

            if task_succeeded_already:
                logger.info(
                    "Skipping re-execution for already-succeeded task; retrying move only: %s",
                    task_file.name,
                )
            else:
                try:
                    identity = load_or_create_watch_identity(task_file)
                except ValueError as exc:
                    logger.error(
                        "Cannot safely start or resume watch task %s: %s",
                        task_file.name,
                        exc,
                    )
                    return 1
                task_args = copy.copy(args)
                task_args.watch_run_id = identity.run_id
                task_args.resume = identity.started
                task_args.force_overwrite_state = not identity.started
                force_new = not identity.started
                if not identity.started:
                    identity = replace(identity, started=True)
                    save_watch_identity(task_file, identity)
                try:
                    raw_result = process_task(task_file, task_args, force_new)
                    if isinstance(raw_result, WatchTaskResult):
                        task_result = raw_result
                        if task_result.run_id != identity.run_id:
                            logger.error(
                                "Workflow result run id %s differs from persisted watch "
                                "identity %s for %s.",
                                task_result.run_id,
                                identity.run_id,
                                task_file.name,
                            )
                            return 1
                        if (
                            identity.protocol_mode is not None
                            and task_result.protocol_mode is not None
                            and task_result.protocol_mode != identity.protocol_mode
                        ):
                            logger.error(
                                "Workflow protocol mode %s differs from persisted watch "
                                "identity %s for %s.",
                                task_result.protocol_mode,
                                identity.protocol_mode,
                                task_file.name,
                            )
                            return 1
                        if (
                            identity.sidecar_version == 2
                            and identity.protocol_mode is None
                            and task_result.protocol_mode is not None
                        ):
                            identity = replace(identity, protocol_mode=task_result.protocol_mode)
                            save_watch_identity(task_file, identity)
                        if (
                            task_result.disposition
                            is WatchTaskDisposition.TECHNICAL_FAILURE
                            and not task_result.resume_available
                        ):
                            identity = replace(identity, started=False)
                            save_watch_identity(task_file, identity)
                    else:
                        exit_code = int(raw_result)
                        task_result = WatchTaskResult(
                            exit_code=exit_code,
                            run_id=identity.run_id,
                            disposition=(
                                WatchTaskDisposition.COMPLETED
                                if exit_code == 0
                                else WatchTaskDisposition.RESUMABLE_HALT
                                if exit_code in {2, 3, 4}
                                else WatchTaskDisposition.TECHNICAL_FAILURE
                            ),
                            status="legacy",
                            step="legacy",
                            work_unit_id=1,
                            gate_reason="legacy",
                            failure_detail=(
                                None if exit_code == 0 else f"legacy exit code {exit_code}"
                            ),
                        )
                except Exception as exc:
                    failed_with_exception = True
                    exception_detail = f"{type(exc).__name__}: {exc}"
                    logger.exception("Task processing crashed for %s.", task_file)

            if (
                not failed_with_exception
                and task_result is not None
                and task_result.disposition is WatchTaskDisposition.RESUMABLE_HALT
            ):
                if not task_result.resume_available:
                    identity = replace(identity, started=False)
                    save_watch_identity(task_file, identity)
                logger.warning(
                    "Pausing watch queue for resumable task %s: run=%s work-unit=%s "
                    "step=%s status=%s gate=%s detail=%s exit=%s",
                    task_file.name,
                    task_result.run_id,
                    task_result.work_unit_id,
                    task_result.step,
                    task_result.status,
                    task_result.gate_reason,
                    task_result.failure_detail or "(none)",
                    task_result.exit_code,
                )
                return task_result.exit_code

            failed = failed_with_exception or (
                task_result is not None
                and task_result.disposition is WatchTaskDisposition.TECHNICAL_FAILURE
            )
            if failed:
                attempts = read_attempt_count(task_file) + 1
                write_attempt_count(task_file, attempts)
                if attempts >= max_retries:
                    # Poison-pill naming makes permanently failing tasks visible to operators.
                    poison_name = f"{task_file.name}.poison"
                    try:
                        destination = move_to_outbox(task_file, outbox_failed_dir, source_name=poison_name)
                        report: Path | None = None
                        try:
                            report = write_poison_failure_report(
                                destination,
                                attempts=attempts,
                                task_result=task_result,
                                exception_detail=exception_detail,
                            )
                        except Exception:
                            logger.exception(
                                "Failed to write poison failure report for %s.",
                                destination,
                            )
                        delete_attempt_sidecar(task_file)
                        delete_success_marker(task_file)
                        delete_watch_identity(task_file)
                        logger.warning(
                            "Task marked poison after %s/%s failures and moved to failed outbox: %s",
                            attempts,
                            max_retries,
                            destination,
                        )
                        if report is not None:
                            logger.warning("Poison failure report written: %s", report)
                    except Exception:
                        logger.exception("Failed to move poison task %s to outbox.", task_file)
                else:
                    logger.warning(
                        "Task failed (%s/%s). Leaving in inbox for retry: %s",
                        attempts,
                        max_retries,
                        task_file.name,
                    )
                if failed_with_exception:
                    continue
                assert task_result is not None
                logger.info(
                    "Task finished with exit code %s: %s",
                    task_result.exit_code,
                    task_file.name,
                )
                continue

            if not task_succeeded_already and (
                task_result is None
                or task_result.disposition is not WatchTaskDisposition.COMPLETED
            ):
                logger.error("Task %s returned no terminal watch result.", task_file.name)
                return 1

            bound_failure: str | None = None
            bound_completion = bool(
                not task_succeeded_already
                and task_result is not None
                and task_result.protocol_mode == "structured-v2"
            )
            if bound_completion:
                assert task_result is not None
                assert identity is not None
                queue_result = finalize_queue_success(
                    task_file,
                    inbox_dir=inbox_dir,
                    outbox_dir=outbox_dir,
                    run_id=identity.run_id,
                    task_digest=identity.task_digest,
                    protocol_mode=identity.protocol_mode or "structured-v2",
                    publish=True,
                )
                if queue_result.disposition is QueueFinalizationDisposition.COMPLETED:
                    logger.info("Moved task to done outbox: %s", queue_result.destination)
                    logger.info(
                        "Task finished with exit code %s: %s",
                        task_result.exit_code,
                        task_file.name,
                    )
                    continue
                logger.error(
                    "Bound queue finalization failed for %s: %s",
                    task_file.name,
                    queue_result.detail,
                )
                bound_failure = queue_result.detail or "bound queue finalization failed"
            elif not task_succeeded_already:
                # Legacy integer callbacks retain the historical timestamp marker and
                # move primitive; production structured results use bound evidence.
                try:
                    write_success_marker(task_file)
                except Exception as exc:
                    bound_failure = f"{type(exc).__name__}: {exc}"
            elif has_bound_queue_success_marker(task_file):
                queue_result = finalize_queue_success(
                    task_file,
                    inbox_dir=inbox_dir,
                    outbox_dir=outbox_dir,
                )
                if queue_result.disposition is QueueFinalizationDisposition.COMPLETED:
                    logger.info(
                        "Task bookkeeping completed for previously succeeded task: %s",
                        task_file.name,
                    )
                    continue
                if queue_result.disposition is QueueFinalizationDisposition.FAILED:
                    logger.error(
                        "Bound queue recovery failed for %s: %s",
                        task_file.name,
                        queue_result.detail,
                    )
                    bound_failure = queue_result.detail or "bound queue recovery failed"

            if bound_failure is not None and has_bound_queue_success_marker(task_file):
                # Bound evidence must never be redirected to failed/stuck: it names the
                # sole safe destination and is the recovery authority for a direct resume.
                logger.error(
                    "Pausing watch queue with recoverable bound success evidence for %s: %s",
                    task_file.name,
                    bound_failure,
                )
                return 1

            try:
                # Compatibility for timestamp-only success markers from older watchers.
                if bound_failure is not None:
                    raise RuntimeError(bound_failure)
                destination = move_to_outbox(task_file, outbox_done_dir)
                delete_attempt_sidecar(task_file)
                delete_success_marker(task_file)
                delete_watch_identity(task_file)
                logger.info("Moved task to done outbox: %s", destination)
            except Exception:
                # Keep retry accounting symmetrical with processing failures.
                attempts = read_attempt_count(task_file) + 1
                write_attempt_count(task_file, attempts)
                marker_exists = has_success_marker(task_file)
                if attempts >= max_retries:
                    failed_name = f"{task_file.name}.move_error" if marker_exists else f"{task_file.name}.poison"
                    try:
                        destination = move_to_outbox(task_file, outbox_failed_dir, source_name=failed_name)
                        delete_attempt_sidecar(task_file)
                        delete_success_marker(task_file)
                        delete_watch_identity(task_file)
                        if marker_exists:
                            logger.warning(
                                "Task SUCCEEDED (exit 0) but move to done/ failed (%s/%s). "
                                "Marking as move_error despite successful execution: %s",
                                attempts,
                                max_retries,
                                destination,
                            )
                        else:
                            logger.warning(
                                "Task marked poison after %s/%s failures and moved to failed outbox: %s",
                                attempts,
                                max_retries,
                                destination,
                            )
                    except Exception:
                        if marker_exists:
                            logger.exception(
                                "Failed to move succeeded-but-unmoved task %s to failed outbox.",
                                task_file,
                            )
                        else:
                            logger.exception("Failed to move poison task %s to outbox.", task_file)
                else:
                    if marker_exists:
                        logger.warning(
                            "Task SUCCEEDED (exit 0) but move to done/ failed (%s/%s). "
                            "Leaving in inbox to retry move only: %s",
                            attempts,
                            max_retries,
                            task_file.name,
                        )
                    else:
                        logger.warning(
                            "Task move to done failed (%s/%s). Leaving in inbox for retry: %s",
                            attempts,
                            max_retries,
                            task_file.name,
                        )
                continue

            if task_succeeded_already:
                logger.info("Task bookkeeping completed for previously succeeded task: %s", task_file.name)
            else:
                assert task_result is not None
                logger.info(
                    "Task finished with exit code %s: %s",
                    task_result.exit_code,
                    task_file.name,
                )
    except KeyboardInterrupt:
        logger.info("Watch mode stopped.")
        return 0
    finally:
        release_inbox_lock(lock_handle)
