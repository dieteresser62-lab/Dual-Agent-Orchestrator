from __future__ import annotations

import argparse
import contextlib
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None

logger = logging.getLogger(__name__)


def list_inbox_tasks(inbox_dir: Path) -> list[Path]:
    tasks = [
        path
        for path in inbox_dir.glob("*.md")
        if path.is_file() and not path.name.endswith(".attempts")
    ]
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


def acquire_inbox_lock(inbox_dir: Path) -> TextIO | None:
    lock_path = inbox_dir / ".lock"
    handle = lock_path.open("a+", encoding="utf-8")
    if fcntl is None:
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
    process_task: Callable[[Path, argparse.Namespace, bool], int],
    min_file_age_seconds: float = 1.0,
    max_retries: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.time,
) -> int:
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
            exit_code: int | None = None
            failed_with_exception = False

            try:
                exit_code = process_task(task_file, args, True)
            except Exception:
                failed_with_exception = True
                logger.exception("Task processing crashed for %s.", task_file)

            failed = failed_with_exception or (exit_code is not None and exit_code != 0)
            if failed:
                attempts = read_attempt_count(task_file) + 1
                write_attempt_count(task_file, attempts)
                if attempts >= max_retries:
                    poison_name = f"{task_file.name}.poison"
                    try:
                        destination = move_to_outbox(task_file, outbox_failed_dir, source_name=poison_name)
                        delete_attempt_sidecar(task_file)
                        logger.warning(
                            "Task marked poison after %s/%s failures and moved to failed outbox: %s",
                            attempts,
                            max_retries,
                            destination,
                        )
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
                logger.info("Task finished with exit code %s: %s", exit_code, task_file.name)
                continue

            try:
                destination = move_to_outbox(task_file, outbox_done_dir)
                delete_attempt_sidecar(task_file)
                logger.info("Moved task to done outbox: %s", destination)
            except Exception:
                logger.exception("Failed to move %s to done outbox.", task_file)

            if failed_with_exception:
                continue

            logger.info("Task finished with exit code %s: %s", exit_code, task_file.name)
    except KeyboardInterrupt:
        logger.info("Watch mode stopped.")
        return 0
    finally:
        release_inbox_lock(lock_handle)
