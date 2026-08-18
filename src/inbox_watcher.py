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
from workflow_state import WorkUnitStatus

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

    def __post_init__(self) -> None:
        if self.exit_code < 0:
            raise ValueError("watch task exit code must be non-negative")
        if not self.run_id.strip():
            raise ValueError("watch task result requires a run id")
        if self.work_unit_id < 1:
            raise ValueError("watch task result requires a 1-based work unit id")
        if not isinstance(self.resume_available, bool):
            raise ValueError("watch task resume availability must be boolean")
        if self.disposition is WatchTaskDisposition.COMPLETED and self.exit_code != 0:
            raise ValueError("completed watch task result requires exit code zero")
        if (
            self.disposition is WatchTaskDisposition.RESUMABLE_HALT
            and self.exit_code not in {2, 3, 4}
        ):
            raise ValueError("resumable watch halt requires exit code 2, 3, or 4")

    @classmethod
    def from_workflow(cls, result: WorkflowRunResult) -> WatchTaskResult:
        state = result.state
        status = state.current_work_unit.status
        resumable = status in {
            WorkUnitStatus.AWAITING_USER_DECISION,
            WorkUnitStatus.WAITING_FOR_QUOTA,
            WorkUnitStatus.AWAITING_RESUME,
        }
        if result.workflow_completed:
            disposition = WatchTaskDisposition.COMPLETED
            exit_code = 0
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
        )


@dataclass(frozen=True)
class WatchTaskIdentity:
    run_id: str
    task_digest: str
    started: bool = False

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("watch task identity requires a run id")
        if len(self.task_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.task_digest
        ):
            raise ValueError("watch task identity requires a SHA-256 task digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "run_id": self.run_id,
            "task_digest": self.task_digest,
            "started": self.started,
        }

    @classmethod
    def from_dict(cls, raw: object) -> WatchTaskIdentity:
        if not isinstance(raw, dict) or set(raw) != {
            "version",
            "run_id",
            "task_digest",
            "started",
        }:
            raise ValueError("watch task identity has an invalid schema")
        if raw["version"] != 1 or not isinstance(raw["started"], bool):
            raise ValueError("watch task identity has an unsupported version or status")
        if not isinstance(raw["run_id"], str) or not isinstance(raw["task_digest"], str):
            raise ValueError("watch task identity fields have invalid types")
        return cls(raw["run_id"], raw["task_digest"], raw["started"])


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
    identity = WatchTaskIdentity(run_id_fn(task_file), digest)
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
    shutil.move(str(task_file), str(destination))
    return destination


def attempt_sidecar_path(task_file: Path) -> Path:
    return task_file.with_name(f"{task_file.name}.attempts")


def success_marker_path(task_file: Path) -> Path:
    return task_file.with_name(f"{task_file.name}.success")


def has_success_marker(task_file: Path) -> bool:
    return success_marker_path(task_file).exists()


def write_success_marker(task_file: Path) -> None:
    marker = success_marker_path(task_file)
    marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


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

            if not task_succeeded_already:
                try:
                    write_success_marker(task_file)
                except Exception:
                    logger.exception("Failed to write success marker for %s.", task_file)

            try:
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
