from __future__ import annotations

import argparse
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def list_inbox_tasks(inbox_dir: Path) -> list[Path]:
    tasks = [path for path in inbox_dir.glob("*.md") if path.is_file()]
    return sorted(tasks, key=lambda path: (path.stat().st_mtime, path.name))


def is_file_stable(path: Path, now_epoch: float, min_age_seconds: float) -> bool:
    try:
        age_seconds = now_epoch - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age_seconds >= max(0.0, float(min_age_seconds))


def build_outbox_destination(outbox_dir: Path, source_name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"
    base_name = f"{stamp}_{source_name}"
    candidate = outbox_dir / base_name
    if not candidate.exists():
        return candidate

    stem = Path(source_name).stem
    suffix = Path(source_name).suffix
    counter = 1
    while True:
        candidate = outbox_dir / f"{stamp}_{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def move_to_outbox(task_file: Path, outbox_dir: Path) -> Path:
    destination = build_outbox_destination(outbox_dir, task_file.name)
    shutil.move(str(task_file), str(destination))
    return destination


def watch_inbox(
    *,
    inbox_dir: Path,
    outbox_dir: Path,
    poll_interval: float,
    args: argparse.Namespace,
    process_task: Callable[[Path, argparse.Namespace, bool], int],
    min_file_age_seconds: float = 1.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.time,
) -> int:
    inbox_dir.mkdir(parents=True, exist_ok=True)
    outbox_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Watching %s for .md files (outbox: %s, poll: %.2fs, min age: %.2fs).",
        inbox_dir,
        outbox_dir,
        poll_interval,
        min_file_age_seconds,
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
            finally:
                try:
                    destination = move_to_outbox(task_file, outbox_dir)
                    logger.info("Moved task to outbox: %s", destination)
                except Exception:
                    logger.exception("Failed to move %s to outbox.", task_file)

            if failed_with_exception:
                continue

            logger.info("Task finished with exit code %s: %s", exit_code, task_file.name)
    except KeyboardInterrupt:
        logger.info("Watch mode stopped.")
        return 0
